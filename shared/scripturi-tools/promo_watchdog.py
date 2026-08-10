"""
promo_watchdog.py — watchdog pentru PROMOȚIILE de parfumuri (surpriză + 2+1) pe EST/GT/NUB/LAB.

De ce: „surpriza" = Shopify Flow (order edit post-checkout), „2+1" = discount nativ Buy X Get Y —
ambele „setează și uită", NIMIC nu verifică că s-au aplicat. Au trecut tăcut:
  • EST228384 — surpriza NU s-a adăugat: Flow-ul numără cutiile cadou (`cutie-cadou`) ca produse →
    crede că-s ≥3 → sare surpriza (0/4 comenzi Esteban cu cutie au surpriză).
  • LAB3107 — 2+1 nu s-a aplicat: Blend No. 81 (81-50ml/81-100ml) LIPSEA din lista discountului →
    din 3 parfumuri doar 2 eligibile → „buy 2 get 1" nu s-a declanșat.

3 verificări (read-only, live Shopify):
  1. COMPLETITUDINE discount — lista de produse a fiecărui „2+1" vs catalogul de parfumuri →
     variante NEadăugate (ar fi prins Blend 81 ÎNAINTE de a pierde o comandă). PROACTIV.
  2. SURPRIZĂ lipsă — comenzi ELIGIBILE (2 parfumuri) PLASATE DE CLIENT (nu draft/CS) fără `surpriza`.
  3. 2+1 lipsă — comenzi cu 3+ parfumuri de ACEEAȘI mărime fără discountul BxGy aplicat.

Reguli confirmate de owner:
  • Surpriza se adaugă DOAR la comenzile plasate de CLIENT (Flow „Order created"), NU la draft/CS.
  • DOAR LabNoir are mărimi 50ml/100ml; 2+1 se aplică pe ACEEAȘI mărime (3× 50ml → „2+1 50ml").
    Celelalte (EST/GT/NUB) au o singură mărime.
  • Surpriza există pe EST/GT/NUB (Flow); LAB = auto-detectat (dacă vreo comandă recentă are `surpriza`).

Email DOAR pe abateri NOI (state file), pe același drum ca data_health (Gmail SA domain-wide).

  .venv/bin/python promo_watchdog.py                        # doar raport în consolă
  .venv/bin/python promo_watchdog.py --email X --key ...    # + email pe abateri NOI
  .venv/bin/python promo_watchdog.py --days 5 --always      # fereastră + forțează email
"""
import os, re, sys, json, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import xconnector as xc

STORES = ["EST", "GT", "NUB", "LAB"]
DUAL_SIZE = {"LAB"}                 # 2+1 per-mărime (50ml/100ml); restul = o singură mărime
SURPRISE_STORES = {"EST", "GT", "NUB"}   # Flow-ul de surpriză confirmat aici; LAB = auto-detect
SURPRISE_QTY = 2                    # oferta „2+1": exact 2 parfumuri → +1 surpriză (1=fără ofertă, 3+=2+1 nativ)
STATE = os.environ.get("PROMO_WATCHDOG_STATE", "/root/Scripturi/data/.promo_watchdog_seen.json")
OK, CRIT = "OK", "CRIT"
DRAFT_SOURCES = {"shopify_draft_order"}   # comenzi făcute de CS (draft) — Flow NU se declanșează → NU le semnala


def gql(st, q):
    return xc.shopify_gql(st["shopDomain"], st["adminToken"], q)


def _size_of(sku):
    """Mărimea din SKU (orice format): '15-50ml'→'50ml', '81-100ml'→'100ml', 'gt-35'/'33'→'one'."""
    s = (sku or "").strip().lower()
    if s.endswith("-50ml"):
        return "50ml"
    if s.endswith("-100ml"):
        return "100ml"
    return "one"


def _is_surprise(sku, title):
    """Surpriza e robustă pe TITLU (EST='surpriza-*', NUB=sku numeric '122' cu titlu 'Parfum surpriză')."""
    return "surpriz" in (title or "").lower() or (sku or "").strip().lower().startswith("surpriza")


