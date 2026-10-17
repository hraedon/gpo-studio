"""Plan 036 WP-0 part 2: replay, re-grade and the canonical claim projection.

`plans/036-change-cost.md`, "What part 2 meets": the negative meta-tests
(omitted input, flipped check, dropped check, changed finding, tampered pack
member, unexpected `source.paths` entry) run against the tool, and every
provenance invariant and one leaf per top-level key per shape is mutated.

The end-to-end tests drive the real `replay.replay_builder` and
`replay.regrade` against a synthetic lane: a temporary git repository with a
builder, a finalizer that binds its own tables the way the lane finalizers do,
and a banked pack. Planted commits change the finalizer or the builder; each
must be refused.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from plan036_support import REPO_ROOT, git_env, load

lanes = load("lanes")
replay = load("replay")
projection = load("projection")
changed = load("changed")
repo_mod = load("repo")

# --- the synthetic lane --------------------------------------------------------------

FINALIZER = '''\
import argparse, hashlib, json, subprocess
from pathlib import Path

DEPLOYED_FILES = {"guest.ps1": "guest.ps1"}
LOCAL_FILES = {"fin.py": "fin.py", "build.py": "build.py"}
FLIP = False
DROP = False
FINDING = "ok"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    result = json.loads((args.run_dir / "result.json").read_text())
    checks = {
        "guest_succeeded": result["ok"] is True,
        "candidate_intact": sha(args.candidate_root / "candidate.bin") == result["candidate"],
    }
    if FLIP:
        checks["guest_succeeded"] = not checks["guest_succeeded"]
    if DROP:
        del checks["candidate_intact"]
    git = lambda *a: subprocess.run(["git", *a], cwd=args.repo_root, capture_output=True,
                                    text=True, check=True).stdout
    tables = {**DEPLOYED_FILES, **LOCAL_FILES}
    verdict = {
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "findings": [FINDING],
        "candidate": {
            path.name: sha(path) for path in sorted(args.candidate_root.iterdir())
        },
        "source": {
            "commit": git("rev-parse", "HEAD").strip(),
            "dirty": bool(git("status", "--porcelain")),
            "paths": tables,
            "files": {name: sha(args.repo_root / path) for name, path in tables.items()},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
    }
    (args.run_dir / "verification.json").write_text(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
BUILDER = '''\
import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True)
(out / "candidate.bin").write_bytes(b"CANDIDATE")
print("built")
'''


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True,
                          env=git_env()
                          ).stdout.decode("utf-8").strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("synthetic") / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t"),
                       ("core.autocrlf", "false")):
        _git(root, "config", key, value)
    (root / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "keep.txt").write_text("x\n", encoding="utf-8")
    (root / "guest.ps1").write_text("Write-Output ok\n", encoding="utf-8")
    (root / "fin.py").write_text(FINALIZER, encoding="utf-8")
    (root / "build.py").write_text(BUILDER, encoding="utf-8")
    base = _commit(root, "lane")
    # The controller's run directory (outside the repository, as on the estate)
    # and the candidate the builder made; the finalizer grades them at `base`.
    outside = root.parent / "controller"
    run, candidate_dir = outside / "run", outside / "candidate"
    run.mkdir(parents=True)
    candidate_dir.mkdir()
    candidate = b"CANDIDATE"
    (candidate_dir / "candidate.bin").write_bytes(candidate)
    (candidate_dir / "builder.stdout.txt").write_bytes(b"built\n")
    (run / "result.json").write_text(json.dumps({
        "ok": True, "run_id": "synthetic-1", "candidate": hashlib.sha256(candidate).hexdigest()
    }), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(root / "fin.py"), str(run), "--candidate-root", str(candidate_dir),
         "--repo-root", str(root), "--no-tag"],
        check=True, capture_output=True,
    )
    banked = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert banked["passed"] is True and banked["source"]["commit"] == base
    assert banked["source"]["dirty"] is False
    # Banking: the run directory verbatim, plus controller-candidate/ and a log.
    import shutil

    pack = root / "pack"
    shutil.copytree(run, pack)
    shutil.copytree(candidate_dir, pack / "controller-candidate")
    (pack / "controller.log").write_text("SCENARIO=synthetic\n", encoding="utf-8")
    bank = _commit(root, "bank")
    planted = {}
    for name, text in (
        ("flip", FINALIZER.replace("FLIP = False", "FLIP = True")),
        ("drop", FINALIZER.replace("DROP = False", "DROP = True")),
        ("finding", FINALIZER.replace('FINDING = "ok"', 'FINDING = "changed"')),
        ("widened", FINALIZER.replace('"build.py": "build.py"}',
                                      '"build.py": "build.py", "keep.txt": "src/keep.txt"}')),
    ):
        _git(root, "checkout", "-q", "--detach", bank)
        (root / "fin.py").write_text(text, encoding="utf-8")
        planted[name] = _commit(root, name)
    for name, text in (
        ("builder-changed", BUILDER.replace("CANDIDATE", "CHANGED!!")),
        ("builder-writes-version", BUILDER.replace(
            'write_bytes(b"CANDIDATE")',
            'write_bytes(b"CANDIDATE" + open("src/version.txt", "rb").read())')),
    ):
        _git(root, "checkout", "-q", "--detach", bank)
        (root / "build.py").write_text(text, encoding="utf-8")
        (root / "src" / "version.txt").write_text("1.1.0\n", encoding="utf-8")
        planted[name] = _commit(root, name)
    _git(root, "checkout", "-q", "--detach", bank)
    files = replay.tree_digests(pack)
    lane = lanes.Lane(
        name="synthetic", verdict="pack/verification.json", pack="pack", commit=base,
        runner="run-synthetic.sh",
        spec=lanes.RunnerSpec(
            builder="build.py", builder_argv=("{out}",), builder_script_form="relative",
            finalizer="fin.py",
            finalizer_argv=("{run}", "--candidate-root", "{candidate}", "--repo-root", "{repo}"),
            finalizer_script_form="relative", builder_stdout="builder.stdout.txt",
        ),
        shape="checks", original_verdict_name="verification.json", batch_manifest="",
        pack_files=files,
    )
    return {"root": root, "base": base, "planted": planted, "lane": lane, "banked": banked,
            "work": tmp_path_factory.mktemp("work")}


def _regrade(synthetic: dict[str, Any], commit: str, label: str) -> dict[str, Any]:
    root, lane = synthetic["root"], synthetic["lane"]
    with replay.clone(root, commit, synthetic["work"] / "clones", f"c-{label}",
                      launcher="current") as at:
        result = replay.regrade(lane, root, at, synthetic["work"], "plain", label)
        tables = projection.finalizer_tables(at.python, at.path, lane.spec.finalizer, "psdirect")
    banked = projection.Side(synthetic["banked"], synthetic["base"],
                             {"deployed": {"guest.ps1": "guest.ps1"},
                              "local": {"fin.py": "fin.py", "build.py": "build.py"}},
                             "", "", lane.pack_files.get)
    regraded = projection.Side(result["verdict"] or {}, commit, tables, result.get("run_root", ""),
                               result.get("candidate_root", ""), lambda member: None)
    return {**result, "compare": projection.compare(banked, regraded, root, lane.shape)}


def test_the_regrade_at_the_verdicts_own_commit_reproduces_it(synthetic: dict[str, Any]) -> None:
    outcome = _regrade(synthetic, synthetic["base"], "own")
    assert outcome["problems"] == []
    assert outcome["compare"]["equal"], outcome["compare"]


def test_a_widened_table_is_compared_relationally_not_refused(synthetic: dict[str, Any]) -> None:
    """Sol round-3 B1: a selected lane's finalizer at F binds more files, so its
    source.paths differ from the banked verdict's; each side is checked against its
    own commit's tables, and the payload is compared exactly."""
    outcome = _regrade(synthetic, synthetic["planted"]["widened"], "widened")
    assert outcome["compare"]["equal"], outcome["compare"]
    assert outcome["verdict"]["source"]["paths"]["keep.txt"] == "src/keep.txt"


