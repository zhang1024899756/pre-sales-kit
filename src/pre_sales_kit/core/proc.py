"""进程与平台探测：找端口占用者、起服务、杀进程。

跨平台，但优先把 Windows 体验做好——客户现场绝大多数是 Windows 笔记本。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pre_sales_kit.core.errors import FixError

IS_WINDOWS = os.name == "nt"

#: 绝对不能碰的进程（系统关键进程 + 桌面外壳）
PROTECTED_PROCESSES = {
    "system",
    "system idle process",
    "idle",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "explorer.exe",
    "spoolsv.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
    "conhost.exe",
    "python.exe" if False else "launchd",  # 占位，保持集合可读
}


@dataclass(frozen=True)
class Listener:
    port: int
    pid: int | None
    process: str | None
    address: str


def which(command: str) -> str | None:
    """在 PATH 里找可执行文件；返回绝对路径。"""
    if not command:
        return None
    return shutil.which(command)


def can_connect(host: str, port: int, timeout: float = 1.0) -> tuple[bool, float, str]:
    """TCP 探测。返回 (是否连通, 耗时秒, 失败原因)。"""
    import time

    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True, time.perf_counter() - started, ""
    except ConnectionRefusedError:
        return False, time.perf_counter() - started, "连接被拒绝（端口没有服务在监听）"
    except TimeoutError:
        return False, time.perf_counter() - started, f"连接超时（{timeout:g}s 无响应）"
    except socket.gaierror as exc:
        return False, time.perf_counter() - started, f"域名解析失败（{exc}）"
    except OSError as exc:
        return False, time.perf_counter() - started, f"连接失败（{exc}）"
    finally:
        sock.close()


def find_listener(port: int) -> Listener | None:
    """找出监听指定端口的进程。拿不到 PID 也返回 Listener(pid=None)。"""
    if IS_WINDOWS:
        return _find_listener_windows(port)
    return _find_listener_posix(port)


def _find_listener_windows(port: int) -> Listener | None:
    try:
        output = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() not in {"TCP"}:
            continue
        local, state, pid_text = parts[1], parts[3].upper(), parts[4]
        if state != "LISTENING":
            continue
        if not local.endswith(f":{port}"):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            pid = None
        return Listener(port=port, pid=pid, process=process_name(pid) if pid else None, address=local)
    return None


def _find_listener_posix(port: int) -> Listener | None:
    lsof = which("lsof")
    if lsof:
        try:
            output = subprocess.run(
                [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            output = ""
        pid: int | None = None
        name: str | None = None
        for line in output.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                pid = int(line[1:])
            elif line.startswith("c"):
                name = line[1:]
        if pid:
            return Listener(port=port, pid=pid, process=name, address=f"*:{port}")
    return None


def process_name(pid: int) -> str | None:
    try:
        if IS_WINDOWS:
            output = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                encoding="utf-8",
                errors="replace",
            ).stdout
            match = re.match(r'"(?P<name>[^"]+)"', output.strip())
            return match.group("name") if match else None
        output = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        ).stdout
        return output.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def pid_alive(pid: int) -> bool:
    """这个 PID 现在还活着吗？

    Windows 上用 tasklist 反查进程名：比 os.kill 可靠（Windows 上没有真正的信号 0），
    也顺手证明「这个 PID 上真的是个进程」而不是刚被回收的空号。
    """
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return process_name(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_process(pid: int, *, force: bool = True) -> str:
    """终止进程；返回给人看的结果描述。拒绝碰系统关键进程。"""
    if pid <= 4:
        raise FixError(f"拒绝终止 PID {pid}（系统进程）")
    name = (process_name(pid) or "").lower()
    if name in PROTECTED_PROCESSES:
        raise FixError(f"拒绝终止系统进程 {name}（PID {pid}）")
    try:
        if IS_WINDOWS:
            command = ["taskkill", "/PID", str(pid), "/T"]
            if force:
                command.append("/F")
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        else:
            result = subprocess.run(
                ["kill", "-TERM", str(pid)], capture_output=True, text=True, timeout=15, check=False
            )
            if force and result.returncode != 0:
                result = subprocess.run(
                    ["kill", "-9", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FixError(f"终止 PID {pid} 失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise FixError(f"终止 PID {pid} 失败：{detail[0] if detail else '未知原因'}")
    suffix = f"（{name}）" if name else ""
    return f"已终止 PID {pid}{suffix}"


def split_command(command: str) -> list[str]:
    """把 YAML 里写的一行命令拆成参数列表（不经过 shell）。"""
    import shlex

    return shlex.split(command, posix=not IS_WINDOWS)


def run_capture(command: str | list[str], *, timeout: float = 20.0) -> tuple[int, str]:
    """同步执行命令，返回 (退出码, 合并输出)。输出过长时只留头尾。"""
    args = command if isinstance(command, list) else split_command(command)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise FixError(f"命令不存在：{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FixError(f"命令超时（{timeout:g}s）：{' '.join(args)}") from exc
    except OSError as exc:
        raise FixError(f"命令执行失败：{exc}") from exc
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return result.returncode, output


def start_detached(command: str | list[str], *, cwd: Path | None = None) -> int:
    """后台起一个服务，不阻塞、不继承输出。返回 PID。"""
    args = command if isinstance(command, list) else split_command(command)
    working_dir = str(cwd) if cwd else None
    try:
        if IS_WINDOWS:
            # 新进程组 + 不弹窗：关掉当前终端也不影响演示服务
            process = subprocess.Popen(
                args,
                cwd=working_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                args,
                cwd=working_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except FileNotFoundError as exc:
        raise FixError(f"启动失败，命令不存在：{args[0]}") from exc
    except OSError as exc:
        raise FixError(f"启动失败：{exc}") from exc
    return process.pid


def platform_summary() -> str:
    """一行平台描述，报告头用：Windows 11 10.0.22631 / macOS 14.5 / Linux 6.8。"""
    if IS_WINDOWS:
        info = sys.getwindowsversion()
        label = platform_name()
        return f"{label} {info.major}.{info.minor}.{info.build}"
    import platform as platform_module

    return f"{platform_name()} {platform_module.release()}"


def platform_name() -> str:
    if IS_WINDOWS:
        info = sys.getwindowsversion()
        if info.major == 10 and info.build >= 22000:
            return "Windows 11"
        if info.major == 10:
            return "Windows 10"
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
