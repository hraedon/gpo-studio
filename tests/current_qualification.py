"""Which banked run is each lane's current certification.

Tests that ask "which run certifies lane X now?" read it here, never from a
batch manifest named in the test, so that banking a new batch changes no test.
That matters for every final release: its batch is banked in a commit B after
the freeze commit F, and the final-tag release gate
(``scripts/check_release_manifest.py``) refuses any test edit after F other than
the registry's verdict tables (Plan 036, the D-9 addendum).

The answer comes from the release evidence reports, read by version, so a new
release needs no edit here:

* the report of the package's version names a ``freeze`` batch manifest (from
  B on): that batch, for every lane. The earlier batches are history; their
  packs and tests stay as they are;
* its ``freeze`` is null (before B): the freeze batch of the previous release,
  the batch that release shipped on, with any run the verdict registry has made
  live since in place of the one it replaced. The previous release is the
  latest final tag ``vX.Y.Z`` below the package's version in HEAD's history,
  and its committed records must agree with that tag (version, approval,
  freeze); a missing or mismatched record fails, and nothing scans further
  back. For 1.2.0 that is 1.1.0 and the release 1.1.0 re-freeze batch.

The registry (``tests/test_committed_evidence.py``) stays the authority on what
is live; ``test_the_current_qualification_is_the_live_set`` in
``tests/test_current_qualification.py`` holds the two equal. The base batch is
read from the previous release's report, never from the registry, so that test
still says something while verdicts are pending requalification: every pending
verdict must be the previous release's run of its lane.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "plan-033"
DOCS = ROOT / "docs"
#: The verdict tables, as data (the same file tests/test_committed_evidence.py
#: loads and checks).
REGISTRY = EVIDENCE / "verdict-registry.json"
_REPORT_NAME = re.compile(r"release-evidence-report-(\d+)\.(\d+)\.(\d+)\.json")
_FINAL_TAG = re.compile(r"v\d+\.\d+\.\d+")


def _load(relative: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    return data


def _version_key(base: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in base.split("."))
    return major, minor, patch


def package_base_version() -> str:
    text = (ROOT / "src" / "gpo_studio" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)', text, re.M)
    assert match is not None, "src/gpo_studio/__init__.py binds no release version"
    return match.group(1)


def release_report(base: str | None = None) -> dict[str, Any]:
    """The release evidence report of ``base`` (default: the package's version)."""
    return _load(f"docs/release-evidence-report-{base or package_base_version()}.json")


def freeze() -> dict[str, str] | None:
    """The report's ``freeze`` (``commit`` and ``batch_manifest``), or None."""
    value = release_report()["freeze"]
    return None if value is None else dict(value)


def latest_freeze() -> dict[str, str]:
    """The newest banked freeze: this release's, else the previous release's.

    Tests that ask whether the release 1.1.0rc1 batches are history ask this,
    not :func:`freeze`, which is null again at the start of every release. It
    never answers "none": a previous release that cannot be bound fails here.
    """
    frozen = freeze()
    if frozen is not None:
        return frozen
    return previous_freeze()


def _gate() -> Any:
    """``scripts/check_release_manifest.py``, whose validators the records must pass."""
    spec = importlib.util.spec_from_file_location(
        "check_release_manifest", ROOT / "scripts" / "check_release_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=ROOT, capture_output=True, timeout=120, check=False,
    )


def _final_tags() -> list[str]:
    """Versions ``X.Y.Z`` of the final release tags ``vX.Y.Z`` in HEAD's history."""
    listed = _git("tag", "--merged", "HEAD", "--list", "v*")
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    names = listed.stdout.decode("utf-8").split()
    return [name[1:] for name in names if _FINAL_TAG.fullmatch(name)]


def _read(relative: str) -> bytes:
    """A committed record's bytes in the working tree; a missing record fails."""
    path = ROOT / relative
    assert path.is_file(), f"{relative} does not exist"
    return path.read_bytes()


def _read_at_tag(base: str, relative: str) -> bytes:
    """A record's bytes as released, at the tag ``v{base}``."""
    shown = _git("cat-file", "blob", f"refs/tags/v{base}^{{commit}}:{relative}")
    assert shown.returncode == 0, (
        f"{relative} is not in the commit tag v{base} names: "
        + shown.stderr.decode("utf-8", "replace").strip()
    )
    return shown.stdout


def release_problems(base: str, report: bytes, manifest: bytes, released: bytes) -> list[str]:
    """Why ``base``'s records cannot stand as a released, frozen release's.

    ``report`` and ``manifest`` are the records in the tree; ``released`` is the
    report as it was at the tag ``v{base}``. They must pass the release gate's
    validators, name ``base`` exactly (the file name, the report's version and
    manifest, and the manifest's title and application version are one
    identity), be approved with a freeze, and record the same freeze the tag
    released.
    """
    gate = _gate()
    manifest_rel = f"docs/release-evidence-{base}.md"
    problems, data = gate.report_problems(report, manifest_rel)
    problems = [f"report: {problem}" for problem in problems]
    if not problems:
        if data["version"] != base:
            problems.append(f"report: version is {data['version']!r}, not {base!r}")
        if data["status"] != "approved":
            problems.append(f"report: status is {data['status']!r}, not 'approved'")
        if data["freeze"] is None:
            problems.append("report: freeze is null; a final release records its freeze")
    problems += [
        f"manifest: {problem}" for problem in gate.manifest_problems(manifest, base, "approved")
    ]
    try:
        tagged = json.loads(released.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return [*problems, f"the report at tag v{base} is not JSON: {error}"]
    if not isinstance(tagged, dict) or (
        tagged.get("version"), tagged.get("status")
    ) != (base, "approved"):
        problems.append(f"the report at tag v{base} is not {base}'s approved report")
    elif not problems and tagged.get("freeze") != data["freeze"]:
        problems.append(
            f"report: freeze {data['freeze']!r} is not the freeze tag v{base} released, "
            f"{tagged.get('freeze')!r}"
        )
    return problems


@cache
def previous_release() -> str:
    """The release before the package's version: the latest final tag below it.

    Bound to the tags, not to whichever reports happen to be committed: the
    highest ``vX.Y.Z`` (no release candidate) in HEAD's history whose version is
    below the package's. Its records must then stand as that release's
    (:func:`release_problems`). A missing, unapproved or mismatched record for
    that release fails here; nothing scans further back to an older release.
    The test jobs check out full history with tags (``fetch-depth: 0``), as the
    release gate's freeze check already needs.
    """
    current = _version_key(package_base_version())
    earlier = [base for base in _final_tags() if _version_key(base) < current]
    assert earlier, (
        f"no final release tag below {package_base_version()} in HEAD's history; "
        "fetch the full history with tags (fetch-depth: 0)"
    )
    base = max(earlier, key=_version_key)
    report_rel = f"docs/release-evidence-report-{base}.json"
    problems = release_problems(
        base,
        _read(report_rel),
        _read(f"docs/release-evidence-{base}.md"),
        _read_at_tag(base, report_rel),
    )
    assert not problems, (
        f"the previous release, {base} (tag v{base}), has no records that stand as "
        f"its release: {'; '.join(problems)}"
    )
    return base


def previous_freeze() -> dict[str, str]:
    """The ``freeze`` of :func:`previous_release`: the batch it shipped on."""
    return dict(release_report(previous_release())["freeze"])


def _registry_live() -> set[str]:
    """Verdicts the registry treats as live: registered, not retired, not pending."""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    parked = set(registry["retired_verdicts"]) | set(registry["pending_requalification"])
    live = {path for path in registry["lane_verdicts"] if path not in parked}
    return live | {registry["wp0_manifest"]}


@cache
def current_batches() -> tuple[str, ...]:
    """The batch manifests whose runs are current, later ones overriding earlier.

    From B on, the release's own freeze batch alone. Before it, the previous
    release's freeze batch, then (in path order) every other banked batch that
    holds a run the registry has made live since; a live verdict no batch
    manifest names is left out, and the live-set test fails on it.
    """
    frozen = freeze()
    if frozen is not None:
        return (frozen["batch_manifest"],)
    base = previous_freeze()["batch_manifest"]
    named = {run["verdict"] for run in _load(base)["runs"]}
    wanted = _registry_live() - named
    later = [
        relative
        for relative in sorted(
            path.relative_to(ROOT).as_posix() for path in EVIDENCE.glob("*-batch.json")
        )
        if relative != base and wanted & {run["verdict"] for run in _load(relative)["runs"]}
    ]
    return (base, *later)


@cache
def current_runs() -> dict[str, dict[str, Any]]:
    """Lane name -> its current run row (``verdict`` is relative to docs/plan-033)."""
    batches = current_batches()
    runs: dict[str, dict[str, Any]] = {run["name"]: run for run in _load(batches[0])["runs"]}
    live = _registry_live()
    for batch in batches[1:]:
        for run in _load(batch)["runs"]:
            # Only the runs that are live replace the base's; the rest of a later
            # batch is history (its other verdicts were retired or replaced).
            if run["verdict"] in live:
                runs[run["name"]] = run
    return runs


def run(name: str) -> dict[str, Any]:
    return dict(current_runs()[name])


def verdict_path(name: str) -> Path:
    return EVIDENCE / run(name)["verdict"]


def verdict(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(verdict_path(name).read_text(encoding="utf-8-sig"))
    return data


def pack(name: str) -> Path:
    return verdict_path(name).parent
