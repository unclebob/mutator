"""Run a project's tests, and decide the command for each language."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mutator.crapper_link import ensure_crapper

Command = str | list[str]

# How often a running command's memory and deadline are checked.
POLL_SECONDS = 0.1

# Process groups of the commands running now, so stop_all() can take them down on a signal.
_LIVE_GROUPS: set[int] = set()
_LIVE_LOCK = threading.RLock()  # re-entrant: a signal handler may call stop_all() while run() holds it
# Set by stop_all(): no new command starts and workers take no new mutant.
STOP = threading.Event()


def _kill_group(group: int) -> None:
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # already gone; macOS says EPERM for zombies
        pass


def stop_all() -> None:
    """Start no more commands and kill the process group of every running one.

    Each command runs in its own session (start_new_session), so a signal to mutator never
    reaches it; without this, an interrupted run leaves its test processes running.
    """

    STOP.set()
    with _LIVE_LOCK:
        groups = list(_LIVE_GROUPS)
    for group in groups:
        _kill_group(group)


@dataclass(frozen=True)
class CommandResult:
    code: int
    timed_out: bool
    seconds: float
    output: str
    # The most memory the command's process group held, in bytes; None where it cannot be read.
    peak_bytes: int | None = None
    # True when the group passed `memory_limit` and was killed.
    out_of_memory: bool = False


class _RusageInfoV2(ctypes.Structure):
    """struct rusage_info_v2 from <sys/resource.h>."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time",
            "ri_system_time",
            "ri_pkg_idle_wkups",
            "ri_interrupt_wkups",
            "ri_pageins",
            "ri_wired_size",
            "ri_resident_size",
            "ri_phys_footprint",
            "ri_proc_start_abstime",
            "ri_proc_exit_abstime",
            "ri_child_user_time",
            "ri_child_system_time",
            "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups",
            "ri_child_pageins",
            "ri_child_elapsed_abstime",
            "ri_diskio_bytesread",
            "ri_diskio_byteswritten",
        )
    ]


_LIBPROC: list = []


