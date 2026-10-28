from pathlib import Path

from mutator.functions import sites_in_file
from mutator.sites import discover_raw


def _sites(tmp_path: Path, relative: str, source: str):
    (tmp_path / "pubspec.yaml").write_text("name: demo\n", encoding="utf-8")
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return sites_in_file(source, path, tmp_path, relative)


def _pairs(source: str) -> list[tuple[str, str]]:
    return [(site.original, site.mutant) for site in discover_raw(source, "dart", "a.dart")]


def test_dart_rules_follow_the_java_decisions(tmp_path):
    source = """class Board {
  int place(int x, bool ok) {
    if (x > 0 && ok) {
      return x + 1;
    }
    return !ok ? -x : 0;
  }

  bool _hide() {
    return true;
  }
}
"""
    sites = _sites(tmp_path, "lib/src/board.dart", source)
    described = {(site.form_id, site.original, site.mutant) for site in sites}
    for pair in [("+", "-"), (">", ">="), ("&&", "||"), ("!", ""), ("-", ""), ("0", "1"), ("1", "0")]:
        assert ("defn/place", *pair) in described
    assert ("defn-/_hide", "true", "false") in described
    assert {site.namespace for site in sites} == {"demo.src.board.Board"}


def test_dart_operator_table():
    source = """bool f(int a, int b, int c, bool p, bool q) {
  var d = a * b / c;
  var e = a < b;
  var g = a <= b;
  var h = a == b;
  var i = a != b;
  var j = a >= b;
  return p || q;
}
"""
    pairs = _pairs(source)
    for pair in [
        ("*", "/"),
        ("/", "*"),
        ("<", "<="),
        ("<=", "<"),
        (">=", ">"),
        ("==", "!="),
        ("!=", "=="),
        ("||", "&&"),
    ]:
        assert pair in pairs


def test_dart_null_safety_and_integer_division_are_not_sites():
    source = """int f(int? x, String? s, Object o, int a, int b) {
  var n = x! + 2;
  var m = a ~/ b;
  var r = a % b;
  var w = x ?? 2;
  var y = s?.length;
  var z = o is int;
  var c = o as int;
  return n;
}
"""
    pairs = _pairs(source)
    originals = {original for original, _mutant in pairs}
    assert originals.isdisjoint({"?.", "??", "%", "~/", "is", "as"})
    assert ("!", "") not in pairs
    assert ("+", "-") in pairs


def test_dart_strings_types_and_annotations_are_not_sites():
    source = """@Deprecated('x')
Map<String, int> g({int a = 0}) {
  var s = 'a + 1 > 0';
  var t = 'v=${a + 1}';
  return {};
}
"""
    assert _pairs(source) == [("0", "1"), ("+", "-"), ("1", "0")]


def test_dart_annotation_arguments_are_not_sites():
    source = """@Retry(times: 1, enabled: true)
int g() {
  return 2;
}
"""
    assert _pairs(source) == []


def test_dart_switch_case_constants_are_sites():
    source = """int f(int a) {
  switch (a) {
    case 1:
      return 2;
  }
  return 3;
}
"""
    assert ("1", "0") in _pairs(source)


def test_dart_private_functions():
    from mutator.functions import is_private

    assert is_private("dart", "_hide", "int _hide() => 1;\n", 1, 1) is True
    assert is_private("dart", "show", "int show() => 1;\n", 1, 1) is False


def test_dart_runs_flutter_or_dart_test_in_the_package(tmp_path):
    from mutator.runner import test_plan

    app = tmp_path / "apps" / "app"
    core = tmp_path / "packages" / "core"
    (app / "lib").mkdir(parents=True)
    (core / "lib").mkdir(parents=True)
    (app / "pubspec.yaml").write_text(
        "name: app\ndependencies:\n  flutter:\n    sdk: flutter\n", encoding="utf-8"
    )
    (core / "pubspec.yaml").write_text("name: core\n", encoding="utf-8")
    assert test_plan(tmp_path, app / "lib" / "a.dart", "dart", None) == (["flutter", "test"], app)
    assert test_plan(tmp_path, core / "lib" / "a.dart", "dart", None) == (["dart", "test"], core)


def test_dart_missing_flutter_fails_the_baseline_clearly(tmp_path, monkeypatch):
    from mutator.runner import CommandRunner

    monkeypatch.setenv("PATH", str(tmp_path))
    result = CommandRunner().run(["flutter", "test"], tmp_path, None)
    assert result.code == 127
    assert "flutter" in result.output


