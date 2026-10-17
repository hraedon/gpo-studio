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


# --- the history: R (an earlier batch) -> F -> B -> A ------------------------

LANES = {
    "wp0": "wp0-evidence",
    "endpoint": "wp6-evidence",
    "loopback-merge": "wp9-evidence",
}
FINALIZERS = {"endpoint": "finalize_endpoint_run.py", "loopback-merge": "finalize_rsop_user_run.py"}
LABEL = "final-20261020"
BATCH = "docs/plan-033/final-batch.json"


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


#: Everything here but the registry's tables is identical in F and B, which is
#: what lets the tables-only rule pass.
_REGISTRY_TEMPLATE = '''"""A verdict registry shaped like tests/test_committed_evidence.py."""

from __future__ import annotations

LANE_VERDICTS = {{
{lanes}}}
RETIRED_VERDICTS: set[str] = set()
{updates}
PENDING_REQUALIFICATION: set[str] = set()

#: The live WP-0 manifest.
WP0_MANIFEST = "{wp0}"

LIVE_VERDICTS = {{
    relative: finalizer
    for relative, finalizer in LANE_VERDICTS.items()
    if relative not in RETIRED_VERDICTS
    and relative not in PENDING_REQUALIFICATION
}}


def test_the_live_set_is_not_empty() -> None:
    assert sorted(LIVE_VERDICTS.items())
    assert len(set(LANE_VERDICTS)) >= len(RETIRED_VERDICTS)
'''


def _registry(updates: str, wp0_label: str) -> str:
    lanes = "".join(
        f"    '{_verdict_path(lane, 'rc')}': '{finalizer}',\n"
        for lane, finalizer in FINALIZERS.items()
    )
    return _REGISTRY_TEMPLATE.format(
        lanes=lanes, updates=updates, wp0=_verdict_path("wp0", wp0_label)
    )


@dataclass
class Plan:
    """How to bank the batch at F. Each test changes one thing from the passing plan."""

    lanes: tuple[str, ...] = tuple(LANES)
    retire: tuple[str, ...] = tuple(FINALIZERS)
    verdict: Callable[[str, dict[str, Any], str, str], None] | None = None
    batch: Callable[[dict[str, Any], str], None] | None = None
    extra_banked: dict[str, str | bytes | None] = field(default_factory=dict)
    #: Lanes whose controller.log is already committed in F, byte for byte.
    carry_into_freeze: tuple[str, ...] = ()


@dataclass
class History:
    root: Path
    earlier: str
    freeze: str
    banked: str
    approved: str


def _build(tmp_path: Path, plan: Plan | None = None) -> History:
    plan = plan or Plan()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    report = json.loads(
        (REPO_ROOT / "docs" / "release-evidence-report-1.1.0.json").read_text("utf-8")
    )
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
        "docs/release-evidence-1.1.0.md": (
            REPO_ROOT / "docs" / "release-evidence-1.1.0.md"
        ).read_bytes(),
        "docs/release-evidence-report-1.1.0.json": _json(report),
        "tests/test_committed_evidence.py": _registry("", "rc"),
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
    lanes = "".join(
        f"    '{_verdict_path(lane, LABEL)}': '{FINALIZERS[lane]}',\n"
        for lane in plan.lanes
        if lane != "wp0"
    )
    retired = "".join(f"    '{_verdict_path(lane, 'rc')}',\n" for lane in plan.retire)
    updates = f"LANE_VERDICTS.update({{\n{lanes}}})\n"
    if retired:
        updates += f"RETIRED_VERDICTS.update({{\n{retired}}})\n"
    banked["tests/test_committed_evidence.py"] = _registry(updates, LABEL)
    report["freeze"] = {"commit": freeze, "batch_manifest": BATCH}
    banked["docs/release-evidence-report-1.1.0.json"] = _json(report)
    banked["CHANGELOG.md"] = "# Changelog\n\nThe final batch, at F.\n"
    banked["tests/fixtures/scenarios/platforms.json"] = _json({"runs": ["final"]})
    banked["docs/plan-033/firewall-results.md"] = "# Firewall results\n\nfirewall-final\n"
    banked |= plan.extra_banked
    banked_commit = _commit(root, banked, "B: bank the batch at F")

    report["status"], report["version"] = "approved", "1.1.0"
    manifest = (root / "docs" / "release-evidence-1.1.0.md").read_text("utf-8").split("\n")
    manifest[4] = gate.APPROVED
    approved = _commit(
        root,
        {
            "docs/release-evidence-report-1.1.0.json": _json(report),
            "docs/release-evidence-1.1.0.md": "\n".join(manifest),
        },
        "A: approve 1.1.0",
    )
    return History(root, earlier, freeze, banked_commit, approved)


