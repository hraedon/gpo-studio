"""Prove the round-trip, parity and import->store->export tests detect broken code.

A round-trip test that compares Studio with itself can pass against code that
reads nothing. In 2026-09 the fdeploy round trip was effectively ``t == t``:
``format_fdeploy`` handed back ``raw_text`` when a reparse matched, and the test
passed against a parser mutated to return zero sections
(``reflections/2026-09-11-opus-5.md``). This script restores defects of that
kind -- a parser that reads nothing, a serialiser that drops what it was given,
a comparator that always says "equal", an export that ignores what import
retained (WI-080) or writes a name Windows does not write (WI-081) -- and
requires the tests named for each defect to fail.

Run it from the repository root::

    uv run python scripts/mutation_check_roundtrip.py            # every mutant
    uv run python scripts/mutation_check_roundtrip.py --list     # the table
    uv run python scripts/mutation_check_roundtrip.py --only NAME [--only NAME ...]
    uv run python scripts/mutation_check_roundtrip.py --tmp-root DIR

It never touches the worktree. Each run (the baseline and every mutant) copies
the files git tracks -- no ``.git``, environments, caches or scratch, whatever
they are called -- into a fresh temporary directory under ``--tmp-root`` (else
``$TMPDIR`` or the system default), which must be outside the repository: a root
inside it is refused before anything is created. Each copy is about 58 MB, so
pass ``--tmp-root`` where the temp directory is RAM-backed. It edits the copy
and runs pytest there with ``PYTHONPATH`` pointing at the copy's ``src``. Before
each pytest run it checks that ``import gpo_studio`` resolves to that copy, so
an editable install in ``.venv`` cannot shadow the mutant. At the end it checks
the worktree's mutated files are byte-identical to how it found them.

First, a baseline: every required test must pass on the unmutated copy, or a
"kill" would mean nothing. Then a mutant is KILLED only if pytest exits 1 and
EVERY required node id appears in a ``FAILED <nodeid>`` line. A collection or
usage error (exit 2-5), a timeout, or an import failure is ERROR, never a kill.

A SURVIVED mutant is a finding, not a flaky check: the named test still passes
with the defect in place, so it is checking that Studio agrees with itself
rather than checking the behaviour it names. Do not weaken the mutant or swap
its tests to make it pass; fix (or report) the test.

Exit status: 0 every mutant killed; 1 at least one survived (names listed);
2 an anchor is missing or ambiguous, the setup or baseline failed, or a mutant
run ended in ERROR.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent

_PYTEST_TIMEOUT_SECONDS = 1800
_TAIL_LINES = 40
_SUMMARY = re.compile(r"^(\d+ \w+(?:, \d+ \w+)*) in [\d.]+s")

Outcome = Literal["KILLED", "SURVIVED", "ERROR"]


class SetupError(RuntimeError):
    """The check itself cannot run: a missing anchor, a shadowed import, a bad baseline."""


@dataclass(frozen=True, slots=True)
class Mutant:
    name: str
    file: str
    anchor: str
    replacement: str
    required: tuple[str, ...]
    why: str


FDEPLOY = "src/gpo_studio/fdeploy.py"
GPP = "src/gpo_studio/gpp.py"
ADAPTERS = "src/gpo_studio/gpp_adapters.py"
T_FDEPLOY = "tests/test_fdeploy.py"
T_NATIVE = "tests/test_gpp_native_preservation.py"
T_NAMES = "tests/test_gpp_typed_attribute_names.py"
T_WRITER = "tests/test_writer_conformance.py"
T_PARITY = "tests/test_report_parity.py"
T_POL = "tests/test_registry_pol.py"
PRINTERS = "[WI01A-Printers-GPMC]"
SHORTCUTS = "[WI01A-Shortcuts-GPMC]"

MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        name="fdeploy-parse-zero-sections",
        file=FDEPLOY,
        anchor="        sections=tuple(sections),\n        raw_text=text,",
        replacement="        sections=(),\n        raw_text=text,",
        required=(
            f"{T_FDEPLOY}::test_the_native_capture_decodes_and_parses_to_its_banked_shape",
            f"{T_FDEPLOY}::test_the_reader_round_trips_the_native_bytes_exactly",
            f"{T_FDEPLOY}::test_unrecognised_sections_and_lines_survive_verbatim",
            f"{T_FDEPLOY}::test_a_non_canonical_section_round_trips_from_its_own_lines",
        ),
        why="The 2026-09-11 mutation: the parser reads no section at all.",
    ),
    Mutant(
        name="fdeploy-format-passthrough",
        file=FDEPLOY,
        anchor="    out: list[str] = list(document.preamble)\n",
        replacement=(
            "    if document.raw_text:\n"
            "        return document.raw_text\n"
            "    out: list[str] = list(document.preamble)\n"
        ),
        required=(f"{T_FDEPLOY}::test_the_serializer_never_hands_back_the_parsed_input",),
        why="format_fdeploy hands back the source text instead of serialising.",
    ),
    Mutant(
        name="fdeploy-format-passthrough-when-reparse-matches",
        file=FDEPLOY,
        anchor="    out: list[str] = list(document.preamble)\n",
        replacement=(
            "    if document.raw_text and parse_fdeploy(document.raw_text) == document:\n"
            "        return document.raw_text\n"
            "    out: list[str] = list(document.preamble)\n"
        ),
        required=(f"{T_FDEPLOY}::test_the_serializer_never_hands_back_the_parsed_input",),
        why=(
            "The exact 2026-09-11 defect (copied from security_template.py): return "
            "raw_text when it re-parses to the document, making format(parse(t)) == t "
            "into t == t."
        ),
    ),
    Mutant(
        name="gpp-adapter-parse-no-items",
        file=GPP,
        anchor="                    results[adapter_key] = items\n",
        replacement="                    results[adapter_key] = ()\n",
        required=(
            f"{T_NATIVE}::test_stored_gpo_serializes_to_the_native_xml{PRINTERS}",
            f"{T_NATIVE}::test_stored_gpo_serializes_to_the_native_xml{SHORTCUTS}",
            f"{T_NATIVE}::test_public_exports_write_the_native_xml{PRINTERS}",
            f"{T_PARITY}::test_studio_import_matches_the_windows_report_of_the_same_backup"
            "[tests/fixtures/native-gpp-gpmc/WI01A-Printers-GPMC]",
        ),
        why=(
            "Import parses every adapter-family preference file (printers, shortcuts, "
            "...) to no items; an unstored import would still return its source bytes."
        ),
    ),
    Mutant(
        name="import-drops-user-preferences",
        file="src/gpo_studio/import_export.py",
        anchor='[("Machine", "computer"), ("User", "user")]',
        replacement='[("Machine", "computer")]',
        required=(
            f"{T_NATIVE}::test_stored_gpo_serializes_to_the_native_xml{PRINTERS}",
            f"{T_NATIVE}::test_public_exports_write_the_native_xml{SHORTCUTS}",
        ),
        why="Backup import collects the Machine side's preference XML and drops User's.",
    ),
    Mutant(
        name="backup-drops-fdeploy",
        file="src/gpo_studio/backup.py",
        anchor="                fdeploy=fdeploy,\n",
        replacement="                fdeploy=None,\n",
        required=(
            "tests/test_fdeploy_on_gpo.py"
            "::test_read_backup_parses_the_native_policy_file_onto_the_backup_gpo",
            "tests/test_fdeploy_on_gpo.py"
            "::test_import_report_persistence_and_fork_carry_the_document",
        ),
        why="read_backup parses fdeploy1.ini and then drops it from the BackupGpo.",
    ),
    Mutant(
        name="export-ignores-retained-element",
        file=GPP,
        anchor=(
            '    raw: str = getattr(item, "native_xml", "")\n'
            "    if not raw:\n"
            "        return rendered\n"
        ),
        replacement='    raw: str = ""\n    if not raw:\n        return rendered\n',
        required=(
            f"{T_NATIVE}::test_stored_gpo_serializes_to_the_native_xml{PRINTERS}",
            f"{T_NATIVE}::test_public_exports_write_the_native_xml{PRINTERS}",
            f"{T_NATIVE}::test_an_unmodelled_attribute_survives_an_edit_of_the_item",
            f"{T_NATIVE}::test_an_edit_to_a_typed_field_wins_and_the_rest_stays_as_imported",
        ),
        why="WI-080 restored: every export rebuilds preference XML from the typed model.",
    ),
    Mutant(
        name="merge-drops-unmodelled-attributes",
        file="src/gpo_studio/gpp_native.py",
        anchor="            merged.append((name, value))  # never typed: the edit cannot",
        replacement="            pass  # MUTANT: drop it -- never typed: the edit cannot",
        required=(
            f"{T_NATIVE}::test_an_unmodelled_attribute_survives_an_edit_of_the_item",
            f"{T_NATIVE}::test_an_edit_to_a_typed_field_wins_and_the_rest_stays_as_imported",
        ),
        why=(
            "WI-080, edited items: the merge drops every retained attribute the model "
            "does not type (a printer's location/port/deleteAll, a shortcut's pidl ...)."
        ),
    ),
    Mutant(
        name="run-once-id-regenerated",
        file=GPP,
        anchor="        if common.run_once_id:\n",
        replacement="        if False:\n",
        required=(
            f"{T_NATIVE}::test_the_imported_run_once_id_is_kept_through_storage_and_edits",
            f"{T_NATIVE}::test_apply_once_off_then_on_restores_the_same_identity",
        ),
        why="WI-080: the writer mints a new FilterRunOnce id, so clients re-apply.",
    ),
    Mutant(
        name="printer-writes-studio-name",
        file=ADAPTERS,
        anchor='            "default": _bool_str(printer.set_default),\n',
        replacement='            "setDefault": _bool_str(printer.set_default),\n',
        required=(
            f"{T_NAMES}::test_the_writer_types_no_attribute_name_a_capture_lacks",
            f"{T_NAMES}::test_an_authored_printer_writes_gpmcs_names_in_gpmcs_order",
            f"{T_NAMES}::test_an_edited_import_writes_gpmcs_names",
        ),
        why="WI-081 restored: the writer types setDefault, a name no capture contains.",
    ),
    Mutant(
        name="drive-writer-drops-label",
        file=ADAPTERS,
        anchor='            "label": drive.label,\n',
        replacement="",
        required=(
            f"{T_WRITER}::test_native_round_trip_is_semantically_identical",
            f"{T_WRITER}::test_isolated_family_round_trips[drives]",
        ),
        why="The Drive Maps serialiser omits the label attribute it was given.",
    ),
    Mutant(
        name="writer-conformance-values-always-equal",
        file="src/gpo_studio/writer_conformance.py",
        anchor="    if expected != actual:\n        differences.append(",
        replacement="    if False:\n        differences.append(",
        required=(
            f"{T_WRITER}::test_meaning_changes_are_reported[path]",
            f"{T_WRITER}::test_meaning_changes_are_reported[action]",
            f"{T_WRITER}::test_meaning_changes_are_reported[persistent]",
            f"{T_WRITER}::test_registry_value_change_is_reported",
            f"{T_WRITER}::test_registry_type_change_is_reported",
        ),
        why="The WP-1B summary comparator never reports a changed scalar.",
    ),
    Mutant(
        name="report-parity-compare-always-equal",
        file="src/gpo_studio/report_parity.py",
        anchor="studio_count=len(ours), divergences=tuple(divergences),",
        replacement="studio_count=len(ours), divergences=(),",
        required=(
            f"{T_PARITY}::test_missing_and_extra_items_are_named",
            f"{T_PARITY}::test_duplicates_are_counted_not_collapsed",
            f"{T_PARITY}::test_order_is_reported_only_when_the_items_agree",
            f"{T_PARITY}::test_a_family_on_one_side_only_is_wholly_missing",
        ),
        why="The report-parity comparator records no divergence for any family.",
    ),
    Mutant(
        name="fdeploy-parity-always-agrees",
        file="src/gpo_studio/fdeploy_parity.py",
        anchor="    return sorted(set(problems))\n",
        replacement="    return []\n",
        required=(
            "tests/test_fdeploy_lane.py::test_an_error_rendering_is_not_an_empty_agreement",
            "tests/test_fdeploy_lane.py::test_an_empty_destination_disagrees_with_the_full_path",
            "tests/test_fdeploy_lane.py::test_case_differences_are_disagreements",
        ),
        why="Studio's fdeploy reading and Windows' report rendering always 'agree'.",
    ),
    Mutant(
        name="admx-parse-drops-policies",
        file="src/gpo_studio/admx.py",
        anchor="        )\n    return policies\n\n\ndef parse_admx(",
        replacement="        )\n    return policies[:1]\n\n\ndef parse_admx(",
        required=(
            "tests/test_admx.py::test_parse_simple_admx",
            "tests/test_admx_real.py::test_parse_windowssettings_admx",
            "tests/test_admx_real.py::test_load_catalogue_loads_all_files",
        ),
        why="ADMX parsing keeps the first policy of each file and drops the rest.",
    ),
    Mutant(
        name="registry-pol-parse-no-records",
        file="src/gpo_studio/registry_pol.py",
        anchor="        records.append(PolRecord(key, value_name, registry_type, value, action))\n",
        replacement="        pass\n",
        required=(
            f"{T_POL}::test_round_trip_supported_types[REG_SZ-hello]",
            f"{T_POL}::test_round_trip_supported_types[REG_DWORD-4294967295]",
            f"{T_POL}::test_round_trip_supported_types[REG_MULTI_SZ-value5]",
            f"{T_POL}::test_delete_marker_round_trip",
            "tests/test_properties.py::test_registry_pol_round_trip_property",
        ),
        why="Registry.pol parsing validates every record and returns none of them.",
    ),
    Mutant(
        name="registry-pol-serialize-drops-deletes",
        file="src/gpo_studio/registry_pol.py",
        anchor='        elif record.action == "delete":\n',
        replacement='        elif record.action == "delete":\n            continue\n',
        required=(
            f"{T_POL}::test_delete_marker_round_trip",
            f"{T_POL}::test_delete_all_values_and_delete_ordering",
        ),
        why="Registry.pol serialisation silently drops every **del. record.",
    ),
)


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int | None
    output: str


def _anchor_count(mutant: Mutant) -> int:
    return (ROOT / mutant.file).read_text(encoding="utf-8").count(mutant.anchor)


def check_anchors(mutants: Sequence[Mutant]) -> None:
    """Every anchor occurs exactly once in the (unmodified) worktree file."""
    for mutant in mutants:
        if not (ROOT / mutant.file).is_file() or _anchor_count(mutant) != 1:
            raise SetupError(f"anchor disappeared/ambiguous: {mutant.name}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_tmp_root(requested: str | None) -> Path:
    """The parent for sandbox trees, refused when it is inside the repository.

    A temp root inside the checkout makes each copy of the tree contain the copy
    being made (Sol B3 / DeepSeek N6). Checked on resolved paths, before
    anything is created, and for the default too: ``$TMPDIR`` may point inside.
    """
    root = Path(requested if requested is not None else tempfile.gettempdir()).resolve()
    repository = ROOT.resolve()
    if root == repository or repository in root.parents:
        raise SetupError(f"the temp root {root} is inside the repository {repository}")
    return root


def tracked_files() -> list[str]:
    """What a sandbox holds: the files git tracks, as they are in the worktree.

    Not a copy of the directory: scratch, environments, caches and anything
    else untracked never enter a sandbox, whatever they are called.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [name for name in listed.split("\0") if name and (ROOT / name).is_file()]


