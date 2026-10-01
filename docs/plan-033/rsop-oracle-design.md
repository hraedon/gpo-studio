# Plan 033 WP-6 — the RSOP oracle: design from measurement

Status: scoped, not built, as of 2026-08-03 when this was written after the
estate qualification round. The lanes have since been built and run; see
[WP-6B results](wp6b-results.md) (computer scope) and
[WP-9 results](wp9-results.md) (user scope). This document records the design
and the measurements it rests on. As with
[`endpoint-lane-design.md`](endpoint-lane-design.md), the estate was probed
before designing against it.

WP-6 is the lane that compares `rsop.py`'s predictions with what Windows
resolves. At the time of writing, that module was 575 lines with a full typed
surface (`RsopTarget` / `RsopQuery` / `RsopResult` / `RsopDiff`), was reachable
from no API endpoint, and had **never been compared to Windows**. It was the
largest standing unverified claim in the project, and
`docs/capability-matrix.md` said so.

## What the estate offers

Probed on `LabCL01` (the client guest) over PowerShell Direct as `LAB\claude`:

| Probe | Result |
|---|---|
| OS build | `26200` — the frozen `client_build_family` |
| `GroupPolicy` module | **absent** |
| `Get-GPResultantSetOfPolicy` | **absent** (it ships with the module) |
| `gpresult.exe /x <file> /f` | exit **0**, no file, `INFO: The user "LAB\claude" does not have RSoP data.` |
| `gpresult.exe /x <file> /f /scope:computer` | exit 0, **230,218 bytes**, root `Rsop`, namespace `http://www.microsoft.com/GroupPolicy/Rsop` |
| `[adsisearcher]` | available |

Three results change the design.

**1. The registry's stated oracle is not available.** `platforms.json`
describes the `rsop-endpoint` lane's oracle as "`gpresult /x` **and
`Get-GPResultantSetOfPolicy`**". The client has no `GroupPolicy` module, and
RSAT is a Feature-on-Demand whose source is on the internet, which the estate
cannot reach. That is the isolation invariant working, not a gap to fix. Build
the lane on `gpresult.exe` alone, or drive `Get-GPResultantSetOfPolicy` from
`LabMS01` across the private switch (untested; see open question 1).

**2. `gpresult /x` fails silently.** Without `/scope:computer` it exits **0**,
writes **no file**, and reports that the invoking account has no RSoP data.
That is true: the brokered account has never logged on interactively to the
client. A lane that trusted the exit code would go on to parse a file that does
not exist, or a stale one from a previous run, and certify it.

This repo has hit the same trap twice before: `gpupdate.exe` (a native exe sets
`$LASTEXITCODE` without throwing, so an empty `catch` never fires) and
`Compress-Archive` (it reports success while silently dropping content).

**Rule: for every native exe, assert on the artifact, never on the exit code.**
Capture stdout, require the file to exist, require it to parse, and require its
`ComputerResults` to name the GPO the run applied.

**3. Computer scope is reachable with inbox tools only.** `/scope:computer`
produces a real `Rsop` document naming the applied GPOs. `[adsisearcher]`
works, so the LDAP `tokenGroups` collection that WP-6 requires for the computer
token is available without RSAT. (`platforms.json` explicitly demands that
collection instead of an interactive `whoami /groups`.)

## The tranche

### WP-6A — reconcile `platforms.json` with reality (do this first)

The corpus has 13 scenarios: 5 ready and **8 blocked**. All 8 are blocked on a
platform qualification that this session delivered:

| Blocked scenarios | Blocked on | Status now |
|---|---|---|
| `lsdou-precedence`, `security-filtering`, `disabled-block-enforced`, `wmi-loopback-slowlink` | `client-win11` *pending-qualification* | Qualified — `endpoint-observe-20260803142424-3050`, a real 26200 client |
| `group-membership`, `regkeys-filesecurity`, `services-area`, `codec-edge-cases` | `member-ws2025-disposable` *pending-qualification* | Qualified — `wp3-security-template-20260803230220-2450` on exactly that host |

`platforms.json` still said `client-win11` was "still not yet tested, so no
certification depends on it", and that the disposable member server "lands
within the planned estate". Both were false. `dc-ws2025` still described
`mvmcitest01` and the `ad.hraedon.com` forest.

This was the fourth time one failure mode recurred. Plan status lines said
`proposed` while implemented; the capability matrix said `failed` while
supported; `environment-spec.md` cited an orphaned commit; and now the platform
registry said `pending` while qualified. AGENTS.md already carried "a landed
domain layer is not a capability" and "plan status lines update with the code".
The proposed third rule: **a qualification is not real until the registry that
gates work on it says so.** Add a test that fails when `platforms.json` and
`environment-spec.md` disagree about a host's status.

**Do not sweep `lgpo` up with the others.** `gpresult`, `whoami` and `secedit`
are marked "rides the client-win11 qualification" or need an OS pin, and they
unblock as a consequence of WP-6A. `lgpo` stays `pending-qualification`. The
tool was ruled in (see WP-5 below), but qualification is restored by
execution, and no lane executes it yet. Its row is correct as written.

### WP-6B — the computer-scope RSOP lane

Build on the two-guest endpoint lane. It already applies real policy to
`LabCL01`, waits on CSE evidence rather than a timer, and separates lane
failure from inconclusive control from finding. Reuse all of it.

1. Author a disposable topology on `LabMS01`: OU, GPOs, links, order,
   enforcement, block-inheritance, security filtering.
   `build-endpoint-candidate.py` is the model. **No pre-existing lab GPOs**, per
   WP-6.
