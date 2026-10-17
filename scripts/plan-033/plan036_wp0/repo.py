"""Repository access shared by every part: git, digests, clones.

Every git call decodes its output as UTF-8 explicitly (never the locale's code
page: cp1252 on a Windows runner is how `fae8376` failed), and every path that
leaves this module is repository-relative and `/`-separated.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
LISTS_DIR = PACKAGE_DIR / "lists"


class Wp0Error(Exception):
    """A refusal. The message says what was refused and why."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


#: Git configuration every git process the tool starts, and every git child a
#: lane starts, runs under (review round 6, Sol B2). It is passed as
#: GIT_CONFIG_COUNT/KEY_n/VALUE_n, git's command-line scope, which overrides
#: every configuration file. With system and global configuration switched off
#: and the repository's own configuration held to `LOCAL_CONFIG_ALLOWED`
#: (`audit_repository`), nothing in configuration can start a helper: no
#: fsmonitor, hook, pager, credential helper, transport, replacement object or
#: automatic maintenance, and no attributes or excludes file outside the tree.
GIT_PINNED_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.fsmonitor", "false"),
    ("core.hooksPath", os.devnull),
    ("core.attributesFile", os.devnull),
    ("core.excludesFile", os.devnull),
    ("core.pager", "cat"),
    ("core.useReplaceRefs", "false"),
    ("core.untrackedCache", "false"),
    ("credential.helper", ""),
    ("protocol.allow", "never"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("status.submoduleSummary", "false"),
    ("submodule.recurse", "false"),
)
#: Variables set (to exactly these values) for every git process; every other
#: GIT_* and XDG_* variable is removed. HOME names no directory, so no file
#: under a home directory is read.
GIT_ENVIRONMENT: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "HOME": os.devnull,
    "GIT_CONFIG_COUNT": str(len(GIT_PINNED_CONFIG)),
    **{f"GIT_CONFIG_KEY_{i}": key for i, (key, _value) in enumerate(GIT_PINNED_CONFIG)},
    **{f"GIT_CONFIG_VALUE_{i}": value for i, (_key, value) in enumerate(GIT_PINNED_CONFIG)},
}
#: Repository-local configuration keys (git prints them lower-cased, the
#: subsection as written) that start no program and change no byte git reads or
#: writes for the lanes' commands, with the values allowed (None: any).
#: `core.hookspath` is allowed because the command scope pins it to the null
#: device. Anything else makes `audit_repository` refuse the clone.
LOCAL_CONFIG_ALLOWED: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (re.compile(r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase"
                r"|precomposeunicode|symlinks)"), None),
    (re.compile(r"core\.hookspath"), None),
    (re.compile(r"core\.autocrlf"), re.compile(r"false")),
    (re.compile(r"remote\..+\.(url|fetch)"), None),
    (re.compile(r"branch\..+\.(remote|merge)"), None),
    (re.compile(r"user\.(name|email)"), None),
    # actions/checkout (v5, as the workflows pin it) persists the job token as
    # an HTTP header in the clone's configuration; it is sent only by an HTTP
    # transport, which protocol.allow=never disables. Its value is never printed.
    (re.compile(r"http\.(.+\.)?extraheader"), None),
)
#: Attributes that name a driver (a program from configuration): refused in any
#: attributes file the clone's git reads, macro definitions included.
_DRIVER_ATTRIBUTE = re.compile(r"^(filter|diff|merge)=")


