"""Pin the WI-028 observation and its byte provenance, without minting a verdict."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033/wi028-evidence"


def read(name: str):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def test_banked_captures_and_collector_retain_their_exact_bytes() -> None:
    provenance = read("provenance.json")
    assert provenance["kind"] == "diagnostic-observation-not-conformance-verdict"
    assert set(provenance["files"]) == {p.name for p in EVIDENCE.iterdir()} - {"provenance.json"}
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == expected, name
    assert (
        hashlib.sha256((ROOT / provenance["collector"]).read_bytes()).hexdigest()
        == provenance["collector_sha256"]
    )
    for phase in ("before", "during", "after"):
        artifacts = {
            row["name"]: row["sha256"]
            for row in provenance["captures"][phase]["raw_artifacts"]
        }
        excerpt = read(f"{phase}-searched-som.json")
        assert excerpt["source_sha256"] == artifacts["gpresult.xml"]
        assert provenance["files"][f"{phase}-snapshot.json"] == artifacts["snapshot.json"]


def test_deleted_ou_survives_refresh_in_both_views() -> None:
    author = read("author.json")
    cleanup = read("cleanup.json")
    assert cleanup["restored_dn"] == author["original_dn"]
    assert cleanup["removed_ou"] == author["ou_dn"]
    assert cleanup["residual_count"] == 0
    marker_rows = []
    for phase, count in (("before", 9), ("during", 10), ("after", 10)):
        snapshot = read(f"{phase}-snapshot.json")
        rows = snapshot["som_after_gpresult"]
        assert rows == snapshot["som_before_gpresult"]
        assert len(rows) == count
        assert len(read(f"{phase}-searched-som.json")["rows"]) == count
        selected = [row for row in rows if row["id"] == author["ou_dn"]]
        assert len(selected) == (0 if phase == "before" else 1)
        xml_selected = [
            row for row in read(f"{phase}-searched-som.json")["rows"]
            if row["Path"] == "ad.labdomain.dev/" + author["run"]
        ]
        assert len(xml_selected) == len(selected)
        if selected:
            marker_rows.append(selected[0])
            assert xml_selected[0]["Order"] == str(selected[0]["SOMOrder"])
            assert xml_selected[0]["Reason"] == "Normal"
            assert selected[0]["reason"] == 1
        if phase != "before":
            assert snapshot["forced_refresh"] is True
    assert marker_rows[0] == marker_rows[1]


def test_session_scope_changes_while_creation_time_does_not_scope_a_run() -> None:
    before = read("before-snapshot.json")["session"][0]
    during = read("during-snapshot.json")["session"][0]
    after = read("after-snapshot.json")["session"][0]
    assert before["SOM"] == after["SOM"]
    assert during["SOM"] == read("author.json")["ou_dn"]
    assert during["SOM"] != after["SOM"]
    assert before["creationTime"] == during["creationTime"] == after["creationTime"]
    assert before["id"] == during["id"] == after["id"]