def _make_sandbox(parent: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    holder = tempfile.TemporaryDirectory(prefix="gpo-studio-mutant-", dir=parent)
    tree = Path(holder.name) / "tree"
    for name in tracked_files():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target, follow_symlinks=False)
    return holder, tree


def _environment(tree: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTEST_ADDOPTS", "PYTHONHOME"}
    }
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _assert_imports_from(tree: Path, env: dict[str, str]) -> None:
    """``import gpo_studio`` in the pytest environment must be the sandbox's copy."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib, gpo_studio; print(pathlib.Path(gpo_studio.__file__).resolve())",
        ],
        cwd=tree,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    expected = (tree / "src" / "gpo_studio" / "__init__.py").resolve()
    found = probe.stdout.strip()
    if probe.returncode != 0 or Path(found) != expected:
        raise SetupError(
            f"gpo_studio does not resolve to the sandbox: expected {expected}, got "
            f"{found or '<nothing>'} (exit {probe.returncode}) {probe.stderr.strip()}"
        )


def _run_pytest(tree: Path, node_ids: Sequence[str]) -> RunResult:
    env = _environment(tree)
    _assert_imports_from(tree, env)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "-q",
        "-rfE",
        *node_ids,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=tree,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=_PYTEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout if isinstance(error.stdout, str) else ""
        return RunResult(None, f"{partial}\n<timed out after {_PYTEST_TIMEOUT_SECONDS}s>")
    return RunResult(completed.returncode, completed.stdout + completed.stderr)


def failed_node_ids(output: str) -> set[str]:
    """The node ids pytest's ``-rf`` summary names as ``FAILED <nodeid>``."""
    failed: set[str] = set()
    for line in output.splitlines():
        if line.startswith("FAILED "):
            failed.add(line.removeprefix("FAILED ").split(" - ", 1)[0].strip())
    return failed


