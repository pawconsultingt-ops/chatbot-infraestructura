"""
Firestore service layer for session and message persistence.

Collection layout
-----------------
sessions/{uid}
    uid          : str
    email        : str
    created_at   : timestamp
    updated_at   : timestamp
    messages     : list[dict]   # ArrayUnion-appended
        role         : str      # 'user' | 'assistant'
        content      : str
        timestamp    : timestamp (SERVER_TIMESTAMP)
        tokens_used  : int
"""

from __future__ import annotations

import os
from typing import Any

from google.cloud import firestore

# ---------------------------------------------------------------------------
# Firestore client (lazy singleton)
# ---------------------------------------------------------------------------

_db: firestore.Client | None = None


def _get_db() -> firestore.Client:
    """Return a shared Firestore client, initialising it on first call.

    Uses the credentials file referenced by ``GOOGLE_APPLICATION_CREDENTIALS``.

    Returns:
        A :class:`google.cloud.firestore.Client` instance.
    """
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


_SESSIONS = "sessions"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_session_history(uid: str) -> list[dict[str, Any]]:
    """Retrieve the full message history for a user session.

    Args:
        uid: Firebase user identifier used as the Firestore document ID.

    Returns:
        The ``messages`` array stored in the session document, or an empty
        list if the document does not exist yet.
    """
    try:
        doc = _get_db().collection(_SESSIONS).document(uid).get()
        if not doc.exists:
            return []
        return doc.to_dict().get("messages", [])
    except Exception as exc:
        print(f"[firestore] get_session_history error for uid={uid!r}: {exc}")
        return []


def save_message(
    uid: str,
    role: str,
    content: str,
    tokens: int = 0,
) -> None:
    """Append a single message to the user's session document.

    If the document does not exist it is created with default metadata.
    Uses :data:`google.cloud.firestore.ArrayUnion` to append atomically
    without overwriting existing messages.

    Args:
        uid:     Firebase user identifier.
        role:    Speaker role, typically ``'user'`` or ``'assistant'``.
        content: Text content of the message.
        tokens:  Number of tokens consumed by this exchange (default ``0``).
    """
    try:
        db = _get_db()
        ref = db.collection(_SESSIONS).document(uid)

        message: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "tokens_used": tokens,
        }

        doc = ref.get()
        if not doc.exists:
            # Bootstrap the document on first message
            ref.set(
                {
                    "uid": uid,
                    "email": "",
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                    "messages": [message],
                }
            )
        else:
            ref.update(
                {
                    "messages": firestore.ArrayUnion([message]),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }
            )
    except Exception as exc:
        print(f"[firestore] save_message error for uid={uid!r}: {exc}")


def clear_session(uid: str) -> None:
    """Delete the entire session document for a user.

    This removes the document and all stored messages permanently.

    Args:
        uid: Firebase user identifier of the session to delete.
    """
    try:
        _get_db().collection(_SESSIONS).document(uid).delete()
    except Exception as exc:
        print(f"[firestore] clear_session error for uid={uid!r}: {exc}")


def get_all_sessions() -> list[dict[str, Any]]:
    """Return a summary of every session in the collection.

    Intended for privileged roles (``'viewer'``, ``'admin'``).  Returns a
    lightweight projection — the full ``messages`` array is **not** included,
    only its length — to avoid large payloads.

    Returns:
        A list of dicts, each with:

        * ``id``          – Firestore document ID (same as ``uid``).
        * ``uid``         – Firebase user identifier.
        * ``email``       – User email stored in the session.
        * ``updated_at``  – Last update timestamp (may be ``None``).
        * ``message_count`` – Number of messages in the session.
    """
    try:
        docs = _get_db().collection(_SESSIONS).stream()
        sessions: list[dict[str, Any]] = []
        for doc in docs:
            data = doc.to_dict()
            sessions.append(
                {
                    "id": doc.id,
                    "uid": data.get("uid", doc.id),
                    "email": data.get("email", ""),
                    "updated_at": data.get("updated_at"),
                    "message_count": len(data.get("messages", [])),
                }
            )
        return sessions
    except Exception as exc:
        print(f"[firestore] get_all_sessions error: {exc}")
        return []
