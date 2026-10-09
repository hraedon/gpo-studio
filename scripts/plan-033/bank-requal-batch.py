#!/usr/bin/env python3
"""Bank a requalification batch run by `run-requal-batch.sh` on the controller.

The procedure the Plan 034 batch followed by hand, as a tool, so the next batch
repeats it rather than re-deriving it:

    stage     pull every passing lane's run directory, controller candidate and
              driver log from the controller, assemble the packs under
              docs/plan-033/<family>-evidence/<label>/<lane>/, and check every
              banked file against the controller's copy by SHA-256;
    manifest  write docs/plan-033/<batch>-batch.json (schema 2) from the
              driver's progress.jsonl and the staged packs;
    retarget  in the named files, replace each currently live verdict's run id
              and commit with the new batch's run for the same lane.

A pack is the controller's run directory verbatim (an RSoP verdict written as
`rsop-verdict.json` / `rsop-user-verdict.json` is banked as
`verification.json`), plus `controller-candidate/` (the builder's output,
which the verdict's candidate hashes bind) and `controller.log` (the driver's
per-lane log). WP-0 has no candidate.

Every remote path comes from the driver's own records, and every one is
refused unless it is non-empty, free of `..`, and under an allowed prefix: an
earlier hand-written staging script with an empty variable copied a whole
/tmp. Pulled bytes go to a scratch directory the operator names (not /tmp,
which is RAM on the dev boxes).

    python scripts/plan-033/bank-requal-batch.py stage \\
        --controller mvmcc02 --batch-dir /home/itadmin/gpo-batch-110b \\
        --label release110-20261009 --scratch ~/wt/scratch/release110b
    python scripts/plan-033/bank-requal-batch.py manifest \\
        --scratch ~/wt/scratch/release110b --out docs/plan-033/release110-batch.json \\
        [--superseded-attempt attempt.json] [--cleanup release110-cleanup]
    python scripts/plan-033/bank-requal-batch.py retarget \\
        --manifest docs/plan-033/release110-batch.json tests/fixtures/scenarios/platforms.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "docs" / "plan-033"
REGISTRY = REPO_ROOT / "tests" / "test_committed_evidence.py"

#: Manifest schema: 2 carries the driver's containment fields and
#: `test_scope_tool` on every run (tests/batch_provenance.py).
SCHEMA_VERSION = 2
#: Progress fields copied verbatim onto every run row.
PROGRESS_FIELDS = (
    "runner", "started_utc", "completed_utc", "exit_status", "budget_seconds",
    "timed_out", "processes_killed", "cancelled", "containment_lost", "scope_failed",
    "test_scope_tool",
)
RENAMED_VERDICTS = {
    "rsop-verdict.json": "verification.json",
    "rsop-user-verdict.json": "verification.json",
}
_COMPUTER_RSOP = (
    "lsdou-precedence", "disabled-block-enforced", "wmi-filtering", "wmi-filtering-error",
    "computer-security-filtering", "computer-security-filtering-deny-read",
    "computer-security-filtering-group-deny",
)
_USER_RSOP = (
    "loopback-merge", "loopback-replace", "user-side-disabled", "user-security-filtering",
    "user-security-filtering-deny", "user-security-filtering-read-deny",
)
#: Lane -> evidence family directory, as every earlier batch placed them.
FAMILY: dict[str, str] = {
    "wp0": "wp0-evidence",
    "wp1b": "wp1b-evidence", "scripts-metadata": "wp1b-evidence",
    "publication": "wp1b-evidence",
    "wp2": "wp2-evidence", "report-parity": "wp2-evidence",
    "wp3-member": "wp3-evidence", "wp3-dc": "wp3-evidence",
    "object-security": "wp3-evidence", "firewall": "wp3-evidence",
    "fdeploy": "wp4-evidence",
    "lifecycle": "wp7-evidence",
    "endpoint": "wp6-evidence",
    **dict.fromkeys(_COMPUTER_RSOP, "wp6-evidence"),
    **dict.fromkeys(_USER_RSOP, "wp9-evidence"),
}
_CANDIDATE = re.compile(r"(/\S*?/[^\s/]*-candidates?-\d[\w-]*)")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _guard(path: str | None, prefixes: tuple[str, ...]) -> str:
    """Refuse a remote path that is empty, climbs, or leaves the allowed roots."""
    if not path or not path.startswith(prefixes) or ".." in path.split("/"):
        raise SystemExit(f"REFUSE remote path {path!r}: not under {prefixes}")
    return path


def _ssh(controller: str, command: str) -> str:
    """Run a command on the controller; its stdout as UTF-8 (bytes kept exact)."""
    out = subprocess.run(["ssh", controller, command], capture_output=True, check=True).stdout
    return out.decode("utf-8")


def _rsync(controller: str, source: str, target: Path) -> None:
    subprocess.run(["rsync", "-a", "--delete", f"{controller}:{source}", str(target)],
                   check=True)


def _remote_hashes(controller: str, path: str) -> dict[str, str]:
    out = _ssh(
        controller,
        f"cd {shlex.quote(path)} && find . -type f -print0 | xargs -0 -r sha256sum",
    )
    hashes = {}
    for line in out.splitlines():
        digest, name = line.split("  ", 1)
        hashes[name.removeprefix("./")] = digest
    return hashes


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): _sha(p)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def stage(args: argparse.Namespace) -> int:
    batch_dir = args.batch_dir.rstrip("/")
    prefixes = (batch_dir + "/", *args.allow_prefix)
    scratch = Path(args.scratch).expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    progress_text = _ssh(args.controller, f"cat {shlex.quote(batch_dir + '/progress.jsonl')}")
    (scratch / "progress.jsonl").write_text(progress_text, encoding="utf-8")
    report: dict[str, Any] = {"label": args.label, "controller": args.controller,
                              "batch_dir": batch_dir, "lanes": {}}
    for line in progress_text.splitlines():
        row = json.loads(line)
        name = row["name"]
        if row["exit_status"] != 0:
            print(f"skip {name}: exit {row['exit_status']}")
            continue
        run_dir = _guard(row["local_run_dir"], prefixes)
        log_path = _guard(f"{batch_dir}/logs/{name}.log", prefixes)
        raw = scratch / "raw" / name
        raw.mkdir(parents=True, exist_ok=True)
        _rsync(args.controller, log_path, raw / "controller.log")
        log = (raw / "controller.log").read_text(encoding="utf-8", errors="replace")
        candidates = sorted({c for c in _CANDIDATE.findall(log) if c.startswith(prefixes)})
        if name == "wp0":
            candidate = None
        elif len(candidates) == 1:
            candidate = _guard(candidates[0], prefixes)
        else:
            raise SystemExit(f"{name}: expected one candidate dir in its log, got {candidates}")
        _rsync(args.controller, f"{run_dir}/", raw / "run")
        if candidate:
            _rsync(args.controller, f"{candidate}/", raw / "candidate")

        dest = EVIDENCE / FAMILY[name] / args.label / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        original = "manifest.json" if name == "wp0" else "verification.json"
        for path in sorted((raw / "run").rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(raw / "run").as_posix()
            if relative in RENAMED_VERDICTS:
                original = relative
                relative = RENAMED_VERDICTS[relative]
            target = dest / relative
            if target.exists():
                raise SystemExit(f"{name}: {relative} would be overwritten")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        if candidate:
            shutil.copytree(raw / "candidate", dest / "controller-candidate")
        shutil.copy2(raw / "controller.log", dest / "controller.log")

        # Every banked byte against the controller's copy.
        expected = {RENAMED_VERDICTS.get(k, k): v
                    for k, v in _remote_hashes(args.controller, run_dir).items()}
        if candidate:
            expected |= {f"controller-candidate/{k}": v
                         for k, v in _remote_hashes(args.controller, candidate).items()}
        logs = _remote_hashes(args.controller, f"{batch_dir}/logs")
        expected["controller.log"] = logs[f"{name}.log"]
        banked = _tree(dest)
        if banked != expected:
            raise SystemExit(f"{name}: banked files differ from the controller's")
        report["lanes"][name] = {
            "pack": dest.relative_to(EVIDENCE).as_posix(),
            "run_dir": run_dir, "candidate_dir": candidate,
            "original_verdict_name": original, "files": len(banked),
        }
        print(f"staged {name}: {len(banked)} files, checked against {args.controller}")
    (scratch / "stage-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


def manifest(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch).expanduser().resolve()
    report = json.loads((scratch / "stage-report.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in
            (scratch / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    commits = {row["commit"] for row in rows}
    if len(commits) != 1:
        raise SystemExit(f"a batch runs on one frozen commit; progress names {commits}")
    (commit,) = commits
    runs: list[dict[str, Any]] = []
    not_passed: list[dict[str, Any]] = []
    for row in rows:
        if row.get("test_scope_tool") is not False:
            raise SystemExit(f"{row['name']}: test_scope_tool is not false; never evidence")
        fields = {key: row[key] for key in PROGRESS_FIELDS}
        if row["exit_status"] != 0:
            not_passed.append({"name": row["name"], "commit": commit, **fields,
                               "local_run_dir": row["local_run_dir"]})
            continue
        lane = report["lanes"][row["name"]]
        pack = EVIDENCE / lane["pack"]
        verdict_name = "manifest.json" if row["name"] == "wp0" else "verification.json"
        verdict = json.loads((pack / verdict_name).read_text(encoding="utf-8-sig"))
        if verdict["source"]["commit"] != commit or verdict["source"]["dirty"] is not False:
            raise SystemExit(f"{row['name']}: verdict source is not the clean frozen commit")
        runs.append({
            "name": row["name"],
            "run_id": verdict["run_id"],
            "verdict": f"{lane['pack']}/{verdict_name}",
            "commit": commit,
            **fields,
            "original_verdict_name": lane["original_verdict_name"],
            "files": _tree(pack),
        })
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": commit,
        "driver": "scripts/plan-033/run-requal-batch.sh",
        "lanes_started": len(rows),
        "runs": runs,
        "not_passed": not_passed,
        "successors": [],
        "post_batch_cleanup": None,
    }
    if args.cleanup:
        # The post-batch directory check: the collector and its capture, by hash.
        cleanup = EVIDENCE / args.cleanup
        files = [cleanup / "collector.ps1", cleanup / "directory.json"]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise SystemExit(f"post-batch check incomplete: {missing}")
        document["post_batch_cleanup"] = {
            f.relative_to(EVIDENCE).as_posix(): _sha(f) for f in files
        }
    if args.superseded_attempt:
        document["superseded_attempts"] = json.loads(
            Path(args.superseded_attempt).read_text(encoding="utf-8"))
    out = Path(args.out)
    with out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(document, indent=2) + "\n")
    print(f"wrote {out}: {len(runs)} passing runs, {len(not_passed)} not passed")
    return 0


def _live_runs() -> dict[str, tuple[str, str]]:
    """Lane -> (run id, commit) for every live verdict, plus WP-0 by commit."""
    registry = runpy.run_path(str(REGISTRY))
    live: dict[str, tuple[str, str]] = {}
    for relative in registry["LIVE_VERDICTS"]:
        verdict = json.loads((EVIDENCE / relative).read_text(encoding="utf-8-sig"))
        live[Path(relative).parent.name] = (verdict["run_id"], verdict["source"]["commit"])
    commits = {commit for _, commit in live.values()}
    for path in sorted(EVIDENCE.glob("wp0-evidence/*/wp0/manifest.json")):
        wp0 = json.loads(path.read_text(encoding="utf-8"))
        if wp0.get("source", {}).get("commit") in commits:
            live["wp0"] = (wp0["run_id"], wp0["source"]["commit"])
    return live


def retarget(args: argparse.Namespace) -> int:
    """Swap each live run id (and its full commit) for the batch's run of that lane.

    Run ids are unique strings, so the swap is exact. Commits are replaced only
    as full 40-character SHAs, and only in the files named: use it on records
    that state the CURRENT qualification, never on history prose.
    """
    batch = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    new = {run["name"]: (run["run_id"], run["commit"]) for run in batch["runs"]}
    swaps: dict[str, str] = {}
    for lane, (run_id, commit) in _live_runs().items():
        if lane in new:
            swaps[run_id] = new[lane][0]
            swaps[commit] = new[lane][1]
    for name in args.files:
        path = Path(name)
        text = original = path.read_text(encoding="utf-8")
        for old, replacement in swaps.items():
            text = text.replace(old, replacement)
        if text != original:
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            print(f"retargeted {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_stage = sub.add_parser("stage", help="pull, assemble and verify the packs")
    p_stage.add_argument("--controller", required=True)
    p_stage.add_argument("--batch-dir", required=True)
    p_stage.add_argument("--label", required=True, help="pack directory, e.g. release110-20261009")
    p_stage.add_argument("--scratch", required=True)
    p_stage.add_argument("--allow-prefix", action="append", default=["/tmp/opencode/"],
                         help="extra remote root a run or candidate dir may live under")
    p_stage.set_defaults(func=stage)
    p_manifest = sub.add_parser("manifest", help="write the batch manifest")
    p_manifest.add_argument("--scratch", required=True)
    p_manifest.add_argument("--out", required=True)
    p_manifest.add_argument("--superseded-attempt")
    p_manifest.add_argument("--cleanup", help="docs/plan-033 subdirectory of the post-batch check")
    p_manifest.set_defaults(func=manifest)
    p_retarget = sub.add_parser("retarget", help="point current-qualification records at the batch")
    p_retarget.add_argument("--manifest", required=True)
    p_retarget.add_argument("files", nargs="+")
    p_retarget.set_defaults(func=retarget)
    args = parser.parse_args()
    status: int = args.func(args)
    return status


if __name__ == "__main__":
    sys.exit(main())
