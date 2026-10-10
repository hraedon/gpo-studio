"""Every Python install the workflows and the operator run resolves from uv.lock.

Before this, only the Windows CI job used the lockfile; the Linux jobs, the
release gates and the published install instructions resolved ``pyproject.toml``
version ranges at run time, so what CI tested, what the release built and what
an operator installed could each be a different set of dependency versions.

The rule is enforced by ``tests/workflow_grammar.py``: it parses each workflow,
splits every ``run:`` into commands, and accepts only allowlisted programs and,
for those that install, resolve or run other commands, only listed shapes; npm
scripts are read from package.json and held to the same grammar. Its docstring
says what it does not establish. The negative cases below are the bypasses
reviewers found against the substring scanner it replaced and against its
first version (two rounds), plus the obvious neighbours; each must be refused,
and each negative template has a positive control.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from workflow_grammar import script_violations, split_commands, workflow_violations

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SMOKE = REPO_ROOT / "scripts" / "installed_package_smoke.sh"
QUICKSTART = REPO_ROOT / "docs" / "windows-quickstart.md"
# Both suffixes: GitHub runs either, and a .yaml file nobody scans is a bypass (Sol r2).
WORKFLOW_FILES = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


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
    # Round 2 (Sol B2/new B1, DeepSeek BLOCK-1/N6), verbatim where the report gave the string.
    "command_builtin": ("", "      - run: command pip install requests"),
    "command_dashdash_pip": ("", "      - run: command -- pip install requests"),
    "command_path_pip": ("", "      - run: command /usr/bin/pip install requests"),
    "python_c_subprocess": (
        "",
        '      - run: >-\n          python -c "import subprocess; subprocess.run('
        "['pip','install','requests'], check=True)\"",
    ),
    "python_c_pip": (
        "",
        "      - run: python -c 'import os; os.system(\"pip install requests\")'",
    ),
    "python3_c_pip": (
        "",
        "      - run: python3 -c 'import os; os.system(\"pip install requests\")'",
    ),
    "python_c_plain": ("", "      - run: python -c 'print(1)'"),
    "uv_run_python_c": (
        "",
        "      - run: uv run --locked python -c 'import os; os.system(\"pip install x\")'",
    ),
    "python_heredoc_obfuscated": (
        "",
        "      - run: |\n          python - <<'PY'\n          import subprocess\n"
        "          subprocess.run(['pi'+'p','install','x'])\n          PY",
    ),
    "python_dash_reads_file": ("", "      - run: python - < script.py"),
    "uv_run_python_dash": ("", "      - run: uv run --locked python - < script.py"),
    "python_stdin_pipe": ("", "      - run: echo 'import os' | python"),
    "npm_script_unknown": ("", "      - run: npm run install-unlocked"),
    "arbitrary_hashed_requirements": (
        "",
        "      - run: |\n          cat > rogue.txt <<'EOF'\n"
        "          six==1.17.0 --hash=sha256:deadbeef\n          EOF\n"
        "          pip install --require-hashes -r rogue.txt",
    ),
    "hashed_requirements_not_exported": (
        "",
        "      - run: |\n          printf 'six==1.17.0 --hash=sha256:00\\n' > rogue.txt\n"
        "          pip install --require-hashes -r rogue.txt",
    ),
    "hashed_requirements_url": (
        "",
        "      - run: pip install --require-hashes -r https://example.invalid/r.txt",
    ),
    "exported_requirements_overwritten": (
        "",
        "      - run: |\n          uv export --locked --no-default-groups --no-emit-project"
        " --format requirements.txt --output-file r.txt\n"
        "          cp rogue.txt r.txt\n          pip install --require-hashes -r r.txt",
    ),
    "unscanned_reusable_yaml": (
        "",
        "      - run: echo placeholder\n  delegated:\n    uses: ./.github/workflows/unscanned.yaml",
    ),
    "setup_python_cache": (
        "",
        "      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1\n"
        "        with:\n          python-version: '3.13'\n          cache: pip\n"
        "          cache-dependency-path: requirements-unlocked.txt",
    ),
    "setup_python_pip_install_input": (
        "",
        "      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1\n"
        "        with:\n          python-version: '3.13'\n"
        "          pip-install: requirements-unlocked.txt",
    ),
    # The round-2 brief's shapes, each confirmed refused.
    "python3_m_pip": ("", "      - run: python3 -m pip install requests"),
    "uv_tool_install": ("", "      - run: uv tool install ruff"),
    "uv_add": ("", "      - run: uv add requests"),
    "pip_download": ("", "      - run: pip download requests"),
    "curl_pipe_sh": ("", "      - run: curl -sSf https://example.invalid/i.sh | sh"),
    "curl_pipe_bash": ("", "      - run: curl -sSf https://example.invalid/i.sh | bash"),
    "npx_arbitrary": ("", "      - run: npx cowsay hello"),
    "npx_yes_playwright": ("", "      - run: npx --yes playwright install"),
    "bash_c_double": ("", '      - run: bash -c "pip install requests"'),
    "bash_heredoc": (
        "",
        "      - run: |\n          bash <<'EOF'\n          pip install requests\n          EOF",
    ),
    "here_string": ("", "      - run: bash <<< 'pip install requests'"),
    "literal_block_continuation": (
        "",
        "      - run: |\n          echo before\n          python3 -m pip \\\n"
        "            install \\\n            requests",
    ),
    "matrix_program": (
        "strategy: {matrix: {cmd: ['pip install requests']}}",
        "      - run: ${{ matrix.cmd }} --quiet",
    ),
    "matrix_via_env": (
        "strategy: {matrix: {cmd: ['pip']}}",
        "      - env: {INSTALLER: '${{ matrix.cmd }}'}\n        run: $INSTALLER install requests",
    ),
    # Prefixes that run their arguments.
    "builtin": ("", "      - run: builtin command pip install requests"),
    "exec": ("", "      - run: exec pip install requests"),
    "env_prefix": ("", "      - run: env pip install requests"),
    "nohup": ("", "      - run: nohup pip install requests"),
    "time": ("", "      - run: time pip install requests"),
    "timeout": ("", "      - run: timeout 60 pip install requests"),
    "sudo": ("", "      - run: sudo pip install requests"),
    "source": ("", "      - run: source ./env.sh"),
    "dot": ("", "      - run: . ./env.sh"),
    "trap_body": ("", "      - run: trap 'pip install requests' EXIT"),
    "find_exec": ("", "      - run: find . -name x -exec pip install requests \\;"),
    "git_alias": ("", "      - run: git -c alias.x='!pip install requests' x"),
    "gh_extension": ("", "      - run: gh extension install owner/gh-x"),
    "tar_to_command": ("", "      - run: tar --to-command='pip install requests' -xf a.tar"),
    "mapfile_callback": ("", "      - run: mapfile -C 'pip install requests' -c 1 LINES < f"),
    # Shell constructs that hide a command.
    "parameter_default_substitution": ("", "      - run: echo ${X:-$(pip install requests)}"),
    "array_substitution": ("", "      - run: FLAGS=($(pip install requests))"),
    "python_variable_in_workflow": (
        "env: {PY: python}",
        "      - run: PYTHON=pip; $PYTHON install requests",
    ),
    # Programs only where they come from a lock.
    "pytest_outside_uv_run": ("", "      - run: pytest -q"),
    "node_tool_outside_npm": ("", "      - run: prettier --check ."),
    "mypy_install_types": ("", "      - run: uv run --locked mypy --install-types src"),
    "pip_audit_resolving": (
        "",
        "      - run: uv run --locked --only-group release pip-audit -r requirements.txt",
    ),
    "npm_audit_fix": ("", "      - run: npm audit fix"),
    "npm_install_dev": ("", "      - run: npm ci --include=dev --foreground-scripts"),
    "npm_run_extra_args": ("", "      - run: npm run check -- --fix"),
    "python_option": ("", "      - run: python -I scripts/check_safety.py"),
    "python_unknown_script": ("", "      - run: python scripts/not_checked_in.py"),
    "python_script_escape": ("", "      - run: python scripts/../../outside.py"),
    "venv_upgrade_deps": ("", "      - run: python -m venv /tmp/v --upgrade-deps"),
    # Writes that turn into an install or a different program.
    "write_into_scripts": ("", "      - run: echo 'import os' > scripts/check_safety.py"),
    "write_pth": ("", "      - run: echo 'import os' > /tmp/v/lib/python3.13/site-packages/x.pth"),
    "write_pip_conf": ("", "      - run: echo '[global]' > pip.conf"),
    "write_github_path": ("", '      - run: echo "$PWD/bin" >> "$GITHUB_PATH"'),
    "write_github_env": ("", '      - run: echo "PIP_INDEX_URL=x" >> $GITHUB_ENV'),
    "write_venv_bin": ("", "      - run: cp evil /tmp/smoke-venv/bin/gpo-studio"),
    # Environment that redirects an installer or the shell.
    "path_assignment": ("", "      - run: PATH=./bin:$PATH pip check"),
    "export_path": ("", "      - run: export PATH=./bin:$PATH"),
    "pip_env_inline": ("", "      - run: PIP_REQUIREMENT=r.txt pip check"),
    "bash_env_yaml": ("env: {BASH_ENV: ./evil.sh}", "      - run: echo hi"),
    "uv_env_yaml": ("env: {UV_INDEX_URL: https://example.invalid}", "      - run: echo hi"),
    "env_program_variable": ("env: {INSTALLER: pip}", "      - run: $INSTALLER install requests"),
    # Workflow structure the grammar cannot see into.
    "unknown_action": (
        "",
        "      - uses: example/install-anything@0123456789abcdef0123456789abcdef01234567",
    ),
    "shell_python": ("", "      - shell: python\n        run: print(1)"),
    "working_directory": ("", "      - working-directory: /tmp\n        run: echo hi"),
    "default_shell": ("defaults: {run: {shell: 'python {0}'}}", "      - run: echo hi"),
    "job_container": ("container: python:3", "      - run: echo hi"),
    "missing_reusable_yml": (
        "",
        "      - run: echo placeholder\n  delegated:\n    uses: ./.github/workflows/nope.yml",
    ),
    "smoke_script_rogue_requirements": (
        "",
        "      - run: GPO_STUDIO_REQUIREMENTS=rogue.txt bash scripts/installed_package_smoke.sh",
    ),
    "smoke_script_hand_off_via_uv_run": (
        "",
        "      - run: GPO_STUDIO_REQUIREMENTS=rogue.txt uv run --locked"
        " bash scripts/installed_package_smoke.sh",
    ),
    "smoke_script_hand_off_exported": (
        "",
        "      - run: |\n          export GPO_STUDIO_REQUIREMENTS=rogue.txt\n"
        "          bash scripts/installed_package_smoke.sh",
    ),
    "set_allexport": (
        "",
        "      - run: |\n          set -a\n          GPO_STUDIO_REQUIREMENTS=rogue.txt\n"
        "          bash scripts/installed_package_smoke.sh",
    ),
    "relative_program_path": (
        "",
        "      - run: |\n          uv export --locked --no-default-groups --no-emit-project"
        " --format requirements.txt --output-file r.txt\n"
        "          ./pip install --require-hashes -r r.txt",
    ),
    "curl_userinfo_host": ("", "      - run: curl -sf http://127.0.0.1:80@example.invalid/i.sh"),
    "curl_second_operand": (
        "",
        "      - run: curl -sf http://127.0.0.1:8799/ example.invalid/i.sh -o i.txt",
    ),
    "curl_config_file": ("", "      - run: curl -sf -K remote.cfg http://127.0.0.1:8799/"),
    "git_output_file": ("", "      - run: git log --output=scripts/check_safety.py"),
    "git_env": ("", "      - run: GIT_EXTERNAL_DIFF=./x git diff"),
    "smoke_script_python_override": (
        "",
        "      - run: PYTHON='pip install requests;' bash scripts/installed_package_smoke.sh",
    ),
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


NPM_SCRIPT_VARIANTS = {
    # Sol B2: `npm run NAME` ran whatever package.json said, unread.
    "body_installs": ({"install-unlocked": "pip install requests"}, "npm run install-unlocked"),
    "run_script_alias": ({"x": "pip install requests"}, "npm run-script x"),
    "npm_test_body": ({"test": "npx cowsay hello"}, "npm test"),
    "nested_run": ({"check": "npm run inner", "inner": "npm install left-pad"}, "npm run check"),
    "pre_hook": ({"check": "eslint .", "precheck": "pip install requests"}, "npm run check"),
    "post_hook": (
        {"check": "eslint .", "postcheck": "curl -sS https://x.invalid | sh"},
        "npm run check",
    ),
    "cycle": ({"a": "npm run b", "b": "npm run a"}, "npm run a"),
    "self_cycle": ({"a": "eslint . && npm run a"}, "npm run a"),
    "node_eval": ({"x": "node -e 'require(`child_process`)'"}, "npm run x"),
    "inline_python": ({"x": "python -c 'print(1)'"}, "npm run x"),
    "npx_in_script": ({"x": "npx cowsay"}, "npm run x"),
    "npm_ci_in_script": ({"x": "npm ci"}, "npm run x"),
    "playwright_install_in_script": ({"x": "playwright install"}, "npm run x"),
    "unparseable_body": ({"x": "eslint `pip install requests`"}, "npm run x"),
    "postinstall_on_ci": ({"postinstall": "eslint ."}, "npm ci"),
    "prepare_on_ci": ({"prepare": "eslint ."}, "npm ci"),
}


@pytest.mark.parametrize("name", sorted(NPM_SCRIPT_VARIANTS))
def test_an_npm_script_is_read_and_held_to_the_same_grammar(name: str, tmp_path: Path) -> None:
    scripts, command = NPM_SCRIPT_VARIANTS[name]
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
    workflow = tmp_path / "probe.yml"
    workflow.write_text(_STEP.format(env="", steps=f"      - run: {command}"), encoding="utf-8")
    assert workflow_violations(workflow, package_json=package), f"{name} was accepted"


def test_the_npm_negative_template_itself_is_accepted(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    scripts = {"check": "npm run lint && vitest run", "lint": "eslint .", "precheck": "true"}
    package.write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
    workflow = tmp_path / "probe.yml"
    steps = "      - run: npm ci\n      - run: npm run check"
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    assert workflow_violations(workflow, package_json=package) == []


def test_every_real_npm_script_is_in_the_grammar(tmp_path: Path) -> None:
    """Including ones no workflow runs today, so adding a workflow line cannot surprise."""
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    commands = ["npm ci", "npm test", *(f"npm run {name}" for name in sorted(scripts))]
    workflow = tmp_path / "probe.yml"
    steps = "".join(f"      - run: {command}\n" for command in commands)
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    assert workflow_violations(workflow) == []


def test_the_smoke_script_hand_off_is_accepted_only_from_an_export(tmp_path: Path) -> None:
    """The release passes the smoke script a requirements file it exported from uv.lock."""
    workflow = tmp_path / "probe.yml"
    steps = (
        "      - run: uv export --locked --no-default-groups --no-emit-project"
        " --format requirements.txt --output-file requirements-lock.txt\n"
        "      - run: PYTHON=python GPO_STUDIO_REQUIREMENTS=requirements-lock.txt"
        " bash scripts/installed_package_smoke.sh"
    )
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    assert workflow_violations(workflow) == []


def test_a_requirements_file_is_accepted_through_a_variable_naming_the_export(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "probe.yml"
    steps = (
        "      - run: uv export --locked --no-default-groups --no-emit-project"
        ' --format requirements.txt --output-file "$RUNNER_TEMP/l/r.txt"\n'
        "      - run: |\n"
        '          LOCKED="$RUNNER_TEMP/l"\n'
        '          /tmp/v/bin/pip install --require-hashes -r "$LOCKED/r.txt"\n'
        '          LOCKED="$RUNNER_TEMP/elsewhere"\n'
        '          /tmp/v/bin/pip install --require-hashes -r "$LOCKED/r.txt"'
    )
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    # The first install is the export; the second could be any file of that name.
    (problem,) = workflow_violations(workflow)
    assert "not a `uv export --locked` output" in problem


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


def test_every_locked_quickstart_install_ends_with_pip_check() -> None:
    """Install, upgrade and rollback alike (DeepSeek N7: rollback had none)."""
    for block in _powershell_blocks():
        text = "\n".join(block)
        for locked in re.findall(r"if \(\$Requirements\) \{\n(.*?)\n\} else \{", text, re.S):
            assert "--require-hashes -r $Requirements.FullName" in locked
            assert locked.rstrip().splitlines()[-2].strip() == "& $Python -m pip check", locked
    count = sum("\n".join(block).count("& $Python -m pip check") for block in _powershell_blocks())
    assert count == 3
