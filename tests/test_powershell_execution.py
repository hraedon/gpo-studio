"""Every PowerShell Studio writes, run by PowerShell itself (final-r9).

DeepSeek executed a discovery script under ``pwsh`` and a U+2019 in the domain
closed the literal and ran ``Write-Output PWNED``. Reading the text is not
enough to rule that out; PowerShell's own parser and runtime are. Each writer
here produces a script with hostile values -- every character PowerShell reads
as a single quote, followed by code -- and the script is run:

* the discovery scripts up to their parameter binding (the line after
  ``param()`` is replaced with one that prints each bound value and exits, so
  nothing reaches a directory service);
* the plan in full, after its ``#Requires`` line, with every GroupPolicy cmdlet
  replaced by a function that prints the arguments it was given (and a stub
  ``GPDomain`` so its domain check passes).

Each test asserts the injected command never ran and every value arrived
exactly as given: CRLF registry data included (Sol final-r9 B3). Nothing here
touches a network: the stubs are local functions and the discovery scripts
exit before their first directory call. Skipped where ``pwsh`` is absent.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from gpo_studio import ad_discovery, export
from gpo_studio.emit_integrity import PS_SINGLE_QUOTES, EmitError
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter

PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this host")

MARKER = "STUDIO-INJECTED"


def _payloads() -> list[object]:
    cases = []
    for quote in PS_SINGLE_QUOTES:
        name = f"U+{ord(quote):04X}"
        # DeepSeek's shape: close the literal and the param(), run, comment out.
        cases.append(pytest.param(f"lab{quote} ) ; Write-Output {MARKER} ; #", id=f"{name}-param"))
        # Close, run, reopen: valid in any argument position.
        cases.append(
            pytest.param(f"lab{quote}; Write-Output {MARKER}; {quote}", id=f"{name}-statement")
        )
    return cases


def _run(script: str, tmp_path: Path) -> str:
    path = tmp_path / "script.ps1"
    path.write_text(script, encoding="utf-8-sig")
    assert PWSH is not None
    completed = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed.stdout + completed.stderr


def _decoded(output: str, label: str) -> list[str]:
    return [
        base64.b64decode(line.split("=", 1)[1]).decode("utf-8")
        for line in output.splitlines()
        if line.startswith(f"{label}=")
    ]


def _bound(script: str, parameters: tuple[str, ...]) -> str:
    """The script up to its parameter binding, then each value and exit."""
    marker = "$ErrorActionPreference = 'Stop'"
    assert marker in script
    report = "; ".join(
        f"Write-Output ('{name}=' + [Convert]::ToBase64String("
        f"[Text.Encoding]::UTF8.GetBytes([string]${name})))"
        for name in parameters
    )
    return script.replace(marker, report + "; exit 0", 1)


_DISCOVERY = {
    "domain": (lambda v: ad_discovery.discovery_script_domain(v), ("Domain",)),
    "gpos": (lambda v: ad_discovery.discovery_script_gpos(v), ("Domain",)),
    "ous": (lambda v: ad_discovery.discovery_script_ous(v), ("Domain", "SearchBase")),
    "principals-term": (
        lambda v: ad_discovery.discovery_script_principal_search(v, domain="lab.test"),
        ("SearchTerm", "Domain", "ObjectClass", "SearchBase"),
    ),
    "principals-domain": (
        lambda v: ad_discovery.discovery_script_principal_search("jdoe", domain=v),
        ("SearchTerm", "Domain", "ObjectClass", "SearchBase"),
    ),
}


@pytest.mark.parametrize("payload", _payloads())
@pytest.mark.parametrize("script", sorted(_DISCOVERY))
def test_a_discovery_script_binds_the_value_and_runs_nothing(
    tmp_path: Path, script: str, payload: str
) -> None:
    generate, parameters = _DISCOVERY[script]
    text = generate(payload)
    output = _run(_bound(text, parameters), tmp_path)
    assert MARKER not in [line.strip() for line in output.splitlines()], output
    bound = [value for name in parameters for value in _decoded(output, name)]
    assert payload in bound, output


@pytest.mark.parametrize(
    "call",
    [
        lambda: ad_discovery.discovery_script_ous("lab.test", search_base="OU=X' ) ; #"),
        lambda: ad_discovery.discovery_script_principal_search("x", object_class="user)(cn=*"),
        lambda: ad_discovery.discovery_script_domain("lab\nWrite-Output x"),
    ],
    ids=["search-base-not-a-dn", "object-class-not-a-token", "domain-line-break"],
)
def test_a_discovery_value_outside_its_kind_is_refused(call: object) -> None:
    with pytest.raises(EmitError):
        call()  # type: ignore[operator]


#: Each GroupPolicy cmdlet the plan calls, as a local function printing its
#: arguments base64-encoded (so a CR or LF in a value cannot hide in the output),
#: and a ``Microsoft.GroupPolicy.GPDomain`` that resolves any name to itself, so
#: the plan's domain check passes. Its behaviour against a stateful stand-in
#: (domain refusal, GPO binding, security filtering) is
#: `tests/test_plan_binding.py`'s.
_STUBS = """
Add-Type -TypeDefinition @'
namespace Microsoft.GroupPolicy {
  public class GPDomain {
    public string DomainName { get; private set; }
    public GPDomain(string name) { DomainName = name; }
  }
}
namespace StudioStub {
  public class Entry {
    readonly string dn;
    public Entry(string path) { dn = path.Substring(path.IndexOf('/', 7) + 1); }
    public System.Guid Guid { get { return new System.Guid(
      System.Security.Cryptography.MD5.HashData(System.Text.Encoding.UTF8.GetBytes(dn))); } }
    public System.Collections.Generic.Dictionary<string, Prop> Properties { get {
      var props = new System.Collections.Generic.Dictionary<string, Prop>();
      props["defaultNamingContext"] = new Prop("DC=stub");
      props["rootDomainNamingContext"] = new Prop("DC=stub");
      props["objectSid"] = new Prop(new byte[] { 1 });
      return props; } }
  }
  public class Prop {
    public object Value { get; private set; }
    public Prop(object value) { Value = value; }
  }
  public class Identity {
    public Sid User { get { return new Sid("S-1-5-21-9-9-9-1500"); } }
    public static Identity GetCurrent() { return new Identity(); }
  }
  public class Account {
    public string Value { get; private set; }
    public Account(string value) { Value = value; }
    public Sid Translate(System.Type type) {
      return new Sid("S-1-5-21-9-9-9-" + (Value.GetHashCode() & 0x7fffffff));
    }
  }
  public class Sid {
    public string Value { get; private set; }
    public Sid(string value) { Value = value; }
    public Sid(byte[] data, int offset) { Value = "S-1-5-21-9-9-8"; }
    public Account Translate(System.Type type) { return new Account("STUB\\\\" + Value); }
  }
}
'@
$script:granted = @{}
function _Out($label, $value) {
  # To the console, not the pipeline: a stub's pipeline output is its return value.
  [Console]::Out.WriteLine($label + '=' + [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes([string]$value)))
}
function Get-GPO { param($Guid, $Name, [switch]$All, $Domain, $ErrorAction) @() }
function New-GPO { param($Name, $Comment, $Domain)
  _Out 'NAME' $Name; _Out 'COMMENT' $Comment
  [pscustomobject]@{ Id = [guid]::NewGuid(); DisplayName = $Name; GpoStatus = '';
    WmiFilter = $null; Owner = 'STUB\\Domain Admins' } }
