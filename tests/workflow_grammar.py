"""A deliberately small grammar for the commands CI, the release and the smoke script run.

``tests/test_locked_installs.py`` uses it to hold every dependency install and
resolution to uv.lock. Substring scanning was bypassed five ways in review
(env-var indirection, folded scalars, a local composite action, flags in a
trailing comment, ``uv pip``), so this does the opposite: it parses the
workflow YAML, splits each ``run:`` script into simple commands with a small
shell lexer, and accepts a command only if its program is on an allowlist and,
for the programs that install or resolve packages, only in an explicitly listed
shape. Anything it does not understand is a violation, not a pass.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class GrammarError(Exception):
    """The script uses a construct this grammar refuses to interpret."""


@dataclass
class Command:
    words: list[str]
    assignments: dict[str, str] = field(default_factory=dict)
    heredoc: str = ""


_OPERATORS = ("&&", "||", ";;", ";", "|", "&", "\n", "(", ")", "{", "}")
_RESERVED_PREFIX = {"if", "then", "else", "elif", "do", "while", "until", "!", "time"}
_SKIPPED = {"fi", "done", "esac"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")


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
    """Simple commands of a POSIX-ish shell script, substitutions included."""
    commands: list[Command] = []
    words: list[str] = []
    word = ""
    in_word = False
    heredocs: list[tuple[str, Command | None]] = []
    current_heredoc_owner: list[Command] = []
    case_depth = 0
    index = 0

    def end_word() -> None:
        nonlocal word, in_word
        if in_word:
            words.append(word)
        word = ""
        in_word = False

    def end_command() -> None:
        nonlocal words
        end_word()
        if words:
            command = _simple(words)
            if command is not None:
                commands.append(command)
                current_heredoc_owner[:] = [command]
        words = []

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
            end = script.index("'", index + 1)
            word += script[index + 1 : end]
            in_word = True
            index = end + 1
            continue
        if char == '"':
            index += 1
            while script[index] != '"':
                if script[index] == "\\":
                    word += script[index + 1]
                    index += 2
                    continue
                if script.startswith("$(", index):
                    end = _read_balanced(script, index + 2)
                    commands.extend(split_commands(script[index + 2 : end - 1]))
                    word += "$(...)"
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
            word += "$(...)"
            in_word = True
            index = end
            continue
        if script.startswith("${", index):
            end = script.index("}", index)
            word += script[index : end + 1]
            in_word = True
            index = end + 1
            continue
        if char == "(" and in_word and word.endswith("="):
            # An array assignment: FLAGS=(--prerelease)
            end = _read_balanced(script, index + 1)
            word += script[index:end]
            index = end
            continue
        if script.startswith("<<", index) and not script.startswith("<<<", index):
            end_word()
            match = re.match(r"<<-?\s*(['\"]?)([A-Za-z_]+)\1", script[index:])
            if match is None:
                raise GrammarError("unparsed heredoc")
            heredocs.append((match[2], None))
            index += match.end()
            continue
        if char in "<>":
            end_word()
            index += 1
            while index < len(script) and script[index] in "<>&=":
                index += 1
            while index < len(script) and script[index] in " \t":
                index += 1
            # The redirection target is not a command word.
            target = re.match(r"[^\s;&|()]*", script[index:])
            assert target is not None
            index += target.end()
            continue
        operator = next((op for op in _OPERATORS if script.startswith(op, index)), None)
        if operator is not None:
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
            if operator == "\n" and heredocs:
                for delimiter, _owner in heredocs:
                    body_end = re.search(
                        rf"^[ \t]*{re.escape(delimiter)}[ \t]*$", script[index:], re.M
                    )
                    if body_end is None:
                        raise GrammarError(f"heredoc {delimiter} never ends")
                    if current_heredoc_owner:
                        current_heredoc_owner[0].heredoc += script[index : index + body_end.start()]
                    index += body_end.end()
                heredocs = []
            continue
        if char in " \t":
            end_word()
            index += 1
            continue
        word += char
        in_word = True
        index += 1
    end_command()
    if heredocs:
        raise GrammarError("heredoc without a body")
    return commands


def _simple(words: list[str]) -> Command | None:
    while words and words[0] in _RESERVED_PREFIX:
        words = words[1:]
    if not words or words[0] in _SKIPPED or words[0] in {"for", "case", "in"}:
        return None
    assignments: dict[str, str] = {}
    while words and _ASSIGNMENT.match(words[0]):
        name, _, value = words[0].partition("=")
        assignments[name.rstrip("+")] = value
        words = words[1:]
    if not words:
        return Command([], assignments)
    return Command(words, assignments)


# --- the allowlist ---------------------------------------------------------------

#: Programs that neither install nor resolve packages and take no command to run.
PLAIN = {
    "basename",
    "cat",
    "cmp",
    "cp",
    "curl",
    "echo",
    "exit",
    "find",
    "gh",
    "git",
    "grep",
    "head",
    "kill",
    "ls",
    "mapfile",
    "mkdir",
    "mktemp",
    "printf",
    "pwd",
    "realpath",
    "rm",
    "seq",
    "set",
    "sha256sum",
    "sleep",
    "stat",
    "tar",
    "test",
    "trap",
    "true",
    "wait",
    "[",
    "cd",
    "command",
    "export",
    "shift",
    "return",
    "tail",
    "sed",
    "sort",
    "wc",
    "break",
    "continue",
}
#: Python-run tools: pytest and the lint/type checkers run code, never install.
PYTHON_TOOLS = {"pytest", "ruff", "mypy", "pip-audit", "pip-licenses", "cyclonedx-py"}
_PIP = re.compile(r"(^|/)pip3?$")
_PYTHON = re.compile(r"(^|/)python(3(\.\d+)?)?$")
_APP = re.compile(r"(^|/)gpo-studio$")
_ARTIFACT = re.compile(
    r"^(dist/gpo_studio-\*\.(whl|tar\.gz)|\$WHEEL|\$\{WHEELS\[0\]\}|\$Wheel\.FullName)$"
)
_SCANNED_SCRIPTS = {"scripts/installed_package_smoke.sh"}


def _program(word: str) -> str:
    """The program a command word names; a word that is (or ends in) a variable is refused."""
    if word == "$PYTHON":
        return "python"
    if word.startswith("$") and "/" in word:
        word = word.rsplit("/", 1)[1]
    if "$" in word or "`" in word:
        raise GrammarError(f"indirect command {word!r}")
    return word


def violations(commands: list[Command], *, where: str) -> Iterator[str]:
    previous: Command | None = None
    for command in commands:
        if command.heredoc and re.search(r"\b(pip|uv|uvx|pipx)\b", command.heredoc):
            yield f"{where}: an installer named inside a heredoc: {command.words}"
        if command.words:
            yield from _check(command, previous, where=where)
            previous = command


def _check(command: Command, previous: Command | None, *, where: str) -> Iterator[str]:
    words = command.words
    try:
        program = _program(words[0])
    except GrammarError as error:
        yield f"{where}: {error}"
        return
    args = words[1:]
    shown = " ".join(words)
    if program in {"eval", "source", ".", "exec", "sh", "env", "xargs", "sudo"}:
        yield f"{where}: refused indirection {shown!r}"
    elif program == "bash":
        if args != [path for path in args if path in _SCANNED_SCRIPTS] or len(args) != 1:
            yield f"{where}: bash may only run a scanned script, not {shown!r}"
    elif _PIP.search(program):
        yield from _check_pip(args, shown, where)
    elif _PYTHON.search(program):
        yield from _check_python(args, shown, where)
    elif program == "uv":
        yield from _check_uv(command, previous, shown, where)
    elif program == "npm":
        if args[:1] not in (["ci"], ["run"], ["audit"]):
            yield f"{where}: npm only as ci, run or audit: {shown!r}"
    elif program == "npx":
        if args[:2] != ["playwright", "install"]:
            yield f"{where}: npx only installs the Playwright browsers: {shown!r}"
    elif program in PYTHON_TOOLS or _APP.search(program) or program in PLAIN:
        urls = [arg for arg in args if "://" in arg]
        if program == "curl" and (
            not urls or not all(url.startswith("http://127.0.0.1:") for url in urls)
        ):
            yield f"{where}: curl may only reach the local server: {shown!r}"
    else:
        yield f"{where}: unrecognised program {program!r} in {shown!r}"


def _check_pip(args: list[str], shown: str, where: str) -> Iterator[str]:
    if args in (["check"], ["uninstall", "--yes", "pip"]):
        return
    if args[:1] != ["install"]:
        yield f"{where}: unrecognised pip use {shown!r}"
        return
    rest = args[1:]
    if rest[:1] == ["--require-hashes"]:
        files = rest[1:]
        if not files or len(files) % 2 or any(flag != "-r" for flag in files[::2]):
            yield f"{where}: --require-hashes must be followed only by -r FILE pairs: {shown!r}"
        return
    flags = [arg for arg in rest if arg.startswith("-")]
    targets = [arg for arg in rest if not arg.startswith("-")]
    allowed = {"--no-deps", "--no-index", "--no-build-isolation", "--upgrade"}
    if (
        {"--no-deps", "--no-index"} <= set(flags)
        and set(flags) <= allowed
        and len(targets) == 1
        and _ARTIFACT.match(targets[0])
    ):
        return
    yield f"{where}: pip install neither hash-checked nor the built artifact alone: {shown!r}"


def _check_python(args: list[str], shown: str, where: str) -> Iterator[str]:
    if args[:2] == ["-m", "pip"]:
        yield from _check_pip(args[2:], shown, where)
    elif args[:2] == ["-m", "build"]:
        if "--no-isolation" not in args:
            yield f"{where}: python -m build without --no-isolation: {shown!r}"
    elif not (
        args[:2] in (["-m", "venv"], ["-m", "gpo_studio"])
        or args[:1] in (["-"], ["-c"])
        or (args[:1] and args[0].startswith(("scripts/", "tests/")) and args[0].endswith(".py"))
    ):
        yield f"{where}: unrecognised python use {shown!r}"


def _check_uv(command: Command, previous: Command | None, shown: str, where: str) -> Iterator[str]:
    args = command.words[1:]
    sub = args[:1]
    if sub == ["lock"]:
        if args != ["lock", "--check"]:
            yield f"{where}: uv lock only as --check: {shown!r}"
    elif sub == ["sync"]:
        rest = args[1:]
        if rest[:1] != ["--locked"]:
            yield f"{where}: uv sync without --locked: {shown!r}"
        elif rest[1:] not in ([], ["--extra", "dev"], ["--only-group", "build-backend"]):
            yield f"{where}: unrecognised uv sync shape {shown!r}"
    elif sub == ["export"]:
        rest = args[1:]
        groups = rest[1:3]
        tail = rest[3:]
        if rest[:1] != ["--locked"] or groups not in (
            ["--no-default-groups", "--no-emit-project"],
            ["--only-group", "build-backend"],
        ):
            yield f"{where}: unrecognised uv export shape {shown!r}"
            return
        if groups[0] == "--only-group":
            if tail[:1] != ["--no-emit-project"]:
                yield f"{where}: unrecognised uv export shape {shown!r}"
                return
            tail = tail[1:]
        if (
            tail[:2] != ["--format", "requirements.txt"]
            or tail[2:3] != ["--output-file"]
            or len(tail) != 4
        ):
            yield f"{where}: unrecognised uv export shape {shown!r}"
    elif sub == ["run"]:
        rest = args[1:]
        if rest[:1] != ["--locked"]:
            yield f"{where}: uv run without --locked: {shown!r}"
            return
        rest = rest[1:]
        if rest[:2] == ["--only-group", "release"]:
            rest = rest[2:]
        if not rest:
            yield f"{where}: uv run with no command: {shown!r}"
            return
        yield from _check(Command(rest), None, where=where)
    elif sub == ["build"]:
        environment = command.assignments.get("UV_PROJECT_ENVIRONMENT")
        locked_sync = (
            previous is not None
            and previous.words == ["uv", "sync", "--locked", "--only-group", "build-backend"]
            and previous.assignments.get("UV_PROJECT_ENVIRONMENT") == environment
            and environment is not None
        )
        if not locked_sync or args[1:3] != ["--wheel", "--out-dir"] or len(args) != 4:
            yield (
                f"{where}: uv build only straight after a locked build-backend sync into "
                f"the same UV_PROJECT_ENVIRONMENT: {shown!r}"
            )
    else:
        yield f"{where}: unrecognised uv command {shown!r} (uv pip, uvx, uv tool, uv add ...)"


# --- workflows -------------------------------------------------------------------

_PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")


def workflow_violations(path: Path) -> list[str]:
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for job_name, job in (document.get("jobs") or {}).items():
        if "uses" in job:
            if not str(job["uses"]).startswith("./.github/workflows/"):
                found.append(f"{path.name}:{job_name}: a reusable workflow outside this repo")
            continue
        for number, step in enumerate(job.get("steps") or [], 1):
            where = f"{path.name}:{job_name}#{number}"
            if "uses" in step:
                uses = str(step["uses"])
                if not _PINNED_ACTION.match(uses):
                    found.append(f"{where}: action {uses!r} is not a remote action pinned by SHA")
            if "run" not in step:
                continue
            script = str(step["run"])
            if "${{" in script:
                found.append(f"{where}: an expression inside run: is a command nobody can read")
                continue
            if step.get("shell") == "pwsh":
                found.extend(_pwsh_violations(script, where))
                continue
            try:
                found.extend(violations(split_commands(script), where=where))
            except GrammarError as error:
                found.append(f"{where}: {error}")
    return found


def script_violations(path: Path) -> list[str]:
    try:
        return list(violations(split_commands(path.read_text(encoding="utf-8")), where=path.name))
    except GrammarError as error:
        return [f"{path.name}: {error}"]


def _pwsh_violations(script: str, where: str) -> Iterator[str]:
    """PowerShell steps may install one module, only at an exact version."""
    for line in script.splitlines():
        stripped = line.strip()
        if re.search(r"\b(pip|uv|uvx|pipx|python|Invoke-Expression|iex)\b", stripped):
            yield f"{where}: refused in a pwsh step: {stripped!r}"
        if re.search(r"\b(Install-Module|Install-Package|Install-Script)\b", stripped) and not (
            re.search(r"-RequiredVersion\s+\d+(\.\d+)+\b", stripped)
        ):
            yield f"{where}: a PowerShell install without -RequiredVersion: {stripped!r}"
