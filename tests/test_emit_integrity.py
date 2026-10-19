"""Every writer checks what it emits, against one table per format (final-r8).

Two halves.

**Completeness, derived from the writers.** Each writer runs over the corpus
(`tests/writer_corpus.py`: the native captures, the records `bd84b3a` stored,
the lane candidates' templates, and synthetic inputs that populate every typed
family, targeting predicate and option); every name it emits -- XML element,
attribute or text, INF section and key, INI key, PowerShell parameter -- must
be classified in `gpo_studio.emit_integrity`, and the corpus must write without
a refusal. A name a writer gains fails here until someone decides what it
holds. This replaces round 7's check that one hand-written inventory equalled
another.

**The reviewers' probes, as tests.** Final-r8 put a line feed into a
``GptTmpl.inf`` principal (a new privilege grant), a right name, an
object-security service name and descriptor, an item uid through the API,
and five more GPP fields through import (`tests/test_identity_fields.py`
section (d) holds those). Each is refused at every layer it can reach: the
request model, the render or export route (422), and the writer itself.
"""

from __future__ import annotations

import dataclasses
import io
import json
import runpy
import zipfile
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from writer_corpus import baseline_collections, gpp_corpus, synthetic_collections

from gpo_studio import api as api_module
from gpo_studio import emit_integrity, export
from gpo_studio.api import app
from gpo_studio.emit_integrity import (
    BACKUP_XML,
    GPP_XML,
    INF_KEYS,
    INF_SECTIONS,
    PS_PARAMETERS,
    REGISTRY_POL,
    EmitError,
    classify_xml,
    inf_key_problem,
    ps_literal,
    xml_names,
    xml_problems,
)
from gpo_studio.firewall_policy import FirewallValidationError, _safe_text
from gpo_studio.gpp import GppError, serialize_gpp, serialize_gpp_unchecked
from gpo_studio.identity_values import (
    date_problem,
    datetime_problem,
    dn_problem,
    security_principal_problem,
)
from gpo_studio.migration import _principal_parts
from gpo_studio.model import (
    GPO,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    ValidationError,
    WmiFilter,
)
from gpo_studio.object_security import (
    FileSecurity,
    FileSystemSecurityFamily,
    RegistryKeySecurity,
    RegistrySecurityFamily,
    ServiceSecurity,
    SystemServicesFamily,
)
from gpo_studio.policy_families import UserRightsFamily
from gpo_studio.ps_plan_validator import validate_plan
from gpo_studio.registry_pol import parse as parse_pol
from gpo_studio.registry_pol import serialize as serialize_pol
from gpo_studio.report import policy_report
from gpo_studio.script_policy import PowerShellScriptEntry, ScriptEntry, ScriptPolicy
from gpo_studio.security_template import (
    _ROW_SECTIONS,
    InfSection,
    PrivilegeRight,
    SecurityTemplate,
    SecurityTemplateError,
    format_security_template,
    parse_security_template,
    template_problems,
)
from gpo_studio.store import WorkspaceStore, gpo_from_dict
from gpo_studio.validation import validate_gpp_collection

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/gpp-store-baseline-bd84b3a"
GUID = "{0B0D0080-0000-4000-8000-000000000000}"

#: Every hostile character a reviewer used, and its kin: each is a control,
#: format or separator character that no single-line value may hold.
CONTROL_SHAPES = {
    "lf": "\n",
    "crlf": "\r\n",
    "nul": "\x00",
    "line-separator": " ",
    "paragraph-separator": " ",
    "rlo": "‮",
    "zwsp": "​",
    "tab": "\t",
}


def _baseline_gpos() -> Iterator[GPO]:
    for record in sorted(BASELINE.glob("*.json")):
        yield gpo_from_dict(json.loads(record.read_text(encoding="utf-8"))["gpo"])


def _plan_gpo(**changes: Any) -> GPO:
    """A GPO exercising every plan statement: each registry type and action, a
    link, an apply and a read filter, a WMI filter and a description."""
    settings = (
        RegistrySetting("s1", "computer", "HKLM", "Software\\Example", "Sz", "REG_SZ", "text"),
        RegistrySetting(
            "s2", "computer", "HKLM", "Software\\Example", "Multi", "REG_MULTI_SZ", ["a", "b"]
        ),
        RegistrySetting("s3", "computer", "HKLM", "Software\\Example", "Dw", "REG_DWORD", 1),
        RegistrySetting("s4", "computer", "HKLM", "Software\\Example", "Bin", "REG_BINARY", "0A0B"),
        RegistrySetting(
            "s5", "computer", "HKLM", "Software\\Example", "Old", "REG_SZ", "", action="delete"
        ),
        RegistrySetting(
            "s6", "user", "HKCU", "Software\\Gone", "", "REG_SZ", "", action="delete_all_values"
        ),
    )
    gpo = GPO(
        guid=GUID,
        name="Example policy",
        description="Line one\nLine two",
        settings=settings,
        links=(GPOLink("l1", "OU=Lab,DC=studio,DC=local"),),
        security_filters=(
            SecurityFilter("f1", "EXAMPLE\\LabAdmins"),
            SecurityFilter("f2", "EXAMPLE\\Readers", permission="read"),
        ),
        wmi_filter=WmiFilter("w1", "Servers", query="SELECT * FROM Win32_OperatingSystem"),
    )
    return replace(gpo, **changes)


# ===========================================================================
# Completeness: every name every writer emits is classified
# ===========================================================================


def test_every_gpp_xml_name_the_writer_emits_is_classified() -> None:
    unclassified: set[tuple[str, str, str]] = set()
    names: set[tuple[str, str, str]] = set()
    for _source, collection in gpp_corpus():
        for _path, data in serialize_gpp_unchecked(collection).items():
            for root, element, attribute, value in xml_names(data):
                names.add((root, element, attribute))
                if classify_xml(GPP_XML, root, element, attribute) is None and not (
                    attribute == "#text" and value == ""
                ):
                    unclassified.add((root, element, attribute))
    assert len(names) > 500, "the corpus no longer exercises the writers"
    assert not unclassified, (
        "the GPP writer emits names emit_integrity.GPP_XML does not classify: "
        f"{sorted(unclassified)}"
    )


