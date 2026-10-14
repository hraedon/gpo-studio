"""Plan 036 WP-0: the live lanes as the tooling sees them, held to the repository.

The launch table in `plan036_wp0/lanes.py` is copied from the runners; these
tests hold it to their text, so a runner that changes how it launches a
builder or finalizer cannot leave the replay running something else. They
also check, read-only, the banked state part 2 depends on: every pack matches
its batch manifest byte for byte, every recorded run input resolves, and every
verdict with a candidate records at least one candidate hash (a replay with
nothing to compare would be vacuous).
"""

from __future__ import annotations

import json
import re

import pytest
from plan036_support import REPO_ROOT, load

lanes = load("lanes")
replay = load("replay")

ORACLE = REPO_ROOT / "scripts" / "windows-oracle"
LIVE = lanes.live_lanes(REPO_ROOT)


def test_there_are_26_live_lanes_in_the_plans_four_shapes() -> None:
    assert len(LIVE) == 26
    assert {lane.name for lane in LIVE} == set(lanes.SHAPE_OF_LANE)
    for lane in LIVE:
        verdict = json.loads((REPO_ROOT / lane.verdict).read_text(encoding="utf-8"))
        shape = (
            "wp0-manifest" if "capability" in verdict
            else "candidates" if "candidates" in verdict
            else "rows-state" if "state" in verdict
            else "checks"
        )
        assert shape == lane.shape, lane.name


def test_every_runner_has_a_launch_spec() -> None:
    runners = {p.name for p in ORACLE.glob("run-*-oracle.sh")} | {"run-windows-oracle.sh"}
    assert runners == set(lanes.RUNNERS)


@pytest.mark.parametrize("runner", sorted(lanes.RUNNERS))
def test_the_launch_spec_is_what_the_runner_runs(runner: str) -> None:
    spec = lanes.RUNNERS[runner]
    text = (ORACLE / runner).read_text(encoding="utf-8")
    assert "uv run python -I" not in text and "python -I" not in text  # RUNNER_PYTHON_FLAGS
    assert lanes.RUNNER_PYTHON_FLAGS == ()
    for script, form in ((spec.builder, spec.builder_script_form),
                         (spec.finalizer, spec.finalizer_script_form)):
        if script is None:
            continue
        name = script.rsplit("/", 1)[1]
        if form == "absolute":
            assert re.search(r'"\$(REPO_ROOT/scripts/[a-z0-9-]+|SCRIPT_DIR)/' + re.escape(name),
                             text), (runner, name)
        else:
            assert re.search(r"uv run python " + re.escape(script), text), (runner, script)
    for token in spec.finalizer_argv:
        if token.startswith("--"):
            assert token in text, (runner, token)
    for token in spec.builder_argv:
        flag = token.split(":")[1] if token.startswith(("{optional:", "{flag:")) else token
        if flag.startswith("--"):
            assert flag in text, (runner, flag)
    if spec.builder_stdout:
        assert f'BUILDER_STDOUT="$CANDIDATE_DIR/{spec.builder_stdout}"' in text
        assert 'tee "$BUILDER_STDOUT"' in text
    if spec.flat_delivery_file:
        finalizer = (REPO_ROOT / spec.finalizer).read_text(encoding="utf-8")
        assert f'"{spec.flat_delivery_file}"' in finalizer, (runner, spec.flat_delivery_file)


@pytest.mark.parametrize("lane", LIVE, ids=lambda lane: lane.name)
def test_the_pack_matches_its_batch_manifest(lane: object) -> None:
    assert replay.verify_pack(REPO_ROOT, lane) == []


@pytest.mark.parametrize("lane", LIVE, ids=lambda lane: lane.name)
def test_every_recorded_run_input_resolves(lane: object) -> None:
    values = lanes.resolve_inputs(REPO_ROOT, lane)
    if "transport" in values:
        assert values["transport"] == "psdirect"
    if "guest_status" in values:
        assert values["guest_status"] == "0"


@pytest.mark.parametrize("lane", [lane for lane in LIVE if lane.has_candidate],
                         ids=lambda lane: lane.name)
def test_every_verdict_records_its_candidate_hashes(lane: object) -> None:
    verdict = json.loads((REPO_ROOT / lane.verdict).read_text(encoding="utf-8"))
    recorded = replay.banked_candidate_hashes(verdict, lane.spec.flat_delivery_file)
    banked = replay.tree_digests(REPO_ROOT / lane.pack / "controller-candidate")
    assert recorded, f"{lane.name}: nothing to replay against"
    for name, digest in recorded.items():
        assert banked.get(name) == digest, (lane.name, name)


def test_a_flat_candidate_delivery_must_name_its_file() -> None:
    with pytest.raises(load("repo").Wp0Error, match="flat candidate_delivery"):
        replay.banked_candidate_hashes({"candidate_delivery": {"controller_sha256": "0" * 64}})
