"""Tests for object-level security families: groups, services, registry, files."""

from __future__ import annotations

from gpo_studio.object_security import (
    FileSecurity,
    FileSystemSecurityFamily,
    RegistryKeySecurity,
    RegistrySecurityFamily,
    RestrictedGroup,
    RestrictedGroupMember,
    RestrictedGroupsFamily,
    ServiceSecurity,
    SystemServicesFamily,
    assess_blast_radius,
)
from gpo_studio.security_template import (
    InfSection,
    SecurityTemplate,
    decode_security_template,
    encode_security_template,
    format_security_template,
    parse_security_template,
)

_ADMIN = "S-1-5-32-544"
_USERS = "S-1-5-32-545"
_SYSTEM = "S-1-5-18"
_BACKUP = "S-1-5-32-551"
_DOMAIN_ADMINS = "S-1-5-21-1-2-3-512"
_SDDL_ADMIN = "D:(A;;CC;;;S-1-5-32-544)"
_SDDL_SYSTEM_ADMIN = f"O:{_SYSTEM}D:(A;;CC;;;{_SYSTEM})(A;;CC;;;{_ADMIN})"

# ---------------------------------------------------------------------------
# Restricted Groups
# ---------------------------------------------------------------------------


def test_restricted_groups_from_template_round_trip() -> None:
    text = f"""\
[Group Membership]
{_ADMIN}__Members = *{_USERS},*{_SYSTEM}
{_ADMIN}__Memberof = *{_DOMAIN_ADMINS}
{_BACKUP}__Members = *{_SYSTEM}
"""
    template = parse_security_template(text)
    family = RestrictedGroupsFamily.from_template(template)
    assert len(family.groups) == 2

    admins = family.get_group(_ADMIN)
    assert admins is not None
    assert tuple(m.sid for m in admins.members) == (_USERS, _SYSTEM)
    assert tuple(m.sid for m in admins.member_of) == (_DOMAIN_ADMINS,)

    backup = family.get_group(_BACKUP)
    assert backup is not None
    assert tuple(m.sid for m in backup.members) == (_SYSTEM,)
    assert backup.member_of == ()

    # Round-trip: serialize → re-parse → compare
    entries = family.to_template_entries()
    assert "Group Membership" in entries
    rebuilt_text = _entries_to_text(entries)
    rebuilt_template = parse_security_template(rebuilt_text)
    rebuilt_family = RestrictedGroupsFamily.from_template(rebuilt_template)
    assert rebuilt_family == family


def test_restricted_groups_get_group_missing() -> None:
    family = RestrictedGroupsFamily(
        groups=(RestrictedGroup(group_sid=_ADMIN),)
    )
    assert family.get_group(_ADMIN) is not None
    assert family.get_group("S-1-5-32-999") is None


def test_restricted_groups_empty_group_warning() -> None:
    family = RestrictedGroupsFamily(
        groups=(RestrictedGroup(group_sid=_ADMIN, members=(), member_of=()),)
    )
    issues = family.validate()
    assert any(i.code == "empty_restricted_group" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "empty_restricted_group")


