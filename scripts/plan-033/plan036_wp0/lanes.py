"""The live lanes, how their runners launch the builder and finalizer, and
where every recorded run input is read from.

The live set is not re-derived here: it is `LIVE_VERDICTS` in
`tests/test_committed_evidence.py` (the freshness gate's own registry) plus the
WP-0 manifest whose commit a live lane verdict also binds, read through
`report-bound-source-cost.py`, the one existing answer to "which verdicts are
live". Each live verdict is matched to exactly one row of one banked batch
manifest (`docs/plan-033/*-batch.json`), which names its lane, its runner, the
verdict's original file name and the SHA-256 of every pack member.

Launch forms are copied from the runners (`scripts/windows-oracle/run-*.sh`),
which the driver starts from the repository root: `uv run python <script>`,
no interpreter flags, the script path spelt as the runner spells it. A test
holds this table to the runners' text so the two cannot drift silently.

Run inputs (the plan's "a per-finalizer table in the tool maps each
command-line input ... to the banked verdict field it is read from"): each
placeholder in a launch template names its source. A source that is absent,
or two sources for one input that disagree, is a refusal naming the input.
The R5 schema-3 run-input record does not exist yet (WP-4), so this table is
the hand-supplied substitute the plan's "Met by hand" row describes.
"""

from __future__ import annotations

import json
import re
import runpy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .repo import REPO_ROOT, Wp0Error, show

EVIDENCE = "docs/plan-033"

#: Interpreter flags each runner passes (`uv run python <script>`): none. The
#: tracer reproduces exactly these; a runner that gained `-I` would need it here.
RUNNER_PYTHON_FLAGS: tuple[str, ...] = ()


@dataclass(frozen=True)
class Source:
    """Where one run input is read from, in the banked record."""

    kind: str  # "verdict" | "controller-log" | "candidate-json" | "driver-env" | "literal"
    key: str  # JSON pointer (/a/b), log key (DOMAIN), env name, or the literal value
    file: str = ""  # candidate file for "candidate-json"

    def describe(self) -> str:
        if self.kind == "candidate-json":
            return f"candidate {self.file}{self.key}"
        return f"{self.kind} {self.key}"


@dataclass(frozen=True)
class RunnerSpec:
    """One runner's launch of its builder and finalizer."""

    builder: str | None  # repository-relative path
    builder_argv: tuple[str, ...]  # after the script; "{out}" and "{input:NAME}" placeholders
    builder_script_form: str  # how the runner spells the builder path: "relative" | "absolute"
    finalizer: str
    finalizer_argv: tuple[str, ...]  # "{run}", "{candidate}", "{repo}", "{input:NAME}"
    finalizer_script_form: str
    inputs: tuple[tuple[str, tuple[Source, ...]], ...] = ()
    #: The runner tees the builder's stdout into this candidate file (part of
    #: the candidate the verdict hashes), or None.
    builder_stdout: str | None = None
    #: The candidate file a flat `candidate_delivery` ({"controller_sha256": ...},
    #: not keyed by file name) hashes, as the finalizer hard-codes it.
    flat_delivery_file: str | None = None


_B = "scripts/plan-033/"
_F = "scripts/windows-oracle/"
_STD_FIN = ("{run}", "--candidate-root", "{candidate}", "--repo-root", "{repo}")
_TRANSPORT = (("transport", (Source("verdict", "/transport"),)),)
_GUEST = (("guest_status", (Source("verdict", "/guest_status"),)),)
_RSOP_INPUTS = (
    (
        "scenario",
        (Source("verdict", "/scenario_id"), Source("controller-log", "SCENARIO")),
    ),
    (
        "domain",
        (
            Source("controller-log", "DOMAIN"),
            Source("candidate-json", "/domain", "topology.json"),
        ),
    ),
    ("computer_name", (Source("candidate-json", "/endpoint_computer", "topology.json"),)),
    ("user_name", (Source("candidate-json", "/endpoint_user", "topology.json"),)),
    ("transport", (Source("verdict", "/transport"), Source("literal", "psdirect"))),
)
_RSOP_BUILDER = (
    "{out}",
    "--scenario",
    "{input:scenario}",
    "--domain",
    "{input:domain}",
    "--computer-name",
    "{input:computer_name}",
    "{optional:--user-name:user_name}",
)