def git_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """*base* (default: this process's environment) with every GIT_* and XDG_*
    variable removed and `GIT_ENVIRONMENT` set."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.upper().startswith(("GIT_", "XDG_"))}
    env.update(GIT_ENVIRONMENT)
    return env


def git_pins_hold(environ: Mapping[str, str]) -> list[str]:
    """What in *environ* differs from the pinned git environment (empty: holds)."""
    problems = [f"{name}={environ.get(name)!r}, not {value!r}"
                for name, value in GIT_ENVIRONMENT.items() if environ.get(name) != value]
    extra = sorted(name for name in environ if name.upper().startswith(("GIT_", "XDG_"))
                   and name not in GIT_ENVIRONMENT and name not in LANE_GIT_IDENTITY)
    problems.extend(f"{name} is set" for name in extra)
    return problems


#: The committer identity a lane's git children see (`git var
#: GIT_COMMITTER_IDENT`), fixed so it never comes from a home directory.
LANE_GIT_IDENTITY: dict[str, str] = {
    "GIT_AUTHOR_NAME": "plan036-wp0", "GIT_AUTHOR_EMAIL": "plan036-wp0@invalid",
    "GIT_COMMITTER_NAME": "plan036-wp0", "GIT_COMMITTER_EMAIL": "plan036-wp0@invalid",
}


def audit_repository(path: Path) -> list[str]:
    """Why git in the checkout *path* could start a helper or read input outside
    the pinned set (empty: it cannot). Review round 6, Sol B2: refuse rather than
    model. Checks every configuration entry git would read there (with system
    and global configuration off), the attributes files, submodules and grafts."""
    problems: list[str] = []
    listing = subprocess.run(
        ["git", "-C", str(path), "config", "--list", "--show-scope", "-z"],
        capture_output=True, check=False, env=git_environment(),
    )
    if listing.returncode != 0:
        return [f"git config --list failed in {path}: "
                + listing.stderr.decode("utf-8", errors="replace").strip()]
    pinned = {(key.lower(), value) for key, value in GIT_PINNED_CONFIG}
    # `-z --show-scope`: scope NUL key LF value NUL, repeated.
    tokens = listing.stdout.decode("utf-8", errors="replace").split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    if len(tokens) % 2:
        return [f"git config --list printed an odd number of fields in {path}"]
    for scope, item in zip(tokens[0::2], tokens[1::2], strict=True):
        key, _, value = item.partition("\n")
        if scope == "command":
            if (key, value) not in pinned:
                problems.append(f"command-scope git configuration {key} is not a pin")
            continue
        if scope not in ("local", "worktree"):
            problems.append(f"git reads {scope} configuration ({key}) despite the pins")
            continue
        allowed = [v for k, v in LOCAL_CONFIG_ALLOWED if k.fullmatch(key)]
        if not allowed or (allowed[0] is not None and not allowed[0].fullmatch(value)):
            problems.append(f"repository configuration {key} is not on the reviewed list "
                            "(it may start a program or change what git reads)")
    common = Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    attribute_files = [common / "info" / "attributes"]
    tracked = git(path, "ls-files", "-z", "--", ":(glob)**/.gitattributes", ".gitattributes")
    attribute_files.extend(path / name for name in tracked.split("\0") if name)
    for attributes in attribute_files:
        if not attributes.is_file():
            continue
        for number, line in enumerate(attributes.read_text("utf-8", errors="replace")
                                      .splitlines(), 1):
            tokens = line.split()
            if not tokens or tokens[0].startswith("#"):
                continue
            if any(_DRIVER_ATTRIBUTE.match(token) for token in tokens[1:]):
                problems.append(f"{attributes}:{number} names a filter, diff or merge driver")
    if (path / ".gitmodules").exists() or any(
        line.startswith("160000 ") for line in git(path, "ls-files", "-s").splitlines()
    ):
        problems.append(f"{path} has submodules, whose own configuration is not audited")
    if (common / "info" / "grafts").exists():
        problems.append(f"{common / 'info' / 'grafts'} rewrites history")
    return problems


def git(repo: Path, *args: str, check: bool = True, hermetic: bool = True) -> str:
    """Run git in *repo* and return stdout decoded as UTF-8.

    *hermetic* (the default) runs it under `git_environment()`; only the
    banking commit, which is the owner's own commit (identity and hooks
    included), runs with the caller's configuration."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        env=git_environment() if hermetic else None,
    )
    if check and result.returncode != 0:
        raise Wp0Error(
            f"git {' '.join(args)} failed in {repo}: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout.decode("utf-8")


def git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False,
                            env=git_environment())
    if result.returncode != 0:
        raise Wp0Error(
            f"git {' '.join(args)} failed in {repo}: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def rev_parse(repo: Path, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def show(repo: Path, commit: str, relative: str) -> bytes | None:
    """The blob at *commit*:*relative*, or None when the path is absent there."""
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{relative}"],
        capture_output=True,
        check=False,
        env=git_environment(),
    )
    if result.returncode != 0:
        return None
    return result.stdout


def blob_sha256(repo: Path, commit: str, relative: str) -> str | None:
    """SHA-256 of git's blob for *relative* at *commit* (not the checkout bytes)."""
    data = show(repo, commit, relative)
    return None if data is None else sha256_bytes(data)


def tracked_files(repo: Path, commit: str, prefix: str = "") -> list[str]:
    out = git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", prefix or ".")
    return sorted(path for path in out.split("\0") if path)


def is_clean(repo: Path) -> bool:
    return git(repo, "status", "--porcelain", "--untracked-files=all") == ""


@contextmanager
def detached_worktree(repo: Path, commit: str, parent: Path, name: str) -> Iterator[Path]:
    """A fresh `git worktree add --detach` of *commit* under *parent*; removed after.

    Every git process here runs hermetically (`git_environment`), so the
    checkout runs no hook and no filter from configuration, and the clone is
    refused if `audit_repository` finds anything git could run there.

    The worktree is the "fresh clone" of the plan: a checkout with no build
    state, no untracked files and no virtual environment of its own. It shares
    the object store with *repo*, which holds every commit it can name.
    """
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / name
    if path.exists():
        raise Wp0Error(f"refusing to reuse an existing directory for a fresh clone: {path}")
    git(repo, "worktree", "add", "--detach", "--quiet", str(path), commit)
    try:
        if rev_parse(path, "HEAD") != rev_parse(repo, commit):
            raise Wp0Error(f"worktree {path} is not at {commit}")
        problems = audit_repository(path)
        if problems:
            # Review round 6, Sol B2: git in this clone could start a program or
            # read input no trace records; refuse rather than model it.
            raise Wp0Error(f"refusing the clone {path}: " + "; ".join(problems[:5]))
        if not is_clean(path):
            raise Wp0Error(f"fresh worktree {path} is not clean")
        yield path
    finally:
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        git(repo, "worktree", "prune", check=False)


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def environ_subset(names: Sequence[str]) -> dict[str, str]:
    return {name: os.environ[name] for name in names if name in os.environ}
