"""PHP: the classes a file declares and the tests that name them.

A mutated PHP file runs only the `*Test.php` files under the project's
`tests/` directory that name one of its classes: a `use` import (aliased or
not), the fully-qualified name anywhere, or a grouped `use Prefix\\{...}`
that expands to it. A file no test names has no command, and its sites are
reported as uncovered instead of running the whole suite.

The tests run in one PHP process (Pest, else PHPUnit), never through
`artisan test`, which starts a second PHP process that would not inherit the
worker's `-d auto_prepend_file`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from mutator.crapper_link import ensure_crapper

_CLASS_LIKE = {
    "class_declaration",
    "trait_declaration",
    "enum_declaration",
    "interface_declaration",
}
_GROUPED_USE = re.compile(r"\buse\s+(?:function\s+|const\s+)?\\?([\w\\]+?)\\\s*\{([^}]*)\}")
_TEST_SUFFIX = "Test.php"


def _namespace(data: bytes, node, crapper) -> str:
    name = crapper.languages.treesitter.child_of_type(node, "namespace_name")
    if name is None:
        return ""
    return crapper.languages.treesitter.node_text(data, name)


def _qualified(namespace: str, name: str) -> str:
    return f"{namespace}\\{name}" if namespace else name


def declared_classes(source: str) -> list[str]:
    """Fully-qualified names of the named classes, traits, enums, and interfaces."""

    crapper = ensure_crapper()
    treesitter = crapper.languages.treesitter
    data, tree = treesitter.parse(source, "php")
    namespace = ""
    found: list[str] = []
    for node in treesitter.descendants(tree.root_node):
        if node.type == "namespace_definition":
            namespace = _namespace(data, node, crapper)
        elif node.type in _CLASS_LIKE:
            name = treesitter.node_text(data, treesitter.child_of_type(node, "name"))
            found.append(_qualified(namespace, name))
    return found


def _grouped_imports(text: str) -> set[str]:
    """`use App\\Models\\{User, Coupon as C}` imports App\\Models\\User and App\\Models\\Coupon."""

    found = set()
    for match in _GROUPED_USE.finditer(text):
        prefix = match.group(1)
        for item in match.group(2).split(","):
            words = item.split()
            if words:
                found.add(f"{prefix}\\{words[0].lstrip(chr(92))}")
    return found


def _names(text: str, fqcn: str) -> bool:
    pattern = r"(?<![\w\\])\\?" + re.escape(fqcn) + r"(?![\w\\])"
    return re.search(pattern, text) is not None or fqcn in _grouped_imports(text)


def _test_files(project: Path):
    for dirpath, _dirnames, filenames in os.walk(project / "tests"):
        for name in filenames:
            if name.endswith(_TEST_SUFFIX):
                yield Path(dirpath) / name


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def naming_tests(project: Path, classes: list[str]) -> list[str]:
    """Test files under `project/tests` that name a class, relative to `project` and sorted."""

    found = []
    for path in _test_files(project):
        text = _read(path)
        if any(_names(text, fqcn) for fqcn in classes):
            found.append(path.relative_to(project).as_posix())
    return sorted(found)


def warn_untested(classes: list[str]) -> None:
    names = ", ".join(classes) or "the file's classes"
    print(f"No test under tests/ names {names}; its sites are uncovered.", file=sys.stderr)


def test_command(project: Path, files: list[str]) -> list[str]:
    runner = "vendor/bin/pest" if (project / "vendor" / "bin" / "pest").is_file() else "vendor/bin/phpunit"
    return ["env", "XDEBUG_MODE=off", "php", runner, *files]