RUNNERS: dict[str, RunnerSpec] = {
    "run-windows-oracle.sh": RunnerSpec(
        builder=None,
        builder_argv=(),
        builder_script_form="",
        finalizer=_F + "finalize_oracle_run.py",
        finalizer_argv=("{run}", "--repo-root", "{repo}"),
        finalizer_script_form="relative",
    ),
    "run-wp1b-oracle.sh": RunnerSpec(
        _B + "build-wp1b-candidates.py", ("{out}",), "relative",
        _F + "finalize_wp1b_run.py", (*_STD_FIN, "--transport", "{input:transport}"), "relative",
        _TRANSPORT,
    ),
    "run-scripts-backup-oracle.sh": RunnerSpec(
        _B + "build-scripts-backup-candidate.py", ("{out}",), "relative",
        _F + "finalize_scripts_backup_run.py", _STD_FIN, "absolute",
        builder_stdout="builder.stdout.txt",
        flat_delivery_file="studio-scripts-backup.zip",
    ),
    "run-publication-oracle.sh": RunnerSpec(
        _B + "build-publication-candidate.py", ("{out}",), "relative",
        _F + "finalize_publication_run.py", _STD_FIN, "absolute",
        builder_stdout="builder.stdout.txt",
        flat_delivery_file="studio-publication-backup.zip",
    ),
    "run-wp2-oracle.sh": RunnerSpec(
        _B + "build-wp2-candidate.py", ("{out}",), "relative",
        _F + "finalize_wp2_import_run.py", (*_STD_FIN, "--transport", "{input:transport}"),
        "relative", _TRANSPORT,
    ),
    "run-report-parity-oracle.sh": RunnerSpec(
        _B + "build-report-parity-candidate.py", ("{out}",), "relative",
        _F + "finalize_report_parity_run.py",
        (*_STD_FIN, "--guest-status", "{input:guest_status}"), "absolute", _GUEST,
        builder_stdout="builder.stdout.txt",
        flat_delivery_file="report-parity-cases.zip",
    ),
    "run-wp3-oracle.sh": RunnerSpec(
        _B + "build-wp3-candidate.py", ("{out}", "{flag:--include-kerberos:kerberos}"),
        "relative",
        _F + "finalize_wp3_run.py", (*_STD_FIN, "--transport", "{input:transport}"), "absolute",
        (
            *_TRANSPORT,
            ("kerberos", (Source("driver-env", "GPO_STUDIO_WP3_KERBEROS"),)),
        ),
    ),
    "run-object-security-oracle.sh": RunnerSpec(
        _B + "build-object-security-candidate.py", ("{out}",), "relative",
        _F + "finalize_object_security_run.py", _STD_FIN, "absolute",
    ),
    "run-firewall-oracle.sh": RunnerSpec(
        _B + "build-firewall-candidate.py", ("{out}",), "absolute",
        _F + "finalize_firewall_run.py", _STD_FIN, "absolute",
        builder_stdout="builder.stdout.txt",
    ),
    "run-fdeploy-oracle.sh": RunnerSpec(
        _B + "build-fdeploy-candidate.py", ("{out}",), "relative",
        _F + "finalize_fdeploy_run.py",
        (*_STD_FIN, "--guest-status", "{input:guest_status}"), "absolute", _GUEST,
        builder_stdout="builder.stdout.txt",
        flat_delivery_file="fdeploy-cases.zip",
    ),
    "run-lifecycle-oracle.sh": RunnerSpec(
        _B + "build-lifecycle-candidate.py", ("{out}",), "relative",
        _F + "finalize_lifecycle_run.py", _STD_FIN, "absolute",
        builder_stdout="builder.stdout.txt",
    ),
    "run-endpoint-oracle.sh": RunnerSpec(
        _B + "build-endpoint-candidate.py", ("{out}",), "relative",
        _F + "finalize_endpoint_run.py", (*_STD_FIN, "--transport", "{input:transport}"),
        "relative",
        (("transport", (Source("verdict", "/transport"), Source("literal", "psdirect"))),),
    ),
    "run-rsop-oracle.sh": RunnerSpec(
        _B + "build-rsop-candidate.py", _RSOP_BUILDER, "absolute",
        _F + "finalize_rsop_run.py", (*_STD_FIN, "--transport", "{input:transport}"), "absolute",
        _RSOP_INPUTS,
    ),
    "run-rsop-user-oracle.sh": RunnerSpec(
        _B + "build-rsop-candidate.py", _RSOP_BUILDER, "absolute",
        _F + "finalize_rsop_user_run.py", (*_STD_FIN, "--transport", "{input:transport}"),
        "absolute", _RSOP_INPUTS,
    ),
}

