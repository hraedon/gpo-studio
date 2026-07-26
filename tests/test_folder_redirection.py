from __future__ import annotations

import pytest

from gpo_studio.folder_redirection import (
    FolderRedirection,
    FolderRedirectionPolicy,
    MigrationAssessment,
    RedirectionRule,
    assess_redirection_migration,
    normalize_redirection_path,
    validate_redirection_path,
)

UNC = "\\\\server\\share\\users\\%USERNAME%\\Documents"
UNC_RAW = "\\\\server\\share\\users\\%USERNAME%\\Documents"
LOCAL = "C:\\Users\\%USERNAME%\\Documents"


# ---------------------------------------------------------------------------
# RedirectionRule
# ---------------------------------------------------------------------------


def test_redirection_rule_valid() -> None:
    rule = RedirectionRule(
        group_sid="S-1-5-21-1-2-3-1001",
        group_name="Finance",
        target_path=UNC,
    )
    assert rule.validate() == ()


def test_redirection_rule_empty_path_error() -> None:
    rule = RedirectionRule(group_name="Finance", target_path="")
    issues = rule.validate()
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_target_path"


def test_redirection_rule_path_traversal_error() -> None:
    rule = RedirectionRule(target_path="\\\\server\\share\\..\\..\\evil")
    issues = rule.validate()
    assert any(i.code == "target_path_traversal" and i.severity == "error" for i in issues)


def test_redirection_rule_local_path_warning() -> None:
    rule = RedirectionRule(target_path="C:\\Redirection\\Finance")
    issues = rule.validate()
    assert any(i.code == "target_path_not_unc" and i.severity == "warning" for i in issues)


def test_redirection_rule_local_with_env_var_no_warning() -> None:
    rule = RedirectionRule(target_path="C:\\Users\\%USERNAME%\\Docs")
    issues = rule.validate()
    assert not any(i.code == "target_path_not_unc" for i in issues)


# ---------------------------------------------------------------------------
# FolderRedirection
# ---------------------------------------------------------------------------


def test_folder_redirection_valid_basic() -> None:
    fr = FolderRedirection(folder="documents", target="basic", basic_path=UNC)
    assert fr.validate() == ()


def test_folder_redirection_valid_advanced() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="advanced",
        rules=(RedirectionRule(group_name="Finance", target_path=UNC),),
    )
    assert fr.validate() == ()


def test_folder_redirection_empty_basic_path_error() -> None:
    fr = FolderRedirection(folder="documents", target="basic", basic_path="")
    issues = fr.validate()
    assert any(i.code == "empty_basic_path" and i.severity == "error" for i in issues)


def test_folder_redirection_advanced_no_rules_error() -> None:
    fr = FolderRedirection(folder="documents", target="advanced", rules=())
    issues = fr.validate()
    assert any(i.code == "no_advanced_rules" and i.severity == "error" for i in issues)


def test_folder_redirection_path_traversal_error() -> None:
    fr = FolderRedirection(
        folder="documents", target="basic", basic_path="\\\\server\\share\\..\\evil"
    )
    issues = fr.validate()
    assert any(i.code == "basic_path_traversal" and i.severity == "error" for i in issues)


def test_folder_redirection_basic_with_rules_warning() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="basic",
        basic_path=UNC,
        rules=(RedirectionRule(target_path=UNC),),
    )
    issues = fr.validate()
    assert any(
        i.code == "rules_ignored_in_basic_mode" and i.severity == "warning" for i in issues
    )


def test_folder_redirection_advanced_with_basic_path_warning() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="advanced",
        basic_path=UNC,
        rules=(RedirectionRule(target_path=UNC),),
    )
    issues = fr.validate()
    assert any(
        i.code == "basic_path_ignored" and i.severity == "warning" for i in issues
    )


def test_folder_redirection_no_exclusive_rights_warning() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="basic",
        basic_path=UNC,
        grant_exclusive_rights=False,
    )
    issues = fr.validate()
    assert any(
        i.code == "no_exclusive_rights" and i.severity == "warning" for i in issues
    )


