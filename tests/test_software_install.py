from __future__ import annotations

import pytest

from gpo_studio.model import ValidationError
from gpo_studio.software_install import (
    CategoryTree,
    DeploymentState,
    DeploymentStatus,
    InstallationScript,
    MsiPackage,
    SoftwareCategory,
    UpgradePolicy,
    UpgradeRule,
    transition_deployment,
    valid_transitions,
)

VALID_GUID = "{12345678-1234-1234-1234-123456789012}"
OTHER_GUID = "{AAAAAAAA-1234-1234-1234-123456789012}"


def test_msi_package_valid() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code=VALID_GUID,
        source_path="\\\\server\\share\\app.msi",
    )
    assert package.validate() == ()


def test_msi_package_empty_name_error() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="",
        product_code=VALID_GUID,
        source_path="\\\\server\\share\\app.msi",
    )
    issues = package.validate()
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_display_name"


def test_msi_package_invalid_guid_error() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code="not-a-guid",
        source_path="\\\\server\\share\\app.msi",
    )
    issues = package.validate()
    assert any(i.code == "invalid_product_code" and i.severity == "error" for i in issues)


def test_msi_package_non_unc_source_warning() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code=VALID_GUID,
        source_path="C:\\app.msi",
    )
    issues = package.validate()
    assert any(i.code == "source_path_not_unc" and i.severity == "warning" for i in issues)


def test_msi_package_remove_and_auto_install_warning() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code=VALID_GUID,
        source_path="\\\\server\\share\\app.msi",
        deployment_action="remove",
        auto_install=True,
    )
    issues = package.validate()
    assert any(
        i.code == "contradictory_deployment_action" and i.severity == "warning"
        for i in issues
    )


def test_msi_package_transform_path_traversal_error() -> None:
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code=VALID_GUID,
        source_path="\\\\server\\share\\app.msi",
        transforms=("..\\evil.mst",),
    )
    issues = package.validate()
    assert any(i.code == "transform_path_traversal" and i.severity == "error" for i in issues)


def test_software_category_valid() -> None:
    category = SoftwareCategory(category_id="cat-1", name="Category 1")
    assert category.validate() == ()


def test_software_category_empty_name_error() -> None:
    category = SoftwareCategory(category_id="cat-1", name="")
    issues = category.validate()
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_category_name"


def test_category_tree_lookups() -> None:
    root = SoftwareCategory(category_id="root", name="Root")
    child = SoftwareCategory(category_id="child", name="Child", parent_id="root")
    grandchild = SoftwareCategory(
        category_id="grandchild", name="Grandchild", parent_id="child"
    )
    tree = CategoryTree(categories=(root, child, grandchild))

    assert tree.get_category("child") == child
    assert tree.get_category("missing") is None
    assert tree.get_children("root") == (child,)
    assert tree.get_children("child") == (grandchild,)
    assert tree.get_root_categories() == (root,)
    assert tree.get_path("grandchild") == (root, child, grandchild)


def test_category_tree_duplicate_id_error() -> None:
    cat = SoftwareCategory(category_id="dup", name="Duplicate")
    tree = CategoryTree(categories=(cat, cat))
    issues = tree.validate()
    assert any(i.code == "duplicate_category_id" and i.severity == "error" for i in issues)


def test_category_tree_missing_parent_error() -> None:
    cat = SoftwareCategory(category_id="orphan", name="Orphan", parent_id="missing")
    tree = CategoryTree(categories=(cat,))
    issues = tree.validate()
    assert any(i.code == "missing_parent_category" and i.severity == "error" for i in issues)


def test_category_tree_circular_reference_error() -> None:
    a = SoftwareCategory(category_id="a", name="A", parent_id="b")
    b = SoftwareCategory(category_id="b", name="B", parent_id="a")
    tree = CategoryTree(categories=(a, b))
    issues = tree.validate()
    assert any(
        i.code == "circular_category_reference" and i.severity == "error" for i in issues
    )


def test_category_tree_depth_warning() -> None:
    categories: list[SoftwareCategory] = []
    parent_id = ""
    for depth in range(1, 13):
        category_id = f"cat-{depth:02d}"
        categories.append(
            SoftwareCategory(
                category_id=category_id,
                name=f"Category {depth}",
                parent_id=parent_id,
            )
        )
        parent_id = category_id
    tree = CategoryTree(categories=tuple(categories))
    issues = tree.validate()
    assert any(i.code == "category_tree_too_deep" and i.severity == "warning" for i in issues)


def test_upgrade_rule_valid() -> None:
    rule = UpgradeRule(rule_id="rule-1", target_product_code=VALID_GUID)
    assert rule.validate() == ()


