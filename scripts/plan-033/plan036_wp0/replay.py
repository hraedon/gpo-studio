"""Part 2: the two-clone builder replay and the re-grade over banked packs.

**Clones.** Every run happens in a fresh `git worktree add --detach` of the
commit under test, with its own environment synced from `uv.lock`
(`uv sync --locked --offline`: the warm cache supplies it; nothing is fetched).
A clone's interpreter therefore imports that clone's `src/` (the editable
install points at one tree), and a builder or finalizer never sees the tree
the tool runs from. The finalizers' own `git rev-parse HEAD` and
`git status --porcelain` then report the clone's commit and a clean tree.

**Launch.** As the runners launch them: `python <script> <args>` from the
clone's root, no interpreter flags (`lanes.RUNNER_PYTHON_FLAGS`), the script
spelt as the runner spells it, the interpreter the clone's environment
provides, with `VIRTUAL_ENV` and `PATH` set as `uv run` sets them. The
environment is reduced to `RUN_ENVIRONMENT` plus a private `TMPDIR`, and the
values the run saw are recorded (filtered by the tracer's allowlist).

**Builder replay** (lanes with a candidate): clone A runs the builder plainly
(the real interpreter, not through the tracer), clone B runs it traced, and
clone A runs it once more in observe mode. All three outputs must be
byte-identical to each other, to every candidate hash the banked verdict
records (`candidate`, `candidate_delivery`), and to the banked
`controller-candidate/` file set. Traced and observe runs must agree on
`sys.flags`, the final `sys.path`, the working directory, the filtered
environment and the module-to-file map, with each clone's root (and each
run's private directories) written as placeholders.

**Pack reconstruction.** A pack is the controller's run directory verbatim,
plus `controller-candidate/` and `controller.log` (and an RSoP verdict renamed
to `verification.json`), so banking is inverted exactly: every member is first
checked against the batch manifest's SHA-256 (same set of members, same
bytes: a tampered, added or removed member refuses), then the run directory is
the pack without `controller-candidate/`, `controller.log`, the banked verdict
and (WP-0) the finalizer's own `normalized/` output; the candidate root is a
copy of `controller-candidate/`.

**Re-grade.** The shipping finalizer, through its normal entry point, with
`--no-tag` and every recorded run input (`lanes.resolve_inputs`, which refuses
a missing one). After it runs, every file it left in the run directory must be
byte-identical to the pack's member of the same name (for WP-0 that includes
the regenerated `normalized/` files), and the verdict it wrote is compared by
`projection.compare`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lanes import RUNNER_PYTHON_FLAGS, Lane, render_argv, resolve_inputs, script_argument
from .repo import PACKAGE_DIR, Wp0Error, detached_worktree, git, is_clean, sha256_file

TRACER = PACKAGE_DIR / "tracer.py"
#: Variables passed through from the tool's environment (everything else is dropped).
RUN_ENVIRONMENT = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "SYSTEMROOT", "WINDIR", "USERPROFILE", "PATHEXT", "COMSPEC",
)
#: State fields the traced and observe runs must agree on.
EQUIVALENCE_FIELDS = ("flags", "path_final", "cwd", "env", "modules")
DEFAULT_TIMEOUT = 1800


@dataclass
class Clone:
    path: Path
    commit: str
    python: list[str]
    env: dict[str, str]


@contextmanager
def clone(
    repo: Path, commit: str, parent: Path, name: str, *, launcher: str = "uv"
) -> Iterator[Clone]:
    """A fresh worktree of *commit* with an interpreter that imports its own src/.

    launcher "uv": `uv sync --locked --offline` into the clone's `.venv` (the
    lanes' environment). launcher "current": this interpreter with the clone's
    `src` on PYTHONPATH (tests on synthetic repositories only).
    """
    with detached_worktree(repo, commit, parent, name) as path:
        base = {k: os.environ[k] for k in RUN_ENVIRONMENT if k in os.environ}
        if launcher == "uv":
            result = subprocess.run(
                ["uv", "sync", "--locked", "--offline", "--quiet"],
                cwd=path,
                capture_output=True,
                check=False,
                env={**os.environ, "VIRTUAL_ENV": "", "UV_PROJECT_ENVIRONMENT": ""},
            )
            if result.returncode != 0:
                raise Wp0Error(
                    f"uv sync failed in {path}: "
                    + result.stderr.decode("utf-8", errors="replace").strip()[-400:]
                )
            bindir = path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            python = [str(bindir / ("python.exe" if os.name == "nt" else "python"))]
            env = {
                **base,
                "VIRTUAL_ENV": str(path / ".venv"),
                "PATH": str(bindir) + os.pathsep + base.get("PATH", ""),
            }
        elif launcher == "current":
            python = [sys.executable]
            env = {**base, "PYTHONPATH": str(path / "src")}
        else:
            raise Wp0Error(f"unknown launcher {launcher}")
        yield Clone(path=path, commit=commit, python=python, env=env)


@dataclass
class RunResult:
    mode: str  # plain | traced | observe
    argv: list[str]
    exit: int
    stdout: str
    stderr: str
    seconds: float
    stdout_bytes: bytes = b""
    trace: dict[str, Any] | None = None
    children: list[dict[str, Any]] = field(default_factory=list)


def run(
    target: Clone,
    script_argv: list[str],
    mode: str,
    private: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> RunResult:
    """Run `python <flags> <script argv>` in the clone, plainly, traced or observed."""
    private.mkdir(parents=True, exist_ok=False)
    tmp = private / "tmp"
    tmp.mkdir()
    trace_dir = private / "trace"
    if mode == "plain":
        argv = [*target.python, *RUNNER_PYTHON_FLAGS, *script_argv]
    elif mode in ("traced", "observe"):
        options = ["--observe"] if mode == "observe" else []
        argv = [
            *target.python, *RUNNER_PYTHON_FLAGS, str(TRACER), str(trace_dir), *options,
            "--", *script_argv,
        ]
    else:
        raise Wp0Error(f"unknown run mode {mode}")
    env = {**target.env, "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)}
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, cwd=target.path, env=env, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise Wp0Error(f"{mode} run timed out after {timeout} s: {script_argv[0]}") from exc
    seconds = time.monotonic() - started
    outcome = RunResult(
        mode=mode,
        argv=argv,
        exit=result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
        seconds=round(seconds, 3),
        stdout_bytes=result.stdout,
    )
    if mode != "plain":
        trace_file = trace_dir / "trace.json"
        if not trace_file.is_file():
            raise Wp0Error(
                f"the tracer wrote no trace for {script_argv[0]}: {outcome.stderr[-400:]}"
            )
        outcome.trace = json.loads(trace_file.read_text(encoding="utf-8"))
        outcome.children = _child_traces(outcome.trace)
    return outcome


def _child_traces(trace: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child in trace.get("children", []):
        path = Path(child) / "trace.json"
        if not path.is_file():
            trace.setdefault("incomplete", []).append(f"a traced child wrote no trace: {child}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        found.append(data)
        found.extend(_child_traces(data))
    return found


def tree_digests(directory: Path) -> dict[str, str]:
    """{relative posix path: sha256} for every file under *directory*."""
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def normalise_state(trace: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    """The equivalence fields, with clone roots and private dirs as placeholders."""
    ordered = sorted(replacements.items(), key=lambda item: -len(item[0]))

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in ordered:
                if old and (value == old or value.startswith(old.rstrip("/\\") + os.sep)):
                    return new + value[len(old) :]
            return value
        if isinstance(value, list):
            return [fix(item) for item in value]
        if isinstance(value, dict):
            return {key: fix(item) for key, item in value.items()}
        return value

    state = {name: fix(trace.get(name)) for name in EQUIVALENCE_FIELDS}
    modules = state.get("modules") or {}
    # The plan compares "the map from each imported repository module to the
    # file it was loaded from": the tracer itself imports standard-library
    # modules a plain run may not (json, subprocess), which are not the lane's.
    state["modules"] = {
        name: path
        for name, path in modules.items()
        if str(path).startswith("<clone>") and not str(path).endswith("plan036_wp0/tracer.py")
    }
    return state


def banked_candidate_hashes(
    verdict: dict[str, Any], flat_delivery_file: str | None = None
) -> dict[str, str]:
    """Every controller-side candidate hash the verdict records, by candidate file.

    `candidate` ({file: sha256}), `candidate_delivery` keyed by file
    ({file: {"controller_sha256": ...}}), or the flat form one finalizer
    writes ({"controller_sha256": ...}), which hashes the file the lane's
    spec names (refused when the spec names none).
    """
    hashes: dict[str, str] = {}

    def add(name: str, digest: object) -> None:
        if not isinstance(digest, str):
            return
        if name in hashes and hashes[name] != digest:
            raise Wp0Error(f"the verdict records two hashes for candidate {name}")
        hashes[name] = digest

    candidate = verdict.get("candidate")
    if isinstance(candidate, dict):
        for name, digest in candidate.items():
            add(str(name), digest)
    delivery = verdict.get("candidate_delivery")
    if isinstance(delivery, dict) and "controller_sha256" in delivery:
        if flat_delivery_file is None:
            raise Wp0Error("a flat candidate_delivery, and the lane names no file it hashes")
        add(flat_delivery_file, delivery["controller_sha256"])
    elif isinstance(delivery, dict):
        for name, entry in delivery.items():
            if isinstance(entry, dict):
                add(str(name), entry.get("controller_sha256"))
    return hashes


def replay_builder(
    lane: Lane, root: Path, clone_a: Clone, clone_b: Clone, workdir: Path
) -> dict[str, Any]:
    """Two fresh clones at the same commit build the candidate; compare everything."""
    if lane.spec.builder is None:
        return {
            "applicable": False,
            "reason": "no candidate: WP-0's fixture is authored on the guest",
        }
    inputs = resolve_inputs(root, lane)
    base = workdir / lane.name / "replay"
    # One output path for all three runs, moved aside after each: a builder that
    # prints its output directory (scripts-metadata does) then prints the same
    # bytes in every run, and the runs differ only in what is under test.
    out = base / "candidate"
    outputs: dict[str, dict[str, str]] = {}
    runs: dict[str, RunResult] = {}
    for label, target, mode in (
        ("plain", clone_a, "plain"),
        ("traced", clone_b, "traced"),
        ("observe", clone_a, "observe"),
    ):
        argv = [
            script_argument(target.path, lane.spec.builder, lane.spec.builder_script_form),
            *render_argv(lane.spec.builder_argv, {**inputs, "out": str(out)}),
        ]
        result = run(target, argv, mode, base / label / "private")
        if result.exit != 0:
            raise Wp0Error(
                f"{lane.name}: the builder exited {result.exit} ({mode}): {result.stderr[-600:]}"
            )
        if not is_clean(target.path):
            raise Wp0Error(f"{lane.name}: the builder left the clone dirty ({mode})")
        if lane.spec.builder_stdout:
            # `... | tee "$CANDIDATE_DIR/builder.stdout.txt"`: the bytes as written
            out.mkdir(parents=True, exist_ok=True)
            (out / lane.spec.builder_stdout).write_bytes(result.stdout_bytes)
        kept = base / label / "candidate"
        if out.exists():
            out.rename(kept)
        outputs[label] = tree_digests(kept)
        runs[label] = result
    verdict = json.loads((root / lane.verdict).read_text(encoding="utf-8"))
    recorded = banked_candidate_hashes(verdict, lane.spec.flat_delivery_file)
    banked_tree = tree_digests(root / lane.pack / "controller-candidate")
    problems: list[str] = []
    if outputs["plain"] != outputs["traced"]:
        problems.append(
            "the two clones built different candidates: "
            + _set_difference(outputs["plain"], outputs["traced"])
        )
    if outputs["plain"] != outputs["observe"]:
        problems.append("the observe run built a different candidate")
    if not recorded:
        # Nothing banked to reproduce: an "identical" here would be vacuous.
        problems.append("the verdict records no candidate hash, so the replay proves nothing")
    for name, digest in sorted(recorded.items()):
        if outputs["plain"].get(name) != digest:
            built = outputs["plain"].get(name)
            problems.append(f"candidate {name}: built {built}, the verdict records {digest}")
    # Files the verdict does not hash but the pack banked: compared, and any
    # difference reported, but outside the plan's definition of identical
    # ("compared with each other and with the banked hashes").
    unhashed = {
        name for name in set(outputs["plain"]) | set(banked_tree) if name not in recorded
    }
    unhashed_differences = sorted(
        name for name in unhashed if outputs["plain"].get(name) != banked_tree.get(name)
    )
    traced, observed = runs["traced"].trace or {}, runs["observe"].trace or {}
    state_b = normalise_state(traced, {str(clone_b.path): "<clone>",
                                       str(base / "traced"): "<private>"})
    state_a = normalise_state(observed, {str(clone_a.path): "<clone>",
                                         str(base / "observe"): "<private>"})
    equivalence = [f"{name}" for name in EQUIVALENCE_FIELDS if state_a[name] != state_b[name]]
    if equivalence:
        problems.append(f"traced and untraced runs differ in {equivalence}")
    return {
        "applicable": True,
        "commit": clone_a.commit,
        "inputs": inputs,
        "argv": [*RUNNER_PYTHON_FLAGS, *argv],
        "candidate": outputs["plain"],
        "recorded_hashes": recorded,
        "identical": not problems,
        "problems": problems,
        "unhashed_files_differing_from_pack": unhashed_differences,
        "equivalence_fields_differing": equivalence,
        "trace": traced,
        "trace_children": runs["traced"].children,
        "seconds": {label: result.seconds for label, result in runs.items()},
    }


def _set_difference(a: dict[str, str], b: dict[str, str]) -> str:
    return (
        f"only built {sorted(set(a) - set(b))}, only banked {sorted(set(b) - set(a))}, "
        f"different {sorted(k for k in set(a) & set(b) if a[k] != b[k])}"
    )


# --- packs and re-grade ------------------------------------------------------------------


def verify_pack(root: Path, lane: Lane) -> list[str]:
    """The pack holds exactly the batch manifest's members, byte for byte."""
    on_disk = tree_digests(root / lane.pack)
    problems: list[str] = []
    for member in sorted(set(lane.pack_files) | set(on_disk)):
        recorded, actual = lane.pack_files.get(member), on_disk.get(member)
        if recorded is None:
            problems.append(f"{member}: in the pack, not in the batch manifest")
        elif actual is None:
            problems.append(f"{member}: in the batch manifest, missing from the pack")
        elif recorded != actual:
            problems.append(f"{member}: SHA-256 {actual} is not the manifest's {recorded}")
    return problems