def _lcov(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


PICK = "int pick(bool a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\n\nint never() {\n  return 0;\n}\n"


def test_dart_coverage_reads_lcov_lines(tmp_path):
    from mutator.coverage import covered_lines

    one = tmp_path / "packages" / "one"
    two = tmp_path / "packages" / "two"
    for package in (one, two):
        (package / "pubspec.yaml").parent.mkdir(parents=True, exist_ok=True)
        (package / "pubspec.yaml").write_text(f"name: {package.name}\n", encoding="utf-8")
        _lcov(package / "lib" / "a.dart", PICK)
    _lcov(one / "coverage" / "lcov.info", "SF:lib/a.dart\nDA:1,1\nDA:8,0\nend_of_record\n")
    absolute = (two / "lib" / "a.dart").resolve()
    _lcov(
        tmp_path / "target" / "coverage" / "dart" / "packages__two" / "lcov.info",
        f"SF:{absolute}\nDA:1,0\nDA:8,1\nend_of_record\n",
    )
    assert 1 in covered_lines(tmp_path, one / "lib" / "a.dart", "dart")
    assert 8 not in covered_lines(tmp_path, one / "lib" / "a.dart", "dart")
    assert 8 in covered_lines(tmp_path, two / "lib" / "a.dart", "dart")


def test_dart_line_without_da_uses_the_last_instrumented_line(tmp_path):
    """The Dart VM marks a function's first line and its calls, not `return 1;`."""

    from mutator.coverage import covered_lines

    (tmp_path / "pubspec.yaml").write_text("name: demo\n", encoding="utf-8")
    source = tmp_path / "lib" / "a.dart"
    _lcov(source, PICK)
    _lcov(tmp_path / "coverage" / "lcov.info", "SF:lib/a.dart\nDA:1,1\nDA:8,0\nend_of_record\n")
    lines = covered_lines(tmp_path, source, "dart")
    assert {1, 2, 3, 5} <= lines
    assert lines.isdisjoint({8, 9})


def test_help_mentions_dart():
    from mutator.cli import HELP

    assert "Dart" in HELP


def test_dart_generic_constructor_calls_are_not_comparisons():
    """tree-sitter reads `Future<void>.delayed(d)` as `Future < void > .delayed(d)`."""

    source = """Future<void> f(int a) async {
  await Future<void>.delayed(Duration(milliseconds: a + 1));
  var l = List<int>.empty();
  return a < 2 ? null : null;
}
"""
    pairs = _pairs(source)
    assert pairs.count(("<", "<=")) == 1
    assert (">", ">=") not in pairs
    assert ("+", "-") in pairs


def test_dart_comparisons_next_to_dot_shorthands_are_sites():
    source = """Color f(int a, int b) => a > b ? .red : .blue;
bool g(int a, int b) => [a < b, .x].isEmpty;
"""
    pairs = _pairs(source)
    assert (">", ">=") in pairs
    assert ("<", "<=") in pairs


BLOCKS = """int branch(int v) {
  if (v > 100) {
    v = v - 1;
  }
  return v;
}

int choose(bool a) {
  var x = 0;
  if (a) {
    x = 1;
  } else {
    x = 2;
  }
  return x;
}

class C {
  void never() {
    print(1);
  }
  int field = 1 + 1;
}

int counter = 1;
"""


def test_dart_lines_do_not_borrow_from_closed_blocks_or_other_declarations(tmp_path):
    from mutator.coverage import covered_lines

    (tmp_path / "pubspec.yaml").write_text("name: demo\n", encoding="utf-8")
    source = tmp_path / "lib" / "a.dart"
    _lcov(source, BLOCKS)
    _lcov(
        tmp_path / "coverage" / "lcov.info",
        "SF:lib/a.dart\nDA:1,1\nDA:3,0\nDA:8,1\nDA:11,1\nDA:19,0\nDA:20,0\nDA:22,1\nend_of_record\n",
    )
    lines = covered_lines(tmp_path, source, "dart")
    assert 5 in lines  # `return v;` after a block that never ran
    assert 15 in lines
    assert 22 in lines  # the field initializer has its own record
    assert 25 not in lines  # a top-level variable with no record
    assert 3 not in lines
