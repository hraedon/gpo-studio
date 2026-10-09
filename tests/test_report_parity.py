"""Offline report parity: Studio's import of a backup against Windows' own report.

Every Windows-produced backup in the corpus carries the ``gpreport.xml`` that
``Backup-GPO`` wrote beside it. Each is imported through the public endpoint,
and Studio's typed model is inventoried and compared with that report. Every
divergence is either absent or one of the named ``KNOWN_DIVERGENCES``, and the
set of known divergences per backup is pinned below, so any change in what a
backup shows fails here until the pin and the report-parity lane are updated
together (WI-048). WI-072 and WI-073 were pinned this way until they were
fixed; their three backups now pin full equality, and their allowances are
gone, so a regression of either is an unexplained divergence.
"""

from __future__ import annotations

import base64
import json
import shutil
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import GPP_REGISTRY_REPORT_NAMESPACE, GppCollection, GppLocalGroup
from gpo_studio.gpp_adapters import (
    GppDrive,
    GppFile,
    GppFolder,
    GppPrinter,
    GppShortcut,
    serialize_gpp_drives,
    serialize_gpp_files,
    serialize_gpp_folders,
    serialize_gpp_printers,
    serialize_gpp_shortcuts,
)
from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.report import policy_report
from gpo_studio.report_parity import (
    GPP_REGISTRY_FAMILY,
    KNOWN_DIVERGENCES,
    MEASURED_REPORT_TYPES,
    OBSERVED_GPP_FAMILIES,
    Divergence,
    FamilyInventory,
    Inventory,
    InventoryItem,
    ReportParityError,
    _TypeTrackingBuilder,
    classify,
    compare,
    inventory_from_json,
    known_divergence,
    studio_gpo_from_backup,
    studio_inventory,
    windows_inventory,
)
from gpo_studio.store import WorkspaceStore, gpo_from_dict
from gpo_studio.xml_safety import parse_xml_bounded

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"
EVIDENCE = ROOT / "docs/plan-033"

#: The Windows-produced backup corpus of test_backup_report_inventory.py, with
#: the known divergences each one shows. An empty set means full equality.
EXPECTED_KNOWN: dict[str, frozenset[str]] = {
    "tests/fixtures/native-gpp-gpmc/WI01A-DriveMaps-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-EnvVars-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-Files-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-Folders-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-IniFiles-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-LocalGroups-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-MixedCSE-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-NestedILT-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-OS-ILT": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-Power-GPMC": frozenset(),  # WI-072 fixed
    "tests/fixtures/native-gpp-gpmc/WI01A-Printers-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-SchedTasks-GPMC": frozenset(),  # WI-073 fixed
    "tests/fixtures/native-gpp-gpmc/WI01A-SchedTasksFull-GPMC": frozenset(),  # WI-073 fixed
    "tests/fixtures/native-gpp-gpmc/WI01A-Services-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-ServicesRecovery-GPMC": frozenset(),
    "tests/fixtures/native-gpp-gpmc/WI01A-Shortcuts-GPMC": frozenset(),
    "tests/fixtures/native-gpp-registry-gpmc/WI01A-Registry-GPMC": frozenset(),
    "tests/fixtures/native-gpp-registry-gpmc/WI01A-RegistryMatrix-GPMC": frozenset(),
    "tests/fixtures/native-gpp-registry-gpmc/WI01A-RegistryShapes-GPMC": frozenset(),
    "docs/plan-033/wp0-evidence/wi059-20260908/wp0/backup": frozenset(),
    "docs/plan-033/wp1b-evidence/wi059-20260908/scripts-metadata/rebackup": frozenset(
        {"scripts-not-modeled"}
    ),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/drives-user/rebackup": frozenset(
        {"legacy-studio-drive-name"}
    ),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/groups-machine/rebackup": frozenset(),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/localusers-machine/rebackup": frozenset(),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/mixed-all/rebackup": frozenset(
        {"legacy-studio-drive-name"}
    ),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/registry-both/rebackup": frozenset(),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/scheduledtasks-machine/rebackup": (
        frozenset()
    ),
    "docs/plan-033/wp1b-evidence/wi059-20260908/wp1b/services-machine/rebackup": frozenset(),
    "docs/plan-033/wp2-evidence/wi059-20260908/wp2/rebackup": frozenset(),
    "docs/plan-033/wp1b-evidence/backup-report-20260908/scripts-metadata/rebackup": frozenset(
        {"scripts-not-modeled"}
    ),
}


