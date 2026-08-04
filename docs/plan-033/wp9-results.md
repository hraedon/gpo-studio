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

## Security filtering: certified

Two scenarios, both run 2026-08-04 against the estate:

| scenario | state | run |
|---|---|---|
| `user-security-filtering` | **pass** | `rsop-user-observe-20260804065146-4224` |
| `user-security-filtering-deny` | **expected-finding** | `rsop-user-observe-20260804065525-9254` |

The pass covers Read+Apply, Read-without-Apply, and Apply through a group the
principal belongs to. Predicted and observed winners are identical:
`Filter=allow`, `AllowOnly=1`, `NestedOnly=1`, `Control=present`, with
`ReadOnlyOnly` absent.

The deny scenario produced **exactly the two divergences its candidate declared
before it ran**:

- `Filter`: predicted `deny`, observed `allow`
- `DenyOnly`: predicted `1`, observed absent

That is WI-033 demonstrated rather than argued: `SecurityFilter` has no deny
polarity, so the model says a GPO applies when a deny ACE means it does not.

### The case the model cannot express

The corpus's `security-filtering` scenario asks four questions. They divide on
something that turned out to matter more than the questions themselves:
**whether Studio's model can be told about the case at all.**

| case | authored | model told | result |
|---|---|---|---|
| Read + Apply | `Set-GPPermission GpoApply` | `apply` filter | applies, wins at link order 1 |
| Read without Apply | `Set-GPPermission GpoRead -Replace` | `read` filter | does not apply |
| Apply via a group | Apply granted to a disposable group | `apply` filter + group membership | applies through the token |
| **Explicit deny on Apply** | a Deny ACE on the control right, written onto the DACL | **nothing — it is inexpressible** | **the model says it applies; Windows does not** |

So the lane runs two scenarios, not one. Merging them would put a genuine
capability gap and three working behaviours behind a single verdict.

`user-security-filtering` covers the first three. `user-security-filtering-deny`
adds the fourth and **declares in advance that it will diverge**, with the
reason, in the candidate. That declaration renames the outcome
(`expected-finding`) and softens nothing: the comparison still runs, the
divergence is recorded in full, and `passed` stays false. A declared divergence
that does *not* happen is `unexpected-agreement`, also not a pass — either the
model gained a capability nobody recorded, or the row was never authored.

Deny rows are dropped from the model query rather than translated into "no
allow". Inventing a representation would make the model look correct about a
case it cannot express, which is the opposite of what an oracle is for.

### MS16-072 shapes every row

Since that update a **user's** GPOs are retrieved in the **computer's** security
context. A GPO the computer cannot read does not reach the user however the
user is filtered, so every filtered GPO here keeps Authenticated Users at Read
and moves only Apply. Stripping Authenticated Users entirely would produce
filtering results that are really read failures.

### Token collection is a gate, not a record

The nesting row states the principal's group membership as a model *input*, so
the estate has to corroborate it. The observation half collects the groups
twice: from the principal's own session (`whoami /groups` inside an interactive
scheduled task) and from the directory's constructed `tokenGroups` attribute.
If the group is missing from the **session** token, the run is a lane failure.

That is not hypothetical. **A token is minted at logon and never updated**, and
the lane creates its group per run — after the guest signed in at boot. The
first live run showed exactly the split the gate was written for: the directory
had the membership, the session did not, and the nesting GPO legitimately did
not apply. A lane without the gate would have reported a defect in `rsop.py`.

The lane therefore restarts the client after the topology is authored, lets
autologon sign the principal in again, and verifies the **new** token holds the
group before the experiment runs.

### What the estate taught about the tooling

Two measurements worth keeping, both made while chasing a check that failed on
a correct estate:

- **`Get-GPPermission` cannot express a deny.** Once a Deny ACE exists for a
  trustee, the cmdlet collapses that trustee's entry to `GpoCustom` with
  `Denied=False` and stops reporting `GpoApply` — while the raw DACL underneath
  carries the allow and the deny both, and the CSE honours the deny. The lane's
  verification reads the DACL, which is also what Plan 033's preconditions ask
  for: real CR/RP ACEs rather than a tool's summary of them.
- **`tokenGroups` cannot be retrieved by a search.** It is constructed per
  object; a subtree search fails with "An operations error occurred", which
  says nothing about the real constraint. It needs an ordinary search for the
  DN followed by a base-scope read of the object.
