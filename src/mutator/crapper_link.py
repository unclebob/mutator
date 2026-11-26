"""Import crapper from the environment or the sibling checkout.

Function names and namespaces come from crapper so `.metrics/mutate` joins
the same operations uml-viewer joins to `.metrics/crap.edn`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _sibling_src() -> Path:
    project = Path(__file__).resolve().parents[2]
    return project.parent / "crapper" / "src"


def _clear_crapper_modules() -> None:
    for name in [key for key in sys.modules if key == "crapper" or key.startswith("crapper.")]:
        del sys.modules[name]


def _import_crapper_core():
    import crapper
    import crapper.coverage

    return crapper


def ensure_crapper():
    try:
        crapper = _import_crapper_core()
    except ImportError:
        _clear_crapper_modules()
        importlib.invalidate_caches()
        sibling = _sibling_src()
        if not (sibling / "crapper").is_dir():
            raise ImportError(
                "crapper is not installed and was not found at "
                f"{sibling}. Clone github.com/unclebob/crapper next to mutator."
            ) from None
        sibling_text = str(sibling)
        if sys.path[:1] != [sibling_text]:
            sys.path.insert(0, sibling_text)
        crapper = _import_crapper_core()
    import crapper.discover
    import crapper.languages
    import crapper.languages.treesitter
    import crapper.runners

    return crapper
