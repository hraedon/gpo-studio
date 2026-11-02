"""Each generated discovery script's output is accepted by its importer (#148 review).

The scripts run on a domain-joined Windows machine and the importers validate
what they print, but nothing ran one against the other. Round 2 of #148 made
the forest importer require a real boolean ``transitive``, while the script
emitted ``$_.Transitive``, a property neither .NET trust type has, so every
generated forest document was refused.

Here each script runs under ``pwsh`` with its directory calls replaced by
synthetic stand-ins, and its JSON is posted to the import route. The
replacements are checked: a directory call this file does not know fails the
test instead of reaching a real directory. Skipped where ``pwsh`` is absent.

The stand-ins behave like the real types where the scripts went wrong live
(#148 round 4, a read-only run against a lab domain):

* a missing attribute is an EMPTY value collection that PowerShell treats as
  true, not ``$null``, and indexing it with ``-1`` fails;
* the searcher checks each filter against RFC 4515, plus AD's refusal of an
  empty assertion value, and echoes it to stderr so tests can read it.

They are still stand-ins: real .NET enums and Windows PowerShell 5.1's
``ConvertTo-Json`` can differ, which is what the live run checks.
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
    (r"\[ADSI\]\"LDAP://\$\(\$adDomain\.Name\)/RootDSE\"", "$StandIn.RootDse"),
    (r"\[ADSI\]", "[string]"),
    (r"New-Object System\.Security\.Principal\.SecurityIdentifier\(", "New-StandInSid("),
]
_DIRECTORY_TOKENS = ("System.DirectoryServices", "[ADSI]", "SecurityIdentifier")

_STAND_IN_TYPES = r"""
using System;
using System.Collections;

public class StandInValues : ReadOnlyCollectionBase {
    public StandInValues(IEnumerable items) {
        if (items != null) { foreach (object item in items) { InnerList.Add(item); } }
    }
    // Like ResultPropertyValueCollection: no negative index.
    public object this[int index] { get { return InnerList[index]; } }
}

public class StandInProperties {
    private readonly Hashtable values;
    public StandInProperties(Hashtable values) {
        this.values = new Hashtable(values, StringComparer.OrdinalIgnoreCase);
    }
    // Like ResultPropertyCollection: a missing name is an empty collection.
    public StandInValues this[string name] {
        get { return new StandInValues(values[name] as IEnumerable); }
    }
}

public static class StandInLdapFilter {
    public static void Check(string filter) {
        int i = 0;
        Filter(filter, ref i);
        if (i != filter.Length) { throw Bad(filter, "text after the filter"); }
    }
    static Exception Bad(string filter, string why) {
        return new ArgumentException("Invalid LDAP filter (" + why + "): " + filter);
    }
    static void Expect(string f, ref int i, char c) {
        if (i >= f.Length || f[i] != c) { throw Bad(f, "expected '" + c + "' at " + i); }
        i++;
    }
    static bool IsHex(char c) { return Uri.IsHexDigit(c); }
    static void Filter(string f, ref int i) {
        Expect(f, ref i, '(');
        if (i >= f.Length) { throw Bad(f, "unterminated"); }
        char c = f[i];
        if (c == '&' || c == '|') {
            i++;
            int count = 0;
            while (i < f.Length && f[i] == '(') { Filter(f, ref i); count++; }
            if (count == 0) { throw Bad(f, "empty filter list"); }
        } else if (c == '!') {
            i++;
            Filter(f, ref i);
        } else {
            Item(f, ref i);
        }
        Expect(f, ref i, ')');
    }
    static void Item(string f, ref int i) {
        int start = i;
        while (i < f.Length && (char.IsLetterOrDigit(f[i]) || f[i] == '-' || f[i] == ';'
                                 || f[i] == '.')) { i++; }
        if (i == start) { throw Bad(f, "missing attribute at " + start); }
        if (i < f.Length && (f[i] == '~' || f[i] == '>' || f[i] == '<')) { i++; }
        Expect(f, ref i, '=');
        int value = i;
        while (i < f.Length && f[i] != ')') {
            char c = f[i];
            if (c == '(' || c == '\0') { throw Bad(f, "unescaped '" + c + "' at " + i); }
            if (c == '\\') {
                if (i + 2 >= f.Length || !IsHex(f[i + 1]) || !IsHex(f[i + 2])) {
                    throw Bad(f, "bad escape at " + i);
                }
                i += 3;
                continue;
            }
            i++;
        }
        // RFC 4515 allows it; AD refuses an empty assertion value.
        if (i == value) { throw Bad(f, "empty value at " + value); }
    }
}

