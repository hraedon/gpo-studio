# Plan 033 WP-6 — the RSOP oracle: what it needs, measured first

Status: scoped, not built. Written 2026-08-03 after the estate qualification
round, in the same spirit as `endpoint-lane-design.md`: probe the estate before
designing against it, because the obvious plan was wrong last time and it is
wrong this time too.

WP-6 is the lane that compares `rsop.py`'s predictions against what Windows
actually resolves. That module is 575 lines, has a full typed surface
(`RsopTarget` / `RsopQuery` / `RsopResult` / `RsopDiff`), is reachable from no
API endpoint, and **has never been compared to Windows once**. It is the largest
standing unverified claim in the project, and `docs/capability-matrix.md` already
says so.

## What the estate actually offers — measured, not assumed

Probed on `LabCL01` (the client guest) over PowerShell Direct as `LAB\claude`:

| Probe | Result |
|---|---|
| OS build | `26200` — the frozen `client_build_family` |
| `GroupPolicy` module | **absent** |
| `Get-GPResultantSetOfPolicy` | **absent** (it ships with the module) |
| `gpresult.exe /x <file> /f` | exit **0**, no file, `INFO: The user "LAB\claude" does not have RSoP data.` |
| `gpresult.exe /x <file> /f /scope:computer` | exit 0, **230,218 bytes**, root `Rsop`, namespace `http://www.microsoft.com/GroupPolicy/Rsop` |
| `[adsisearcher]` | available |

Three of those change the design.

**1. The registry's stated oracle is not available.** `platforms.json` describes
the `rsop-endpoint` lane's oracle as "`gpresult /x` **and
`Get-GPResultantSetOfPolicy`**". The client has no `GroupPolicy` module, and
RSAT is a Feature-on-Demand whose source is on the internet — which an estate
with no egress cannot reach. That is the isolation invariant working, not a gap
to fix. The lane must be built on `gpresult.exe` alone, or drive
`Get-GPResultantSetOfPolicy` from `LabMS01` across the private switch (untested;
see open questions).

**2. `gpresult /x` fails silently, and it is the same trap this repo has hit
twice.** Without `/scope:computer` it exits **0**, writes **no file**, and
reports that the invoking account has no RSoP data — true, because the brokered
account has never logged on interactively to the client. A lane that trusted the
exit code would proceed to parse a file that does not exist, or worse, parse a
*stale* one from a previous run and certify it.

This is the `gpupdate.exe` lesson again (a native exe sets `$LASTEXITCODE`
without throwing, so an empty `catch` never fires) and the `Compress-Archive`
lesson again (an operation that reports success while silently dropping
content). The rule this lane must encode: **for every native exe, assert on the
artifact, never on the exit code.** Capture stdout, require the file to exist,
require it to parse, and require its `ComputerResults` to name the GPO the run
applied.

**3. The computer-scope half is reachable today with inbox tools only.**
`/scope:computer` produces a genuine `Rsop` document naming the applied GPOs.
`[adsisearcher]` works, so the LDAP `tokenGroups` collection WP-6 requires for
the computer token — and which `platforms.json` explicitly demands *instead of*
an interactive `whoami /groups` — is available without RSAT.

## The tranche

### WP-6A — reconcile `platforms.json` with reality (do this first; it is nearly free)

The corpus has 13 scenarios: 5 ready, **8 blocked**. Every one of the 8 is
blocked on a platform qualification **this session delivered**:

| Blocked scenarios | Blocked on | Status now |
|---|---|---|
| `lsdou-precedence`, `security-filtering`, `disabled-block-enforced`, `wmi-loopback-slowlink` | `client-win11` *pending-qualification* | Qualified — `endpoint-observe-20260803142424-3050`, a real 26200 client |
| `group-membership`, `regkeys-filesecurity`, `services-area`, `codec-edge-cases` | `member-ws2025-disposable` *pending-qualification* | Qualified — `wp3-security-template-20260803230220-2450` on exactly that host |

`platforms.json` still says `client-win11` is "still not yet tested, so no
certification depends on it" and that the disposable member server "lands within
the planned estate". Both are now false. `dc-ws2025` still describes
`mvmcitest01` and the `ad.hraedon.com` forest.

**This is the fourth recurrence of one failure mode in this project**: plan
status lines said `proposed` while implemented; the capability matrix said
`failed` while supported; `environment-spec.md` cited an orphaned commit; now
the platform registry says `pending` while qualified. AGENTS.md already carries
*"a landed domain layer is not a capability"* and *"plan status lines update with
the code"*. It needs a third: **a qualification is not real until the registry
that gates work on it says so.** Worth a test that fails when
`platforms.json` and `environment-spec.md` disagree about a host's status —
otherwise this recurs a fifth time.

