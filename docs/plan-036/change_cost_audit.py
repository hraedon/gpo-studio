#!/usr/bin/env python3
"""Plan 036's change-cost audit: every lane re-qualification, classified.

Reads the banked evidence records under ``docs/plan-033/*-evidence/`` and git
history, pairs each *banked qualifying record* with the previous one of the
same lane, and records what changed between them and which proposed policy
would have made the estate run unnecessary.

A banked qualifying record is a JSON object with a ``source.commit`` (not all
zeros), ``source.dirty`` false, a bound file record (``source.files``, or
``source.bound`` for WP-0), a ``run_id``, and no explicit ``passed: false``. Three WP-0
``manifest.json`` records carry no ``passed`` member at all (their schema
records ``capability.evidence_state`` instead); they are included, and the
record lists them under ``schema_exceptions``. Requiring ``passed is True``
would give 225 records, 25 lanes and 200 pairs. The output is
``docs/plan-036/change-cost-audit.json``; Plan 036 quotes its summary.

This is a record, not a gate. No test or workflow runs it, and it changes
nothing it reads. Re-running it on the same tree must reproduce the committed
record byte for byte; that is the check a reviewer applies. The PowerShell
normalisation needs ``pwsh``; without it every PowerShell comparison would be
``null`` and the record would differ, so ``--write`` refuses to run without
``pwsh``. A PowerShell file that does not parse normalises to ``null``, never
to a digest.

Candidate identity is conservative: a pair whose either side records no
candidate hashes counts as *not* identical (one RSoP pair is excluded this
way), so the replay counts are lower bounds of what the rule would admit.

    python -I docs/plan-036/change_cost_audit.py            # summary to stdout
    python -I docs/plan-036/change_cost_audit.py --write    # rewrite the record

Policies, all *conditional*: a pair counted as eligible would have been
discharged only if the proposed proof obligations had held for it.

* ``normalised``: every changed bound file compares equal after removing
  Python docstrings (AST), PowerShell comments (pwsh tokens; needs ``pwsh``),
  or shell comment lines.
* ``replay``: only controller-side Python changed (builder, finalizer,
  ``oracle_evidence.py``, product modules), no guest script, runner or
  transport, and the two verdicts record identical candidate hashes.
* ``transport``: ``psdirect.ps1`` is the only changed bound file.
* ``either``: the replay rule with ``psdirect.ps1`` also allowed to change.
  The candidate must still be identical.

A pair whose bound file *set* differs is never eligible: its two verdicts bind
different things.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "plan-033"
RECORD = ROOT / "docs" / "plan-036" / "change-cost-audit.json"
OCTOBER = "2026-10-01"

_RUNNER_TO_FINALIZER = {
    "run-windows-oracle.sh": "oracle",
    "run-wp1b-oracle.sh": "wp1b",
    "run-wp2-oracle.sh": "wp2_import",
    "run-wp3-oracle.sh": "wp3",
    "run-object-security-oracle.sh": "object_security",
    "run-scripts-backup-oracle.sh": "scripts_backup",
    "run-publication-oracle.sh": "publication",
    "run-report-parity-oracle.sh": "report_parity",
    "run-firewall-oracle.sh": "firewall",
    "run-fdeploy-oracle.sh": "fdeploy",
    "run-endpoint-oracle.sh": "endpoint",
    "run-rsop-oracle.sh": "rsop",
    "run-rsop-user-oracle.sh": "rsop_user",
    "run-lifecycle-oracle.sh": "lifecycle",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def _show(commit: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def _files(source: dict[str, Any]) -> dict[str, str] | None:
    files = source.get("files")
    if isinstance(files, dict):
        return {str(k): str(v) for k, v in files.items()}
    bound = source.get("bound")
    if isinstance(bound, list):
        return {str(e["path"]).rsplit("/", 1)[-1]: str(e["sha256"]) for e in bound}
    return None


def _lane(names: set[str], verdict: dict[str, Any], relative: str) -> str:
    family = None
    for name in sorted(names):
        if name.startswith("finalize_") and name.endswith("_run.py"):
            family = name[len("finalize_") : -len("_run.py")]
    if family is None:
        runners = sorted(n for n in names if n in _RUNNER_TO_FINALIZER)
        family = _RUNNER_TO_FINALIZER[runners[0]] if runners else "oracle"
    family = {"oracle": "wp0", "wp2": "wp2_import"}.get(family, family)
    if family in {"rsop", "rsop_user"}:
        return f"{family}:{verdict.get('scenario_id')}"
    if family == "wp3":
        env = verdict.get("environment") or {}
        is_dc = env.get("computer_system_domain_role") == 5 or "/wp3-dc/" in relative
        is_dc = is_dc or "/dc/" in relative
        return "wp3:dc" if is_dc else "wp3:member"
    return family


def _candidate(verdict: dict[str, Any]) -> str | None:
    if verdict.get("candidate"):
        return json.dumps(verdict["candidate"], sort_keys=True)
    delivery = verdict.get("candidate_delivery")
    if isinstance(delivery, dict):
        if "controller_sha256" in delivery:
            return str(delivery["controller_sha256"])
        return json.dumps(
            {
                k: v.get("controller_sha256") if isinstance(v, dict) else v
                for k, v in delivery.items()
            },
            sort_keys=True,
        )
    return None


def collect() -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for path in sorted(EVIDENCE.glob("*-evidence/**/*.json")):
        try:
            verdict = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(verdict, dict) or not isinstance(verdict.get("source"), dict):
            continue
        source = verdict["source"]
        commit = str(source.get("commit", ""))
        files = _files(source)
        run_id = verdict.get("run_id")
        if not commit or set(commit) == {"0"} or source.get("dirty") or not files:
            continue
        if verdict.get("passed") is False or not run_id or run_id in seen:
            continue
        seen.add(run_id)
        relative = path.relative_to(EVIDENCE).as_posix()
        out.append(
            {
                "file": relative,
                "run_id": run_id,
                "commit": commit,
                "files": files,
                "paths": source.get("paths") or {},
                "lane": _lane(set(files), verdict, relative),
                "candidate": _candidate(verdict),
                "no_passed_member": "passed" not in verdict,
            }
        )
    for item in out:
        # A commit missing from the clone sorts first and its files resolve to
        # nothing, so its pair is recorded with `normalised_equal: null`.
        shown = subprocess.run(
            ["git", "show", "-s", "--format=%cI", item["commit"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        item["committed"] = shown.stdout.strip() if shown.returncode == 0 else ""
    return out


def _role(name: str) -> str:
    if name == "psdirect.ps1":
        return "transport"
    if name.endswith((".ps1", ".psm1")):
        return "guest"
    if name.endswith(".sh"):
        return "runner"
    if name.endswith(".py"):
        return "controller-python"
    return "fixture"


_TREES: dict[str, list[str]] = {}


def _resolve(name: str, commit: str, hints: list[dict[str, str]]) -> str | None:
    for hint in hints:
        if name in hint:
            return hint[name]
    if commit not in _TREES:
        listed = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", commit],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        _TREES[commit] = listed.stdout.splitlines() if listed.returncode == 0 else []
    matches = [
        p for p in _TREES[commit] if p.rsplit("/", 1)[-1] == name and not p.startswith("docs/")
    ]
    return matches[0] if len(matches) == 1 else None


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            first = body[0].value if body and isinstance(body[0], ast.Expr) else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _py_norm(data: bytes) -> str:
    return ast.dump(_strip_docstrings(ast.parse(data)), include_attributes=False)


def _sh_norm(data: bytes) -> list[bytes]:
    return [x.rstrip() for x in data.split(b"\n") if x.strip() and not x.lstrip().startswith(b"#")]


_PS_SCRIPT = r"""
param([string]$ListFile)
$sha = [System.Security.Cryptography.SHA256]::Create()
foreach ($p in Get-Content -LiteralPath $ListFile) {
  $tokens = $null; $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors)
  $sb = [System.Text.StringBuilder]::new(); $nl = $true
  foreach ($t in $tokens) {
    if ($t.Kind -eq 'Comment') { continue }
    if ($t.Kind -eq 'NewLine' -or $t.Kind -eq 'LineContinuation') {
      if ($nl) { continue }; [void]$sb.Append("`n"); $nl = $true; continue }
    $nl = $false
    [void]$sb.Append($t.Kind.ToString()).Append([char]0x1f).Append($t.Text).Append([char]0x1e)
  }
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($sb.ToString())
  $h = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
  if ($errors.Count -gt 0) { $h = 'PARSE-ERROR' }
  "$h $p"
}
"""


def _ps_norms(blobs: dict[str, bytes]) -> dict[str, str | None] | None:
    if not blobs or shutil.which("pwsh") is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        names = {}
        for index, key in enumerate(sorted(blobs)):
            path = Path(tmp) / f"{index}.ps1"
            path.write_bytes(blobs[key])
            names[str(path)] = key
        listing = Path(tmp) / "list.txt"
        listing.write_text("\n".join(names) + "\n", encoding="utf-8")
        script = Path(tmp) / "norm.ps1"
        script.write_text(_PS_SCRIPT, encoding="utf-8")
        output = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script), "-ListFile", str(listing)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    result: dict[str, str | None] = {}
    for line in output.splitlines():
        digest, path = line.split(" ", 1)
        result[names[path]] = None if digest == "PARSE-ERROR" else digest
    return result


def pairs(verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lanes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in verdicts:
        lanes[item["lane"]].append(item)
    rows: list[dict[str, Any]] = []
    ps_blobs: dict[str, bytes] = {}
    for lane in sorted(lanes):
        ordered = sorted(lanes[lane], key=lambda v: (v["committed"], v["run_id"]))
        for a, b in zip(ordered, ordered[1:], strict=False):
            names_a, names_b = set(a["files"]), set(b["files"])
            changed = []
            for name in sorted(names_a & names_b):
                if a["files"][name] == b["files"][name]:
                    continue
                hints = [b["paths"], a["paths"]]
                path_b = _resolve(name, b["commit"], hints)
                path_a = _resolve(name, a["commit"], hints) or path_b
                entry: dict[str, Any] = {"name": name, "role": _role(name), "path": path_b}
                old = _show(a["commit"], path_a) if path_a else None
                new = _show(b["commit"], path_b) if path_b else None
                if old is None or new is None:
                    entry["normalised_equal"] = None
                elif name.endswith(".py"):
                    try:
                        entry["normalised_equal"] = _py_norm(old) == _py_norm(new)
                    except SyntaxError:
                        entry["normalised_equal"] = None
                elif name.endswith((".ps1", ".psm1")):
                    key_a = f"{a['commit']}:{path_a}"
                    key_b = f"{b['commit']}:{path_b}"
                    ps_blobs[key_a], ps_blobs[key_b] = old, new
                    entry["_ps"] = (key_a, key_b)
                elif name.endswith(".sh"):
                    entry["normalised_equal"] = _sh_norm(old) == _sh_norm(new)
                else:
                    entry["normalised_equal"] = old.replace(b"\r\n", b"\n") == new.replace(
                        b"\r\n", b"\n"
                    )
                changed.append(entry)
            rows.append(
                {
                    "lane": lane,
                    "predecessor": {
                        "run_id": a["run_id"],
                        "commit": a["commit"],
                        "file": a["file"],
                    },
                    "successor": {"run_id": b["run_id"], "commit": b["commit"], "file": b["file"]},
                    "october": b["committed"] >= OCTOBER,
                    "bound_set_changed": names_a != names_b,
                    "candidate_identical": a["candidate"] is not None
                    and a["candidate"] == b["candidate"],
                    "changed": changed,
                }
            )
    ps = _ps_norms(ps_blobs)
    for row in rows:
        for entry in row["changed"]:
            keys = entry.pop("_ps", None)
            if keys is not None:
                old_digest = None if ps is None else ps[keys[0]]
                new_digest = None if ps is None else ps[keys[1]]
                both = old_digest is not None and new_digest is not None
                entry["normalised_equal"] = old_digest == new_digest if both else None
        roles = {e["role"] for e in row["changed"]}
        estate_side = roles & {"guest", "runner"}
        python_only = bool(roles) and roles <= {"controller-python"}
        eligible_set = not row["bound_set_changed"]
        row["kind"] = _kind(row, roles)
        row["eligible"] = {
            "normalised": eligible_set
            and bool(row["changed"])
            and all(e["normalised_equal"] is True for e in row["changed"]),
            "replay": eligible_set and python_only and row["candidate_identical"],
            "transport": eligible_set and roles == {"transport"},
            "either": eligible_set
            and bool(roles)
            and not estate_side
            and roles <= {"controller-python", "transport"}
            and (roles == {"transport"} or row["candidate_identical"]),
        }
    return rows


def _kind(row: dict[str, Any], roles: set[str]) -> str:
    if row["bound_set_changed"]:
        return "bound set edited"
    if not roles:
        return "no bound file changed"
    side = sorted(roles & {"guest", "runner", "transport"})
    if side:
        return "+".join(side)
    return "controller python, candidate " + (
        "identical" if row["candidate_identical"] else "changed"
    )


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, subset in (("all", rows), ("october", [r for r in rows if r["october"]])):
        out[label] = {
            "pairs": len(subset),
            "kinds": dict(sorted(Counter(r["kind"] for r in subset).items())),
            "eligible": {
                k: sum(r["eligible"][k] for r in subset)
                for k in ("normalised", "replay", "transport", "either")
            },
            "normalised_equal_files": sum(
                e["normalised_equal"] is True for r in subset for e in r["changed"]
            ),
        }
    return out


def build() -> dict[str, Any]:
    verdicts = collect()
    rows = pairs(verdicts)
    tool = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {
        "schema_version": 2,
        "tool_sha256": tool,
        "evidence_tree": _git("rev-parse", "HEAD:docs/plan-033").strip(),
        "pwsh_normalisation": shutil.which("pwsh") is not None,
        "schema_exceptions": sorted(v["file"] for v in verdicts if v["no_passed_member"]),
        "verdicts": len(verdicts),
        "lanes": len({v["lane"] for v in verdicts}),
        "summary": summary(rows),
        "pairs": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the committed record")
    args = parser.parse_args(argv)
    if args.write and shutil.which("pwsh") is None:
        print("refusing --write: pwsh is needed for the PowerShell comparisons", file=sys.stderr)
        return 2
    record = build()
    text = json.dumps(record, indent=1, sort_keys=True) + "\n"
    if args.write:
        RECORD.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {k: record[k] for k in ("verdicts", "lanes", "summary")}, indent=1, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")
    sys.exit(main())