def _is_giftbox(sku, title):
    return (sku or "").strip().lower() == "cutie-cadou" or "cutie cadou" in (title or "").lower()


def _disc_size(title):
    """Mărimea unui discount 2+1 din titlu: '... 50 ml'→'50ml', '... 100 ml'→'100ml', altfel 'one'."""
    t = (title or "").lower()
    if "100" in t:
        return "100ml"
    if "50" in t:
        return "50ml"
    return "one"


def load_discounts(st):
    """{titlu: {'size':..., 'type':'products|collections|all', 'skus':set()}} pt BxGy (2+1) active.
    `type` contează: DOAR 'products' (listă fixă, ex LabNoir) poate avea variante LIPSĂ. 'collections'/'all'
    (ex EST/NUB pe 'Toate parfumurile') se auto-completează → completitudinea NU se aplică."""
    q = ('query{ automaticDiscountNodes(first:30){ edges{ node{ automaticDiscount{ __typename '
         '... on DiscountAutomaticBxgy{ title status customerBuys{ items{ __typename '
         '... on DiscountProducts{ productVariants(first:250){ edges{ node{ sku } } } } '
         '... on DiscountCollections{ collections(first:1){ edges{ node{ id } } } } '
         '... on AllDiscountItems{ allItems } } } } } } } } }')
    d = gql(st, q)
    out = {}
    for e in (((d.get("data") or {}).get("automaticDiscountNodes") or {}).get("edges") or []):
        ad = e["node"]["automaticDiscount"]
        if ad.get("__typename") != "DiscountAutomaticBxgy" or ad.get("status") != "ACTIVE":
            continue
        t = ad.get("title") or "?"
        it = ((ad.get("customerBuys") or {}).get("items") or {})
        typ = {"DiscountProducts": "products", "DiscountCollections": "collections",
               "AllDiscountItems": "all"}.get(it.get("__typename"), "?")
        skus = set(v["node"]["sku"] for v in (it.get("productVariants") or {}).get("edges") or [] if v["node"].get("sku"))
        out[t] = {"size": _disc_size(t), "type": typ, "skus": skus}
    return out


def load_catalog(st):
    """{size: set(sku)} — variantele de parfum din colecția 'Toate parfumurile'."""
    cat = {}
    cursor = None
    for _ in range(12):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ products(first:100, query:"collection_title:\'Toate parfumurile\'"%s){ '
             'pageInfo{ hasNextPage endCursor } edges{ node{ variants(first:12){ edges{ node{ sku } } } } } } }' % after)
        r = gql(st, q)
        pr = (r.get("data") or {}).get("products") or {}
        for e in pr.get("edges") or []:
            for v in (e["node"].get("variants") or {}).get("edges") or []:
                sku = (v["node"].get("sku") or "").strip()
                if sku:
                    cat.setdefault(_size_of(sku), set()).add(sku)   # perfum = orice variantă din 'Toate parfumurile'
        if not pr.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = pr["pageInfo"]["endCursor"]
    return cat


def recent_orders(st, days):
    q = ('query{ orders(first:120, sortKey:CREATED_AT, reverse:true, query:"created_at:>%s AND status:any"){ '
         'edges{ node{ name createdAt cancelledAt tags sourceName '
         'discountApplications(first:10){ edges{ node{ __typename '
         '... on AutomaticDiscountApplication{ title } ... on DiscountCodeApplication{ code } } } } '
         'lineItems(first:30){ edges{ node{ sku title quantity } } } } } } }'
         % _since(days))
    d = gql(st, q)
    return [e["node"] for e in (((d.get("data") or {}).get("orders") or {}).get("edges") or [])]


def _since(days):
    # Nu putem folosi datetime.now() în workflow, dar acesta e un script normal → import local ok.
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- checks

