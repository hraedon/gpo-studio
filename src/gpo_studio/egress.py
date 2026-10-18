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
  string literal closes on the line it opens. Each plan line, with its holes
  abstracted (a literal or ``[char]`` term, a literal array of any length, a
  number, a byte array), must be a statement of :data:`PLAN_GRAMMAR`; a
  discovery script must have exactly its generator's shape. The plan also
  passes `ps_plan_validator.validate_plan`.
* ``GptTmpl.inf`` (``inf_text`` and ``inf_base64``): every physical line one
  line, no continuation, nothing outside a section, the parse free of
  `inf_problems`, exactly the sections the response declares, and
  ``inf_base64`` its encoding.
* ``scripts.ini`` / ``psscripts.ini``: UTF-16LE with BOM, CRLF lines, each a
  section header or a ``key=value`` the Scripts table accepts.
* GPP XML: every attribute and text value held to `emit_integrity.GPP_XML`,
  every element in its file's vocabulary (`emit_integrity.GPP_ELEMENTS`), and
  -- where the route has the GPO -- the files read back as the GPO's
  preferences: each item's common options and targeting as the model holds
  them. The GPMC backup documents: `BACKUP_XML`.
* ``Registry.pol``: parses, and re-serializes to the same bytes.
* ZIP (``export.zip``, the GPMC backups): read entry by entry (a duplicate
  name is refused, so it cannot hide an earlier member); every member name
  safe and of a known kind; every member the export must hold present exactly
  once; an empty archive refused; each member validated by its format.
* ``report.txt``: every line one line.
* Firewall rule strings: one line per field, and the registry settings decode
  through the firewall codec and re-encode to the same settings.

The routes that build a GPO's plan, Studio bundle and GPMC backup call
:func:`checked_plan`, :func:`checked_studio_bundle` and
:func:`checked_gpmc_backup`, and the GPO's artifact capabilities are whether
those same functions refuse (:func:`plan_refusal` and its siblings), so a
capability and its download cannot disagree.

**What egress does not establish.** It checks that an artifact is well formed
and that user data stays in its place: no value becomes a statement, entry,
row, element or member. It does not check that a writer meant what it wrote:
a broken writer that adds a statement of an allowed shape, a valid registry
record, a valid report line or a member of a known kind passes. That class is
the writers' unit tests, the execution tests and the Windows lanes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Literal, assert_never

