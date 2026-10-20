"""Plan 035 WP-2: GPME's view of a Studio-imported firewall GPO (WI-077).

Ground truth is ``fixtures/native-firewall-gpme-view/`` (provenance in that
directory). windows-console-driver estate window 19 imported the firewall
lane's candidate bundle, built from this repository at ``badcaf9`` and
registered ``[{35378EAC-...}{B05566AC-...}]``, into a disposable GPO. It
walked the Group Policy Management Editor to Inbound Rules and opened one
rule's Properties sheet. The transaction certified that the walk changed
nothing (post == pre). The rule list below is display evidence for a person,
not an oracle.

These tests check that the display agrees with what Studio authored. Every
inbound rule in the candidate, and no other, is listed, with the profile,
enabled state, action and group Studio wrote. A display that drifted from
the candidate would show up here, without another estate window.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path

from gpo_studio.firewall_policy import FirewallRule

ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = ROOT / "tests" / "fixtures" / "native-firewall-gpme-view"
_VIEW = json.loads((_FIXTURES / "inbound-rules.json").read_text(encoding="utf-8"))
_PROVENANCE = json.loads((_FIXTURES / "provenance.json").read_text(encoding="utf-8"))
_BUILDER = runpy.run_path(str(ROOT / "scripts/plan-033/build-firewall-candidate.py"))

_PROFILE_LABEL = {(): "All", ("domain",): "Domain"}
# GPME's Action column for the three measured wire actions: the
# authenticated-bypass rule (Action=ByPass; Security=Authenticate) displays
# as "Secure" with Override "Yes" -- the console's name for the same rule the
# readback calls action=Allow, override_block_rules=True.
_ACTION_LABEL = {"allow": "Allow", "block": "Block", "bypass": "Secure"}


def _inbound() -> dict[str, FirewallRule]:
    policy = _BUILDER["candidate_policy"]()
    return {rule.name: rule for rule in policy.rules if rule.direction == "inbound"}


def _rows() -> dict[str, dict[str, str]]:
    return {row["Name"]: row for row in _VIEW["rows"]}


def test_the_view_lists_exactly_the_candidates_inbound_rules() -> None:
    assert len(_VIEW["rows"]) == len(_rows()) == 10
    assert set(_rows()) == set(_inbound())


def test_each_row_shows_the_authored_profile_state_action_and_group() -> None:
    rows = _rows()
    for name, rule in _inbound().items():
        row = rows[name]
        assert row["Profile"] == _PROFILE_LABEL[rule.profiles], name
        assert row["Enabled"] == ("Yes" if rule.enabled else "No"), name
        assert row["Action"] == _ACTION_LABEL[rule.action], name
        assert row["Override"] == ("Yes" if rule.action == "bypass" else "No"), name
        assert row["Group"] == (rule.group or ""), name


def test_the_view_is_bound_to_the_imported_registration_and_bundle() -> None:
    imported = _PROVENANCE["what_was_imported"]
    assert "3f146885bb8e362e4d178582cc5710cd8067980cb9413a5411c75e2edf651e0f" in imported
    assert "{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}" in imported
    assert _VIEW["columns"][:6] == ["Name", "Group", "Profile", "Enabled", "Action", "Override"]
