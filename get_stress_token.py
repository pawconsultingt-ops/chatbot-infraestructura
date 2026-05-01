"""
get_stress_token.py

Generates a Firebase ID token for stress testing by:
  1. Creating a custom token for a test user via Firebase Admin SDK
  2. Exchanging it for an ID token via the Firebase REST API
  3. Assigning the 'assistant_user' role to that test user

Usage:
    python get_stress_token.py
    python get_stress_token.py --uid stress-test-user --role assistant_user
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────────
FIREBASE_WEB_API_KEY   = "AIzaSyBkDpQUVlTM522gYdUjdINsF1LtifNR6nA"
SERVICE_ACCOUNT_PATH   = Path(__file__).parent / "service-account.json"
BACKEND_CREDENTIALS    = Path(__file__).parent / "backend" / ".env"

DEFAULT_TEST_UID       = "stress-test-user-001"
DEFAULT_ROLE           = "assistant_user"


def _load_env():
    if BACKEND_CREDENTIALS.exists():
        for line in BACKEND_CREDENTIALS.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _init_firebase():
    import firebase_admin
    from firebase_admin import credentials

    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    creds_json_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if creds_json_env:
        cred = credentials.Certificate(json.loads(creds_json_env))
    else:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", str(SERVICE_ACCOUNT_PATH))
        cred = credentials.Certificate(cred_path)

    firebase_admin.initialize_app(cred)


def _create_custom_token(uid: str) -> str:
    from firebase_admin import auth
    token_bytes = auth.create_custom_token(uid)
    return token_bytes.decode() if isinstance(token_bytes, bytes) else token_bytes


def _exchange_for_id_token(custom_token: str) -> str:
    url = (
        f"https://identitytoolkit.googleapis.com/v1/"
        f"accounts:signInWithCustomToken?key={FIREBASE_WEB_API_KEY}"
    )
    payload = json.dumps({
        "token": custom_token,
        "returnSecureToken": True,
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    return data["idToken"]


def _ensure_user_exists(uid: str) -> None:
    from firebase_admin import auth
    try:
        auth.get_user(uid)
        print(f"  User '{uid}' already exists.")
    except auth.UserNotFoundError:
        auth.create_user(uid=uid, display_name="Stress Test Bot")
        print(f"  Created new user '{uid}'.")


def _assign_role(uid: str, role: str) -> None:
    from firebase_admin import auth
    _ensure_user_exists(uid)
    auth.set_custom_user_claims(uid, {"role": role})


def main():
    parser = argparse.ArgumentParser(description="Get Firebase ID token for stress tests")
    parser.add_argument("--uid",  default=DEFAULT_TEST_UID, help="Firebase UID for test user")
    parser.add_argument("--role", default=DEFAULT_ROLE,     help="Role claim to assign")
    args = parser.parse_args()

    print(f"Initialising Firebase Admin SDK...")
    _load_env()
    _init_firebase()

    print(f"Assigning role '{args.role}' to uid '{args.uid}'...")
    _assign_role(args.uid, args.role)

    print(f"Creating custom token...")
    custom_token = _create_custom_token(args.uid)

    print(f"Exchanging for ID token via REST API...")
    id_token = _exchange_for_id_token(custom_token)

    print(f"\n{'='*60}")
    print(f"Firebase ID Token (valid for 1 hour):")
    print(f"{'='*60}")
    print(id_token)
    print(f"{'='*60}\n")

    # Write token to file so PowerShell can read it
    token_file = Path(__file__).parent / ".stress_token"
    token_file.write_text(id_token, encoding="utf-8")
    print(f"Token also saved to: {token_file}")
    print(f"\nTo use in PowerShell:")
    print(f'  $env:STRESS_AUTH_TOKEN = Get-Content .stress_token -Raw')
    print(f'  .\\stress_tests\\run_scenario1.ps1')


if __name__ == "__main__":
    main()