def test_the_gpp_corpus_writes_without_a_refusal() -> None:
    """No false refusal: everything Windows wrote, and every typed default, writes."""
    for source, collection in gpp_corpus():
        files = serialize_gpp_unchecked(collection)
        for path, data in files.items():
            assert xml_problems(GPP_XML, path, data) == [], source
        serialize_gpp(collection)


#: GPP_XML entries no corpus writer emits today, each with why it is classified
#: anyway. Anything else in the table must be emitted, or it is stale.
_CLASSIFIED_AHEAD = {
    ("*", "*", "desc"): "GPMC's item description; no capture sets one",
    ("*", "FilterUser", "sid"): "GPMC writes it; Studio's user predicate writes name only",
    ("*", "FilterDate", "period"): "GPMC's date-filter form; retained on import",
    ("*", "FilterDiskSpace", "drive"): "GPMC's disk filter; retained on import",
    ("*", "FilterFile", "type"): "GPMC's file filter; retained on import",
    ("*", "FilterIpRange", "useIPv6"): "GPMC's IP filter; retained on import",
    ("*", "FilterLanguage", "default"): "GPMC's language filter; retained on import",
    ("*", "FilterLanguage", "system"): "GPMC's language filter; retained on import",
    ("*", "FilterLanguage", "native"): "GPMC's language filter; retained on import",
    ("*", "FilterRegistry", "hive"): "GPMC's registry filter; retained on import",
    ("*", "FilterRegistry", "type"): "GPMC's registry filter; retained on import",
    ("*", "FilterRegistry", "valueType"): "GPMC's registry filter; retained on import",
    ("*", "FilterRegistry", "valueData"): "GPMC's registry filter; retained on import",
    ("*", "FilterRegistry", "subtype"): "GPMC's registry filter; retained on import",
    ("*", "FilterComputerName", "type"): "GPMC's computer filter; retained on import",
    ("*", "FilterWmi", "nameSpace"): "GPMC's WMI filter; retained on import",
    ("*", "FilterWmi", "property"): "GPMC's WMI filter; retained on import",
    ("*", "FilterWmi", "variableName"): "GPMC's WMI filter; retained on import",
    ("*", "FilterGroup", "userContext"): "emitted only by GPMC; retained on import",
    ("Drives", "Properties", "username"): "round 7's inventory; a retained attribute",
    ("ScheduledTasks", "GroupId", "#text"): "a task principal by group; retained on import",
}


def test_every_gpp_xml_entry_is_emitted_or_explained() -> None:
    used: set[tuple[str, str, str]] = set()
    for _source, collection in gpp_corpus():
        for data in serialize_gpp_unchecked(collection).values():
            for root, element, attribute, _value in xml_names(data):
                for key in (
                    (root, element, attribute),
                    ("*", element, attribute),
                    ("*", "*", attribute),
                ):
                    if key in GPP_XML:
                        used.add(key)
                        break
    stale = sorted(set(GPP_XML) - used - set(_CLASSIFIED_AHEAD))
    assert not stale, f"GPP_XML entries no writer emits and nothing explains: {stale}"
    assert not set(_CLASSIFIED_AHEAD) - set(GPP_XML)


def _backup_documents() -> Iterator[tuple[str, bytes]]:
    gpos = [*_baseline_gpos(), _plan_gpo(settings=(), links=(), security_filters=())]
    built = 0
    for gpo in gpos:
        try:
            data = export.gpmc_backup_bundle(gpo)
        except ValidationError:
            continue  # an unverified extension: refused before any document is built
        built += 1
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if name.rsplit("/", 1)[-1] in ("Backup.xml", "bkupInfo.xml", "manifest.xml"):
                    yield name, archive.read(name)
    assert built >= 5


def test_every_backup_document_name_is_classified() -> None:
    unclassified = set()
    for name, data in _backup_documents():
        assert xml_problems(BACKUP_XML, name, data) == [], name
        for root, element, attribute, value in xml_names(data):
            if attribute == "#text" and value == "" or element == "GroupPolicyObject":
                continue
            if classify_xml(BACKUP_XML, root, element, attribute) is None:
                unclassified.add((root, element, attribute))
    # Container elements carry no value; everything that does is classified.
    containers = {key for key in unclassified if key[2] == "#text"}
    assert not unclassified - containers, sorted(unclassified - containers)


def _builder_sections(script: str, function: str, **kwargs: Any) -> tuple[InfSection, ...]:
    symbols = runpy.run_path(str(ROOT / "scripts" / "plan-033" / script))
    return tuple(symbols[function](**kwargs))


def _native_template() -> SecurityTemplate:
    """As parsed, ``raw_text`` and all: the lossless path is checked too (Sol
    final-r9 B2 -- round 8's test blanked it and so never exercised it)."""
    text = (ROOT / "tests/fixtures/native-security-template-gpmc/gpttmpl.inf.txt").read_text(
        encoding="utf-8"
    )
    return parse_security_template(text[text.index("[Unicode]") :])


def _inf_corpus() -> Iterator[tuple[str, SecurityTemplate]]:
    yield (
        "policy families",
        SecurityTemplate(
            sections=_builder_sections(
                "build-wp3-candidate.py", "_candidate_sections", include_kerberos=True
            )
        ),
    )
    yield (
        "object security",
        SecurityTemplate(
            sections=_builder_sections("build-object-security-candidate.py", "candidate_sections")
        ),
    )
    yield "native GptTmpl.inf", _native_template()
    yield "native GptTmpl.inf, normalized", replace(_native_template(), raw_text="")


def _inf_keys(corpus: Iterator[tuple[str, SecurityTemplate]]) -> set[tuple[str, str]]:
    return {
        (section.name, key)
        for _source, template in corpus
        for section in template.sections
        for key, _value in section.entries
    }


