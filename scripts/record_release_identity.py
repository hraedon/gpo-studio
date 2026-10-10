"""Record the resolved release identity in the release evidence files.

Runs in the release ``publish`` job after the SBOM is finalized and the
identity-gated evidence manifest and report are copied to
``release-evidence.md`` and ``release-evidence-report.json`` in the working
directory. It stamps the report with the source commit, tag and artifact
hashes, and appends the same identity to the manifest.

    GITHUB_SHA=... GITHUB_REF_NAME=... python scripts/record_release_identity.py

Moved out of an inline ``python -`` heredoc in ``release.yml`` unchanged:
``tests/workflow_grammar.py`` refuses inline interpreter code.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    wheel = next(Path("dist").glob("gpo_studio-*.whl"))
    sdist = next(Path("dist").glob("gpo_studio-*.tar.gz"))
    sbom = Path("sbom.cdx.json")
    requirements = Path("requirements-lock.txt")
    artifacts = {
        "wheel_sha256": sha256(wheel),
        "sdist_sha256": sha256(sdist),
        "sbom_sha256": sha256(sbom),
    }

    report_path = Path("release-evidence-report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["release_identity"] = {
        "source_commit": os.environ["GITHUB_SHA"],
        "tag": os.environ["GITHUB_REF_NAME"],
    }
    report["artifact_hashes"].update(artifacts)
    report["artifact_hashes"].pop("evidence_report_sha256", None)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with Path("release-evidence.md").open("a", encoding="utf-8") as stream:
        stream.write("\n## Resolved release identity\n\n")
        stream.write(f"- Source commit: `{os.environ['GITHUB_SHA']}`\n")
        stream.write(f"- Tag: `{os.environ['GITHUB_REF_NAME']}`\n")
        stream.write(f"- Wheel `{wheel.name}` SHA-256: `{artifacts['wheel_sha256']}`\n")
        stream.write(
            f"- Source distribution `{sdist.name}` SHA-256: `{artifacts['sdist_sha256']}`\n"
        )
        stream.write(f"- SBOM SHA-256: `{artifacts['sbom_sha256']}`\n")
        stream.write(
            f"- Locked runtime requirements `{requirements.name}` SHA-256: "
            f"`{sha256(requirements)}`\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
