# /// script
# requires-python = ">=3.10"
# dependencies = ["msal>=1.24"]
# ///
"""Microsoft Graph OAuth — interactive (loopback) flow with token cache.

Usage:
    from scripts.microsoft_auth import get_token
    tok = get_token()  # returns access_token string
    # then: headers={'Authorization': f'Bearer {tok}'}
"""
import json
import os
from pathlib import Path
import msal

CLIENT_ID = "7c52a278-ae7a-4bb3-9244-3786c3d20a77"
# "consumers" = personal MS accounts; the app is multi-tenant + personal so this works.
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Files.ReadWrite.All"]  # MSAL adds offline_access/openid/profile automatically

CACHE_DIR = Path.home() / ".config" / "microsoft"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "msal-cache.bin"


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize())
        os.chmod(CACHE_FILE, 0o600)


def get_token() -> str:
    cache = _load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        # opens browser → http://localhost callback
        result = app.acquire_token_interactive(scopes=SCOPES)
    _save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {json.dumps(result, indent=2)}")
    return result["access_token"]


if __name__ == "__main__":
    tok = get_token()
    print(f"OK, token length={len(tok)}")