def test_every_inf_section_and_key_the_writer_emits_is_classified() -> None:
    """Key-derived (Sol final-r9 B2): a new setting name is a decision."""
    seen = set()
    for source, template in _inf_corpus():
        assert template_problems(template) == [], source
        format_security_template(template)
        seen |= {section.name.casefold() for section in template.sections}
    assert seen <= set(INF_SECTIONS), sorted(seen - set(INF_SECTIONS))
    assert seen <= set(INF_KEYS), sorted(seen - set(INF_KEYS))
    assert {"privilege rights", "group membership", "service general setting"} <= seen
    keys = _inf_keys(_inf_corpus())
    assert len(keys) > 30
    unclassified = sorted(k for k in keys if inf_key_problem(*k, vocabulary=True) is not None)
    assert not unclassified, unclassified


def test_a_planted_inf_key_fails_the_inventory() -> None:
    """Sol's plant: ``ReviewPlant = 1`` in a real corpus template fails the
    vocabulary the writers' keys are held to. The writer itself checks a fixed
    section's key by shape only, so a valid Windows key it does not list is
    written (DeepSeek final-r10 N1)."""
    _source, template = next(_inf_corpus())
    planted = tuple(
        InfSection(s.name, (*s.entries, ("ReviewPlant", "1")), s.unknown_lines)
        if s.name == "System Access"
        else s
        for s in template.sections
    )
    keys = _inf_keys(iter([("planted", SecurityTemplate(sections=planted))]))
    assert ("System Access", "ReviewPlant") in keys
    assert inf_key_problem("System Access", "ReviewPlant", vocabulary=True) is not None
    assert inf_key_problem("System Access", "ReviewPlant") is None


@pytest.mark.parametrize(
    "key",
    [
        "EnableAdminAccount",
        "EnableGuestAccount",
        "NewAdministratorName",
        "NewGuestName",
        "LSAAnonymousNameLookup",
        "RequireLogonToChangePassword",
        "ForceLogoffWhenHourExpire",
    ],
)
def test_a_documented_system_access_key_is_written(key: str) -> None:
    """DeepSeek final-r10 N1: a secedit-shaped template re-emits, unchanged."""
    text = (
        "[Unicode]\nUnicode=yes\n[System Access]\nMinimumPasswordAge = 0\n"
        f'{key} = 0\n[Version]\nsignature="$CHICAGO$"\nRevision=1\n'
    )
    template = parse_security_template(text)
    assert format_security_template(template) == text
    assert inf_key_problem("System Access", key, vocabulary=True) is None


def test_a_spaced_row_line_is_a_row() -> None:
    template = SecurityTemplate(
        sections=(
            InfSection("Registry Keys", (), ('"MACHINE\\Software\\X", 0, "D:(A;;KA;;;BA)"',)),
        )
    )
    assert template_problems(template) == []


def _script_policy() -> ScriptPolicy:
    return ScriptPolicy(
        startup=(ScriptEntry("l1", "a1", "zz-marker.cmd", "/c alpha beta"),),
        logoff=(ScriptEntry("l2", "a2", "bye.cmd", "", script_type="logoff"),),
        powershell_startup=(PowerShellScriptEntry("p1", "a3", "boot.ps1", "-Verbose", order=2),),
        powershell_order="run_windows_powershell_scripts_first",
    )


def test_every_scripts_ini_key_is_classified() -> None:
    sections = [
        *export._legacy_scripts_sections(_script_policy()),
        *export._powershell_scripts_sections(_script_policy()),
    ]
    for fixture in ("scripts.ini.txt", "psscripts.ini.txt"):
        text = (ROOT / "tests/fixtures/native-scripts-gpmc" / fixture).read_text(encoding="utf-8")
        current: list[tuple[str, str]] = []
        for line in text.splitlines():
            if line.startswith("[") and line.endswith("]"):
                current = []
                sections.append((line[1:-1], current))
            elif "=" in line and sections and not line.startswith(("first", "CR")):
                key, _, value = line.partition("=")
                current.append((key, value))
    assert sections
    assert emit_integrity.scripts_ini_problems("scripts.ini", sections) == []


