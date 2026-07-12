"""FastAPI delivery layer for the local workspace."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .admx import AdmxCatalogue, AdmxError, load_catalogue
from .backup import read_backup
from .canonical import semantic_hash
from .diff import diff_gpos, three_way_diff
from .export import export_bundle, gpmc_backup_bundle, powershell_plan
from .identity import ClaimedIdentity, claimed_identity
from .import_export import collect_cse_metadata, extract_settings, resolve_gpo
from .model import (
    GPO,
    ConflictError,
    NotFoundError,
    StudioError,
    ValidationError,
    ValidationIssue,
)
from .policy_config import PolicyConfiguration, resolve_policy
from .store import WorkspaceStore, gpo_from_dict
from .validation import validate_gpo, validate_setting

STATIC = Path(__file__).with_name("static")


class Audit(BaseModel):
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Interactive edit", min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)


class CreateGPO(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Create draft", min_length=1, max_length=500)


class MetadataMutation(Audit):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    computer_enabled: bool = True
    user_enabled: bool = True
    status: Literal["draft", "ready", "archived"] = "draft"
    domain: str = Field(default="studio.local", max_length=255)


class SettingData(BaseModel):
    side: Literal["computer", "user"]
    hive: Literal["HKLM", "HKCU"]
    key: str = Field(min_length=1, max_length=1000)
    value_name: str = Field(max_length=255)
    registry_type: Literal[
        "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
    ]
    value: str | int | list[str]
    action: Literal["set", "delete"] = "set"
    comment: str = Field(default="", max_length=1000)


class SettingMutation(Audit):
    setting: SettingData


class LinkData(BaseModel):
    target: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    enforced: bool = False
    order: int = Field(default=1, ge=1, le=999)


class LinkMutation(Audit):
    link: LinkData


class SecurityFilterData(BaseModel):
    principal: str = Field(min_length=1, max_length=255)
    permission: Literal["apply", "read"] = "apply"
    inheritable: bool = True


class SecurityFilterMutation(Audit):
    filter: SecurityFilterData


class WmiFilterData(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    query: str = Field(default="", max_length=4000)
    language: str = Field(default="WQL", max_length=50)


class WmiFilterMutation(Audit):
    wmi_filter: WmiFilterData


class DeleteMutation(Audit):
    pass


class RestoreMutation(Audit):
    pass


class ThreeWayDiffRequest(BaseModel):
    baseline: str | dict[str, Any]
    draft: str | dict[str, Any]
    observed: str | dict[str, Any]


class BackupImportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Import GPMC backup", min_length=1, max_length=500)


class ConfigurePolicyRequest(Audit):
    gpo_guid: str = Field(min_length=1, max_length=255)
    side: Literal["computer", "user"]
    values: dict[str, bool | int | str | list[str]]


def _store(request: Request) -> WorkspaceStore:
    return cast(WorkspaceStore, request.app.state.store)


def _catalogue(request: Request) -> AdmxCatalogue:
    return cast(AdmxCatalogue, getattr(request.app.state, "admx_catalogue", AdmxCatalogue()))


def _identity(actor: str) -> ClaimedIdentity:
    return claimed_identity(actor)


def _gpo_payload(gpo: Any) -> dict[str, Any]:
    return {
        "gpo": gpo.to_dict(),
        "validation": [asdict(item) for item in validate_gpo(gpo)],
        "semantic_sha256": semantic_hash(gpo),
    }


def _validate_inbox_path(path: str) -> Path:
    inbox = os.getenv("GPO_STUDIO_INBOX_DIR")
    if not inbox:
        return Path(path)
    try:
        inbox_resolved = Path(inbox).resolve()
        requested_resolved = Path(path).resolve()
    except OSError:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="inbox_path_unresolvable",
                message=f"Cannot resolve backup path or inbox directory: {path}",
                path="path",
            )
        ]) from None
    if not requested_resolved.is_relative_to(inbox_resolved):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="path_outside_inbox",
                message=f"Backup path is outside the configured inbox directory: {path}",
                path="path",
            )
        ])
    return requested_resolved


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not hasattr(app.state, "store"):
        app.state.store = WorkspaceStore(os.getenv("GPO_STUDIO_DB", "gpo-studio.db"))
        app.state.owns_store = True
    if not hasattr(app.state, "admx_catalogue"):
        admx_dir = os.getenv("GPO_STUDIO_ADMX_DIR", "./admx")
        try:
            app.state.admx_catalogue = load_catalogue(Path(admx_dir))
        except AdmxError as error:
            logging.getLogger("gpo_studio.api").warning(
                "Failed to load ADMX catalogue: %s", error
            )
            app.state.admx_catalogue = AdmxCatalogue()
    yield
    if getattr(app.state, "owns_store", False):
        app.state.store.close()


app = FastAPI(
    title="GPO Studio",
    version=__version__,
    description="Offline-first Group Policy authoring workspace",
    lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.exception_handler(StudioError)
async def studio_error(_request: Request, error: StudioError) -> JSONResponse:
    status = (
        404
        if isinstance(error, NotFoundError)
        else 409
        if isinstance(error, ConflictError)
        else 422
    )
    detail: dict[str, Any] = {"message": str(error)}
    if isinstance(error, ValidationError):
        detail["issues"] = [asdict(item) for item in error.issues]
    return JSONResponse({"error": detail}, status_code=status)


@app.exception_handler(RequestValidationError)
async def request_validation(_request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": "Invalid request", "issues": error.errors()}}, status_code=422
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    catalogue = _catalogue(request)
    return {
        "status": "ok",
        "version": __version__,
        "mode": "offline-workspace",
        "admx_loaded": len(catalogue.policies) > 0,
    }


@app.get("/api/admx/search")
def admx_search(request: Request, q: str = "") -> dict[str, Any]:
    catalogue = _catalogue(request)
    query = q.lower()
    if query:
        policies = [
            p
            for p in catalogue.policies
            if query in p.id.lower()
            or query in p.display_name.lower()
            or query in p.explain_text.lower()
            or query in p.parent_category.lower()
            or query in p.key.lower()
        ]
    else:
        policies = list(catalogue.policies)
    policies = policies[:50]
    items = [
        {
            "id": p.id,
            "class_": p.class_,
            "key": p.key,
            "display_name": p.display_name,
            "explain_text": p.explain_text,
            "supported_on": p.supported_on,
            "parent_category": p.parent_category,
        }
        for p in policies
    ]
    return {"items": items, "count": len(items)}


@app.get("/api/admx/policies/{policy_id}")
def admx_policy_detail(request: Request, policy_id: str) -> dict[str, Any]:
    catalogue = _catalogue(request)
    for policy in catalogue.policies:
        if policy.id == policy_id:
            return asdict(policy)
    raise NotFoundError(f"Policy '{policy_id}' not found")


@app.post("/api/admx/policies/{policy_id}/configure")
def configure_policy(
    request: Request, policy_id: str, body: ConfigurePolicyRequest
) -> dict[str, Any]:
    catalogue = _catalogue(request)
    policy = None
    for p in catalogue.policies:
        if p.id == policy_id:
            policy = p
            break
    if policy is None:
        raise NotFoundError(f"Policy '{policy_id}' not found")
    config = PolicyConfiguration(side=body.side, values=body.values)
    settings = resolve_policy(policy, config)
    issues: list[ValidationIssue] = []
    for s in settings:
        issues.extend(validate_setting(s))
    if any(i.severity == "error" for i in issues):
        raise ValidationError(issues)
    gpo = _store(request).put_settings(
        body.gpo_guid,
        body.expected_revision,
        settings,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/admx/categories")
def admx_categories(request: Request) -> dict[str, Any]:
    catalogue = _catalogue(request)
    items = [
        {"id": c.id, "parent_id": c.parent_id, "display_name": c.display_name}
        for c in catalogue.categories
    ]
    return {"items": items, "count": len(items)}


@app.get("/api/gpos")
def list_gpos(request: Request) -> dict[str, Any]:
    gpos = _store(request).list_gpos()
    return {"items": [gpo.to_dict() for gpo in gpos], "count": len(gpos)}


@app.post("/api/gpos", status_code=201)
def create_gpo(request: Request, body: CreateGPO) -> dict[str, Any]:
    gpo = _store(request).create_gpo(
        body.name, body.description, identity=_identity(body.actor), reason=body.reason
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}")
def get_gpo(request: Request, guid: str) -> dict[str, Any]:
    return _gpo_payload(_store(request).get_gpo(guid))


@app.patch("/api/gpos/{guid}")
def update_gpo(request: Request, guid: str, body: MetadataMutation) -> dict[str, Any]:
    values = body.model_dump(exclude={"actor", "reason", "expected_revision"})
    gpo = _store(request).update_metadata(
        guid,
        body.expected_revision,
        values,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


def _validated_setting(body: SettingMutation) -> dict[str, Any]:
    values = body.setting.model_dump()
    from .store import _setting

    issues = validate_setting(_setting({"id": "pending", **values}))
    if any(item.severity == "error" for item in issues):
        raise ValidationError(issues)
    return values


@app.post("/api/gpos/{guid}/settings", status_code=201)
def add_setting(request: Request, guid: str, body: SettingMutation) -> dict[str, Any]:
    gpo = _store(request).put_setting(
        guid,
        body.expected_revision,
        _validated_setting(body),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/settings/{setting_id}")
def edit_setting(
    request: Request, guid: str, setting_id: str, body: SettingMutation
) -> dict[str, Any]:
    gpo = _store(request).put_setting(
        guid,
        body.expected_revision,
        _validated_setting(body),
        setting_id=setting_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/settings/{setting_id}")
def delete_setting(
    request: Request, guid: str, setting_id: str, body: DeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_setting(
        guid,
        setting_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post("/api/gpos/{guid}/links", status_code=201)
def add_link(request: Request, guid: str, body: LinkMutation) -> dict[str, Any]:
    gpo = _store(request).put_link(
        guid,
        body.expected_revision,
        body.link.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/links/{link_id}")
def edit_link(request: Request, guid: str, link_id: str, body: LinkMutation) -> dict[str, Any]:
    gpo = _store(request).put_link(
        guid,
        body.expected_revision,
        body.link.model_dump(),
        link_id=link_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/links/{link_id}")
def delete_link(request: Request, guid: str, link_id: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).delete_link(
        guid,
        link_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post("/api/gpos/{guid}/security-filters", status_code=201)
def add_security_filter(
    request: Request, guid: str, body: SecurityFilterMutation
) -> dict[str, Any]:
    gpo = _store(request).put_security_filter(
        guid,
        body.expected_revision,
        body.filter.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/security-filters/{filter_id}")
def edit_security_filter(
    request: Request, guid: str, filter_id: str, body: SecurityFilterMutation
) -> dict[str, Any]:
    gpo = _store(request).put_security_filter(
        guid,
        body.expected_revision,
        body.filter.model_dump(),
        filter_id=filter_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/security-filters/{filter_id}")
def delete_security_filter(
    request: Request, guid: str, filter_id: str, body: DeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_security_filter(
        guid,
        filter_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/wmi-filter")
def set_wmi_filter(request: Request, guid: str, body: WmiFilterMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        body.wmi_filter.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/wmi-filter")
def clear_wmi_filter(request: Request, guid: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        None,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}/revisions")
def revisions(request: Request, guid: str) -> dict[str, Any]:
    items = _store(request).revisions(guid)
    return {
        "items": [
            {
                "revision": item.revision,
                "actor": item.actor,
                "reason": item.reason,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@app.get("/api/gpos/{guid}/revisions/{revision}")
def revision(request: Request, guid: str, revision: int) -> dict[str, Any]:
    item = _store(request).get_revision(guid, revision)
    return asdict(item)


@app.post("/api/gpos/{guid}/revisions/{revision}/restore")
def restore(request: Request, guid: str, revision: int, body: RestoreMutation) -> dict[str, Any]:
    gpo = _store(request).restore_revision(
        guid,
        revision,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}/export.zip")
def bundle(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    errors = [item for item in validate_gpo(gpo) if item.severity == "error"]
    if errors:
        raise ValidationError(errors)
    headers = {"Content-Disposition": f'attachment; filename="{gpo.guid}-publication.zip"'}
    return Response(export_bundle(gpo), media_type="application/zip", headers=headers)


@app.get("/api/gpos/{guid}/plan.ps1")
def plan(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    return Response(powershell_plan(gpo), media_type="text/plain; charset=utf-8")


def _resolve_gpo(request: Request, ref: str | dict[str, Any]) -> GPO:
    return resolve_gpo(_store(request), ref)


@app.get("/api/gpos/{guid}/diff")
def diff_gpo(request: Request, guid: str, against_revision: int) -> dict[str, Any]:
    current = _store(request).get_gpo(guid)
    old_revision = _store(request).get_revision(guid, against_revision)
    old_gpo = gpo_from_dict(old_revision.snapshot)
    result = diff_gpos(old_gpo, current)
    return asdict(result)


@app.post("/api/diff")
def ad_hoc_diff(request: Request, body: ThreeWayDiffRequest) -> dict[str, Any]:
    baseline = _resolve_gpo(request, body.baseline)
    draft = _resolve_gpo(request, body.draft)
    observed = _resolve_gpo(request, body.observed)
    result = three_way_diff(baseline, draft, observed)
    return asdict(result)


@app.post("/api/backups/import", status_code=201)
def import_backup(request: Request, body: BackupImportRequest) -> dict[str, Any]:
    backup_dir = _validate_inbox_path(body.path)
    if not backup_dir.is_dir():
        raise StudioError(f"Backup path is not a directory: {body.path}")
    for xml_file in ("manifest.xml", "bkupInfo.xml"):
        xml_path = backup_dir / xml_file
        if xml_path.is_symlink():
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="symlink_in_backup",
                    message=f"Symlinks are not allowed in backup content: {xml_file}",
                    path="path",
                )
            ])
    backup = read_backup(backup_dir)
    if not backup.gpos:
        raise StudioError("No GPOs found in backup")
    if len(backup.gpos) > 1:
        raise StudioError(
            f"Multi-GPO backups are not supported (found {len(backup.gpos)} GPOs)"
        )
    backup_gpo = backup.gpos[0]
    gpo_dir = backup_dir / backup_gpo.guid

    machine_settings = extract_settings(gpo_dir / "Machine" / "Registry.pol", "computer")
    user_settings = extract_settings(gpo_dir / "User" / "Registry.pol", "user")
    all_settings = tuple(machine_settings + user_settings)
    cse_metadata = collect_cse_metadata(backup_gpo)

    temp_gpo = GPO(
        guid="import-preview",
        name=backup_gpo.display_name or "Imported GPO",
        settings=all_settings,
    )
    gpo_issues = [i for i in validate_gpo(temp_gpo) if i.severity == "error"]
    if gpo_issues:
        raise ValidationError(gpo_issues)

    gpo = _store(request).create_gpo(
        name=backup_gpo.display_name or "Imported GPO",
        description=f"Imported from GPMC backup {backup.backup_id}",
        identity=_identity(body.actor),
        reason=body.reason,
        settings=all_settings,
        source_guid=backup_gpo.guid,
        cse_metadata=cse_metadata,
        domain=backup_gpo.domain or "studio.local",
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}/gpmc-backup")
def gpmc_backup(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    if gpo.cse_metadata:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="unknown_cse_content",
                message="GPO has unknown CSE content and cannot be exported as a GPMC backup.",
                path="cse_metadata",
            )
        ])
    headers = {"Content-Disposition": f'attachment; filename="{gpo.guid}-gpmc-backup.zip"'}
    return Response(gpmc_backup_bundle(gpo), media_type="application/zip", headers=headers)
