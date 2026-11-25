"""Swift mutation sites join the functions crapper reports."""

import sys
from pathlib import Path

from mutator.cli import parse_args, run, select_files
from mutator.functions import sites_in_file

SOURCE = """func place(x: Int, ready: Bool, name: String?) -> Int {
    guard x > 0, ready else { return 0 }
    if x >= 1 && ready || !ready {
        return x + 1
    } else if x != 0 {
        return x - 1
    }
    while x < 2 {
        x = x * 2 / 1
    }
    let value = name ?? "guest"
    let flag = true
    let n = -x
    let forced = name!
    let same = name === nil
    let other = name !== nil
    return value.count
}

struct Box {
    private func hide() -> Bool { false }
    fileprivate func seal() -> Bool { true }
    func open() -> Int { 1 }
}

let stored = { (n: Int) -> Int in n + 1 }

func mount() -> Int {
    let handler = { (n: Int) -> Int in
        n > 0 ? n : 0
    }
    return handler(1)
}

func quote() -> String {
    // x > 0 && true ?? name!
    return "x > 0 && true ?? name!"
}

func bind(name: String?) -> Int {
    if let name, name.count > 1 {
        return name.count
    }
    repeat {
        return 0
    } while name == nil
}
"""


def _sites(tmp_path: Path, source: str = SOURCE, relative: str = "Sources/Demo/Box.swift"):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return sites_in_file(source, path, tmp_path, relative)


def _pairs(sites):
    return {(site.original, site.mutant) for site in sites}


def test_swift_operators_match_the_other_languages_and_negate_conditions(tmp_path):
    sites = _sites(tmp_path)
    pairs = _pairs(sites)
    assert ("+", "-") in pairs
    assert ("-", "+") in pairs
    assert ("-", "") in pairs
    assert ("*", "/") in pairs
    assert ("/", "*") in pairs
    assert (">", ">=") in pairs
    assert (">=", ">") in pairs
    assert ("<", "<=") in pairs
    assert ("!=", "==") in pairs
    assert ("==", "!=") in pairs
    assert ("===", "!==") in pairs
    assert ("!==", "===") in pairs
    assert ("&&", "||") in pairs
    assert ("||", "&&") in pairs
    assert ("??", "||") in pairs
    assert ("true", "false") in pairs
    assert ("false", "true") in pairs
    assert ("0", "1") in pairs
    assert ("1", "0") in pairs
    assert ("!", "") in pairs
    negated = {site.original for site in sites if site.mutant == f"!({site.original})"}
    assert negated == {
        "x > 0",
        "ready",
        "x >= 1 && ready || !ready",
        "x != 0",
        "x < 2",
        "name.count > 1",
        "name == nil",
    }
    assert all(site.original != "?" for site in sites)
    assert all(site.original != "?." for site in sites)
    assert all(site.original != "%" for site in sites)


def test_a_nested_closure_stays_on_the_parent_and_a_stored_closure_is_its_own(tmp_path):
    sites = _sites(tmp_path)
    mount = [site for site in sites if site.name == "mount"]
    assert (">", ">=") in _pairs(mount)
    assert ("0", "1") in _pairs(mount)
    assert {site.namespace for site in mount} == {"Demo"}
    assert all(site.name != "handler" for site in sites)
    stored = [site for site in sites if site.name == "stored"]
    assert _pairs(stored) == {("+", "-"), ("1", "0")}
    assert {site.namespace for site in stored} == {"Demo"}


def test_strings_comments_and_force_unwrap_are_left_alone(tmp_path):
    sites = _sites(tmp_path)
    assert all(site.name != "quote" for site in sites)
    deleted = [site for site in sites if site.original == "!" and site.mutant == ""]
    assert len(deleted) == 1
    assert deleted[0].name == "place"


def test_private_and_fileprivate_join_the_type_namespace(tmp_path):
    sites = _sites(tmp_path)
    by_name = {site.name: site for site in sites if site.name in {"hide", "seal", "open"}}
    assert by_name["hide"].form_id == "defn-/hide"
    assert by_name["seal"].form_id == "defn-/seal"
    assert by_name["open"].form_id == "defn/open"
    assert {site.namespace for site in by_name.values()} == {"Demo.Box"}
    assert by_name["hide"].mutant == "true"
    assert by_name["seal"].mutant == "false"
    assert (by_name["open"].original, by_name["open"].mutant) == ("1", "0")


def test_swift_test_files_are_skipped_and_production_sources_are_kept(tmp_path, capsys):
    source = tmp_path / "Sources" / "Demo" / "Box.swift"
    source.parent.mkdir(parents=True)
    source.write_text("func open() -> Int { 1 }\n", encoding="utf-8")
    test_file = tmp_path / "Tests" / "DemoTests" / "Box.swift"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("func check() {}\n", encoding="utf-8")
    named = tmp_path / "Sources" / "Demo" / "BoxTests.swift"
    named.write_text("func check() {}\n", encoding="utf-8")
    chosen = select_files(
        parse_args(["--root", str(tmp_path), str(source), str(test_file), str(named)])
    )
    captured = capsys.readouterr()
    assert chosen == [source.resolve()]
    assert "Skipping test file" in captured.err
    assert "Tests/DemoTests/Box.swift" in captured.err
    assert "BoxTests.swift" in captured.err


def test_a_swift_run_records_killed_and_survived_on_the_crapper_namespace(tmp_path, capsys):
    relative = "Sources/Demo/Place.swift"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        "func place(x: Int) -> Int {\n    guard x > 0 else { return x }\n    return x\n}\n",
        encoding="utf-8",
    )
    command = (
        f"{sys.executable} -c \"from pathlib import Path; "
        "text = Path('Sources/Demo/Place.swift').read_text(); "
        "raise SystemExit(0 if 'x > 0' in text else 1)\""
    )
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(tmp_path),
            "--test-command",
            command,
            relative,
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "KILLED" in captured.out
    assert "SURVIVED" in captured.out
    assert "Uncovered" in captured.out
    snapshot = tmp_path / ".metrics" / "mutate" / "Demo.edn"
    text = snapshot.read_text(encoding="utf-8")
    assert ':namespace "Demo"' in text
    assert ':source "Sources/Demo/Place.swift"' in text
    assert ':id "defn/place"' in text
    assert ":killed" in text
    assert ":survived" in text
    assert path.read_text(encoding="utf-8").startswith("func place")