def test_every_plan_parameter_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recorded from the writer: every quoted argument names its parameter, and
    the table holds exactly the parameters the plan uses."""
    seen: set[str] = set()
    original = export.ps_literal

    def recording(value: str, parameter: str, **kwargs: Any) -> str:
        seen.add(parameter)
        return original(value, parameter, **kwargs)

    monkeypatch.setattr(export, "ps_literal", recording)
    for gpo in (*_baseline_gpos(), _plan_gpo(), _plan_gpo(description="")):
        plan = export.powershell_plan(gpo)
        assert validate_plan(plan).valid, gpo.name
    assert seen == set(PS_PARAMETERS)


def test_registry_pol_fields_are_the_records_fields() -> None:
    fields = {f.name for f in dataclasses.fields(RegistrySetting)}
    assert {"key", "value_name", "value"} <= fields
    assert set(REGISTRY_POL) == {"key", "value_name", "type", "data"}


# ===========================================================================
# The reviewers' probes
# ===========================================================================


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client


_RIGHTS_URL = "/api/security-template/policy-families"
_OBJECTS_URL = "/api/security-template/object-security"


def _rights(name: str, principals: list[str]) -> dict[str, Any]:
    return {"user_rights": {"assignments": [{"name": name, "principals": principals}]}}


@pytest.mark.parametrize("shape", sorted(CONTROL_SHAPES))
def test_a_principal_cannot_add_a_privilege_line(client: TestClient, shape: str) -> None:
    """Sol B2, DeepSeek B1: ``*S-1-5-32-544<LF>SeDebugPrivilege = *S-1-5-18``."""
    injected = f"*S-1-5-32-544{CONTROL_SHAPES[shape]}SeDebugPrivilege = *S-1-5-18"
    response = client.post(_RIGHTS_URL, json=_rights("SeBackupPrivilege", [injected]))
    assert response.status_code == 422, response.text
    # And the writer, handed the same value directly, refuses it too.
    # The family writer refuses it before joining (final-r9 N2) ...
    family = UserRightsFamily(assignments=(PrivilegeRight("SeBackupPrivilege", (injected,)),))
    with pytest.raises(SecurityTemplateError):
        family.to_template_entries()
    # ... and the INF writer, handed the joined value directly, refuses it too.
    section = InfSection("Privilege Rights", (("SeBackupPrivilege", injected),))
    with pytest.raises(SecurityTemplateError):
        format_security_template(SecurityTemplate(sections=(section,)))


@pytest.mark.parametrize(
    "principal",
    [
        "*S-1-5-32-544x",  # Sol: a junk-suffixed starred SID
        "*s-1-5-32-544",
        "LAB\\Admins ",  # DeepSeek: NBSP
        " LAB\\Admins",
        "LAB\\Ad,mins",  # the list separator
        'LAB\\"Admins"',
        "",
    ],
)
def test_a_malformed_principal_is_refused(client: TestClient, principal: str) -> None:
    response = client.post(_RIGHTS_URL, json=_rights("SeBackupPrivilege", [principal]))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "name", ["SeBackupPrivilege\nSeDebugPrivilege", "SeBackupPrivilege ", "Backup", "Se‮X"]
)
def test_a_right_name_must_be_a_right_constant(client: TestClient, name: str) -> None:
    """DeepSeek B1: the right name itself became ``SeBackupPrivilege<LF>SeDebug...``."""
    response = client.post(_RIGHTS_URL, json=_rights(name, ["*S-1-5-32-544"]))
    assert response.status_code == 422, response.text


def test_the_certified_rights_and_names_still_render(client: TestClient) -> None:
    response = client.post(
        _RIGHTS_URL,
        json=_rights("SeBackupPrivilege", ["*S-1-5-32-544", "LAB\\Backup Operators"]),
    )
    assert response.status_code == 200, response.text
    assert "SeBackupPrivilege = *S-1-5-32-544,LAB\\Backup Operators" in response.json()["inf_text"]


@pytest.mark.parametrize("shape", sorted(CONTROL_SHAPES))
@pytest.mark.parametrize(
    "field", ["service_name", "service_sddl", "registry_key", "registry_sddl", "file_path"]
)
def test_an_object_security_row_cannot_add_a_row(
    client: TestClient, field: str, shape: str
) -> None:
    """Sol B3 (service name) and DeepSeek B1 (descriptor), and the neighbouring
    row keys: each refused by the request model, and by the writer directly."""
    bad = CONTROL_SHAPES[shape]
    service = {"service_name": "Spooler", "startup_mode": "automatic", "raw_sddl": "D:(A;;CC;;;SY)"}
    registry = {"key_path": "MACHINE\\Software\\Example", "raw_sddl": "D:(A;;KA;;;BA)"}
    files = {"file_path": "%SystemRoot%\\Example", "raw_sddl": "D:(A;;FA;;;BA)"}
    target, key = {
        "service_name": (service, "service_name"),
        "service_sddl": (service, "raw_sddl"),
        "registry_key": (registry, "key_path"),
        "registry_sddl": (registry, "raw_sddl"),
        "file_path": (files, "file_path"),
    }[field]
    target[key] = target[key] + bad + '",2,"D:(A;;GA;;;WD)'
    body = {"services": [service], "registry_keys": [registry], "files": [files]}
    assert client.post(_OBJECTS_URL, json=body).status_code == 422

    families = (
        SystemServicesFamily(
            services=(ServiceSecurity(service["service_name"], "automatic", service["raw_sddl"]),)
        ),
        RegistrySecurityFamily(
            keys=(RegistryKeySecurity(registry["key_path"], registry["raw_sddl"]),)
        ),
        FileSystemSecurityFamily(files=(FileSecurity(files["file_path"], files["raw_sddl"]),)),
    )
    sections = tuple(
        InfSection(name, tuple(entries.items()))
        for family in families
        for name, entries in family.to_template_entries().items()
    )
    with pytest.raises(SecurityTemplateError):
        format_security_template(SecurityTemplate(sections=sections))


def test_a_quote_in_a_row_key_cannot_close_the_field() -> None:
    template = SecurityTemplate(
        sections=(
            InfSection(
                "Service General Setting", (('Spooler",2,"D:(A;;GA;;;WD)', '2,"D:(A;;CC;;;SY)"'),)
            ),
        )
    )
    assert any("double quote" in p.problem for p in template_problems(template))
    assert "service general setting" in _ROW_SECTIONS


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [
        ("MinimumPasswordAge = 0\nMaximumPasswordAge", "1", "contains"),
        ("Key=Name", "1", "'='"),
        (";Comment", "1", "comment"),
        ("Key", "value\\", "continuation"),
        ("Key", " 1", "whitespace"),
    ],
)
def test_the_inf_grammar_cannot_be_turned(key: str, value: str, problem: str) -> None:
    template = SecurityTemplate(sections=(InfSection("System Access", ((key, value),)),))
    found = template_problems(template)
    assert any(problem in p.problem for p in found), found


@pytest.mark.parametrize("shape", sorted(CONTROL_SHAPES))
def test_an_item_uid_from_the_api_is_refused(client: TestClient, shape: str) -> None:
    """DeepSeek B2: ``unknown_attrs=[["uid", "{...}<LF>"]]`` shipped in Groups.xml."""
    gpo = client.post(
        "/api/gpos", json={"name": "uid probe", "actor": "probe", "reason": "r8"}
    ).json()["gpo"]
    for field in ("unknown_attrs", "unknown_props_attrs"):
        name = "uid" if field == "unknown_attrs" else "vendorNote"
        response = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    field: [
                        [name, "{11111111-2222-3333-4444-555555555555}" + CONTROL_SHAPES[shape]]
                    ],
                },
                "actor": "probe",
                "reason": "r8",
                "expected_revision": gpo["revision"],
            },
        )
        assert response.status_code == 422, (field, response.text)


@pytest.mark.parametrize("value", ["{11111111-2222-3333-4444-55555555555x}", "not-a-guid", "{}"])
def test_a_junk_item_uid_from_the_api_is_refused(client: TestClient, value: str) -> None:
    gpo = client.post(
        "/api/gpos", json={"name": "uid probe", "actor": "probe", "reason": "r8"}
    ).json()["gpo"]
    response = client.post(
        f"/api/gpos/{gpo['guid']}/preferences/groups",
        json={
            "scope": "computer",
            "group": {
                "name": "Administrators",
                "sid": "S-1-5-32-544",
                "unknown_attrs": [["uid", value]],
            },
            "actor": "probe",
            "reason": "r8",
            "expected_revision": gpo["revision"],
        },
    )
    assert response.status_code == 422, response.text


def _typed_string_fields() -> Iterator[Any]:
    """(family, index, field) for every string field of every synthetic typed item."""
    (collection, _user) = synthetic_collections()
    skip = {"id", "native_xml", "password", "account_password", "run_as_password", "task_xml"}
    for family in (f.name for f in dataclasses.fields(collection)):
        items = getattr(collection, family)
        if not isinstance(items, tuple) or not items or not dataclasses.is_dataclass(items[0]):
            continue
        for index, item in enumerate(items):
            for item_field in dataclasses.fields(item):
                # Literal (enum) fields are typed, not text: the API refuses any
                # value outside their set before a writer sees it.
                if item_field.name in skip or item_field.type != "str":
                    continue
                yield pytest.param(
                    family, index, item_field.name, id=f"{family}[{index}].{item_field.name}"
                )


@pytest.mark.parametrize(("family", "index", "name"), list(_typed_string_fields()))
@pytest.mark.parametrize("shape", ["lf", "line-separator", "rlo", "nul"])
def test_no_typed_string_field_writes_a_control_character(
    family: str, index: int, name: str, shape: str
) -> None:
    """Every string a typed item carries, through the store's boundary and the
    writer: refused, or never written."""
    (collection, _user) = synthetic_collections()
    items = list(getattr(collection, family))
    hostile = (getattr(items[index], name) or "x") + CONTROL_SHAPES[shape] + "Injected"
    items[index] = replace(items[index], **{name: hostile})
    changed = replace(collection, **{family: tuple(items)})
    errors = [i for i in validate_gpp_collection(changed) if i.severity == "error"]
    if errors:
        return
    try:
        files = serialize_gpp(changed)
    except (GppError, ValueError):
        return
    for path, data in files.items():
        for root, element, attribute, value in xml_names(data):
            if value != hostile:
                continue
            # Only a multi-line field may carry TAB or LF, which XML encodes.
            field = classify_xml(GPP_XML, root, element, attribute)
            assert field is not None and field.kind == "text" and shape == "lf", (
                f"{family}.{name} wrote {hostile!r} into {path} {element}@{attribute}"
            )


def test_run_once_id_is_held_to_a_guid() -> None:
    (collection, _user) = synthetic_collections()
    group = collection.groups[0]
    bad = replace(group, common=replace(group.common, run_once_id="{1111}\nx"))
    errors = [
        i
        for i in validate_gpp_collection(replace(collection, groups=(bad,)))
        if i.severity == "error"
    ]
    assert errors
    with pytest.raises(GppError):
        serialize_gpp(replace(collection, groups=(bad,)))


# --- the PowerShell plan ----------------------------------------------------------------


@pytest.mark.parametrize("quote", ["'", "‘", "’", "‚", "‛"])
def test_every_powershell_single_quote_is_doubled(quote: str) -> None:
    """PowerShell ends a literal at any of these; doubling only ``'`` let a
    typographic apostrophe close the string and run the rest as code."""
    name = f"Paul{quote}s policy{quote}; Remove-GPO -All; {quote}"
    plan = export.powershell_plan(_plan_gpo(name=name))
    doubled = name.replace(quote, quote * 2)
    assert f"-Name '{doubled}'" in plan
    result = validate_plan(plan)
    assert result.valid, result


@pytest.mark.parametrize("shape", sorted(CONTROL_SHAPES))
@pytest.mark.parametrize("parameter", ["Name", "Key", "ValueName", "TargetName", "Target", "Guid"])
def test_a_plan_argument_is_one_line(parameter: str, shape: str) -> None:
    value = "Example" + CONTROL_SHAPES[shape] + "Remove-GPO -All"
    with pytest.raises(EmitError):
        ps_literal(value, parameter)


@pytest.mark.parametrize(
    "gpo",
    [
        pytest.param(_plan_gpo(name="Example\nRemove-GPO -All"), id="name"),
        pytest.param(
            _plan_gpo(links=(GPOLink("l1", "OU=Lab,DC=studio,DC=local\\"),)), id="link-target"
        ),
        pytest.param(
            _plan_gpo(security_filters=(SecurityFilter("f1", "EXAMPLE\\a b"),)),
            id="principal",
        ),
        pytest.param(
            _plan_gpo(
                settings=(
                    RegistrySetting("s1", "computer", "HKLM", "Software\\A\nB", "V", "REG_SZ", "x"),
                )
            ),
            id="registry-key",
        ),
    ],
)
def test_powershell_plan_validates_its_own_input(gpo: GPO) -> None:
    """DeepSeek N3: a direct caller cannot skip the route's validation."""
    with pytest.raises((ValidationError, EmitError)):
        export.powershell_plan(gpo)


