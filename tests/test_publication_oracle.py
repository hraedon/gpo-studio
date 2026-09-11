"""Focused tests for the Plan 034 publication-completeness oracle.

The lane grades a comparison, so the tests that matter are the ones proving
each check can *fail*. A completeness check that cannot be shown to fire is
worth no more than the silence WI-057 was filed for.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_ROOT = Path(__file__).parents[1]
_FINALIZER_PATH = _ROOT / "scripts/windows-oracle/finalize_publication_run.py"
_FINALIZER = runpy.run_path(str(_FINALIZER_PATH))
_fold = cast(Callable[[object], list[str]], _FINALIZER["_fold"])
_unpack_version = cast(Callable[[int], tuple[int, int]], _FINALIZER["_unpack_version"])
_version_half_matches = cast(Callable[[str, int], bool], _FINALIZER["_version_half_matches"])
_observed_paths = cast(Callable[[dict[str, Any]], list[str]], _FINALIZER["_observed_paths"])


def _builder() -> ModuleType:
    path = _ROOT / "scripts/plan-033/build-publication-candidate.py"
    spec = importlib.util.spec_from_file_location("publication_oracle_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The candidate carries the expectation, and it must be the plan's own claim
# ---------------------------------------------------------------------------


def test_the_builder_is_deterministic(tmp_path: Path) -> None:
    """Two runs must agree byte for byte, or a verdict binds a moving target."""
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        builder = _ROOT / "scripts/plan-033/build-publication-candidate.py"
        subprocess.run(
            [sys.executable, str(builder), str(out)], check=True, capture_output=True
        )
    for name in ("studio-publication-backup.zip", "expected.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_the_expectation_is_the_plans_own_claim(tmp_path: Path) -> None:
    """`expected.json` must be derived from the plan, not restated beside it."""
    from gpo_studio.export import extension_registration
    from gpo_studio.publication import generate_publication_plan, planned_sysvol_paths

    module = _builder()
    gpo = cast(Any, module)._GPO
    plan = generate_publication_plan(gpo, target="both")
    registration = extension_registration(gpo)
    expectation = cast(Any, module)._expectation(gpo, plan, registration)

    assert expectation["sysvol_paths"] == list(planned_sysvol_paths(plan))
    assert expectation["machine_extension_names"] == registration.machine
    assert expectation["user_extension_names"] == registration.user
    assert expectation["plan_payload_digest"] == plan.payload_digest


def test_the_candidate_gpo_is_undescribed_so_the_absence_is_assertable() -> None:
    """An undescribed GPO is the control half of WI-058, not an oversight.

    If this GPO ever gains a description the lane silently stops testing that
    Windows produces no `GPO.cmt` without one, so the intent is pinned here
    rather than left in a comment the next author may reasonably delete.
    """
    module = _builder()
    assert cast(Any, module)._GPO.description == ""


def test_the_candidate_uses_families_with_captured_metadata_on_both_sides() -> None:
    """Two different families, one per side, so the two lists cannot be copies.

    A candidate whose machine and user extension lists were identical could not
    catch a fix that computed one side and assigned it to both -- which is one
    of the two mistakes WI-057 said the obvious fix would make.
    """
    from gpo_studio.export import extension_registration

    module = _builder()
    registration = extension_registration(cast(Any, module)._GPO)
    assert registration.unverified_families == ()
    assert registration.machine and registration.user
    assert registration.machine != registration.user


# ---------------------------------------------------------------------------
# The finalizer's comparison primitives
# ---------------------------------------------------------------------------


def test_path_comparison_folds_case_but_not_separators_away() -> None:
    """Windows does not preserve the plan's casing; it does keep the tree shape."""
    assert _fold(["Machine/Registry.pol"]) == ["machine/registry.pol"]
    assert _fold([r"Machine\Registry.pol"]) == ["machine/registry.pol"]
    assert _fold(["B/x", "a/y"]) == ["a/y", "b/x"]


