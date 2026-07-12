from __future__ import annotations

import pytest

from gpo_studio.sddl import (
    Ace,
    Acl,
    SddlError,
    SecurityDescriptor,
    format_ace,
    format_sddl,
    parse_ace,
    parse_sddl,
)

_ADMIN = "S-1-5-32-544"
_USERS = "S-1-5-32-545"
_SYSTEM = "S-1-5-18"


def test_parse_simple_sddl() -> None:
    sd = parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN})")
    assert sd.owner_sid == _ADMIN
    assert sd.group_sid == _ADMIN
    assert sd.sacl is None
    assert sd.dacl is not None
    assert len(sd.dacl.aces) == 1
    ace = sd.dacl.aces[0]
    assert ace.type == "ALLOWED"
    assert ace.flags == ()
    assert ace.rights == ("CC",)
    assert ace.object_guid == ""
    assert ace.inherit_object_guid == ""
    assert ace.trustee_sid == _ADMIN


def test_parse_sddl_with_flags() -> None:
    sd = parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(A;CI;CC;;;{_ADMIN})")
    assert sd.dacl is not None
    ace = sd.dacl.aces[0]
    assert ace.flags == ("CI",)
    assert ace.rights == ("CC",)


def test_parse_sddl_with_multiple_aces() -> None:
    sd = parse_sddl(
        f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})(A;;GA;;;{_USERS})"
    )
    assert sd.dacl is not None
    assert len(sd.dacl.aces) == 2
    assert sd.dacl.aces[0].trustee_sid == _ADMIN
    assert sd.dacl.aces[1].trustee_sid == _USERS


def test_parse_sddl_with_sacl() -> None:
    sd = parse_sddl(
        f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})S:(A;;GA;;;{_USERS})"
    )
    assert sd.sacl is not None
    assert len(sd.sacl.aces) == 1
    assert sd.sacl.aces[0].trustee_sid == _USERS


def test_parse_denied_ace() -> None:
    sd = parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(D;;CC;;;{_ADMIN})")
    assert sd.dacl is not None
    ace = sd.dacl.aces[0]
    assert ace.type == "DENIED"


def test_format_simple_sddl() -> None:
    sd = SecurityDescriptor(
        owner_sid=_ADMIN,
        group_sid=_ADMIN,
        dacl=Acl(
            aces=(
                Ace(
                    type="ALLOWED",
                    flags=(),
                    rights=("CC",),
                    object_guid="",
                    inherit_object_guid="",
                    trustee_sid=_ADMIN,
                ),
            )
        ),
        sacl=None,
    )
    assert format_sddl(sd) == f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN})"


@pytest.mark.parametrize(
    "sddl",
    [
        f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN})",
        f"O:{_ADMIN}G:{_ADMIN}D:(A;CI;CC;;;{_ADMIN})",
        f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})(A;;GA;;;{_USERS})",
        f"O:{_ADMIN}G:{_ADMIN}D:(A;CIOI;CCDC;;;{_ADMIN})",
        f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})S:(A;;GA;;;{_USERS})",
        f"O:{_SYSTEM}G:{_SYSTEM}D:(D;;CC;;;{_ADMIN})",
        f"O:{_ADMIN}G:{_ADMIN}",
    ],
)
def test_round_trip(sddl: str) -> None:
    assert format_sddl(parse_sddl(sddl)) == sddl


def test_parse_multiple_rights() -> None:
    sd = parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(A;;CCDC;;;{_ADMIN})")
    assert sd.dacl is not None
    ace = sd.dacl.aces[0]
    assert ace.rights == ("CC", "DC")


def test_parse_empty_dacl() -> None:
    sd = parse_sddl(f"O:{_ADMIN}G:{_ADMIN}")
    assert sd.dacl is None
    assert sd.sacl is None


def test_format_with_no_dacl() -> None:
    sd = SecurityDescriptor(
        owner_sid=_ADMIN,
        group_sid=_ADMIN,
        dacl=None,
        sacl=None,
    )
    assert format_sddl(sd) == f"O:{_ADMIN}G:{_ADMIN}"


def test_parse_ace_direct() -> None:
    ace = parse_ace(f"A;CIOI;CCDC;;;{_ADMIN}")
    assert ace.type == "ALLOWED"
    assert ace.flags == ("CI", "OI")
    assert ace.rights == ("CC", "DC")
    assert ace.trustee_sid == _ADMIN


def test_format_ace_direct() -> None:
    ace = Ace(
        type="DENIED",
        flags=("CI", "OI"),
        rights=("CC", "DC"),
        object_guid="",
        inherit_object_guid="",
        trustee_sid=_ADMIN,
    )
    assert format_ace(ace) == f"D;CIOI;CCDC;;;{_ADMIN}"


def test_parse_ace_invalid_type() -> None:
    with pytest.raises(SddlError):
        parse_ace(f"X;;CC;;;{_ADMIN}")


def test_parse_ace_wrong_field_count() -> None:
    with pytest.raises(SddlError):
        parse_ace(f"A;CC;{_ADMIN}")


def test_parse_empty_string_raises() -> None:
    with pytest.raises(SddlError):
        parse_sddl("")


def test_parse_sddl_with_section_marker_in_ace() -> None:
    sd = parse_sddl("D:(A;;CC;;;S:1)")
    assert sd.owner_sid == ""
    assert sd.group_sid == ""
    assert sd.sacl is None
    assert sd.dacl is not None
    assert len(sd.dacl.aces) == 1
    assert sd.dacl.aces[0].trustee_sid == "S:1"


def test_round_trip_without_owner_group() -> None:
    sddl = f"D:(A;;CC;;;{_ADMIN})"
    assert format_sddl(parse_sddl(sddl)) == sddl


def test_round_trip_with_section_marker_in_ace() -> None:
    sddl = "D:(A;;CC;;;S:1)"
    assert format_sddl(parse_sddl(sddl)) == sddl


def test_parse_ace_odd_length_rights() -> None:
    with pytest.raises(SddlError):
        parse_ace(f"A;;CCD;;;{_ADMIN}")


def test_parse_ace_odd_length_flags() -> None:
    with pytest.raises(SddlError):
        parse_ace(f"A;CIO;;;{_ADMIN}")


def test_parse_sddl_duplicate_owner_section() -> None:
    with pytest.raises(SddlError):
        parse_sddl(f"O:{_ADMIN}O:{_USERS}D:(A;;CC;;;{_ADMIN})")


def test_parse_sddl_duplicate_dacl_section() -> None:
    with pytest.raises(SddlError):
        parse_sddl(
            f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN})D:(A;;CC;;;{_USERS})"
        )


def test_parse_sddl_unclosed_parenthesis() -> None:
    with pytest.raises(SddlError):
        parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN}")


def test_parse_sddl_extra_close_parenthesis() -> None:
    with pytest.raises(SddlError):
        parse_sddl(f"O:{_ADMIN}G:{_ADMIN}D:(A;;CC;;;{_ADMIN}))")
