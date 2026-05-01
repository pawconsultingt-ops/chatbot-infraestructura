"""
Authentication module for FastAPI using Firebase Admin SDK.
"""

import os
from typing import Any

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import credentials
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ---------------------------------------------------------------------------
# Firebase initialisation
# ---------------------------------------------------------------------------

def _ensure_firebase() -> None:
    """Ensure the Firebase Admin default app is initialised.

    Uses ``firebase_admin.get_app()`` to check for the default app
    (more reliable than inspecting ``firebase_admin._apps`` directly).
    Reads the service-account path from ``GOOGLE_APPLICATION_CREDENTIALS``.

    Raises:
        HTTPException 503: If the credentials file is missing or invalid.
    """
    # Fast path: default app already exists
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass  # Default app not yet initialised — proceed below

    cred_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "../service-account.json",
    )

    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Firebase no está configurado correctamente. "
                f"Descarga el service-account.json desde Firebase Console y "
                f"colócalo en la ruta indicada por GOOGLE_APPLICATION_CREDENTIALS. "
                f"Detalle: {exc}"
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Bearer-token extractor
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Core token verification
# ---------------------------------------------------------------------------

def verify_firebase_token(token: str) -> dict[str, Any]:
    """Verify a Firebase ID token and return the decoded user payload.

    Args:
        token: A Firebase ID token string obtained from the client SDK.

    Returns:
        A dict with the following keys:

        * ``uid``   – Firebase user identifier.
        * ``email`` – User's email address (may be ``None`` for anonymous users).
        * ``role``  – Value of the ``role`` custom claim, or ``None`` if absent.

    Raises:
        HTTPException 503: If Firebase Admin SDK is not properly configured.
        HTTPException 401: If the token is invalid or expired.
    """
    # Raises 503 if service-account is missing/invalid — propagates as-is,
    # never gets swallowed by the 401 handler below.
    _ensure_firebase()

    try:
        decoded: dict[str, Any] = fb_auth.verify_id_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return {
        "uid":   decoded.get("uid"),
        "email": decoded.get("email"),
        "role":  decoded.get("role"),
    }


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency that extracts and validates the Bearer token.

    Args:
        credentials: Injected by FastAPI from the ``Authorization`` header.

    Returns:
        The user dict returned by :func:`verify_firebase_token`.

    Raises:
        HTTPException 401: If the ``Authorization`` header is missing or the
            token is invalid.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or malformed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_firebase_token(credentials.credentials)


def require_role(allowed_roles: list[str]):
    """FastAPI dependency factory that enforces role-based access control.

    Args:
        allowed_roles: Role strings permitted to access the endpoint.

    Returns:
        A FastAPI dependency callable that validates the current user's role.

    Raises:
        HTTPException 403: If the user's ``role`` claim is not in
            ``allowed_roles``.
    """

    def _check_role(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acceso denegado. Roles permitidos: {allowed_roles}. "
                    f"Tu rol actual: '{user.get('role')}'. "
                    f"Ejecuta: python assign_role.py <uid> {allowed_roles[0]}"
                ),
            )
        return user

    return _check_role
