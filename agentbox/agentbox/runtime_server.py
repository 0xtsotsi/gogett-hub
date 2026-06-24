from __future__ import annotations

import ast
import contextlib
import io
import json
import logging
import os
import pty
import signal
import subprocess
import sys
import time
import traceback
import types
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

logger = logging.getLogger(__name__)
MAX_RUNTIME_RESPONSE_ERROR_LENGTH = 2000
EXEC_COMMAND_FIELDS = {
    "cmd",
    "max_output_tokens",
    "process_id",
    "timeout",
    "tty",
    "workdir",
    "yield_time_ms",
}
DEFAULT_SHELL = "/bin/bash"


@dataclass
class RuntimeProcess:
    process_id: str
    popen: subprocess.Popen[str]
    command: str
    cwd: str
    started_at: float = field(default_factory=time.time)
    tty: bool = False
    pty_master_fd: int | None = None
    stdout: Queue[str] = field(default_factory=Queue)
    stderr: Queue[str] = field(default_factory=Queue)
    lock: Lock = field(default_factory=Lock)


@dataclass
class RuntimeSession:
    session_id: str
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = "/workspace"
    globals: dict[str, Any] = field(default_factory=dict)
    processes: dict[str, RuntimeProcess] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.globals:
            # Back the execution namespace with a real module registered in
            # sys.modules. Under Python 3.14 (PEP 649) annotations are evaluated
            # lazily, and libraries like pydantic resolve a class's deferred
            # annotations via sys.modules[cls.__module__].__dict__. Without a
            # registered module, names imported by the user code (e.g.
            # ``typing.Optional``) are unresolvable at schema-build time and only
            # builtins-only annotations like ``int | None`` work. Pointing the
            # module's __dict__ at session.globals keeps every user import in
            # scope for that resolution.
            module_name = f"__agentbox_{self.session_id}__"
            module = types.ModuleType(module_name)
            module.__dict__["__builtins__"] = __builtins__
            sys.modules[module_name] = module
            self.globals = module.__dict__


sessions: dict[str, RuntimeSession] = {}
sessions_lock = Lock()
execution_lock = Lock()


def _truncate_error(value: str) -> str:
    if len(value) <= MAX_RUNTIME_RESPONSE_ERROR_LENGTH:
        return value
    return f"{value[:MAX_RUNTIME_RESPONSE_ERROR_LENGTH]}... [truncated]"


def get_or_create_session(
    session_id: str = "default",
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> RuntimeSession:
    with sessions_lock:
        session = sessions.get(session_id)
        if session is None:
            session = RuntimeSession(session_id=session_id)
            sessions[session_id] = session
        if env:
            session.env.update({str(key): str(value) for key, value in env.items()})
        if cwd:
            session.cwd = cwd
        return session


def delete_session(session_id: str) -> bool:
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session is None:
        return False
    module_name = session.globals.get("__name__")
    if isinstance(module_name, str):
        sys.modules.pop(module_name, None)
    for process_id in list(session.processes):
        terminate_process(session_id, process_id, session=session)
    return True


@contextlib.contextmanager
def session_process_context(session: RuntimeSession):
    old_cwd = os.getcwd()
    old_env = os.environ.copy()
    try:
        os.environ.update(session.env)
        os.makedirs(session.cwd, exist_ok=True)
        os.chdir(session.cwd)
        yield
    finally:
        session.cwd = os.getcwd()
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


def execute_python(session_id: str, source: str) -> dict[str, Any]:
    session = get_or_create_session(session_id)
    stdout = io.StringIO()
    stderr = io.StringIO()
    result_repr: str | None = None

    with execution_lock:
        with session_process_context(session):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    tree = ast.parse(source, filename="<agentbox>", mode="exec")
                    if tree.body and isinstance(tree.body[-1], ast.Expr):
                        prefix = ast.Module(body=tree.body[:-1], type_ignores=tree.type_ignores)
                        ast.fix_missing_locations(prefix)
                        if prefix.body:
                            exec(compile(prefix, "<agentbox>", "exec"), session.globals)

                        expression = ast.Expression(tree.body[-1].value)
                        ast.fix_missing_locations(expression)
                        result = eval(compile(expression, "<agentbox>", "eval"), session.globals)
                        if result is not None:
                            result_repr = repr(result)
                    else:
                        exec(compile(tree, "<agentbox>", "exec"), session.globals)
                except BaseException as exc:
                    traceback.print_exc(file=stderr)
                    return {
                        "ok": False,
                        "stdout": stdout.getvalue(),
                        "stderr": stderr.getvalue(),
                        "result": None,
                        "error_name": exc.__class__.__name__,
                    }

    return {
        "ok": True,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "result": result_repr,
        "error_name": None,
    }


def execute_command(
    session_id: str,
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: str | None = None,
) -> dict[str, Any]:
    session = get_or_create_session(session_id, cwd=cwd)
    proc: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""
    try:
        resolved_cwd = _resolve_cwd(session, cwd)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_session_env(session),
            cwd=resolved_cwd,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                stdout, stderr = proc.communicate(timeout=2)
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
        else:
            stdout, stderr = "", ""
        return {
            "ok": False,
            "stdout": stdout or "",
            "stderr": (stderr or "") + f"\nCommand timed out after {timeout_seconds} seconds",
            "exit_code": None,
        }
    except OSError as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
        }
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": proc.returncode,
    }


