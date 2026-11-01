"""The current qualification, and the freeze batch once one is banked.

``tests/current_qualification.py`` says which banked run certifies each lane
now: while the release evidence report's ``freeze`` is null, the freeze batch
of the previous release (with any run the registry has made live since), and
the release's own freeze batch after. The first tests hold it to the registry
and the release evidence. The rest check the freeze batch the way the release
1.1.0 batch tests check theirs, reading everything from its manifest, so that
banking a release's final batch (commit B, after the freeze commit F) needs no
test edit: the final-tag release gate refuses one (Plan 036, the D-9
addendum). Before this release's B they check the previous release's freeze
batch, which is still the base of the current qualification.
"""

from __future__ import annotations

import hashlib
import json
import re
import runpy
import subprocess
from collections.abc import Iterator
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
    """Live, or pending requalification by the next batch (it still certifies nothing new)."""
    registry = _registry()
    runs = cq.current_runs()
    assert {r["verdict"] for n, r in runs.items() if n != "wp0"} == set(
        registry["LIVE_VERDICTS"]
    ) | set(registry["PENDING_REQUALIFICATION"])
    assert runs["wp0"]["verdict"] == registry["WP0_MANIFEST"]


def test_the_current_batches_follow_the_report() -> None:
    frozen = cq.freeze()
    if frozen is None:
        previous = cq.release_report(cq.previous_release())
        assert previous["status"] == "approved"
        assert cq.current_batches()[0] == previous["freeze"]["batch_manifest"]
    else:
        assert cq.current_batches() == (frozen["batch_manifest"],)


def _clear() -> None:
    for cached in (cq.previous_release, cq.current_batches, cq.current_runs):
        cached.cache_clear()


@pytest.fixture
def fresh() -> Iterator[None]:
    """Selector caches cleared around a test that substitutes its inputs."""
    _clear()
    try:
        yield
    finally:
        _clear()


def _tagged_finals() -> list[tuple[int, int, int]]:
    """Final release tags in HEAD's history, read here independently of the module."""
    names = subprocess.run(
        ["git", "tag", "--merged", "HEAD", "--list", "v*"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    found = []
    for name in names:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", name)
        if match is not None:
            found.append((int(match[1]), int(match[2]), int(match[3])))
    return found


def test_the_previous_release_is_the_latest_final_tag_below_the_version() -> None:
    """Bound to the release tags, and its records agree with what the tag released."""
    current = cq._version_key(cq.package_base_version())
    expected = max(version for version in _tagged_finals() if version < current)
    previous = cq.previous_release()
    assert cq._version_key(previous) == expected
    report = f"docs/release-evidence-report-{previous}.json"
    assert cq.release_problems(
        previous,
        (ROOT / report).read_bytes(),
        (ROOT / f"docs/release-evidence-{previous}.md").read_bytes(),
        cq._read_at_tag(previous, report),
    ) == []
    data = cq.release_report(previous)
    assert (data["version"], data["status"]) == (previous, "approved")
    assert cq.previous_freeze() == data["freeze"]


def _previous_records() -> tuple[str, dict[str, Any], bytes, bytes]:
    previous = cq.previous_release()
    report = f"docs/release-evidence-report-{previous}.json"
    data = json.loads((ROOT / report).read_text(encoding="utf-8"))
    return previous, data, (ROOT / f"docs/release-evidence-{previous}.md").read_bytes(), (
        cq._read_at_tag(previous, report)
    )


def _encoded(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("ascii")


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda d: d.update(version="9.9.9"), id="version-not-the-file-name"),
        pytest.param(lambda d: d.update(status="candidate"), id="not-approved"),
        pytest.param(lambda d: d.update(freeze=None), id="no-freeze"),
        pytest.param(
            lambda d: d.update(freeze={**d["freeze"], "commit": "0" * 40}),
            id="freeze-not-the-released-one",
        ),
        pytest.param(
            lambda d: d.update(manifest="docs/release-evidence-9.9.9.md"), id="other-manifest"
        ),
    ],
)
def test_a_predecessor_report_that_is_not_its_release_fails(
    monkeypatch: pytest.MonkeyPatch, fresh: None, change: Any
) -> None:
    """Daybreak #166 r1: a 9.9.9 report under 1.1.0's file name was accepted."""
    previous, data, manifest, released = _previous_records()
    change(data)
    assert cq.release_problems(previous, _encoded(data), manifest, released) != []
    report_rel = f"docs/release-evidence-report-{previous}.json"
    real_read = cq._read
    monkeypatch.setattr(
        cq, "_read", lambda rel: _encoded(data) if rel == report_rel else real_read(rel)
    )
    _clear()
    with pytest.raises(AssertionError, match=f"previous release, {re.escape(previous)}"):
        cq.previous_release()
    with pytest.raises(AssertionError):
        cq.current_batches()


