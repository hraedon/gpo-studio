"""Part 2: the canonical claim projection, with relational provenance.

`plans/036-change-cost.md`, "What part 2 meets": a re-graded verdict and the
banked one are compared in two parts.

**Provenance (`source`), relationally.** Each side's `source` is checked
against what that side's own commit says it should be, never against the
other side (a selected lane's finalizer at `F` has wider tables than at its
verdict's commit, so an equality rule would refuse the very edit WP-0 makes):

- `source.commit` equals the side's commit (the verdict's commit on the
  banked side, the re-grade's commit on the other);
- `source.paths` equals exactly the binding map that side's finalizer tables
  produce at that commit (`{**DEPLOYED_FILES, **LOCAL_FILES}`, or the
  transport-keyed form, for the transport the verdict records);
- `source.files` has exactly the keys of `source.paths`, and each digest is
  git's blob SHA-256 of that path at that commit;
- `source.dirty` is false; `source.banked_copies` equals the sorted deployed
  names;
- WP-0's `source.bound`: each entry's path and digest against the table WP-0's
  finalizer builds it from at that commit (`_HARNESS_ORCHESTRATOR_FILES`, the
  controller-executed rows of `_HARNESS_INPUT_FILES`; the guest-deployed rows
  are bound through the harness-input record and the `artifacts` block, which
  are payload and compared exactly), in order, none more and none fewer;
- any other key under `source` is refused: nothing in it is compared raw.

**Payload (everything outside `source`), exactly**, after one transformation
applied to both sides: a string leaf that is an absolute path under that
side's run directory or candidate root becomes `{"member": <pack-relative
member>, "sha256": <its digest>}`. Finalizer-generated timestamps would be the
second transformation; `FINALIZER_TIMESTAMPS` lists them per shape and is
empty today (every timestamp in a live verdict is read from the pack, so it is
compared exactly), and `timestamp_problems` holds the rule for when one is
added. No check, finding, `state` or `passed` is ever transformed or excluded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .repo import Wp0Error, blob_sha256

#: Per shape, the exact JSON pointers of timestamps the finalizer generates
#: (not read from the pack). Empty: no live verdict has one (measured).
FINALIZER_TIMESTAMPS: dict[str, tuple[str, ...]] = {
    "checks": (),
    "rows-state": (),
    "candidates": (),
    "wp0-manifest": (),
}
#: Sol round-4 N5: a generated timestamp may not follow the re-grade's end by more.
CLOCK_TOLERANCE = timedelta(seconds=60)
_SOURCE_KEYS = frozenset({"commit", "dirty", "files", "paths", "banked_copies"})
_WP0_SOURCE_KEYS = frozenset({"commit", "dirty", "bound"})

#: Run in the side's clone with the side's interpreter: prints that commit's
#: finalizer tables as JSON. Executes the finalizer's module body only (as
#: check-bound-source-bytes.py does), never its main().
_TABLES_PROBE = r"""
import json, runpy, sys
finalizer, transport = sys.argv[1], sys.argv[2]
symbols = runpy.run_path(finalizer)
if finalizer.endswith("finalize_oracle_run.py"):
    namespace = symbols["finalize_oracle_run"].__globals__
    table = namespace["_HARNESS_INPUT_FILES"][transport]
    bound = namespace["_HARNESS_ORCHESTRATOR_FILES"]
    print(json.dumps({"wp0": [list(row) for row in table],
                      "wp0_bound": [list(row) for row in bound]}))
else:
    out = {}
    for keyed, flat, name in (
        ("TRANSPORT_DEPLOYED_FILES", "DEPLOYED_FILES", "deployed"),
        ("TRANSPORT_LOCAL_FILES", "LOCAL_FILES", "local"),
    ):
        out[name] = dict(symbols[keyed][transport] if keyed in symbols else symbols[flat])
    print(json.dumps(out))
