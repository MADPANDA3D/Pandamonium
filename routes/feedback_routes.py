"""Guided in-app bug report API (MAD-856).

Every endpoint is authenticated; submission uses a server-held GitHub App
installation token (see ``src/github_issues.py``) and never returns or accepts
one. Screenshots are validated by magic bytes, size, and count; the exact
public issue title/body shown in review is rebuilt server-side at submit time
so the preview and the posted issue cannot diverge.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from src import feedback_diagnostics as diagnostics
from src import feedback_report as report
from src import feedback_store as store
from src import github_issues as github
from src.auth_helpers import require_authenticated_request

logger = logging.getLogger(__name__)


def _owner(request: Request) -> str:
    return require_authenticated_request(request)


def _feedback_enabled() -> bool:
    try:
        from src.settings import get_setting

        return bool(get_setting("feedback_enabled", True))
    except Exception:
        return True


def _github_status() -> dict[str, Any]:
    config = github.resolve_config()
    if config is None:
        return {
            "configured": False,
            "public_submission": False,
            "repo": github.DEFAULT_REPO,
            "reason": github.config_reason(),
            "security_url": github._env("PANDAMONIUM_FEEDBACK_SECURITY_URL")
            or f"https://github.com/{github.DEFAULT_REPO}/security/advisories/new",
        }
    return {
        "configured": True,
        "public_submission": True,
        "reason": "",
        **config.public_status(),
    }


def _bounded_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="The report payload must be a JSON object.")
    return payload


def _client_context(draft: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    context = dict(draft.get("client") or {})
    session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        context["session_id"] = session_id
    return context


def setup_feedback_routes():
    router = APIRouter(prefix="/api/feedback", tags=["feedback"])

    @router.get("/config")
    def feedback_config(request: Request):
        """Surface availability, limits, and honest submission state."""
        require_authenticated_request(request)
        enabled = _feedback_enabled()
        status = _github_status()
        return {
            "enabled": enabled,
            "public_submission": bool(enabled and status["public_submission"]),
            "configured": status["configured"],
            "repo": status["repo"],
            "reason": "" if enabled else "In-app bug reports are disabled on this installation.",
            "github_reason": status["reason"],
            "security_url": status["security_url"],
            "limits": {
                "max_summary_chars": report.MAX_SUMMARY_CHARS,
                "max_steps": report.MAX_STEPS,
                "max_attachments": report.MAX_ATTACHMENTS,
                "max_attachment_bytes": report.MAX_ATTACHMENT_BYTES,
                "max_total_attachment_bytes": report.MAX_TOTAL_ATTACHMENT_BYTES,
                "allowed_types": list(report.REPORT_TYPES.keys()),
            },
        }

    @router.get("/diagnostics")
    def feedback_diagnostics(
        request: Request,
        route: str = "",
        user_agent_class: str = "",
        viewport_class: str = "",
        locale: str = "",
        session_id: str = "",
        request_id: str = "",
    ):
        """Allowlisted, redacted diagnostics; never raw logs or message content."""
        owner = _owner(request)
        bundle = diagnostics.build_diagnostics(
            route=route,
            session_id=session_id,
            request_id=request_id,
            owner=owner or None,
            client={
                "user_agent_class": user_agent_class,
                "viewport_class": viewport_class,
                "locale": locale,
            },
        )
        return {"ok": True, "diagnostics": bundle}

    @router.post("/upload")
    async def feedback_upload(
        request: Request,
        draft_id: str = Form(...),
        label: str = Form(""),
        route: str = Form(""),
        file: UploadFile = File(...),
    ):
        """Validate and store one screenshot for a report draft."""
        owner = _owner(request)
        try:
            normalized_draft = report.validate_draft_id(draft_id)
        except report.FeedbackValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
        existing = store.list_attachments(normalized_draft, owner=owner)
        existing_bytes = sum(int(row.get("bytes") or 0) for row in existing)
        data = await file.read(report.MAX_ATTACHMENT_BYTES + 1)
        try:
            content_type = report.validate_image(
                data, total_bytes=existing_bytes, count=len(existing)
            )
        except report.FeedbackValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
        suffix = report.ALLOWED_IMAGE_TYPES[content_type]
        record = store.save_attachment(
            draft_id=normalized_draft,
            owner=owner,
            data=data,
            content_type=content_type,
            suffix=suffix,
            name=str(getattr(file, "filename", "") or "screenshot")[:160],
            label=str(label or "")[: report.MAX_LABEL_CHARS],
            route=report.safe_route(route),
        )
        return {
            "ok": True,
            "attachment": {
                "id": record["id"],
                "name": record["name"],
                "label": record["label"],
                "route": record["route"],
                "bytes": record["bytes"],
                "sha256": record["sha256"],
                "content_type": record["content_type"],
            },
        }

    @router.get("/attachment/{draft_id}/{attachment_id}")
    def feedback_attachment(request: Request, draft_id: str, attachment_id: str):
        owner = _owner(request)
        record = store.get_attachment(draft_id, attachment_id, owner=owner)
        if record is None:
            raise HTTPException(status_code=404, detail="Screenshot not found.")
        path = store.attachment_path(record)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Screenshot not found.")
        data = path.read_bytes()
        return Response(
            content=data,
            media_type=str(record.get("content_type") or "application/octet-stream"),
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    def _manifest(draft: dict[str, Any], owner: str) -> list[dict[str, Any]]:
        manifest: list[dict[str, Any]] = []
        for attachment_id in draft.get("attachments") or []:
            record = store.get_attachment(draft["draft_id"], attachment_id, owner=owner)
            if record is None:
                raise HTTPException(
                    status_code=400,
                    detail="A screenshot could not be found for this report. Re-add it or remove it, then retry.",
                )
            manifest.append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "label": record["label"],
                    "route": record["route"],
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                    "content_type": record["content_type"],
                }
            )
        return manifest

    def _search_duplicates(draft: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        """Bounded duplicate search; fail-soft so GitHub latency never blocks."""
        if github.resolve_config() is None:
            return [], ""
        try:
            client = github.client_for_submission()
            return client.search_open_issues(report.search_terms_for(draft)), ""
        except github.GitHubIssuesError as exc:
            return [], exc.message
        except Exception:
            return [], "The duplicate search could not be completed."

    @router.post("/prepare")
    async def feedback_prepare(request: Request):
        """Return the exact public title/body, attachments, diagnostics, duplicates."""
        owner = _owner(request)
        try:
            payload = _bounded_body(await request.json())
        except (ValueError, TypeError):
            payload = {}
        try:
            draft = report.validate_draft(payload)
            manifest = _manifest(draft, owner)
        except report.FeedbackValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
        context = _client_context(draft, payload)
        bundle = diagnostics.build_diagnostics(
            route=draft.get("route") or ("/" + context.get("route", "") if context.get("route") else ""),
            session_id=str(context.get("session_id") or ""),
            request_id=str(context.get("request_id") or ""),
            owner=owner or None,
            client=context,
            include_diagnostics=draft.get("include_diagnostics", True),
        )
        config = github.resolve_config()
        url_base = config.attachment_url_base if config else ""
        title = report.build_issue_title(draft)
        body = report.build_issue_body(
            draft, diagnostics=bundle, attachments=manifest, url_base=url_base
        )
        duplicates, duplicate_error = _search_duplicates(draft)
        return {
            "ok": True,
            "title": title,
            "body": body,
            "fingerprint": report.fingerprint_for(draft),
            "diagnostics": bundle,
            "attachments": manifest,
            "duplicates": duplicates,
            "duplicates_error": duplicate_error,
            "security_private_only": draft["type"] == "security",
            "security_url": (config.security_url if config else _github_status()["security_url"]),
            "public_submission": bool(config is not None and _feedback_enabled()),
        }

    @router.post("/submit")
    async def feedback_submit(request: Request):
        """Submit a confirmed report exactly once; security reports never post."""
        owner = _owner(request)
        try:
            payload = _bounded_body(await request.json())
        except (ValueError, TypeError):
            payload = {}
        if not _feedback_enabled():
            raise HTTPException(status_code=503, detail="In-app bug reports are disabled on this installation.")
        try:
            draft = report.validate_draft(payload)
            report.validate_confirmation(payload.get("confirmation"))
            decision = report.validate_duplicate_decision(payload.get("duplicate_decision"))
            idempotency_key = report.validate_idempotency_key(payload.get("idempotency_key"))
            manifest = _manifest(draft, owner)
        except report.FeedbackValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)

        if draft["type"] == "security":
            config = github.resolve_config()
            raise HTTPException(
                status_code=409,
                detail=(
                    "Security reports are not posted publicly. Submit them privately "
                    "through the security advisory page instead: "
                    + (config.security_url if config else _github_status()["security_url"])
                ),
            )

        try:
            client = github.client_for_submission()
        except github.GitHubIssuesError as exc:
            raise HTTPException(status_code=503, detail=exc.message)

        claim = store.claim_submission(
            idempotency_key, draft_id=draft["draft_id"], fingerprint=report.fingerprint_for(draft)
        )
        if claim["state"] == "submitted":
            return {
                "ok": True,
                "already_submitted": True,
                "issue_url": claim.get("issue_url") or "",
                "issue_number": int(claim.get("issue_number") or 0),
                "message": "This report was already submitted; returning the existing issue.",
            }
        if claim["state"] == "in_progress":
            raise HTTPException(
                status_code=409,
                detail="This report is already being submitted. Wait a moment, then check the result.",
            )

        context = _client_context(draft, payload)
        bundle = diagnostics.build_diagnostics(
            route=draft.get("route") or ("/" + context.get("route", "") if context.get("route") else ""),
            session_id=str(context.get("session_id") or ""),
            request_id=str(context.get("request_id") or ""),
            owner=owner or None,
            client=context,
            include_diagnostics=draft.get("include_diagnostics", True),
        )
        title = report.build_issue_title(draft)
        body = report.build_issue_body(
            draft,
            diagnostics=bundle,
            attachments=manifest,
            url_base=client.config.attachment_url_base,
        )
        try:
            if decision == "existing":
                candidates, _ = _search_duplicates(draft)
                try:
                    issue_number = int(payload.get("existing_issue_number") or 0)
                except (TypeError, ValueError):
                    issue_number = 0
                match = next((row for row in candidates if int(row.get("number") or 0) == issue_number), None)
                if match is None:
                    raise report.FeedbackValidationError(
                        "duplicate_not_found",
                        "That existing issue was not found in the duplicate results. Re-check the list, then retry.",
                    )
                result = client.add_issue_comment(
                    issue_number=issue_number,
                    body=body,
                )
            else:
                result = client.create_issue(title=title, body=body)
        except report.FeedbackValidationError as exc:
            store.mark_failed(idempotency_key, error=exc.code)
            raise HTTPException(status_code=400, detail=exc.message)
        except github.GitHubIssuesError as exc:
            store.mark_failed(idempotency_key, error=exc.code)
            logger.warning("feedback submission failed code=%s retryable=%s", exc.code, exc.retryable)
            raise HTTPException(status_code=502 if exc.retryable else 400, detail=exc.message)

        issue_url = str(result.get("issue_url") or result.get("url") or "")
        issue_number = int(result.get("issue_number") or result.get("number") or 0)
        store.mark_submitted(idempotency_key, issue_url=issue_url, issue_number=issue_number)
        return {
            "ok": True,
            "already_submitted": False,
            "issue_url": issue_url,
            "issue_number": issue_number,
            "attached_to_existing": decision == "existing",
            "redacted_report": {"title": title, "body": body},
        }

    return router