2. Compute the prediction with `rsop.py` on the controller **before** applying
   anything. Commit it as an input artifact so the prediction cannot be fitted
   to the observation afterwards.
3. Apply, settle on CSE evidence, then capture
   `gpresult /x … /f /scope:computer` with the artifact-based assertions above.
4. Parse the `Rsop` namespace into the shape `rsop.py` emits, and diff.
5. Keep three outcomes separate: the prediction matches; the prediction is
   wrong (a finding about Studio); the experiment did not run (inconclusive).

The candidate set is the four already-authored `rsop-topology` scenarios. They
encode the questions and unblock at WP-6A.

**Include a vocabulary control.** The endpoint lane needed a hand-written
native control row, because a candidate that legitimately fails to apply looks
the same in the evidence as a real defect. The RSOP lane needs at least one row
whose winning GPO is decided by a mechanism Studio does not model, so that
"Studio predicted wrong" can be told apart from "nothing applied".

### WP-6C — user scope is a scope decision

**Ruled 2026-08-03: WP-6 is computer-scope-only. User scope becomes its own
work package, now `WP-9` in `plans/033`.** An interactive logon on a
disposable estate was explicitly approved, so this split is a sequencing
constraint, not a permanent one.

The user half needs an interactive logon on the client. The estate had never
had one, and PowerShell Direct does not provide one. Computer scope exercises
link order, enforcement, block-inheritance and security filtering, which is
most of `rsop.py`'s interesting surface. Loopback and user-side are a second
lane's worth of work and should not gate the first result.

What the ruling requires:

- WP-6's acceptance no longer mentions loopback or user-side winners. Those
  criteria moved to WP-9; they were not dropped. A deleted criterion is how an
  unverified claim becomes a silent one.
- `docs/capability-matrix.md` must say **computer-scope-only** against every
  `rsop.py` capability WP-6 certifies. "RSOP validated" without the qualifier
  would overclaim by half.
- Loopback stays an unverified claim until WP-9 runs, and the matrix must show
  it as one. `rsop.py` models merge and replace; nothing in WP-6 touches
  either.

WP-9 gets the interactive logon: script an autologon on `LabCL01` and take a
dedicated checkpoint, so the logged-on state is reproducible rather than set up
by hand. `Get-GPResultantSetOfPolicy -User` from `LabMS01` remains a second
oracle to try (open question 1), but it is a bonus, not the plan. Do not design
against an untested transport.

## Related work

**WP-3 expansion is unblocked.** Four security-template scenarios were waiting
on a disposable member server, which now exists and is qualified. The lane
already runs there. Expanding it to the services / regkeys / filestore /
group_mgmt areas needs no new infrastructure. `security_template.py` is the one
domain layer already proven wrong on the wire, so it is where a lane is most
likely to find something.

**WP-5 keeps both legs: LGPO is approved on the estate.** The lane requires
`LGPO.exe`; the estate has none and cannot fetch it. This document recommended
narrowing WP-5 to its domain-GPO-processing leg. **The owner ruled on
2026-08-03 that LGPO.exe on the estate is acceptable**, so the lane keeps its
LGPO leg and the binary is pushed in over `psdirect`.

The ruling applies to this estate only. The lab guests are disposable,
checkpoint-backed and isolated, so an external Microsoft binary is a controlled
addition to a throwaway machine. The isolation invariant is about egress, not
about what is deliberately placed inside.

WP-5 must implement all of these conditions:

1. **Hash-pin on the way in and verify on the guest.** The transfer is the
   trust boundary. `environment-spec.md` records `lgpo_sha256 = 0c97f295…`
   from the mvmcitest01 era. WP-5 must verify the binary it pushes against a
   pin, not just record the hash of whatever arrived; recording alone is not
   verification.
2. **Stage it outside the golden checkpoints.** Push it after restore, so no
   `domain-joined` checkpoint carries the binary and the estate stays
   reproducible from clean media.
3. **Restore `lgpo` to qualified in `platforms.json` only when the lane
   executes it.** The 2026-07-29 de-gating exists because a `pass` was being
   gated on a binary no lane ran. WP-5 earns the qualification back by
   execution.

The domain leg matters most, because it tests Studio's output reaching a client
through SYSVOL. WP-5's acceptance requires both legs; that is unchanged.

**WI-025** (WP-1B candidate artifacts not hash-bound) applies to the endpoint
lane too. That lane already takes `--candidate-root`, so it never had the
guest-supplied-expectation defect, but it records no candidate hashes either.
Same fix, one re-certification run for both.

## Open questions (not guessed)

1. Can `LabMS01` reach `LabCL01` over the private switch for RPC/WMI? Domain
   join proves guest-to-guest traffic works, but
   `Get-GPResultantSetOfPolicy -Computer` needs specific firewall state on a
   client SKU. Untested. If it works, WP-6C gets easier and the lane gains a
   second independent oracle.
2. Does `gpresult /x` on this build emit the extension data `rsop.py` predicts,
   or only the winning-GPO list? The 230 KB document was not parsed in detail;
   only its root, namespace and GPO names were read.
3. Is `rsop.py`'s output shape close enough to the `Rsop` schema to diff without
   a lossy adapter? If the adapter has to make choices, those choices are part
   of what is being tested and must be reviewable.

Expect this lane to rewrite what it touches. Every domain layer an external
oracle has examined has needed correction, and WP-1B changed four shipped 1.0
modules. Scope WP-6 as "find out whether `rsop.py` is right", not "validate
`rsop.py`".