def test_a_predecessor_manifest_for_another_release_fails(fresh: None) -> None:
    previous, data, manifest, released = _previous_records()
    other = manifest.replace(
        f"GPO Studio {previous}".encode(), b"GPO Studio 9.9.9"
    ).replace(f"Application version: {previous}".encode(), b"Application version: 9.9.9")
    assert other != manifest
    assert cq.release_problems(previous, _encoded(data), other, released) != []
    assert cq.release_problems(previous, _encoded(data), manifest, released) == []


def test_a_released_predecessor_without_records_fails_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, fresh: None
) -> None:
    """A tagged release with no committed records is a gap, not a reason to look further back.

    The package is made 99.0.0 and a final tag v98.0.0 is added above every real
    one: the selector must bind 98.0.0 and fail on its missing records, not skip
    to the real previous release.
    """
    real = cq._final_tags()
    monkeypatch.setattr(cq, "package_base_version", lambda: "99.0.0")
    monkeypatch.setattr(cq, "_final_tags", lambda: [*real, "98.0.0"])
    _clear()
    with pytest.raises(AssertionError, match="98.0.0"):
        cq.previous_release()


def test_with_no_earlier_final_tag_the_selector_fails(
    monkeypatch: pytest.MonkeyPatch, fresh: None
) -> None:
    """A clone without tags (not fetch-depth: 0) cannot say what shipped last."""
    monkeypatch.setattr(cq, "_final_tags", lambda: [])
    _clear()
    with pytest.raises(AssertionError, match="fetch-depth: 0"):
        cq.previous_release()


def test_a_run_made_live_after_the_previous_release_replaces_its_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override is by live verdict, so a pending one never displaces a base run."""
    if cq.freeze() is not None:
        pytest.skip("this release's freeze batch is banked; there is no base to override")
    base = cq.previous_freeze()["batch_manifest"]
    rows = json.loads((ROOT / base).read_text(encoding="utf-8"))["runs"]
    lane = next(row for row in rows if row["name"] != "wp0")
    later = next(
        relative
        for relative in sorted(
            p.relative_to(ROOT).as_posix() for p in EVIDENCE.glob("*-batch.json")
        )
        if relative != base
        and any(
            row["name"] == lane["name"]
            for row in json.loads((ROOT / relative).read_text(encoding="utf-8"))["runs"]
        )
    )
    replacement = next(
        row
        for row in json.loads((ROOT / later).read_text(encoding="utf-8"))["runs"]
        if row["name"] == lane["name"]
    )
    live = cq._registry_live()
    monkeypatch.setattr(cq, "_registry_live", lambda: live | {replacement["verdict"]})
    cq.current_batches.cache_clear()
    cq.current_runs.cache_clear()
    try:
        assert cq.current_batches() == (base, later)
        runs = cq.current_runs()
        assert runs[lane["name"]]["verdict"] == replacement["verdict"]
        others = {row["name"]: row["verdict"] for row in rows if row["name"] != lane["name"]}
        assert {name: runs[name]["verdict"] for name in others} == others
    finally:
        cq.current_batches.cache_clear()
        cq.current_runs.cache_clear()


def test_the_release_evidence_names_every_current_run() -> None:
    document = (ROOT / f"docs/release-evidence-{cq.package_base_version()}.md").read_text(
        encoding="utf-8"
    )
    missing = sorted(r["run_id"] for r in cq.current_runs().values() if r["run_id"] not in document)
    assert not missing, missing


# --- the freeze batch ------------------------------------------------------------


def _frozen() -> tuple[str, dict[str, Any]]:
    """The newest banked freeze batch: this release's, or before B the previous one's.

    So the checks below keep running between releases, on the batch the previous
    release shipped on, rather than skipping until this release's B.
    """
    frozen = cq.latest_freeze()
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
