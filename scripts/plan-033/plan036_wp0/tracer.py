"""Plan 036 WP-0's line tracer: run one Python script and record what executed.

    python <flags> tracer.py <out-dir> [--observe] -- <script> [args...]

`<flags>` are exactly the interpreter flags the lane's runner uses for the
script (today none: `uv run python <script>`). This file imports only the
standard library and is run as a script, never imported by a lane.

**How the script is started.** The tracer makes the process look as it would
under `python <flags> <script> <args>` (measured on the controller's CPython,
and checked by `tests/test_plan036_tracer.py` against the interpreter itself):

- without `-I`/`-P`, CPython puts the script's directory at `sys.path[0]`
  (symlinks followed on POSIX, the plain full path on Windows); the tracer
  replaces its own directory, which the interpreter put there, with that.
  With `-I` or `-P` nothing was prepended and nothing is changed (the first
  entry is then the standard library's zip);
- `sys.argv` is `[<script as given>, *args]`;
- the script runs as a fresh `__main__` module whose `__file__` is the
  absolute (not resolved) path, `__spec__` None, `__package__` None,
  `__cached__` None and `__loader__` a `SourceFileLoader`, with
  `sys.modules["__main__"]` replaced, exactly the namespace CPython builds.
  (The plan says `runpy.run_path`; that sets `sys.argv[0]` to the path it was
  given, which differs from what the interpreter does for a relative script,
  so the namespace is built directly instead.)

**What it records** (`<out-dir>/trace.json`): every (file, line) a LINE event
reported (`sys.monitoring`, each location disabled after its first event, so
the cost is one callback per line); every file opened (audit `open` events);
every child process (with its working directory, the environment variables it
was given that differ from this run's, review round 6); the run's whole
environment as it started (`environ`); and, at exit, the state the equivalence
check compares: `sys.flags`, the final `sys.path`, the working directory, the
environment filtered by `ENV_ALLOWLIST` (and every GIT_* variable), and the file
each imported module was loaded from.

**Children.** `subprocess.Popen` is wrapped. A child whose executable is a
Python interpreter and whose arguments are interpreter flags followed by a
script path is re-launched as `python <its flags> tracer.py <out-dir>/child-N
-- <script> <args>`, flags (including `-I`) preserved. Every other launch is
recorded; these make the trace **incomplete** (and the lane is selected,
D-9's strict default): a Python child given `-c`, `-m` or a script on standard
input; a Python child started any way but `subprocess` (`os.posix_spawn`,
`os.exec*`, `os.spawn*`, `os.system`, `os.fork`, seen by the audit hook); and
any shell child (`sh`, `bash`, `cmd`, `pwsh -Command`, `shell=True`). The
harness, not the tracer, decides whether a shell child is a declared process
edge that runs no interpreter; the tracer records it as `shell` for that.

`--observe` runs the same launch emulation with no monitoring, no wrapper and
no audit hook: it is the untraced side of the equivalence check for state the
real interpreter does not expose (the outputs are also compared against a run
of the plain interpreter, which does not go through this file at all).
"""

from __future__ import annotations

import atexit
import builtins
import json
import os
import sys
import types

ENV_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_NUMERIC", "LC_TIME",
    "TZ", "PYTHONHASHSEED", "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING", "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE", "PYTHONSAFEPATH", "PYTHONWARNINGS", "PYTHONDEVMODE",
    "PYTHONINTMAXSTRDIGITS", "SOURCE_DATE_EPOCH", "VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_INDEX_FILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
    "TMPDIR", "TEMP", "TMP",
)
#: Interpreter flags that consume the next argument.
_FLAGS_WITH_VALUE = frozenset("WXQ")
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "cmd", "cmd.exe", "powershell",
                     "powershell.exe"})
_TOOL_NAME = "plan036-wp0"


