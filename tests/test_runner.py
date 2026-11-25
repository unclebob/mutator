import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import mutator.runner as runner_module
from mutator.runner import STOP, CommandRunner, _higher, _linux_footprint, _over, _seconds_left, _stop
from mutator.runner import group_footprint, stop_all
from mutator.runner import _clojure_command, _python_command, nearest
from mutator.runner import test_plan as plan_command


def test_nearest_walks_up_to_the_marker(tmp_path):
    root = tmp_path / "proj"
    nested = root / "src" / "demo"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    source = nested / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert nearest(source, "pyproject.toml", root) == root
    assert nearest(nested, "pyproject.toml", root) == root
    assert nearest(source, "missing.toml", root) is None


def test_clojure_commands(tmp_path):
    bb = tmp_path / "bb-spec"
    bb.mkdir()
    (bb / "bb.edn").write_text('{:tasks {spec "spec"}}', encoding="utf-8")
    assert _clojure_command(bb) == ["bb", "spec", "--tag", "~no-mutate"]

    bb_test = tmp_path / "bb-test"
    bb_test.mkdir()
    (bb_test / "bb.edn").write_text("{:tasks {test (clojure \"-M:test\")}}", encoding="utf-8")
    assert _clojure_command(bb_test) == ["bb", "test"]

    spec = tmp_path / "spec"
    spec.mkdir()
    (spec / "deps.edn").write_text(
        "{:aliases {:spec {:extra-deps {speclj/speclj {}}}}}", encoding="utf-8"
    )
    assert _clojure_command(spec) == ["clj", "-M:spec", "--tag", "~no-mutate"]

    spec_only = tmp_path / "spec-only"
    spec_only.mkdir()
    (spec_only / "deps.edn").write_text("{:aliases {:spec {}}}", encoding="utf-8")
    assert _clojure_command(spec_only) == ["clj", "-M:spec"]

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "deps.edn").write_text("{:deps {}}", encoding="utf-8")
    assert _clojure_command(plain) == ["clj", "-M:test"]


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def test_python_command_uses_an_absolute_project_interpreter(tmp_path):
    project = tmp_path / "proj"
    dot = project / ".venv" / "bin" / "python"
    _executable(dot)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(project) == [str(dot.absolute()), "-m", "pytest"]

    other = tmp_path / "other"
    plain = other / "venv" / "bin" / "python"
    _executable(plain)
    assert _python_command(other) == [str(plain.absolute()), "-m", "unittest", "discover"]

    _executable(other / ".venv" / "bin" / "python")
    assert _python_command(other) == [
        str((other / ".venv" / "bin" / "python").absolute()),
        "-m",
        "unittest",
        "discover",
    ]


def test_python_command_keeps_the_virtualenv_symlink(tmp_path):
    project = tmp_path / "proj"
    target = project / "real-python"
    _executable(target)
    link = project / ".venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(project) == [str(link.absolute()), "-m", "pytest"]


def test_python_command_falls_back_when_the_virtualenv_cannot_run(tmp_path):
    project = tmp_path / "proj"
    binary = project / ".venv" / "bin" / "python"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    assert _python_command(project) == [sys.executable, "-m", "unittest", "discover"]

    missing = tmp_path / "missing"
    missing.mkdir()
    assert _python_command(missing) == [sys.executable, "-m", "unittest", "discover"]


def test_python_commands(tmp_path):
    pytest_dir = tmp_path / "py"
    pytest_dir.mkdir()
    (pytest_dir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert _python_command(pytest_dir)[-2:] == ["-m", "pytest"]

    unit = tmp_path / "unit"
    unit.mkdir()
    assert _python_command(unit)[-3:] == ["-m", "unittest", "discover"]

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "setup.cfg").write_text("[tool:pytest]\n", encoding="utf-8")
    assert _python_command(cfg)[-2:] == ["-m", "pytest"]

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\ndependencies=['pytest']\n", encoding="utf-8")
    assert _python_command(project)[-2:] == ["-m", "pytest"]

    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "conftest.py").write_text("", encoding="utf-8")
    assert _python_command(conf)[-2:] == ["-m", "pytest"]