def test_comment_and_registry_data_may_span_lines() -> None:
    """Written as [char] codes, so each statement stays one physical line
    (`tests/test_powershell_execution.py` runs it and reads the value back)."""
    plan = export.powershell_plan(_plan_gpo())
    assert "-Comment ('Line one' + [char]0x000A + 'Line two')" in plan
    assert validate_plan(plan).valid


def test_a_comment_line_cannot_be_ended() -> None:
    wmi = WmiFilter("w1", "Servers Remove-GPO -All", query="SELECT *\nFROM x")
    plan = export.powershell_plan(_plan_gpo(wmi_filter=wmi))
    assert not any(line.startswith("Remove-GPO") for line in plan.splitlines())
    assert not any(line.startswith("FROM x") for line in plan.splitlines())


# --- Registry.pol, scripts.ini, firewall, report, backup ---------------------------------


@pytest.mark.parametrize("field", ["key", "value_name"])
def test_a_registry_pol_name_holds_no_nul(field: str) -> None:
    setting = RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x")
    with pytest.raises(EmitError):
        serialize_pol([replace(setting, **{field: "A\x00B"})])


@pytest.mark.parametrize(
    ("key", "name"),
    [("Software\\Example;Branch", "Value;Name"), ("Software\\X", "\u3b00"), ("A;", ";")],
)
def test_a_semicolon_in_a_registry_pol_name_is_data(key: str, name: str) -> None:
    """Sol final-r9 B1: MS-GPREG 2.2.1's IdCharacter and ValueCharacter include
    ';', and the field is NUL-terminated, so a ';' before the NUL is data. A
    character whose UTF-16 bytes contain 3B 00 must not split the field either."""
    setting = RegistrySetting("s", "computer", "HKLM", key, name, "REG_SZ", "x;y")
    (record,) = parse_pol(serialize_pol([setting]))
    assert (record.key, record.value_name, record.value) == (key, name, "x;y")


