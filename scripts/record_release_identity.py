"""Record the resolved release identity in the release evidence files.

Runs in the release ``publish`` job after the SBOM is finalized and the
identity-gated evidence manifest and report are copied to
``release-evidence.md`` and ``release-evidence-report.json`` in the working
directory. It stamps the copies, which are what the release publishes:

* the report gets ``release_identity`` (tag, source commit, workflow ref, run
  id, attempt and URL), the artifact hashes, and ``evidence.automated_evidence``;
* the manifest's "Automated evidence" placeholder paragraph (the one starting
  ``To be resolved from the tagged release workflow run:``) is replaced by the
  same record, and the resolved identity is appended.

What it records, and nothing more (Sol final-r3 B1): the workflow run that
published (its URL and attempt); that the jobs gating publication passed in
that run, which is true because ``publish`` runs only after ``release-identity``,
``ci``, ``identifier-gate`` and ``verify`` succeed (``needs:``, pinned by
``tests/test_release_manifest_gate.py``); that ``verify`` checked branch
coverage against the floor (the measured total stays in that job's log; it is
not carried to this job, so it is not recorded); and the artifact hashes.

It fails closed, before writing anything, if a GitHub variable it records is
missing or malformed, if the placeholder paragraph is not there exactly once,
if the report already carries ``automated_evidence``, or if a placeholder
survives the rewrite.

    GITHUB_SHA=... GITHUB_REF_NAME=... GITHUB_RUN_ID=... GITHUB_RUN_ATTEMPT=... \\
    GITHUB_SERVER_URL=... GITHUB_REPOSITORY=... GITHUB_WORKFLOW_REF=... \\
        python scripts/record_release_identity.py

Moved out of an inline ``python -`` heredoc in ``release.yml`` unchanged:
``tests/workflow_grammar.py`` refuses inline interpreter code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

#: The paragraph of the committed manifest this script resolves.
PLACEHOLDER = "To be resolved from the tagged release workflow run:"
SECTION = "## Automated evidence"
#: The jobs `publish` needs in release.yml; each must succeed before it runs.
GATING_JOBS = ("release-identity", "ci", "identifier-gate", "verify")
_ENV = {
    "GITHUB_SHA": re.compile(r"^[0-9a-f]{40}$"),
    "GITHUB_REF_NAME": re.compile(r"^v\d+\.\d+\.\d+(?:-rc\.[1-9]\d*)?$"),
    "GITHUB_RUN_ID": re.compile(r"^[1-9]\d*$"),
    "GITHUB_RUN_ATTEMPT": re.compile(r"^[1-9]\d*$"),
    "GITHUB_SERVER_URL": re.compile(r"^https://[A-Za-z0-9.-]+$"),
    "GITHUB_REPOSITORY": re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
    "GITHUB_WORKFLOW_REF": re.compile(
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[\w.-]+@\S+$"
    ),
}


class RecordError(Exception):
    """The release evidence cannot be recorded truthfully."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_identity(environ: dict[str, str]) -> dict[str, str]:
    """The GitHub variables recorded, refusing any missing or malformed one."""
    found: dict[str, str] = {}
    for name, pattern in _ENV.items():
        value = environ.get(name, "")
        if not pattern.match(value):
            raise RecordError(f"{name} is missing or malformed ({value!r}); refusing to record")
        found[name] = value
    return found


def resolve_manifest(text: str, record: str) -> str:
    """Replace the Automated evidence placeholder paragraph with ``record``."""
    lines = text.split("\n")
    if lines.count(SECTION) != 1:
        raise RecordError(f"the manifest has {lines.count(SECTION)} {SECTION!r} headings")
    start = lines.index(SECTION)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = lines[start + 1 : end]
    found = [i for i, line in enumerate(section) if line.startswith(PLACEHOLDER)]
    if len(found) != 1 or text.count(PLACEHOLDER) != 1:
        raise RecordError(
            f"the {SECTION!r} section must hold the placeholder paragraph exactly once; "
            f"found {text.count(PLACEHOLDER)}"
        )
    first = found[0]
    last = first
    while last + 1 < len(section) and section[last + 1].strip():
        last += 1
    section[first : last + 1] = record.rstrip("\n").split("\n")
    resolved = "\n".join([*lines[: start + 1], *section, *lines[end:]])
    if "To be resolved" in "\n".join(section):
        raise RecordError(f"a placeholder survived in the {SECTION!r} section")
    return resolved


