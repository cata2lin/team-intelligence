# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
mapping_audit.py — auditează tab-ul „Mapping" (sheet „CPA și financiar") pentru bug-uri de
ATRIBUIRE a spend-ului între branduri, de tipul descoperit la Belasil: un cont TikTok PARTAJAT
de mai multe branduri, dar un brand îl revendică cu **token de campanie GOL** → înghite TOATE
campaniile de pe cont, inclusiv ale altui brand (ex. Belasil înghițea campaniile „NEW TIKTOK
ESTEBAN"). Asta umflă un brand și DUBLU-numără spend-ul.

Regula: un cont TikTok partajat e sigur DOAR dacă fiecare brand care-l revendică are un token de
campanie DISTINCT și ne-gol (col „Campanie"). Token gol pe cont partajat = bug.

  static (implicit): citește Mapping o dată, listează conturile partajate + flag-urile.
  --live: pentru brandurile flag-uite, rulează raportul TikTok live și cuantifică spend-ul
          campaniilor cu tag-ul ALTUI brand (phantom confirmat, în RON).

Usage:
  export GA4_SA_JSON="$(uv run <kb.py> secret-get GA4_SA_JSON)"
  uv run mapping_audit.py                 # audit static
  export DATABASE_URL_METRICS=...; uv run mapping_audit.py --live   # + cuantificare live
"""
import json, os, re, subprocess, sys
from pathlib import Path
from collections import defaultdict
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SS = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"
KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
TIKTOK_PY = Path(__file__).resolve().parents[2] / "tiktok-ads" / "tiktok.py"
# coloane Mapping: 0 Brand · 1 Facebook · 2 Tiktok · 3 Shopify · 4 Google · 5 Campanie(token) · 6 Cont multiplu
C_BRAND, C_FB, C_TT, C_TOKEN, C_MULTI = 0, 1, 2, 5, 6

def _kb(k):
    try:
        return subprocess.run(["uv", "run", str(KB), "secret-get", k], capture_output=True, text=True, timeout=45).stdout.strip()
    except Exception:
        return ""

def _split(s):
    return [x.strip() for x in (s or "").replace("\n", ",").split(",") if x.strip()]

def _tok(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

def load_mapping():
    raw = os.environ.get("GA4_SA_JSON") or _kb("GA4_SA_JSON")
    if not raw:
        sys.exit("lipsește GA4_SA_JSON")
    creds = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    api = build("sheets", "v4", credentials=creds).spreadsheets()
    rows = api.values().get(spreadsheetId=SS, range="'Mapping'!A:G").execute().get("values", [])
    out = []
    for r in rows[1:]:
        def g(i): return r[i].strip() if i < len(r) and r[i] else ""
        if not g(C_BRAND):
            continue
        out.append({"brand": g(C_BRAND), "tt": _split(g(C_TT)), "token": g(C_TOKEN), "multi": g(C_MULTI)})
    return out

def audit_static(brands):
    owners = defaultdict(list)   # cont(lower) -> [(brand, token)]
    for b in brands:
        for acc in b["tt"]:
            owners[acc.strip().lower()].append((b["brand"], b["token"]))
    shared = {a: o for a, o in owners.items() if len(o) > 1}
    print(f"\n══ AUDIT MAPPING · conturi TikTok PARTAJATE: {len(shared)} ══")
    flags = []
    for acc, o in sorted(shared.items()):
        names = [f"{nm}[{tok or '∅'}]" for nm, tok in o]
        empty = [nm for nm, tok in o if not tok]
        toks = [_tok(tok) for _, tok in o if tok]
        dup = len(toks) != len(set(toks))
        risk = "🔴" if empty else ("🟠" if dup else "🟢")
        print(f"  {risk} {acc:26} ← {', '.join(names)}")
        if empty:
            print(f"       ⚠ token GOL la: {', '.join(empty)} → înghite TOATE campaniile contului (phantom + dublu-numărare)")
            for nm in empty:
                flags.append((nm, acc, [x for x in o if x[0] != nm]))
        elif dup:
            print(f"       ⚠ token DUPLICAT între branduri → nu se pot separa campaniile")
    if not flags:
        print("\n  ✓ niciun cont partajat cu token gol — atribuire curată.")
    return flags

def audit_live(flags, brands):
    dsn = os.environ.get("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    if not dsn:
        print("\n(--live necesită DATABASE_URL_METRICS)"); return
    # tokenurile TUTUROR brandurilor, ca să detectăm tag-uri „străine" în numele campaniilor
    all_tokens = {b["brand"]: _tok(b["token"]) for b in brands if _tok(b["token"])}
    env = dict(os.environ, DATABASE_URL_METRICS=dsn)
    print("\n══ VERIFICARE LIVE (campanii cu tag-ul altui brand) ══")
    for brand, acc, others in flags:
        try:
            out = subprocess.run(["uv", "run", str(TIKTOK_PY), "report", brand, "--level", "campaign",
                                  "--range", "last_14d", "--sort", "spend"],
                                 capture_output=True, text=True, timeout=180, env=env).stdout
        except Exception as e:
            print(f"  {brand}: live n/a ({e})"); continue
        foreign = []
        for ln in out.splitlines():
            if not ln or ln.startswith("#") or ln.lstrip().startswith(("nume", "TOTAL")):
                continue
            name = ln[:40].strip()
            m = re.match(r"\s*(\d+)", ln[40:])
            spend = int(m.group(1)) if m else 0
            up = _tok(name)
            hits = [ob for ob, t in all_tokens.items() if ob != brand and t and t in up]
            if hits and name:
                foreign.append((name, spend, hits[0]))
        tot = sum(s for _, s, _ in foreign)
        if foreign:
            print(f"  🔴 {brand}: ~{tot:,} RON/14z din campanii ale altui brand:")
            for name, spend, ob in foreign[:8]:
                print(f"       {spend:>7,}  „{name[:48]}\"  → de fapt {ob}")
        else:
            print(f"  🟢 {brand}: nicio campanie cu tag străin (poate deja reparat).")

def main():
    live = "--live" in sys.argv
    brands = load_mapping()
    flags = audit_static(brands)
    if live and flags:
        audit_live(flags, brands)
    elif flags and not live:
        print("\n  → rulează cu --live ca să cuantific spend-ul fantomă din API.")

if __name__ == "__main__":
    main()
