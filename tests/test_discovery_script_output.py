"""Each generated discovery script's output is accepted by its importer (#148 review).

The scripts run on a domain-joined Windows machine and the importers validate
what they print, but nothing ran one against the other. Round 2 of #148 made
the forest importer require a real boolean ``transitive``, while the script
emitted ``$_.Transitive``, a property neither .NET trust type has, so every
generated forest document was refused.

Here each script runs under ``pwsh`` with its directory calls replaced by
synthetic stand-in objects, and its JSON is posted to the import route. The
replacements are checked: a directory call this file does not know fails the
test instead of reaching a real directory. Skipped where ``pwsh`` is absent.
The projection is exercised, not Windows: real .NET objects (enums, property
collections) and Windows PowerShell 5.1's ConvertTo-Json can still differ.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import ad_discovery
from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore

PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this host")

# Every directory entry point the scripts use, and its stand-in.
_REPLACEMENTS = [
    (r"\[System\.DirectoryServices\.ActiveDirectory\.Forest\]::GetCurrentForest\(\)",
     "$StandIn.Forest"),
    (r"\[System\.DirectoryServices\.ActiveDirectory\.Domain\]::GetCurrentDomain\(\)",
     "$StandIn.Domain"),
    (r"\[System\.DirectoryServices\.ActiveDirectory\.Domain\]::GetDomain\(\$ctx\)",
     "$StandIn.Domain"),
    (r"New-Object System\.DirectoryServices\.ActiveDirectory\.DirectoryContext\([^)]*\)",
     "$null"),
    (r"New-Object System\.DirectoryServices\.DirectorySearcher\(.*\)$", "$StandIn.Searcher"),
    (r"\[ADSI\]", "[string]"),
    (r"New-Object System\.Security\.Principal\.SecurityIdentifier\(", "New-StandInSid("),
]
_DIRECTORY_TOKENS = ("System.DirectoryServices", "[ADSI]", "SecurityIdentifier")

_PRELUDE = r"""
function New-StandInSid { param($a)
    $v = if ($a -is [array]) { $a[0] } else { $a }
    if ($v -isnot [string] -or -not $v.StartsWith('S-1-')) { throw 'not a SID' }
    [pscustomobject]@{ Value = $v }
}
function New-Named($name) { [pscustomobject]@{ Name = $name } }
function New-Result($properties) { [pscustomobject]@{ Properties = $properties } }
$standInSiteA = [pscustomobject]@{ Name = 'Site-A'; Subnets = @() }
$standInSiteA.Subnets = @([pscustomobject]@{ Name = '192.0.2.0/24'; Site = $standInSiteA })
$standInSiteB = [pscustomobject]@{ Name = 'Site-B'; Subnets = @() }
$standInSiteB.Subnets = @(
    [pscustomobject]@{ Name = '198.51.100.0/24'; Site = $standInSiteB },
    [pscustomobject]@{ Name = '203.0.113.0/24'; Site = $standInSiteB })
$standInTrusts = @(
    foreach ($standInType in 'Forest', 'ParentChild', 'TreeRoot', 'CrossLink', 'External') {
        [pscustomobject]@{
            SourceName = 'example.test'; TargetName = "$($standInType.ToLower()).example.test"
            TrustDirection = 'Bidirectional'; TrustType = $standInType
        }
    })
$standInDomain = [pscustomobject]@{
    Name = 'example.test'
    DomainControllers = @(New-Named 'dc01.example.test')
    PdcRoleOwner = New-Named 'dc01.example.test'
    RidRoleOwner = New-Named 'dc01.example.test'
    InfrastructureRoleOwner = New-Named 'dc01.example.test'
    DomainMode = 'Windows2016Domain'
}
$standInDomain | Add-Member ScriptMethod GetDirectoryEntry {
    [pscustomobject]@{ distinguishedName = 'DC=example,DC=test' } }
