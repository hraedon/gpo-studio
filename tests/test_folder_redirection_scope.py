"""Plan 034 WP-4: the code facts the Folder Redirection ruling rests on.

The ruling was taken on 2026-09-11 --
[`scope-decision-2026-09-11-folder-redirection.md`](../docs/scope-decision-2026-09-11-folder-redirection.md)
-- as **read target, write deferred**. These tests are what keep that decision
honest in both directions.

Two tests that used to live here measured `folder_redirection.py`: an advanced
policy with three group rules collapsed to one registry tuple carrying neither
the group SIDs nor the option flags. They were deleted with the module on
2026-10-07 (operator ruling, recorded as an addendum to the decision document).
The measurement is kept in the scope brief; the code it measured is in git.

The writer guard changed shape twice. It first asserted that nothing in the
product read or wrote fdeploy. When the reader landed it became the other half
of the same contract: the reader exists, and the **writer still does not**. It
also used to check that no conversion hung off `FolderRedirectionPolicy`,
which was the module's own route to a writer. That route went with the module,
so what remains is the guard over every product module: none but `fdeploy.py`
may emit fdeploy bytes. A writer needs the `Flags` encoding R12 owes (WI-066),
so this fails if one appears before that capture does.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
CAPTURE = _ROOT / "tests" / "fixtures" / "native-folder-redirection-gpmc"


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
    # The old module's deletion is recorded where the ruling about it lives.
    assert "2026-10-07" in text and "folder_redirection.py" in text


def test_the_writer_half_is_still_deferred_behind_r12() -> None:
    """The reader may exist; nothing may compose an fdeploy from our model.

    `encode_fdeploy` and `format_fdeploy` are the codec's other half and exist
    to prove the reader lossless against native bytes. The line this holds is
    that no *other* product module reaches for either, because emitting a
    document Windows has never written needs the `Flags` encoding WI-066 owes
    -- and a writer built on one observation is `object_security.py`'s
    propagation codes again.

    This is the half of the guard that outlived `folder_redirection.py`: it
    names no module, so it covers whichever one a writer would arrive in.
    """
    source = _ROOT / "src" / "gpo_studio"
    writer_halves = ("encode_fdeploy", "format_fdeploy")
    callers = sorted(
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if path.name != "fdeploy.py"
        and any(
            name in path.read_text(encoding="utf-8") for name in writer_halves
        )
    )
    assert callers == [], (
        f"{callers} now emit fdeploy bytes. Plan 034 WP-4 deferred the writer "
        "until R12 measures the Flags encoding (WI-066); if that capture "
        "landed, say so in the decision document and change this test."
    )

