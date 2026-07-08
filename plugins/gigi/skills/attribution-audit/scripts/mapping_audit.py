# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0", "psycopg2-binary>=2.9"]
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
  --uncaptured: citește fila-sursă 'Tiktok Ads' și pentru fiecare cont partajat împarte spend-ul
          pe BRAND-TOKEN real (din numele campaniei) → prinde spend-ul NECAPTURAT (un brand rulează
          campanii pe cont dar Mapping-ul nu-i revendică contul → banii cad printre degete / în alt
          brand). Golul care lipsea din auditul token-gol: contul e „curat" (tokenuri distincte) dar
          tot pierde bani. NECESITĂ doar GA4_SA_JSON (nu DB).
  --currency: verifică dacă flag-ul ×curs (incTik, PER-BRAND) se potrivește cu moneda REALĂ
          (PER-CONT, din DB tiktok_ad_accounts) a fiecărui cont. Bug tipic: brand incTik=DA (pt un cont
          USD) care capturează ȘI de pe un cont RON → RON umflat ×curs (Bonhaus RO/Nocturna Europa);
          sau cont USD la brand incTik=gol → USD sub-numărat. NECESITĂ DATABASE_URL_METRICS + GA4_SA_JSON.
  --live: pentru brandurile cu token GOL, rulează raportul TikTok live și cuantifică spend-ul
          campaniilor cu tag-ul ALTUI brand (phantom confirmat, în RON). NECESITĂ DATABASE_URL_METRICS.

Usage:
  export GA4_SA_JSON="$(uv run <kb.py> secret-get GA4_SA_JSON)"
  uv run mapping_audit.py                            # audit static (token gol/duplicat)
  uv run mapping_audit.py --uncaptured [--days 60]   # + spend NECAPTURAT pe conturi partajate
  export DATABASE_URL_METRICS=...; uv run mapping_audit.py --currency   # + nepotriviri de monedă (DB)
  export DATABASE_URL_METRICS=...; uv run mapping_audit.py --live       # + phantom token-gol (DB)
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

def _serial_to_date(v):
    import datetime
    try:
        return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(v)))
    except Exception:
        return None