def automated_record(identity: dict[str, str], run_url: str, hashes: dict[str, str]) -> str:
    jobs = ", ".join(f"`{job}`" for job in GATING_JOBS)
    return (
        "Recorded by the tagged release workflow run that published these assets:\n"
        f"[run {identity['GITHUB_RUN_ID']}]({run_url}), attempt "
        f"{identity['GITHUB_RUN_ATTEMPT']}, workflow `{identity['GITHUB_WORKFLOW_REF']}`,\n"
        f"tag `{identity['GITHUB_REF_NAME']}` at commit `{identity['GITHUB_SHA']}`. That\n"
        f"run's publish job runs only after the {jobs} jobs\n"
        "succeed, so each passed in that run (`ci` is every `ci.yml` job, `test-windows`\n"
        "included); its `verify` job checked branch coverage against the floor\n"
        "(`scripts/check_coverage.py`). The measured coverage total is in that job's log\n"
        "and is not recorded here. Artifact hashes: wheel\n"
        f"`{hashes['wheel_sha256']}`, source distribution\n"
        f"`{hashes['sdist_sha256']}`, SBOM `{hashes['sbom_sha256']}`\n"
        "(also in `SHA256SUMS`). Local success is not release evidence.\n"
    )


def record(root: Path, environ: dict[str, str]) -> None:
    identity = run_identity(environ)
    run_url = (
        f"{identity['GITHUB_SERVER_URL']}/{identity['GITHUB_REPOSITORY']}/actions/runs/"
        f"{identity['GITHUB_RUN_ID']}/attempts/{identity['GITHUB_RUN_ATTEMPT']}"
    )
    wheels = sorted((root / "dist").glob("gpo_studio-*.whl"))
    sdists = sorted((root / "dist").glob("gpo_studio-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RecordError(f"expected one wheel and one sdist in dist/, found {wheels + sdists}")
    sbom, requirements = root / "sbom.cdx.json", root / "requirements-lock.txt"
    artifacts = {
        "wheel_sha256": sha256(wheels[0]),
        "sdist_sha256": sha256(sdists[0]),
        "sbom_sha256": sha256(sbom),
    }
    requirements_sha256 = sha256(requirements)
    report_path, manifest_path = root / "release-evidence-report.json", root / "release-evidence.md"
    report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    evidence = report.get("evidence")
    if not isinstance(evidence, dict) or "automated_evidence" in evidence:
        raise RecordError("the report's evidence is missing or already holds automated_evidence")
    # Everything that can refuse runs before the first write.
    manifest = resolve_manifest(
        manifest_path.read_text(encoding="utf-8"),
        automated_record(identity, run_url, artifacts),
    )
    report["release_identity"] = {
        "source_commit": identity["GITHUB_SHA"],
        "tag": identity["GITHUB_REF_NAME"],
        "workflow_ref": identity["GITHUB_WORKFLOW_REF"],
        "run_id": identity["GITHUB_RUN_ID"],
        "run_attempt": identity["GITHUB_RUN_ATTEMPT"],
        "run_url": run_url,
    }
    report["artifact_hashes"].update(artifacts)
    report["artifact_hashes"].pop("evidence_report_sha256", None)
    evidence["automated_evidence"] = {
        "run_url": run_url,
        "run_attempt": identity["GITHUB_RUN_ATTEMPT"],
        "jobs_passed_before_publish": list(GATING_JOBS),
        "coverage": "checked against the floor by this run's verify job; total not recorded",
        "artifact_hashes": dict(artifacts),
    }
    report_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    report_path.write_text(report_text, encoding="utf-8")
    manifest += (
        "\n## Resolved release identity\n\n"
        f"- Source commit: `{identity['GITHUB_SHA']}`\n"
        f"- Tag: `{identity['GITHUB_REF_NAME']}`\n"
        f"- Workflow run: {run_url}\n"
        f"- Wheel `{wheels[0].name}` SHA-256: `{artifacts['wheel_sha256']}`\n"
        f"- Source distribution `{sdists[0].name}` SHA-256: `{artifacts['sdist_sha256']}`\n"
        f"- SBOM SHA-256: `{artifacts['sbom_sha256']}`\n"
        f"- Locked runtime requirements `{requirements.name}` SHA-256: "
        f"`{requirements_sha256}`\n"
    )
    manifest_path.write_text(manifest, encoding="utf-8")


def main() -> int:
    try:
        record(Path.cwd(), dict(os.environ))
    except (RecordError, OSError, ValueError, KeyError) as error:
        print(f"record release identity: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