@pytest.mark.parametrize("planted", ["flip", "drop", "finding"])
def test_a_planted_finalizer_change_is_refused(synthetic: dict[str, Any], planted: str) -> None:
    outcome = _regrade(synthetic, synthetic["planted"][planted], planted)
    assert not outcome["compare"]["equal"]
    assert outcome["compare"]["payload"], planted


def _replay(synthetic: dict[str, Any], commit: str, label: str) -> dict[str, Any]:
    root, lane = synthetic["root"], synthetic["lane"]
    work = synthetic["work"] / f"replay-{label}"
    with replay.clone(root, commit, work / "clones", "a", launcher="current") as a, \
            replay.clone(root, commit, work / "clones", "b", launcher="current") as b:
        result: dict[str, Any] = replay.replay_builder(lane, root, a, b, work)
    return result


def test_the_replay_reproduces_the_banked_candidate(synthetic: dict[str, Any]) -> None:
    result = _replay(synthetic, synthetic["base"], "base")
    assert result["identical"], result["problems"]
    assert result["candidate"]["builder.stdout.txt"]  # the tee'd stdout is part of it


@pytest.mark.parametrize("planted", ["builder-changed", "builder-writes-version"])
def test_a_planted_candidate_change_makes_the_replay_differ(
    synthetic: dict[str, Any], planted: str
) -> None:
    """Including the plan's planted builder that writes the version into its candidate:
    gate (b) of the release-metadata exemption then fails, so the line counts."""
    result = _replay(synthetic, synthetic["planted"][planted], planted)
    assert not result["identical"]


