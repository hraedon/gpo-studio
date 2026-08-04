# WP-3 expansion — what the security-template lane does not yet touch

**Status: design note, written 2026-08-04 from the code rather than the plan.**
Nothing here has run. It exists so the expansion is sequenced by *risk* instead
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

## Two things to measure before building Tranche B

Both are cheap and neither needs the full lane:

- **Does `secedit /export` reproduce an SDDL byte-for-byte?** Import a template
  with a known-canonical descriptor, export, and diff. The answer decides
  whether (1) above is required or merely prudent.
- **Does `secedit /validate` reject a malformed SDDL, or accept it silently?**
  If it accepts, then `validate_security_template` is the only thing standing
  between Studio and an invalid template, and its coverage of these sections
  (currently: none — it checks `[Version]`, empty names, unknown sections, and
  two account-policy consistency rules) becomes a finding in its own right.

The second question is the more interesting one, and it can be answered without
writing a single lane row.

## What this note deliberately does not do

It does not estimate the work, and it does not promise a finding. The claim is
narrower: the sections the lane has never touched are the ones where the module
has already been wrong once, the comparison it would use is known to produce
false differences on exactly those sections, and the sequencing above is what
keeps a false difference from being reported as a defect.
