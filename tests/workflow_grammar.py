"""A closed set of reviewed command templates for CI, the release and the smoke script.

``tests/test_locked_installs.py`` uses it to hold every dependency install and
resolution to uv.lock (Python) and package-lock.json (npm). Three earlier
versions parsed the options of general-purpose programs, and every review round
found another side channel (``command pip``, ``python -c``, ``printf -v``,
``cp --target-directory=``, a written ``.git/config`` that turns ``git fetch``
into ``ext::sh``, ``CURL_HOME``, PowerShell casing). So this does not interpret
options at all. After parsing the workflow YAML and splitting each ``run:``
script into simple commands with a small shell lexer, it requires:

* every command -- assignments, ``for`` headers and the inside of every command
  substitution included -- to equal one entry of ``TEMPLATES``: literal tokens
  plus a few named slots with tight patterns, each entry with a one-line reason;
* every redirection target to be one its template lists (``/dev/null`` always);
* every ``env:`` entry, at any level, to be one of ``ENV`` (name and exact value);
* facts a template needs (a lock exported, ``npm ci`` run, the venv or the
  distributions built) to have been established earlier in the same job;
* every package.json script a template runs to be held to the same table;
* every PowerShell step, and every step of a Windows job (PowerShell by
  default), to be one of ``PWSH_BODIES``, compared case-insensitively;
* every action and input to be on ``ACTIONS``, every job to run on one of
  ``RUNNERS``, and no ``shell:``, ``working-directory``, default or container
  that would change how the text above runs.

Anything else is a violation. A new command is a deliberate edit to the table.

What it does *not* establish, so nobody leans on it for more:

* It reads workflow text, not what the code it lets run does. ``pytest``,
  ``eslint``, ``vitest``, the repository's own ``scripts/*.py`` and the pinned
  actions all execute code; a test or script that shells out to pip is outside
  this check (it would be reviewed as code).
* ``scripts/installed_package_smoke.sh`` trusts its caller's ``PYTHON``,
  ``GPO_STUDIO_WHEEL`` and ``GPO_STUDIO_REQUIREMENTS``. The workflow call that
  sets them is one literal template (``python``, the wheel found in the ``dist``
  built in that job, the requirements exported in that job); a person running
  the script by hand can point it anywhere.
* ``npx playwright install`` downloads browser builds that the locked
  ``@playwright/test`` names; they are not hashed by package-lock.json.
* Facts are tracked per job in document order; a step skipped by ``if:`` is
  treated as having run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class GrammarError(Exception):
    """The script uses a construct this grammar refuses to interpret."""


@dataclass
class Command:
    #: The program and its arguments, quotes removed; substitutions keep their source text.
    words: list[str]
    #: ``NAME=value`` words before the program (or the whole command, for an assignment).
    assignments: tuple[str, ...] = ()
    #: Targets of output redirections (``>``, ``>>``, ``&>``...), quotes removed.
    writes: tuple[str, ...] = ()
    #: Targets of input redirections (``<``).
    reads: tuple[str, ...] = ()

    @property
    def tokens(self) -> tuple[str, ...]:
        return (*self.assignments, *self.words)


_OPERATORS = ("&&", "||", ";;", ";", "|", "&", "\n", "(", ")", "{", "}")
_RESERVED_PREFIX = {"if", "then", "else", "elif", "do", "while", "until", "!"}
_SKIPPED = {"fi", "done", "esac"}
_CONTINUATION = re.compile(r"[ \t]*\\\n[ \t]*")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
#: ``${NAME}``, ``${#NAME[@]}``, ``${NAME[0]}``, ``${NAME:-literal}``: nothing that runs.
_PARAMETER = re.compile(r"^\$\{#?[A-Za-z_][A-Za-z0-9_]*(\[[^\]$`(){}]*\])?(:?[-=+?][^$`(){}]*)?\}$")


def _read_balanced(text: str, start: int) -> int:
    """Index just past the ``)`` closing the ``(`` before *start*."""
    depth = 1
    index = start
    quote = ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise GrammarError("unbalanced parenthesis")


def split_commands(script: str) -> list[Command]:
    """Simple commands of a POSIX-ish shell script, substitutions included.

    A command substitution's commands come before the command that uses its
    output, and the word keeps the substitution's source text.
    """
    commands: list[Command] = []
    words: list[str] = []
    writes: list[str] = []
    reads: list[str] = []
    word = ""
    in_word = False
    case_depth = 0
    index = 0

    def end_word() -> None:
        nonlocal word, in_word
        if in_word:
            words.append(word)
        word = ""
        in_word = False

    def end_command() -> None:
        nonlocal words, writes, reads
        end_word()
        if words or writes or reads:
            command = _simple(words, writes, reads)
            if command is not None:
                commands.append(command)
        words = []
        writes = []
        reads = []

    while index < len(script):
        char = script[index]
        if char == "\\" and index + 1 < len(script):
            if script[index + 1] == "\n":
                index += 2
                continue
            word += script[index + 1]
            in_word = True
            index += 2
            continue
        if char == "#" and not in_word:
            while index < len(script) and script[index] != "\n":
                index += 1
            continue
        if char == "'":
            end = script.find("'", index + 1)
            if end < 0:
                raise GrammarError("unterminated single quote")
            word += script[index + 1 : end]
            in_word = True
            index = end + 1
            continue
        if char == '"':
            index += 1
            while True:
                if index >= len(script):
                    raise GrammarError("unterminated double quote")
                if script[index] == '"':
                    break
                if script[index] == "\\":
                    word += script[index + 1 : index + 2]
                    index += 2
                    continue
                if script.startswith("$(", index):
                    end = _read_balanced(script, index + 2)
                    commands.extend(split_commands(script[index + 2 : end - 1]))
                    word += _CONTINUATION.sub(" ", script[index:end])
                    index = end
                    continue
                if script[index] == "`":
                    raise GrammarError("backtick command substitution")
                word += script[index]
                index += 1
            in_word = True
            index += 1
            continue
        if char == "`":
            raise GrammarError("backtick command substitution")
        if script.startswith("$(", index) or script.startswith("<(", index):
            end = _read_balanced(script, index + 2)
            commands.extend(split_commands(script[index + 2 : end - 1]))
            word += _CONTINUATION.sub(" ", script[index:end])
            in_word = True
            index = end
            continue
        if script.startswith("${", index):
            end = script.find("}", index)
            if end < 0 or not _PARAMETER.match(script[index : end + 1]):
                shown = script[index : index + 40]
                raise GrammarError(f"parameter expansion this grammar cannot read: {shown!r}")
            word += script[index : end + 1]
            in_word = True
            index = end + 1
            continue
        if char == "(" and in_word and word.endswith("="):
            # An array assignment: FLAGS=(--prerelease)
            end = _read_balanced(script, index + 1)
            if re.search(r"\$\(|<\(|`", script[index:end]):
                raise GrammarError("command substitution inside an array assignment")
            word += script[index:end]
            index = end
            continue
        if script.startswith("<<", index):
            # Heredocs and here-strings feed text to a program: inline code nobody scans.
            raise GrammarError("heredoc or here-string (put the text in a checked-in file)")
        if char in "<>":
            if in_word and word.isdigit():
                word, in_word = "", False  # a file descriptor: 2>/dev/null
            end_word()
            start = index
            index += 1
            while index < len(script) and script[index] in "<>&|=":
                index += 1
            operator_text = script[start:index]
            while index < len(script) and script[index] in " \t":
                index += 1
            if operator_text == "<" and script.startswith("<(", index):
                continue  # `< <(...)`: the process substitution becomes a word below
            # The redirection target is not a command word.
            target = re.match(r"[^\s;&|()]*", script[index:])
            assert target is not None
            index += target.end()
            text = target[0].replace('"', "").replace("'", "")
            if re.fullmatch(r"\d*-?", text) and "&" in operator_text:
                continue  # a descriptor duplication: 2>&1
            (writes if ">" in operator_text else reads).append(text)
            continue
        operator = next((op for op in _OPERATORS if script.startswith(op, index)), None)
        if operator is not None:
            if operator == "&" and script.startswith("&>", index):
                # `&> file` redirects both streams; it does not background the command.
                index += 1
                while index < len(script) and script[index] in ">":
                    index += 1
                while index < len(script) and script[index] in " \t":
                    index += 1
                target = re.match(r"[^\s;&|()]*", script[index:])
                assert target is not None
                index += target.end()
                writes.append(target[0].replace('"', "").replace("'", ""))
                continue
            if operator == ")" and case_depth and words[:1] != ["case"]:
                # A case pattern: `*-rc.*)` is not a command.
                words, word, in_word = [], "", False
                index += 1
                continue
            end_word()
            if words[:1] == ["case"]:
                case_depth += 1
                words = []
            elif words[:1] == ["esac"]:
                case_depth -= 1
            end_command()
            index += len(operator)
            continue
        if char in " \t":
            end_word()
            index += 1
            continue
        word += char
        in_word = True
        index += 1
    end_command()
    return commands


def _simple(words: list[str], writes: list[str], reads: list[str]) -> Command | None:
    while words and words[0] in _RESERVED_PREFIX:
        words = words[1:]
    if words and words[0] in _SKIPPED:
        return None
    if not words and not writes and not reads:
        return None
    # `for NAME in WORDS` assigns NAME: it stays a command the templates must match.
    assignments: list[str] = []
    while words and words[0] != "for" and _ASSIGNMENT.match(words[0]):
        assignments.append(words[0])
        words = words[1:]
    return Command(list(words), tuple(assignments), tuple(writes), tuple(reads))


# --- the template table ----------------------------------------------------------

#: Named slots a template token may contain, written ``@NAME@`` (``@NAME*@`` as the
#: last token takes every remaining word). Each is as tight as the real uses allow.
SLOTS = {
    # The five throwaway venvs the workflows create, and nothing else under /tmp.
    "VENV": r"/tmp/(?:smoke|sdist|audit|sbom|wheel)-venv",
    # echo's text: it cannot run anything, and an option (-e, -n) cannot start a word.
    "TEXT": r"(?!-)[^`]*",
    # A package.json script name; the script itself is then held to this table.
    "NPM_SCRIPT": r"[a-z][a-z:]*",
}
_SLOT = re.compile(r"@([A-Z_]+)(\*?)@")


@dataclass(frozen=True)
class Template:
    tokens: tuple[str, ...]
    why: str
    scopes: frozenset[str]
    #: Redirection targets this command may write, besides /dev/null.
    writes: frozenset[str] = frozenset()
    #: Facts that must already hold in this job (``@SLOT@`` is filled from the match).
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    #: "npm-run" resolves the named package.json script; "npm-ci" checks install hooks.
    action: str = ""


def _t(
    spec: str | tuple[str, ...],
    why: str,
    *,
    scope: str = "workflow",
    writes: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    action: str = "",
) -> Template:
    tokens = _split_spec(spec) if isinstance(spec, str) else spec
    return Template(
        tokens, why, frozenset(scope.split()), frozenset(writes), requires, provides, action
    )


def _split_spec(spec: str) -> tuple[str, ...]:
    """Split a template written as one string at spaces outside ``$(...)``."""
    tokens: list[str] = []
    current = ""
    depth = 0
    for char in spec:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == " " and depth == 0:
            tokens.append(current)
            current = ""
        else:
            current += char
    return (*tokens, current)


_LOCKED_RUN = "a tool from uv.lock via `uv run --locked`; it resolves nothing"
_LOCKED_SCRIPT = "a checked-in script under `uv run --locked`"
_SCRIPT = "a checked-in script on the runner's Python; it installs nothing"
_VENV_RUN = "a checked-in script or the installed app, inside a venv built from the lock"
_SHELL = "shell control flow or a test; it runs nothing and writes nothing"
_ASSIGN = "binds a variable later templates name, to this exact value only"
_ECHO = "prints text; a redirection is checked separately"
_LOCAL = "talks to the server this job started on 127.0.0.1 only"
_READ = "reads or lists local files only"
_RT = "$RUNNER_TEMP/locked"
_REQ = f"{_RT}/requirements-lock.txt"
_BACKEND = f"{_RT}/build-backend-lock.txt"
_EXPORT = ("uv", "export", "--locked")
_RUNTIME = ("--no-default-groups", "--no-emit-project", "--format", "requirements.txt")
_BACKEND_GROUP = (
    "--only-group",
    "build-backend",
    "--no-emit-project",
    "--format",
    "requirements.txt",
)
_SDIST_EXCLUDES = (
    r"\.db$|\.sqlite3$|\.venv/|node_modules/|\.claude/|githooks/|reflections/|exports/|"
    r"playwright-report/|test-results/"
)
_LICENSES = (
    "MIT;MIT License;MIT-0;BSD-2-Clause;BSD-3-Clause;Apache-2.0 OR BSD-2-Clause;"
    "Apache-2.0 OR BSD-3-Clause;MPL-2.0;Mozilla Public License 2.0 (MPL 2.0);PSF-2.0"
)
_RC_NOTES = (
    'NOTES_ARGS=(--notes "Immutable release candidate for hands-on validation. This '
    "prerelease is not approved for production or as the final ${GITHUB_REF_NAME%-rc.*} "
    "release. Download the wheel, requirements-lock.txt and SHA256SUMS from Assets and "
    'follow docs/nvda-validation-runbook.md.")'
)
_CI_GPO = (
    '{"name":"Smoke Test","domain":"test.local","actor":"ci",'
    '"reason":"installed-package smoke test"}'
)
_SMOKE_GPO = (
    '{"name":"Smoke Test GPO","domain":"test.local","actor":"smoke",'
    '"reason":"installed-package smoke test"}'
)
_SMOKE_SETTING = (
    '{"setting":{"side":"computer","hive":"HKLM","key":"Software\\\\Test",'
    '"value_name":"Setting1","registry_type":"REG_DWORD","value":"1"},'
    '"actor":"smoke","reason":"add test setting","expected_revision":1}'
)
_JSON = ("-H", "Content-Type: application/json", "-d")
_SMOKE_PY = '"$TMPDIR/venv/bin/python"'

TEMPLATES: tuple[Template, ...] = (
    # Python environments: only uv.lock, through uv or its hash-pinned export.
    _t("uv lock --check", "checks uv.lock against pyproject.toml; resolves nothing"),
    _t("uv sync --locked --extra dev", "installs exactly uv.lock with the dev extra"),
    _t(
        (*_EXPORT, *_RUNTIME, "--output-file", "requirements-lock.txt"),
        "exports uv.lock's runtime closure, hash-pinned",
        provides=("lock:requirements-lock.txt",),
    ),
    _t(
        (*_EXPORT, *_RUNTIME, "--output-file", _REQ),
        "exports uv.lock's runtime closure, hash-pinned",
        provides=(f"lock:{_REQ}",),
    ),
    _t(
        (*_EXPORT, *_BACKEND_GROUP, "--output-file", _BACKEND),
        "exports uv.lock's build-backend group, hash-pinned",
        provides=(f"lock:{_BACKEND}",),
    ),
    _t(f"mkdir -p {_RT}", "makes the directory the exports go to, outside the checkout"),
    _t(f"LOCKED={_RT}", _ASSIGN, provides=("var:LOCKED",)),
    _t(
        "python -m venv @VENV@", "an empty venv from the runner's Python", provides=("venv:@VENV@",)
    ),
    _t(
        "@VENV@/bin/pip install --require-hashes -r $LOCKED/requirements-lock.txt",
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", "var:LOCKED", f"lock:{_REQ}"),
    ),
    _t(
        "@VENV@/bin/pip install --require-hashes -r $LOCKED/requirements-lock.txt"
        " -r $LOCKED/build-backend-lock.txt",
        "installs the locks exported in this job, hash-checked",
        requires=("venv:@VENV@", "var:LOCKED", f"lock:{_REQ}", f"lock:{_BACKEND}"),
    ),
    _t(
        f"@VENV@/bin/pip install --require-hashes -r {_REQ}",
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", f"lock:{_REQ}"),
    ),
    _t(
        "@VENV@/bin/pip install --require-hashes -r requirements-lock.txt",
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", "lock:requirements-lock.txt"),
    ),
    _t(
        f"@VENV@/bin/pip install --require-hashes -r requirements-lock.txt -r {_BACKEND}",
        "installs the locks exported in this job, hash-checked",
        requires=("venv:@VENV@", "lock:requirements-lock.txt", f"lock:{_BACKEND}"),
    ),
    _t(
        "@VENV@/bin/pip install --no-deps --no-index dist/gpo_studio-*.whl",
        "installs this job's built wheel alone, no index",
        requires=("venv:@VENV@", "dist"),
    ),
    _t(
        "@VENV@/bin/pip install --no-deps --no-index $WHEEL",
        "installs this job's built wheel alone, no index",
        requires=("venv:@VENV@", "dist", "var:WHEEL"),
    ),
    _t(
        "@VENV@/bin/pip install --no-deps --no-index --no-build-isolation dist/gpo_studio-*.tar.gz",
        "builds this job's sdist with the locked backend already installed, no index",
        requires=("venv:@VENV@", "dist"),
    ),
    _t(
        "@VENV@/bin/pip check",
        "verifies the installed set; installs nothing",
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/pip uninstall --yes pip",
        "keeps pip itself out of the SBOM'd environment",
        requires=("venv:@VENV@",),
    ),
    # Builds: the locked `release` group's build, never isolated.
    _t(
        "uv run --locked --only-group release python -m build --no-isolation --outdir dist",
        "builds with the locked backend, no isolation",
        provides=("dist",),
    ),
    _t(
        "uv run --locked --only-group release python -m build --no-isolation --outdir dist-repro",
        "rebuilds for the reproducibility comparison",
        provides=("dist-repro",),
    ),
    _t(
        "uv run --locked --only-group release python -m build --no-isolation --wheel",
        "builds the wheel with the locked backend, no isolation",
        provides=("dist",),
    ),
    # Locked tools.
    _t("uv run --locked ruff check .", _LOCKED_RUN),
    _t("uv run --locked mypy src", _LOCKED_RUN),
    _t(
        "uv run --locked pytest -q --cov=src/gpo_studio --cov-branch"
        " --cov-report=json:coverage.json",
        _LOCKED_RUN,
    ),
    _t("uv run --locked pytest tests/test_identifier_gate.py -q", _LOCKED_RUN),
    _t("uv run --locked pytest tests/test_properties.py -q", _LOCKED_RUN),
    _t(
        "uv run --locked --only-group release pip-audit --strict --require-hashes --disable-pip"
        f" --requirement {_RT}/requirements-all-platforms.txt",
        "audits the exported lock without installing it (--disable-pip)",
        requires=(f"lock:{_REQ}",),
    ),
    _t(
        (
            "uv",
            "run",
            "--locked",
            "--only-group",
            "release",
            "pip-licenses",
            "--python",
            "/tmp/audit-venv/bin/python",
            "--allow-only",
            _LICENSES,
        ),
        "reads the licences of the venv built from the lock",
        requires=("venv:/tmp/audit-venv",),
    ),
    _t(
        "uv run --locked --only-group release cyclonedx-py environment /tmp/sbom-venv/bin/python"
        " --pyproject pyproject.toml --output-reproducible --output-format JSON"
        " --output-file sbom.cdx.json",
        "reads the venv built from the lock into an SBOM",
        requires=("venv:/tmp/sbom-venv",),
    ),
    _t("uv run --locked python scripts/check_coverage.py coverage.json", _LOCKED_SCRIPT),
    _t(
        "uv run --locked python scripts/check_locked_build_backend.py"
        " --tmp-root $RUNNER_TEMP/backend-check",
        _LOCKED_SCRIPT,
    ),
    _t("uv run --locked python scripts/check_safety.py", _LOCKED_SCRIPT),
    _t(
        "uv run --locked python scripts/mutation_check_roundtrip.py"
        " --tmp-root $RUNNER_TEMP/mutants",
        _LOCKED_SCRIPT,
    ),
    _t("uv run --locked python scripts/rehearse_upgrade_rollback.py", _LOCKED_SCRIPT),
    # Checked-in scripts on the runner's Python (setup-python), stdlib only.
    _t(
        f"python scripts/requirements_all_platforms.py {_REQ} {_RT}/requirements-all-platforms.txt",
        _SCRIPT,
    ),
    _t(
        "python scripts/finalize_sbom.py sbom.cdx.json --repository $GITHUB_REPOSITORY"
        " --source-commit $GITHUB_SHA",
        _SCRIPT,
    ),
    _t("python scripts/check_sbom_shape.py sbom.cdx.json", _SCRIPT),
    _t("python scripts/record_release_identity.py", _SCRIPT),
    _t(
        "python scripts/check_release_manifest.py --tag ${GITHUB_REF_NAME}"
        " --github-output ${GITHUB_OUTPUT}",
        _SCRIPT,
    ),
    _t(
        "python scripts/check_release_manifest.py --tag ${GITHUB_REF_NAME}"
        " --github-output ${GITHUB_OUTPUT} --remote-tag-sha ${GITHUB_SHA}",
        _SCRIPT,
    ),
    _t(
        "python scripts/check_release_manifest.py --tag ${GITHUB_REF_NAME}"
        " --remote-tag-sha ${GITHUB_SHA}",
        _SCRIPT,
    ),
    _t(
        "python scripts/check_release_manifest.py --tag ${GITHUB_REF_NAME}"
        " --wheel ${WHEELS[0]} --sdist ${SDISTS[0]}",
        _SCRIPT,
    ),
    _t("python3 scripts/check_committed_identifiers.py --strict", _SCRIPT),
    _t("python3 tests/test_identifier_gate_visibility.py", _SCRIPT),
    # Inside the venvs built above.
    _t(
        "@VENV@/bin/python scripts/print_installed_version.py", _VENV_RUN, requires=("venv:@VENV@",)
    ),
    _t(
        "@VENV@/bin/python scripts/print_installed_version.py --metadata",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/python scripts/check_health_version.py /tmp/health.json",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/python scripts/rehearse_upgrade_rollback.py",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t("@VENV@/bin/gpo-studio --help", _VENV_RUN, requires=("venv:@VENV@",)),
    _t(
        "@VENV@/bin/gpo-studio run --database /tmp/smoke.db --port 8799",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t("VENV_VERSION=$(/tmp/smoke-venv/bin/python scripts/print_installed_version.py)", _ASSIGN),
    _t(
        "META_VERSION=$(/tmp/smoke-venv/bin/python scripts/print_installed_version.py --metadata)",
        _ASSIGN,
    ),
    _t("SDIST_VERSION=$(/tmp/sdist-venv/bin/python scripts/print_installed_version.py)", _ASSIGN),
    # The built distributions.
    _t("for artifact in dist/*", _SHELL, requires=("dist",)),
    _t("basename $artifact", _READ),
    _t('cmp $artifact dist-repro/$(basename "$artifact")', _READ, requires=("dist-repro",)),
    _t(
        "WHEEL=$(find dist -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)",
        "binds WHEEL to the wheel built in this job",
        requires=("dist",),
        provides=("var:WHEEL",),
    ),
    _t("find dist -maxdepth 1 -name gpo_studio-*.whl -print -quit", _READ),
    _t("find dist -maxdepth 1 -name gpo_studio-*.whl", _READ),
    _t("find dist -maxdepth 1 -name gpo_studio-*.tar.gz", _READ),
    _t(("mapfile", "-t", "WHEELS", "<(find dist -maxdepth 1 -name 'gpo_studio-*.whl')"), _READ),
    _t(("mapfile", "-t", "SDISTS", "<(find dist -maxdepth 1 -name 'gpo_studio-*.tar.gz')"), _READ),
    _t("test ${#WHEELS[@]} -eq 1", _SHELL),
    _t("test ${#SDISTS[@]} -eq 1", _SHELL),
    _t("SDIST=$(ls dist/gpo_studio-*.tar.gz)", _ASSIGN),
    _t("ls dist/gpo_studio-*.tar.gz", _READ),
    _t("tar tzf $SDIST", _READ),
    _t(("grep", "-qE", _SDIST_EXCLUDES), _READ),
    _t(("grep", "-E", _SDIST_EXCLUDES), _READ),
    _t(
        "for required in README.md LICENSE pyproject.toml AGENTS.md SECURITY.md CHANGELOG.md"
        " CONTRIBUTING.md",
        _SHELL,
    ),
    _t("grep -q /$required$", _READ),
    # The smoke script, handed only what this job built and exported.
    _t(
        "PYTHON=python GPO_STUDIO_WHEEL=$WHEEL GPO_STUDIO_REQUIREMENTS=requirements-lock.txt"
        " bash scripts/installed_package_smoke.sh",
        "the scanned smoke script, on this job's wheel and exported lock",
        requires=("dist", "var:WHEEL", "lock:requirements-lock.txt"),
    ),
    # The CI server smoke test.
    _t("SERVER_PID=$!", _ASSIGN, scope="workflow smoke"),
    _t(("trap", 'kill "$SERVER_PID" 2>/dev/null || true', "EXIT"), "stops the server on exit"),
    _t("trap - EXIT", _SHELL),
    _t("for attempt in $(seq 1 20)", _SHELL),
    _t("seq 1 20", _SHELL),
    _t("curl -sf http://127.0.0.1:8799/api/health", _LOCAL, writes=("/tmp/health.json",)),
    _t("curl -sf http://127.0.0.1:8799/api/gpos", _LOCAL),
    _t("curl -sf http://127.0.0.1:8799/", _LOCAL),
    _t(("curl", "-sf", "-X", "POST", "http://127.0.0.1:8799/api/gpos", *_JSON, _CI_GPO), _LOCAL),
    _t("kill $SERVER_PID", _SHELL),
    _t("wait $SERVER_PID", _SHELL),
    _t("sleep 0.25", _SHELL),
    _t("break", _SHELL),
    _t("true", _SHELL),
    # SBOM, audit and release bookkeeping.
    _t("[ ! -s sbom.cdx.json ]", _SHELL),
    _t("stat -c%s sbom.cdx.json", _READ),
    _t(
        "sha256sum dist/* requirements-lock.txt sbom.cdx.json release-evidence.md"
        " release-evidence-report.json",
        "checksums the release files into SHA256SUMS",
        writes=("SHA256SUMS",),
    ),
    _t("cp ${EVIDENCE_MANIFEST} release-evidence.md", "copies the gate-approved manifest"),
    _t("cp ${EVIDENCE_REPORT} release-evidence-report.json", "copies the gate-approved report"),
    _t("grep -qxF manifest=${EXPECTED_MANIFEST} ${GITHUB_OUTPUT}", _READ),
    _t("grep -qxF report=${EXPECTED_REPORT} ${GITHUB_OUTPUT}", _READ),
    _t("git fetch --no-tags origin main", "fetches main from the checkout's own remote"),
    _t("git merge-base --is-ancestor ${GITHUB_SHA} origin/main", _READ),
    _t("FLAGS=()", _ASSIGN),
    _t("FLAGS+=(--prerelease)", _ASSIGN),
    _t("NOTES_ARGS=(--generate-notes)", _ASSIGN),
    _t((_RC_NOTES,), _ASSIGN),
    _t(
        (
            "gh",
            "release",
            "create",
            "$GITHUB_REF_NAME",
            "dist/*",
            "requirements-lock.txt",
            "sbom.cdx.json",
            "SHA256SUMS",
            "release-evidence.md",
            "release-evidence-report.json",
            "--verify-tag",
            "${NOTES_ARGS[@]}",
            "--title",
            "GPO Studio ${GITHUB_REF_NAME#v}",
            "${FLAGS[@]}",
        ),
        "publishes the attested files",
        requires=("dist", "lock:requirements-lock.txt"),
    ),
    # Shared shell.
    _t("set -euo pipefail", _SHELL, scope="workflow smoke"),
    _t("exit 1", _SHELL, scope="workflow smoke"),
    _t("echo @TEXT*@", _ECHO, scope="workflow smoke"),
    _t("[ $VENV_VERSION != $META_VERSION ]", _SHELL, scope="workflow smoke"),
    _t("test $VENV_VERSION = $SDIST_VERSION", _SHELL),
    # npm: only package-lock.json, and only scripts read from package.json.
    _t("npm ci", "installs exactly package-lock.json", provides=("npm-ci",), action="npm-ci"),
    _t("npm audit --audit-level=high", "reports advisories; installs nothing"),
    _t(
        "npx playwright install --with-deps chromium firefox",
        "the browsers the locked @playwright/test names, after npm ci in this job",
        requires=("npm-ci",),
    ),
    _t(
        "npm run @NPM_SCRIPT@",
        "a package.json script, itself held to this table",
        requires=("npm-ci",),
        action="npm-run",
    ),
    _t(
        "npm run @NPM_SCRIPT@",
        "a package.json script, itself held to this table",
        scope="npm",
        action="npm-run",
    ),
    _t(
        "prettier --check eslint.config.mjs playwright.config.mjs vitest.config.mjs"
        " tests/browser tests/frontend",
        "a locked node tool; reads only",
        scope="npm",
    ),
    _t(
        "eslint src/gpo_studio/static/js tests/browser tests/frontend *.config.mjs",
        "a locked node tool; reads only",
        scope="npm",
    ),
    _t("vitest run", "the locked unit-test runner", scope="npm"),
    _t("playwright test", "the locked browser-test runner", scope="npm"),
    # scripts/installed_package_smoke.sh
    _t("[ ! -f scripts/print_installed_version.py ]", _SHELL, scope="smoke"),
    _t("TMPDIR=$(mktemp -d)", _ASSIGN, scope="smoke"),
    _t("mktemp -d", "makes the script's scratch directory", scope="smoke"),
    _t("SERVER_PID=", _ASSIGN, scope="smoke"),
    _t(
        ("trap", 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMPDIR"', "EXIT"),
        "stops the server and removes the scratch directory on exit",
        scope="smoke",
    ),
    _t(
        "PYTHON=${PYTHON:-python3.13}", "the caller's interpreter (documented trust)", scope="smoke"
    ),
    _t("PORT=8799", _ASSIGN, scope="smoke"),
    _t("DB=$TMPDIR/smoke.db", _ASSIGN, scope="smoke"),
    _t("[ -n ${GPO_STUDIO_WHEEL:-} ]", _SHELL, scope="smoke"),
    _t(
        'WHEEL=$(realpath "$GPO_STUDIO_WHEEL")',
        "the caller's wheel (documented trust)",
        scope="smoke",
    ),
    _t("realpath $GPO_STUDIO_WHEEL", _READ, scope="smoke"),
    _t("command -v uv", "looks uv up; runs nothing", scope="smoke"),
    _t(
        "UV_PROJECT_ENVIRONMENT=$TMPDIR/build-env uv sync --locked --only-group build-backend",
        "installs uv.lock's build-backend group into a throwaway environment",
        scope="smoke",
        provides=("build-env",),
    ),
    _t(
        "UV_PROJECT_ENVIRONMENT=$TMPDIR/build-env uv build --wheel --out-dir $TMPDIR/dist",
        "builds in that locked environment, without isolation ([tool.uv])",
        scope="smoke",
        requires=("build-env",),
    ),
    _t(
        "WHEEL=$(find \"$TMPDIR/dist\" -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)",
        "binds WHEEL to the wheel just built",
        scope="smoke",
    ),
    _t("find $TMPDIR/dist -maxdepth 1 -name gpo_studio-*.whl -print -quit", _READ, scope="smoke"),
    _t("[ -n ${GPO_STUDIO_REQUIREMENTS:-} ]", _SHELL, scope="smoke"),
    _t(
        'REQUIREMENTS=$(realpath "$GPO_STUDIO_REQUIREMENTS")',
        "the caller's exported lock (documented trust)",
        scope="smoke",
    ),
    _t("realpath $GPO_STUDIO_REQUIREMENTS", _READ, scope="smoke"),
    _t("REQUIREMENTS=$TMPDIR/requirements-lock.txt", _ASSIGN, scope="smoke"),
    _t(
        (*_EXPORT, *_RUNTIME, "--output-file", "$REQUIREMENTS"),
        "exports uv.lock's runtime closure, hash-pinned",
        scope="smoke",
    ),
    _t(
        "$PYTHON -m venv $TMPDIR/venv --clear",
        "an empty venv from the caller's interpreter",
        scope="smoke",
        provides=("venv",),
    ),
    _t(
        "$TMPDIR/venv/bin/pip install --require-hashes -r $REQUIREMENTS",
        "installs the exported (or handed-off) lock, hash-checked",
        scope="smoke",
        requires=("venv",),
    ),
    _t(
        "$TMPDIR/venv/bin/pip install --no-deps --no-index $WHEEL",
        "installs the wheel alone, no index",
        scope="smoke",
        requires=("venv",),
    ),
    _t(
        "$TMPDIR/venv/bin/pip check",
        "verifies the installed set",
        scope="smoke",
        requires=("venv",),
    ),
    _t("$TMPDIR/venv/bin/python scripts/print_installed_version.py", _VENV_RUN, scope="smoke"),
    _t(
        "$TMPDIR/venv/bin/python scripts/print_installed_version.py --metadata",
        _VENV_RUN,
        scope="smoke",
    ),
    _t("$TMPDIR/venv/bin/python scripts/read_json_field.py version", _VENV_RUN, scope="smoke"),
    _t("$TMPDIR/venv/bin/python scripts/read_json_field.py gpo.guid", _VENV_RUN, scope="smoke"),
    _t(f"VENV_VERSION=$({_SMOKE_PY} scripts/print_installed_version.py)", _ASSIGN, scope="smoke"),
    _t(
        f"META_VERSION=$({_SMOKE_PY} scripts/print_installed_version.py --metadata)",
        _ASSIGN,
        scope="smoke",
    ),
    _t("$TMPDIR/venv/bin/gpo-studio --help", _VENV_RUN, scope="smoke"),
    _t("head -1", _READ, scope="smoke"),
    _t("$TMPDIR/venv/bin/gpo-studio run --database $DB --port $PORT", _VENV_RUN, scope="smoke"),
    _t("$TMPDIR/venv/bin/gpo-studio workspace check --database $DB", _VENV_RUN, scope="smoke"),
    _t("sleep 2", _SHELL, scope="smoke"),
    _t("curl -sf http://127.0.0.1:$PORT/api/health", _LOCAL, scope="smoke"),
    _t('HEALTH=$(curl -sf "http://127.0.0.1:$PORT/api/health")', _ASSIGN, scope="smoke"),
    _t(
        f'HEALTH_VERSION=$(echo "$HEALTH" | {_SMOKE_PY} scripts/read_json_field.py version)',
        _ASSIGN,
        scope="smoke",
    ),
    _t("[ $HEALTH_VERSION != $VENV_VERSION ]", _SHELL, scope="smoke"),
    _t(
        ("curl", "-sf", "-X", "POST", "http://127.0.0.1:$PORT/api/gpos", *_JSON, _SMOKE_GPO),
        _LOCAL,
        scope="smoke",
    ),
    _t(
        (
            'RESPONSE=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" -H "Content-Type:'
            f" application/json\" -d '{_SMOKE_GPO}')",
        ),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        f'GPO_GUID=$(echo "$RESPONSE" | {_SMOKE_PY} scripts/read_json_field.py gpo.guid)',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            "curl",
            "-sf",
            "-X",
            "POST",
            "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/settings",
            *_JSON,
            _SMOKE_SETTING,
        ),
        _LOCAL,
        scope="smoke",
    ),
    _t(
        "curl -sf -o $TMPDIR/export.zip http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/export.zip",
        _LOCAL,
        scope="smoke",
    ),
    _t('ZIP_SIZE=$(stat -c%s "$TMPDIR/export.zip")', _ASSIGN, scope="smoke"),
    _t("stat -c%s $TMPDIR/export.zip", _READ, scope="smoke"),
    _t("[ $ZIP_SIZE -lt 500 ]", _SHELL, scope="smoke"),
    _t(
        'PLAN=$(curl -sf "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/plan.ps1")',
        _ASSIGN,
        scope="smoke",
    ),
    _t("curl -sf http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/plan.ps1", _LOCAL, scope="smoke"),
    _t("grep -q Set-GPRegistryValue", _READ, scope="smoke"),
    _t(
        'UI_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/")',
        _ASSIGN,
        scope="smoke",
    ),
    _t("curl -sf -o /dev/null -w %{http_code} http://127.0.0.1:$PORT/", _LOCAL, scope="smoke"),
    _t("[ $UI_STATUS != 200 ]", _SHELL, scope="smoke"),
    _t("kill $SERVER_PID", _SHELL, scope="smoke"),
)

#: ``env:`` entries (workflow, job or step) a workflow may set: name -> exact values.
ENV: dict[str, set[str]] = {
    "EVIDENCE_MANIFEST": {"${{ steps.manifest.outputs.manifest }}"},
    "EVIDENCE_REPORT": {"${{ steps.manifest.outputs.report }}"},
    "EXPECTED_MANIFEST": {"${{ needs.release-identity.outputs.manifest }}"},
    "EXPECTED_REPORT": {"${{ needs.release-identity.outputs.report }}"},
    "GH_TOKEN": {"${{ github.token }}"},
    "GITHUB_TOKEN": {"${{ secrets.GITHUB_TOKEN }}"},
    "GPO_STUDIO_FORBIDDEN_IDENTIFIERS": {"${{ secrets.GPO_STUDIO_FORBIDDEN_IDENTIFIERS }}"},
    "GPO_STUDIO_TEST_PYTHON": {"${{ github.workspace }}/.venv/bin/python"},
    "SOURCE_DATE_EPOCH": {"315532800"},
}

#: Every PowerShell step body, and every step of a Windows job (whose default shell
#: is PowerShell), must be one of these, compared case-insensitively with whitespace
#: collapsed: PowerShell resolves commands without regard to case.
PWSH_BODIES: dict[str, str] = {
    # The only install: PSScriptAnalyzer, exact version, PSGallery, current user.
    "Set-PSRepository PSGallery -InstallationPolicy Trusted\n"
    "Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser -Force\n": (
        "installs PSScriptAnalyzer 1.25.0 exactly"
    ),
    "$ErrorActionPreference = 'Stop'\n"
    "$failed = $false\n"
    "# Repo-wide rather than scripts/-only, so a .ps1 added elsewhere\n"
    "# cannot escape the gate -- but vendored trees are not ours to fix,\n"
    "# and including them would make a local run disagree with CI, whose\n"
    "# checkout has no node_modules.\n"
    "Get-ChildItem -Recurse -Filter *.ps1 |\n"
    "  Where-Object { $_.FullName -notmatch '[\\\\/]node_modules[\\\\/]' } |\n"
    "  ForEach-Object {\n"
    "  $errors = $null\n"
    "  [System.Management.Automation.Language.Parser]::ParseFile(\n"
    "    $_.FullName, [ref]$null, [ref]$errors) | Out-Null\n"
    "  if ($errors) {\n"
    "    $failed = $true\n"
    '    Write-Output "::error file=$($_.Name)::$($errors.Count) parse error(s)"\n'
    "    $errors | ForEach-Object "
    '{ Write-Output "  line $($_.Extent.StartLineNumber): $($_.Message)" }\n'
    "  }\n"
    "}\n"
    "if ($failed) { exit 1 }\n"
    'Write-Output "All scripts parse."\n': "parses every .ps1; installs nothing",
    "$ErrorActionPreference = 'Stop'\n"
    "$targets = @(\n"
    "  Get-ChildItem -Recurse -Include *.ps1, *.psm1, *.psd1 |\n"
    "    Where-Object { $_.FullName -notmatch '[\\\\/]node_modules[\\\\/]' } |\n"
    "    ForEach-Object { $_.FullName }\n"
    ")\n"
    "$findings = @($targets | ForEach-Object {\n"
    "  Invoke-ScriptAnalyzer -Path $_ -Settings ./PSScriptAnalyzerSettings.psd1\n"
    "})\n"
    "if ($findings) {\n"
    "  $findings | ForEach-Object {\n"
    '    Write-Output "::error file=$($_.ScriptName),line=$($_.Line)::'
    '[$($_.RuleName)] $($_.Message)"\n'
    "  }\n"
    '  Write-Output "$($findings.Count) finding(s)."\n'
    "  exit 1\n"
    "}\n"
    'Write-Output "PSScriptAnalyzer: no findings."\n': "runs the installed analyzer",
    # The Windows test job: the same locked commands as the Linux job.
    "uv sync --locked --extra dev": "installs exactly uv.lock with the dev extra",
    "uv run --locked ruff check .": _LOCKED_RUN,
    "uv run --locked mypy src": _LOCKED_RUN,
    "uv run --locked pytest -q --cov=src/gpo_studio --cov-branch"
    " --cov-report=json:coverage.json": _LOCKED_RUN,
    "uv run --locked python scripts/check_coverage.py coverage.json": _LOCKED_SCRIPT,
}


def _normalise_pwsh(body: str) -> str:
    return " ".join(body.split()).casefold()


_PWSH = {_normalise_pwsh(body) for body in PWSH_BODIES}

#: Lifecycle scripts ``npm ci`` would run from the root package.json.
_NPM_INSTALL_HOOKS = {
    "preinstall",
    "install",
    "postinstall",
    "prepublish",
    "preprepare",
    "prepare",
    "postprepare",
}


def _compile(token: str) -> tuple[re.Pattern[str], bool]:
    """A template token as a pattern, and whether it is a trailing variadic slot."""
    variadic = _SLOT.fullmatch(token)
    if variadic and variadic[2]:
        return re.compile(SLOTS[variadic[1]]), True
    parts: list[str] = []
    last = 0
    for slot in _SLOT.finditer(token):
        parts.append(re.escape(token[last : slot.start()]))
        parts.append(f"(?P<{slot[1]}>{SLOTS[slot[1]]})")
        last = slot.end()
    parts.append(re.escape(token[last:]))
    return re.compile("".join(parts)), False


_COMPILED = {template: [_compile(token) for token in template.tokens] for template in TEMPLATES}


def _match(template: Template, tokens: tuple[str, ...]) -> dict[str, str] | None:
    captures: dict[str, str] = {}
    patterns = _COMPILED[template]
    for index, (pattern, variadic) in enumerate(patterns):
        if variadic:
            rest = tokens[index:]
            return captures if all(pattern.fullmatch(word) for word in rest) else None
        if index >= len(tokens):
            return None
        found = pattern.fullmatch(tokens[index])
        if found is None:
            return None
        for name, value in found.groupdict().items():
            if captures.setdefault(name, value) != value:
                return None
    return captures if len(tokens) == len(patterns) else None


def _fill(fact: str, captures: dict[str, str]) -> str:
    return _SLOT.sub(lambda slot: captures[slot[1]], fact)


@dataclass
class Context:
    """What the checker knows while it walks one job, script or package.json script."""

    root: Path = REPO_ROOT
    package_json: Path | None = None
    scope: str = "workflow"
    #: Facts established earlier in this job (a lock exported, a venv built...).
    facts: set[str] = field(default_factory=set)
    #: ``var:`` facts: shell variables, which do not outlive their step.
    step_facts: set[str] = field(default_factory=set)
    npm_stack: tuple[str, ...] = ()

    def holds(self, fact: str) -> bool:
        return fact in self.facts or fact in self.step_facts

    def establish(self, fact: str) -> None:
        (self.step_facts if fact.startswith("var:") else self.facts).add(fact)

    def npm_scripts(self) -> dict[str, Any]:
        path = self.package_json or self.root / "package.json"
        scripts = json.loads(path.read_text(encoding="utf-8")).get("scripts") or {}
        assert isinstance(scripts, dict)
        return scripts


def violations(commands: list[Command], *, where: str, ctx: Context | None = None) -> Iterator[str]:
    ctx = ctx if ctx is not None else Context()
    for command in commands:
        yield from _check(command, ctx, where)


def _check(command: Command, ctx: Context, where: str) -> Iterator[str]:
    tokens = command.tokens
    shown = " ".join(tokens)
    matches = [
        (template, captures)
        for template in TEMPLATES
        if ctx.scope in template.scopes and (captures := _match(template, tokens)) is not None
    ]
    if not matches:
        yield f"{where}: no reviewed template matches {shown!r}"
        return
    chosen = next(
        (
            (template, captures)
            for template, captures in matches
            if all(ctx.holds(_fill(fact, captures)) for fact in template.requires)
        ),
        None,
    )
    if chosen is None:
        template, captures = matches[0]
        missing = [
            _fill(fact, captures)
            for fact in template.requires
            if not ctx.holds(_fill(fact, captures))
        ]
        yield f"{where}: {shown!r} needs {missing} established earlier in this job"
        return
    template, captures = chosen
    for target in command.writes:
        if target != "/dev/null" and target not in template.writes:
            yield f"{where}: {shown!r} may not write {target!r}"
    for target in command.reads:
        yield f"{where}: {shown!r} may not read a redirected {target!r}"
    for fact in template.provides:
        ctx.establish(_fill(fact, captures))
    if template.action == "npm-ci":
        yield from _check_npm_ci(ctx, where)
    elif template.action == "npm-run":
        yield from _check_npm_script(captures["NPM_SCRIPT"], ctx, where)


def _check_npm_ci(ctx: Context, where: str) -> Iterator[str]:
    hooks = sorted(_NPM_INSTALL_HOOKS & set(ctx.npm_scripts()))
    if hooks:
        yield f"{where}: npm ci would run package.json lifecycle scripts {hooks}"
    if (ctx.root / "binding.gyp").exists():
        yield f"{where}: npm ci would run node-gyp for binding.gyp"


def _check_npm_script(name: str, ctx: Context, where: str) -> Iterator[str]:
    """Lex the package.json script and its pre/post hooks with the npm templates."""
    scripts = ctx.npm_scripts()
    if name not in scripts:
        yield f"{where}: npm script {name!r} is not in package.json"
        return
    if name in ctx.npm_stack:
        yield f"{where}: npm script {name!r} runs itself ({' -> '.join(ctx.npm_stack)})"
        return
    for hook in (f"pre{name}", name, f"post{name}"):
        if hook not in scripts:
            continue
        body = scripts[hook]
        here = f"{where} (npm {hook})"
        if not isinstance(body, str):
            yield f"{here}: not a string"
            continue
        inner = Context(
            ctx.root,
            ctx.package_json,
            "npm",
            ctx.facts,
            ctx.step_facts,
            (*ctx.npm_stack, name),
        )
        try:
            yield from violations(split_commands(body), where=here, ctx=inner)
        except GrammarError as error:
            yield f"{here}: {error}"


# --- workflows -------------------------------------------------------------------

_PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
#: Remote actions a workflow may use, and the inputs each may be given. An action
#: that installs packages (setup-python's ``pip-install``, say) is an install this
#: grammar cannot see, so a new action or input is a deliberate edit here.
ACTIONS: dict[str, dict[str, set[str] | None]] = {
    "actions/attest": {"sbom-path": None, "subject-path": None},
    "actions/attest-build-provenance": {"subject-path": None},
    "actions/cache": {"key": None, "path": {"~/.cache/ms-playwright"}},
    "actions/checkout": {"fetch-depth": None},
    "actions/setup-node": {"cache": {"npm"}, "node-version": None},
    "actions/setup-python": {"python-version": None},
    "actions/upload-artifact": {"if-no-files-found": None, "name": None, "path": None},
    "astral-sh/setup-uv": {"enable-cache": None, "python-version": None, "version": None},
    "gitleaks/gitleaks-action": {},
}


#: Runners a job may use: the default shell is bash on one and PowerShell on the other.
RUNNERS = ("ubuntu-latest", "windows-latest")


def _env_violations(env: Any, where: str) -> Iterator[str]:
    for name, value in (env or {}).items():
        if str(value) not in ENV.get(str(name), set()):
            yield f"{where}: env {name}={value!r} is not on the allowlist"


def _defaults_violations(owner: Any, where: str) -> Iterator[str]:
    run = (owner.get("defaults") or {}).get("run") or {}
    if run.get("shell") not in (None, "bash"):
        yield f"{where}: default shell {run.get('shell')!r} is not bash"
    if "working-directory" in run:
        yield f"{where}: a default working-directory moves every relative path checked here"


def _action_violations(step: Any, where: str) -> Iterator[str]:
    uses = str(step["uses"])
    if not _PINNED_ACTION.match(uses):
        yield f"{where}: action {uses!r} is not a remote action pinned by SHA"
        return
    name = uses.split("@", 1)[0]
    inputs = ACTIONS.get(name)
    if inputs is None:
        yield f"{where}: action {name!r} is not on the allowlist (it could install anything)"
        return
    for key, value in (step.get("with") or {}).items():
        if key not in inputs:
            yield f"{where}: input {key!r} of {name} is not allowed"
            continue
        allowed = inputs[key]
        if allowed is not None and str(value) not in allowed:
            yield f"{where}: {name} {key}: {value!r} is not one of {sorted(allowed)}"


def workflow_violations(
    path: Path, *, root: Path = REPO_ROOT, package_json: Path | None = None
) -> list[str]:
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[str] = []
    found.extend(_env_violations(document.get("env"), path.name))
    found.extend(_defaults_violations(document, path.name))
    for job_name, job in (document.get("jobs") or {}).items():
        if "uses" in job:
            uses = str(job["uses"])
            local = re.fullmatch(r"\./\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", uses)
            if local is None or not (root / uses).is_file():
                found.append(
                    f"{path.name}:{job_name}: reusable workflow {uses!r} is not a file "
                    "in this repository's .github/workflows"
                )
            continue
        found.extend(_env_violations(job.get("env"), f"{path.name}:{job_name}"))
        found.extend(_defaults_violations(job, f"{path.name}:{job_name}"))
        if "container" in job:
            found.append(f"{path.name}:{job_name}: a job container is an unscanned environment")
        runner = job.get("runs-on")
        if runner not in RUNNERS:
            # The runner decides the default shell; a label or expression would hide it.
            found.append(f"{path.name}:{job_name}: runs-on {runner!r} is not one of {RUNNERS}")
        windows = runner == "windows-latest"
        # Jobs are separate machines: nothing one established holds in another.
        ctx = Context(root=root, package_json=package_json)
        for number, step in enumerate(job.get("steps") or [], 1):
            where = f"{path.name}:{job_name}#{number}"
            if "uses" in step:
                found.extend(_action_violations(step, where))
            found.extend(_env_violations(step.get("env"), where))
            if "working-directory" in step:
                found.append(f"{where}: working-directory moves every relative path checked here")
            if "run" not in step:
                continue
            script = str(step["run"])
            if "${{" in script:
                found.append(f"{where}: an expression inside run: is a command nobody can read")
                continue
            shell = step.get("shell")
            if shell == "pwsh" or (shell is None and windows):
                if _normalise_pwsh(script) not in _PWSH:
                    found.append(f"{where}: no reviewed PowerShell body matches {script!r}")
                continue
            if shell not in (None, "bash"):
                found.append(f"{where}: shell {shell!r} is not bash or pwsh")
                continue
            ctx.step_facts = set()
            try:
                found.extend(violations(split_commands(script), where=where, ctx=ctx))
            except GrammarError as error:
                found.append(f"{where}: {error}")
    return found


def script_violations(path: Path, *, root: Path = REPO_ROOT) -> list[str]:
    ctx = Context(root=root, scope="smoke")
    try:
        commands = split_commands(path.read_text(encoding="utf-8"))
        return list(violations(commands, where=path.name, ctx=ctx))
    except GrammarError as error:
        return [f"{path.name}: {error}"]