def _libproc():
    """libproc, loaded once; None when it cannot be loaded."""

    if not _LIBPROC:
        try:
            _LIBPROC.append(ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"))
        except OSError:
            _LIBPROC.append(None)
    return _LIBPROC[0]


def _darwin_footprint(pgid: int) -> int | None:
    """Physical footprint of the group, as Activity Monitor counts it: compressed and swapped pages too."""

    lib = _libproc()
    if lib is None:
        return None
    pids = (ctypes.c_int * 4096)()
    count = lib.proc_listpgrppids(pgid, pids, ctypes.sizeof(pids))
    total = 0
    for pid in pids[: max(count, 0)]:
        info = _RusageInfoV2()
        if lib.proc_pid_rusage(pid, 2, ctypes.byref(info)) == 0:  # 2 = RUSAGE_INFO_V2
            total += info.ri_phys_footprint
    return total


def _linux_resident(pid_dir: Path, pgid: int, page: int) -> int:
    """Resident bytes of one /proc/<pid> entry when it belongs to `pgid`, else 0."""

    try:
        stat = (pid_dir / "stat").read_text()
        # The command name is in parentheses and may hold spaces: split after the last ')'.
        if int(stat.rsplit(")", 1)[1].split()[2]) != pgid:
            return 0
        return int((pid_dir / "statm").read_text().split()[1]) * page
    except (OSError, IndexError, ValueError):  # the process exited while it was read
        return 0


def _linux_footprint(pgid: int, proc: Path = Path("/proc")) -> int:
    page = os.sysconf("SC_PAGE_SIZE")
    return sum(_linux_resident(entry, pgid, page) for entry in proc.iterdir() if entry.name.isdigit())


def group_footprint(pgid: int) -> int | None:
    """Bytes of memory the process group holds; None on a platform that cannot tell."""

    if sys.platform == "darwin":
        return _darwin_footprint(pgid)
    if sys.platform.startswith("linux"):
        return _linux_footprint(pgid)
    return None


def _stop(process, code: int, reason: str, peak: int | None):
    _kill_group(process.pid)
    output, _err = process.communicate()
    return output, code, reason, peak


def _higher(peak: int | None, size: int | None) -> int | None:
    """The larger reading. A reading that could not be taken (None) is skipped."""

    if size is None:
        return peak
    if peak is None:
        return size
    return max(peak, size)


def _over(size: int | None, limit: int | None) -> bool:
    """True only when both are known and the size is past the limit."""

    if size is None or limit is None:
        return False
    return size > limit


def _seconds_left(started: float, timeout: float | None) -> float:
    """Seconds until the deadline. Without a timeout there is no deadline."""

    if timeout is None:
        return math.inf
    return timeout - (time.monotonic() - started)


def _wait(process, started: float, timeout: float | None, memory_limit: int | None):
    """Wait for `process`, killing its group on the deadline or past the memory limit.

    Returns (output, code, reason, peak): reason is None, "timeout" or "memory".
    communicate() is retried after each poll; a retry loses no output.
    """

    peak = None
    while True:
        size = group_footprint(process.pid)
        peak = _higher(peak, size)
        if _over(size, memory_limit):
            return _stop(process, 137, "memory", peak)
        left = _seconds_left(started, timeout)
        if left <= 0:
            return _stop(process, 124, "timeout", peak)
        try:
            output, _err = process.communicate(timeout=min(POLL_SECONDS, left))
        except subprocess.TimeoutExpired:
            continue
        code = process.returncode if process.returncode is not None else 1
        return output, code, None, peak


def _worker_home(cwd: Path) -> Path | None:
    """The overlay root when cwd is inside target/mutation-workers."""

    for candidate in [cwd, *cwd.parents]:
        parent = candidate.parent
        if not candidate.name.startswith("worker-"):
            continue
        if not parent.name.startswith("run-"):
            continue
        if parent.parent.name == "mutation-workers":
            return candidate
    return None


def _source_entries(cwd: Path, worker: Path) -> list[str]:
    found = []
    for directory in (cwd / "src", cwd, worker / "src", worker):
        text = str(directory)
        if text in found:
            continue
        if directory.is_dir():
            found.append(text)
    return found


def _prefer_worker_sources(cwd: Path, environment: dict) -> None:
    """An editable install would otherwise import the unmutated tree."""

    worker = _worker_home(cwd)
    if worker is None:
        return
    entries = _source_entries(cwd, worker)
    current = environment.get("PYTHONPATH")
    if current:
        entries.append(current)
    if not entries:
        return
    environment["PYTHONPATH"] = os.pathsep.join(entries)


def display_command(command: Command) -> str:
    """A shell-looking rendering. A list is quoted so it can be pasted."""

    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


class CommandRunner:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run(
        self, command: Command, cwd: Path, timeout: float | None, memory_limit: int | None = None
    ) -> CommandResult:
        """Run `command` in `cwd`.

        A list is an argument vector and does not go through a shell, so a
        path with spaces or metacharacters stays one argument. A string is a
        command the user typed in `--test-command` or `--coverage-command`.
        The command's process group is killed after `timeout` seconds, or once
        it holds more than `memory_limit` bytes.
        """

        if self.verbose:
            print(f"+ ({cwd}) {display_command(command)}", file=sys.stderr)
        if STOP.is_set():
            return CommandResult(code=130, timed_out=False, seconds=0.0, output="stopped")
        started = time.monotonic()
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        _prefer_worker_sources(cwd, environment)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                shell=isinstance(command, str),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
                env=environment,
            )
        except OSError as exc:
            seconds = time.monotonic() - started
            return CommandResult(code=127, timed_out=False, seconds=seconds, output=str(exc))
        with _LIVE_LOCK:
            _LIVE_GROUPS.add(process.pid)
        if STOP.is_set():  # stop_all() ran between the check above and the registration
            _kill_group(process.pid)
        try:
            output, code, reason, peak = _wait(process, started, timeout, memory_limit)
        except BaseException:  # Ctrl-C or a signal's exit: the group would outlive mutator
            _kill_group(process.pid)
            raise
        finally:
            with _LIVE_LOCK:
                _LIVE_GROUPS.discard(process.pid)
        seconds = time.monotonic() - started
        return CommandResult(
            code=code,
            timed_out=reason == "timeout",
            seconds=seconds,
            output=output or "",
            peak_bytes=peak,
            out_of_memory=reason == "memory",
        )


