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
* the match to be on the words *as written*: quotes, escapes and ``$`` intact,
  so ``'$X'`` (no expansion) never matches a reviewed ``"$X"``;
* facts a template needs (a lock exported, ``npm ci`` run, the venv or the
  distributions built) to have been established earlier in the same job, by a
  command whose failure would have failed the job: not in a condition, an
  ``&&``/``||`` list, a pipeline, the background or a substitution, not in an
  ``if:`` step, and visible only inside the block (branch, loop, group) that
  established it; ``continue-on-error`` is refused outright;
* every package.json script a template runs to be held to the same table, no
  project npm configuration (``.npmrc``) to replace the shell it runs in, and
  every npm program a template runs (``LOCKED_BINS``) to be pinned in
  package.json, present in package-lock.json at that version, and run from
  ``node_modules/.bin`` -- never fetched by ``npx``; every lock entry, npm and uv
  alike, to carry a hash;
* every Python template to run with ``-I``, so neither the script's directory
  nor the working directory comes first on ``sys.path``;
* every pip install to be wheel-only (``--only-binary :all:``) or, for this
  project's own sdist alone, ``--no-build-isolation`` into a venv this job filled
  with the locked build backend: ``--require-hashes`` does not stop an sdist's
  isolated build resolving its build requirements from the index. Every uv.lock
  package must have a wheel each CI target accepts -- decided by
  ``packaging.tags``' rules for linux x86_64 (glibc ``LINUX_GLIBC_FLOOR``) and
  win_amd64 on CPython 3.13 and 3.14 -- so neither pip nor uv builds a
  dependency; and at run time ``[tool.uv] no-build-package`` names every locked
  dependency, so uv refuses to build one at all, while the one build it does,
  gpo-studio's, is non-isolated against the locked backend
  (``no-build-isolation-package``; the global setting is refused, since a fresh
  environment then builds before the backend is installed);
* ``npm ci --ignore-scripts``, with every lock entry that has install scripts
  listed in ``REVIEWED_INSTALL_SCRIPTS``, and no ``node_modules`` (or ``.venv``,
  ``dist``, ``.yarn``, Plug'n'Play loader) tracked at any depth: Node resolves a
  nearer ``node_modules`` first, and ``npm ci`` rebuilds only the root one;
* every PowerShell step, and every step of a Windows job (PowerShell by
  default), to be exactly one of ``PWSH_BODIES``;
* no comment and no backslash escape (but a continuation line) inside a command
  substitution, and no substitution at all inside an ``echo`` slot -- places bash
  and this lexer could disagree about where a ``$(...)`` ends; a test runs every
  accepted script under ``bash -x`` with nothing on PATH and requires each
  command bash reaches to equal, word for word, one the grammar found;
* no tracked symlink or submodule, no two tracked paths that are one path on a
  case-insensitive disk, and every name below compared case- and
  Unicode-normalisation-insensitively, as the Windows job's NTFS compares it;
* no tracked file to change what those tools load or run
  (``configuration_violations``, which inventories each tool's discovery rules
  in ``TOOL_DISCOVERY``, read from the locked version's source and re-read when
  it moves: config files by name, pinned by path and SHA-256 where one is reviewed;
  modules that would shadow a tool or the standard library; compiled ``.pyc``;
  every ``[tool.*]`` table and ``[build-system]`` exactly as reviewed; no
  tracked ``node_modules/`` or ``dist/``);
* every ``${{ }}`` expression outside ``run:`` (where none may appear) to be one
  of ``REVIEWED_EXPRESSIONS`` or ``matrix.KEY`` over a literal matrix, every
  ``if:`` to be one of ``REVIEWED_CONDITIONS``, and a matrix-valued action input
  to be checked value by value;
* every action to be exactly one ``ACTIONS`` entry -- its reviewed ``uses:``
  revision, every required input present with a listed value (an input that
  only names files excepted), no other input -- and facts to survive only an
  entry reviewed as leaving the workspace alone; every job to run on one of
  ``RUNNERS``,
  and no ``shell:``, ``working-directory``, default, ``container`` or
  ``services`` image that would change how the text above runs.

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
* ``playwright install --with-deps`` downloads the browser builds the locked
  ``@playwright/test`` names, which package-lock.json does not hash, and installs
  their system libraries through the runner's distribution package manager.
  PSScriptAnalyzer, the one PowerShell module, is outside both locks too; it is
  pinned by the SHA-256 of its package instead, checked before it is imported.
