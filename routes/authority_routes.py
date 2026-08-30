"""Authenticated operator controls for JOS-P5 approval receipts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.auth_helpers import require_user
from src.authority_protocol import APPROVAL_SCOPES, AuthorityStore, authority_store, operator_identity


class AuthorityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: str = Field(pattern=r"^(approve|deny)$")
    scope: str = Field(default="once", pattern=r"^(once|session|time_bounded|persistent)$")
    ttl_seconds: int = Field(default=900, ge=1, le=86_400)


def setup_authority_routes(store: AuthorityStore = authority_store) -> APIRouter:
    router = APIRouter(prefix="/api/authority", tags=["authority"])

    def _operator(owner: str) -> str:
        identity = operator_identity(owner)
        if not identity:
            raise HTTPException(401, "Authenticated operator required")
        return identity

    @router.get("")
    async def list_authority_state(owner: str = Depends(require_user)):
        return store.list_state(operator_id=_operator(owner))

    @router.post("/decisions/{decision_id}")
    async def resolve_authority_decision(
        decision_id: str,
        payload: AuthorityResolution,
        owner: str = Depends(require_user),
    ):
        if payload.scope not in APPROVAL_SCOPES:
            raise HTTPException(400, "Invalid approval scope")
        try:
            return store.resolve(
                decision_id,
                operator_id=_operator(owner),
                choice=payload.choice,
                scope=payload.scope,
                ttl_seconds=payload.ttl_seconds,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/receipts/{receipt_id}")
    async def revoke_authority_receipt(receipt_id: str, owner: str = Depends(require_user)):
        try:
            return store.revoke(receipt_id, operator_id=_operator(owner))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
