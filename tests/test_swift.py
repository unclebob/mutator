"""Swift mutation sites join the functions crapper reports."""

import os
import shutil
import sys
from pathlib import Path

import pytest

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
    assert "*Tests.swift" in help_text
    assert parse_args(["--xcode-scheme"]).exit_code == 1


def test_a_swift_file_without_a_package_or_xcode_project_uses_swift_test(tmp_path):
    root = tmp_path / "loose"
    source = _write(root / "Place.swift", "func place() -> Int { 1 }\n")
    command, directory = plan_command(root, source, "swift", None)
    assert command == ["swift", "test"]
    assert directory == root.resolve()


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
        project.name,
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
    assert command[:4] == ["xcodebuild", "test", "-workspace", workspace.name]
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
    assert app_command[2:6] == ["-project", project.name, "-scheme", "App"]
    assert app_dir == root.resolve()


def test_a_worker_copies_an_xcode_bundle_and_links_a_file_with_that_suffix(tmp_path):
    from mutator.workers import create_workers, new_run_dir

    root = tmp_path / "ios"
    source = _write(root / "App" / "ContentView.swift", "func body() -> Int { 1 }\n")
    _write(root / "App.xcodeproj" / "project.pbxproj", "path = App/ContentView.swift;\n")
    _write(root / "Notes.xcodeproj", "not a bundle\n")
    worker = create_workers(
        new_run_dir(root), root, "App/ContentView.swift", source.read_bytes(), 1
    )[0]
    bundle = worker / "App.xcodeproj"
    assert bundle.is_dir() and not bundle.is_symlink()
    assert (bundle / "project.pbxproj").read_text(encoding="utf-8").startswith("path =")
    linked = worker / "Notes.xcodeproj"
    assert linked.is_symlink()
    assert linked.read_text(encoding="utf-8") == "not a bundle\n"


def test_a_worker_copies_an_asset_catalog_and_keeps_safe_links(tmp_path):
    from mutator.workers import create_workers, new_run_dir

    root = tmp_path / "ios"
    source = _write(root / "DungeonDoor" / "Core" / "AppConfig.swift", "func body() -> Int { 1 }\n")
    _write(root / "DungeonDoor.xcodeproj" / "project.pbxproj", "path = DungeonDoor/Core/AppConfig.swift;\n")
    shared = tmp_path / "shared-icon"
    _write(shared / "AppIcon.png", "png-bytes")
    catalog = root / "DungeonDoor" / "Assets.xcassets"
    _write(catalog / "AccentColor.colorset" / "Contents.json", '{"color":"accent"}\n')
    (catalog / "AppIcon.appiconset").symlink_to(shared)
    _write(root / "DungeonDoor" / "Notes.xcassets", "not a catalog\n")
    _write(root / "DungeonDoor" / "Preview" / "readme.txt", "preview\n")
    _write(root / "Docs" / "readme.txt", "notes\n")
    worker = create_workers(
        new_run_dir(root),
        root,
        "DungeonDoor/Core/AppConfig.swift",
        source.read_bytes(),
        1,
    )[0]
    copied = worker / "DungeonDoor" / "Assets.xcassets"
    icon = copied / "AppIcon.appiconset" / "AppIcon.png"
    accent = copied / "AccentColor.colorset" / "Contents.json"
    assert copied.is_dir() and not copied.is_symlink()
    assert (copied / "AppIcon.appiconset").is_dir()
    assert not (copied / "AppIcon.appiconset").is_symlink()
    assert icon.is_file() and not icon.is_symlink()
    assert icon.read_text(encoding="utf-8") == "png-bytes"
    assert accent.is_file() and not accent.is_symlink()
    assert accent.read_text(encoding="utf-8") == '{"color":"accent"}\n'
    assert (worker / "DungeonDoor" / "Notes.xcassets").is_symlink()
    assert (worker / "DungeonDoor" / "Preview").is_symlink()
    assert (worker / "Docs").is_symlink()
    assert (catalog / "AppIcon.appiconset").is_symlink()
    assert (shared / "AppIcon.png").read_text(encoding="utf-8") == "png-bytes"


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
    assert "My App.xcodeproj" in xcode_text.splitlines()
    assert "-scheme" in xcode_text
    assert "App" in xcode_text.splitlines()
    assert destination in xcode_text.splitlines()


