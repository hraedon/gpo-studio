# Plan 032 shape assessment — HARDEN

**Verdict: HARDEN.** All three modules are a sound foundation whose defects are
additive, not structural — a competent implementer starting here gets to
fit-for-purpose materially faster than one starting from a blank file, because
the expensive judgement (the role/operation matrix, the gate decomposition, the
identity seam these plug into) is already made and pinned by 153 tests.

**One scoped exception, and it is the only thing I would delete today:**
`HostedConfig`'s inert policy flags and `AuthenticatedIdentity`'s dead fields —
`csrf_enabled`, `hsts_enabled`, `rate_limit_per_minute` (hosting.py:73–75),
`groups` and `is_admin` (hosting.py:260, 263). These name controls that no code
anywhere implements or reads. They are precisely the "counting a draft as
progress" failure `domain-layer-status.md` exists to reject, and unlike the rest
of the module they carry no design value — a future implementer re-derives them
in thirty seconds. Everything else stays.

---

## The four load-bearing reasons

### 1. Authorization is a seam that exists and is simply never called

This is the distinction the brief asks for, and it lands on the "hardening"
side. `check_authorization(identity, operation, grants, scope)`
(hosting.py:484–519) is the right signature with the right semantics: it
iterates all matching grants and returns allow only on a positive match,
denying by default at the bottom (513–519). It is *not* first-grant-wins — a
grant whose role lacks the operation falls through to the next grant, which is
the correct union-over-grants behaviour and is a thing implementers get wrong.
`ROLE_PERMISSIONS` (hosting.py:396–424) encodes seven roles against fifteen
operations with genuine least-privilege subsets, and 90 tests pin it at 99%
branch coverage.

Nothing calls it. But "nothing calls it" is a wiring fact, not a shape fact.
The design has an obvious place to put authorization; there is no unpicking to
do. Compare the alternative failure mode — a design where authorization would
have to be threaded through twenty call sites that currently have no principal
in scope. That is not what this is.

### 2. The identity gate has a place to be enforced, and it is not in hosting.py

Plan 032's central acceptance gate — "client-controlled identity/forwarding
headers never affect actor identity" — is currently *unstated in code*, which
the survey correctly found. But it is enforceable at a single point that
already exists:

- `identity.py` defines an `Identity` Protocol with `actor`, `is_trusted`, and
  `source`, and `ClaimedIdentity` explicitly returns `is_trusted = False` with
  `source = "request-body"`. The module docstring says in as many words that it
  "establishes the interface that trusted authentication middleware will
  satisfy in multi-user deployments".
- `api.py:1217` — `def _identity(actor: str) -> ClaimedIdentity` is the **sole**
  construction point, called at 38 sites, always as `_identity(body.actor)`.
- `store.py:61` — `_resolve_actor(identity)` is the **sole** consumption point;
  every revision-writing path (store.py:581, 709, 750, 876) goes through it.

So the property "in hosted mode, `body.actor` is ignored" is a change to one
function plus 38 mechanical call-site edits, or zero call-site edits if
`_identity` becomes a FastAPI dependency. That is hardening by any reading.

The caveat, and it is the one real coherence defect in hosting.py:
`AuthenticatedIdentity` **does not satisfy that protocol**. Verified:

```
isinstance(AuthenticatedIdentity(subject=..., provider="windows", session_id=...), Identity) = False
has .actor: False | has .is_trusted: False
```

It has `subject`/`display_name` where the seam wants `actor`/`is_trusted`/
`source`. Two identity types in one codebase that do not connect. The fix is
three properties or a fifteen-line adapter. Note the asymmetry this creates for
the discard case: the seam that actually matters is Plan **002**'s, and it
survives whatever you decide about Plan 032. hosting.py's contribution to the
identity gate is close to nil either way — which is an argument against
overvaluing it, not an argument for deleting the parts that do carry value.

Confirmed absent, as the survey said: **no code anywhere in `src/` reads a
forwarded header.** `rg -ni "x-forwarded|forwarded|REMOTE_USER|LOGON_USER|
proxy_headers|root_path"` over `src/` returns exactly one hit — a prose word in
hosting.py's docstring (line 11). There is nothing to unpick because there is
nothing there.

