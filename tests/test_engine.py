import os
import shutil
import subprocess
import threading

import pytest

from mutator.engine import mutate_file, restore_backups
from mutator.edn import loads
from mutator.metrics import snapshot_path
from mutator.runner import CommandResult, CommandRunner
from mutator.workers import create_workers, delete_tree, new_run_dir, worker_count


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
    # Baseline plus the three survivors. The killed mutant stays in the snapshot.
    assert runner.calls - first_calls == 4


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


# Composer's generated files find the project from their own directory, and
# PHP resolves symlinks in __DIR__. These are the shapes Composer writes.
COMPOSER_FILES = {
    "composer.json": '{"autoload": {"psr-4": {"App\\\\": "app/"}}}\n',
    "phpunit.xml": "<phpunit/>\n",
    "vendor/autoload.php": "<?php\nrequire_once __DIR__ . '/composer/autoload_real.php';\n",
    "vendor/composer/autoload_psr4.php": (
        "<?php\n"
        "$vendorDir = dirname(__DIR__);\n"
        "$baseDir = dirname($vendorDir);\n"
        "return array('App\\\\' => array($baseDir . '/app'));\n"
    ),
    "vendor/composer/autoload_real.php": (
        "<?php\n"
        "foreach (require __DIR__ . '/autoload_psr4.php' as $prefix => $dirs) {\n"
        "    spl_autoload_register(function ($class) use ($prefix, $dirs) {\n"
        "        if (strncmp($class, $prefix, strlen($prefix)) === 0) {\n"
        "            require $dirs[0] . '/' . str_replace('\\\\', '/', substr($class, strlen($prefix))) . '.php';\n"
        "        }\n"
        "    });\n"
        "}\n"
    ),
    "vendor/bin/phpunit": (
        "#!/usr/bin/env php\n"
        "<?php\n"
        "$GLOBALS['_composer_autoload_path'] = __DIR__ . '/..'.'/autoload.php';\n"
        "include __DIR__ . '/..'.'/phpunit/phpunit/phpunit';\n"
    ),
    "vendor/phpunit/phpunit/phpunit": (
        "<?php\n"
        "require $GLOBALS['_composer_autoload_path'];\n"
        "exit(require getcwd() . '/tests/BoardTest.php');\n"
    ),
    "tests/BoardTest.php": (
        "<?php\n"
        "use App\\Board;\n"
        "return Board::positive(1) === true && Board::positive(0) === false ? 0 : 1;\n"
    ),
    "app/Board.php": (
        "<?php\n"
        "namespace App;\n"
        "\n"
        "class Board\n"
        "{\n"
        "    public static function positive($x)\n"
        "    {\n"
        "        return $x > 0;\n"
        "    }\n"
        "\n"
        "    public static function label()\n"
        "    {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    ),
}


def _composer_project(root):
    for relative, text in COMPOSER_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "vendor" / "bin" / "phpunit").chmod(0o755)
    return root / "app" / "Board.php"


def test_composer_autoload_files_are_real_files_in_the_worker(tmp_path):
    source = _composer_project(tmp_path)
    (tmp_path / ".phpunit.cache").mkdir()
    base = new_run_dir(tmp_path)
    try:
        worker = create_workers(base, tmp_path, "app/Board.php", source.read_bytes(), 1)[0]
        for relative in (
            "phpunit.xml",
            "vendor/autoload.php",
            "vendor/composer/autoload_psr4.php",
            "vendor/composer/autoload_real.php",
            "vendor/bin/phpunit",
        ):
            parts = (worker / relative).relative_to(worker).parts
            for index in range(1, len(parts) + 1):
                assert not worker.joinpath(*parts[:index]).is_symlink(), relative
        assert os.access(worker / "vendor" / "bin" / "phpunit", os.X_OK)
        assert (worker / "vendor" / "phpunit").is_symlink()
        assert (worker / "tests").is_symlink()
        assert not (worker / ".phpunit.cache").exists()
    finally:
        delete_tree(base)
    assert (tmp_path / "vendor" / "composer" / "autoload_real.php").is_file()


@pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed")
def test_a_php_mutant_is_killed_through_the_composer_autoloader(tmp_path):
    source = _composer_project(tmp_path)
    original = source.read_bytes()
    result = mutate_file(
        source,
        tmp_path,
        runner=CommandRunner(),
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command=None,
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
        max_workers=2,
    )
    assert result.baseline_failed is False
    assert {(form.name, form.killed, form.survived) for form in result.forms} == {
        ("positive", 2, 0),
        ("label", 0, 1),
    }
    assert source.read_bytes() == original


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