#: The plan's four verdict shapes ("The canonical verdict, per shape").
SHAPE_OF_LANE: dict[str, str] = {
    **dict.fromkeys(
        (
            "wp2", "wp3-dc", "wp3-member", "object-security", "scripts-metadata",
            "publication", "lifecycle", "report-parity", "firewall", "fdeploy",
        ),
        "checks",
    ),
    "endpoint": "rows-state",
    **dict.fromkeys(
        (
            "lsdou-precedence", "disabled-block-enforced", "wmi-filtering",
            "wmi-filtering-error", "computer-security-filtering",
            "computer-security-filtering-deny-read", "computer-security-filtering-group-deny",
            "loopback-merge", "loopback-replace", "user-side-disabled",
            "user-security-filtering", "user-security-filtering-deny",
            "user-security-filtering-read-deny",
        ),
        "rows-state",
    ),
    "wp1b": "candidates",
    "wp0": "wp0-manifest",
}


@dataclass(frozen=True)
class Lane:
    name: str
    verdict: str  # repository-relative path of the banked verdict
    pack: str  # repository-relative pack directory
    commit: str  # the verdict's source commit
    runner: str
    spec: RunnerSpec
    shape: str
    original_verdict_name: str
    batch_manifest: str
    pack_files: dict[str, str]  # pack-relative member -> sha256, from the batch manifest

    @property
    def has_candidate(self) -> bool:
        return self.spec.builder is not None


def _registry_live(root: Path) -> list[str]:
    generator = root / "scripts" / "plan-033" / "report-bound-source-cost.py"
    namespace = runpy.run_path(str(generator))
    live: dict[str, str] = namespace["live_verdicts"]()
    commits = {
        json.loads((root / EVIDENCE / relative).read_text(encoding="utf-8"))["source"]["commit"]
        for relative in live
    }
    wp0 = namespace["_live_wp0_manifests"](commits)
    return sorted(
        [f"{EVIDENCE}/{relative}" for relative in live]
        + [path.relative_to(namespace["REPO_ROOT"]).as_posix() for path in wp0]
    )


