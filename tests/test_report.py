from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import gpo_studio.report as report_module
from gpo_studio.backup import read_backup
from gpo_studio.gpp import GppCollection
from gpo_studio.import_export import collect_gpp_collections
from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.report import policy_report

_FIXTURES = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"


def test_policy_report_is_deterministic_plain_text() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Unicode policy — 東京",
        description="Review <script>alert('inert')</script>",
        revision=3,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Maximum",
                registry_type="REG_QWORD",
                value="18446744073709551615",
            ),
            RegistrySetting(
                id="setting-2",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Reviewers",
                registry_type="REG_MULTI_SZ",
                value=["alpha", "beta"],
            ),
        ),
    )

    first = policy_report(gpo)
    assert first == policy_report(gpo)
    assert "GPO Studio policy report" in first
    assert "Unicode policy — 東京" in first
    assert "18446744073709551615" in first
    assert "alpha; beta" in first
    assert "['alpha', 'beta']" not in first
    assert "Policy semantic SHA-256:" in first
    assert "<script>" in first  # safe because the API serves text/plain


def test_report_mapping_covers_every_typed_gpp_item_family() -> None:
    item_fields = [
        field.name
        for field in fields(GppCollection)
        if field.name not in {"scope", "source_files"}
        and not field.name.endswith(("_unknown_attrs", "_unknown_children"))
    ]
    distinctive = {
        field_name: tuple(range(cardinality))
        for cardinality, field_name in enumerate(item_fields, start=1)
    }
    counts = report_module._gpp_item_counts(cast(GppCollection, SimpleNamespace(**distinctive)))
    assert [count for _, count in counts] == list(range(1, len(item_fields) + 1))
    assert len(counts) == len(item_fields)


def test_policy_report_counts_native_drives_services_and_tasks() -> None:
    cases = (
        ("WI01A-DriveMaps-GPMC", "user: ", "4 drives item(s)"),
        ("WI01A-ServicesRecovery-GPMC", "computer: ", "2 services item(s)"),
        (
            "WI01A-SchedTasksFull-GPMC",
            "computer: ",
            "2 scheduled tasks item(s), 2 immediate tasks item(s)",
        ),
    )
    for fixture, scope_prefix, expected_count in cases:
        backup_gpo = read_backup(_FIXTURES / fixture).gpos[0]
        assert backup_gpo.content_root is not None
        gpo = GPO(
            guid=backup_gpo.guid,
            name=backup_gpo.display_name,
            domain=backup_gpo.domain,
            gpp_collections=collect_gpp_collections(backup_gpo.content_root),
        )
        rendered = policy_report(gpo)
        assert scope_prefix in rendered
        assert expected_count in rendered


def test_groups_and_local_groups_remain_distinct_and_output_is_inert() -> None:
    # Both model fields can coexist; reporting one must never hide or double-count the other.
    from gpo_studio.gpp import GppGroup
    from gpo_studio.gpp_adapters import GppLocalGroup

    collection = GppCollection(
        scope="computer",
        groups=(GppGroup(name="<script>group</script>"),),
        local_groups=(GppLocalGroup(group_name="local"),),
    )
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="inert",
        gpp_collections=(collection,),
    )
    rendered = policy_report(gpo)
    assert "1 groups item(s)" in rendered
    assert "1 local groups item(s)" in rendered
    assert rendered == policy_report(gpo)
