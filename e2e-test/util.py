import base64
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from ctypes import wintypes
from enum import Enum
from pathlib import Path
import time
from typing import List, Optional

if sys.platform == "win32":
    import ctypes

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore
    except Exception:
        pass

    @contextmanager
    def cbreak_noecho():
        PROCESSED = 1
        WRAP = 2
        VIRTUAL = 4
        Stdin = subprocess.STD_INPUT_HANDLE
        Stdout = subprocess.STD_OUTPUT_HANDLE
        OldStdinMode = wintypes.DWORD()
        OldStdoutMode = wintypes.DWORD()
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleMode(
            kernel32.GetStdHandle(Stdin), ctypes.byref(OldStdinMode)
        )
        kernel32.SetConsoleMode(kernel32.GetStdHandle(Stdin), 0)
        kernel32.GetConsoleMode(
            kernel32.GetStdHandle(Stdout), ctypes.byref(OldStdoutMode)
        )
        outMode = VIRTUAL | WRAP | PROCESSED
        kernel32.SetConsoleMode(kernel32.GetStdHandle(Stdout), outMode)
        try:
            yield
        finally:
            kernel32.SetConsoleMode(kernel32.GetStdHandle(Stdin), OldStdinMode)
            kernel32.SetConsoleMode(kernel32.GetStdHandle(Stdout), OldStdoutMode)

else:
    import termios

    @contextmanager
    def cbreak_noecho():
        LFLAG = 3
        CC = 6
        stdin = sys.stdin.fileno()
        orig = termios.tcgetattr(stdin)
        term = deepcopy(orig)
        term[LFLAG] &= ~(termios.ICANON | termios.ECHO)
        term[CC][termios.VMIN] = 1
        term[CC][termios.VTIME] = 0
        termios.tcsetattr(stdin, termios.TCSANOW, term)
        try:
            yield
        finally:
            termios.tcsetattr(stdin, termios.TCSANOW, orig)


_stdout_lock = threading.Lock()


class Style(Enum):
    title = 0
    subtitle = 1
    text = 2
    progress = 3
    divider = 4
    error = 5


_styles = {
    Style.title: {"color": "\033[96m", "box": 2},
    Style.subtitle: {"color": "\033[92m", "box": 1},
    Style.text: {"color": "\033[97m", "text": 1},
    Style.progress: {"color": "\033[2m", "text": 1},
    Style.divider: {"color": "\033[2m", "line": 1},
    Style.error: {"color": "\033[91m", "line": 1, "text": 1},
}
_style_lines = {
    2: "╔═╗║ ║╚═╝",
    1: "┌─┐│ │└─┘",
}
_style_reset = "\033[0m"
_style_term_size: Optional[tuple[int, int]] = None
_style_term_size_chk: float = 0.0

def _write_style_unsafe(style: Style, text: str):
    """Write styled text to console"""

    def make_line(line_type, txt: str):
        if not line_type:
            return None
        ltxt = len(txt.splitlines()[0])
        global _style_term_size
        global _style_term_size_chk
        if _style_term_size is None or (time.monotonic() - _style_term_size_chk) > 0.1:
            _style_term_size = shutil.get_terminal_size()
            _style_term_size_chk = time.monotonic()
        cols = _style_term_size[0]
        l = cols if ltxt > cols else ltxt
        return _style_lines[line_type][1] * l

    def make_box(box_type, txtmsg):
        if not box_type:
            return None
        chars = _style_lines[box_type]
        b = lambda i: chars[i]
        l = len(txtmsg) + 2
        t = f"{b(0)}{b(1) * l}{b(2)}"
        i = f"{b(3)} {txtmsg} {b(5)}"
        m = f"{b(6)}{b(1) * l}{b(8)}"
        return f"{t}\n{i}\n{m}"

    ht = _styles[style]
    color = ht["color"]
    reset = _style_reset

    if "line" in ht:
        line = make_line(ht["line"], text)
        sys.stdout.write(f"{color}{line}{reset}\n")

    if "text" in ht:
        sys.stdout.write(f"{color}{text}{reset}\n")

    if "box" in ht:
        box = make_box(ht["box"], text)
        sys.stdout.write(f"{color}{box}{reset}\n")


def write_style(style: Style, text: str):
    with _stdout_lock:
        _write_style_unsafe(style, text)


class Command(Enum):
    flush = 0
    clear_line = 1
    delete_line = 2
    save_cursor = 3
    restore_cursor = 4
    move_to = 5
    cursor_position = 6
    set_region = 7
    reset_region = 8
    enter_alternate = 9
    exit_alternate = 10


