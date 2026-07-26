"""Software installation (MSI deployment) model for GPO Studio.

Models Group Policy software installation packages, categories, upgrade rules,
installation scripts, and a small deployment state machine used to track the
lifecycle of a deployment inside the workspace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .model import ValidationError, ValidationIssue
from .script_policy import validate_parameters

DeploymentAction = Literal["assign", "publish", "remove"]
DeploymentContext = Literal["computer", "user"]

UpgradeAction = Literal["upgrade", "replace", "remove_previous"]

DeploymentState = Literal[
    "pending",
    "deployed",
    "upgrading",
    "removing",
    "removed",
    "failed",
    "orphaned",
]

_GUID_PATTERN = re.compile(
    r"^\{?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}?$"
)


def _is_valid_guid(value: str) -> bool:
    """Return True if *value* looks like a GUID with optional braces."""
    return bool(_GUID_PATTERN.match(value))


def _has_path_traversal(value: str) -> bool:
    """Return True if a relative transform/file name contains traversal."""
    return ".." in value or "/" in value or "\\" in value


@dataclass(frozen=True, slots=True)
class MsiPackage:
    package_id: str
    display_name: str
    product_code: str = ""
    upgrade_code: str = ""
    package_code: str = ""
    version: str = ""
    publisher: str = ""
    language: str = ""
    source_path: str = ""
    deployment_action: DeploymentAction = "assign"
    deployment_context: DeploymentContext = "computer"
    ignore_language: bool = False
    uninstall_on_removal: bool = True
    auto_install: bool = False
    include_sub_apps: bool = False
    categories: tuple[str, ...] = ()
    script_id: str = ""
    transforms: tuple[str, ...] = ()
    unknown_attrs: tuple[tuple[str, str], ...] = ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate MSI package deployment."""
        issues: list[ValidationIssue] = []
        base_path = f"package.{self.package_id}"

        if not self.display_name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_display_name",
                    message="display_name must not be empty",
                    path=f"{base_path}.display_name",
                )
            )

        if not self.product_code:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_product_code",
                    message="product_code is required for deployment",
                    path=f"{base_path}.product_code",
                )
            )
        elif not _is_valid_guid(self.product_code):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="invalid_product_code",
                    message="product_code must be a valid GUID",
                    path=f"{base_path}.product_code",
                )
            )

        if not self.source_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_source_path",
                    message="source_path must not be empty",
                    path=f"{base_path}.source_path",
                )
            )
        elif not self.source_path.startswith("\\\\"):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="source_path_not_unc",
                    message="source_path should be a UNC path",
                    path=f"{base_path}.source_path",
                )
            )

        if self.deployment_action == "remove" and self.auto_install:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="contradictory_deployment_action",
                    message="remove action with auto_install=True is contradictory",
                    path=f"{base_path}.deployment_action",
                )
            )

        for transform in self.transforms:
            if _has_path_traversal(transform):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="transform_path_traversal",
                        message=f"transform {transform!r} contains path traversal",
                        path=f"{base_path}.transforms",
                    )
                )

        return tuple(issues)


@dataclass(frozen=True, slots=True)
class SoftwareCategory:
    category_id: str
    name: str
    parent_id: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate category."""
        issues: list[ValidationIssue] = []
        base_path = f"category.{self.category_id}"

        if not self.category_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_category_id",
                    message="category_id must not be empty",
                    path=f"{base_path}.category_id",
                )
            )

        if not self.name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_category_name",
                    message="name must not be empty",
                    path=f"{base_path}.name",
                )
            )

        return tuple(issues)


@dataclass(frozen=True, slots=True)
class CategoryTree:
    categories: tuple[SoftwareCategory, ...] = field(default_factory=tuple)

    def _by_id(self) -> dict[str, SoftwareCategory]:
        return {category.category_id: category for category in self.categories}

    def get_category(self, category_id: str) -> SoftwareCategory | None:
        """Return the category with *category_id*, or None."""
        return self._by_id().get(category_id)

    def get_children(self, parent_id: str) -> tuple[SoftwareCategory, ...]:
        """Return all direct children of *parent_id*."""
        return tuple(
            category for category in self.categories if category.parent_id == parent_id
        )

    def get_root_categories(self) -> tuple[SoftwareCategory, ...]:
        """Return all top-level categories (no parent)."""
        return tuple(category for category in self.categories if not category.parent_id)

    def get_path(self, category_id: str) -> tuple[SoftwareCategory, ...]:
        """Return the full path from the root to *category_id*."""
        by_id = self._by_id()
        path: list[SoftwareCategory] = []
        current_id = category_id
        seen: set[str] = set()

        while current_id:
            if current_id in seen or current_id not in by_id:
                break
            seen.add(current_id)
            category = by_id[current_id]
            path.append(category)
            current_id = category.parent_id

        return tuple(reversed(path))

    def _depth(self, category_id: str) -> int:
        """Compute the depth of *category_id* in the tree (root == 1)."""
        by_id = self._by_id()
        depth = 0
        current_id = category_id
        seen: set[str] = set()

        while current_id:
            if current_id in seen or current_id not in by_id:
                break
            seen.add(current_id)
            depth += 1
            current_id = by_id[current_id].parent_id

        return depth

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate category tree."""
        issues: list[ValidationIssue] = []
        seen_ids: set[str] = set()
        by_id = self._by_id()

        for category in self.categories:
            if category.category_id in seen_ids:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate_category_id",
                        message=f"duplicate category_id {category.category_id!r}",
                        path=f"category_tree.{category.category_id}",
                    )
                )
            seen_ids.add(category.category_id)

        for category in self.categories:
            if category.parent_id and category.parent_id not in by_id:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="missing_parent_category",
                        message=(
                            f"parent_id {category.parent_id!r} referenced by "
                            f"{category.category_id!r} does not exist"
                        ),
                        path=f"category_tree.{category.category_id}.parent_id",
                    )
                )

        for category in self.categories:
            current_id = category.category_id
            chain: set[str] = set()
            while current_id:
                if current_id in chain:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="circular_category_reference",
                            message=f"circular parent reference involving {category.category_id!r}",
                            path=f"category_tree.{category.category_id}.parent_id",
                        )
                    )
                    break
                chain.add(current_id)
                parent = by_id.get(current_id)
                if parent is None:
                    break
                current_id = parent.parent_id

        for category in self.categories:
            if self._depth(category.category_id) > 10:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="category_tree_too_deep",
                        message=(
                            f"category {category.category_id!r} exceeds "
                            f"maximum nesting depth of 10"
                        ),
                        path=f"category_tree.{category.category_id}",
                    )
                )

        return tuple(issues)