* GitHub's runner image itself, and the pinned actions -- their code, and what
  they download with the listed input values (uv 0.12.0, CPython, Node 24, the
  Playwright cache, checkout's credentials) -- are trusted as provisioned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import unicodedata
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
    #: Each word of the command as written: quotes, escapes and ``$`` intact. Templates
    #: match these, so ``'$X'`` (no expansion) never matches a reviewed ``"$X"``.
    raw: tuple[str, ...] = ()
    #: How many leading ``raw`` words are ``NAME=value`` assignments.
    assigned: int = 0
    #: Targets of output redirections (``>``, ``>>``, ``&>``...), quotes removed.
    writes: tuple[str, ...] = ()
    #: Targets of input redirections (``<``).
    reads: tuple[str, ...] = ()
    #: The control-flow blocks the command sits in (branch, loop body, case arm, group).
    block: tuple[int, ...] = ()
    #: Its outcome is not enforced: in a condition, an ``&&``/``||`` list, a pipeline,
    #: the background, a ``!``, or a command substitution. Such a command proves nothing.
    conditional: bool = False

    @property
    def tokens(self) -> tuple[str, ...]:
        return self.raw

    @property
    def assignments(self) -> tuple[str, ...]:
        return self.raw[: self.assigned]


_OPERATORS = ("&&", "||", ";;", ";", "|", "&", "\n", "(", ")", "{", "}")
_JOINING = {"&&", "||", "|", "&"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
#: ``${NAME}``, ``${#NAME[@]}``, ``${NAME[0]}``, ``${NAME:-literal}``: nothing that runs.
#: The ``${...}`` forms a script may use, quoted or not: a name, an optional ``#``
#: length, a literal subscript (``@``, ``*`` or digits -- a subscript is evaluated
#: arithmetically, and arithmetic runs ``a[$(cmd)]``), and a default or pattern
#: operator with a literal word. Never ``@P``/``@E``/``@A``/``@Q`` (prompt expansion
#: runs a substitution taken from the value, Sol r10 B1), ``!`` indirection, a nested
#: expansion, or a substring offset (arithmetic again).
_PARAMETER = re.compile(
    r"^\$\{#?[A-Za-z_][A-Za-z0-9_]*(\[(?:@|\*|\d+)\])?"
    r"((?::?[-=+?]|##?|%%?)[^$`(){}\\@!]*)?\}$"
)


def _read_balanced(text: str, start: int) -> int:
    """Index just past the ``)`` closing the ``(`` before *start*."""
    depth = 1
    index = start
    quote = ""
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'":
            # An escape is a place bash and this reader could disagree about where the
            # `)` is (DeepSeek r8: `\)`), and no reviewed command needs one; a
            # continuation never reaches here (refused by _logical).
            raise GrammarError("backslash escape inside a command substitution")
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#":
            # Bash would read a comment here and ignore a `)` on that line; this reader
            # would not. No reviewed command needs one, so refuse rather than model it.
            raise GrammarError("comment syntax inside a command substitution")
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise GrammarError("unbalanced parenthesis")


class _Blocks:
    """The stack of compound commands around the lexer's position."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, int]] = []
        self.counter = 0

    def _new(self) -> int:
        self.counter += 1
        return self.counter

    def push(self, kind: str) -> None:
        self.frames.append((kind, self._new()))

    def replace(self, kind: str) -> None:
        if not self.frames:
            raise GrammarError(f"{kind} outside a compound command")
        self.frames[-1] = (kind, self._new())

    def pop(self) -> None:
        if not self.frames:
            raise GrammarError("a compound command closed that never opened")
        self.frames.pop()

    @property
    def path(self) -> tuple[int, ...]:
        return tuple(number for _kind, number in self.frames)

    @property
    def in_condition(self) -> bool:
        return any(kind == "condition" for kind, _number in self.frames)


def _logical(script: str) -> str:
    """The script with its line continuations removed, as bash removes them first.

    Bash deletes an unquoted backslash-newline before it recognises anything else, so
    ``$\\<newline>(`` is a substitution. Outside quotes and substitutions that removal
    is done here, exactly; a continuation inside double quotes, a substitution or a
    parameter expansion is refused instead (nesting changes the rule there, and no
    reviewed command needs one). A comment line keeps its trailing backslash: bash
    does not continue a comment.
    """
    out: list[str] = []
    index = 0
    word_start = True
    while index < len(script):
        char = script[index]
        if char == "\\":
            if script.startswith("\\\n", index):
                index += 2
                continue
            out.append(script[index : index + 2])
            index += 2
            word_start = False
            continue
        if char == "'":
            end = script.find("'", index + 1)
            end = len(script) - 1 if end < 0 else end
            out.append(script[index : end + 1])
            index = end + 1
            word_start = False
            continue
        if char == "#" and word_start:
            end = script.find("\n", index)
            end = len(script) if end < 0 else end
            out.append(script[index:end])
            index = end
            continue
        if char == '"' or script.startswith(("$(", "<(", ">(", "${"), index):
            end = _quoted_end(script, index)
            if "\\\n" in script[index:end]:
                raise GrammarError("a line continuation inside quotes or an expansion")
            out.append(script[index:end])
            index = end
            word_start = False
            continue
        out.append(char)
        word_start = char in " \t\n;&|()<>"
        index += 1
    return "".join(out)


def _quoted_end(script: str, index: int) -> int:
    """Index just past the double-quoted string or expansion starting at *index*."""
    if script[index] == '"':
        position = index + 1
        while position < len(script) and script[position] != '"':
            position += 2 if script[position] == "\\" else 1
        return position + 1
    if script.startswith("${", index):
        end = script.find("}", index)
        return len(script) if end < 0 else end + 1
    depth = 0
    position = index + 1
    quote = ""
    while position < len(script):
        char = script[position]
        if quote:
            if char == "\\" and quote == '"':
                position += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "\\":
            position += 2
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return len(script)


def split_commands(script: str, *, closed: bool = True) -> list[Command]:
    """Simple commands of a POSIX-ish shell script, substitutions included.

    A command substitution's commands come before the command that uses its
    output (and are conditional); the word keeps the substitution's source text.
    """
    script = _logical(script)
    if "$'" in script:
        raise GrammarError("ANSI-C quoting ($'...'), which no reviewed command uses")
    commands: list[Command] = []
    words: list[str] = []
    raws: list[str] = []
    writes: list[str] = []
    reads: list[str] = []
    word = ""
    in_word = False
    start = -1
    case_depth = 0
    joined = False  # the previous command ended in && || | &
    blocks = _Blocks()
    index = 0

    def begin() -> None:
        nonlocal start
        if not in_word:
            start = index

    def end_word() -> None:
        nonlocal word, in_word
        if in_word:
            words.append(word)
            raws.append(script[start:index])
        word = ""
        in_word = False

    def nested(text: str) -> None:
        for command in split_commands(text):
            command.block = blocks.path
            command.conditional = True
            commands.append(command)

    def end_command(operator: str) -> None:
        nonlocal words, raws, writes, reads, joined
        end_word()
        negated = False
        while words and words[0] in {"if", "then", "else", "elif", "do", "while", "until", "!"}:
            keyword = words[0]
            if keyword in {"if", "while", "until"}:
                blocks.push("condition")
            elif keyword == "elif":
                blocks.replace("condition")
            elif keyword in {"then", "else", "do"}:
                blocks.replace("body")
            else:
                negated = True
            words, raws = words[1:], raws[1:]
        if words and words[0] in {"fi", "done", "esac"}:
            blocks.pop()
            words, raws = words[1:], raws[1:]
        if words or writes or reads:
            assigned = 0
            if words[:1] != ["for"]:
                while assigned < len(raws) and _ASSIGNMENT.match(raws[assigned]):
                    assigned += 1
            conditional = joined or operator in _JOINING or negated or blocks.in_condition
            commands.append(
                Command(
                    list(words[assigned:]),
                    tuple(raws),
                    assigned,
                    tuple(writes),
                    tuple(reads),
                    blocks.path,
                    conditional,
                )
            )
            if words[:1] == ["for"]:
                blocks.push("condition")  # `do` turns it into the loop body
        joined = operator in _JOINING
        words, raws, writes, reads = [], [], [], []

    while index < len(script):
        char = script[index]
        if char == "\\" and index + 1 < len(script):
            if script[index + 1] == "\n":
                index += 2
                continue
            begin()
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
            begin()
            word += script[index + 1 : end]
            in_word = True
            index = end + 1
            continue
        if char == '"':
            begin()
            in_word = True
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
                if script.startswith(("$((", "$["), index):
                    raise GrammarError("arithmetic expansion, which evaluates a[$(cmd)]")
                if script.startswith("$(", index):
                    end = _read_balanced(script, index + 2)
                    nested(script[index + 2 : end - 1])
                    word += script[index:end]
                    index = end
                    continue
                if script.startswith("${", index):
                    end = script.find("}", index)
                    if end < 0 or not _PARAMETER.fullmatch(script[index : end + 1]):
                        shown = script[index : index + 40]
                        raise GrammarError(
                            f"parameter expansion this grammar cannot read: {shown!r}"
                        )
                    word += script[index : end + 1]
                    index = end + 1
                    continue
                if script[index] == "`":
                    raise GrammarError("backtick command substitution")
                word += script[index]
                index += 1
            index += 1
            continue
        if char == "`":
            raise GrammarError("backtick command substitution")
        if script.startswith(("$((", "$["), index):
            raise GrammarError("arithmetic expansion, which evaluates a[$(cmd)]")
        if script.startswith("$(", index) or script.startswith("<(", index):
            end = _read_balanced(script, index + 2)
            nested(script[index + 2 : end - 1])
            begin()
            word += script[index:end]
            in_word = True
            index = end
            continue
        if script.startswith("${", index):
            end = script.find("}", index)
            if end < 0 or not _PARAMETER.fullmatch(script[index : end + 1]):
                shown = script[index : index + 40]
                raise GrammarError(f"parameter expansion this grammar cannot read: {shown!r}")
            begin()
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
            first = index
            index += 1
            while index < len(script) and script[index] in "<>&|=":
                index += 1
            operator_text = script[first:index]
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
                # A case pattern: `*-rc.*)` is not a command; an arm starts.
                words, raws, word, in_word = [], [], "", False
                blocks.replace("body")
                index += 1
                continue
            end_word()
            if words[:1] == ["case"]:
                case_depth += 1
                words, raws = [], []
                blocks.push("condition")  # the first pattern turns it into an arm
                if operator == ")":  # `case $x in a)` on one line
                    blocks.replace("body")
                    index += 1
                    continue
            elif words[:1] == ["esac"]:
                case_depth -= 1
            end_command(operator)
            if operator in {"(", "{"}:
                blocks.push("body")
            elif operator in {")", "}"}:
                blocks.pop()
            index += len(operator)
            continue
        if char in " \t":
            end_word()
            index += 1
            continue
        begin()
        word += char
        in_word = True
        index += 1
    end_command("\n")
    if closed and blocks.frames:
        raise GrammarError("a compound command never closed")
    return commands


# --- the template table ----------------------------------------------------------

#: Named slots a template token may contain, written ``@NAME@`` (``@NAME*@`` as the
#: last token takes every remaining word). Each is as tight as the real uses allow.
SLOTS = {
    # The five throwaway venvs the workflows create, and nothing else under /tmp.
    "VENV": r"/tmp/(?:smoke|sdist|audit|sbom|wheel)-venv",
    # echo's text: it cannot run anything, an option (-e, -n) cannot start a word, and
    # no substitution may sit in it -- a reviewed one is a literal template instead, so
    # the reader below never decides where a slot's substitution ends.
    # Every `$` in it is a bare `$NAME` or `${NAME}` -- the forms the workflows print,
    # and none that evaluates a value a second time (Sol r10 B1: `${X@P}`).
    "TEXT": (r"(?!-)(?:(?![<>]\()[^$`\\]|\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})*"),
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


#: Unquoted expansions a template may contain outside an assignment (where bash
#: neither splits nor globs): each is word-split, so its value must be one that
#: cannot become an option.
REVIEWED_UNQUOTED = {
    "$SERVER_PID": "the shell's own $! -- a process number",
    "$(seq 1 20)": "seq's output: the numbers 1 to 20",
}
_SLOT_MARKER = re.compile(r"@[A-Z_]+\*?@")


def _unquoted(raw: str) -> str:
    """*raw* with its quoted parts and expansions blanked, leaving what bash globs/splits."""
    text = re.sub(r"\$\((?:[^()]|\([^()]*\))*\)", "$S", raw)
    text = re.sub(r"'[^']*'", "Q", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', "Q", text)
    return re.sub(r"\$\{[^}]*\}", "$V", text)


_GLOB = re.compile(r"[*?]|\[[^\]]+\]")


def _unquoted_backslash(raw: str) -> bool:
    """A backslash outside single and double quotes (DeepSeek r12 N1)."""
    quote = ""
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == "'":
            if char == "'":
                quote = ""
        elif quote == '"':
            if char == "\\":
                index += 1
            elif char == '"':
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "\\":
            return True
        index += 1
    return False


def _array_elements(token: str) -> list[str]:
    """The elements of ``NAME=(...)`` / ``NAME+=(...)``: bash globs and splits them."""
    match = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\+?=\((.*)\)", token, re.S)
    if match is None:
        return []
    return list(split_commands(match[1], closed=False)[-1].raw) if match[1].strip() else []


def option_risk(raw: str, *, reviewed: bool = True) -> str | None:
    """Why bash could turn a tracked filename (or a value) in *raw* into an option.

    An unquoted glob is safe only after a literal prefix that is not ``-``
    (``dist/*`` expands to ``dist/...``; ``*.config.mjs`` can expand to
    ``--config=evil.config.mjs``, Sol r11 B1). An unquoted expansion is word-split,
    so it must be one REVIEWED_UNQUOTED names.
    """
    if _unquoted_backslash(raw):
        # `\-*` is a glob whose expansions start with `-`; decoding escapes to tell
        # which is which would be one more parser, and no template needs one.
        return f"an unquoted backslash escape: {raw!r}"
    text = _unquoted(_SLOT_MARKER.sub("", raw))
    glob = _GLOB.search(text)
    if glob:
        prefix = text[: glob.start()]
        if not prefix or prefix.startswith("-") or "$" in prefix or "Q" in prefix:
            return f"an unquoted glob that can expand to an option: {raw!r}"
    if "$" in text and not (reviewed and raw in REVIEWED_UNQUOTED):
        return f"an unquoted expansion, word-split into who knows what: {raw!r}"
    return None


def _t(
    spec: str,
    why: str,
    *,
    scope: str = "workflow",
    writes: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    action: str = "",
) -> Template:
    """A template written as the shell source it matches, quoting and all."""
    command = split_commands(spec, closed=False)[-1]
    tokens = command.raw
    checked = [*tokens[command.assigned :]]
    for assignment in tokens[: command.assigned]:
        # A plain assignment is neither split nor globbed; an array's elements are
        # (DeepSeek r12 N2: `FLAGS=(*)`).
        checked.extend(_array_elements(assignment))
        if _unquoted_backslash(assignment):
            checked.append(assignment)
    for token in checked:
        risk = option_risk(token)
        if risk:
            raise ValueError(f"template {spec!r}: {risk}")
    return Template(
        tokens, why, frozenset(scope.split()), frozenset(writes), requires, provides, action
    )


_LOCKED_RUN = "a tool from uv.lock via `uv run --locked`; it resolves nothing"
_LOCKED_SCRIPT = "a checked-in script under `uv run --locked`"
_SCRIPT = "a checked-in script on the runner's Python; it installs nothing"
_VENV_RUN = "a checked-in script or the installed app, inside a venv built from the lock"
_SHELL = "shell control flow or a test; it runs nothing and writes nothing"
_ASSIGN = "binds a variable later templates name, to this exact value only"
_ECHO = "prints text; a redirection is checked separately"
_LOCAL = "talks to the server this job started on 127.0.0.1 only"
_READ = "reads or lists local files only"
TEMPLATES: tuple[Template, ...] = (
    _t(
        "uv lock --check",
        "checks uv.lock against pyproject.toml; resolves nothing",
    ),
    _t(
        "uv sync --locked --extra dev",
        "installs exactly uv.lock with the dev extra",
    ),
    _t(
        (
            "uv export --locked --no-default-groups --no-emit-project --format "
            "requirements.txt --output-file requirements-lock.txt"
        ),
        "exports uv.lock's runtime closure, hash-pinned",
        provides=("lock:requirements-lock.txt",),
    ),
    _t(
        (
            "uv export --locked --no-default-groups --no-emit-project --format "
            'requirements.txt --output-file "$RUNNER_TEMP/locked/requirements-lock.txt"'
        ),
        "exports uv.lock's runtime closure, hash-pinned",
        provides=("lock:$RUNNER_TEMP/locked/requirements-lock.txt",),
    ),
    _t(
        (
            "uv export --locked --only-group build-backend --no-emit-project --format "
            'requirements.txt --output-file "$RUNNER_TEMP/locked/build-backend-lock.txt"'
        ),
        "exports uv.lock's build-backend group, hash-pinned",
        provides=("lock:$RUNNER_TEMP/locked/build-backend-lock.txt",),
    ),
    _t(
        'mkdir -p "$RUNNER_TEMP/locked"',
        "makes the directory the exports go to, outside the checkout",
    ),
    _t(
        'LOCKED="$RUNNER_TEMP/locked"',
        _ASSIGN,
        provides=("var:LOCKED",),
    ),
    _t(
        "python -I -m venv @VENV@",
        "an empty venv from the runner's Python",
        provides=("venv:@VENV@",),
    ),
    _t(
        (
            "@VENV@/bin/pip install --only-binary :all: --require-hashes -r "
            '"$LOCKED/requirements-lock.txt"'
        ),
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", "var:LOCKED", "lock:$RUNNER_TEMP/locked/requirements-lock.txt"),
    ),
    _t(
        (
            "@VENV@/bin/pip install --only-binary :all: --require-hashes -r "
            '"$LOCKED/requirements-lock.txt" -r "$LOCKED/build-backend-lock.txt"'
        ),
        "installs the locks exported in this job, hash-checked",
        requires=(
            "venv:@VENV@",
            "var:LOCKED",
            "lock:$RUNNER_TEMP/locked/requirements-lock.txt",
            "lock:$RUNNER_TEMP/locked/build-backend-lock.txt",
        ),
        provides=("backend:@VENV@",),
    ),
    _t(
        (
            "@VENV@/bin/pip install --only-binary :all: --require-hashes -r "
            '"$RUNNER_TEMP/locked/requirements-lock.txt"'
        ),
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", "lock:$RUNNER_TEMP/locked/requirements-lock.txt"),
    ),
    _t(
        "@VENV@/bin/pip install --only-binary :all: --require-hashes -r requirements-lock.txt",
        "installs the lock exported in this job, hash-checked",
        requires=("venv:@VENV@", "lock:requirements-lock.txt"),
    ),
    _t(
        (
            "@VENV@/bin/pip install --only-binary :all: --require-hashes -r "
            'requirements-lock.txt -r "$RUNNER_TEMP/locked/build-backend-lock.txt"'
        ),
        "installs the locks exported in this job, hash-checked",
        requires=(
            "venv:@VENV@",
            "lock:requirements-lock.txt",
            "lock:$RUNNER_TEMP/locked/build-backend-lock.txt",
        ),
        provides=("backend:@VENV@",),
    ),
    _t(
        "@VENV@/bin/pip install --only-binary :all: --no-deps --no-index dist/gpo_studio-*.whl",
        "installs this job's built wheel alone, no index",
        requires=("venv:@VENV@", "dist"),
    ),
    _t(
        '@VENV@/bin/pip install --only-binary :all: --no-deps --no-index "$WHEEL"',
        "installs this job's built wheel alone, no index",
        requires=("venv:@VENV@", "dist", "var:WHEEL"),
    ),
    _t(
        (
            "@VENV@/bin/pip install --no-deps --no-index --no-build-isolation "
            "dist/gpo_studio-*.tar.gz"
        ),
        "builds this job's sdist with the locked backend already installed, no index",
        requires=("venv:@VENV@", "dist", "backend:@VENV@"),
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
    _t(
        ("uv run --locked --only-group release python -I -m build --no-isolation --outdir dist"),
        "builds with the locked backend, no isolation",
        provides=("dist",),
    ),
    _t(
        (
            "uv run --locked --only-group release python -I -m build --no-isolation "
            "--outdir dist-repro"
        ),
        "rebuilds for the reproducibility comparison",
        provides=("dist-repro",),
    ),
    _t(
        "uv run --locked --only-group release python -I -m build --no-isolation --wheel",
        "builds the wheel with the locked backend, no isolation",
        provides=("dist",),
    ),
    _t(
        "uv run --locked ruff check .",
        _LOCKED_RUN,
    ),
    _t(
        "uv run --locked mypy src",
        _LOCKED_RUN,
    ),
    _t(
        (
            "uv run --locked pytest -q --cov=src/gpo_studio --cov-branch "
            "--cov-report=json:coverage.json"
        ),
        _LOCKED_RUN,
    ),
    _t(
        "uv run --locked pytest tests/test_identifier_gate.py -q",
        _LOCKED_RUN,
    ),
    _t(
        "uv run --locked pytest tests/test_properties.py -q",
        _LOCKED_RUN,
    ),
    _t(
        "uv run --locked pytest tests/test_stored_type_fuzz.py -q",
        _LOCKED_RUN,
    ),
    _t(
        (
            "uv run --locked --only-group release pip-audit --strict --require-hashes "
            "--disable-pip --requirement "
            '"$RUNNER_TEMP/locked/requirements-all-platforms.txt"'
        ),
        "audits the exported lock without installing it (--disable-pip)",
        requires=(
            "lock:$RUNNER_TEMP/locked/requirements-lock.txt",
            "lock:$RUNNER_TEMP/locked/requirements-all-platforms.txt",
        ),
    ),
    _t(
        (
            "uv run --locked --only-group release pip-licenses --python "
            "/tmp/audit-venv/bin/python --allow-only 'MIT;MIT "
            "License;MIT-0;BSD-2-Clause;BSD-3-Clause;Apache-2.0 OR "
            "BSD-2-Clause;Apache-2.0 OR BSD-3-Clause;MPL-2.0;Mozilla Public License 2.0 "
            "(MPL 2.0);PSF-2.0'"
        ),
        "reads the licences of the venv built from the lock",
        requires=("venv:/tmp/audit-venv",),
    ),
    _t(
        (
            "uv run --locked --only-group release cyclonedx-py environment "
            "/tmp/sbom-venv/bin/python --pyproject pyproject.toml --output-reproducible "
            "--output-format JSON --output-file sbom.cdx.json"
        ),
        "reads the venv built from the lock into an SBOM",
        requires=("venv:/tmp/sbom-venv",),
    ),
    _t(
        "uv run --locked python -I scripts/check_coverage.py coverage.json",
        _LOCKED_SCRIPT,
    ),
    _t(
        (
            "uv run --locked python -I scripts/check_locked_build_backend.py --tmp-root "
            '"$RUNNER_TEMP/backend-check"'
        ),
        _LOCKED_SCRIPT,
    ),
    _t(
        "uv run --locked python -I scripts/check_safety.py",
        _LOCKED_SCRIPT,
    ),
    _t(
        (
            "uv run --locked python -I scripts/mutation_check_roundtrip.py --tmp-root "
            '"$RUNNER_TEMP/mutants"'
        ),
        _LOCKED_SCRIPT,
    ),
    _t(
        "uv run --locked python -I scripts/rehearse_upgrade_rollback.py",
        _LOCKED_SCRIPT,
    ),
    _t(
        (
            "python -I scripts/requirements_all_platforms.py "
            '"$RUNNER_TEMP/locked/requirements-lock.txt" '
            '"$RUNNER_TEMP/locked/requirements-all-platforms.txt"'
        ),
        "derives the audited copy from the lock exported in this job",
        requires=("lock:$RUNNER_TEMP/locked/requirements-lock.txt",),
        provides=("lock:$RUNNER_TEMP/locked/requirements-all-platforms.txt",),
    ),
    _t(
        (
            "python -I scripts/finalize_sbom.py sbom.cdx.json --repository "
            '"$GITHUB_REPOSITORY" --source-commit "$GITHUB_SHA"'
        ),
        _SCRIPT,
    ),
    _t(
        "python -I scripts/check_sbom_shape.py sbom.cdx.json",
        _SCRIPT,
    ),
    _t(
        "python -I scripts/record_release_identity.py",
        _SCRIPT,
    ),
    _t(
        (
            'python -I scripts/check_release_manifest.py --tag "${GITHUB_REF_NAME}" '
            '--github-output "${GITHUB_OUTPUT}"'
        ),
        _SCRIPT,
    ),
    _t(
        (
            'python -I scripts/check_release_manifest.py --tag "${GITHUB_REF_NAME}" '
            '--github-output "${GITHUB_OUTPUT}" --remote-tag-sha "${GITHUB_SHA}"'
        ),
        _SCRIPT,
    ),
    _t(
        (
            'python -I scripts/check_release_manifest.py --tag "${GITHUB_REF_NAME}" '
            '--remote-tag-sha "${GITHUB_SHA}"'
        ),
        _SCRIPT,
    ),
    _t(
        (
            'python -I scripts/check_release_manifest.py --tag "${GITHUB_REF_NAME}" --wheel '
            '"${WHEELS[0]}" --sdist "${SDISTS[0]}"'
        ),
        _SCRIPT,
    ),
    _t(
        "python3 -I scripts/check_committed_identifiers.py --strict",
        _SCRIPT,
    ),
    _t(
        "python3 -I tests/test_identifier_gate_visibility.py",
        _SCRIPT,
    ),
    _t(
        "@VENV@/bin/python -I scripts/print_installed_version.py",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/python -I scripts/print_installed_version.py --metadata",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/python -I scripts/check_health_version.py /tmp/health.json",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/python -I scripts/rehearse_upgrade_rollback.py",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/gpo-studio --help",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        "@VENV@/bin/gpo-studio run --database /tmp/smoke.db --port 8799",
        _VENV_RUN,
        requires=("venv:@VENV@",),
    ),
    _t(
        ("VENV_VERSION=$(/tmp/smoke-venv/bin/python -I scripts/print_installed_version.py)"),
        _ASSIGN,
    ),
    _t(
        (
            "META_VERSION=$(/tmp/smoke-venv/bin/python "
            "-I scripts/print_installed_version.py --metadata)"
        ),
        _ASSIGN,
    ),
    _t(
        ("SDIST_VERSION=$(/tmp/sdist-venv/bin/python -I scripts/print_installed_version.py)"),
        _ASSIGN,
    ),
    _t(
        "for artifact in dist/*",
        _SHELL,
        requires=("dist",),
    ),
    _t(
        'basename "$artifact"',
        _READ,
    ),
    _t(
        'cmp "$artifact" "dist-repro/$(basename "$artifact")"',
        _READ,
        requires=("dist-repro",),
    ),
    _t(
        "WHEEL=$(find dist -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)",
        "binds WHEEL to the wheel built in this job",
        requires=("dist",),
        provides=("var:WHEEL",),
    ),
    _t(
        "find dist -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit",
        _READ,
    ),
    _t(
        "find dist -maxdepth 1 -name 'gpo_studio-*.whl'",
        _READ,
    ),
    _t(
        "find dist -maxdepth 1 -name 'gpo_studio-*.tar.gz'",
        _READ,
    ),
    _t(
        "mapfile -t WHEELS <(find dist -maxdepth 1 -name 'gpo_studio-*.whl')",
        _READ,
    ),
    _t(
        "mapfile -t SDISTS <(find dist -maxdepth 1 -name 'gpo_studio-*.tar.gz')",
        _READ,
    ),
    _t(
        'test "${#WHEELS[@]}" -eq 1',
        _SHELL,
    ),
    _t(
        'test "${#SDISTS[@]}" -eq 1',
        _SHELL,
    ),
    _t(
        "SDIST=$(ls dist/gpo_studio-*.tar.gz)",
        _ASSIGN,
    ),
    _t(
        "ls dist/gpo_studio-*.tar.gz",
        _READ,
    ),
    _t(
        'tar tzf "$SDIST"',
        _READ,
    ),
    _t(
        (
            "grep -qE "
            "'\\.db$|\\.sqlite3$|\\.venv/|node_modules/|\\.claude/|githooks/|"
            "reflections/|exports/|playwright-report/|test-results/'"
        ),
        _READ,
    ),
    _t(
        (
            "grep -E "
            "'\\.db$|\\.sqlite3$|\\.venv/|node_modules/|\\.claude/|githooks/|"
            "reflections/|exports/|playwright-report/|test-results/'"
        ),
        _READ,
    ),
    _t(
        (
            "for required in README.md LICENSE pyproject.toml AGENTS.md SECURITY.md "
            "CHANGELOG.md CONTRIBUTING.md"
        ),
        _SHELL,
    ),
    _t(
        'grep -q "/$required$"',
        _READ,
    ),
    _t(
        (
            'PYTHON=python GPO_STUDIO_WHEEL="$WHEEL" '
            "GPO_STUDIO_REQUIREMENTS=requirements-lock.txt bash "
            "scripts/installed_package_smoke.sh"
        ),
        "the scanned smoke script, on this job's wheel and exported lock",
        requires=("dist", "var:WHEEL", "lock:requirements-lock.txt"),
    ),
    _t(
        "SERVER_PID=$!",
        _ASSIGN,
        scope="smoke workflow",
    ),
    _t(
        "trap 'kill \"$SERVER_PID\" 2>/dev/null || true' EXIT",
        "stops the server on exit",
    ),
    _t(
        "trap - EXIT",
        _SHELL,
    ),
    _t(
        "for attempt in $(seq 1 20)",
        _SHELL,
    ),
    _t(
        "seq 1 20",
        _SHELL,
    ),
    _t(
        "curl -sf http://127.0.0.1:8799/api/health",
        _LOCAL,
        writes=("/tmp/health.json",),
    ),
    _t(
        "curl -sf http://127.0.0.1:8799/api/gpos",
        _LOCAL,
    ),
    _t(
        "curl -sf http://127.0.0.1:8799/",
        _LOCAL,
    ),
    _t(
        (
            'curl -sf -X POST http://127.0.0.1:8799/api/gpos -H "Content-Type: '
            'application/json" -d \'{"name":"Smoke '
            'Test","domain":"test.local","actor":"ci","reason":"installed-package smoke '
            "test\"}'"
        ),
        _LOCAL,
    ),
    _t(
        "kill $SERVER_PID",
        _SHELL,
    ),
    _t(
        "wait $SERVER_PID",
        _SHELL,
    ),
    _t(
        "sleep 0.25",
        _SHELL,
    ),
    _t(
        "break",
        _SHELL,
    ),
    _t(
        "true",
        _SHELL,
    ),
    _t(
        "[ ! -s sbom.cdx.json ]",
        _SHELL,
    ),
    _t(
        "stat -c%s sbom.cdx.json",
        _READ,
    ),
    _t(
        (
            "sha256sum dist/* requirements-lock.txt sbom.cdx.json release-evidence.md "
            "release-evidence-report.json"
        ),
        "checksums the release files into SHA256SUMS",
        writes=("SHA256SUMS",),
    ),
    _t(
        'cp "${EVIDENCE_MANIFEST}" release-evidence.md',
        "copies the gate-approved manifest",
    ),
    _t(
        'cp "${EVIDENCE_REPORT}" release-evidence-report.json',
        "copies the gate-approved report",
    ),
    _t(
        'grep -qxF "manifest=${EXPECTED_MANIFEST}" "${GITHUB_OUTPUT}"',
        _READ,
    ),
    _t(
        'grep -qxF "report=${EXPECTED_REPORT}" "${GITHUB_OUTPUT}"',
        _READ,
    ),
    _t(
        "git fetch --no-tags origin main",
        "fetches main from the checkout's own remote",
    ),
    _t(
        'git merge-base --is-ancestor "${GITHUB_SHA}" origin/main',
        _READ,
    ),
    _t(
        "FLAGS=()",
        _ASSIGN,
    ),
    _t(
        "FLAGS+=(--prerelease)",
        _ASSIGN,
    ),
    _t(
        "NOTES_ARGS=(--generate-notes)",
        _ASSIGN,
    ),
    _t(
        (
            'NOTES_ARGS=(--notes "Immutable release candidate for hands-on validation. '
            "This prerelease is not approved for production or as the final "
            "${GITHUB_REF_NAME%-rc.*} release. Download the wheel, requirements-lock.txt "
            'and SHA256SUMS from Assets and follow docs/nvda-validation-runbook.md.")'
        ),
        _ASSIGN,
    ),
    _t(
        (
            'gh release create "$GITHUB_REF_NAME" dist/* requirements-lock.txt '
            "sbom.cdx.json SHA256SUMS release-evidence.md release-evidence-report.json "
            '--verify-tag "${NOTES_ARGS[@]}" --title "GPO Studio ${GITHUB_REF_NAME#v}" '
            '"${FLAGS[@]}"'
        ),
        "publishes the attested files",
        requires=("dist", "lock:requirements-lock.txt"),
    ),
    _t(
        "set -euo pipefail",
        _SHELL,
        scope="smoke workflow",
    ),
    _t(
        "exit 1",
        _SHELL,
        scope="smoke workflow",
    ),
    _t(
        "echo @TEXT*@",
        _ECHO,
        scope="smoke workflow",
    ),
    _t(
        'echo "SBOM generated: $(stat -c%s sbom.cdx.json) bytes"',
        _ECHO,
    ),
    _t(
        '[ "$VENV_VERSION" != "$META_VERSION" ]',
        _SHELL,
        scope="smoke workflow",
    ),
    _t(
        'test "$VENV_VERSION" = "$SDIST_VERSION"',
        _SHELL,
    ),
    _t(
        "npm ci --ignore-scripts",
        "installs exactly package-lock.json, running no package's install scripts",
        provides=("npm-ci",),
        action="npm-ci",
    ),
    _t(
        "npm audit --audit-level=high",
        "reports advisories; installs nothing",
    ),
    _t(
        "node_modules/.bin/playwright install --with-deps chromium firefox",
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
        (
            "node_modules/.bin/prettier --check eslint.config.mjs playwright.config.mjs "
            "vitest.config.mjs tests/browser tests/frontend"
        ),
        "the locked tool by its own path: no PATH fallback if node_modules is gone",
        scope="npm",
    ),
    _t(
        (
            "node_modules/.bin/eslint -- src/gpo_studio/static/js tests/browser tests/frontend "
            "eslint.config.mjs playwright.config.mjs vitest.config.mjs"
        ),
        "the locked tool by its own path: no PATH fallback if node_modules is gone",
        scope="npm",
    ),
    _t(
        "node_modules/.bin/vitest run",
        "the locked unit-test runner, by its own path",
        scope="npm",
    ),
    _t(
        "node_modules/.bin/playwright test",
        "the locked browser-test runner, by its own path",
        scope="npm",
    ),
    _t(
        "[ ! -f scripts/print_installed_version.py ]",
        _SHELL,
        scope="smoke",
    ),
    _t(
        "TMPDIR=$(mktemp -d)",
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        "mktemp -d",
        "makes the script's scratch directory",
        scope="smoke",
    ),
    _t(
        'SERVER_PID=""',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        'trap \'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMPDIR"\' EXIT',
        "stops the server and removes the scratch directory on exit",
        scope="smoke",
    ),
    _t(
        "PYTHON=${PYTHON:-python3.13}",
        "the caller's interpreter (documented trust)",
        scope="smoke",
    ),
    _t(
        "PORT=8799",
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        'DB="$TMPDIR/smoke.db"',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        '[ -n "${GPO_STUDIO_WHEEL:-}" ]',
        _SHELL,
        scope="smoke",
    ),
    _t(
        'WHEEL=$(realpath "$GPO_STUDIO_WHEEL")',
        "the caller's wheel (documented trust)",
        scope="smoke",
    ),
    _t(
        'realpath "$GPO_STUDIO_WHEEL"',
        _READ,
        scope="smoke",
    ),
    _t(
        "command -v uv",
        "looks uv up; runs nothing",
        scope="smoke",
    ),
    _t(
        ('UV_PROJECT_ENVIRONMENT="$TMPDIR/build-env" uv sync --locked --only-group build-backend'),
        "installs uv.lock's build-backend group into a throwaway environment",
        scope="smoke",
        provides=("build-env",),
    ),
    _t(
        ('UV_PROJECT_ENVIRONMENT="$TMPDIR/build-env" uv build --wheel --out-dir "$TMPDIR/dist"'),
        "builds in that locked environment, without isolation ([tool.uv])",
        scope="smoke",
        requires=("build-env",),
    ),
    _t(
        ("WHEEL=$(find \"$TMPDIR/dist\" -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)"),
        "binds WHEEL to the wheel just built",
        scope="smoke",
    ),
    _t(
        "find \"$TMPDIR/dist\" -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit",
        _READ,
        scope="smoke",
    ),
    _t(
        '[ -n "${GPO_STUDIO_REQUIREMENTS:-}" ]',
        _SHELL,
        scope="smoke",
    ),
    _t(
        'REQUIREMENTS=$(realpath "$GPO_STUDIO_REQUIREMENTS")',
        "the caller's exported lock (documented trust)",
        scope="smoke",
    ),
    _t(
        'realpath "$GPO_STUDIO_REQUIREMENTS"',
        _READ,
        scope="smoke",
    ),
    _t(
        'REQUIREMENTS="$TMPDIR/requirements-lock.txt"',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            "uv export --locked --no-default-groups --no-emit-project --format "
            'requirements.txt --output-file "$REQUIREMENTS"'
        ),
        "exports uv.lock's runtime closure, hash-pinned",
        scope="smoke",
    ),
    _t(
        '"$PYTHON" -I -m venv "$TMPDIR/venv" --clear',
        "an empty venv from the caller's interpreter",
        scope="smoke",
        provides=("venv",),
    ),
    _t(
        '"$TMPDIR/venv/bin/pip" install --only-binary :all: --require-hashes -r "$REQUIREMENTS"',
        "installs the exported (or handed-off) lock, hash-checked",
        scope="smoke",
        requires=("venv",),
    ),
    _t(
        '"$TMPDIR/venv/bin/pip" install --only-binary :all: --no-deps --no-index "$WHEEL"',
        "installs the wheel alone, no index",
        scope="smoke",
        requires=("venv",),
    ),
    _t(
        '"$TMPDIR/venv/bin/pip" check',
        "verifies the installed set",
        scope="smoke",
        requires=("venv",),
    ),
    _t(
        '"$TMPDIR/venv/bin/python" -I scripts/print_installed_version.py',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/python" -I scripts/print_installed_version.py --metadata',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/python" -I scripts/read_json_field.py version',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/python" -I scripts/read_json_field.py gpo.guid',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        'VENV_VERSION=$("$TMPDIR/venv/bin/python" -I scripts/print_installed_version.py)',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            'META_VERSION=$("$TMPDIR/venv/bin/python" -I '
            "scripts/print_installed_version.py --metadata)"
        ),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/gpo-studio" --help',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        "head -1",
        _READ,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/gpo-studio" run --database "$DB" --port "$PORT"',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        '"$TMPDIR/venv/bin/gpo-studio" workspace check --database "$DB"',
        _VENV_RUN,
        scope="smoke",
    ),
    _t(
        "sleep 2",
        _SHELL,
        scope="smoke",
    ),
    _t(
        'curl -sf "http://127.0.0.1:$PORT/api/health"',
        _LOCAL,
        scope="smoke",
    ),
    _t(
        'HEALTH=$(curl -sf "http://127.0.0.1:$PORT/api/health")',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            'HEALTH_VERSION=$(echo "$HEALTH" | "$TMPDIR/venv/bin/python" '
            "-I scripts/read_json_field.py version)"
        ),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        '[ "$HEALTH_VERSION" != "$VENV_VERSION" ]',
        _SHELL,
        scope="smoke",
    ),
    _t(
        (
            'curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" -H "Content-Type: '
            'application/json" -d \'{"name":"Smoke Test '
            'GPO","domain":"test.local","actor":"smoke","reason":"installed-package '
            "smoke test\"}'"
        ),
        _LOCAL,
        scope="smoke",
    ),
    _t(
        (
            'RESPONSE=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" -H '
            '"Content-Type: application/json" -d \'{"name":"Smoke Test '
            'GPO","domain":"test.local","actor":"smoke","reason":"installed-package '
            "smoke test\"}')"
        ),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            'GPO_GUID=$(echo "$RESPONSE" | "$TMPDIR/venv/bin/python" '
            "-I scripts/read_json_field.py gpo.guid)"
        ),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        (
            'curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/settings" -H '
            '"Content-Type: application/json" -d '
            '"{\\"setting\\":{\\"side\\":\\"computer\\",\\"hive\\":\\"HKLM\\",\\"key\\":\\"Softw'
            'are\\\\\\\\Test\\",\\"value_name\\":\\"Setting1\\",\\"registry_type\\":\\"REG_DWOR'
            'D\\",\\"value\\":\\"1\\"},\\"actor\\":\\"smoke\\",\\"reason\\":\\"add '
            'test setting\\",\\"expected_revision\\":1}"'
        ),
        _LOCAL,
        scope="smoke",
    ),
    _t(
        ('curl -sf -o "$TMPDIR/export.zip" "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/export.zip"'),
        _LOCAL,
        scope="smoke",
    ),
    _t(
        'ZIP_SIZE=$(stat -c%s "$TMPDIR/export.zip")',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        'stat -c%s "$TMPDIR/export.zip"',
        _READ,
        scope="smoke",
    ),
    _t(
        '[ "$ZIP_SIZE" -lt 500 ]',
        _SHELL,
        scope="smoke",
    ),
    _t(
        'PLAN=$(curl -sf "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/plan.ps1")',
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        'curl -sf "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/plan.ps1"',
        _LOCAL,
        scope="smoke",
    ),
    _t(
        'grep -q "Set-GPRegistryValue"',
        _READ,
        scope="smoke",
    ),
    _t(
        ('UI_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/")'),
        _ASSIGN,
        scope="smoke",
    ),
    _t(
        'curl -sf -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/"',
        _LOCAL,
        scope="smoke",
    ),
    _t(
        '[ "$UI_STATUS" != "200" ]',
        _SHELL,
        scope="smoke",
    ),
    _t(
        'kill "$SERVER_PID"',
        _SHELL,
        scope="smoke",
    ),
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
    # The exhaustive stored-type fuzz (ci.yml `type-fuzz-full`).
    "GPO_STUDIO_FULL_FUZZ": {"1"},
    "GPO_STUDIO_TEST_PYTHON": {"${{ github.workspace }}/.venv/bin/python"},
    "SOURCE_DATE_EPOCH": {"315532800"},
}

#: Every PowerShell step body, and every step of a Windows job (whose default shell
#: is PowerShell), must be exactly one of these. Exactly, not case- or space-folded:
#: folding would accept spellings that PowerShell parses differently (a newline and
#: a space separate statements differently), and an exact match refuses every case
#: variant of anything that is not on the list.
PWSH_BODIES: dict[str, str] = {
    # The only package from outside the two locks, pinned by its SHA-256 instead.
    (
        "$ErrorActionPreference = 'Stop'\n"
        "$root = Join-Path $env:RUNNER_TEMP 'psscriptanalyzer'\n"
        "New-Item -ItemType Directory -Force -Path $root | Out-Null\n"
        "$package = Join-Path $root 'PSScriptAnalyzer.1.25.0.zip'\n"
        "Invoke-WebRequest -Uri 'https://www.powershellgallery.com/ap"
        "i/v2/package/PSScriptAnalyzer/1.25.0' -OutFile $package\n"
        "$expected = '14E634C828EB98EFB9F40B2918BA90F139ED5ECCDF663A2A747736D996995D60'\n"
        "$actual = (Get-FileHash -Path $package -Algorithm SHA256).Hash\n"
        'if ($actual -ne $expected) { throw "PSScriptAnalyzer package'
        ' SHA-256 is $actual, not $expected" }\n'
        "Expand-Archive -Path $package -DestinationPath (Join-Path $root 'module')\n"
    ): "fetches PSScriptAnalyzer 1.25.0 and verifies its SHA-256; installs nothing",
    (
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
        '    $errors | ForEach-Object { Write-Output "  line $($_.Ext'
        'ent.StartLineNumber): $($_.Message)" }\n'
        "  }\n"
        "}\n"
        "if ($failed) { exit 1 }\n"
        'Write-Output "All scripts parse."\n'
    ): "parses every .ps1; installs nothing",
    (
        "$ErrorActionPreference = 'Stop'\n"
        "Import-Module (Join-Path $env:RUNNER_TEMP 'psscriptanalyzer/"
        "module/PSScriptAnalyzer.psd1') -Force\n"
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
        '    Write-Output "::error file=$($_.ScriptName),line=$($_.Li'
        'ne)::[$($_.RuleName)] $($_.Message)"\n'
        "  }\n"
        '  Write-Output "$($findings.Count) finding(s)."\n'
        "  exit 1\n"
        "}\n"
        'Write-Output "PSScriptAnalyzer: no findings."\n'
    ): "imports the verified analyzer and runs it",
    # The Windows test job: the same locked commands as the Linux job.
    "uv sync --locked --extra dev": "installs exactly uv.lock with the dev extra",
    "uv run --locked ruff check .": _LOCKED_RUN,
    "uv run --locked mypy src": _LOCKED_RUN,
    (
        "uv run --locked pytest -q --cov=src/gpo_studio --cov-branch"
        " --cov-report=json:coverage.json"
    ): _LOCKED_RUN,
    "uv run --locked python -I scripts/check_coverage.py coverage.json": _LOCKED_SCRIPT,
}


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
    #: Facts established earlier in this job (a lock exported, a venv built...), each
    #: with the block it was established in: it holds only inside that block.
    facts: list[tuple[str, tuple[int, ...]]] = field(default_factory=list)
    #: Facts that end with the step: shell variables, and anything established inside
    #: a block (the block numbers are the step's own).
    step_facts: list[tuple[str, tuple[int, ...]]] = field(default_factory=list)
    #: The step has an ``if:``; it may not run, so it establishes nothing.
    conditional_step: bool = False
    npm_stack: tuple[str, ...] = ()

    def holds(self, fact: str, block: tuple[int, ...]) -> bool:
        return any(
            name == fact and block[: len(where)] == where
            for name, where in (*self.facts, *self.step_facts)
        )

    def establish(self, fact: str, command: Command) -> None:
        """Only a command whose failure fails the job proves its effect happened."""
        if command.conditional or self.conditional_step:
            return
        local = fact.startswith("var:") or command.block
        (self.step_facts if local else self.facts).append((fact, command.block))

    def invalidate(self) -> None:
        """An action changed the workspace or /tmp: nothing established so far holds."""
        self.facts = []
        self.step_facts = []

    def end_step(self) -> None:
        self.step_facts = []
        self.conditional_step = False

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
            if all(ctx.holds(_fill(fact, captures), command.block) for fact in template.requires)
        ),
        None,
    )
    if chosen is None:
        template, captures = matches[0]
        missing = [
            _fill(fact, captures)
            for fact in template.requires
            if not ctx.holds(_fill(fact, captures), command.block)
        ]
        yield (
            f"{where}: {shown!r} needs {missing} established earlier in this job by a"
            " command whose failure would have failed it"
        )
        return
    template, captures = chosen
    for position, token in enumerate(template.tokens):
        if position < len(tokens) and "@TEXT" in token:
            for word in tokens[position:]:
                if _GLOB.search(_unquoted(word)) or option_risk(word, reviewed=False):
                    yield f"{where}: echo text may hold no unquoted glob or expansion: {word!r}"
    if command.words[:1] in (["npm"], ["npx"]):
        # Every npm invocation reads the project's npm config, whatever the template
        # (DeepSeek r12 N3: `npm audit` had no action, so no check).
        yield from _check_npm_config(ctx, where)
    for target in command.writes:
        if target != "/dev/null" and target not in template.writes:
            yield f"{where}: {shown!r} may not write {target!r}"
    for target in command.reads:
        yield f"{where}: {shown!r} may not read a redirected {target!r}"
    for fact in template.provides:
        ctx.establish(_fill(fact, captures), command)
    if template.action == "npm-ci":
        yield from _check_npm_ci(ctx, where)
    elif template.action == "npm-run":
        yield from _check_npm_script(captures["NPM_SCRIPT"], ctx, where)


def _check_npm_config(ctx: Context, where: str) -> Iterator[str]:
    """A project .npmrc can replace the shell every script runs in (``script-shell``)."""
    for name in (".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs"):
        if (ctx.root / name).exists():
            yield f"{where}: {name} configures npm itself, outside these templates"


#: The programs the npm templates run (node_modules/.bin on PATH, or by that path),
#: and the package each must come from: pinned exactly in package.json, present
#: in package-lock.json with that version, and providing that binary. Otherwise a
#: script's `vitest` or npx's `playwright` resolves from the runner or the registry.
LOCKED_BINS = {
    "eslint": "eslint",
    "playwright": "@playwright/test",
    "prettier": "prettier",
    "vitest": "vitest",
}


#: Lock entries whose packages carry install scripts (``hasInstallScript``). CI runs
#: ``npm ci --ignore-scripts``, so none runs; each is listed so a new one is seen.
REVIEWED_INSTALL_SCRIPTS = {
    # fsevents: macOS-only and optional, so not even installed on the Linux runners.
    "node_modules/fsevents",
    "node_modules/vite/node_modules/fsevents",
}


def npm_lock_violations(package_json: Path, lock_path: Path) -> list[str]:
    """package-lock.json hashes every package and locks every program a template runs."""
    if not lock_path.is_file():
        return [f"{lock_path.name}: missing, so npm ci would resolve from package.json"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package = json.loads(package_json.read_text(encoding="utf-8"))
    packages: dict[str, Any] = lock.get("packages") or {}
    found: list[str] = []
    for path, entry in packages.items():
        if not path:
            continue
        if entry.get("link"):
            found.append(f"{lock_path.name}: {path} links a directory, not a hashed package")
            continue
        resolved = str(entry.get("resolved", ""))
        if not resolved.startswith("https://registry.npmjs.org/"):
            found.append(f"{lock_path.name}: {path} is not resolved from the npm registry")
        if not str(entry.get("integrity", "")).startswith("sha512-"):
            found.append(f"{lock_path.name}: {path} has no sha512 integrity, so nothing pins it")
    for path, entry in packages.items():
        if entry.get("hasInstallScript") and path not in REVIEWED_INSTALL_SCRIPTS:
            found.append(f"{lock_path.name}: {path} has install scripts nobody reviewed")
        for program, target in (entry.get("bin") or {}).items():
            if ".." in str(target).split("/") or str(target).startswith("/"):
                found.append(f"{lock_path.name}: {path} links {program} outside its package")
    declared = package.get("devDependencies") or {}
    for name, spec in sorted(declared.items()):
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(spec)):
            found.append(f"package.json: {name} {spec!r} is not one exact registry version")
    for program, name in sorted(LOCKED_BINS.items()):
        entry = packages.get(f"node_modules/{name}") or {}
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(declared.get(name, ""))):
            found.append(f"package.json: {name} (for {program}) is not pinned to one version")
        elif entry.get("version") != declared[name]:
            found.append(f"{lock_path.name}: {name} {declared[name]} is not the locked version")
        if program not in (entry.get("bin") or {}):
            found.append(f"{lock_path.name}: no locked package provides {program}")
    return found


def _check_npm_ci(ctx: Context, where: str) -> Iterator[str]:
    package_json = ctx.package_json or ctx.root / "package.json"
    for problem in npm_lock_violations(package_json, package_json.parent / "package-lock.json"):
        yield f"{where}: {problem}"
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
            ctx.conditional_step,
            (*ctx.npm_stack, name),
        )
        try:
            yield from violations(split_commands(body), where=here, ctx=inner)
        except GrammarError as error:
            yield f"{here}: {error}"


# --- the files that configure those tools ---------------------------------------


#: What each templated tool discovers on its own, beyond its arguments, and how
#: that input is closed. A tracked file can change what an allowed tool loads or
#: runs in three ways; each is neutralised in the template or pinned here:
#:
#: * module shadowing: Python puts the script's directory (or the working
#:   directory, for ``-m``) first on sys.path. Every Python template runs with
#:   ``-I``, and no tracked module at the top of the checkout, scripts/ or tests/
#:   may shadow the standard library or a tool the templates run (SHADOWED); no
#:   compiled ``.pyc`` may be tracked either (an unchecked-hash pyc runs in place
#:   of its source).
#: * configuration discovery: pip, uv, npm, git, curl, pytest, mypy, coverage,
#:   hatchling, prettier, eslint, vitest/vite, Playwright and PSScriptAnalyzer each
#:   look for config files by name (TOOL_DISCOVERY, read from each locked
#:   version's source) or in pyproject.toml and package.json. A tracked match must
#:   be reviewed here by path and SHA-256 (REVIEWED_CONFIG); the pyproject tables
#:   and the project's entry points are pinned whole (REVIEWED_TOOL), and no
#:   ``pytest_plugins`` may name a module to load as a plugin.
#: * plugins and rule paths: prettier/eslint/vitest/Playwright configs and the
#:   PSScriptAnalyzer settings can load modules; their content is pinned, and the
#:   analyzer settings may name no custom rule path either.
#:
#: Environment variables that would do the same are closed by ENV, and the npm
#: programs must come from package-lock.json (LOCKED_BINS). Tests, scripts/ and
#: these pinned configs are code the tools exist to run; they are reviewed as code.
def _variants(stem: str, extensions: str) -> list[str]:
    return [f"{stem}.{extension}" for extension in extensions.split()]


_JS = "js mjs cjs ts mts cts"
#: Each tool the templates run, the version whose source this list was read from,
#: and every file that version finds on its own (in the working directory, a
#: parent, or beside the file it is working on). ``test_the_discovery_inventory_
#: matches_the_locked_tools`` fails when a locked version moves, so an upgrade
#: re-reviews the list; where the tool is installed it also reads the names back
#: out of the tool's own source. ``None``: not in a lock (runner-provided).
TOOL_DISCOVERY: dict[str, tuple[str | None, list[str]]] = {
    # _pytest/config/findpaths.py locate_config(); conftest.py beside any test.
    "pytest": (
        "9.1.1",
        [
            "pytest.toml",
            ".pytest.toml",
            "pytest.ini",
            ".pytest.ini",
            "pyproject.toml",
            "tox.ini",
            "setup.cfg",
            "conftest.py",
        ],
    ),
    # coverage/config.py config_files_to_try(), loaded by pytest-cov.
    "coverage": (
        "7.15.2",
        [".coveragerc", ".coveragerc.toml", "setup.cfg", "tox.ini", "pyproject.toml"],
    ),
    "pytest-cov": ("7.1.0", []),
    # mypy/defaults.py CONFIG_NAMES + SHARED_CONFIG_NAMES.
    "mypy": ("1.20.2", ["mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg"]),
    # ruff loads no code; listed so a change is still seen.
    "ruff": ("0.16.0", ["ruff.toml", ".ruff.toml", "pyproject.toml"]),
    # hatchling/utils/constants.py DEFAULT_CONFIG_FILE, DEFAULT_BUILD_SCRIPT.
    "hatchling": ("1.32.4", ["pyproject.toml", "hatch.toml", "hatch_build.py"]),
    "build": ("1.6.1", ["pyproject.toml"]),
    "pip-audit": ("2.10.1", []),
    "pip-licenses": ("5.5.5", []),
    "cyclonedx-bom": ("7.5.0", ["pyproject.toml"]),
    # uv's project and user configuration, and its interpreter requests.
    "uv": (
        "0.12.0",
        ["uv.toml", "pyproject.toml", ".python-version", ".python-versions", "uv.lock"],
    ),
    # lib/config/config-loader.js FLAT_CONFIG_FILENAMES.
    "eslint": ("10.7.0", _variants("eslint.config", _JS)),
    # prettier's config searcher; .editorconfig feeds its options.
    "prettier": (
        "3.9.5",
        [
            "package.json",
            "package.yaml",
            ".prettierrc",
            ".editorconfig",
            *_variants(".prettierrc", "json json5 yaml yml toml " + _JS),
            *_variants("prettier.config", _JS),
        ],
    ),
    # vitest CONFIG_NAMES x CONFIG_EXTENSIONS.
    "vitest": ("4.1.11", [*_variants("vitest.config", _JS), *_variants("vite.config", _JS)]),
    # vite DEFAULT_CONFIG_FILES, its .env files, postcss-load-config's search places.
    "vite": (
        "8.3.1",
        [
            *_variants("vite.config", _JS),
            ".env",
            ".env.local",
            ".env.test",
            ".env.test.local",
            ".postcssrc",
            *_variants(".postcssrc", "json yaml yml " + _JS),
            *_variants("postcss.config", _JS),
        ],
    ),
    # playwright/lib/common: playwright.config + each extension it tries.
    # playwright/lib/common: playwright.config + each extension it tries, and the
    # tsconfig.json / jsconfig.json it applies (paths, baseUrl) to every file it loads.
    "@playwright/test": (
        "1.61.1",
        [
            *_variants("playwright.config", "ts js mts mjs cts cjs"),
            "tsconfig.json",
            "jsconfig.json",
        ],
    ),
    # Runner-provided: npm (with Node 24), pip (ensurepip), git, curl, CPython.
    "npm": (None, [".npmrc", "npm-shrinkwrap.json", "package-lock.json", "package.json"]),
    "pip": (None, ["pip.conf", "pip.ini"]),
    "git": (None, [".gitmodules", ".gitconfig"]),
    "curl": (None, [".curlrc", "_curlrc"]),
    "python": (None, ["sitecustomize.py", "usercustomize.py", "setup.py", ".pydistutils.cfg"]),
    "PSScriptAnalyzer": ("1.25.0", ["PSScriptAnalyzerSettings.psd1"]),
}
#: Discovered by other tools a contributor might add later, refused until reviewed.
_ALSO_REFUSED = {
    ".wgetrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    ".node-version",
    ".nvmrc",
    ".tool-versions",
    ".pypirc",
    ".eslintrc",
    ".babelrc",
    ".browserslistrc",
    "browserslist",
    ".swcrc",
    ".pnp.cjs",
    ".pnp.js",
    ".pnp.loader.mjs",
}
DISCOVERED = {name for _version, names in TOOL_DISCOVERY.values() for name in names}
#: The three files every check above already pins by content at the root; elsewhere
#: they are refused like any other discovered file (pytest and prettier look up
#: from the file they are working on, so a nested one would win).
_PINNED_AT_ROOT = {"pyproject.toml", "package.json", "package-lock.json", "uv.lock"}
CONFIG_PATTERNS = re.compile(
    r"^(\.env\..*|\.eslintrc\..*|\.babelrc\..*|babel\.config\..*|tsconfig\..*\.json"
    r"|jsconfig\..*\.json|.*\.pth)$",
    re.IGNORECASE,
)


#: path -> SHA-256 of each reviewed configuration file the tools discover today.
REVIEWED_CONFIG: dict[str, str] = {
    ".prettierrc.json": "7752dba7407ce87e2bc87866f69a8f41d38b9d6ae30e7ebcf04bbbebdf9458d5",
    "PSScriptAnalyzerSettings.psd1": (
        "0dcee3b3aa0c2b11c3aba45d1d897a3f712ca00603b02a4d72764a3e77cbe069"
    ),
    "eslint.config.mjs": "aeab775a04d5c68c037f474ad1dea8db5a629bef1300fd38b32c22673f15daa9",
    "playwright.config.mjs": "6bb4b13ed0a761e11af43831b3dde1977991034eaf767a833a8118b8330b6640",
    "vitest.config.mjs": "656c53e8b1c94f0efee345cebfb1fd7ec4ab46523bc365430a79517663f717d3",
}
#: Keys that make PSScriptAnalyzer import and run modules named by its settings.
_ANALYZER_CODE_KEYS = re.compile(r"CustomRulePath|RecurseCustomRulePath|IncludeDefaultRules", re.I)
#: Trees a checkout must not carry: their contents would stand in for what a
#: template builds or installs (a node_modules/.bin shim, a prebuilt dist/).
UNTRACKED_TREES = ("node_modules", ".venv", "dist", "dist-repro", ".git", ".yarn")
#: Files a template writes at the root: a tracked one would be read where the job
#: expects its own output (an export, a checksum list, an SBOM).
WRITTEN_AT_ROOT = {
    "requirements-lock.txt",
    "SHA256SUMS",
    "sbom.cdx.json",
    "release-evidence.md",
    "release-evidence-report.json",
    "coverage.json",
}
#: Tracked symlinks (git mode 120000) reviewed by path and target. None: a symlink
#: can make any path the checks above approve (`.venv`, `scripts`) mean another.
REVIEWED_SYMLINKS: dict[str, str] = {}
#: Module names a tracked file must not provide where Python could find it first.
SHADOWED = set(sys.stdlib_module_names) | {
    "_pytest",
    "build",
    "cyclonedx",
    "cyclonedx_py",
    "editables",
    "gpo_studio",
    "hatchling",
    "mypy",
    "packaging",
    "pip",
    "pip_audit",
    "piplicenses",
    "pluggy",
    "pyproject_hooks",
    "pytest",
    "ruff",
    "setuptools",
    "sitecustomize",
    "usercustomize",
}
_SHADOW_DIRS = ("", "scripts/", "tests/")
#: pyproject.toml's tool tables and [build-system], exactly as reviewed. ruff's is
#: free: ruff loads no code. Any other [tool.*] table is refused.
REVIEWED_TOOL: dict[str, Any] = {
    # "uv" is checked by _tool_uv_violations: its no-build-package must name exactly
    # the locked dependencies, which a literal here would duplicate.
    "hatch": {
        "version": {"path": "src/gpo_studio/__init__.py"},
        "build": {
            "targets": {
                "wheel": {"packages": ["src/gpo_studio"]},
                "sdist": {
                    "exclude": [
                        ".venv",
                        "node_modules",
                        "*.db",
                        "*.sqlite3",
                        "exports/",
                        "playwright-report/",
                        "test-results/",
                        ".claude/",
                        "githooks/",
                        "reflections/",
                        "dist/",
                        "dist-repro/",
                        "build/",
                        # Post-freeze records and evidence (final-r1 B2).
                        "/docs/plan-033/*-evidence/",
                        "/docs/plan-033/*-cleanup/",
                        "/docs/plan-033/*-batch.json",
                        "/docs/plan-033/*-batch.md",
                        "/docs/plan-033/*-results.md",
                        "/docs/plan-033/environment-spec.md",
                        "/docs/plan-033/bound-source-cost.md",
                        "/docs/plan-033/verdict-registry.json",
                        "/docs/capability-matrix.md",
                        "/docs/work-items.md",
                        "/docs/release-evidence-*.md",
                        "/docs/release-evidence-report-*.json",
                        "/tests/",
                    ]
                },
            }
        },
    },
    "pytest": {"ini_options": {"testpaths": ["tests"]}},
    "mypy": {"python_version": "3.13", "strict": True, "files": ["src"]},
}
REVIEWED_BUILD_SYSTEM = {"requires": ["hatchling"], "build-backend": "hatchling.build"}
#: The project's own entry points: a ``pytest11`` entry point would load a tracked
#: module into every pytest run as a plugin, and a script names code to run.
REVIEWED_PROJECT_ENTRY_POINTS = {"scripts": {"gpo-studio": "gpo_studio.__main__:main"}}
#: package.json's top-level keys: no packageManager, workspaces, overrides or config.
PACKAGE_JSON_KEYS = {"name", "private", "type", "engines", "scripts", "devDependencies"}


def _shadows(path: str) -> str | None:
    """The module name *path* would provide on a sys.path entry it sits directly in."""
    for directory in _SHADOW_DIRS:
        if not path.startswith(directory):
            continue
        rest = path[len(directory) :]
        parts = rest.split("/")
        if len(parts) == 1 and re.search(r"\.(py|pyc|pyw|so|pyd)\Z", rest):
            return rest.split(".", 1)[0]
        if len(parts) == 2 and parts[1] == "__init__.py":
            return parts[0]
    return None


#: The (platform, CPython minor) pairs CI installs on: the Linux jobs and the
#: Windows test job, on the matrix's 3.13 and 3.14.
#: The CPython minors CI runs and this check models. requires-python must admit
#: exactly these (Sol r10 B2: `>=3.13` admitted 3.15, which the lock cannot install
#: without building), so a metadata change cannot outrun the model.
MODELED_PYTHON_MINORS = (13, 14)
_CI_TARGETS = tuple(
    (platform, minor) for platform in ("linux", "win") for minor in MODELED_PYTHON_MINORS
)
#: The oldest glibc a Linux CI runner is assumed to have. ubuntu-latest has been
#: Ubuntu 24.04 (glibc 2.39) since early 2025; 22.04's 2.35 is assumed so that a
#: move between those images cannot strand a wheel. A lower floor accepts fewer
#: wheels, so the assumption errs strict. Revisit it if CI pins an older image.
LINUX_GLIBC_FLOOR = (2, 35)


def _supported_tags(platform: str, minor: int) -> frozenset[str]:
    """The tags an installer on that runner accepts, from ``packaging.tags``' own rules."""
    from packaging import tags

    if platform == "linux":
        # manylinux_2_N is installable on glibc >= 2.N; the legacy names are aliases.
        legacy = {17: "manylinux2014_x86_64", 12: "manylinux2010_x86_64", 5: "manylinux1_x86_64"}
        platforms: list[str] = []
        for glibc_minor in range(LINUX_GLIBC_FLOOR[1], 4, -1):
            platforms.append(f"manylinux_2_{glibc_minor}_x86_64")
            if glibc_minor in legacy:
                platforms.append(legacy[glibc_minor])
    else:
        platforms = ["win_amd64"]
    version = (3, minor)
    supported = [
        *tags.cpython_tags(version, abis=[f"cp3{minor}"], platforms=platforms),
        *tags.compatible_tags(version, f"cp3{minor}", platforms),
    ]
    return frozenset(str(tag) for tag in supported)


_SUPPORTED = {target: _supported_tags(*target) for target in _CI_TARGETS}


def _wheel_runs_on(artifact: dict[str, Any], platform: str, minor: int) -> bool:
    from packaging.utils import parse_wheel_filename

    name = str(artifact["url"]).rsplit("/", 1)[1]
    _name, _version, _build, wheel_tags = parse_wheel_filename(name)
    return any(str(tag) in _SUPPORTED[(platform, minor)] for tag in wheel_tags)


def admitted_python_minors(pyproject_path: Path) -> tuple[int, ...]:
    """The CPython 3.x minors ``requires-python`` admits (99 stands for "unbounded")."""
    from packaging.specifiers import SpecifierSet

    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    specifier = SpecifierSet(pyproject["project"]["requires-python"])
    return tuple(minor for minor in range(100) if specifier.contains(f"3.{minor}"))


def python_range_violations(pyproject_path: Path) -> list[str]:
    admitted = admitted_python_minors(pyproject_path)
    if admitted != MODELED_PYTHON_MINORS:
        shown = "unbounded" if 99 in admitted else [f"3.{minor}" for minor in admitted]
        return [
            f"pyproject.toml: requires-python admits {shown}, but CI and this check model "
            f"{[f'3.{minor}' for minor in MODELED_PYTHON_MINORS]}"
        ]
    return []


def uv_lock_violations(lock_path: Path) -> list[str]:
    """Every package uv.lock names comes from PyPI with a SHA-256 for every artifact."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    found: list[str] = []
    for package in lock.get("package", []):
        source = package.get("source", {})
        if source == {"editable": "."}:
            continue
        if source.get("registry") != "https://pypi.org/simple":
            found.append(f"uv.lock: {package['name']} is not from PyPI ({source})")
        artifacts = ([package["sdist"]] if "sdist" in package else []) + package.get("wheels", [])
        if not artifacts or not all(
            str(artifact.get("hash", "")).startswith("sha256:") for artifact in artifacts
        ):
            found.append(f"uv.lock: {package['name']} has an artifact without a SHA-256")
        for target in _CI_TARGETS:
            if not any(_wheel_runs_on(artifact, *target) for artifact in package.get("wheels", [])):
                # uv and pip would build the sdist, resolving its build requirements
                # from the index, unhashed and outside this lock (Sol r8 B2).
                found.append(f"uv.lock: {package['name']} has no wheel for {target}")
        # uv re-derives these URLs from the index today; checked anyway, like npm's.
        if not all(
            str(artifact.get("url", "")).startswith("https://files.pythonhosted.org/")
            for artifact in artifacts
        ):
            found.append(f"uv.lock: {package['name']} has an artifact not on PyPI's file host")
    return found


def _fold(text: str) -> str:
    """How a case- and normalisation-insensitive filesystem (NTFS, APFS) sees a name.

    The Windows test job runs on NTFS, so ``PYTEST.TOML`` is ``pytest.toml`` there.
    """
    return unicodedata.normalize("NFC", text).casefold()


_DISCOVERED_FOLDED = {_fold(name) for name in DISCOVERED | _ALSO_REFUSED}
_SHADOWED_FOLDED = {_fold(name) for name in SHADOWED}


def _tool_uv_violations(table: Any, lock_path: Path) -> list[str]:
    """[tool.uv]: the locked backend by default, no build isolation, and no dependency
    built at all -- ``no-build-package`` must name exactly the locked dependencies, so
    uv refuses an sdist build at run time, not only in this check (Sol r9 B2)."""
    locked = sorted(
        str(package["name"])
        for package in tomllib.loads(lock_path.read_text(encoding="utf-8"))["package"]
        if package.get("source") != {"editable": "."}
    )
    expected = {
        "required-version": ">=0.12.0",
        "default-groups": ["build-backend"],
        # Not the global no-build-isolation: it builds gpo-studio before its backend
        # is installed in a fresh environment (CI on 59cb548).
        "no-build-isolation-package": ["gpo-studio"],
        "no-build-package": locked,
    }
    if not isinstance(table, dict) or set(table) != set(expected):
        return ["pyproject.toml: [tool.uv] has other keys than the reviewed ones"]
    found = [
        f"pyproject.toml: [tool.uv] {key} differs from the reviewed value"
        for key in ("required-version", "default-groups", "no-build-isolation-package")
        if table[key] != expected[key]
    ]
    if sorted(table["no-build-package"]) != locked:
        missing = sorted(set(locked) - set(table["no-build-package"]))
        found.append(f"pyproject.toml: [tool.uv] no-build-package misses {missing or 'nothing'}")
    return found


def configuration_violations(root: Path = REPO_ROOT) -> list[str]:
    """Tracked files that would change what a reviewed template loads or runs.

    Every name is compared folded (case and Unicode normalisation), as the Windows
    job's filesystem compares it; a reviewed file must sit at its exact path.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True, check=True
    ).stdout.decode()
    found: list[str] = []
    seen: dict[str, str] = {}
    for record in filter(None, listed.split("\0")):
        mode, _object, rest = record.split(" ", 2)
        path = rest.split("\t", 1)[1]
        folded = _fold(path)
        if folded in seen and seen[folded] != path:
            found.append(f"{path}: the same path as {seen[folded]} on a case-insensitive disk")
        seen.setdefault(folded, path)
        if mode == "120000":
            target = os.readlink(root / path) if (root / path).is_symlink() else ""
            if REVIEWED_SYMLINKS.get(path) != target:
                found.append(f"{path}: a tracked symlink, which can redirect a reviewed path")
        elif mode == "160000":
            found.append(f"{path}: a submodule, whose content is fetched from elsewhere")
        name = folded.rsplit("/", 1)[-1]
        if "$" in path or "`" in path:
            found.append(f"{path}: a tracked path a template's variable could spell literally")
        if any(part.startswith("-") for part in path.split("/")):
            # A glob or a directory walk can hand it to a tool as an option (Sol r11 B1).
            found.append(f"{path}: a tracked name that starts with '-' reads as an option")
        trees = {_fold(tree) for tree in UNTRACKED_TREES} & set(folded.split("/"))
        if trees:
            # At any depth: Node resolves a nearer node_modules first, and npm ci
            # rebuilds only the root one (Sol r8 B1).
            found.append(f"{path}: tracked as or inside {sorted(trees)[0]}")
        if folded in {_fold(written) for written in WRITTEN_AT_ROOT}:
            found.append(f"{path}: a file a template writes, tracked")
        if name.endswith(".pyc") or "__pycache__" in folded.split("/"):
            found.append(f"{path}: compiled Python, which can run in place of its source")
        module = _shadows(folded)
        if module in _SHADOWED_FOLDED:
            found.append(f"{path}: shadows the {module!r} module a template runs or imports")
        if name.endswith(".py") and (folded.startswith("tests/") or "/" not in folded):
            # pytest imports a module-level `pytest_plugins` as plugins, before any test.
            source = (root / path).read_text(encoding="utf-8", errors="replace")
            if re.search(r"^pytest_plugins\s*[:=]", source, re.M):
                found.append(f"{path}: pytest_plugins loads modules as pytest plugins")
        if ".config" in folded.split("/")[:-1]:
            found.append(f"{path}: a .config/ tree is where several tools look for config")
        if name in {_fold(pinned) for pinned in _PINNED_AT_ROOT}:
            config = path not in _PINNED_AT_ROOT  # pinned below by content, at the root only
        elif name == "conftest.py":
            config = path != "tests/conftest.py" and not folded.startswith("tests/")
        else:
            config = bool(name in _DISCOVERED_FOLDED or CONFIG_PATTERNS.match(name))
        if config:
            content = (root / path).read_bytes()
            if REVIEWED_CONFIG.get(path) != hashlib.sha256(content).hexdigest():
                found.append(f"{path}: configures a tool the templates run, unreviewed")
            if name.endswith(".psd1") and _ANALYZER_CODE_KEYS.search(content.decode("utf-8")):
                found.append(f"{path}: names analyzer rule modules to load")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tools = pyproject.get("tool", {})
    for table in sorted(set(tools) - {"ruff", "uv"}):
        if tools[table] != REVIEWED_TOOL.get(table):
            found.append(f"pyproject.toml: [tool.{table}] differs from the reviewed table")
    found.extend(_tool_uv_violations(tools.get("uv"), root / "uv.lock"))
    if pyproject.get("build-system") != REVIEWED_BUILD_SYSTEM:
        found.append("pyproject.toml: [build-system] differs from the reviewed table")
    project = pyproject.get("project", {})
    points = {
        key: project[key] for key in ("scripts", "gui-scripts", "entry-points") if key in project
    }
    if points != REVIEWED_PROJECT_ENTRY_POINTS:
        found.append("pyproject.toml: the project's entry points differ from the reviewed ones")
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    extra = sorted(set(package) - PACKAGE_JSON_KEYS)
    if extra:
        found.append(f"package.json: unreviewed top-level keys {extra}")
    found.extend(npm_lock_violations(root / "package.json", root / "package-lock.json"))
    found.extend(python_range_violations(root / "pyproject.toml"))
    if (root / "uv.lock").is_file():
        found.extend(uv_lock_violations(root / "uv.lock"))
    return found


# --- workflows -------------------------------------------------------------------

_PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")


@dataclass(frozen=True)
class ActionSpec:
    """One reviewed action: the exact revision, and the inputs it is run with.

    ``required`` inputs must be present, with one of the listed values (``None``:
    any value -- an input that only names files to upload or attest); ``optional``
    inputs may be. Any other input is refused. ``preserves`` is why the action
    leaves every fact's files alone (``""``: it does not; facts are voided).
    """

    required: dict[str, frozenset[str] | None]
    optional: dict[str, frozenset[str] | None]
    preserves: str


def _values(*values: str) -> frozenset[str]:
    return frozenset(values)


#: Every action a workflow may use, keyed by its exact ``uses:`` value: one reviewed
#: revision each (Sol r13: a key of owner/repo admitted any SHA, and a missing
#: ``version:`` let setup-uv choose its own uv). An action that installs packages
#: (setup-python's ``pip-install``, say) is an install this grammar cannot see, so
#: a new action, revision or input is a deliberate edit here. Each SHA was checked
#: against its repository (``gh api repos/OWNER/REPO/commits/SHA``) when recorded;
#: the tag in each comment is the one the workflows name beside it. The pinned
#: actions, and what they fetch with these inputs (uv, CPython, Node, the cache),
#: are trusted.
_PYTHONS = _values("3.13", "3.14")
ACTIONS: dict[str, ActionSpec] = {
    # v5. The one allowed action that changes the workspace: `git clean -ffdx`.
    "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd": ActionSpec(
        required={}, optional={"fetch-depth": _values("0")}, preserves=""
    ),
    # v6
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1": ActionSpec(
        required={"python-version": _values("3.13")},
        optional={},
        preserves="CPython into the tool cache",
    ),
    # v5
    "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444": ActionSpec(
        required={"node-version": _values("24"), "cache": _values("npm")},
        optional={},
        preserves="Node into the tool cache; cache: npm restores ~/.npm only",
    ),
    # v10.0.1
    "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d": ActionSpec(
        required={"version": _values("0.12.0"), "python-version": _PYTHONS},
        optional={"enable-cache": _values("True")},
        preserves="uv into the tool cache; its cache is uv's download cache",
    ),
    # v5. The path is the Playwright browser cache, outside the workspace.
    "actions/cache@caa296126883cff596d87d8935842f9db880ef25": ActionSpec(
        required={
            "path": _values("~/.cache/ms-playwright"),
            "key": _values("${{ runner.os }}-playwright-${{ hashFiles('package-lock.json') }}"),
        },
        optional={},
        preserves="restores ~/.cache/ms-playwright only",
    ),
    # v6
    "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f": ActionSpec(
        required={"name": None, "path": None},
        optional={"if-no-files-found": _values("error", "ignore")},
        preserves="reads the named paths",
    ),
    # v4.1.1
    "actions/attest@a1948c3f048ba23858d222213b7c278aabede763": ActionSpec(
        required={"subject-path": None, "sbom-path": None},
        optional={},
        preserves="reads the subjects, writes an attestation outside the workspace",
    ),
    # v3
    "actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a": ActionSpec(
        required={"subject-path": None},
        optional={},
        preserves="reads the subjects, writes an attestation",
    ),
    # v3
    "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e": ActionSpec(
        required={}, optional={}, preserves="reads the git history"
    ),
}
#: The reviewed actions that leave the checkout's untracked files (node_modules,
#: dist/, exported locks), the /tmp venvs and $RUNNER_TEMP alone, by exact
#: ``uses:``. Every other step -- actions/checkout first of all -- voids every fact
#: established before it in the job.
WORKSPACE_PRESERVING_ACTIONS = {
    uses: spec.preserves for uses, spec in ACTIONS.items() if spec.preserves
}


def action_spec(name: str) -> tuple[str, ActionSpec]:
    """The one reviewed ``uses:`` value and spec for an ``owner/repo``."""
    (found,) = [(uses, spec) for uses, spec in ACTIONS.items() if uses.split("@")[0] == name]
    return found


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


#: Every ``${{ }}`` expression a workflow may contain outside ``run:`` (where none
#: may): context values that select nothing to download, each reviewed. A
#: ``matrix.KEY`` expression is expanded instead, and each value checked where used.
REVIEWED_EXPRESSIONS = {
    "github.event_name == 'pull_request'",
    "github.ref",
    "github.sha",
    "github.token",
    "github.workspace",
    "hashFiles('package-lock.json')",
    "needs.release-identity.outputs.manifest",
    "needs.release-identity.outputs.report",
    "runner.os",
    "secrets.GITHUB_TOKEN",
    "secrets.GPO_STUDIO_FORBIDDEN_IDENTIFIERS",
    "steps.manifest.outputs.manifest",
    "steps.manifest.outputs.report",
}
#: The keys a job that calls a local reusable workflow may have.
_REUSABLE_CALL_KEYS = {"uses", "needs", "permissions", "secrets"}
#: ``if:`` conditions a step or job may carry.
REVIEWED_CONDITIONS = {"failure()"}
_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_MATRIX_ONLY = re.compile(r"^\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}$")


def _matrix(job: Any, where: str) -> tuple[dict[str, list[str]], list[str]]:
    """The job's matrix as key -> literal values, and why it cannot be read statically."""
    matrix = (job.get("strategy") or {}).get("matrix")
    if matrix is None:
        return {}, []
    if not isinstance(matrix, dict):
        return {}, [f"{where}: a matrix that is not a literal mapping cannot be checked"]
    values: dict[str, list[str]] = {}
    problems: list[str] = []
    for key, entries in matrix.items():
        if key in {"include", "exclude"} or not isinstance(entries, list):
            problems.append(f"{where}: matrix {key!r} is not a literal list")
            continue
        if not all(isinstance(entry, (str, int, float)) for entry in entries) or any(
            "${{" in str(entry) for entry in entries
        ):
            problems.append(f"{where}: matrix {key!r} has a value that is not a literal")
            continue
        values[str(key)] = [str(entry) for entry in entries]
    return values, problems


def _expression_violations(
    node: Any, where: str, matrix: dict[str, list[str]], key: str = ""
) -> Iterator[str]:
    """Every expression outside run: is reviewed, or a matrix key whose values are literal."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            if child_key in {"run", "steps", "jobs"}:
                continue
            yield from _expression_violations(child, where, matrix, str(child_key))
    elif isinstance(node, list):
        for child in node:
            yield from _expression_violations(child, where, matrix, key)
    elif isinstance(node, str):
        if key == "if":
            condition = _EXPRESSION.fullmatch(node.strip())
            text = condition[1] if condition else node.strip()
            if text not in REVIEWED_CONDITIONS:
                yield f"{where}: condition {node!r} is not reviewed"
            return
        for found in _EXPRESSION.finditer(node):
            expression = found[1]
            matrix_key = re.fullmatch(r"matrix\.([A-Za-z0-9_-]+)", expression)
            if matrix_key and matrix_key[1] in matrix:
                continue
            if expression not in REVIEWED_EXPRESSIONS:
                yield (
                    f"{where}: expression ${{{{ {expression} }}}} cannot be resolved to "
                    "reviewed values"
                )


def _action_violations(
    step: Any, where: str, matrix: dict[str, list[str]] | None = None
) -> Iterator[str]:
    uses = str(step["uses"])
    if not _PINNED_ACTION.fullmatch(uses):
        yield f"{where}: action {uses!r} is not a remote action pinned by SHA"
        return
    spec = ACTIONS.get(uses)
    if spec is None:
        name = uses.split("@", 1)[0]
        known = [reviewed for reviewed in ACTIONS if reviewed.split("@")[0] == name]
        reason = f"is not the reviewed revision {known[0]}" if known else "is not on the allowlist"
        yield f"{where}: action {uses!r} {reason} (it could install anything)"
        return
    inputs = step.get("with") or {}
    for key in sorted(set(spec.required) - set(inputs)):
        # The action would choose for itself (setup-uv: any uv; setup-python: any CPython).
        yield f"{where}: {uses} needs input {key!r}"
    for key, value in inputs.items():
        if key in spec.required:
            allowed = spec.required[key]
        elif key in spec.optional:
            allowed = spec.optional[key]
        else:
            yield f"{where}: input {key!r} of {uses} is not allowed"
            continue
        if allowed is None:
            continue
        # fullmatch: `$` with match() let a YAML block scalar's trailing newline pass
        # as the reviewed matrix expression (Sol final-r5 B1). The value checked is
        # the value the action receives, byte for byte.
        expanded = _MATRIX_ONLY.fullmatch(str(value))
        if str(value) in allowed:
            continue  # listed exactly, any reviewed expression in it included
        if expanded:
            # Each matrix value is a separate run with that literal input.
            candidates = (matrix or {}).get(expanded[1])
            if candidates is None:
                yield f"{where}: {uses} {key}: {value!r} names no literal matrix key"
                continue
        elif "${{" in str(value):
            yield f"{where}: {uses} {key}: {value!r} is an expression, not a listed value"
            continue
        else:
            candidates = [str(value)]
        for candidate in candidates:
            if candidate not in allowed:
                yield f"{where}: {uses} {key}: {candidate!r} is not one of {sorted(allowed)}"


def workflow_violations(
    path: Path, *, root: Path = REPO_ROOT, package_json: Path | None = None
) -> list[str]:
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[str] = []
    found.extend(_env_violations(document.get("env"), path.name))
    found.extend(_defaults_violations(document, path.name))
    found.extend(_expression_violations(document, path.name, {}))
    for job_name, job in (document.get("jobs") or {}).items():
        if "uses" in job:
            uses = str(job["uses"])
            local = re.fullmatch(r"\./\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", uses)
            if local is None or not (root / uses).is_file():
                found.append(
                    f"{path.name}:{job_name}: reusable workflow {uses!r} is not a file "
                    "in this repository's .github/workflows"
                )
            found.extend(_expression_violations(job, f"{path.name}:{job_name}", {}))
            extra = sorted(set(job) - _REUSABLE_CALL_KEYS)
            if extra:
                # `with:` inputs would reach the called workflow as data this file never sees.
                found.append(f"{path.name}:{job_name}: reusable-workflow call keys {extra}")
            continue
        found.extend(_env_violations(job.get("env"), f"{path.name}:{job_name}"))
        found.extend(_defaults_violations(job, f"{path.name}:{job_name}"))
        matrix, problems = _matrix(job, f"{path.name}:{job_name}")
        found.extend(problems)
        found.extend(_expression_violations(job, f"{path.name}:{job_name}", matrix))
        for number, step in enumerate(job.get("steps") or [], 1):
            found.extend(_expression_violations(step, f"{path.name}:{job_name}#{number}", matrix))
        for key in ("container", "services"):
            if key in job:
                # Both start an image (mutable, outside either lock) before any step.
                found.append(f"{path.name}:{job_name}: a job {key} runs an unscanned image")
        if "continue-on-error" in job:
            found.append(f"{path.name}:{job_name}: continue-on-error hides a failed install")
        runner = job.get("runs-on")
        if runner not in RUNNERS:
            # The runner decides the default shell; a label or expression would hide it.
            found.append(f"{path.name}:{job_name}: runs-on {runner!r} is not one of {RUNNERS}")
        windows = runner == "windows-latest"
        inherited = ((document.get("defaults") or {}).get("run") or {}).get("shell") or (
            (job.get("defaults") or {}).get("run") or {}
        ).get("shell")
        if windows and inherited:
            # A Windows step without its own shell: runs under the inherited default,
            # but would be checked here as PowerShell. Refused rather than modelled.
            found.append(f"{path.name}:{job_name}: an inherited default shell on Windows")
        # Jobs are separate machines: nothing one established holds in another.
        ctx = Context(root=root, package_json=package_json)
        for number, step in enumerate(job.get("steps") or [], 1):
            where = f"{path.name}:{job_name}#{number}"
            if "uses" in step:
                found.extend(_action_violations(step, where, matrix))
                if str(step["uses"]) not in WORKSPACE_PRESERVING_ACTIONS:
                    # checkout's `git clean -ffdx` removes node_modules, dist/, exports
                    # (Sol r12 B1); anything not reviewed as leaving them is assumed to.
                    ctx.invalidate()
            found.extend(_env_violations(step.get("env"), where))
            if "working-directory" in step:
                found.append(f"{where}: working-directory moves every relative path checked here")
            if "continue-on-error" in step:
                found.append(f"{where}: continue-on-error hides a failed install")
            if "run" not in step:
                continue
            script = str(step["run"])
            if "${{" in script:
                found.append(f"{where}: an expression inside run: is a command nobody can read")
                continue
            shell = step.get("shell")
            if windows and shell not in (None, "pwsh"):
                # Read as the Bash table, but outside the bash traces; PowerShell only.
                found.append(f"{where}: shell {shell!r} on Windows (PowerShell only)")
                continue
            if shell == "pwsh" or (shell is None and windows):
                if script not in PWSH_BODIES:
                    found.append(f"{where}: no reviewed PowerShell body matches {script!r}")
                continue
            if shell not in (None, "bash"):
                found.append(f"{where}: shell {shell!r} is not bash or pwsh")
                continue
            ctx.end_step()
            ctx.conditional_step = "if" in step or "continue-on-error" in step
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