def execute_shell_command(
    session_id: str,
    cmd: str,
    *,
    timeout_seconds: int,
    cwd: str | None = None,
) -> dict[str, Any]:
    marker = f"__AGENTBOX_CWD_{uuid4().hex}__"
    instrumented_cmd = (
        f"{cmd}\n"
        "_agentbox_status=$?\n"
        f"printf '\\n{marker}%s\\n' \"$PWD\"\n"
        "exit $_agentbox_status"
    )
    result = execute_command(
        session_id,
        [DEFAULT_SHELL, "-lc", instrumented_cmd],
        timeout_seconds=timeout_seconds,
        cwd=cwd,
    )
    stdout = str(result.get("stdout") or "")
    marker_position = stdout.rfind(f"\n{marker}")
    if marker_position < 0 and stdout.startswith(marker):
        marker_position = 0
    if marker_position >= 0:
        cwd_start = marker_position + (1 if stdout[marker_position] == "\n" else 0)
        cwd_start += len(marker)
        cwd_end = stdout.find("\n", cwd_start)
        if cwd_end < 0:
            cwd_end = len(stdout)
        new_cwd = stdout[cwd_start:cwd_end].strip()
        if new_cwd.startswith("/"):
            get_or_create_session(session_id).cwd = new_cwd
        result["stdout"] = stdout[:marker_position]
    return result


def _session_env(session: RuntimeSession) -> dict[str, str]:
    return {**os.environ, **session.env}


def _resolve_cwd(session: RuntimeSession, cwd: str | None = None) -> str:
    resolved = cwd or session.cwd or "/workspace"
    os.makedirs(resolved, exist_ok=True)
    session.cwd = resolved
    return resolved


def _read_stream(stream: Any, output: Queue[str]) -> None:
    try:
        while True:
            chunk = stream.readline()
            if not chunk:
                break
            output.put(chunk)
    finally:
        with contextlib.suppress(Exception):
            stream.close()


def _read_pty(master_fd: int, output: Queue[str]) -> None:
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.put(chunk.decode("utf-8", errors="replace"))
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


def _drain_output(output: Queue[str]) -> str:
    chunks: list[str] = []
    while True:
        try:
            chunks.append(output.get_nowait())
        except Empty:
            break
    return "".join(chunks)


