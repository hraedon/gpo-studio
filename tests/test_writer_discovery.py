"""Every function that writes artifact text is registered with emit_integrity.

Final-r9 found a live PowerShell writer -- the AD discovery scripts -- with the
typographic-quote code-execution bug the plan had just been fixed for, because
the list of writers was kept by hand and did not name it (DeepSeek B1). This
test reads the source instead: it finds every function under
``src/gpo_studio`` that builds PowerShell, INI/INF, serialized XML,
Registry.pol, UTF-16 text or ``|``-delimited records, or that defines a quoting
helper, and requires each to be either in
:data:`gpo_studio.emit_integrity.ARTIFACT_WRITERS` (with the check it runs) or
in :data:`NOT_WRITERS` below (with why it writes nothing). Planted modules show
each detection rule fires.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from gpo_studio.emit_integrity import ARTIFACT_WRITERS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "gpo_studio"

PS_MARKERS = (
    "param(",
    "$ErrorActionPreference",
    "#Requires",
    "Set-GP",
    "Get-GP",
    "New-GP",
    "New-Object System.",
)
INI_HEADER = re.compile(r"^\[[A-Za-z][A-Za-z ]*\]|^\[\{\}\]")

#: Functions the scan flags that write no artifact, and why.
NOT_WRITERS: dict[str, str] = {
    "api._quoted": "a request-model validator for a quoted INF field",
    "api._quoted_field_problem": "a validator for a quoted INF field",
    "api._firewall_limitations": "limitation prose naming cmdlets",
    "api.gpmc_backup_with_scripts_preview": "decodes the scripts files of a built bundle",
    "conformance.malformed_preg_truncated": "malformed parser input; never exported",
    "conformance.malformed_preg_invalid_type": "malformed parser input; never exported",
    "emit_integrity.inf_problems": "the INF check itself",
    "emit_integrity.scripts_ini_problems": "the scripts.ini check itself",
    "export._plan_precondition": "a refusal message naming Set-GPPermission",
    "fdeploy.decode_fdeploy": "reader",
    "fdeploy.parse_fdeploy": "reader",
    "fdeploy.validate_fdeploy": "validator",
    "fdeploy.native_digest": "reader",
    "fdeploy.format_fdeploy": "no caller outside fdeploy: Folder Redirection is read-only (WI-066)",
    "fdeploy.encode_fdeploy": "no caller outside fdeploy: Folder Redirection is read-only (WI-066)",
    "fdeploy_parity.encoding_facts": "reader",
    "gpp._capture_unknown_children": (
        "import: renders retained children to text that serialize_gpp writes and checks"
    ),
    "gpp_adapters._extract_task_xml": "import: reads a task payload",
    "ilt.parse_ilt": "reader",
    "object_security._strip_quotes": "reader",
    "ps_plan_validator._strip_single_quoted_tracked": "validator",
    "ps_plan_validator.validate_plan": "validator",
    "registry_pol.parse": "reader",
    "report_parity.label": "a lane comparison label",
    "script_policy._has_unquoted_metacharacters": "validator",
    "script_policy.quote_parameter": "no caller in src (its tests only)",
    "security_template.decode_security_template": "reader",
    "security_template.validate_security_template": "validator",
    "snapshot_documents.extract_fdeploy": "reader",
    "wmi_filter._strip_string_literals": "validator",
    "writer_conformance._report_side_collection": "reads a GPMC report document",
    "xml_safety._normalize_xml_encoding": "reader",
}
#: NOT_WRITERS entries whose reason is "no caller": checked, not trusted.
_UNUSED = ("script_policy.quote_parameter", "fdeploy.format_fdeploy", "fdeploy.encode_fdeploy")


def _body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    body: list[ast.stmt] = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # a docstring describes; it writes nothing
    return [node for statement in body for node in ast.walk(statement)]


def _texts(nodes: list[ast.AST]) -> list[str | bytes]:
    found: list[str | bytes] = []
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.append(
                "".join(
                    part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
                )
            )
    return found


def _constants(tree: ast.Module) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            source = ast.unparse(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if "PReg" in source:
                        kinds[target.id] = "registry-pol"
                    if "\\xff\\xfe" in source:
                        kinds[target.id] = "utf16-text"
    return kinds


def _kinds(
    function: ast.FunctionDef | ast.AsyncFunctionDef, constants: dict[str, str]
) -> set[str]:
    found: set[str] = set()
    if re.search(r"quote|literal", function.name):
        found.add("quote-helper")
    nodes = _body(function)
    for text in _texts(nodes):
        if isinstance(text, bytes):
            if text.startswith(b"PReg"):
                found.add("registry-pol")
            if text.startswith(b"\xff\xfe"):
                found.add("utf16-text")
            continue
        if any(marker in text for marker in PS_MARKERS):
            found.add("powershell")
        if INI_HEADER.match(text):
            found.add("ini")
    for node in nodes:
        if isinstance(node, ast.Name) and node.id in constants:
            found.add(constants[node.id])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("tostring", "tostringlist"):
                found.add("xml")
            if (
                node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and node.func.value.value == "|"
            ):
                found.add("delimited-record")
    return found


def scan(source: Path) -> dict[str, set[str]]:
    """``module.function`` -> what it builds, for every function that builds any."""
    found: dict[str, set[str]] = {}
    for path in sorted(source.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                kinds = _kinds(node, constants)
                if kinds:
                    found[f"{path.stem}.{node.name}"] = kinds
    return found


def test_every_writer_in_the_source_is_registered() -> None:
    discovered = scan(SOURCE)
    unregistered = sorted(set(discovered) - set(ARTIFACT_WRITERS) - set(NOT_WRITERS))
    assert not unregistered, (
        "these functions write artifact text and are not registered in "
        f"emit_integrity.ARTIFACT_WRITERS (or explained in NOT_WRITERS): "
        f"{[(name, sorted(discovered[name])) for name in unregistered]}"
    )


def test_no_registration_is_stale() -> None:
    discovered = set(scan(SOURCE))
    assert not sorted(set(ARTIFACT_WRITERS) - discovered)
    assert not sorted(set(NOT_WRITERS) - discovered)
    assert not set(ARTIFACT_WRITERS) & set(NOT_WRITERS)


@pytest.mark.parametrize("name", _UNUSED)
def test_a_writer_said_to_be_unused_has_no_caller(name: str) -> None:
    function = name.split(".")[1]
    calls = [
        f"{path.name}:{node.lineno}"
        for path in sorted(SOURCE.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == function)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == function)
        )
    ]
    assert not calls, f"{name} has callers: {calls}"


def test_no_function_doubles_quotes_by_hand() -> None:
    """One PowerShell quoting primitive (`emit_integrity.ps_literal`): a
    ``.replace("'", "''")`` anywhere else is a second one, which is how the
    discovery scripts kept the bug the plan had fixed."""
    found = []
    for path in sorted(SOURCE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "replace"
                and len(node.args) == 2
                and all(isinstance(a, ast.Constant) for a in node.args)
                and [a.value for a in node.args] == ["'", "''"]  # type: ignore[attr-defined]
            ):
                found.append(f"{path.name}:{node.lineno}")
    assert not found, found


# --- planted writers: each rule fires --------------------------------------------------

_PLANTS = {
    "quote-helper": 'def _ps_quote(v):\n    return "\'" + v.replace("\'", "\'\'") + "\'"\n',
    "powershell": 'def script(d):\n    return f"param([string]$Domain = {d})"\n',
    "ini": 'def ini(name):\n    return f"[{name}]\\r\\n"\n',
    "xml": "import xml.etree.ElementTree as ET\ndef doc(e):\n    return ET.tostring(e)\n",
    "registry-pol": 'HEAD = b"PReg" + b"\\x01"\ndef pol():\n    return HEAD\n',
    "utf16-text": 'BOM = b"\\xff\\xfe"\ndef text(s):\n    return BOM + s.encode("utf-16-le")\n',
    "delimited-record": 'def rule(parts):\n    return "|".join(parts)\n',
}


@pytest.mark.parametrize("kind", sorted(_PLANTS))
def test_a_planted_writer_is_found(tmp_path: Path, kind: str) -> None:
    (tmp_path / "planted.py").write_text(_PLANTS[kind], encoding="utf-8")
    found = scan(tmp_path)
    assert any(kind in kinds for kinds in found.values()), found
    unregistered = set(found) - set(ARTIFACT_WRITERS) - set(NOT_WRITERS)
    assert unregistered, "a planted writer would have passed the registration test"


def test_a_docstring_alone_is_not_a_writer(tmp_path: Path) -> None:
    (tmp_path / "prose.py").write_text(
        'def f():\n    """Compared with Get-GPOReport and param( prose."""\n    return 1\n',
        encoding="utf-8",
    )
    assert scan(tmp_path) == {}