function Rename-GPO { param($Guid, $TargetName, $Domain) _Out 'RENAME' $TargetName }
function Set-GPRegistryValue { param($Guid, $Key, $ValueName, $Type, $Value, $Domain)
  _Out 'KEY' $Key; _Out 'VALUENAME' $ValueName
  foreach ($item in @($Value)) { _Out 'VALUE' $item } }
function Remove-GPRegistryValue { param($Guid, $Key, $ValueName, $Domain, $ErrorAction)
  _Out 'KEY' $Key; _Out 'VALUENAME' $ValueName }
function Get-GPRegistryValue { param($Guid, $Key, $Domain, $ErrorAction) _Out 'KEY' $Key; $null }
function Get-GPInheritance { param($Target, $Domain) _Out 'TARGET' $Target
  $type = 'Domain'
  if ($Target -match '(?i)^OU=') { $type = 'OU' }
  [pscustomobject]@{ ContainerType = $type; Path = $Target; GpoLinks = @() } }
function New-GPLink { param($Guid, $Target, $LinkEnabled, $Enforced, $Order, $Domain) }
function Set-GPLink { param($Guid, $Target, $LinkEnabled, $Enforced, $Order, $Domain) }
function Get-GPPermission { param($Guid, [switch]$All, $TargetName, $TargetType, $Domain)
  @(foreach ($name in $script:granted.Keys) {
    [pscustomobject]@{ Permission = $script:granted[$name]; Denied = $false; Inherited = $false;
      Trustee = [pscustomobject]@{ Domain = ''; Name = $name; SidType = 'Group';
        Sid = [pscustomobject]@{ Value = ([StudioStub.Account]$name).Translate([string]).Value } } }
  }) }
function Set-GPPermission { param($Guid, $PermissionLevel, $TargetName, $TargetType,
  [switch]$Replace, $Domain)
  _Out 'TRUSTEE' $TargetName
  $script:granted[$TargetName] = [string]$PermissionLevel }
