"""A final tag is held to its freeze commit F (owner decision, 2026-10-17).

For 1.1.0 every lane is requalified on the estate at a freeze commit F, the
runs are banked in an evidence-only commit B whose parent is F, the approval is
a records-only commit A, and the tag goes on A (``plans/036-change-cost.md``,
the D-9 addendum; WI-083). ``scripts/check_release_manifest.py`` checks that
on the tagged checkout: these tests build that history in temporary git
repositories and show each way it can go wrong being refused.

Git runs with no system or global configuration (``GIT_CONFIG_NOSYSTEM``,
``GIT_CONFIG_GLOBAL``), for the gate's own git calls as well as the fixture's,
and every file is written as LF bytes, so the tests behave the same on the
Windows CI leg.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_gate() -> ModuleType:
    # A name of its own, so this module does not replace the one
    # tests/test_release_manifest_gate.py loads.
    spec = importlib.util.spec_from_file_location(
        "check_release_manifest_freeze", REPO_ROOT / "scripts" / "check_release_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Freeze Gate Test",
    "GIT_AUTHOR_EMAIL": "gate@example.invalid",
    "GIT_COMMITTER_NAME": "Freeze Gate Test",
    "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}
#: Variables a git hook (or a developer's shell) can set that would point git at
#: another repository.
_GIT_REDIRECTS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
)


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _GIT_ENV.items():
        monkeypatch.setenv(name, value)
    for name in _GIT_REDIRECTS:
        monkeypatch.delenv(name, raising=False)


def _git(cwd: Path, *args: str) -> str:
    command = [
        "git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
        "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args,
    ]
    result = subprocess.run(
        command, cwd=cwd, env={**os.environ, **_GIT_ENV}, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8").strip()


def _write(root: Path, relative: str, content: str | bytes) -> None:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def _commit(root: Path, files: dict[str, str | bytes | None], message: str) -> str:
    for relative, content in files.items():
        if content is None:
            _git(root, "rm", "-q", "--", relative)
        else:
            _write(root, relative, content)
            _git(root, "add", "--", relative)
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD")



def _json(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


def _registry_json(data: dict[str, Any]) -> str:
    """The registry's canonical form (the gate refuses any other)."""
    return json.dumps(data, indent=2, ensure_ascii=True) + "\n"


# --- the history: R (an earlier batch) -> F -> B -> A ------------------------

LANES = {
    "wp0": "wp0-evidence",
    "endpoint": "wp6-evidence",
    "loopback-merge": "wp9-evidence",
}
FINALIZERS = {"endpoint": "finalize_endpoint_run.py", "loopback-merge": "finalize_rsop_user_run.py"}
LABEL = "final-20261020"
BATCH = "docs/plan-033/final-batch.json"
REGISTRY = "docs/plan-033/verdict-registry.json"
REPORT = "docs/release-evidence-report-1.1.0.json"
MANIFEST = "docs/release-evidence-1.1.0.md"
CLOSED = "Done; the owner's hands-on NVDA acceptance is waived for 1.1.0 and not run."
#: The registry module the tests run: identical in every commit, as the gate requires.
REGISTRY_MODULE = (
    '"""Loads docs/plan-033/verdict-registry.json."""\n\nimport json\n\n'
    'DATA = json.load(open("docs/plan-033/verdict-registry.json"))\n'
)


def _verdict_path(lane: str, label: str) -> str:
    name = "manifest.json" if lane == "wp0" else "verification.json"
    return f"{LANES[lane]}/{label}/{lane}/{name}"


def _log(lane: str) -> bytes:
    return f"{lane} passed\r\n".encode()


def _verdict(lane: str, commit: str, run_id: str) -> dict[str, Any]:
    if lane == "wp0":
        return {
            "run_id": run_id,
            "capability": {"evidence_state": "pass"},
            "source": {"commit": commit, "dirty": False, "bound": {}},
        }
    return {
        "run_id": run_id,
        "passed": True,
        "transport": "psdirect",
        "source": {"commit": commit, "dirty": False, "files": {}},
    }


@dataclass
class Plan:
    """How to bank the batch at F. Each test changes one thing from the passing plan."""

    lanes: tuple[str, ...] = tuple(LANES)
    retire: tuple[str, ...] = tuple(FINALIZERS)
    verdict: Callable[[str, dict[str, Any], str, str], None] | None = None
    batch: Callable[[dict[str, Any], str], None] | None = None
    registry: Callable[[dict[str, Any]], None] | None = None
    #: Files written into B as well (None deletes); applied last.
    extra_banked: dict[str, str | bytes | None] = field(default_factory=dict)
    #: Lanes whose controller.log is already committed in F, byte for byte.
    carry_into_freeze: tuple[str, ...] = ()
    #: lane -> commit its evidence tag names, or None for no tag; default F.
    tags: dict[str, str | None] = field(default_factory=dict)
    lightweight_tags: bool = False
    #: F's verdicts are pending requalification, not live (a bound file changed
    #: before F, as the final-r6 sweep did): the batch must still clear them.
    pending_at_freeze: bool = False
    #: The endpoint lane's verdict is also RETIRED at F (DeepSeek final-r7 B2).
    overlap_at_freeze: bool = False


@dataclass
class History:
    root: Path
    earlier: str
    freeze: str
    banked: str
    approved: str


def _approve_files(root: Path) -> dict[str, str | bytes | None]:
    """The documented A: status, version, line 5 and the remaining items closed."""
    report = json.loads((root / REPORT).read_text("utf-8"))
    report["status"], report["version"] = "approved", "1.1.0"
    report["evidence"]["remaining_before_approval"] = [CLOSED]
    lines = (root / MANIFEST).read_text("utf-8").split("\n")
    lines[4] = gate.APPROVED
    start, end = gate._remaining_section(lines)
    lines[start + 1 : end] = ["", f"1. {CLOSED}", ""]
    return {REPORT: _json(report), MANIFEST: "\n".join(lines)}