def _corpus() -> list[Path]:
    return sorted(NATIVE.glob("*/manifest.xml")) + sorted(
        (ROOT / "tests/fixtures/native-gpp-registry-gpmc").glob("*/manifest.xml")
    ) + sorted(
        p for p in EVIDENCE.glob("*-evidence/wi059-20260908/**/manifest.xml")
        if "rebackup" in p.parts or "backup" in p.parts
    ) + [EVIDENCE / "wp1b-evidence/backup-report-20260908/scripts-metadata/rebackup/manifest.xml"]


def test_the_pinned_corpus_is_the_whole_windows_backup_corpus() -> None:
    """A backup added to the corpus without a pin would never be compared."""
    found = {m.parent.relative_to(ROOT).as_posix() for m in _corpus()}
    assert found == set(EXPECTED_KNOWN)


def _gpreport(case: Path) -> bytes:
    reports = list(case.glob("*/gpreport.xml"))
    assert len(reports) == 1, case
    return reports[0].read_bytes()


def _import_via_api(case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GPO:
    inbox = tmp_path / "inbox"
    shutil.copytree(case, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "parity.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app) as client:
            response = client.post("/api/backups/import", json={
                "path": str(inbox), "actor": "parity-test", "reason": "report parity",
            })
            assert response.status_code == 201, response.text
            return gpo_from_dict(response.json()["gpo"])


@pytest.mark.parametrize("relative", sorted(EXPECTED_KNOWN))
def test_studio_import_matches_the_windows_report_of_the_same_backup(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = ROOT / relative
    gpo = _import_via_api(case, tmp_path, monkeypatch)
    result = compare(windows_inventory(_gpreport(case)), studio_inventory(gpo))
    known, unexplained = classify(result)
    assert unexplained == (), [d.describe() for d in unexplained]
    assert set(known) == EXPECTED_KNOWN[relative]
    # Every family Windows reported was compared, and each has items: a
    # comparison of two empty inventories would pass for the wrong reason.
    assert result.families
    assert sum(f.windows_count for f in result.families) > 0


@pytest.mark.parametrize("relative", sorted(EXPECTED_KNOWN))
def test_the_lane_helper_imports_what_the_endpoint_imports(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane builds its expectation without a web process; it must agree."""
    case = ROOT / relative
    via_api = _import_via_api(case, tmp_path, monkeypatch)
    helper = studio_gpo_from_backup(case)
    # The endpoint's JSON round trip stringifies DWORD values; compare the
    # identity and rendering, which is what the inventory reads.
    def rows(gpo: GPO) -> list[tuple[str, ...]]:
        return [
            (s.side, s.hive, s.key, s.value_name, s.registry_type, str(s.value), s.action)
            for s in gpo.settings
        ]

    assert rows(helper) == rows(via_api)
    assert studio_inventory(helper) == studio_inventory(via_api)
    assert helper.backup_inventory is not None
    assert base64.b64decode(helper.backup_inventory.report_xml_base64) == _gpreport(case)


# ---------------------------------------------------------------------------
# Work items the corpus pinned (WI-048), now fixed: the pins are full equality
# ---------------------------------------------------------------------------


def test_wi072_the_retained_power_plan_is_written() -> None:
    gpo = studio_gpo_from_backup(NATIVE / "WI01A-Power-GPMC")
    collection = gpo.gpp_collections[0]
    assert collection.power_options == ()
    assert any("GlobalPowerOptionsV2" in c for c in collection.power_options_unknown_children)
    windows = windows_inventory(_gpreport(NATIVE / "WI01A-Power-GPMC"))
    ours = studio_inventory(gpo).family("user", "PowerOptionsSettings")
    assert [i.element for i in ours] == ["GlobalPowerOptionsV2"]
    assert ours == windows.family("user", "PowerOptionsSettings")


@pytest.mark.parametrize("case", ["WI01A-SchedTasks-GPMC", "WI01A-SchedTasksFull-GPMC"])
def test_wi073_scheduled_and_immediate_tasks_keep_their_interleaving(case: str) -> None:
    gpo = studio_gpo_from_backup(NATIVE / case)
    windows = windows_inventory(_gpreport(NATIVE / case))
    theirs = windows.family("computer", "ScheduledTasksSettings")
    assert [i.element for i in theirs][:3] == ["TaskV2", "ImmediateTaskV2", "TaskV2"]
    assert studio_inventory(gpo).family("computer", "ScheduledTasksSettings") == theirs


def test_no_known_divergence_names_a_fixed_work_item() -> None:
    names = {known.name for known in KNOWN_DIVERGENCES}
    assert not names & {"adapter-root-unknowns-dropped", "scheduled-task-order"}
    assert not {known.work_item for known in KNOWN_DIVERGENCES} & {"WI-072", "WI-073"}


def test_every_known_defect_names_a_registered_work_item() -> None:
    register = (ROOT / "docs/work-items.md").read_text(encoding="utf-8")
    for known in KNOWN_DIVERGENCES:
        if known.work_item is not None:
            assert f"## {known.work_item} " in register, known.name


# ---------------------------------------------------------------------------
# Writer names: GPME's derivation, measured against GPMC-authored captures
# ---------------------------------------------------------------------------


def _names(xml: bytes) -> list[str]:
    import xml.etree.ElementTree as ET

    return [item.get("name", "") for item in ET.fromstring(xml)]


def test_preference_item_names_follow_gpme() -> None:
    cases: list[tuple[bytes, list[str]]] = [
        (serialize_gpp_drives((GppDrive(letter="M", path=r"\\s\h"),), "user"), ["M:"]),
        (serialize_gpp_drives((GppDrive(letter="", path=r"\\s\h"),), "user"), [r"\\s\h"]),
        (serialize_gpp_files((GppFile(target=r"%TEMP%\a.xml"),), "user"), ["a.xml"]),
        (serialize_gpp_files((GppFile(target="%APPDATA%\\App\\"),), "user"), [""]),
        (serialize_gpp_folders((GppFolder(path=r"%USERPROFILE%\P"),), "user"), ["P"]),
        (serialize_gpp_printers((GppPrinter(path=r"\\ps\Lab"),), "user"), ["Lab"]),
        (serialize_gpp_shortcuts((GppShortcut(shortcut_path=r"%D%\T\Mgr"),), "user"), ["Mgr"]),
        (serialize_gpp_shortcuts((GppShortcut(name="Own"),), "user"), ["Own"]),
    ]
    for xml, expected in cases:
        assert _names(xml) == expected


# ---------------------------------------------------------------------------
# The differ's primitives: each divergence kind must be shown to fire
# ---------------------------------------------------------------------------


def _inv(*items: InventoryItem, family: str = "DriveMapSettings") -> Inventory:
    return Inventory(families=(FamilyInventory("user", family, items),))


_A = InventoryItem("Drive", name="A:", uid="{1}", action="U")
_B = InventoryItem("Drive", name="B:", uid="{2}", action="C")


def test_equal_inventories_compare_equal() -> None:
    result = compare(_inv(_A, _B), _inv(_A, _B))
    assert result.equal and result.divergences == ()
    assert result.families[0].windows_count == 2


def test_missing_and_extra_items_are_named() -> None:
    result = compare(_inv(_A, _B), _inv(_A, replace(_B, action="R")))
    kinds = [(d.kind, d.item) for d in result.divergences]
    assert kinds == [
        ("missing_in_studio", _B), ("extra_in_studio", replace(_B, action="R")),
    ]
    assert not result.equal


def test_duplicates_are_counted_not_collapsed() -> None:
    result = compare(_inv(_A, _A), _inv(_A))
    assert [d.kind for d in result.divergences] == ["missing_in_studio"]


def test_order_is_reported_only_when_the_items_agree() -> None:
    result = compare(_inv(_A, _B), _inv(_B, _A))
    assert [d.kind for d in result.divergences] == ["order"]
    assert "windows" in result.divergences[0].describe()


def test_a_family_on_one_side_only_is_wholly_missing() -> None:
    result = compare(_inv(_A), Inventory(families=()))
    assert [d.kind for d in result.divergences] == ["missing_in_studio"]


def test_admx_rendering_is_its_own_named_divergence() -> None:
    reg = InventoryItem("RegistrySetting", key="K", name="N", value="String:v")
    windows = Inventory(
        families=(FamilyInventory("computer", "RegistrySettings", (reg,)),),
        admx_policies=(("computer", 2),),
    )
    studio = Inventory(families=(FamilyInventory("computer", "RegistrySettings", (reg,)),))
    known, unexplained = classify(compare(windows, studio))
    assert unexplained == ()
    assert set(known) == {"admx-policy-rendering"}


def _report(computer: str = "", user: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GPO xmlns="http://www.microsoft.com/GroupPolicy/Settings" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<Computer>{computer}</Computer><User>{user}</User></GPO>"
    ).encode()


def _registry_ext(body: str) -> str:
    return (
        "<ExtensionData><Extension "
        'xmlns:q1="http://www.microsoft.com/GroupPolicy/Settings/Registry" '
        f'xsi:type="q1:RegistrySettings">{body}</Extension><Name>Registry</Name></ExtensionData>'
    )


def test_windows_registry_entries_carry_key_name_and_rendered_value() -> None:
    # Windows qualifies every child with the policy-registry namespace (q1).
    body = (
        "<q1:RegistrySetting><q1:KeyPath>Software\\X</q1:KeyPath>"
        "<q1:AdmSetting>false</q1:AdmSetting>"
        "<q1:Value><q1:Name>V</q1:Name><q1:Number>7</q1:Number></q1:Value>"
        "</q1:RegistrySetting>"
        "<q1:RegistrySetting><q1:KeyPath>Software\\Y</q1:KeyPath></q1:RegistrySetting>"
        "<q1:Policy><q1:Name>Some ADMX policy</q1:Name></q1:Policy>"
        "<q1:Blocked>false</q1:Blocked>"
    )
    inventory = windows_inventory(_report(computer=_registry_ext(body)))
    assert inventory.family("computer", "RegistrySettings") == (
        InventoryItem("RegistrySetting", key="Software\\X", name="V", value="Number:7"),
        InventoryItem("RegistrySetting", key="Software\\Y"),
    )
    assert inventory.admx_policies == (("computer", 1),)


def test_windows_inventory_refuses_what_is_not_a_report() -> None:
    with pytest.raises(ReportParityError, match="not a GPMC settings report"):
        windows_inventory(b"<Other/>")
    with pytest.raises(ReportParityError, match="xsi:type"):
        windows_inventory(_report(user="<ExtensionData><Extension/></ExtensionData>"))


def test_a_report_without_settings_has_an_empty_inventory() -> None:
    assert windows_inventory(_report()).families == ()
    assert windows_inventory(_report(user="<ExtensionData/>")).families == ()


def _setting(**changes: object) -> RegistrySetting:
    base = RegistrySetting(
        id="r", side="computer", hive="HKLM", key="Software\\K", value_name="N",
        registry_type="REG_SZ", value="v",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "setting,expected_name,expected_value",
    [
        (_setting(), "N", "String:v"),
        (_setting(registry_type="REG_DWORD", value=5), "N", "Number:5"),
        (_setting(registry_type="REG_MULTI_SZ", value=["a", "b"]), "N", "REG_MULTI_SZ:a; b"),
        (_setting(action="delete"), "**del.N", ""),
        (_setting(action="delete_all_values", value_name=""), "**delvals.", ""),
    ],
)
def test_studio_registry_renderings(
    setting: RegistrySetting, expected_name: str, expected_value: str
) -> None:
    gpo = GPO(guid="00000000-0000-0000-0000-000000000001", name="t", settings=(setting,))
    (item,) = studio_inventory(gpo).family("computer", "RegistrySettings")
    assert (item.name, item.value) == (expected_name, expected_value)


def test_an_unobserved_family_is_never_given_a_guessed_report_name() -> None:
    from gpo_studio.gpp_adapters import GppDataSource

    gpo = GPO(
        guid="00000000-0000-0000-0000-000000000002", name="t",
        gpp_collections=(GppCollection(scope="user", data_sources=(GppDataSource(dsn="D"),)),),
    )
    (family,) = studio_inventory(gpo).families
    assert family.family == "unobserved:DataSources/DataSources.xml"
    assert "DataSources/DataSources.xml" not in OBSERVED_GPP_FAMILIES


def test_a_model_the_writer_refuses_is_reported_not_crashed() -> None:
    gpo = GPO(
        guid="00000000-0000-0000-0000-000000000003", name="t",
        gpp_collections=(
            GppCollection(scope="computer", local_groups=(GppLocalGroup(group_name="G"),)),
        ),
    )
    with pytest.raises(ReportParityError, match="cannot be rendered"):
        studio_inventory(gpo)
    assert "(inventory unavailable:" in policy_report(gpo)


def test_the_text_report_lists_the_same_inventory() -> None:
    gpo = studio_gpo_from_backup(NATIVE / "WI01A-DriveMaps-GPMC")
    text = policy_report(gpo)
    assert "Settings inventory (Windows report families)" in text
    assert "user/DriveMapSettings: 4 item(s)" in text
    for item in studio_inventory(gpo).family("user", "DriveMapSettings"):
        assert item.label() in text


def test_inventory_json_round_trips() -> None:
    gpo = studio_gpo_from_backup(
        EVIDENCE / "wp1b-evidence/wi059-20260908/wp1b/mixed-all/rebackup"
    )
    inventory = studio_inventory(gpo)
    rebuilt = inventory_from_json(json.loads(json.dumps(inventory.to_json())))
    assert rebuilt == inventory
    admx = Inventory(families=(), admx_policies=(("user", 3),))
    assert inventory_from_json(admx.to_json()) == admx


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"families": {}},
        {"families": [{"side": "computer", "family": "F"}]},
        {"families": [{"side": "nowhere", "family": "F", "items": []}]},
        {"families": [{"side": "user", "family": 3, "items": []}]},
        {"families": [{"side": "user", "family": "F", "items": [{"element": "E"}]}]},
        {"families": [], "admx_policies": {"user": "3"}},
    ],
)
def test_malformed_inventory_json_is_refused(data: object) -> None:
    with pytest.raises(ReportParityError):
        inventory_from_json(data)


# ---------------------------------------------------------------------------
# Known-divergence matchers are narrow: a new defect must stay unexplained
# ---------------------------------------------------------------------------


_T1 = InventoryItem("TaskV2", name="t1", action="U")
_T2 = InventoryItem("TaskV2", name="t2", action="R")
_I1 = InventoryItem("ImmediateTaskV2", name="i1", action="C")


def _order(windows: tuple[InventoryItem, ...], studio: tuple[InventoryItem, ...],
           family: str = "ScheduledTasksSettings") -> Divergence:
    return Divergence(
        "computer", family, "order", windows_order=windows, studio_order=studio,
    )


@pytest.mark.parametrize(
    "studio_order",
    [
        (_T1, _T2, _I1),  # the WI-073 regression: written grouped
        (_T2, _T1, _I1),  # reordered within a task type
        (_I1, _T1, _T2),  # immediate tasks moved first
    ],
)
def test_any_task_reordering_is_unexplained(studio_order: tuple[InventoryItem, ...]) -> None:
    """WI-073 is fixed: no task order is known any more, the partition included."""
    windows = _inv(_T1, _I1, _T2, family="ScheduledTasksSettings")
    studio = _inv(*studio_order, family="ScheduledTasksSettings")
    known, unexplained = classify(compare(windows, studio))
    assert known == {} and [d.kind for d in unexplained] == ["order"]
    assert _order((_T1, _I1, _T2), studio_order).windows_order == (_T1, _I1, _T2)


def test_registry_data_is_not_stripped() -> None:
    """Review finding 3: padding in REG_SZ data is data."""
    body = (
        "<q1:RegistrySetting><q1:KeyPath>Software\\X</q1:KeyPath>"
        "<q1:Value><q1:Name>V</q1:Name><q1:String>  padded  </q1:String></q1:Value>"
        "</q1:RegistrySetting>"
        "<q1:RegistrySetting><q1:KeyPath>Software\\X</q1:KeyPath>"
        "<q1:Value><q1:Name>E</q1:Name><q1:String></q1:String></q1:Value>"
        "</q1:RegistrySetting>"
    )
    items = windows_inventory(_report(computer=_registry_ext(body))).family(
        "computer", "RegistrySettings"
    )
    assert [i.value for i in items] == ["String:  padded  ", "String:"]


def test_report_identity_reads_guid_domain_and_name() -> None:
    from gpo_studio.report_parity import report_identity

    report = (
        NATIVE / "WI01A-DriveMaps-GPMC/{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}/gpreport.xml"
    ).read_bytes()
    identity = report_identity(report)
    assert identity.guid == "f0197e25-3e19-4835-b296-3c35dc069635"
    assert identity.name == "WI01A-DriveMaps-GPMC"
    assert identity.domain == "ad.hraedon.com"
    with pytest.raises(ReportParityError, match="no GPO identifier"):
        report_identity(_report())
    with pytest.raises(ReportParityError, match="not a GPMC settings report"):
        report_identity(b"<Other/>")


def test_a_dropped_power_plan_is_unexplained() -> None:
    """WI-072 is fixed: a missing GlobalPowerOptionsV2 is no longer known."""
    plan = InventoryItem("GlobalPowerOptionsV2", name="p")
    windows = _inv(plan, family="PowerOptionsSettings")
    known, unexplained = classify(compare(windows, Inventory(families=())))
    assert known == {}
    assert [(d.kind, d.item) for d in unexplained] == [("missing_in_studio", plan)]


def _drive(name: str, **fields: str) -> InventoryItem:
    return InventoryItem("Drive", name=name, action=fields.get("action", "U"))


def test_the_legacy_drive_name_matches_only_a_complete_pair() -> None:
    legacy = known_divergence("legacy-studio-drive-name")
    windows_p = Divergence("user", "DriveMapSettings", "missing_in_studio", _drive("P"))
    studio_p = Divergence("user", "DriveMapSettings", "extra_in_studio", _drive("P:"))
    pair = (windows_p, studio_p)
    assert legacy.matches(windows_p, pair) and legacy.matches(studio_p, pair)
    # Either half alone is a real divergence: a drive one side lacks.
    assert not legacy.matches(windows_p, (windows_p,))
    assert not legacy.matches(studio_p, (studio_p,))
    # The halves must be the same item apart from the colon.
    other = Divergence("user", "DriveMapSettings", "extra_in_studio", _drive("P:", action="R"))
    assert not legacy.matches(windows_p, (windows_p, other))
    letter_q = Divergence("user", "DriveMapSettings", "extra_in_studio", _drive("Q:"))
    assert not legacy.matches(windows_p, (windows_p, letter_q))
    # Direction matters: Studio's bare letter is not the legacy shape.
    reversed_pair = (
        Divergence("user", "DriveMapSettings", "missing_in_studio", _drive("P:")),
        Divergence("user", "DriveMapSettings", "extra_in_studio", _drive("P")),
    )
    assert not any(legacy.matches(d, reversed_pair) for d in reversed_pair)
    # Other families, elements, kinds and names never match.
    unc = Divergence("user", "DriveMapSettings", "extra_in_studio", _drive("\\\\s\\h"))
    assert not legacy.matches(unc, (unc,))
    file_p = Divergence("user", "DriveMapSettings", "missing_in_studio",
                        InventoryItem("File", name="P"))
    assert not legacy.matches(file_p, (file_p,))
    files = Divergence("user", "FilesSettings", "missing_in_studio", _drive("P"))
    assert not legacy.matches(files, (files,))
    assert not legacy.matches(Divergence("user", "DriveMapSettings", "order"), pair)


def test_a_lone_extra_drive_with_a_colon_stays_unexplained() -> None:
    """The broad matcher this replaced absorbed exactly this case."""
    result = compare(Inventory(families=()), _inv(_drive("M:")))
    known, unexplained = classify(result)
    assert known == {} and len(unexplained) == 1


def test_an_unknown_divergence_stays_unexplained() -> None:
    result = compare(_inv(_A, family="FilesSettings"), Inventory(families=()))
    known, unexplained = classify(result)
    assert known == {} and len(unexplained) == 1



# ---------------------------------------------------------------------------
# Batch-2 review: report types are classified by full QName
# ---------------------------------------------------------------------------

_GPP_NS = "http://www.microsoft.com/GroupPolicy/Settings/Windows/Registry"
_POLICY_NS = "http://www.microsoft.com/GroupPolicy/Settings/Registry"
_GPP_ITEM = (
    '<g:RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
    '<g:Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}" name="V" '
    'uid="{80BA2F39-55EC-40E1-A554-A6096468D60E}"><g:Properties action="C"/></g:Registry>'
    "</g:RegistrySettings>"
)


def _extension(type_ns: str, body: str, extra_ns: str = "") -> str:
    return (
        f'<ExtensionData><Extension xmlns:t="{type_ns}" xmlns:g="{_GPP_NS}" '
        f'xmlns:q1="{_POLICY_NS}" {extra_ns}xsi:type="t:RegistrySettings">{body}'
        "</Extension><Name>Registry</Name></ExtensionData>"
    )


def test_a_gpp_registry_type_is_filed_as_gpp_registry() -> None:
    inventory = windows_inventory(_report(computer=_extension(_GPP_NS, _GPP_ITEM)))
    assert [f.family for f in inventory.families] == [GPP_REGISTRY_FAMILY]


def test_an_unmeasured_type_namespace_is_not_filed_as_a_measured_family() -> None:
    """The reviewer's mutation: same local name and child, type QName in urn:unmeasured."""
    inventory = windows_inventory(_report(computer=_extension("urn:unmeasured", _GPP_ITEM)))
    families = [f.family for f in inventory.families]
    assert GPP_REGISTRY_FAMILY not in families
    assert families == ["unmeasured:{urn:unmeasured}RegistrySettings"]


def test_an_undeclared_type_prefix_is_unmeasured() -> None:
    report = _report(computer=(
        '<ExtensionData><Extension xsi:type="nope:DriveMapSettings"/>'
        "<Name>x</Name></ExtensionData>"
    ))
    assert [f.family for f in windows_inventory(report).families] == [
        "unmeasured:{}DriveMapSettings"
    ]


def test_one_extension_holding_both_registry_families_is_split_by_element() -> None:
    """Policy settings and a GPP Registry container are filed separately."""
    body = (
        "<q1:RegistrySetting><q1:KeyPath>Software\\X</q1:KeyPath>"
        "<q1:Value><q1:Name>V</q1:Name><q1:Number>7</q1:Number></q1:Value>"
        "</q1:RegistrySetting>" + _GPP_ITEM
    )
    for type_ns in (_POLICY_NS, _GPP_NS):
        inventory = windows_inventory(_report(computer=_extension(type_ns, body)))
        assert inventory.family("computer", "RegistrySettings") == (
            InventoryItem("RegistrySetting", key="Software\\X", name="V", value="Number:7"),
        )
        assert [i.name for i in inventory.family("computer", GPP_REGISTRY_FAMILY)] == ["V"]


def _report_type_qnames() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    reports = [
        path for path in ROOT.rglob("*.xml")
        if path.name in ("gpreport.xml", "gpreport-verify.xml", "gpreport-after-import.xml")
        or path.parent.name == "reports"
    ]
    for path in reports:
        data = path.read_bytes()
        try:
            builder = _TypeTrackingBuilder(error_class=ReportParityError)
            parse_xml_bounded(
                data, max_size=64 * 1024 * 1024, error_class=ReportParityError, builder=builder
            )
        except ReportParityError:
            continue
        found |= {
            qname for qname in builder.type_qnames.values()
            if qname[0].startswith("http://www.microsoft.com/GroupPolicy/Settings/")
        }
    return found


def test_the_measured_type_table_is_exactly_what_windows_reports_declared() -> None:
    """`MEASURED_REPORT_TYPES` is read off the repository's Windows reports."""
    assert set(MEASURED_REPORT_TYPES) == _report_type_qnames()


def test_the_gpp_registry_namespace_has_one_source() -> None:
    from gpo_studio import writer_conformance

    assert GPP_REGISTRY_REPORT_NAMESPACE == _GPP_NS
    assert (GPP_REGISTRY_REPORT_NAMESPACE, "RegistrySettings") in MEASURED_REPORT_TYPES
    assert writer_conformance._REPORT_ROOT_NAMESPACE["RegistrySettings"] is (
        GPP_REGISTRY_REPORT_NAMESPACE
    )



# ---------------------------------------------------------------------------
# Batch-2 re-review: a measured declaration must match its children's shape
# ---------------------------------------------------------------------------

_BANKED_POLICY_REPORT = (
    EVIDENCE / "wp1b-evidence/plan034-20261008/wp1b/registry-both/gpreport-after-import.xml"
)
_NATIVE_GPP_REPORT = (
    ROOT / "tests/fixtures/native-gpp-registry-gpmc/WI01A-Registry-GPMC/gpreport-verify.xml"
)


def _retype(report: bytes, old_prefix: str, new_namespace: str) -> bytes:
    """Change only the Extension's declared xsi:type namespace, as the reviewer did."""
    text = report.decode("utf-16") if report[:2] == b"\xff\xfe" else report.decode("utf-8-sig")
    old = f'xsi:type="{old_prefix}:RegistrySettings"'
    assert old in text
    text = text.replace(
        old, f'xmlns:bad="{new_namespace}" xsi:type="bad:RegistrySettings"'
    )
    return text.encode("utf-16")


@pytest.mark.parametrize(
    ("report", "new_namespace"),
    [
        (_BANKED_POLICY_REPORT, _GPP_NS),
        (_NATIVE_GPP_REPORT, _POLICY_NS),
    ],
    ids=["policy-declared-as-gpp", "gpp-declared-as-policy"],
)
def test_a_declaration_for_the_other_measured_family_does_not_pass(
    report: Path, new_namespace: str
) -> None:
    original = report.read_bytes()
    prefix = "q1"
    mutated = _retype(original, prefix, new_namespace)
    result = compare(windows_inventory(original), windows_inventory(mutated))
    assert not result.equal
    families = {d.family for d in result.divergences}
    assert any(f.startswith("unmeasured:declared ") for f in families), families


def test_a_gpp_registry_item_without_its_measured_shape_is_unmeasured() -> None:
    body = (
        '<g:RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
        '<g:Registry name="V"/>'
        "</g:RegistrySettings>"
    )
    inventory = windows_inventory(_report(computer=_extension(_GPP_NS, body)))
    assert inventory.family("computer", GPP_REGISTRY_FAMILY) == ()
    assert f"unmeasured:{{{_GPP_NS}}}Registry" in [f.family for f in inventory.families]
