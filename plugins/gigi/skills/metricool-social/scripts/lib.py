"""Portable helpers (Mac + VPS): fetch a KB secret directly from the DB, upload to Vercel Blob.
No kb.py / social_post path dependency — works anywhere KB_DATABASE_URL is set."""
import os, re, sys, json, subprocess, mimetypes, requests

def _kburl():
    return os.environ.get("KB_DATABASE_URL") or subprocess.run(
        ["/bin/zsh", "-lc", "echo $KB_DATABASE_URL"], capture_output=True, text=True).stdout.strip()

def secret(key):
    """KB secret via direct DB query (secrets.value = plaintext); fallback kb.py if present."""
    url = _kburl()
    if url:
        try:
            import psycopg2
            with psycopg2.connect(url, connect_timeout=15) as c, c.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE key=%s", (key,))
                r = cur.fetchone()
                if r and r[0]:
                    return r[0].strip()
        except Exception:
            pass
    for kb in ("/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py",
               os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")):
        if os.path.exists(kb):
            v = subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True, timeout=60).stdout.strip()
            if v:
                return v
    return ""

def blob_upload(path):
    """Upload a local file to Vercel Blob → public URL."""
    tok = secret("BLOB_READ_WRITE_TOKEN_TOM") or secret("BLOB_READ_WRITE_TOKEN_SCENTUM")
    if not tok:
        sys.exit("Lipsește BLOB_READ_WRITE_TOKEN.")
    data = open(path, "rb").read()
    ct = mimetypes.guess_type(path)[0] or "video/mp4"
    name = "social/" + re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))
    r = requests.put(f"https://blob.vercel-storage.com/{name}",
                     headers={"authorization": "Bearer " + tok, "x-api-version": "7",
                              "x-content-type": ct, "x-add-random-suffix": "1"},
                     data=data, timeout=120).json()
    if not r.get("url"):
        sys.exit("Blob upload eșuat: " + json.dumps(r)[:200])
    return r["url"]
