"""服务进程管理：start / stop / status / restart。

启动：在独立控制台窗口运行 uvicorn，PID 写入 storage/server.pid。
停止：优先 PID 文件，并按端口反查 PID 做双保险，避免端口残留。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_PARENT = ROOT
PID_FILE = ROOT / "cnki_paper_api" / "storage" / "server.pid"
LOG_FILE = ROOT / "cnki_paper_api" / "storage" / "server.log"
PAPERS_FILE = ROOT / "cnki_paper_api" / "storage" / "papers.json"
PID_FILE.parent.mkdir(parents=True, exist_ok=True)


def _clear_papers() -> int:
    """清空临时论文库（start/stop 时各调一次，防止堆积）。返回清空条数。"""
    import json
    try:
        if not PAPERS_FILE.exists():
            return 0
        data = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
        n = len(data) if isinstance(data, dict) else 0
        PAPERS_FILE.write_text("{}", encoding="utf-8")
        return n
    except Exception:
        return 0


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5,
            ).stdout.decode("gbk", errors="replace")
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pids_by_port(port: int) -> set[int]:
    """按端口反查监听进程 PID（Windows netstat / 跨平台 lsof 兜底）。"""
    pids: set[int] = set()
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, timeout=10).stdout.decode("gbk", errors="replace")
            for line in out.splitlines():
                up = line.upper()
                if f":{port}" in line and "LISTENING" in up:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.add(int(parts[-1]))
        else:
            out = subprocess.run(["lsof", "-ti", f":{port}"],
                                 capture_output=True, timeout=10).stdout.decode(errors="replace")
            pids = {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        pass
    return pids


def _config() -> dict:
    env_file = ROOT / "cnki_paper_api" / ".env"
    cfg = {"HOST": "127.0.0.1", "PORT": 8765}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k in cfg:
                    cfg[k] = int(v) if k == "PORT" else v
    return cfg


def start() -> None:
    pid = _read_pid()
    if pid and _alive(pid):
        print(f"[start] 服务已在运行，PID={pid}")
        return
    cfg = _config()
    port = cfg["PORT"]
    if _port_in_use(port):
        owners = _pids_by_port(port)
        print(f"[start] 端口 {port} 已被占用（PID {owners or '未知'}），但 PID 文件无效。")
        print("        先运行 stop.bat 清理，或手动 taskkill /PID <pid> /F")
        return

    n = _clear_papers()
    print(f"[start] 已清空临时论文库（{n} 条）")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write("\n===== start =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "cnki_paper_api.api.main:app",
             "--host", cfg["HOST"], "--port", str(port)],
            cwd=str(PKG_PARENT), env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    # 等端口就绪，最多 15 秒
    ready = False
    for _ in range(30):
        if _port_in_use(port):
            ready = True
            break
        time.sleep(0.5)
    if ready:
        print(f"[start] 服务已就绪 PID={proc.pid}")
        print(f"[start] 接口文档: http://{cfg['HOST']}:{port}/docs")
        print(f"[start] 日志: {LOG_FILE}")
    else:
        print(f"[start] 进程已拉起但端口 {port} 未就绪，请查看日志：")
        print(LOG_FILE.read_text(encoding="utf-8", errors="replace")[-1500:])


def stop() -> None:
    cfg = _config()
    port = cfg["PORT"]
    pids = _pids_by_port(port)
    pid = _read_pid()
    if pid and _alive(pid):
        pids.add(pid)

    if not pids:
        print("[stop] 未发现运行中的服务进程（PID 文件与端口均未找到）。")
        PID_FILE.unlink(missing_ok=True)
        n = _clear_papers()
        print(f"[stop] 已清空临时论文库（{n} 条）")
        return

    for p in sorted(pids):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        print(f"[stop] 已终止 PID={p}")

    # 等端口释放
    for _ in range(20):
        if not _port_in_use(port):
            break
        time.sleep(0.2)
    PID_FILE.unlink(missing_ok=True)
    n = _clear_papers()
    print(f"[stop] 已清空临时论文库（{n} 条）")
    print(f"[stop] 完成。端口 {port} {'仍被占用' if _port_in_use(port) else '已释放'}")


def status() -> None:
    cfg = _config()
    port = cfg["PORT"]
    pid = _read_pid()
    by_port = _pids_by_port(port)
    running = bool(by_port) or bool(pid and _alive(pid))
    print(f"服务状态: {'运行中' if running else '未运行'}")
    if pid:
        print(f"PID 文件: {pid} ({'存活' if _alive(pid) else '失效'})")
    if by_port:
        print(f"端口 {port} 监听进程: {sorted(by_port)}")
    else:
        print(f"端口 {port} 占用: {'是' if _port_in_use(port) else '否'}")
    if running:
        try:
            import httpx
            r = httpx.get(f"http://{cfg['HOST']}:{port}/health", timeout=3)
            print(f"HTTP 健康: {r.status_code} {r.json()}")
        except Exception as exc:
            print(f"HTTP 健康: 无法访问 ({exc})")
    if LOG_FILE.exists():
        print(f"日志: {LOG_FILE}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    actions = {
        "start": start, "stop": stop, "status": status,
        "restart": lambda: (stop(), time.sleep(1), start()),
    }
    actions.get(cmd, status)()


if __name__ == "__main__":
    main()
