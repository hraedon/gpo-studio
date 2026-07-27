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

# The web process is everything reachable from the FastAPI delivery layer.
WEB_PROCESS_ENTRYPOINT = "api"

# Modules exempt from a forbidden-import category because they are lab or
# release tooling that never executes inside the web process.  An exemption is
# only honoured while the module stays unreachable from WEB_PROCESS_ENTRYPOINT;
# _check_exempt_unreachable() fails the build the moment one becomes reachable,
# so an exemption cannot silently widen into a charter breach.
#
# oracle_evidence: shells out to `git` (rev-parse/status/show) to compute source
# provenance and bind harness inputs to a commit for Plan 033 evidence packs.
# It is driven by scripts/windows-oracle/, never by a request path.
CATEGORY_EXEMPTIONS: dict[str, set[str]] = {
    "Shell execution": {"oracle_evidence"},
}


def _module_imports(tree: ast.Module) -> set[str]:
    """Return the in-package modules a parsed module imports."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:
                imported.add(node.module.split(".")[0])
            elif node.module and node.module.startswith("gpo_studio."):
                imported.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("gpo_studio."):
                    imported.add(alias.name.split(".")[1])
    return imported


def _web_process_modules(trees: dict[str, ast.Module]) -> set[str]:
    """Compute the module names transitively reachable from the web process."""
    reachable: set[str] = set()
    stack = [WEB_PROCESS_ENTRYPOINT]
    while stack:
        name = stack.pop()
        if name in reachable or name not in trees:
            continue
        reachable.add(name)
        stack.extend(_module_imports(trees[name]) - reachable)
    return reachable


def _check_exempt_unreachable(reachable: set[str]) -> list[str]:
    """Fail if any exempt module has become part of the web process."""
    violations: list[str] = []
    for category, exempt in CATEGORY_EXEMPTIONS.items():
        for module in sorted(exempt & reachable):
            violations.append(
                f"{module}.py: exempt from '{category}' but now reachable from "
                f"{WEB_PROCESS_ENTRYPOINT}.py — the exemption assumes this module "
                f"never runs in the web process. Remove the import or the exemption."
            )
    return violations


def _is_exempt(category: str, filepath: Path) -> bool:
    return filepath.stem in CATEGORY_EXEMPTIONS.get(category, set())


def _check_imports(tree: ast.Module, filepath: Path) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                for category, forbidden in FORBIDDEN_IMPORTS.items():
                    if _is_exempt(category, filepath):
                        continue
                    if module in forbidden or alias.name in forbidden:
                        violations.append(
                            f"{filepath}:{node.lineno}: forbidden import "
                            f"'{alias.name}' ({category})"
                        )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            for category, forbidden in FORBIDDEN_IMPORTS.items():
                if _is_exempt(category, filepath):
                    continue
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
    trees: dict[str, ast.Module] = {}

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as e:
            all_violations.append(f"{py_file}: syntax error: {e}")
            continue

        trees[py_file.stem] = tree
        all_violations.extend(_check_imports(tree, py_file))
        all_violations.extend(_check_unsafe_xml(tree, py_file))
        all_violations.extend(_check_publication_code(tree, py_file))

    all_violations.extend(_check_exempt_unreachable(_web_process_modules(trees)))

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
