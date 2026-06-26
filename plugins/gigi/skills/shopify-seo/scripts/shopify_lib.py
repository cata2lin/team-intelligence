# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Shared Shopify Admin API helpers for the shopify-seo skill.

Auth model: the ARONA "ARONA Assistant" custom app (full scopes) is installed on
the team's stores. We mint a short-lived shpat_ token per run via the OAuth
client_credentials grant. App id/secret + api version come from the SharedClaude
secrets table via kb.py. NEVER print a token or secret value.

Usage from another script in this folder:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from shopify_lib import Store
    s = Store("esteban.ro")          # any domain the ARONA app is installed on
    data = s.gql("{ shop { name } }")
    asset = s.asset_get("layout/theme.liquid")
    s.asset_put("layout/theme.liquid", new_value)
"""
import os, subprocess, json, time, urllib.parse
from pathlib import Path
import requests, urllib.request


def _kb_path() -> str:
    """Locate core's kb.py: env override, then relative to the marketplace."""
    env = os.environ.get("KB_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    # scripts -> shopify-seo -> skills -> gigi -> plugins -> core/scripts/kb.py
    for up in range(3, 7):
        cand = here.parents[up] / "core" / "scripts" / "kb.py"
        if cand.exists():
            return str(cand)
    raise FileNotFoundError("kb.py not found; set KB_PATH env var")


KB = _kb_path()


def secret(key: str) -> str:
    """Fetch a secret value from the SharedClaude DB. Never logged."""
    return subprocess.run(["uv", "run", KB, "secret-get", key],
                          capture_output=True, text=True).stdout.strip()


class Store:
    """One Shopify store, authenticated via the ARONA app client_credentials grant.

    `store` may be:
      - a store KEY (e.g. "esteban") -> admin domain from secret
        {app_prefix}_ESTEBAN_DOMAIN (this is the *.myshopify.com domain;
        the Admin API + OAuth token endpoint only work on that, NOT the
        custom public domain like esteban.ro)
      - a *.myshopify.com domain directly.

    Exposes:
      self.admin   -> *.myshopify.com   (use for Admin API / token / assets)
      self.public  -> custom storefront (use for live storefront fetches)
    """

    def __init__(self, store: str, app_prefix: str = "SHOPIFY_ARONA", token: str | None = None):
        import re
        store = store.replace("https://", "").replace("http://", "").strip("/")
        self.ver = secret(f"{app_prefix}_API_VERSION") or "2025-01"
        if token:
            # explicit token (e.g. a static shpat_* from stores.csv); `store` is the
            # myshopify admin domain. Works for ANY store, not just ARONA-app ones.
            self.admin = store
            self.tok = token
        else:
            if store.endswith("myshopify.com"):
                self.admin = store
            else:
                key = re.sub(r"[^A-Z0-9]", "", store.upper().split(".")[0])
                self.admin = secret(f"{app_prefix}_{key}_DOMAIN") or store
            cid = secret(f"{app_prefix}_CLIENT_ID")
            csec = secret(f"{app_prefix}_CLIENT_SECRET")
            resp = requests.post(
                f"https://{self.admin}/admin/oauth/access_token",
                json={"client_id": cid, "client_secret": csec,
                      "grant_type": "client_credentials"}, timeout=30)
            if "application/json" not in resp.headers.get("content-type", ""):
                raise RuntimeError(f"OAuth token endpoint did not return JSON for '{self.admin}'. "
                                   "Pass a *.myshopify.com domain + token=, or a store key whose "
                                   f"{app_prefix}_<KEY>_DOMAIN secret is set.")
            self.tok = resp.json()["access_token"]
        self.domain = self.admin  # back-compat alias
        self._theme = None
        try:
            self.public = self.gql("{shop{primaryDomain{host}}}")["shop"]["primaryDomain"]["host"]
        except Exception:
            self.public = self.admin

    @classmethod
    def from_csv(cls, prefix: str, csv_secret: str = "SHOPIFY_STORES_CSV"):
        """Build a Store for any team shop by its stores.csv prefix (OFER, ROSSI, …)."""
        import csv, io
        rows = list(csv.reader(io.StringIO(secret(csv_secret))))
        row = next((r for r in rows[1:] if r and r[0].strip().upper() == prefix.upper()), None)
        if not row:
            raise RuntimeError(f"prefix '{prefix}' not found in {csv_secret}")
        return cls(row[1].strip(), token=row[2].strip())

    # ---- GraphQL ----
    def gql(self, query: str, variables: dict | None = None, retries: int = 6) -> dict:
        url = f"https://{self.domain}/admin/api/{self.ver}/graphql.json"
        hdr = {"X-Shopify-Access-Token": self.tok, "Content-Type": "application/json"}
        for a in range(retries):
            r = requests.post(url, headers=hdr,
                              json={"query": query, "variables": variables or {}}, timeout=60).json()
            if "errors" in r and any("throttl" in str(e).lower() for e in r["errors"]):
                time.sleep(2 * (a + 1)); continue
            if "data" not in r:
                raise RuntimeError(f"GraphQL error: {str(r.get('errors'))[:300]}")
            return r["data"]
        raise RuntimeError("GraphQL throttled out")

    def gql_all(self, root: str, inner: str, query_filter: str = "") -> list:
        """Paginate a top-level connection. `inner` is the node body (no braces)."""
        out, cursor = [], None
        qf = f',query:"{query_filter}"' if query_filter else ""
        while True:
            after = f',after:"{cursor}"' if cursor else ""
            q = f'{{{root}(first:200{after}{qf}){{pageInfo{{hasNextPage endCursor}}nodes{{{inner}}}}}}}'
            d = self.gql(q)[root]
            out += d["nodes"]
            if not d["pageInfo"]["hasNextPage"]:
                return out
            cursor = d["pageInfo"]["endCursor"]

    # ---- REST ----
    def rest(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"https://{self.domain}/admin/api/{self.ver}/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
            headers={"X-Shopify-Access-Token": self.tok, "Content-Type": "application/json"})
        for a in range(6):
            try:
                return json.load(urllib.request.urlopen(req, timeout=60))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(2 * (a + 1)); continue
                raise RuntimeError(f"REST {method} {path} -> {e.code}: {e.read()[:300]}")
        raise RuntimeError("REST throttled out")

    # ---- Theme assets ----
    def theme_main_id(self) -> int:
        if self._theme is None:
            themes = self.rest("GET", "themes.json")["themes"]
            self._theme = next(t["id"] for t in themes if t["role"] == "main")
        return self._theme

    def asset_get(self, key: str, theme_id: int | None = None) -> str:
        tid = theme_id or self.theme_main_id()
        q = urllib.parse.urlencode({"asset[key]": key})
        return self.rest("GET", f"themes/{tid}/assets.json?{q}")["asset"]["value"]

    def asset_put(self, key: str, value: str, theme_id: int | None = None) -> dict:
        tid = theme_id or self.theme_main_id()
        return self.rest("PUT", f"themes/{tid}/assets.json", {"asset": {"key": key, "value": value}})


def fetch_live(url: str, accept: str | None = None, bust: bool = True) -> str:
    """GET a storefront URL (no auth) with a cache-buster. Returns HTML text."""
    import ssl
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    if bust:
        url += ("&" if "?" in url else "?") + "nc=" + str(time.time_ns())
    hdr = {"User-Agent": "Mozilla/5.0 Chrome"}
    if accept:
        hdr["Accept"] = accept
    for a in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=25, context=ctx).read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(2)
    return ""
