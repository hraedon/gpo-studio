# What each file costs to edit

**Generated.** `python scripts/plan-033/report-bound-source-cost.py
--write` rewrites it and `tests/test_bound_source_cost.py` fails when
this file and the evidence disagree. Do not hand-edit the tables.

Every live verdict binds its harness and source by `(commit, path,
sha256)`. Editing a bound file expires every verdict that binds it, and
a verdict is re-earned only by a run on the estate. So the cost of a
change is not its diff --- it is the number below, in lanes that must
be re-run before the evidence is honest again.

That is WI-048's ordering argument, and this table is what makes it
checkable before the edit instead of after. It exists because the
information was already complete and unreadable: spread across
21 packs, so pricing one file meant opening all of them.

**A zero-cost file is not a safe file.** It means no lane measured it,
which is a statement about coverage rather than about quality --- and
for anything in `src/gpo_studio/`, usually the more interesting one.

Live set: 20 lane verdicts plus WP-0. Retired and
pending-requalification verdicts are excluded; they bind the commits
they name and are not re-earned by an edit today.

## Product source

| File | Lanes | Which |
|---|---:|---|
| `src/gpo_studio/oracle_evidence.py` | 21 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, endpoint, loopback-merge, loopback-replace, lsdou-precedence, object-security, publication, scripts-metadata, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled, wmi-filtering, wmi-filtering-error, wp0, wp1b, wp2, wp3-dc, wp3-member |
| `src/gpo_studio/security_template.py` | 3 | object-security, wp3-dc, wp3-member |
| `src/gpo_studio/canonical.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/export.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/gpp.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/model.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/policy_families.py` | 2 | wp3-dc, wp3-member |
| `src/gpo_studio/registry_pol.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/validation.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/xml_safety.py` | 2 | publication, scripts-metadata |
| `src/gpo_studio/object_security.py` | 1 | object-security |
| `src/gpo_studio/publication.py` | 1 | publication |
| `src/gpo_studio/script_policy.py` | 1 | scripts-metadata |
| `src/gpo_studio/sddl.py` | 1 | object-security |

## Harness

| File | Lanes | Which |
|---|---:|---|
| `scripts/windows-oracle/psdirect.ps1` | 21 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, endpoint, loopback-merge, loopback-replace, lsdou-precedence, object-security, publication, scripts-metadata, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled, wmi-filtering, wmi-filtering-error, wp0, wp1b, wp2, wp3-dc, wp3-member |
| `scripts/plan-033/build-rsop-candidate.py` | 12 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, loopback-merge, loopback-replace, lsdou-precedence, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled, wmi-filtering, wmi-filtering-error |
| `scripts/windows-oracle/run-rsop-author.ps1` | 12 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, loopback-merge, loopback-replace, lsdou-precedence, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled, wmi-filtering, wmi-filtering-error |
| `scripts/windows-oracle/finalize_rsop_run.py` | 6 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, lsdou-precedence, wmi-filtering, wmi-filtering-error |
| `scripts/windows-oracle/finalize_rsop_user_run.py` | 6 | loopback-merge, loopback-replace, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled |
| `scripts/windows-oracle/run-rsop-observe.ps1` | 6 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, lsdou-precedence, wmi-filtering, wmi-filtering-error |
| `scripts/windows-oracle/run-rsop-oracle.sh` | 6 | computer-security-filtering, computer-security-filtering-deny-read, disabled-block-enforced, lsdou-precedence, wmi-filtering, wmi-filtering-error |
| `scripts/windows-oracle/run-rsop-user-observe.ps1` | 6 | loopback-merge, loopback-replace, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled |
| `scripts/windows-oracle/run-rsop-user-oracle.sh` | 6 | loopback-merge, loopback-replace, user-security-filtering, user-security-filtering-deny, user-security-filtering-read-deny, user-side-disabled |
| `scripts/plan-033/build-wp3-candidate.py` | 2 | wp3-dc, wp3-member |
| `scripts/windows-oracle/finalize_wp3_run.py` | 2 | wp3-dc, wp3-member |
| `scripts/windows-oracle/run-wp3-oracle.sh` | 2 | wp3-dc, wp3-member |
| `scripts/windows-oracle/run-wp3-security-template.ps1` | 2 | wp3-dc, wp3-member |
| `scripts/plan-033/build-endpoint-candidate.py` | 1 | endpoint |
| `scripts/plan-033/build-object-security-candidate.py` | 1 | object-security |
| `scripts/plan-033/build-publication-candidate.py` | 1 | publication |
| `scripts/plan-033/build-scripts-backup-candidate.py` | 1 | scripts-metadata |
| `scripts/plan-033/build-wp1b-candidates.py` | 1 | wp1b |
| `scripts/plan-033/build-wp2-candidate.py` | 1 | wp2 |
| `scripts/windows-oracle/finalize_endpoint_run.py` | 1 | endpoint |
| `scripts/windows-oracle/finalize_object_security_run.py` | 1 | object-security |
| `scripts/windows-oracle/finalize_oracle_run.py` | 1 | wp0 |
| `scripts/windows-oracle/finalize_publication_run.py` | 1 | publication |
| `scripts/windows-oracle/finalize_scripts_backup_run.py` | 1 | scripts-metadata |
| `scripts/windows-oracle/finalize_wp1b_run.py` | 1 | wp1b |
| `scripts/windows-oracle/finalize_wp2_import_run.py` | 1 | wp2 |
| `scripts/windows-oracle/run-endpoint-author.ps1` | 1 | endpoint |
| `scripts/windows-oracle/run-endpoint-observe.ps1` | 1 | endpoint |
| `scripts/windows-oracle/run-endpoint-oracle.sh` | 1 | endpoint |
| `scripts/windows-oracle/run-object-security-oracle.sh` | 1 | object-security |
| `scripts/windows-oracle/run-object-security-template.ps1` | 1 | object-security |
| `scripts/windows-oracle/run-publication-import.ps1` | 1 | publication |
| `scripts/windows-oracle/run-publication-oracle.sh` | 1 | publication |
| `scripts/windows-oracle/run-scripts-backup-import.ps1` | 1 | scripts-metadata |
| `scripts/windows-oracle/run-scripts-backup-oracle.sh` | 1 | scripts-metadata |
| `scripts/windows-oracle/run-windows-oracle.sh` | 1 | wp0 |
| `scripts/windows-oracle/run-wp1b-oracle.sh` | 1 | wp1b |
| `scripts/windows-oracle/run-wp1b-writer.ps1` | 1 | wp1b |
| `scripts/windows-oracle/run-wp2-import.ps1` | 1 | wp2 |
| `scripts/windows-oracle/run-wp2-oracle.sh` | 1 | wp2 |

