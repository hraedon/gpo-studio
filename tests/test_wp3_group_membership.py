"""The WP-3 finalizer must compare `Group Membership` the way `secedit` writes it.

`secedit` rewrites every principal as a SID on export -- the group in the key
AND the members in the value -- so an authored `Power Users__Members =
Administrator` comes back as `*S-1-5-32-547__Members = *S-1-5-21-<machine>-500`.
Neither half survives a literal comparison, and one of those SIDs is
machine-specific, so the expected side cannot spell it out either.

These tests pin the reduction that makes the comparison possible, and pin that
it is still a comparison: the failure cases have to fail.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from gpo_studio.security_template import parse_security_template

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "windows-oracle" / "finalize_wp3_run.py"
)
_spec = importlib.util.spec_from_file_location("finalize_wp3_run", _MODULE_PATH)
assert _spec and _spec.loader
finalize_wp3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finalize_wp3)


def _exported(entries: str):
    return parse_security_template(
        "[Unicode]\nUnicode=yes\n"
        "[Group Membership]\n"
        f"{entries}\n"
        '[Version]\nsignature="$CHICAGO$"\n'
    )


class TestGroupMembershipComparison:
    def test_a_name_matches_the_sid_secedit_exports(self) -> None:
        """The case the section exists to test."""
        template = _exported(
            "*S-1-5-32-547__Members = *S-1-5-21-1-2-3-500"
        )
        assert finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator",
        )

    def test_the_sid_only_control_row_matches(self) -> None:
        """The control: if this fails, the comparison is broken and nothing else counts."""
        template = _exported("*S-1-5-32-551__Members = *S-1-5-32-544")
        assert finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="*S-1-5-32-551__Members",
            expected="*S-1-5-32-544",
        )

    def test_a_different_member_does_not_match(self) -> None:
        """Still a comparison: Guest is not Administrator."""
        template = _exported("*S-1-5-32-547__Members = *S-1-5-21-1-2-3-501")
        assert not finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator",
        )

    def test_a_different_group_does_not_match(self) -> None:
        """The key is a principal too, and it has to be the right one."""
        template = _exported("*S-1-5-32-544__Members = *S-1-5-21-1-2-3-500")
        assert not finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator",
        )

    def test_members_and_memberof_are_not_interchangeable(self) -> None:
        """`__Members` and `__Memberof` mean opposite things."""
        template = _exported("*S-1-5-32-547__Memberof = *S-1-5-21-1-2-3-500")
        assert not finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator",
        )

    def test_member_order_does_not_matter(self) -> None:
        """A set, like the principal-rights comparison it is modelled on."""
        template = _exported(
            "*S-1-5-32-547__Members = *S-1-5-32-545,*S-1-5-21-1-2-3-500"
        )
        assert finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator,Users",
        )

    def test_an_extra_member_does_not_match(self) -> None:
        """A set comparison must still notice a member nobody authored."""
        template = _exported(
            "*S-1-5-32-547__Members = *S-1-5-21-1-2-3-500,*S-1-5-32-545"
        )
        assert not finalize_wp3._setting_matches(
            template,
            section="Group Membership",
            key="Power Users__Members",
            expected="Administrator",
        )


class TestGroupMembershipKeySet:
    def _settings(self) -> list[dict[str, str]]:
        return [
            {
                "section": "Group Membership",
                "key": "Power Users__Members",
                "value": "Administrator",
            }
        ]

    def test_the_rewritten_key_still_counts_as_authored(self) -> None:
        template = _exported("*S-1-5-32-547__Members = *S-1-5-21-1-2-3-500")
        assert finalize_wp3._candidate_key_set_matches(template, self._settings())

    def test_an_unauthored_entry_is_rejected(self) -> None:
        """The bijection matters: an extra entry is a difference, not a bonus."""
        template = _exported(
            "*S-1-5-32-547__Members = *S-1-5-21-1-2-3-500\n"
            "*S-1-5-32-551__Members = *S-1-5-32-544"
        )
        assert not finalize_wp3._candidate_key_set_matches(template, self._settings())
