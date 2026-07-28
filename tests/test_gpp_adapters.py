"""Tests for low-artifact GPP adapters (Plan 024 WP-2 and WP-3)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from gpo_studio.gpp import (
    GppCollection,
    GppCommonOptions,
    GppError,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import (
    GppApplication,
    GppDataSource,
    GppDevice,
    GppDrive,
    GppEnvironment,
    GppFile,
    GppFolder,
    GppFolderOptions,
    GppImmediateTask,
    GppIniFile,
    GppLocalGroup,
    GppLocalGroupMember,
    GppLocalUser,
    GppNetworkShare,
    GppPowerOptions,
    GppPrinter,
    GppRegionalOptions,
    GppScheduledTask,
    GppService,
    GppShortcut,
    parse_gpp_applications,
    parse_gpp_data_sources,
    parse_gpp_devices,
    parse_gpp_drives,
    parse_gpp_environment,
    parse_gpp_files,
    parse_gpp_folder_options,
    parse_gpp_folders,
    parse_gpp_immediate_tasks,
    parse_gpp_ini_files,
    parse_gpp_local_groups,
    parse_gpp_local_users,
    parse_gpp_network_shares,
    parse_gpp_power_options,
    parse_gpp_printers,
    parse_gpp_regional_options,
    parse_gpp_scheduled_tasks,
    parse_gpp_services,
    parse_gpp_shortcuts,
    serialize_gpp_applications,
    serialize_gpp_data_sources,
    serialize_gpp_devices,
    serialize_gpp_drives,
    serialize_gpp_environment,
    serialize_gpp_files,
    serialize_gpp_folder_options,
    serialize_gpp_folders,
    serialize_gpp_immediate_tasks,
    serialize_gpp_ini_files,
    serialize_gpp_local_groups,
    serialize_gpp_local_users,
    serialize_gpp_network_shares,
    serialize_gpp_power_options,
    serialize_gpp_printers,
    serialize_gpp_regional_options,
    serialize_gpp_scheduled_tasks,
    serialize_gpp_services,
    serialize_gpp_shortcuts,
)
from gpo_studio.ilt import IltFilter, IltPredicate


def _all_true_common() -> GppCommonOptions:
    return GppCommonOptions(
        apply_once=True,
        remove_when_unapplied=True,
        user_security_context=True,
        disabled=True,
        stop_on_error=True,
    )


def _sample_ilt() -> IltFilter:
    return IltFilter(
        items=(
            IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
            IltPredicate(type="group", value="S-1-5-32-544", negate=True),
        )
    )


# ---------------------------------------------------------------------------
# Environment Variables
# ---------------------------------------------------------------------------


def test_environment_roundtrip() -> None:
    env = GppEnvironment(name="GAMEDRIVE", value="Z:", user=True, action="replace")
    data = serialize_gpp_environment((env,), "computer")
    parsed = parse_gpp_environment(data)
    assert len(parsed) == 1
    assert parsed[0] == env


def test_environment_common_options() -> None:
    env = GppEnvironment(name="PATH", value="/usr/bin", common=_all_true_common())
    data = serialize_gpp_environment((env,), "user")
    assert b"<FilterRunOnce " in data
    assert b"applyOnce=" not in data
    assert b'removePolicy="1"' in data
    assert b'userContext="1"' in data
    assert b'disabled="1"' in data
    assert b'bypassErrors="0"' in data
    parsed = parse_gpp_environment(data)
    assert parsed[0].common == _all_true_common()


def test_common_options_are_on_item_not_properties() -> None:
    env = GppEnvironment(name="PATH", value="bin", common=_all_true_common())
    root = ET.fromstring(serialize_gpp_environment((env,), "user"))
    item = root.find("EnvironmentVariable")
    assert item is not None
    props = item.find("Properties")
    assert props is not None
    for name in ("removePolicy", "userContext", "disabled", "bypassErrors"):
        assert name in item.attrib
        assert name not in props.attrib
    assert "applyOnce" not in item.attrib
    assert item.find("Filters/FilterRunOnce") is not None


def test_environment_ilt_filter() -> None:
    env = GppEnvironment(name="VAR", value="val", ilt_filter=_sample_ilt())
    data = serialize_gpp_environment((env,), "computer")
    parsed = parse_gpp_environment(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_environment_unknown_attrs_preserved() -> None:
    env = GppEnvironment(
        name="VAR",
        unknown_attrs=(("image", "2"), ("changed", "2024-01-01")),
    )
    data = serialize_gpp_environment((env,), "computer")
    parsed = parse_gpp_environment(data)
    assert parsed[0].unknown_attrs == env.unknown_attrs


def test_environment_uses_correct_clsid() -> None:
    env = GppEnvironment(name="X")
    data = serialize_gpp_environment((env,), "computer")
    assert b'clsid="{BF141A63-327B-438a-B9BF-2C188F13B7AD}"' in data
    assert b'clsid="{78570023-8373-4a19-BA80-2F150738EA19}"' in data


# ---------------------------------------------------------------------------
# INI Files
# ---------------------------------------------------------------------------


def test_ini_file_roundtrip() -> None:
    ini = GppIniFile(
        path=r"C:\Temp\test.ini",
        section="Settings",
        property="Key",
        value="Value",
        action="add",
    )
    data = serialize_gpp_ini_files((ini,), "computer")
    parsed = parse_gpp_ini_files(data)
    assert len(parsed) == 1
    assert parsed[0] == ini


def test_ini_file_common_options() -> None:
    ini = GppIniFile(path="/tmp/x.ini", common=_all_true_common())
    data = serialize_gpp_ini_files((ini,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_ini_files(data)
    assert parsed[0].common == _all_true_common()


def test_ini_file_ilt_filter() -> None:
    ini = GppIniFile(path="/tmp/x.ini", ilt_filter=_sample_ilt())
    data = serialize_gpp_ini_files((ini,), "computer")
    parsed = parse_gpp_ini_files(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_ini_file_unknown_attrs_preserved() -> None:
    ini = GppIniFile(
        path="/tmp/x.ini",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_ini_files((ini,), "computer")
    parsed = parse_gpp_ini_files(data)
    assert parsed[0].unknown_attrs == ini.unknown_attrs


# ---------------------------------------------------------------------------
# Regional Options
# ---------------------------------------------------------------------------


def test_regional_options_roundtrip() -> None:
    reg = GppRegionalOptions(
        user_locale="en-US",
        user_ime="0409:00000409",
        user_number="s1,1",
        user_currency="$",
        user_time="HH:mm:ss",
        user_date="yyyy-MM-dd",
        user_timezone="Pacific Standard Time",
    )
    data = serialize_gpp_regional_options((reg,), "computer")
    parsed = parse_gpp_regional_options(data)
    assert len(parsed) == 1
    assert parsed[0] == reg


def test_regional_options_common_options() -> None:
    reg = GppRegionalOptions(user_locale="en-GB", common=_all_true_common())
    data = serialize_gpp_regional_options((reg,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_regional_options(data)
    assert parsed[0].common == _all_true_common()


def test_regional_options_ilt_filter() -> None:
    reg = GppRegionalOptions(user_locale="en-US", ilt_filter=_sample_ilt())
    data = serialize_gpp_regional_options((reg,), "computer")
    parsed = parse_gpp_regional_options(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_regional_options_unknown_attrs_preserved() -> None:
    reg = GppRegionalOptions(
        user_locale="en-US",
        unknown_attrs=(("changed", "2024-01-01"),),
    )
    data = serialize_gpp_regional_options((reg,), "computer")
    parsed = parse_gpp_regional_options(data)
    assert parsed[0].unknown_attrs == reg.unknown_attrs


# ---------------------------------------------------------------------------
# Power Options
# ---------------------------------------------------------------------------


def test_power_options_roundtrip() -> None:
    pwr = GppPowerOptions(
        scheme_name="Balanced",
        scheme_guid="{381b4222-f694-41f0-9685-ff5bb260df2e}",
        ac_power_setting="{381b4222-f694-41f0-9685-ff5bb260df2e}",
        dc_power_setting="{381b4222-f694-41f0-9685-ff5bb260df2e}",
    )
    data = serialize_gpp_power_options((pwr,), "computer")
    parsed = parse_gpp_power_options(data)
    assert len(parsed) == 1
    assert parsed[0] == pwr


def test_power_options_common_options() -> None:
    pwr = GppPowerOptions(scheme_name="High Performance", common=_all_true_common())
    data = serialize_gpp_power_options((pwr,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_power_options(data)
    assert parsed[0].common == _all_true_common()


def test_power_options_ilt_filter() -> None:
    pwr = GppPowerOptions(scheme_name="Balanced", ilt_filter=_sample_ilt())
    data = serialize_gpp_power_options((pwr,), "computer")
    parsed = parse_gpp_power_options(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_power_options_unknown_attrs_preserved() -> None:
    pwr = GppPowerOptions(
        scheme_name="Balanced",
        unknown_attrs=(("image", "1"),),
    )
    data = serialize_gpp_power_options((pwr,), "computer")
    parsed = parse_gpp_power_options(data)
    assert parsed[0].unknown_attrs == pwr.unknown_attrs


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def test_device_roundtrip() -> None:
    dev = GppDevice(
        device_class="{4d36e967-e325-11ce-bfc1-08002be10318}",
        device_name="Disk drive",
        device_action="disable",
        action="add",
    )
    data = serialize_gpp_devices((dev,), "computer")
    parsed = parse_gpp_devices(data)
    assert len(parsed) == 1
    assert parsed[0] == dev


def test_device_common_options() -> None:
    dev = GppDevice(device_name="Disk", common=_all_true_common())
    data = serialize_gpp_devices((dev,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_devices(data)
    assert parsed[0].common == _all_true_common()


def test_device_ilt_filter() -> None:
    dev = GppDevice(device_name="Disk", ilt_filter=_sample_ilt())
    data = serialize_gpp_devices((dev,), "computer")
    parsed = parse_gpp_devices(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_device_unknown_attrs_preserved() -> None:
    dev = GppDevice(
        device_name="Disk",
        unknown_attrs=(("image", "3"),),
    )
    data = serialize_gpp_devices((dev,), "computer")
    parsed = parse_gpp_devices(data)
    assert parsed[0].unknown_attrs == dev.unknown_attrs


def test_device_action_codes() -> None:
    dev = GppDevice(device_name="NIC", device_action="enable")
    data = serialize_gpp_devices((dev,), "computer")
    assert b'deviceAction="ENABLE"' in data
    dev2 = GppDevice(device_name="NIC", device_action="disable")
    data2 = serialize_gpp_devices((dev2,), "computer")
    assert b'deviceAction="DISABLE"' in data2


# ---------------------------------------------------------------------------
# Folder Options
# ---------------------------------------------------------------------------


def test_folder_options_roundtrip() -> None:
    fo = GppFolderOptions(
        show_hidden=True,
        show_extensions=False,
        show_super_hidden=True,
        show_full_path=True,
        launch_in_separate=True,
    )
    data = serialize_gpp_folder_options((fo,), "computer")
    parsed = parse_gpp_folder_options(data)
    assert len(parsed) == 1
    assert parsed[0] == fo


def test_folder_options_common_options() -> None:
    fo = GppFolderOptions(show_hidden=True, common=_all_true_common())
    data = serialize_gpp_folder_options((fo,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_folder_options(data)
    assert parsed[0].common == _all_true_common()


def test_folder_options_ilt_filter() -> None:
    fo = GppFolderOptions(show_hidden=True, ilt_filter=_sample_ilt())
    data = serialize_gpp_folder_options((fo,), "computer")
    parsed = parse_gpp_folder_options(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_folder_options_unknown_attrs_preserved() -> None:
    fo = GppFolderOptions(
        show_hidden=True,
        unknown_attrs=(("changed", "2024-01-01"),),
    )
    data = serialize_gpp_folder_options((fo,), "computer")
    parsed = parse_gpp_folder_options(data)
    assert parsed[0].unknown_attrs == fo.unknown_attrs


def test_folder_options_default_show_extensions() -> None:
    fo = GppFolderOptions()
    data = serialize_gpp_folder_options((fo,), "computer")
    assert b'showExtensions="1"' in data
    assert b'showHidden="0"' in data


# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------


def test_data_source_roundtrip() -> None:
    ds = GppDataSource(
        dsn="MyDSN",
        driver="SQL Server",
        description="Test DSN",
        attributes="DATABASE=TestDB",
        user_dsn=True,
        action="replace",
    )
    data = serialize_gpp_data_sources((ds,), "computer")
    parsed = parse_gpp_data_sources(data)
    assert len(parsed) == 1
    assert parsed[0] == ds


def test_data_source_common_options() -> None:
    ds = GppDataSource(dsn="DSN1", common=_all_true_common())
    data = serialize_gpp_data_sources((ds,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_data_sources(data)
    assert parsed[0].common == _all_true_common()


def test_data_source_ilt_filter() -> None:
    ds = GppDataSource(dsn="DSN1", ilt_filter=_sample_ilt())
    data = serialize_gpp_data_sources((ds,), "computer")
    parsed = parse_gpp_data_sources(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_data_source_unknown_attrs_preserved() -> None:
    ds = GppDataSource(
        dsn="DSN1",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_data_sources((ds,), "computer")
    parsed = parse_gpp_data_sources(data)
    assert parsed[0].unknown_attrs == ds.unknown_attrs


# ---------------------------------------------------------------------------
# Collection integration
# ---------------------------------------------------------------------------


def test_gpp_collection_with_all_adapters() -> None:
    """Collection with all adapter types round-trips through serialize_gpp."""
    col = GppCollection(
        scope="computer",
        environment=(GppEnvironment(name="X", value="Y"),),
        ini_files=(GppIniFile(path="/tmp/x.ini", section="S", property="P", value="V"),),
        regional_options=(GppRegionalOptions(user_locale="en-US"),),
        power_options=(GppPowerOptions(scheme_name="Balanced"),),
        devices=(GppDevice(device_name="Disk"),),
        folder_options=(GppFolderOptions(show_hidden=True),),
        data_sources=(GppDataSource(dsn="DSN1", driver="SQL Server"),),
        drives=(GppDrive(letter="Z:", path=r"\\server\share"),),
        files=(GppFile(source=r"\\server\a.txt", target=r"C:\Temp\a.txt"),),
        folders=(GppFolder(path=r"C:\Temp\Folder"),),
        network_shares=(GppNetworkShare(name="Share", path=r"C:\Shared"),),
        printers=(GppPrinter(path=r"\\server\printer", action_type="update"),),
        shortcuts=(GppShortcut(name="Note", target_path=r"C:\Windows\notepad.exe"),),
        applications=(GppApplication(name="App", path=r"\\server\app.exe"),),
    )
    files = serialize_gpp(col)
    assert "EnvironmentVariables/EnvironmentVariables.xml" in files
    assert "IniFiles/IniFiles.xml" in files
    assert "RegionalOptions/RegionalOptions.xml" in files
    assert "PowerOptions/PowerOptions.xml" in files
    assert "Devices/Devices.xml" in files
    assert "FolderOptions/FolderOptions.xml" in files
    assert "DataSources/DataSources.xml" in files
    assert "Drives/Drives.xml" in files
    assert "Files/Files.xml" in files
    assert "Folders/Folders.xml" in files
    assert "NetworkShares/NetworkShares.xml" in files
    assert "Printers/Printers.xml" in files
    assert "Shortcuts/Shortcuts.xml" in files
    assert "Applications/Applications.xml" in files

    parsed = parse_gpp_collection("computer", files)
    assert parsed.scope == "computer"
    assert len(parsed.environment) == 1
    assert parsed.environment[0] == col.environment[0]
    assert len(parsed.ini_files) == 1
    assert parsed.ini_files[0] == col.ini_files[0]
    assert len(parsed.regional_options) == 1
    assert parsed.regional_options[0] == col.regional_options[0]
    assert len(parsed.power_options) == 1
    assert parsed.power_options[0] == col.power_options[0]
    assert len(parsed.devices) == 1
    assert parsed.devices[0] == col.devices[0]
    assert len(parsed.folder_options) == 1
    assert parsed.folder_options[0] == col.folder_options[0]
    assert len(parsed.data_sources) == 1
    assert parsed.data_sources[0] == col.data_sources[0]
    assert len(parsed.drives) == 1
    assert parsed.drives[0] == col.drives[0]
    assert len(parsed.files) == 1
    assert parsed.files[0] == col.files[0]
    assert len(parsed.folders) == 1
    assert parsed.folders[0] == col.folders[0]
    assert len(parsed.network_shares) == 1
    assert parsed.network_shares[0] == col.network_shares[0]
    assert len(parsed.printers) == 1
    assert parsed.printers[0] == col.printers[0]
    assert len(parsed.shortcuts) == 1
    assert parsed.shortcuts[0] == col.shortcuts[0]
    assert len(parsed.applications) == 1
    assert parsed.applications[0] == col.applications[0]


def test_gpp_collection_dict_roundtrip() -> None:
    """gpp_collection_to_dict → gpp_collection_from_dict preserves all adapters."""
    col = GppCollection(
        scope="user",
        environment=(GppEnvironment(name="X", value="Y", user=True),),
        ini_files=(GppIniFile(path="/tmp/x.ini", section="S"),),
        regional_options=(GppRegionalOptions(user_locale="en-US"),),
        power_options=(GppPowerOptions(scheme_name="Balanced"),),
        devices=(GppDevice(device_name="Disk", device_action="disable"),),
        folder_options=(GppFolderOptions(show_hidden=True, show_extensions=False),),
        data_sources=(GppDataSource(dsn="DSN1", driver="SQL Server", user_dsn=True),),
        drives=(GppDrive(letter="Z:", path=r"\\server\share"),),
        files=(GppFile(source=r"\\server\a.txt", target=r"C:\Temp\a.txt"),),
        folders=(GppFolder(path=r"C:\Temp\Folder"),),
        network_shares=(GppNetworkShare(name="Share", path=r"C:\Shared"),),
        printers=(GppPrinter(path=r"\\server\printer", action_type="update"),),
        shortcuts=(GppShortcut(name="Note", target_path=r"C:\Windows\notepad.exe"),),
        applications=(GppApplication(name="App", path=r"\\server\app.exe"),),
    )
    d = gpp_collection_to_dict(col)
    restored = gpp_collection_from_dict(d)

    assert restored.scope == col.scope
    assert len(restored.environment) == 1
    assert restored.environment[0] == col.environment[0]
    assert len(restored.ini_files) == 1
    assert restored.ini_files[0] == col.ini_files[0]
    assert len(restored.regional_options) == 1
    assert restored.regional_options[0] == col.regional_options[0]
    assert len(restored.power_options) == 1
    assert restored.power_options[0] == col.power_options[0]
    assert len(restored.devices) == 1
    assert restored.devices[0] == col.devices[0]
    assert len(restored.folder_options) == 1
    assert restored.folder_options[0] == col.folder_options[0]
    assert len(restored.data_sources) == 1
    assert restored.data_sources[0] == col.data_sources[0]
    assert len(restored.drives) == 1
    assert restored.drives[0] == col.drives[0]
    assert len(restored.files) == 1
    assert restored.files[0] == col.files[0]
    assert len(restored.folders) == 1
    assert restored.folders[0] == col.folders[0]
    assert len(restored.network_shares) == 1
    assert restored.network_shares[0] == col.network_shares[0]
    assert len(restored.printers) == 1
    assert restored.printers[0] == col.printers[0]
    assert len(restored.shortcuts) == 1
    assert restored.shortcuts[0] == col.shortcuts[0]
    assert len(restored.applications) == 1
    assert restored.applications[0] == col.applications[0]


def test_gpp_collection_empty_adapters_not_serialized() -> None:
    """Empty adapter sections don't produce files."""
    col = GppCollection(scope="computer")
    files = serialize_gpp(col)
    assert files == {}