def _truncate_output(value: str, max_output_tokens: int | None) -> str:
    if not max_output_tokens or max_output_tokens <= 0:
        return value
    max_chars = max_output_tokens * 4
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _process_response(
    session: RuntimeSession,
    runtime_process: RuntimeProcess,
    *,
    max_output_tokens: int | None = None,
    wait_ms: int | None = None,
) -> dict[str, Any]:
    if wait_ms and wait_ms > 0:
        # Yield window is a *maximum* wait, not a fixed delay: return as soon as
        # the process exits so a quick command isn't blocked for the whole window
        # (e.g. an `echo` must not take the default 30s). Long-running commands
        # still run out the window and yield a process_id as before.
        with contextlib.suppress(subprocess.TimeoutExpired):
            runtime_process.popen.wait(timeout=wait_ms / 1000)

    with runtime_process.lock:
        stdout = _truncate_output(_drain_output(runtime_process.stdout), max_output_tokens)
        stderr = _truncate_output(_drain_output(runtime_process.stderr), max_output_tokens)
        exit_code = runtime_process.popen.poll()
        completed = exit_code is not None
        if completed:
            time.sleep(0.01)
            stdout += _truncate_output(_drain_output(runtime_process.stdout), max_output_tokens)
            stderr += _truncate_output(_drain_output(runtime_process.stderr), max_output_tokens)
            session.processes.pop(runtime_process.process_id, None)

    return {
        "success": exit_code == 0 if completed else True,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "completed": completed,
        "process_id": None if completed else runtime_process.process_id,
        "error": None if (exit_code in {0, None}) else stderr,
    }


def _process_info(runtime_process: RuntimeProcess) -> dict[str, Any]:
    exit_code = runtime_process.popen.poll()
    return {
        "process_id": runtime_process.process_id,
        "cmd": runtime_process.command,
        "cwd": runtime_process.cwd,
        "tty": runtime_process.tty,
        "started_at": runtime_process.started_at,
        "completed": exit_code is not None,
        "exit_code": exit_code,
    }


def list_processes(session_id: str) -> dict[str, Any]:
    session = get_or_create_session(session_id)
    return {
        "processes": [
            _process_info(runtime_process)
            for runtime_process in session.processes.values()
        ]
    }


def start_interactive_command(
    session_id: str,
    *,
    cmd: str,
    cwd: str | None = None,
    tty: bool = False,
    max_output_tokens: int | None = None,
    yield_time_ms: int | None = None,
) -> dict[str, Any]:
    session = get_or_create_session(session_id, cwd=cwd)
    process_id = f"proc-{uuid4().hex}"
    try:
        resolved_cwd = _resolve_cwd(session, cwd)
        pty_master_fd: int | None = None
        if tty:
            master_fd, slave_fd = pty.openpty()
            try:
                popen = subprocess.Popen(
                    cmd,
                    shell=True,
                    executable=DEFAULT_SHELL,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    text=True,
                    bufsize=0,
                    cwd=resolved_cwd,
                    env=_session_env(session),
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                with contextlib.suppress(OSError):
                    os.close(slave_fd)
            pty_master_fd = master_fd
        else:
            popen = subprocess.Popen(
                cmd,
                shell=True,
                executable=DEFAULT_SHELL,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=resolved_cwd,
                env=_session_env(session),
                start_new_session=True,
            )
    except OSError as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "completed": True,
            "process_id": None,
            "error": str(exc),
        }

    runtime_process = RuntimeProcess(
        process_id=process_id,
        popen=popen,
        command=cmd,
        cwd=resolved_cwd,
        tty=tty,
        pty_master_fd=pty_master_fd,
    )
    session.processes[process_id] = runtime_process
    if pty_master_fd is not None:
        Thread(target=_read_pty, args=(pty_master_fd, runtime_process.stdout), daemon=True).start()
    elif popen.stdout is not None:
        Thread(target=_read_stream, args=(popen.stdout, runtime_process.stdout), daemon=True).start()
    if pty_master_fd is None and popen.stderr is not None:
        Thread(target=_read_stream, args=(popen.stderr, runtime_process.stderr), daemon=True).start()
    return _process_response(
        session,
        runtime_process,
        max_output_tokens=max_output_tokens,
        wait_ms=yield_time_ms,
    )


def write_process_stdin(
    session_id: str,
    *,
    process_id: str,
    chars: str | None = None,
    max_output_tokens: int | None = None,
    yield_time_ms: int | None = None,
) -> dict[str, Any]:
    session = get_or_create_session(session_id)
    runtime_process = session.processes.get(process_id)
    if runtime_process is None:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "completed": True,
            "process_id": process_id,
            "error": "Process not found",
        }
    with runtime_process.lock:
        if chars and runtime_process.popen.poll() is None and runtime_process.pty_master_fd is not None:
            with contextlib.suppress(OSError):
                os.write(runtime_process.pty_master_fd, chars.encode("utf-8"))
        elif chars and runtime_process.popen.stdin and runtime_process.popen.poll() is None:
            try:
                runtime_process.popen.stdin.write(chars)
                runtime_process.popen.stdin.flush()
            except BrokenPipeError:
                pass
    return _process_response(
        session,
        runtime_process,
        max_output_tokens=max_output_tokens,
        wait_ms=yield_time_ms,
    )


