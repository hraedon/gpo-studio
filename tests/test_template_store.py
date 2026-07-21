from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.template_store import (
    TemplateError,
    build_lock,
    detect_central_store,
    detect_collisions,
    ingest_source,
    merge_catalogues,
    validate_lock,
)

_ADMX_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions
    revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="test" namespace="Synthetic.{ns}" />
  </policyNamespaces>
  <resources minRequiredRevision="1.0" />
  <categories>
    <category name="Cat" displayName="$(string.Cat)" />
  </categories>
  <policies>
    <policy name="{policy_name}" class="Machine" displayName="$(string.{policy_name})"
            explainText="$(string.{policy_name}_help)" key="Software\\Policies\\Synthetic"
            valueName="{policy_name}">
      <parentCategory ref="Cat" />
      <supportedOn ref="SUPPORTED_Win7" />
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
    </policy>
  </policies>
</policyDefinitions>
"""

_ADML_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources
    revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <displayName>Test</displayName>
  <description>Test</description>
  <resources>
    <stringTable>
      <string id="Cat">Category</string>
      <string id="{policy_name}">{policy_name} Display</string>
      <string id="{policy_name}_help">Help text</string>
    </stringTable>
  </resources>
</policyDefinitionResources>
"""


def _write_admx_pair(directory: Path, filename: str, ns: str, policy_name: str) -> None:
    admx = _ADMX_TEMPLATE.format(ns=ns, policy_name=policy_name)
    adml = _ADML_TEMPLATE.format(policy_name=policy_name)
    (directory / f"{filename}.admx").write_text(admx, encoding="utf-8")
    en_us = directory / "en-US"
    en_us.mkdir(exist_ok=True)
    (en_us / f"{filename}.adml").write_text(adml, encoding="utf-8")


