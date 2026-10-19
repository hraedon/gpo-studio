"""Every text file Studio's code opens names its encoding.

Without ``encoding=``, Python decodes and encodes text with the locale's code
page -- UTF-8 on Linux, cp1252 on a Windows runner -- so a UTF-8 source file
read by ``scripts/check_safety.py`` failed there (UnicodeDecodeError
'charmap', test-windows run 39182659419) while every Linux job passed. This
holds ``src/``, ``scripts/`` and the test helpers this branch added to an
explicit encoding: the builtin ``open`` in a text mode, and ``Path.read_text``,
``Path.write_text`` and ``Path.open`` in a text mode, each need one (a
positional one counts). Binary modes, ``os.open`` (a file descriptor) and the
archive modules' ``open`` are not text opens.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = (
    ROOT / "src",
    ROOT / "scripts",
    ROOT / "tests" / "oracles" / "legacy_58b80aa.py",
    ROOT / "tests" / "native_artifacts.py",
)

#: Text opens left as they are, each with why. These scripts' bytes are
#: bound by banked evidence (the firewall lane's verification records name
#: them), so an edit would need the lane re-run; they run on the Linux
#: controller, never on Windows, and read and write files they wrote.
EXEMPT: dict[tuple[str, str], str] = {
    ("scripts/windows-oracle/finalize_firewall_run.py", "read_text"): "evidence-bound harness",
    ("scripts/windows-oracle/finalize_firewall_run.py", "write_text"): "evidence-bound harness",
    ("scripts/plan-033/sanitize-firewall-fixtures.py", "write_text"): "evidence-bound harness",
}

#: Receivers whose ``.open`` is not a file's text open.
NOT_TEXT_OPENERS = {"os", "tarfile", "zipfile", "archive", "gzip", "bz2", "lzma"}


def _mode(call: ast.Call, position: int) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    if len(call.args) > position and isinstance(call.args[position], ast.Constant):
        return str(call.args[position].value)
    return None


def _encodingless(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "open":
            mode = _mode(node, 1) or "r"
            if "b" not in mode and len(node.args) < 4:
                found.append((node.lineno, "open"))
        elif isinstance(function, ast.Attribute):
            receiver = function.value
            receiver_name = receiver.id if isinstance(receiver, ast.Name) else ""
            if function.attr == "read_text" and not node.args:
                found.append((node.lineno, "read_text"))
            elif function.attr == "write_text" and len(node.args) < 2:
                found.append((node.lineno, "write_text"))
            elif function.attr == "open" and receiver_name not in NOT_TEXT_OPENERS:
                mode = _mode(node, 0) if node.args or node.keywords else "r"
                # A Path's open: text unless its mode says binary. Calls that
                # pass a non-mode first argument (another module's open) are
                # left alone: the mode is then not a literal string.
                if mode is not None and "b" not in mode and len(node.args) < 3:
                    found.append((node.lineno, "open"))
    return found


def _files() -> list[Path]:
    files: list[Path] = []
    for path in SCANNED:
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return files


def test_every_text_open_names_its_encoding() -> None:
    problems = []
    for path in _files():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, kind in _encodingless(tree):
            if (relative, kind) not in EXEMPT:
                problems.append(f"{relative}:{line}: {kind} without encoding=")
    assert problems == []


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("open(p)", True),
        ("open(p, 'w')", True),
        ("open(p, 'rb')", False),
        ("open(p, encoding='utf-8')", False),
        ("p.read_text()", True),
        ("p.read_text('utf-8')", False),
        ("p.write_text(s)", True),
        ("p.write_text(s, 'utf-8')", False),
        ("p.open('w')", True),
        ("p.open('wb')", False),
        ("p.open()", True),
        ("os.open(p, flags)", False),
        ("tarfile.open(p)", False),
    ],
)
def test_the_check_sees_an_encodingless_open(source: str, caught: bool) -> None:
    """Control for the check itself."""
    assert bool(_encodingless(ast.parse(source))) is caught, source
