# WP-3 expansion — what the security-template lane does not yet touch

**Status: design note, written 2026-08-04 from the code rather than the plan.**
No lane row has run. The two measurements at the end WERE taken, on the estate,
and one of them replaced this note's original recommendation -- which is why
they are at the end rather than in a follow-up. It exists so the expansion is sequenced by *risk* instead
of by section order, because the sections differ enormously in how easy it is to
tell a Studio defect from a `secedit` normalisation.

## What is certified, and what is not

`security_template.py` knows eleven sections (`KNOWN_SECTIONS`). The certified
tranche — and every WP-3 run to date, including the estate qualification —
exercises **three** of them:

| section | certified? | shape |
|---|---|---|
| `System Access` | yes | plain `key = value` |
| `Event Audit` | yes | plain `key = value` |
| `Privilege Rights` | yes | `right = principal,principal,…` |
| `Kerberos Policy` | **no** | plain `key = value` |
| `Registry Values` | **no** | `path = type,data` |
| `Group Membership` | **no** | `group__Members = principal,…` |
| `Registry Keys` | **no** | `"path",mode,"SDDL"` |
| `File Security` | **no** | `"path",mode,"SDDL"` |
| `Service General Setting` | **no** | `"service",startup,"SDDL"` |

`Unicode` and `Version` are preamble and are already asserted.

So the lane covers the three simplest shapes and none of the hard ones. That is
worth saying plainly: **WP-3's green record is a statement about key/value
parsing, not about security descriptors.** `security_template.py` is also the
one domain layer already proven wrong on the wire (it emitted something that was
not valid MS-GPSB), and the region that proved it is not the region the lane
tests.

## The hazard that decides the sequencing

The finalizer compares by exact string match, with exactly one exception:

```python
if section.casefold() == "privilege rights":
    # compare principal SETS, not the string
```

That exception is a tell. It exists because `secedit` reorders principals on
export, and comparing the string produced a difference that was not a defect.

**Every unexercised section has its own version of that problem, and three of
them have a much worse one.** `Registry Keys`, `File Security` and
`Service General Setting` carry **SDDL**. Windows canonicalises security
descriptors: ACEs are reordered into canonical order, well-known SIDs may be
written differently, inherited flags are materialised. An exact comparison
against a `secedit` export will therefore diverge for reasons that have nothing
to do with Studio — and the lane would report a **finding about Studio for a
Microsoft normalisation**, which is the precise failure mode this project keeps
designing against (see the WMI control in `wp6b-results.md`, and the loopback
control in `wp9-results.md`).

## Proposed sequencing: two tranches, split by that hazard

### Tranche A — sections the existing comparison can already judge

`Kerberos Policy`, `Registry Values`, `Group Membership`.

- `Kerberos Policy` is `System Access` in a different section; it needs a DC
  template to be meaningful, which the estate has.
- `Registry Values` is `path = type,data`. The trap is the **type prefix**
  (`1` = REG_SZ, `4` = REG_DWORD, `7` = REG_MULTI_SZ …) and the quoting of
  string data. Worth a row per type rather than one row.
- `Group Membership` needs the same set-comparison `Privilege Rights` has, and
  the `__Members` / `__Memberof` suffix convention is a parsing surface in its
  own right.

Tranche A needs **no new comparison machinery** beyond generalising the
principal-set rule from one hard-coded section name to a small table. It can be
certified the same way the current tranche is.

### Tranche B — sections that need a semantic comparator first

`Registry Keys`, `File Security`, `Service General Setting`.

These should **not** be attempted with the current comparison. What they need
first is a decision about what "equal" means for a security descriptor, and a
control that proves the decision is sound:

1. compare parsed descriptors rather than SDDL strings — owner, group, and the
   ACE list as a *set* of (type, flags, rights, trustee) — so canonical
   reordering is not a difference;
2. carry a **control row whose SDDL is already canonical**, so that if even
   *that* row differs on export, the comparison itself is wrong and the run is
   inconclusive rather than a finding. This is the direct analogue of the WMI
   lane's true-filter control and the loopback lane's event 5311;
3. only then add rows that are expected to differ.

Without (2), a canonicalisation bug in the comparator is indistinguishable from
a defect in `security_template.py`, and the run that "found" it would be the
most convincing wrong answer this lane could produce.

## The two measurements — taken 2026-08-04, and one of them changes the plan

Both were cheap, neither needed a lane row, and they were run on the estate's
member server before anything above was built on.

**1. `secedit /validate` DOES reject a malformed SDDL.** A `Registry Keys` entry
carrying `D:PAR(A;CI;KA;;;NOT-A-SID)(this is not sddl` fails with exit 1 and
`"The access control list (ACL) structure is invalid. Error building security
descriptor for object MACHINE\SOFTWARE\StudioProbe."` So `secedit` is a real
oracle for descriptor validity, and Studio emitting an invalid one would be
caught rather than silently accepted. `validate_security_template` still cannot
catch it itself — that remains true and remains worth fixing — but it is not the
only guard.

**2. `secedit /export` reproduced a canonical SDDL byte-for-byte.**
`D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)` came back verbatim. So for an
already-canonical descriptor, a string comparison of the SDDL is sound, and the
semantic comparator proposed above is **prudent rather than required** — it is
still the right thing for descriptors that are not already canonical, but it is
not what blocks the tranche.

### What actually blocks it, and it was not the SDDL

The exported entry reads:

```
1="machine\software\studioprobe", 2, "D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)"
```

against an authored entry keyed by the path. Three transformations at once:

- **the key is now an ordinal index** (`1=`), and the path has moved into the
  value;
- **the path is lower-cased**;
- **commas gain trailing spaces**.

The finalizer looks entries up with `template.get_value(section, key)`. For
these sections there is no such key on the export side, so **every row would be
reported as missing** — a clean sweep of false findings, and none of them about
SDDL at all.

So Tranche B's real prerequisite is an **entry-shape comparator**: match by
parsing the value into (path, mode, descriptor), compare the path
case-insensitively, and compare the descriptor. That is a different and smaller
piece of work than the semantic-SDDL comparator this note originally proposed,
and it is needed for all three Tranche B sections regardless of what the
descriptors contain.

The canonical-SDDL control row is still worth carrying, for the same reason as
before: it is what distinguishes a comparator bug from a module defect.

## What this note deliberately does not do

It does not estimate the work, and it does not promise a finding. The claim is
narrower: the sections the lane has never touched are the ones where the module
has already been wrong once, the comparison it would use is known to produce
false differences on exactly those sections, and the sequencing above is what
keeps a false difference from being reported as a defect.
