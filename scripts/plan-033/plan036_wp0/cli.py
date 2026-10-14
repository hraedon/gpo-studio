"""Command line for the WP-0 tooling (`scripts/plan-033/wp0-live-closure.py`)."""

from __future__ import annotations

import argparse
import json
import locale
import os
import sys
from pathlib import Path
from typing import Any

from . import changed, record, scan
from .repo import REPO_ROOT, Wp0Error, is_clean, rev_parse
from .tracer import ENV_ALLOWLIST


def _python_version(value: str | None) -> str:
    return value or f"{sys.version_info.major}.{sys.version_info.minor}"


def _environment() -> dict[str, Any]:
    return {
        "variables": {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ},
        "locale": list(locale.getlocale()),
        "preferred_encoding": locale.getpreferredencoding(False),
        "tool_python": list(sys.version_info[:3]),
    }


def _summary(result: dict[str, Any]) -> str:
    lines = [f"target {result['target']}"]
    part1 = result["part1"]
    lines.append(
        f"part 1: {len(part1['analysed_modules'])} modules analysed (mypy, Python "
        f"{part1['python_version']}, {part1['platform']}); completeness problems: "
        f"{len(part1['completeness'])}; unused declarations: {len(part1['unused_declarations'])}"
    )
    for name, outcome in sorted(result["lanes"].items()):
        lane1 = part1["lanes"][name]
        replayed = outcome["replay"]
        selection = result["selection"][name]
        replay_text = (
            "n/a" if not replayed.get("applicable")
            else ("identical" if replayed.get("identical") else f"DIFFERS {replayed['problems']}")
        )
        lines.append(
            f"  {name:40} closure {len(lane1['closure']):3}  scan-fail "
            f"{len(lane1['scan']['failing']):4}  replay {replay_text:9}  re-grade "
            f"{'equal' if outcome['regrade_equal'] else 'DIFFERS'}  fidelity "
            f"{'equal' if outcome['fidelity_equal'] else 'DIFFERS'}  "
            f"{'SELECTED' if selection['selected'] else '-'} {selection['reasons']}"
        )
    lines.append(f"option 2 selects {len(result['option2'])}: {result['option2']}")
    lines.append(f"option 3 would select {len(result['option3'])}: {result['option3']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wp0-live-closure.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    derive = sub.add_parser("derive", help="part 1 on the working tree")
    derive.add_argument("--python-version")
    derive.add_argument("--out", type=Path, required=True)

    runp = sub.add_parser("run", help="parts 1 and 2 and D-9's selection at a commit")
    runp.add_argument("--target", required=True)
    runp.add_argument("--workdir", type=Path, required=True)
    runp.add_argument("--python-version")
    runp.add_argument("--lanes", help="comma-separated lane names (default: every live lane)")
    runp.add_argument("--launcher", choices=["uv", "current"], default="uv")

    select = sub.add_parser("select", help="the convergence rule over selection passes")
    select.add_argument("passes", type=Path, nargs="+", help="result.json of each pass, in order")

    write = sub.add_parser("write-records", help="full run at HEAD, then the successor and pin")
    write.add_argument("--workdir", type=Path, required=True)
    write.add_argument("--python-version", required=True)

    sub.add_parser("check-pin", help="the release-only closure gate, on the working tree")

    bank = sub.add_parser("check-bank", help="step 8: B is F's child and changes no dependency")
    bank.add_argument("--freeze", required=True, help="the freeze commit F")
    bank.add_argument("--bank", default="HEAD", help="the banking commit B (default HEAD)")

    args = parser.parse_args(argv)
    try:
        if args.command == "derive":
            part1 = record.derive_part1(REPO_ROOT, _python_version(args.python_version))
            args.out.write_text(json.dumps(part1, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
            failing = sum(len(lane["scan"]["failing"]) for lane in part1["lanes"].values())
            print(f"wrote {args.out}; {failing} failing scan sites; "
                  f"{len(part1['completeness'])} completeness problems; "
                  f"{len(part1['unused_declarations'])} declarations that do not match the code")
            for line in part1["unused_declarations"][:20]:
                print(f"  {line}")
            ok = not failing and not part1["completeness"] and not part1["unused_declarations"]
            return 0 if ok else 1
        if args.command == "run":
            wanted = set(args.lanes.split(",")) if args.lanes else None
            args.workdir.mkdir(parents=True, exist_ok=False)
            result = record.run(REPO_ROOT, args.target, args.workdir,
                                _python_version(args.python_version), lanes_wanted=wanted,
                                launcher=args.launcher)
            (args.workdir / "result.json").write_text(
                json.dumps(result, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
            )
            successor = record.successor_record(result, _environment())
            (args.workdir / "successor-draft.json").write_text(
                json.dumps(successor, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(_summary(result))
            refused = record.refusals(result)
            print(f"records could {'NOT ' if refused else ''}be written from this run"
                  + (f": {len(refused)} refusals, first {refused[:3]}" if refused else ""))
            return 0
        if args.command == "select":
            passes, option3 = [], set()
            for path in args.passes:
                data = json.loads(path.read_text(encoding="utf-8"))
                passes.append(set(data["option2"]))
                option3 = set(data["option3"])
            print(json.dumps(changed.converge(passes, option3), indent=1))
            return 0
        if args.command == "write-records":
            head = rev_parse(REPO_ROOT, "HEAD")
            if not is_clean(REPO_ROOT):
                raise Wp0Error("write-records needs a clean tree at the freeze commit")
            args.workdir.mkdir(parents=True, exist_ok=False)
            result = record.run(REPO_ROOT, head, args.workdir, args.python_version)
            refused = record.write_records(REPO_ROOT, result, _environment())
            if refused:
                print("REFUSED: the records are written only from a run with no difference:",
                      file=sys.stderr)
                for line in refused:
                    print(f"  {line}", file=sys.stderr)
                return 1
            print(f"wrote {record.SUCCESSOR_RECORD} and {record.PIN_RECORD}")
            return 0
        if args.command == "check-bank":
            pin_path = REPO_ROOT / record.PIN_RECORD
            if not pin_path.is_file():
                print(f"no pin at {record.PIN_RECORD}: B must carry it")
                return 1
            pin = json.loads(pin_path.read_text("utf-8"))
            problems = record.bank_problems(REPO_ROOT, args.freeze, args.bank, pin)
            for problem in problems:
                print(problem)
            return 1 if problems else 0
        if args.command == "check-pin":
            pin_path = REPO_ROOT / record.PIN_RECORD
            if not pin_path.is_file():
                print(f"no pin at {record.PIN_RECORD} (WI-083: written at the freeze)")
                return 1
            problems = record.verify_pin(REPO_ROOT, json.loads(pin_path.read_text("utf-8")))
            for problem in problems:
                print(problem)
            return 1 if problems else 0
    except Wp0Error as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover


__all__ = ["main", "scan"]
