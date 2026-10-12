"""Prove, by running uv offline, that building gpo-studio uses only the locked backend.

`[build-system].requires = ["hatchling"]` names no version. An isolated build
(uv's or pip's default) resolves it from the index at build time, whatever
uv.lock says -- which is what every editable `uv sync` did until the project
build was made non-isolated against the lock's `build-backend` group.

The check, on a copy of the tracked tree outside the repository:

0. ``uv sync --locked`` into a new environment with an empty cache (this step
   uses the network) must succeed: the order uv builds and installs in differs
   with a cold cache, and a global ``no-build-isolation`` built gpo-studio before
   hatchling was installed there -- while every warm-cache run passed (CI on
   59cb548).
1. A fresh uv cache is seeded with the locked `build-backend` closure only
   (`uv sync --locked --only-group build-backend`; this step uses the network).
2. With ``UV_OFFLINE=1``, ``uv sync --locked --extra dev`` installs that closure
   and the project, every other locked package excluded. It must succeed, say
   "Proceeding without build isolation", never resolve build requirements, and
   leave the lock's hatchling installed. ``uv build --wheel`` must too.
3. Control: the same offline sync with ``[tool.uv]`` removed from the copy's
   pyproject.toml must build in an isolated environment and fail resolving
   ``build-system.requires`` there -- the isolated build reaching for the index,
   told apart from uv's generic hint by uv's own log lines -- or the check above
   would prove nothing.

    uv run --locked python scripts/check_locked_build_backend.py --tmp-root DIR

Exit 0 when all three hold, 1 otherwise. DIR (default: the system temp dir)
must be outside the repository.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CheckFailed(Exception):
    pass


def backend_closure(lock: dict[str, object]) -> dict[str, str]:
    """Name -> version for the `build-backend` group and everything it pulls in."""
    packages = {
        str(package["name"]): package
        for package in lock["package"]  # type: ignore[attr-defined]
    }
    project = packages["gpo-studio"]
    group = project["dev-dependencies"]["build-backend"]
    pending = [str(item["name"]) for item in group]
    closure: dict[str, str] = {}
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        package = packages[name]
        closure[name] = str(package["version"])
        pending.extend(str(item["name"]) for item in package.get("dependencies", []))
    return closure


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _copy_tracked_tree(destination: Path) -> None:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    for relative in filter(None, listed.split("\0")):
        source = ROOT / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _uv(args: list[str], *, env: dict[str, str], project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", *args, "--project", str(project)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _site_packages(environment: Path) -> Path:
    found = sorted(environment.glob("lib/python3*/site-packages")) or sorted(
        environment.glob("Lib/site-packages")
    )
    if not found:
        raise CheckFailed(f"no site-packages under {environment}")
    return found[0]


def run(tmp_root: Path) -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    closure = backend_closure(lock)
    if "hatchling" not in closure:
        raise CheckFailed(f"the build-backend group does not lock hatchling: {closure}")
    everything = {str(package["name"]) for package in lock["package"]}
    excluded = sorted(everything - set(closure) - {"gpo-studio"})

    with tempfile.TemporaryDirectory(prefix="gpo-studio-backend-", dir=tmp_root) as raw:
        work = Path(raw)
        tree = work / "tree"
        _copy_tracked_tree(tree)
        base = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
        base["UV_CACHE_DIR"] = str(work / "cache")
        base["UV_PYTHON_DOWNLOADS"] = "never"
        # The interpreter running this check, so no uv setting chooses another.
        base["UV_PYTHON"] = sys.executable

        cold = _uv(
            ["sync", "--locked"],
            env={
                **base,
                "UV_CACHE_DIR": str(work / "cold-cache"),
                "UV_PROJECT_ENVIRONMENT": str(work / "cold-env"),
            },
            project=tree,
        )
        if cold.returncode != 0:
            raise CheckFailed(
                "a new environment with an empty cache could not install the project "
                f"from the lock:\n{(cold.stdout + cold.stderr)[-4000:]}"
            )
        print("cold sync: a new environment and an empty cache install the project")

        seed = _uv(
            ["sync", "--locked", "--only-group", "build-backend"],
            env={**base, "UV_PROJECT_ENVIRONMENT": str(work / "seed-env")},
            project=tree,
        )
        if seed.returncode != 0:
            raise CheckFailed(f"seeding the cache failed:\n{seed.stderr}")

        offline = {**base, "UV_OFFLINE": "1"}
        no_install = [arg for name in excluded for arg in ("--no-install-package", name)]
        probe_env = {**offline, "UV_PROJECT_ENVIRONMENT": str(work / "probe-env")}
        synced = _uv(
            ["sync", "--locked", "--extra", "dev", "-v", *no_install], env=probe_env, project=tree
        )
        log = synced.stdout + synced.stderr
        if synced.returncode != 0:
            raise CheckFailed(f"offline locked sync failed:\n{log[-4000:]}")
        if "Proceeding without build isolation" not in log:
            raise CheckFailed("the project build did not run without isolation")
        if "Resolving build requirements" in log or "build-system.requires" in log:
            raise CheckFailed("the project build resolved its own build requirements")
        installed = _site_packages(work / "probe-env")
        for name, version in closure.items():
            dist = re.sub(r"[-.]+", "_", name)
            if not (installed / f"{dist}-{version}.dist-info").is_dir():
                raise CheckFailed(f"{name} {version} (uv.lock) is not what the build used")
        print(f"offline sync: built without isolation from the locked {closure}")

        built = _uv(
            ["build", "--wheel", "--out-dir", str(work / "dist"), "-v"],
            env=probe_env,
            project=tree,
        )
        log = built.stdout + built.stderr
        if built.returncode != 0 or "Proceeding without build isolation" not in log:
            raise CheckFailed(
                f"offline uv build did not build from the locked backend:\n{log[-4000:]}"
            )
        print("offline uv build: built without isolation")

        pyproject = tree / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        start = text.index("\n[tool.uv]\n")
        end = text.index("\n[", start + 1)
        pyproject.write_text(text[:start] + text[end:], encoding="utf-8")
        control = _uv(
            ["sync", "--extra", "dev", "-v", *no_install],
            env={**offline, "UV_PROJECT_ENVIRONMENT": str(work / "control-env")},
            project=tree,
        )
        log = control.stdout + control.stderr
        # Each marker belongs to the isolated path, not to uv's closing hint (which
        # also says "build-system.requires" after an unrelated failure): the build
        # created its own environment, resolved its requirements, and failed there.
        isolated = (
            "Proceeding without build isolation" not in log
            and "Creating build environment" in log
            and "Resolving build requirements" in log
            and "Failed to resolve requirements from `build-system.requires`" in log
        )
        if control.returncode == 0 or not isolated:
            raise CheckFailed(
                "control: without [tool.uv] the offline build should build in isolation and "
                f"fail resolving build-system.requires, and it did not (exit "
                f"{control.returncode}):\n{log[-4000:]}"
            )
        print("control: an isolated build resolves build-system.requires and fails offline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--tmp-root", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args(argv)
    tmp_root = args.tmp_root.resolve()
    if _inside(tmp_root, ROOT):
        print(f"ERROR: --tmp-root {tmp_root} is inside the repository {ROOT}", file=sys.stderr)
        return 1
    tmp_root.mkdir(parents=True, exist_ok=True)
    try:
        run(tmp_root)
    except CheckFailed as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("locked build backend: proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
