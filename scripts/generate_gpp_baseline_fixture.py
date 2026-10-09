"""Record what a pre-WI-080 Studio stored and exported for every native GPP capture.

Run with the source tree of the commit to record (the fixture committed under
``tests/fixtures/gpp-store-baseline-bd84b3a`` was made from ``bd84b3a``, the
last commit before WI-080)::

    git archive bd84b3a src | tar -x -C /some/dir
    python scripts/generate_gpp_baseline_fixture.py --source /some/dir/src \\
        --commit bd84b3a --out tests/fixtures/gpp-store-baseline-bd84b3a

and, to check that the committed fixture is exactly what that commit makes,
``--check`` in place of writing (``tests/test_gpp_native_preservation.py``
runs it when the commit is in the repository)::

    python scripts/generate_gpp_baseline_fixture.py --source /some/dir/src \\
        --commit bd84b3a --out tests/fixtures/gpp-store-baseline-bd84b3a --check

For each capture in ``tests/fixtures/native-gpp-gpmc`` and
``native-gpp-registry-gpmc`` it imports the backup with THAT tree's code, as
``POST /api/backups/import`` does, and writes one JSON file holding:

* ``gpo``: the GPO exactly as that tree stores it (``GPO.to_dict()``, the
  workspace snapshot), with a fixed GUID;
* ``exports``: every preference file that tree's ``serialize_gpp`` wrote for
  the stored collections, as text, keyed ``scope/Family/File.xml``;
* ``policy_semantic_sha256``, ``review_model_sha256`` and ``native_backup_id``
  as that tree computed them.

Import assigns editor ids with ``uuid.uuid4``; here that is replaced, for the
run, by a counter seeded per capture, so the records -- ids included -- are
the same on every run and ``--check`` can compare bytes. No id reaches an
export or a digest.

``tests/test_gpp_native_preservation.py`` loads these records with the
CURRENT code and holds its digests and exports to them. Nothing here may be
regenerated with a later commit: the point is that the records are the old
code's.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURE_ROOTS = (
    ROOT / "tests/fixtures/native-gpp-gpmc",
    ROOT / "tests/fixtures/native-gpp-registry-gpmc",
)


def _deterministic_uuid4(seed: int) -> None:
    """Make ``uuid.uuid4`` count from *seed* (the editor ids of one capture)."""
    counter = itertools.count(1)
    uuid.uuid4 = lambda: uuid.UUID(int=(seed << 64) | next(counter), version=4)


def _write(out: Path, commit: str) -> None:
    from gpo_studio.backup import read_backup
    from gpo_studio.canonical import policy_semantic_sha256, review_model_sha256
    from gpo_studio.export import native_backup_id
    from gpo_studio.gpp import serialize_gpp
    from gpo_studio.import_export import collect_gpp_collections
    from gpo_studio.model import GPO

    out.mkdir(parents=True, exist_ok=True)
    captures = sorted(m.parent for root in CAPTURE_ROOTS for m in root.glob("*/manifest.xml"))
    for index, capture in enumerate(captures):
        _deterministic_uuid4(index + 1)
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
                "source_commit": commit,
                "capture": capture.relative_to(ROOT).as_posix(),
            },
            "gpo": gpo.to_dict(),
            "exports": exports,
            "policy_semantic_sha256": policy_semantic_sha256(gpo),
            "review_model_sha256": review_model_sha256(gpo),
            "native_backup_id": native_backup_id(gpo),
        }
        (out / f"{capture.name}.json").write_text(
            json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true",
        help="regenerate into a temporary directory and compare with --out byte for byte",
    )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.source.resolve()))

    import gpo_studio

    loaded_from = Path(gpo_studio.__file__).resolve()
    if not loaded_from.is_relative_to(args.source.resolve()):
        raise SystemExit(f"gpo_studio was imported from {loaded_from}, not --source")
    if not args.check:
        _write(args.out, args.commit)
        return 0
    with tempfile.TemporaryDirectory() as raw:
        fresh = Path(raw)
        _write(fresh, args.commit)
        names = sorted(
            {p.name for p in fresh.glob("*.json")} | {p.name for p in args.out.glob("*.json")}
        )
        differing = [
            name for name in names
            if not (fresh / name).exists()
            or not (args.out / name).exists()
            or (fresh / name).read_bytes() != (args.out / name).read_bytes()
        ]
    if differing:
        print(f"differs from a fresh run at {args.commit}: {', '.join(differing)}")
        return 1
    print(f"{len(names)} records reproduce byte for byte at {args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