def test_folder_redirection_orphaned_data_warning() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="basic",
        basic_path=UNC,
        move_contents=False,
        remove_redirect_on_policy_removal=True,
    )
    issues = fr.validate()
    assert any(i.code == "orphaned_data_risk" and i.severity == "warning" for i in issues)


def test_folder_redirection_not_configured_is_clean() -> None:
    fr = FolderRedirection(folder="documents")
    assert fr.target == "not_configured"
    assert fr.validate() == ()


# ---------------------------------------------------------------------------
# effective_path
# ---------------------------------------------------------------------------


def test_effective_path_basic_username_substitution() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="basic",
        basic_path="\\\\server\\share\\%USERNAME%\\Documents",
    )
    assert fr.effective_path("alice") == "\\\\server\\share\\alice\\Documents"


def test_effective_path_basic_keeps_var_by_default() -> None:
    fr = FolderRedirection(
        folder="documents",
        target="basic",
        basic_path="\\\\server\\share\\%USERNAME%\\Documents",
    )
    assert fr.effective_path() == "\\\\server\\share\\%USERNAME%\\Documents"


def test_effective_path_advanced_uses_first_rule() -> None:
    fr = FolderRedirection(
        folder="desktop",
        target="advanced",
        rules=(
            RedirectionRule(target_path="\\\\srv\\d\\%USERNAME%\\Desktop"),
            RedirectionRule(target_path="\\\\srv\\d\\other"),
        ),
    )
    assert fr.effective_path("bob") == "\\\\srv\\d\\bob\\Desktop"


def test_effective_path_not_configured_returns_empty() -> None:
    fr = FolderRedirection(folder="documents")
    assert fr.effective_path() == ""
    assert fr.effective_path("alice") == ""


# ---------------------------------------------------------------------------
# FolderRedirectionPolicy
# ---------------------------------------------------------------------------


def test_policy_get_folder() -> None:
    docs = FolderRedirection(folder="documents", target="basic", basic_path=UNC)
    desktop = FolderRedirection(folder="desktop", target="basic", basic_path=UNC)
    policy = FolderRedirectionPolicy(folders=(docs, desktop))
    assert policy.get_folder("desktop") is desktop
    assert policy.get_folder("documents") is docs
    assert policy.get_folder("music") is None


def test_policy_configured_folders() -> None:
    docs = FolderRedirection(folder="documents", target="basic", basic_path=UNC)
    unconf = FolderRedirection(folder="desktop")
    policy = FolderRedirectionPolicy(folders=(docs, unconf))
    configured = policy.configured_folders()
    assert configured == (docs,)


def test_policy_duplicate_folder_error() -> None:
    a = FolderRedirection(folder="documents", target="basic", basic_path=UNC)
    b = FolderRedirection(folder="documents", target="basic", basic_path=LOCAL)
    policy = FolderRedirectionPolicy(folders=(a, b))
    issues = policy.validate()
    assert any(i.code == "duplicate_folder" and i.severity == "error" for i in issues)


def test_policy_all_not_configured_warning() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(folder="documents"),
            FolderRedirection(folder="desktop"),
        )
    )
    issues = policy.validate()
    assert any(i.code == "empty_policy" and i.severity == "warning" for i in issues)


def test_policy_validates_each_folder() -> None:
    bad = FolderRedirection(folder="documents", target="basic", basic_path="")
    policy = FolderRedirectionPolicy(folders=(bad,))
    issues = policy.validate()
    assert any(i.code == "empty_basic_path" for i in issues)


def test_policy_valid_no_issues() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(folder="documents", target="basic", basic_path=UNC),
            FolderRedirection(folder="desktop", target="basic", basic_path=UNC),
        )
    )
    assert policy.validate() == ()


# ---------------------------------------------------------------------------
# to_registry_settings
# ---------------------------------------------------------------------------


def test_to_registry_settings_basic_mapping() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Documents",
            ),
        )
    )
    settings = policy.to_registry_settings()
    assert len(settings) == 1
    reg_path, value_name, value_data = settings[0]
    assert value_name == "Personal"
    assert value_data == "\\\\srv\\d\\%USERNAME%\\Documents"
    assert "User Shell Folders" in reg_path