from .emit_integrity import (
    BACKUP_XML,
    GPP_XML,
    PS_SINGLE_QUOTES,
    EmitError,
    EmitProblem,
    gpp_element_problems,
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
_ARRAY = re.compile(r"@\((?:\x00S(?:, \x00S)*)?\)")
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


#: The plan's statement grammar: every line the plan writer can produce, with
#: typed holes -- ``<s>`` a single-quoted literal or ``[char]`` concatenation
#: term, ``@(<s>)`` a literal array of any length (empty included), ``<n>`` a
#: number, ``<bytes>`` a byte array -- and ``#`` a comment. Written out rather
#: than derived from the writer at run time; ``tests/test_egress.py`` holds it
#: equal to the shapes the writer produces over an exhaustive generator
#: (``tests/plan_corpus.py``) and a hypothesis-generated set, so it is complete
#: for the writer and has no dead entry (DeepSeek final-r11 B1: a reference
#: corpus missed ``$desiredApply = @()`` and an empty REG_MULTI_SZ, and egress
#: refused the writer's own output).
PLAN_GRAMMAR: frozenset[str] = frozenset(
    line.replace("<s>", _LITERAL)
    for line in (
        "",
        "        $desiredApply -notcontains $perm.Trustee.Name -and",
        "        $protected -notcontains $perm.Trustee.Name) {",
        "        Set-GPPermission -Guid $gpo.Id -PermissionLevel None -TargetName $perm.Trustee.Name -TargetType $perm.Trustee.SidType -ErrorAction SilentlyContinue",  # noqa: E501
        "    $gpo = Get-GPO -Name <s> -ErrorAction SilentlyContinue",
        "    $gpo = New-GPO -Name <s> -Comment <s>",
        "    Rename-GPO -Guid $gpo.Id -TargetName <s> | Out-Null",
        "    Where-Object { $_.GpoId -eq $gpo.Id }",
        "    if ($perm.Permission -eq <s> -and",
        "    }",
        "  <s>, <s>, <s>)",
        "#",
        "$ErrorActionPreference = <s>",
        "$desiredApply = @(<s>)",
        "$existing = Get-GPPermission -Guid $gpo.Id -All -ErrorAction SilentlyContinue",
        "$existing = Get-GPRegistryValue -Guid $gpo.Id -Key <s> -ErrorAction SilentlyContinue",
        "$existingLink = (Get-GPInheritance -Target <s>).GpoLinks |",
        "$gpo = Get-GPO -Guid <s> -ErrorAction SilentlyContinue",
        "$gpo.GpoStatus = <s>",
        "$protected = @(<s>, <s>,",
        "Remove-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -ErrorAction SilentlyContinue",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoApply -TargetName <s> -TargetType Computer -Replace | Out-Null",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoApply -TargetName <s> -TargetType Group -Replace | Out-Null",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoApply -TargetName <s> -TargetType User -Replace | Out-Null",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName <s> -TargetType Computer -Replace | Out-Null",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName <s> -TargetType Group -Replace | Out-Null",  # noqa: E501
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName <s> -TargetType User -Replace | Out-Null",  # noqa: E501
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type Binary -Value ([byte[]]@(<s>))",  # noqa: E501
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type Binary -Value <bytes>",
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type DWord -Value <n>",
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type ExpandString -Value <s>",
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type MultiString -Value @(<s>)",
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type QWord -Value <n>",
        "Set-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName <s> -Type String -Value <s>",
        "[CmdletBinding(SupportsShouldProcess=$true)]",
        "else { New-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled No -Enforced No -Order <n> | Out-Null }",  # noqa: E501
        "else { New-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled No -Enforced Yes -Order <n> | Out-Null }",  # noqa: E501
        "else { New-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled Yes -Enforced No -Order <n> | Out-Null }",  # noqa: E501
        "else { New-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled Yes -Enforced Yes -Order <n> | Out-Null }",  # noqa: E501
        "elseif ($gpo.DisplayName -ne <s>) {",
        "foreach ($perm in $existing) {",
        "if ($existing) { $existing | ForEach-Object { Remove-GPRegistryValue -Guid $gpo.Id -Key <s> -ValueName $_.ValueName -ErrorAction SilentlyContinue } }",  # noqa: E501
        "if ($existingLink) { Set-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled No -Enforced No -Order <n> | Out-Null }",  # noqa: E501
        "if ($existingLink) { Set-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled No -Enforced Yes -Order <n> | Out-Null }",  # noqa: E501
        "if ($existingLink) { Set-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled Yes -Enforced No -Order <n> | Out-Null }",  # noqa: E501
        "if ($existingLink) { Set-GPLink -Guid $gpo.Id -Target <s> -LinkEnabled Yes -Enforced Yes -Order <n> | Out-Null }",  # noqa: E501
        "if (-not $gpo) {",
        "param()",
        "}",
    )
)


def powershell_plan_problems(text: str, where: str = "plan.ps1") -> list[EmitProblem]:
    from .ps_plan_validator import validate_plan

    skeleton, problems = ps_skeleton(text)
    for number, shape in enumerate(skeleton, start=1):
        if shape not in PLAN_GRAMMAR:
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


#: Members each export must hold (Sol final-r11 B3): an empty or partial archive
#: is not the artifact, whatever a writer returned.
_STUDIO_REQUIRED = (
    re.compile(r"manifest\.json"),
    re.compile(r"apply\.ps1"),
    re.compile(r"Machine/Registry\.pol"),
    re.compile(r"User/Registry\.pol"),
)
_GPMC_REQUIRED = (
    re.compile(r"manifest\.xml"),
    re.compile(rf"{_GUID_DIR}/Backup\.xml"),
    re.compile(rf"{_GUID_DIR}/bkupInfo\.xml"),
)


def zip_problems(
    data: bytes,
    kind: Literal["studio", "gpmc"],
    model: Any = None,
    *,
    strict_elements: bool = False,
) -> list[EmitProblem]:
    """An export ZIP, member by member: by its `ZipInfo`, so a duplicate name
    cannot hide an earlier entry (``read(name)`` returns the last); every name
    safe, unique and of a known kind; every required member present; each
    member valid by its format; and, given the GPO, its preference files reading
    back as the GPO's preferences, with every element either in the closed
    vocabulary or held by retained content an import recorded for that file.

    Without the GPO there is no provenance to consult: *strict_elements* then
    holds every preference file to the closed vocabulary alone (the route
    backstop for bytes no checked builder produced, DeepSeek final-r13 N2)."""
    members = _STUDIO_MEMBERS if kind == "studio" else _GPMC_MEMBERS
    required = _STUDIO_REQUIRED if kind == "studio" else _GPMC_REQUIRED
    where = "export.zip" if kind == "studio" else "gpmc-backup"
    problems: list[EmitProblem] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        return [EmitProblem(where, "archive", "", f"is not a ZIP: {error}")]
    gpp_files: dict[str, dict[str, bytes]] = {}
    with archive:
        infos = archive.infolist()
        if not infos:
            return [EmitProblem(where, "archive", "", "is empty")]
        names = [info.filename for info in infos]
        for name in sorted({n for n in names if names.count(n) > 1}):
            problems.append(EmitProblem(where, "member name", name, "appears more than once"))
        for pattern in required:
            if sum(1 for name in names if pattern.fullmatch(name)) != 1:
                problems.append(
                    EmitProblem(where, "member", pattern.pattern, "is not present exactly once")
                )
        for info in infos:
            name = info.filename
            if (
                not _SAFE_NAME.fullmatch(name)
                or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or info.is_dir()
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
            with archive.open(info) as handle:
                content = handle.read()
            problems += _member_problems(f"{where}:{name}", kinds[0], content)
            if kinds[0] == "gpp":
                side, relative = _gpp_side(name)
                gpp_files.setdefault(side, {})[relative] = content
    if model is not None and not problems:
        problems += gpp_file_element_problems(where, model, gpp_files)
        problems += gpp_agreement_problems(where, model, gpp_files)
    elif strict_elements and not problems:
        for side, files in sorted(gpp_files.items()):
            for relative, content in sorted(files.items()):
                problems += gpp_element_problems(f"{where}:{side}/{relative}", content)
    return problems


def gpp_file_element_problems(
    where: str, gpo: Any, files_by_side: Mapping[str, Mapping[str, bytes]]
) -> list[EmitProblem]:
    """Every element of each exported preference file outside the closed
    vocabulary and outside the retained content an IMPORT recorded for that
    file's families (final-r13; per file, DeepSeek final-r13 N1).

    Provenance is the record `gpp_provenance` keeps, not the content's
    presence: retained content without a record -- stored through an older
    API, or migrated from a workspace whose history does not show an import --
    is held to the vocabulary, and a refusal names the items to re-import or
    delete."""
    from . import gpp_provenance
    from .gpp import _gpp_file_families

    file_families = _gpp_file_families()
    by_scope = {collection.scope: collection for collection in gpo.gpp_collections}
    problems: list[EmitProblem] = []
    for side, files in sorted(files_by_side.items()):
        collection = by_scope.get(side)
        for relative, content in sorted(files.items()):
            families = file_families.get(relative, ())
            verified: frozenset[str] = frozenset()
            if collection is not None:
                verified = gpp_provenance.element_names(collection, families, verified=True)
            found = gpp_element_problems(f"{where}:{side}/{relative}", content, verified)
            if found and collection is not None:
                unverified = gpp_provenance.element_names(collection, families, verified=False)
                found = [
                    replace(
                        problem,
                        problem=(
                            f"{problem.problem}: it is retained content with no import "
                            "record ("
                            + ", ".join(
                                gpp_provenance.unverified_items(
                                    collection, families, problem.name
                                )[:5]
                            )
                            + "). Re-import the GPO from its source backup, or delete "
                            "these items"
                        ),
                    )
                    if problem.name in unverified
                    else problem
                    for problem in found
                ]
            problems += found
    return problems


def _gpp_side(name: str) -> tuple[str, str]:
    """(scope, path under Preferences/) of a GPP member."""
    side, _, relative = name.partition("/Preferences/")
    return ("computer" if side.endswith("Machine") else "user"), relative


def _typed_projection(collection: Any) -> dict[str, list[tuple[Any, ...]]]:
    """Per family, per item: the common options, the typed targeting predicates
    and the number of raw ones -- what a retained attribute or a raw predicate
    could otherwise change behind the model (Sol final-r11 B1)."""
    import dataclasses

    from .ilt import IltPredicate

    projection: dict[str, list[tuple[Any, ...]]] = {}
    for field in dataclasses.fields(collection):
        items = getattr(collection, field.name)
        if not (isinstance(items, tuple) and items and hasattr(items[0], "common")):
            continue
        rows: list[tuple[Any, ...]] = []
        for item in items:
            predicates = item.ilt_filter.items if item.ilt_filter is not None else ()
            rows.append(
                (
                    item.common,
                    # The writer strips a padded IP range (validation does too);
                    # compare what it means, not its padding.
                    tuple(
                        replace(p, value=p.value.strip())
                        for p in predicates
                        if isinstance(p, IltPredicate)
                    ),
                    sum(1 for p in predicates if isinstance(p, str)),
                )
            )
        projection[field.name] = rows
    return projection


def gpp_agreement_problems(
    where: str, gpo: Any, files_by_side: Mapping[str, Mapping[str, bytes]]
) -> list[EmitProblem]:
    """The exported preference files must read back as the GPO's preferences:
    each item's common options and targeting as the model holds them."""
    from .gpp import GppError, parse_gpp_collection

    problems: list[EmitProblem] = []
    model = {collection.scope: collection for collection in gpo.gpp_collections}
    for side in sorted(set(model) | set(files_by_side)):
        files = dict(files_by_side.get(side, {}))
        try:
            written = parse_gpp_collection(side, files)  # type: ignore[arg-type]
        except (GppError, ValueError) as error:
            problems.append(EmitProblem(where, side, "", f"preferences do not read back: {error}"))
            continue
        expected = _typed_projection(model[side]) if side in model else {}
        if _typed_projection(written) != expected:
            problems.append(
                EmitProblem(
                    where,
                    f"{side} preferences",
                    "",
                    "do not read back as the GPO's preferences (common options or targeting)",
                )
            )
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
            body = _body(result)
            problems = zip_problems(body, "studio", strict_elements=not _was_checked(body))
        case "zip-gpmc":
            body = _body(result)
            problems = zip_problems(body, "gpmc", strict_elements=not _was_checked(body))
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


# ---------------------------------------------------------------------------
# Checked artifacts: what the routes return AND what the capabilities ask
# ---------------------------------------------------------------------------
#
# One function per artifact builds it with the writer and validates it here,
# with the GPO, so the preference files must also read back as the model. The
# route returns its result; the capability is whether it raises. A capability
# and a download can therefore never disagree (DeepSeek final-r11 B1: the plan
# capability asked only the writer, and egress refused what it advertised).


#: Digests of the archives a checked builder validated with their GPO, so the
#: route backstop knows which bytes already passed the provenance-aware
#: element check (DeepSeek final-r13 N2). Bytes no checked builder produced --
#: a future route that writes an archive itself -- are held to the closed
#: vocabulary alone. Bounded: a route checks the bytes it just built.
_CHECKED_ARCHIVES: OrderedDict[str, None] = OrderedDict()
_CHECKED_ARCHIVES_SIZE = 64
_CHECKED_LOCK = threading.Lock()


def _remember_checked(data: bytes) -> None:
    key = hashlib.sha256(data).hexdigest()
    with _CHECKED_LOCK:
        _CHECKED_ARCHIVES[key] = None
        _CHECKED_ARCHIVES.move_to_end(key)
        while len(_CHECKED_ARCHIVES) > _CHECKED_ARCHIVES_SIZE:
            _CHECKED_ARCHIVES.popitem(last=False)


def _was_checked(data: bytes) -> bool:
    with _CHECKED_LOCK:
        return hashlib.sha256(data).hexdigest() in _CHECKED_ARCHIVES


def checked_plan(gpo: Any) -> str:
    from . import export

    text = export.powershell_plan(gpo)
    problems = powershell_plan_problems(text)
    if problems:
        raise EmitError(problems)
    return text


def checked_studio_bundle(gpo: Any) -> bytes:
    from . import export

    data = export.export_bundle(gpo)
    problems = zip_problems(data, "studio", gpo)
    if problems:
        raise EmitError(problems)
    _remember_checked(data)
    return data


def checked_gpmc_backup(gpo: Any, scripts: Mapping[str, Any] | None = None) -> bytes:
    from . import export

    data = export.gpmc_backup_bundle(gpo, scripts=scripts)
    problems = zip_problems(data, "gpmc", gpo)
    if problems:
        raise EmitError(problems)
    _remember_checked(data)
    return data


def _refusal(build: Callable[[], Any], gpo: Any, path: str, *, blocked_first: bool) -> Any:
    """Why *build* would refuse, or ``None`` -- by running it."""
    from .export import _plan_precondition, _writer_refusal
    from .model import ValidationError
    from .validation import validate_gpo

    if blocked_first:
        precondition = _plan_precondition(gpo)
        if precondition is not None:
            return precondition
        if any(issue.severity == "error" for issue in validate_gpo(gpo)):
            return None  # reported by validation: the caller's `blocked`
    try:
        build()
    except (ValidationError, ValueError) as error:
        return _writer_refusal(error, path)
    return None


def plan_refusal(gpo: Any) -> Any:
    """Why `checked_plan` refuses this GPO, or ``None`` (the route's own function)."""
    return _refusal(lambda: checked_plan(gpo), gpo, "plan.ps1", blocked_first=True)


def studio_export_refusal(gpo: Any) -> Any:
    """Why `checked_studio_bundle` refuses this GPO, or ``None``."""
    return _refusal(lambda: checked_studio_bundle(gpo), gpo, "export.zip", blocked_first=True)


def gpmc_backup_refusal(gpo: Any, scripts: Mapping[str, Any] | None = None) -> Any:
    """Why `checked_gpmc_backup` refuses this GPO, or ``None``."""
    return _refusal(
        lambda: checked_gpmc_backup(gpo, scripts), gpo, "gpmc-backup", blocked_first=False
    )
