import json
from pathlib import Path

from mutator.cli import run
from mutator.coverage import (
    CoverageCache,
    NormalizedIndex,
    _jacoco_paths,
    _subdirs_1_and_2,
    covered_lines,
)
from mutator.crapper_link import ensure_crapper


def test_jacoco_marks_lines_with_covered_instructions(tmp_path):
    report = tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """<?xml version="1.0"?>
<report name="demo">
  <package name="demo">
    <sourcefile name="Board.java">
      <line nr="3" mi="0" ci="2" mb="0" cb="0"/>
      <line nr="4" mi="4" ci="0" mb="1" cb="0"/>
      <line nr="5" mi="0" ci="0" mb="0" cb="1"/>
    </sourcefile>
  </package>
</report>
""",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "demo" / "Board.java"
    source.parent.mkdir(parents=True)
    source.write_text("package demo;\nclass Board {}\n", encoding="utf-8")
    assert covered_lines(tmp_path, source, "java") == {3, 5}


def test_go_profile_marks_hit_lines(tmp_path):
    profile = tmp_path / "target" / "coverage" / "go" / "coverage.out"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        "mode: set\n"
        "demo/widget.go:2.1,4.2 1 1\n"
        "demo/widget.go:5.1,6.2 1 0\n",
        encoding="utf-8",
    )
    source = tmp_path / "demo" / "widget.go"
    source.parent.mkdir()
    source.write_text("package demo\nfunc Run() {}\n", encoding="utf-8")
    assert covered_lines(tmp_path, source, "go") == {2, 3, 4}
    assert covered_lines(tmp_path, tmp_path / "other" / "missing.go", "go") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert covered_lines(empty, empty / "demo" / "widget.go", "go") is None


def test_lcov_marks_executed_lines(tmp_path):
    report = tmp_path / "target" / "coverage" / "python" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text(
        "SF:src/demo/app.py\nDA:1,3\nDA:2,0\nend_of_record\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "demo" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def place():\n    return 1\n", encoding="utf-8")
    assert covered_lines(tmp_path, source, "python") == {1}
    assert covered_lines(tmp_path, source, "clojure") == {1}


def test_lcov_does_not_let_a_shorter_path_steal_hits(tmp_path):
    report = tmp_path / "target" / "coverage" / "python" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text(
        "SF:b/app.py\nDA:2,1\nend_of_record\n"
        "SF:a/b/app.py\nDA:1,1\nend_of_record\n",
        encoding="utf-8",
    )
    short = tmp_path / "b" / "app.py"
    long = tmp_path / "a" / "b" / "app.py"
    short.parent.mkdir(parents=True)
    long.parent.mkdir(parents=True)
    short.write_text("def place():\n    return 1\n", encoding="utf-8")
    long.write_text("def place():\n    return 1\n", encoding="utf-8")
    assert covered_lines(tmp_path, long, "python") == {1}
    assert covered_lines(tmp_path, short, "python") == {2}


def test_a_prefixed_report_path_beats_a_shorter_suffix(tmp_path):
    source = _py_source(tmp_path)
    parts = [part for part in source.resolve().as_posix().split("/") if part]
    prefixed = "x/y/" + "/".join(parts)
    shorter = "/".join(parts[1:])
    _lcov(tmp_path, [(prefixed, 2), (shorter, 9)])
    assert covered_lines(tmp_path, source, "python") == {2}


def _lcov(root: Path, records: list[tuple[str, int]]) -> None:
    report = root / "target" / "coverage" / "python" / "lcov.info"
    report.parent.mkdir(parents=True)
    chunks = [f"SF:{name}\nDA:{line},1\nend_of_record\n" for name, line in records]
    report.write_text("".join(chunks), encoding="utf-8")


def _py_source(tmp_path: Path) -> Path:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def place():\n    return 1\n", encoding="utf-8")
    return source


def test_a_trailing_slash_on_the_report_path_still_marks_the_file(tmp_path):
    source = _py_source(tmp_path)
    _lcov(tmp_path, [(source.resolve().as_posix() + "/", 2)])
    assert covered_lines(tmp_path, source, "python") == {2}


