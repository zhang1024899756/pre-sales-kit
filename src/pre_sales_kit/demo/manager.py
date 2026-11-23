"""演示服务的启停与状态 —— `kit demo` 的实现。

一条安全原则贯穿全部：**只停自己起的进程**。
客户机器上的 8080 可能跑着他们真实的服务，kill 错一次的代价比多敲一条命令大得多。
所以每条记录都带 PID，停之前先核对「端口上的监听者是不是还是这个 PID」。
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pre_sales_kit.core.errors import FixError, KitError
from pre_sales_kit.core.proc import (
    IS_WINDOWS,
    can_connect,
    find_listener,
    kill_process,
    pid_alive,
    process_name,
)

#: 状态目录与文件名（放在当前工作目录下，跟 doctor.yaml 同级）
STATE_DIR = ".kit"
STATE_FILE = "demo.json"

#: 默认两个服务：门户和报警通知
DEFAULT_SERVICES: tuple[tuple[str, int], ...] = (("portal", 8080), ("alarm", 6379))

ROLE_LABEL = {"portal": "演示门户", "alarm": "报警通知服务"}


def label_of(role: str) -> str:
    return ROLE_LABEL.get(role, role)


@dataclass
class ServiceRecord:
    """一条「kit 起过的服务」记录。"""

    role: str
    port: int
    pid: int
    started: str
    db: str | None = None

    @property
    def label(self) -> str:
        return label_of(self.role)


@dataclass
class DemoState:
    services: list[ServiceRecord] = field(default_factory=list)

    def find(self, role: str) -> ServiceRecord | None:
        return next((record for record in self.services if record.role == role), None)

    def put(self, record: ServiceRecord) -> None:
        self.services = [item for item in self.services if item.role != record.role]
        self.services.append(record)

    def forget(self, role: str) -> None:
        self.services = [item for item in self.services if item.role != role]

    @property
    def empty(self) -> bool:
        return not self.services


@dataclass
class Outcome:
    """一个动作的结果，直接拿去打印。"""

    role: str
    port: int
    #: started / running / stopped / gone / failed / skipped
    status: str
    detail: str

    @property
    def label(self) -> str:
        return label_of(self.role)


# ---------------- 状态文件 ----------------


def state_path(root: Path | str) -> Path:
    return Path(root) / STATE_DIR / STATE_FILE


def log_path(root: Path | str, role: str) -> Path:
    return Path(root) / STATE_DIR / f"demo-{role}.log"


def load_state(path: Path) -> DemoState:
    """读状态文件；不存在就是空状态，坏了就报清楚该删哪个文件。"""
    if not path.exists():
        return DemoState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KitError(
            f"演示服务状态文件读不了：{path}（{exc}）",
            hint=f"删除 {path} 后重新 kit demo up；它只是记账，删掉不影响任何正在运行的服务",
        ) from exc
    services: list[ServiceRecord] = []
    for raw in payload.get("services") or []:
        try:
            services.append(
                ServiceRecord(
                    role=str(raw["role"]),
                    port=int(raw["port"]),
                    pid=int(raw["pid"]),
                    started=str(raw.get("started") or ""),
                    db=raw.get("db"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return DemoState(services=services)


def save_state(path: Path, state: DemoState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "saved": datetime.now().astimezone().isoformat(timespec="seconds"),
        "services": [
            {
                "role": record.role,
                "port": record.port,
                "pid": record.pid,
                "started": record.started,
                "db": record.db,
            }
            for record in state.services
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------- 描述现状 ----------------


def describe(record: ServiceRecord) -> tuple[str, str]:
    """把「记录 + 当前系统状态」翻译成人话。返回 (状态词, 细节)。"""
    listener = find_listener(record.port)
    alive = pid_alive(record.pid)
    if listener is not None:
        if listener.pid == record.pid:
            return "running", f"127.0.0.1:{record.port} 在监听 · PID {record.pid}"
        if listener.pid is None:
            return "unverified", f"127.0.0.1:{record.port} 在监听，但拿不到监听者 PID"
        process = listener.process or "未知进程"
        return "taken", f"端口被 PID {listener.pid}（{process}）占用，不是 kit 起的"
    if alive:
        return "starting", f"进程 PID {record.pid} 还在，但端口没监听"
    return "dead", "进程已退出，端口也空着"


def probe_port(port: int) -> str | None:
    """这个端口现在是空的吗？返回占用者描述，空着返回 None。"""
    listener = find_listener(port)
    if listener is None:
        return None
    if listener.pid is None:
        return f"127.0.0.1:{port} 已被占用（拿不到占用者）"
    return f"127.0.0.1:{port} 已被 PID {listener.pid}（{listener.process or '未知进程'}）占用"


# ---------------- 启动 ----------------


def service_command(role: str, port: int, db: str | None) -> list[str]:
    """起服务的命令行。

    用当前解释器 + `-m`：不依赖 PATH 里有没有 kit，
    也不依赖 spec_dir 下有没有 demo_app.py。
    """
    command = [sys.executable, "-m", "pre_sales_kit.demo", "--port", str(port), "--role", role]
    if db:
        command += ["--db", db]
    return command


def _spawn(command: list[str], *, root: Path, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = log.open("ab")
    except OSError as exc:
        raise FixError(f"打不开日志文件 {log}：{exc}") from exc
    try:
        if IS_WINDOWS:
            process = subprocess.Popen(
                command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except (OSError, ValueError) as exc:
        raise FixError(f"启动失败：{exc}") from exc
    finally:
        handle.close()
    return process.pid


def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        if can_connect("127.0.0.1", port, timeout=0.4)[0]:
            return True
        time.sleep(0.1)
    return False


def start(
    root: Path,
    targets: list[tuple[str, int]],
    *,
    db: str | None = None,
    wait: float = 5.0,
) -> list[Outcome]:
    """起一批服务；端口被别的进程占着就不起，也不去动它。"""
    path = state_path(root)
    state = load_state(path)
    outcomes: list[Outcome] = []

    for role, port in targets:
        existing = state.find(role)
        occupied = probe_port(port)
        if occupied is not None:
            if existing is not None and existing.port == port:
                word, detail = describe(existing)
                if word in {"running", "unverified"}:
                    outcomes.append(Outcome(role, port, "running", f"已经在跑了 · {detail}"))
                    continue
            outcomes.append(
                Outcome(role, port, "failed", f"{occupied}；没有动它，先腾出端口或换 --port")
            )
            continue
        if existing is not None and pid_alive(existing.pid):
            outcomes.append(
                Outcome(
                    role,
                    port,
                    "failed",
                    f"上次起的进程 PID {existing.pid} 还在，但端口没监听；先 kit demo down 再起",
                )
            )
            continue

        log = log_path(root, role)
        spawned = _spawn(service_command(role, port, db), root=root, log=log)
        if _wait_port(port, wait):
            # 记**真正监听端口的进程**，不是 Popen 返回的那个。
            # Windows 上 python.exe 可能是应用商店别名（或类似的启动器），
            # 它自己不在监听，真正监听的是它拉起的子进程——记错了，
            # describe 会说「这不是 kit 起的」，stop 也会杀错进程。
            listener = find_listener(port)
            listen_pid = listener.pid if listener is not None and listener.pid else spawned
            state.put(
                ServiceRecord(
                    role=role,
                    port=port,
                    pid=listen_pid,
                    started=datetime.now().astimezone().isoformat(timespec="seconds"),
                    db=db,
                )
            )
            detail = f"PID {listen_pid} · 日志 {log.name}"
            if listen_pid != spawned:
                detail += f"（启动器 {spawned} 起的是子进程）"
            outcomes.append(Outcome(role, port, "started", detail))
        else:
            victim = spawned
            listener = find_listener(port)
            if listener is not None and listener.pid:
                victim = listener.pid
            with contextlib.suppress(FixError):
                kill_process(victim)
            tail = _log_tail(log)
            detail = f"{wait:g}s 内端口没起来，已终止 PID {victim} · 日志 {log}"
            if tail:
                detail += f"：{tail}"
            outcomes.append(Outcome(role, port, "failed", detail))

    save_state(path, state)
    return outcomes


def _log_tail(path: Path, lines: int = 3) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " | ".join(item.strip() for item in content[-lines:] if item.strip())


# ---------------- 停止 ----------------


def stop(root: Path, *, roles: list[str] | None = None) -> list[Outcome]:
    """停掉 kit 起过的服务。端口上换成了别的进程就只报告，不动手。"""
    path = state_path(root)
    state = load_state(path)
    wanted = set(roles or ())
    outcomes: list[Outcome] = []

    for record in list(state.services):
        if wanted and record.role not in wanted:
            continue
        word, detail = describe(record)
        if word == "running" or (word == "starting" and pid_alive(record.pid)):
            try:
                message = kill_process(record.pid)
            except FixError as exc:
                outcomes.append(Outcome(record.role, record.port, "failed", str(exc)))
                continue
            state.forget(record.role)
            outcomes.append(Outcome(record.role, record.port, "stopped", message))
        elif word == "taken":
            state.forget(record.role)
            outcomes.append(
                Outcome(record.role, record.port, "skipped", f"{detail}；只清了记录，没动那个进程")
            )
        else:
            state.forget(record.role)
            outcomes.append(Outcome(record.role, record.port, "gone", "本来就没在跑，已清理记录"))

    if wanted:
        missing = wanted - {outcome.role for outcome in outcomes}
        for role in sorted(missing):
            outcomes.append(
                Outcome(role, 0, "skipped", "状态文件里没有这条记录（不是 kit 起的，或已被清理）")
            )

    save_state(path, state)
    return outcomes


# ---------------- 只读查询 ----------------


@dataclass
class StatusRow:
    record: ServiceRecord
    state: str
    detail: str


def status(root: Path) -> tuple[list[StatusRow], list[str]]:
    """返回 (状态行, 额外提示)。提示用于「状态文件是空的，但端口被占着」这种情况。"""
    state = load_state(state_path(root))
    rows = [StatusRow(record, *describe(record)) for record in state.services]
    known = {record.port for record in state.services}
    hints: list[str] = []
    for role, port in DEFAULT_SERVICES:
        if port in known:
            continue
        occupied = probe_port(port)
        if occupied is not None:
            hints.append(f"{label_of(role)}（{port}）{occupied}，但不是 kit 起的")
    return rows, hints


def open_port(port: int) -> bool:
    return webbrowser.open(f"http://127.0.0.1:{port}/")


def guess_port(root: Path) -> int:
    """打开浏览器时用哪个端口：状态里的门户优先，否则 8080。"""
    state = load_state(state_path(root))
    record = state.find("portal")
    return record.port if record is not None else DEFAULT_SERVICES[0][1]


def process_label(pid: int) -> str:
    return process_name(pid) or "未知进程"
