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

from gpo_studio import emit_integrity, export
from gpo_studio.api import app
from gpo_studio.emit_integrity import (
    BACKUP_XML,
    GPP_XML,
    INF_SECTIONS,
    PS_PARAMETERS,
    REGISTRY_POL,
    EmitError,
    classify_xml,
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
    text = (ROOT / "tests/fixtures/native-security-template-gpmc/gpttmpl.inf.txt").read_text(
        encoding="utf-8"
    )
    body = text[text.index("[Unicode]") :]
    return replace(parse_security_template(body), raw_text="")


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


def test_every_inf_section_the_writer_emits_is_classified() -> None:
    seen = set()
    for source, template in _inf_corpus():
        assert template_problems(template) == [], source
        format_security_template(template)
        seen |= {section.name.casefold() for section in template.sections}
    assert seen <= set(INF_SECTIONS), sorted(seen - set(INF_SECTIONS))
    assert {"privilege rights", "group membership", "service general setting"} <= seen


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
    original = export._ps_quote

    def recording(value: str, parameter: str) -> str:
        seen.add(parameter)
        return original(value, parameter)

    monkeypatch.setattr(export, "_ps_quote", recording)
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
    family = UserRightsFamily(assignments=(PrivilegeRight("SeBackupPrivilege", (injected,)),))
    sections = tuple(
        InfSection(name, tuple(entries.items()))
        for name, entries in family.to_template_entries().items()
    )
    with pytest.raises(SecurityTemplateError):
        format_security_template(SecurityTemplate(sections=sections))


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
        export._ps_quote(value, parameter)


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
                    RegistrySetting("s1", "computer", "HKLM", "Software\\A;B", "V", "REG_SZ", "x"),
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
    plan = export.powershell_plan(_plan_gpo())
    assert "-Comment 'Line one\nLine two'" in plan
    assert validate_plan(plan).valid


def test_a_comment_line_cannot_be_ended() -> None:
    wmi = WmiFilter("w1", "Servers Remove-GPO -All", query="SELECT *\nFROM x")
    plan = export.powershell_plan(_plan_gpo(wmi_filter=wmi))
    assert not any(line.startswith("Remove-GPO") for line in plan.splitlines())
    assert not any(line.startswith("FROM x") for line in plan.splitlines())


# --- Registry.pol, scripts.ini, firewall, report, backup ---------------------------------


@pytest.mark.parametrize("field", ["key", "value_name"])
@pytest.mark.parametrize("bad", ["A;B", "A\x00B", "A\nB", "A B"])
def test_a_registry_pol_name_holds_no_separator(field: str, bad: str) -> None:
    setting = RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x")
    with pytest.raises(EmitError):
        serialize_pol([replace(setting, **{field: bad})])


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