def test_restricted_groups_deleted_sid_warning() -> None:
    family = RestrictedGroupsFamily(
        groups=(
            RestrictedGroup(
                group_sid=_ADMIN,
                members=(RestrictedGroupMember(sid="S-1-0-0"),),
                member_of=(RestrictedGroupMember(sid="S-1-5-7"),),
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "deleted_sid_in_group" for i in issues)
    assert any(i.code == "deleted_sid_in_memberof" for i in issues)


def test_restricted_groups_star_prefixed_key() -> None:
    """Real templates prefix the group SID with ``*`` in the key."""
    text = f"""\
[Group Membership]
*{_ADMIN}__Members = *{_USERS}
"""
    template = parse_security_template(text)
    family = RestrictedGroupsFamily.from_template(template)
    admins = family.get_group(_ADMIN)
    assert admins is not None
    assert tuple(m.sid for m in admins.members) == (_USERS,)


def test_restricted_groups_empty_family_entries() -> None:
    family = RestrictedGroupsFamily()
    assert family.to_template_entries() == {}


# ---------------------------------------------------------------------------
# System Services
# ---------------------------------------------------------------------------


def test_system_services_from_template_with_sddl() -> None:
    text = f"""\
[Service General Setting]
Spooler = 2,"{_SDDL_ADMIN}"
wuauserv = 4,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    family = SystemServicesFamily.from_template(template)
    assert len(family.services) == 2

    spooler = family.get_service("Spooler")
    assert spooler is not None
    assert spooler.startup_mode == "automatic"
    assert spooler.raw_sddl == _SDDL_ADMIN
    assert spooler.security_descriptor is not None
    assert spooler.security_descriptor.dacl is not None
    assert len(spooler.security_descriptor.dacl.aces) == 1

    wuauserv = family.get_service("wuauserv")
    assert wuauserv is not None
    assert wuauserv.startup_mode == "disabled"
    assert wuauserv.security_descriptor is not None
    assert wuauserv.security_descriptor.owner_sid == _SYSTEM


def test_system_services_startup_mode_mapping() -> None:
    text = """\
[Service General Setting]
svc_auto = 2,""
svc_manual = 3,""
svc_disabled = 4,""
svc_unknown = 7,""
"""
    template = parse_security_template(text)
    family = SystemServicesFamily.from_template(template)
    assert family.get_service("svc_auto").startup_mode == "automatic"
    assert family.get_service("svc_manual").startup_mode == "manual"
    assert family.get_service("svc_disabled").startup_mode == "disabled"
    assert family.get_service("svc_unknown").startup_mode is None


def test_service_general_setting_native_rows_are_read() -> None:
    # Row shape inferred for this section too; only [Registry Keys] is
    # per-key measured (R4).
    text = f"""\
[Service General Setting]
"Spooler",2,"{_SDDL_ADMIN}"
"""
    family = SystemServicesFamily.from_template(parse_security_template(text))
    spooler = family.get_service("Spooler")
    assert spooler is not None
    assert spooler.startup_mode == "automatic"
    assert spooler.raw_sddl == _SDDL_ADMIN


def test_system_services_round_trip() -> None:
    text = f"""\
[Service General Setting]
Spooler = 2,"{_SDDL_ADMIN}"
 BITS = 3,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    family = SystemServicesFamily.from_template(template)
    entries = family.to_template_entries()
    rebuilt_text = _entries_to_text(entries)
    rebuilt_family = SystemServicesFamily.from_template(
        parse_security_template(rebuilt_text)
    )
    assert len(rebuilt_family.services) == len(family.services)
    for orig, rebuilt in zip(family.services, rebuilt_family.services, strict=True):
        assert orig.service_name == rebuilt.service_name
        assert orig.startup_mode == rebuilt.startup_mode
        assert orig.raw_sddl == rebuilt.raw_sddl


def test_system_services_unparseable_sddl_error() -> None:
    text = """\
[Service General Setting]
BadSvc = 2,"NOT_VALID_SDDL"
"""
    template = parse_security_template(text)
    family = SystemServicesFamily.from_template(template)
    issues = family.validate()
    assert any(
        i.code == "unparseable_service_sddl" and i.severity == "error"
        for i in issues
    )


def test_system_services_empty_name_error() -> None:
    family = SystemServicesFamily(
        services=(ServiceSecurity(service_name=""),)
    )
    issues = family.validate()
    assert any(i.code == "empty_service_name" for i in issues)


def test_system_services_get_service_case_insensitive() -> None:
    family = SystemServicesFamily(
        services=(ServiceSecurity(service_name="Spooler"),)
    )
    assert family.get_service("spooler") is not None
    assert family.get_service("SPOOLER") is not None


# ---------------------------------------------------------------------------
# Registry Security
# ---------------------------------------------------------------------------


def test_registry_security_from_template() -> None:
    text = f"""\
[Registry Keys]
"MACHINE\\Software\\Policies" = 1,"{_SDDL_ADMIN}"
MACHINE\\Software\\Test = 0,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    family = RegistrySecurityFamily.from_template(template)
    assert len(family.keys) == 2

    key0 = family.keys[0]
    assert key0.key_path == r"MACHINE\Software\Policies"
    assert key0.propagation == "do_not_allow_replace"
    assert key0.security_descriptor is not None

    key1 = family.keys[1]
    assert key1.key_path == r"MACHINE\Software\Test"
    assert key1.propagation == "propagate"


def test_registry_security_native_rows_are_read() -> None:
    """Native ``[Registry Keys]`` rows are bare quoted-CSV lines, not ``=``.

    Measured on Windows Server 2025: native GptTmpl.inf carries
    ``"KEY",code,"SDDL"`` and the ``key = value`` shape appears nowhere (R4).
    These rows carry no ``=`` so the template parser preserves them in
    ``unknown_lines``; the family reader must find them there.
    """
    text = f"""\
[Registry Keys]
"MACHINE\\Software\\Alpha",0,"{_SDDL_ADMIN}"
"MACHINE\\Software\\Bravo",2,"{_SDDL_ADMIN}"
"""
    template = parse_security_template(text)
    family = RegistrySecurityFamily.from_template(template)
    assert len(family.keys) == 2
    assert family.keys[0].key_path == r"MACHINE\Software\Alpha"
    assert family.keys[0].propagation == "propagate"
    assert family.keys[0].raw_sddl == _SDDL_ADMIN
    assert family.keys[1].propagation == "replace"


def test_registry_security_measured_propagation_codes() -> None:
    """The wire codes, as measured per authored key on Server 2025 (R4):
    0 = propagate, 1 = do-not-allow-replace, 2 = replace."""
    text = f"""\
[Registry Keys]
"Key0",0,"{_SDDL_ADMIN}"
"Key1",1,"{_SDDL_ADMIN}"
"Key2",2,"{_SDDL_ADMIN}"
"""
    template = parse_security_template(text)
    family = RegistrySecurityFamily.from_template(template)
    assert family.keys[0].propagation == "propagate"
    assert family.keys[1].propagation == "do_not_allow_replace"
    assert family.keys[2].propagation == "replace"

    # The writer side is the inverse mapping: each mode serializes back to its
    # measured code.
    entries = family.to_template_entries()
    values = list(entries["Registry Keys"].values())
    assert values == [
        f'0,"{_SDDL_ADMIN}"',
        f'1,"{_SDDL_ADMIN}"',
        f'2,"{_SDDL_ADMIN}"',
    ]


def test_registry_security_unknown_propagation_code_defaults_to_propagate() -> None:
    text = f"""\
[Registry Keys]
"Key7",7,"{_SDDL_ADMIN}"
"""
    family = RegistrySecurityFamily.from_template(parse_security_template(text))
    assert family.keys[0].propagation == "propagate"


def test_registry_security_replace_on_system_warning() -> None:
    family = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"MACHINE\SYSTEM\CurrentControlSet\Services\Test",
                propagation="replace",
            ),
        )
    )
    issues = family.validate()
    assert any(
        i.code == "replace_on_system_hive" and i.severity == "warning"
        for i in issues
    )


def test_registry_security_replace_on_hklm_system_warning() -> None:
    family = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"HKLM\SYSTEM\Setup",
                propagation="replace",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "replace_on_system_hive" for i in issues)


def test_registry_security_no_warning_for_propagate_on_system() -> None:
    family = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"MACHINE\SYSTEM\CurrentControlSet",
                propagation="propagate",
            ),
        )
    )
    issues = family.validate()
    assert not any(i.code == "replace_on_system_hive" for i in issues)


def test_registry_security_empty_key_error() -> None:
    family = RegistrySecurityFamily(
        keys=(RegistryKeySecurity(key_path="   "),)
    )
    issues = family.validate()
    assert any(
        i.code == "empty_registry_key" and i.severity == "error" for i in issues
    )


def test_registry_security_round_trip() -> None:
    text = f"""\
[Registry Keys]
"MACHINE\\Software\\Test" = 1,"{_SDDL_ADMIN}"
"MACHINE\\Software\\Other" = 2,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    family = RegistrySecurityFamily.from_template(template)
    entries = family.to_template_entries()
    rebuilt_text = _entries_to_text(entries)
    rebuilt_family = RegistrySecurityFamily.from_template(
        parse_security_template(rebuilt_text)
    )
    assert len(rebuilt_family.keys) == len(family.keys)
    for orig, rebuilt in zip(family.keys, rebuilt_family.keys, strict=True):
        assert orig.key_path == rebuilt.key_path
        assert orig.propagation == rebuilt.propagation
        assert orig.raw_sddl == rebuilt.raw_sddl


def test_registry_security_writer_emits_native_quoted_csv_rows() -> None:
    """The writer path emits the row shape secedit accepts (R9).

    ``secedit /validate`` (Server 2025) rejects ``key = value`` rows in
    ``[Registry Keys]`` ("must have 3 fields each line") and accepts the
    native bare quoted-CSV row ``"KEY",code,"SDDL"``.  Row emission lives in
    ``format_security_template``; the ``[Version]`` preamble secedit requires
    before it parses rows at all is the template-level caller's job -- the
    family emits only its own section.
    """
    family = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"MACHINE\SOFTWARE\StudioLab\Audit",
                raw_sddl="D:PAR(A;OICI;FA;;;BA)",
                propagation="replace",
            ),
        )
    )
    text = _entries_to_text(family.to_template_entries())

    expected_row = '"MACHINE\\SOFTWARE\\StudioLab\\Audit",2,"D:PAR(A;OICI;FA;;;BA)"'
    assert f"[Registry Keys]\n{expected_row}" in text
    body = text.split("[Registry Keys]\n", 1)[1]
    assert "=" not in body, "emitted [Registry Keys] rows must be bare CSV, not key=value"

    # Completed with the caller-owned preamble, this is exactly the shape the
    # secedit-validated native candidate carries.
    candidate = (
        "[Unicode]\n"
        "Unicode=yes\n"
        "\n"
        "[Version]\n"
        'signature="$CHICAGO$"\n'
        "Revision=1\n"
        "\n"
        + text
    )
    assert expected_row in candidate

    # And the emitted shape reads back into the family.
    rebuilt = RegistrySecurityFamily.from_template(parse_security_template(candidate))
    assert len(rebuilt.keys) == 1
    assert rebuilt.keys[0].key_path == r"MACHINE\SOFTWARE\StudioLab\Audit"
    assert rebuilt.keys[0].propagation == "replace"
    assert rebuilt.keys[0].raw_sddl == "D:PAR(A;OICI;FA;;;BA)"

    # The template writer owns the wire encoding (UTF-16LE BOM + CRLF).
    encoded = encode_security_template(candidate)
    assert encoded.startswith(b"\xff\xfe")
    assert decode_security_template(encoded) == candidate.replace("\n", "\r\n")


