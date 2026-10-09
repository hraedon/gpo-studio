"""Record what a pre-WI-080 Studio stored and exported for every native GPP capture.

Run with the source tree of the commit to record (the fixture committed under
``tests/fixtures/gpp-store-baseline-bd84b3a`` was made from ``bd84b3a``, the
last commit before WI-080)::

    git archive bd84b3a src | tar -x -C /some/dir
    python scripts/generate_gpp_baseline_fixture.py --source /some/dir/src \\
        --commit bd84b3a --out tests/fixtures/gpp-store-baseline-bd84b3a

For each capture in ``tests/fixtures/native-gpp-gpmc`` and
``native-gpp-registry-gpmc`` it imports the backup with THAT tree's code, as
``POST /api/backups/import`` does, and writes one JSON file holding:

* ``gpo``: the GPO exactly as that tree stores it (``GPO.to_dict()``, the
  workspace snapshot), with a fixed GUID;
* ``exports``: every preference file that tree's ``serialize_gpp`` wrote for
  the stored collections, as text, keyed ``scope/Family/File.xml``;
* ``policy_semantic_sha256``, ``review_model_sha256`` and ``native_backup_id``
  as that tree computed them.

``tests/test_gpp_native_preservation.py`` loads these records with the
CURRENT code and holds its digests and exports to them. Nothing here may be
regenerated with a later commit: the point is that the records are the old
code's.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURE_ROOTS = (
    ROOT / "tests/fixtures/native-gpp-gpmc",
    ROOT / "tests/fixtures/native-gpp-registry-gpmc",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.source.resolve()))

    import gpo_studio
    from gpo_studio.backup import read_backup
    from gpo_studio.canonical import policy_semantic_sha256, review_model_sha256
    from gpo_studio.export import native_backup_id
    from gpo_studio.gpp import serialize_gpp
    from gpo_studio.import_export import collect_gpp_collections
    from gpo_studio.model import GPO

    loaded_from = Path(gpo_studio.__file__).resolve()
    if not loaded_from.is_relative_to(args.source.resolve()):
        raise SystemExit(f"gpo_studio was imported from {loaded_from}, not --source")
    args.out.mkdir(parents=True, exist_ok=True)
    captures = sorted(m.parent for root in CAPTURE_ROOTS for m in root.glob("*/manifest.xml"))
    for index, capture in enumerate(captures):
        backup_gpo = read_backup(capture).gpos[0]
        assert backup_gpo.content_root is not None
        gpo = GPO(
            guid=f"0b0d0080-0000-4000-8000-{index:012d}",
            name=capture.name,
            domain="studio.local",
            status="archived",
            gpp_collections=collect_gpp_collections(backup_gpo.content_root),
        )
        exports = {
            f"{collection.scope}/{name}": data.decode("utf-8")
            for collection in gpo.gpp_collections
            for name, data in sorted(serialize_gpp(collection).items())
        }
        record = {
            "provenance": {
                "generated_by": "scripts/generate_gpp_baseline_fixture.py",
                "source_commit": args.commit,
                "capture": capture.relative_to(ROOT).as_posix(),
            },
            "gpo": gpo.to_dict(),
            "exports": exports,
            "policy_semantic_sha256": policy_semantic_sha256(gpo),
            "review_model_sha256": review_model_sha256(gpo),
            "native_backup_id": native_backup_id(gpo),
        }
        (args.out / f"{capture.name}.json").write_text(
            json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
