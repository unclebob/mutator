import errno
from pathlib import Path

import pytest

from mutator.workers import delete_tree, importers_of


def test_delete_tree_retries_when_a_directory_gains_a_file(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    (tree / "old-report.xml").write_text("done", encoding="utf-8")
    injected = {"count": 0}
    original_rmdir = Path.rmdir

    def rmdir(self):
        if self == tree and injected["count"] == 0:
            injected["count"] += 1
            (self / "late-report.xml").write_text("flushed", encoding="utf-8")
            raise OSError(errno.ENOTEMPTY, "Directory not empty")
        return original_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", rmdir)
    delete_tree(tree, attempts=3, sleep=lambda _: None)
    assert not tree.exists()
    assert injected["count"] == 1


def test_delete_tree_tolerates_a_file_that_vanishes(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    vanishing = tree / "report.xml"
    vanishing.write_text("temp", encoding="utf-8")
    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == vanishing and vanishing.exists():
            original_unlink(self)
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    delete_tree(tree, attempts=1, sleep=lambda _: None)
    assert not tree.exists()


def test_delete_tree_ignores_a_missing_path(tmp_path):
    missing = tmp_path / "already-gone"
    delete_tree(missing, attempts=1, sleep=lambda _: None)
    assert not missing.exists()


def test_delete_tree_removes_a_regular_file(tmp_path):
    path = tmp_path / "worker-file"
    path.write_text("x", encoding="utf-8")
    delete_tree(path, attempts=1, sleep=lambda _: None)
    assert not path.exists()


def test_delete_tree_unlinks_a_symlink_without_following_it(tmp_path):
    real = tmp_path / "project"
    real.mkdir()
    kept = real / "important.txt"
    kept.write_text("keep", encoding="utf-8")
    tree = tmp_path / "worker"
    tree.mkdir()
    (tree / "src").symlink_to(real)
    link_root = tmp_path / "worker-link"
    link_root.symlink_to(real)
    delete_tree(tree, attempts=1, sleep=lambda _: None)
    delete_tree(link_root, attempts=1, sleep=lambda _: None)
    assert not tree.exists()
    assert not link_root.exists()
    assert kept.read_text(encoding="utf-8") == "keep"


def test_delete_tree_reraises_after_the_attempts_are_exhausted(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    (tree / "stuck.txt").write_text("x", encoding="utf-8")

    def rmdir(self):
        raise OSError(errno.ENOTEMPTY, "Directory not empty")

    monkeypatch.setattr(Path, "rmdir", rmdir)
    sleeps: list[float] = []
    with pytest.raises(OSError) as caught:
        delete_tree(tree, attempts=3, sleep=sleeps.append)
    assert caught.value.errno == errno.ENOTEMPTY
    assert sleeps == [0.25, 0.5]
    assert tree.exists()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_importers_of_follows_extensionless_and_index_imports(tmp_path):
    _write(tmp_path / "src" / "target.ts", "export const x = 1;\n")
    _write(tmp_path / "src" / "direct.ts", "import { x } from './target';\n")
    _write(tmp_path / "src" / "lib" / "index.ts", "export * from '../direct';\n")
    _write(tmp_path / "src" / "outer.ts", "import './lib';\n")
    _write(tmp_path / "src" / "climbing.ts", "import './gone/../target';\n")
    _write(tmp_path / "src" / "unrelated.ts", "import './missing';\n")
    assert importers_of(tmp_path, "src/target.ts") == {
        "src/climbing.ts",
        "src/direct.ts",
        "src/lib/index.ts",
        "src/outer.ts",
    }


def test_importers_of_resolves_only_candidates_that_exist(tmp_path, monkeypatch):
    _write(tmp_path / "src" / "target.ts", "export const x = 1;\n")
    _write(tmp_path / "src" / "user.ts", "import { x } from './target';\n")
    resolved = []
    original_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        resolved.append(self)
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    importers_of(tmp_path, "src/target.ts")
    assert not [path for path in resolved if not path.exists()]
