import subprocess
import sys

import mutator.cli as cli_module
from mutator.cli import _coverage_for, parse_args, run
from mutator.crapper_link import ensure_crapper
from mutator.runner import CommandResult


def test_lines_option_parses_positive_numbers():
    options = parse_args(["--lines", "2, 4", "src/demo.py"])
    assert options.lines == {2, 4}


def test_changed_selects_a_dirty_source_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "src/demo.py"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--changed", "--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "demo.py:2" in captured.out


def test_a_directory_argument_skips_a_named_test_file(tmp_path, capsys):
    src = tmp_path / "pkg" / "app.py"
    src.parent.mkdir()
    src.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    test = tmp_path / "pkg" / "test_app.py"
    test.write_text("def test_place():\n    assert True\n", encoding="utf-8")
    code = run(
        ["--scan", "--no-coverage", "--root", str(tmp_path), str(tmp_path / "pkg"), str(test)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Skipping test file" in captured.err
    assert "app.py:2" in captured.out


def test_help_and_conflicting_flags():
    help_options = parse_args(["--help"])
    assert help_options.exit_code == 0
    assert "uml-viewer" in help_options.message
    assert "__pycache__" in help_options.message
    assert "testdata" in help_options.message
    assert "skipped at any depth" in help_options.message
    assert "src/test" in help_options.message
    assert "first component" in help_options.message
    conflict = parse_args(["--mutate-all", "--since-last-run", "src/demo.py"])
    assert conflict.exit_code == 1
    assert run(["--help"]) == 0


def test_scan_lists_sites_without_writing_metrics(tmp_path, monkeypatch):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--root", str(tmp_path), "src/demo.py"])
    assert code == 0
    assert not (tmp_path / ".metrics").exists()


def test_real_python_mutants_update_the_snapshot(tmp_path, capsys):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text(
        "def add(a, b):\n"
        "    if a > 0:\n"
        "        return a + b\n"
        "    return 0\n",
        encoding="utf-8",
    )
    command = (
        f"{sys.executable} -c \"import sys; sys.path.insert(0, 'src'); "
        "from demo import add; raise SystemExit(0 if add(2, 3) == 5 else 1)\""
    )
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(tmp_path),
            "--test-command",
            command,
            "src/demo.py",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "SURVIVED" in captured.out
    assert "KILLED" in captured.out
    snapshot = tmp_path / ".metrics" / "mutate" / "demo.edn"
    assert snapshot.is_file()
    text = snapshot.read_text(encoding="utf-8")
    assert ":namespace \"demo\"" in text
    assert ":id \"defn/add\"" in text
    assert path.read_text(encoding="utf-8").startswith("def add")


def test_a_failed_coverage_command_does_not_read_the_report(tmp_path, capsys):
    path = tmp_path / "src" / "app.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    report = tmp_path / "target" / "coverage" / "python" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text("SF:src/app.py\nDA:1,1\nDA:2,1\nend_of_record\n", encoding="utf-8")
    code = run(
        [
            "--root",
            str(tmp_path),
            "--coverage-command",
            "exit 3",
            "--test-command",
            "false",
            "src/app.py",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "will not be read" in captured.err
    assert "Baseline failed" not in captured.err
    assert path.read_text(encoding="utf-8").startswith("def place")


def test_cli_scan_and_mutate_cache_coverage_and_history_once(tmp_path, monkeypatch, capsys):
    crapper = ensure_crapper()
    a = tmp_path / "src" / "a.py"
    b = tmp_path / "src" / "b.py"
    a.parent.mkdir()
    a.write_text("def fa(x):\n    return x > 0\n", encoding="utf-8")
    b.write_text("def fb(x):\n    return x > 0\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("hi\n", encoding="utf-8")

    lcov = tmp_path / "target" / "coverage" / "python" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text(
        "SF:src/a.py\nDA:2,1\nend_of_record\n"
        "SF:src/b.py\nDA:2,1\nend_of_record\n",
        encoding="utf-8",
    )

    real_bundle = crapper.coverage.load_bundle(tmp_path)
    monkeypatch.delattr(crapper.coverage, "load_lcov", raising=False)
    orig_history = cli_module.load_all_history
    bundle_calls = []
    history_calls = []

    def counting_bundle(root):
        bundle_calls.append(root)
        return real_bundle

    def counting_history(root):
        history_calls.append(root)
        return orig_history(root)

    monkeypatch.setattr(crapper.coverage, "load_bundle", counting_bundle)
    monkeypatch.setattr(cli_module, "load_all_history", counting_history)

    assert _coverage_for(parse_args(["--root", str(tmp_path)]), tmp_path, readme) is None
    assert run(["--scan", "--use-existing-coverage", "--root", str(tmp_path), str(a), str(b)]) == 0
    captured = capsys.readouterr()
    assert "a.py:2" in captured.out
    assert "b.py:2" in captured.out
    assert len(bundle_calls) == 1
    assert len(history_calls) == 1

    bundle_calls.clear()
    history_calls.clear()
    command = f"{sys.executable} -c \"raise SystemExit(0)\""
    code = run(
        [
            "--use-existing-coverage",
            "--mutate-all",
            "--root",
            str(tmp_path),
            "--test-command",
            command,
            str(a),
            str(b),
        ]
    )
    capsys.readouterr()
    assert code == 3
    assert len(bundle_calls) == 1
    assert len(history_calls) == 1