# ---------------------------------------------------------------------------
# File Security
# ---------------------------------------------------------------------------


def test_file_security_from_template() -> None:
    text = f"""\
[File Security]
"%SystemRoot%\\System32\\test.dll" = 1,"{_SDDL_SYSTEM_ADMIN}"
"C:\\Data\\file.txt" = 0,"{_SDDL_ADMIN}"
"""
    template = parse_security_template(text)
    family = FileSystemSecurityFamily.from_template(template)
    assert len(family.files) == 2

    f0 = family.files[0]
    assert f0.file_path == r"%SystemRoot%\System32\test.dll"
    assert f0.propagation == "do_not_allow_replace"
    assert f0.security_descriptor is not None

    f1 = family.files[1]
    assert f1.file_path == r"C:\Data\file.txt"
    assert f1.propagation == "propagate"


def test_file_security_native_rows_are_read() -> None:
    # [File Security] shares the quoted-CSV row family.  Its row shape is
    # inferred -- same writer path and same secedit three-field arity as
    # [Registry Keys] -- not per-key measured like [Registry Keys] (R4).
    text = f"""\
[File Security]
"%SystemRoot%\\System32\\a.dll",2,"{_SDDL_ADMIN}"
"""
    family = FileSystemSecurityFamily.from_template(parse_security_template(text))
    assert family.files[0].file_path == r"%SystemRoot%\System32\a.dll"
    assert family.files[0].propagation == "replace"
    assert family.files[0].raw_sddl == _SDDL_ADMIN


