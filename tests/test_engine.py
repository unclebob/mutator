import os
import shutil
import subprocess
import threading
from pathlib import Path

from mutator.engine import _backup, _drop_bytecode, mutate_file, restore_backups, scan_file
from mutator.runner import CommandResult
from mutator.edn import dumps, loads
from mutator.functions import form_digests
from mutator.metrics import snapshot_path
from mutator.workers import create_workers, delete_tree, importers_of, new_run_dir, worker_count


class FakeRunner:
    def __init__(self):
        self.verbose = False
        self.calls = 0
        self._lock = threading.Lock()

    def run(self, command, cwd, timeout):
        with self._lock:
            self.calls += 1
        text = (cwd / "src" / "demo.py").read_text(encoding="utf-8")
        namespace: dict = {}
        exec(text, namespace)
        ok = namespace["add"](1, 2) == 3
        return CommandResult(code=0 if ok else 1, timed_out=False, seconds=0.01, output="")


SOURCE = """def add(a, b):
    if a > 0:
        return a + b
    return a + b
"""


def test_mutants_are_killed_or_kept_and_the_snapshot_is_differential(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    original = path.read_bytes()
    runner = FakeRunner()
    first = mutate_file(
        path,
        tmp_path,
        runner=runner,
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert path.read_bytes() == original
    assert first.baseline_failed is False
    form = first.forms[0]
    assert form.id == "defn/add"
    assert form.namespace == "demo"
    # `>` and the unexecuted `+` survive. The executed `+` is killed. `0` -> `1`
    # still returns 3 for add(1, 2).
    assert form.killed == 1
    assert form.survived == 3
    assert form.sites == 4
    assert form.killed + form.survived + form.uncovered == form.sites
    data = loads(snapshot_path(tmp_path, "demo").read_text(encoding="utf-8"))
    assert data["namespace"] == "demo"
    assert data["forms"][0]["id"] == "defn/add"
    assert data["forms"][0]["killed"] == 1
    assert data["forms"][0]["survived"] == 3
    first_calls = runner.calls

    second = mutate_file(
        path,
        tmp_path,
        runner=runner,
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=False,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert second.forms[0].killed == 1
    assert second.forms[0].survived == 3
    # Baseline, the unmutated worker, and the three survivors.
    # The killed mutant stays in the snapshot and is not run again.
    assert first_calls == 6
    assert runner.calls - first_calls == 5


def test_uncovered_sites_are_not_executed(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    runner = FakeRunner()
    result = mutate_file(
        path,
        tmp_path,
        runner=runner,
        covered_lines=set(),
        ignore_coverage=False,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert runner.calls == 0
    assert result.forms[0].uncovered == 4
    assert result.forms[0].killed == 0
    assert result.forms[0].sites == 4


def test_a_site_inside_a_covered_multi_line_statement_is_run(tmp_path):
    # LCOV marks only the line a statement starts on.
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def add(a, b):\n    return (a\n            + b)\n", encoding="utf-8")
    runner = FakeRunner()
    result = mutate_file(
        path,
        tmp_path,
        runner=runner,
        covered_lines={2},
        ignore_coverage=False,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert result.forms[0].uncovered == 0
    assert result.forms[0].killed == 1


def test_a_failed_baseline_does_not_rewrite_metrics(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")

    class Red:
        verbose = False

        def run(self, command, cwd, timeout):
            return CommandResult(code=1, timed_out=False, seconds=0.01, output="nope")

    result = mutate_file(
        path,
        tmp_path,
        runner=Red(),
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert result.baseline_failed
    assert not snapshot_path(tmp_path, "demo").exists()


def test_worker_count_follows_sites_cores_and_the_requested_cap():
    cores = os.cpu_count() or 1
    assert worker_count(10, None) == min(10, cores)
    assert worker_count(10, 3) == min(3, cores)
    assert worker_count(2, 8) == min(2, cores)


def test_workers_keep_a_private_copy_and_link_the_rest(tmp_path):
    source = tmp_path / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    sibling = tmp_path / "src" / "other.py"
    sibling.write_text("kept = True\n", encoding="utf-8")
    marker = tmp_path / "tests" / "keep.txt"
    marker.parent.mkdir()
    marker.write_text("keep", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    original = source.read_bytes()

    base = new_run_dir(tmp_path)
    try:
        workers = create_workers(base, tmp_path, "src/demo.py", original, 2)
        assert len(workers) == 2
        for worker in workers:
            private = worker / "src" / "demo.py"
            assert private.is_file()
            assert not private.is_symlink()
            assert private.read_bytes() == original
            assert (worker / "src" / "other.py").is_symlink()
            assert (worker / "tests").is_symlink()
            assert (worker / "tests" / "keep.txt").read_text(encoding="utf-8") == "keep"
            assert (worker / "pyproject.toml").is_file()
            assert not (worker / "pyproject.toml").is_symlink()
        (workers[0] / "src" / "demo.py").write_text("changed\n", encoding="utf-8")
        assert workers[1].joinpath("src/demo.py").read_bytes() == original
        assert source.read_bytes() == original
    finally:
        delete_tree(base)

    assert source.read_bytes() == original
    assert sibling.read_text(encoding="utf-8") == "kept = True\n"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not base.exists()


def test_node_imports_the_worker_copy_through_a_relative_specifier(tmp_path):
    source = tmp_path / "src" / "find.mjs"
    source.parent.mkdir()
    source.write_text('export const value = "real"\n', encoding="utf-8")
    beside = tmp_path / "src" / "find.test.mjs"
    beside.write_text('import { value } from "./find.mjs";\nconsole.log(value)\n', encoding="utf-8")
    (tmp_path / "src" / "notes.mjs").write_text("export const notes = 1\n", encoding="utf-8")
    main = tmp_path / "src" / "main.mjs"
    main.write_text('import { value } from "./find.mjs";\nexport const main = value\n', encoding="utf-8")
    (tmp_path / "src" / "app.test.mjs").write_text('import { main } from "./main.mjs";\n', encoding="utf-8")
    distant = tmp_path / "tests" / "use.mjs"
    distant.parent.mkdir()
    distant.write_text('import { value } from "../src/find.mjs";\nconsole.log(value)\n', encoding="utf-8")
    (tmp_path / "tests" / "other.test.mjs").write_text("export const n = 1\n", encoding="utf-8")

    base = new_run_dir(tmp_path)
    try:
        worker = create_workers(base, tmp_path, "src/find.mjs", source.read_bytes(), 1)[0]
        private = worker / "src" / "find.mjs"
        private.write_text('export const value = "worker"\n', encoding="utf-8")
        for relative in ("src/find.test.mjs", "src/main.mjs", "src/app.test.mjs", "tests/use.mjs"):
            copied = worker / relative
            assert copied.is_file()
            assert not copied.is_symlink()
        assert (worker / "src" / "notes.mjs").is_symlink()
        assert not (worker / "tests").is_symlink()
        assert (worker / "tests" / "other.test.mjs").is_symlink()
        if shutil.which("node"):
            for script in ("src/find.test.mjs", "tests/use.mjs"):
                completed = subprocess.run(
                    ["node", script],
                    cwd=worker,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                assert completed.returncode == 0
                assert completed.stdout.strip() == "worker"
    finally:
        delete_tree(base)

    assert beside.read_text(encoding="utf-8").startswith("import")
    assert distant.read_text(encoding="utf-8").startswith("import")
    assert source.read_text(encoding="utf-8") == 'export const value = "real"\n'


def test_a_mutation_run_leaves_the_project_tree_unchanged(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    original = path.read_text(encoding="utf-8")

    class Record:
        verbose = False

        def run(self, command, cwd, timeout):
            if timeout is not None:
                assert "mutation-workers" in cwd.as_posix()
                assert (cwd / "src" / "demo.py").read_text(encoding="utf-8") != original
            return CommandResult(code=0 if timeout is None else 1, timed_out=False, seconds=0.01, output="")

    mutate_file(
        path,
        tmp_path,
        runner=Record(),
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
        max_workers=2,
    )
    assert path.read_text(encoding="utf-8") == original
    assert [item for item in (tmp_path / "target").rglob("*") if item.is_file()] == []


def test_restore_backups_puts_an_interrupted_mutant_back(tmp_path):
    source = tmp_path / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("original\n", encoding="utf-8")
    backup = tmp_path / "target" / "mutator-backup" / "src" / "demo.py"
    backup.parent.mkdir(parents=True)
    backup.write_text("original\n", encoding="utf-8")
    source.write_text("mutated\n", encoding="utf-8")
    restored = restore_backups(tmp_path)
    assert restored == [source]
    assert source.read_text(encoding="utf-8") == "original\n"
    assert not backup.exists()


def test_worker_count_is_one_when_the_processor_count_is_unknown(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert worker_count(4, None) == 1
    assert worker_count(4, 3) == 1


def test_workers_can_be_created_twice_and_under_a_missing_directory(tmp_path):
    source = tmp_path / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    original = source.read_bytes()
    base = tmp_path / "fresh" / "run"
    first = create_workers(base, tmp_path, "src/demo.py", original, 1)
    second = create_workers(base, tmp_path, "src/demo.py", original, 1)
    assert first[0] == second[0]
    assert (second[0] / "src" / "demo.py").read_bytes() == original
    assert source.read_bytes() == original


def test_a_module_is_not_listed_as_its_own_importer(tmp_path):
    source = tmp_path / "src" / "find.mjs"
    source.parent.mkdir()
    source.write_text(
        'import { value } from "./find.mjs";\nexport const value = 1\n',
        encoding="utf-8",
    )
    assert importers_of(tmp_path, "src/find.mjs") == set()


def test_importers_keep_queries_hashes_bare_specifiers_and_nesting(tmp_path):
    source = tmp_path / "src" / "find.mjs"
    source.parent.mkdir()
    source.write_text('export const value = "real"\n', encoding="utf-8")
    plain = tmp_path / "src" / "plain.mjs"
    plain.write_text('import { value } from "./find";\n', encoding="utf-8")
    typed = tmp_path / "src" / "typed.mjs"
    typed.write_text('import { value } from "./find.js";\n', encoding="utf-8")
    query = tmp_path / "src" / "query.mjs"
    query.write_text('import { value } from "./find.mjs?raw";\n', encoding="utf-8")
    hashed = tmp_path / "src" / "hashed.mjs"
    hashed.write_text('import { value } from "./find.mjs#part";\n', encoding="utf-8")
    nested = tmp_path / "src" / "nested" / "use.mjs"
    nested.parent.mkdir()
    nested.write_text('import { value } from "../find.mjs";\n', encoding="utf-8")
    escape = tmp_path / "src" / "escape.mjs"
    escape.write_text('import x from "../../outside.js";\n', encoding="utf-8")
    git_hook = tmp_path / ".git" / "hook.mjs"
    git_hook.parent.mkdir()
    git_hook.write_text('import { value } from "../src/find.mjs";\n', encoding="utf-8")
    original = source.read_bytes()
    base = new_run_dir(tmp_path)
    try:
        worker = create_workers(base, tmp_path, "src/find.mjs", original, 1)[0]
        nested_dir = worker / "src" / "nested"
        assert nested_dir.is_dir() and not nested_dir.is_symlink()
        for relative in (
            "src/plain.mjs",
            "src/typed.mjs",
            "src/query.mjs",
            "src/hashed.mjs",
            "src/nested/use.mjs",
        ):
            copied = worker / relative
            assert copied.is_file()
            assert not copied.is_symlink()
        assert not (worker / ".git").exists()
        assert escape.read_text(encoding="utf-8").startswith("import")
    finally:
        delete_tree(base)
    assert source.read_bytes() == original
    assert nested.read_text(encoding="utf-8").startswith("import")


def test_the_first_backup_creates_a_nested_directory(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_bytes(b"original")
    saved = _backup(tmp_path, path, b"original")
    assert saved == tmp_path / "target" / "mutator-backup" / "src" / "demo.py"
    assert saved.read_bytes() == b"original"
    assert path.read_bytes() == b"original"


def test_dropping_bytecode_ignores_a_pyc_that_vanishes(tmp_path, monkeypatch):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("x", encoding="utf-8")
    missing = path.parent / "__pycache__" / "demo.cpython-314.pyc"
    missing.parent.mkdir()

    def glob(self, pattern):
        if self == missing.parent and pattern.startswith("demo."):
            return [missing]
        return []

    monkeypatch.setattr(Path, "glob", glob)
    _drop_bytecode(path)
    assert not missing.exists()


def test_a_scan_without_coverage_does_not_call_a_site_uncovered(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    path.write_text("def place(x):\n    return x > 0\n", encoding="utf-8")
    text = scan_file(path, tmp_path, covered_lines=None, ignore_coverage=False, lines=None)
    assert "uncovered" not in text
    assert "> -> >=" in text


def test_a_scan_marks_a_function_whose_recorded_text_changed(tmp_path):
    path = tmp_path / "src" / "demo.py"
    path.parent.mkdir()
    source = "def place(x):\n    return x > 0\n"
    path.write_text(source, encoding="utf-8")
    digests = form_digests(source, path, tmp_path)
    (namespace, form_id), _digest = next(iter(digests.items()))
    snapshot = snapshot_path(tmp_path, namespace)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        dumps(
            {
                "namespace": namespace,
                "source": "src/demo.py",
                "forms": [{"id": form_id, "hash": "stale", "file": "src/demo.py"}],
                "outcomes": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    text = scan_file(path, tmp_path, covered_lines=None, ignore_coverage=True, lines=None)
    assert "whose text changed" in text
    assert any(line.startswith("* ") for line in text.splitlines())