def test_gpp_collection_backward_compat_with_old_dict() -> None:
    """Dicts without adapter keys produce empty adapter tuples."""
    d = {
        "scope": "computer",
        "groups": [],
        "registry": [],
    }
    restored = gpp_collection_from_dict(d)
    assert restored.environment == ()
    assert restored.ini_files == ()
    assert restored.regional_options == ()
    assert restored.power_options == ()
    assert restored.devices == ()
    assert restored.folder_options == ()
    assert restored.data_sources == ()
    assert restored.drives == ()
    assert restored.files == ()
    assert restored.folders == ()
    assert restored.network_shares == ()
    assert restored.printers == ()
    assert restored.shortcuts == ()
    assert restored.applications == ()


def test_ensure_editor_ids_assigns_ids_to_adapters() -> None:
    """ensure_editor_ids assigns UUIDs to empty-id adapter items."""
    col = GppCollection(
        scope="computer",
        environment=(GppEnvironment(name="X"),),
        devices=(GppDevice(device_name="Disk"),),
        data_sources=(GppDataSource(dsn="DSN"),),
        drives=(GppDrive(letter="Z:"),),
        files=(GppFile(target=r"C:\Temp\a.txt"),),
        folders=(GppFolder(path=r"C:\Temp\Folder"),),
        network_shares=(GppNetworkShare(name="Share"),),
        printers=(GppPrinter(path=r"\\server\printer"),),
        shortcuts=(GppShortcut(name="Note"),),
        applications=(GppApplication(name="App"),),
    )
    result = ensure_editor_ids(col)
    assert result.environment[0].id != ""
    assert result.devices[0].id != ""
    assert result.data_sources[0].id != ""
    assert result.drives[0].id != ""
    assert result.files[0].id != ""
    assert result.folders[0].id != ""
    assert result.network_shares[0].id != ""
    assert result.printers[0].id != ""
    assert result.shortcuts[0].id != ""
    assert result.applications[0].id != ""


