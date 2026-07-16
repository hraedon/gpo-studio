"""Static safety checks for the GPO Studio codebase.

Verifies that the web process contains no:
- Direct AD/SMB/SYSVOL write dependencies (ldap, smb, win32, GroupPolicy)
- Shell execution (subprocess, os.system, shlex, pty)
- Unsafe XML parsing (ET.fromstring/ET.parse without bounded wrapper)
- Forbidden publication code in the web process

Exit 0 if all checks pass, exit 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "gpo_studio"

FORBIDDEN_IMPORTS: dict[str, list[str]] = {
    "Direct AD/SMB/SYSVOL dependencies": [
        "ldap",
        "ldap3",
        "smb",
        "smbprotocol",
        "win32security",
        "win32net",
        "win32com",
        "pywintypes",
        "ctypes.wintypes",
    ],
    "Shell execution": [
        "subprocess",
        "os.system",
        "shlex",
        "pty",
        "commands",
    ],
}


def _check_imports(tree: ast.Module, filepath: Path) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                for category, forbidden in FORBIDDEN_IMPORTS.items():
                    if module in forbidden or alias.name in forbidden:
                        violations.append(
                            f"{filepath}:{node.lineno}: forbidden import "
                            f"'{alias.name}' ({category})"
                        )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            for category, forbidden in FORBIDDEN_IMPORTS.items():
                if top in forbidden or module in forbidden:
                    violations.append(
                        f"{filepath}:{node.lineno}: forbidden import "
                        f"'{module}' ({category})"
                    )
    return violations


def _check_unsafe_xml(tree: ast.Module, filepath: Path) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in ("fromstring", "parse"):
                value = node.func.value
                if (
                    isinstance(value, ast.Name)
                    and value.id in ("ET", "ElementTree")
                    and filepath.name != "xml_safety.py"
                ):
                    violations.append(
                        f"{filepath}:{node.lineno}: unsafe XML call "
                        f"ET.{attr_name}() — use parse_xml_bounded() "
                        f"from xml_safety.py instead"
                    )
    return violations


def _check_publication_code(tree: ast.Module, filepath: Path) -> list[str]:
    if filepath.name != "api.py":
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name_lower = node.name.lower()
            if any(kw in name_lower for kw in ("publish", "sysvol_write", "ad_write")):
                violations.append(
                    f"{filepath}:{node.lineno}: publication/write function "
                    f"'{node.name}' found in web process — publication must be "
                    f"an explicit adapter boundary"
                )
    return violations


def main() -> int:
    if not SRC_DIR.exists():
        print(f"error: source directory not found: {SRC_DIR}", file=sys.stderr)
        return 1

    all_violations: list[str] = []

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as e:
            all_violations.append(f"{py_file}: syntax error: {e}")
            continue

        all_violations.extend(_check_imports(tree, py_file))
        all_violations.extend(_check_unsafe_xml(tree, py_file))
        all_violations.extend(_check_publication_code(tree, py_file))

    if all_violations:
        print("Static safety check violations:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        print(f"\nTotal: {len(all_violations)} violation(s)", file=sys.stderr)
        return 1

    print("Static safety checks passed: no forbidden imports, unsafe XML, or publication code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
