#!/usr/bin/env python3
"""Build the deterministic synthetic candidate used by the WP-2 Windows gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.model import GPO, RegistrySetting


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="WP2 Synthetic Native Backup",
        domain="synthetic.test",
        settings=(
            RegistrySetting(
                id="machine",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\GPOStudio\WP2",
                value_name="MachineValue",
                registry_type="REG_DWORD",
                value=42,
            ),
            RegistrySetting(
                id="user",
                side="user",
                hive="HKCU",
                key=r"Software\Policies\GPOStudio\WP2",
                value_name="UserValue",
                registry_type="REG_SZ",
                value="synthetic-wp2-value",
            ),
        ),
    )
    expected = {
        "backup_id": native_backup_id(gpo),
        "source_gpo_id": "{" + gpo.guid.upper() + "}",
        "settings": [
            {
                "side": setting.side,
                "hive": setting.hive,
                "key": setting.key,
                "value_name": setting.value_name,
                "registry_type": setting.registry_type,
                "value": setting.value,
            }
            for setting in gpo.settings
        ],
    }
    (args.output_dir / "candidate.zip").write_bytes(gpmc_backup_bundle(gpo))
    (args.output_dir / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