def test_a_hand_built_preg_record_with_a_semicolon_reads() -> None:
    """The wire bytes as MS-GPREG lays them out, not Studio's writer."""
    import struct

    def text(value: str) -> bytes:
        return (value + "\0").encode("utf-16le")

    sep = ";".encode("utf-16le")
    data = "v".encode("utf-16le") + b"\0\0"
    record = (
        "[".encode("utf-16le")
        + text("Software\\A;B")
        + sep
        + text("V;1")
        + sep
        + struct.pack("<I", 1)
        + sep
        + struct.pack("<I", len(data))
        + sep
        + data
        + "]".encode("utf-16le")
    )
    (parsed,) = parse_pol(b"PReg" + struct.pack("<I", 1) + record)
    assert (parsed.key, parsed.value_name, parsed.value) == ("Software\\A;B", "V;1", "v")


def test_registry_pol_data_is_length_encoded() -> None:
    setting = RegistrySetting(
        "s", "computer", "HKLM", "Software\\X", "Notice", "REG_SZ", "Line one\r\nLine two"
    )
    (record,) = parse_pol(serialize_pol([setting]))
    assert record.value == "Line one\r\nLine two"
    with pytest.raises(EmitError):
        serialize_pol([replace(setting, value="a\x00b")])
    with pytest.raises(EmitError):
        serialize_pol([replace(setting, registry_type="REG_MULTI_SZ", value=["a\x00b"])])


@pytest.mark.parametrize("shape", sorted(CONTROL_SHAPES))
def test_a_script_line_is_one_line(shape: str) -> None:
    with pytest.raises(EmitError):
        export._native_ini_bytes(
            [("Startup", [("0CmdLine", "a.cmd" + CONTROL_SHAPES[shape] + "1CmdLine=evil.cmd")])]
        )


@pytest.mark.parametrize("bad", ["Rule Name", "Rule‮Name", "Rule|Name", "Rule\x0bName"])
def test_a_firewall_rule_field_is_one_line(bad: str) -> None:
    with pytest.raises(FirewallValidationError):
        _safe_text(bad, "rule name")


def test_the_report_escapes_what_would_start_a_line() -> None:
    gpo = _plan_gpo(
        name="Example\nStatus: ready",
        links=(GPOLink("l1", "OU=Lab,DC=studio,DC=local Links injected"),),
    )
    report = policy_report(gpo)
    lines = report.splitlines()
    assert "Status: ready" not in lines
    assert not any(line.startswith("Links injected") for line in lines)
    assert "Example\\u000aStatus: ready" in report
    assert "Line one" in lines and "Line two" in lines  # a description's own lines


@pytest.mark.parametrize("name", ["Example Policy", "Example\x0bPolicy"])
def test_a_backup_document_value_is_one_line(name: str) -> None:
    with pytest.raises((EmitError, ValidationError)):
        export.gpmc_backup_bundle(_plan_gpo(name=name, settings=(), links=()))


def test_a_backup_document_with_a_bad_guid_is_refused() -> None:
    with pytest.raises((EmitError, ValidationError)):
        export.gpmc_backup_bundle(_plan_gpo(guid="{not-a-guid}", settings=()))


# --- DeepSeek final-r8 N2, N6, N7, N8 --------------------------------------------------


@pytest.mark.parametrize("value", ["2026-02-31", "2026-04-31", "2027-02-29", "2026-13-01"])
def test_an_impossible_date_is_refused(value: str) -> None:
    assert date_problem(value) is not None
    assert datetime_problem(value + "T00:00:00") is not None


def test_a_real_leap_day_is_a_date() -> None:
    assert date_problem("2028-02-29") is None


@pytest.mark.parametrize("offset", ["+99:99", "+15:00", "-14:60"])
def test_an_impossible_utc_offset_is_refused(offset: str) -> None:
    assert datetime_problem(f"2026-01-01T00:00:00{offset}") is not None


@pytest.mark.parametrize("offset", ["+14:00", "-12:00", "+05:30", "Z"])
def test_a_real_utc_offset_is_accepted(offset: str) -> None:
    assert datetime_problem(f"2026-01-01T00:00:00{offset}") is None


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("*S-1-5-32-544", True),
        ("*S-1-5-32-544x", False),
        ("*S-1-5-32-544\n", False),
        ("*", False),
        ("LAB\\Admins", True),
        ("S-1-5-32-544", True),
        ("s-1-5-32-544x", False),
    ],
)
def test_a_starred_sid_must_be_a_sid(value: str, valid: bool) -> None:
    assert (security_principal_problem(value) is None) is valid


@pytest.mark.parametrize("text", ["S-1-5-32-544", "s-1-5-32-544"])
def test_a_migration_table_sid_is_case_insensitive(text: str) -> None:
    """DeepSeek N7: round 7 made a lower-case ``s-1-...`` a name; restored."""
    assert _principal_parts(text) == (text, "")


@pytest.mark.parametrize(
    ("dn", "valid"),
    [
        ("CN=a+OU=b,DC=x", True),
        ("CN=a\\+b,DC=x", True),
        ("CN=a+,DC=x", False),
        ("CN=a+XX=b,DC=x", False),
        ("CN=a++OU=b,DC=x", False),
    ],
)
def test_a_multi_valued_rdn_is_a_dn(dn: str, valid: bool) -> None:
    """DeepSeek N8: RFC 4514 section 3 joins attribute-value pairs with ``+``."""
    assert (dn_problem(dn) is None) is valid


def test_baseline_collections_exist() -> None:
    assert sum(1 for _ in baseline_collections()) > 10


# ===========================================================================
# Final-r9 probes
# ===========================================================================