### 3. The gate pipeline's defects are "gates read declared fields instead of derived facts" — a bug class, not a shape

`run_publisher_gates` → seven pure gate functions → `PublisherDecision` with
`blocking_gates` (publisher.py:395–420, 671–690) is the right decomposition and
is what you would build anyway. What is wrong is that every gate trusts the
plan object's self-declared attributes: `_blast_radius_gate` and `_rsop_gate`
read `plan.risk_level` (publisher.py:564, 642), a plain dataclass field set once
at generation time; `_approval_gate` reads `approval.state` and
`approval.current_approvals`. A `PublicationPlan` constructed or `replace()`d
with `risk_level="low"` passes.

Fixing this does not restructure anything. It needs a content digest on the
plan and one extra parameter on the gate functions. `canonical.py` already
provides `canonical_json_bytes`, so the digest is nearly free. See the
shortest-path list.

### 4. Plan 032 is ~5% implemented — but volume is not the criterion the operator set

Honest quantification, against the plan's own structure:

| Work package | State |
|---|---|
| WP-1 deployment profile / hosting architecture | config **dataclass only**; no startup wiring, no ADR, no prototypes, no support matrix, no contract tests |
| WP-2 authentication, sessions, proxy trust | `SessionConfig` **policy dataclass only**; no session implementation, cookies, CSRF, OIDC, or header handling |
| WP-3 authorization | matrix + check function + self-approval predicate — the most complete part, ~40% of the *design*, 0% of the wiring, 0% of the generated matrix tests |
| WP-4 PostgreSQL persistence | **absent** (a `database_url` string that rejects `sqlite`) |
| WP-5 web edge hardening | **absent** (three inert booleans) |
| WP-6 installer | **absent** |
| WP-7 service operations | `AuditEvent` + in-memory `filter_audit_events`; no sink, no Event Log, no correlation |
| WP-8 staged rollout | **absent** |