_IOS_BODY = "func body(x: Int) -> Int {\n    if x > 0 {\n        return x\n    }\n    return 0\n}\n"

# A stand-in for xcodebuild. It realpaths the project it was given, then reads
# the Swift file named in that project. A symlink, or an absolute path back to
# the original tree, compiles the unmutated source and cannot kill a mutant.
_XCODEBUILD = """#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

def flag(argv, name):
    if name not in argv:
        return None
    return argv[argv.index(name) + 1]

def resolved(start, value):
    path = Path(value)
    if not path.is_absolute():
        path = start / path
    return path.resolve()

argv = sys.argv[1:]
cwd = Path.cwd()
log = Path(os.environ["XCODE_LOG"])
project = flag(argv, "-project")
workspace = flag(argv, "-workspace")
scheme = flag(argv, "-scheme")
with log.open("a", encoding="utf-8") as handle:
    handle.write(str(cwd) + "\\n")
    for arg in argv:
        handle.write(arg + "\\n")
    handle.write("---\\n")
bundle_arg = project or workspace
if bundle_arg is None or scheme is None:
    raise SystemExit(1)
bundle = resolved(cwd, bundle_arg)
scheme_file = bundle / "xcshareddata" / "xcschemes" / (scheme + ".xcscheme")
if not scheme_file.is_file():
    raise SystemExit(1)
project_bundle = bundle
if workspace is not None:
    data = (bundle / "contents.xcworkspacedata").read_text(encoding="utf-8")
    named = re.search(r"[\\w./-]+\\.xcodeproj", data)
    if named is None:
        raise SystemExit(1)
    project_bundle = resolved(bundle.parent, named.group(0))
pbx = project_bundle / "project.pbxproj"
if not pbx.is_file():
    raise SystemExit(1)
sources = re.findall(r"[\\w./-]+\\.swift", pbx.read_text(encoding="utf-8"))
if not sources:
    raise SystemExit(1)
body = ""
for relative in sources:
    file = (project_bundle.parent / relative).resolve()
    body += file.read_text(encoding="utf-8")
with log.open("a", encoding="utf-8") as handle:
    handle.write("compiled " + str(project_bundle) + "\\n")
    handle.write(body)
    handle.write("===\\n")
if ">=" in body or "!(" in body:
    raise SystemExit(1)
raise SystemExit(0)
"""

# actool rejects a symlinked catalog, then the build dies even when the mutant
# still compiles. A real catalog with AppIcon and AccentColor is accepted.
_CATALOG_BUILD = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

cwd = Path.cwd()
log = Path(os.environ["XCODE_LOG"])
found = []
for dirpath, dirnames, _filenames in os.walk(cwd):
    for name in list(dirnames):
        if name.endswith(".xcassets"):
            found.append(Path(dirpath) / name)
            dirnames.remove(name)
broken = []
for catalog in found:
    icon = catalog / "AppIcon.appiconset" / "AppIcon.png"
    accent = catalog / "AccentColor.colorset" / "Contents.json"
    if catalog.is_symlink() or not icon.is_file() or not accent.is_file():
        broken.append(str(catalog))
with log.open("a", encoding="utf-8") as handle:
    handle.write(str(cwd) + "\\n")
    handle.write("broken " + str(len(broken)) + "\\n")
    handle.write("---\\n")
if not found or broken:
    catalog = broken[0] if broken else str(cwd)
    sys.stderr.write(
        catalog
        + ': error: None of the input catalogs contained a matching '
        + 'stickers icon set, app icon set, or icon stack named "AppIcon".\\n'
    )
    raise SystemExit(65)