def test_an_omitted_run_input_is_refused(synthetic: dict[str, Any], tmp_path: Path) -> None:
    lane = synthetic["lane"]
    spec = lanes.RunnerSpec(**{**lane.spec.__dict__, "inputs": (
        ("guest_status", (lanes.Source("verdict", "/guest_status"),)),)})
    with pytest.raises(repo_mod.Wp0Error, match="guest_status"):
        lanes.resolve_inputs(synthetic["root"], lanes.Lane(**{**lane.__dict__, "spec": spec}))


def test_disagreeing_sources_for_one_input_are_refused(synthetic: dict[str, Any]) -> None:
    lane = synthetic["lane"]
    spec = lanes.RunnerSpec(**{**lane.spec.__dict__, "inputs": (
        ("scenario", (lanes.Source("controller-log", "SCENARIO"),
                      lanes.Source("literal", "something-else"))),)})
    with pytest.raises(repo_mod.Wp0Error, match="disagrees"):
        lanes.resolve_inputs(synthetic["root"], lanes.Lane(**{**lane.__dict__, "spec": spec}))


@pytest.mark.parametrize("tamper", ["changed", "added", "removed"])
def test_a_tampered_pack_is_refused(synthetic: dict[str, Any], tmp_path: Path, tamper: str) -> None:
    lane, root = synthetic["lane"], synthetic["root"]
    copy_root = tmp_path / "copy"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", "-q", str(copy_root),
                    synthetic["base"]], check=True, capture_output=True, env=git_env())
    try:
        import shutil

        shutil.copytree(root / "pack", copy_root / "pack")
        target = copy_root / "pack" / "result.json"
        if tamper == "changed":
            target.write_bytes(target.read_bytes() + b" ")
        elif tamper == "added":
            (copy_root / "pack" / "extra.txt").write_text("x", encoding="utf-8")
        else:
            target.unlink()
        assert replay.verify_pack(copy_root, lane)
        with pytest.raises(repo_mod.Wp0Error, match="does not match its batch manifest"):
            replay.reconstruct(copy_root, lane, tmp_path / "rebuilt")
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(copy_root)],
                       check=False, capture_output=True, env=git_env())


# --- the projection on the real banked verdicts (all four shapes) ---------------------------

SAMPLE_LANES = {"checks": "wp2", "rows-state": "lsdou-precedence", "candidates": "wp1b",
                "wp0-manifest": "wp0"}


def _history_available() -> bool:
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=False)
    return shallow.stdout.strip() == "false"


@pytest.fixture(scope="module")
def banked_sides(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if not _history_available():
        pytest.skip("shallow clone: the verdicts' commits are not fetched here")
    by_name = {lane.name: lane for lane in lanes.live_lanes(REPO_ROOT)}
    sides: dict[str, Any] = {}
    for shape, name in SAMPLE_LANES.items():
        lane = by_name[name]
        directory = tmp_path_factory.mktemp(f"tables-{name}")
        archive = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", lane.commit,
                                  "scripts/windows-oracle"], capture_output=True, check=True).stdout
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(directory, filter="data")
        tables = projection.finalizer_tables([sys.executable], directory, lane.spec.finalizer,
                                             "psdirect")
        verdict = json.loads((REPO_ROOT / lane.verdict).read_text(encoding="utf-8"))
        sides[shape] = (lane, projection.Side(verdict, lane.commit, tables, "", "",
                                              lane.pack_files.get))
    return sides


def _with(side: Any, verdict: dict[str, Any]) -> Any:
    return projection.Side(verdict, side.commit, side.tables, side.run_root, side.candidate_root,
                           side.member_digest)


@pytest.mark.parametrize("shape", sorted(SAMPLE_LANES))
def test_a_banked_verdict_satisfies_its_own_commits_provenance(
    banked_sides: dict[str, Any], shape: str
) -> None:
    _lane, side = banked_sides[shape]
    assert projection.provenance_problems(side, REPO_ROOT, shape) == []
    assert projection.payload_differences(side, side, shape) == []


def _first_leaf(value: Any, path: list[Any]) -> list[Any] | None:
    if isinstance(value, dict):
        for key in sorted(value):
            found = _first_leaf(value[key], [*path, key])
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _first_leaf(item, [*path, index])
            if found is not None:
                return found
        return None
    return path