- **Required outputs: 8 listed, 0 exist.** No ADR (there is no `docs/adr*` at
  all), no hosted-mode architecture document (`docs/architecture.md` contains
  zero occurrences of "hosted"/"IIS"/"Postgres"), no hosted threat model
  (`publisher-threat-model.md` is Plan 030's publisher, a different boundary).
- **Acceptance gates: 9 listed, 0 enforced.** Gate 1's *predicate* exists
  (`HostedConfig.validate`, hosting.py:77–188) but nothing invokes it, so
  nothing fails closed. Gate 2 is unstated. Gate 3's matrix exists but no API
  operation is bound to an `Operation` literal. Gates 4–9 have no code.

Against a production-readiness standard this is a rout. Against the standard
the operator actually set — *faster than a blank file?* — the 5% that exists is
the 5% that is design judgement rather than plumbing, and the missing 95% is
plumbing that the existing shape accommodates without modification. That is the
whole argument.

The ordering violation does bite in one specific place, and it is worth naming
precisely rather than generally: **WP-1's ADR was supposed to choose between
IIS+HttpPlatformHandler, a Windows Service behind IIS/ARR, and a non-Windows
host behind an OIDC proxy — and `HostedConfig` has silently made that choice.**
It puts `tls_certificate_path` and `tls_key_path` in the *application's* config
while also mandating a loopback bind (hosting.py:64, 80–115) — i.e. the app
holds a certificate it will never present, because the edge terminates TLS.
That field list is the part of hosting.py most likely to be invalidated by the
review that was skipped. `ROLE_PERMISSIONS` and `check_authorization` are the
part no topology decision can touch. That is the clean seam inside the module:
**the authorization half is topology-independent and should be kept
unconditionally; the config half should be frozen until the ADR exists.**

---

## The two threat-model rows: both VERIFIED

Both were recorded as hypotheses. Both reproduce. Script:
`scratchpad/probe.py`; output inline below.

### Row: "Artifact swapped after approval" → required control "Canonical digest in every approval signature; publisher recomputes it" — **CONTROL ABSENT**

- `publication.py:141–142` — `_new_plan_id()` returns
  `f"plan-{uuid.uuid4().hex[:12]}"`. A random identifier with no relationship
  to plan content.
- `publisher.py:471` — `_approval_gate`'s only content binding is
  `if approval.plan_id != plan.plan_id`.
- There is **no digest of plan content anywhere.** `publication.py` does
  content-hash individual step artifacts (`_artifact_id_for`, line 153), but
  those digests are never checked against an approval, and the most dangerous
  plan content — the gPLink target DN, carried as free text in
  `step.detail` — has no artifact and therefore no hash at all.

Reproduction: approve a benign plan (one registry setting, linked to
`OU=Servers`), then `dataclasses.replace` the steps to relink to
`OU=Domain Controllers` and swap the Registry.pol artifact id, keeping
`plan_id`:

```
ROW A: plan_id unchanged? True
  approval_gate on MUTATED plan passed: True | Sufficient approvals collected
  full decision approved: True | blocking: []
```

The entire seven-gate pipeline approves a plan that now targets the Domain
Controllers OU under an approval collected for a plan that did not. Note that
`_blast_radius_gate` did not catch it either, because `plan.risk_level` is a
stored field computed from the *original* GPO (publication.py:398) and is not
re-derived — reason 3 above, demonstrated.

This also breaks Plan 032's own WP-3 requirement, "Any change to an approved
payload invalidates the approval and requires review again."

### Row: "Stolen author session self-approves" → required control "`author != approver`" — **CONTROL EXISTS BUT THE GATES BYPASS IT**

- `publisher.py:288–296` — `approve_request` **does** raise on
  `approver == request.requested_by`. The check is real and tested
  (`test_approve_request_self_approval_raises`, test_publisher.py:371).
- `publisher.py:450–515` — `_approval_gate` checks `requires_approval`, `None`,
  `plan_id` equality, `rejected`, `expired`, and the approval count. It never
  compares `approval.approved_by` or `approval.approvers` against
  `approval.requested_by`.
- `publisher.py:179–236` — `ApprovalRequest.validate()` does not check it
  either.
- `run_publisher_gates` and `evaluate_publication` take **no principal
  parameter at all**, and `PublisherDecision.decided_by` is hardcoded to `""`
  (publisher.py:689). `hosting.can_self_approve` exists (hosting.py:522–528) and
  is never called from publisher.py — the two modules do not import each other.

Reproduction: construct an `ApprovalRequest` directly with
`requested_by="alice"`, `approved_by="alice"`, `approvers=("alice",)`,
`state="approved"` — the shape any persistence layer would rehydrate:

```
ROW B: ApprovalRequest.validate() issues: []
  approval_gate on SELF-APPROVED request passed: True | Sufficient approvals collected
  full decision approved: True
```

The self-approval prohibition is enforced only on the *transition*, never on
the *state*. Any path that reconstitutes an approval from storage — which is
every real deployment — skips it.

Coverage corroborates that these are untested paths rather than defended ones.
Running the three test files, `publisher.py` misses lines **472, 483, 491, 499**
— which are, in order, the returns for plan_id mismatch, rejected, expired, and
insufficient approvals. **All four of `_approval_gate`'s content-binding refusal
branches have never been exercised by a test.** Only the `approval is None`
path is covered.

### Third finding, not in the survey: actor/profile identity conflation

`PublisherProfileSet.profiles_for_actor(actor)` (publisher.py:128–132) selects
profiles by `p.profile_id == actor`. `PublisherProfile` has no principal field —
there is no way to express "alice holds profile p1". Verified:

```
BONUS: effective_capabilities('alice') = []
        effective_capabilities('p1')    = 7 caps
```

An actor gets capabilities if and only if their name happens to equal a profile
id. This is a missing field, not a broken design, but it is the reason the
capability gate cannot currently be evaluated against a real principal.

---

## If HARDEN: the shortest path to fit-for-purpose

Ordered. Items 1–4 are what convert "unstated" into "stated and tested" and are
roughly two days; items 5–8 close the threat-model rows and are roughly one.

0. **Write the WP-1 ADR before touching `HostedConfig`.** It is a day's writing
   and it is the ordering violation being remediated. Until it exists, freeze
   the field list — specifically `tls_certificate_path`/`tls_key_path`/
   `trusted_proxy_addresses`, which encode a topology choice the review was
   supposed to make. The authorization half is unaffected; work on it in
   parallel.

1. **Make `AuthenticatedIdentity` satisfy the existing `Identity` protocol.**
   Add `actor`, `is_trusted` (→ `True`), and `source` (→ the provider), or
   introduce a `TrustedIdentity` adapter. ~15 lines. This is what makes Plan
   002's seam and Plan 032's model one system rather than two.

2. **State the identity gate in code and give it its negative test.** Replace
   the 38 `_identity(body.actor)` call sites in `api.py` with a single
   dependency `current_identity(request)`: in the `local` profile it returns
   `claimed_identity(body.actor)` exactly as today; in `hosted` it derives from
   the session and *ignores the body entirely*. Then the acceptance test that
   does not currently exist: one hosted request carrying
   `{"actor": "administrator"}` **and** a spoofed `X-Forwarded-User` header,
   asserting the recorded revision actor is the session subject. That single
   test is Plan 032 acceptance gate 2. Nothing else in the plan is worth doing
   before it.

3. **Call `DeploymentConfig.validate()` at startup and fail closed.** It is
   written and 99%-covered and nothing invokes it. ~10 lines in `api.py` /
   `__main__.py`, plus a test that `hosted` + a `sqlite://` URL refuses to boot.
   Turns gate 1 from a predicate into a gate.

4. **Bind grants to groups, or delete the group fields.**
   `check_authorization` must consult `identity.groups` and `identity.is_admin`
   (hosting.py:260, 263), which are currently dead. Windows and OIDC both
   express roles as group membership, and `HostedConfig.admin_group` has no
   path to `platform_admin` without this. A subject-only grant table cannot
   represent the deployment the plan describes.

5. **Add `payload_digest` to `PublicationPlan`.** Compute it in
   `generate_publication_plan` from `canonical.canonical_json_bytes` over
   `(gpo_guid, target, steps, rollback_plan, risk_level)` — deliberately
   including `risk_level`, deliberately excluding `plan_id`. `canonical.py`
   already exists; this is ~15 lines.

6. **Bind the approval to the digest.** `ApprovalRequest` gains `plan_digest`;
   `create_approval_request` records it; `_approval_gate` refuses on
   `approval.plan_digest != plan.payload_digest`. Closes the "artifact swapped
   after approval" row *and* Plan 032 WP-3's re-approval requirement, and —
   because `risk_level` is inside the digest — closes the stale-risk hole in
   `_blast_radius_gate`/`_rsop_gate` without needing to pass the `GPO` into the
   gates.

7. **Thread a principal through the gates.** `run_publisher_gates(plan,
   profile, approval, *, principal: Identity)`, and add an eighth
   `separation_of_duties_gate` that refuses when
   `principal.actor == approval.requested_by` or
   `principal.actor in approval.approvers` — i.e. move the predicate that today
   lives only in `approve_request` into the gate list, where a rehydrated
   approval cannot slip past it. Populate `PublisherDecision.decided_by`
   (currently hardcoded `""`, publisher.py:689). Reuse
   `hosting.can_self_approve` so there is one implementation.

8. **Fix the profile/actor conflation.** Give `PublisherProfile` a
   `principals: tuple[str, ...]` field and match on it in `profiles_for_actor`
   instead of `profile_id == actor`.

Then the cleanups, cheap and worth doing in the same pass:

9. Test `_approval_gate`'s four uncovered refusal branches (publisher.py:472,
   483, 491, 499).