def test_a_go_package_path_is_one_argument(tmp_path):
    root = tmp_path / "proj"
    package = root / "a;echo no"
    package.mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    source = package / "widget.go"
    source.write_text("package main\nfunc Run() int { return 1 }\n", encoding="utf-8")
    command, directory = plan_command(root, source, "go", None)
    assert command == ["go", "test", "-count=1", "./a;echo no"]
    assert directory == root
    echoed = CommandRunner().run(["/bin/echo", command[-1]], directory, None)
    assert echoed.code == 0
    assert echoed.output.strip() == "./a;echo no"

    spaced = root / "my dir" / "widget.go"
    spaced.parent.mkdir()
    spaced.write_text("package main\nfunc Run() int { return 1 }\n", encoding="utf-8")
    command, _directory = plan_command(root, spaced, "go", None)
    assert command == ["go", "test", "-count=1", "./my dir"]
    override, override_dir = plan_command(root, source, "go", "go test ./...")
    assert override == "go test ./..."
    assert override_dir == root.resolve()


def test_a_worker_overlay_is_imported_ahead_of_the_environment(tmp_path):
    worker = tmp_path / "target" / "mutation-workers" / "run-1" / "worker-0"
    (worker / "src").mkdir(parents=True)
    (worker / "src" / "demo.py").write_text("VALUE = 'worker'\n", encoding="utf-8")
    command = f"{sys.executable} -c 'import demo; print(demo.VALUE)'"
    result = CommandRunner().run(command, worker, 5)
    assert result.code == 0
    assert result.output.strip() == "worker"


# Touch every page, or the zero-filled allocation never counts toward the footprint.
ALLOCATE = "import time; x = bytearray({mb} << 20); x[::4096] = b'1' * len(x[::4096]); time.sleep({sleep})"
needs_footprint = pytest.mark.skipif(
    group_footprint(os.getpgrp()) is None, reason="process group memory is not readable here"
)


@needs_footprint
def test_a_run_records_the_peak_memory_of_its_whole_process_group(tmp_path):
    grandchild = ALLOCATE.format(mb=200, sleep=1)
    code = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {grandchild!r}])"
    result = CommandRunner().run([sys.executable, "-c", code], tmp_path, 30)
    assert result.code == 0
    assert result.peak_bytes >= 150 << 20
    assert result.out_of_memory is False


@needs_footprint
def test_a_run_over_its_memory_limit_is_killed_early(tmp_path):
    started = time.monotonic()
    command = [sys.executable, "-c", ALLOCATE.format(mb=400, sleep=30)]
    result = CommandRunner().run(command, tmp_path, 60, memory_limit=100 << 20)
    assert result.out_of_memory is True
    assert result.timed_out is False
    assert result.code == 137
    assert result.peak_bytes > 100 << 20
    assert time.monotonic() - started < 10


def test_a_run_under_its_memory_limit_finishes_normally(tmp_path):
    result = CommandRunner().run([sys.executable, "-c", "print('ok')"], tmp_path, 30, memory_limit=1 << 30)
    assert result.code == 0
    assert result.out_of_memory is False
    assert result.output.strip() == "ok"


def test_linux_footprint_sums_only_the_group_from_proc(tmp_path):
    def entry(name, stat, statm):
        directory = tmp_path / name
        directory.mkdir()
        if stat is not None:
            (directory / "stat").write_text(stat)
        (directory / "statm").write_text(statm)

    entry("10", "10 (go) S 1 7 7 0", "900 3 0")
    entry("11", "11 (other) S 1 8 8 0", "900 50 0")
    entry("12", "12 (a ) b) S 10 7 7 0", "900 2 0")  # a name holding ") "
    entry("13", None, "900 40 0")  # exited between the listing and the read
    entry("self", "1 (x) S 1 7 7 0", "900 99 0")
    assert _linux_footprint(7, tmp_path) == 5 * os.sysconf("SC_PAGE_SIZE")


def test_stopping_a_group_that_already_exited_keeps_its_output(monkeypatch):
    class Exited:
        pid = 4321

        def communicate(self):
            return "done", None

    def killpg(_group, _signal):
        raise PermissionError(1, "Operation not permitted")  # macOS, for a group of zombies

    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    assert _stop(Exited(), 124, "timeout", 7) == ("done", 124, "timeout", 7)


def test_a_missing_memory_reading_keeps_the_peak_and_is_never_over_the_limit():
    assert _higher(None, None) is None
    assert _higher(None, 5) == 5
    assert _higher(7, None) == 7
    assert _higher(7, 5) == 7
    assert not _over(None, 10)
    assert not _over(11, None)
    assert not _over(10, 10)
    assert _over(11, 10)