def summary_counts(output: str) -> dict[str, int]:
    """The counts of pytest's final summary line (``3 passed, 1 warning in 0.5s``)."""
    for line in reversed(output.splitlines()):
        match = _SUMMARY.match(line.strip("= ").strip())
        if match:
            return {
                kind: int(number)
                for number, kind in (part.split(" ", 1) for part in match.group(1).split(", "))
            }
    return {}


def _tail(output: str) -> str:
    return "\n".join("    " + line for line in output.rstrip().splitlines()[-_TAIL_LINES:])


def _required_union(mutants: Sequence[Mutant]) -> list[str]:
    return sorted({node for mutant in mutants for node in mutant.required})


def run_baseline(mutants: Sequence[Mutant], tmp_root: Path) -> None:
    """Every required test passes on the unmutated tree, and none is skipped."""
    required = _required_union(mutants)
    holder, tree = _make_sandbox(tmp_root)
    with holder:
        result = _run_pytest(tree, required)
    counts = summary_counts(result.output)
    clean = {key: value for key, value in counts.items() if key not in {"warning", "warnings"}}
    if result.returncode != 0 or clean != {"passed": len(required)}:
        raise SetupError(
            f"baseline failed: the {len(required)} required tests must all pass "
            f"unmutated (exit {result.returncode}, outcome {counts})\n{_tail(result.output)}"
        )
    print(f"BASELINE: {len(required)} required tests pass on the unmutated tree", flush=True)