"""


@dataclass(frozen=True)
class Side:
    """One side of a comparison: a verdict and what its commit says."""

    verdict: dict[str, Any]
    commit: str
    tables: dict[str, Any]  # {"deployed": {...}, "local": {...}} or {"wp0": [[id, rel, path]]}
    run_root: str  # absolute run directory the verdict was produced in
    candidate_root: str
    member_digest: Callable[[str], str | None]  # pack-relative member -> sha256


def finalizer_tables(
    python: list[str], clone: Path, finalizer: str, transport: str
) -> dict[str, Any]:
    """The binding tables *finalizer* defines at the clone's commit."""
    result = subprocess.run(
        [*python, "-c", _TABLES_PROBE, str(clone / finalizer), transport],
        cwd=clone,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise Wp0Error(
            f"could not read {finalizer}'s tables at {clone}: "
            + result.stderr.decode("utf-8", errors="replace").strip()[-400:]
        )
    tables: dict[str, Any] = json.loads(result.stdout.decode("utf-8"))
    return tables


def provenance_problems(side: Side, repo: Path, shape: str) -> list[str]:
    """Every way *side*'s `source` departs from what its commit says it should be."""
    source = side.verdict.get("source")
    if not isinstance(source, dict):
        return ["source is missing or not an object"]
    problems: list[str] = []
    if source.get("commit") != side.commit:
        problems.append(f"source.commit {source.get('commit')!r} is not {side.commit}")
    if source.get("dirty") is not False:
        problems.append(f"source.dirty is {source.get('dirty')!r}, not false")
    if shape == "wp0-manifest":
        extra = sorted(set(source) - _WP0_SOURCE_KEYS)
        if extra:
            problems.append(f"source carries keys nothing checks: {extra}")
        rows = side.tables.get("wp0_bound")
        bound = source.get("bound")
        if not isinstance(rows, list) or not isinstance(bound, list):
            return [*problems, "source.bound or WP-0's table is missing"]
        expected = [row[2] for row in rows]
        actual = [entry.get("path") for entry in bound if isinstance(entry, dict)]
        if actual != expected:
            problems.append(f"source.bound paths {actual} are not WP-0's table {expected}")
        for entry in bound:
            if not isinstance(entry, dict):
                problems.append(f"source.bound entry {entry!r} is not an object")
                continue
            digest = blob_sha256(repo, side.commit, str(entry.get("path")))
            if entry.get("sha256") != digest:
                problems.append(
                    f"source.bound {entry.get('path')}: {entry.get('sha256')} is not the "
                    f"blob at {side.commit[:12]} ({digest})"
                )
        return problems
    extra = sorted(set(source) - _SOURCE_KEYS)
    if extra:
        problems.append(f"source carries keys nothing checks: {extra}")
    deployed = side.tables.get("deployed", {})
    local = side.tables.get("local", {})
    expected_paths = {**deployed, **local}
    paths = source.get("paths")
    if paths != expected_paths:
        problems.append(
            "source.paths is not the finalizer's tables at that commit: "
            + _dict_difference(paths if isinstance(paths, dict) else {}, expected_paths)
        )
    files = source.get("files")
    if not isinstance(files, dict):
        problems.append("source.files is missing")
    else:
        if set(files) != set(expected_paths):
            problems.append(
                f"source.files keys {sorted(files)} are not source.paths keys "
                f"{sorted(expected_paths)}"
            )
        for name, digest in sorted(files.items()):
            path = expected_paths.get(name)
            if path is None:
                continue
            blob = blob_sha256(repo, side.commit, path)
            if digest != blob:
                problems.append(
                    f"source.files[{name}] {digest} is not the blob of {path} at "
                    f"{side.commit[:12]} ({blob})"
                )
    banked = source.get("banked_copies")
    if banked != sorted(deployed):
        problems.append(f"source.banked_copies {banked} is not {sorted(deployed)}")
    return problems


def _dict_difference(actual: dict[str, Any], expected: dict[str, Any]) -> str:
    more = sorted(set(actual) - set(expected))
    fewer = sorted(set(expected) - set(actual))
    changed = sorted(k for k in set(actual) & set(expected) if actual[k] != expected[k])
    return f"extra {more}, missing {fewer}, different {changed}"


def canonical_payload(side: Side, shape: str) -> Any:
    """Everything outside `source`, with run-directory paths made pack-relative."""
    payload = {key: value for key, value in side.verdict.items() if key != "source"}
    timestamps = set(FINALIZER_TIMESTAMPS.get(shape, ()))

    def transform(value: Any, pointer: str) -> Any:
        if pointer in timestamps:
            return {"finalizer-timestamp": True}
        if isinstance(value, dict):
            return {key: transform(item, f"{pointer}/{key}") for key, item in value.items()}
        if isinstance(value, list):
            return [transform(item, f"{pointer}/{index}") for index, item in enumerate(value)]
        if isinstance(value, str):
            roots = ((side.run_root, ""), (side.candidate_root, "controller-candidate/"))
            for root, prefix in roots:
                if root and (value == root or value.startswith(root.rstrip("/") + "/")):
                    member = prefix + value[len(root) :].lstrip("/")
                    return {"member": member, "sha256": side.member_digest(member)}
        return value

    return transform(payload, "")


def payload_differences(banked: Side, regraded: Side, shape: str) -> list[str]:
    """JSON-pointer-addressed differences between the two canonical payloads."""
    differences: list[str] = []
    _diff(canonical_payload(banked, shape), canonical_payload(regraded, shape), "", differences)
    return differences


def _diff(a: Any, b: Any, pointer: str, out: list[str]) -> None:
    if type(a) is not type(b):
        out.append(f"{pointer or '/'}: {_brief(a)} != {_brief(b)}")
        return
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{pointer}/{key}: absent in the banked verdict, {_brief(b[key])}")
            elif key not in b:
                out.append(f"{pointer}/{key}: {_brief(a[key])}, absent in the re-grade")
            else:
                _diff(a[key], b[key], f"{pointer}/{key}", out)
        return
    if isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{pointer}: {len(a)} items != {len(b)} items")
        for index, (left, right) in enumerate(zip(a, b, strict=False)):
            _diff(left, right, f"{pointer}/{index}", out)
        return
    if a != b:
        out.append(f"{pointer or '/'}: {_brief(a)} != {_brief(b)}")


def _brief(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= 120 else text[:117] + "..."


def timestamp_problems(
    value: str, run_completed: datetime, regrade_end: datetime, previous: datetime | None
) -> list[str]:
    """The invariant a finalizer-generated timestamp must hold instead of equality."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return [f"{value!r} is not an ISO timestamp"]
    problems: list[str] = []
    if moment < run_completed:
        problems.append(f"{value} precedes the run's recorded completion {run_completed}")
    if moment > regrade_end + CLOCK_TOLERANCE:
        problems.append(f"{value} follows the re-grade's end {regrade_end} by more than 60 s")
    if previous is not None and moment < previous:
        problems.append(f"{value} is out of order with the previous generated timestamp")
    return problems


def compare(banked: Side, regraded: Side, repo: Path, shape: str) -> dict[str, Any]:
    """The full canonical comparison. `equal` is True only if all three lists are empty."""
    result: dict[str, Any] = {
        "banked_provenance": provenance_problems(banked, repo, shape),
        "regraded_provenance": provenance_problems(regraded, repo, shape),
        "payload": payload_differences(banked, regraded, shape),
    }
    result["equal"] = not any(result[key] for key in list(result))
    # Digests of both canonical payloads: a record of this comparison is bound
    # to the banked pack, whose payload digest anyone can recompute from the
    # committed evidence (review round 2).
    result["banked_payload_sha256"] = payload_digest(banked, shape)
    result["regraded_payload_sha256"] = payload_digest(regraded, shape)
    return result


def payload_digest(side: Side, shape: str) -> str:
    """SHA-256 of the canonical payload (sorted-key JSON)."""
    data = json.dumps(canonical_payload(side, shape), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