public class StandInSearcher {
    public string Filter = "";
    public int PageSize;
    public string SearchScope = "Subtree";
    public ArrayList PropertiesToLoad = new ArrayList();
    public object[] Results = new object[0];
    private void Searched() {
        StandInLdapFilter.Check(Filter);
        Console.Error.WriteLine("LDAP-FILTER " + Filter);
    }
    public object[] FindAll() { Searched(); return Results; }
    public object FindOne() { Searched(); return Results.Length > 0 ? Results[0] : null; }
}
"""

_PRELUDE = r"""
Add-Type -TypeDefinition $StandInTypes
function New-StandInSid { param($a)
    $v = if ($a -is [array]) { $a[0] } else { $a }
    # Like the real constructor, it also accepts SDDL aliases such as DA or BA.
    if ($v -cin @('DA', 'BA', 'AU', 'SY', 'WD')) { $v = 'S-1-5-32-544' }
    # Case-insensitive, like the real constructor.
    if ($v -isnot [string] -or $v -notlike 'S-1-*') { throw 'not a SID' }
    $sid = [pscustomobject]@{ Value = $v; BinaryLength = 2 }
    $sid | Add-Member ScriptMethod GetBinaryForm {
        param($buffer, $offset) $buffer[$offset] = 1; $buffer[$offset + 1] = 5 }
    $sid
}
function New-Named($name) { [pscustomobject]@{ Name = $name } }
function New-Result([hashtable]$properties) {
    [pscustomobject]@{ Properties = [StandInProperties]::new($properties) }
}
$standInSiteA = [pscustomobject]@{ Name = 'Site-A'; Subnets = @() }
$standInSiteA.Subnets = @([pscustomobject]@{ Name = '192.0.2.0/24'; Site = $standInSiteA })
$standInSiteB = [pscustomobject]@{ Name = 'Site-B'; Subnets = @() }
$standInSiteB.Subnets = @(
    [pscustomobject]@{ Name = '198.51.100.0/24'; Site = $standInSiteB },
    [pscustomobject]@{ Name = '2001:db8:1::/64'; Site = $standInSiteB })
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
$standInDomainEntry = [pscustomobject]@{
    distinguishedName = 'DC=example,DC=test'; 'msDS-Behavior-Version' = @(7) }