def run_mutant(mutant: Mutant, tmp_root: Path) -> tuple[Outcome, str]:
    holder, tree = _make_sandbox(tmp_root)
    with holder:
        target = tree / mutant.file
        source = target.read_text(encoding="utf-8")
        if source.count(mutant.anchor) != 1:
            raise SetupError(f"anchor disappeared/ambiguous: {mutant.name}")
        target.write_text(source.replace(mutant.anchor, mutant.replacement), encoding="utf-8")
        result = _run_pytest(tree, mutant.required)
    if result.returncode != 1:
        if result.returncode == 0:
            return "SURVIVED", "every required test passed\n" + _tail(result.output)
        return "ERROR", f"pytest exit {result.returncode}\n" + _tail(result.output)
    still_passing = [node for node in mutant.required if node not in failed_node_ids(result.output)]
    if still_passing:
        return "SURVIVED", "not FAILED: " + ", ".join(still_passing) + "\n" + _tail(result.output)
    return "KILLED", ""


def _select(only: Sequence[str]) -> tuple[Mutant, ...]:
    if not only:
        return MUTANTS
    known = {mutant.name for mutant in MUTANTS}
    unknown = sorted(set(only) - known)
    if unknown:
        raise SetupError(f"unknown mutant(s): {', '.join(unknown)}")
    return tuple(mutant for mutant in MUTANTS if mutant.name in set(only))


