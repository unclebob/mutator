"""Mutator skips root test trees and src/test, not a deeper test directory."""

import subprocess
from pathlib import Path

from mutator.cli import _comma_and, parse_args, select_files
from mutator.crapper_link import ensure_crapper
from mutator.sources import is_mutable_source, is_skipped_test, is_test_path, iter_source_files


SOURCE = "def place(x):\n    return x > 0\n"
JAVA = "class Foo { boolean ok() { return true; } }\n"


def _write(root: Path, relative: str, body: str | None = None) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = JAVA if path.suffix == ".java" else SOURCE
    path.write_text(body, encoding="utf-8")
    return path.resolve()


def _chosen(root: Path, *args: str) -> set[str]:
    files = select_files(parse_args(["--root", str(root), *args]))
    return {path.relative_to(root).as_posix() for path in files}


CASES = {
    "test/Foo.java": False,
    "tests/foo.py": False,
    "spec/foo.py": False,
    "specs/foo.py": False,
    "__tests__/foo.py": False,
    "src/test/java/com/example/Foo.java": False,
    "src/tests/foo.py": False,
    "src/agnesse/slim/test/SymbolManagingTableTable.java": True,
    "src/foo/tests/bar.py": True,
    "src/pkg/spec/foo.py": True,
    "src/demo.py": True,
    "lib/test/deep.py": True,
    "src/pkg/node_modules/foo.py": False,
    "target/generated/Foo.java": False,
    "build/out.py": False,
    "src/pkg/foo_test.go": False,
    "src/pkg/test_foo.py": False,
    "src/pkg/foo_test.py": False,
    "src/pkg/foo.test.ts": False,
    "src/pkg/foo.spec.ts": False,
    "src/pkg/conftest.py": False,
}


def _tree(root: Path) -> None:
    for relative in CASES:
        _write(root, relative)


def test_walk_skips_root_and_src_test_trees_but_mutates_a_deeper_test_directory(tmp_path):
    _tree(tmp_path)
    assert _chosen(tmp_path) == {relative for relative, keep in CASES.items() if keep}


def test_changed_files_use_the_same_test_path_rule(tmp_path):
    _tree(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    assert _chosen(tmp_path, "--changed") == {relative for relative, keep in CASES.items() if keep}


def test_an_explicit_deep_test_file_is_mutated_and_a_root_test_file_is_skipped(tmp_path, capsys):
    fixture = _write(tmp_path, "src/agnesse/slim/test/SymbolManagingTableTable.java")
    root_test = _write(tmp_path, "test/Foo.java")
    maven = _write(tmp_path, "src/test/java/com/example/Foo.java")
    chosen = select_files(
        parse_args(
            [
                "--root",
                str(tmp_path),
                str(fixture),
                str(root_test),
                str(maven),
            ]
        )
    )
    captured = capsys.readouterr()
    assert chosen == [fixture]
    assert "Skipping test file" in captured.err
    assert "test/Foo.java" in captured.err
    assert "src/test/java" in captured.err


def test_a_deep_test_directory_argument_is_walked_and_a_root_test_directory_is_not(tmp_path):
    fixture = _write(tmp_path, "src/agnesse/slim/test/SymbolManagingTableTable.java")
    sibling = _write(tmp_path, "src/agnesse/slim/test/Other.java")
    _write(tmp_path, "test/Foo.java")
    _write(tmp_path, "src/demo.py")
    deep = _chosen(tmp_path, "src/agnesse/slim/test")
    assert deep == {
        "src/agnesse/slim/test/SymbolManagingTableTable.java",
        "src/agnesse/slim/test/Other.java",
    }
    assert fixture.relative_to(tmp_path).as_posix() in deep
    assert sibling.relative_to(tmp_path).as_posix() in deep
    assert _chosen(tmp_path, "test") == set()


def test_source_root_walks_a_deep_test_directory(tmp_path):
    _write(tmp_path, "src/agnesse/slim/test/SymbolManagingTableTable.java")
    _write(tmp_path, "test/Foo.java")
    _write(tmp_path, "src/demo.py")
    assert _chosen(tmp_path, "--source-root", "src/agnesse/slim") == {
        "src/agnesse/slim/test/SymbolManagingTableTable.java"
    }


def test_an_empty_relative_path_is_not_a_test_path():
    assert is_test_path(Path(""), {"test"}) is False


def test_a_path_outside_the_project_is_not_mutable(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = _write(tmp_path, "outside.py")
    crapper = ensure_crapper()
    assert is_mutable_source(outside, root, crapper) is False
    assert is_skipped_test(tmp_path / "note.txt", root, crapper) is False


def test_iter_source_files_accepts_a_file_and_ignores_a_missing_path(tmp_path):
    kept = _write(tmp_path, "src/demo.py")
    crapper = ensure_crapper()
    found = iter_source_files([kept, tmp_path / "missing"], tmp_path, crapper)
    assert found == [kept]


def test_comma_and_names_a_single_item():
    assert _comma_and({"only"}) == "only"


def test_src_test_itself_is_a_test_path():
    test_dirs = {"test", "tests"}
    assert is_test_path(Path("src/test"), test_dirs) is True
    assert is_test_path(Path("src/tests"), test_dirs) is True
    assert is_test_path(Path("src"), test_dirs) is False


def test_an_explicit_skip_dir_file_is_not_reported_as_a_test(tmp_path, capsys):
    build = _write(tmp_path, "target/generated/Foo.java")
    chosen = select_files(parse_args(["--root", str(tmp_path), str(build)]))
    assert chosen == []
    assert "Skipping test file" not in capsys.readouterr().err


def test_a_skip_directory_is_not_entered(tmp_path):
    from mutator.sources import _enter

    assert _enter(tmp_path, "target", tmp_path, {"target"}, {"test"}) is False
    assert _enter(tmp_path, "src", tmp_path, {"target"}, {"test"}) is True
    (tmp_path / "src").mkdir()
    assert _enter(tmp_path / "src", "test", tmp_path, {"target"}, {"test", "tests"}) is False
    assert _enter(tmp_path / "src", "main", tmp_path, {"target"}, {"test", "tests"}) is True