def _candidate_records() -> tuple[dict[str, Any], str]:
    """The committed report and manifest, put back to a candidate's state.

    The fixture copies the real files so the gate meets their real shape, but the
    committed files move through the release (at A they are approved), so their
    stage is set here: candidate, no freeze, one item still pending.
    """
    report = json.loads((REPO_ROOT / REPORT).read_text("utf-8"))
    report.update(status="candidate", version="1.1.0rc1", freeze=None)
    report["evidence"]["remaining_before_approval"] = ["final approval (pending)"]
    lines = (REPO_ROOT / MANIFEST).read_text("utf-8").split("\n")
    lines[4] = gate.CANDIDATE
    start, end = gate._remaining_section(lines)
    assert start < len(lines), "the manifest has no Remaining before approval section"
    lines[start + 1 : end] = ["", "1. **Pending.** Final approval.", ""]
    return report, "\n".join(lines)


def _build(tmp_path: Path, plan: Plan | None = None) -> History:
    plan = plan or Plan()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    report, manifest_text = _candidate_records()
    rc_registry: dict[str, Any] = {
        "schema_version": 1,
        "lane_verdicts": {_verdict_path(lane, "rc"): fin for lane, fin in FINALIZERS.items()},
        "retired_verdicts": (
            [_verdict_path("endpoint", "rc")] if plan.overlap_at_freeze else []
        ),
        "pending_requalification": (
            sorted(_verdict_path(lane, "rc") for lane in FINALIZERS)
            if plan.pending_at_freeze else []
        ),
        "wp0_manifest": _verdict_path("wp0", "rc"),
    }
    common: dict[str, str | bytes | None] = {
        "src/gpo_studio/__init__.py": '"""GPO Studio."""\n\n__version__ = "1.1.0rc1"\n',
        "src/gpo_studio/core.py": "VALUE = 1\n",
        "scripts/tool.py": "print('tool')\n",
        "pyproject.toml": "[project]\nname = 'gpo-studio'\n",
        "uv.lock": "version = 1\n",
        ".github/workflows/release.yml": "name: release\n",
        ".gitattributes": "docs/plan-033/**/*.json -text\n",
        "SECURITY.md": "# Security\n",
        "CHANGELOG.md": "# Changelog\n",
        "README.md": "# GPO Studio\n",
        "docs/capability-matrix.md": "# Capability matrix\n",
        "docs/work-items.md": "# Work items\n",
        "docs/plan-033/environment-spec.md": "# Environment\n",
        "docs/plan-033/bound-source-cost.md": "# Cost\n",
        "docs/plan-033/firewall-results.md": "# Firewall results\n",
        "tests/fixtures/scenarios/platforms.json": _json({"runs": ["rc"]}),
        "tests/test_other.py": "def test_other() -> None:\n    assert True\n",
        "tests/test_committed_evidence.py": REGISTRY_MODULE,
        MANIFEST: manifest_text,
        REPORT: _json(report),
        REGISTRY: _registry_json(rc_registry),
    }
    earlier = _commit(root, common, "R: an earlier batch's tree")
    rc_verdicts: dict[str, str | bytes | None] = {
        f"docs/plan-033/{_verdict_path(lane, 'rc')}": _json(_verdict(lane, earlier, f"{lane}-rc"))
        for lane in LANES
    }
    _commit(root, rc_verdicts, "Bank the earlier batch")
    at_freeze: dict[str, str | bytes | None] = {
        "src/gpo_studio/__init__.py": '"""GPO Studio."""\n\n__version__ = "1.1.0"\n'
    }
    for lane in plan.carry_into_freeze:
        at_freeze[f"docs/plan-033/{LANES[lane]}/{LABEL}/{lane}/controller.log"] = _log(lane)
    freeze = _commit(root, at_freeze, "F: the freeze commit")

    banked: dict[str, str | bytes | None] = {}
    runs = []
    for lane in plan.lanes:
        run_id = f"{lane}-final"
        verdict = _verdict(lane, freeze, run_id)
        if plan.verdict is not None:
            plan.verdict(lane, verdict, freeze, earlier)
        pack = f"{LANES[lane]}/{LABEL}/{lane}"
        files = {
            _verdict_path(lane, LABEL).rsplit("/", 1)[1]: _json(verdict).encode(),
            "controller.log": _log(lane),
        }
        if lane != "wp0":
            files["controller-candidate/candidate.bin"] = bytes(range(8))
        for name, data in files.items():
            banked[f"docs/plan-033/{pack}/{name}"] = data
        runs.append({
            "name": lane,
            "run_id": run_id,
            "verdict": _verdict_path(lane, LABEL),
            "commit": freeze,
            "exit_status": 0,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        })
    cleanup = {
        "final-cleanup/collector.ps1": b"Get-ADObject\n",
        "final-cleanup/directory.json": _json({"result": "clean"}).encode(),
    }
    batch: dict[str, Any] = {
        "schema_version": 3,
        "source_commit": freeze,
        "driver": "scripts/plan-033/run-requal-batch.sh",
        "lanes_started": len(runs),
        "runs": runs,
        "not_passed": [],
        "successors": [],
        "post_batch_cleanup": {
            name: hashlib.sha256(data).hexdigest() for name, data in cleanup.items()
        },
    }
    if plan.batch is not None:
        plan.batch(batch, earlier)
    banked |= {f"docs/plan-033/{name}": data for name, data in cleanup.items()}
    banked[BATCH] = _json(batch)
    banked["docs/plan-033/final-batch.md"] = "# The final batch\n"
    registry = json.loads(json.dumps(rc_registry))
    registry["lane_verdicts"].update({
        _verdict_path(lane, LABEL): FINALIZERS[lane] for lane in plan.lanes if lane != "wp0"
    })
    registry["retired_verdicts"] = sorted(_verdict_path(lane, "rc") for lane in plan.retire)
    registry["pending_requalification"] = []
    registry["wp0_manifest"] = _verdict_path("wp0", LABEL)
    if plan.registry is not None:
        plan.registry(registry)
    banked[REGISTRY] = _registry_json(registry)
    report["freeze"] = {"commit": freeze, "batch_manifest": BATCH}
    banked[REPORT] = _json(report)
    banked["tests/fixtures/scenarios/platforms.json"] = _json({"runs": ["final"]})
    banked["docs/plan-033/firewall-results.md"] = "# Firewall results\n\nfirewall-final\n"
    banked |= plan.extra_banked
    banked_commit = _commit(root, banked, "B: bank the batch at F")
    for run in runs:
        target = plan.tags.get(run["name"], freeze)
        if target is None or not all(c.isalnum() or c == "-" for c in run["run_id"]):
            continue
        name = f"evidence/{run['run_id']}"
        if plan.lightweight_tags:
            _git(root, "tag", name, target)
        else:
            _git(root, "tag", "-a", "-m", f"Source tree for {run['run_id']}", name, target)
    approved = _commit(root, _approve_files(root), "A: approve 1.1.0")
    return History(root, earlier, freeze, banked_commit, approved)