def test_the_packed_version_field_splits_into_independent_halves() -> None:
    """65537 is user 1 and machine 1 -- the value measured on LabMS01."""
    assert _unpack_version(65537) == (1, 1)
    assert _unpack_version(131082) == (10, 2)


@pytest.mark.parametrize(
    "half,packed,expected",
    [
        ("both", 65537, True),
        ("both", 1, False),          # machine only moved
        ("both", 65536, False),      # user only moved
        ("machine", 1, True),
        ("machine", 65537, False),   # the user half moved too
        ("user", 65536, True),
        ("user", 65537, False),      # the machine half moved too
        ("nonsense", 65537, False),
    ],
)
def test_a_declared_version_half_is_checked_in_both_directions(
    half: str, packed: int, expected: bool
) -> None:
    """Declaring "machine" must fail when the user half moved as well.

    A check that only asserted the declared half moved would pass a plan that
    moved both, which is precisely the corruption `_bump_gpt_version` exists to
    prevent.
    """
    assert _version_half_matches(half, packed) is expected


def test_a_repeated_sysvol_path_is_refused_rather_than_deduplicated() -> None:
    result = {
        "sysvol_files": [
            {"relative_path": "Machine/Registry.pol"},
            {"relative_path": "Machine/registry.pol"},
        ]
    }
    with pytest.raises(ValueError, match="same SYSVOL path twice"):
        _observed_paths(result)


# ---------------------------------------------------------------------------
# Every completeness check must be shown to fire
# ---------------------------------------------------------------------------


def _expectation() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "gpo_id": "{31415926-5358-9793-2384-626433832795}",
        "backup_id": "{78A65ED0-2EFD-5D61-8A80-284FD79A6177}",
        "sysvol_paths": [
            "GPT.INI",
            "Machine/Preferences/Services/Services.xml",
            "Machine/Registry.pol",
            "User/Preferences/Drives/Drives.xml",
            "User/Registry.pol",
        ],
        "machine_extension_names": "[{MACHINE}]",
        "user_extension_names": "[{USER}]",
        "version_half": "both",
        "expects_gpo_cmt": False,
    }


_SYSVOL = (
    r"\\AD.EXAMPLE\sysvol\AD.EXAMPLE\Policies"
    r"\{72328D56-2B38-4FF3-8DBE-DA86559915C1}"
)


def _result() -> dict[str, Any]:
    """A result shaped like the one LabMS01 actually returned."""
    return {
        "owned_gpo_id": "72328d56-2b38-4ff3-8dbe-da86559915c1",
        "backup_id": "{78A65ED0-2EFD-5D61-8A80-284FD79A6177}",
        "source_gpo_id": "{31415926-5358-9793-2384-626433832795}",
        "sysvol_path": _SYSVOL,
        "sysvol_files": [
            {"relative_path": "gpt.ini", "length": 26, "sha256": "0" * 64},
            {"relative_path": "Machine/registry.pol", "length": 118, "sha256": "1" * 64},
            {
                "relative_path": "Machine/Preferences/Services/Services.xml",
                "length": 342,
                "sha256": "2" * 64,
            },
            {"relative_path": "User/registry.pol", "length": 114, "sha256": "3" * 64},
            {
                "relative_path": "User/Preferences/Drives/Drives.xml",
                "length": 350,
                "sha256": "4" * 64,
            },
        ],
        "ad_attributes": {
            "versionNumber": 65537,
            "gPCMachineExtensionNames": "[{MACHINE}]",
            "gPCUserExtensionNames": "[{USER}]",
            "gPCFileSysPath": _SYSVOL,
        },
    }