def _verify(history: History) -> str:
    result = gate.check(history.root, "v1.1.0")
    summary: str = gate.verify_freeze(history.root, result)
    return summary


@pytest.fixture
def history(tmp_path: Path) -> History:
    return _build(tmp_path)


# --- the passing case ---------------------------------------------------------


def test_an_evidence_only_descendant_of_the_freeze_passes(
    history: History, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: every refusal below changes one thing from this history."""
    summary = _verify(history)
    assert f"freeze {history.freeze[:12]}: 3 lanes earned at F" in summary
    assert gate.main(["--tag", "v1.1.0", "--root", str(history.root)]) == 0
    assert "lanes earned at F" in capsys.readouterr().out


def test_records_may_change_again_after_the_approval(history: History) -> None:
    """Rule by tree, not by commit: a later records-only commit still passes."""
    _commit(history.root, {"docs/work-items.md": "# Work items\n\nWI-083 closed.\n"}, "records")
    _verify(history)


# --- anything but evidence and records ----------------------------------------


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
        ("tests/test_other.py", "def test_other() -> None:\n    pass\n"),
        ("tests/test_planted.py", "def test_planted() -> None:\n    pass\n"),
        ("docs/plan-033/wp6-evidence/rc/endpoint/verification.json", "{}\n"),
        ("docs/plan-033/wp6-evidence/rc/endpoint/extra.json", "{}\n"),
        ("docs/plan-033/other-batch.json", "{}\n"),
        ("docs/plan-033/new-results.md", "# A results document F did not have\n"),
        ("README.md", None),
    ],
)
def test_a_change_outside_evidence_and_records_after_the_freeze_is_refused(
    history: History, path: str, content: str | None
) -> None:
    _commit(history.root, {path: content}, f"plant {path}")
    with pytest.raises(gate.ReleaseGateError, match="differs from the freeze commit") as caught:
        _verify(history)
    assert path in str(caught.value)


def test_a_planted_change_under_src_fails_the_cli(
    history: History, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(history.root, {"src/gpo_studio/core.py": "VALUE = 2\n"}, "plant")
    assert gate.main(["--tag", "v1.1.0", "--root", str(history.root)]) == 1
    error = capsys.readouterr().err
    assert error.startswith("release gate: FAIL: ") and "M src/gpo_studio/core.py" in error
    assert "Traceback" not in error


def test_an_executable_evidence_file_is_refused(history: History) -> None:
    path = f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/run.sh"
    _write(history.root, path, "#!/bin/sh\n")
    os.chmod(history.root.joinpath(*path.split("/")), 0o755)
    _git(history.root, "add", "--", path)
    _git(history.root, "update-index", "--chmod=+x", "--", path)
    _git(history.root, "commit", "-q", "-m", "an executable in a pack")
    with pytest.raises(gate.ReleaseGateError, match=r"run\.sh \(mode 100755\)"):
        _verify(history)


def test_the_registry_may_change_only_in_its_verdict_tables(history: History) -> None:
    registry = history.root / "tests" / "test_committed_evidence.py"
    text = registry.read_text("utf-8")
    _commit(
        history.root,
        {"tests/test_committed_evidence.py": text + "# A comment is not a statement.\n"},
        "comment only",
    )
    _verify(history)
    weakened = text.replace("assert sorted(LIVE_VERDICTS.items())", "assert True")
    assert weakened != text
    _commit(history.root, {"tests/test_committed_evidence.py": weakened}, "weaken a test")
    with pytest.raises(gate.ReleaseGateError, match="outside its verdict tables"):
        _verify(history)


def test_a_dirty_checkout_is_refused(history: History) -> None:
    _write(history.root, "src/gpo_studio/core.py", "VALUE = 3\n")
    with pytest.raises(gate.ReleaseGateError, match="uncommitted changes to tracked files"):
        _verify(history)


# --- the freeze commit itself -------------------------------------------------


def _record_freeze(history: History, commit: str) -> None:
    path = history.root / "docs" / "release-evidence-report-1.1.0.json"
    report = json.loads(path.read_text("utf-8"))
    report["freeze"]["commit"] = commit
    _commit(history.root, {"docs/release-evidence-report-1.1.0.json": _json(report)}, "re-point")


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
    _git(history.root, "checkout", "-q", "main")
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
        manifest="docs/release-evidence-1.1.0.md",
        report="docs/release-evidence-report-1.1.0.json",
        freeze_commit=history.freeze,
        batch_manifest=BATCH,
    )
    with pytest.raises(gate.ReleaseGateError, match="is the freeze commit .* itself"):
        gate.verify_freeze(history.root, report)


def test_an_approved_report_without_a_freeze_is_refused(history: History) -> None:
    path = history.root / "docs" / "release-evidence-report-1.1.0.json"
    report = json.loads(path.read_text("utf-8"))
    report["freeze"] = None
    _commit(history.root, {"docs/release-evidence-report-1.1.0.json": _json(report)}, "drop")
    with pytest.raises(gate.ReleaseGateError, match="an approved report must record its freeze"):
        gate.check(history.root, "v1.1.0")


# --- every live verdict earned at F -------------------------------------------


@pytest.mark.parametrize("lane", ["endpoint", "wp0"])
def test_a_verdict_earned_at_another_commit_is_refused(tmp_path: Path, lane: str) -> None:
    def earlier_commit(name: str, verdict: dict[str, Any], freeze: str, earlier: str) -> None:
        if name == lane:
            verdict["source"]["commit"] = earlier

    history = _build(tmp_path, Plan(verdict=earlier_commit))
    with pytest.raises(gate.ReleaseGateError, match=f"lane '{lane}'.*earned at '") as caught:
        _verify(history)
    assert history.earlier in str(caught.value)


def test_a_batch_run_recorded_at_another_commit_is_refused(tmp_path: Path) -> None:
    def moved(batch: dict[str, Any], earlier: str) -> None:
        batch["runs"][1]["commit"] = earlier

    with pytest.raises(gate.ReleaseGateError, match="lane 'endpoint': the batch records its run"):
        _verify(_build(tmp_path, Plan(batch=moved)))


def test_a_batch_that_ran_elsewhere_is_refused(tmp_path: Path) -> None:
    def moved(batch: dict[str, Any], earlier: str) -> None:
        batch["source_commit"] = earlier

    with pytest.raises(gate.ReleaseGateError, match="the batch ran at"):
        _verify(_build(tmp_path, Plan(batch=moved)))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v["source"].update(dirty=True), "not earned from a clean tree"),
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


def test_a_banked_file_that_differs_from_the_batch_hash_is_refused(history: History) -> None:
    path = f"docs/plan-033/{LANES['endpoint']}/{LABEL}/endpoint/controller.log"
    _commit(history.root, {path: "edited after banking\n"}, "edit a banked file")
    with pytest.raises(gate.ReleaseGateError, match="not what the batch banked"):
        _verify(history)


def test_evidence_present_at_the_freeze_is_not_banked_by_it(tmp_path: Path) -> None:
    """A banked file carried in F (or earlier) was not produced by a run at F."""
    with pytest.raises(gate.ReleaseGateError, match="must be added after the freeze commit"):
        _verify(_build(tmp_path, Plan(carry_into_freeze=("endpoint",))))


def test_pending_requalification_refuses_a_final_tag(history: History) -> None:
    registry = history.root / "tests" / "test_committed_evidence.py"
    text = registry.read_text("utf-8").replace(
        "PENDING_REQUALIFICATION: set[str] = set()",
        f"PENDING_REQUALIFICATION: set[str] = {{'{_verdict_path('endpoint', 'rc')}'}}",
    )
    _commit(history.root, {"tests/test_committed_evidence.py": text}, "park a verdict")
    with pytest.raises(gate.ReleaseGateError, match="PENDING_REQUALIFICATION is not empty"):
        _verify(history)


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
    report = json.loads((root / "docs" / "release-evidence-report-1.1.0.json").read_text("utf-8"))
    assert report["status"] == "candidate" and report["freeze"] is None
    assert gate.main(["--tag", "v1.1.0-rc.1", "--root", str(root)]) == 0


# --- the registry is read, never run ------------------------------------------


def test_the_committed_registry_reads_as_pytest_reads_it() -> None:
    """The gate's static reading of the real registry equals what pytest computes."""
    path = REPO_ROOT / "tests" / "test_committed_evidence.py"
    registry = gate.parse_registry(path.read_text("utf-8"), "registry")
    executed = runpy.run_path(str(path))
    assert registry.live == executed["LIVE_VERDICTS"]
    assert registry.lane_verdicts == executed["LANE_VERDICTS"]
    assert registry.retired == executed["RETIRED_VERDICTS"]
    assert registry.pending == executed["PENDING_REQUALIFICATION"]
    assert registry.wp0_manifest == executed["WP0_MANIFEST"]


_LIVE = (
    "LIVE_VERDICTS = {\n    relative: finalizer\n"
    "    for relative, finalizer in LANE_VERDICTS.items()\n"
    "    if relative not in RETIRED_VERDICTS\n"
    "    and relative not in PENDING_REQUALIFICATION\n}\n"
)
_TABLES = (
    "LANE_VERDICTS = {'a/x/verification.json': 'f.py'}\n"
    "RETIRED_VERDICTS = set()\n"
    "PENDING_REQUALIFICATION: set[str] = set()\n"
    "WP0_MANIFEST = 'w/x/manifest.json'\n"
)


@pytest.mark.parametrize(
    "planted",
    [
        "LANE_VERDICTS['b/y/verification.json'] = 'g.py'\n",
        "del RETIRED_VERDICTS\n",
        "RETIRED_VERDICTS.discard('a/x/verification.json')\n",
        "RETIRED_VERDICTS |= {'a/x/verification.json'}\n",
        "LANE_VERDICTS.update(dict(b='c'))\n",
        "LANE_VERDICTS.update({'b': name})\n",
        "RETIRED_VERDICTS.update(set(['a']))\n",
        "LANE_VERDICTS = {}\n",
        "globals()['LANE_VERDICTS'] = {}\n",
        "mutate(LANE_VERDICTS)\n",
        "def f():\n    global RETIRED_VERDICTS\n    RETIRED_VERDICTS = set()\n",
        "import sys\nsys.modules[__name__].__dict__['WP0_MANIFEST'] = 'x'\n",
        "for LANE_VERDICTS in []:\n    pass\n",
        "alias = RETIRED_VERDICTS\nalias.clear()\n",
        "pair = (LANE_VERDICTS, 1)\n",
        "def f():\n    return LANE_VERDICTS\n",
        "import this_module\nthis_module.LANE_VERDICTS.clear()\n",
    ],
)
def test_a_table_written_any_other_way_is_refused(planted: str) -> None:
    with pytest.raises(gate.ReleaseGateError):
        gate.parse_registry(_TABLES + planted + _LIVE, "planted")


def test_a_table_changed_after_the_live_set_is_computed_is_refused() -> None:
    late = "RETIRED_VERDICTS.update({'a/x/verification.json'})\n"
    with pytest.raises(gate.ReleaseGateError, match="after LIVE_VERDICTS is computed"):
        gate.parse_registry(_TABLES + _LIVE + late, "late")


def test_live_verdicts_must_be_the_plain_subtraction() -> None:
    altered = _LIVE.replace("    and relative not in PENDING_REQUALIFICATION\n", "")
    with pytest.raises(gate.ReleaseGateError, match="LIVE_VERDICTS must be assigned once"):
        gate.parse_registry(_TABLES + altered, "altered")
    registry = gate.parse_registry(_TABLES + _LIVE, "plain")
    assert registry.live == {"a/x/verification.json": "f.py"}


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
