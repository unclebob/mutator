from mutator.coverage import _package_owns_report, covered_lines


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


def _write_hpc(tmp_path, package, module, covered_line=2):
    markup = (
        tmp_path
        / "dist-newstyle/build/x/demo/t/spec/hpc/vanilla/html"
        / f"{package}-inplace"
        / f"{module}.hs.html"
    )
    markup.parent.mkdir(parents=True, exist_ok=True)
    markup.write_text(
        f'<span class="lineno">    {covered_line} </span>'
        '<span class="istickedoff">choose = 1</span>\n'
        '<span class="lineno">    3 </span>'
        '<span class="nottickedoff">missing = 0</span>\n',
        encoding="utf-8",
    )


def test_haskell_coverage_uses_hpc_markup(tmp_path):
    source = tmp_path / "src" / "Demo.hs"
    source.parent.mkdir(parents=True)
    source.write_text("module Demo where\nchoose = 1\n", encoding="utf-8")
    _write_hpc(tmp_path, "demo", "Demo")
    assert covered_lines(tmp_path, source, "haskell") == {2}


def test_haskell_coverage_maps_hierarchical_dotted_modules(tmp_path):
    source = tmp_path / "hbs2-peer/lib/HBS2/Peer/RepositoryLease/Worker.hs"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module HBS2.Peer.RepositoryLease.Worker where\nchoose = 1\n",
        encoding="utf-8",
    )
    _write_hpc(tmp_path, "hbs2-peer-0.26.0.3", "HBS2.Peer.RepositoryLease.Worker")
    assert covered_lines(tmp_path, source, "haskell") == {2}


def test_haskell_coverage_uses_package_and_refuses_ambiguity(tmp_path):
    alpha = tmp_path / "alpha/lib/Shared/Store.hs"
    beta = tmp_path / "beta/lib/Shared/Store.hs"
    gamma = tmp_path / "gamma/lib/Shared/Store.hs"
    for source in (alpha, beta, gamma):
        source.parent.mkdir(parents=True)
        source.write_text("module Shared.Store where\nchoose = 1\n", encoding="utf-8")
    _write_hpc(tmp_path, "alpha-1.0-inplace", "Shared.Store", covered_line=2)
    _write_hpc(tmp_path, "beta-1.0-inplace", "Shared.Store", covered_line=4)
    assert covered_lines(tmp_path, beta, "haskell") == {4}
    assert covered_lines(tmp_path, gamma, "haskell") is None


def test_empty_hpc_report_key_belongs_to_no_package():
    assert not _package_owns_report("demo", "")


def test_d_coverage_uses_compiler_listing_hits(tmp_path):
    source = tmp_path / "source" / "demo.d"
    source.parent.mkdir(parents=True)
    source.write_text("module demo;\nint choose(bool ok) { return ok ? 1 : 0; }\n", encoding="utf-8")
    report = tmp_path / "target" / "coverage" / "d" / "demo.lst"
    report.parent.mkdir(parents=True)
    report.write_text(
        "       |module demo;\n"
        "      1|int choose(bool ok) {\n"
        "0000000|  return ok ? 1 : 0;\n"
        "       |}\n"
        "source/demo.d is 50% covered\n",
        encoding="utf-8",
    )
    assert covered_lines(tmp_path, source, "d") == {2}