def _verify(history: History) -> str:
    result = gate.check(history.root, "v1.1.0")
    summary: str = gate.verify_freeze(history.root, result)
    return summary


def _rebuild_on(history: History, base: str, commits: list[dict[str, str | bytes | None]]) -> None:
    """Reset to ``base`` and commit each change set in turn (a new history for the tag)."""
    _git(history.root, "reset", "-q", "--hard", base)
    for index, files in enumerate(commits):
        _commit(history.root, files, f"step {index}")


def _files_of(history: History, commit: str) -> dict[str, str | bytes | None]:
    """The paths a commit changed, with their contents after it (None: deleted)."""
    out = _git(history.root, "diff", "--name-status", "--no-renames", f"{commit}^", commit)
    files: dict[str, str | bytes | None] = {}
    for line in out.splitlines():
        status, path = line.split("\t", 1)
        files[path] = None if status == "D" else subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=history.root, env={**os.environ, **_GIT_ENV},
            capture_output=True, check=True,
        ).stdout
    return files


@pytest.fixture
def history(tmp_path: Path) -> History:
    return _build(tmp_path)


# --- the passing case ---------------------------------------------------------


def test_an_evidence_only_descendant_of_the_freeze_passes(
    history: History, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: every refusal below changes one thing from this history."""
    summary = _verify(history)
    assert f"freeze {history.freeze[:12]}: the tagged tree is F plus" in summary
    assert "records 3 runs at F, each with its evidence tag" in summary
    assert "does not authenticate the estate runs" in summary
    assert gate.main(["--tag", "v1.1.0", "--root", str(history.root)]) == 0
    assert "does not authenticate the estate runs" in capsys.readouterr().out


def test_a_records_only_commit_between_banking_and_approval_passes(history: History) -> None:
    approval = _files_of(history, history.approved)
    _rebuild_on(history, history.banked, [
        {"docs/work-items.md": "# Work items\n\nWI-083 closed.\n"}, approval,
    ])
    _verify(history)


# --- the tree: anything but evidence and records -------------------------------


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("src/gpo_studio/core.py", "VALUE = 2\n"),
        ("src/gpo_studio/planted.py", "import os\n"),
        ("scripts/tool.py", "print('changed')\n"),
        ("pyproject.toml", "[project]\nname = 'other'\n"),
        ("uv.lock", "version = 2\n"),
        (".github/workflows/release.yml", "name: changed\n"),
        (".gitattributes", "* -text\n"),
        ("SECURITY.md", "# Security, changed\n"),
        # Sol final-r1 B2: README is the wheel's long description; CHANGELOG ships.
        ("README.md", "# GPO Studio\n\nPOST-FREEZE-ALLOWLIST-PROBE\n"),
        ("CHANGELOG.md", "# Changelog\n\nThe final batch, at F.\n"),
        ("tests/test_other.py", "def test_other() -> None:\n    pass\n"),
        ("tests/test_planted.py", "def test_planted() -> None:\n    pass\n"),
        ("tests/test_committed_evidence.py", REGISTRY_MODULE + "# a comment\n"),
        ("docs/plan-033/wp6-evidence/rc/endpoint/verification.json", "{}\n"),
        ("docs/plan-033/wp6-evidence/rc/endpoint/extra.json", "{}\n"),
        ("docs/plan-033/other-batch.json", "{}\n"),
        ("docs/plan-033/new-results.md", "# A results document F did not have\n"),
        ("docs/work-items.md", None),
    ],
)
def test_a_change_outside_evidence_and_records_in_the_banking_commit_is_refused(
    tmp_path: Path, path: str, content: str | None
) -> None:
    history = _build(tmp_path, Plan(extra_banked={path: content}))
    with pytest.raises(gate.ReleaseGateError, match="differs from the freeze commit") as caught:
        _verify(history)
    assert path in str(caught.value)


def test_a_planted_change_under_src_fails_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _build(tmp_path, Plan(extra_banked={"src/gpo_studio/core.py": "VALUE = 2\n"}))
    assert gate.main(["--tag", "v1.1.0", "--root", str(history.root)]) == 1
    error = capsys.readouterr().err
    assert error.startswith("release gate: FAIL: ") and "M src/gpo_studio/core.py" in error
    assert "Traceback" not in error


def test_an_executable_evidence_file_is_refused(history: History) -> None:
    approval = _files_of(history, history.approved)
    _git(history.root, "reset", "-q", "--hard", history.banked)
    path = f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/run.sh"
    _write(history.root, path, "#!/bin/sh\n")
    os.chmod(history.root.joinpath(*path.split("/")), 0o755)
    _git(history.root, "add", "--", path)
    _git(history.root, "update-index", "--chmod=+x", "--", path)
    _git(history.root, "commit", "-q", "-m", "an executable in a pack")
    _commit(history.root, approval, "A")
    with pytest.raises(gate.ReleaseGateError, match=r"run\.sh \(mode 100755\)"):
        _verify(history)


def test_a_file_python_could_load_in_a_pack_is_refused(tmp_path: Path) -> None:
    """Sol final-r1 B3 / DeepSeek probe 4: a hashed conftest.py added to a pack."""
    payload = "raise RuntimeError('arbitrary post-freeze Python payload')\n"

    def add_hash(batch: dict[str, Any], earlier: str) -> None:
        run = next(row for row in batch["runs"] if row["name"] == "endpoint")
        run["files"]["conftest.py"] = hashlib.sha256(payload.encode()).hexdigest()

    plan = Plan(batch=add_hash, extra_banked={
        f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/conftest.py": payload,
    })
    refusal = "importable modules or .pth files: \\['conftest.py'\\]"
    with pytest.raises(gate.ReleaseGateError, match=refusal):
        _verify(_build(tmp_path, plan))


@pytest.mark.parametrize(
    "path",
    ["docs/plan-033/firewall-results.md\n", "docs/work-items.md\n",
     "docs/plan-033/fire\u202ewall-results.md"],
)
def test_a_record_path_with_a_trailing_newline_is_not_a_record(path: str) -> None:
    """Sol final-r5 N1: the results-record pattern and the record list are whole-value."""
    assert gate._is_record(path.rstrip("\n").replace("\u202e", ""), "1.1.0")
    assert not gate._is_record(path, "1.1.0")


def test_a_dirty_checkout_is_refused(history: History) -> None:
    _write(history.root, "src/gpo_studio/core.py", "VALUE = 3\n")
    with pytest.raises(gate.ReleaseGateError, match="uncommitted changes to tracked files"):
        _verify(history)


# --- one reading of every changed record (Sol final-r1 B1, DeepSeek N4) ----------


def test_a_coding_declaration_in_the_registry_module_is_refused(tmp_path: Path) -> None:
    """Sol's probe: a raw_unicode_escape cookie hid a statement from a UTF-8 reader.

    The registry module is a test, so it must be byte-identical to F; the probe's
    edit is refused before anything parses it.
    """
    planted = "# coding: raw_unicode_escape\n" + REGISTRY_MODULE.replace(
        "import json\n", 'import json\n# harmless\\u000aprint("REGISTRY_HIDDEN_CODE_EXECUTED")\n'
    )
    history = _build(tmp_path, Plan(extra_banked={"tests/test_committed_evidence.py": planted}))
    with pytest.raises(gate.ReleaseGateError, match="M tests/test_committed_evidence.py"):
        _verify(history)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("docs/work-items.md", "# -*- coding: latin-1 -*-\n# Work items\n"),
        ("docs/work-items.md", "\n# vim: set fileencoding=utf-16 :\n"),
        ("docs/work-items.md", b"\xef\xbb\xbf# Work items\n"),
        ("tests/fixtures/scenarios/platforms.json", b"\xff\xfe{\x00}\x00"),
        ("docs/plan-033/firewall-results.md", b"# Firewall \xe9\n"),
    ],
)
def test_a_record_with_a_second_reading_is_refused(
    tmp_path: Path, path: str, content: str | bytes
) -> None:
    history = _build(tmp_path, Plan(extra_banked={path: content}))
    with pytest.raises(gate.ReleaseGateError, match="byte-order mark|coding declaration|UTF-8"):
        _verify(history)


# --- the verdict tables are data (Sol B1, DeepSeek B1) ----------------------------


def _set_finalizer(value: str) -> Callable[[dict[str, Any]], None]:
    def change(registry: dict[str, Any]) -> None:
        registry["lane_verdicts"][_verdict_path("endpoint", LABEL)] = value
    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # DeepSeek probe 6: a finalizer path into a pack.
        (_set_finalizer(f"../../docs/plan-033/wp6-evidence/{LABEL}/endpoint/evil.py"),
         "bare finalize_\\*.py names"),
        # A real finalizer, but not the lane's at F.
        (_set_finalizer("finalize_rsop_user_run.py"), "lane's finalizer at F"),
        (lambda r: r["lane_verdicts"].update(
            {_verdict_path("endpoint", "rc"): "finalize_rsop_run.py"}),
         "entries present at F were changed"),
        (lambda r: r["lane_verdicts"].pop(_verdict_path("loopback-merge", "rc")),
         "entries present at F were changed or removed|not registered"),
        (lambda r: r["retired_verdicts"].append(_verdict_path("endpoint", LABEL)),
         "only verdicts registered at F may be retired"),
        (lambda r: r["lane_verdicts"].update({"wp6-evidence/x/endpoint/verification.json":
                                               "finalize_endpoint_run.py"}),
         "added but not run"),
        (lambda r: r["pending_requalification"].append(_verdict_path("endpoint", LABEL)),
         "pending_requalification is not empty"),
        (lambda r: r.update(wp0_manifest=_verdict_path("wp0", "rc")),
         "wp0_manifest is not the batch's WP-0 run"),
        (lambda r: r.update(extra=1), "keys must be exactly"),
        # Sol final-r5 N1 / DeepSeek final-r5 N2.
        (_set_finalizer("finalize_endpoint_run.py\n"), "bare finalize_\\*.py names"),
        (lambda r: r.update(wp0_manifest=_verdict_path("wp0", LABEL).replace(
            "manifest.json", "manifest\u202ejson")), "does not match the registry schema"),
        (lambda r: r.update(wp0_manifest=_verdict_path("wp0", LABEL) + "\n"),
         "does not match the registry schema"),
    ],
)
def test_a_registry_change_beyond_the_batch_is_refused(
    tmp_path: Path, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    with pytest.raises(gate.ReleaseGateError, match=message):
        _verify(_build(tmp_path, Plan(registry=change)))


def test_a_registry_not_in_canonical_form_is_refused(history: History) -> None:
    approval = _files_of(history, history.approved)
    data = json.loads((history.root / REGISTRY).read_text("utf-8"))
    _rebuild_on(history, history.freeze, [
        {**_files_of(history, history.banked), REGISTRY: json.dumps(data) + "\n"}, approval,
    ])
    with pytest.raises(gate.ReleaseGateError, match="canonical form"):
        _verify(history)


# --- the commits: F -> B -> (records) -> A (Sol B4, DeepSeek N2) -----------------


def test_banking_and_approval_in_one_commit_are_refused(history: History) -> None:
    """Sol's probe: B and A squashed into one child of F."""
    _git(history.root, "reset", "-q", "--soft", history.freeze)
    _git(history.root, "commit", "-q", "-m", "combined bank and approval")
    with pytest.raises(gate.ReleaseGateError, match="needs a banking commit B and a later"):
        _verify(history)


def test_code_changed_and_reverted_after_the_freeze_is_refused(history: History) -> None:
    """DeepSeek probe 2: the tree is F's again, but a commit after F changed code."""
    approval = _files_of(history, history.approved)
    _rebuild_on(history, history.banked, [
        {"src/gpo_studio/core.py": "VALUE = 999\n"},
        {"src/gpo_studio/core.py": "VALUE = 1\n"},
        approval,
    ])
    with pytest.raises(gate.ReleaseGateError, match="records-only commit .* changes what it may"):
        _verify(history)


def test_a_banked_file_edited_in_a_later_commit_is_refused(history: History) -> None:
    """DeepSeek probe 3: a pack file and its recorded hash changed after B."""
    approval = _files_of(history, history.approved)
    log = f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/controller.log"
    batch = json.loads((history.root / BATCH).read_text("utf-8"))
    forged = b"fabricated after the run\r\n"
    batch["runs"][1]["files"]["controller.log"] = hashlib.sha256(forged).hexdigest()
    _rebuild_on(history, history.banked, [{log: forged, BATCH: _json(batch)}, approval])
    with pytest.raises(gate.ReleaseGateError, match="records-only commit .* changes what it may"):
        _verify(history)


def test_a_merge_after_the_freeze_is_refused(history: History) -> None:
    approval = _files_of(history, history.approved)
    _git(history.root, "reset", "-q", "--hard", history.banked)
    _git(history.root, "checkout", "-q", "-b", "side", history.banked)
    _commit(history.root, {"docs/work-items.md": "# Work items\n\nside\n"}, "side")
    _git(history.root, "checkout", "-q", "-")
    _commit(history.root, {"docs/capability-matrix.md": "# Matrix\n\nmain\n"}, "main")
    _git(history.root, "merge", "-q", "--no-edit", "side")
    _commit(history.root, approval, "A")
    with pytest.raises(gate.ReleaseGateError, match="not a single-parent child"):
        _verify(history)


def test_an_empty_commit_after_the_freeze_is_refused(history: History) -> None:
    """DeepSeek final-r2 N4: every commit from F to the tag must change something."""
    approval = _files_of(history, history.approved)
    _rebuild_on(history, history.banked, [{}, approval])
    with pytest.raises(gate.ReleaseGateError, match="changes nothing"):
        _verify(history)


@pytest.mark.parametrize("erase", ["section", "report-list"])
def test_an_approval_that_erases_the_remaining_items_is_refused(
    history: History, erase: str
) -> None:
    """DeepSeek final-r2 N3: A closes the remaining items; it may not delete them."""
    approval = _files_of(history, history.approved)
    if erase == "section":
        text = approval[MANIFEST]
        lines = (text.decode() if isinstance(text, bytes) else str(text)).split("\n")
        start, end = gate._remaining_section(lines)
        approval[MANIFEST] = "\n".join(lines[:start] + lines[end:])
        message = "must keep .* Remaining before approval section"
    else:
        report = json.loads(approval[REPORT])  # type: ignore[arg-type]
        report["evidence"]["remaining_before_approval"] = []
        approval[REPORT] = _json(report)
        message = "must keep remaining_before_approval as a non-empty list"
    _rebuild_on(history, history.banked, [approval])
    with pytest.raises(gate.ReleaseGateError, match=message):
        _verify(history)


def test_a_records_commit_after_the_approval_is_refused(history: History) -> None:
    """The tag goes on A: a later commit, even of records, is not the approval."""
    _commit(history.root, {"docs/work-items.md": "# Work items\n\nlater\n"}, "after A")
    with pytest.raises(gate.ReleaseGateError, match="approves before the tagged commit"):
        _verify(history)


def test_approving_in_the_banking_commit_is_refused(history: History) -> None:
    approval = _files_of(history, history.approved)
    banked = _files_of(history, history.banked)
    _rebuild_on(history, history.freeze, [
        {**banked, **approval}, {"docs/work-items.md": "# Work items\n\nlater\n"},
    ])
    with pytest.raises(gate.ReleaseGateError, match="approves before the tagged commit|"
                                                     "must be the approval commit"):
        _verify(history)


@pytest.mark.parametrize("what", ["other-file", "report-key", "report-evidence", "manifest"])
def test_an_approval_that_changes_more_than_the_approval_is_refused(
    history: History, what: str
) -> None:
    approval = _files_of(history, history.approved)
    report = json.loads(approval[REPORT])  # type: ignore[arg-type]
    if what == "other-file":
        approval["docs/work-items.md"] = "# Work items\n\nalso in A\n"
        message = "changes more than the approval records"
    elif what == "report-key":
        report["workspace_schema_version"] = 5
        approval[REPORT] = _json(report)
        message = "beyond status, version"
    elif what == "report-evidence":
        report["evidence"]["lane_verdicts"] = "rewritten at approval"
        approval[REPORT] = _json(report)
        message = "beyond status, version"
    else:
        text = approval[MANIFEST]
        assert isinstance(text, (str, bytes))
        lines = (text.decode() if isinstance(text, bytes) else text).split("\n")
        lines[10] = lines[10] + " Edited at approval."
        approval[MANIFEST] = "\n".join(lines)
        message = "beyond line 5"
    _rebuild_on(history, history.banked, [approval])
    with pytest.raises(gate.ReleaseGateError, match=message):
        _verify(history)


# --- the freeze commit itself -------------------------------------------------


def _record_freeze(history: History, commit: str) -> None:
    report = json.loads((history.root / REPORT).read_text("utf-8"))
    report["freeze"]["commit"] = commit
    approval = _files_of(history, history.approved)
    approval[REPORT] = _json(report)
    _rebuild_on(history, history.banked, [approval])


def test_a_freeze_commit_missing_from_the_clone_is_refused(history: History) -> None:
    _record_freeze(history, "1" * 40)
    with pytest.raises(gate.ReleaseGateError, match="is not in this clone"):
        _verify(history)


def test_a_shallow_clone_is_refused(history: History, tmp_path: Path) -> None:
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", history.root.as_uri(), str(shallow))
    with pytest.raises(gate.ReleaseGateError, match="this clone is shallow; check out with"):
        _verify(History(shallow, history.earlier, history.freeze, history.banked, history.approved))


def test_a_freeze_commit_that_is_not_an_ancestor_is_refused(history: History) -> None:
    _git(history.root, "checkout", "-q", "-b", "side", history.earlier)
    side = _commit(history.root, {"src/gpo_studio/core.py": "VALUE = 9\n"}, "a sibling of F")
    _git(history.root, "checkout", "-q", "-")
    _record_freeze(history, side)
    with pytest.raises(gate.ReleaseGateError, match="is not an ancestor of the tagged commit"):
        _verify(history)


def test_the_tag_cannot_be_the_freeze_commit_itself(history: History) -> None:
    _git(history.root, "checkout", "-q", "--detach", history.freeze)
    # F's own report is not approved, so build what check() would return for A.
    report = gate.ReleaseManifest(
        version="1.1.0",
        base_version="1.1.0",
        is_candidate=False,
        manifest=MANIFEST,
        report=REPORT,
        freeze_commit=history.freeze,
        batch_manifest=BATCH,
    )
    with pytest.raises(gate.ReleaseGateError, match="is the freeze commit .* itself"):
        gate.verify_freeze(history.root, report)


def test_an_approved_report_without_a_freeze_is_refused(history: History) -> None:
    report = json.loads((history.root / REPORT).read_text("utf-8"))
    report["freeze"] = None
    _commit(history.root, {REPORT: _json(report)}, "drop")
    with pytest.raises(gate.ReleaseGateError, match="an approved report must record its freeze"):
        gate.check(history.root, "v1.1.0")


# --- every live verdict recorded at F -------------------------------------------


@pytest.mark.parametrize("lane", ["endpoint", "wp0"])
def test_a_verdict_recorded_at_another_commit_is_refused(tmp_path: Path, lane: str) -> None:
    def earlier_commit(name: str, verdict: dict[str, Any], freeze: str, earlier: str) -> None:
        if name == lane:
            verdict["source"]["commit"] = earlier

    history = _build(tmp_path, Plan(verdict=earlier_commit))
    with pytest.raises(gate.ReleaseGateError, match=f"lane '{lane}'.*recorded at '") as caught:
        _verify(history)
    assert history.earlier in str(caught.value)


def test_a_batch_run_recorded_at_another_commit_is_refused(tmp_path: Path) -> None:
    def moved(batch: dict[str, Any], earlier: str) -> None:
        batch["runs"][1]["commit"] = earlier

    with pytest.raises(gate.ReleaseGateError, match="lane 'endpoint': the batch records its run"):
        _verify(_build(tmp_path, Plan(batch=moved)))


def _drop(key: str) -> Callable[[dict[str, Any], str], None]:
    return lambda batch, earlier: batch.pop(key)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b, e: b.update(source_commit=e), "the batch ran at"),
        (lambda b, e: b.update(not_passed=[{"name": "x"}]), "not every lane passed"),
        (lambda b, e: b.update(successors=[{"name": "x"}]), "successor runs"),
        (lambda b, e: b.update(runs={}), "lanes_started passing runs"),
        (lambda b, e: b.update(lanes_started=2), "lanes_started passing runs"),
        (lambda b, e: b.update(lanes_started=True), "lanes_started passing runs"),
        (lambda b, e: b.update(post_batch_cleanup=None), "post-batch directory check"),
        (lambda b, e: b.update(post_batch_cleanup={"x/collector.ps1": "0" * 64}),
         "post-batch directory check"),
        (_drop("successors"), "a batch manifest must have"),
        (lambda b, e: b["runs"][2].update(name="endpoint"), "distinct lane name"),
        (lambda b, e: b["runs"][1].update(verdict="wp6-evidence/x/other/verification.json"),
         "not where the bank tool banks it"),
        (lambda b, e: b["runs"][1].update(verdict=f"wp6-evidence/{LABEL}/endpoint/manifest.json"),
         "not where the bank tool banks it"),
        (lambda b, e: b["runs"][1].update(run_id="../x"), "is not a run id"),
        (lambda b, e: b["runs"][1].update(files=[]), "does not hash its pack"),
        (lambda b, e: b["runs"][1]["files"].update({"../x": "0" * 64}), "does not hash its pack"),
        (lambda b, e: b["runs"][1]["files"].update({"x.bin": "zz"}), "does not hash its pack"),
        (lambda b, e: b["runs"][1]["files"].pop("verification.json"), "does not hash its pack"),
        # Sol final-r5 N1: a trailing newline in every converted field.
        (lambda b, e: b.update(post_batch_cleanup={
            k + "\n": v for k, v in b["post_batch_cleanup"].items()}),
         "post-batch directory check"),
        (lambda b, e: b.update(post_batch_cleanup={
            k: v + "\n" for k, v in b["post_batch_cleanup"].items()}),
         "post-batch directory check"),
        (lambda b, e: b["runs"][1]["files"].update(
            {"controller.log": b["runs"][1]["files"]["controller.log"] + "\n"}),
         "does not hash its pack"),
        (lambda b, e: b["runs"][1].update(verdict=b["runs"][1]["verdict"] + "\n"),
         "not where the bank tool banks it"),
        (lambda b, e: b["runs"][1].update(run_id="endpoint-final\n"), "is not a run id"),
        # DeepSeek final-r5 N2: control and format characters in a pack file name.
        (lambda b, e: b["runs"][1]["files"].update({"evil\u202etxt.log": "0" * 64}),
         "does not hash its pack"),
        (lambda b, e: b["runs"][1]["files"].update({"x\u0085.log": "0" * 64}),
         "does not hash its pack"),
    ],
)
def test_a_malformed_batch_manifest_is_refused(
    tmp_path: Path, change: Callable[[dict[str, Any], str], None], message: str
) -> None:
    """DeepSeek final-r1 N5: every shape branch of the batch manifest."""
    with pytest.raises(gate.ReleaseGateError, match=message):
        _verify(_build(tmp_path, Plan(batch=change)))