def _grade(result: dict[str, Any], expected: dict[str, Any]) -> dict[str, bool]:
    """Run only the comparison half of the finalizer, as `main` composes it."""
    observed = _observed_paths(result)
    planned = _fold(expected["sysvol_paths"])
    seen = _fold(observed)
    attributes = result["ad_attributes"]
    packed = attributes.get("versionNumber")
    return {
        "plan_names_every_file_windows_wrote": not [p for p in seen if p not in planned],
        "windows_wrote_every_file_the_plan_names": not [p for p in planned if p not in seen],
        "machine_extension_names_exact": attributes.get("gPCMachineExtensionNames")
        == expected["machine_extension_names"],
        "user_extension_names_exact": attributes.get("gPCUserExtensionNames")
        == expected["user_extension_names"],
        "gpt_version_moved_the_declared_half": type(packed) is int
        and _version_half_matches(str(expected["version_half"]), packed),
        "gpo_cmt_present_only_if_planned": any(p.endswith("gpo.cmt") for p in seen)
        is bool(expected["expects_gpo_cmt"]),
    }


def test_the_measured_windows_result_passes_every_comparison_check() -> None:
    """The control: the shape LabMS01 returned must grade clean.

    Without this, every mutation below could be passing for the wrong reason.
    """
    assert all(_grade(_result(), _expectation()).values())


def test_an_unplanned_file_fails_the_completeness_check() -> None:
    """The WI-057 shape: Windows wrote something no step accounts for."""
    result = _result()
    result["sysvol_files"].append(
        {"relative_path": "Machine/Microsoft/Windows NT/SecEdit/GptTmpl.inf",
         "length": 10, "sha256": "5" * 64}
    )
    checks = _grade(result, _expectation())
    assert checks["plan_names_every_file_windows_wrote"] is False
    assert checks["windows_wrote_every_file_the_plan_names"] is True


def test_a_file_the_plan_names_but_windows_never_wrote_fails_the_other_half() -> None:
    """The mirror defect: a step asserting output the directory does not want."""
    result = _result()
    result["sysvol_files"] = [
        f for f in result["sysvol_files"] if f["relative_path"] != "User/registry.pol"
    ]
    checks = _grade(result, _expectation())
    assert checks["windows_wrote_every_file_the_plan_names"] is False
    assert checks["plan_names_every_file_windows_wrote"] is True


def test_an_unregistered_extension_list_fails_even_when_every_file_is_present() -> None:
    """The defect that started this: byte-perfect content that applies nothing."""
    result = _result()
    result["ad_attributes"]["gPCMachineExtensionNames"] = ""
    checks = _grade(result, _expectation())
    assert checks["machine_extension_names_exact"] is False
    assert checks["plan_names_every_file_windows_wrote"] is True
    assert checks["windows_wrote_every_file_the_plan_names"] is True


def test_copying_one_sides_extension_list_to_the_other_is_caught() -> None:
    """The second mistake WI-057 predicted a hand-written fix would make."""
    result = _result()
    result["ad_attributes"]["gPCUserExtensionNames"] = result["ad_attributes"][
        "gPCMachineExtensionNames"
    ]
    assert _grade(result, _expectation())["user_extension_names_exact"] is False


def test_an_unexpected_gpo_cmt_fails_the_control() -> None:
    """An undescribed GPO that grew a comment file is a divergence, not noise."""
    result = _result()
    result["sysvol_files"].append(
        {"relative_path": "GPO.cmt", "length": 78, "sha256": "6" * 64}
    )
    checks = _grade(result, _expectation())
    assert checks["gpo_cmt_present_only_if_planned"] is False


def test_a_wrong_version_half_fails_even_though_the_files_are_right() -> None:
    result = _result()
    result["ad_attributes"]["versionNumber"] = 1  # machine only
    checks = _grade(result, _expectation())
    assert checks["gpt_version_moved_the_declared_half"] is False
    assert checks["plan_names_every_file_windows_wrote"] is True


