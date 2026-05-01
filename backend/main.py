"""
FastAPI application entry point.

Exposes the chat, history, admin, and health endpoints.
Loads environment variables, configures CORS, and adds a metrics middleware
before registering all routes.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()  # must run before any module that reads env vars

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import run_agent
from auth import get_current_user, require_role
from firestore_service import (
    clear_session,
    get_all_sessions,
    get_session_history,
    save_message,
)
from metrics import RequestRecord, collector, request_ctx

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chatbot Infraestructura API",
    description="Agente conversacional para infraestructura cloud.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Metrics middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Capture per-request metrics with < 2 ms overhead.

    Hot-path work (before and after call_next):
      - Two perf_counter() calls
      - One integer parse from headers
      - One ContextVar.set() + ContextVar.reset()
      - One collector.record() call (~0.05-0.15 ms)

    Sub-request data (inference_ms, token counts, uid) flows back through
    ``request_ctx``: the route layer writes into the shared dict; this
    middleware reads it after await call_next returns.
    """
    # Initialise per-request context dict before calling into the route.
    ctx: dict[str, Any] = {}
    token = request_ctx.set(ctx)

    t0 = time.perf_counter()
    req_bytes = int(request.headers.get("content-length", 0))

    response = await call_next(request)

    latency_ms = (time.perf_counter() - t0) * 1_000.0
    resp_bytes = int(response.headers.get("content-length", 0))

    record = RequestRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        endpoint=request.url.path,
        method=request.method,
        status_code=response.status_code,
        latency_e2e_ms=round(latency_ms, 3),
        inference_ms=round(ctx.get("inference_ms", 0.0), 3),
        tokens_in=ctx.get("tokens_in", 0),
        tokens_out=ctx.get("tokens_out", 0),
        used_search=ctx.get("used_search", False),
        req_bytes=req_bytes,
        resp_bytes=resp_bytes,
        uid=ctx.get("uid", "anonymous"),
        ram_used_mb=0.0,   # stamped by collector.record()
        cpu_avg_pct=0.0,   # stamped by collector.record()
    )
    collector.record(record)

    request_ctx.reset(token)
    return response

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session identifier. Defaults to the user's uid.",
    )


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    tokens_used: int
    used_search: bool


class HistoryResponse(BaseModel):
    messages: list[dict[str, Any]]
    total_messages: int