def test_the_first_of_two_equal_paths_keeps_its_hits(tmp_path):
    source = _py_source(tmp_path)
    parts = [part for part in source.resolve().as_posix().split("/") if part]
    first = "pre/" + "/".join(parts)
    _lcov(tmp_path, [(first, 2), (first + "/", 9)])
    assert covered_lines(tmp_path, source, "python") == {2}


def test_go_profile_does_not_cross_files(tmp_path):
    profile = tmp_path / "target" / "coverage" / "go" / "coverage.out"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        "mode: set\n"
        "b/widget.go:2.1,2.2 1 1\n"
        "a/b/widget.go:3.1,3.2 1 0\n",
        encoding="utf-8",
    )
    short = tmp_path / "b" / "widget.go"
    long = tmp_path / "a" / "b" / "widget.go"
    short.parent.mkdir(parents=True)
    long.parent.mkdir(parents=True)
    short.write_text("package b\nfunc Run() {}\n", encoding="utf-8")
    long.write_text("package b\nfunc Run() {}\n", encoding="utf-8")
    assert covered_lines(tmp_path, short, "go") == {2}
    assert covered_lines(tmp_path, long, "go") == set()

    profile.write_text("mode: set\na/b/widget.go:2.1,2.2 1 1\n", encoding="utf-8")
    assert covered_lines(tmp_path, short, "go") is None
    assert covered_lines(tmp_path, long, "go") == {2}


def _go_module(module_dir, module, profile_text):
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "go.mod").write_text(f"module {module}\n\ngo 1.21\n", encoding="utf-8")
    profile = module_dir / "target" / "coverage" / "go" / "coverage.out"
    profile.parent.mkdir(parents=True)
    profile.write_text(profile_text, encoding="utf-8")