def terminate_process(
    session_id: str,
    process_id: str,
    *,
    session: RuntimeSession | None = None,
) -> dict[str, Any]:
    runtime_session = session or get_or_create_session(session_id)
    runtime_process = runtime_session.processes.pop(process_id, None)
    if runtime_process is None:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "completed": True,
            "process_id": process_id,
            "error": "Process not found",
        }

    popen = runtime_process.popen
    if popen.poll() is None:
        with contextlib.suppress(Exception):
            os.killpg(popen.pid, signal.SIGTERM)
        try:
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                os.killpg(popen.pid, signal.SIGKILL)
            popen.wait(timeout=5)

    if runtime_process.pty_master_fd is not None:
        with contextlib.suppress(OSError):
            os.close(runtime_process.pty_master_fd)

    return {
        "success": True,
        "stdout": _drain_output(runtime_process.stdout),
        "stderr": _drain_output(runtime_process.stderr),
        "exit_code": popen.poll(),
        "completed": True,
        "process_id": process_id,
        "error": None,
    }


class RuntimeHandler(BaseHTTPRequestHandler):
    server_version = "AgentBoxRuntime/0.1"

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except Exception as exc:
            logger.error(
                "Unhandled AgentBox runtime request error: method=%s path=%s "
                "error=%s traceback=%s",
                getattr(self, "command", None),
                getattr(self, "path", None),
                _truncate_error(f"{type(exc).__name__}: {exc}"),
                _truncate_error(traceback.format_exc()),
            )
            with contextlib.suppress(Exception):
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "detail": {
                            "message": "Unhandled runtime server error",
                            "error": _truncate_error(f"{type(exc).__name__}: {exc}"),
                        }
                    },
                )

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        parts = self._path_parts()
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "processes":
            self._send_json(HTTPStatus.OK, list_processes(parts[1]))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def do_DELETE(self) -> None:
        parts = self._path_parts()
        if len(parts) == 4 and parts[0] == "sessions" and parts[2] == "processes":
            self._send_json(HTTPStatus.OK, terminate_process(parts[1], parts[3]))
            return
        if len(parts) == 2 and parts[0] == "sessions":
            deleted = delete_session(parts[1])
            self._send_json(HTTPStatus.OK, {"session_id": parts[1], "deleted": deleted})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def do_POST(self) -> None:
        payload = self._read_json()
        if payload is None:
            return

        parts = self._path_parts()
        if len(parts) == 2 and parts[0] == "sessions":
            session = get_or_create_session(
                parts[1],
                env=payload.get("env") if isinstance(payload.get("env"), dict) else None,
                cwd=payload.get("cwd") if isinstance(payload.get("cwd"), str) else None,
            )
            self._send_json(
                HTTPStatus.OK,
                {"session_id": session.session_id, "cwd": session.cwd, "env_keys": sorted(session.env)},
            )
            return

        if self.path == "/execute" or (
            len(parts) == 3 and parts[0] == "sessions" and parts[2] == "execute"
        ):
            session_id = parts[1] if len(parts) == 3 else str(payload.get("session_id") or "default")
            code = payload.get("code")
            if not isinstance(code, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Field 'code' must be a string"})
                return
            self._send_json(HTTPStatus.OK, execute_python(session_id, code))
            return

        if self.path == "/command" or (
            len(parts) == 3 and parts[0] == "sessions" and parts[2] == "command"
        ):
            session_id = parts[1] if len(parts) == 3 else str(payload.get("session_id") or "default")
            command = payload.get("command")
            if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Field 'command' must be a string list"})
                return
            try:
                response = execute_command(
                    session_id,
                    command,
                    timeout_seconds=int(payload.get("timeout_seconds") or 60),
                    cwd=payload.get("cwd") if isinstance(payload.get("cwd"), str) else None,
                )
            except subprocess.TimeoutExpired as exc:
                response = {
                    "ok": False,
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "Command timed out",
                    "exit_code": None,
                }
            self._send_json(HTTPStatus.OK, response)
            return

        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "exec-command":
            session_id = parts[1]
            unknown_fields = sorted(set(payload) - EXEC_COMMAND_FIELDS)
            if unknown_fields:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"detail": f"Unsupported field(s): {', '.join(unknown_fields)}"},
                )
                return
            process_id = payload.get("process_id")
            if isinstance(process_id, str) and process_id:
                self._send_json(
                    HTTPStatus.OK,
                    write_process_stdin(
                        session_id,
                        process_id=process_id,
                        max_output_tokens=payload.get("max_output_tokens")
                        if isinstance(payload.get("max_output_tokens"), int)
                        else None,
                        yield_time_ms=payload.get("yield_time_ms")
                        if isinstance(payload.get("yield_time_ms"), int)
                        else None,
                    ),
                )
                return

            cmd = payload.get("cmd")
            if not isinstance(cmd, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Field 'cmd' must be a string"})
                return
            yield_time_ms = payload.get("yield_time_ms")
            interactive = bool(payload.get("tty")) or isinstance(yield_time_ms, int)
            if interactive:
                response = start_interactive_command(
                    session_id,
                    cmd=cmd,
                    cwd=payload.get("workdir") if isinstance(payload.get("workdir"), str) else None,
                    tty=bool(payload.get("tty")),
                    max_output_tokens=payload.get("max_output_tokens")
                    if isinstance(payload.get("max_output_tokens"), int)
                    else None,
                    yield_time_ms=yield_time_ms if isinstance(yield_time_ms, int) else None,
                )
            else:
                try:
                    result = execute_shell_command(
                        session_id,
                        cmd,
                        timeout_seconds=int(payload.get("timeout") or 300),
                        cwd=payload.get("workdir") if isinstance(payload.get("workdir"), str) else None,
                    )
                    response = {
                        "success": bool(result.get("ok")),
                        "stdout": _truncate_output(
                            result.get("stdout") or "",
                            payload.get("max_output_tokens")
                            if isinstance(payload.get("max_output_tokens"), int)
                            else None,
                        ),
                        "stderr": _truncate_output(
                            result.get("stderr") or "",
                            payload.get("max_output_tokens")
                            if isinstance(payload.get("max_output_tokens"), int)
                            else None,
                        ),
                        "exit_code": result.get("exit_code"),
                        "completed": True,
                        "process_id": None,
                        "error": None if result.get("ok") else result.get("stderr"),
                    }
                except subprocess.TimeoutExpired as exc:
                    response = {
                        "success": False,
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "Command timed out",
                        "exit_code": None,
                        "completed": True,
                        "process_id": None,
                        "error": "Command timed out",
                    }
            self._send_json(HTTPStatus.OK, response)
            return

        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "write-stdin":
            process_id = payload.get("process_id")
            if not isinstance(process_id, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Field 'process_id' must be a string"})
                return
            self._send_json(
                HTTPStatus.OK,
                write_process_stdin(
                    parts[1],
                    process_id=process_id,
                    chars=payload.get("chars") if isinstance(payload.get("chars"), str) else None,
                    max_output_tokens=payload.get("max_output_tokens")
                    if isinstance(payload.get("max_output_tokens"), int)
                    else None,
                    yield_time_ms=payload.get("yield_time_ms")
                    if isinstance(payload.get("yield_time_ms"), int)
                    else None,
                ),
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _path_parts(self) -> list[str]:
        return [unquote(part) for part in urlparse(self.path).path.strip("/").split("/") if part]

    def _read_json(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid JSON body"})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "JSON body must be an object"})
            return None
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _RuntimeHTTPServer(ThreadingHTTPServer):
    # Agents issue parallel tool calls, which arrive as a burst of simultaneous
    # connections on one runtime. The stdlib default listen backlog is 5, so a
    # burst larger than that risks dropped/reset SYNs that surface to the manager
    # as transport failures (HTTP 502). Raise the backlog and let the kernel
    # absorb the burst; each accepted connection still gets its own thread.
    request_queue_size = 256
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    port = int(os.environ.get("AGENTBOX_RUNTIME_PORT", "8080"))
    server = _RuntimeHTTPServer(("0.0.0.0", port), RuntimeHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
