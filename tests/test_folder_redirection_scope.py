"""Plan 034 WP-4: the code facts the Folder Redirection ruling rests on.

The ruling was taken on 2026-09-11 --
[`scope-decision-2026-09-11-folder-redirection.md`](../docs/scope-decision-2026-09-11-folder-redirection.md)
-- as **read target, write deferred**. These tests are what keep that decision
honest in both directions.

Two of them assert the measurements the ruling rested on, which are properties
of this repository rather than of Windows: a brief resting on a code fact that
has since changed is the same defect as a status line that has gone stale.
They still describe `folder_redirection.py`, which the ruling did not touch.

The last one changed shape when the ruling was acted on. It used to assert
that nothing in the product read or wrote fdeploy, and its own docstring said
that when that stopped being true, "the brief has to say which option was
taken". It has, and so the assertion is now the other half of the same
contract: the reader exists, and the **writer still does not**. A writer needs
the `Flags` encoding R12 owes (WI-066), so this fails if one appears before
that capture does.
"""

from __future__ import annotations

from pathlib import Path

from gpo_studio.folder_redirection import (
    FolderRedirection,
    FolderRedirectionPolicy,
    RedirectionRule,
)

_ROOT = Path(__file__).resolve().parents[1]
CAPTURE = _ROOT / "tests" / "fixtures" / "native-folder-redirection-gpmc"


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


def test_the_ruling_is_recorded_where_a_reader_would_look_for_it() -> None:
    """A decision taken in a commit message is a note, not a ruling.

    The same argument `docs/work-items.md` makes for WI numbers. The read half
    landed, so the document that says *which* option was taken has to exist and
    has to name the deferral, or the code is the only record of the decision.
    """
    decision = _ROOT / "docs" / "scope-decision-2026-09-11-folder-redirection.md"
    assert decision.exists(), (
        "the fdeploy reader is in the product with no recorded ruling; "
        "docs/scope-brief-2026-09-11-folder-redirection.md recommended one"
    )
    text = decision.read_text(encoding="utf-8")
    assert "read target" in text
    assert "WI-066" in text  # the deferral's gate, named


def test_the_writer_half_is_still_deferred_behind_r12() -> None:
    """The reader may exist; nothing may compose an fdeploy from our model.

    `encode_fdeploy` is the codec's other half and exists to prove the reader
    lossless against native bytes. The line this holds is that no *other*
    product module reaches for it, because emitting a document Windows has
    never written needs the `Flags` encoding WI-066 owes -- and a writer built
    on one observation is `object_security.py`'s propagation codes again.
    """
    source = _ROOT / "src" / "gpo_studio"
    callers = sorted(
        path.name
        for path in source.glob("*.py")
        if path.name != "fdeploy.py"
        and "encode_fdeploy" in path.read_text(encoding="utf-8")
    )
    assert callers == [], (
        f"{callers} now emit fdeploy bytes. Plan 034 WP-4 deferred the writer "
        "until R12 measures the Flags encoding (WI-066); if that capture "
        "landed, say so in the decision document and change this test."
    )

    # The other direction the writer could arrive from: a conversion hung off
    # the existing model, which is the shape `to_registry_settings` already is.
    assert not [
        name
        for name in dir(FolderRedirectionPolicy)
        if "fdeploy" in name.casefold()
    ]