class DeleteResponse(BaseModel):
    message: str

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health_check():
    """Public liveness + metrics endpoint — no authentication required.

    Returns basic liveness fields plus a real-time metrics snapshot that
    covers latency percentiles, throughput, and system resource usage.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": collector.snapshot(),
    }


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["chat"],
    status_code=status.HTTP_200_OK,
)
def chat(
    request: Request,
    body: ChatRequest,
    user: dict[str, Any] = Depends(require_role(["assistant_user"])),
):
    """Send a message to the agent and receive a reply.

    Authenticated users with the ``assistant_user`` role only.

    Flow:
        1. Resolve the session ID (defaults to the caller's uid).
        2. Fetch conversation history from Firestore.
        3. Run the LangChain agent (inference timer + token counter active).
        4. Persist both the user message and the agent reply.
        5. Return the structured response.

    Args:
        request: FastAPI Request — used to stamp uid into the metrics context.
        body:    Request body containing ``message`` and optional ``session_id``.
        user:    Injected by :func:`require_role`.

    Returns:
        :class:`ChatResponse` with the agent reply and metadata.

    Raises:
        HTTPException 502: If the agent raises an unexpected runtime error.
    """
    uid = user["uid"]
    session_id = body.session_id or uid

    # Stamp uid so the middleware can include it in the CSV record.
    ctx = request_ctx.get()
    if ctx is not None:
        ctx["uid"] = uid

    # 1. History
    try:
        history = get_session_history(session_id)
    except Exception as exc:
        print(f"[/chat] Firestore read error: {exc}")
        history = []

    # 2. Run agent — writes inference_ms / token counts back into ctx
    result = run_agent(message=body.message, history=history)
    reply: str = result["response"]
    tokens_used: int = result["tokens_used"]
    used_search: bool = result["used_search"]

    if not reply:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El agente no devolvió una respuesta válida.",
        )

    # 3. Persist both turns
    try:
        save_message(session_id, "user", body.message, tokens=0)
        save_message(session_id, "assistant", reply, tokens=tokens_used)
    except Exception as exc:
        # Non-fatal: log and continue — user still gets their reply
        print(f"[/chat] Firestore write error: {exc}")

    return ChatResponse(
        reply=reply,
        session_id=session_id,
        tokens_used=tokens_used,
        used_search=used_search,
    )


@app.get(
    "/history",
    response_model=HistoryResponse,
    tags=["chat"],
)
def get_history(
    request: Request,
    uid_param: Optional[str] = Query(
        default=None,
        alias="uid",
        description="Target uid (viewer/admin only). Omit to use own session.",
    ),
    user: dict[str, Any] = Depends(require_role(["assistant_user", "viewer"])),
):
    """Return the conversation history for a session.

    - ``assistant_user`` — can only access their own history.
    - ``viewer`` / ``admin`` — can pass a ``uid`` query param to inspect any session.

    Args:
        request:   FastAPI Request — used to stamp uid into the metrics context.
        uid_param: Optional uid query parameter.
        user:      Injected by :func:`require_role`.

    Returns:
        :class:`HistoryResponse` with the messages list and count.

    Raises:
        HTTPException 403: If an ``assistant_user`` tries to access another uid.
    """
    caller_uid = user["uid"]
    caller_role = user.get("role")

    ctx = request_ctx.get()
    if ctx is not None:
        ctx["uid"] = caller_uid

    if uid_param and uid_param != caller_uid and caller_role not in ("viewer", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para ver el historial de otro usuario.",
        )

    target_uid = uid_param if uid_param and caller_role in ("viewer", "admin") else caller_uid

    try:
        messages = get_session_history(target_uid)
    except Exception as exc:
        print(f"[/history] Firestore read error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo obtener el historial en este momento.",
        ) from exc

    return HistoryResponse(messages=messages, total_messages=len(messages))


@app.delete(
    "/history",
    response_model=DeleteResponse,
    tags=["chat"],
)
def delete_history(
    request: Request,
    user: dict[str, Any] = Depends(require_role(["assistant_user"])),
):
    """Delete the entire conversation history for the authenticated user.

    Args:
        request: FastAPI Request — used to stamp uid into the metrics context.
        user:    Injected by :func:`require_role`.

    Returns:
        Confirmation message.

    Raises:
        HTTPException 503: If Firestore deletion fails.
    """
    uid = user["uid"]

    ctx = request_ctx.get()
    if ctx is not None:
        ctx["uid"] = uid

    try:
        clear_session(uid)
    except Exception as exc:
        print(f"[/history DELETE] Firestore delete error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo borrar el historial en este momento.",
        ) from exc

    return DeleteResponse(message="Historial borrado exitosamente")


@app.get(
    "/admin/sessions",
    response_model=list[dict[str, Any]],
    tags=["admin"],
)
def admin_sessions(
    request: Request,
    user: dict[str, Any] = Depends(require_role(["admin"])),
):
    """Return a summary of all user sessions.

    Restricted to the ``admin`` role.

    Args:
        request: FastAPI Request — used to stamp uid into the metrics context.
        user:    Injected by :func:`require_role` (used only for authorisation).

    Returns:
        List of session summary dicts from :func:`get_all_sessions`.

    Raises:
        HTTPException 503: If the Firestore query fails.
    """
    ctx = request_ctx.get()
    if ctx is not None:
        ctx["uid"] = user["uid"]

    try:
        return get_all_sessions()
    except Exception as exc:
        print(f"[/admin/sessions] Firestore error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron obtener las sesiones en este momento.",
        ) from exc