def test_to_registry_settings_all_known_folders() -> None:
    expected = {
        "documents": "Personal",
        "desktop": "Desktop",
        "appdata_roaming": "AppData",
        "appdata_local": "Local AppData",
        "start_menu": "Start Menu",
        "pictures": "My Pictures",
        "music": "My Music",
        "videos": "My Video",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    }
    folders = tuple(
        FolderRedirection(folder=f, target="basic", basic_path="\\\\srv\\d\\%USERNAME%")
        for f in expected
    )
    policy = FolderRedirectionPolicy(folders=folders)
    settings = policy.to_registry_settings()
    value_names = {v_name for _, v_name, _ in settings}
    assert value_names == set(expected.values())
    assert len(settings) == len(expected)


def test_to_registry_settings_skips_not_configured() -> None:
    policy = FolderRedirectionPolicy(
        folders=(FolderRedirection(folder="documents"),)
    )
    assert policy.to_registry_settings() == ()


def test_to_registry_settings_skips_unmapped_folders() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="contacts",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Contacts",
            ),
            FolderRedirection(
                folder="saved_games",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Saved",
            ),
        )
    )
    assert policy.to_registry_settings() == ()


def test_to_registry_settings_advanced_uses_first_rule() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="advanced",
                rules=(RedirectionRule(target_path="\\\\srv\\d\\%USERNAME%\\Docs"),),
            ),
        )
    )
    settings = policy.to_registry_settings()
    assert len(settings) == 1
    _, _, value_data = settings[0]
    assert value_data == "\\\\srv\\d\\%USERNAME%\\Docs"


# ---------------------------------------------------------------------------
# validate_redirection_path
# ---------------------------------------------------------------------------


def test_validate_path_unc_ok() -> None:
    issues = validate_redirection_path(UNC_RAW)
    assert issues == ()


def test_validate_path_local_warning() -> None:
    issues = validate_redirection_path("C:\\Users\\Public\\Docs")
    assert any(i.code == "local_path" and i.severity == "warning" for i in issues)


def test_validate_path_system_folder_error() -> None:
    issues = validate_redirection_path("%SYSTEMROOT%\\Users\\X")
    assert any(
        i.code == "system_folder_redirect" and i.severity == "error" for i in issues
    )


def test_validate_path_windir_error() -> None:
    issues = validate_redirection_path("%WINDIR%\\Temp\\X")
    assert any(i.code == "system_folder_redirect" and i.severity == "error" for i in issues)


def test_validate_path_temp_warning() -> None:
    issues = validate_redirection_path("\\\\srv\\share\\%TEMP%\\data")
    assert any(i.code == "temp_location_redirect" and i.severity == "warning" for i in issues)


def test_validate_path_traversal_error() -> None:
    issues = validate_redirection_path("\\\\server\\share\\..\\evil")
    assert any(i.code == "path_traversal" and i.severity == "error" for i in issues)


def test_validate_path_empty_error() -> None:
    issues = validate_redirection_path("")
    assert len(issues) == 1
    assert issues[0].code == "empty_path"
    assert issues[0].severity == "error"


def test_validate_path_invalid_characters_error() -> None:
    issues = validate_redirection_path("\\\\server\\sha<re\\x")
    assert any(i.code == "invalid_path_characters" and i.severity == "error" for i in issues)


def test_validate_path_too_long_error() -> None:
    long_path = "\\\\server\\share\\" + ("a" * 300)
    issues = validate_redirection_path(long_path)
    assert any(i.code == "path_too_long" and i.severity == "error" for i in issues)


def test_validate_path_extended_prefix_allows_long() -> None:
    long_path = "\\\\?\\" + ("C:\\folder\\" * 30) + "x"
    issues = validate_redirection_path(long_path)
    assert not any(i.code == "path_too_long" for i in issues)


# ---------------------------------------------------------------------------
# normalize_redirection_path
# ---------------------------------------------------------------------------


def test_normalize_forward_slash_conversion() -> None:
    assert normalize_redirection_path("//server/share/users/x") == "\\\\server\\share\\users\\x"


def test_normalize_trailing_backslash_stripped() -> None:
    assert normalize_redirection_path("\\\\server\\share\\x\\") == "\\\\server\\share\\x"


