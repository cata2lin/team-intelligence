# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
sync_brand_accounts.py — reconciliază maparea brand→cont de reclame din SURSA DE ADEVĂR
(tab „Mapping" din sheet-ul „CPA și financiar") în `metrics.brand_{meta,google,tiktok}_ad_accounts`,
ca marketingul per brand să fie o singură interogare (și profitability.py să-l tragă automat).

DE CE: numele contului ÎNȘEALĂ (ex. contul Facebook „Esteban 3" e de fapt al **Ofertele Zilei**,
nu Esteban). Singura sursă corectă = tab-ul Mapping. Conturile PARTAJATE (col „Cont multiplu" sau
revendicate de >1 brand) primesc `campaignFilter` = tokenul de campanie (col „Campanie"), altfel
spend-ul se dublu-numără (bug-ul Belasil↔Esteban pe TikTok).

DRY-RUN implicit (nu scrie nimic). `--apply` scrie (după ce validezi dry-run-ul).
  uv run sync_brand_accounts.py                 # dry-run: ce s-ar adăuga + spend-ul afectat
  uv run sync_brand_accounts.py --channel meta  # doar un canal
  uv run sync_brand_accounts.py --apply
"""
import argparse, os, re, subprocess, sys, uuid
from pathlib import Path
import pg8000.dbapi
from google.oauth2 import service_account
from googleapiclient.discovery import build

SS = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"  # „CPA și financiar"
TAB = "Mapping"
C_BRAND, C_FB, C_TT, C_GOOGLE, C_TOKEN, C_MULTI = 0, 1, 2, 4, 5, 6
CH = {  # canal -> (tabel mapare, tabel conturi, col cont id, col nume, col mapare cont, sumă spend)
    "meta":   ("brand_meta_ad_accounts",   "meta_ad_accounts",          "adAccountId",      "name",           "meta_ad_insights_daily",   "spendRon"),
    "google": ("brand_google_ads_accounts","google_ads_customer_accounts","customerAccountId","descriptiveName","google_ads_insights_daily","costRon"),
    "tiktok": ("brand_tiktok_ad_accounts", "tiktok_ad_accounts",        "adAccountId",      "name",           "tiktok_ad_insights_daily", "spendRon"),
}
COLCH = {"meta": C_FB, "google": C_GOOGLE, "tiktok": C_TT}


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _kb(k):
    return subprocess.run(["uv", "run", str(Path(__file__).resolve().parents[3] / "core/scripts/kb.py"),
                           "secret-get", k], capture_output=True, text=True).stdout.strip()


def _sa_creds():
    here = Path(__file__).resolve()
    for up_ in range(0, 7):
        c = here.parents[up_] / "google_credentials.json"
        if c.exists():
            return service_account.Credentials.from_service_account_file(
                str(c), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
                subject="gheorghe.beschea@overheat.agency")
    raw = _kb("GA4_SA_JSON")
    import json
    return service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        subject="gheorghe.beschea@overheat.agency")


def metrics_conn():
    import urllib.parse as up
    url = os.getenv("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"),
                                ssl_context=True)


def load_mapping(svc):
    rows = svc.values().get(spreadsheetId=SS, range=f"'{TAB}'").execute().get("values", [])
    out = []
    for r in rows[1:]:
        r = r + [""] * 7
        brand = r[C_BRAND].strip()
        if not brand:
            continue
        out.append({
            "brand": brand,
            "fb": [x.strip() for x in re.split(r"[,\n]", r[C_FB]) if x.strip()],
            "tt": [x.strip() for x in re.split(r"[,\n]", r[C_TT]) if x.strip()],
            "google": [x.strip() for x in re.split(r"[,\n]", r[C_GOOGLE]) if x.strip()],
            "token": _norm(r[C_TOKEN]).upper().replace(" ", ""),
            "multi": r[C_MULTI].strip(),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", choices=list(CH), help="doar un canal")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-with-spend", action="store_true",
                    help="aplică DOAR conturile dedicate (ne-partajate, token=None) cu spend90>0 — sigure, fără dublu-numărare")
    ap.add_argument("--create-brands", action="store_true",
                    help="creează în tabela `brands` brandurile din Mapping care lipsesc (ca să se poată mapa conturile lor)")
    a = ap.parse_args()
    channels = [a.channel] if a.channel else list(CH)

    svc = build("sheets", "v4", credentials=_sa_creds()).spreadsheets()
    mapping = load_mapping(svc)
    cn = metrics_conn(); cur = cn.cursor()

    # brands metrics: name_norm -> id
    cur.execute("SELECT id, name FROM brands")
    brand_id = {_norm(n): i for i, n in cur.fetchall()}
    cur.execute("SELECT slug FROM brands")
    slugs = {s for (s,) in cur.fetchall()}

    # creează brandurile din Mapping care lipsesc (ca să se poată mapa conturile lor)
    missing_brands = [m["brand"] for m in mapping if _norm(m["brand"]) not in brand_id]
    if missing_brands:
        print(f"\n[BRANDS] lipsesc din metrics: {', '.join(missing_brands)}")
        if a.create_brands and a.apply:
            for bn in missing_brands:
                base = re.sub(r"[^a-z0-9]+", "-", bn.lower()).strip("-") or "brand"
                slug = base; i = 2
                while slug in slugs:
                    slug = f"{base}-{i}"; i += 1
                slugs.add(slug)
                bid = "c" + uuid.uuid4().hex[:24]
                cur.execute('INSERT INTO brands (id, slug, name, "updatedAt") VALUES (%s,%s,%s, now())', [bid, slug, bn])
                brand_id[_norm(bn)] = bid
            cn.commit()
            print(f"  ✅ create {len(missing_brands)} branduri.")
        elif a.create_brands:
            print("  (dry-run — rulează cu --create-brands --apply ca să le creez)")

    # câte branduri revendică fiecare nume de cont (per canal) → partajat dacă >1
    total_adds = 0; total_written = 0
    for ch in channels:
        maptbl, accttbl, idcol, namecol, inscol, spendcol = CH[ch]
        colidx = COLCH[ch]
        # conturi metrics: name_norm -> [(id, name, spend90)]
        cur.execute(f'''SELECT a.id, a."{namecol}",
                        coalesce((SELECT sum(i."{spendcol}") FROM {inscol} i WHERE i."{idcol}"=a.id AND i.date>=current_date-90),0)
                        FROM {accttbl} a''')
        acct_by_name = {}
        for aid, an, sp in cur.fetchall():
            acct_by_name.setdefault(_norm(an), []).append((aid, an, float(sp or 0)))
        # mapări existente: (brandId, accountId) -> campaignFilter curent
        cur.execute(f'SELECT "brandId", "{idcol}", "campaignFilter" FROM {maptbl} WHERE "isActive"')
        existing = {(b, ac): cf for b, ac, cf in cur.fetchall()}
        # numărător de revendicări per nume cont (pt detectarea partajării)
        claims = {}
        for m in mapping:
            for nm in m[{"meta": "fb", "google": "google", "tiktok": "tt"}[ch]]:
                claims[_norm(nm)] = claims.get(_norm(nm), 0) + 1

        print(f"\n{'='*78}\n[{ch.upper()}] mapări lipsă (din Mapping → {maptbl})\n{'='*78}")
        adds = []; updates = []; unmatched = []
        for m in mapping:
            bid = brand_id.get(_norm(m["brand"]))
            names = m[{"meta": "fb", "google": "google", "tiktok": "tt"}[ch]]
            for nm in names:
                accts = acct_by_name.get(_norm(nm))
                if not accts:
                    unmatched.append((m["brand"], nm)); continue
                shared = claims.get(_norm(nm), 0) > 1          # token DOAR pe contul multi-revendicat (nu pe flag-ul brandului)
                for aid, an, sp in accts:
                    if not bid:
                        unmatched.append((m["brand"], f"{nm} (brand negăsit în metrics)")); continue
                    token = m["token"] if shared else None
                    if (bid, aid) in existing:
                        if (existing[(bid, aid)] or None) != (token or None):   # filtru greșit → corectează
                            updates.append((m["brand"], bid, an, aid, token, existing[(bid, aid)]))
                        continue
                    adds.append((m["brand"], bid, an, aid, token, sp))
        for brand, bid, an, aid, token, sp in sorted(adds, key=lambda x: -x[5]):
            tag = f"  [token={token}]" if token else ""
            print(f"  + {brand:18} ← cont '{an}'  spend90={sp:,.0f} RON{tag}")
        for brand, bid, an, aid, token, oldf in updates:
            print(f"  ~ {brand:18} cont '{an}': campaignFilter {oldf or 'NULL'} → {token or 'NULL'}")
        total_adds += len(adds)
        if unmatched:
            print(f"  — neasociate (cont în Mapping dar negăsit în metrics / brand lipsă): "
                  + ", ".join(f"{b}:{n}" for b, n in unmatched[:12]) + (" …" if len(unmatched) > 12 else ""))

        if a.apply:
            to_write = [x for x in adds if (x[4] is None and x[5] > 0)] if a.only_with_spend else adds
            for brand, bid, an, aid, token, sp in to_write:
                nid = "c" + uuid.uuid4().hex[:24]
                cur.execute(f'''INSERT INTO {maptbl} (id,"brandId","{idcol}","campaignFilter","isActive","updatedAt")
                                VALUES (%s,%s,%s,%s,true, now())''', [nid, bid, aid, token])
            nfix = 0
            if not a.only_with_spend:                          # corectează filtrele greșite pe rândurile existente
                for brand, bid, an, aid, token, oldf in updates:
                    cur.execute(f'UPDATE {maptbl} SET "campaignFilter"=%s, "updatedAt"=now() '
                                f'WHERE "brandId"=%s AND "{idcol}"=%s AND "isActive"', [token, bid, aid])
                nfix = len(updates)
            cn.commit(); total_written += len(to_write)
            print(f"  ✅ APLICAT: {len(to_write)} inserate"
                  + (f", {nfix} filtre corectate" if nfix else "")
                  + (" (doar dedicate cu spend)" if a.only_with_spend else "") + f" în {maptbl}.")

    if a.apply:
        rest = total_adds - total_written
        tail = (f" {rest} rămân nescrise (filtrate de --only-with-spend)." if rest else
                " Marketingul se populează pe măsură ce sync-ul de insights prinde conturile (multe au încă 0).")
        print(f"\nAPLICAT — {total_written} mapări inserate (din {total_adds} candidate).{tail}")
    else:
        print(f"\nDRY-RUN — {total_adds} mapări candidate. Sigure de aplicat acum (dedicate + cu spend): "
              f"rulează cu `--apply --only-with-spend`. Restul: vezi nota despre token-aware/sync.")
    cn.close()


if __name__ == "__main__":
    main()