class TestIngestSource:
    def test_ingest_valid_directory(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("test-src", "local", tmp_path)
        assert result.source.name == "test-src"
        assert result.source.kind == "local"
        assert len(result.catalogue.policies) == 1
        assert result.catalogue.policies[0].qualified_id == "Synthetic.TestNS:TestPolicy"
        assert result.errors == ()
        assert len(result.source.files) == 2

    def test_ingest_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(TemplateError, match="does not exist"):
            ingest_source("bad", "local", tmp_path / "nonexistent")

    def test_ingest_missing_adml_reports_error(self, tmp_path: Path) -> None:
        admx = _ADMX_TEMPLATE.format(ns="TestNS", policy_name="TestPolicy")
        (tmp_path / "orphan.admx").write_text(admx, encoding="utf-8")
        result = ingest_source("orphan-src", "local", tmp_path)
        assert len(result.errors) == 1
        assert "ADML" in result.errors[0].message
        assert len(result.catalogue.policies) == 0

    def test_ingest_malformed_admx_skips_and_continues(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "good", "GoodNS", "GoodPolicy")
        (tmp_path / "bad.admx").write_text("not xml at all", encoding="utf-8")
        en_us = tmp_path / "en-US"
        en_us.mkdir(exist_ok=True)
        (en_us / "bad.adml").write_text("not xml", encoding="utf-8")
        result = ingest_source("mixed", "local", tmp_path)
        assert len(result.catalogue.policies) == 1
        assert len(result.errors) == 1
        assert "bad.admx" in result.errors[0].relative_path

    def test_ingest_empty_directory(self, tmp_path: Path) -> None:
        result = ingest_source("empty", "local", tmp_path)
        assert len(result.catalogue.policies) == 0
        assert result.errors == ()


class TestDetectCentralStore:
    def test_direct_policy_definitions(self, tmp_path: Path) -> None:
        pd = tmp_path / "PolicyDefinitions"
        pd.mkdir()
        (pd / "test.admx").write_text("<x/>")
        assert detect_central_store(tmp_path) == pd

    def test_flat_layout(self, tmp_path: Path) -> None:
        (tmp_path / "test.admx").write_text("<x/>")
        assert detect_central_store(tmp_path) == tmp_path

    def test_domain_subdirectory(self, tmp_path: Path) -> None:
        domain = tmp_path / "ad.hraedon.com"
        pd = domain / "PolicyDefinitions"
        pd.mkdir(parents=True)
        (pd / "test.admx").write_text("<x/>")
        assert detect_central_store(tmp_path) == pd

    def test_no_admx_returns_none(self, tmp_path: Path) -> None:
        assert detect_central_store(tmp_path) is None


class TestDetectCollisions:
    def test_no_collisions_single_source(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("src1", "local", tmp_path)
        report = detect_collisions((result,))
        assert not report.has_issues

    def test_namespace_collision(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "src1"
        dir2 = tmp_path / "src2"
        dir1.mkdir()
        dir2.mkdir()
        _write_admx_pair(dir1, "a", "SharedNS", "PolicyA")
        _write_admx_pair(dir2, "b", "SharedNS", "PolicyB")
        r1 = ingest_source("src1", "local", dir1)
        r2 = ingest_source("src2", "vendor-pack", dir2)
        report = detect_collisions((r1, r2))
        assert len(report.namespace_collisions) == 1
        assert report.namespace_collisions[0].namespace == "Synthetic.SharedNS"
        assert set(report.namespace_collisions[0].sources) == {"src1", "src2"}

    def test_file_collision_same_hash(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "src1"
        dir2 = tmp_path / "src2"
        dir1.mkdir()
        dir2.mkdir()
        _write_admx_pair(dir1, "same", "NS1", "Policy1")
        _write_admx_pair(dir2, "same", "NS2", "Policy2")
        r1 = ingest_source("src1", "local", dir1)
        r2 = ingest_source("src2", "local", dir2)
        report = detect_collisions((r1, r2))
        file_cols = [c for c in report.file_collisions if "same.admx" in c.relative_path]
        assert len(file_cols) == 1

    def test_policy_drift(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "src1"
        dir2 = tmp_path / "src2"
        dir1.mkdir()
        dir2.mkdir()
        _write_admx_pair(dir1, "a", "SharedNS", "SharedPolicy")
        admx2 = _ADMX_TEMPLATE.format(ns="SharedNS", policy_name="SharedPolicy")
        admx2 = admx2.replace(
            'key="Software\\Policies\\Synthetic"',
            'key="Software\\Policies\\Different"',
        )
        (dir2 / "b.admx").write_text(admx2, encoding="utf-8")
        en_us = dir2 / "en-US"
        en_us.mkdir()
        (en_us / "b.adml").write_text(
            _ADML_TEMPLATE.format(policy_name="SharedPolicy"), encoding="utf-8"
        )
        r1 = ingest_source("src1", "local", dir1)
        r2 = ingest_source("src2", "vendor-pack", dir2)
        report = detect_collisions((r1, r2))
        assert len(report.policy_drift) == 1
        assert "SharedPolicy" in report.policy_drift[0].qualified_id

    def test_missing_adml_reported(self, tmp_path: Path) -> None:
        (tmp_path / "orphan.admx").write_text("<x/>")
        result = ingest_source("src", "local", tmp_path)
        report = detect_collisions((result,))
        assert len(report.missing_adml) == 1
        assert report.missing_adml[0].admx_path == "orphan.admx"


class TestTemplateLock:
    def test_build_and_validate_lock(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("src1", "local", tmp_path)
        lock = build_lock((result,))
        assert len(lock.source_hashes) == 1
        violations = validate_lock(lock, (result,))
        assert violations == []

    def test_lock_detects_changed_source(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("src1", "local", tmp_path)
        lock = build_lock((result,))
        (tmp_path / "new.admx").write_text("<x/>")
        (tmp_path / "en-US" / "new.adml").write_text("<x/>")
        result2 = ingest_source("src1", "local", tmp_path)
        violations = validate_lock(lock, (result2,))
        assert len(violations) == 1
        assert "changed" in violations[0]

    def test_lock_detects_removed_source(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("src1", "local", tmp_path)
        lock = build_lock((result,))
        violations = validate_lock(lock, ())
        assert len(violations) == 1
        assert "no longer available" in violations[0]

    def test_lock_detects_added_source(self, tmp_path: Path) -> None:
        _write_admx_pair(tmp_path, "test", "TestNS", "TestPolicy")
        result = ingest_source("src1", "local", tmp_path)
        lock = build_lock((result,))
        dir2 = tmp_path / "src2"
        dir2.mkdir()
        _write_admx_pair(dir2, "extra", "ExtraNS", "ExtraPolicy")
        result2 = ingest_source("src2", "vendor-pack", dir2)
        violations = validate_lock(lock, (result, result2))
        assert len(violations) == 1
        assert "added after lock" in violations[0]


class TestMergeCatalogues:
    def test_merge_multiple_sources(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "src1"
        dir2 = tmp_path / "src2"
        dir1.mkdir()
        dir2.mkdir()
        _write_admx_pair(dir1, "a", "NS1", "PolicyA")
        _write_admx_pair(dir2, "b", "NS2", "PolicyB")
        r1 = ingest_source("src1", "local", dir1)
        r2 = ingest_source("src2", "vendor-pack", dir2)
        merged = merge_catalogues((r1, r2))
        assert len(merged.policies) == 2
        qids = {p.qualified_id for p in merged.policies}
        assert "Synthetic.NS1:PolicyA" in qids
        assert "Synthetic.NS2:PolicyB" in qids
