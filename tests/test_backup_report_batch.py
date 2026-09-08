"""The two targeted WI-060 successors retain their complete native evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/plan-033"


def test_targeted_successors_are_complete_clean_and_restored() -> None:
    batch = json.loads((EVIDENCE / "backup-report-batch.json").read_text(encoding="utf-8"))
    assert len(batch["runs"]) == 2
    assert {r["name"] for r in batch["runs"]} == {"scripts-metadata", "publication"}
    assert len({r["run_id"] for r in batch["runs"]}) == 2
    for run in batch["runs"]:
        directory = (EVIDENCE / run["verdict"]).parent
        assert set(run["files"]) == {
            p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
        }
        for relative, expected in run["files"].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8"))
        assert verdict["passed"] is True
        assert verdict["run_id"] == run["run_id"]
        assert verdict["source"]["commit"] == run["commit"] == batch["source_commit"]
        assert verdict["source"]["dirty"] is False
        native = json.loads((directory / "result.json").read_text(encoding="utf-8-sig"))
        assert native["cleanup_succeeded"] is True
        assert native["cleanup_state_restored"] is True
        assert native["environment"]["computer_system_domain_role"] == 3
        previous = json.loads((EVIDENCE / run["replaces"]).read_text(encoding="utf-8"))
        assert previous["run_id"] != verdict["run_id"]
        assert previous["source"]["commit"] != verdict["source"]["commit"]
