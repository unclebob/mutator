from mutator.edn import dumps, loads
from mutator.metrics import HistoryIndex, load_all_history, load_history, snapshot_path, write_results
from mutator.model import FormResult, History


def _form(namespace, name, file_name, digest, killed=1, private=False):
    form_id = f"defn-/{name}" if private else f"defn/{name}"
    return FormResult(
        id=form_id,
        namespace=namespace,
        name=name,
        private=private,
        file=file_name,
        line=2,
        end_line=4,
        digest=digest,
        killed=killed,
        survived=0,
        uncovered=1,
        sites=2,
    )


def _viewer_name(form_id):
    """The same split uml-viewer.application.overlay/form-name performs."""

    if form_id.startswith("defn-/"):
        return form_id[6:], True
    if form_id.startswith("defn/"):
        return form_id[5:], False
    return None, False


def test_one_snapshot_per_namespace_preserves_the_other_file(tmp_path):
    write_results(
        tmp_path,
        "a.go",
        [_form("demo", "Run", "a.go", "hash-a")],
        {},
    )
    write_results(
        tmp_path,
        "b.go",
        [_form("demo", "Stop", "b.go", "hash-b", killed=4)],
        {},
    )
    path = snapshot_path(tmp_path, "demo")
    data = loads(path.read_text(encoding="utf-8"))
    assert data["namespace"] == "demo"
    names = []
    for form in data["forms"]:
        operation, private = _viewer_name(form["id"])
        names.append((operation, private, form["killed"], form["uncovered"], form["sites"]))
    assert ("Run", False, 1, 1, 2) in names
    assert ("Stop", False, 4, 1, 2) in names
    assert list(tmp_path.joinpath(".metrics", "mutate").rglob("*.edn")) == [path]

    write_results(
        tmp_path,
        "a.go",
        [_form("demo", "Run", "a.go", "hash-a", killed=9)],
        {},
    )
    again = loads(path.read_text(encoding="utf-8"))
    by_id = {form["id"]: form for form in again["forms"]}
    assert by_id["defn/Run"]["killed"] == 9
    assert by_id["defn/Stop"]["killed"] == 4


def test_private_form_id_and_history_round_trip(tmp_path):
    outcome = '["hide.clj","demo.core","defn-/hide",8,12,"false","true"]'
    write_results(
        tmp_path,
        "hide.clj",
        [_form("demo.core", "hide", "hide.clj", "digest", private=True)],
        {outcome: "killed"},
    )
    data = loads(snapshot_path(tmp_path, "demo.core").read_text(encoding="utf-8"))
    assert _viewer_name(data["forms"][0]["id"]) == ("hide", True)
    history = load_history(tmp_path, "hide.clj")
    assert history.forms[("demo.core", "defn-/hide")].digest == "digest"
    assert history.outcomes[outcome] == "killed"


def test_snapshot_paths_follow_the_namespace(tmp_path):
    assert snapshot_path(tmp_path, "example.com/demo.Widget") == (
        tmp_path / ".metrics" / "mutate" / "example.com" / "demo.Widget.edn"
    )
    assert snapshot_path(tmp_path, "crate::foo::Bar") == (
        tmp_path / ".metrics" / "mutate" / "crate" / "foo" / "Bar.edn"
    )


def test_a_non_canonical_snapshot_is_folded_into_the_namespace_file(tmp_path):
    stale = tmp_path / ".metrics" / "mutate" / "legacy.edn"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        dumps(
            {
                "version": 1,
                "namespace": "demo",
                "source": "b.go",
                "forms": [
                    {
                        "id": "defn/Stop",
                        "file": "b.go",
                        "line": 3,
                        "hash": "hash-b",
                        "killed": 4,
                        "survived": 0,
                        "uncovered": 1,
                        "sites": 2,
                    }
                ],
                "outcomes": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_results(tmp_path, "a.go", [_form("demo", "Run", "a.go", "hash-a")], {})
    assert not stale.exists()
    data = loads(snapshot_path(tmp_path, "demo").read_text(encoding="utf-8"))
    by_id = {form["id"]: form for form in data["forms"]}
    assert by_id["defn/Stop"]["killed"] == 4
    assert by_id["defn/Run"]["file"] == "a.go"


def test_dropping_a_file_removes_its_namespace_when_nothing_else_remains(tmp_path):
    write_results(tmp_path, "a.go", [_form("demo", "Run", "a.go", "hash-a")], {})
    write_results(tmp_path, "a.go", [], {})
    assert not snapshot_path(tmp_path, "demo").exists()


def test_load_all_history_matches_load_history_across_files_and_namespaces(tmp_path, monkeypatch):
    import mutator.metrics as metrics_module

    outcome_a = '["a.go","demo","defn/Run",2,3,"+","-"]'
    outcome_b = '["b.go","demo","defn/Stop",2,3,">","<"]'
    outcome_c = '["pkg/c.py","pkg.c","defn/go",2,3,"1","0"]'
    write_results(tmp_path, "a.go", [_form("demo", "Run", "a.go", "hash-a")], {outcome_a: "killed"})
    write_results(tmp_path, "b.go", [_form("demo", "Stop", "b.go", "hash-b")], {outcome_b: "survived"})
    write_results(tmp_path, "pkg/c.py", [_form("pkg.c", "go", "pkg/c.py", "hash-c")], {outcome_c: "killed"})

    mutate_dir = tmp_path / ".metrics" / "mutate"
    (mutate_dir / "broken.edn").write_text("{", encoding="utf-8")
    (mutate_dir / "scalar.edn").write_text("123", encoding="utf-8")
    (mutate_dir / "no_ns.edn").write_text(dumps({"forms": [], "outcomes": {}}), encoding="utf-8")
    (mutate_dir / "00_older.edn").write_text(
        dumps(
            {
                "version": 1,
                "namespace": "demo",
                "source": "a.go",
                "forms": [{"id": "defn/Run", "hash": "stale-hash"}],
                "outcomes": {"not-json": ":killed", outcome_a: ":survived"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    orig_edn_files = metrics_module._edn_files
    edn_calls = []

    def counting_edn_files(root):
        edn_calls.append(root)
        return orig_edn_files(root)

    monkeypatch.setattr(metrics_module, "_edn_files", counting_edn_files)
    index = load_all_history(tmp_path)
    assert edn_calls == [tmp_path]

    for key in ("a.go", "b.go", "pkg/c.py", "missing.py"):
        assert index.for_file(key) == load_history(tmp_path, key)

    copy_a = index.for_file("a.go")
    copy_a.forms.clear()
    copy_a.outcomes.clear()
    assert ("demo", "defn/Run") in index.for_file("a.go").forms
    assert outcome_a in index.for_file("a.go").outcomes
    assert HistoryIndex().for_file("missing.py") == History()
