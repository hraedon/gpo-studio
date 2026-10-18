"""Egress: every artifact a route returns is validated, in its final form, by
its format, before the response leaves (final-r10).

Rounds 8 and 9 put the integrity rule in the writers and then tried to prove
every writer was covered: first by a hand-kept list, then by a static scan of
the source. Reviewers bypassed both with ordinary Python (a lambda, a
``"".join``, a ``StringIO``, a template, bytes concatenation). A static scan of
Python behaviour does not converge. So the boundary is now the response: each
API route that returns or embeds an artifact is declared, with its format, in
`api.ROUTE_ARTIFACTS`, and its endpoint is wrapped by `api.egress`, which runs
:func:`check` on the result. ``tests/test_egress.py`` enumerates
``app.routes`` at run time and fails on any route not declared as an artifact
route or as returning no artifact (with the reason); for each artifact route
it plants a hostile artifact in the writer and requires a refusal. However a
writer is built, what leaves through a route is held to its format here. The
per-writer emit checks (`emit_integrity`) stay, as defence in depth.

Formats, and what "valid" means for each:

* PowerShell -- the plan (``plan.ps1``, ``apply.ps1``) and the AD discovery
  scripts: tokenized as PowerShell tokenizes them (all five single-quote
  characters, doubled-quote escapes, double-quoted strings, comments); every
  string literal closes on the line it opens; and after every literal and
  every ``[char]`` concatenation term is replaced by a placeholder, the
  script's structure is one the writer produces from neutral input -- each
  plan line one of the plan's line shapes, each discovery script exactly its
  generator's shape. A value that leaves its literal changes the structure.
  The plan also passes `ps_plan_validator.validate_plan`.
* ``GptTmpl.inf`` (``inf_text`` and ``inf_base64``): every physical line one
  line, nothing outside a section, the parse free of `inf_problems`, the text
  a fixed point of parse-and-format, and ``inf_base64`` its encoding.
* ``scripts.ini`` / ``psscripts.ini``: UTF-16LE with BOM, CRLF lines, each a
  section header or a ``key=value`` the Scripts table accepts.
* GPP XML and the GPMC backup documents: parsed, every attribute and text value
  held to `emit_integrity.GPP_XML` / `BACKUP_XML`.
* ``Registry.pol``: parses, and re-serializes to the same bytes.
* ZIP (``export.zip``, the GPMC backups): every member name safe (no
  traversal, no control character, no absolute path), every member one of the
  export's known kinds, validated by its format; an unknown member is refused.
* ``report.txt``: every line one line.
* Firewall rule strings: one line per field, and the registry settings decode
  through the firewall codec and re-encode to the same settings.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from functools import lru_cache
from typing import Any, Literal, assert_never

from .emit_integrity import (
    BACKUP_XML,
    GPP_XML,
    PS_SINGLE_QUOTES,
    EmitError,
    EmitProblem,
    inf_problems,
    inf_text_problems,
    line_problem,
    pol_data_problem,
    pol_name_problem,
    scripts_ini_problems,
    xml_problems,
)

Format = Literal[
    "powershell-plan",
    "powershell-discovery-forest",
    "powershell-discovery-domain",
    "powershell-discovery-sites",
    "powershell-discovery-ous",
    "powershell-discovery-gpos",
    "powershell-discovery-principals",
    "inf",
    "report-text",
    "zip-studio",
    "zip-gpmc",
    "scripts-preview",
    "firewall-settings",
]


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

_DOUBLE_QUOTES = '"“”„'
_LITERAL = "\x00S"  # placeholders cannot occur in tokenized output otherwise
_TERM = re.compile(
    r"\((?:''|\x00S|\[char\](?:0x[0-9A-F]{4}|::ConvertFromUtf32\(0x[0-9A-F]{1,6}\)))"
    r"(?: \+ (?:\x00S|\[char\](?:0x[0-9A-F]{4}|::ConvertFromUtf32\(0x[0-9A-F]{1,6}\))))+\)"
)
_ARRAY = re.compile(r"@\(\x00S(?:, \x00S)*\)")
_BYTES = re.compile(r"\(\[byte\[\]\]\((?:0x[0-9A-Fa-f]{2})(?:,0x[0-9A-Fa-f]{2})*\)\)")
_NUMBER = re.compile(r"(?<![\w$.\[])\d+(?![\w])")


def ps_skeleton(text: str) -> tuple[list[str], list[EmitProblem]]:
    """The structure of a PowerShell script: one entry per physical line, every
    single-quoted literal, ``[char]`` concatenation, string array, byte array and
    number replaced by a placeholder, and every comment's text by ``#``.

    A single-quoted literal is read as PowerShell reads it: any of the five
    single-quote characters opens and closes it, and two of them in a row are one
    escaped quote. A literal still open at the end of its line is a problem: the
    writers never put a line break inside one.
    """
    skeleton: list[str] = []
    problems: list[EmitProblem] = []
    for number, line in enumerate(text.split("\n"), start=1):
        out: list[str] = []
        index = 0
        while index < len(line):
            character = line[index]
            if character in PS_SINGLE_QUOTES:
                index += 1
                while True:
                    if index >= len(line):
                        problems.append(
                            EmitProblem(
                                "PowerShell", f"line {number}", line, "has an unclosed literal"
                            )
                        )
                        break
                    if line[index] in PS_SINGLE_QUOTES:
                        if index + 1 < len(line) and line[index + 1] in PS_SINGLE_QUOTES:
                            index += 2
                            continue
                        index += 1
                        break
                    index += 1
                out.append(_LITERAL)
                continue
            if character in _DOUBLE_QUOTES:
                end = index + 1
                while end < len(line) and line[end] not in _DOUBLE_QUOTES:
                    end += 2 if line[end] == "`" else 1
                out.append(line[index : end + 1])
                index = end + 1
                continue
            if character == "#" and (index == 0 or line[index - 1] in " \t(;{"):
                comment = line[index:]
                problem = line_problem(comment)
                if problem is not None:
                    problems.append(EmitProblem("PowerShell", f"line {number}", comment, problem))
                out.append("#")
                break
            out.append(character)
            index += 1
        shape = "".join(out)
        shape = _TERM.sub(_LITERAL, shape)
        shape = _ARRAY.sub("@(" + _LITERAL + ")", shape)
        shape = _BYTES.sub("<bytes>", shape)
        shape = _NUMBER.sub("<n>", shape)
        problem = line_problem(shape.replace(_LITERAL, ""))
        if problem is not None:
            problems.append(EmitProblem("PowerShell", f"line {number}", line, problem))
        skeleton.append(shape)
    return skeleton, problems


def _reference_plan_gpos() -> Iterable[Any]:
    """Neutral GPOs that exercise every statement the plan writer has."""
    from .model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter

    def setting(
        n: int, kind: str, value: object, action: str = "set", side: str = "computer"
    ) -> RegistrySetting:
        hive = "HKLM" if side == "computer" else "HKCU"
        return RegistrySetting(
            f"s{n}",
            side,  # type: ignore[arg-type]
            hive,  # type: ignore[arg-type]
            "Software\\Neutral",
            f"V{n}",
            kind,  # type: ignore[arg-type]
            value,  # type: ignore[arg-type]
            action,  # type: ignore[arg-type]
        )

    settings = (
        setting(1, "REG_SZ", "text"),
        setting(2, "REG_EXPAND_SZ", "%x%"),
        setting(3, "REG_MULTI_SZ", ["a", "b"]),
        setting(4, "REG_DWORD", 1),
        setting(5, "REG_QWORD", 2),
        setting(6, "REG_BINARY", "0A0B"),
        setting(7, "REG_BINARY", ""),
        setting(8, "REG_SZ", "line\nbreak"),
        setting(9, "REG_SZ", "", "delete"),
        setting(10, "REG_SZ", "", "delete_all_values", "user"),
    )
    guid = "{0B0D0080-0000-4000-8000-000000000000}"
    filters = tuple(
        SecurityFilter(f"f{index}", f"NEUTRAL\\P{index}", permission=permission, target_type=kind)  # type: ignore[arg-type]
        for index, (permission, kind) in enumerate(
            (p, k) for p in ("apply", "read") for k in ("user", "group", "computer")
        )
    )
    links = tuple(
        GPOLink(f"l{index}", "OU=Neutral,DC=studio,DC=local", enabled=e, enforced=f)
        for index, (e, f) in enumerate((e, f) for e in (True, False) for f in (True, False))
    )
    base = GPO(guid=guid, name="Neutral", settings=settings, links=links, security_filters=filters)
    yield base
    yield replace(
        base,
        description="Neutral\ndescription",
        wmi_filter=WmiFilter("w", "W", query="SELECT * FROM Win32_OperatingSystem"),
    )
    for computer, user in ((True, False), (False, True), (False, False)):
        yield replace(GPO(guid=guid, name="Neutral"), computer_enabled=computer, user_enabled=user)


@lru_cache(maxsize=1)
def plan_line_shapes() -> frozenset[str]:
    """Every line shape the plan writer produces from neutral input."""
    from .export import powershell_plan

    shapes: set[str] = set()
    for gpo in _reference_plan_gpos():
        skeleton, problems = ps_skeleton(powershell_plan(gpo))
        assert not problems, problems
        shapes.update(skeleton)
    return frozenset(shapes)


def powershell_plan_problems(text: str, where: str = "plan.ps1") -> list[EmitProblem]:
    from .ps_plan_validator import validate_plan

    skeleton, problems = ps_skeleton(text)
    shapes = plan_line_shapes()
    for number, shape in enumerate(skeleton, start=1):
        if shape not in shapes:
            problems.append(
                EmitProblem(
                    where,
                    f"line {number}",
                    text.split("\n")[number - 1],
                    "is not a statement the plan writer produces",
                )
            )
    result = validate_plan(text)
    if not result.valid:
        problems.append(EmitProblem(where, "plan", "", f"fails the plan validator: {result}"))
    return problems


#: Each discovery script's generator, with neutral arguments.
def _discovery_generators() -> Mapping[str, Callable[[], str]]:
    from . import ad_discovery as d

    return {
        "forest": d.discovery_script_forest,
        "domain": lambda: d.discovery_script_domain("neutral.test"),
        "sites": d.discovery_script_sites,
        "ous": lambda: d.discovery_script_ous("neutral.test", "OU=N,DC=neutral,DC=test"),
        "gpos": lambda: d.discovery_script_gpos("neutral.test"),
        "principals": lambda: d.discovery_script_principal_search(
            "neutral", "neutral.test", "user", "OU=N,DC=neutral,DC=test"
        ),
    }


def discovery_script_problems(text: str, kind: str) -> list[EmitProblem]:
    skeleton, problems = ps_skeleton(text)
    reference, _ = ps_skeleton(_discovery_generators()[kind]())
    if skeleton != reference:
        problems.append(
            EmitProblem(
                f"discovery script ({kind})",
                "script",
                "",
                "does not have the shape its generator produces",
            )
        )
    return problems


# ---------------------------------------------------------------------------
# INF, INI, XML, Registry.pol, report text
# ---------------------------------------------------------------------------


def inf_text_egress_problems(
    text: str,
    inf_base64: str | None = None,
    declared: list[Mapping[str, Any]] | None = None,
) -> list[EmitProblem]:
    from .security_template import (
        _ROW_SECTIONS,
        encode_security_template,
        format_security_template,
        parse_security_template,
        template_problems,
    )

    problems = inf_text_problems(text)
    parsed = parse_security_template(text)
    problems += inf_problems(
        tuple((s.name, s.entries, s.unknown_lines) for s in parsed.sections), _ROW_SECTIONS
    )
    problems += template_problems(parsed)
    if not problems:
        rebuilt = format_security_template(replace(parsed, raw_text=""))
        if parse_security_template(rebuilt).sections != parsed.sections:
            problems.append(EmitProblem("GptTmpl.inf", "text", "", "is not a fixed point"))
    if declared is not None:
        # The text must say exactly what the response says it does: a valid
        # entry the composed sections do not hold (an extra privilege grant)
        # is refused even though it is well-formed INF.
        from .security_template import InfSection, _as_read_back

        expected = _as_read_back(
            tuple(
                InfSection(
                    str(section["name"]),
                    tuple((str(key), str(value)) for key, value in section["entries"]),
                )
                for section in declared
            )
        )
        if parsed.sections != expected:
            problems.append(
                EmitProblem(
                    "GptTmpl.inf", "text", "", "does not hold exactly the declared sections"
                )
            )
    if inf_base64 is not None:
        try:
            decoded = base64.b64decode(inf_base64, validate=True)
        except (binascii.Error, ValueError):
            decoded = b""
        if decoded != encode_security_template(text):
            problems.append(
                EmitProblem("GptTmpl.inf", "inf_base64", "", "is not the encoding of inf_text")
            )
    return problems


def scripts_ini_egress_problems(where: str, data: bytes) -> list[EmitProblem]:
    if not data.startswith(b"\xff\xfe"):
        return [EmitProblem(where, "encoding", "", "is not UTF-16LE with a byte-order mark")]
    try:
        text = data[2:].decode("utf-16-le")
    except UnicodeDecodeError:
        return [EmitProblem(where, "encoding", "", "is not valid UTF-16LE")]
    return scripts_ini_text_problems(where, text)


def scripts_ini_text_problems(where: str, text: str) -> list[EmitProblem]:
    problems: list[EmitProblem] = []
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, start=1):
        problem = line_problem(line)
        if problem is not None:
            problems.append(EmitProblem(where, f"line {number}", line, problem))
            continue
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1], []))
        elif "=" in line and sections:
            key, _, value = line.partition("=")
            sections[-1][1].append((key, value))
        else:
            problems.append(EmitProblem(where, f"line {number}", line, "is not a section or key"))
    return problems + scripts_ini_problems(where, sections)


def registry_pol_egress_problems(where: str, data: bytes) -> list[EmitProblem]:
    from .registry_pol import RegistryPolError, parse, serialize

    try:
        records = parse(data)
    except RegistryPolError as error:
        return [EmitProblem(where, "Registry.pol", "", f"does not parse: {error}")]
    problems: list[EmitProblem] = []
    for record in records:
        for name, text in (("key", record.key), ("value_name", record.value_name)):
            problem = pol_name_problem(text)
            if problem is not None:
                problems.append(EmitProblem(where, name, text, problem))
        items = record.value if isinstance(record.value, list) else [record.value]
        for item in items:
            if isinstance(item, str) and pol_data_problem(item) is not None:
                problems.append(EmitProblem(where, "data", item, "contains NUL"))
    if not problems and serialize(records) != data:
        problems.append(EmitProblem(where, "Registry.pol", "", "does not re-serialize to itself"))
    return problems


def report_text_problems(text: str) -> list[EmitProblem]:
    return [
        EmitProblem("report.txt", f"line {number}", line, problem)
        for number, line in enumerate(text.split("\n"), start=1)
        if (problem := line_problem(line)) is not None
    ]


# ---------------------------------------------------------------------------
# ZIP exports
# ---------------------------------------------------------------------------

_GUID_DIR = r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}"
_SIDE = r"(?:Machine|User)"
#: Each export's members: path pattern -> how it is validated.
_STUDIO_MEMBERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"manifest\.json"), "json"),
    (re.compile(r"apply\.ps1"), "plan"),
    (re.compile(rf"{_SIDE}/Registry\.pol"), "pol"),
    (re.compile(rf"{_SIDE}/Preferences/[A-Za-z]+/[A-Za-z]+\.xml"), "gpp"),
)
_GPMC_MEMBERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"manifest\.xml"), "backup"),
    (re.compile(rf"{_GUID_DIR}/(?:Backup|bkupInfo)\.xml"), "backup"),
    (re.compile(rf"{_GUID_DIR}/DomainSysvol/GPO/{_SIDE}/registry\.pol"), "pol"),
    (
        re.compile(rf"{_GUID_DIR}/DomainSysvol/GPO/{_SIDE}/Preferences/[A-Za-z]+/[A-Za-z]+\.xml"),
        "gpp",
    ),
    (re.compile(rf"{_GUID_DIR}/DomainSysvol/GPO/{_SIDE}/Scripts/(?:ps)?scripts\.ini"), "ini"),
)
_SAFE_NAME = re.compile(r"[A-Za-z0-9{}._/-]+")


def zip_problems(data: bytes, kind: Literal["studio", "gpmc"]) -> list[EmitProblem]:
    members = _STUDIO_MEMBERS if kind == "studio" else _GPMC_MEMBERS
    where = "export.zip" if kind == "studio" else "gpmc-backup"
    problems: list[EmitProblem] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        return [EmitProblem(where, "archive", "", f"is not a ZIP: {error}")]
    with archive:
        for name in archive.namelist():
            if (
                not _SAFE_NAME.fullmatch(name)
                or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
            ):
                problems.append(
                    EmitProblem(where, "member name", name, "is not a safe member name")
                )
                continue
            kinds = [k for pattern, k in members if pattern.fullmatch(name)]
            if not kinds:
                problems.append(
                    EmitProblem(where, "member", name, "is not a member this export writes")
                )
                continue
            content = archive.read(name)
            problems += _member_problems(f"{where}:{name}", kinds[0], content)
    return problems


def _member_problems(where: str, kind: str, content: bytes) -> list[EmitProblem]:
    if kind == "json":
        try:
            json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            return [EmitProblem(where, "json", "", f"is not JSON: {error}")]
        return []
    if kind == "plan":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return [EmitProblem(where, "encoding", "", "is not UTF-8")]
        return powershell_plan_problems(text, where)
    if kind == "pol":
        return registry_pol_egress_problems(where, content)
    if kind in ("gpp", "backup"):
        try:
            return xml_problems(GPP_XML if kind == "gpp" else BACKUP_XML, where, content)
        except ValueError as error:
            return [EmitProblem(where, "xml", "", f"does not parse: {error}")]
    if kind == "ini":
        return scripts_ini_egress_problems(where, content)
    return [EmitProblem(where, "member", "", f"has no validator for {kind!r}")]


# ---------------------------------------------------------------------------
# Firewall render
# ---------------------------------------------------------------------------


def firewall_settings_problems(settings: list[Mapping[str, Any]]) -> list[EmitProblem]:
    from .firewall_policy import from_registry_records, to_registry_settings
    from .model import RegistrySetting

    problems: list[EmitProblem] = []
    rebuilt: list[RegistrySetting] = []
    for body in settings:
        for name in ("key", "value_name", "value"):
            text = str(body[name])
            problem = pol_name_problem(text) or line_problem(text)
            if problem is not None:
                problems.append(EmitProblem("firewall render", name, text, problem))
        value: str | int = body["value"]
        if body["registry_type"] in ("REG_DWORD", "REG_QWORD"):
            value = int(body["value"])
        rebuilt.append(
            RegistrySetting(
                "",
                body["side"],
                body["hive"],
                body["key"],
                body["value_name"],
                body["registry_type"],
                value,
                body["action"],
            )
        )
    if problems:
        return problems
    try:
        decoded = from_registry_records(rebuilt)
    except ValueError as error:  # the codec refuses what it cannot read
        return [EmitProblem("firewall render", "settings", "", f"do not decode: {error}")]
    if decoded.unrecognised_records or any(i.severity == "error" for i in decoded.issues):
        return [
            EmitProblem("firewall render", "settings", "", "do not decode as a firewall policy")
        ]
    reencoded = [(s.key, s.value_name, s.value) for s in to_registry_settings(decoded.policy)]
    if reencoded != [(s.key, s.value_name, s.value) for s in rebuilt]:
        problems.append(EmitProblem("firewall render", "settings", "", "do not round-trip"))
    return problems


# ---------------------------------------------------------------------------
# The check a route's wrapper runs
# ---------------------------------------------------------------------------


def _body(result: Any) -> bytes:
    body = getattr(result, "body", None)
    if not isinstance(body, bytes):
        raise TypeError("an artifact route returned no response body")
    return body


def check(format: Format, result: Any) -> None:
    """Refuse (`EmitError`, a 422) an artifact route's result its format refuses."""
    problems: list[EmitProblem]
    match format:
        case "powershell-plan":
            problems = powershell_plan_problems(_body(result).decode("utf-8"))
        case (
            "powershell-discovery-forest"
            | "powershell-discovery-domain"
            | "powershell-discovery-sites"
            | "powershell-discovery-ous"
            | "powershell-discovery-gpos"
            | "powershell-discovery-principals"
        ):
            problems = discovery_script_problems(result["script"], format.rsplit("-", 1)[1])
        case "inf":
            problems = inf_text_egress_problems(
                result["inf_text"], result["inf_base64"], result["sections"]
            )
        case "report-text":
            problems = report_text_problems(_body(result).decode("utf-8"))
        case "zip-studio":
            problems = zip_problems(_body(result), "studio")
        case "zip-gpmc":
            problems = zip_problems(_body(result), "gpmc")
        case "scripts-preview":
            problems = [
                problem
                for item in result.get("files", [])
                for problem in scripts_ini_text_problems(str(item.get("path", "")), item["text"])
            ]
        case "firewall-settings":
            problems = firewall_settings_problems(result["registry_settings"])
        case _:
            assert_never(format)
    if problems:
        raise EmitError(problems)
