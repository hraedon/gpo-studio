from __future__ import annotations

import io
import json
import zipfile

from gpo_studio.canonical import (
    canonical_json,
    semantic_dict,
    semantic_hash,
    semantic_hash_link,
    semantic_hash_setting,
)
from gpo_studio.export import export_bundle
from gpo_studio.model import GPO, GPOLink, RegistrySetting


def sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic workstation policy",
        description="Fixture only",
        revision=3,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(GPOLink(id="link-1", target="OU=Lab,DC=example,DC=test"),),
    )


def test_canonical_json_sorts_keys() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_escapes_strings() -> None:
    assert canonical_json("\t") == r'"\t"'
    assert canonical_json("\n") == r'"\n"'
    assert canonical_json("\r") == r'"\r"'
    assert canonical_json("\b") == r'"\b"'
    assert canonical_json("\f") == r'"\f"'
    assert canonical_json('"') == r'"\""'
    assert canonical_json("\\") == r'"\\"'
    assert canonical_json("\x00") == r'"\u0000"'
    assert canonical_json("\x1f") == r'"\u001f"'
    assert canonical_json("/") == '"/"'
    assert canonical_json("é") == '"é"'


def test_canonical_json_numbers() -> None:
    assert canonical_json(42) == "42"
    assert canonical_json(0) == "0"
    assert canonical_json(-5) == "-5"
    assert canonical_json(3.14) == "3.14"


def test_semantic_hash_stable_across_ordering() -> None:
    setting_a = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    setting_b = RegistrySetting(
        id="s2",
        side="computer",
        hive="HKLM",
        key=r"Software\B",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    gpo_ab = GPO(guid="g1", name="test", settings=(setting_a, setting_b))
    gpo_ba = GPO(guid="g1", name="test", settings=(setting_b, setting_a))
    assert semantic_hash(gpo_ab) == semantic_hash(gpo_ba)


def test_semantic_hash_changes_on_value_change() -> None:
    setting_1 = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    setting_2 = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=2,
    )
    gpo_1 = GPO(guid="g1", name="test", settings=(setting_1,))
    gpo_2 = GPO(guid="g1", name="test", settings=(setting_2,))
    assert semantic_hash(gpo_1) != semantic_hash(gpo_2)


def test_semantic_hash_excludes_non_semantic_fields() -> None:
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    gpo_a = GPO(
        guid="g1",
        name="test",
        revision=1,
        created_at="2024-01-01",
        updated_at="2024-01-02",
        settings=(setting,),
    )
    gpo_b = GPO(
        guid="g1",
        name="test",
        revision=99,
        created_at="2025-01-01",
        updated_at="2025-01-02",
        settings=(setting,),
    )
    assert semantic_hash(gpo_a) == semantic_hash(gpo_b)


def test_semantic_hash_setting() -> None:
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    h = semantic_hash_setting(setting)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    setting_diff_val = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=2,
    )
    assert semantic_hash_setting(setting) != semantic_hash_setting(setting_diff_val)
    setting_diff_case = RegistrySetting(
        id="s2",
        side="computer",
        hive="HKLM",
        key=r"software\a",
        value_name="enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    assert semantic_hash_setting(setting) == semantic_hash_setting(setting_diff_case)


def test_semantic_hash_link() -> None:
    link = GPOLink(
        id="l1",
        target="OU=Lab,DC=example,DC=test",
        enabled=True,
        enforced=False,
        order=1,
    )
    h = semantic_hash_link(link)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    link_diff_case = GPOLink(
        id="l2",
        target="ou=lab,dc=example,dc=test",
        enabled=True,
        enforced=False,
        order=1,
    )
    assert semantic_hash_link(link) == semantic_hash_link(link_diff_case)
    link_diff_order = GPOLink(
        id="l1",
        target="OU=Lab,DC=example,DC=test",
        enabled=True,
        enforced=False,
        order=2,
    )
    assert semantic_hash_link(link) != semantic_hash_link(link_diff_order)


def test_bundle_v2_includes_semantic_hash() -> None:
    gpo = sample_gpo()
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 2
        assert "semantic_sha256" in manifest
        assert manifest["semantic_sha256"] == semantic_hash(gpo)
        assert "canonical_model" in manifest
        assert manifest["canonical_model"] == semantic_dict(gpo)