def audit_uncaptured(brands, days=60):
    """Reconciliere TikTok pe TOATE conturile: citește 'Tiktok Ads' (A date · B cont · C campanie ·
    D spend) și aplică fiecărei campanii EXACT regula de atribuire din raport (col C = cont revendicat;
    col G = filtru: cont în G → token-gated, cont în C dar nu în G → capture-all; col G gol → token pe
    toate). Clasifică: capturat de exact 1 brand / NECAPTURAT (0 branduri = bani pierduți din raport) /
    DUBLU (>1 brand = dublă-numărare). Modelarea capture-all (col G) e esențială."""
    import datetime, socket
    socket.setdefaulttimeout(240)
    raw = os.environ.get("GA4_SA_JSON") or _kb("GA4_SA_JSON")
    creds = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    api = build("sheets", "v4", credentials=creds).spreadsheets()
    rows = None
    for attempt in range(6):
        try:
            rows = api.values().get(spreadsheetId=SS, range="'Tiktok Ads'!A:D",
                                    valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
            break
        except Exception as e:
            if attempt == 5:
                print(f"\n(--uncaptured: nu am putut citi fila 'Tiktok Ads': {e})"); return
            import time; time.sleep(3 * (attempt + 1))
    import datetime as _dt
    # reguli per brand: C (conturi norm), token (norm), G (conturi norm din col „Cont multiplu")
    rules = []
    for b in brands:
        C = {_tok(x) for x in b["tt"]}
        if not C:
            continue
        G = {_tok(x) for x in _split(b.get("multi", ""))}
        rules.append((b["brand"], C, _tok(b["token"]), G))
    def captors(acc, camp):
        """care branduri capturează campania — EXACT regula formulei din raport (col C + col G).
        cont în C obligatoriu; col G ne-gol: cont în G = token-gated, cont în C dar NU în G =
        capture-all; col G gol = token pe toate. (Capture-all e cheia — fără el, campaniile fără
        token pe un cont capture-all par fals 'neatribuibile'.)"""
        A = _tok(acc); N = _tok(camp); out = []
        for name, C, tok, G in rules:
            if A not in C:
                continue
            if G:
                if A in G:
                    if (not tok) or (tok in N):
                        out.append(name)
                else:
                    out.append(name)          # cont în C dar nu în G → capture-all
            else:
                if (not tok) or (tok in N):
                    out.append(name)
        return out
    # fereastra = ultimele `days` zile față de max data din sursă
    maxd = None
    for r in rows[1:]:
        d = _serial_to_date(r[0]) if r else None
        if d and (maxd is None or d > maxd):
            maxd = d
    cut = maxd - _dt.timedelta(days=days) if maxd else None
    tot = cap = unc = dbl = 0.0
    unc_by = defaultdict(float); dbl_by = defaultdict(float); acc_unc = defaultdict(float)
    for r in rows[1:]:
        if len(r) < 4:
            continue
        d = _serial_to_date(r[0])
        if cut and (not d or d < cut):
            continue
        sp = float(r[3]) if isinstance(r[3], (int, float)) else 0.0
        if sp <= 0:
            continue
        tot += sp
        cs = captors(r[1], r[2])
        if len(cs) == 0:
            unc += sp; unc_by[(str(r[1]), str(r[2])[:50])] += sp; acc_unc[str(r[1])] += sp
        elif len(cs) == 1:
            cap += sp
        else:
            dbl += sp; dbl_by[(str(r[1]), tuple(sorted(cs)))] += sp
    pct = lambda x: (x / tot * 100 if tot else 0)
    print(f"\n══ RECONCILIERE TikTok (ultimele {days}z) · total {tot:,.0f} RON ══")
    print(f"  🟢 capturat exact 1 brand : {cap:>12,.0f}  ({pct(cap):.1f}%)")
    print(f"  🔴 NECAPTURAT (0 branduri): {unc:>12,.0f}  ({pct(unc):.1f}%)")
    print(f"  🟠 DUBLU (>1 brand)       : {dbl:>12,.0f}  ({pct(dbl):.1f}%)")
    if acc_unc:
        print("\n  🔴 NECAPTURAT — campanii pe care NICIUN brand nu le prinde:")
        for a, v in sorted(acc_unc.items(), key=lambda x: -x[1]):
            if v > 50:
                print(f"     {v:>10,.0f}  cont '{a}'")
        for (a, c), v in sorted(unc_by.items(), key=lambda x: -x[1])[:10]:
            if v > 50:
                print(f"        {v:>8,.0f}  [{a}] {c}")
    if dbl_by:
        print("\n  🟠 DUBLU-capturate (același spend la 2+ branduri → dublă-numărare):")
        for (a, cs), v in sorted(dbl_by.items(), key=lambda x: -x[1])[:10]:
            print(f"     {v:>10,.0f}  cont '{a}' → {', '.join(cs)}")
    if not acc_unc and not dbl_by:
        print("\n  ✓ 100% capturat, 0 necapturat, 0 dublu — atribuire completă și fără suprapuneri.")

def _read_curs_config():
    """Din Curs valutar: incTik per brand (col E) + conturi TikTok marcate RON (rânduri
    `TikTok: <cont> | RON` = col A prefix „TikTok:", col B monedă)."""
    raw = os.environ.get("GA4_SA_JSON") or _kb("GA4_SA_JSON")
    creds = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    api = build("sheets", "v4", credentials=creds).spreadsheets()
    rows = api.values().get(spreadsheetId=SS, range="'Curs valutar'!A:F").execute().get("values", [])
    incTik, ron_cfg = {}, set()
    for r in rows:
        a = (str(r[0]).strip() if r and r[0] else "")
        if not a:
            continue
        m = re.match(r'^tiktok\s*:\s*(.+)$', a, re.I)
        if m:
            if len(r) > 1 and str(r[1]).strip().upper() == "RON":
                ron_cfg.add(_tok(m.group(1)))
            continue
        if len(r) > 4 and str(r[4]).strip().upper() == "DA":
            incTik[a] = True
    return incTik, ron_cfg

def audit_currency(brands):
    """Verifică dacă flag-ul ×curs (incTik, PER-BRAND din Curs valutar) se potrivește cu moneda
    REALĂ (PER-CONT, din DB `tiktok_ad_accounts`) a fiecărui cont TikTok pe care-l capturează brandul.
    Bug tipic (Bonhaus RO): brand incTik=DA (corect pt contul lui USD) dar capturează ȘI de pe un cont
    RON (Nocturna Europa) → partea RON umflată ×curs. Sau invers: cont USD la brand incTik=gol → USD
    SUB-numărat. NECESITĂ DATABASE_URL_METRICS + GA4_SA_JSON."""
    dsn = os.environ.get("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    if not dsn:
        print("\n(--currency necesită DATABASE_URL_METRICS pt moneda conturilor)"); return
    try:
        import psycopg2
    except ImportError:
        print("\n(--currency: psycopg2 indisponibil)"); return
    conn = psycopg2.connect(dsn.split("?")[0]); cur = conn.cursor()
    cur.execute("SELECT name, currency FROM tiktok_ad_accounts")
    accc = {_tok(n): (c or "").upper() for n, c in cur.fetchall()}
    conn.close()
    incTik, ron_cfg = _read_curs_config()
    print("\n══ AUDIT MONEDĂ TikTok — flag ×curs (incTik) vs moneda REALĂ a contului (DB) ══")
    print(f"  conturi marcate RON în Curs valutar (nu se ×curs): {sorted(ron_cfg) or '(niciunul)'}")
    issues = []
    for b in brands:
        it = incTik.get(b["brand"], False)
        for acc in b["tt"]:
            an = _tok(acc)
            curr = accc.get(an)
            if not curr:
                continue  # cont necunoscut în DB → nu-l putem verifica
            marked_ron = an in ron_cfg
            if curr == "USD":
                if not (it and not marked_ron):
                    why = "brand incTik=GOL → USD SUB-numărat (÷curs lipsă)" if not it else "cont USD marcat RON greșit"
                    issues.append((b["brand"], acc, curr, it, why))
            elif it and not marked_ron:      # cont RON (sau alt non-USD) + ×curs
                issues.append((b["brand"], acc, curr, it, "incTik=DA + NEmarcat RON → UMFLAT ×curs"))
    if not issues:
        print("  ✅ fiecare cont e tratat corect (USD→×curs, RON→fără curs). Nicio nepotrivire de monedă.")
    else:
        for brand, acc, curr, it, why in issues:
            print(f"  🔴 {brand:16} cont '{acc}' ({curr}, brand incTik={'DA' if it else 'gol'}) — {why}")
        print("\n  Fix: marchează conturile RON în Curs valutar (rând `TikTok: <cont> | RON`) → ×curs")
        print("       se aplică DOAR conturilor non-RON, chiar dacă brandul are incTik=DA. Vezi SKILL.md.")

def main():
    live = "--live" in sys.argv
    unc = "--uncaptured" in sys.argv
    curr = "--currency" in sys.argv
    days = 60
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            try: days = int(sys.argv[i + 1])
            except ValueError: pass
    brands = load_mapping()
    flags = audit_static(brands)
    if unc:
        audit_uncaptured(brands, days)
    if curr:
        audit_currency(brands)
    if live and flags:
        audit_live(flags, brands)
    elif flags and not live:
        print("\n  → rulează cu --live ca să cuantific spend-ul fantomă (token gol) din API.")
    if not unc:
        print("  → rulează cu --uncaptured (necapturat/dublu) sau --currency (nepotriviri de monedă).")

if __name__ == "__main__":
    main()