def test_the_lossless_inf_path_is_checked_too() -> None:
    """Sol B2: an unchanged parsed template was returned verbatim, before any check."""
    text = (
        '[Unicode]\nUnicode=yes\n[Version]\nsignature="$CHICAGO$"\nRevision=1\n'
        "[Privilege Rights]\nSeBackupPrivilege = *S-1-5-32-544‮X\n"
    )
    template = parse_security_template(text)
    assert template.raw_text
    with pytest.raises(SecurityTemplateError):
        format_security_template(template)


@pytest.mark.parametrize(
    ("section", "lines"),
    [
        ("Version", ("[Privilege Rights]", "SeDebugPrivilege = *S-1-5-18")),
        ("Privilege Rights", ("SeDebugPrivilege = *S-1-5-18",)),
        ("Registry Keys", ('"MACHINE\\Software\\X",0,"D:(A;;KA;;;BA)"', "[Privilege Rights]")),
        ("Registry Keys", ("MACHINE\\Software\\X,0,D:(A;;KA;;;WD)",)),
        ("System Access", ("MinimumPasswordAge \\",)),
    ],
    ids=["header-then-entry", "entry", "header-in-row-section", "unquoted-row", "continuation"],
)
def test_a_preserved_line_cannot_become_structure(section: str, lines: tuple[str, ...]) -> None:
    """Sol B2: preserved lines were checked only for control characters, so a
    `[Privilege Rights]` line and an entry after it became an active grant."""
    template = SecurityTemplate(
        sections=(
            InfSection("Version", (("signature", '"$CHICAGO$"'),)),
            InfSection(section, (), lines),
        )
    )
    assert template_problems(template), lines
    with pytest.raises(SecurityTemplateError):
        format_security_template(template)


def test_a_preserved_comment_and_a_preserved_row_still_write() -> None:
    row = '"MACHINE\\Software\\X",0,"D:(A;;KA;;;BA)"'
    template = SecurityTemplate(
        sections=(
            InfSection("Version", (("signature", '"$CHICAGO$"'),), ("; a comment = fine",)),
            InfSection("Registry Keys", (), (row,)),
        )
    )
    text = format_security_template(template)
    assert row in text.splitlines() and "; a comment = fine" in text.splitlines()


def test_a_comma_in_one_principal_is_refused_before_the_join() -> None:
    """DeepSeek N2: once joined, `LAB\\A,LAB\\B` reads as two principals."""
    family = UserRightsFamily(
        assignments=(PrivilegeRight("SeBackupPrivilege", ("LAB\\A,LAB\\B",)),)
    )
    with pytest.raises(SecurityTemplateError):
        family.to_template_entries()


def test_a_comma_in_a_restricted_group_member_is_refused() -> None:
    from gpo_studio.object_security import (
        RestrictedGroup,
        RestrictedGroupMember,
        RestrictedGroupsFamily,
    )

    group = RestrictedGroup(
        group_sid="S-1-5-32-544",
        members=(RestrictedGroupMember(sid="LAB\\A,LAB\\B"),),
    )
    with pytest.raises(SecurityTemplateError):
        RestrictedGroupsFamily(groups=(group,)).to_template_entries()


def test_an_empty_row_value_is_refused() -> None:
    """DeepSeek N4: `"Spooler",` is not a row."""
    template = SecurityTemplate(
        sections=(InfSection("Service General Setting", (("Spooler", ""),)),)
    )
    assert any("row value" in p.problem for p in template_problems(template))


def test_an_empty_service_row_is_refused_at_the_api(client: TestClient) -> None:
    response = client.post(_OBJECTS_URL, json={"services": [{"service_name": "Spooler"}]})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unwritable_template_value"


def test_an_empty_security_option_value_is_refused(client: TestClient) -> None:
    body = {"security_options": {"options": [{"key": "MACHINE\\Software\\X", "value": ""}]}}
    assert client.post(_RIGHTS_URL, json=body).status_code == 422


