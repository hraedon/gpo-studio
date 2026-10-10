"""A deliberately small grammar for the commands CI, the release and the smoke script run.

``tests/test_locked_installs.py`` uses it to hold every dependency install and
resolution to uv.lock (Python) and package-lock.json (npm). Substring scanning
was bypassed five ways in review (env-var indirection, folded scalars, a local
composite action, flags in a trailing comment, ``uv pip``), and a first grammar
was bypassed again (``command pip ...``, ``python -c``, unread ``npm run``
scripts, requirements files that never came from the lock). So this parses the
workflow YAML, splits each ``run:`` script into simple commands with a small
shell lexer, and accepts a command only if its program is on an allowlist and,
for every program that installs, resolves, or runs other commands, only in an
explicitly listed shape. Constructs it cannot read -- inline interpreter code
(``python -c``, ``python -``, heredocs, here-strings), command-running prefixes
(``command``, ``env``, ``exec``, ``xargs``, ``bash -c`` ...), variables or
expressions as program names, unknown actions -- are violations, not passes.

What it does *not* establish, so nobody leans on it for more:

* It reads workflow text, not what the code it lets run does. ``pytest``,
  ``eslint``, ``vitest``, the repository's own ``scripts/*.py`` and the
  pinned actions all execute code; a test or script that shells out to pip is
  outside this check (it would be reviewed as code).
* It does not model data flow through files. It refuses writes (redirections,
  ``cp``, ``curl -o``) to the paths that would turn a write into an install --
  scripts, sources, interpreter/installer config, requirements files, a venv's
  ``bin/`` or ``site-packages`` -- but a file written some other way and then
  consumed by an allowed program is not tracked.
* PowerShell steps get a line denylist (no Python or installer at all, modules
  only at an exact version), not this grammar.
* ``npx playwright install`` downloads browser builds that the locked
  ``@playwright/test`` version names; they are not hashed by package-lock.json.
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
    words: list[str]
    assignments: dict[str, str] = field(default_factory=dict)
    #: Targets of output redirections (``>``, ``>>``, ``&>``...), quotes removed.
    writes: list[str] = field(default_factory=list)


_OPERATORS = ("&&", "||", ";;", ";", "|", "&", "\n", "(", ")", "{", "}")
_RESERVED_PREFIX = {"if", "then", "else", "elif", "do", "while", "until", "!"}
_SKIPPED = {"fi", "done", "esac"}
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
        nonlocal words, writes
        end_word()
        if words or writes:
            command = _simple(words, writes)
            if command is not None:
                commands.append(command)
        words = []
        writes = []

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
                    word += script[index:end]
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
            word += script[index:end]
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
            # The redirection target is not a command word.
            target = re.match(r"[^\s;&|()]*", script[index:])
            assert target is not None
            index += target.end()
            text = target[0].replace('"', "").replace("'", "")
            if ">" in operator_text and not re.fullmatch(r"\d*-?", text):
                writes.append(text)
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


def _simple(words: list[str], writes: list[str]) -> Command | None:
    while words and words[0] in _RESERVED_PREFIX:
        words = words[1:]
    if words and (words[0] in _SKIPPED or words[0] in {"for", "case", "in"}):
        return None
    assignments: dict[str, str] = {}
    while words and _ASSIGNMENT.match(words[0]):
        name, _, value = words[0].partition("=")
        assignments[name.rstrip("+")] = value
        words = words[1:]
    if not words and not assignments and not writes:
        return None
    return Command(list(words), assignments, list(writes))


# --- the allowlist ---------------------------------------------------------------

#: Programs that neither install nor resolve packages and run no other command.
#: The ones with options that could (or that write files) are checked further below.
PLAIN = {
    "[",
    "basename",
    "break",
    "cat",
    "cmp",
    "continue",
    "cp",
    "curl",
    "echo",
    "exit",
    "export",
    "false",
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
    "return",
    "rm",
    "seq",
    "set",
    "sha256sum",
    "shift",
    "sleep",
    "stat",
    "tail",
    "tar",
    "test",
    "true",
    "wait",
    "wc",
}
#: Programs that run another command (or a shell) and so would launder it past this check.
INDIRECTION = {
    ".",
    "alias",
    "builtin",
    "busybox",
    "chroot",
    "coproc",
    "dash",
    "doas",
    "env",
    "eval",
    "exec",
    "fish",
    "flock",
    "function",
    "ionice",
    "ksh",
    "nice",
    "nohup",
    "nsenter",
    "parallel",
    "runuser",
    "script",
    "setsid",
    "sh",
    "source",
    "stdbuf",
    "strace",
    "su",
    "sudo",
    "systemd-run",
    "taskset",
    "time",
    "timeout",
    "unbuffer",
    "unshare",
    "watch",
    "xargs",
    "zsh",
}
#: Locked Python tools; only under ``uv run --locked``, where they come from uv.lock.
PYTHON_TOOLS = {"pytest", "ruff", "mypy", "pip-audit", "pip-licenses", "cyclonedx-py"}
#: Locked node tools; only inside a package.json script, where node_modules/.bin is on PATH.
NODE_TOOLS = {"prettier", "eslint", "vitest", "playwright"}
_PIP = re.compile(r"(^|/)pip3?$")
_PYTHON = re.compile(r"(^|/)python(3(\.\d+)?)?$")
_APP = re.compile(r"(^|/)gpo-studio$")
_ARTIFACT = re.compile(
    r"^(dist/gpo_studio-\*\.(whl|tar\.gz)|\$WHEEL|\$\{WHEELS\[0\]\}|\$Wheel\.FullName)$"
)
_SCANNED_SCRIPTS = {"scripts/installed_package_smoke.sh"}
_VENV_PROGRAM = re.compile(r"^(/tmp/[A-Za-z0-9_.-]+|\$TMPDIR/venv)/bin/[A-Za-z0-9_.-]+$")
_LOCAL_URL = re.compile(r"^http://127\.0\.0\.1:(\d+|\$PORT)(/[^@\s]*)?$")
_CURL_FLAGS = {"-s", "-f", "-S", "-sf", "-fs", "-sS", "-sSf", "-fsS"}
_CURL_OPTIONS = {"-X", "-H", "-d", "-o", "-w"}
#: Variables the smoke script trusts from its caller; a workflow may set them only inline.
_HANDOFF_ENV = {"PYTHON", "GPO_STUDIO_REQUIREMENTS"}
#: The smoke script's hand-off: the caller passes a requirements file it exported.
_HANDOFF = '$(realpath "$GPO_STUDIO_REQUIREMENTS")'
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
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
#: Environment that redirects an installer, the interpreter, or the shell itself.
_DANGEROUS_ENV = re.compile(
    r"^(PIP_.*|UV_.*|NPM_CONFIG_.*|PYTHON(PATH|HOME|USERBASE|STARTUP)|NODE_OPTIONS|PATH|"
    r"BASH_ENV|ENV|LD_PRELOAD|LD_LIBRARY_PATH|GIT_.*)$",
    re.IGNORECASE,
)
#: Files whose overwrite would change what gets installed or what an allowed program runs.
_PROTECTED_NAMES = {
    ".npmrc",
    ".pypirc",
    ".pydistutils.cfg",
    "package-lock.json",
    "package.json",
    "pip.conf",
    "pip.ini",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "uv.toml",
}
_PROTECTED_DIRS = {"scripts", "tests", "src", ".github", "githooks", "node_modules", ".venv"}
_PROTECTED_SUFFIXES = (".py", ".pyc", ".pth", ".sh", ".js", ".mjs", ".cjs", ".ps1", ".psm1")


@dataclass
class Context:
    """What the checker knows while it walks one workflow file or script."""

    root: Path = REPO_ROOT
    package_json: Path | None = None
    #: The smoke script trusts its caller's ``$PYTHON`` and requirements hand-off.
    script: bool = False
    #: Every ``--output-file`` of an accepted ``uv export --locked`` so far.
    exported: set[str] = field(default_factory=set)
    #: Every value each variable was assigned in the current step or script.
    values: dict[str, list[str]] = field(default_factory=dict)
    npm_stack: tuple[str, ...] = ()

    def resolve(self, word: str) -> set[str]:
        """The strings *word* can stand for: its first variable, by each value assigned so far.

        Deliberately one level: a value that itself names a variable stays literal, so it
        matches only an identically spelled export (both sides resolve the same way).
        """
        match = _VARIABLE.search(word)
        if match is None:
            return {word}
        name = match[1] or match[2]
        if name not in self.values:
            return {word}
        return {word[: match.start()] + value + word[match.end() :] for value in self.values[name]}

    def npm_scripts(self) -> dict[str, Any]:
        path = self.package_json or self.root / "package.json"
        scripts = json.loads(path.read_text(encoding="utf-8")).get("scripts") or {}
        assert isinstance(scripts, dict)
        return scripts


def _program(word: str, ctx: Context) -> str:
    """The program a command word names; a word that is (or ends in) a variable is refused."""
    if word == "$PYTHON" and ctx.script:
        return "python"
    if "/" in word:
        # Only a program inside a venv this workflow made: `./pip` could be anything.
        if not _VENV_PROGRAM.match(word):
            raise GrammarError(f"a program path outside a /tmp venv: {word!r}")
        word = word.rsplit("/", 1)[1]
    if "$" in word or "`" in word:
        raise GrammarError(f"indirect command {word!r}")
    return word


def _protected(target: str, ctx: Context) -> bool:
    for text in ctx.resolve(target):
        path = re.sub(r"^(\$\{?(GITHUB_WORKSPACE|PWD)\}?/|\./)+", "", text).rstrip("/")
        name = path.rsplit("/", 1)[-1]
        exported_names = {item.rsplit("/", 1)[-1] for item in ctx.exported}
        if (
            "GITHUB_ENV" in path
            or "GITHUB_PATH" in path
            or path.split("/", 1)[0] in _PROTECTED_DIRS
            or name in _PROTECTED_NAMES
            or name.endswith(_PROTECTED_SUFFIXES)
            or name in exported_names
            or path in ctx.exported
            or "site-packages" in path
            or "/bin/" in f"/{path}"
            or path.endswith("/bin")
        ):
            return True
    return False


def _repo_file(path: str, ctx: Context, *, under: tuple[str, ...], suffix: str) -> bool:
    """*path* is a checked-in-style relative path to an existing file under *under*."""
    if not path.startswith(tuple(f"{prefix}/" for prefix in under)) or not path.endswith(suffix):
        return False
    if ".." in path.split("/") or "$" in path:
        return False
    candidate = (ctx.root / path).resolve()
    return candidate.is_file() and candidate.is_relative_to(ctx.root.resolve())


def violations(commands: list[Command], *, where: str, ctx: Context | None = None) -> Iterator[str]:
    ctx = ctx if ctx is not None else Context()
    previous: Command | None = None
    for command in commands:
        yield from _check_environment(command, ctx, where)
        for target in command.writes:
            if _protected(target, ctx):
                yield f"{where}: a write to a protected path {target!r}"
        if not command.words:
            for name, value in command.assignments.items():
                ctx.values.setdefault(name, []).append(value)
            continue
        yield from _check(command, previous, ctx, where=where)
        previous = command


def _check_environment(command: Command, ctx: Context, where: str) -> Iterator[str]:
    names = list(command.assignments)
    if command.words[:1] == ["export"]:
        names += [arg.partition("=")[0] for arg in command.words[1:]]
    inline_ok = {"UV_PROJECT_ENVIRONMENT"} if command.words[:1] == ["uv"] else set()
    for name in names:
        if _DANGEROUS_ENV.match(name) and name not in inline_ok:
            yield f"{where}: refused environment {name!r} (it redirects an installer or the shell)"
    if command.words[:1] == ["export"] and not ctx.script:
        for name in sorted(_HANDOFF_ENV & set(names)):
            yield f"{where}: export of {name} reaches the smoke script unchecked; set it inline"


def _check(
    command: Command, previous: Command | None, ctx: Context, *, where: str, in_uv: bool = False
) -> Iterator[str]:
    words = command.words
    try:
        program = _program(words[0], ctx)
    except GrammarError as error:
        yield f"{where}: {error}"
        return
    args = words[1:]
    shown = " ".join(words)
    if program in INDIRECTION:
        yield f"{where}: refused indirection {shown!r}"
    elif program == "command":
        # `command -v NAME` only looks NAME up; any other form runs it.
        if len(args) != 2 or args[0] != "-v" or not re.fullmatch(r"[A-Za-z0-9_.-]+", args[1]):
            yield f"{where}: command only as `command -v NAME`, not {shown!r}"
    elif program == "trap":
        yield from _check_trap(args, ctx, where, shown)
    elif program == "bash":
        yield from _check_bash(command, ctx, where, shown)
    elif _PIP.search(program):
        yield from _check_pip(args, shown, where, ctx)
    elif _PYTHON.search(program):
        yield from _check_python(args, shown, where, ctx)
    elif program == "uv":
        yield from _check_uv(command, previous, ctx, shown, where)
    elif program == "npm":
        yield from _check_npm(args, ctx, where, shown)
    elif program == "npx":
        browsers = {"--with-deps", "chromium", "firefox", "webkit"}
        if args[:2] != ["playwright", "install"] or not set(args[2:]) <= browsers:
            yield f"{where}: npx only installs the Playwright browsers: {shown!r}"
    elif program in PYTHON_TOOLS:
        if not in_uv:
            yield f"{where}: {program} only under `uv run --locked`: {shown!r}"
        elif program == "mypy" and any(arg.startswith("--install-types") for arg in args):
            yield f"{where}: mypy --install-types installs from the index: {shown!r}"
        elif program == "pip-audit" and (
            "--disable-pip" not in args or any("://" in arg for arg in args)
        ):
            yield f"{where}: pip-audit only with --disable-pip and local files: {shown!r}"
    elif program in NODE_TOOLS:
        if not ctx.npm_stack:
            yield f"{where}: {program} only inside a scanned package.json script: {shown!r}"
        elif program == "playwright" and args[:1] != ["test"]:
            yield f"{where}: playwright only as `playwright test`: {shown!r}"
    elif _APP.search(program):
        pass
    elif program in PLAIN:
        yield from _check_plain(program, args, ctx, where, shown)
    else:
        yield f"{where}: unrecognised program {program!r} in {shown!r}"


def _check_plain(
    program: str, args: list[str], ctx: Context, where: str, shown: str
) -> Iterator[str]:
    if program == "curl":
        # Every operand that is not a listed option's value is a URL to curl.
        urls: list[str] = []
        rest = iter(args)
        for arg in rest:
            if arg in _CURL_FLAGS:
                continue
            if arg in _CURL_OPTIONS:
                value = next(rest, "")
                if arg == "-o" and _protected(value, ctx):
                    yield f"{where}: a write to a protected path {value!r}"
                continue
            urls.append(arg)
        if not urls or not all(_LOCAL_URL.match(url) for url in urls):
            yield f"{where}: curl may only reach the local server: {shown!r}"
    elif program == "cp":
        operands = [arg for arg in args if not arg.startswith("-")]
        targets = operands[-1:] + [
            value for flag, value in zip(args, args[1:], strict=False) if flag == "-t"
        ]
        for target in targets:
            if _protected(target, ctx):
                yield f"{where}: a write to a protected path {target!r}"
    elif program == "find":
        if any(
            arg in {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls"}
            or arg.startswith("-fprint")
            for arg in args
        ):
            yield f"{where}: find only to list files: {shown!r}"
    elif program == "gh":
        if args[:2] != ["release", "create"]:
            yield f"{where}: gh only as `gh release create`: {shown!r}"
    elif program == "git":
        read_only = {"fetch", "merge-base", "rev-parse", "diff", "log", "show", "status"}
        if (
            args[:1] == []
            or args[0] not in read_only
            or any(
                "upload-pack" in arg or arg.startswith(("--exec", "--output", "--ext-diff"))
                for arg in args
            )
        ):
            yield f"{where}: git only as {sorted(read_only)} with no program options: {shown!r}"
    elif program == "tar":
        if len(args) != 2 or args[0] not in {"tf", "-tf", "tzf", "-tzf", "ztf", "-ztf"}:
            yield f"{where}: tar only to list one archive: {shown!r}"
    elif program == "mapfile":
        if len(args) != 2 or args[0] != "-t" or not re.fullmatch(r"[A-Za-z_]\w*", args[1]):
            yield f"{where}: mapfile only as `mapfile -t NAME`: {shown!r}"
    elif program == "set":
        # Shell options only; `set -a` would export every later assignment.
        options = {"pipefail", "errexit", "nounset", "xtrace"}
        if not all(re.fullmatch(r"[-+][euxo]+", arg) or arg in options for arg in args):
            yield f"{where}: set only as -e/-u/-x/-o pipefail: {shown!r}"


def _check_trap(args: list[str], ctx: Context, where: str, shown: str) -> Iterator[str]:
    """``trap BODY SIGNAL...``: the body is a script the shell runs later, so it is checked."""
    if not args or args[0] in {"-", ""}:
        return
    if len(args) < 2:
        yield f"{where}: unrecognised trap {shown!r}"
        return
    try:
        yield from violations(split_commands(args[0]), where=f"{where} (trap)", ctx=ctx)
    except GrammarError as error:
        yield f"{where} (trap): {error}"


def _check_bash(command: Command, ctx: Context, where: str, shown: str) -> Iterator[str]:
    args = command.words[1:]
    if len(args) != 1 or args[0] not in _SCANNED_SCRIPTS:
        yield f"{where}: bash may only run a scanned script, not {shown!r}"
        return
    if not _repo_file(args[0], ctx, under=("scripts",), suffix=".sh"):
        yield f"{where}: bash script {args[0]!r} is not a checked-in file"
    # The smoke script trusts these two from its caller; hold the caller to them.
    python = command.assignments.get("PYTHON")
    if python is not None and not (_PYTHON.search(python) and "$" not in python):
        yield f"{where}: the smoke script's PYTHON must name a python: {shown!r}"
    requirements = command.assignments.get("GPO_STUDIO_REQUIREMENTS")
    if requirements is not None and not ctx.resolve(requirements) <= ctx.exported:
        yield f"{where}: GPO_STUDIO_REQUIREMENTS is not a `uv export --locked` output: {shown!r}"


def _requirement_ok(path: str, ctx: Context) -> bool:
    """A ``-r`` file is one this file exported from uv.lock (or the smoke script's hand-off)."""
    if "://" in path or path.startswith("-"):
        return False
    accepted = ctx.exported | ({_HANDOFF} if ctx.script else set())
    return ctx.resolve(path) <= accepted


def _check_pip(args: list[str], shown: str, where: str, ctx: Context) -> Iterator[str]:
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
        for path in files[1::2]:
            if not _requirement_ok(path, ctx):
                yield (
                    f"{where}: requirements file {path!r} is not a `uv export --locked` "
                    f"output of this file: {shown!r}"
                )
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


def _check_python(args: list[str], shown: str, where: str, ctx: Context) -> Iterator[str]:
    if args[:2] == ["-m", "pip"]:
        yield from _check_pip(args[2:], shown, where, ctx)
    elif args[:2] == ["-m", "build"]:
        if "--no-isolation" not in args:
            yield f"{where}: python -m build without --no-isolation: {shown!r}"
    elif args[:2] == ["-m", "venv"]:
        if len(args) not in (3, 4) or (len(args) == 4 and args[3] != "--clear"):
            yield f"{where}: python -m venv only as `-m venv DIR [--clear]`: {shown!r}"
    elif args[:2] == ["-m", "gpo_studio"] or (
        args[:1] and _repo_file(args[0], ctx, under=("scripts", "tests"), suffix=".py")
    ):
        pass
    elif args[:1] in (["-c"], ["-"]) or not args:
        yield f"{where}: inline python code is unreadable here (use a scripts/*.py): {shown!r}"
    else:
        yield f"{where}: unrecognised python use {shown!r}"


def _check_npm(args: list[str], ctx: Context, where: str, shown: str) -> Iterator[str]:
    if args == ["ci"] and not ctx.npm_stack:
        hooks = sorted(_NPM_INSTALL_HOOKS & set(ctx.npm_scripts()))
        if hooks:
            yield f"{where}: npm ci would run package.json lifecycle scripts {hooks}"
        if (ctx.root / "binding.gyp").exists():
            yield f"{where}: npm ci would run node-gyp for binding.gyp"
        return
    if args[:1] == ["audit"] and not ctx.npm_stack:
        if not all(re.fullmatch(r"--audit-level=[a-z]+", arg) for arg in args[1:]):
            yield f"{where}: npm audit only reports (--audit-level=...): {shown!r}"
        return
    if args == ["test"]:
        name = "test"
    elif len(args) == 2 and args[0] in {"run", "run-script"}:
        name = args[1]
    elif ctx.npm_stack:
        yield f"{where}: npm inside a package.json script only as test or run NAME: {shown!r}"
        return
    else:
        yield f"{where}: npm only as ci, audit, test or run NAME: {shown!r}"
        return
    yield from _check_npm_script(name, ctx, where)


def _check_npm_script(name: str, ctx: Context, where: str) -> Iterator[str]:
    """Lex the package.json script and its pre/post hooks with this same grammar."""
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
        ctx.npm_stack = (*ctx.npm_stack, name)
        try:
            yield from violations(split_commands(body), where=here, ctx=ctx)
        except GrammarError as error:
            yield f"{here}: {error}"
        finally:
            ctx.npm_stack = ctx.npm_stack[:-1]


def _check_uv(
    command: Command, previous: Command | None, ctx: Context, shown: str, where: str
) -> Iterator[str]:
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
            return
        if "://" in tail[3]:
            yield f"{where}: uv export to a URL: {shown!r}"
            return
        ctx.exported |= ctx.resolve(tail[3]) | {tail[3]}
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
        yield from _check(Command(rest, command.assignments), None, ctx, where=where, in_uv=True)
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


def _yaml_env_violations(env: Any, where: str) -> Iterator[str]:
    for name in env or {}:
        if _DANGEROUS_ENV.match(str(name)) or str(name) in {"PYTHON", "GPO_STUDIO_REQUIREMENTS"}:
            yield f"{where}: refused environment {name!r} (it redirects an installer or the shell)"


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
    ctx = Context(root=root, package_json=package_json)
    found.extend(_yaml_env_violations(document.get("env"), path.name))
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
        found.extend(_yaml_env_violations(job.get("env"), f"{path.name}:{job_name}"))
        found.extend(_defaults_violations(job, f"{path.name}:{job_name}"))
        if "container" in job:
            found.append(f"{path.name}:{job_name}: a job container is an unscanned environment")
        for number, step in enumerate(job.get("steps") or [], 1):
            where = f"{path.name}:{job_name}#{number}"
            if "uses" in step:
                found.extend(_action_violations(step, where))
            found.extend(_yaml_env_violations(step.get("env"), where))
            if "working-directory" in step:
                found.append(f"{where}: working-directory moves every relative path checked here")
            if "run" not in step:
                continue
            script = str(step["run"])
            if "${{" in script:
                found.append(f"{where}: an expression inside run: is a command nobody can read")
                continue
            shell = step.get("shell")
            if shell == "pwsh":
                found.extend(_pwsh_violations(script, where))
                continue
            if shell not in (None, "bash"):
                found.append(f"{where}: shell {shell!r} is not bash or pwsh")
                continue
            ctx.values = {}
            try:
                found.extend(violations(split_commands(script), where=where, ctx=ctx))
            except GrammarError as error:
                found.append(f"{where}: {error}")
    return found


def script_violations(path: Path, *, root: Path = REPO_ROOT) -> list[str]:
    ctx = Context(root=root, script=True)
    try:
        commands = split_commands(path.read_text(encoding="utf-8"))
        return list(violations(commands, where=path.name, ctx=ctx))
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