def nearest(start: Path, marker: str, stop: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    stop = stop.resolve()
    current = current.resolve()
    while True:
        if (current / marker).is_file():
            return current
        if current == stop or current.parent == current:
            return None
        current = current.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _clojure_command(directory: Path) -> list[str]:
    deps = directory / "deps.edn"
    bb = directory / "bb.edn"
    if bb.is_file() and not deps.is_file():
        text = _read(bb)
        if "spec" in text:
            return ["bb", "spec", "--tag", "~no-mutate"]
        return ["bb", "test"]
    text = _read(deps)
    if ":spec" in text and "speclj" in text:
        return ["clj", "-M:spec", "--tag", "~no-mutate"]
    if ":spec" in text:
        return ["clj", "-M:spec"]
    return ["clj", "-M:test"]


def _project_interpreter(directory: Path) -> str:
    """The project's virtualenv, or this process when the project has none.

    The path stays absolute and the symlink is left in place. A worker does
    not link ``.venv``, and resolving ``bin/python`` would leave the virtualenv.
    """

    for name in (".venv", "venv"):
        candidate = (directory / name / "bin" / "python").absolute()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def _python_command(directory: Path) -> list[str]:
    interpreter = _project_interpreter(directory)
    pyproject = _read(directory / "pyproject.toml")
    if (
        (directory / "pytest.ini").is_file()
        or (directory / "conftest.py").is_file()
        or "pytest" in pyproject
        or (directory / "setup.cfg").is_file() and "pytest" in _read(directory / "setup.cfg")
    ):
        return [interpreter, "-m", "pytest"]
    return [interpreter, "-m", "unittest", "discover"]


def _nearest_marker(source: Path, root: Path, markers: tuple[str, ...]) -> Path:
    for marker in markers:
        found = nearest(source, marker, root)
        if found is not None:
            return found
    return root


def _go_package(directory: Path, source: Path) -> str:
    relative = source.resolve().parent.relative_to(directory.resolve())
    if relative == Path("."):
        return "."
    return "./" + relative.as_posix()


def _clojure_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("deps.edn", "bb.edn"))
    return _clojure_command(directory), directory


def _java_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("pom.xml",))
    return ["mvn", "-q", "test", "-DexcludeTags=no-mutate"], directory


def _go_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("go.mod",))
    return ["go", "test", "-count=1", _go_package(directory, source)], directory


def _typescript_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("package.json",))
    return ["npm", "test"], directory


def _rust_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("Cargo.toml",))
    return ["cargo", "test"], directory


def _python_plan(root: Path, source: Path) -> tuple[Command, Path]:
    directory = _nearest_marker(source, root, ("pyproject.toml", "pytest.ini", "setup.cfg"))
    return _python_command(directory), directory


def _xcode_flag(project: Path) -> str:
    if project.suffix == ".xcworkspace":
        return "-workspace"
    return "-project"


def _xcode_scheme(project: Path, requested: str | None) -> str:
    if requested:
        return requested
    schemes = ensure_crapper().runners.xcode_schemes(project)
    if schemes:
        return schemes[0]
    return project.stem


def _xcode_command(project: Path, scheme: str | None, destination: str | None) -> list[str]:
    """The project path is relative to the working directory, which is ``project.parent``.

    An absolute path would name the original tree. The worker maps the working
    directory and would then compile the unmutated project.
    """

    command = [
        "xcodebuild",
        "test",
        _xcode_flag(project),
        project.name,
        "-scheme",
        _xcode_scheme(project, scheme),
    ]
    if destination:
        command.extend(["-destination", destination])
    return command


def _swift_plan(
    root: Path, source: Path, scheme: str | None, destination: str | None
) -> tuple[Command, Path]:
    """SwiftPM uses ``swift test``. An Xcode or iOS project uses ``xcodebuild``.

    A file inside a package is not also sent to xcodebuild. Coverage stays
    with crapper; this command only decides whether the tests pass.
    """

    crapper = ensure_crapper()
    packages = crapper.runners.swift_packages(root, [source])
    if packages:
        return ["swift", "test"], packages[0]
    projects = crapper.runners.xcode_projects(root, [source])
    if not projects:
        return ["swift", "test"], root
    project = projects[0]
    return _xcode_command(project, scheme, destination), project.parent


_PLANS = {
    "clojure": _clojure_plan,
    "java": _java_plan,
    "go": _go_plan,
    "typescript": _typescript_plan,
    "rust": _rust_plan,
    "python": _python_plan,
}


def test_plan(
    root: Path,
    source: Path,
    language: str,
    override: str | None,
    *,
    xcode_scheme: str | None = None,
    xcode_destination: str | None = None,
) -> tuple[Command, Path]:
    """The test command and the directory it runs in.

    A user override stays a shell string. Every generated command is an
    argument vector, so a package path or an Xcode destination is one argument.
    ``xcode_scheme`` and ``xcode_destination`` apply to an Xcode or iOS project.
    iOS needs a destination and a Mac with Xcode.
    """

    root = root.resolve()
    if override:
        return override, root
    if language == "swift":
        return _swift_plan(root, source.resolve(), xcode_scheme, xcode_destination)
    plan = _PLANS.get(language)
    if plan is None:
        return ["false"], root
    return plan(root, source)