def test_upgrade_rule_empty_product_code_error() -> None:
    rule = UpgradeRule(rule_id="rule-1", target_product_code="")
    issues = rule.validate()
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_target_product_code"


def test_upgrade_rule_invalid_guid_error() -> None:
    rule = UpgradeRule(rule_id="rule-1", target_product_code="not-a-guid")
    issues = rule.validate()
    assert any(
        i.code == "invalid_target_product_code" and i.severity == "error" for i in issues
    )


def test_upgrade_policy_rules_for_product() -> None:
    rule_a = UpgradeRule(rule_id="a", target_product_code=VALID_GUID)
    rule_b = UpgradeRule(rule_id="b", target_product_code=OTHER_GUID)
    policy = UpgradePolicy(rules=(rule_a, rule_b))
    assert policy.rules_for_product(VALID_GUID) == (rule_a,)
    assert policy.rules_for_product(OTHER_GUID) == (rule_b,)
    assert policy.rules_for_product("missing") == ()


def test_valid_transitions_all_states() -> None:
    assert valid_transitions("pending") == ("deployed", "removed", "failed")
    assert valid_transitions("deployed") == ("upgrading", "removing", "orphaned", "failed")
    assert valid_transitions("upgrading") == ("deployed", "failed")
    assert valid_transitions("removing") == ("removed", "failed")
    assert valid_transitions("removed") == ()
    assert valid_transitions("failed") == ("pending", "removed")
    assert valid_transitions("orphaned") == ("removing", "removed")


@pytest.mark.parametrize(
    ("state", "next_state"),
    [
        ("pending", "deployed"),
        ("deployed", "upgrading"),
        ("upgrading", "deployed"),
        ("deployed", "removing"),
        ("removing", "removed"),
        ("failed", "pending"),
        ("orphaned", "removed"),
    ],
)
def test_transition_deployment_valid(state: DeploymentState, next_state: DeploymentState) -> None:
    status = DeploymentStatus(package_id="pkg-1", state=state)
    new_status = transition_deployment(status, next_state)
    assert new_status.state == next_state
    assert new_status.package_id == status.package_id


@pytest.mark.parametrize(
    ("state", "next_state"),
    [
        ("pending", "pending"),
        ("deployed", "pending"),
        ("removed", "deployed"),
        ("failed", "orphaned"),
        ("orphaned", "deployed"),
    ],
)
def test_transition_deployment_invalid(state: DeploymentState, next_state: DeploymentState) -> None:
    status = DeploymentStatus(package_id="pkg-1", state=state)
    with pytest.raises(ValidationError) as exc_info:
        transition_deployment(status, next_state)
    assert exc_info.value.issues[0].code == "invalid_state_transition"


def test_installation_script_valid() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="artifact-1",
    )
    assert script.validate() == ()


def test_installation_script_empty_artifact_error() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="",
    )
    issues = script.validate()
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_artifact_id"


def test_installation_script_contradictory_flags_warning() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="artifact-1",
        ignore_exit_code=True,
        expected_exit_codes=(0, 3010),
    )
    issues = script.validate()
    assert any(
        i.code == "contradictory_exit_code_handling" and i.severity == "warning"
        for i in issues
    )


def test_installation_script_no_success_codes_warning() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="artifact-1",
        expected_exit_codes=(),
    )
    issues = script.validate()
    assert any(i.code == "no_success_exit_codes" and i.severity == "warning" for i in issues)


def test_installation_script_unsafe_parameters_error() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="artifact-1",
        parameters="foo | bar",
    )
    issues = script.validate()
    assert any(
        i.code == "unquoted_metacharacter"
        and i.severity == "error"
        and i.path == "installation_script.script-1.parameters"
        for i in issues
    )


def test_installation_script_safe_parameters_pass() -> None:
    script = InstallationScript(
        script_id="script-1",
        script_type="pre_install",
        artifact_id="artifact-1",
        parameters="-Quiet -NoRestart",
    )
    issues = script.validate()
    assert not any(i.code == "unquoted_metacharacter" for i in issues)


def test_full_lifecycle_integration() -> None:
    """Create a package and walk it through deploy -> upgrade -> remove."""
    package = MsiPackage(
        package_id="pkg-1",
        display_name="Test App",
        product_code=VALID_GUID,
        source_path="\\\\server\\share\\app.msi",
    )
    assert package.validate() == ()

    status = DeploymentStatus(package_id=package.package_id, state="pending")
    status = transition_deployment(status, "deployed")
    assert status.state == "deployed"

    status = transition_deployment(status, "upgrading")
    assert status.state == "upgrading"

    status = transition_deployment(status, "deployed")
    assert status.state == "deployed"

    status = transition_deployment(status, "removing")
    assert status.state == "removing"

    status = transition_deployment(status, "removed")
    assert status.state == "removed"