class _State:
    out_dir = ""
    environ: dict[str, str] = {}
    lines: dict[str, set[int]] = {}
    opened: list[tuple[str, str]] = []
    processes: list[dict[str, object]] = []
    incomplete: list[str] = []
    children: list[str] = []
    child_count = 0
    exit_code: int | None = None
    observe = False
    written = False
    mid_launch = False
    script = ""
    argv: list[str] = []
    tool_id: int | None = None


def _usage() -> None:
    sys.stderr.write("usage: tracer.py <out-dir> [--observe] -- <script> [args...]\n")
    raise SystemExit(2)


def _parse(argv: list[str]) -> tuple[str, bool, str, list[str]]:
    if len(argv) < 3 or "--" not in argv:
        _usage()
    split = argv.index("--")
    head, tail = argv[:split], argv[split + 1 :]
    if not head or not tail:
        _usage()
    out_dir, options = head[0], head[1:]
    if any(option != "--observe" for option in options):
        _usage()
    return out_dir, "--observe" in options, tail[0], tail[1:]


def _is_python(executable: str) -> bool:
    name = os.path.basename(executable).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name.startswith("python") or name.startswith("pypy"):
        return True
    try:
        return os.path.realpath(executable) == os.path.realpath(sys.executable)
    except OSError:
        return False


def _resolve(program: str) -> str:
    if os.sep in program or (os.altsep and os.altsep in program):
        return program
    import shutil

    return shutil.which(program) or program


def classify_launch(args: list[str]) -> tuple[str, list[str], str, list[str]]:
    """('script', flags, script, rest) | ('python-other', ...) | ('shell', ...) | ('process', ...).

    *args* is the child's argv with args[0] the program.
    """
    program = _resolve(args[0])
    name = os.path.basename(program).lower()
    if _is_python(program):
        flags: list[str] = []
        index = 1
        while index < len(args):
            token = args[index]
            if token == "-":
                return ("python-other", flags, "<stdin>", args[index + 1 :])
            if token == "--":
                index += 1
                break
            if not token.startswith("-") or token == "-":
                break
            body = token[1:]
            if token.startswith("--"):
                flags.append(token)
                index += 1
                continue
            for position, letter in enumerate(body):
                if letter in ("c", "m"):
                    return ("python-other", flags, f"-{letter}", args[index:])
                if letter in _FLAGS_WITH_VALUE:
                    if position + 1 < len(body):
                        flags.append(token)
                    else:
                        flags.extend([token, args[index + 1] if index + 1 < len(args) else ""])
                        index += 1
                    break
            else:
                flags.append(token)
            index += 1
        if index >= len(args):
            return ("python-other", flags, "<stdin>", [])
        return ("script", flags, args[index], args[index + 1 :])
    if name in _SHELLS or name.removesuffix(".exe") in _SHELLS:
        return ("shell", [], program, args[1:])
    if name.removesuffix(".exe") == "pwsh":
        lowered = [a.lower() for a in args[1:]]
        if any(a in ("-command", "-c", "-encodedcommand", "-ec") for a in lowered):
            return ("shell", [], program, args[1:])
    return ("process", [], program, args[1:])


def _record_child(kind: str, args: list[str], route: str) -> None:
    entry: dict[str, object] = {"kind": kind, "argv": list(args), "route": route}
    _State.processes.append(entry)
    if kind == "python-other":
        _State.incomplete.append(f"a Python child the tracer cannot follow ({route}): {args}")
    elif kind == "shell":
        entry["needs_declaration"] = True
    elif kind == "untraced-python":
        _State.incomplete.append(f"a Python child started outside subprocess ({route}): {args}")