def _excluded_from_run(lane: Lane, member: str) -> bool:
    if member.startswith("controller-candidate/") or member == "controller.log":
        return True
    banked_verdict = lane.verdict.rsplit("/", 1)[1]
    if member == banked_verdict:
        return True
    return lane.shape == "wp0-manifest" and member.startswith("normalized/")


def reconstruct(root: Path, lane: Lane, destination: Path) -> tuple[Path, Path]:
    """The run directory and candidate root banking started from (refuses a bad pack)."""
    problems = verify_pack(root, lane)
    if problems:
        raise Wp0Error(f"{lane.name}: the pack does not match its batch manifest: {problems[:5]}")
    pack = root / lane.pack
    run_dir = destination / "run"
    candidate = destination / "candidate"
    run_dir.mkdir(parents=True, exist_ok=False)
    for member in sorted(lane.pack_files):
        if _excluded_from_run(lane, member):
            continue
        target = run_dir / member
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pack / member, target)
    if (pack / "controller-candidate").is_dir():
        shutil.copytree(pack / "controller-candidate", candidate)
    else:
        candidate.mkdir()
    return run_dir, candidate


def regrade(
    lane: Lane, root: Path, target: Clone, workdir: Path, mode: str, label: str
) -> dict[str, Any]:
    """Run the lane's finalizer at the clone's commit over a reconstructed pack."""
    inputs = resolve_inputs(root, lane)
    base = workdir / lane.name / f"regrade-{label}-{mode}"
    run_dir, candidate = reconstruct(root, lane, base / "pack")
    values = {**inputs, "run": str(run_dir), "candidate": str(candidate), "repo": str(target.path)}
    argv = [
        script_argument(target.path, lane.spec.finalizer, lane.spec.finalizer_script_form),
        *render_argv(lane.spec.finalizer_argv, values),
        "--no-tag",
    ]
    before = tree_digests(run_dir)
    result = run(target, argv, mode, base / "private")
    if not is_clean(target.path):
        raise Wp0Error(f"{lane.name}: the finalizer left the clone dirty ({mode})")
    output = run_dir / lane.original_verdict_name
    if not output.is_file():
        return {
            "commit": target.commit,
            "mode": mode,
            "argv": result.argv[len(target.python) :],
            "exit": result.exit,
            "verdict": None,
            "problems": [f"the finalizer wrote no {lane.original_verdict_name}: "
                         f"{result.stderr[-600:]}"],
            "trace": result.trace,
            "trace_children": result.children,
        }
    verdict = json.loads(output.read_text(encoding="utf-8"))
    after = tree_digests(run_dir)
    expected_after = {
        member: digest
        for member, digest in lane.pack_files.items()
        if not member.startswith("controller-candidate/") and member != "controller.log"
        and member != lane.verdict.rsplit("/", 1)[1]
    }
    problems: list[str] = []
    after.pop(lane.original_verdict_name, None)
    if after != expected_after:
        problems.append(
            "the run directory after the re-grade differs from the pack: "
            + _set_difference(after, expected_after)
        )
    if tree_digests(candidate) != {
        member[len("controller-candidate/") :]: digest
        for member, digest in lane.pack_files.items()
        if member.startswith("controller-candidate/")
    }:
        problems.append("the finalizer changed the candidate root")
    return {
        "commit": target.commit,
        "mode": mode,
        "argv": result.argv[len(target.python) :],
        "exit": result.exit,
        "verdict": verdict,
        "run_root": str(run_dir),
        "candidate_root": str(candidate),
        "files_before": len(before),
        "problems": problems,
        "trace": result.trace,
        "trace_children": result.children,
        "seconds": result.seconds,
    }


def git_identity(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()