def test_two_labels_in_one_batch_are_refused(tmp_path: Path) -> None:
    def relabel(batch: dict[str, Any], earlier: str) -> None:
        batch["runs"][2]["verdict"] = batch["runs"][2]["verdict"].replace(LABEL, "other-label")

    with pytest.raises(gate.ReleaseGateError, match="one batch banks under one label"):
        _verify(_build(tmp_path, Plan(batch=relabel)))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v["source"].update(dirty=True), "not recorded from a clean tree"),
        (lambda v: v.update(passed=False), "not a pass"),
        (lambda v: v.update(run_id="another-run"), "run_id is not the batch's"),
    ],
)
def test_a_verdict_that_is_not_a_clean_pass_of_the_batch_run_is_refused(
    tmp_path: Path, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    def changed(name: str, verdict: dict[str, Any], freeze: str, earlier: str) -> None:
        if name == "loopback-merge":
            change(verdict)

    with pytest.raises(gate.ReleaseGateError, match=f"lane 'loopback-merge'.*{message}"):
        _verify(_build(tmp_path, Plan(verdict=changed)))


def test_verdicts_pending_at_the_freeze_and_cleared_by_the_batch_pass(tmp_path: Path) -> None:
    """Pending at F, re-run and retired in B: the shape the final-r6 sweep leaves."""
    history = _build(tmp_path, Plan(pending_at_freeze=True))
    registry = json.loads(subprocess.run(
        ["git", "show", f"{history.freeze}:{REGISTRY}"], cwd=history.root,
        env={**os.environ, **_GIT_ENV}, capture_output=True, check=True,
    ).stdout)
    assert registry["pending_requalification"]
    _verify(history)


def test_a_verdict_pending_at_the_freeze_must_be_retired_by_the_batch(tmp_path: Path) -> None:
    with pytest.raises(gate.ReleaseGateError, match="must be retired by the batch"):
        _verify(_build(tmp_path, Plan(pending_at_freeze=True, retire=("endpoint",))))


def test_a_verdict_both_retired_and_pending_at_the_freeze_is_refused(tmp_path: Path) -> None:
    """DeepSeek final-r7 B2, the reviewer's probe: endpoint both retired and
    pending at F dropped out of the lanes the batch must re-run, so a batch
    without it passed."""
    plan = Plan(pending_at_freeze=True, overlap_at_freeze=True, lanes=("wp0", "loopback-merge"))
    with pytest.raises(gate.ReleaseGateError, match="must be registered and disjoint"):
        _verify(_build(tmp_path, plan))


def test_a_verdict_both_retired_and_pending_at_the_tag_is_refused(tmp_path: Path) -> None:
    endpoint_rc = _verdict_path("endpoint", "rc")

    def overlap(registry: dict[str, Any]) -> None:
        registry["pending_requalification"] = [endpoint_rc]

    with pytest.raises(gate.ReleaseGateError, match="must be registered and disjoint"):
        _verify(_build(tmp_path, Plan(registry=overlap)))


def test_a_pending_path_that_is_not_a_registered_verdict_is_refused(tmp_path: Path) -> None:
    def stray(registry: dict[str, Any]) -> None:
        registry["pending_requalification"] = ["wp6-evidence/x/endpoint/verification.json"]

    with pytest.raises(gate.ReleaseGateError, match="not registered"):
        _verify(_build(tmp_path, Plan(registry=stray)))


def test_a_lane_pending_at_the_freeze_but_not_re_run_is_refused(tmp_path: Path) -> None:
    plan = Plan(lanes=("wp0", "endpoint"), pending_at_freeze=True)
    with pytest.raises(gate.ReleaseGateError, match=r"not re-run: \['loopback-merge'\]"):
        _verify(_build(tmp_path, plan))


def test_a_lane_live_at_the_freeze_but_not_re_run_is_refused(tmp_path: Path) -> None:
    """Retiring a lane's verdict without a run at F would drop it silently."""
    plan = Plan(lanes=("wp0", "endpoint"), retire=tuple(FINALIZERS))
    with pytest.raises(gate.ReleaseGateError, match=r"not re-run: \['loopback-merge'\]"):
        _verify(_build(tmp_path, plan))


def test_a_live_verdict_from_before_the_freeze_is_refused(tmp_path: Path) -> None:
    """The batch re-ran the lane, but the registry still holds the old verdict live."""
    plan = Plan(retire=("endpoint",))
    with pytest.raises(gate.ReleaseGateError, match="more than one live verdict.*loopback-merge"):
        _verify(_build(tmp_path, plan))


def test_a_banked_file_that_differs_from_the_batch_hash_is_refused(tmp_path: Path) -> None:
    log = f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/controller.log"
    history = _build(tmp_path, Plan(extra_banked={log: b"edited before banking\n"}))
    with pytest.raises(gate.ReleaseGateError, match="not what the batch banked"):
        _verify(history)


def test_a_post_batch_check_that_differs_from_its_hash_is_refused(tmp_path: Path) -> None:
    """DeepSeek final-r1 N5: the cleanup hash branch."""
    path = "docs/plan-033/final-cleanup/directory.json"
    history = _build(tmp_path, Plan(extra_banked={path: _json({"result": "dirty"})}))
    with pytest.raises(gate.ReleaseGateError, match="directory.json is not what the batch hashed"):
        _verify(history)


def test_evidence_present_at_the_freeze_is_not_banked_by_it(tmp_path: Path) -> None:
    """A banked file carried in F (or earlier) was not produced by a run at F."""
    with pytest.raises(gate.ReleaseGateError, match="must be added after the freeze commit"):
        _verify(_build(tmp_path, Plan(carry_into_freeze=("endpoint",))))


# --- the evidence tags, and what the gate does not prove (Sol B3, DeepSeek N3) --


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (Plan(tags={"endpoint": None}), "no annotated tag refs/tags/evidence/endpoint-final"),
        (Plan(lightweight_tags=True), "no annotated tag"),
        (Plan(tags={"wp0": "EARLIER"}), "does not peel to the freeze commit"),
    ],
)
def test_a_run_without_its_evidence_tag_at_the_freeze_is_refused(
    tmp_path: Path, plan: Plan, message: str
) -> None:
    if plan.tags.get("wp0") == "EARLIER":
        history = _build(tmp_path, Plan(tags={"wp0": None}))
        _git(history.root, "tag", "-a", "-m", "elsewhere", "evidence/wp0-final", history.earlier)
    else:
        history = _build(tmp_path, plan)
    with pytest.raises(gate.ReleaseGateError, match=message):
        _verify(history)


