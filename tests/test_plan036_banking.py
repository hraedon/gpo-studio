"""Plan 036 WP-0, steps 7 and 8 end to end on a synthetic repository (review round 3).

Sol B2: the plan runs parts 1 and 2 at the freeze commit F with the new
verdicts live, then banks them in one commit B whose sole parent is F. The
tooling could not do that: `write-records` targeted a clean HEAD and read lanes
from a clone of it, so the new evidence was never seen while F stayed the
target. These tests drive the sequence the plan prescribes:

F -> the estate run at F -> staged evidence -> `write-records --target F
--evidence ... --bank-branch` -> B -> `check-pin` at B,

and the refusals: an extra file in B, a B with two parents, and staged evidence
that changes a dependency.

The synthetic lane is real code run for real (a builder, and a finalizer that
binds its own files and the tree's cleanliness, as the lane finalizers do).
Three things are stood in for, each tested elsewhere: lane discovery (a small
registry instead of the batch manifests), the reviewed lists (generated to
declare exactly the synthetic modules' sites), and the uv environment builder
(the interpreter running the tests; `replay.uv_environment` has its own tests).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from plan036_support import load

cli = load("cli")
record = load("record")
replay = load("replay")
scan = load("scan")
lanes = load("lanes")

pytestmark = pytest.mark.skipif(
    bool(record.running_interpreter_problems()),
    reason="the gate and the records run only under the lanes' interpreter on Linux",
)

PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"

FINALIZER = '''\
import argparse, hashlib, json, subprocess
from pathlib import Path

DEPLOYED_FILES = {"guest.ps1": "guest.ps1"}
LOCAL_FILES = {"fin.py": "scripts/fin.py", "build.py": "scripts/build.py",
               "notes.txt": "docs/notes.txt"}


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
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.repo_root,
                         capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=args.repo_root,
                            capture_output=True, text=True, check=True).stdout
    tables = {**DEPLOYED_FILES, **LOCAL_FILES}
    checks = {
        "guest_succeeded": result["ok"] is True,
        "candidate_intact": sha(args.candidate_root / "candidate.bin") == result["candidate"],
        "source_tree_clean": not status,
    }
    verdict = {
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "findings": [],
        "candidate": {p.name: sha(p) for p in sorted(args.candidate_root.iterdir())},
        "source": {
            "commit": out,
            "dirty": bool(status),
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
SPEC = lanes.RunnerSpec(
    builder="scripts/build.py", builder_argv=("{out}",), builder_script_form="relative",
    finalizer="scripts/fin.py",
    finalizer_argv=("{run}", "--candidate-root", "{candidate}", "--repo-root", "{repo}"),
    finalizer_script_form="relative", builder_stdout="builder.stdout.txt",
)
REGISTRY = "docs/registry.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True
                          ).stdout.decode("utf-8").strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def synthetic_live_lanes(root: Path = Path()) -> list[Any]:
    """The synthetic repository's one live lane, read from its registry at *root*."""
    registry = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    out = []
    for row in registry["lanes"]:
        verdict = json.loads((root / row["verdict"]).read_text(encoding="utf-8"))
        pack = row["verdict"].rsplit("/", 1)[0]
        out.append(lanes.Lane(
            name=row["name"], verdict=row["verdict"], pack=pack,
            commit=verdict["source"]["commit"], runner="run-synthetic.sh", spec=SPEC,
            shape="checks", original_verdict_name="verification.json", batch_manifest="",
            pack_files=replay.tree_digests(root / pack),
        ))
    return out


def stub_environment(path: Path, commit: str, tool_root: Path) -> tuple[list[str], dict[str, Any]]:
    lock = hashlib.sha256((path / "uv.lock").read_bytes()).hexdigest()
    return [sys.executable], {
        "method": replay.UV_METHOD, "commit": commit, "uv": "uv (test stand-in)",
        "python": PYTHON, "uv_lock_sha256": lock, "backend_source": "the commit's own uv.lock",
        "backend_lock_sha256": lock, "backend_lock_commit": commit,
        "backend": ["hatchling==1.32.4"], "steps": [["uv", "sync"]], "installed": ["-e <clone>"],
    }