_write_commands = {
    Command.flush: "",
    Command.clear_line: "\033[2K\033[1G",
    Command.delete_line: "\033[2K\033[1M",
    Command.save_cursor: "\0337",
    Command.restore_cursor: "\0338",
    Command.move_to: "\033[{arg0};{arg1}H",
    Command.cursor_position: "\033[6n",
    Command.set_region: "\033[{arg0};{arg1}r",
    Command.reset_region: "\033[r",
    Command.enter_alternate: "\033[?1049h\033[H",
    Command.exit_alternate: "\033[?1049l",
}


def write_console(
    command: Command, arg0: int = 0, arg1: int = 0
) -> Optional[tuple[int, int]]:
    """Write control signals to console"""

    cmd = _write_commands[command]

    sys.stdout.write(cmd.format(arg0=arg0, arg1=arg1))
    if command == Command.flush:
        sys.stdout.flush()


_read_commands = {
    Command.cursor_position: r"\033\[(\d+);(\d+)R",
}


def read_console(command: Command) -> List[int]:
    """Read information from console"""

    cmd = _read_commands[command]

    write_console(Command.flush)
    with cbreak_noecho():
        write_console(command)
        write_console(Command.flush)
        buf = ""
        while not buf.endswith(cmd[-1]):
            buf += sys.stdin.read(1)

    m = re.match(cmd, buf)
    if not m:
        raise RuntimeError(f"Unexpected cursor response: {buf!r}")
    l: List[int] = []
    for g in m.groups():
        l.append(int(g))
    return l


class ScreenLayout:
    def __init__(self, async_rows: int = 10):
        self.async_rows = async_rows
        self._lines: deque[str] = deque(maxlen=async_rows)
        self._lines_lck = threading.Lock()
        self._setup()

    def _resize(self):
        self._cols, self._rows = shutil.get_terminal_size((120, 24))
        self.divider_row = self._rows - self.async_rows
        self.async_top = self.divider_row + 1
        self.main_bottom = self.divider_row - 1

    def _setup(self):
        """Setup scroll region for main pane and draw alternate pane."""
        with _stdout_lock:
            self._resize()
            [row, _] = read_console(Command.cursor_position)
            # async pane
            if row > self.divider_row:
                for i in range(self.async_rows + 1):
                    _write_style_unsafe(Style.progress, "")
                row = self.main_bottom
            # divider
            write_console(Command.move_to, self.divider_row, 1)
            _write_style_unsafe(Style.divider, "-" * self._cols)
            # main pane
            write_console(Command.set_region, 1, self.main_bottom)
            write_console(Command.move_to, row, 1)
            write_console(Command.flush)

    def reset(self):
        """Reset scroll region and clear the alternate pane."""
        with _stdout_lock:
            write_console(Command.reset_region)
            # divider and async pane
            for r in range(self.divider_row, self._rows + 1):
                write_console(Command.move_to, r, 1)
                write_console(Command.clear_line)
            # move to divider row so subsequent output starts there
            write_console(Command.move_to, self.divider_row, 1)
            write_console(Command.flush)

    def render_async(self, line: str):
        """Overwrite the alternate pane in-place."""
        with self._lines_lck:
            self._lines.append(line)
            lines = list(self._lines)
        with _stdout_lock:
            self._resize()
            write_console(Command.save_cursor)
            for i in range(self.async_rows):
                write_console(Command.move_to, self.async_top + i, 1)
                write_console(Command.clear_line)
                if i < len(lines):
                    truncated = lines[i][: self._cols - 1]
                    _write_style_unsafe(Style.progress, truncated)
            write_console(Command.restore_cursor)
            write_console(Command.flush)