def _print_table(mutants: Sequence[Mutant]) -> None:
    for mutant in mutants:
        print(f"{mutant.name}  ({mutant.file})")
        print(f"  {mutant.why}")
        for node in mutant.required:
            print(f"    must fail: {node}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--list", action="store_true", help="print the mutants and exit")
    parser.add_argument(
        "--only", action="append", default=[], metavar="NAME", help="run only this mutant"
    )
    parser.add_argument(
        "--tmp-root",
        metavar="DIR",
        default=None,
        help=(
            "parent directory for the temporary trees, outside the repository "
            "(default: the system temp dir)"
        ),
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        mutants = _select(args.only)
        if args.list:
            _print_table(mutants)
            return 0
        tmp_root = resolve_tmp_root(args.tmp_root)
        tmp_root.mkdir(parents=True, exist_ok=True)
        check_anchors(mutants)
        guarded = sorted({mutant.file for mutant in mutants})
        before = {file: _digest(ROOT / file) for file in guarded}
        run_baseline(mutants, tmp_root)
        outcomes: dict[str, Outcome] = {}
        for mutant in mutants:
            outcome, detail = run_mutant(mutant, tmp_root)
            outcomes[mutant.name] = outcome
            print(f"{outcome}: {mutant.name}", flush=True)
            if detail:
                print(detail, flush=True)
        changed = [file for file in guarded if _digest(ROOT / file) != before[file]]
        if changed:
            raise SetupError(f"the worktree changed during the run: {', '.join(changed)}")
    except SetupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print(f"Total runtime: {time.monotonic() - started:.1f}s")
        return 2
    killed = [name for name, outcome in outcomes.items() if outcome == "KILLED"]
    survived = [name for name, outcome in outcomes.items() if outcome == "SURVIVED"]
    errored = [name for name, outcome in outcomes.items() if outcome == "ERROR"]
    print(f"Mutation check: {len(killed)}/{len(outcomes)} killed")
    if survived:
        print("SURVIVED (a required test is self-consistent, not checking): " + ", ".join(survived))
    if errored:
        print("ERROR (not a kill): " + ", ".join(errored))
    print(f"Total runtime: {time.monotonic() - started:.1f}s")
    if errored:
        return 2
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
