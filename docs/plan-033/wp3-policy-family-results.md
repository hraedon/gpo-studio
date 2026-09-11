# Policy-family reconciliation: the first Plan 034 lane

Status: representative serializer conformance measured on 2026-09-07;
**surfaced 2026-09-11** at `POST /api/security-template/policy-families`
(Plan 034 WP-3), in the emission direction only. This does not qualify policy
application or every supported input. Plan 034 remains in progress.

The surface composes the INF in `api.py` rather than in `policy_families.py`,
because the serializers, their codec and `build-wp3-candidate.py` are all in
this lane's bound file set: sharing a function between the builder and the
endpoint would expire both verdicts below and cost an estate re-run.
`tests/test_policy_family_surface.py` holds the two compositions equal instead,
section for section in both scopes, so the drift this document already records
catching once cannot return unnoticed. Lifting the composition into the library
belongs to the next batch that re-runs the estate anyway.

The WP-3 candidate now calls the real `AccountPolicyFamily`, `AuditPolicyFamily`,
`UserRightsFamily`, and `SecurityOptionsFamily` serializers. Previously it
handwrote the INF sections and could pass while those serializers emitted
different keys. The existing Group Membership controls remain in the candidate.

## What Windows rejected

The first member-server run, `wp3-security-template-20260907070521-9490`,
at `00f062a`, rejected `AuditDirectoryServiceAccess`. The native key is
`AuditDSAccess`, also specified by
[MS-GPSB section 2.2.4](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpsb/01f8e057-f6a8-4d6e-8a00-99bcd241b403).
The serializer and its tests were corrected in `5f7d0fb`.

The first DC run, `wp3-security-template-20260907070830-5315`, at `5f7d0fb`,
rejected `EnforceLogonRestrictions` and `EnforceUserLogonRestrictions`.
Both fields were speculative additions to the unsurfaced model. They were
removed, including their parsing and emission, in `4e27f27`; the native
`TicketValidateClient` field remains. The five-key native R7 capture had never
proved the two additional keys. The R7 unit measurements remain valid, while
the earlier description of the module as correct was too broad.

Both raw rejection messages are retained under
[`wp3-evidence/policy-families/rejected-keys`](wp3-evidence/policy-families/rejected-keys).
Neither run applied policy. Both recorded successful cleanup with no database
residuals. The first failure also exposed a runner issue: shell error handling
stopped before pulling the result. The runner now retrieves failure evidence
and lets the finalizer return a failed verdict. This path was exercised by the
DC rejection; it retained a clean-source failed verdict without creating a
passing evidence tag.

## Final measurements

Both final runs bind source commit `4e27f27cf6f4f3a6ba0cbd84af25ea0ebfecbbbc`
and have **21/21 checks passing**, exact candidate delivery, zero differences,
and no temporary database residuals.

| Guest | Run | Measured scope |
|---|---|---|
| Member server, observed domain role 3 | [`wp3-security-template-20260907071149-3752`](wp3-evidence/policy-families/member/verification.json) | Password/lockout, all nine Event Audit keys, two user rights, four registry value types, Group Membership controls |
| Domain controller, observed domain role 5 | [`wp3-security-template-20260907071106-1024`](wp3-evidence/policy-families/dc/verification.json) | Same tranche plus five Kerberos keys |

The Kerberos values are `MaxTicketAge=10`, `MaxRenewAge=7`,
`MaxServiceAge=600`, `MaxClockSkew=5`, and `TicketValidateClient=1`.
The finalizer refuses a Kerberos candidate unless the guest reports an integer
DC role (4 or 5). The member-server default excludes Kerberos rather than
accepting its empty export as evidence.

These are `secedit /validate`, `/import` into a new temporary database, and
`/export` observations. No `/configure` is invoked. The private database is
removed and its absence re-queried by the guest harness. This proves the
tested wire representations survive Windows' security database; it does not
prove endpoint application, GPMC editing, or meaningful behavior for every
possible numeric/boolean value.

The finalizer binds the builder, guest harness, controller, transport, itself,
and both serializer modules. Changing a serializer now expires the live
verdict through the existing freshness gate. Each passing run has an immutable
`evidence/<run-id>` tag. The old `verification-estate.json` remains unchanged as
retired evidence with its original five-file binding.

The committed packs retain the candidate, controller expectations, native
export, result, command streams/logs, and verdict. Harness copies are recoverable
from the tagged source and are not duplicated in the pack. Full local packs are
also retained under the controller's temporary `opencode/wp3-oracle-run-*`
directories. Tests rehash every retained raw artifact and check member/DC scope.

## Re-run

Supply the existing transport's four credential environment variables through
the credential broker or a local credential loader; never put their values in
the command line or repository. Git Bash with Windows `uv` and `pwsh` was used
for these runs.

```bash
GPO_STUDIO_LAB_HOST=<host> GPO_STUDIO_LAB_GUEST=<member> \
  bash scripts/windows-oracle/run-wp3-oracle.sh
GPO_STUDIO_LAB_HOST=<host> GPO_STUDIO_LAB_GUEST=<dc> \
  GPO_STUDIO_WP3_KERBEROS=1 bash scripts/windows-oracle/run-wp3-oracle.sh
```

Run from a clean committed checkout. Changing a bound file requires fresh
member and DC verdicts; do not edit old evidence to match the new code.

## WP-2 NetSecurity discriminator

[`netsecurity-probe/result.json`](wp3-evidence/policy-families/netsecurity-probe/result.json)
records NetSecurity `2.0.0.0` on the estate member server. Queries against an
unlinked disposable GPO succeeded for both firewall and IPsec. A synthetic
outbound TCP block rule to documentation address `192.0.2.1`, port `65000`,
was created in that GPO and read back with the authored fields intact. The
GPO was never linked; removal was followed by `Get-GPO -All` and a zero-match
check. The exact probe script is retained beside the result and takes no
credentials itself.

This settles availability and the tested firewall authoring/readback path.
It does **not** certify `network_security.py`, IPsec authoring, firewall backup
serialization, or PKI/wired/wireless policy. A model-vocabulary and backup lane
is the next step; NetSecurity availability is no longer its blocker.

## Controller test blocker

The installed pinned Playwright Firefox 1532 cannot start on this controller.
Windows Application event 33 from `SideBySide` reports that dependent assembly
`mozglue`, version `1.0.0.0`, cannot be found; invoking `firefox.exe --version`
fails before the browser starts. Its `mozglue.dll` is present, so this is not a
Studio page failure. The 27 Chromium tests pass; the two Firefox smoke tests
remain blocked locally. Keep the Firefox project enabled. Remote CI's browser
job is the independent gate while this pinned Windows browser issue is resolved.
