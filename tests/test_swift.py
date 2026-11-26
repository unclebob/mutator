"""Swift mutation sites join the functions crapper reports."""

import os
import sys
from pathlib import Path

from mutator.cli import parse_args, run, select_files
from mutator.functions import sites_in_file
from mutator.runner import test_plan as plan_command

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


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_xcode_options_require_a_value():
    options = parse_args(
        ["--xcode-scheme", "App", "--xcode-destination", "platform=iOS Simulator,name=iPhone 16"]
    )
    assert options.xcode_scheme == "App"
    assert options.xcode_destination == "platform=iOS Simulator,name=iPhone 16"
    help_text = parse_args(["--help"]).message
    assert "--xcode-scheme" in help_text
    assert "--xcode-destination" in help_text
    assert "swift test" in help_text
    assert "xcodebuild" in help_text
    assert parse_args(["--xcode-scheme"]).exit_code == 1


def test_a_swift_package_runs_swift_test(tmp_path):
    root = tmp_path / "pkg"
    _write(root / "Package.swift", "// swift-tools-version:5.9\n")
    source = _write(root / "Sources" / "Demo" / "Box.swift", "func open() -> Int { 1 }\n")
    command, directory = plan_command(root, source, "swift", None)
    assert command == ["swift", "test"]
    assert directory == root.resolve()
    override, override_dir = plan_command(root, source, "swift", "swift test --filter open")
    assert override == "swift test --filter open"
    assert override_dir == root.resolve()