10. Reconcile the two scope semantics — `hosting._grant_matches_scope`
    (hosting.py:472–481) is exact string equality, `publisher._dn_within_any_scope`
    (publisher.py:762–771) is DN-nesting. Two modules of one subsystem should not
    disagree about what a scope is; the DN-nesting one is right.
11. Add an exhaustiveness guard on `ROLE_PERMISSIONS`. A new `Role` with no
    matrix entry `KeyError`s at runtime (hosting.py:505) rather than failing
    `mypy --strict`, which is the failure mode AGENTS.md's `assert_never` rule
    exists to prevent.
12. Delete the inert flags — `csrf_enabled`, `hsts_enabled`,
    `rate_limit_per_minute` — unless steps 2–4 wire them.

**What must *not* be claimed on completion.** All of the above is
self-consistency work. Per `domain-layer-status.md` §4 and AGENTS.md, none of it
makes any of these layers proven; identity propagation and spoof resistance are
WP-8 Stage A items requiring the Windows lab, and the plan says so.

---

## What carrying it costs, weighed

**Cost, measured.** 2,264 source lines (5.8% of `src/`'s 38,797) plus 1,853
test lines; 153 tests running in 0.86s; inside `mypy --strict` and `ruff`.
Direct CI cost is negligible.

**The real cost is the metric inversion, and it is worse than "misleading".**
`scripts/check_coverage.py` enforces `_TOTAL_FLOOR = 84.0`. `hosting.py` sits at
**99% branch coverage** — because a module of pure validation dataclasses with
no I/O is trivially coverable. `publisher.py` is 87%, `publication.py` 74%.
So the code furthest from anything an operator can reach is the code that most
flatters the headline number, and **deleting it would lower the reported
coverage of the product.** That is `domain-layer-status.md`'s argument made
concrete, and it is worth fixing independently of this decision: the coverage
floors should exclude unsurfaced layers, so that surfacing a layer is what earns
it a floor. (Note the 99% figure is itself hollow — the four refusal branches
that carry the security property are among the uncovered lines in the module
that is only at 87%.)

**Against that, the salvage value.** `domain-layer-status.md` §3 says "the
salvage value is structure, not behaviour," and that ruling cuts *in favour* of
keeping these three specifically. Its worked examples of evidence-driven rewrite
— `security_template.py`'s MS-GPSB encoding, the GPP writers' +547 lines — are
all **wire-format** failures: byte layout, attribute names, units, omission
rules. None of these three modules has a wire format. `hosting.py` emits no
bytes at all; `publisher.py` emits a decision record; only
`publication.py`'s PowerShell generator faces Windows, and it is already
fail-closed behind an empty `_WINDOWS_VERIFIED_OPERATIONS` allowlist
(publication.py:138) that refuses before contacting AD or SYSVOL. **These are
the layers least exposed to the failure mode the ruling was written about**, and
therefore the ones whose structure is most likely to survive their evidence
lane intact.

**Net.** Carrying cost is one bad line in a coverage config plus about 4k lines
of fast, typed, well-tested code. Salvage value is a seven-by-fifteen role
matrix, a correct deny-by-default check, a seven-gate pipeline with the right
decomposition, and 153 tests that document what all of it promises. Discarding
buys a cleaner coverage number and costs a week of re-deriving decisions that
are already made and reviewable. Keep it, do steps 0–8, and fix the coverage
floors so it stops flattering the product.

---

## What I did not need, and one thing I would want

I did not need anything I could not read; the two hypotheses were settled by
execution, not inference.

The one thing that would change the shape of item 0 — and only item 0 — is the
answer the skipped ADR was supposed to produce: **which of Plan 032 WP-1's three
topologies is the target?** If it is IIS+HttpPlatformHandler or a Windows
Service behind IIS/ARR, `HostedConfig`'s current field list is roughly right and
needs only the TLS-ownership question resolved. If it is a non-Windows host
behind an OIDC reverse proxy, `tls_certificate_path`, `tls_key_path`, and
`trusted_proxy_addresses` all belong to the edge and should leave the
application's config entirely — at which point `HostedConfig` is mostly
rewritten. That is a ~60-line question affecting one dataclass. It does not
touch the authorization model, the identity seam, or either publication module,
and so it does not move the verdict.