def check_store(store, days, rows):
    toks = {t["prefix"]: t for t in xc.load_shopify_tokens()}
    st = toks.get(store)
    if not st:
        rows.append((CRIT, "%s.token" % store, "fără token Shopify")); return
    discounts = load_discounts(st)
    catalog = load_catalog(st)
    perfume_skus = set().union(*catalog.values()) if catalog else set()   # toate variantele de parfum (orice format SKU)
    disc_skus = set()
    disc_is_products = False   # LabNoir = listă fixă → completitudinea contează; colecție = auto-complet
    for t, dd in discounts.items():
        disc_skus |= dd["skus"]
        if dd["type"] == "products":
            disc_is_products = True

    # ---- Check 1: COMPLETITUDINE discount — DOAR pt discounturi pe listă de PRODUSE (ex LabNoir)
    if not discounts:
        rows.append((CRIT, "%s.2+1" % store, "NICIUN discount BxGy 2+1 activ (verifică că oferta e pornită)"))
    for t, dd in discounts.items():
        if dd["type"] != "products":
            continue   # colecție/toate = auto-complet, nimic nu poate „lipsi"
        universe = catalog.get(dd["size"], set()) if dd["size"] != "one" else perfume_skus
        missing = sorted(universe - dd["skus"])
        if missing:
            rows.append((CRIT, "%s.disc.%s" % (store, _slug(t)),
                         "%d parfumuri LIPSESC din „%s" % (len(missing), t) + "\" → clienții nu primesc 2+1: " + ", ".join(missing[:20])))

    # ---- comenzi recente (pt check 2 + 3)
    orders = recent_orders(st, days)
    surprise_on = store in SURPRISE_STORES or any(
        _is_surprise(li["node"].get("sku"), li["node"].get("title"))
        for o in orders for li in (o.get("lineItems") or {}).get("edges") or [])

    for o in orders:
        if o.get("cancelledAt"):
            continue
        name = o["name"]
        tags = [str(x).lower() for x in (o.get("tags") or [])]
        lis = [li["node"] for li in (o.get("lineItems") or {}).get("edges") or []]
        # unități de parfum pe mărime (parfum = SKU în catalog; surpriza/cutie excluse)
        by_size = {}
        has_surp = False
        for li in lis:
            sku = (li.get("sku") or "").strip()
            if _is_surprise(sku, li.get("title")):
                has_surp = True; continue
            if sku in perfume_skus and not _is_giftbox(sku, li.get("title")):
                by_size[_size_of(sku)] = by_size.get(_size_of(sku), 0) + (li.get("quantity") or 0)
        total_perf = sum(by_size.values())
        has_box = any(_is_giftbox(li.get("sku"), li.get("title")) for li in lis)
        # discount 2+1 aplicat pe comandă?
        disc_titles = [(e["node"].get("title") or e["node"].get("code") or "").lower()
                       for e in (o.get("discountApplications") or {}).get("edges") or []]
        has_bxgy = any("2+1" in t or "2 plus 1" in t or "cadou" in t or "gratis" in t for t in disc_titles)
        is_draft = (o.get("sourceName") or "") in DRAFT_SOURCES

        # ---- Check 2: SURPRIZĂ lipsă (DOAR client, nu draft/CS; exact 2 parfumuri; nu tagat farasurpriza)
        if surprise_on and total_perf == SURPRISE_QTY and not has_surp and not is_draft \
           and "farasurpriza" not in tags:
            rows.append((CRIT, "%s.surp.%s" % (store, name),
                         "%s: 2 parfumuri, plasată de client%s, FĂRĂ surpriză" % (name, " + cutie cadou" if has_box else "")))

        # ---- Check 3: 2+1 lipsă (3+ parfumuri de ACEEAȘI mărime, fără discount aplicat) — DOAR client, nu draft/CS
        if not has_bxgy and not is_draft:
            for size, qty in by_size.items():
                if qty >= 3:
                    # dacă discountul e pe LISTĂ de produse (LabNoir), poate un produs lipsește din listă
                    if disc_is_products:
                        offlist = sorted(set(li.get("sku") for li in lis
                                   if (li.get("sku") in perfume_skus) and _size_of(li.get("sku")) == size
                                   and li.get("sku") not in disc_skus))
                        why = (" (produs neinclus în discount: %s → vezi completitudine)" % ", ".join(offlist)) if offlist \
                              else " (toate în listă — discount NEaplicat, verifică oferta)"
                    else:
                        # discount pe COLECȚIE (EST/NUB/GT) → toate parfumurile-s incluse; neaplicat = alt motiv
                        why = " (discount pe colecție%s → NEaplicat, ex. cutii cadou blochează)" % (" + cutie cadou" if has_box else "")
                    rows.append((CRIT, "%s.2+1.%s.%s" % (store, name, size),
                                 "%s: %d parfumuri %s fără 2+1%s" % (name, qty, size if size != "one" else "", why)))


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:24]