def run_command(
    title: str,
    cmd: List[str],
    cwd: Optional[Path] = None,
    check=True,
    capture_output=True,
    env=None,
    timeout=120,
    log=True,
):
    """Run a command and return the result."""
    if log:
        write_style(Style.text, f"Running: {cmd}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=check,
        capture_output=capture_output,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not log:
        return result
    if result.returncode == 0:
        if result.stdout:
            write_style(Style.text, result.stdout)
        if result.stderr:
            write_style(Style.progress, result.stderr)
    else:
        write_style(Style.error, f"{title} stdout: {result.stdout}")
        write_style(Style.error, f"{title} stderr: {result.stderr}")
    return result


class AsyncCommand:
    """Wraps a long-running subprocess with live scrolling output."""

    def __init__(
        self, title: str, process: subprocess.Popen[str], layout: ScreenLayout
    ):
        self.title = title
        self.process = process
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()
        self._layout = layout

    def _drain(self):
        assert self.process.stdout
        for raw in self.process.stdout:
            line = raw.rstrip()
            self._layout.render_async(line)
        assert self.process.stderr
        for raw in self.process.stderr:
            line = raw.rstrip()
            self._layout.render_async("error:" + line)
        self._done.set()

    def poll(self, timeout=0) -> Optional[int]:
        return self.process.poll()

    def terminate(self, timeout=10):
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self._done.wait(timeout=timeout / 2)
        self._layout.reset()


class DetachedAsyncCommand(AsyncCommand):
    def __init__(
        self,
        title: str,
        exe_name: str,
        pid: Optional[int] = None,
    ):
        self.title = title
        self._exe_name = exe_name
        self._pid = pid
        self._start_time = time.monotonic()

    def _find_pid(self) -> Optional[int]:
        """Look up the PID by exe name via tasklist."""
        if self._pid is not None:
            return self._pid
        try:
            out = subprocess.check_output(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {self._exe_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.strip().splitlines():
                parts = line.strip('"').split('","')
                if parts and parts[0].lower() == self._exe_name.lower():
                    self._pid = int(parts[1])
                    return self._pid
        except Exception:
            pass
        return None

    def poll(self, timeout=5) -> Optional[int]:
        """Return None if the process is still running, 0 if it has exited."""
        pid = self._find_pid()
        if pid is None:
            # PID not found — only report as exited after grace period
            elapsed = time.monotonic() - self._start_time
            if elapsed < timeout:
                return None
            return 0
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            if str(pid) in out:
                return None  # still running
            return 0
        except Exception:
            return 0

    def terminate(self, timeout=0):
        """Kill the detached process by PID (preferred) or exe name."""
        pid = self._find_pid()
        if pid is not None:
            subprocess.call(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            write_style(Style.text, f"{self.title} terminated (PID {pid})")
        else:
            # Fallback: kill by image name (may affect other instances)
            write_style(
                Style.error,
                f"{self.title}: PID unknown, killing by name '{self._exe_name}'",
            )
            subprocess.call(
                ["taskkill", "/IM", self._exe_name, "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _run_detached(
    title: str,
    cmd: List[str],
    cwd: Optional[Path] = None,
    env=None,
) -> AsyncCommand:
    """Launch command in a new Windows Terminal split pane, detached."""

    cwd_str = str(cwd).replace("'", "''") if cwd else None
    cmd_parts = "& " + " ".join(f'"{part}"' for part in cmd)
    exe_name = Path(cmd[0]).name

    script_lines = []
    if cwd_str:
        script_lines.append(f"Set-Location '{cwd_str}'")

    if env:
        for k, v in env.items():
            escaped = str(v).replace("'", "''")
            script_lines.append(f"${{env:{k}}} = '{escaped}'")

    script_lines.append(cmd_parts)
    script_lines.append("exit")

    # Encode as UTF-16LE base64 (what pwsh -EncodedCommand expects)
    full_script = "; ".join(script_lines)
    encoded = base64.b64encode(full_script.encode("utf-16-le")).decode("ascii")

    wt_cmd = [
        "wt",
        "-w",
        "0",
        "split-pane",
        "-H",
        "--title",
        title,
        "pwsh",
        "-NoExit",
        "-NoProfile",
        "-EncodedCommand",
        encoded,
    ]

    wt_process = subprocess.Popen(
        wt_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wt_process.wait()  # wt exits immediately after spawning the pane

    write_style(Style.text, f"{title} launched detached in new WT pane")
    return DetachedAsyncCommand(title, exe_name)


def run_command_async(
    title: str,
    cmd: List[str],
    cwd: Optional[Path] = None,
    env=None,
    detach=False,
) -> AsyncCommand:
    """Start a command asynchronously and return the Popen process."""

    write_style(Style.text, f"Running async: {cmd}")

    if detach:
        return _run_detached(title, cmd, cwd, env)

    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        write_style(Style.error, f"{title} stdout: {stdout}")
        write_style(Style.error, f"{title} stderr: {stderr}")
        raise RuntimeError(f"{title} failed to start")

    layout = ScreenLayout(async_rows=12)
    cmd_obj = AsyncCommand(title, process, layout)

    write_style(Style.text, f"{title} started (PID {process.pid})")
    return cmd_obj