@pytest.mark.parametrize(
    ("url", "body"),
    [
        (_RIGHTS_URL, _rights("SeBackupPrivilege\nSeDebugPrivilege", ["*S-1-5-32-544"])),
        (_RIGHTS_URL, _rights("SeBackupPrivilege", ["*S-1-5-32-544\n"])),
        (_OBJECTS_URL, {"services": [{"service_name": "Spooler\nX", "startup_mode": "automatic"}]}),
    ],
    ids=["right-name", "principal", "service-name"],
)
def test_a_render_refusal_carries_its_documented_code(
    client: TestClient, url: str, body: dict[str, Any]
) -> None:
    """Sol N1: the request model refused first and answered without a code."""
    response = client.post(url, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unwritable_template_value"


@pytest.mark.parametrize("offset", ["+14:59", "+14:01", "-14:30"])
def test_no_offset_past_fourteen_hours(offset: str) -> None:
    """DeepSeek N5."""
    assert datetime_problem(f"2026-01-01T00:00:00{offset}") is not None


def _new_gpo(client: TestClient) -> dict[str, Any]:
    return client.post(
        "/api/gpos", json={"name": "Writer probe", "actor": "probe", "reason": "r9"}
    ).json()["gpo"]


def _add_setting(client: TestClient, gpo: dict[str, Any], **setting: Any) -> Any:
    body = {
        "side": "computer",
        "hive": "HKLM",
        "key": "Software\\Example",
        "value_name": "Value",
        "registry_type": "REG_SZ",
        "value": "x",
        **setting,
    }
    return client.post(
        f"/api/gpos/{gpo['guid']}/settings",
        json={
            "setting": body,
            "actor": "probe",
            "reason": "r9",
            "expected_revision": gpo["revision"],
        },
    )


def _exports(client: TestClient, guid: str) -> dict[str, int]:
    return {
        route: client.get(f"/api/gpos/{guid}/{route}").status_code
        for route in ("export.zip", "gpmc-backup", "plan.ps1")
    }


def test_crlf_registry_data_is_accepted_end_to_end(client: TestClient) -> None:
    """Sol B3: accepted, stored, advertised, and every export writes it."""
    gpo = _new_gpo(client)
    added = _add_setting(client, gpo, value_name="LegalNoticeText", value="Line one\r\nLine two")
    assert added.status_code == 201, added.text
    capabilities = added.json()["artifact_capabilities"]
    assert all(
        capabilities[name]["enabled"]
        for name in ("studio_export", "gpmc_export", "powershell_plan")
    ), capabilities
    assert _exports(client, gpo["guid"]) == {"export.zip": 200, "gpmc-backup": 200, "plan.ps1": 200}
    plan = client.get(f"/api/gpos/{gpo['guid']}/plan.ps1").text
    assert "[char]0x000D + [char]0x000A" in plan


def test_a_semicolon_registry_name_is_accepted_end_to_end(client: TestClient) -> None:
    """Sol B1: through the API, into both exports, and back out of Registry.pol."""
    gpo = _new_gpo(client)
    added = _add_setting(client, gpo, key="Software\\Example;Branch", value_name="Value;Name")
    assert added.status_code == 201, added.text
    assert _exports(client, gpo["guid"]) == {"export.zip": 200, "gpmc-backup": 200, "plan.ps1": 200}
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/gpos/{gpo['guid']}/export.zip").content))
    (record,) = parse_pol(archive.read("Machine/Registry.pol"))
    assert (record.key, record.value_name) == ("Software\\Example;Branch", "Value;Name")


def test_a_description_with_a_format_character_gets_a_plan(client: TestClient) -> None:
    """DeepSeek N6: advertised enabled, then refused. It is now written, as [char]."""
    gpo = client.post(
        "/api/gpos",
        json={"name": "Zw", "description": "line one​line two", "actor": "p", "reason": "r9"},
    ).json()
    capabilities = gpo["artifact_capabilities"]
    assert capabilities["powershell_plan"]["enabled"], capabilities
    assert client.get(f"/api/gpos/{gpo['gpo']['guid']}/plan.ps1").status_code == 200


#: Stored before final-r9's rules: each is something a writer now refuses.
_LEGACY = {
    "nul-data": RegistrySetting(
        "s1", "computer", "HKLM", "Software\\Example", "V", "REG_SZ", "a\x00b"
    ),
    "line-break-key": RegistrySetting(
        "s1", "computer", "HKLM", "Software\\A\nB", "V", "REG_SZ", "x"
    ),
}


@pytest.mark.parametrize("legacy", sorted(_LEGACY))
def test_a_legacy_value_a_writer_refuses_never_breaks_a_read(legacy: str) -> None:
    """DeepSeek B2: `native_backup_refusal` let EmitError out, so the GPO's own
    detail read and every mutation response after commit answered 422."""
    from gpo_studio.api import _gpo_payload

    gpo = replace(_plan_gpo(settings=()), settings=(_LEGACY[legacy],))
    payload = _gpo_payload(gpo)  # must not raise
    capabilities = payload["artifact_capabilities"]
    for name in ("studio_export", "gpmc_export", "powershell_plan"):
        assert capabilities[name]["enabled"] is False, (name, capabilities[name])
    assert payload["validation"], "the read names what to repair"
    # Each refusal is reported, never raised, whichever layer refuses.
    for refusal in (
        export.native_backup_refusal,
        export.plan_refusal,
        export.studio_export_refusal,
    ):
        refusal(gpo)


def test_a_legacy_gpo_reads_and_takes_an_unrelated_edit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The HTTP contract: GET is 200, and a valid edit commits AND answers 200."""
    gpo = _new_gpo(client)
    added = _add_setting(client, gpo)
    assert added.status_code == 201
    store = app.state.store
    original = store.get_gpo

    def legacy(guid: str) -> GPO:
        stored = original(guid)
        return replace(stored, settings=(*stored.settings, _LEGACY["nul-data"]))

    monkeypatch.setattr(store, "get_gpo", legacy)
    read = client.get(f"/api/gpos/{gpo['guid']}")
    assert read.status_code == 200, read.text
    assert read.json()["artifact_capabilities"]["gpmc_export"]["enabled"] is False
    assert client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup").status_code == 422


@pytest.mark.parametrize("writer", ["_gpmc_preg_bytes", "serialize", "ps_literal", "check_xml"])
def test_any_writer_refusal_is_reported_by_the_read_not_raised(
    monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    """DeepSeek B2 as a class: whichever writer refuses -- Registry.pol in the
    backup or the bundle, a plan argument, a backup document -- the GPO payload
    (every read and every mutation response) reports it and does not raise."""
    from gpo_studio.api import _gpo_payload

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise EmitError([emit_integrity.EmitProblem("probe", writer, "x", "refused by probe")])

    monkeypatch.setattr(export, writer, refuse)
    # The refusals are cached by GPO content; the writer changed under it here.
    monkeypatch.setattr(api_module, "_REFUSAL_CACHE", api_module.OrderedDict())
    payload = _gpo_payload(_plan_gpo())  # a valid GPO: only the writer refuses
    capabilities = payload["artifact_capabilities"]
    refused = [
        name
        for name in ("studio_export", "gpmc_export", "powershell_plan")
        if not capabilities[name]["enabled"]
    ]
    assert refused, capabilities
    for name in refused:
        assert "refused by probe" in capabilities[name]["reason"], capabilities[name]


@pytest.mark.parametrize("quote", list(emit_integrity.PS_SINGLE_QUOTES))
def test_the_discovery_routes_double_every_quote(client: TestClient, quote: str) -> None:
    """DeepSeek B1 through the real routes (`tests/test_powershell_execution.py`
    runs the scripts themselves)."""
    domain = f"lab{quote} ) ; Write-Output PWNED ; #"
    for route in ("domain", "gpos"):
        response = client.get(f"/api/discovery/{route}/script", params={"domain": domain})
        assert response.status_code == 200, response.text
        script = response.json()["script"]
        assert f"'lab{quote}{quote} ) ; Write-Output PWNED ; #'" in script


@pytest.mark.parametrize(
    ("route", "params"),
    [
        ("ous", {"domain": "lab.test", "search_base": "not a DN"}),
        ("principals", {"q": "x", "object_class": "user)(cn=*"}),
        ("domain", {"domain": "lab\nWrite-Output x"}),
    ],
)
def test_a_discovery_value_of_the_wrong_kind_is_a_422(
    client: TestClient, route: str, params: dict[str, str]
) -> None:
    response = client.get(f"/api/discovery/{route}/script", params=params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unwritable_artifact_value"