$standInDomain | Add-Member ScriptMethod GetDirectoryEntry { $standInDomainEntry }
$standInForest = [pscustomobject]@{
    Name = 'example.test'
    SchemaRoleOwner = New-Named 'dc01.example.test'
    NamingRoleOwner = New-Named 'dc01.example.test'
    Domains = @($standInDomain)
    GlobalCatalogs = @(New-Named 'dc01.example.test')
    Sites = @($standInSiteA, $standInSiteB)
}
$standInForest | Add-Member ScriptMethod GetAllTrustRelationships { $standInTrusts }
$standInSearcher = [StandInSearcher]::new()
$StandIn = [pscustomobject]@{
    Forest = $standInForest; Domain = $standInDomain; Searcher = $standInSearcher
    RootDse = [pscustomobject]@{
        configurationNamingContext = 'CN=Configuration,DC=example,DC=test' }
}
"""

_GPO_A = "{31B2F340-016D-11D2-945F-00C04FB984F9}"
_GPO_B = "{6AC1786C-016F-11D2-945F-00C04FB984F9}"
_GPO_C = "{11111111-2222-3333-4444-555555555555}"
_GPO_D = "{66666666-7777-8888-9999-AAAAAAAAAAAA}"


def _gpc(guid: str, options: int) -> str:
    return f"[LDAP://cn={guid},cn=policies,cn=system,DC=example,DC=test;{options}]"


# Stand-in search results per script, as PowerShell: one result per line (a comma
# would pass the next New-Result call as an argument).
_RESULTS: dict[str, str] = {
    "domain": "$standInSearcher.Results = @(New-Result @{ nETBIOSName = @('EXAMPLE') })",
    "ous": (
        "$standInSearcher.Results = @(\n"
        # The domain head, with its own link.
        "New-Result @{ distinguishedName = @('DC=example,DC=test'); name = @('example');"
        f" gPLink = @('{_gpc(_GPO_A, 0)}') }}\n"
        # Options 0..3 in one gPLink: the last entry is link order 1.
        "New-Result @{ distinguishedName = @('OU=Two,DC=example,DC=test'); name = @('Two');"
        " description = @('Synthetic');"
        f" gPLink = @('{_gpc(_GPO_A, 0)}{_gpc(_GPO_B, 1)}{_gpc(_GPO_C, 2)}{_gpc(_GPO_D, 3)}') }}\n"
        # No description and no links: both attributes absent.
        "New-Result @{ distinguishedName = @('OU=Three,DC=example,DC=test');"
        " name = @('Three') })"
    ),
    "gpos": (
        "$standInSearcher.Results = @(\n"
        f"New-Result @{{ name = @('{_GPO_A}'); displayName = @('Synthetic A');"
        " gPCFileSysPath = @('\\\\example.test\\SysVol\\example.test\\Policies\\a') }\n"
        f"New-Result @{{ name = @('{_GPO_B}') }})"
    ),
    "principals": (
        "$standInSearcher.Results = @(\n"
        "New-Result @{ objectGUID = @(,[byte[]](1..16)); objectSid = @('S-1-5-21-1-2-3-1001');"
        " sIDHistory = @('S-1-5-21-1-2-3-2001'); objectClass = @('top', 'person', 'user');"
        " sAMAccountName = @('synthetic'); displayName = @('Synthetic User');"
        " distinguishedName = @('CN=synthetic,DC=example,DC=test') }\n"
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
    "principals-by-sid": (
        lambda: ad_discovery.discovery_script_principal_search("S-1-5-21-1-2-3-1001"),
        "/api/discovery/principals",
    ),
}


def _stand_in(script: str, results: str, extra: str = "") -> str:
    for pattern, replacement in _REPLACEMENTS:
        script = re.sub(pattern, lambda _m, r=replacement: r, script, flags=re.MULTILINE)
    for token in _DIRECTORY_TOKENS:
        assert token not in script, f"a directory call has no stand-in: {token}"
    marker = "$ErrorActionPreference = 'Stop'\n"
    assert script.count(marker) == 1
    types = "$StandInTypes = @'\n" + _STAND_IN_TYPES + "\n'@\n"
    return script.replace(marker, marker + types + _PRELUDE + results + "\n" + extra + "\n")


def _execute(script: str, tmp_path: Path, name: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / f"{name}.ps1"
    path.write_text(script, "utf-8")
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True, text=True, timeout=180, check=False,
    )


def _run_script(
    script: str, tmp_path: Path, name: str, results: str = "", extra: str = ""
) -> tuple[dict[str, Any], list[str]]:
    completed = _execute(_stand_in(script, results, extra), tmp_path, name)
    assert completed.returncode == 0, completed.stderr
    filters = [
        line.removeprefix("LDAP-FILTER ")
        for line in completed.stderr.splitlines() if line.startswith("LDAP-FILTER ")
    ]
    return json.loads(completed.stdout), filters


def _run(kind: str, tmp_path: Path, extra: str = "") -> tuple[dict[str, Any], list[str]]:
    generate, _route = SCRIPTS[kind]
    return _run_script(
        generate(), tmp_path, kind, _RESULTS.get(kind.split("-")[0], ""), extra
    )


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
    output, _filters = _run(kind, tmp_path)
    response = client.post(SCRIPTS[kind][1], json=output)
    assert response.status_code == 200, (output, response.text)
    # Nothing the importer stored reads "None" (str(None), #148 live review).
    assert "None" not in json.dumps(client.get("/api/discovery/summary").json())


def test_forest_trusts_carry_their_transitivity(tmp_path: Path) -> None:
    trusts = {trust["target"]: trust for trust in _run("forest", tmp_path)[0]["trusts"]}
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
    completed = _execute(script, tmp_path, "kerberos")
    assert completed.returncode != 0
    assert "Cannot tell whether the Kerberos trust to kerberos.example.test" in completed.stderr


def test_the_domain_script_reads_the_domain_object(tmp_path: Path) -> None:
    for kind in ("domain", "domain-named"):
        output, filters = _run(kind, tmp_path)
        assert output["domain_controllers"] == ["dc01.example.test"]
        assert output["pdc_emulator"] == "dc01.example.test"
        assert output["functional_level"] == "Windows2016Domain"
        # The NetBIOS name is read from the domain's crossRef (#148 live review).
        assert output["netbios_name"] == "EXAMPLE"
        assert filters == ["(&(objectClass=crossRef)(nCName=DC=example,DC=test))"]


def test_a_domain_without_a_crossref_netbios_name_prints_empty_text(tmp_path: Path) -> None:
    # Before: the missing attribute printed null, stored as the text "None".
    output, _filters = _run_script(
        ad_discovery.discovery_script_domain(), tmp_path, "domain-no-netbios",
        "$standInSearcher.Results = @(New-Result @{ })",
    )
    assert output["netbios_name"] == ""


@pytest.mark.parametrize(
    ("level", "expected"), [(10, "Windows2025Domain"), (11, "Level 11")]
)
def test_a_functional_level_dotnet_cannot_name_is_reported(
    tmp_path: Path, level: int, expected: str
) -> None:
    # .NET's DomainMode reports Unknown for Windows Server 2025 (level 10).
    output, _filters = _run(
        "domain", tmp_path,
        "$standInDomain.DomainMode = 'Unknown'\n"
        f"$standInDomainEntry.'msDS-Behavior-Version' = @({level})",
    )
    assert output["functional_level"] == expected


def test_ou_links_carry_enabled_enforced_and_order_from_gplink(tmp_path: Path) -> None:
    # #160: option bit 0x1 is DISABLED, 0x2 enforced; enabled was inverted.
    # The last gPLink entry is link order 1 (#148 live review).
    ous = {ou["distinguished_name"]: ou for ou in _run("ous", tmp_path)[0]["ous"]}
    links = {
        re.search(r"\{[^}]+\}", link["target"])[0]: (  # type: ignore[index]
            link["enabled"], link["enforced"], link["order"]
        )
        for link in ous["OU=Two,DC=example,DC=test"]["gpo_links"]
    }
    assert links == {
        _GPO_A: (True, False, 4),
        _GPO_B: (False, False, 3),
        _GPO_C: (True, True, 2),
        _GPO_D: (False, True, 1),
    }
    assert [link["order"] for link in ous["OU=Two,DC=example,DC=test"]["gpo_links"]] == [
        1, 2, 3, 4
    ]


def test_the_domain_head_and_its_links_are_discovered(tmp_path: Path) -> None:
    output, filters = _run("ous", tmp_path)
    assert filters == ["(|(objectCategory=organizationalUnit)(objectCategory=domainDNS))"]
    ous = {ou["distinguished_name"]: ou for ou in output["ous"]}
    (link,) = ous["DC=example,DC=test"]["gpo_links"]
    assert (link["enabled"], link["enforced"], link["order"]) == (True, False, 1)
    assert _GPO_A in link["target"]
    # Missing attributes are empty text, not null.
    assert ous["OU=Three,DC=example,DC=test"]["description"] == ""
    assert ous["OU=Three,DC=example,DC=test"]["gpo_links"] == []


def test_principal_fields_read_missing_and_multi_valued_attributes(tmp_path: Path) -> None:
    principals = _run("principals", tmp_path)[0]["principals"]
    assert [p["sid_history"] for p in principals] == [
        ["S-1-5-21-1-2-3-2001"],
        ["S-1-5-21-1-2-3-2002", "S-1-5-21-1-2-3-2003"],
        [],
    ]
    # The last objectClass value is the most specific class; [-1] failed.
    assert [p["object_class"] for p in principals] == ["user", "group", "computer"]
    assert [p["display_name"] for p in principals] == ["Synthetic User", "", ""]
    assert [p["sam_account_name"] for p in principals] == ["synthetic", "", ""]


@pytest.mark.parametrize(
    ("term", "object_class", "expected"),
    [
        (
            "synthetic", "",
            "(&(objectCategory=*)(|(sAMAccountName=synthetic)(cn=synthetic)))",
        ),
        (
            "synthetic", "user",
            "(&(objectClass=user)(|(sAMAccountName=synthetic)(cn=synthetic)))",
        ),
        (
            "S-1-5-21-1-2-3-1001", "",
            "(&(objectCategory=*)(|(sAMAccountName=S-1-5-21-1-2-3-1001)"
            "(cn=S-1-5-21-1-2-3-1001)(objectSid=\\01\\05)))",
        ),
        (
            "a*b(c)d\\e", "",
            "(&(objectCategory=*)(|(sAMAccountName=a\\2ab\\28c\\29d\\5ce)"
            "(cn=a\\2ab\\28c\\29d\\5ce)))",
        ),
    ],
    ids=["name", "name-and-class", "sid", "rfc4515-escapes"],
)
def test_principal_search_filters_are_well_formed(
    tmp_path: Path, term: str, object_class: str, expected: str
) -> None:
    # Before: (&((objectCategory=*))...) and, for a name, (objectSid=), which
    # AD refuses; the stand-in searcher now refuses both.
    script = ad_discovery.discovery_script_principal_search(term, object_class=object_class)
    _output, filters = _run_script(script, tmp_path, "principal-filter", _RESULTS["principals"])
    assert filters == [expected]


def test_the_stand_in_searcher_refuses_the_filters_the_scripts_used_to_build(
    tmp_path: Path,
) -> None:
    for bad in (
        "(&((objectCategory=*))(|(cn=x)))",
        "(&(objectCategory=*)(|(cn=x)(objectSid=)))",
        "(cn=a*b(c)",
    ):
        script = _stand_in(
            "$ErrorActionPreference = 'Stop'\n"
            "$searcher = New-Object System.DirectoryServices.DirectorySearcher($x)\n"
            f"$searcher.Filter = '{bad}'\n$searcher.FindAll() | Out-Null\n",
            "",
        )
        completed = _execute(script, tmp_path, "bad-filter")
        assert completed.returncode != 0
        assert "Invalid LDAP filter" in completed.stderr


def test_an_empty_principal_search_term_is_refused(tmp_path: Path) -> None:
    completed = _execute(
        _stand_in(ad_discovery.discovery_script_principal_search(""), ""), tmp_path, "empty"
    )
    assert completed.returncode != 0
    assert "A search term is required" in completed.stderr


def test_sites_with_ipv4_and_ipv6_subnets_are_accepted(
    client: TestClient, tmp_path: Path
) -> None:
    output, _filters = _run("sites", tmp_path)
    assert sorted(subnet["cidr"] for subnet in output["subnets"]) == [
        "192.0.2.0/24", "198.51.100.0/24", "2001:db8:1::/64"
    ]
    response = client.post("/api/discovery/sites", json=output)
    assert response.status_code == 200, response.text


def _ous_with_gplink(gplink: str) -> str:
    return (
        "$standInSearcher.Results = @(New-Result @{"
        " distinguishedName = @('OU=Probe,DC=example,DC=test'); name = @('Probe');"
        f" gPLink = @('{gplink}') }})"
    )


@pytest.mark.parametrize(
    ("gplink", "message"),
    [
        ("[LDAP://CN=bad;garbage]", "Malformed gPLink on OU=Probe,DC=example,DC=test"),
        (f"{_gpc(_GPO_A, 0)}[LDAP://CN=bad;garbage]{_gpc(_GPO_B, 0)}", "Malformed gPLink"),
        ("[]", "Malformed gPLink"),
        (f"{_gpc(_GPO_A, 0)}junk", "Malformed gPLink"),
        (_gpc(_GPO_A, 4294967296), "is not a DWORD"),
        (_gpc(_GPO_A, 99999999999), "Malformed gPLink"),
    ],
    ids=["garbage-only", "garbage-between", "empty-entry", "trailing-text", "overflow",
         "eleven-digits"],
)
def test_a_malformed_gplink_stops_ou_discovery(tmp_path: Path, gplink: str, message: str) -> None:
    # #164 review: the regex took fragments, so a malformed entry vanished and
    # the rest were renumbered; an option above Int32 stopped the run unexplained.
    script = _stand_in(
        ad_discovery.discovery_script_ous("example.test"), _ous_with_gplink(gplink)
    )
    completed = _execute(script, tmp_path, "bad-gplink")
    assert completed.returncode != 0
    assert message in completed.stderr


@pytest.mark.parametrize(
    ("gplink", "links"),
    [
        (" ", []),
        ("", []),
        (f"{_gpc(_GPO_A, 4294967295)}", [(False, True, 1)]),
        (f"{_gpc(_GPO_A, 0)} {_gpc(_GPO_B, 1)}".replace("LDAP://", "ldap://"),
         [(False, False, 1), (True, False, 2)]),
    ],
    ids=["single-space", "empty", "dword-max", "lowercase-and-spaced"],
)
def test_well_formed_gplink_values_still_parse(
    tmp_path: Path, gplink: str, links: list[tuple[bool, bool, int]]
) -> None:
    output, _filters = _run_script(
        ad_discovery.discovery_script_ous("example.test"), tmp_path, "good-gplink",
        _ous_with_gplink(gplink),
    )
    (ou,) = output["ous"]
    assert [(link["enabled"], link["enforced"], link["order"]) for link in ou["gpo_links"]] == links


@pytest.mark.parametrize("alias", ["DA", "BA"])
def test_an_sddl_alias_is_searched_by_name_only(tmp_path: Path, alias: str) -> None:
    # #164 review, confirmed live: "DA" matched Domain Admins by SID because
    # the SecurityIdentifier constructor accepts SDDL aliases.
    _output, filters = _run_script(
        ad_discovery.discovery_script_principal_search(alias), tmp_path, "alias",
        _RESULTS["principals"],
    )
    assert filters == [f"(&(objectCategory=*)(|(sAMAccountName={alias})(cn={alias})))"]


def test_a_hexadecimal_identifier_authority_is_searched_as_a_sid(tmp_path: Path) -> None:
    # #164 review: authorities of 2^32 or more are written in hexadecimal
    # (MS-DTYP); the decimal-only gate sent such a SID down the name path.
    term = "S-1-0x010000000000-1"
    _output, filters = _run_script(
        ad_discovery.discovery_script_principal_search(term), tmp_path, "hex-sid",
        _RESULTS["principals"],
    )
    assert filters == [
        f"(&(objectCategory=*)(|(sAMAccountName={term})(cn={term})(objectSid=\\01\\05)))"
    ]


@pytest.mark.parametrize("term", ["s-1-5-21-1-2-3-1001", "S-1-0X010000000000-1", "s-1-12-1"])
def test_a_literal_sid_in_any_case_is_searched_as_a_sid(tmp_path: Path, term: str) -> None:
    # #164 review: MS-DTYP's ABNF strings are case-insensitive and Windows
    # accepts "s-1-..."; the case-sensitive gate searched these by name.
    _output, filters = _run_script(
        ad_discovery.discovery_script_principal_search(term), tmp_path, "sid-case",
        _RESULTS["principals"],
    )
    assert filters == [
        f"(&(objectCategory=*)(|(sAMAccountName={term})(cn={term})(objectSid=\\01\\05)))"
    ]


@pytest.mark.parametrize("term", ["S-1-5", "s-1-5", "DA", "ba"])
def test_aliases_and_sids_without_a_subauthority_stay_name_only(
    tmp_path: Path, term: str
) -> None:
    _output, filters = _run_script(
        ad_discovery.discovery_script_principal_search(term), tmp_path, "sid-name",
        _RESULTS["principals"],
    )
    assert filters == [f"(&(objectCategory=*)(|(sAMAccountName={term})(cn={term})))"]
