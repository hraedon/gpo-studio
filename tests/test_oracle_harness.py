"""Tests for the Plan 033 WP-0 dry-run orchestrator and fixture recipes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpo_studio.oracle_evidence import OracleEvidenceError
from gpo_studio.oracle_harness import (
    DryRunConfig,
    FixtureRecipe,
    RecipeError,
    discover_recipes,
    generate_dry_run_manifest,
    load_recipe,
    parse_recipe,
    run_dry_run,
    run_dry_run_suite,
    validate_dry_run_manifest,
)

RECIPES_DIR = Path(__file__).resolve().parent / "fixtures" / "recipes"


def _minimal_recipe() -> dict[str, object]:
    return {
        "fixture_id": "test-fixture",
        "description": "A test fixture",
        "gpo_name_prefix": "Test",
        "settings": [
            {
                "side": "computer",
                "hive": "HKLM",
                "key": "SOFTWARE\\Test",
                "value_name": "Value",
                "value_type": "REG_SZ",
                "value": "data",
                "action": "set",
            }
        ],
    }


# --- recipe parsing -------------------------------------------------------


def test_parse_minimal_recipe() -> None:
    recipe = parse_recipe(_minimal_recipe())
    assert recipe.fixture_id == "test-fixture"
    assert len(recipe.settings) == 1
    assert recipe.settings[0].side == "computer"
    assert recipe.settings[0].action == "set"


def test_parse_recipe_rejects_missing_fixture_id() -> None:
    raw = _minimal_recipe()
    del raw["fixture_id"]
    with pytest.raises(RecipeError, match="fixture_id"):
        parse_recipe(raw)


def test_parse_recipe_rejects_empty_settings() -> None:
    raw = _minimal_recipe()
    raw["settings"] = []
    with pytest.raises(RecipeError, match="non-empty"):
        parse_recipe(raw)


def test_parse_recipe_rejects_invalid_side() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["side"] = "both"
    with pytest.raises(RecipeError, match="side"):
        parse_recipe(raw)


def test_parse_recipe_rejects_invalid_hive() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["hive"] = "HKCR"
    with pytest.raises(RecipeError, match="hive"):
        parse_recipe(raw)


def test_parse_recipe_rejects_invalid_value_type() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["value_type"] = "REG_LINK"
    with pytest.raises(RecipeError, match="value_type"):
        parse_recipe(raw)


def test_parse_recipe_rejects_invalid_action() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["action"] = "create"
    with pytest.raises(RecipeError, match="action"):
        parse_recipe(raw)


def test_parse_recipe_rejects_non_object() -> None:
    with pytest.raises(RecipeError, match="JSON object"):
        parse_recipe("not a dict")


def test_parse_recipe_rejects_unsupported_links() -> None:
    raw = _minimal_recipe()
    raw["links"] = [{"target_dn": "OU=Test,DC=example", "enabled": True, "enforced": False}]
    with pytest.raises(RecipeError, match="links"):
        parse_recipe(raw)


def test_parse_recipe_rejects_unsupported_security_filters() -> None:
    raw = _minimal_recipe()
    raw["security_filters"] = [{"principal": "TestGroup", "permission": "apply"}]
    with pytest.raises(RecipeError, match="security_filters"):
        parse_recipe(raw)


def test_parse_recipe_rejects_unsupported_wmi_filter() -> None:
    raw = _minimal_recipe()
    raw["wmi_filter"] = {"name": "TestFilter", "query": "SELECT * FROM Win32_OperatingSystem"}
    with pytest.raises(RecipeError, match="wmi_filter"):
        parse_recipe(raw)


def test_parse_recipe_accepts_empty_optional_collections() -> None:
    raw = _minimal_recipe()
    raw["links"] = []
    raw["security_filters"] = []
    raw["wmi_filter"] = None
    recipe = parse_recipe(raw)
    assert recipe.links == ()
    assert recipe.security_filters == ()
    assert recipe.wmi_filter is None


def test_parse_recipe_rejects_delete_action() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["action"] = "delete"
    with pytest.raises(RecipeError, match="not yet supported"):
        parse_recipe(raw)


def test_parse_recipe_rejects_side_hive_mismatch() -> None:
    raw = _minimal_recipe()
    assert isinstance(raw["settings"], list)
    raw["settings"][0]["side"] = "user"
    raw["settings"][0]["hive"] = "HKLM"
    with pytest.raises(RecipeError, match="inconsistent"):
        parse_recipe(raw)


def test_parse_recipe_rejects_bad_links_type() -> None:
    raw = _minimal_recipe()
    raw["links"] = "not-a-list"
    with pytest.raises(RecipeError, match="links"):
        parse_recipe(raw)


def test_parse_recipe_rejects_bad_wmi_filter_type() -> None:
    raw = _minimal_recipe()
    raw["wmi_filter"] = "not-an-object"
    with pytest.raises(RecipeError, match="wmi_filter"):
        parse_recipe(raw)


# --- recipe file loading --------------------------------------------------


def test_load_recipe_from_file(tmp_path: Path) -> None:
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(_minimal_recipe()), encoding="utf-8")
    recipe = load_recipe(path)
    assert recipe.fixture_id == "test-fixture"


def test_load_recipe_rejects_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RecipeError, match="invalid JSON"):
        load_recipe(path)


def test_load_recipe_rejects_missing_file() -> None:
    with pytest.raises(RecipeError, match="cannot read"):
        load_recipe(Path("/nonexistent/recipe.json"))


def test_committed_recipes_parse() -> None:
    """Every committed recipe must parse without error."""
    if not RECIPES_DIR.is_dir():
        pytest.skip("no committed recipes directory")
    recipes = discover_recipes(RECIPES_DIR)
    assert len(recipes) >= 1
    for recipe in recipes:
        assert recipe.fixture_id
        assert recipe.settings


# --- dry-run manifest generation ------------------------------------------


def _recipe() -> FixtureRecipe:
    return parse_recipe(_minimal_recipe())


def test_dry_run_manifest_is_valid() -> None:
    config = DryRunConfig(recipe=_recipe())
    manifest = run_dry_run(config)
    assert manifest["schema_version"] == 1
    assert manifest["capability"]["evidence_state"] == "inconclusive"


def test_dry_run_manifest_has_one_comparison_per_setting() -> None:
    config = DryRunConfig(recipe=_recipe())
    manifest = run_dry_run(config)
    comparisons = manifest["comparisons"]
    assert isinstance(comparisons, list)
    assert len(comparisons) == 1


def test_dry_run_manifest_cleanup_is_clean() -> None:
    config = DryRunConfig(recipe=_recipe())
    manifest = run_dry_run(config)
    cleanup = manifest["cleanup"]
    assert isinstance(cleanup, dict)
    assert cleanup["succeeded"] is True
    assert cleanup["state_restored"] is True
    assert cleanup["failures"] == []


def test_dry_run_manifest_rejects_when_mutated() -> None:
    config = DryRunConfig(recipe=_recipe())
    manifest = generate_dry_run_manifest(config)
    manifest["schema_version"] = 99
    with pytest.raises(OracleEvidenceError):
        validate_dry_run_manifest(manifest)


def test_dry_run_manifest_passing_state_rejected_with_synthetic_commands() -> None:
    """A manifest claiming 'pass' with synthetic data must fail validation
    because the pass-state invariants reject synthetic identifiers."""
    config = DryRunConfig(recipe=_recipe())
    manifest = generate_dry_run_manifest(config)
    assert isinstance(manifest["capability"], dict)
    manifest["capability"]["evidence_state"] = "pass"
    with pytest.raises(OracleEvidenceError):
        validate_dry_run_manifest(manifest)


def test_dry_run_with_custom_config() -> None:
    config = DryRunConfig(
        recipe=_recipe(),
        server_build="custom-build",
        locale="en-GB",
        dirty=False,
    )
    manifest = run_dry_run(config)
    env = manifest["environment"]
    assert isinstance(env, dict)
    assert env["server_build"] == "custom-build"
    assert env["locale"] == "en-GB"
    source = manifest["source"]
    assert isinstance(source, dict)
    assert source["dirty"] is False


def test_generate_dry_run_manifest_structure() -> None:
    config = DryRunConfig(recipe=_recipe())
    manifest = generate_dry_run_manifest(config)
    assert "run_id" in manifest
    assert "started_at" in manifest
    assert "completed_at" in manifest
    assert "source" in manifest
    assert "fixture" in manifest
    assert "environment" in manifest
    assert "tools" in manifest
    assert "artifacts" in manifest
    assert "commands" in manifest
    assert "comparisons" in manifest
    assert "cleanup" in manifest
    assert "capability" in manifest


# --- suite runner ---------------------------------------------------------


def test_run_dry_run_suite(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "a.json").write_text(json.dumps(_minimal_recipe()), encoding="utf-8")
    second = _minimal_recipe()
    assert isinstance(second, dict)
    second["fixture_id"] = "second-fixture"
    second["gpo_name_prefix"] = "Second"
    (recipe_dir / "b.json").write_text(json.dumps(second), encoding="utf-8")

    manifests = run_dry_run_suite(recipe_dir)
    assert len(manifests) == 2
    fixture_ids = {
        m["fixture"]["fixture_id"]
        for m in manifests
        if isinstance(m.get("fixture"), dict)
    }
    assert fixture_ids == {"test-fixture", "second-fixture"}


def test_run_dry_run_suite_empty_dir(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "empty"
    recipe_dir.mkdir()
    assert run_dry_run_suite(recipe_dir) == []


def test_run_dry_run_suite_with_overrides(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "a.json").write_text(json.dumps(_minimal_recipe()), encoding="utf-8")
    manifests = run_dry_run_suite(recipe_dir, config_overrides={"locale": "fr-FR"})
    env = manifests[0]["environment"]
    assert isinstance(env, dict)
    assert env["locale"] == "fr-FR"