def _estate_run(repo: Path, commit: str, scratch: Path, name: str) -> Path:
    """The estate's run of the lane at *commit*: a clean clone builds the candidate
    and the finalizer grades it; returns the banked pack (run directory, candidate,
    controller log)."""
    clone = scratch / f"estate-{name}"
    _git(repo, "worktree", "add", "-q", "--detach", str(clone), commit)
    try:
        run, candidate = scratch / f"run-{name}", scratch / f"candidate-{name}"
        run.mkdir()
        out = subprocess.run([sys.executable, "scripts/build.py", str(candidate)], cwd=clone,
                             capture_output=True, check=True)
        (candidate / "builder.stdout.txt").write_bytes(out.stdout)
        digest = hashlib.sha256((candidate / "candidate.bin").read_bytes()).hexdigest()
        (run / "result.json").write_text(json.dumps(
            {"ok": True, "run_id": name, "candidate": digest}), encoding="utf-8")
        subprocess.run([sys.executable, "scripts/fin.py", str(run), "--candidate-root",
                        str(candidate), "--repo-root", str(clone)], cwd=clone, check=True,
                       capture_output=True)
    finally:
        _git(repo, "worktree", "remove", "--force", str(clone))
    pack = scratch / f"pack-{name}"
    shutil.copytree(run, pack)
    shutil.copytree(candidate, pack / "controller-candidate")
    (pack / "controller.log").write_text("SCENARIO=synthetic\n", encoding="utf-8")
    return pack


