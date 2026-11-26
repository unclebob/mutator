"""Decide which lines the tests already exercise.

Java uses JaCoCo source lines. Go uses statement profiles, which name a
file by its module import path, the way crapper names it. Clojure prefers
Cloverage's per-line form counts, then LCOV. Swift uses the regions crapper
reads from llvm-cov JSON, or an xccov function summary when the report has
no regions. The other languages use LCOV. A file that does not appear in a
report has no coverage data.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from mutator.crapper_link import ensure_crapper


def _candidates(normalize, source_path: str) -> list[str]:
    absolute = normalize(str(Path(source_path).resolve())) if source_path else ""
    candidates = []
    for value in (normalize(source_path), absolute):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _exact(exact: dict[str, Any], candidates: list[str]) -> Any:
    for candidate in candidates:
        if candidate in exact:
            return exact[candidate]
    return None


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.split("/") if part)


def _as_suffix(longer: tuple[str, ...], shorter: tuple[str, ...]) -> int | None:
    """How many extra parts `longer` adds in front of `shorter`, when it ends with it."""

    if len(longer) >= len(shorter) and longer[-len(shorter) :] == shorter:
        return len(longer) - len(shorter)
    return None


def _suffix_rank(key_parts: tuple[str, ...], source: tuple[str, ...]) -> tuple[int, int] | None:
    """Rank `(0, extra)` is a key that ends with the source. `(1, missing)` is the reverse.

    An equal path is rank `(0, 0)`. A shorter key matches only as a suffix of
    a longer source.
    """

    if not source or not key_parts:
        return None
    extra = _as_suffix(key_parts, source)
    if extra is not None:
        return (0, extra)
    missing = _as_suffix(source, key_parts)
    if missing is None:
        return None
    return (1, missing)


def _rank(key_parts: tuple[str, ...], sources: list[tuple[str, ...]]) -> tuple[int, int] | None:
    """The best suffix rank of one report key against the source paths.

    The smaller rank is the longer shared suffix. Two different keys at the
    same rank are ambiguous.
    """

    found: list[tuple[int, int]] = []
    for source in sources:
        rank = _suffix_rank(key_parts, source)
        if rank is not None:
            found.append(rank)
    return min(found) if found else None


def _source_parts(candidates: list[str]) -> list[tuple[str, ...]]:
    return [parts for candidate in candidates if (parts := _parts(candidate))]


def _keep_match(best, best_rank, key_parts, value, rank):
    if best_rank is None or rank < best_rank:
        return rank, [(key_parts, value)]
    if rank == best_rank:
        best.append((key_parts, value))
    return best_rank, best


def _best_value(
    entries: list[tuple[int, tuple[str, ...], Any]],
    sources: list[tuple[str, ...]],
) -> Any:
    best_rank: tuple[int, int] | None = None
    best: list[tuple[tuple[str, ...], Any]] = []
    for _order, key_parts, value in entries:
        rank = _rank(key_parts, sources)
        if rank is None:
            continue
        best_rank, best = _keep_match(best, best_rank, key_parts, value, rank)
    if len({parts for parts, _value in best}) != 1:
        return None
    return best[0][1]


class NormalizedIndex:
    """Normalized report index with O(1) exact and basename-scoped suffix lookup."""

    def __init__(self, index: dict[str, Any], normalize=None) -> None:
        self._normalize = normalize if normalize is not None else ensure_crapper().coverage.normalize_path
        self.exact: dict[str, Any] = {}
        self.by_basename: dict[str, list[tuple[int, tuple[str, ...], Any]]] = {}
        for order, (key, value) in enumerate(index.items()):
            norm = self._normalize(key)
            if norm in self.exact:
                continue
            self.exact[norm] = value
            parts = _parts(norm)
            if parts:
                self.by_basename.setdefault(parts[-1], []).append((order, parts, value))

    def _matching_entries(
        self, sources: list[tuple[str, ...]]
    ) -> list[tuple[int, tuple[str, ...], Any]]:
        basenames = list(dict.fromkeys(parts[-1] for parts in sources))
        if len(basenames) == 1:
            return self.by_basename.get(basenames[0], [])
        entries = [item for base in basenames for item in self.by_basename.get(base, ())]
        entries.sort(key=lambda item: item[0])
        return entries

    def lookup(self, source_path: str, normalize=None, name: str | None = None) -> Any:
        if not self.exact:
            return None
        norm_fn = self._normalize if normalize is None else normalize
        candidates = _candidates(norm_fn, source_path)
        if name:
            candidates.append(name)
        found = _exact(self.exact, candidates)
        if found is not None:
            return found
        sources = _source_parts(candidates)
        return _best_value(self._matching_entries(sources), sources)


def _covered_from_pairs(lines: dict[int, tuple[int, int]] | None) -> set[int] | None:
    if lines is None:
        return None
    return {number for number, (covered, _total) in lines.items() if covered > 0}


def _child_dirs(directory: Path, skip_dirs: set[str]) -> list[Path]:
    try:
        with os.scandir(directory) as entries:
            dirs = [
                Path(entry.path)
                for entry in entries
                if entry.name not in skip_dirs and entry.is_dir()
            ]
    except OSError:
        return []
    dirs.sort(key=lambda path: path.name)
    return dirs


def _subdirs_1_and_2(root: Path, skip_dirs: set[str]) -> list[Path]:
    first = _child_dirs(root, skip_dirs)
    second = [child for directory in first for child in _child_dirs(directory, skip_dirs)]
    return first + second


def _jacoco_paths(root: Path) -> list[Path]:
    crapper = ensure_crapper()
    skip_dirs = set(crapper.discover.SKIP_DIRS)
    paths = [root / "target" / "site" / "jacoco" / "jacoco.xml"]
    for subdir in _subdirs_1_and_2(root, skip_dirs):
        paths.append(subdir / "target" / "site" / "jacoco" / "jacoco.xml")
    return paths


def _line_number(line) -> int | None:
    try:
        number = int(line.get("nr") or "0")
        instructions = int(line.get("ci") or "0")
        branches = int(line.get("cb") or "0")
    except ValueError:
        return None
    if instructions > 0 or branches > 0:
        return number
    return None


def _source_lines(source) -> set[int]:
    covered = set()
    for line in source.findall("line"):
        number = _line_number(line)
        if number is not None:
            covered.add(number)
    return covered


def _package_lines(xml_root) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for package in xml_root.iter("package"):
        package_name = (package.get("name") or "").strip("/")
        for source in package.findall("sourcefile"):
            filename = source.get("name") or ""
            key = f"{package_name}/{filename}" if package_name else filename
            found[key] = _source_lines(source)
    return found


def _read_jacoco(path: Path, crapper):
    text = path.read_text(encoding="utf-8", errors="replace")
    cleaned = crapper.coverage._DOCTYPE.sub("", text)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError:
        return None


def _jacoco_index(root: Path) -> dict[str, set[int]]:
    crapper = ensure_crapper()
    found: dict[str, set[int]] = {}
    seen: set[Path] = set()
    for path in _jacoco_paths(root):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        xml_root = _read_jacoco(path, crapper)
        if xml_root is None:
            continue
        found.update(_package_lines(xml_root))
    return found


def _swift_hits(record) -> set[int]:
    """Lines the tests hit.

    A region covers every line from its start through its end. `line_covered`
    asks the innermost region, so a nested region with count zero leaves those
    lines uncovered, and a line in no region stays uncovered. An xccov summary
    has no regions, so the function line is covered when that function has
    covered lines.
    """

    if record.regions:
        covered: set[int] = set()
        for start, end, _count in record.regions:
            for line in range(start, end + 1):
                if record.line_covered(line):
                    covered.add(line)
        return covered
    return {line for line, covered, _executable in record.summaries if covered > 0}


def _segments_to_lines(segments) -> set[int] | None:
    if segments is None:
        return None
    covered: set[int] = set()
    for start, end, _statements, hits in segments:
        if hits > 0:
            covered.update(range(start, end + 1))
    return covered


_FORMAT_LOADERS: dict[str, tuple[str, str]] = {
    "lcov": ("load_lcov", "lcov"),
    "go": ("load_go_profile", "go_profile"),
    "clojure_html": ("load_form_html", "form_html"),
}


class CoverageCache:
    """Lazy per-root coverage cache compatible with bundled or split crapper loaders."""

    def __init__(self) -> None:
        self._bundles: dict[Path, Any] = {}
        self._indexes: dict[tuple[Path, str], NormalizedIndex] = {}
        self._swift: dict[Path, dict[str, Any]] = {}
        self._swift_hits_cache: dict[int, set[int]] = {}

    def _bundle(self, root_key: Path):
        bundle = self._bundles.get(root_key)
        if bundle is None:
            bundle = ensure_crapper().coverage.load_bundle(root_key)
            self._bundles[root_key] = bundle
        return bundle

    def _load_format(self, root_key: Path, loader_name: str, bundle_attr: str) -> dict:
        crapper = ensure_crapper()
        loader = getattr(crapper.coverage, loader_name, None)
        if loader is not None:
            return loader(root_key) or {}
        return getattr(self._bundle(root_key), bundle_attr) or {}

    def _index(self, root_key: Path, kind: str) -> NormalizedIndex:
        cache_key = (root_key, kind)
        cached = self._indexes.get(cache_key)
        if cached is not None:
            return cached
        crapper = ensure_crapper()
        if kind == "java":
            raw = _jacoco_index(root_key)
        else:
            loader_name, bundle_attr = _FORMAT_LOADERS[kind]
            raw = self._load_format(root_key, loader_name, bundle_attr)
        index = NormalizedIndex(raw, crapper.coverage.normalize_path)
        self._indexes[cache_key] = index
        return index

    def _swift_map(self, root_key: Path) -> dict:
        cached = self._swift.get(root_key)
        if cached is None:
            cached = self._load_format(root_key, "load_swift", "swift")
            self._swift[root_key] = cached
        return cached

    def _swift_lines(self, root_key: Path, path: str) -> set[int] | None:
        swift_map = self._swift_map(root_key)
        if not swift_map:
            return None
        record = ensure_crapper().coverage._lookup(swift_map, path, [])
        if record is None:
            return None
        key = id(record)
        hits = self._swift_hits_cache.get(key)
        if hits is None:
            hits = _swift_hits(record)
            self._swift_hits_cache[key] = hits
        return hits

    def _go_lines(self, root_key: Path, path: str) -> set[int] | None:
        index = self._index(root_key, "go")
        if not index.exact:
            return None
        crapper = ensure_crapper()
        name = crapper.coverage.go_import_name(Path(path), root_key)
        segments = index.lookup(path, crapper.coverage.normalize_path, name)
        return _segments_to_lines(segments)

    def _clojure_lines(self, root_key: Path, path: str, normalize) -> set[int] | None:
        html = _covered_from_pairs(self._index(root_key, "clojure_html").lookup(path, normalize))
        if html is not None:
            return html
        return _covered_from_pairs(self._index(root_key, "lcov").lookup(path, normalize))

    def covered_lines(self, root: Path, source_path: Path, language: str) -> set[int] | None:
        root_key = root.resolve()
        path = source_path.resolve().as_posix()
        normalize = ensure_crapper().coverage.normalize_path
        if language == "java":
            return self._index(root_key, "java").lookup(path, normalize)
        if language == "go":
            return self._go_lines(root_key, path)
        if language == "swift":
            return self._swift_lines(root_key, path)
        if language == "clojure":
            return self._clojure_lines(root_key, path, normalize)
        return _covered_from_pairs(self._index(root_key, "lcov").lookup(path, normalize))


def covered_lines(
    root: Path,
    source_path: Path,
    language: str,
    *,
    cache: CoverageCache | None = None,
) -> set[int] | None:
    """Lines with a hit, or None when this file is absent from coverage."""

    active = cache if cache is not None else CoverageCache()
    return active.covered_lines(root, source_path, language)
