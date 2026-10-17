"""The current qualification, and the freeze batch once one is banked.

``tests/current_qualification.py`` says which banked run certifies each lane
now: the release 1.1.0rc1 batches while the release evidence report's
``freeze`` is null, the freeze batch after. The first tests hold it to the
registry and the release evidence. The rest check the freeze batch the way the
release 1.1.0 batch tests check theirs, reading everything from its manifest,
so that banking the 1.1.0 final batch (commit B, after the freeze commit F)
needs no test edit: the final-tag release gate refuses one (Plan 036, the D-9
addendum). Until a freeze batch is banked they skip, saying so.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import current_qualification as cq
import pytest
from batch_provenance import exec_failed_problems, scope_provenance_problems

ROOT = cq.ROOT
EVIDENCE = cq.EVIDENCE


@cache
def _registry() -> dict[str, Any]:
    return runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))


def test_the_current_qualification_is_the_live_set() -> None:
    registry = _registry()
    runs = cq.current_runs()
    assert {r["verdict"] for n, r in runs.items() if n != "wp0"} == set(
        registry["LIVE_VERDICTS"]
    )
    assert runs["wp0"]["verdict"] == registry["WP0_MANIFEST"]


def test_the_current_batches_follow_the_report() -> None:
    frozen = cq.freeze()
    if frozen is None:
        assert cq.current_batches() == (cq.RC_BATCH, cq.RC_SUCCESSORS)
    else:
        assert cq.current_batches() == (frozen["batch_manifest"],)


def test_the_release_evidence_names_every_current_run() -> None:
    document = (ROOT / f"docs/release-evidence-{cq.package_base_version()}.md").read_text(
        encoding="utf-8"
    )
    missing = sorted(r["run_id"] for r in cq.current_runs().values() if r["run_id"] not in document)
    assert not missing, missing


# --- the freeze batch ------------------------------------------------------------


def _frozen() -> tuple[str, dict[str, Any]]:
    frozen = cq.freeze()
    if frozen is None:
        pytest.skip("no freeze batch yet: the release evidence report's freeze is null")
    manifest = json.loads((ROOT / frozen["batch_manifest"]).read_text(encoding="utf-8"))
    return frozen["commit"], manifest


def test_the_freeze_batch_states_its_provenance() -> None:
    _, batch = _frozen()
    assert scope_provenance_problems(batch) == []
    assert exec_failed_problems(batch) == []
    assert batch["not_passed"] == [] and batch["successors"] == []


def test_the_freeze_batch_reran_every_lane_at_the_freeze_commit() -> None:
    commit, batch = _frozen()
    assert batch["source_commit"] == commit
    assert batch["lanes_started"] == len(batch["runs"])
    names = [run["name"] for run in batch["runs"]]
    assert len(set(names)) == len(names)
    for run in batch["runs"]:
        assert run["commit"] == commit, run["name"]
        assert run["exit_status"] == 0, run["name"]
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8-sig"))
        assert verdict["run_id"] == run["run_id"], run["name"]
        assert verdict["source"]["commit"] == commit, run["name"]
        assert verdict["source"]["dirty"] is False, run["name"]


def test_every_freeze_batch_file_matches_its_banked_hash() -> None:
    _, batch = _frozen()
    for run in batch["runs"]:
        pack = (EVIDENCE / run["verdict"]).parent
        banked = {
            p.relative_to(pack).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pack.rglob("*"))
            if p.is_file()
        }
        assert banked == run["files"], run["name"]
    cleanup = batch["post_batch_cleanup"]
    assert cleanup, "the freeze batch has no post-batch directory check"
    for relative, digest in cleanup.items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == digest


def test_the_freeze_batch_post_batch_check_is_clean() -> None:
    _, batch = _frozen()
    (directory,) = [path for path in batch["post_batch_cleanup"] if path.endswith(".json")]
    capture = json.loads((EVIDENCE / directory).read_text(encoding="utf-8-sig"))
    assert capture["computer_restored"] is True and capture["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert capture[key] == [], key


def test_every_freeze_batch_digest_resolves_at_its_commit() -> None:
    _, batch = _frozen()
    for run in batch["runs"]:
        source = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8-sig"))[
            "source"
        ]
        if "bound" in source:
            rows = {row["path"]: row["sha256"] for row in source["bound"]}
        else:
            rows = {source["paths"][name]: digest for name, digest in source["files"].items()}
        for path, recorded in rows.items():
            blob = subprocess.run(
                ["git", "show", f"{source['commit']}:{path}"],
                cwd=ROOT, capture_output=True, check=True,
            ).stdout
            assert hashlib.sha256(blob).hexdigest() == recorded, (run["name"], path)


def test_every_freeze_batch_candidate_is_the_banked_controller_candidate() -> None:
    _, batch = _frozen()
    for run in batch["runs"]:
        if run["name"] == "wp0":
            continue
        pack = (EVIDENCE / run["verdict"]).parent
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8-sig"))
        for relative, digest in (verdict.get("candidate") or {}).items():
            banked = (pack / "controller-candidate" / Path(relative)).read_bytes()
            assert hashlib.sha256(banked).hexdigest() == digest, (run["name"], relative)