def _lists(root: Path, directory: Path) -> None:
    """Reviewed lists declaring exactly the synthetic modules' sites."""
    paths = ["scripts/build.py", "scripts/fin.py"]
    analysis = scan.analyse(root, paths, PYTHON)
    base = scan.Lists.load()
    counts: dict[tuple[str, str, str], int] = {}
    for item in scan.classify(scan.scan_all(analysis), base):
        if item.status in scan.FAILING:
            kind = "process" if item.site.target == "subprocess.run" else "unresolved"
            key = (item.site.module, kind, item.site.target)
            counts[key] = counts.get(key, 0) + 1
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for (module, kind, target), count in sorted(counts.items()):
        group = groups.setdefault((module, kind), {
            "module": module, "scopes": "*", "kind": kind,
            "reaches": {"executable": "git"} if kind == "process" else {"what": "synthetic"},
            "reason": "the synthetic lane of tests/test_plan036_banking.py", "targets": {},
        })
        group["targets"][target] = count
    directory.mkdir()
    shutil.copyfile(record.LISTS_DIR / "inert.json", directory / "inert.json")
    (directory / "edges.json").write_text(json.dumps(
        {"comment": "test", "edges": [], "groups": list(groups.values())}), encoding="utf-8")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t"),
                       ("core.autocrlf", "false")):
        _git(repo, "config", key, value)
    (repo / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "synthetic"\nversion = "0"\nrequires-python = ">=3.13"\n', "utf-8")
    (repo / "uv.lock").write_text('version = 1\n[[package]]\nname = "hatchling"\n', "utf-8")
    (repo / "guest.ps1").write_text("Write-Output ok\n", encoding="utf-8")
    (repo / "docs" / "notes.txt").write_text("bound by the finalizer\n", encoding="utf-8")
    (repo / "scripts" / "fin.py").write_text(FINALIZER, encoding="utf-8")
    (repo / "scripts" / "build.py").write_text(BUILDER, encoding="utf-8")
    code = _commit(repo, "the lane")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    old = _estate_run(repo, code, scratch, "old")
    shutil.copytree(old, repo / "docs" / "packs" / "old")
    (repo / REGISTRY).write_text(json.dumps(
        {"lanes": [{"name": "synthetic", "verdict": "docs/packs/old/verification.json"}]}),
        encoding="utf-8")
    freeze = _commit(repo, "F: the freeze commit")
    # Step 6: the lane runs on the estate at F; its pack and the registry entry
    # that makes it live are staged, not committed.
    new = _estate_run(repo, freeze, scratch, "new")
    evidence = tmp_path / "evidence"
    shutil.copytree(new, evidence / "docs" / "packs" / "new")
    (evidence / "docs").mkdir(exist_ok=True)
    (evidence / REGISTRY).write_text(json.dumps(
        {"lanes": [{"name": "synthetic", "verdict": "docs/packs/new/verification.json"}]}),
        encoding="utf-8")
    lists_dir = tmp_path / "lists"
    _lists(repo, lists_dir)
    original = scan.Lists.load.__func__
    monkeypatch.setattr(scan.Lists, "load",
                        classmethod(lambda cls, directory=lists_dir: original(cls, directory)))
    monkeypatch.setattr(record, "live_lanes", synthetic_live_lanes)
    monkeypatch.setattr(replay, "uv_environment", stub_environment)
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    return {"repo": repo, "freeze": freeze, "evidence": evidence, "tmp": tmp_path}


def _write_records(world: dict[str, Any], workdir: str, *extra: str) -> int:
    code: int = cli.main([
        "write-records", "--target", world["freeze"], "--evidence", str(world["evidence"]),
        "--workdir", str(world["tmp"] / workdir), "--python-version", PYTHON, *extra,
    ])
    return code


def test_steps_7_and_8_bank_the_new_evidence_and_the_gate_is_green_at_b(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, freeze = world["repo"], world["freeze"]
    assert _write_records(world, "w", "--bank-branch", "bank") == 0, capsys.readouterr()
    bank = _git(repo, "rev-parse", "bank")
    assert _git(repo, "rev-list", "--parents", "-n", "1", bank).split()[1:] == [freeze]
    changed = set(_git(repo, "diff", "--name-only", freeze, bank).split())
    assert changed == {*record.staged_evidence(world["evidence"]), record.SUCCESSOR_RECORD,
                       record.PIN_RECORD}
    _git(repo, "checkout", "-q", "bank")
    pin = json.loads((repo / record.PIN_RECORD).read_text(encoding="utf-8"))
    successor = json.loads((repo / record.SUCCESSOR_RECORD).read_text(encoding="utf-8"))
    assert pin["target_commit"] == successor["target_commit"] == freeze
    lane = successor["lanes"]["synthetic"]
    assert lane["verdict"] == "docs/packs/new/verification.json"
    assert lane["verdict_commit"] == freeze and lane["selected"] is False
    capsys.readouterr()
    assert cli.main(["check-bank", "--freeze", freeze, "--bank", bank]) == 0, \
        capsys.readouterr()
    assert cli.main(["check-pin"]) == 0, capsys.readouterr()


def test_b_with_an_extra_file_is_refused(world: dict[str, Any]) -> None:
    repo, freeze = world["repo"], world["freeze"]
    assert _write_records(world, "w") == 0
    records = world["tmp"] / "w" / "records"
    _git(repo, "checkout", "-q", "-b", "bank", freeze)
    shutil.copytree(world["evidence"], repo, dirs_exist_ok=True)
    shutil.copytree(records, repo, dirs_exist_ok=True)
    (repo / "docs" / "extra.txt").write_text("not run with\n", encoding="utf-8")
    _commit(repo, "B with an extra file")
    pin = json.loads((repo / record.PIN_RECORD).read_text(encoding="utf-8"))
    problems = record.history_problems(repo, pin)
    assert any("record was not run with" in p and "docs/extra.txt" in p for p in problems), \
        problems


def test_b_with_two_parents_is_refused(world: dict[str, Any]) -> None:
    repo, freeze = world["repo"], world["freeze"]
    assert _write_records(world, "w", "--bank-branch", "bank") == 0
    _git(repo, "checkout", "-q", "-b", "side", freeze)
    (repo / "docs" / "side.txt").write_text("side\n", encoding="utf-8")
    side = _commit(repo, "side")
    tree = _git(repo, "rev-parse", "bank^{tree}")
    merged = _git(repo, "commit-tree", tree, "-p", freeze, "-p", side, "-m", "B, two parents")
    _git(repo, "checkout", "-q", "-B", "merged", merged)
    pin = json.loads((repo / record.PIN_RECORD).read_text(encoding="utf-8"))
    problems = record.history_problems(repo, pin)
    assert any("is not a child of the freeze commit" in p for p in problems), problems


def test_staged_evidence_that_changes_a_dependency_is_refused(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    (world["evidence"] / "docs" / "notes.txt").write_text("edited\n", encoding="utf-8")
    assert _write_records(world, "w") == 2
    assert "changes a dependency" in capsys.readouterr().err


def test_staged_evidence_outside_the_evidence_paths_is_refused(
    world: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    (world["evidence"] / "scripts").mkdir()
    (world["evidence"] / "scripts" / "new.py").write_text("x = 1\n", encoding="utf-8")
    assert _write_records(world, "w") == 2
    assert "outside" in capsys.readouterr().err