raise SystemExit(0)
"""


def _labels(output: str, description: str) -> list[str]:
    return [line.split()[0] for line in output.splitlines() if description in line]


def _install_xcodebuild(bin_dir: Path, log: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "xcodebuild"
    script.write_text(_XCODEBUILD, encoding="utf-8")
    script.chmod(0o755)


def _ios_source(root: Path, project_name: str) -> Path:
    project = root / project_name
    _write(project / "project.pbxproj", "path = App/ContentView.swift;\n")
    _write(project / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    _write(project / "xcshareddata" / "xcschemes" / "Zed.xcscheme", "<Scheme/>\n")
    return _write(root / "App" / "ContentView.swift", _IOS_BODY)


def _assert_worker_saw_the_mutant(output: str, log_text: str, source: Path) -> None:
    assert _labels(output, "> -> >=") == ["KILLED"]
    assert _labels(output, "x > 0 -> !(x > 0)") == ["KILLED"]
    assert _labels(output, "0 -> 1") == ["SURVIVED", "SURVIVED"]
    compiled = log_text.split("compiled ")[1:]
    worker = [chunk for chunk in compiled if "mutation-workers" in chunk.splitlines()[0]]
    assert worker
    assert any(">=" in chunk or "!(" in chunk for chunk in worker)
    assert any("return 1" in chunk for chunk in worker)
    assert source.read_text(encoding="utf-8") == _IOS_BODY


def test_xcodebuild_compiles_the_mutant_in_the_worker(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    log = tmp_path / "xcode.log"
    _install_xcodebuild(bin_dir, log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("XCODE_LOG", str(log))
    root = tmp_path / "ios"
    source = _ios_source(root, "My App.xcodeproj")
    destination = "platform=iOS Simulator,name=iPhone 16"
    code = run(
        [
            "--no-coverage",
            "--root",
            str(root),
            "--max-workers",
            "1",
            "--xcode-destination",
            destination,
        ]
    )
    captured = capsys.readouterr()
    text = log.read_text(encoding="utf-8")
    assert code == 3
    assert destination in text.splitlines()
    assert "My App.xcodeproj" in text.splitlines()
    scheme_at = text.splitlines().index("-scheme")
    assert text.splitlines()[scheme_at + 1] == "App"
    _assert_worker_saw_the_mutant(captured.out, text, source)
    snapshot = (root / ".metrics" / "mutate" / "My App.edn").read_text(encoding="utf-8")
    assert ":killed" in snapshot
    assert ":survived" in snapshot


def test_xcodebuild_workspace_compiles_the_mutant_in_the_worker(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    log = tmp_path / "xcode.log"
    _install_xcodebuild(bin_dir, log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("XCODE_LOG", str(log))
    root = tmp_path / "ios"
    _ios_source(root, "App.xcodeproj")
    workspace = root / "App.xcworkspace"
    _write(workspace / "contents.xcworkspacedata", '<FileRef location = "group:App.xcodeproj">\n')
    _write(workspace / "xcshareddata" / "xcschemes" / "App.xcscheme", "<Scheme/>\n")
    source = root / "App" / "ContentView.swift"
    code = run(["--no-coverage", "--root", str(root), "--max-workers", "1"])
    captured = capsys.readouterr()
    text = log.read_text(encoding="utf-8")
    assert code == 3
    assert "App.xcworkspace" in text.splitlines()
    assert "-workspace" in text.splitlines()
    _assert_worker_saw_the_mutant(captured.out, text, source)
    snapshot = (root / ".metrics" / "mutate" / "App.edn").read_text(encoding="utf-8")
    assert ":killed" in snapshot
    assert ":survived" in snapshot


def test_a_failing_xcodebuild_baseline_records_no_kills(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "xcodebuild"
    tool.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    root = tmp_path / "ios"
    source = _ios_source(root, "App.xcodeproj")
    code = run(["--no-coverage", "--root", str(root), "--max-workers", "1"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Baseline failed" in captured.err
    assert "xcodebuild" in captured.err
    assert source.read_text(encoding="utf-8") == _IOS_BODY
    assert not (root / ".metrics" / "mutate").exists()


def test_a_compiling_mutant_survives_when_the_worker_catalog_is_real(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    log = tmp_path / "xcode.log"
    bin_dir.mkdir()
    tool = bin_dir / "xcodebuild"
    tool.write_text(_CATALOG_BUILD, encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("XCODE_LOG", str(log))
    root = tmp_path / "ios"
    source = _write(root / "DungeonDoor" / "Core" / "AppConfig.swift", _IOS_BODY)
    project = root / "DungeonDoor.xcodeproj"
    _write(project / "project.pbxproj", "path = DungeonDoor/Core/AppConfig.swift;\n")
    _write(project / "xcshareddata" / "xcschemes" / "DungeonDoor.xcscheme", "<Scheme/>\n")
    catalog = root / "DungeonDoor" / "Assets.xcassets"
    _write(catalog / "AppIcon.appiconset" / "AppIcon.png", "png-bytes")
    _write(catalog / "AccentColor.colorset" / "Contents.json", '{"color":"accent"}\n')
    code = run(
        [
            "--no-coverage",
            "--root",
            str(root),
            "--max-workers",
            "1",
            "--xcode-destination",
            "platform=iOS Simulator,name=iPhone 17",
        ]
    )
    captured = capsys.readouterr()
    text = log.read_text(encoding="utf-8")
    assert code == 3
    assert "SURVIVED" in captured.out
    assert "KILLED" not in captured.out
    assert "mutation-workers" in text
    assert "broken 0" in text
    assert "broken 1" not in text
    assert source.read_text(encoding="utf-8") == _IOS_BODY
    assert catalog.is_dir() and not catalog.is_symlink()


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


def _swift_on_path(monkeypatch) -> None:
    if shutil.which("swift"):
        return
    toolchains = Path.home() / ".local" / "share" / "swiftly" / "toolchains"
    binaries = sorted(toolchains.glob("*/usr/bin/swift")) if toolchains.is_dir() else []
    if not binaries:
        pytest.skip("swift is not installed")
    monkeypatch.setenv("PATH", f"{binaries[-1].parent}{os.pathsep}{os.environ.get('PATH', '')}")


def test_swift_test_kills_and_survives_mutants_in_a_package(tmp_path, monkeypatch, capsys):
    _swift_on_path(monkeypatch)
    root = tmp_path / "pkg"
    _write(
        root / "Package.swift",
        "// swift-tools-version:5.9\n"
        "import PackageDescription\n"
        "let package = Package(name: \"Demo\", targets: [\n"
        "    .target(name: \"Demo\"),\n"
        "    .testTarget(name: \"DemoTests\", dependencies: [\"Demo\"]),\n"
        "])\n",
    )
    source = _write(
        root / "Sources" / "Demo" / "Place.swift",
        "func place(x: Int) -> Int {\n    if x > 0 {\n        return x\n    }\n    return 0\n}\n",
    )
    _write(
        root / "Tests" / "DemoTests" / "PlaceTests.swift",
        "import XCTest\n"
        "@testable import Demo\n"
        "final class PlaceTests: XCTestCase {\n"
        "    func testTwo() { XCTAssertEqual(place(x: 2), 2) }\n"
        "    func testOne() { XCTAssertEqual(place(x: 1), 1) }\n"
        "    func testZero() { XCTAssertEqual(place(x: 0), 0) }\n"
        "}\n",
    )
    code = run(
        ["--no-coverage", "--mutate-all", "--max-workers", "1", "--root", str(root)]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "KILLED" in captured.out
    assert "SURVIVED" in captured.out
    text = (root / ".metrics" / "mutate" / "Demo.edn").read_text(encoding="utf-8")
    assert ':namespace "Demo"' in text
    assert ':id "defn/place"' in text
    assert source.read_text(encoding="utf-8").startswith("func place")


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
