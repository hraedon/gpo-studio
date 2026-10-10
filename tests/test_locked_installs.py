"""Every Python install the workflows and the operator run resolves from uv.lock.

Before this, only the Windows CI job used the lockfile; the Linux jobs, the
release gates and the published install instructions resolved ``pyproject.toml``
version ranges at run time, so what CI tested, what the release built and what
an operator installed could each be a different set of dependency versions.

The rule is enforced by ``tests/workflow_grammar.py``: it parses each workflow,
splits every ``run:`` into commands, and accepts only allowlisted programs and,
for those that install or resolve, only listed shapes. The negative cases below
are the bypasses two reviewers found against the substring scanner this
replaced, plus the obvious neighbours; each must be refused.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from workflow_grammar import script_violations, split_commands, workflow_violations

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SMOKE = REPO_ROOT / "scripts" / "installed_package_smoke.sh"
QUICKSTART = REPO_ROOT / "docs" / "windows-quickstart.md"
WORKFLOW_FILES = sorted(WORKFLOWS.glob("*.yml"))


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(?P<body>(?:^(?:    .*|\s*)\n)*)", text, re.M)
    assert match is not None, f"job {job!r} not found"
    return match["body"]


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda path: path.name)
def test_every_workflow_command_is_in_the_grammar(path: Path) -> None:
    assert workflow_violations(path) == []


def test_the_smoke_script_is_in_the_grammar() -> None:
    assert script_violations(SMOKE) == []


def test_the_grammar_sees_the_commands_that_matter() -> None:
    """The control: a lexer that found nothing would pass every file above."""
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "uv sync --locked --extra dev" in text
    seen = {" ".join(command.words[:3]) for command in split_commands(SMOKE.read_text())}
    assert "$TMPDIR/venv/bin/pip install --require-hashes" in seen
    assert "uv build --wheel" in seen


_STEP = """name: probe
on: push
jobs:
  probe:
    runs-on: ubuntu-latest
    {env}
    steps:
      - uses: actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd # v5
{steps}
"""

UNLOCKED_VARIANTS = {
    # Sol B2 / DeepSeek N3, verbatim shapes.
    "env_indirection": ("env: {PIP_COMMAND: pip}", "      - run: $PIP_COMMAND install requests"),
    "folded_scalar": ("", "      - run: >-\n          python -m pip\n          install requests"),
    "composite_action": ("", "      - uses: ./.github/actions/unlocked"),
    "same_line_comment_flags": (
        "",
        "      - run: uv sync --locked --extra dev; python -m pip install requests"
        " # --no-deps --no-index",
    ),
    "uv_pip_comment_flags": (
        "",
        "      - run: uv sync --locked --extra dev; uv pip install requests"
        " # --require-hashes -r ignored",
    ),
    "uv_pip_sync": ("", "      - run: uv pip sync requirements.txt"),
    "expression_command": (
        "env: {INSTALL_CMD: pip install x}",
        "      - run: ${{ env.INSTALL_CMD }}",
    ),
    # Neighbours.
    "anchor_alias": (
        "",
        "      - &unlocked\n        run: >-\n          python -m pip\n          install requests\n"
        "      - *unlocked",
    ),
    "uvx": ("", "      - run: uvx ruff check ."),
    "uv_tool": ("", "      - run: uv tool install ruff"),
    "uv_build_unlocked": ("", "      - run: uv build --wheel --out-dir dist"),
    "uv_sync_unlocked": ("", "      - run: uv sync --extra dev"),
    "uv_run_unlocked": ("", "      - run: uv run pytest"),
    "uv_run_pip": ("", "      - run: uv run --locked pip install requests"),
    "uv_export_with_dev": (
        "",
        "      - run: uv export --locked --extra dev --no-emit-project --format requirements.txt"
        " --output-file r.txt",
    ),
    "pip_hashes_plus_package": ("", "      - run: pip install --require-hashes -r r.txt requests"),
    "pip_no_deps_not_artifact": ("", "      - run: pip install --no-deps --no-index requests"),
    "pip_editable": ("", "      - run: pip install -e '.[dev]'"),
    "venv_pip_path": ("", "      - run: /tmp/v/bin/pip install requests"),
    "variable_pip_path": ("", """      - run: '"$VENV/bin/pip" install requests'"""),
    "isolated_build": ("", "      - run: python -m build --wheel"),
    "pipx": ("", "      - run: pipx install ruff"),
    "eval": ("", '      - run: eval "pip install requests"'),
    "bash_c": ("", "      - run: bash -c 'pip install requests'"),
    "sh_c": ("", "      - run: sh -c 'pip install requests'"),
    "backticks": ("", "      - run: echo `pip install requests`"),
    "substitution": ("", "      - run: echo $(pip install requests)"),
    "heredoc_installer": (
        "",
        "      - run: |\n          python - <<'PY'\n          import subprocess\n"
        "          subprocess.run(['pip', 'install', 'requests'])\n          PY",
    ),
    "npm_install": ("", "      - run: npm install left-pad"),
    "curl_remote": ("", "      - run: curl -sSf https://example.invalid/install.sh -o i.sh"),
    "unknown_program": ("", "      - run: poetry install"),
    "pwsh_unpinned_module": (
        "",
        "      - shell: pwsh\n        run: Install-Module PSScriptAnalyzer -Force",
    ),
    "unpinned_action": ("", "      - uses: astral-sh/setup-uv@v7"),
}


@pytest.mark.parametrize("name", sorted(UNLOCKED_VARIANTS))
def test_an_unlocked_or_unreadable_command_is_refused(name: str, tmp_path: Path) -> None:
    env, steps = UNLOCKED_VARIANTS[name]
    workflow = tmp_path / "probe.yml"
    workflow.write_text(_STEP.format(env=env, steps=steps), encoding="utf-8")
    assert workflow_violations(workflow), f"{name} was accepted"


def test_the_negative_template_itself_is_accepted(tmp_path: Path) -> None:
    """Without this, every variant above could fail for a reason unrelated to its command."""
    workflow = tmp_path / "probe.yml"
    workflow.write_text(
        _STEP.format(env="", steps="      - run: uv sync --locked --extra dev"), encoding="utf-8"
    )
    assert workflow_violations(workflow) == []


def test_ci_checks_the_lockfile_matches_pyproject() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv lock --check" in _job_block(ci, "test")


def test_every_setup_uv_installs_the_same_exact_uv_the_project_requires() -> None:
    """DeepSeek N4: the action was pinned, the uv it installed floated."""
    import yaml

    versions = []
    for path in WORKFLOW_FILES:
        for job in (yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"] or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                    versions.append(str((step.get("with") or {}).get("version", "")))
    assert len(versions) >= 10, versions
    assert len(set(versions)) == 1, f"setup-uv installs different uv versions: {set(versions)}"
    (version,) = set(versions)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    tool = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]
    floor = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", tool["required-version"])
    assert floor is not None, tool["required-version"]
    assert tuple(map(int, version.split("."))) >= tuple(map(int, floor.groups()))


def test_the_published_requirements_are_runtime_only() -> None:
    """No dev extra and no dependency group may leak into the operator's lock."""
    import yaml

    release = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    exports = [
        command.words
        for step in release["jobs"]["publish"]["steps"]
        if "run" in step
        for command in split_commands(str(step["run"]))
        if command.words[:2] == ["uv", "export"] and "requirements-lock.txt" in command.words
    ]
    # Not --no-dev: build-backend is a default group, and --no-dev would keep it.
    assert exports == [
        [
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--no-emit-project",
            "--format",
            "requirements.txt",
            "--output-file",
            "requirements-lock.txt",
        ]
    ]