## Bound by nothing

Every other module in `src/gpo_studio/`. No lane reads these, so an
edit costs no estate time --- and none of their behaviour has been
measured against Windows by the oracle either.

- `src/gpo_studio/__init__.py`
- `src/gpo_studio/__main__.py`
- `src/gpo_studio/ad_discovery.py`
- `src/gpo_studio/adm.py`
- `src/gpo_studio/admx.py`
- `src/gpo_studio/api.py`
- `src/gpo_studio/artifact_store.py`
- `src/gpo_studio/backup.py`
- `src/gpo_studio/backup_inventory.py`
- `src/gpo_studio/conformance.py`
- `src/gpo_studio/delegation.py`
- `src/gpo_studio/diff.py`
- `src/gpo_studio/estate.py`
- `src/gpo_studio/evidence.py`
- `src/gpo_studio/folder_redirection.py`
- `src/gpo_studio/gpmc_interop.py`
- `src/gpo_studio/gpp_adapters.py`
- `src/gpo_studio/hosting.py`
- `src/gpo_studio/identity.py`
- `src/gpo_studio/ilt.py`
- `src/gpo_studio/import_export.py`
- `src/gpo_studio/lifecycle.py`
- `src/gpo_studio/migration.py`
- `src/gpo_studio/network_security.py`
- `src/gpo_studio/numeric.py`
- `src/gpo_studio/oracle_harness.py`
- `src/gpo_studio/payload.py`
- `src/gpo_studio/policy_config.py`
- `src/gpo_studio/provenance.py`
- `src/gpo_studio/ps_plan_validator.py`
- `src/gpo_studio/publisher.py`
- `src/gpo_studio/remediation_corpus.py`
- `src/gpo_studio/report.py`
- `src/gpo_studio/rsop.py`
- `src/gpo_studio/safe_io.py`
- `src/gpo_studio/schema.py`
- `src/gpo_studio/settings_browser.py`
- `src/gpo_studio/snapshot_documents.py`
- `src/gpo_studio/software_install.py`
- `src/gpo_studio/som.py`
- `src/gpo_studio/store.py`
- `src/gpo_studio/template_store.py`
- `src/gpo_studio/wmi_catalogue.py`
- `src/gpo_studio/wmi_filter.py`
- `src/gpo_studio/workspace_ops.py`
- `src/gpo_studio/writer_conformance.py`
