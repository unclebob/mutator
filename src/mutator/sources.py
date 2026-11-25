"""Choose the source files a mutation run will consider.

crapper.discover treats any TEST_DIRS name at any depth as a test path, which
skips production fixtures such as src/agnesse/slim/test/. Mutator therefore
walks the tree itself.

A path relative to the project root is a test path when its first component is
a TEST_DIRS name (test, tests, spec, specs, or __tests__), or when it sits
under src/test or src/tests, the Maven and Gradle test layout. SKIP_DIRS are
skipped at any depth. Filename-based test files are still detected by
crapper.discover.is_test_file, applied to the filename so the any-depth
directory check is not reused.

Swift adds its own names. A directory component that ends with ``Tests``
(``Tests``, ``AppTests``) is a test target at any depth, and so is a file
named ``*Tests.swift``. ``.build``, ``.swiftpm``, and ``DerivedData`` are
crapper SKIP_DIRS.
"""

from __future__ import annotations

import os
from pathlib import Path

SRC_TEST_DIRS = frozenset({"test", "tests"})


def is_test_path(relative: Path, test_dirs: set[str]) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in test_dirs:
        return True
    return len(parts) >= 2 and parts[0] == "src" and parts[1] in SRC_TEST_DIRS


def _swift_test_relative(relative: Path) -> bool:
    """Swift test targets are ``Tests``, ``*Tests``, or ``*Tests.swift``.

    ``test`` at any depth stays a production path. Swift's name is ``Tests``.
    """

    if relative.suffix != ".swift":
        return False
    if relative.name.endswith("Tests.swift"):
        return True
    return any(part.endswith("Tests") for part in relative.parts[:-1])


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def is_mutable_source(path: Path, root: Path, crapper) -> bool:
    if crapper.discover.language_of(path) is None:
        return False
    relative = _relative_to(path, root)
    if relative is None:
        return False
    if not set(crapper.discover.SKIP_DIRS).isdisjoint(relative.parts):
        return False
    if is_test_path(relative, set(crapper.discover.TEST_DIRS)):
        return False
    if _swift_test_relative(relative):
        return False
    return not crapper.discover.is_test_file(path.name)


def is_skipped_test(path: Path, root: Path, crapper) -> bool:
    if crapper.discover.language_of(path) is None:
        return False
    relative = _relative_to(path, root)
    if relative is not None and is_test_path(relative, set(crapper.discover.TEST_DIRS)):
        return True
    if relative is not None and _swift_test_relative(relative):
        return True
    return crapper.discover.is_test_file(path.name)


def _enter(current: Path, name: str, root: Path, skip_dirs: set[str], test_dirs: set[str]) -> bool:
    if name in skip_dirs:
        return False
    relative = _relative_to(current / name, root)
    return relative is None or not is_test_path(relative, test_dirs)


def iter_source_files(roots: list[Path], project_root: Path, crapper) -> list[Path]:
    skip_dirs = set(crapper.discover.SKIP_DIRS)
    test_dirs = set(crapper.discover.TEST_DIRS)
    project_root = project_root.resolve()
    files: set[Path] = set()
    for start in roots:
        start = start.resolve()
        if start.is_file():
            if is_mutable_source(start, project_root, crapper):
                files.add(start)
            continue
        if not start.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            current = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames if _enter(current, name, project_root, skip_dirs, test_dirs)
            ]
            for name in filenames:
                path = (current / name).resolve()
                if is_mutable_source(path, project_root, crapper):
                    files.add(path)
    return sorted(files, key=lambda path: path.as_posix())