"""


def _runnable(plan: str) -> str:
    """The plan with its module requirement removed, the stubs defined after
    its ``param(...)`` block (which must stay the first statement), and the
    SID/account types replaced by stubs (their translation needs Windows)."""
    lines = [line for line in plan.splitlines() if not line.startswith("#Requires")]
    index = lines.index("param([switch]$AdoptExistingGpo)")
    text = "\n".join([*lines[: index + 1], _STUBS, *lines[index + 1 :]]) + "\n"
    for real, stub in (
        ("[System.Security.Principal.NTAccount]", "[StudioStub.Account]"),
        ("[System.Security.Principal.SecurityIdentifier]", "[StudioStub.Sid]"),
        ("[System.Security.Principal.WindowsIdentity]", "[StudioStub.Identity]"),
        ("[System.DirectoryServices.DirectoryEntry]", "[StudioStub.Entry]"),
    ):
        text = text.replace(real, stub)
    return text


def _plan_gpo(text: str) -> GPO:
    return GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name=f"Policy {text}",
        description=f"About {text}",
        settings=(
            RegistrySetting(
                "s1",
                "computer",
                "HKLM",
                "Software\\Example",
                f"Value {text}",
                "REG_SZ",
                f"Data {text}",
            ),
            RegistrySetting(
                "s2",
                "computer",
                "HKLM",
                "Software\\Example",
                "Multi",
                "REG_MULTI_SZ",
                [f"one {text}", "two"],
            ),
        ),
        links=(GPOLink("l1", "OU=Lab,DC=studio,DC=local"),),
        security_filters=(SecurityFilter("f1", "EXAMPLE\\LabAdmins"),),
    )


@pytest.mark.parametrize("payload", _payloads())
def test_the_plan_runs_with_every_value_intact_and_nothing_injected(
    tmp_path: Path, payload: str
) -> None:
    plan = export.powershell_plan(_plan_gpo(payload))
    output = _run(_runnable(plan), tmp_path)
    assert MARKER not in [line.strip() for line in output.splitlines()], output
    assert f"Policy {payload}" in _decoded(output, "NAME"), output
    (comment,) = _decoded(output, "COMMENT")
    assert comment == f"About {payload}\n\n{export.plan_binding_marker(_plan_gpo(payload))}"
    assert f"Value {payload}" in _decoded(output, "VALUENAME"), output
    values = _decoded(output, "VALUE")
    assert f"Data {payload}" in values and f"one {payload}" in values, output


@pytest.mark.parametrize(
    "data",
    [
        "Line one\r\nLine two",
        "Line one\nLine two",
        "Tab\there",
        "Zero\u200bwidth",
        "Sep\u2028x",
        # Astral format characters: [char] is one UTF-16 unit, so these take
        # [char]::ConvertFromUtf32 (Sol and DeepSeek final-r10 B1).
        "Kaithi\U000110bdsign",
        "Beam\U0001d173x",
        "Tag\U000e0001x",
        "Tags\U000e0020\U000e007fx",
        "Smile\U0001f600x",  # astral, not a format character: written inline
    ],
    ids=[
        "crlf",
        "lf",
        "tab",
        "zwsp",
        "line-separator",
        "u110bd",
        "u1d173",
        "ue0001",
        "tag-block",
        "u1f600",
    ],
)
def test_registry_data_and_a_comment_arrive_exactly(tmp_path: Path, data: str) -> None:
    """Sol final-r9 B3: valid registry data the store accepts is written by the
    plan, and arrives byte for byte, not refused at export."""
    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="Notice",
        description=data.replace("\r", ""),
        settings=(
            RegistrySetting(
                "s1", "computer", "HKLM", "Software\\Example", "LegalNoticeText", "REG_SZ", data
            ),
        ),
    )
    plan = export.powershell_plan(gpo)
    assert "\r" not in plan and data not in plan.splitlines()
    output = _run(_runnable(plan), tmp_path)
    assert _decoded(output, "VALUE") == [data], output
    marker = export.plan_binding_marker(gpo)
    assert _decoded(output, "COMMENT") == [data.replace("\r", "") + "\n\n" + marker], output


@pytest.mark.parametrize(
    "value",
    ["\uffff", "a\uffffb", "\U00010000", "\U0010fffd", "\ufeff", "\U000e0001\U000110bd"],
    ids=["ffff", "ffff-inside", "first-astral", "last-astral", "bom", "two-astral-cf"],
)
def test_a_literal_carries_every_boundary_code_point(tmp_path: Path, value: str) -> None:
    """The quoting primitive alone, at the BMP/astral boundary, read back by pwsh."""
    from gpo_studio.emit_integrity import ps_literal

    literal = ps_literal(value, "Value")
    script = (
        f"$v = {literal}\n"
        "[Console]::Out.WriteLine('V=' + [Convert]::ToBase64String("
        "[Text.Encoding]::UTF8.GetBytes([string]$v)))\n"
    )
    output = _run(script, tmp_path)
    assert _decoded(output, "V") == [value], (literal, output)