def _wrap_popen() -> None:
    import subprocess
    from typing import Any

    original_init: Any = subprocess.Popen.__init__

    def text(value: Any) -> str:
        return os.fsdecode(os.fspath(value))

    def launch_facts(pos: tuple[Any, ...], kw: dict[str, Any]) -> dict[str, object]:
        """Review round 6, Sol B2: what the child sees besides its argv. The
        effective working directory, every environment variable that differs
        from this process's starting environment, and a preexec_fn (Python run
        in the forked child before exec, which no trace records)."""
        if len(pos) > 1:
            _State.incomplete.append(
                f"Popen with positional options the tracer does not model: {len(pos)}")
        env = kw.get("env")
        effective = dict(os.environ) if env is None else {
            text(key): text(value) for key, value in env.items()}
        changed = sorted(name for name in set(effective) | set(_State.environ)
                         if effective.get(name) != _State.environ.get(name))
        cwd = kw.get("cwd")
        if kw.get("preexec_fn") is not None:
            _State.incomplete.append("a child's preexec_fn runs Python in the forked child, "
                                     "which no trace records")
        return {"cwd": os.path.abspath(text(cwd)) if cwd is not None else os.getcwd(),
                "env_changed": changed}

    def init(self: Any, args: Any, *pos: Any, **kw: Any) -> None:
        facts = launch_facts(pos, kw)
        if kw.get("shell"):
            _record_child("shell", [str(args)], "subprocess shell=True")
            original_init(self, args, *pos, **kw)
            return
        argv = [text(a) for a in args] if isinstance(args, (list, tuple)) else [text(args)]
        executable = kw.get("executable")
        program = [text(executable), *argv[1:]] if executable else argv
        kind, flags, script, rest = classify_launch(program)
        if kind == "script":
            _State.child_count += 1
            child_out = os.path.join(_State.out_dir, f"child-{_State.child_count}")
            _State.children.append(child_out)
            tracer = os.path.abspath(__file__)
            relaunched = [program[0], *flags, tracer, child_out, "--", script, *rest]
            _State.processes.append({"kind": "python-script", "argv": argv, "traced_as": child_out,
                                     **facts})
            _State.mid_launch = True
            try:
                original_init(self, relaunched, *pos, **kw)
            finally:
                _State.mid_launch = False
            return
        _record_child(kind, argv, "subprocess")
        _State.processes[-1].update(facts)
        _State.mid_launch = True
        try:
            original_init(self, args, *pos, **kw)
        finally:
            _State.mid_launch = False

    subprocess.Popen.__init__ = init  # type: ignore[method-assign]


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event == "open":
        path, mode = args[0], args[1]
        if isinstance(path, (str, bytes)):
            name = os.fsdecode(path)
            _State.opened.append((os.path.abspath(name), str(mode)))
        return
    if event == "subprocess.Popen":
        if not _State.mid_launch:
            argv = [os.fsdecode(a) if isinstance(a, (str, bytes)) else str(a)
                    for a in (args[1] if isinstance(args[1], (list, tuple)) else [args[1]])]
            kind = "untraced-python" if argv and _is_python(_resolve(argv[0])) else "process"
            _record_child(kind, argv, "Popen not through the wrapper")
        return
    if event in ("os.posix_spawn", "os.exec", "os.spawn"):
        path = os.fsdecode(args[0]) if isinstance(args[0], (str, bytes)) else str(args[0])
        argv = [str(a) for a in args[1]] if isinstance(args[1], (list, tuple)) else [path]
        kind = "untraced-python" if _is_python(path) else classify_launch([path, *argv[1:]])[0]
        if kind in ("process", "shell"):
            _record_child(kind, [path, *argv[1:]], event)
            if kind == "shell":
                _State.incomplete.append(f"a shell started outside subprocess ({event}): {argv}")
        else:
            _record_child("untraced-python", [path, *argv[1:]], event)
        return
    if event == "os.system":
        _record_child("shell", [str(args[0])], event)
        _State.incomplete.append(f"os.system runs a shell: {args[0]!r}")
        return
    if event in ("os.fork", "os.forkpty"):
        _State.incomplete.append(f"{event}: a forked child runs Python this trace does not follow")
        return
    if event == "os.startfile":
        _record_child("process", [str(a) for a in args], event)
        _State.incomplete.append("os.startfile can start any program, Python included")


