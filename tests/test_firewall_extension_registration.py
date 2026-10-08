"""The Registry CSE's tool half for firewall policy (batch 2).

Native firewall authoring registers the firewall snap-in's tool GUID with the
Registry CSE, not the Administrative Templates one. The expectation is read
from the banked native capture (tests/fixtures/native-firewall-gpmc, the
firewall lane's fixture), never restated from export.py.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from gpo_studio.export import extension_registration, gpmc_backup_bundle
from gpo_studio.import_export import extract_side_settings
from gpo_studio.model import GPO, RegistrySetting

CAPTURE = Path(__file__).parent / "fixtures" / "native-firewall-gpmc"
CENSUS = (
    Path(__file__).parent / "fixtures" / "live-domain-census" / "r06-cse-census"
    / "extension-lists.csv"
)
FIREWALL_KEY = r"SOFTWARE\Policies\Microsoft\WindowsFirewall"


def _capture() -> dict[str, object]:
    return json.loads((CAPTURE / "capture.json").read_text(encoding="utf-8-sig"))  # type: ignore[no-any-return]


def _backup_xml() -> str:
    return next(CAPTURE.glob("backup/*/Backup.xml")).read_text(encoding="utf-8-sig")


def _captured_settings(tmp_path: Path) -> list[RegistrySetting]:
    machine = tmp_path / "content" / "Machine"
    machine.mkdir(parents=True)
    (machine / "registry.pol").write_bytes((CAPTURE / "Registry.pol").read_bytes())
    return extract_side_settings(tmp_path / "content", "computer")


def _gpo(settings: list[RegistrySetting]) -> GPO:
    return GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000f3",
        name="Firewall registration",
        domain="synthetic.test",
        settings=tuple(settings),
    )


def _ordinary(side: str = "computer") -> RegistrySetting:
    return RegistrySetting(
        id=f"ordinary-{side}",
        side=side,  # type: ignore[arg-type]
        hive="HKLM" if side == "computer" else "HKCU",
        key=r"Software\Policies\GPOStudio\Ordinary",
        value_name="V",
        registry_type="REG_DWORD",
        value=1,
    )


def test_the_fixture_is_the_captured_registry_pol() -> None:
    provenance = json.loads((CAPTURE / "provenance.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((CAPTURE / "Registry.pol").read_bytes()).hexdigest()
    assert digest == provenance["registry_pol"]["sha256"]


def test_the_capture_holds_firewall_keys_only(tmp_path: Path) -> None:
    settings = _captured_settings(tmp_path)
    assert len(settings) == 25
    assert all(
        s.key.casefold() == FIREWALL_KEY.casefold()
        or s.key.casefold().startswith(FIREWALL_KEY.casefold() + "\\")
        for s in settings
    )


def test_firewall_only_policy_registers_what_windows_registered(tmp_path: Path) -> None:
    ad = _capture()["ad_attributes"]
    assert isinstance(ad, dict)
    gpo = _gpo(_captured_settings(tmp_path))

    registration = extension_registration(gpo)
    assert registration.machine == ad["gPCMachineExtensionNames"]
    assert registration.user == ad["gPCUserExtensionNames"] == ""

    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        backup_xml = next(
            archive.read(n) for n in archive.namelist() if n.endswith("/Backup.xml")
        ).decode()
    recorded = re.search(r"<(?:\w+:)?MachineExtensionGuids>(.*?)</", backup_xml)
    assert recorded is not None
    native = re.search(r"<MachineExtensionGuids><!\[CDATA\[(.*?)\]\]>", _backup_xml())
    assert native is not None
    assert recorded.group(1) == native.group(1)


def test_mixed_machine_content_is_left_unchanged_because_it_is_unmeasured(
    tmp_path: Path,
) -> None:
    """Firewall keys AND ordinary policy on the machine side: no capture.

    The live census shows a production group carrying BOTH tool halves,
    [{35378EAC}{B05566AC}{D02B1F72}], but holds no Registry.pol content, so
    nothing says that combination produced it. Until a capture does (WI-075),
    the pre-batch-2 Administrative Templates pair is kept rather than a guess.
    """
    with CENSUS.open(encoding="utf-8-sig", newline="") as stream:
        machine_lists = [row["machine"] for row in csv.DictReader(stream)]
    combined = (
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}"
        "{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
    )
    assert any(combined in value for value in machine_lists)

    gpo = _gpo([*_captured_settings(tmp_path), _ordinary()])
    assert extension_registration(gpo).machine == (
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
    )


def test_ordinary_policy_keeps_the_administrative_templates_pair() -> None:
    registration = extension_registration(_gpo([_ordinary(), _ordinary("user")]))
    assert registration.machine == (
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
    )
    assert registration.user == (
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F73-3407-48AE-BA88-E8213C6761F1}]"
    )


def test_a_look_alike_key_is_not_firewall_policy() -> None:
    lookalike = RegistrySetting(
        id="lookalike",
        side="computer",
        hive="HKLM",
        key=r"SOFTWARE\Policies\Microsoft\WindowsFirewallSomethingElse",
        value_name="V",
        registry_type="REG_DWORD",
        value=1,
    )
    assert "{D02B1F72-" in extension_registration(_gpo([lookalike])).machine