def _go_source(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("package main\nfunc main() {}\n", encoding="utf-8")
    return path


def _swift_report(root: Path, payload: dict) -> None:
    report = root / "target" / "coverage" / "swift" / "llvm-cov.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(payload), encoding="utf-8")


def test_swift_llvm_cov_regions_mark_hit_lines(tmp_path):
    source = tmp_path / "Sources" / "Demo" / "Box.swift"
    source.parent.mkdir(parents=True)
    source.write_text("func choose(x: Int) -> Int { 1 }\n", encoding="utf-8")
    _swift_report(
        tmp_path,
        {
            "type": "llvm.coverage.json.export",
            "data": [
                {
                    "files": [{"filename": str(source)}],
                    "functions": [
                        {
                            "regions": [
                                [2, 1, 2, 2, 1, 0, 0, 0],
                                [4, 1, 4, 2, 0, 0, 0, 0],
                                [3, 9, 3, 18, 0, 0, 0, 4],
                            ]
                        }
                    ],
                }
            ],
        },
    )
    assert covered_lines(tmp_path, source, "swift") == {2}
    assert covered_lines(tmp_path, tmp_path / "Sources" / "Demo" / "Missing.swift", "swift") is None


def test_swift_xccov_summaries_mark_a_covered_function_line(tmp_path):
    source = tmp_path / "Sources" / "Demo" / "Box.swift"
    source.parent.mkdir(parents=True)
    source.write_text("func choose(x: Int) -> Int { 1 }\n", encoding="utf-8")
    _swift_report(
        tmp_path,
        {
            "targets": [
                {
                    "files": [
                        {
                            "path": "Sources/Demo/Box.swift",
                            "functions": [
                                {"lineNumber": 1, "coveredLines": 1, "executableLines": 2},
                                {"lineNumber": 9, "coveredLines": 0, "executableLines": 1},
                            ],
                        }
                    ]
                }
            ]
        },
    )
    assert covered_lines(tmp_path, source, "swift") == {1}


def test_swift_regions_with_no_hits_are_present_and_empty(tmp_path):
    source = tmp_path / "Sources" / "Demo" / "Box.swift"
    source.parent.mkdir(parents=True)
    source.write_text("func choose(x: Int) -> Int { 1 }\n", encoding="utf-8")
    _swift_report(
        tmp_path,
        {
            "type": "llvm.coverage.json.export",
            "data": [
                {
                    "files": [{"filename": str(source)}],
                    "functions": [{"regions": [[2, 1, 2, 2, 0, 0, 0, 0]]}],
                }
            ],
        },
    )
    assert covered_lines(tmp_path, source, "swift") == set()
    empty = tmp_path / "other"
    empty.mkdir()
    assert covered_lines(empty, source, "swift") is None


# Line 5 is the end of a region that starts on line 4. Line 2 is in no region.
# Lines 6 and 7 are a nested region whose count is zero.
_PLACE = """func place(x: Int) -> Int {
    let ready = true
    let extra = 1
    if x > 0 {
        return x + 1
    }
    return 0
}
"""


def _place(tmp_path: Path) -> Path:
    source = tmp_path / "Sources" / "Demo" / "Place.swift"
    source.parent.mkdir(parents=True)
    source.write_text(_PLACE, encoding="utf-8")
    return source


def _llvm_regions(root: Path, source: Path, regions: list) -> None:
    _swift_report(
        root,
        {
            "type": "llvm.coverage.json.export",
            "data": [
                {
                    "files": [{"filename": str(source)}],
                    "functions": [{"regions": regions}],
                }
            ],
        },
    )


def _scan_line(output: str, fragment: str) -> str:
    matches = [line for line in output.splitlines() if fragment in line]
    assert len(matches) == 1
    return matches[0]


def test_a_multiline_covered_region_covers_the_mutation_on_its_end_line(tmp_path, capsys):
    source = _place(tmp_path)
    _llvm_regions(tmp_path, source, [[4, 1, 5, 20, 1, 0, 0, 0]])
    assert covered_lines(tmp_path, source, "swift") == {4, 5}
    code = run(["--scan", "--use-existing-coverage", "--root", str(tmp_path), str(source)])
    captured = capsys.readouterr()
    assert code == 0
    assert "uncovered" not in _scan_line(captured.out, ":5 + -> -")


def test_a_nested_zero_count_region_leaves_its_lines_uncovered(tmp_path, capsys):
    source = _place(tmp_path)
    _llvm_regions(
        tmp_path,
        source,
        [
            [4, 1, 7, 14, 3, 0, 0, 0],
            [6, 1, 7, 14, 0, 0, 0, 0],
        ],
    )
    assert covered_lines(tmp_path, source, "swift") == {4, 5}
    code = run(["--scan", "--use-existing-coverage", "--root", str(tmp_path), str(source)])
    captured = capsys.readouterr()
    assert code == 0
    assert "uncovered" not in _scan_line(captured.out, ":5 + -> -")
    assert "uncovered" in _scan_line(captured.out, ":7 0 -> 1")


def test_a_line_outside_every_swift_region_is_uncovered(tmp_path, capsys):
    source = _place(tmp_path)
    _llvm_regions(tmp_path, source, [[4, 1, 5, 20, 1, 0, 0, 0]])
    assert covered_lines(tmp_path, source, "swift") == {4, 5}
    code = run(["--scan", "--use-existing-coverage", "--root", str(tmp_path), str(source)])
    captured = capsys.readouterr()
    assert code == 0
    assert "uncovered" in _scan_line(captured.out, ":2 true -> false")


def test_go_profile_keyed_by_import_path_marks_hit_lines(tmp_path):
    # `go test -coverprofile` names files by module import path, not by disk path.
    _go_module(
        tmp_path,
        "github.com/acme/tool",
        "mode: set\n"
        "github.com/acme/tool/cmd/tool/main.go:2.1,3.2 1 1\n"
        "github.com/acme/tool/cmd/tool/main.go:4.1,4.2 1 0\n"
        "github.com/acme/tool/main.go:7.1,7.2 1 1\n",
    )
    nested = _go_source(tmp_path / "cmd" / "tool" / "main.go")
    top = _go_source(tmp_path / "main.go")
    assert covered_lines(tmp_path, nested, "go") == {2, 3}
    assert covered_lines(tmp_path, top, "go") == {7}


def test_go_profile_of_a_module_in_a_subdirectory(tmp_path):
    _go_module(
        tmp_path / "svc",
        "example.com/api",
        "mode: set\nexample.com/api/calc/calc.go:4.2,6.1 1 1\n",
    )
    source = _go_source(tmp_path / "svc" / "calc" / "calc.go")
    assert covered_lines(tmp_path, source, "go") == {4, 5, 6}


def test_coverage_cache_uses_per_format_loader_once_without_load_bundle(tmp_path, monkeypatch):
    crapper = ensure_crapper()
    a = tmp_path / "src" / "a.py"
    b = tmp_path / "src" / "b.py"
    a.parent.mkdir(parents=True)
    a.write_text("def a():\n    return 1\n", encoding="utf-8")
    b.write_text("def b():\n    return 2\n", encoding="utf-8")
    calls = []

    def fake_load_lcov(root):
        calls.append(root)
        return {
            a.resolve().as_posix(): {1: (1, 1)},
            b.resolve().as_posix(): {2: (1, 1)},
        }

    def fail_bundle(_root):
        raise AssertionError("load_bundle should not be called when load_lcov exists")

    monkeypatch.setattr(crapper.coverage, "load_lcov", fake_load_lcov, raising=False)
    monkeypatch.setattr(crapper.coverage, "load_bundle", fail_bundle)

    cache = CoverageCache()
    assert covered_lines(tmp_path, a, "python", cache=cache) == {1}
    assert covered_lines(tmp_path, b, "python", cache=cache) == {2}
    assert calls == [tmp_path.resolve()]


def test_coverage_cache_falls_back_to_single_load_bundle(tmp_path, monkeypatch):
    crapper = ensure_crapper()

    a = tmp_path / "src" / "a.py"
    b = tmp_path / "src" / "b.py"
    clj = tmp_path / "src" / "core.clj"
    a.parent.mkdir(parents=True)
    a.write_text("def a():\n    return 1\n", encoding="utf-8")
    b.write_text("def b():\n    return 2\n", encoding="utf-8")
    clj.write_text("(ns core)\n(defn f [] 1)\n", encoding="utf-8")
    _lcov(
        tmp_path,
        [
            (a.resolve().as_posix(), 1),
            (b.resolve().as_posix(), 2),
            (clj.resolve().as_posix(), 2),
        ],
    )
    _go_module(tmp_path, "example.com/demo", "mode: set\nexample.com/demo/w.go:2.1,2.2 1 1\n")
    go_file = _go_source(tmp_path / "w.go")
    swift_file = tmp_path / "Sources" / "Demo" / "Box.swift"
    swift_file.parent.mkdir(parents=True)
    swift_file.write_text("func f() -> Int { 1 }\n", encoding="utf-8")
    _llvm_regions(tmp_path, swift_file, [[1, 1, 1, 10, 1, 0, 0, 0]])

    real_bundle = crapper.coverage.load_bundle(tmp_path)
    for loader in ("load_lcov", "load_go_profile", "load_form_html", "load_swift"):
        monkeypatch.delattr(crapper.coverage, loader, raising=False)

    bundle_calls = []

    def counting_load_bundle(root):
        bundle_calls.append(root)
        return real_bundle

    monkeypatch.setattr(crapper.coverage, "load_bundle", counting_load_bundle)

    cache = CoverageCache()
    assert covered_lines(tmp_path, a, "python", cache=cache) == {1}
    assert covered_lines(tmp_path, b, "python", cache=cache) == {2}
    assert covered_lines(tmp_path, clj, "clojure", cache=cache) == {2}
    assert covered_lines(tmp_path, go_file, "go", cache=cache) == {2}
    assert covered_lines(tmp_path, swift_file, "swift", cache=cache) == {1}
    assert covered_lines(tmp_path, swift_file, "swift", cache=cache) == {1}
    assert bundle_calls == [tmp_path.resolve()]


def test_coverage_cache_caches_jacoco_and_empty_reports(tmp_path, monkeypatch):
    import mutator.coverage as coverage_module

    crapper = ensure_crapper()
    report = tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """<?xml version="1.0"?>
<report name="demo">
  <package name="demo">
    <sourcefile name="One.java">
      <line nr="2" mi="0" ci="1" mb="0" cb="0"/>
    </sourcefile>
    <sourcefile name="Two.java">
      <line nr="3" mi="0" ci="1" mb="0" cb="0"/>
    </sourcefile>
  </package>
</report>
""",
        encoding="utf-8",
    )
    one = tmp_path / "src" / "demo" / "One.java"
    two = tmp_path / "src" / "demo" / "Two.java"
    one.parent.mkdir(parents=True)
    one.write_text("package demo;\nclass One {}\n", encoding="utf-8")
    two.write_text("package demo;\nclass Two {}\n", encoding="utf-8")

    orig_jacoco = coverage_module._jacoco_index
    calls = []

    def counting_jacoco(root):
        calls.append(root)
        return orig_jacoco(root)

    monkeypatch.setattr(coverage_module, "_jacoco_index", counting_jacoco)
    cache = CoverageCache()
    assert covered_lines(tmp_path, one, "java", cache=cache) == {2}
    assert covered_lines(tmp_path, two, "java", cache=cache) == {3}
    assert calls == [tmp_path.resolve()]

    monkeypatch.setattr(crapper.coverage, "load_form_html", lambda _r: {"src/demo/core.clj": {4: (1, 1)}}, raising=False)
    monkeypatch.setattr(crapper.coverage, "load_lcov", lambda _r: (_ for _ in ()).throw(AssertionError("unexpected")), raising=False)
    clj = tmp_path / "src" / "demo" / "core.clj"
    clj.write_text("(ns demo.core)\n", encoding="utf-8")
    assert covered_lines(tmp_path, clj, "clojure", cache=CoverageCache()) == {4}