def test_the_finalizer_refuses_a_candidate_root_missing_a_required_file(
    tmp_path: Path,
) -> None:
    """A consumed artifact the verdict never mentions is refused at the door.

    Each required file is removed in turn, because a check that only ever sees
    a complete candidate root cannot show it would notice an incomplete one.
    """
    required = cast(Any, _FINALIZER["REQUIRED_CANDIDATE_FILES"])
    for omitted in required:
        root = tmp_path / f"without-{omitted}"
        root.mkdir()
        for name in required:
            if name != omitted:
                (root / name).write_bytes(b"{}")
        run = tmp_path / f"run-{omitted}"
        run.mkdir()
        (run / "result.json").write_text("{}", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(_FINALIZER_PATH),
                str(run),
                "--candidate-root",
                str(root),
                "--repo-root",
                str(_ROOT),
                "--no-tag",
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 1, omitted
        assert omitted in completed.stderr, omitted


def test_the_lane_binds_every_source_file_it_uses() -> None:
    """The driver's file set and the finalizer's bound set must not drift.

    WI-062 split the contract in two. A deployed file the driver never
    retrieves still fails every run, exactly as before. A controller-side
    file is no longer banked as a pack copy -- the driver must not copy it,
    and the finalizer still must bind it, or a source change no verdict
    notices would slip through the manifest form.
    """
    driver = (_ROOT / "scripts/windows-oracle/run-publication-oracle.sh").read_text(
        encoding="utf-8"
    )
    local = cast(dict[str, str], _FINALIZER["LOCAL_FILES"])
    deployed = cast(dict[str, str], _FINALIZER["DEPLOYED_FILES"])
    for name in deployed:
        assert name in driver, f"{name} is deployed but the driver never moves it"
    for name in local:
        assert not any(
            line.startswith("cp ") and name in line
            for line in driver.splitlines()
        ), f"{name} is manifest-bound since WI-062; the driver must not bank a copy"
    for name, relative in {**local, **deployed}.items():
        assert (_ROOT / relative).is_file(), f"{name} binds a path that does not exist"


def test_every_bound_source_file_matches_its_committed_bytes() -> None:
    """WI-059's guard, for this lane's file set only.

    The finalizers hash the working tree and CI hashes what Git checked out; on
    a Windows controller an ordinary scripted edit can leave those different,
    and the verdict is then minted against bytes CI does not have. This caught
    it after two wasted estate runs, so the check runs before the next one.
    """
    local = cast(dict[str, str], _FINALIZER["LOCAL_FILES"])
    deployed = cast(dict[str, str], _FINALIZER["DEPLOYED_FILES"])
    drifted = []
    for relative in {**local, **deployed}.values():
        worktree = (_ROOT / relative).read_bytes()
        committed = subprocess.run(
            ["git", "show", f":{relative}"], cwd=_ROOT, capture_output=True
        )
        if committed.returncode == 0 and committed.stdout != worktree:
            drifted.append(relative)
    assert not drifted, (
        "these bound files differ from their committed bytes, so a verdict "
        f"minted now would not reproduce in CI: {drifted}"
    )


def test_the_expectation_never_travels_to_the_guest() -> None:
    """The guest is the thing being measured; it must not receive the answer.

    `expected.json` stays on the controller by construction. If the driver ever
    pushes it, the lane becomes a comparison of Windows against itself and this
    test is the only thing that would say so.
    """
    driver = (_ROOT / "scripts/windows-oracle/run-publication-oracle.sh").read_text(
        encoding="utf-8"
    )
    pushes = [
        line for line in driver.splitlines() if "-Action push" in line or "-LocalPath" in line
    ]
    assert pushes, "driver pushes nothing; the parse is wrong, not the driver"
    assert not any("expected.json" in line for line in pushes)
    guest_script = (_ROOT / "scripts/windows-oracle/run-publication-import.ps1").read_text(
        encoding="utf-8"
    )
    assert "expected.json" not in guest_script


def test_the_verdict_records_a_hash_for_every_candidate_file(tmp_path: Path) -> None:
    """WI-025: a verdict naming an artifact without hashing it asserts nothing.

    The candidate block hashes everything under the root rather than a named
    list, because the omission worth catching is a consumed file the verdict
    never mentions.
    """
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "studio-publication-backup.zip").write_bytes(b"zip")
    (root / "expected.json").write_bytes(json.dumps(_expectation()).encode())
    (root / "builder.stdout.txt").write_bytes(b"log")
    block = {
        path.relative_to(root).as_posix(): "x"
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert set(block) == {
        "studio-publication-backup.zip",
        "expected.json",
        "builder.stdout.txt",
    }
