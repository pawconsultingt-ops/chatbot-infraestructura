"""
assign_role.py — CLI utility to assign a Firebase custom role claim.

Usage:
    python assign_role.py <uid> <role>

Roles:
    assistant_user  Regular chat user
    viewer          Read-only access to sessions
    admin           Full access including admin/sessions endpoint

Examples:
    python assign_role.py xK7mP9qR2nLs assistant_user
    python assign_role.py yM3kL8wQ5nRt viewer
    python assign_role.py zP2jN6vB4mCw admin
"""

import sys
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, credentials

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_ROLES = {"assistant_user", "viewer", "admin"}

ROLE_DESCRIPTIONS = {
    "assistant_user": "Usuario estándar — acceso al chat y a su propio historial.",
    "viewer":         "Lector — puede consultar sesiones ajenas (solo lectura).",
    "admin":          "Administrador — acceso completo, incluyendo /admin/sessions.",
}

# ---------------------------------------------------------------------------
# Firebase initialisation
# ---------------------------------------------------------------------------

def init_firebase() -> None:
    """Initialise Firebase Admin SDK using the service-account file.

    Reads the path from the ``GOOGLE_APPLICATION_CREDENTIALS`` environment
    variable, falling back to ``../service-account.json`` relative to this
    script's location.
    """
    if firebase_admin._apps:
        return

    cred_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(Path(__file__).parent.parent / "service-account.json"),
    )

    if not Path(cred_path).exists():
        print(f"[ERROR] No se encontró el archivo de credenciales: {cred_path}")
        print("        Verifica GOOGLE_APPLICATION_CREDENTIALS en tu .env")
        sys.exit(1)

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def assign_role(uid: str, role: str) -> None:
    """Assign a custom role claim to a Firebase user.

    Args:
        uid:  Firebase user identifier.
        role: Role string — must be one of :data:`VALID_ROLES`.
    """
    # 1. Validate role
    if role not in VALID_ROLES:
        print(f"[ERROR] Rol inválido: '{role}'")
        print(f"        Roles permitidos: {', '.join(sorted(VALID_ROLES))}")
        sys.exit(1)

    # 2. Fetch user record (validates that uid exists)
    try:
        user_record = auth.get_user(uid)
    except auth.UserNotFoundError:
        print(f"[ERROR] No existe ningún usuario con uid: '{uid}'")
        print("        Verifica que el uid sea correcto en Firebase Console.")
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] No se pudo obtener el usuario: {exc}")
        sys.exit(1)

    # 3. Assign custom claim
    try:
        auth.set_custom_user_claims(uid, {"role": role})
    except Exception as exc:
        print(f"[ERROR] No se pudo asignar el rol: {exc}")
        sys.exit(1)

    # 4. Confirmation output
    email_info = f" ({user_record.email})" if user_record.email else ""
    print()
    print("  [OK] Rol asignado correctamente")
    print(f"    uid   : {uid}{email_info}")
    print(f"    rol   : {role}")
    print(f"    acceso: {ROLE_DESCRIPTIONS[role]}")
    print()
    print("  AVISO: El usuario debe cerrar e iniciar sesion para que el cambio")
    print("         tenga efecto (el custom claim se incluye en el nuevo ID token).")
    print()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    _, uid, role = sys.argv

    init_firebase()
    assign_role(uid, role)


if __name__ == "__main__":
    main()