$standInForest = [pscustomobject]@{
    Name = 'example.test'
    SchemaRoleOwner = New-Named 'dc01.example.test'
    NamingRoleOwner = New-Named 'dc01.example.test'
    Domains = @($standInDomain)
    GlobalCatalogs = @(New-Named 'dc01.example.test')
    Sites = @($standInSiteA, $standInSiteB)
}
$standInForest | Add-Member ScriptMethod GetAllTrustRelationships { $standInTrusts }
$standInSearcher = [pscustomobject]@{
    Filter = ''; PageSize = 0
    PropertiesToLoad = [System.Collections.ArrayList]::new()
}
$standInSearcher | Add-Member ScriptMethod FindAll { $StandInResults }
$standInSearcher | Add-Member ScriptMethod FindOne { $StandInResults | Select-Object -First 1 }
$StandIn = [pscustomobject]@{
    Forest = $standInForest; Domain = $standInDomain; Searcher = $standInSearcher
}
"""

_GPO_A = "{31B2F340-016D-11D2-945F-00C04FB984F9}"
_GPO_B = "{6AC1786C-016F-11D2-945F-00C04FB984F9}"
# Stand-in search results per script, as PowerShell: one result per line (a comma
# would pass the next New-Result call as an argument).
_RESULTS: dict[str, str] = {
    "domain": "$StandInResults = @(New-Result @{ nETBIOSName = @('EXAMPLE') })",
    "ous": (
        "$StandInResults = @("
        "New-Result @{ distinguishedName = @('OU=One,DC=example,DC=test'); name = @('One');"
        f" gPLink = @('[LDAP://cn={_GPO_A},cn=policies,cn=system,DC=example,DC=test;0]') }}\n"
        "New-Result @{ distinguishedName = @('OU=Two,DC=example,DC=test'); name = @('Two');"
        " description = @('Synthetic');"
        f" gPLink = @('[LDAP://cn={_GPO_A},cn=policies,cn=system,DC=example,DC=test;2]"
        f"[LDAP://cn={_GPO_B},cn=policies,cn=system,DC=example,DC=test;1]') }}\n"
        "New-Result @{ distinguishedName = @('OU=Three,DC=example,DC=test');"
        " name = @('Three') })"
    ),
    "gpos": (
        "$StandInResults = @("
        f"New-Result @{{ name = @('{_GPO_A}'); displayName = @('Synthetic A');"
        " gPCFileSysPath = @('\\\\example.test\\SysVol\\example.test\\Policies\\a') }\n"
        f"New-Result @{{ name = @('{_GPO_B}') }})"
    ),
    "principals": (
        "$StandInResults = @("
        "New-Result @{ objectGUID = @(,[byte[]](1..16)); objectSid = @('S-1-5-21-1-2-3-1001');"
        " sIDHistory = @('S-1-5-21-1-2-3-2001'); objectClass = @('top', 'person', 'user');"
        " sAMAccountName = @('synthetic'); distinguishedName = @('CN=synthetic,DC=example,DC=test')"
        " }\n"
        "New-Result @{ objectGUID = @(,[byte[]](17..32)); objectSid = @('S-1-5-21-1-2-3-1002');"
        " sIDHistory = @('S-1-5-21-1-2-3-2002', 'S-1-5-21-1-2-3-2003');"
        " objectClass = @('top', 'group') }\n"
        "New-Result @{ objectGUID = @(,[byte[]](33..48)); objectSid = @('S-1-5-21-1-2-3-1003');"
        " objectClass = @('top', 'computer') })"
    ),
}

SCRIPTS: dict[str, tuple[Callable[[], str], str]] = {
    "forest": (ad_discovery.discovery_script_forest, "/api/discovery/forest"),
    "domain": (ad_discovery.discovery_script_domain, "/api/discovery/domain"),
    "domain-named": (
        lambda: ad_discovery.discovery_script_domain("example.test"), "/api/discovery/domain"
    ),
    "sites": (ad_discovery.discovery_script_sites, "/api/discovery/sites"),
    "ous": (lambda: ad_discovery.discovery_script_ous("example.test"), "/api/discovery/ous"),
    "gpos": (ad_discovery.discovery_script_gpos, "/api/discovery/gpos"),
    "principals": (
        lambda: ad_discovery.discovery_script_principal_search("synthetic"),
        "/api/discovery/principals",
    ),
}


def _stand_in(script: str, results: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        script = re.sub(pattern, lambda _m, r=replacement: r, script, flags=re.MULTILINE)
    for token in _DIRECTORY_TOKENS:
        assert token not in script, f"a directory call has no stand-in: {token}"
    marker = "$ErrorActionPreference = 'Stop'\n"
    assert script.count(marker) == 1
    return script.replace(marker, marker + _PRELUDE + results + "\n")


def _run(kind: str, tmp_path: Path) -> dict[str, Any]:
    generate, _route = SCRIPTS[kind]
    path = tmp_path / f"{kind}.ps1"
    path.write_text(_stand_in(generate(), _RESULTS.get(kind.split("-")[0], "")), "utf-8")
    assert PWSH is not None
    completed = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        yield TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


@pytest.mark.parametrize("kind", SCRIPTS)
def test_each_discovery_script_output_is_accepted_by_its_importer(
    kind: str, client: TestClient, tmp_path: Path
) -> None:
    output = _run(kind, tmp_path)
    response = client.post(SCRIPTS[kind][1], json=output)
    assert response.status_code == 200, (output, response.text)


def test_forest_trusts_carry_their_transitivity(tmp_path: Path) -> None:
    # Before: "transitive": null for every trust, which the importer refuses.
    trusts = {trust["target"]: trust for trust in _run("forest", tmp_path)["trusts"]}
    assert {target: trust["transitive"] for target, trust in trusts.items()} == {
        "forest.example.test": True,
        "parentchild.example.test": True,
        "treeroot.example.test": True,
        "crosslink.example.test": True,
        "external.example.test": False,
    }


def test_a_trust_of_unknown_transitivity_stops_the_forest_script(tmp_path: Path) -> None:
    script = _stand_in(ad_discovery.discovery_script_forest(), "").replace(
        "foreach ($standInType in 'Forest',", "foreach ($standInType in 'Kerberos', 'Forest',"
    )
    path = tmp_path / "kerberos.ps1"
    path.write_text(script, "utf-8")
    assert PWSH is not None
    completed = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode != 0
    assert "Cannot tell whether the Kerberos trust to kerberos.example.test" in completed.stderr


def test_the_domain_script_reads_the_domain_object(tmp_path: Path) -> None:
    # Before: $Domain is a [string] parameter, so the Domain object assigned
    # to it became its name and every property read was null.
    for kind in ("domain", "domain-named"):
        output = _run(kind, tmp_path)
        assert output["domain_controllers"] == ["dc01.example.test"]
        assert output["pdc_emulator"] == "dc01.example.test"
        assert output["netbios_name"] == "EXAMPLE"


def test_a_single_sid_history_value_is_a_list(tmp_path: Path) -> None:
    # Before: one value was emitted as a string, which the importer refuses.
    principals = _run("principals", tmp_path)["principals"]
    assert [p["sid_history"] for p in principals] == [
        ["S-1-5-21-1-2-3-2001"],
        ["S-1-5-21-1-2-3-2002", "S-1-5-21-1-2-3-2003"],
        [],
    ]
