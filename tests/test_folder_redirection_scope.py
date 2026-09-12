"""Plan 034 WP-4: the code facts the Folder Redirection ruling rests on.

[`scope-brief-2026-09-11-folder-redirection.md`](../docs/scope-brief-2026-09-11-folder-redirection.md)
assembles what a ruling needs. Two of its inputs are properties of this
repository rather than of Windows, and a brief resting on a code fact that has
since changed is the same defect as a status line that has gone stale -- the
thing this project keeps building guards against.

So: the survey's zero-cost discriminator, run rather than quoted, and the
banked capture it is set against. Both assert the **present** behaviour. If the
ruling is taken as (b) or (c) and a writer is built, these fail, and that
failure is the prompt to retire the brief rather than to leave it standing over
code it no longer describes.
"""

from __future__ import annotations

from pathlib import Path

from gpo_studio.folder_redirection import (
    FolderRedirection,
    FolderRedirectionPolicy,
    RedirectionRule,
)

CAPTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "native-folder-redirection-gpmc"
)


def _advanced_policy_with_three_groups() -> FolderRedirectionPolicy:
    """The survey's own example: advanced mode, three groups, options off."""
    return FolderRedirectionPolicy(
        folders=(
            FolderRedirection(
                folder="documents",
                target="advanced",
                rules=(
                    RedirectionRule(
                        group_sid="S-1-5-21-1-1-1-1001",
                        group_name="Sales",
                        target_path=r"\\fs01\sales\%USERNAME%\Documents",
                    ),
                    RedirectionRule(
                        group_sid="S-1-5-21-1-1-1-1002",
                        group_name="Eng",
                        target_path=r"\\fs02\eng\%USERNAME%\Documents",
                    ),
                    RedirectionRule(
                        group_sid="S-1-5-21-1-1-1-1003",
                        group_name="Ops",
                        target_path=r"\\fs03\ops\%USERNAME%\Documents",
                    ),
                ),
                grant_exclusive_rights=False,
                move_contents=False,
                remove_redirect_on_policy_removal=True,
                also_redirect_subfolders=False,
            ),
        )
    )


def test_three_group_rules_collapse_to_one_registry_tuple() -> None:
    """The survey asked for this and it had never been run.

    "A code fact, not an oracle result, but it bounds what any lane could
    certify" -- and what it bounds it to is nothing: two of the three rules are
    dropped without a word.
    """
    settings = _advanced_policy_with_three_groups().to_registry_settings()
    assert len(settings) == 1
    assert settings[0][2] == r"\\fs01\sales\%USERNAME%\Documents"


def test_neither_the_group_sids_nor_the_option_flags_survive() -> None:
    """R3 shows Windows encoding both; the module's output carries neither.

    `fdeploy1.ini` keys its per-folder section by SID and encodes the four
    options as `Flags=1021`. A serializer that emits a single path is not a
    lossy Folder Redirection writer -- it is not one at all, which is what makes
    this a scope question rather than a bug.
    """
    rendered = str(_advanced_policy_with_three_groups().to_registry_settings())
    assert "S-1-5-21" not in rendered
    assert "1021" not in rendered
    assert "Flags" not in rendered


def test_the_capture_the_brief_quotes_is_still_what_is_banked() -> None:
    """The other half: a brief quoting bytes that have moved is fiction.

    Read from the committed R3 fixture rather than restated, so the two cannot
    drift apart. The transcripts are prose descriptions of the native files,
    not the files themselves, which is why this matches on content rather than
    hashing.
    """
    policy = (CAPTURE / "fdeploy1.ini.txt").read_text(encoding="utf-8")
    assert "version=100" in policy
    assert "[Folder_Redirection]" in policy
    assert "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}" in policy
    assert "Flags=1021" in policy
    # The marker is empty; that it carries no policy is the reason the module
    # addressing "fdeploy.ini" would still have addressed the wrong file.
    marker = (CAPTURE / "fdeploy.ini.txt").read_text(encoding="utf-8")
    assert "[Folder_Redirection]" not in marker


def test_nothing_in_the_product_yet_reads_or_writes_fdeploy() -> None:
    """The premise of every option in the brief, asserted rather than assumed.

    Option (a) preserves, (b) parses, (c) also emits -- all three start from
    "there is no handling today". When that stops being true this fails, and
    the brief has to say which option was taken.
    """
    source = Path(__file__).resolve().parents[1] / "src" / "gpo_studio"
    mentions = sorted(
        path.name
        for path in source.glob("*.py")
        if "fdeploy" in path.read_text(encoding="utf-8").lower()
    )
    assert mentions == [], (
        f"{mentions} now mention fdeploy. A ruling was taken, or a writer was "
        "started without one; either way "
        "docs/scope-brief-2026-09-11-folder-redirection.md is no longer a "
        "description of the code and needs to record the decision."
    )
