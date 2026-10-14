"""Repository access shared by every part: git, digests, clones.

Every git call decodes its output as UTF-8 explicitly (never the locale's code
page: cp1252 on a Windows runner is how `fae8376` failed), and every path that
leaves this module is repository-relative and `/`-separated.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
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


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Run git in *repo* and return stdout decoded as UTF-8."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise Wp0Error(
            f"git {' '.join(args)} failed in {repo}: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout.decode("utf-8")


def git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
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