def test_requirements_lock_is_checksummed_attested_and_published() -> None:
    publish = _job_block((WORKFLOWS / "release.yml").read_text(encoding="utf-8"), "publish")
    sums = re.search(r"sha256sum (?P<args>[^>]*)> SHA256SUMS", publish.replace("\\\n", " "))
    assert sums is not None
    assert "requirements-lock.txt" in sums["args"].split()

    provenance = publish.split("actions/attest-build-provenance", 1)[1].split("- name:", 1)[0]
    subjects = provenance.split("subject-path: |", 1)[1].split()
    assert "requirements-lock.txt" in subjects and "SHA256SUMS" in subjects

    create = publish.split('gh release create "$GITHUB_REF_NAME"', 1)[1].split("--verify-tag")[0]
    assert "requirements-lock.txt" in create.split()

    export = publish.index("--output-file requirements-lock.txt")
    assert publish.index("--no-isolation --outdir dist\n") < export, (
        "export after the build, or the file can be swept into the sdist"
    )
    assert export < publish.index("bash scripts/installed_package_smoke.sh")


def _powershell_blocks() -> list[list[str]]:
    text = QUICKSTART.read_text(encoding="utf-8")
    return [block.splitlines() for block in re.findall(r"```powershell\n(.*?)```", text, re.S)]


def test_the_quickstart_installs_the_wheel_from_the_lock() -> None:
    """Each wheel install is locked, or is the fallback for a release without the file."""
    wheel_installs = 0
    locked_installs = 0
    for block in _powershell_blocks():
        for index, line in enumerate(block):
            if "pip install" not in line:
                continue
            if "--require-hashes -r $Requirements.FullName" in line:
                locked_installs += 1
                continue
            if "$Wheel.FullName" not in line:
                assert line.strip() == "& $Python -m pip install --upgrade pip", line
                continue
            wheel_installs += 1
            if "--no-deps --no-index" in line:
                continue
            previous = next(text for text in reversed(block[:index]) if text.strip())
            assert previous.strip() == "} else {", (
                f"an unlocked wheel install outside the no-lockfile fallback: {line!r}"
            )
    # install, upgrade and rollback: one locked path and one fallback each.
    assert wheel_installs == 6
    assert locked_installs == 3


def test_the_quickstart_verifies_the_lockfile_and_the_attestations() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")
    checksum = next(
        "\n".join(block)
        for block in _powershell_blocks()
        if any("Expected exactly one gpo_studio wheel" in line for line in block)
    )
    assert "requirements-lock\\.txt$" in checksum
    assert "foreach ($File in @($Wheel, $Requirements)" in checksum
    # The attestation instruction names the workflow release.yml attests with.
    assert "gh attestation verify $File.FullName --repo hraedon/gpo-studio" in text
    assert "--signer-workflow hraedon/gpo-studio/.github/workflows/release.yml" in text
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "actions/attest-build-provenance" in _job_block(release, "publish")