@dataclass(frozen=True, slots=True)
class UpgradeRule:
    rule_id: str
    target_product_code: str
    upgrade_action: UpgradeAction = "upgrade"
    ignore_language: bool = False

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate upgrade rule."""
        issues: list[ValidationIssue] = []
        base_path = f"upgrade_rule.{self.rule_id}"

        if not self.target_product_code:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_target_product_code",
                    message="target_product_code must not be empty",
                    path=f"{base_path}.target_product_code",
                )
            )
        elif not _is_valid_guid(self.target_product_code):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="invalid_target_product_code",
                    message="target_product_code must be a valid GUID",
                    path=f"{base_path}.target_product_code",
                )
            )

        return tuple(issues)


@dataclass(frozen=True, slots=True)
class UpgradePolicy:
    rules: tuple[UpgradeRule, ...] = field(default_factory=tuple)

    def rules_for_product(self, product_code: str) -> tuple[UpgradeRule, ...]:
        """Return all upgrade rules targeting *product_code*."""
        return tuple(rule for rule in self.rules if rule.target_product_code == product_code)

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate all upgrade rules."""
        issues: list[ValidationIssue] = []
        for rule in self.rules:
            issues.extend(rule.validate())
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class DeploymentStatus:
    package_id: str
    state: DeploymentState
    last_transition: str = ""
    error_detail: str = ""
    affected_computers: int = 0
    affected_users: int = 0

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate deployment status."""
        issues: list[ValidationIssue] = []
        if not self.package_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_package_id",
                    message="package_id must not be empty",
                    path="deployment_status.package_id",
                )
            )
        return tuple(issues)


def valid_transitions(state: DeploymentState) -> tuple[DeploymentState, ...]:
    """Return the valid next states from *state*."""
    match state:
        case "pending":
            return ("deployed", "removed", "failed")
        case "deployed":
            return ("upgrading", "removing", "orphaned", "failed")
        case "upgrading":
            return ("deployed", "failed")
        case "removing":
            return ("removed", "failed")
        case "removed":
            return ()
        case "failed":
            return ("pending", "removed")
        case "orphaned":
            return ("removing", "removed")
        case _:
            assert_never(state)


def transition_deployment(
    status: DeploymentStatus,
    new_state: DeploymentState,
    detail: str = "",
) -> DeploymentStatus:
    """Transition *status* to *new_state*.

    Raises ValidationError if the transition is invalid.
    """
    if new_state not in valid_transitions(status.state):
        raise ValidationError(
            [
                ValidationIssue(
                    severity="error",
                    code="invalid_state_transition",
                    message=(
                        f"cannot transition from {status.state!r} to {new_state!r}"
                    ),
                    path="deployment_status.state",
                )
            ]
        )
    return DeploymentStatus(
        package_id=status.package_id,
        state=new_state,
        last_transition="",
        error_detail=detail,
        affected_computers=status.affected_computers,
        affected_users=status.affected_users,
    )


@dataclass(frozen=True, slots=True)
class InstallationScript:
    script_id: str
    script_type: Literal["pre_install", "post_install", "pre_remove", "post_remove"]
    artifact_id: str
    parameters: str = ""
    run_as: Literal["system", "user"] = "system"
    ignore_exit_code: bool = False
    expected_exit_codes: tuple[int, ...] = (0,)

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate installation script."""
        issues: list[ValidationIssue] = []
        base_path = f"installation_script.{self.script_id}"

        if not self.artifact_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_artifact_id",
                    message="artifact_id must not be empty",
                    path=f"{base_path}.artifact_id",
                )
            )

        if not self.expected_exit_codes:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="no_success_exit_codes",
                    message="expected_exit_codes is empty; no success codes defined",
                    path=f"{base_path}.expected_exit_codes",
                )
            )
        elif self.ignore_exit_code:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="contradictory_exit_code_handling",
                    message=(
                        "ignore_exit_code=True with non-empty expected_exit_codes "
                        "is contradictory"
                    ),
                    path=f"{base_path}.ignore_exit_code",
                )
            )

        issues.extend(
            ValidationIssue(
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                path=f"{base_path}.parameters",
            )
            for issue in validate_parameters(self.parameters)
        )

        return tuple(issues)