def test_file_security_path_traversal_error() -> None:
    family = FileSystemSecurityFamily(
        files=(
            FileSecurity(file_path=r"C:\..\..\Windows\system32\config\SAM"),
        )
    )
    issues = family.validate()
    assert any(
        i.code == "path_traversal" and i.severity == "error" for i in issues
    )


def test_file_security_systemroot_replace_warning() -> None:
    family = FileSystemSecurityFamily(
        files=(
            FileSecurity(
                file_path=r"%SystemRoot%\System32\cmd.exe",
                propagation="replace",
            ),
        )
    )
    issues = family.validate()
    assert any(
        i.code == "replace_on_system_root" and i.severity == "warning"
        for i in issues
    )


def test_file_security_case_insensitive_systemroot_warning() -> None:
    family = FileSystemSecurityFamily(
        files=(
            FileSecurity(
                file_path=r"%systemroot%\explorer.exe",
                propagation="replace",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "replace_on_system_root" for i in issues)


def test_file_security_no_warning_for_propagate() -> None:
    family = FileSystemSecurityFamily(
        files=(
            FileSecurity(
                file_path=r"%SystemRoot%\System32\cmd.exe",
                propagation="propagate",
            ),
        )
    )
    issues = family.validate()
    assert not any(i.code == "replace_on_system_root" for i in issues)


def test_file_security_empty_path_error() -> None:
    family = FileSystemSecurityFamily(
        files=(FileSecurity(file_path="   "),)
    )
    issues = family.validate()
    assert any(
        i.code == "empty_file_path" and i.severity == "error" for i in issues
    )


def test_file_security_round_trip() -> None:
    text = f"""\
[File Security]
"%SystemRoot%\\test.dll" = 1,"{_SDDL_ADMIN}"
"C:\\Data\\file.txt" = 2,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    family = FileSystemSecurityFamily.from_template(template)
    entries = family.to_template_entries()
    rebuilt_text = _entries_to_text(entries)
    rebuilt_family = FileSystemSecurityFamily.from_template(
        parse_security_template(rebuilt_text)
    )
    assert len(rebuilt_family.files) == len(family.files)
    for orig, rebuilt in zip(family.files, rebuilt_family.files, strict=True):
        assert orig.file_path == rebuilt.file_path
        assert orig.propagation == rebuilt.propagation
        assert orig.raw_sddl == rebuilt.raw_sddl


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------


def test_blast_radius_mixed_families() -> None:
    services = SystemServicesFamily(
        services=(
            ServiceSecurity(
                service_name="WinDefend",
                startup_mode="disabled",
                raw_sddl=_SDDL_ADMIN,
                security_descriptor=None,
            ),
            ServiceSecurity(service_name="Spooler", startup_mode="automatic"),
        )
    )
    registry_keys = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"MACHINE\SYSTEM\CurrentControlSet\Services\BadSvc",
                propagation="replace",
            ),
            RegistryKeySecurity(
                key_path=r"MACHINE\Software\MyApp",
                propagation="propagate",
            ),
        )
    )
    file_security = FileSystemSecurityFamily(
        files=(
            FileSecurity(
                file_path=r"%SystemRoot%\System32\cmd.exe",
                propagation="replace",
            ),
            FileSecurity(file_path=r"C:\App\data.bin", propagation="propagate"),
        )
    )
    restricted_groups = RestrictedGroupsFamily(
        groups=(
            RestrictedGroup(
                group_sid=_ADMIN,
                members=(RestrictedGroupMember(sid=_USERS),),
            ),
            RestrictedGroup(
                group_sid=_BACKUP,
                members=(RestrictedGroupMember(sid=_SYSTEM),),
            ),
            RestrictedGroup(group_sid="S-1-5-32-547"),  # Power Users
            RestrictedGroup(
                group_sid=_DOMAIN_ADMINS,
                members=(RestrictedGroupMember(sid=_USERS),),
            ),
        )
    )
    items = assess_blast_radius(
        services, registry_keys, file_security, restricted_groups
    )
    # 2 services + 2 registry + 2 files + 4 restricted groups = 10 items
    assert len(items) == 10

    by_target = {item.target: item for item in items}
    # WinDefend disabled → critical
    assert by_target["WinDefend"].risk_level == "critical"
    assert by_target["WinDefend"].category == "service"
    # Spooler automatic, no ACL → low
    assert by_target["Spooler"].risk_level == "low"
    # Replace on MACHINE\SYSTEM → critical
    system_key = r"MACHINE\SYSTEM\CurrentControlSet\Services\BadSvc"
    assert by_target[system_key].risk_level == "critical"
    assert by_target[system_key].category == "registry_key"
    # Propagate on MACHINE\Software → medium
    software_key = r"MACHINE\Software\MyApp"
    assert by_target[software_key].risk_level == "medium"
    # Replace on %SystemRoot%\System32 → critical
    cmd_path = r"%SystemRoot%\System32\cmd.exe"
    assert by_target[cmd_path].risk_level == "critical"
    assert by_target[cmd_path].category == "file"
    # Propagate on C:\App → low
    assert by_target[r"C:\App\data.bin"].risk_level == "low"
    # BUILTIN\Administrators group → high
    assert by_target[_ADMIN].risk_level == "high"
    assert by_target[_ADMIN].category == "restricted_group"
    # Backup Operators → high
    assert by_target[_BACKUP].risk_level == "high"
    # Power Users → medium
    assert by_target["S-1-5-32-547"].risk_level == "medium"
    # Domain Admins → critical
    assert by_target[_DOMAIN_ADMINS].risk_level == "critical"


def test_blast_radius_empty_families() -> None:
    items = assess_blast_radius(
        SystemServicesFamily(),
        RegistrySecurityFamily(),
        FileSystemSecurityFamily(),
        RestrictedGroupsFamily(),
    )
    assert items == ()


def test_blast_radius_service_risk_levels() -> None:
    """Verify each risk level for services."""
    # Manual critical → high
    svc = ServiceSecurity(service_name="wuauserv", startup_mode="manual")
    family = SystemServicesFamily(services=(svc,))
    items = assess_blast_radius(family, RegistrySecurityFamily(),
                                 FileSystemSecurityFamily(), RestrictedGroupsFamily())
    assert items[0].risk_level == "high"

    # Disabled non-critical → medium
    svc2 = ServiceSecurity(service_name="Spooler", startup_mode="disabled")
    family2 = SystemServicesFamily(services=(svc2,))
    items2 = assess_blast_radius(family2, RegistrySecurityFamily(),
                                  FileSystemSecurityFamily(), RestrictedGroupsFamily())
    assert items2[0].risk_level == "medium"

    # ACL on critical service, no startup change → high
    from gpo_studio.sddl import Acl, SecurityDescriptor
    sd = SecurityDescriptor(owner_sid=_SYSTEM, group_sid=_SYSTEM, dacl=Acl(aces=()), sacl=None)
    svc3 = ServiceSecurity(service_name="WinDefend", security_descriptor=sd)
    family3 = SystemServicesFamily(services=(svc3,))
    items3 = assess_blast_radius(family3, RegistrySecurityFamily(),
                                  FileSystemSecurityFamily(), RestrictedGroupsFamily())
    assert items3[0].risk_level == "high"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_integration_full_round_trip() -> None:
    text = f"""\
[Version]
signature="$CHICAGO$"

[Group Membership]
{_ADMIN}__Members = *{_USERS},*{_SYSTEM}
{_ADMIN}__Memberof = *{_DOMAIN_ADMINS}
{_BACKUP}__Members = *{_SYSTEM}

[Service General Setting]
Spooler = 2,"{_SDDL_ADMIN}"
wuauserv = 4,"{_SDDL_SYSTEM_ADMIN}"

[Registry Keys]
"MACHINE\\Software\\Policies" = 1,"{_SDDL_ADMIN}"
"MACHINE\\SYSTEM\\Test" = 2,"{_SDDL_SYSTEM_ADMIN}"

[File Security]
"%SystemRoot%\\System32\\test.dll" = 1,"{_SDDL_SYSTEM_ADMIN}"
"C:\\Data\\file.txt" = 0,"{_SDDL_ADMIN}"
"""
    template = parse_security_template(text)

    # Extract all families
    groups = RestrictedGroupsFamily.from_template(template)
    services = SystemServicesFamily.from_template(template)
    registry = RegistrySecurityFamily.from_template(template)
    files = FileSystemSecurityFamily.from_template(template)

    # Serialize all families back to entries
    merged: dict[str, dict[str, str]] = {}
    for part in (
        groups.to_template_entries(),
        services.to_template_entries(),
        registry.to_template_entries(),
        files.to_template_entries(),
    ):
        for section, entries in part.items():
            merged.setdefault(section, {}).update(entries)

    # Build a new template from the merged entries
    rebuilt_text = _entries_to_text(merged)
    rebuilt_template = parse_security_template(rebuilt_text)

    # Re-parse families from the rebuilt template
    rebuilt_groups = RestrictedGroupsFamily.from_template(rebuilt_template)
    rebuilt_services = SystemServicesFamily.from_template(rebuilt_template)
    rebuilt_registry = RegistrySecurityFamily.from_template(rebuilt_template)
    rebuilt_files = FileSystemSecurityFamily.from_template(rebuilt_template)

    # Verify round-trip fidelity
    assert rebuilt_groups == groups
    assert len(rebuilt_services.services) == len(services.services)
    for orig, rebuilt in zip(
        services.services, rebuilt_services.services, strict=True
    ):
        assert orig.service_name == rebuilt.service_name
        assert orig.startup_mode == rebuilt.startup_mode
        assert orig.raw_sddl == rebuilt.raw_sddl
    assert len(rebuilt_registry.keys) == len(registry.keys)
    for orig, rebuilt in zip(registry.keys, rebuilt_registry.keys, strict=True):
        assert orig.key_path == rebuilt.key_path
        assert orig.propagation == rebuilt.propagation
        assert orig.raw_sddl == rebuilt.raw_sddl
    assert len(rebuilt_files.files) == len(files.files)
    for orig, rebuilt in zip(files.files, rebuilt_files.files, strict=True):
        assert orig.file_path == rebuilt.file_path
        assert orig.propagation == rebuilt.propagation
        assert orig.raw_sddl == rebuilt.raw_sddl


def test_integration_blast_radius_from_template() -> None:
    text = f"""\
[Group Membership]
{_ADMIN}__Members = *{_USERS}

[Service General Setting]
wuauserv = 4,"{_SDDL_ADMIN}"

[Registry Keys]
"MACHINE\\SYSTEM\\Security" = 2,"{_SDDL_SYSTEM_ADMIN}"

[File Security]
"%SystemRoot%\\System32\\kernel32.dll" = 2,"{_SDDL_SYSTEM_ADMIN}"
"""
    template = parse_security_template(text)
    groups = RestrictedGroupsFamily.from_template(template)
    services = SystemServicesFamily.from_template(template)
    registry = RegistrySecurityFamily.from_template(template)
    files = FileSystemSecurityFamily.from_template(template)

    items = assess_blast_radius(services, registry, files, groups)
    assert len(items) == 4
    # All four should be high-or-worse risk
    for item in items:
        assert item.risk_level in ("high", "critical"), (
            f"{item.target} risk was {item.risk_level}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entries_to_text(entries: dict[str, dict[str, str]]) -> str:
    """Serialize family entries through the real INF writer path.

    Row-shaped sections (``[Registry Keys]``, ``[File Security]``,
    ``[Service General Setting]``) come out as bare quoted-CSV rows -- the
    native shape ``secedit`` accepts (R9) -- and the rest as ``key = value``.
    """
    template = SecurityTemplate(
        sections=tuple(
            InfSection(name=section, entries=tuple(section_entries.items()))
            for section, section_entries in entries.items()
        )
    )
    return format_security_template(template)
