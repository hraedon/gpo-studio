"""Plan 033 WP-0 dry-run orchestrator and fixture recipe loader.

This module drives the Windows external-oracle harness without requiring a
live Windows environment.  It loads fixture recipes, generates synthetic
manifests for dry-run validation, and validates the resulting manifest
against the oracle evidence contract.

The dry-run path produces a complete evidence record with synthetic hashes
and ``inconclusive`` evidence state, proving the harness wiring is correct
before a live lab run.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .oracle_evidence import (
    MANIFEST_SCHEMA_VERSION,
    NORMALIZER_VERSION,
    parse_oracle_manifest,
)

RECIPE_SCHEMA_VERSION = 1
_SYNTHETIC_HASH = "0" * 64


class RecipeError(ValueError):
    """Raised when a fixture recipe fails validation."""


@dataclass(frozen=True, slots=True)
class RecipeSetting:
    side: str
    hive: str
    key: str
    value_name: str
    value_type: str
    value: str | int | list[str] | None
    action: str


@dataclass(frozen=True, slots=True)
class FixtureRecipe:
    fixture_id: str
    description: str
    gpo_name_prefix: str
    settings: tuple[RecipeSetting, ...]
    links: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    security_filters: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    wmi_filter: Mapping[str, Any] | None = None


def _require_str(data: Mapping[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RecipeError(f"{label}.{key} must be a non-empty string")
    return value


def _parse_setting(raw: object, index: int) -> RecipeSetting:
    label = f"settings[{index}]"
    if not isinstance(raw, dict):
        raise RecipeError(f"{label} must be an object")
    side = raw.get("side")
    if side not in ("computer", "user"):
        raise RecipeError(f"{label}.side must be computer or user")
    hive = raw.get("hive")
    if hive not in ("HKLM", "HKCU"):
        raise RecipeError(f"{label}.hive must be HKLM or HKCU")
    expected_hive = "HKLM" if side == "computer" else "HKCU"
    if hive != expected_hive:
        raise RecipeError(
            f"{label} side {side!r} is inconsistent with hive {hive!r}; "
            f"computer settings must use HKLM and user settings must use HKCU"
        )
    value_type = raw.get("value_type")
    valid_types = (
        "REG_SZ",
        "REG_EXPAND_SZ",
        "REG_BINARY",
        "REG_DWORD",
        "REG_MULTI_SZ",
        "REG_QWORD",
    )
    if value_type not in valid_types:
        raise RecipeError(f"{label}.value_type must be one of {valid_types}")
    action = raw.get("action")
    if action != "set":
        raise RecipeError(
            f"{label}.action must be 'set'; {action!r} is not yet supported "
            "by the Windows oracle harness"
        )
    value_name = raw.get("value_name", "")
    if not isinstance(value_name, str):
        raise RecipeError(f"{label}.value_name must be a string")
    return RecipeSetting(
        side=side,
        hive=hive,
        key=_require_str(raw, "key", label),
        value_name=value_name,
        value_type=value_type,
        value=raw.get("value"),
        action=action,
    )


def parse_recipe(raw: object) -> FixtureRecipe:
    """Parse and validate a fixture recipe from parsed JSON."""
    if not isinstance(raw, dict):
        raise RecipeError("recipe must be a JSON object")
    fixture_id = _require_str(raw, "fixture_id", "recipe")
    description = _require_str(raw, "description", "recipe")
    gpo_name_prefix = _require_str(raw, "gpo_name_prefix", "recipe")
    settings_raw = raw.get("settings")
    if not isinstance(settings_raw, list) or not settings_raw:
        raise RecipeError("recipe.settings must be a non-empty array")
    settings = tuple(
        _parse_setting(item, i) for i, item in enumerate(settings_raw)
    )
    links_raw = raw.get("links", [])
    if not isinstance(links_raw, list):
        raise RecipeError("recipe.links must be an array")
    if links_raw:
        raise RecipeError(
            "recipe.links is not yet supported by the Windows oracle harness; "
            "remove it until SOM link operations are implemented"
        )
    links = tuple(links_raw)
    filters_raw = raw.get("security_filters", [])
    if not isinstance(filters_raw, list):
        raise RecipeError("recipe.security_filters must be an array")
    if filters_raw:
        raise RecipeError(
            "recipe.security_filters is not yet supported by the Windows oracle "
            "harness; remove it until security filtering is implemented"
        )
    security_filters = tuple(filters_raw)
    wmi_filter = raw.get("wmi_filter")
    if wmi_filter is not None and not isinstance(wmi_filter, dict):
        raise RecipeError("recipe.wmi_filter must be an object or null")
    if wmi_filter is not None:
        raise RecipeError(
            "recipe.wmi_filter is not yet supported by the Windows oracle "
            "harness; remove it until WMI filtering is implemented"
        )
    return FixtureRecipe(
        fixture_id=fixture_id,
        description=description,
        gpo_name_prefix=gpo_name_prefix,
        settings=settings,
        links=links,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
    )


def load_recipe(path: Path) -> FixtureRecipe:
    """Load and validate a fixture recipe from a JSON file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecipeError(f"cannot read recipe {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RecipeError(f"invalid JSON in recipe {path}: {exc}") from exc
    return parse_recipe(raw)


@dataclass(frozen=True, slots=True)
class DryRunConfig:
    """Configuration for a synthetic dry-run manifest."""

    recipe: FixtureRecipe
    server_build: str = "synthetic-server-build"
    client_build: str = "synthetic-client-build"
    powershell_edition: str = "Desktop"
    powershell_version: str = "5.1.synthetic"
    group_policy_module_version: str = "synthetic-module-version"
    gpmc_version: str = "synthetic-gpmc-version"
    locale: str = "en-US"
    lgpo_sha256: str = _SYNTHETIC_HASH
    source_commit: str = "0" * 40
    dirty: bool = True


def generate_dry_run_manifest(config: DryRunConfig) -> dict[str, object]:
    """Generate a synthetic oracle manifest for dry-run validation.

    The manifest uses ``inconclusive`` evidence state and synthetic hashes.
    It exercises the full manifest structure so that the harness wiring can
    be validated without a live Windows environment.
    """
    recipe = config.recipe
    run_id = f"dry-run-{recipe.fixture_id}-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    started = now.isoformat()
    completed = now.isoformat()

    settings_hash = _SYNTHETIC_HASH
    input_hash = _SYNTHETIC_HASH
    output_hash = _SYNTHETIC_HASH

    comparisons: list[dict[str, object]] = []
    for i, _setting in enumerate(recipe.settings):
        assertion_id = f"dry-run-setting-{i}"
        comparisons.append(
            {
                "assertion_id": assertion_id,
                "oracle": "dry-run-synthetic",
                "boundary_owner": "gpo-backup-content",
                "normalizer_version": NORMALIZER_VERSION,
                "expected_artifact_id": "synthetic-input",
                "observed_artifact_id": "synthetic-output",
                "expected_sha256": settings_hash,
                "observed_sha256": settings_hash,
                "equal": True,
                "differences": [],
            }
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started,
        "completed_at": completed,
        "source": {
            "commit": config.source_commit,
            "dirty": config.dirty,
        },
        "fixture": {
            "fixture_id": recipe.fixture_id,
            "generation_recipe": f"fixtures/recipes/{recipe.fixture_id}.json",
        },
        "environment": {
            "server_build": config.server_build,
            "client_build": config.client_build,
            "powershell_edition": config.powershell_edition,
            "powershell_version": config.powershell_version,
            "group_policy_module_version": config.group_policy_module_version,
            "gpmc_version": config.gpmc_version,
            "locale": config.locale,
            "lgpo_sha256": config.lgpo_sha256,
        },
        "tools": [
            {
                "name": "dry-run-orchestrator",
                "version": "1.0.0",
                "sha256": None,
            },
        ],
        "artifacts": [
            {
                "artifact_id": "synthetic-input",
                "role": "input",
                "relative_path": "artifacts/input.xml",
                "sha256": input_hash,
                "size_bytes": 0,
            },
            {
                "artifact_id": "synthetic-output",
                "role": "output",
                "relative_path": "artifacts/output.xml",
                "sha256": output_hash,
                "size_bytes": 0,
            },
        ],
        "commands": [
            {
                "command_id": "dry-run-noop",
                "command_line": "echo dry-run",
                "exit_code": 0,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "relevant_event_ids": [],
            },
        ],
        "comparisons": comparisons,
        "cleanup": {
            "attempted": True,
            "succeeded": True,
            "state_restored": True,
            "removed_resources": ["synthetic-gpo"],
            "failures": [],
        },
        "capability": {
            "matrix_row": f"dry-run.{recipe.fixture_id}",
            "evidence_state": "inconclusive",
        },
    }


def validate_dry_run_manifest(raw: object) -> None:
    """Validate a dry-run manifest against the oracle evidence contract.

    Raises :class:`OracleEvidenceError` on any violation.
    """
    parse_oracle_manifest(raw)


def run_dry_run(config: DryRunConfig) -> dict[str, object]:
    """Generate and validate a dry-run manifest, returning it if valid."""
    manifest = generate_dry_run_manifest(config)
    validate_dry_run_manifest(manifest)
    return manifest


def discover_recipes(directory: Path) -> list[FixtureRecipe]:
    """Load all ``*.json`` fixture recipes from a directory."""
    recipes: list[FixtureRecipe] = []
    for path in sorted(directory.glob("*.json")):
        recipes.append(load_recipe(path))
    return recipes


def run_dry_run_suite(
    recipe_dir: Path,
    *,
    config_overrides: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """Run a dry-run for every recipe in a directory.

    Returns a list of validated manifests, one per recipe.
    """
    recipes = discover_recipes(recipe_dir)
    manifests: list[dict[str, object]] = []
    for recipe in recipes:
        overrides: dict[str, object] = dict(config_overrides or {})
        config = DryRunConfig(recipe=recipe, **overrides)  # type: ignore[arg-type]
        manifests.append(run_dry_run(config))
    return manifests
