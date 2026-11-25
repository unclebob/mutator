"""PHP: the classes a file declares and the tests that name them.

A mutated PHP file runs only the `*Test.php` files under the project's
`tests/` directory that name one of its classes: a `use` import (aliased or
not), the fully-qualified name anywhere, or a grouped `use Prefix\\{...}`
that expands to it. A file no test names has no command, and its sites are
reported as uncovered instead of running the whole suite.

A worker links the project, and PHP resolves links: `__DIR__` in the linked
`vendor/` is the real project, so Composer would load the original class.
Each worker therefore gets `.mutator-php/prepend.php`, which requires the
real `vendor/autoload.php` and adds a class-map entry pointing each class at
the worker's mutated copy. Composer caches its loader and checks the class
map first, and a later entry replaces an optimized one. The mutant command
runs PHP with `-d auto_prepend_file=` that file.

The tests run in one PHP process (Pest, else PHPUnit), never through
`artisan test`, which starts a second PHP process that would not inherit the
`-d` flag. A string `--test-command` cannot be rewritten; it gets the file's
path in `MUTATOR_PHP_PREPEND` instead.
"""

from __future__ import annotations

import os
import re
import shlex
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
_PREPEND = Path(".mutator-php") / "prepend.php"
_PREPEND_VARIABLE = "MUTATOR_PHP_PREPEND"


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


def _literal(text: str) -> str:
    """A PHP single-quoted string."""

    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def prepend_text(autoload: Path, classes: list[str], mutated: Path) -> str:
    entries = ", ".join(f"{_literal(fqcn)} => {_literal(str(mutated))}" for fqcn in classes)
    return (
        "<?php\n"
        f"$mutatorLoader = require {_literal(str(autoload))};\n"
        f"$mutatorLoader->addClassMap([{entries}]);\n"
        "unset($mutatorLoader);\n"
    )


def write_prepend(worker: Path, autoload: Path, mutated: Path, original: bytes) -> None:
    """Point the classes of the mutated file at the worker's copy. Nothing without Composer."""

    classes = declared_classes(original.decode("utf-8"))
    if not autoload.is_file() or not classes:
        return
    target = worker / _PREPEND
    target.parent.mkdir(parents=True, exist_ok=True)
    text = prepend_text(autoload.resolve(), classes, mutated.resolve())
    target.write_text(text, encoding="utf-8")


def _with_prepend(command: list[str], prepend: Path) -> list[str]:
    if "php" not in command:
        return command
    at = command.index("php") + 1
    return [*command[:at], "-d", f"auto_prepend_file={prepend}", *command[at:]]


def worker_command(command, worker: Path):
    """The mutant's command: PHP loads the worker's copy through the prepend file."""

    prepend = (worker / _PREPEND).resolve()
    if not prepend.is_file():
        return command
    if isinstance(command, str):
        return f"export {_PREPEND_VARIABLE}={shlex.quote(str(prepend))}; {command}"
    return _with_prepend(command, prepend)


def warn_override(command) -> None:
    if isinstance(command, str) and _PREPEND_VARIABLE not in command:
        print(
            f"A PHP --test-command must pass -d auto_prepend_file=\"${_PREPEND_VARIABLE}\", "
            "or each mutant runs the original class and survives.",
            file=sys.stderr,
        )