def _mutate(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return "mutated"
    return f"{value}-mutated"


@pytest.mark.parametrize("shape", sorted(SAMPLE_LANES))
def test_mutating_one_leaf_of_every_top_level_key_is_detected(
    banked_sides: dict[str, Any], shape: str
) -> None:
    _lane, side = banked_sides[shape]
    keys = [k for k in side.verdict if k != "source"]
    assert keys
    for key in keys:
        mutated = copy.deepcopy(side.verdict)
        leaf = _first_leaf(mutated[key], [key])
        if leaf is None:  # an empty container: replace it
            mutated[key] = ["mutated"] if isinstance(mutated[key], list) else {"m": 1}
        else:
            node = mutated
            for part in leaf[:-1]:
                node = node[part]
            node[leaf[-1]] = _mutate(node[leaf[-1]])
        assert projection.payload_differences(side, _with(side, mutated), shape), key


def test_a_flipped_or_dropped_check_and_a_changed_finding_are_detected(
    banked_sides: dict[str, Any],
) -> None:
    _lane, side = banked_sides["checks"]
    name = sorted(side.verdict["checks"])[0]
    flipped = copy.deepcopy(side.verdict)
    flipped["checks"][name] = not flipped["checks"][name]
    dropped = copy.deepcopy(side.verdict)
    del dropped["checks"][name]
    assert projection.payload_differences(side, _with(side, flipped), "checks")
    assert projection.payload_differences(side, _with(side, dropped), "checks")
    _lane, rsop = banked_sides["rows-state"]
    finding = copy.deepcopy(rsop.verdict)
    finding["expected_finding"] = _mutate(finding["expected_finding"])
    assert projection.payload_differences(rsop, _with(rsop, finding), "rows-state")


PROVENANCE_MUTATIONS = {
    "commit": lambda s: s.__setitem__("commit", "0" * 40),
    "dirty": lambda s: s.__setitem__("dirty", True),
    "unexpected-path": lambda s: s["paths"].__setitem__("extra.py", "src/gpo_studio/rsop.py"),
    "missing-path": lambda s: s["paths"].pop(sorted(s["paths"])[0]),
    "file-digest": lambda s: s["files"].__setitem__(sorted(s["files"])[0], "0" * 64),
    "extra-file-key": lambda s: s["files"].__setitem__("extra.py", "0" * 64),
    "banked-copies": lambda s: s.__setitem__("banked_copies", []),
    "unknown-key": lambda s: s.__setitem__("note", "x"),
}


@pytest.mark.parametrize("mutation", sorted(PROVENANCE_MUTATIONS))
@pytest.mark.parametrize("shape", ["checks", "rows-state", "candidates"])
def test_every_provenance_invariant_is_enforced(
    banked_sides: dict[str, Any], shape: str, mutation: str
) -> None:
    _lane, side = banked_sides[shape]
    verdict = copy.deepcopy(side.verdict)
    PROVENANCE_MUTATIONS[mutation](verdict["source"])
    assert projection.provenance_problems(_with(side, verdict), REPO_ROOT, shape), mutation


@pytest.mark.parametrize("mutation", ["order", "digest", "extra", "commit"])
def test_wp0s_bound_rows_are_checked_against_its_table(
    banked_sides: dict[str, Any], mutation: str
) -> None:
    _lane, side = banked_sides["wp0-manifest"]
    verdict = copy.deepcopy(side.verdict)
    bound = verdict["source"]["bound"]
    if mutation == "order":
        bound.reverse()
    elif mutation == "digest":
        bound[0]["sha256"] = "0" * 64
    elif mutation == "extra":
        bound.append({"path": "src/gpo_studio/rsop.py", "sha256": "0" * 64})
    else:
        verdict["source"]["commit"] = "0" * 40
    assert projection.provenance_problems(_with(side, verdict), REPO_ROOT, "wp0-manifest")


def test_run_directory_paths_are_compared_as_pack_members() -> None:
    def side(root: str, digest: str) -> Any:
        verdict = {"artifact": f"{root}/commands/out.txt", "source": {}}
        return projection.Side(verdict, "c", {}, root, f"{root}-candidate",
                               lambda member: digest)

    same = projection.payload_differences(side("/a/run", "d1"), side("/b/run", "d1"), "checks")
    other = projection.payload_differences(side("/a/run", "d1"), side("/b/run", "d2"), "checks")
    assert same == [] and other


def test_the_generated_timestamp_invariant() -> None:
    completed = datetime(2026, 10, 9, 0, 12, tzinfo=UTC)
    end = completed + timedelta(hours=1)
    ok = (completed + timedelta(minutes=5)).isoformat()
    assert projection.timestamp_problems(ok, completed, end, None) == []
    early = (completed - timedelta(seconds=1)).isoformat()
    late = (end + timedelta(seconds=61)).isoformat()
    assert projection.timestamp_problems(early, completed, end, None)
    assert projection.timestamp_problems(late, completed, end, None)
    assert projection.timestamp_problems(ok, completed, end, end)  # out of order