def test_ensure_editor_ids_preserves_existing_adapter_ids() -> None:
    col = GppCollection(
        scope="computer",
        environment=(GppEnvironment(name="X", id="env-1"),),
    )
    result = ensure_editor_ids(col)
    assert result.environment[0].id == "env-1"


def test_adapter_action_codes() -> None:
    """All GppAction values round-trip correctly."""
    for action, code in [("add", "C"), ("replace", "R"), ("update", "U"), ("remove", "D")]:
        env = GppEnvironment(name="X", action=action)  # type: ignore[arg-type]
        data = serialize_gpp_environment((env,), "computer")
        assert f'action="{code}"'.encode() in data
        parsed = parse_gpp_environment(data)
        assert parsed[0].action == action


def test_adapters_with_ilt_and_common_and_unknowns() -> None:
    """Full round-trip with all features enabled."""
    env = GppEnvironment(
        name="COMPLEX",
        value="value",
        user=True,
        action="replace",
        common=_all_true_common(),
        ilt_filter=_sample_ilt(),
        unknown_attrs=(("image", "2"), ("changed", "2024-01-01")),
        unknown_children=(),
    )
    data = serialize_gpp_environment((env,), "computer")
    parsed = parse_gpp_environment(data)
    assert parsed[0] == env


def test_multiple_items_in_one_file() -> None:
    """Multiple items of the same type in one file round-trip correctly."""
    envs = (
        GppEnvironment(name="VAR1", value="val1", user=True),
        GppEnvironment(name="VAR2", value="val2", user=False, action="remove"),
        GppEnvironment(name="VAR3", value="val3"),
    )
    data = serialize_gpp_environment(envs, "computer")
    parsed = parse_gpp_environment(data)
    assert len(parsed) == 3
    assert parsed[0] == envs[0]
    assert parsed[1] == envs[1]
    assert parsed[2] == envs[2]


def test_collection_with_groups_and_adapters() -> None:
    """Collection with both legacy (groups/registry) and new adapters works."""
    from gpo_studio.gpp import GppGroup, GppRegistry, GppRegistryValue

    col = GppCollection(
        scope="computer",
        groups=(GppGroup(name="Admins", sid="S-1-5-32-544"),),
        registry=(
            GppRegistry(
                key="Software\\Test",
                value=GppRegistryValue(name="Enabled", value=1, registry_type="REG_DWORD"),
            ),
        ),
        environment=(GppEnvironment(name="X", value="Y"),),
    )
    files = serialize_gpp(col)
    assert "Groups/Groups.xml" in files
    assert "Registry/Registry.xml" in files
    assert "EnvironmentVariables/EnvironmentVariables.xml" in files

    parsed = parse_gpp_collection("computer", files)
    assert len(parsed.groups) == 1
    assert parsed.groups[0].name == "Admins"
    assert len(parsed.registry) == 1
    assert len(parsed.environment) == 1
    assert parsed.environment[0].name == "X"


# ---------------------------------------------------------------------------
# Drive Maps
# ---------------------------------------------------------------------------


def test_drive_roundtrip() -> None:
    drive = GppDrive(
        letter="Z:",
        path=r"\\server\share",
        label="Share",
        persistent=True,
        use_letter=True,
        action="replace",
    )
    data = serialize_gpp_drives((drive,), "user")
    parsed = parse_gpp_drives(data)
    assert len(parsed) == 1
    assert parsed[0] == drive