def _batch_rows(root: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for manifest in sorted((root / EVIDENCE).glob("*-batch.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for row in data.get("runs", []):
            verdict = f"{EVIDENCE}/{row['verdict']}"
            if verdict in rows:
                raise Wp0Error(f"{verdict} is banked by two batch manifests")
            rows[verdict] = (manifest.relative_to(root).as_posix(), row)
    return rows


def live_lanes(root: Path = REPO_ROOT) -> list[Lane]:
    """Every live lane, matched to its batch row and runner spec; refuses gaps."""
    rows = _batch_rows(root)
    lanes: list[Lane] = []
    for verdict in _registry_live(root):
        if verdict not in rows:
            raise Wp0Error(f"{verdict} is live but no batch manifest banks it")
        manifest, row = rows[verdict]
        name = row["name"]
        runner = row["runner"]
        if runner not in RUNNERS:
            raise Wp0Error(f"{name}: runner {runner} has no launch spec")
        if name not in SHAPE_OF_LANE:
            raise Wp0Error(f"{name}: not one of the plan's four verdict shapes")
        record = json.loads((root / verdict).read_text(encoding="utf-8"))
        commit = record["source"]["commit"]
        if row.get("commit") != commit:
            raise Wp0Error(f"{name}: batch row commit {row.get('commit')} != verdict {commit}")
        files = row.get("files")
        if not isinstance(files, dict) or not files:
            raise Wp0Error(f"{name}: the batch row records no pack member digests")
        lanes.append(
            Lane(
                name=name,
                verdict=verdict,
                pack=verdict.rsplit("/", 1)[0],
                commit=commit,
                runner=runner,
                spec=RUNNERS[runner],
                shape=SHAPE_OF_LANE[name],
                original_verdict_name=row["original_verdict_name"],
                batch_manifest=manifest,
                pack_files={str(k): str(v) for k, v in files.items()},
            )
        )
    names = [lane.name for lane in lanes]
    if len(set(names)) != len(names):
        raise Wp0Error(f"two live verdicts for one lane: {sorted(names)}")
    return sorted(lanes, key=lambda lane: lane.name)


# --- run inputs ---------------------------------------------------------------


def _pointer(document: Any, pointer: str) -> Any:
    node = document
    for part in pointer.strip("/").split("/"):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(pointer)
        node = node[part]
    return node


_LOG_LINE = re.compile(r"^([A-Z_]+)=(.*)$")
_DRIVER_ROW = re.compile(r'^\s*"([a-z0-9-]+)\|([^|]+)\|(\d+)\|([^"]*)"\s*$')


def _controller_log(root: Path, lane: Lane, key: str) -> str:
    data = (root / lane.pack / "controller.log").read_bytes().decode("utf-8", errors="strict")
    values = {
        match.group(2)
        for line in data.splitlines()
        if (match := _LOG_LINE.match(line)) and match.group(1) == key
    }
    if len(values) != 1:
        raise KeyError(f"controller.log {key}= ({len(values)} values)")
    return values.pop()


def _driver_env(root: Path, lane: Lane, key: str) -> str:
    """The lane's environment assignment in the driver's LANES table at its commit."""
    text = show(root, lane.commit, "scripts/plan-033/run-requal-batch.sh")
    if text is None:
        raise KeyError("driver absent at the verdict's commit")
    for line in text.decode("utf-8").splitlines():
        match = _DRIVER_ROW.match(line)
        if match and match.group(1) == lane.name:
            for assignment in match.group(4).split():
                name, _, value = assignment.partition("=")
                if name == key:
                    return value
            raise KeyError(f"driver row for {lane.name} sets no {key}")
    raise KeyError(f"no driver row for {lane.name}")


def resolve_inputs(root: Path, lane: Lane) -> dict[str, str]:
    """Every run input the lane's launch templates need, from the banked record.

    Refuses (Wp0Error naming the input) if a source is missing or two sources
    disagree. The empty string is a value (an RSoP lane with no principal).
    """
    verdict = json.loads((root / lane.verdict).read_text(encoding="utf-8"))
    resolved: dict[str, str] = {}
    for name, sources in lane.spec.inputs:
        values: list[tuple[str, str]] = []
        for source in sources:
            try:
                if source.kind == "verdict":
                    raw = _pointer(verdict, source.key)
                elif source.kind == "controller-log":
                    raw = _controller_log(root, lane, source.key)
                elif source.kind == "candidate-json":
                    document = json.loads(
                        (root / lane.pack / "controller-candidate" / source.file).read_text(
                            encoding="utf-8"
                        )
                    )
                    raw = _pointer(document, source.key)
                elif source.kind == "driver-env":
                    raw = _driver_env(root, lane, source.key)
                elif source.kind == "literal":
                    raw = source.key
                else:  # pragma: no cover - the table is closed
                    raise Wp0Error(f"unknown input source kind {source.kind}")
            except (KeyError, OSError, ValueError) as exc:
                raise Wp0Error(
                    f"{lane.name}: run input {name!r} is not recorded ({source.describe()}: {exc})"
                ) from exc
            if raw is None:
                raw = ""
            if isinstance(raw, bool) or not isinstance(raw, (str, int)):
                raise Wp0Error(f"{lane.name}: run input {name!r} has a non-scalar value {raw!r}")
            values.append((source.describe(), str(raw)))
        distinct = {value for _, value in values}
        if len(distinct) != 1:
            raise Wp0Error(f"{lane.name}: run input {name!r} disagrees between {values}")
        resolved[name] = distinct.pop()
    return resolved


def render_argv(template: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """Expand a launch template; refuses an unknown or unresolved placeholder."""
    argv: list[str] = []
    for token in template:
        if not (token.startswith("{") and token.endswith("}")):
            argv.append(token)
            continue
        body = token[1:-1]
        kind, _, rest = body.partition(":")
        if kind == "input":
            if rest not in values:
                raise Wp0Error(f"launch template needs input {rest!r}")
            argv.append(values[rest])
        elif kind == "optional":
            flag, _, name = rest.partition(":")
            if name not in values:
                raise Wp0Error(f"launch template needs input {name!r}")
            if values[name]:
                argv.extend([flag, values[name]])
        elif kind == "flag":
            flag, _, name = rest.partition(":")
            if name not in values:
                raise Wp0Error(f"launch template needs input {name!r}")
            if values[name] not in ("0", "1"):
                raise Wp0Error(f"input {name!r} must be 0 or 1, not {values[name]!r}")
            if values[name] == "1":
                argv.append(flag)
        elif body in values:
            argv.append(values[body])
        else:
            raise Wp0Error(f"launch template placeholder {token} has no value")
    return argv


def script_argument(root: Path, script: str, form: str) -> str:
    """The script path as the runner spells it (relative to the repo, or absolute)."""
    return str(root / script) if form == "absolute" else script