def test_jacoco_paths_skip_skip_dirs(tmp_path):
    (tmp_path / "afile.txt").write_text("x", encoding="utf-8")
    for rel in (
        "node_modules/pkg/target/site/jacoco/jacoco.xml",
        "build/sub/target/site/jacoco/jacoco.xml",
        "svc/target/site/jacoco/jacoco.xml",
        "group/mod/target/site/jacoco/jacoco.xml",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("<report/>", encoding="utf-8")

    paths = _jacoco_paths(tmp_path)
    as_rel = [path.relative_to(tmp_path).as_posix() for path in paths]
    assert "node_modules/pkg/target/site/jacoco/jacoco.xml" not in as_rel
    assert "build/sub/target/site/jacoco/jacoco.xml" not in as_rel
    assert as_rel == [
        "target/site/jacoco/jacoco.xml",
        "group/target/site/jacoco/jacoco.xml",
        "svc/target/site/jacoco/jacoco.xml",
        "group/mod/target/site/jacoco/jacoco.xml",
    ]
    assert _subdirs_1_and_2(tmp_path / "does-not-exist", {"node_modules"}) == []


def test_normalized_index_parity(tmp_path):
    normalize = ensure_crapper().coverage.normalize_path
    assert NormalizedIndex({}, normalize).lookup("src/app.py", normalize) is None

    source = _py_source(tmp_path)
    parts = [part for part in source.resolve().as_posix().split("/") if part]

    exact_idx = NormalizedIndex({"./src/app.py": {1}, "src/app.py": {9}}, normalize)
    assert exact_idx.lookup("src/app.py", normalize) == {1}

    trailing_idx = NormalizedIndex(
        {"pre/" + "/".join(parts): {2}, "pre/" + "/".join(parts) + "/": {9}},
        normalize,
    )
    assert trailing_idx.lookup(str(source), normalize) == {2}

    prefixed = "x/y/" + "/".join(parts)
    shorter = "/".join(parts[1:])
    prefixed_idx = NormalizedIndex({prefixed: {2}, shorter: {9}}, normalize)
    assert prefixed_idx.lookup(str(source), normalize) == {2}

    tie_idx = NormalizedIndex({"left/src/app.py": {1}, "right/src/app.py": {2}}, normalize)
    assert tie_idx.lookup("src/app.py", normalize) is None

    assert tie_idx.lookup("", normalize) is None
    assert tie_idx.lookup("other.py", normalize) is None

    multi_idx = NormalizedIndex({"pkg/named.py": {7}, "other/app.py": {8}}, normalize)
    assert multi_idx.lookup("src/app.py", normalize, name="pkg/named.py") == {7}