Note the ordering trap: `gpresult`, `whoami` and `secedit` are marked
"rides the client-win11 qualification" / needs an OS pin. They unblock as a
consequence of WP-6A, but `lgpo` stays `pending-qualification` **by design** —
see WP-5 below.

### WP-6B — the computer-scope RSOP lane

Build on the two-guest endpoint lane, which already applies real policy to
`LabCL01`, waits on CSE evidence rather than a timer, and separates *lane
failure* from *inconclusive control* from *finding*. Reuse all of it.

1. Author a disposable topology on `LabMS01` — OU, GPOs, links, order,
   enforcement, block-inheritance, security filtering. `build-endpoint-candidate.py`
   is the model. **No pre-existing lab GPOs**, per WP-6.
2. Compute the prediction with `rsop.py` on the controller, *before* applying
   anything. Commit it as an input artifact so the prediction cannot be
   retrofitted to the observation.
3. Apply, settle on CSE evidence, then capture `gpresult /x … /f /scope:computer`
   with artifact-based assertions as above.
4. Parse the `Rsop` namespace into the same shape `rsop.py` emits, and diff.
5. Three outcomes, never collapsed: prediction matches; prediction wrong (a
   finding about Studio); experiment did not run (inconclusive).

The four already-authored `rsop-topology` scenarios are the candidate set —
they exist, they encode the questions, and they unblock at WP-6A.

**Carry the vocabulary-control lesson across.** The endpoint lane needed a
hand-written native control row, because a candidate that legitimately fails to
apply is indistinguishable in the evidence from a real defect. An RSOP lane needs
the same: at least one row whose winning GPO is decided by a mechanism Studio
does not model, so "Studio predicted wrong" can be told apart from "nothing
applied".

### WP-6C — user scope is a scope decision, not a task

The user half needs an interactive logon on the client, which the estate has
never had and which PowerShell Direct does not provide. Options: script an
autologon on `LabCL01` and snapshot it; drive
`Get-GPResultantSetOfPolicy -User` from `LabMS01` across the private switch; or
**declare WP-6 computer-scope-only and say so in the capability matrix.**

I would take the third for now. Computer scope exercises link order,
enforcement, block-inheritance and security filtering — the whole of `rsop.py`'s
interesting surface — and loopback/user-side is a second lane's worth of work
that should not gate the first result.

## Adjacent, and cheaper than it looks

**WP-3 expansion is unblocked.** Four security-template scenarios were waiting on
a disposable member server that now exists and is qualified. The lane already
runs there. Expanding it to the services / regkeys / filestore / group_mgmt areas
needs no new infrastructure — and `security_template.py` is the one domain layer
already *proven* wrong on the wire, so this is where a lane is most likely to
find something.

**WP-5 needs a ruling before it can be planned.** The lane requires `LGPO.exe`;
the estate has none and cannot fetch it. Either push the binary through
`psdirect` with a pinned hash — which puts an external Microsoft binary on a
deliberately isolated estate — or narrow WP-5 to its domain-GPO-processing leg
and record that the LGPO path is untested. **Recommendation: narrow it.** The
LGPO leg mostly tests Microsoft's tool; the domain leg tests Studio's output
reaching a client through SYSVOL, which is the claim that matters.

**WI-025** (WP-1B candidate artifacts not hash-bound) applies to the endpoint
lane too — it already takes `--candidate-root`, so it never had the
guest-supplied-expectation defect, but it records no candidate hashes either.
Same fix, one re-certification run for both.

## Open questions, deliberately not guessed

1. Can `LabMS01` reach `LabCL01` over the private switch for RPC/WMI? Domain
   join proves guest-to-guest works, but `Get-GPResultantSetOfPolicy -Computer`
   needs specific firewall state on a client SKU. Untested. If it works, WP-6C
   gets easier and the lane gains a second independent oracle.
2. Does `gpresult /x` on this build emit the extension data `rsop.py` predicts,
   or only the winning-GPO list? The 230 KB document was not parsed in detail —
   only its root, namespace and GPO names were read.
3. Is `rsop.py`'s output shape close enough to the `Rsop` schema to diff without
   a lossy adapter? If the adapter has to make choices, those choices are part
   of what is being tested and must be reviewable.

Expect this lane to rewrite what it touches. Every domain layer an external
oracle has examined has needed correction; WP-1B changed four shipped 1.0
modules. WP-6 should be scoped as *"find out whether `rsop.py` is right"*, not
*"validate `rsop.py`"*.
