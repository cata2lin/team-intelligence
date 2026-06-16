# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Shared, READ-WRITE brand reference, backed by the KB config `BRAND_REFERENCE`.
Any ad/Shopify/analytics skill should READ ids/handles from here instead of re-deriving them,
and WRITE back anything new it discovers (a campaign id, a logo url, a new brand) so the whole
team's scripts stay current. One source of truth, kept fresh by whoever touches it.

  uv run brandref.py get belasil                 # all fields for a brand
  uv run brandref.py get belasil gads_pmax       # one field
  uv run brandref.py set belasil gads_pmax 22478321481   # upsert one field (read-modify-write merge)
  uv run brandref.py list                        # brands

As a library:
  import brandref
  ref = brandref.get("belasil")                  # dict
  brandref.set_field("belasil", "logo_url", "https://…")   # merge-updates KB
"""
import sys, json, subprocess
from pathlib import Path

_KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"

def _kb(*args, value=None):
    cmd = ["uv", "run", str(_KB), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=45)

def load():
    out = _kb("secret-get", "BRAND_REFERENCE").stdout.strip()
    try:
        return json.loads(out) if out else {}
    except Exception:
        return {}

def save(d):
    # KB config: replace the whole JSON (callers do read-modify-write via set_field)
    r = _kb("secret-set", "BRAND_REFERENCE", json.dumps(d, ensure_ascii=False), "--service", "brands", "--config")
    return r.returncode == 0

def get(brand, key=None):
    e = load().get((brand or "").lower(), {})
    return e.get(key) if key else e

def set_field(brand, key, value):
    """Upsert one field for a brand (merge — never clobbers other fields/brands)."""
    d = load()
    d.setdefault((brand or "").lower(), {})[key] = value
    save(d)
    return d[(brand or "").lower()]

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        print("branduri:", [k for k in load() if not k.startswith("_")])
    elif a[0] == "get":
        v = get(a[1], a[2] if len(a) > 2 else None)
        print(json.dumps(v, ensure_ascii=False, indent=1) if isinstance(v, dict) else v)
    elif a[0] == "set" and len(a) >= 4:
        print("ok:", json.dumps(set_field(a[1], a[2], " ".join(a[3:])), ensure_ascii=False))
    else:
        print(__doc__)
