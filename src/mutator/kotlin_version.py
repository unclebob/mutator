from __future__ import annotations

import re
from pathlib import Path

_FILES = (
    "build.gradle.kts",
    "build.gradle",
    "settings.gradle.kts",
    "settings.gradle",
    "gradle.properties",
    "gradle/libs.versions.toml",
    "pom.xml",
)

_VERSION = r"""["']?(\d+)\.(\d+)"""

_PATTERNS = [
    re.compile(r"""(?:kotlin\(\s*"[\w-]+"\s*\)|["']org\.jetbrains\.kotlin\.[\w.-]+["'])\s*version\s*""" + _VERSION),
    re.compile(r"kotlin[_.-]?version\s*[=:>]\s*" + _VERSION, re.IGNORECASE),
    re.compile(r"kotlin-gradle-plugin:" + _VERSION),
    re.compile(r"kotlin-maven-plugin</artifactId>\s*<version>\s*" + _VERSION),
    re.compile(r"^\s*kotlin\s*=\s*" + _VERSION, re.MULTILINE),
]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _version_in(text: str) -> tuple[int, int] | None:
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _directories(source: Path, root: Path):
    root = root.resolve()
    current = source.resolve().parent
    while True:
        yield current
        if current == root or current.parent == current:
            return
        current = current.parent


def project_version(source: Path, root: Path) -> tuple[int, int] | None:
    for directory in _directories(source, root):
        for name in _FILES:
            found = _version_in(_read(directory / name))
            if found is not None:
                return found
    return None


def before_1_9(source: Path, root: Path) -> bool:
    version = project_version(source, root)
    return version is not None and version < (1, 9)
