# WP-9 — the user-scope RSOP lane: what it found

**Status:** built and run 2026-08-04.

WP-6B settled the computer side of `rsop.py` and was ruled computer-scope-only,
because the estate had never had an interactive logon. It has one now
(`windows-evidence-lab`, `scripts/enable_lab_autologon.ps1` and the
`user-logged-on` checkpoint), so this is the lane that can settle what was
deferred: user-side resolution, loopback merge, and loopback replace.

## What made it cheaper than the design assumed

The design note expected the user half to need a session to execute *inside*,
and carried `Get-GPResultantSetOfPolicy -User` from the member server as a
possible second oracle for the same reason. Neither turned out to be necessary,
and the difference came from measuring the client before building anything:

| measured on the estate | consequence |
|---|---|
| `gpresult /x <f> /f /scope:user /user <DOMAIN>\<principal>` run by an admin over PowerShell Direct writes a real `UserResults` document for a principal signed in at the console | the observation half runs as the harness account, outside the session |
| the same call **without** `/user` exits 0 and writes nothing | the computer lane's trap, one argument further along |
| the principal's hive is readable at `HKEY_USERS\<SID>` | winning HKCU values are readable from outside the session. `HKCU` in the harness process is the *harness account's* hive, and reading it would report every expected value absent — a clean sweep of false findings |
| a scheduled task with `-LogonType Interactive` runs inside the principal's session, and needs no password | `gpupdate /target:user` can be forced for another account |

Open question 1 of the design note — whether the member server can reach the
client over the private switch for RPC/WMI — is therefore **still untested and
no longer needed**.

## The loopback control is the reason the lane can be believed

Under `replace`, the expected observation is that an entire GPO's values are
**absent**. That is also exactly what a run in which loopback never engaged
looks like. Read only the values, and a machine that ignored loopback entirely
produces a clean "pass" for the model's loopback behaviour.

Windows states the mode it actually used in event **5311**, so the finalizer
treats a mode mismatch as `inconclusive` — never a pass it did not earn, never
a finding it invented. Same lesson as the endpoint lane's native vocabulary
control, arrived at from the other direction: a mechanism Studio does not model
has to be visible in the evidence, or its absence is indistinguishable from a
model failure.

**It was needed on the first loopback run.** See below.

## What is gated, and what is only recorded

The winners are gated. The applied-GPO sets are **recorded and not gated**
(WI-032): `RsopResult` carries one `is_applied` per GPO meaning "applied on at
least one side", while `UserResults` lists what applied to the *user*. On a
topology whose GPOs also scope the computer those are different questions, and
gating a comparison between them would manufacture findings out of a reporting
gap in the model's result shape.


## The certification

Three scenarios, each its own experiment with its own topology and its own
prediction, each `pass`:

| scenario | loopback | predicted = observed | what it settles |
|---|---|---|---|
| `user-side-disabled` | disabled | `Control=present`, `UserWinner=userOn`, **`UserVal` absent** | a disabled user side contributes nothing to the user scope |
| `loopback-merge` | merge | `Loop=computerLocation`, `CompOnly=1`, `UserOnly=1`, `Control=present` | computer-location user settings win conflicts, and user-location settings survive |
| `loopback-replace` | replace | `Loop=computerLocation`, `CompOnly=1`, `Control=present`, **`UserOnly` absent** | the user's own container is discarded entirely |

Run ids, each tagged and each bound to a clean tree:
`rsop-user-observe-20260804050024-4383`,
`rsop-user-observe-20260804045552-9148`,
`rsop-user-observe-20260804045809-8312`.
Verdicts are committed under `wp9-evidence/`.

In every case the model's prediction — computed on the controller *before*
anything was applied, and committed as an input artifact — matched Windows
exactly. `rsop.py` was not modified during any of this: the two corrections
this session made were both to the harness.

**Read it narrowly.** It covers user-side resolution, loopback merge and
loopback replace, on a real 26200 client, for the topologies above. It says
nothing about security filtering on user principals, WMI filters, or slow-link
behaviour on the user side — those scenarios are still blocked or unwritten.

### One question answered on the way

`user-side-disabled` carried an open question: does `gpresult` report a GPO
with its user side disabled as applied-with-no-settings, or omit it? **It omits
it.** `UserResults` named the two enabled GPOs and nothing else.

## Two defects the first live runs found, neither of which produced a wrong answer

**1. Loopback could not engage.** `UserPolicyMode` is a *machine* policy, and
the observation half refreshed only the user side — so the client was asked to
demonstrate a mode it had never received. The evidence showed only the
user-location values, event 5311 said "no loopback mode", and the finalizer
refused to call it anything but a lane problem.

Reading the values alone would have reported *"replace discarded the
computer-location settings"*: a fabricated finding about a mode that never ran.
The computer side now refreshes first, and on a loopback scenario that ordering
is the experiment rather than tidiness.

**2. The lane could not run twice.** The driver's post-teardown refresh was a
bare `gpupdate` over PowerShell Direct, which refreshes the *harness account's*
user policy — an account this lane never gives any policy to. The principal's
HKCU values survived teardown, and the next run found them in the hive before it
began.

The pre-run residual check caught them rather than letting them satisfy the
control, but a lane that cannot run back-to-back fails WP-8's repeatability
requirement. The teardown refresh is now a *mode* of the observation script,
because only that script knows how to refresh policy inside another account's
session.

Both were found by guards the lane already had, which is the outcome to want:
the failure modes this lane is most exposed to are the ones that produce a
confident wrong answer, and both of these were caught before they could.

**3. The `UserPolicyMode` constant was backwards, and Windows caught it.** 1 is
merge and 2 is replace; the builder had them the other way round. The first
replace run therefore authored merge, event 5311 said so, and the finalizer
reported `inconclusive` instead of comparing anything.

What that avoided is worth stating precisely. Under merge the user-location GPO
still applies, so `UserOnly` was present — and the replace prediction says it
must be absent. Without the 5311 control the lane would have reported
*"`rsop.py` gets loopback replace wrong"*: a confident, well-evidenced, entirely
false finding about the model, caused by a constant in the harness.

Three defects, three guards, no wrong answers. The guards were the point.