def _on_line(code: types.CodeType, line: int) -> object:
    _State.lines.setdefault(code.co_filename, set()).add(line)
    return sys.monitoring.DISABLE


def _module_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if isinstance(path, str):
            files[name] = os.path.abspath(path)
    return files


def _write() -> None:
    if _State.written:
        return
    _State.written = True
    if _State.tool_id is not None:
        sys.monitoring.set_events(_State.tool_id, 0)
    flags = {name: getattr(sys.flags, name) for name in dir(sys.flags)
             if not name.startswith("_") and not callable(getattr(sys.flags, name))}
    record = {
        "schema": 1,
        "observe_only": _State.observe,
        "script": _State.script,
        "argv": _State.argv,
        "executable": sys.executable,
        "version": list(sys.version_info),
        "flags": flags,
        "path_final": list(sys.path),
        "cwd": os.getcwd(),
        "env": {name: value for name, value in sorted(os.environ.items())
                if name in ENV_ALLOWLIST or name.startswith("GIT_")},
        "environ": dict(sorted(_State.environ.items())),
        "modules": _module_files(),
        "lines": {name: sorted(lines) for name, lines in sorted(_State.lines.items())},
        "opened": sorted(set(_State.opened)),
        "processes": _State.processes,
        "children": _State.children,
        "incomplete": _State.incomplete,
        "exit": _State.exit_code,
    }
    os.makedirs(_State.out_dir, exist_ok=True)
    target = os.path.join(_State.out_dir, "trace.json")
    with open(target + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(target + ".tmp", target)


def main() -> None:
    # Review round 6: every variable the run was given, before the script runs.
    _State.environ = dict(os.environ)
    out_dir, observe, script, rest = _parse(sys.argv[1:])
    _State.out_dir = os.path.abspath(out_dir)
    _State.observe = observe
    _State.script = script
    _State.argv = [script, *rest]
    absolute = os.path.abspath(script)
    with open(absolute, "rb") as handle:
        source = handle.read()
    code = compile(source, absolute, "exec", dont_inherit=True)
    if not sys.flags.safe_path:
        # The interpreter put this file's directory at sys.path[0]; the script's
        # directory is what `python <script>` would have put there: resolved
        # through symlinks on POSIX (CPython's realpath), the full path without
        # resolution on Windows (CPython's GetFullPathNameW).
        if os.name == "nt":
            sys.path[0] = os.path.dirname(os.path.abspath(script))
        else:
            sys.path[0] = os.path.dirname(os.path.realpath(script))
    sys.argv = [script, *rest]
    from importlib.machinery import SourceFileLoader

    module = types.ModuleType("__main__")
    module.__dict__.update(
        {
            "__file__": absolute,
            "__cached__": None,
            "__spec__": None,
            "__package__": None,
            "__loader__": SourceFileLoader("__main__", absolute),
            "__builtins__": builtins,
        }
    )
    atexit.register(_write)  # registered first: runs after the script's own handlers
    if not observe:
        sys.addaudithook(_audit)
        _wrap_popen()
        monitoring = sys.monitoring
        free = [monitoring.COVERAGE_ID, *range(6)]
        tool = next((t for t in free if monitoring.get_tool(t) is None), None)
        if tool is None:
            raise SystemExit("tracer: no sys.monitoring tool id is free")
        monitoring.use_tool_id(tool, _TOOL_NAME)
        monitoring.register_callback(tool, monitoring.events.LINE, _on_line)
        monitoring.set_events(tool, monitoring.events.LINE)
        _State.tool_id = tool
    sys.modules["__main__"] = module
    try:
        exec(code, module.__dict__)
    except SystemExit as exc:
        _State.exit_code = _exit_status(exc.code)
        raise
    except BaseException:
        _State.exit_code = 1
        raise
    else:
        _State.exit_code = 0


def _exit_status(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


if __name__ == "__main__":
    main()
