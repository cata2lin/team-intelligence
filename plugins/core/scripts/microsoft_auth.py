# /// script
# requires-python = ">=3.10"
# dependencies = ["msal>=1.24", "psycopg2-binary>=2.9"]
# ///
"""Microsoft Graph OAuth with a DB-backed token cache.

The MSAL token cache (which holds the long-lived refresh token) is stored in the
SharedClaude knowledge base (secret `MS_MSAL_CACHE`). So the Microsoft sign-in
happens ONCE for the whole team: a person with access to the OneDrive workbook
runs `microsoft_auth.py --login` a single time, the refresh token is saved to the
DB, and from then on every employee/machine gets an access token SILENTLY with no
login. (Microsoft requires that one interactive consent for delegated access to a
personal OneDrive — after it, nothing.)

    from microsoft_auth import get_token
    tok = get_token()           # silent; uses the shared DB token
"""
import json
import os
import sys
from pathlib import Path

import msal

CLIENT_ID = "7c52a278-ae7a-4bb3-9244-3786c3d20a77"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Files.ReadWrite.All"]
CACHE_SECRET = "MS_MSAL_CACHE"
LOCAL_CACHE = Path.home() / ".config" / "microsoft" / "msal-cache.bin"


def _db():
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg2
        return psycopg2.connect(url, connect_timeout=12)
    except Exception:
        return None


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    data = None
    conn = _db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE key=%s", (CACHE_SECRET,))
                row = cur.fetchone()
                if row and row[0]:
                    data = row[0]
        except Exception:
            pass
        finally:
            conn.close()
    if not data and LOCAL_CACHE.exists():
        data = LOCAL_CACHE.read_text()
    if data:
        try:
            cache.deserialize(data)
        except Exception:
            pass
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    blob = cache.serialize()
    try:
        LOCAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_CACHE.write_text(blob)
        os.chmod(LOCAL_CACHE, 0o600)
    except Exception:
        pass
    conn = _db()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO secrets (key, value, service, kind, is_sensitive)
                       VALUES (%s,%s,'microsoft','secret',true)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                    (CACHE_SECRET, blob),
                )
        except Exception:
            pass
        finally:
            conn.close()


def get_token(interactive: bool = False) -> str:
    cache = _load_cache()
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        if not interactive:
            raise RuntimeError(
                "No shared Microsoft token yet. A teammate with access to the OneDrive "
                "workbook must run once:  uv run <core>/scripts/microsoft_auth.py --login\n"
                "After that one sign-in the token is stored in the DB and shared with everyone."
            )
        result = app.acquire_token_interactive(scopes=SCOPES)
    _save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {json.dumps(result, indent=2)}")
    return result["access_token"]


if __name__ == "__main__":
    do_login = "--login" in sys.argv
    tok = get_token(interactive=do_login)
    where = "signed in + cached to the DB for the whole team" if do_login else "from the shared DB token"
    print(f"OK, token length={len(tok)} ({where}).")
