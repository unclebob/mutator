import sys
import types
from pathlib import Path

import pytest

from mutator import crapper_link


@pytest.fixture(autouse=True)
def _isolate_crapper_modules():
    saved = {k: v for k, v in sys.modules.items() if k == "crapper" or k.startswith("crapper.")}
    for key in list(saved):
        sys.modules.pop(key, None)
    yield
    for key in [k for k in sys.modules if k == "crapper" or k.startswith("crapper.")]:
        sys.modules.pop(key, None)
    sys.modules.update(saved)


def _make_fake_sibling(base: Path) -> Path:
    sibling = base / "sibling"
    pkg = sibling / "crapper"
    (pkg / "languages").mkdir(parents=True)
    for rel in (
        "__init__.py",
        "coverage.py",
        "discover.py",
        "runners.py",
        "languages/__init__.py",
        "languages/treesitter.py",
    ):
        (pkg / rel).write_text("", encoding="utf-8")
    return sibling


def test_ensure_crapper_recovers_from_namespace_package_shadowing(tmp_path, monkeypatch):
    shadow_root = tmp_path / "workspace"
    (shadow_root / "crapper").mkdir(parents=True)
    fake_sibling = _make_fake_sibling(tmp_path)
    monkeypatch.setattr(crapper_link, "_sibling_src", lambda: fake_sibling)
    monkeypatch.setattr(sys, "path", [str(shadow_root)])

    loaded = crapper_link.ensure_crapper()

    assert loaded.__file__ is not None
    assert hasattr(loaded, "coverage")
    assert Path(loaded.__file__).resolve().is_relative_to((fake_sibling / "crapper").resolve())


def test_ensure_crapper_clears_preexisting_namespace_module(tmp_path, monkeypatch):
    fake_sibling = _make_fake_sibling(tmp_path)
    sys.modules["crapper"] = types.ModuleType("crapper")
    monkeypatch.setattr(crapper_link, "_sibling_src", lambda: fake_sibling)
    monkeypatch.setattr(sys, "path", [str(fake_sibling)])

    loaded = crapper_link.ensure_crapper()

    assert loaded.__file__ is not None
    assert hasattr(loaded, "coverage")
    assert Path(loaded.__file__).resolve().is_relative_to((fake_sibling / "crapper").resolve())


def test_ensure_crapper_raises_when_sibling_is_missing(tmp_path, monkeypatch):
    (tmp_path / "crapper").mkdir()
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    monkeypatch.setattr(crapper_link, "_sibling_src", lambda: tmp_path / "missing-crapper" / "src")

    with pytest.raises(ImportError, match="Clone github.com/unclebob/crapper next to mutator"):
        crapper_link.ensure_crapper()