def test_a_consistent_forgery_with_its_tags_passes_which_is_the_stated_limit(
    tmp_path: Path,
) -> None:
    """DeepSeek probe 5: a hand-written verdict, hashed and tagged, passes.

    Nothing in the tree can tell a careful forgery from the estate's bytes. The
    gate's claim is therefore the narrow one (the tagged tree is F plus
    internally consistent evidence recorded at F); estate provenance rests on
    the controller transcripts and the banking review. If this test starts
    failing because the gate learned to authenticate runs, widen the claim.
    """

    def forge(name: str, verdict: dict[str, Any], freeze: str, earlier: str) -> None:
        if name == "endpoint":
            verdict.clear()
            verdict.update(run_id="endpoint-final", passed=True, transport="psdirect",
                           source={"commit": freeze, "dirty": False, "files": {}})

    summary = _verify(_build(tmp_path, Plan(verdict=forge)))
    assert "does not authenticate the estate runs" in summary


# --- release candidates are not subject to it ---------------------------------


def test_an_rc_tag_is_not_subject_to_the_freeze_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No git, no freeze: an -rc tag is checked exactly as before."""

    def refuse(root: Path, result: object) -> str:
        raise AssertionError("verify_freeze ran for a release candidate")

    monkeypatch.setattr(gate, "verify_freeze", refuse)
    root = tmp_path / "not-a-repository"
    _write(root, "src/gpo_studio/__init__.py", '__version__ = "1.1.0rc1"\n')
    (root / "docs").mkdir()
    for name in ("release-evidence-1.1.0.md", "release-evidence-report-1.1.0.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    # A candidate as the documented RC approval makes it, whatever the committed
    # files say today: status, version and line 5, and no freeze.
    report_path = root / "docs" / "release-evidence-report-1.1.0.json"
    report = json.loads(report_path.read_text("utf-8"))
    report.update(status="candidate", version="1.1.0rc1", freeze=None)
    report_path.write_text(_json(report), encoding="utf-8", newline="\n")
    manifest = root / "docs" / "release-evidence-1.1.0.md"
    lines = manifest.read_text("utf-8").split("\n")
    lines[4] = gate.CANDIDATE
    manifest.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    assert gate.main(["--tag", "v1.1.0-rc.1", "--root", str(root)]) == 0


# --- the gate reads the registry the tests run ------------------------------------


def test_the_gate_reads_the_committed_registry_as_the_tests_do() -> None:
    """The gate's reading of the JSON registry equals what the evidence tests load."""
    registry = gate._load_registry_blob(
        (REPO_ROOT / REGISTRY).read_bytes(), "the committed registry"
    )
    executed = runpy.run_path(str(REPO_ROOT / "tests" / "test_committed_evidence.py"))
    assert gate._live(registry) == executed["LIVE_VERDICTS"]
    assert registry["lane_verdicts"] == executed["LANE_VERDICTS"]
    assert set(registry["retired_verdicts"]) == executed["RETIRED_VERDICTS"]
    assert registry["wp0_manifest"] == executed["WP0_MANIFEST"]


# --- the workflow gives the gate the history it needs ----------------------------


def _steps_by_job(text: str) -> dict[str, str]:
    jobs: dict[str, str] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            current = line.strip().rstrip(":")
            jobs[current] = ""
        elif current is not None:
            jobs[current] += line + "\n"
    return jobs


def test_every_job_that_runs_the_gate_checks_out_full_history() -> None:
    """A final-tag run of the gate refuses a shallow clone, so give it the history."""
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    jobs = _steps_by_job(text.split("\njobs:\n", 1)[1])
    running = [job for job, body in jobs.items() if "scripts/check_release_manifest.py" in body]
    assert sorted(running) == ["publish", "release-identity"]
    for job in running:
        body = jobs[job]
        checkouts = body.count("uses: actions/checkout@")
        assert checkouts == 1, job
        checkout = body.split("uses: actions/checkout@", 1)[1].split("\n      - ", 1)[0]
        assert "\n        with:\n          fetch-depth: 0\n" in checkout + "\n", job