def test_an_xcode_project_runs_xcodebuild_with_scheme_and_destination(tmp_path):
    root = tmp_path / "ios"
    project = root / "App.xcodeproj"
    _write(project / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    _write(project / "xcshareddata" / "xcschemes" / "Zed.xcscheme", "<Scheme/>\n")
    source = _write(root / "App" / "ContentView.swift", "func body() -> Int { 1 }\n")
    destination = "platform=iOS Simulator,name=iPhone 16"
    command, directory = plan_command(root, source, "swift", None, xcode_destination=destination)
    assert command == [
        "xcodebuild",
        "test",
        "-project",
        str(project.resolve()),
        "-scheme",
        "App",
        "-destination",
        destination,
    ]
    assert directory == root.resolve()
    chosen, _directory = plan_command(
        root, source, "swift", None, xcode_scheme="Zed", xcode_destination=destination
    )
    assert chosen[chosen.index("-scheme") + 1] == "Zed"


def test_a_workspace_uses_its_flag_and_a_missing_scheme_uses_the_project_name(tmp_path):
    root = tmp_path / "ios"
    workspace = root / "App.xcworkspace"
    _write(workspace / "contents.xcworkspacedata", "<Workspace/>\n")
    _write(root / "App.xcodeproj" / "project.pbxproj", "")
    _write(workspace / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    source = _write(root / "App" / "ContentView.swift", "func body() -> Int { 1 }\n")
    command, directory = plan_command(root, source, "swift", None)
    assert command[:4] == ["xcodebuild", "test", "-workspace", str(workspace.resolve())]
    assert "-destination" not in command
    assert directory == root.resolve()

    bare = tmp_path / "bare"
    project = bare / "Solo.xcodeproj"
    _write(project / "project.pbxproj", "")
    solo = _write(bare / "Solo" / "View.swift", "func body() -> Int { 1 }\n")
    bare_command, _bare_dir = plan_command(bare, solo, "swift", None)
    assert bare_command[bare_command.index("-scheme") + 1] == "Solo"


def test_a_package_is_tested_with_swift_and_an_app_outside_it_with_xcodebuild(tmp_path):
    root = tmp_path / "repo"
    _write(root / "libs" / "Demo" / "Package.swift", "// swift-tools-version:5.9\n")
    package_source = _write(
        root / "libs" / "Demo" / "Sources" / "Demo" / "Box.swift",
        "func open() -> Int { 1 }\n",
    )
    project = root / "App.xcodeproj"
    _write(project / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    app_source = _write(root / "App" / "ContentView.swift", "func body() -> Int { 1 }\n")
    package_command, package_dir = plan_command(root, package_source, "swift", None)
    app_command, app_dir = plan_command(root, app_source, "swift", None)
    assert package_command == ["swift", "test"]
    assert package_dir == (root / "libs" / "Demo").resolve()
    assert app_command[2:6] == ["-project", str(project.resolve()), "-scheme", "App"]
    assert app_dir == root.resolve()


def test_a_worker_does_not_share_swift_build_directories(tmp_path):
    from mutator.workers import create_workers, new_run_dir

    root = tmp_path / "pkg"
    source = _write(root / "Sources" / "Demo" / "Box.swift", "func open() -> Int { 1 }\n")
    _write(root / "Package.swift", "// swift-tools-version:5.9\n")
    for name in (".build", ".swiftpm", "DerivedData"):
        _write(root / name / "marker", "x")
    worker = create_workers(
        new_run_dir(root), root, "Sources/Demo/Box.swift", source.read_bytes(), 1
    )[0]
    for name in (".build", ".swiftpm", "DerivedData"):
        assert not (worker / name).exists()
    assert (worker / "Package.swift").is_file()
    copy = worker / "Sources" / "Demo" / "Box.swift"
    assert copy.is_file() and not copy.is_symlink()


def _fake_tool(directory: Path, name: str, log: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$PWD\" >> '{log}'\n"
        f"printf '%s\\n' \"$@\" >> '{log}'\n"
        f"printf '%s\\n' --- >> '{log}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def test_a_run_invokes_swift_test_and_xcodebuild_with_the_destination(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    swift_log = tmp_path / "swift.log"
    xcode_log = tmp_path / "xcode.log"
    _fake_tool(bin_dir, "swift", swift_log)
    _fake_tool(bin_dir, "xcodebuild", xcode_log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    package = tmp_path / "pkg"
    _write(package / "Package.swift", "// swift-tools-version:5.9\n")
    _write(package / "Sources" / "Demo" / "Box.swift", "func open() -> Int { 1 }\n")
    swift_code = run(["--no-coverage", "--root", str(package), "--max-workers", "1"])
    swift_text = swift_log.read_text(encoding="utf-8")
    assert swift_code == 3
    assert "test\n" in swift_text
    assert str(package.resolve()) in swift_text

    app = tmp_path / "ios"
    project = app / "My App.xcodeproj"
    _write(project / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    _write(app / "App" / "ContentView.swift", "func body() -> Int { 1 }\n")
    destination = "platform=iOS Simulator,name=iPhone 16"
    xcode_code = run(
        [
            "--no-coverage",
            "--root",
            str(app),
            "--max-workers",
            "1",
            "--xcode-scheme",
            "App",
            "--xcode-destination",
            destination,
        ]
    )
    xcode_text = xcode_log.read_text(encoding="utf-8")
    assert xcode_code == 3
    assert "xcodebuild" not in xcode_text
    assert "-project" in xcode_text
    assert str(project.resolve()) in xcode_text
    assert "-scheme" in xcode_text
    assert "App" in xcode_text.splitlines()
    assert destination in xcode_text.splitlines()


def test_scan_marks_a_swift_line_uncovered_when_llvm_cov_did_not_hit_it(tmp_path, capsys):
    import json

    source = tmp_path / "Sources" / "Demo" / "Box.swift"
    source.parent.mkdir(parents=True)
    source.write_text("func place(x: Int) -> Int {\n    return x > 0 ? x : 0\n}\n", encoding="utf-8")
    report = tmp_path / "target" / "coverage" / "swift" / "llvm-cov.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "type": "llvm.coverage.json.export",
                "data": [
                    {
                        "files": [{"filename": str(source)}],
                        "functions": [{"regions": [[1, 1, 1, 2, 1, 0, 0, 0]]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    code = run(["--scan", "--use-existing-coverage", "--root", str(tmp_path), str(source)])
    captured = capsys.readouterr()
    assert code == 0
    assert "uncovered" in captured.out
    assert "x > 0 -> !(x > 0)" in captured.out or "> -> >=" in captured.out


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
