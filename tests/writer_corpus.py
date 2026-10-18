"""Every input the writers are run over when a test asks "what do they emit?".

The writer-derived completeness test (`tests/test_emit_integrity.py`) runs each
serializer over this corpus and requires every emitted name -- XML attribute,
element text, INF section and key, INI key, PowerShell parameter -- to be
classified in `gpo_studio.emit_integrity`. The corpus is the native captures,
the stored records `bd84b3a` wrote, and synthetic inputs that populate every
typed family, every targeting predicate and every common option, so a writer
gaining a field is seen even where no capture exercises it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from gpo_studio.gpp import (
    GppCollection,
    GppCommonOptions,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    model_only,
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
    GppLocalUser,
    GppNetworkShare,
    GppPowerOptions,
    GppPrinter,
    GppRegionalOptions,
    GppScheduledTask,
    GppService,
    GppShortcut,
)
from gpo_studio.ilt import IltFilter, IltOsCriteria, IltPredicate
from gpo_studio.import_export import collect_gpp_collections
from gpo_studio.store import gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOTS = (
    ROOT / "tests/fixtures/native-gpp-gpmc",
    ROOT / "tests/fixtures/native-gpp-registry-gpmc",
)
BASELINE = ROOT / "tests/fixtures/gpp-store-baseline-bd84b3a"

COMMON = GppCommonOptions(
    apply_once=True,
    remove_when_unapplied=True,
    user_security_context=True,
    disabled=True,
    stop_on_error=True,
)
#: One predicate of every type Studio types, with a value of the kind it holds.
PREDICATE_VALUES: dict[str, str] = {
    "ou": "OU=Lab,DC=example,DC=com",
    "group": "S-1-5-32-544",
    "registry": "HKLM\\Software\\Example\\Value",
    "ip_range": "10.0.0.0/8",
    "environment": "STAGE=lab",
    "wmi_query": "SELECT * FROM Win32_OperatingSystem",
    "computer_name": "LAB-WS01",
    "domain": "example.com",
    "user": "EXAMPLE\\user1",
    "date": "2026-01-01|2026-12-31",
    "disk_space": "10",
    "os": "",
    "language": "1033",
    "service": "Spooler",
    "file": "C:\\Example\\file.txt",
    "folder": "C:\\Example",
}
ILT = IltFilter(
    items=(
        *(
            IltPredicate(
                type=kind,  # type: ignore[arg-type]
                value=value,
                os_criteria=IltOsCriteria() if kind == "os" else None,
            )
            for kind, value in PREDICATE_VALUES.items()
        ),
        IltPredicate(type="group", value="EXAMPLE\\Lab Admins"),
    )
)


def synthetic_collections() -> tuple[GppCollection, ...]:
    """Every typed family, populated, in both scopes."""
    common = {"common": COMMON, "ilt_filter": ILT}
    items = {
        "groups": (
            GppGroup(
                name="Administrators",
                sid="S-1-5-32-544",
                description="Managed",
                members=(GppGroupMember(sid="S-1-5-21-1-2-3-500", name="EXAMPLE\\admin"),),
                **common,
            ),
        ),
        "registry": (
            GppRegistry(
                key="Software\\Example",
                value=GppRegistryValue(name="Value", value="data"),
                **common,
            ),
            GppRegistry(
                key="Software\\Example",
                value=GppRegistryValue(
                    name="Multi", value=["one", "two"], registry_type="REG_MULTI_SZ"
                ),
            ),
        ),
        "environment": (GppEnvironment(name="STAGE", value="lab", **common),),
        "ini_files": (
            GppIniFile(path="C:\\Example\\a.ini", section="S", property="P", value="V", **common),
        ),
        "regional_options": (GppRegionalOptions(user_locale="1033", **common),),
        "power_options": (
            GppPowerOptions(
                scheme_name="Balanced",
                scheme_guid="{381B4222-F694-41F0-9685-FF5BB260DF2E}",
                **common,
            ),
        ),
        "devices": (GppDevice(device_class="USB", device_name="Example device", **common),),
        "folder_options": (GppFolderOptions(**common),),
        "data_sources": (
            GppDataSource(dsn="Example", driver="SQL Server", description="d", **common),
        ),
        "drives": (GppDrive(letter="H", path="\\\\srv\\home", label="Home", **common),),
        "files": (GppFile(source="\\\\srv\\a.txt", target="C:\\a.txt", **common),),
        "folders": (GppFolder(path="C:\\Example", **common),),
        "network_shares": (
            GppNetworkShare(name="Share", path="C:\\Share", comment="c", user_limit=5, **common),
        ),
        "printers": (GppPrinter(path="\\\\print\\lab", comment="c", **common),),
        "shortcuts": (
            GppShortcut(
                name="Example",
                target_path="C:\\Example\\a.exe",
                arguments="/a",
                start_in="C:\\Example",
                icon_path="C:\\Example\\a.ico",
                shortcut_path="%DesktopDir%\\Example",
                **common,
            ),
        ),
        "applications": (
            GppApplication(
                name="App", path="C:\\a.msi", command_line="/q", run_as="EXAMPLE\\svc", **common
            ),
        ),
        "services": (
            GppService(
                service_name="Spooler",
                first_failure="restart",
                second_failure="restart",
                third_failure="restart",
                reset_fail_count_delay_seconds=86400,
                restart_service_delay_milliseconds=60000,
                restart_computer_delay_milliseconds=60000,
                restart_message="Restarting",
                program="C:\\Windows\\System32\\cmd.exe",
                arguments="/c exit 0",
                account_name="LocalSystem",
                **common,
            ),
        ),
        "local_users": (
            GppLocalUser(user_name="example", full_name="Example", description="d", **common),
        ),
        "scheduled_tasks": (
            GppScheduledTask(
                name="Task v1",
                run_as="EXAMPLE\\svc",
                program="cmd.exe",
                arguments="/c",
                trigger_time="02:00",
                element_variant="Task",
                **common,
            ),
            GppScheduledTask(
                name="Task v2",
                run_as="EXAMPLE\\svc",
                program="cmd.exe",
                arguments="/c",
                trigger_time="2026-01-01T02:00:00",
                **common,
            ),
        ),
        "immediate_tasks": (
            GppImmediateTask(name="Now", run_as="EXAMPLE\\svc", program="cmd.exe", **common),
        ),
    }
    return tuple(
        GppCollection(scope=scope, **items)  # type: ignore[arg-type]
        for scope in ("computer", "user")
    )


def capture_collections() -> Iterator[tuple[str, GppCollection]]:
    for root in CAPTURE_ROOTS:
        for gpo_dir in sorted(root.glob("*/*/DomainSysvol/GPO")):
            for collection in collect_gpp_collections(gpo_dir):
                yield gpo_dir.parents[2].name, collection


def baseline_collections() -> Iterator[tuple[str, GppCollection]]:
    for record in sorted(BASELINE.glob("*.json")):
        gpo = gpo_from_dict(json.loads(record.read_text(encoding="utf-8"))["gpo"])
        for collection in gpo.gpp_collections:
            yield record.stem, collection


def gpp_corpus() -> Iterator[tuple[str, GppCollection]]:
    """(source, collection): each as imported and as the typed model writes it."""
    for source, collection in (*capture_collections(), *baseline_collections()):
        yield source, collection
        yield f"{source} (model only)", model_only(collection)
    for index, collection in enumerate(synthetic_collections()):
        yield f"synthetic {index}", collection
