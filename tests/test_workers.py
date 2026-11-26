import errno
from pathlib import Path

import pytest

from mutator.workers import delete_tree


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


def test_delete_tree_unlinks_a_symlink_that_vanishes(tmp_path, monkeypatch):
    real = tmp_path / "project"
    real.mkdir()
    kept = real / "important.txt"
    kept.write_text("keep", encoding="utf-8")
    link = tmp_path / "worker-link"
    link.symlink_to(real)
    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == link and link.is_symlink():
            original_unlink(self)
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    delete_tree(link, attempts=1, sleep=lambda _: None)
    assert not link.exists()
    assert kept.read_text(encoding="utf-8") == "keep"


def test_symlink_replaces_a_file_and_creates_a_missing_parent(tmp_path):
    from mutator.workers import symlink

    target = tmp_path / "real.txt"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "nested" / "link.txt"
    link.parent.mkdir()
    link.write_text("old", encoding="utf-8")
    symlink(link, target)
    other = tmp_path / "deep" / "child" / "link.txt"
    symlink(other, target)
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "keep"
    assert other.is_symlink()
    assert other.read_text(encoding="utf-8") == "keep"
    assert target.read_text(encoding="utf-8") == "keep"


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