# ---------------------------------------------------------------- email + state

def _load_seen():
    try:
        return set(json.load(open(STATE)))
    except Exception:
        return set()


def _save_seen(keys):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(sorted(keys), open(STATE, "w"))
    except Exception:
        pass


def _send_email(to, subject, body, key, sender):
    """Identic cu data_health.py — Gmail API, service account cu delegare domain-wide."""
    import base64
    from email.mime.text import MIMEText
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/gmail.modify"]).with_subject(sender)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEText(body, _charset="utf-8")
    msg["to"] = to; msg["from"] = sender; msg["subject"] = subject
    svc.users().messages().send(
        userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()


def main():
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="Watchdog promoții parfumuri (surpriză + 2+1)")
    ap.add_argument("--days", type=int, default=3, help="fereastra de comenzi verificate (default 3)")
    ap.add_argument("--stores", help="listă prefixe (default EST,GT,NUB,LAB)")
    ap.add_argument("--email", help="destinatar (email trimis DOAR pe abateri NOI)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    ap.add_argument("--always", action="store_true", help="email chiar dacă nimic nou")
    a = ap.parse_args()

    stores = [s.strip().upper() for s in (a.stores.split(",") if a.stores else STORES) if s.strip()]
    rows = []
    for store in stores:
        try:
            check_store(store, a.days, rows)
        except Exception as e:
            rows.append((CRIT, "%s.check" % store, "checkul a crăpat: %s: %s" % (type(e).__name__, str(e)[:120])))

    bad = [r for r in rows if r[0] == CRIT]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    out = ["PROMO WATCHDOG ARONA — %s UTC (fereastră %dz)" % (now, a.days), ""]
    if bad:
        out.append("🔴 ABATERI (%d)" % len(bad))
        out += ["  %-26s %s" % (k, d) for _, k, d in bad]
    else:
        out.append("🟢 Totul ok — discounturi complete, surpriza + 2+1 aplicate corect.")
    out += ["", "Reguli: surpriza DOAR la comenzi client (nu draft/CS); 2+1 pe aceeași mărime (LabNoir 50/100ml)."]
    report = "\n".join(out)
    print(report)

    # email DOAR pe abateri NOI (chei nevăzute)
    seen = _load_seen()
    cur_keys = set(k for _, k, _ in bad)
    new_keys = cur_keys - seen
    _save_seen(cur_keys)   # baseline = abaterile curente (rezolvate uitate; recurențele re-alertează)
    if a.email and (new_keys or a.always):
        sev = "🔴 %d abateri noi" % len(new_keys) if new_keys else "🟢 fără nou"
        new_report = report if new_keys else report + "\n\n(--always: nimic nou)"
        try:
            _send_email(a.email, "[promo-watchdog] ARONA — %s" % sev, new_report, a.key, a.sender)
            print("\n[email] trimis către %s (%d abateri noi)" % (a.email, len(new_keys)))
        except Exception as e:
            print("\n[email] EȘUAT: %s: %s" % (type(e).__name__, e))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