def test_no_timeout_means_no_deadline():
    assert _seconds_left(time.monotonic(), None) == float("inf")
    assert _seconds_left(time.monotonic() - 5, 2) < 0


def _wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.05)
    return condition()


def test_stop_all_kills_a_running_command_and_starts_no_new_one(tmp_path):
    result = {}
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    thread = threading.Thread(target=lambda: result.update(run=CommandRunner().run(command, tmp_path, 60)))
    thread.start()
    try:
        assert _wait_until(lambda: runner_module._LIVE_GROUPS)
        stop_all()
        thread.join(10)
        assert not thread.is_alive()
        assert result["run"].code != 0
        later = CommandRunner().run([sys.executable, "-c", "print('late')"], tmp_path, 10)
        assert later.code == 130
        assert later.output == "stopped"
    finally:
        STOP.clear()


def _mutator_script(tmp_path, body, before=""):
    """Run `body` in a fresh Python with the signal handlers installed, as main() does."""

    script = before + "from pathlib import Path\nfrom mutator.cli import _stop_on_signals\n_stop_on_signals()\n" + body
    source_root = str(Path(runner_module.__file__).resolve().parents[1])
    environment = {**os.environ, "PYTHONPATH": source_root}
    return subprocess.Popen([sys.executable, "-c", script], cwd=tmp_path, env=environment)


@pytest.mark.parametrize("signum, exit_code", [(signal.SIGTERM, 128 + signal.SIGTERM), (signal.SIGINT, -signal.SIGINT)])
def test_a_signal_to_mutator_kills_the_running_command_in_its_own_session(tmp_path, signum, exit_code):
    pidfile = tmp_path / "pid"
    body = (
        "from mutator.runner import CommandRunner\n"
        f"CommandRunner().run('echo $$ > {pidfile}; exec sleep 30', Path('.'), None)\n"  # a baseline: no timeout
    )
    process = _mutator_script(tmp_path, body)
    try:
        assert _wait_until(lambda: pidfile.exists() and pidfile.read_text().strip())
        child = int(pidfile.read_text())
        process.send_signal(signum)
        assert process.wait(timeout=10) == exit_code
        assert _wait_until(lambda: not _alive(child), seconds=5)
    finally:
        process.kill()


def test_ctrl_c_stops_the_worker_pool_without_starting_more_mutants(tmp_path):
    log = tmp_path / "log"
    body = (
        "from types import SimpleNamespace\n"
        "from mutator.runner import CommandRunner\n"
        "from mutator.workers import _run_all\n"
        "work = Path('.').resolve()\n"
        "dirs = []\n"
        "for name in ('w0', 'w1'):\n"
        "    (work / name).mkdir()\n"
        "    (work / name / 'x.py').write_text('x')\n"
        "    dirs.append(work / name)\n"
        "sites = [SimpleNamespace(start=0, end=1, original='x', mutant='y', line=1, description=f'm{i}',\n"
        "                         mutation_id=f'm{i}') for i in range(4)]\n"
        f"command = 'echo $$ >> {log}; exec sleep 30'\n"
        "_run_all(dirs, sites, {}, work, 'x.py', b'x', CommandRunner(), command, work, 60, 'x.py', None)\n"
    )
    process = _mutator_script(tmp_path, body)
    try:
        assert _wait_until(lambda: log.exists() and len(log.read_text().split()) == 2)
        children = [int(pid) for pid in log.read_text().split()]
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == -signal.SIGINT
        assert _wait_until(lambda: not any(_alive(child) for child in children), seconds=5)
        assert len(log.read_text().split()) == 2  # the two queued mutants never started
    finally:
        process.kill()


def test_stop_all_runs_in_a_thread_that_holds_the_registry(tmp_path):
    body = "from mutator.runner import _LIVE_LOCK, stop_all\nwith _LIVE_LOCK:\n    stop_all()\n"
    process = _mutator_script(tmp_path, body)  # a signal handler runs in the thread run() registers from
    try:
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()


def test_an_ignored_signal_stays_ignored(tmp_path):
    nohup = "import signal\nsignal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
    body = "import os, signal, time\nos.kill(os.getpid(), signal.SIGHUP)\ntime.sleep(0.2)\n"
    process = _mutator_script(tmp_path, body, before=nohup)
    try:
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
