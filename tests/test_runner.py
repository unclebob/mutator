import subprocess
import sys

import mutator.runner as runner_module
from mutator.runner import (
    CommandRunner,
    _clojure_command,
    _interpreter_path,
    _popen_kwargs,
    _python_command,
    _terminate_process_tree,
    nearest,
)
from mutator.runner import test_plan as plan_command
from mutator.workers import delete_tree


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


def test_command_runner_kills_timed_out_process_tree(tmp_path):
    worker = tmp_path / "worker"
    worker.mkdir()
    child = (
        "import subprocess, sys; "
        'proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
        "proc.wait()"
    )
    result = CommandRunner().run([sys.executable, "-c", child], worker, 0.3)
    assert result.timed_out is True
    assert result.code == 124
    assert result.seconds < 5.0
    delete_tree(worker, attempts=1)
    assert not worker.exists()


def test_python_command_finds_windows_scripts_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module.os, "name", "nt")
    project = tmp_path / "proj"
    project.mkdir()
    assert _python_command(project) == [sys.executable, "-m", "unittest", "discover"]

    venv_python = project / "venv" / "Scripts" / "python.exe"
    _executable(venv_python)
    assert _python_command(project) == [str(venv_python.absolute()), "-m", "unittest", "discover"]

    dot_venv_python = project / ".venv" / "Scripts" / "python.exe"
    _executable(dot_venv_python)
    assert _python_command(project) == [str(dot_venv_python.absolute()), "-m", "unittest", "discover"]


def test_process_tree_and_interpreter_branches(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module.os, "name", "nt")
    if not hasattr(runner_module.subprocess, "CREATE_NEW_PROCESS_GROUP"):
        monkeypatch.setattr(runner_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    assert _popen_kwargs() == {"creationflags": runner_module.subprocess.CREATE_NEW_PROCESS_GROUP}

    class FakeProc:
        def __init__(self, poll_result):
            self.pid = 4242
            self._poll_result = poll_result
            self.killed = False

        def poll(self):
            return self._poll_result

        def kill(self):
            self.killed = True

    seen_runs = []
    monkeypatch.setattr(
        runner_module.subprocess,
        "run",
        lambda cmd, **kwargs: seen_runs.append((cmd, kwargs)),
    )
    done_proc = FakeProc(0)
    _terminate_process_tree(done_proc)
    assert seen_runs[0][0] == ["taskkill", "/F", "/T", "/PID", "4242"]
    assert done_proc.killed is False

    def fail_run(*_args, **_kwargs):
        raise OSError("taskkill missing")

    monkeypatch.setattr(runner_module.subprocess, "run", fail_run)
    live_proc = FakeProc(None)
    _terminate_process_tree(live_proc)
    assert live_proc.killed is True

    monkeypatch.setattr(runner_module.os, "name", "posix")
    assert _popen_kwargs() == {"start_new_session": True}
    assert _interpreter_path(tmp_path, ".venv") == (tmp_path / ".venv" / "bin" / "python").absolute()

    monkeypatch.setattr(runner_module.signal, "SIGKILL", 9, raising=False)
    killpg_calls = []
    monkeypatch.setattr(
        runner_module.os,
        "killpg",
        lambda pid, sig: killpg_calls.append((pid, sig)),
        raising=False,
    )
    _terminate_process_tree(live_proc)
    assert killpg_calls == [(4242, 9)]

    def missing_pg(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(runner_module.os, "killpg", missing_pg, raising=False)
    _terminate_process_tree(live_proc)
