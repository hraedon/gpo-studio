"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .model import WorkspaceError
from .workspace_ops import (
    backup_workspace,
    full_integrity_check,
    quick_check,
    restore_workspace,
)


def _cmd_workspace_check(args: argparse.Namespace) -> int:
    import sqlite3

    db_path = args.database
    if not os.path.exists(db_path):
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        result = full_integrity_check(conn) if args.full else quick_check(conn)
    except Exception as e:
        print(f"error: integrity check failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if result.ok:
        print(f"ok: {db_path} passed integrity check")
        return 0
    else:
        print(f"fail: {db_path} has integrity errors:", file=sys.stderr)
        for err in result.errors:
            print(f"  {err}", file=sys.stderr)
        return 1


def _cmd_workspace_backup(args: argparse.Namespace) -> int:
    db_path = args.database
    backup_path = args.output
    try:
        meta = backup_workspace(db_path, backup_path)
    except WorkspaceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"backup created: {backup_path}")
    print(f"  schema_version: {meta.schema_version}")
    print(f"  app_version: {meta.app_version}")
    print(f"  gpo_count: {meta.gpo_count}")
    print(f"  revision_count: {meta.revision_count}")
    print(f"  source_db_sha256: {meta.source_db_sha256}")
    print(f"  backup_db_sha256: {meta.backup_db_sha256}")
    return 0


def _cmd_workspace_restore(args: argparse.Namespace) -> int:
    backup_path = args.backup
    target_path = args.target
    try:
        restored = restore_workspace(backup_path, target_path, replace=args.replace)
    except WorkspaceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"restored: {restored}")
    if args.replace:
        print("  (old database retained with .bak suffix)")
    else:
        print("  (restored to new path — verify before switching)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gpo-studio",
        description="Offline-first Group Policy authoring workbench",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: run mode)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: run mode)")
    parser.add_argument("--database", default=None, help="Workspace database path")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start the web workbench")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8765)
    run_parser.add_argument("--database", default="gpo-studio.db")

    ws_parser = subparsers.add_parser("workspace", help="Workspace management commands")
    ws_sub = ws_parser.add_subparsers(dest="workspace_command", required=True)

    check_parser = ws_sub.add_parser("check", help="Run an integrity check on the workspace")
    check_parser.add_argument("--database", default="gpo-studio.db")
    check_parser.add_argument(
        "--full", action="store_true", help="Run a full integrity check (slower)"
    )

    backup_parser = ws_sub.add_parser("backup", help="Create a backup of the workspace")
    backup_parser.add_argument("--database", default="gpo-studio.db")
    backup_parser.add_argument(
        "--output", required=True, help="Output path for the backup .db file"
    )

    restore_parser = ws_sub.add_parser("restore", help="Restore a workspace from a backup")
    restore_parser.add_argument("backup", help="Path to the backup .db file")
    restore_parser.add_argument("target", help="Path for the restored database")
    restore_parser.add_argument(
        "--replace",
        action="store_true",
        help="If target exists, rename it to .bak before restoring",
    )

    args = parser.parse_args()

    if args.command == "workspace":
        if args.workspace_command == "check":
            code = _cmd_workspace_check(args)
            sys.exit(code)
        elif args.workspace_command == "backup":
            code = _cmd_workspace_backup(args)
            sys.exit(code)
        elif args.workspace_command == "restore":
            code = _cmd_workspace_restore(args)
            sys.exit(code)
    elif args.command == "run":
        db = args.database
        host = args.host
        port = args.port
    else:
        db = args.database or "gpo-studio.db"
        host = args.host
        port = args.port
    os.environ["GPO_STUDIO_DB"] = db
    uvicorn.run("gpo_studio.api:app", host=host, port=port)


if __name__ == "__main__":
    main()