def test_normalize_collapse_multiple_backslashes() -> None:
    assert normalize_redirection_path("\\\\server\\\\share\\\\x") == "\\\\server\\share\\x"


def test_normalize_drive_root_kept() -> None:
    assert normalize_redirection_path("C:\\") == "C:\\"


def test_normalize_local_trailing_stripped() -> None:
    assert normalize_redirection_path("C:/Users/foo/") == "C:\\Users\\foo"


def test_normalize_unc_root_kept() -> None:
    assert normalize_redirection_path("\\\\server\\share") == "\\\\server\\share"


def test_normalize_empty_returns_empty() -> None:
    assert normalize_redirection_path("") == ""


def test_normalize_extended_prefix_preserved() -> None:
    assert normalize_redirection_path("\\\\?\\C:\\folder\\\\x") == "\\\\?\\C:\\folder\\x"


# ---------------------------------------------------------------------------
# Migration assessment
# ---------------------------------------------------------------------------


def test_migration_appdata_high_risk() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="appdata_roaming",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\AppData",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert len(assessments) == 1
    assert assessments[0].folder == "appdata_roaming"
    assert assessments[0].estimated_risk == "high"


def test_migration_network_target_medium_risk() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="pictures",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Pics",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert assessments[0].estimated_risk == "medium"
    assert any("network" in w for w in assessments[0].warnings)


def test_migration_desktop_medium_risk() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="desktop",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Desktop",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert assessments[0].estimated_risk == "medium"


def test_migration_local_low_risk() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="saved_games",
                target="basic",
                basic_path="D:\\Redirect\\%USERNAME%\\Saved",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert assessments[0].estimated_risk == "low"


def test_migration_move_contents_false_warning() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="music",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Music",
                move_contents=False,
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert any("move_contents" in w for w in assessments[0].warnings)


def test_migration_uses_current_paths() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Documents",
            ),
        )
    )
    assessments = assess_redirection_migration(
        policy, current_paths={"documents": "C:\\Users\\alice\\Documents"}
    )
    assert assessments[0].source_path == "C:\\Users\\alice\\Documents"
    assert assessments[0].target_path == "\\\\srv\\d\\%USERNAME%\\Documents"


def test_migration_skips_not_configured() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(folder="documents"),
            FolderRedirection(
                folder="desktop",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\Desktop",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert len(assessments) == 1
    assert assessments[0].folder == "desktop"


def test_migration_appdata_to_network_stays_high() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="appdata_local",
                target="basic",
                basic_path="\\\\srv\\d\\%USERNAME%\\LocalAppData",
            ),
        )
    )
    assessments = assess_redirection_migration(policy)
    assert assessments[0].estimated_risk == "high"


def test_migration_assessment_is_frozen_dataclass() -> None:
    assessment = MigrationAssessment(
        folder="documents", source_path="", target_path="", estimated_risk="low"
    )
    with pytest.raises(AttributeError):
        assessment.estimated_risk = "high"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_validate_then_registry_settings() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="basic",
                basic_path="\\\\srv\\users\\%USERNAME%\\Documents",
            ),
            FolderRedirection(
                folder="desktop",
                target="basic",
                basic_path="\\\\srv\\users\\%USERNAME%\\Desktop",
            ),
        )
    )
    # Valid policy produces no issues.
    assert policy.validate() == ()
    # Registry settings are emitted for both configured folders.
    settings = policy.to_registry_settings()
    value_names = {v_name for _, v_name, _ in settings}
    assert value_names == {"Personal", "Desktop"}
    for _, _, value_data in settings:
        assert "%USERNAME%" in value_data


def test_round_trip_advanced_policy() -> None:
    policy = FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="advanced",
                rules=(
                    RedirectionRule(
                        group_name="Finance",
                        target_path="\\\\srv\\fin\\%USERNAME%\\Documents",
                    ),
                ),
            ),
        )
    )
    assert policy.validate() == ()
    settings = policy.to_registry_settings()
    assert len(settings) == 1
    _, value_name, value_data = settings[0]
    assert value_name == "Personal"
    assert value_data == "\\\\srv\\fin\\%USERNAME%\\Documents"