def test_drive_common_options() -> None:
    drive = GppDrive(letter="Y:", path=r"\\server\share", common=_all_true_common())
    data = serialize_gpp_drives((drive,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_drives(data)
    assert parsed[0].common == _all_true_common()


def test_drive_ilt_filter() -> None:
    drive = GppDrive(letter="X:", path=r"\\server\share", ilt_filter=_sample_ilt())
    data = serialize_gpp_drives((drive,), "computer")
    parsed = parse_gpp_drives(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_drive_unknown_attrs_preserved() -> None:
    drive = GppDrive(
        letter="W:",
        path=r"\\server\share",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_drives((drive,), "computer")
    parsed = parse_gpp_drives(data)
    assert parsed[0].unknown_attrs == drive.unknown_attrs


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_file_roundtrip() -> None:
    fi = GppFile(
        source=r"\\server\source.txt",
        target=r"C:\Temp\target.txt",
        read_only=True,
        hidden=True,
        archive=False,
        suppress=True,
        action="add",
    )
    data = serialize_gpp_files((fi,), "computer")
    parsed = parse_gpp_files(data)
    assert len(parsed) == 1
    assert parsed[0] == fi


def test_file_common_options() -> None:
    fi = GppFile(
        source=r"\\server\a.txt",
        target=r"C:\Temp\a.txt",
        common=_all_true_common(),
    )
    data = serialize_gpp_files((fi,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_files(data)
    assert parsed[0].common == _all_true_common()


def test_file_ilt_filter() -> None:
    fi = GppFile(
        source=r"\\server\a.txt",
        target=r"C:\Temp\a.txt",
        ilt_filter=_sample_ilt(),
    )
    data = serialize_gpp_files((fi,), "computer")
    parsed = parse_gpp_files(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_file_unknown_attrs_preserved() -> None:
    fi = GppFile(
        target=r"C:\Temp\a.txt",
        unknown_attrs=(("image", "3"),),
    )
    data = serialize_gpp_files((fi,), "computer")
    parsed = parse_gpp_files(data)
    assert parsed[0].unknown_attrs == fi.unknown_attrs


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def test_folder_roundtrip() -> None:
    folder = GppFolder(
        path=r"C:\Temp\Folder",
        read_only=True,
        hidden=True,
        archive=False,
        suppress=True,
        action="remove",
    )
    data = serialize_gpp_folders((folder,), "computer")
    parsed = parse_gpp_folders(data)
    assert len(parsed) == 1
    assert parsed[0] == folder


def test_folder_common_options() -> None:
    folder = GppFolder(path=r"C:\Temp\Folder", common=_all_true_common())
    data = serialize_gpp_folders((folder,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_folders(data)
    assert parsed[0].common == _all_true_common()


def test_folder_ilt_filter() -> None:
    folder = GppFolder(path=r"C:\Temp\Folder", ilt_filter=_sample_ilt())
    data = serialize_gpp_folders((folder,), "computer")
    parsed = parse_gpp_folders(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_folder_unknown_attrs_preserved() -> None:
    folder = GppFolder(
        path=r"C:\Temp\Folder",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_folders((folder,), "computer")
    parsed = parse_gpp_folders(data)
    assert parsed[0].unknown_attrs == folder.unknown_attrs


# ---------------------------------------------------------------------------
# Network Shares
# ---------------------------------------------------------------------------


def test_network_share_roundtrip() -> None:
    share = GppNetworkShare(
        name="ShareName",
        path=r"C:\Shared",
        comment="Test share",
        user_limit=10,
        action="replace",
    )
    data = serialize_gpp_network_shares((share,), "computer")
    parsed = parse_gpp_network_shares(data)
    assert len(parsed) == 1
    assert parsed[0] == share


def test_network_share_common_options() -> None:
    share = GppNetworkShare(
        name="ShareName",
        path=r"C:\Shared",
        common=_all_true_common(),
    )
    data = serialize_gpp_network_shares((share,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_network_shares(data)
    assert parsed[0].common == _all_true_common()


def test_network_share_ilt_filter() -> None:
    share = GppNetworkShare(
        name="ShareName",
        path=r"C:\Shared",
        ilt_filter=_sample_ilt(),
    )
    data = serialize_gpp_network_shares((share,), "computer")
    parsed = parse_gpp_network_shares(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_network_share_unknown_attrs_preserved() -> None:
    share = GppNetworkShare(
        name="ShareName",
        path=r"C:\Shared",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_network_shares((share,), "computer")
    parsed = parse_gpp_network_shares(data)
    assert parsed[0].unknown_attrs == share.unknown_attrs


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------


def test_printer_roundtrip() -> None:
    printer = GppPrinter(
        path=r"\\server\printer",
        action_type="update",
        set_default=True,
        use_local=True,
        comment="Main printer",
    )
    data = serialize_gpp_printers((printer,), "user")
    parsed = parse_gpp_printers(data)
    assert len(parsed) == 1
    assert parsed[0] == printer


def test_printer_common_options() -> None:
    printer = GppPrinter(
        path=r"\\server\printer",
        common=_all_true_common(),
    )
    data = serialize_gpp_printers((printer,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_printers(data)
    assert parsed[0].common == _all_true_common()


def test_printer_ilt_filter() -> None:
    printer = GppPrinter(
        path=r"\\server\printer",
        ilt_filter=_sample_ilt(),
    )
    data = serialize_gpp_printers((printer,), "computer")
    parsed = parse_gpp_printers(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_printer_unknown_attrs_preserved() -> None:
    printer = GppPrinter(
        path=r"\\server\printer",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_printers((printer,), "computer")
    parsed = parse_gpp_printers(data)
    assert parsed[0].unknown_attrs == printer.unknown_attrs


def test_printer_action_codes() -> None:
    for action_type, code in [("create", "C"), ("delete", "D"), ("update", "U")]:
        printer = GppPrinter(path=r"\\server\printer", action_type=action_type)  # type: ignore[arg-type]
        data = serialize_gpp_printers((printer,), "computer")
        assert f'action="{code}"'.encode() in data
        parsed = parse_gpp_printers(data)
        assert parsed[0].action_type == action_type


def test_printer_roundtrip_delete_action() -> None:
    printer = GppPrinter(
        path=r"\\server\printer",
        action_type="delete",
        action="update",
    )
    data = serialize_gpp_printers((printer,), "user")
    parsed = parse_gpp_printers(data)
    assert len(parsed) == 1
    assert parsed[0].action_type == "delete"
    assert parsed[0].action == "update"


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------


def test_shortcut_roundtrip() -> None:
    sc = GppShortcut(
        name="Notepad",
        target_path=r"C:\Windows\notepad.exe",
        arguments="%1",
        start_in=r"C:\Temp",
        icon_path=r"C:\Windows\notepad.exe",
        icon_index=1,
        window_style="maximized",
        shortcut_path=r"C:\Users\Public\Desktop\Notepad.lnk",
        action="replace",
    )
    data = serialize_gpp_shortcuts((sc,), "user")
    parsed = parse_gpp_shortcuts(data)
    assert len(parsed) == 1
    assert parsed[0] == sc


def test_shortcut_common_options() -> None:
    sc = GppShortcut(
        name="Notepad",
        target_path=r"C:\Windows\notepad.exe",
        common=_all_true_common(),
    )
    data = serialize_gpp_shortcuts((sc,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_shortcuts(data)
    assert parsed[0].common == _all_true_common()


def test_shortcut_ilt_filter() -> None:
    sc = GppShortcut(
        name="Notepad",
        target_path=r"C:\Windows\notepad.exe",
        ilt_filter=_sample_ilt(),
    )
    data = serialize_gpp_shortcuts((sc,), "computer")
    parsed = parse_gpp_shortcuts(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_shortcut_unknown_attrs_preserved() -> None:
    sc = GppShortcut(
        name="Notepad",
        target_path=r"C:\Windows\notepad.exe",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_shortcuts((sc,), "computer")
    parsed = parse_gpp_shortcuts(data)
    assert parsed[0].unknown_attrs == sc.unknown_attrs


def test_shortcut_window_styles() -> None:
    styles = [
        ("normal", "Normal"),
        ("minimized", "Minimized"),
        ("maximized", "Maximized"),
    ]
    for style, code in styles:
        sc = GppShortcut(name="X", target_path="C:\\X.exe", window_style=style)  # type: ignore[arg-type]
        data = serialize_gpp_shortcuts((sc,), "computer")
        assert f'window="{code}"'.encode() in data
        parsed = parse_gpp_shortcuts(data)
        assert parsed[0].window_style == style


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def test_application_roundtrip() -> None:
    app = GppApplication(
        name="Install App",
        path=r"\\server\installer.exe",
        command_line="/quiet",
        run_as=r"DOMAIN\Admin",
        action="add",
    )
    data = serialize_gpp_applications((app,), "computer")
    parsed = parse_gpp_applications(data)
    assert len(parsed) == 1
    assert parsed[0] == app


def test_application_common_options() -> None:
    app = GppApplication(
        name="Install App",
        path=r"\\server\installer.exe",
        common=_all_true_common(),
    )
    data = serialize_gpp_applications((app,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_applications(data)
    assert parsed[0].common == _all_true_common()


def test_application_ilt_filter() -> None:
    app = GppApplication(
        name="Install App",
        path=r"\\server\installer.exe",
        ilt_filter=_sample_ilt(),
    )
    data = serialize_gpp_applications((app,), "computer")
    parsed = parse_gpp_applications(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_application_unknown_attrs_preserved() -> None:
    app = GppApplication(
        name="Install App",
        path=r"\\server\installer.exe",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_applications((app,), "computer")
    parsed = parse_gpp_applications(data)
    assert parsed[0].unknown_attrs == app.unknown_attrs


# ---------------------------------------------------------------------------
# NT Services (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def test_service_roundtrip() -> None:
    svc = GppService(
        service_name="Spooler",
        display_name="Print Spooler",
        startup_type="disabled",
        service_action="stop",
        first_failure="restart",
        second_failure="reboot",
        reset_period_days=1,
        restart_delay_minutes=5,
        recovery_command=r"C:\fix.bat",
        timeout_seconds=60,
        account_name="LocalSystem",
        action="replace",
    )
    data = serialize_gpp_services((svc,), "computer")
    parsed = parse_gpp_services(data)
    assert len(parsed) == 1
    assert parsed[0] == svc


def test_service_common_options() -> None:
    svc = GppService(service_name="Spooler", common=_all_true_common())
    data = serialize_gpp_services((svc,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_services(data)
    assert parsed[0].common == _all_true_common()


def test_service_ilt_filter() -> None:
    svc = GppService(service_name="Spooler", ilt_filter=_sample_ilt())
    data = serialize_gpp_services((svc,), "computer")
    parsed = parse_gpp_services(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_service_unknown_attrs_preserved() -> None:
    svc = GppService(
        service_name="Spooler",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_services((svc,), "computer")
    parsed = parse_gpp_services(data)
    assert parsed[0].unknown_attrs == svc.unknown_attrs


def test_service_startup_type_codes() -> None:
    """Startup types serialize as the symbolic names GPMC writes.

    This previously asserted the numeric codes 2/3/4. That was self-consistent
    -- Studio round-tripping its own output -- but wrong against Windows: no
    genuine GPMC capture contains a numeric startupType, and every real value
    was rejected on parse (WI-019). Corrected against the native corpus.
    """
    for startup, code in [
        ("automatic", "AUTOMATIC"),
        ("manual", "MANUAL"),
        ("disabled", "DISABLED"),
        ("no_change", "NOCHANGE"),
    ]:
        svc = GppService(service_name="S", startup_type=startup)  # type: ignore[arg-type]
        data = serialize_gpp_services((svc,), "computer")
        assert f'startupType="{code}"'.encode() in data
        parsed = parse_gpp_services(data)
        assert parsed[0].startup_type == startup


def test_service_startup_numeric_codes_still_parse() -> None:
    """Numeric codes are accepted on parse only, for older persisted data."""
    svc = GppService(service_name="S", startup_type="automatic")
    data = serialize_gpp_services((svc,), "computer")
    legacy = data.replace(b'startupType="AUTOMATIC"', b'startupType="2"')
    assert parse_gpp_services(legacy)[0].startup_type == "automatic"


def test_service_password_denied() -> None:
    svc = GppService(service_name="Spooler", account_password="s3cr3t")
    try:
        serialize_gpp_services((svc,), "computer")
    except GppError:
        return
    raise AssertionError("expected GppError for non-empty account_password")


def test_service_password_always_empty_in_xml() -> None:
    svc = GppService(service_name="Spooler", account_name="LocalSystem")
    data = serialize_gpp_services((svc,), "computer")
    assert b"cpassword" not in data.lower()
    assert b"accountPassword" not in data
    assert b"password" not in data.lower()


# ---------------------------------------------------------------------------
# Local Users (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def test_local_user_roundtrip() -> None:
    user = GppLocalUser(
        user_name="DbAdmin",
        full_name="Database Admin",
        description="Local Database Admin",
        password_never_expires=True,
        user_cannot_change_password=False,
        account_disabled=True,
        account_locked_out=False,
        action="replace",
    )
    data = serialize_gpp_local_users((user,), "computer")
    parsed = parse_gpp_local_users(data)
    assert len(parsed) == 1
    assert parsed[0] == user


def test_local_user_common_options() -> None:
    user = GppLocalUser(user_name="DbAdmin", common=_all_true_common())
    data = serialize_gpp_local_users((user,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_local_users(data)
    assert parsed[0].common == _all_true_common()


def test_local_user_ilt_filter() -> None:
    user = GppLocalUser(user_name="DbAdmin", ilt_filter=_sample_ilt())
    data = serialize_gpp_local_users((user,), "computer")
    parsed = parse_gpp_local_users(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_local_user_unknown_attrs_preserved() -> None:
    user = GppLocalUser(
        user_name="DbAdmin",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_local_users((user,), "computer")
    parsed = parse_gpp_local_users(data)
    assert parsed[0].unknown_attrs == user.unknown_attrs


def test_local_user_password_denied() -> None:
    user = GppLocalUser(user_name="DbAdmin", password="s3cr3t")
    try:
        serialize_gpp_local_users((user,), "computer")
    except GppError:
        return
    raise AssertionError("expected GppError for non-empty password")


# ---------------------------------------------------------------------------
# Local Groups (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def test_local_group_roundtrip() -> None:
    group = GppLocalGroup(
        group_name="Database Admins",
        description="Local Database Admins",
        delete_all_users=True,
        delete_all_groups=True,
        members=(
            GppLocalGroupMember(name=r"domain\sampleuser", action="add"),
            GppLocalGroupMember(name=r"domain\olduser", action="remove"),
        ),
        action="replace",
    )
    data = serialize_gpp_local_groups((group,), "computer")
    parsed = parse_gpp_local_groups(data)
    assert len(parsed) == 1
    assert parsed[0] == group


def test_local_group_common_options() -> None:
    group = GppLocalGroup(group_name="Admins", common=_all_true_common())
    data = serialize_gpp_local_groups((group,), "computer")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_local_groups(data)
    assert parsed[0].common == _all_true_common()


def test_local_group_ilt_filter() -> None:
    group = GppLocalGroup(group_name="Admins", ilt_filter=_sample_ilt())
    data = serialize_gpp_local_groups((group,), "computer")
    parsed = parse_gpp_local_groups(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_local_group_unknown_attrs_preserved() -> None:
    group = GppLocalGroup(
        group_name="Admins",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_local_groups((group,), "computer")
    parsed = parse_gpp_local_groups(data)
    assert parsed[0].unknown_attrs == group.unknown_attrs


def test_local_group_no_members_roundtrip() -> None:
    group = GppLocalGroup(group_name="Admins", delete_all_users=True)
    data = serialize_gpp_local_groups((group,), "computer")
    parsed = parse_gpp_local_groups(data)
    assert len(parsed) == 1
    assert parsed[0] == group
    assert parsed[0].members == ()


def test_local_group_member_action_codes() -> None:
    for action, code in [("add", "ADD"), ("remove", "REMOVE")]:
        group = GppLocalGroup(
            group_name="Admins",
            members=(GppLocalGroupMember(name="x", action=action),),  # type: ignore[arg-type]
        )
        data = serialize_gpp_local_groups((group,), "computer")
        assert f'action="{code}"'.encode() in data
        parsed = parse_gpp_local_groups(data)
        assert parsed[0].members[0].action == action


# ---------------------------------------------------------------------------
# Scheduled Tasks (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def test_scheduled_task_roundtrip() -> None:
    """The Task Scheduler 1.0 element, where the scalar attributes belong.

    This previously used the default element_variant (TaskV2) and asserted the
    scalar attributes round-tripped on it. They did -- through Studio's own
    parser -- but a v2 item ignores them entirely on Windows (WI-018), so the
    scalar path is now exercised against the v1 element that actually defines
    those attributes.
    """
    task = GppScheduledTask(
        name="Cleanup",
        element_variant="Task",
        run_as=r"DOMAIN\Admin",
        program=r"\\scratch\filecleanup.exe",
        arguments="-all",
        start_in="c:\\",
        enabled=True,
        trigger_type="daily",
        trigger_time="10:00",
        trigger_days="Monday,Wednesday",
        action="replace",
    )
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    assert b'triggerType="DAILY"' in data
    parsed = parse_gpp_scheduled_tasks(data)
    assert len(parsed) == 1
    assert parsed[0] == task


def test_scheduled_task_v2_roundtrips_through_an_embedded_payload() -> None:
    """A TaskV2 carries its schedule in <Task>, and the scalars project back."""
    task = GppScheduledTask(
        name="Cleanup",
        element_variant="TaskV2",
        run_as=r"DOMAIN\Admin",
        program=r"\\scratch\filecleanup.exe",
        arguments="-all",
        start_in="c:\\",
        trigger_type="weekly",
        trigger_time="2026-01-01T10:00:00",
        trigger_days="Monday",
        action="replace",
    )
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    # The shape genuine GPMC writes: payload present, v1 scalars absent.
    assert b"<Task version=" in data
    assert b"ScheduleByWeek" in data
    for scalar in (b"program=", b"arguments=", b"startIn=", b"triggerType="):
        assert scalar not in data
    parsed = parse_gpp_scheduled_tasks(data)[0]
    assert parsed.trigger_type == "weekly"
    assert parsed.trigger_days == "Monday"
    assert parsed.trigger_time == "2026-01-01T10:00:00"
    assert parsed.program == task.program
    assert parsed.arguments == task.arguments


def test_scheduled_task_common_options() -> None:
    task = GppScheduledTask(name="Cleanup", common=_all_true_common())
    data = serialize_gpp_scheduled_tasks((task,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_scheduled_tasks(data)
    assert parsed[0].common == _all_true_common()


def test_scheduled_task_ilt_filter() -> None:
    task = GppScheduledTask(name="Cleanup", ilt_filter=_sample_ilt())
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    parsed = parse_gpp_scheduled_tasks(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_scheduled_task_unknown_attrs_preserved() -> None:
    task = GppScheduledTask(
        name="Cleanup",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    parsed = parse_gpp_scheduled_tasks(data)
    assert parsed[0].unknown_attrs == task.unknown_attrs


def test_scheduled_task_trigger_type_codes() -> None:
    """Scalar trigger codes, on the v1 element that defines them."""
    for trigger, code in [
        ("once", "ONCE"),
        ("daily", "DAILY"),
        ("weekly", "WEEKLY"),
        ("monthly", "MONTHLY"),
        ("at_logon", "ATLOGON"),
        ("at_startup", "ATSTARTUP"),
    ]:
        task = GppScheduledTask(
            name="X",
            element_variant="Task",
            trigger_type=trigger,  # type: ignore[arg-type]
        )
        data = serialize_gpp_scheduled_tasks((task,), "computer")
        assert f'triggerType="{code}"'.encode() in data
        parsed = parse_gpp_scheduled_tasks(data)
        assert parsed[0].trigger_type == trigger


def test_scheduled_task_password_denied() -> None:
    task = GppScheduledTask(name="Cleanup", run_as_password="s3cr3t")
    try:
        serialize_gpp_scheduled_tasks((task,), "computer")
    except GppError:
        return
    raise AssertionError("expected GppError for non-empty run_as_password")


def test_scheduled_task_variant_taskv2_parsed() -> None:
    xml = (
        b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
        b'<TaskV2 clsid="{D8896631-B747-47a7-84A6-C155337F3BC8}" name="V2Task">'
        b'<Properties action="C" name="V2Task" runAs="SYSTEM" />'
        b"</TaskV2></ScheduledTasks>"
    )
    parsed = parse_gpp_scheduled_tasks(xml)
    assert len(parsed) == 1
    assert parsed[0].element_variant == "TaskV2"
    assert parsed[0].name == "V2Task"


def test_scheduled_task_variant_legacy_task_parsed() -> None:
    xml = (
        b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
        b'<Task clsid="{2DEECB1C-261F-4e13-9B21-16FB83BC03BD}" name="LegacyTask">'
        b'<Properties action="U" name="LegacyTask" runAs="SYSTEM" />'
        b"</Task></ScheduledTasks>"
    )
    parsed = parse_gpp_scheduled_tasks(xml)
    assert len(parsed) == 1
    assert parsed[0].element_variant == "Task"
    assert parsed[0].name == "LegacyTask"


def test_scheduled_task_variant_both_parsed() -> None:
    xml = (
        b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
        b'<TaskV2 clsid="{D8896631-B747-47a7-84A6-C155337F3BC8}" name="V2">'
        b'<Properties action="C" name="V2" />'
        b"</TaskV2>"
        b'<Task clsid="{2DEECB1C-261F-4e13-9B21-16FB83BC03BD}" name="Legacy">'
        b'<Properties action="U" name="Legacy" />'
        b"</Task></ScheduledTasks>"
    )
    parsed = parse_gpp_scheduled_tasks(xml)
    assert len(parsed) == 2
    assert parsed[0].element_variant == "TaskV2"
    assert parsed[1].element_variant == "Task"


def test_scheduled_task_roundtrip_taskv2() -> None:
    task = GppScheduledTask(name="Cleanup", element_variant="TaskV2")
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    assert b"<TaskV2 " in data
    # A TaskV2 now carries an embedded <Task version="1.2"> payload, which is
    # what genuine GPMC writes; this previously asserted its absence.
    assert b"<Task version=" in data
    parsed = parse_gpp_scheduled_tasks(data)
    assert parsed[0].element_variant == "TaskV2"
    assert parsed[0].task_xml
    assert parsed[0].name == task.name


def test_scheduled_task_roundtrip_legacy_task() -> None:
    task = GppScheduledTask(name="Old", element_variant="Task")
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    assert b"<Task " in data
    assert b"<TaskV2 " not in data
    parsed = parse_gpp_scheduled_tasks(data)
    assert parsed[0].element_variant == "Task"
    assert parsed[0] == task


def test_scheduled_task_default_variant_is_taskv2() -> None:
    task = GppScheduledTask(name="Default")
    assert task.element_variant == "TaskV2"
    data = serialize_gpp_scheduled_tasks((task,), "computer")
    assert b"<TaskV2 " in data


# ---------------------------------------------------------------------------
# Immediate Tasks (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def test_immediate_task_roundtrip() -> None:
    task = GppImmediateTask(
        name="PingCorporate",
        run_as=r"DOMAIN\Admin",
        program=r"c:\ping.exe",
        arguments="-ip 10.10.10.10",
        start_in="c:\\",
        action="replace",
    )
    data = serialize_gpp_immediate_tasks((task,), "computer")
    parsed = parse_gpp_immediate_tasks(data)
    assert len(parsed) == 1
    assert parsed[0] == task


def test_immediate_task_common_options() -> None:
    task = GppImmediateTask(name="Ping", common=_all_true_common())
    data = serialize_gpp_immediate_tasks((task,), "user")
    assert b"<FilterRunOnce " in data
    parsed = parse_gpp_immediate_tasks(data)
    assert parsed[0].common == _all_true_common()


def test_immediate_task_ilt_filter() -> None:
    task = GppImmediateTask(name="Ping", ilt_filter=_sample_ilt())
    data = serialize_gpp_immediate_tasks((task,), "computer")
    parsed = parse_gpp_immediate_tasks(data)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == _sample_ilt().predicates


def test_immediate_task_unknown_attrs_preserved() -> None:
    task = GppImmediateTask(
        name="Ping",
        unknown_attrs=(("image", "2"),),
    )
    data = serialize_gpp_immediate_tasks((task,), "computer")
    parsed = parse_gpp_immediate_tasks(data)
    assert parsed[0].unknown_attrs == task.unknown_attrs


def test_immediate_task_password_denied() -> None:
    task = GppImmediateTask(name="Ping", run_as_password="s3cr3t")
    try:
        serialize_gpp_immediate_tasks((task,), "computer")
    except GppError:
        return
    raise AssertionError("expected GppError for non-empty run_as_password")


# ---------------------------------------------------------------------------
# Task XML opaque subtree preservation (D2 hybrid model)
# ---------------------------------------------------------------------------

_TASK_XML_FIXTURE = (
    b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
    b'<TaskV2 clsid="{D8896631-B747-47a7-84A6-C155337F3BC8}" name="Cleanup">'
    b'<Properties action="U" name="Cleanup" runAs="NT AUTHORITY\\SYSTEM">'
    b"<Task version=\"1.3\">"
    b"<RegistrationInfo><Author>LAB\\admin</Author></RegistrationInfo>"
    b"<Triggers><CalendarTrigger>"
    b"<StartBoundary>2026-01-01T02:00:00</StartBoundary>"
    b"<Enabled>true</Enabled>"
    b"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    b"</CalendarTrigger></Triggers>"
    b"<Settings><Enabled>true</Enabled></Settings>"
    b"<Principals><Principal id=\"Author\">"
    b"<UserId>NT AUTHORITY\\SYSTEM</UserId>"
    b"<LogonType>S4U</LogonType>"
    b"</Principal></Principals>"
    b"<Actions><Exec>"
    b"<Command>C:\\Windows\\System32\\cleanmgr.exe</Command>"
    b"<Arguments>/sagerun:1</Arguments>"
    b"<WorkingDirectory>C:\\Windows</WorkingDirectory>"
    b"</Exec></Actions>"
    b"</Task>"
    b"</Properties></TaskV2></ScheduledTasks>"
)

_IMMEDIATE_TASK_XML_FIXTURE = (
    b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
    b'<ImmediateTaskV2 clsid="{9756B581-76EC-4169-9AFC-0CA8D43ADB5F}" name="Init">'
    b'<Properties action="C" name="Init" runAs="NT AUTHORITY\\SYSTEM">'
    b"<Task version=\"1.2\">"
    b"<Triggers />"
    b"<Principals><Principal id=\"Author\">"
    b"<UserId>NT AUTHORITY\\SYSTEM</UserId>"
    b"</Principal></Principals>"
    b"<Actions><Exec>"
    b"<Command>C:\\Windows\\System32\\cmd.exe</Command>"
    b"<Arguments>/c echo init</Arguments>"
    b"</Exec></Actions>"
    b"</Task>"
    b"</Properties></ImmediateTaskV2></ScheduledTasks>"
)


def test_scheduled_task_xml_captured() -> None:
    parsed = parse_gpp_scheduled_tasks(_TASK_XML_FIXTURE)
    assert len(parsed) == 1
    assert parsed[0].task_xml != ""
    assert "<Task version=" in parsed[0].task_xml


def test_scheduled_task_xml_contains_expected_elements() -> None:
    parsed = parse_gpp_scheduled_tasks(_TASK_XML_FIXTURE)
    task_xml = parsed[0].task_xml
    assert "<Triggers>" in task_xml
    assert "<Actions>" in task_xml
    assert "<Principals>" in task_xml
    assert "<Exec>" in task_xml


def test_scheduled_task_projections_from_task_xml() -> None:
    parsed = parse_gpp_scheduled_tasks(_TASK_XML_FIXTURE)
    task = parsed[0]
    assert task.program == "C:\\Windows\\System32\\cleanmgr.exe"
    assert task.arguments == "/sagerun:1"
    assert task.start_in == "C:\\Windows"


def test_scheduled_task_xml_roundtrip() -> None:
    parsed = parse_gpp_scheduled_tasks(_TASK_XML_FIXTURE)
    data = serialize_gpp_scheduled_tasks(parsed, "computer")
    reparsed = parse_gpp_scheduled_tasks(data)
    assert len(reparsed) == 1
    assert reparsed[0].task_xml == parsed[0].task_xml
    assert reparsed[0] == parsed[0]


def test_immediate_task_xml_captured() -> None:
    parsed = parse_gpp_immediate_tasks(_IMMEDIATE_TASK_XML_FIXTURE)
    assert len(parsed) == 1
    assert parsed[0].task_xml != ""
    assert "<Task version=" in parsed[0].task_xml


def test_immediate_task_projections_from_task_xml() -> None:
    parsed = parse_gpp_immediate_tasks(_IMMEDIATE_TASK_XML_FIXTURE)
    task = parsed[0]
    assert task.program == "C:\\Windows\\System32\\cmd.exe"
    assert task.arguments == "/c echo init"
    assert task.start_in == ""


def test_immediate_task_xml_roundtrip() -> None:
    parsed = parse_gpp_immediate_tasks(_IMMEDIATE_TASK_XML_FIXTURE)
    data = serialize_gpp_immediate_tasks(parsed, "computer")
    reparsed = parse_gpp_immediate_tasks(data)
    assert len(reparsed) == 1
    assert reparsed[0].task_xml == parsed[0].task_xml
    assert reparsed[0] == parsed[0]


def test_scheduled_task_no_task_xml_still_works() -> None:
    """Authoring without an explicit payload works -- one is synthesized.

    The v1 element keeps no payload; the v2 element gains a synthesized one,
    because a v2 item with no <Task> is inert on Windows (WI-018).
    """
    v1 = GppScheduledTask(
        name="Legacy",
        element_variant="Task",
        program=r"c:\tool.exe",
        arguments="--flag",
    )
    parsed_v1 = parse_gpp_scheduled_tasks(serialize_gpp_scheduled_tasks((v1,), "computer"))[0]
    assert parsed_v1.task_xml == ""
    assert parsed_v1 == v1

    v2 = GppScheduledTask(name="Modern", program=r"c:\tool.exe", arguments="--flag")
    parsed_v2 = parse_gpp_scheduled_tasks(serialize_gpp_scheduled_tasks((v2,), "computer"))[0]
    assert parsed_v2.task_xml
    assert parsed_v2.program == "c:\\tool.exe"
    assert parsed_v2.arguments == "--flag"


def test_scheduled_task_variants_preserve_document_order() -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<ScheduledTasks>
  <Task name="legacy"><Properties name="legacy"/></Task>
  <TaskV2 name="modern"><Properties name="modern"/></TaskV2>
  <Task name="legacy-2"><Properties name="legacy-2"/></Task>
</ScheduledTasks>"""
    parsed = parse_gpp_scheduled_tasks(xml)
    assert [item.name for item in parsed] == ["legacy", "modern", "legacy-2"]
    assert [item.element_variant for item in parsed] == ["Task", "TaskV2", "Task"]


# ---------------------------------------------------------------------------
# Collection integration for privileged execution adapters
# ---------------------------------------------------------------------------


def test_gpp_collection_with_privileged_adapters() -> None:
    """Collection with all privileged execution adapter types round-trips."""
    col = GppCollection(
        scope="computer",
        services=(GppService(service_name="Spooler", startup_type="disabled"),),
        local_users=(GppLocalUser(user_name="DbAdmin", account_disabled=True),),
        scheduled_tasks=(
            GppScheduledTask(name="Cleanup", program=r"\\server\cleanup.exe"),
        ),
        immediate_tasks=(
            GppImmediateTask(name="Ping", program=r"c:\ping.exe"),
        ),
    )
    files = serialize_gpp(col)
    assert "Services/Services.xml" in files
    assert "Groups/Groups.xml" in files
    assert "ScheduledTasks/ScheduledTasks.xml" in files

    # Verify that <User> inner elements are in the merged
    # Groups\Groups.xml file with the correct MS-GPPREF CLSIDs.
    groups_xml = files["Groups/Groups.xml"]
    assert b'clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"' in groups_xml
    assert b'clsid="{DF5F1855-51E5-4d24-8B1A-D9BDE98BA1D1}"' in groups_xml
    assert b"<User " in groups_xml

    parsed = parse_gpp_collection("computer", files)
    assert parsed.scope == "computer"
    assert len(parsed.services) == 1
    assert parsed.services[0] == col.services[0]
    assert len(parsed.local_users) == 1
    assert parsed.local_users[0] == col.local_users[0]
    assert len(parsed.local_groups) == 0
    assert len(parsed.scheduled_tasks) == 1
    # A TaskV2 authored without an explicit payload gains a synthesized one
    # (WI-018), so exact model equality no longer holds -- everything else must
    # still round-trip unchanged.
    round_tripped = parsed.scheduled_tasks[0]
    assert round_tripped.task_xml
    assert replace(round_tripped, task_xml="") == col.scheduled_tasks[0]
    assert len(parsed.immediate_tasks) == 1
    assert parsed.immediate_tasks[0] == col.immediate_tasks[0]


def test_deprecated_local_groups_cannot_be_silently_dropped() -> None:
    col = GppCollection(
        scope="computer",
        local_groups=(GppLocalGroup(group_name="Administrators"),),
    )
    with pytest.raises(GppError, match="canonical groups field"):
        serialize_gpp(col)


def test_legacy_local_groups_dict_migrates_to_canonical_groups() -> None:
    restored = gpp_collection_from_dict({
        "scope": "computer",
        "local_groups": [
            {
                "group_name": "Administrators",
                "description": "Migrated",
                "delete_all_users": True,
                "members": [
                    {"name": r"SYNTHETIC\User", "sid": "S-1-5-32-545", "action": "add"}
                ],
            }
        ],
    })
    assert restored.local_groups == ()
    assert len(restored.groups) == 1
    assert restored.groups[0].name == "Administrators"
    assert restored.groups[0].remove_all_users is True
    assert restored.groups[0].members[0].name == r"SYNTHETIC\User"


def test_legacy_scheduled_task_dict_defaults_to_task_variant() -> None:
    restored = gpp_collection_from_dict({
        "scope": "computer",
        "scheduled_tasks": [{"name": "Legacy"}],
    })
    assert restored.scheduled_tasks[0].element_variant == "Task"


def test_gpp_collection_dict_roundtrip_privileged_adapters() -> None:
    """Dict round-trip preserves all privileged execution adapter items."""
    col = GppCollection(
        scope="user",
        services=(GppService(service_name="Spooler", timeout_seconds=45),),
        local_users=(GppLocalUser(user_name="DbAdmin", password_never_expires=True),),
        scheduled_tasks=(GppScheduledTask(name="Cleanup", trigger_type="weekly"),),
        immediate_tasks=(GppImmediateTask(name="Ping", program="c:\\ping.exe"),),
    )
    d = gpp_collection_to_dict(col)
    restored = gpp_collection_from_dict(d)
    assert restored.scope == col.scope
    assert len(restored.services) == 1
    assert restored.services[0] == col.services[0]
    assert len(restored.local_users) == 1
    assert restored.local_users[0] == col.local_users[0]
    assert len(restored.scheduled_tasks) == 1
    assert restored.scheduled_tasks[0] == col.scheduled_tasks[0]
    assert len(restored.immediate_tasks) == 1
    assert restored.immediate_tasks[0] == col.immediate_tasks[0]


def test_gpp_collection_empty_privileged_adapters_not_serialized() -> None:
    """Empty privileged adapter sections don't produce files."""
    col = GppCollection(scope="computer")
    files = serialize_gpp(col)
    assert "Services/Services.xml" not in files
    assert "Groups/Groups.xml" not in files
    assert "ScheduledTasks/ScheduledTasks.xml" not in files


def test_gpp_collection_backward_compat_without_privileged_adapters() -> None:
    """Dicts without privileged adapter keys produce empty tuples."""
    d = {
        "scope": "computer",
        "groups": [],
        "registry": [],
    }
    restored = gpp_collection_from_dict(d)
    assert restored.services == ()
    assert restored.local_users == ()
    assert restored.local_groups == ()
    assert restored.scheduled_tasks == ()
    assert restored.immediate_tasks == ()


def test_ensure_editor_ids_assigns_ids_to_privileged_adapters() -> None:
    """ensure_editor_ids assigns UUIDs to empty-id privileged adapter items."""
    col = GppCollection(
        scope="computer",
        services=(GppService(service_name="S"),),
        local_users=(GppLocalUser(user_name="U"),),
        scheduled_tasks=(GppScheduledTask(name="T"),),
        immediate_tasks=(GppImmediateTask(name="I"),),
    )
    result = ensure_editor_ids(col)
    assert result.services[0].id != ""
    assert result.local_users[0].id != ""
    assert result.scheduled_tasks[0].id != ""
    assert result.immediate_tasks[0].id != ""


def test_ensure_editor_ids_preserves_privileged_adapter_ids() -> None:
    col = GppCollection(
        scope="computer",
        services=(GppService(service_name="S", id="svc-1"),),
    )
    result = ensure_editor_ids(col)
    assert result.services[0].id == "svc-1"


# ---------------------------------------------------------------------------
# MS-GPPREF CLSID conformance
# ---------------------------------------------------------------------------

def test_clsids_match_ms_gppref_spec() -> None:
    """Verify all adapter CLSID constants match the authoritative MS-GPPREF
    table (https://learn.microsoft.com/en-us/openspecs/windows_protocols/
    ms-gppref/12512ed6-0632-4e90-a112-d3d2cd41df6c).
    """
    from gpo_studio.gpp_adapters import (
        _ADAPTER_META,
        _APPLICATION_CLSID,
        _APPLICATIONS_CLSID,
        _DATA_SOURCE_CLSID,
        _DATA_SOURCES_CLSID,
        _DEVICE_CLSID,
        _DEVICES_CLSID,
        _DRIVE_CLSID,
        _DRIVES_CLSID,
        _ENV_VAR_CLSID,
        _ENV_VARS_CLSID,
        _FILE_CLSID,
        _FILES_CLSID,
        _FOLDER_CLSID,
        _FOLDER_OPTIONS_CLSID,
        _FOLDERS_CLSID,
        _GLOBAL_FOLDER_OPTIONS_VISTA_CLSID,
        _GROUP_CLSID,
        _GROUPS_CLSID,
        _IMMEDIATE_TASK_V2_CLSID,
        _INI_CLSID,
        _INI_FILES_CLSID,
        _NET_SHARE_CLSID,
        _NETWORK_SHARES_CLSID,
        _NT_SERVICE_CLSID,
        _NT_SERVICES_CLSID,
        _POWER_OPTIONS_CLSID,
        _POWER_SCHEME_CLSID,
        _PRINTER_CLSID,
        _PRINTERS_CLSID,
        _REGIONAL_CLSID,
        _REGIONAL_OPTIONS_CLSID,
        _SCHEDULED_TASKS_CLSID,
        _SHORTCUT_CLSID,
        _SHORTCUTS_CLSID,
        _TASK_CLSID,
        _USER_CLSID,
    )

    # Outer (root) CLSIDs
    assert _APPLICATIONS_CLSID == "{16DB8EC4-EBFC-4958-98EE-712E9DD3A966}"
    assert _DATA_SOURCES_CLSID == "{380F820F-F21B-41ac-A3CC-24D4F80F067B}"
    assert _DEVICES_CLSID == "{4DD26924-3F32-47aa-BF33-36D51BD1E54E}"
    assert _DRIVES_CLSID == "{8FDDCC1A-0C3C-43cd-A6B4-71A6DF20DA8C}"
    assert _ENV_VARS_CLSID == "{BF141A63-327B-438a-B9BF-2C188F13B7AD}"
    assert _FILES_CLSID == "{215B2E53-57CE-475c-80FE-9EEC14635851}"
    assert _FOLDER_OPTIONS_CLSID == "{8AB5F5D7-F676-48ab-A94E-1186E120EFDC}"
    assert _FOLDERS_CLSID == "{77CC39E7-3D16-4f8f-AF86-EC0BBEE2C861}"
    assert _INI_FILES_CLSID == "{694C651A-08F2-47fa-A427-34C4F62BA207}"
    assert _GROUPS_CLSID == "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
    assert _NETWORK_SHARES_CLSID == "{520870D8-A6E7-47e8-A8D8-E6A4E76EAEC2}"
    assert _POWER_OPTIONS_CLSID == "{7B0F9381-C3B8-4525-8167-87349B671D94}"
    assert _PRINTERS_CLSID == "{1F577D12-3D1B-471e-A1B7-060317597B9C}"
    assert _REGIONAL_CLSID == "{BDBA23C2-DE02-434e-8D89-13E53CB6710B}"
    assert _SCHEDULED_TASKS_CLSID == "{CC63F200-7309-4ba0-B154-A71CD118DBCC}"
    assert _NT_SERVICES_CLSID == "{2CFB484A-4E96-4b5d-A0B6-093D2F91E6AE}"
    assert _SHORTCUTS_CLSID == "{872ECB34-B2EC-401b-A585-D32574AA90EE}"

    # Inner (item) CLSIDs
    assert _APPLICATION_CLSID == "{C8535E2E-148D-494d-8E9A-71FC46649B5E}"
    assert _DATA_SOURCE_CLSID == "{5C209626-D820-4d69-8D50-1FACD6214488}"
    assert _DEVICE_CLSID == "{2E1C95D0-85FB-403a-A57C-A508854FB7C8}"
    assert _DRIVE_CLSID == "{935D1B74-9CB8-4e3c-9914-7DD559B7A417}"
    assert _ENV_VAR_CLSID == "{78570023-8373-4a19-BA80-2F150738EA19}"
    assert _FILE_CLSID == "{50BE44C8-567A-4ed1-B1D0-9234FE1F38AF}"
    assert _GLOBAL_FOLDER_OPTIONS_VISTA_CLSID == "{DBF1E3CD-4CA2-407c-BE84-5F67D3BE754D}"
    assert _FOLDER_CLSID == "{07DA02F5-F9CD-4397-A550-4AE21B6B4BD3}"
    assert _INI_CLSID == "{EEFACE84-D3D8-4680-8D4B-BF103E759448}"
    assert _USER_CLSID == "{DF5F1855-51E5-4d24-8B1A-D9BDE98BA1D1}"
    assert _GROUP_CLSID == "{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}"
    assert _NET_SHARE_CLSID == "{2888C5E7-94FC-4739-90AA-2C1536D68BC0}"
    assert _POWER_SCHEME_CLSID == "{DE828AFA-7E71-480e-8081-5447CBE87754}"
    assert _PRINTER_CLSID == "{9A5E9697-9095-436d-A0EE-4D128FDFBCE5}"
    assert _REGIONAL_OPTIONS_CLSID == "{C126A328-BECF-4acc-BA8D-C9C7F6B84E49}"
    assert _TASK_CLSID == "{2DEECB1C-261F-4e13-9B21-16FB83BC03BD}"
    assert _IMMEDIATE_TASK_V2_CLSID == "{9756B581-76EC-4169-9AFC-0CA8D43ADB5F}"
    assert _NT_SERVICE_CLSID == "{AB6F0B67-341F-4e51-92F9-005FBFBA1A43}"
    assert _SHORTCUT_CLSID == "{4F2F7C55-2790-433e-8127-0739D1CFA327}"

    # Verify _ADAPTER_META consistency: root and item CLSIDs match the
    # constants, and element names match the MS-GPPREF spec.
    expected_meta: dict[str, tuple[str, str, str, str]] = {
        "environment": (
            "EnvironmentVariables", _ENV_VARS_CLSID,
            "EnvironmentVariable", _ENV_VAR_CLSID,
        ),
        "ini_files": ("IniFiles", _INI_FILES_CLSID, "Ini", _INI_CLSID),
        "regional_options": (
            "Regional", _REGIONAL_CLSID,
            "RegionalOptions", _REGIONAL_OPTIONS_CLSID,
        ),
        "power_options": (
            "PowerOptions", _POWER_OPTIONS_CLSID,
            "PowerScheme", _POWER_SCHEME_CLSID,
        ),
        "devices": ("Devices", _DEVICES_CLSID, "Device", _DEVICE_CLSID),
        "folder_options": (
            "FolderOptions", _FOLDER_OPTIONS_CLSID,
            "GlobalFolderOptionsVista", _GLOBAL_FOLDER_OPTIONS_VISTA_CLSID,
        ),
        "data_sources": (
            "DataSources", _DATA_SOURCES_CLSID,
            "DataSource", _DATA_SOURCE_CLSID,
        ),
        "drives": ("Drives", _DRIVES_CLSID, "Drive", _DRIVE_CLSID),
        "files": ("Files", _FILES_CLSID, "File", _FILE_CLSID),
        "folders": ("Folders", _FOLDERS_CLSID, "Folder", _FOLDER_CLSID),
        "network_shares": (
            "NetworkShareSettings", _NETWORK_SHARES_CLSID,
            "NetShare", _NET_SHARE_CLSID,
        ),
        "printers": (
            "Printers", _PRINTERS_CLSID,
            "SharedPrinter", _PRINTER_CLSID,
        ),
        "shortcuts": (
            "Shortcuts", _SHORTCUTS_CLSID,
            "Shortcut", _SHORTCUT_CLSID,
        ),
        "applications": (
            "Applications", _APPLICATIONS_CLSID,
            "Application", _APPLICATION_CLSID,
        ),
        "services": (
            "NTServices", _NT_SERVICES_CLSID,
            "NTService", _NT_SERVICE_CLSID,
        ),
        "local_users": ("Groups", _GROUPS_CLSID, "User", _USER_CLSID),
        "local_groups": ("Groups", _GROUPS_CLSID, "Group", _GROUP_CLSID),
        "scheduled_tasks": (
            "ScheduledTasks", _SCHEDULED_TASKS_CLSID,
            "Task", _TASK_CLSID,
        ),
        "immediate_tasks": (
            "ScheduledTasks", _SCHEDULED_TASKS_CLSID,
            "ImmediateTaskV2", _IMMEDIATE_TASK_V2_CLSID,
        ),
    }
    for key, expected in expected_meta.items():
        assert _ADAPTER_META[key] == expected, (
            f"_ADAPTER_META[{key!r}] mismatch: "
            f"expected {expected}, got {_ADAPTER_META[key]}"
        )


# ---------------------------------------------------------------------------
# Unknown Properties children round-trip
# ---------------------------------------------------------------------------


def test_environment_unknown_props_children_roundtrip() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<EnvironmentVariables clsid="{BF141A63-327B-438a-B9BF-2C188F13B7AD}">'
        b'<EnvironmentVariable clsid="{78570023-8373-4a19-BA80-2F150738EA19}"'
        b' name="VAR">'
        b'<Properties action="U" name="VAR" value="1" user="0">'
        b"<VendorExt>data</VendorExt>"
        b"</Properties>"
        b"</EnvironmentVariable>"
        b"</EnvironmentVariables>"
    )
    parsed = parse_gpp_environment(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_props_children) == 1
    assert "VendorExt" in parsed[0].unknown_props_children[0]
    data = serialize_gpp_environment(parsed, "computer")
    assert b"VendorExt" in data
    reparsed = parse_gpp_environment(data)
    assert reparsed[0].unknown_props_children == parsed[0].unknown_props_children


def test_scheduled_task_unknown_props_children_roundtrip() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<ScheduledTasks clsid="{CC63F200-7309-4ba0-B154-A71CD118DBCC}">'
        b'<TaskV2 clsid="{D8896631-B747-47a7-84A6-C155337F3BC8}" name="T">'
        b'<Properties action="U" name="T" runAs="" program="cmd.exe"'
        b' arguments="" startIn="" enabled="1" triggerType="ONCE"'
        b' triggerTime="" triggerDays="">'
        b"<VendorExt>ext</VendorExt>"
        b"</Properties>"
        b"</TaskV2>"
        b"</ScheduledTasks>"
    )
    parsed = parse_gpp_scheduled_tasks(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_props_children) == 1
    assert "VendorExt" in parsed[0].unknown_props_children[0]
    data = serialize_gpp_scheduled_tasks(parsed, "computer")
    assert b"VendorExt" in data


def test_local_group_unknown_props_children_roundtrip() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G">'
        b'<Properties action="U" groupName="G" description=""'
        b' deleteAllUsers="0" deleteAllGroups="0">'
        b"<VendorExt>ext</VendorExt>"
        b"</Properties>"
        b"</Group>"
        b"</Groups>"
    )
    parsed = parse_gpp_local_groups(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_props_children) == 1
    assert "VendorExt" in parsed[0].unknown_props_children[0]
    data = serialize_gpp_local_groups(parsed, "computer")
    assert b"VendorExt" in data


def test_gpp_group_unknown_props_children_roundtrip() -> None:
    from gpo_studio.gpp import parse_gpp_groups, serialize_gpp_groups

    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="Admins">'
        b'<Properties action="U" groupName="Admins" description=""'
        b' deleteAllUsers="0" deleteAllGroups="0">'
        b"<VendorExt>ext</VendorExt>"
        b"</Properties>"
        b"</Group>"
        b"</Groups>"
    )
    parsed = parse_gpp_groups(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_props_children) == 1
    assert "VendorExt" in parsed[0].unknown_props_children[0]
    collection = GppCollection(scope="computer", groups=parsed)
    data = serialize_gpp_groups(collection)
    assert b"VendorExt" in data
    reparsed = parse_gpp_groups(data)
    assert reparsed[0].unknown_props_children == parsed[0].unknown_props_children


def test_gpp_registry_unknown_props_children_roundtrip() -> None:
    from gpo_studio.gpp import (
        GppCollection,
        parse_gpp_registry,
        serialize_gpp_registry,
    )

    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
        b'<Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"'
        b' name="HKLM\\Software\\Test">'
        b'<Properties action="C" hive="HKEY_LOCAL_MACHINE"'
        b' key="Software\\Test" name="Val" type="REG_SZ" value="1">'
        b"<VendorExt>ext</VendorExt>"
        b"</Properties>"
        b"</Registry>"
        b"</RegistrySettings>"
    )
    parsed = parse_gpp_registry(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_props_children) == 1
    assert "VendorExt" in parsed[0].unknown_props_children[0]
    collection = GppCollection(scope="computer", registry=parsed)
    data = serialize_gpp_registry(collection)
    assert b"VendorExt" in data
    reparsed = parse_gpp_registry(data)
    assert reparsed[0].unknown_props_children == parsed[0].unknown_props_children


def test_unknown_props_children_dict_roundtrip() -> None:
    env = GppEnvironment(
        name="VAR",
        unknown_props_children=("<VendorExt>data</VendorExt>",),
    )
    collection = GppCollection(scope="computer", environment=(env,))
    d = gpp_collection_to_dict(collection)
    restored = gpp_collection_from_dict(d)
    assert restored.environment[0].unknown_props_children == env.unknown_props_children
