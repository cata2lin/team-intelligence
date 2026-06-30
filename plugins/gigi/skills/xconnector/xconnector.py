# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000", "pypdf"]
# ///
"""
xconnector.py — punte READ-ONLY spre xConnector (curierat) pt fluxul ARONA.

Ce poate AZI (prin cheia API, durabil):
  • address-issues — comenzile NEPORNITE (fără AWB) cu adresă WRONG/UNKNOWN la xConnector,
    cu adresa curentă + ce zice validatorul (candidat + scor) + verdict auto/manual.
    = semnal de 'confirmă/corectează adresa ÎNAINTE de AWB" (prevenție refuzuri), pereche cu gigi:cs-address-guard.
  • summary — câte comenzi pe fiecare status, câte fără AWB, per magazin.

API DE SCRIERE — EXPUS din 2026-06-24 (docs: https://xconnector.app/api-docs.html ; spec: /api-spec.yaml).
Creare AWB / dispatch / facturi NU mai sunt dashboard-only. Endpoint-uri sync: POST /api/actions/
create-shipping-label, cancel-shipping-label, dispatch-order, estimate-shipping-price, create-invoice
(+ create-invoice-payment/cancel-invoice/revert-invoice), locker-notification; POST /api/v1/picking-lists/
add-order; GET /api/orders/by-tracking-number. BLOCAJ real: toate /api/actions/* + ai-correct-address cer
rolul ROLE_AUTOMATION pe merchant + permisiuni per-cheie (API_CREATE_SHIPPING_LABEL etc.) — fără ele = 403.
Pe GT (ix5bxc-hr) ROLE_AUTOMATION e încă DE ACTIVAT de vendor; până atunci AWB-ul rămâne pe Shopify Flow.

LECȚIE VALIDARE (descoperit 2026-06-24): addressStatus WRONG/UNKNOWN SUPRA-flaghează. Validarea e
asincronă/în batch — comenzi stau WRONG/UNKNOWN ore→1 zi, apoi un sweep al xConnector le trece pe VALID fără
editare (~16% se auto-vindecă). WRONG NU e predictor de eșec la livrare (pe un eșantion, 6/8 colete cu adresă
WRONG s-au livrat). → nu trata un flag proaspăt ca problemă reală: rulează `correct --min-age-hours N` (sare
comenzile mai noi de N ore, lasă sweep-ul lor să ruleze) și `recheck` (vezi care s-au auto-validat) înainte
de a deranja CS-ul.

Auth: cheia API xConnector per magazin. Sursă (în ordine): secret KB `XCONNECTOR_SHOPS` (JSON
[{shopDomain,apiKey}]), altfel `~/.aac/input.json`. NICIODATĂ printată.

  uv run xconnector.py summary
  uv run xconnector.py address-issues [--shop ix5bxc-hr.myshopify.com] [--days 60] [--json]
  uv run xconnector.py recheck [--order GT123,GT456] [--days 30]   # care s-au auto-validat (VALID/PERFECT)
Read-only pe xConnector (recheck/issues/summary nu scriu nimic; `correct` scrie corecții de adresă cu --apply).
"""
import os, sys, json, re, time, hashlib, argparse, subprocess, urllib.parse, urllib.request, urllib.error

# Windows (depozit + mașinile CS): consola e cp1252 → forțez UTF-8 DIN PRIMA, ca să NU crape pe
# diacriticele românești (ț/ș/ă/î/â) sau pe caracterele „═ → ⚠️ ✅". errors=replace = niciodată crash.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
XBASE = "https://xconnector.app"
VBASE = "https://address-validator.xconnector.app"


KB_UNREACHABLE = False  # True când un apel KB EȘUEAZĂ (KB_DATABASE_URL greșit/stale ≠ 'secret lipsă')
_KB_WARNED = False


def _kb_secret(key):
    """(value, ok). Setează KB_UNREACHABLE DOAR la eșec de CONEXIUNE la KB (KB_DATABASE_URL lipsă/greșit → kb.py
    iese 3, sau eroare psycopg2). NU la 'secret absent' (kb.py iese 1 cu „secret '...' is not set" — KB e ok),
    ca să NU strige fals 'KB inaccesibil' când doar lipsește un secret."""
    global KB_UNREACHABLE
    try:
        r = subprocess.run(["uv", "run", KB, "secret-get", key], capture_output=True, text=True, timeout=30)
    except Exception:
        KB_UNREACHABLE = True
        return "", False
    if r.returncode == 0:
        return r.stdout.strip(), True
    err = (r.stderr or "").lower()
    if r.returncode == 3 or any(t in err for t in (
            "could not connect", "could not translate", "connection refused", "could not receive",
            "operationalerror", "psycopg2", "timeout expired", "no route to host", "server closed")):
        KB_UNREACHABLE = True   # conexiune picată / URL greșit — NU 'secret absent'
    return "", False


def warn_kb_if_unreachable():
    """Avertisment UNIC, vizibil, când KB e inaccesibil — ca să NU confunzi cu 'comandă/date inexistente'."""
    global _KB_WARNED
    if KB_UNREACHABLE and not _KB_WARNED:
        _KB_WARNED = True
        print("⚠️ KB INACCESIBIL — n-am putut citi cheile din SharedClaude (verifică KB_DATABASE_URL; host "
              "corect: 38.242.226.83:5432/SharedClaude). Asta NU înseamnă că o comandă/date 'nu există' — "
              "e o problemă de credențiale/conexiune (vezi memoria kb-stale-cache).")


def load_shops():
    """[{shopDomain, apiKey}] din KB (XCONNECTOR_SHOPS) sau ~/.aac/input.json. Secret — nu se printează."""
    raw = os.environ.get("XCONNECTOR_SHOPS")
    if not raw:
        raw, ok = _kb_secret("XCONNECTOR_SHOPS")
        if not ok:
            warn_kb_if_unreachable()
    if raw and raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            pass
    p = os.path.expanduser("~/.aac/input.json")
    if os.path.exists(p):
        return json.load(open(p)).get("shops", [])
    return []


def http(method, url, headers, body=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return "ERR", str(e)[:160]


class XC:
    def __init__(self, apikey):
        self.h = {"Authorization": "Bearer " + apikey, "Content-Type": "application/json"}
        self.vtok = None

    def get(self, path, q=""):
        s, b = http("GET", XBASE + path + (("?" + q) if q else ""), self.h)
        try:
            return s, json.loads(b)
        except Exception:
            return s, b

    def orders(self, dfrom, dto, filters=None):
        """toate comenzile în fereastră (paginat), cu addressStatus + documents.
        `filters` = dict opțional cu filtrele server-side getOrders (xConnector, adăugate 2026-06):
          - sku=<str|list>          potrivire EXACTĂ; listă → param repetat (?sku=A&sku=B)
          - skuMode='ANY'|'ALL'     cum se combină mai multe sku (implicit ANY)
          - excludeSku=<str|list>   exclude comenzile cu SKU-ul (cere un filtru pozitiv alături)
          - totalItemsCount=<str>   nr TOTAL bucăți (CSV permis, ex '1' sau '1,2')
          - lineItemsCount=<str>    nr LINII (CSV permis)
          - sort='sku'|'totalItemsCount'|'lineItemsCount'|'date'|'fulfillmentDate'
          - sortDir='asc'|'desc'    (implicit desc)
        Valorile None/''/[] sunt ignorate. Listele → param repetat; restul → o singură valoare."""
        base = [("fromOrderDate", dfrom), ("toOrderDate", dto)]
        for k, v in (filters or {}).items():
            if v is None or v == "" or v == []:
                continue
            if isinstance(v, (list, tuple)):
                base += [(k, str(x)) for x in v if x not in (None, "")]
            else:
                base.append((k, str(v)))
        out, seen = [], set()
        MAXP = 1000   # plafon de SIGURANȚĂ anti-buclă (200k comenzi) — fereastra reală se epuizează mult înainte; dacă SE atinge → avertizez (zero trunchiere tăcută)
        page = 0
        while page < MAXP:
            q = urllib.parse.urlencode(base + [("page", str(page)), ("size", "200")])
            s = d = None
            for attempt in range(8):   # REÎNCEARCĂ pagina pe throttle/timeout/5xx — altfel scanare PARȚIALĂ tăcută
                s, d = self.get("/api/orders", q)
                if s == 200 or s == 400:   # 200 ok; 400 = DETERMINIST (plafon de offset / cerere proastă) → nu retry
                    break
                # 429 = rate-limit xConnector (poate ține ~1-2 min) → backoff LUNG ca să treacă peste spike;
                # timeout/5xx → backoff scurt. Răbdarea totală (~6 min) împiedică sărirea unui magazin pe un blip.
                time.sleep(min((15 * (attempt + 1)) if s == 429 else (3 * (attempt + 1)), 90))
            if s != 200:
                if s == 400 and len(out) >= 9000:
                    # PLAFONUL DE OFFSET al xConnector: pagina 50 (offset 10000) întoarce 400 — NU e eroare, e CAPUL.
                    # Ieșire GRAȚIOASĂ; len(out)≈10000 declanșează bisecția pe dată în _scan_all_orders (prinde restul).
                    break
                # picată definitiv (throttle persistent SAU 400 la offset mic = cerere proastă) → NU returna tăcut
                # parțial (ar subnumăra masiv, ex Ofertele 2600 în loc de ~13000). Ridică, apelantul sare/reia.
                raise RuntimeError("xConnector getOrders a picat la pagina %d (%s→%s, status %s) după retries" % (page, dfrom, dto, s))
            arr = d if isinstance(d, list) else (d.get("content") or d.get("orders") or [])
            if not arr:
                break
            added = 0
            for o in arr:
                oid = o.get("orderId")
                if oid not in seen:
                    seen.add(oid); out.append(o); added += 1
            if len(arr) < 200 or added == 0:   # pagină incompletă SAU API repetă (zero noi) = epuizat
                break
            page += 1
        else:   # am ieșit prin plafon, NU prin epuizare → posibil trunchiat
            sys.stderr.write("  ⚠️ paginare oprită la plafonul de %d pagini (%s→%s) — POSIBIL TRUNCHIAT, restrânge fereastra\n" % (MAXP, dfrom, dto))
        return out

    def by_id(self, oid):
        s, d = self.get("/api/orders/by-id", "orderId=%s" % oid)
        return d if s == 200 and isinstance(d, dict) else {}

    def post(self, path, body):
        """POST /api/actions/* sau alt endpoint de scriere. Întoarce (status, json|text)."""
        s, b = http("POST", XBASE + path, self.h, body)
        try:
            return s, json.loads(b)
        except Exception:
            return s, b

    def list_connectors(self):
        # cache pe instanță + retry: un blip pe acest call NU trebuie să facă un magazin întreg
        # să pară „connector ambiguu/absent" și să-l sară (s-a întâmplat la EST în runul de 60z).
        if getattr(self, "_conn_cache", None):
            return self._conn_cache
        for attempt in range(4):
            s, d = self.get("/api/merchant/connectors")
            if s == 200 and isinstance(d, list) and d:
                self._conn_cache = d
                return d
            time.sleep(1.5 * (attempt + 1))
        return []

    def vtoken(self):
        if not self.vtok:
            s, d = http("POST", XBASE + "/api/token", self.h)
            try:
                self.vtok = json.loads(d).get("accessToken")
            except Exception:
                self.vtok = None
        return self.vtok

    def match(self, addr):
        h = {"Content-Type": "application/json"}
        t = self.vtoken()
        if t:
            h["Authorization"] = "Bearer " + t
        s, b = http("POST", VBASE + "/match-address", h, addr)
        try:
            return json.loads(b)
        except Exception:
            return []


# ── Shopify Admin (declanșează Shopify Flow → acțiunea xConnector create/cancel AWB) ──
# Mecanism: comenzile noi stau pe FULFILLMENT HOLD (Flow Order-created->Hold). Noi eliberăm
# hold-ul DOAR la comenzile sigure → Flow Fulfillment-hold-released -> xConnector Create AWB.
SHOPIFY_API = "2026-04"


def _stores_csv_tokens():
    """[{prefix, shopDomain, adminToken}] din SHOPIFY_STORES_CSV (canonic, TOATE magazinele; col prefix/shop/token).
    Sursă: env SHOPIFY_STORES_CSV (path sau text) sau KB. NUB = OAuth-rotation (token static mort, merge pe VPS).
    Pe VPS fără uv/KB → întoarce [] grațios (cron-ul folosește env SHOPIFY_ADMIN_TOKENS)."""
    import csv, io
    raw = os.environ.get("SHOPIFY_STORES_CSV") or ""
    if raw and "\n" not in raw and os.path.exists(raw):
        try:
            raw = open(raw, encoding="utf-8-sig").read()
        except Exception:
            raw = ""
    if not raw or "\n" not in raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_STORES_CSV"],
                                 capture_output=True, text=True, timeout=40).stdout
        except Exception:
            raw = ""
    out = []
    try:
        for row in csv.DictReader(io.StringIO(raw)):
            pref = (row.get("prefix") or "").strip().lstrip("﻿").upper()
            shop = (row.get("shop") or "").strip().replace("https://", "").strip("/")
            tok = (row.get("token") or "").strip()
            if pref and shop and tok:
                out.append({"prefix": pref, "shopDomain": shop, "adminToken": tok})
    except Exception:
        pass
    return out


def load_shopify_tokens():
    """[{prefix, shopDomain, adminToken}] pt TOATE magazinele: bază din SHOPIFY_STORES_CSV (canonic),
    suprascris de SHOPIFY_ADMIN_TOKENS (env/KB) pt override-uri/tokenuri proaspete. NU se printează."""
    by_dom = {t["shopDomain"]: t for t in _stores_csv_tokens()}
    raw = os.environ.get("SHOPIFY_ADMIN_TOKENS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_ADMIN_TOKENS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
    try:
        for t in (json.loads(raw) if raw.startswith("[") else []):
            if t.get("shopDomain") and t.get("adminToken"):
                by_dom[t["shopDomain"]] = t
    except Exception:
        pass
    return list(by_dom.values())


def shopify_gql(shop, token, query, variables=None):
    url = "https://%s/admin/api/%s/graphql.json" % (shop, SHOPIFY_API)
    h = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    body = {"query": query}
    if variables is not None:
        body["variables"] = variables
    d = None
    for attempt in range(6):
        s, b = http("POST", url, h, body)
        try:
            d = json.loads(b)
        except Exception:
            return {"_status": s, "_raw": b[:200]}
        errs = d.get("errors") or []
        throttled = (s == 429) or any(
            isinstance(e, dict) and (e.get("extensions") or {}).get("code") == "THROTTLED"
            for e in errs
        )
        if throttled:
            time.sleep(2 * (attempt + 1))
            continue
        # POLITICOS cu rația Shopify: bucket-ul GraphQL e PER-token de app, partajat cu
        # celelalte aplicații ARONA care lovesc același magazin. Lăsăm mereu ≥50% din
        # bucket liber pentru ele — ne oprim singuri când scădem sub jumătate, până se
        # reumple la ~60% (la restoreRate-ul magazinului).
        ts = (((d.get("extensions") or {}).get("cost") or {}).get("throttleStatus")) or {}
        avail = ts.get("currentlyAvailable")
        maxb = ts.get("maximumAvailable") or 0
        restore = ts.get("restoreRate") or 50
        if avail is not None and maxb and avail < maxb * 0.5:
            need = (maxb * 0.6 - avail) / max(restore, 1)
            time.sleep(min(max(need, 0.0), 8.0))
        return d
    return d


def find_order(shop, token, name):
    """nodul comenzii + fulfillmentOrders (id+status), după orderName (ex GT44004)."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ id name displayFulfillmentStatus '
         'fulfillmentOrders(first:10){ edges{ node{ id status } } } } } } }') % name.replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return edges[0]["node"] if edges else None


def shopify_order_tags(name, toks):
    """tagurile comenzii Shopify (lower-case), după orderName (ex GT43675). [] dacă n-o găsesc."""
    pm = re.match(r"^([A-Za-z]+)", name or "")
    t = toks.get(pm.group(1).upper()) if pm else None
    if not t:
        return []
    q = 'query{ orders(first:1, query:"name:%s"){ edges{ node{ tags } } } }' % (name or "").replace('"', "")
    d = shopify_gql(t["shopDomain"], t["adminToken"], q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return [str(x).lower() for x in (edges[0]["node"].get("tags") or [])] if edges else []


def shopify_order_id(name, st):
    """Shopify order legacyResourceId (= orderId xConnector) după orderName. None dacă nu există / fără token.
    Folosit ca FALLBACK de lookup când comanda e în afara ferestrei de scan xConnector (veche / volum mare)."""
    if not st or not name:
        return None
    q = 'query{ orders(first:1, query:"name:%s"){ edges{ node{ legacyResourceId } } } }' % name.replace('"', "")
    d = shopify_gql(st["shopDomain"], st["adminToken"], q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return edges[0]["node"].get("legacyResourceId") if edges else None


def shopify_release_holds(shop, token, name):
    """Eliberează HOLD-urile de fulfillment ale comenzii (ca să se poată face AWB). (n_eliberate, [motive])."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ fulfillmentOrders(first:10){ edges{ node{ '
         'id status fulfillmentHolds{ reason } } } } } } } }') % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return 0, []
    fos = ((edges[0]["node"].get("fulfillmentOrders") or {}).get("edges")) or []
    # NU elibera hold-uri LEGITIME (fraudă/stoc/plată) — alea NU trebuie expediate automat.
    protected = {"HIGH_RISK_OF_FRAUD", "INVENTORY_OUT_OF_STOCK", "AWAITING_PAYMENT"}
    released, reasons, skipped = 0, [], []
    for fo in fos:
        n = fo["node"]
        if n.get("status") != "ON_HOLD":
            continue
        fo_reasons = [h.get("reason") for h in (n.get("fulfillmentHolds") or [])]
        if any(r in protected for r in fo_reasons):
            skipped += [r for r in fo_reasons if r in protected]
            continue  # hold legitim → îl las (NU fac AWB peste fraudă/stoc/plată)
        m = ('mutation{ fulfillmentOrderReleaseHold(id:"%s"){ fulfillmentOrder{ status } userErrors{ message } } }') % n["id"]
        r = shopify_gql(shop, token, m)
        errs = (((r.get("data") or {}).get("fulfillmentOrderReleaseHold") or {}).get("userErrors")) or []
        if not errs:
            released += 1
            reasons += [h.get("reason") for h in (n.get("fulfillmentHolds") or []) if h.get("reason")]
    return released, reasons, skipped


def cmd_awb(a):
    """create = ELIBERează hold-ul (→ Flow hold-released -> xConnector Create AWB);
    hold = pune fulfillment-ul în hold; cancel = info (fără trigger de tag)."""
    action = a.cmd.split("-")[1]  # create | cancel | hold
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    pm = re.match(r"^([A-Za-z]+)", a.order)
    pref = pm.group(1).upper() if pm else ""
    sh = toks.get(pref)
    if not sh:
        print("Niciun token Shopify pt prefixul '%s' (am: %s). Adaugă în KB SHOPIFY_ADMIN_TOKENS." % (pref, list(toks))); return
    shop, token = sh["shopDomain"], sh["adminToken"]
    node = find_order(shop, token, a.order)
    if not node:
        print("Comanda %s negăsită în Shopify (%s)." % (a.order, shop)); return
    fos = [e["node"] for e in ((node.get("fulfillmentOrders") or {}).get("edges") or [])]
    print("Comandă %s | fulfillment: %s | fulfillmentOrders: %s" % (
        a.order, node.get("displayFulfillmentStatus"), [(f["id"].split("/")[-1], f["status"]) for f in fos]))

    def mut(fo_id, name_):
        body = ('fulfillmentHold:{reason:OTHER, reasonNotes:"xc-review"}, ' if name_ == "fulfillmentOrderHold" else "")
        sub = ("fulfillmentOrder{status} " if name_ == "fulfillmentOrderReleaseHold" else "")
        m = 'mutation{ %s(%sid:"%s"){ %suserErrors{field message} } }' % (name_, body, fo_id, sub)
        d = shopify_gql(shop, token, m)
        return (((d.get("data") or {}).get(name_) or {}).get("userErrors")) or d.get("errors")

    if action == "cancel":
        print("  Anulare AWB: setează un Flow Order-cancelled -> Cancel-shipping-label, sau anulează din dashboard xConnector.")
        print("  (nu există trigger pe tag pt cancel; hold-release e doar pt create.)"); return

    if action == "hold":
        tgt = [f for f in fos if f["status"] == "OPEN"]
        if not tgt:
            print("  Nimic OPEN de pus în hold (status: %s)." % [f["status"] for f in fos]); return
        if not a.apply:
            print("  DRY-RUN: aș pune în hold %d fulfillmentOrder(s)." % len(tgt)); return
        ok = sum(0 if mut(f["id"], "fulfillmentOrderHold") else 1 for f in tgt)
        print("  ✅ %d pus în hold." % ok); return

    # create = eliberează hold-ul → Flow 'hold released" → Create AWB
    held = [f for f in fos if f["status"] == "ON_HOLD"]
    if not held:
        print("  Comanda NU e în hold → Flow-ul hold-released nu se declanșează.")
        print("  → pune-o întâi în hold (Flow Order-created->Hold la comenzi noi, sau `awb-hold --order %s --apply`)." % a.order); return
    if not a.apply:
        print("  DRY-RUN: aș ELIBERA hold-ul pe %d fulfillmentOrder(s) → Flow → Create AWB." % len(held)); return
    ok = sum(0 if mut(f["id"], "fulfillmentOrderReleaseHold") else 1 for f in held)
    print("  ✅ hold eliberat pe %d → Flow hold-released -> xConnector creează AWB." % ok)


def release_hold(shop, token, name):
    """eliberează hold-ul pe fulfillment-order-ele ON_HOLD ale comenzii. (nr eliberate, nr held)"""
    node = find_order(shop, token, name)
    if not node:
        return 0, 0
    fos = [e["node"] for e in ((node.get("fulfillmentOrders") or {}).get("edges") or [])]
    held = [f for f in fos if f["status"] == "ON_HOLD"]
    rel = 0
    for f in held:
        m = 'mutation{ fulfillmentOrderReleaseHold(id:"%s"){ userErrors{message} } }' % f["id"]
        d = shopify_gql(shop, token, m)
        e = (((d.get("data") or {}).get("fulfillmentOrderReleaseHold") or {}).get("userErrors")) or d.get("errors")
        if not e:
            rel += 1
    return rel, len(held)


def cmd_awb_auto(a):
    """POARTA auto-AWB: validez adresa la xConnector și eliberez hold-ul (→ Flow → Create AWB)
    DOAR la comenzile fără AWB cu adresă VALIDĂ. WRONG/UNKNOWN rămân în hold (CS / auto-correct)."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        noawb = [o for o in xc.orders(dfrom, dto) if not has_awb(o)]
        valid = [o for o in noawb if o.get("addressStatus") == "VALID"]
        bad = [o for o in noawb if o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        print("═" * 70)
        print("  %s — %d fără AWB | %d adresă VALIDĂ | %d adresă proastă (rămân în hold)"
              % (sh["shopDomain"], len(noawb), len(valid), len(bad)))
        rel = 0
        for o in valid:
            name = o.get("orderName")
            pm = re.match(r"^([A-Za-z]+)", name or "")
            st = toks.get(pm.group(1).upper() if pm else "")
            if not st:
                continue
            if not a.apply:
                node = find_order(st["shopDomain"], st["adminToken"], name)
                fos = [e["node"] for e in ((node or {}).get("fulfillmentOrders", {}).get("edges") or [])] if node else []
                if any(f["status"] == "ON_HOLD" for f in fos):
                    print("  [dry] aș elibera %s (adresă validă, în hold) → AWB" % name)
                continue
            r, _ = release_hold(st["shopDomain"], st["adminToken"], name)
            rel += r
        if a.apply:
            print("  → ELIBERAT %d comenzi cu adresă validă → Flow creează AWB." % rel)
        # corecția pe cele invalide (cu --correct): repară conservator → cele reparate se eliberează
        if a.correct and bad:
            cor = manual = relc = 0
            print("  — corecție pe %d adrese proaste (conservator)%s:" % (len(bad), "" if a.apply else " [DRY-RUN]"))
            for o in bad:
                st, applied, detail = correct_address(xc, o, sh["shopDomain"], apply=a.apply)
                name = o.get("orderName")
                if st in ("would-correct", "corrected"):
                    cor += 1
                    print("    %s %s → %s" % (name, "✅ corectat" if st == "corrected" else "[ar corecta]", detail))
                    if a.apply and st == "corrected":
                        pm = re.match(r"^([A-Za-z]+)", name or "")
                        stk = toks.get(pm.group(1).upper() if pm else "")
                        if stk:
                            r, _ = release_hold(stk["shopDomain"], stk["adminToken"], name); relc += r
                else:
                    manual += 1
            print("    → %d corectabile (%d eliberate după corecție) | %d → CS manual" % (cor, relc, manual))
        elif bad:
            print("  → %d cu adresă proastă RĂMÂN ÎN HOLD (rulează cu --correct, sau CS): %s"
                  % (len(bad), ", ".join(o.get("orderName") for o in bad[:10])))


def has_awb(o):
    return any((d.get("documentType") == "SHIPPING_LABEL") for d in (o.get("documents") or []) if isinstance(d, dict))


def skip_shop(sh, a):
    """True dacă magazinul trebuie SĂRIT: nu e în --shop (suportă LISTĂ comma + prefix/substring,
    ex `--shop covoareauto-ro,bonhaus`) sau e în --exclude (validatorul RO nu acoperă CZ/PL/BG)."""
    dom = sh.get("shopDomain") or ""
    shop = getattr(a, "shop", None)
    if shop:
        wants = [w.strip() for w in shop.split(",") if w.strip()]
        if not any(w == dom or dom.startswith(w) for w in wants):  # full domain sau prefix (ancorat, fără substring oriunde)
            return True
    excl = {x.strip() for x in (getattr(a, "exclude", "") or "").split(",") if x.strip()}
    return dom in excl


def order_age_hours(xc, oid):
    """Vârsta comenzii în ore, din cel mai vechi eveniment de validare (addressValidationHistory).
    None dacă nu există istoric/timestamp. Folosit de `correct --min-age-hours` ca să sară comenzile
    proaspete (validarea xConnector e async/batch — multe se auto-validează în câteva ore)."""
    import datetime
    d = xc.by_id(oid)
    ts = [h.get("timestamp") for h in (d.get("addressValidationHistory") or [])
          if isinstance(h, dict) and h.get("timestamp")]
    if not ts:
        return None
    try:
        t0 = datetime.datetime.fromisoformat(min(ts).replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - t0).total_seconds() / 3600.0
    except Exception:
        return None


def fscore(m, k):
    v = m.get(k) or {}
    return (v.get("value"), v.get("score")) if isinstance(v, dict) else (v, None)


def verdict(matchers):
    """conservator (ca aac): UN singur candidat cu toate core-urile ≥0.95 → auto; altfel manual."""
    ms = matchers if isinstance(matchers, list) else (matchers.get("matchers") or matchers.get("matches") or [])
    if not ms:
        return "fără candidați → manual", None
    strong = [m for m in ms if all((fscore(m, f)[1] or 0) >= 0.95 for f in ("zipCode", "county", "city", "streetName"))]
    top = ms[0]
    sug = "%s, %s %s (%s)" % (fscore(top, "streetName")[0], fscore(top, "city")[0], fscore(top, "zipCode")[0], fscore(top, "county")[0])
    if len(strong) == 1:
        return "✅ auto-corectabil (candidat unic ≥0.95)", sug
    if len(strong) > 1:
        return "⚠️ %d candidați tari → manual" % len(strong), sug
    return "⚠️ niciun candidat ≥0.95 → manual", sug


def _digest(obj, n):
    s = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "")) if not unicodedata.combining(c)).lower().strip()


def zip_confirm(xc, zipc):
    if not zipc:
        return None
    t = xc.vtoken()
    h = {"Authorization": "Bearer " + t} if t else {}
    s, b = http("GET", VBASE + "/zip-code?countryId=1&zipCode=" + urllib.parse.quote(str(zipc)), h)
    try:
        d = json.loads(b)
        return d if (s == 200 and d) else None
    except Exception:
        return None


def correct_address(xc, o, shop_domain, apply=False):
    """CONSERVATOR (după aac): corectează adresa DOAR dacă există UN candidat cu toate core ≥0.95
    + zip confirmat la /zip-code + numărul casei păstrat. Întoarce (status, applied|None, detalii).
    status: would-correct | corrected | manual | error:<code>."""
    oid = o["orderId"]
    d = xc.by_id(oid)
    ad = d.get("shippingAddress") or {}
    ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                   "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
    msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
    # zip/oraș/județ ≥0.95 + stradă ≥0.90 (relaxat — există plasă de siguranță DPD/client la preluare).
    # UN singur candidat (fără competitor) = nu riscăm o adresă validă-dar-greșită.
    strong = [m for m in msl if all((fscore(m, f)[1] or 0) >= 0.95 for f in ("zipCode", "county", "city"))
              and (fscore(m, "streetName")[1] or 0) >= 0.90]
    if len(strong) != 1:
        return "manual", None, "%d candidați (zip/oraș/județ≥0.95, stradă≥0.90)" % len(strong)
    m = strong[0]
    czip = str(fscore(m, "zipCode")[0] or "")
    ccity = fscore(m, "city")[0] or ad.get("city") or ""
    ccounty = fscore(m, "county")[0] or ad.get("province") or ""
    tok = m.get("tokenizedAddress") or {}
    orig_nums = re.findall(r"\b(\d+[A-Za-z]?)\b", ad.get("address1") or "")
    num = (tok.get("streetNumber") or "").strip()
    if not num and len(orig_nums) == 1:
        num = orig_nums[0]
    if not num or (orig_nums and num not in orig_nums):
        return "manual", None, "număr casă nesigur"
    if not zip_confirm(xc, czip):
        return "manual", None, "zip neconfirmat"
    # construiește adresa: păstrează TOT, înlocuiește core; strada canonică doar dacă diferă după folding
    stype = (tok.get("streetType") or "").strip()
    # numele CANONIC al străzii (valoarea matcher-ului), nu forma tokenizată a clientului (aac HARD RULE 5)
    sname = (fscore(m, "streetName")[0] or tok.get("streetName") or "").strip()
    new_a1 = ad.get("address1")
    if _fold(stype + " " + sname) != _fold(ad.get("address1") or ""):
        new_a1 = ("%s %s Nr. %s" % (stype.title(), sname.title(), num)).strip()
    applied = dict(ad)
    applied["country"] = "Romania"
    if _fold(ccounty) != _fold(ad.get("province") or ""):
        applied["province"] = ccounty.title()
    if _fold(ccity) != _fold(ad.get("city") or ""):
        applied["city"] = ccity.title()
    applied["zip"] = czip
    applied["address1"] = new_a1
    detail = "%s, %s %s (%s)" % (new_a1, applied.get("city"), czip, applied.get("province"))
    if not apply:
        return "would-correct", applied, detail
    body = {"orderId": oid,
            "idempotencyKey": "aac-%s-%s-%s-%s" % (_digest(shop_domain, 8), oid,
                              _digest({k: _fold(str(v)) for k, v in ad.items()}, 12), _digest(applied, 12)),
            "appliedShippingAddress": applied,
            "expectedAddressHash": d.get("addressHash"), "expectedStatusHash": d.get("statusHash"),
            "expectedEvidenceHash": d.get("evidenceHash"), "agentClaimedConfidence": 0.96,
            "agentRationale": "Single canonical candidate, all core fields >=0.95, zip confirmed, house number preserved.",
            "modelName": "gigi-xconnector", "mcpClientId": "gigi-xconnector"}
    s, b = http("POST", XBASE + "/api/orders/ai-correct-address", xc.h, body)
    return ("corrected" if s == 200 else "error:%s" % s), applied, detail


def cmd_correct(a):
    """CRON (model order-created): comenzile fără AWB cu adresă WRONG/UNKNOWN →
    tag 'duplicata' = skip · corectabilă = aac ai-correct-address (cu --apply) · grea = triaj CS.
    Fără --apply = dry-run (arată ce ar face). Corecția face adresa VALID → gata de AWB (bulk dashboard)."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        bad = [o for o in xc.orders(dfrom, dto) if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        corrected = dup = cs = fresh = 0
        min_age = getattr(a, "min_age_hours", 0) or 0
        cs_rows = []
        print("═" * 74)
        print("  %s — %d fără AWB cu adresă WRONG/UNKNOWN (%dz)%s%s"
              % (sh["shopDomain"], len(bad), a.days, "" if a.apply else "  [DRY-RUN]",
                 "  [min-age %dh]" % min_age if min_age else ""))
        for o in bad:
            name = o.get("orderName")
            if min_age:
                age = order_age_hours(xc, o.get("orderId"))
                if age is not None and age < min_age:
                    fresh += 1
                    print("  %s  🕒 proaspăt (%.0fh < %dh) → las sweep-ul xConnector să ruleze, skip" % (name, age, min_age))
                    continue
            st, applied, detail = correct_address(xc, o, sh["shopDomain"], apply=False)
            if st == "would-correct":
                if "duplicata" in shopify_order_tags(name, toks):
                    dup += 1
                    print("  %s  ⏭  duplicata → skip" % name)
                    continue
                if a.apply:
                    st2, _, det2 = correct_address(xc, o, sh["shopDomain"], apply=True)
                    if st2 == "corrected":
                        corrected += 1
                        print("  %s  ✅ corectat → %s  (VALID, gata de AWB)" % (name, det2))
                    else:
                        cs += 1
                        print("  %s  ⚠ apply %s" % (name, st2))
                else:
                    corrected += 1
                    print("  %s  [ar corecta] → %s" % (name, detail))
            else:
                cs += 1
                cs_rows.append((name, o.get("addressStatus"), detail or ""))
        print("  → %s%d corectate · %d duplicata skip%s · %d → CS"
              % ("APLICAT: " if a.apply else "ar corecta: ", corrected, dup,
                 " · %d proaspete (skip)" % fresh if min_age else "", cs))
        if cs_rows:
            print("  Triaj CS (adrese grele — contact client):")
            for nm, status, why in cs_rows[:40]:
                print("    %-9s %-8s %s" % (nm, status, why))


def cmd_summary(shops, a):
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        os_ = xc.orders(a.dfrom, a.dto)
        noawb = [o for o in os_ if not has_awb(o)]
        from collections import Counter
        st = Counter(o.get("addressStatus") for o in noawb)
        print("═" * 60)
        print("  %s — %d comenzi (fereastră %s→%s)" % (sh["shopDomain"], len(os_), a.dfrom, a.dto))
        print("  FĂRĂ AWB (nepornite): %d  |  status: %s" % (len(noawb), dict(st)))
        bad = sum(v for k, v in st.items() if k in ("WRONG", "UNKNOWN"))
        print("  → de confirmat/corectat înainte de AWB: %d (WRONG+UNKNOWN, fără AWB)" % bad)


def cmd_issues(shops, a):
    rows = []
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        os_ = xc.orders(a.dfrom, a.dto)
        bad = [o for o in os_ if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        if not a.json:
            print("═" * 78)
            print("  %s — %d comenzi nepornite cu adresă problemă (de confirmat înainte de AWB)" % (sh["shopDomain"], len(bad)))
            print("═" * 78)
        for o in bad:
            d = xc.by_id(o.get("orderId"))
            ad = d.get("shippingAddress") or {}
            cur = "%s, %s %s (%s)" % (ad.get("address1", ""), ad.get("city", ""), ad.get("zip", ""), ad.get("province", ""))
            ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                           "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
            verd, sug = verdict(ms)
            rows.append({"shop": sh["shopDomain"], "order": o.get("orderName"), "orderId": o.get("orderId"),
                         "status": o.get("addressStatus"), "current": cur, "suggestion": sug, "verdict": verd})
            if not a.json:
                print("  #%-8s [%s]  %s" % (o.get("orderName"), o.get("addressStatus"), cur))
                print("       validator: %s" % (sug or "—"))
                print("       %s" % verd)
            time.sleep(0.2)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif rows:
        auto = sum(1 for r in rows if r["verdict"].startswith("✅"))
        print("\n  TOTAL: %d de confirmat | %d auto-corectabile | %d manual" % (len(rows), auto, len(rows) - auto))
        print("  (corecția propriu-zisă: skill-ul xConnector aac `/agentic-address-correction`, dry-run→--apply)")


def cmd_recheck(a):
    """READ: re-verifică addressStatus CURENT — care s-au auto-validat (VALID/PERFECT) vs încă WRONG/UNKNOWN.
    Validarea xConnector e async/batch, deci multe comenzi flagate se vindecă singure în câteva ore.
    Cu --order GT1,GT2 verifică lista dată; fără, ia coada curentă fără AWB cu adresă WRONG/UNKNOWN."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    names = [s.strip().lstrip("#") for s in (a.order or "").split(",") if s.strip()]
    for sh in load_shops():
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        idx = {o.get("orderName"): o for o in xc.orders(dfrom, dto)}
        if names:
            targets = [idx.get(n, {"orderName": n, "addressStatus": "?(în afara ferestrei)"}) for n in names]
        else:
            targets = [o for o in idx.values() if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        healed = stuck = 0
        print("═" * 60)
        print("  %s — recheck %d comenzi (%dz)" % (sh["shopDomain"], len(targets), a.days))
        for o in targets:
            st = o.get("addressStatus")
            good = st in ("VALID", "PERFECT")
            awb = has_awb(o)
            if good:
                healed += 1
            elif st in ("WRONG", "UNKNOWN"):
                stuck += 1
            print("  %-9s %s%s" % (o.get("orderName"), ("✅ " if good else "… ") + str(st),
                                   "  (are AWB)" if awb else ""))
        print("  → %d auto-validate (VALID/PERFECT) · %d încă WRONG/UNKNOWN" % (healed, stuck))


# ── AWB direct prin API (/api/actions/*) — necesită ROLE_AUTOMATION + permisiuni write pe cheie ──
# Scriu bani/stare → DRY-RUN by default; POST real DOAR cu --apply. orderId trimis = Shopify order ID
# (câmpul `orderId` din /api/orders, NU merchantOrderId).
BILLING_TYPES = {"SMART_BILL", "SMARTBILL", "SMART_BILL_RO", "FACTURIS", "OBLIO", "FGO"}


# Prefix orderName → domeniu myshopify, STATIC (hardcodat) ca să NU depindă de SHOPIFY_STORES_CSV — care
# pe o mașină cu KB stale lipsește, lăsând doar GT din SHOPIFY_ADMIN_TOKENS. Potrivirea e pe cel mai LUNG
# prefix care e ÎNCEPUTUL literelor din orderName, deci e robustă la trunchiere (GRAND16613 → „GRAN").
PREFIX_DOMAIN = {
    "APR": "8e3700-d9", "BELA": "dvk4hu-dq", "BG": "a98a4e-16", "BON": "bonhaus", "BONBG": "ux1x6n-n2",
    "CARP": "nxfer1-n4", "COV": "bb4nmc-pb", "CZ": "vthuzq-7j", "EST": "6f9e22-9d", "GEN": "cn54vk-uz",
    "GRAN": "n12w89-yy", "GT": "ix5bxc-hr", "LUX": "de51c5-b8", "MAG": "covoareauto-ro", "NOC": "1eee37-2d",
    "NUB": "bmuwvv-jy", "OFER": "ofertelezilei", "PAT": "ce-pat-ai", "PL": "f0yrmh-ia", "RED": "audusp-rf",
    "ROSSI": "1d2bce-2",
}


def domain_for_order(name):
    """Domeniul myshopify după prefixul din orderName (cel mai LUNG prefix înregistrat care e începutul
    literelor — robust la trunchiere: GRAND16613→GRAN, BONBG…→BONBG peste BON). None dacă necunoscut."""
    pm = re.match(r"^([A-Za-z]+)", name or "")
    if not pm:
        return None
    letters = pm.group(1).upper()
    best = ""
    for pref in PREFIX_DOMAIN:
        if letters.startswith(pref) and len(pref) > len(best):
            best = pref
    return (PREFIX_DOMAIN[best] + ".myshopify.com") if best else None


def resolve_order(name, a, days=60):
    """Întoarce (shop, xc, order_obj) pt orderName. `--shop` restrânge la un magazin; altfel INFEREZ magazinul
    din prefixul comenzii (rapid, NU scanez toate 19) și-l încerc PRIMUL, cu fallback la restul dacă nu nimeresc."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    shops = load_shops()
    if a.shop:
        scan = [sh for sh in shops if sh["shopDomain"] == a.shop]
    else:
        guess = domain_for_order(name)  # ex MAG24088 → covoareauto-ro.myshopify.com (independent de CSV/KB)
        scan = sorted(shops, key=lambda sh: 0 if (guess and sh["shopDomain"] == guess) else 1) if guess else shops
    # 1) scan pe fereastră (dă `documents`/AWB) — magazinul ghicit primul + sortat date DESC, ca să găsesc
    # comenzile recente din PRIMA pagină (instant), nu „stând să caute" prin toate paginile.
    for sh in scan:
        xc = XC(sh["apiKey"])
        for o in xc.orders(dfrom, dto, {"sort": "date", "sortDir": "desc"}):
            if o.get("orderName") == name:
                return sh, xc, o
    # 2) FALLBACK comenzi vechi / volum mare (în afara ferestrei): Shopify orderName→orderId → xConnector by-id.
    # (by-id NU întoarce `documents` → fără info AWB pe această cale; awb-make e protejat: xConnector respinge dublul.)
    toks = {t.get("shopDomain"): t for t in load_shopify_tokens()}
    for sh in scan:
        st = toks.get(sh["shopDomain"])
        oid = shopify_order_id(name, st) if st else None
        if not oid:
            continue
        xc = XC(sh["apiKey"])
        d = xc.by_id(oid)
        if isinstance(d, dict) and d.get("orderName"):
            return sh, xc, d
    return None, None, None


def awb_doc(o):
    for d in (o.get("documents") or []):
        if isinstance(d, dict) and d.get("documentType") == "SHIPPING_LABEL":
            return d
    return None


def doc_tracking(doc):
    """AWB number: câmpul direct dacă există, altfel din param `t=` al URL-ului etichetei."""
    if not doc:
        return None
    t = doc.get("trackingNumber") or doc.get("awbNumber")
    if t:
        return t
    m = re.search(r"[?&]t=([^&]+)", doc.get("url") or "")
    return m.group(1) if m else None


def courier_connectors(xc):
    return [c for c in xc.list_connectors() if c.get("active") and (c.get("type") or "").upper() not in BILLING_TYPES]


def pick_connector(xc, a):
    """(connector|None, lista_curieri). None = ambiguu (mai mulți curieri) → cere --connector."""
    cons = courier_connectors(xc)
    if getattr(a, "connector", None):
        try:
            cid = int(a.connector)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return None, cons
        m = [c for c in xc.list_connectors() if c.get("id") == cid]
        return (m[0] if m else {"id": cid, "name": "?", "type": "?"}), cons
    if len(cons) == 1:
        return cons[0], cons
    # default curier ARONA = DPD Romania (preferat numele exact, apoi orice DPD non-SWAP, apoi orice DPD)
    dpd = ([c for c in cons if (c.get("name") or "") == "DPD Romania"]
           or [c for c in cons if (c.get("type") or "").upper() == "DPD" and "SWAP" not in (c.get("name") or "").upper()]
           or [c for c in cons if (c.get("type") or "").upper() == "DPD"])
    if len(dpd) == 1:
        return dpd[0], cons
    return None, cons


def _ask_connector(cons):
    print("  Mai mulți curieri activi — alege cu --connector ID:")
    for c in cons:
        print("    %-7s %-14s %s" % (c.get("id"), c.get("type"), c.get("name")))


# Grandia: produse voluminoase (după productType) → curier DRAGON_STAR; restul → DPD (default).
GRANDIA_DOMAIN = "n12w89-yy.myshopify.com"
GRANDIA_BULKY_TYPES = {"magazii de grădină", "lavoare", "mese și măsuțe", "oglinzi led"}


def order_product_types(shop, token, name):
    """Set de productType (lower) ale liniilor comenzii din Shopify."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ lineItems(first:100){ edges{ node{ '
         'product{ productType } } } } } } } }') % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return set()
    li = ((edges[0]["node"].get("lineItems") or {}).get("edges")) or []
    return {((e["node"].get("product") or {}).get("productType") or "").strip().lower() for e in li}


def route_connector(sh, st, order_name, cons, default_con):
    """Rutare per-produs: Grandia cu produs voluminos → Dragon Star; altfel default (DPD). Alte magazine → default."""
    if not sh or sh.get("shopDomain") != GRANDIA_DOMAIN or not st:
        return default_con
    if order_product_types(st["shopDomain"], st["adminToken"], order_name) & GRANDIA_BULKY_TYPES:
        ds = [c for c in cons if (c.get("type") or "").upper() == "DRAGON_STAR" and c.get("active")]
        if ds:
            return ds[0]
    return default_con


# ── Validare adrese INTERNAȚIONALE prin HERE Geocoding (validatorul RO al xConnector dă fals WRONG/UNKNOWN) ──
# KPI = AWB făcut. Externe au adrese bune; HERE le validează → AWB cu curierul local (home delivery, ~100% din ele).
HERE_COUNTRY = {"vthuzq-7j.myshopify.com": "CZE", "f0yrmh-ia.myshopify.com": "POL", "ux1x6n-n2.myshopify.com": "BGR"}
HERE_MIN_SCORE = 0.9  # curierul pt CZ/PL/BG = DPD Romania (livrează cross-border), via pick_connector default


def here_key():
    k = os.environ.get("HERE_API_KEY")
    if k:
        return k
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", "HERE_API_KEY"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def here_validate(addr, country, key):
    """queryScore HERE Geocoding (0-1) pt o adresă, restrâns pe țară. 0.0 la eroare/fără rezultat/fără cheie."""
    if not key or not country:
        return 0.0
    q = ", ".join([x for x in [addr.get("address1"), addr.get("address2"), addr.get("city"), addr.get("zip")] if x])
    if not q:
        return 0.0
    url = ("https://geocode.search.hereapi.com/v1/geocode?q=%s&in=countryCode:%s&apiKey=%s"
           % (urllib.parse.quote(q), country, urllib.parse.quote(key)))
    s, b = http("GET", url, {})
    try:
        items = json.loads(b).get("items") or []
        return float((items[0].get("scoring") or {}).get("queryScore", 0)) if items else 0.0
    except Exception:
        return 0.0


# ── Nr. COLETE pt AWB din metafield-uri Shopify (vezi memoria parcel-count-metafields) ──
# order `xconnector.parcel-count` (total calculat), altfel ceil(Σ product box × qty), altfel 1. CEIL pe decimal (1.5→2).
PARCEL_PRODUCT_KEYS = ("nr_cutii", "nr_produse")  # namespace custom — cutii REALE (NU `nrproduse` = nr produse parfumuri)


def _ceil_pos(x):
    i = int(x)
    return i + 1 if x > i else i


def order_parcel_count(shop, token, name):
    if not shop or not token or not name:
        return 1
    # nr colete REAL = order xconnector.parcel-count (total deja calculat), altfel cutii din produs.
    # NU folosim `custom.nrproduse` (= nr PRODUSE, există doar pe parfumuri GT/Esteban, care n-au colete multiple → 1).
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ '
         'pc: metafield(namespace:"xconnector", key:"parcel-count"){ value } '
         'lineItems(first:100){ edges{ node{ quantity product{ '
         'k1: metafield(namespace:"custom", key:"nr_cutii"){ value } '
         'k2: metafield(namespace:"custom", key:"nr_produse"){ value } } } } } } } } }') % name.replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return 1
    node = edges[0]["node"]
    pc = (node.get("pc") or {}).get("value")
    if pc not in (None, ""):
        try:
            return max(1, _ceil_pos(float(pc)))
        except Exception:
            pass
    total = 0.0
    found = False
    for e in ((node.get("lineItems") or {}).get("edges") or []):
        li = e["node"]; p = li.get("product") or {}
        for k in ("k1", "k2"):
            v = (p.get(k) or {}).get("value")
            if v not in (None, ""):
                try:
                    total += float(v) * (li.get("quantity") or 1); found = True; break
                except Exception:
                    pass
    return max(1, _ceil_pos(total)) if (found and total > 0) else 1


def resolve_parcels(a, st, order_name):
    """--parcels explicit forțează; altfel auto din metafield (order/product); fallback 1."""
    if getattr(a, "parcels", None):
        return a.parcels
    if st:
        return order_parcel_count(st["shopDomain"], st["adminToken"], order_name)
    return 1


def _err_text(s, d):
    """Text de eroare lizibil din răspunsul xConnector (ApiErrorResponse / errorMessage / brut)."""
    if isinstance(d, dict):
        return d.get("errorDescription") or d.get("errorMessage") or d.get("errorCode") or json.dumps(d, ensure_ascii=False)[:200]
    return "%s %s" % (s, str(d)[:200])


def _label_result(s, d):
    if s != 200 or not isinstance(d, dict):
        print("  ❌ eroare %s: %s" % (s, d if isinstance(d, str) else (d.get("errorDescription") or d)))
        return
    if not d.get("accepted"):
        print("  ❌ respins: %s" % d.get("errorMessage", d))
        return
    for L in (d.get("shippingLabels") or []):
        if L.get("success"):
            print("  ✅ AWB %s | %s | %s RON | %s" % (L.get("trackingNumber"), L.get("carrierName"),
                                                       L.get("price"), L.get("shippingLabelUrl")))
        else:
            print("  ❌ label: %s" % L.get("errorMessage"))


def cmd_connectors(a):
    for sh in load_shops():
        if a.shop and sh["shopDomain"] != a.shop:
            continue
        xc = XC(sh["apiKey"])
        cons = xc.list_connectors()
        print("═" * 60)
        print("  %s — %d connectori" % (sh["shopDomain"], len(cons)))
        for c in cons:
            kind = "factură" if (c.get("type") or "").upper() in BILLING_TYPES else "curier"
            print("    %-7s %-7s %-14s %-24s %s" % (c.get("id"), kind, c.get("type"), c.get("name"),
                                                    "activ" if c.get("active") else "INACTIV"))


def cmd_awb_make(a, _resolved=None):
    sh, xc, o = _resolved or resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită%s." % (a.order, " în %s" % a.shop if a.shop else " (căutat în toate)")); return
    if has_awb(o):
        print("  ⚠ %s ARE deja AWB (%s) — folosește awb-regen ca să-l refaci (anulează + reface)." % (a.order, doc_tracking(awb_doc(o))))
        return
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return
    con, cons = pick_connector(xc, a)
    if not con:
        _ask_connector(cons); return
    st = {t.get("shopDomain"): t for t in load_shopify_tokens()}.get(sh["shopDomain"])
    if st and not getattr(a, "force", False):  # cadou UGC/influencer → NU fac AWB (flux separat); --force dacă chiar vrei
        if any(tg in shopify_order_tags(a.order, {st.get("prefix", ""): st}) for tg in INFLUENCER_TAGS):
            print("  ⛔ %s are tag `influencer` (cadou UGC) → NU fac AWB. Folosește --force dacă chiar vrei." % a.order)
            return
    if not getattr(a, "connector", None):  # rutare per-produs (Grandia → Dragon Star) doar dacă nu s-a forțat connectorul
        con = route_connector(sh, st, a.order, cons, con)
    parcels = resolve_parcels(a, st, a.order)  # nr colete din metafield (sau --parcels forțat)
    body = {"orderId": o.get("orderId"), "connectorId": con["id"], "parcelCount": parcels,
            "parcelType": a.type, "notifyCustomer": bool(a.notify)}
    print("═" * 60)
    print("  AWB make · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  curier: %s [%s] · colete: %d · tip: %s · notify: %s" % (con.get("name"), con.get("id"), parcels, a.type, bool(a.notify)))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/create-shipping-label:\n    %s" % json.dumps(body)); return
    # Adresă WRONG/UNKNOWN → corecție conservatoare înainte (best-effort; AWB-ul poate merge și fără).
    if o.get("addressStatus") in ("WRONG", "UNKNOWN"):
        cstt, _, _ = correct_address(xc, o, sh["shopDomain"], apply=True)
        if cstt == "corrected":
            print("  ✎ adresă corectată conservator înainte de AWB")
    s, d = xc.post("/api/actions/create-shipping-label", body)
    ok = s == 200 and isinstance(d, dict) and d.get("accepted") and any(L.get("success") for L in (d.get("shippingLabels") or []))
    # CAPTEZ eroarea: xConnector are DOAR unfulfilled/fulfilled (NU 'on hold'). `has_awb(o)` era False (unfulfilled)
    # → deci 'no open fulfillment order' = comanda e ON HOLD în Shopify → ELIBEREZ hold-ul și REÎNCERC pe loc.
    msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
    if not ok and st and ("fulfillment" in msg.lower() or "was not created" in msg.lower()):
        nrel, reasons, skipped = shopify_release_holds(st["shopDomain"], st["adminToken"], a.order)
        if skipped:
            print("  ⛔ HOLD LEGITIM (%s) → NU eliberez / NU fac AWB peste fraudă/stoc/plată." % ", ".join(sorted(set(skipped))))
        if nrel:
            print("  ⏸️→▶️ comanda era pe HOLD (%s) → eliberat, reîncerc AWB" % (", ".join(reasons) or "fără motiv"))
            time.sleep(1.2)  # lasă Shopify să redeschidă fulfillment order-ul
            s, d = xc.post("/api/actions/create-shipping-label", body)
            ok = s == 200 and isinstance(d, dict) and d.get("accepted") and any(L.get("success") for L in (d.get("shippingLabels") or []))
        elif not skipped:
            print("  ℹ️ unfulfilled în xConnector dar NU pe hold → cel mai probabil adresă/connector (eroare reală, nu hold).")
    _label_result(s, d)


def cmd_awb_void(a, _resolved=None):
    sh, xc, o = _resolved or resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    if not doc and not a.apply:
        print("  %s nu are AWB (SHIPPING_LABEL) de anulat." % a.order); return
    cid = getattr(a, "connector", None) or (doc or {}).get("connectorId")
    body = {"orderId": o.get("orderId")}
    if cid:
        try:
            body["connectorId"] = int(cid)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return
    print("  AWB void · %s · connector %s · tracking %s" % (a.order, cid, doc_tracking(doc)))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/cancel-shipping-label:\n    %s" % json.dumps(body)); return
    s, d = xc.post("/api/actions/cancel-shipping-label", body)
    print("  %s" % ("✅ anulat" if (s == 200 and isinstance(d, dict) and d.get("accepted")) else "❌ %s: %s" % (s, d)))


def cmd_awb_regen(a):
    """Anulează AWB-ul curent și îl reface cu alte condiții (parcelCount/parcelType/connector)."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    con, cons = pick_connector(xc, a)
    if not con:
        _ask_connector(cons); return
    st = {t.get("shopDomain"): t for t in load_shopify_tokens()}.get(sh["shopDomain"])
    if not getattr(a, "connector", None):
        con = route_connector(sh, st, a.order, cons, con)
    parcels = resolve_parcels(a, st, a.order)  # nr colete din metafield (sau --parcels forțat)
    print("═" * 60)
    print("  REGEN AWB · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  pas 1: anulez AWB curent (%s)" % (doc_tracking(doc) or "—"))
    print("  pas 2: creez nou — curier %s [%s] · %d colete · %s" % (con.get("name"), con.get("id"), parcels, a.type))
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return
    if doc:
        vbody = {"orderId": o.get("orderId")}
        if doc.get("connectorId"):
            vbody["connectorId"] = doc["connectorId"]
        sv, dv = xc.post("/api/actions/cancel-shipping-label", vbody)
        voided = (sv == 200 and isinstance(dv, dict) and dv.get("accepted"))
        print("  void: %s" % ("✅" if voided else "❌ %s: %s" % (sv, dv)))
        if not voided:
            print("  ⛔ void eșuat → NU recreez (risc 2 AWB-uri). Rezolvă manual."); return
        time.sleep(1.5)
    mbody = {"orderId": o.get("orderId"), "connectorId": con["id"], "parcelCount": parcels,
             "parcelType": a.type, "notifyCustomer": bool(a.notify)}
    _label_result(*xc.post("/api/actions/create-shipping-label", mbody))


def cmd_awb_label(a):
    """Arată tracking + URL-ul de descărcare a etichetei (PDF) pt comanda dată."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    if not doc:
        print("  %s nu are AWB." % a.order); return
    cid, trk = doc.get("connectorId"), doc_tracking(doc)
    url = doc.get("url") or doc.get("awbPdfUrl") or (
        XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s" % (cid, urllib.parse.quote(str(trk or ""))))
    print("  %s (%s) · AWB %s · connector %s" % (a.order, sh["shopDomain"], trk, cid))
    print("  etichetă: %s" % url)
    if doc.get("downloaded") is False:
        print("  ⚠️ încă NEDESCĂRCAT (în coada de print depozit) — DESCHIDEREA linkului îl marchează `downloaded`")
        print("     și-l SCOATE din coada de print. NU deschide dacă nu vrei să-l consumi din print.")


# ── CS: „du-mă la comanda X" — linkuri Shopify / xConnector / tracking (rezolvat 100% prin xConnector) ──
def order_links(sh_domain, o):
    """Linkuri pt o comandă (zero Shopify API — totul din DTO-ul xConnector):
      shopify    = admin order (orderId = ID Shopify), xconnector = dashboard order (merchantOrderId),
      tracking   = redirect curier (track?connectorId&trackingNumber), awb = nr tracking."""
    out = {}
    oid, moid = o.get("orderId"), o.get("merchantOrderId")
    if oid:
        out["shopify"] = "https://%s/admin/orders/%s" % (sh_domain, oid)
    if moid:
        out["xconnector"] = "%s/shop/%s/order?orderId=%s" % (XBASE, sh_domain, moid)
    doc = awb_doc(o); trk = doc_tracking(doc) if doc else None
    if trk:
        out["awb"] = trk
        out["tracking"] = "%s/track?connectorId=%s&trackingNumber=%s" % (XBASE, doc.get("connectorId"), urllib.parse.quote(str(trk)))
    return out


def _open_urls(urls):
    opener = "open" if sys.platform == "darwin" else ("xdg-open" if sys.platform.startswith("linux") else None)
    if not opener:
        print("  (deschidere automată indisponibilă pe %s — copiază linkurile)" % sys.platform); return
    for u in urls:
        try:
            subprocess.Popen([opener, u], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    print("  → deschis în browser")


def find_by_awb(awb, a):
    """(shop, order) după tracking number, prin xConnector by-tracking-number (zero Shopify). None dacă negăsit."""
    for s in load_shops():
        if a.shop and s["shopDomain"] != a.shop:
            continue
        st, d = XC(s["apiKey"]).get("/api/orders/by-tracking-number", "trackingNumber=%s" % urllib.parse.quote(str(awb)))
        if st == 200 and isinstance(d, dict) and d.get("orderName"):
            return s, d
    return None, None


def cmd_links(a):
    """CS „du-mă la comanda X" — totul prin xConnector (NU consumă rația Shopify).
    `links --order GT123` (după nr comandă) sau `links --awb <tracking>` (după AWB) → comandă + status +
    linkuri Shopify + xConnector + tracking. `--open` le deschide în browser."""
    if getattr(a, "awb", None) or getattr(a, "order", None):
        if getattr(a, "awb", None):
            sh, o = find_by_awb(a.awb, a)
            if not o:
                print("AWB %s negăsit în niciun magazin." % a.awb); return
        else:
            sh, _, o = resolve_order(a.order, a, a.days)
            if not o:
                print("Comanda %s negăsită." % a.order); return
        L = order_links(sh["shopDomain"], o)
        print("  %s (%s)%s" % (o.get("orderName"), sh["shopDomain"], (" · AWB %s" % L["awb"]) if L.get("awb") else " · fără AWB"))
        # STATUS (ce se întâmplă cu comanda) — fără Shopify: xConnector + AWBprint
        deliv = awbprint_status(o.get("orderName"))  # status livrare REAL (aggregated_status)
        disp = "expediat" if o.get("dispatched") else "neexpediat"
        if "documents" not in o:   # rezolvat prin fallback by-id (Shopify→ID) → DTO-ul n-are documents
            has = "AWB: vezi dashboard (comandă veche, rezolvată prin ID)"
        else:
            has = "AWB făcut" if awb_doc(o) else "FĂRĂ AWB"
        print("  Status:     adresă=%s · %s · %s%s" % (o.get("addressStatus") or "?", has, disp,
                                                       (" · livrare=%s" % deliv) if deliv else ""))
        print("  Shopify:    %s" % L.get("shopify", "—"))
        print("  xConnector: %s" % L.get("xconnector", "—"))
        print("  Tracking:   %s" % L.get("tracking", "— (fără AWB)"))
        print("  → profil client + alte comenzi: gigi:cs-customer-360 · tichete: gigi:cs-tickets (din DB/Richpanel, fără Shopify)")
        if getattr(a, "open", False):
            _open_urls([L[k] for k in ("shopify", "xconnector", "tracking") if L.get(k)])
        return
    print("Dă --order GT123 sau --awb <tracking>.")


# ── Anulare comandă (xConnector cancel AWB + Shopify cancel order), cu gardă „plecată" ──
# „Plecată" = preluată de curier (status AWBprint, sursa de adevăr) → NU se poate anula.
# Neplecată + are AWB → anulez AWB apoi comanda. Fără AWB → doar comanda.
PLECATA = {"in_transit", "delivered", "back_to_sender", "returning_to_sender", "customer_pickup",
           "unsuccessful_delivery", "refused", "deferred_delivery", "redirected", "lost", "lost_in_transit"}
ALREADY_CANCELLED = {"cancelled"}


def awbprint_status(order_name):
    """aggregated_status din AWBprint (sursa de adevăr curier) pt orderName. None dacă lipsește DB/order/pg8000."""
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return None
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url)
    con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select aggregated_status from orders where order_number = :n order by id desc limit 1", n=order_name)
        return (rows[0][0] if rows else None)
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def shopify_order_cancel(shop, token, order_gid, reason="CUSTOMER", refund=False, restock=True, notify=False):
    """orderCancel (Shopify Admin). refund OFF by default — NU returna bani fără decizie explicită
    (pt COD inutil; pt comenzi plătite, refund real). Întoarce listă de erori (gol = OK)."""
    reason = reason if reason in ("CUSTOMER", "OTHER", "DECLINED", "FRAUD", "INVENTORY", "STAFF") else "OTHER"
    m = ('mutation{ orderCancel(orderId:"%s", reason:%s, refund:%s, restock:%s, '
         'notifyCustomer:%s, staffNote:"anulare via xconnector"){ job{ id } orderCancelUserErrors{ message } } }'
         ) % (order_gid, reason, "true" if refund else "false", "true" if restock else "false", "true" if notify else "false")
    d = shopify_gql(shop, token, m)
    oc = (d.get("data") or {}).get("orderCancel")
    if oc is None:
        return d.get("errors") or [{"message": "orderCancel a întors null (răspuns Shopify neașteptat)"}]
    return oc.get("orderCancelUserErrors") or d.get("errors")


def cmd_order_cancel(a):
    """Anulează o comandă: dacă e PLECATĂ (preluată de curier) → refuz; dacă e neplecată și are AWB →
    anulez AWB (xConnector) APOI comanda (Shopify); fără AWB → doar comanda. Dry-run by default."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    # „A PLECAT?" = statusul de curier din AWBprint (aggregated_status). xConnector NU expune un status real
    # de expediere (doar `dispatched` boolean — NErelevant — și `downloaded` = status de PRINT). Testul
    # AUTORITATIV final rămâne încercarea de a anula AWB-ul: dacă a plecat, xConnector dă eroare și ne oprim.
    status = awbprint_status(a.order)
    awb = has_awb(o)
    trk = doc_tracking(awb_doc(o))
    print("═" * 60)
    print("  ANULARE comandă · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  status livrare (AWBprint): %s · AWB: %s" % (status or "necunoscut", trk or "—"))
    if status in ALREADY_CANCELLED:
        print("  • comanda e deja anulată."); return
    if status in PLECATA and not getattr(a, "force", False):
        print("  ⛔ comanda a PLECAT (%s) — NU se poate anula. (forțează cu --force ca să încerci oricum;"
              " xConnector va da eroare dacă AWB-ul chiar a plecat)." % status); return
    if status is None and awb:
        print("  ℹ status de curier necunoscut — încerc anularea AWB; dacă a plecat, xConnector dă eroare și mă opresc.")
    # tokenul Shopify din MAGAZINUL găsit (nu din prefix) + comanda — verificate ÎNAINTE de orice scriere,
    # ca să nu rămână comanda activă cu AWB anulat.
    by_dom = {t.get("shopDomain"): t for t in load_shopify_tokens()}
    st = by_dom.get(sh["shopDomain"])
    if not st:
        print("  ⚠ fără token Shopify pt %s în SHOPIFY_ADMIN_TOKENS → nu pot anula comanda. Nu ating nimic." % sh["shopDomain"]); return
    do_refund = bool(getattr(a, "refund", False))
    do_notify = bool(getattr(a, "notify", False))
    do_restock = not getattr(a, "no_restock", False)
    plan = (["anulez AWB %s (xConnector)" % (trk or "—")] if awb else []) + \
           ["anulez comanda în Shopify%s%s · email client: %s"
            % (" + REFUND" if do_refund else " (fără refund)",
               " + restock" if do_restock else " (FĂRĂ restock)",
               "TRIMIT" if do_notify else "NU trimit")]
    print("  plan: %s" % "  →  ".join(plan))
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    node = find_order(st["shopDomain"], st["adminToken"], a.order)
    if not node or not node.get("id"):
        print("  Comanda %s negăsită în Shopify (%s) → nu ating nimic." % (a.order, sh["shopDomain"])); return
    if awb:
        body = {"orderId": o.get("orderId")}
        cid = (awb_doc(o) or {}).get("connectorId")
        if cid:
            body["connectorId"] = cid
        sv, dv = xc.post("/api/actions/cancel-shipping-label", body)
        if not (sv == 200 and isinstance(dv, dict) and dv.get("accepted")):
            err = _err_text(sv, dv)
            print("  ⛔ AWB-ul NU s-a putut anula — cel mai probabil coletul A PLECAT deja la curier.")
            print("     → NU anulez comanda. ANUNȚĂ CS/clientul: comanda a plecat, nu se mai poate anula.")
            print("     (eroare xConnector: %s)" % err)
            return
        print("  ✅ AWB anulat")
    errs = shopify_order_cancel(st["shopDomain"], st["adminToken"], node["id"],
                                refund=do_refund, restock=not getattr(a, "no_restock", False), notify=bool(a.notify))
    print("  %s" % ("✅ comandă anulată în Shopify" if not errs else "❌ Shopify: %s" % errs))


# ── CRON safety-net: comenzi open+unfulfilled > N min (Shopify Flow a ratat AWB-ul) → validează + fă AWB ──
def parse_iso(ts):
    import datetime
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


DUP_TAGS = ("duplicata", "duplicata3", "duplicat4")
# Comenzi PLASATE/gestionate de CS (replasare COD, swap, resend, modify) — cs-actions le taghează cu agentul CS.
# fulfill NU le atinge (nici AWB, nici dedup): le gestionează CS, sunt diferite de comenzile clientului.
CS_AGENT_TAGS = {"raluca", "oana", "andra", "anna", "oanao"}
# Comenzi de tip cadou UGC/influencer (100% discount, flux separat) — NU li se face AWB automat din cron.
INFLUENCER_TAGS = ("influencer",)


def shopify_unfulfilled(shop, token, since_date, max_pages=12):
    """Comenzi open + unfulfilled: [(name, createdAt, financialStatus, tags[], customerGid, sourceName)]. None la auth fail."""
    out, cursor = [], None
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:250%s, query:"fulfillment_status:unfulfilled AND status:open AND created_at:>=%s"){ '
             'edges{ cursor node{ name createdAt displayFinancialStatus tags sourceName customer{ id } } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and not out and d.get("errors"):
            return None
        for e in edges:
            n = e["node"]
            out.append((n.get("name"), n.get("createdAt"), n.get("displayFinancialStatus"),
                        [str(t).lower() for t in (n.get("tags") or [])], (n.get("customer") or {}).get("id"),
                        n.get("sourceName")))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    return out


def customer_is_newest(shop, token, customer_gid, this_created, since_date):
    """True dacă `this_created` e cea mai NOUĂ comandă NEANULATĂ a clientului în fereastră (regula „păstrează cea mai nouă").
    None dacă fără client / nu pot determina (apelantul tratează conservator)."""
    if not customer_gid:
        return None
    q = ('query{ customer(id:"%s"){ orders(first:50, query:"created_at:>=%s"){ edges{ node{ createdAt cancelledAt } } } } }'
         ) % (customer_gid, since_date)
    d = shopify_gql(shop, token, q)
    edges = ((((d.get("data") or {}).get("customer") or {}).get("orders") or {}).get("edges")) or []
    dates = [e["node"].get("createdAt") for e in edges if e.get("node") and not e["node"].get("cancelledAt")]
    if not dates:
        return None
    return (this_created or "") >= max(dates)  # ISO compară lexicografic = cronologic


def cancel_duplicate(sh, xc, o, st, name, apply):
    """Anulează un duplicat VECHI (protecție livrare: NU anulez ce a plecat). reason OTHER, fără refund/restock/notify.
    Întoarce: would-cancel | cancelled | shipped-skip | failed."""
    if has_awb(o) and awbprint_status(name) in PLECATA:
        return "shipped-skip"
    if not apply:
        return "would-cancel"
    if has_awb(o):
        body = {"orderId": o.get("orderId")}
        cid = (awb_doc(o) or {}).get("connectorId")
        if cid:
            body["connectorId"] = cid
        sv, dv = xc.post("/api/actions/cancel-shipping-label", body)
        if not (sv == 200 and isinstance(dv, dict) and dv.get("accepted")):
            return "failed"  # AWB plecat/eroare → NU anulez comanda
    node = find_order(st["shopDomain"], st["adminToken"], name)
    if not node or not node.get("id"):
        return "failed"
    # RESTOCK ON: comanda nu a plecat → trebuie repus stocul (altfel rămâne decrementat = scos din stoc).
    errs = shopify_order_cancel(st["shopDomain"], st["adminToken"], node["id"],
                                reason="OTHER", refund=False, restock=True, notify=False)
    return "cancelled" if not errs else "failed"


def _create_label(xc, body, tries=3):
    """POST create-shipping-label cu retry scurt pe eșec TRANZITORIU (throttle DPD pe rafală:
    429/5xx sau 422 generic 'Shipping label was not created'). O adresă real-proastă (HERE a trecut-o
    dar curierul o respinge) eșuează toate cele `tries` → rămâne la CS. Întoarce (ok, status, data)."""
    s = d = None
    for i in range(tries):
        s, d = xc.post("/api/actions/create-shipping-label", body)
        if s == 200 and isinstance(d, dict) and d.get("accepted") and \
           any(L.get("success") for L in (d.get("shippingLabels") or [])):
            return True, s, d
        msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
        transient = s in (429, 500, 502, 503, 504) or (s == 422 and "was not created" in msg)
        if not transient or i == tries - 1:
            break
        time.sleep(1.5 * (i + 1))  # backoff: 1.5s, 3s
    return False, s, d


def cmd_fulfill(a):
    """Safety-net peste Shopify Flow: comenzi open+unfulfilled mai vechi de --max-age-min (Flow a ratat AWB-ul) →
    VALID → fă AWB (create-shipping-label, DPD default); WRONG/UNKNOWN → corecție conservatoare → dacă devine
    VALID, fă AWB; altfel → CS. Sare cele cu AWB (Flow le-a făcut). DUPLICATE (tag duplicata/duplicata3/duplicat4):
    păstrează cea mai NOUĂ comandă a clientului (→ AWB), anulează cele VECHI (reason OTHER, fără refund/restock/notify,
    protecție livrare: nu anulez ce a plecat) — consistent cu Shopify Flow-urile. Dry-run by default (--apply scrie).
    Exclude magazinele externe (validator RO) + recomand --exclude Grandia (Dragon Star ≠ DPD)."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    max_age = getattr(a, "max_age_min", 15) or 15
    dto = now.date().isoformat()
    dfrom = (now - datetime.timedelta(days=a.days)).date().isoformat()
    toks_dom = {t["shopDomain"]: t for t in load_shopify_tokens()}
    since7 = (now - datetime.timedelta(days=7)).date().isoformat()
    for sh in load_shops():
        if skip_shop(sh, a):
            continue
        print("═" * 72)
        st = toks_dom.get(sh["shopDomain"])
        if not st:
            print("  %s — fără token Shopify → skip" % sh["shopDomain"]); continue
        xc = XC(sh["apiKey"])
        xmap = {o.get("orderName"): o for o in xc.orders(dfrom, dto)}
        unf = shopify_unfulfilled(st["shopDomain"], st["adminToken"], dfrom)
        if unf is None:
            print("  %s — Shopify auth FAIL (OAuth-rotation?) → skip" % sh["shopDomain"]); continue
        con, cons = pick_connector(xc, a)  # DPD Romania default — și pt externe (DPD livrează cross-border CZ/PL/BG)
        intl = sh["shopDomain"] in HERE_COUNTRY
        hkey = here_key() if intl else None
        ready = fixable = hard = had_awb = noxc = made = fixed = failed = team_n = infl = 0
        dup_keep = dup_cancel = dup_shipped = dup_unknown = 0
        for name, created, fin, tags, cust, source in unf:
            c = parse_iso(created)
            if not c or (now - c).total_seconds() / 60.0 <= max_age:
                continue
            o = xmap.get(name)
            if not o:
                noxc += 1; continue
            if has_awb(o):
                had_awb += 1; continue
            # CADOU UGC/INFLUENCER (tag `influencer`) → NU se expediază prin cron (flux separat). Skip ÎNAINTE
            # de team_placed, fiindcă multe sunt draft orders (altfel ar primi AWB ca team_placed).
            if any(tg in tags for tg in INFLUENCER_TAGS):
                infl += 1; continue
            # PLASATĂ DE CS (tag agent) sau prin DRAFT ORDER (replasare COD/swap/resend, UGC) → NU aplic dedup
            # (ar părea fals duplicat al comenzii vechi a clientului), DAR le fac AWB normal — sunt legitime de expediat.
            team_placed = any(t in CS_AGENT_TAGS for t in tags) or source == "shopify_draft_order"
            if team_placed:
                team_n += 1
            elif any(tg in tags for tg in DUP_TAGS):
                newest = customer_is_newest(st["shopDomain"], st["adminToken"], cust, created, since7)
                if newest is False:
                    res = cancel_duplicate(sh, xc, o, st, name, a.apply)
                    if res == "shipped-skip":
                        dup_shipped += 1
                    elif res in ("cancelled", "would-cancel"):
                        dup_cancel += 1
                    else:
                        failed += 1
                    continue
                if newest is None:
                    dup_unknown += 1; continue  # fără client → nu pot verifica → NU expediez, NU anulez
                dup_keep += 1  # e cea mai nouă (de păstrat) → cade prin la logica de AWB
            if intl:
                # extern (CZ/PL/BG): validatorul RO dă fals WRONG → validez cu HERE Geocoding
                ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
                do_awb = here_validate(ad, HERE_COUNTRY[sh["shopDomain"]], hkey) >= HERE_MIN_SCORE
                if do_awb:
                    ready += 1
                else:
                    hard += 1; continue
            else:
                ast = o.get("addressStatus")
                do_awb = ast in ("VALID", "PERFECT")
                if do_awb:
                    ready += 1
                else:
                    stt, _, _ = correct_address(xc, o, sh["shopDomain"], apply=False)
                    if stt != "would-correct":
                        hard += 1; continue
                    fixable += 1
                    if a.apply:
                        st2, _, _ = correct_address(xc, o, sh["shopDomain"], apply=True)
                        do_awb = (st2 == "corrected")
                        if do_awb:
                            fixed += 1
            if a.apply and do_awb:
                ocon = route_connector(sh, st, name, cons, con)  # Grandia: voluminos → Dragon Star
                if not ocon:
                    failed += 1; continue
                pcount = order_parcel_count(st["shopDomain"], st["adminToken"], name)  # nr colete din metafield
                body = {"orderId": o.get("orderId"), "connectorId": ocon["id"], "parcelCount": pcount,
                        "parcelType": "PARCEL", "notifyCustomer": bool(a.notify)}
                ok, _, _ = _create_label(xc, body)  # retry scurt pe throttle DPD (rafală)
                made += 1 if ok else 0
                failed += 0 if ok else 1
        print("  %s — unfulfilled >%dmin: AWB %d gata + %d corectabile + %d grele→CS  ·  DUP: %d păstrate, %d de-anulat, %d plecate(protejate), %d fără-client  ·  CS/draft (AWB fără dedup): %d · influencer-skip: %d  (aveau AWB %d, fără xc %d)"
              % (sh["shopDomain"], max_age, ready, fixable, hard, dup_keep, dup_cancel, dup_shipped, dup_unknown, team_n, infl, had_awb, noxc))
        if a.apply:
            print("  → APLICAT: AWB %d (din care %d după corecție) · duplicate anulate %d · eșuate %d" % (made, fixed, dup_cancel, failed))
        else:
            print("  → [DRY-RUN] AWB la %d gata + până la %d corectabile · aș anula %d duplicate vechi · %d → CS" % (ready, fixable, dup_cancel, hard))


# ── FACTURI prin API (/api/actions/*-invoice) — mirror AWB: make / cancel / storno(revert) / regen / doc ──
# Connector de facturare = tip SMART_BILL (din connectors). Dry-run by default; POST real DOAR cu --apply.
def billing_connectors(xc):
    return [c for c in xc.list_connectors() if c.get("active") and (c.get("type") or "").upper() in BILLING_TYPES]


def pick_billing(xc, a):
    """(connector_facturare|None, lista). None = ambiguu/absent → cere --connector."""
    bills = billing_connectors(xc)
    if getattr(a, "connector", None):
        try:
            cid = int(a.connector)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return None, bills
        m = [c for c in xc.list_connectors() if c.get("id") == cid]
        if m and (m[0].get("type") or "").upper() not in BILLING_TYPES:
            print("  ⚠ connectorul %s (%s) NU e de facturare — pt facturi alege un connector SMART_BILL (vezi `connectors`)." % (cid, m[0].get("type")))
            return None, bills
        return (m[0] if m else {"id": cid, "name": "?", "type": "?"}), bills
    if len(bills) == 1:
        return bills[0], bills
    return None, bills


def inv_doc(o):
    for d in (o.get("documents") or []):
        if isinstance(d, dict) and d.get("documentType") == "INVOICE":
            return d
    return None


def _invoice_result(s, d):
    if s != 200 or not isinstance(d, dict):
        print("  ❌ eroare: %s" % _err_text(s, d)); return
    if not d.get("accepted"):
        print("  ❌ respins: %s" % _err_text(s, d)); return
    if not (d.get("invoices") or []):
        print("  ✅ acceptat (fără detaliu factură în răspuns)"); return
    for inv in (d.get("invoices") or []):
        if inv.get("success"):
            print("  ✅ %sfactură %s %s" % ("STORNO " if inv.get("storno") else "",
                                            inv.get("invoiceSerie") or "", inv.get("invoiceNumber") or ""))
        else:
            print("  ❌ factură: %s" % inv.get("errorMessage"))


def _inv_resolve(a):
    rid = getattr(a, "refund_id", None)
    if rid is not None:
        try:
            int(rid)
        except (TypeError, ValueError):
            print("  --refund-id trebuie să fie numeric (Shopify refund ID) — abort, ca să nu fac storno total din greșeală."); return None
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită%s." % (a.order, " în %s" % a.shop if a.shop else " (căutat în toate)")); return None
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return None
    con, bills = pick_billing(xc, a)
    if not con:
        print("  Connector de facturare ambiguu/absent — alege --connector ID:")
        for c in bills:
            print("    %-7s %-14s %s" % (c.get("id"), c.get("type"), c.get("name")))
        return None
    return sh, xc, o, con


def _inv_body(o, con, a):
    body = {"orderId": o.get("orderId"), "connectorId": con["id"]}
    if getattr(a, "lang", None):
        body["languageCode"] = a.lang
    rid = getattr(a, "refund_id", None)
    if rid:
        try:
            body["refundId"] = int(rid)
        except (TypeError, ValueError):
            pass
    return body


def cmd_inv_make(a):
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    if inv_doc(o):
        print("  ⚠ %s are deja factură — folosește inv-regen ca să o refaci (anulează + creează)." % a.order); return
    body = _inv_body(o, con, a)
    print("═" * 60)
    print("  FACTURĂ make · %s (%s) · %s [%s]" % (a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/create-invoice:\n    %s" % json.dumps(body)); return
    _invoice_result(*xc.post("/api/actions/create-invoice", body))


def _inv_simple(a, endpoint, label):
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    body = _inv_body(o, con, a)
    print("═" * 60)
    print("  %s · %s (%s) · %s [%s]" % (label, a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    if not a.apply:
        print("  DRY-RUN — aș POST %s:\n    %s" % (endpoint, json.dumps(body))); return
    _invoice_result(*xc.post(endpoint, body))


def cmd_inv_cancel(a):
    _inv_simple(a, "/api/actions/cancel-invoice", "FACTURĂ cancel")


def cmd_inv_storno(a):
    _inv_simple(a, "/api/actions/revert-invoice", "FACTURĂ STORNO")


def cmd_inv_regen(a):
    """Anulează factura curentă și o reface (ca awb-regen). Create gardat pe succesul cancel-ului."""
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    print("═" * 60)
    print("  REGEN FACTURĂ · %s (%s) · %s [%s]" % (a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    print("  pas 1: anulez factura curentă · pas 2: creez una nouă")
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    cv = xc.post("/api/actions/cancel-invoice", {"orderId": o.get("orderId"), "connectorId": con["id"]})
    ok = (cv[0] == 200 and isinstance(cv[1], dict) and cv[1].get("accepted"))
    print("  cancel: %s" % ("✅" if ok else "❌ %s: %s" % (cv[0], _err_text(*cv))))
    if not ok:
        print("  ⛔ anulare factură eșuată → NU recreez."); return
    time.sleep(1)
    _invoice_result(*xc.post("/api/actions/create-invoice", _inv_body(o, con, a)))


def cmd_inv_doc(a):
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    d = inv_doc(o)
    if not d:
        print("  %s nu are factură (document INVOICE)." % a.order); return
    print("  %s (%s) · factură %s" % (a.order, sh["shopDomain"], d.get("name") or ""))
    print("  PDF: %s" % (d.get("url") or "—"))


# ── Facturare în MASĂ: toate comenzile PLĂTITE fără factură (shipping inclus, data = azi) ──
def _resolve_target_shops(shop_arg, shops):
    """--shop = CSV de domenii myshopify SAU prefixe de comandă (GT, GRAN, …) SAU 'all'/gol = toate."""
    if not shop_arg or shop_arg.strip().lower() == "all":
        return shops
    wanted = set()
    for tok in shop_arg.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "." in tok:
            wanted.add(tok)                                        # domeniu complet
        elif tok.upper() in PREFIX_DOMAIN:
            wanted.add(PREFIX_DOMAIN[tok.upper()] + ".myshopify.com")  # prefix comandă
        else:
            wanted.add(tok + ".myshopify.com")                    # subdomeniu „gol"
    return [sh for sh in shops if sh["shopDomain"] in wanted]


def shopify_paid_uninvoiced(shop, token, since_date, max_pages=40):
    """Comenzile Shopify PLĂTITE din fereastră, eligibile de facturat:
    payment status = PAID, NEanulate, FĂRĂ refund, total (încasări) > 0, NEtest.
    Întoarce listă de (orderName, {total}). None la auth fail."""
    out, cursor, truncated = [], None, False
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:100%s, sortKey:CREATED_AT, reverse:true, query:"financial_status:paid AND created_at:>=%s"){ '
             'edges{ cursor node{ name cancelledAt test displayFinancialStatus '
             'currentTotalPriceSet{ shopMoney{ amount } } '
             'totalRefundedSet{ shopMoney{ amount } } } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and not out and d.get("errors"):
            return None
        for e in edges:
            n = e["node"]
            if n.get("cancelledAt") or n.get("test"):
                continue
            if (n.get("displayFinancialStatus") or "").upper() != "PAID":
                continue
            refunded = float((((n.get("totalRefundedSet") or {}).get("shopMoney")) or {}).get("amount") or 0)
            if refunded > 0:
                continue
            total = float((((n.get("currentTotalPriceSet") or {}).get("shopMoney")) or {}).get("amount") or 0)
            if total <= 0:
                continue
            out.append((n.get("name"), {"total": total}))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    else:
        truncated = True
    if truncated:
        sys.stderr.write("  ⚠️ %s: paginare oprită la plafon (%d pag) — restrânge --days\n" % (shop, max_pages))
    return out


def shopify_status_by_ids(shop, token, ids, batch=50):
    """{order_id_numeric: {paid, total, refunded, cancelled, name}} pt o LISTĂ de Shopify order IDs.
    Interoghează DOAR aceste comenzi (nodes by id, în loturi) — minimul de apeluri Shopify, dar fresh.
    Folosit de inv-bulk: candidații vin din xConnector (fără factură), aici verificăm DOAR plata lor,
    în loc să scanăm toate comenzile plătite (≈80% mai puține apeluri pe rația Shopify partajată).
    {} dacă nu sunt IDs. REÎNCEARCĂ ID-urile lipsă (lot throttlat/incomplet) până nu mai e progres —
    altfel la scară (mii de comenzi, zeci de loturi) Shopify throttle face să se piardă TĂCUT loturi
    întregi și subnumără plătiții (ex Ofertele: 48 în loc de ~400)."""
    out = {}
    def _gid(x):
        x = str(x)
        return x if x.startswith("gid://") else "gid://shopify/Order/%s" % x
    def _num(x):
        return str(x).rsplit("/", 1)[-1]
    uniq = [str(i) for i in dict.fromkeys(ids) if i]
    if not uniq:
        return out
    q = ('query($ids:[ID!]!){ nodes(ids:$ids){ ... on Order { id name cancelledAt test '
         'displayFinancialStatus currentTotalPriceSet{ shopMoney{ amount } } '
         'totalRefundedSet{ shopMoney{ amount } } } } }')
    pending = list(uniq)
    for _round in range(6):
        if not pending:
            break
        still = []
        for k in range(0, len(pending), batch):
            chunk_ids = pending[k:k + batch]
            d = shopify_gql(shop, token, q, {"ids": [_gid(i) for i in chunk_ids]})
            got = set()
            for n in (((d.get("data") or {}).get("nodes")) or []):
                if not isinstance(n, dict) or not n.get("id"):
                    continue
                num = _num(n["id"]); got.add(num)
                total = float((((n.get("currentTotalPriceSet") or {}).get("shopMoney")) or {}).get("amount") or 0)
                refunded = float((((n.get("totalRefundedSet") or {}).get("shopMoney")) or {}).get("amount") or 0)
                out[num] = {
                    "paid": (n.get("displayFinancialStatus") or "").upper() == "PAID",
                    "total": total, "refunded": refunded,
                    "cancelled": bool(n.get("cancelledAt")) or bool(n.get("test")),
                    "name": n.get("name"),
                }
            still.extend(i for i in chunk_ids if _num(i) not in got)
        if len(still) >= len(pending):
            break   # zero progres → restul sunt probabil chiar inaccesibile (șterse), nu throttle
        pending = still
    return out


def _create_invoice_rl(xc, body, max_retry=6):
    """create-invoice respectând rata SmartBill. LIMITA REALĂ MĂSURATĂ: X-RateLimit-Limit=30/fereastră
    (≈30/min); la depășire SmartBill dă 403 fără Retry-After + o penalizare „lipicioasă" (blocaj care
    NU se ridică repede). Strategia: pasăm SUB limită (vezi pacing-ul adaptiv din emit) ca să nu declanșăm
    penalizarea; aici doar retry RĂBDĂTOR cu cooldown de-o-fereastră dacă totuși o lovim.
    Întoarce (ok, status, data, rate_limited) — rate_limited=True dacă am renunțat din cauza limitei."""
    s, d = None, None
    for attempt in range(max_retry):
        s, d = xc.post("/api/actions/create-invoice", body)
        txt = (json.dumps(d) if isinstance(d, (dict, list)) else str(d)).lower()
        # ATENȚIE: SmartBill folosește HTTP 422 pt AMBELE — rate-limit ȘI erori de business (produs fără
        # cod, date lipsă etc.). Tratăm 422 ca rate-limit DOAR dacă MESAJUL confirmă; altfel e eroare reală
        # care NU se rezolvă prin retry (nu mai pierdem ~12 min/ordin retrying o eroare permanentă).
        # Rate-limit: „Ai depasit limita maxima de requesturi admisa. Vei putea executa alte requesturi dupa N min".
        rl_text = ("depasit limita maxima" in txt) or ("requesturi admisa" in txt) \
            or ("executa alte requesturi" in txt) or ("too many request" in txt) \
            or ("rate limit" in txt) or ("rate-limit" in txt) or ("throttl" in txt)
        rate = rl_text or (s == 429)   # 429 = clar rate-limit; 422/403 DOAR cu mesaj de rate-limit
        if rate:
            m = re.search(r"dup[ăa]\s+(\d+)\s*min", txt)   # cooldown-ul exact din mesaj („dupa 10 min")
            if m:
                wait = int(m.group(1)) * 60 + 30
            elif s == 422 or rl_text:
                wait = 600   # penalizarea 422 lipicioasă ≈10 min — NU re-ataca des (resetează timerul)
            else:
                wait = min(60 * (attempt + 1), 180)   # 429 gentil
            wait = min(wait, 660)
            print("    ⏳ rate-limit SmartBill (status %s) — pauză %ds (retry %d/%d)" % (s, wait, attempt + 1, max_retry), flush=True)
            time.sleep(wait); continue
        ok = s == 200 and isinstance(d, dict) and d.get("accepted") and \
            (not (d.get("invoices") or []) or any(i.get("success") for i in d.get("invoices") or []))
        return ok, s, d, False
    return False, s, d, True


_SCAN_CAP_GUARD = 9500   # plafonul xConnector ≈10000; bisectăm la ≥9500 fiindcă uneori întoarce 9999
                         # (10000 minus duplicate dedup-ate) — un prag de 10000 ratează exact cazul ăsta (ex Esteban).


def _dedup_orders(lists):
    seen, out = set(), []
    for o in lists:
        nm = o.get("orderName")
        if nm and nm not in seen:
            seen.add(nm); out.append(o)
    return out


def _scan_all_orders(xc, dfrom, dto, depth=0):
    """Scanează TOATE comenzile din [dfrom,dto], OCOLIND plafonul xConnector `getOrders` (≈10000/cerere,
    pagina 50/offset 10000 → 400). STRATEGIE: la depth 0, dacă fereastra e mare (>25 zile), o sparge din
    start în FELII FIXE de ~20 zile (sub plafon pt magazinele noastre) — evită risipa de a scana 50 pagini
    (~75s) doar ca să DESCOPERE că o fereastră e capată înainte s-o împartă. Bisecția pe dată rămâne ca
    plasă de siguranță: dacă o felie tot atinge plafonul (spike, ex Black Friday), se înjumătățește recursiv.
    Altfel magazinele mari (Ofertele, Reduceri, Esteban…) pierd comenzile mai vechi de ultimele ~10000."""
    import datetime
    d0 = datetime.date.fromisoformat(dfrom)
    d1 = datetime.date.fromisoformat(dto)
    span = (d1 - d0).days
    if depth == 0 and span > 25:
        parts, cur = [], d0
        while cur <= d1:
            chunk_to = min(cur + datetime.timedelta(days=19), d1)
            parts.extend(_scan_all_orders(xc, cur.isoformat(), chunk_to.isoformat(), depth=1))
            cur = chunk_to + datetime.timedelta(days=1)
        return _dedup_orders(parts)
    rows = list(xc.orders(dfrom, dto, {"sort": "date", "sortDir": "desc"}))
    if len(rows) < _SCAN_CAP_GUARD or span <= 1 or depth >= 9:
        return rows   # sub plafon (sau nu mai pot împărți) — complet
    mid = d0 + datetime.timedelta(days=span // 2)   # felie tot capată (spike) → bisectează
    left = _scan_all_orders(xc, dfrom, mid.isoformat(), depth + 1)
    right = _scan_all_orders(xc, (mid + datetime.timedelta(days=1)).isoformat(), dto, depth + 1)
    return _dedup_orders(left + right)


def cmd_inv_bulk(a):
    """Facturează TOATE comenzile plătite din ultimele --days zile (≈2 luni) care NU au factură,
    nu-s anulate/refunded și au încasări > 0. Shipping = inclus automat de SmartBill; data facturii = azi.
    Dry-run by default; emite facturi DOAR cu --apply."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    if not shops:
        print("Nicio configurație xConnector (KB XCONNECTOR_SHOPS)."); return
    toks = {t.get("shopDomain"): t.get("adminToken") for t in load_shopify_tokens()}
    targets = _resolve_target_shops(a.shop, shops)
    if getattr(a, "exclude", ""):
        ex = set(d["shopDomain"] for d in _resolve_target_shops(a.exclude, shops))
        before = len(targets)
        targets = [sh for sh in targets if sh["shopDomain"] not in ex]
        if before != len(targets):
            print("  (exclus %d magazin(e) deja-procesate: %s)" % (before - len(targets), a.exclude))
    if not targets:
        print("Niciun magazin potrivit pt --shop=%r. Folosește prefix (GT) / domeniu / 'all'." % a.shop); return
    print("═" * 64)
    print("FACTURARE ÎN MASĂ · fereastră %s → %s (%d zile) · %s" % (
        dfrom, dto, a.days, "APPLY (emite real)" if a.apply else "DRY-RUN (nimic emis)"))
    print("Criterii: payment=PAID · neanulate · fără refund · încasări>0 · fără factură. Shipping inclus, data=azi.")
    print("═" * 64)
    G = dict(cand=0, inv=0, err=0, skip_inv=0, skip_xc=0, paid=0)
    errmsgs = {}   # mesaj de eroare business → câte ori (ex „Produsul LIVRARE EXPRESS nu are codul specificat")
    # Pacing ADAPTIV pt SmartBill (limită reală ≈30/min, penalizare lipicioasă la depășire).
    # Pornim sub limită și încetinim singuri dacă totuși o atingem; persistă ÎNTRE magazine.
    pace = 2.5   # secunde/factură ≈ 24/min — SUB plafonul de 30/fereastră, cu headroom pt
                 # fluxul normal de facturare al xConnector care consumă din ACELAȘI bucket SmartBill
    for sh in targets:
        dom = sh["shopDomain"]
        st = toks.get(dom)
        if not st:
            print("\n══ %s ══  ⚠ fără token Shopify (SHOPIFY_ADMIN_TOKENS) → skip" % dom); continue
        xc = XC(sh["apiKey"])
        con, bills = pick_billing(xc, a)
        if not con:
            tail = (" (am: %s)" % ", ".join("%s=%s" % (c.get("id"), c.get("type")) for c in bills)) if bills else ""
            print("\n══ %s ══  ⚠ connector de facturare ambiguu/absent → alege --connector ID%s → skip" % (dom, tail)); continue
        # 1) xConnector: TOATE comenzile din fereastră + statusul facturii (ZERO Shopify — bridge-ul nostru).
        #    _scan_all_orders bisectează fereastra ca să treacă de plafonul de 10000/cerere (altfel magazinele
        #    mari pierd comenzile mai vechi de cele mai recente 10000).
        xorders = []
        try:
            scanned = _scan_all_orders(xc, dfrom, dto)
        except Exception as e:
            print("\n══ %s ══  ⚠ scanare xConnector eșuată (%s) → SKIP magazinul (reia la rularea următoare)" % (dom, str(e)[:120]))
            continue
        for o in scanned:
            nm = o.get("orderName")
            if nm:
                xorders.append((nm, o.get("orderId"), inv_doc(o) is not None))
        n_inv = sum(1 for _, _, hi in xorders if hi)
        uninvoiced = [(nm, oid) for nm, oid, hi in xorders if not hi and oid]
        # 2) Shopify TARGETAT: verifică plata DOAR pt comenzile fără factură (mic + fresh),
        #    în loc să scanăm toate comenzile plătite (≈80% mai puține apeluri pe rația partajată)
        stat = shopify_status_by_ids(dom, st, [oid for _, oid in uninvoiced])
        if uninvoiced and not stat:
            print("\n══ %s ══  ⚠ Shopify auth/empty → skip" % dom); continue
        # 3) păstrează PLĂTITE + neanulate + fără refund + total>0
        todo, n_paid = [], 0
        for nm, oid in uninvoiced:
            s_ = stat.get(str(oid).rsplit("/", 1)[-1])
            if not s_ or not s_["paid"] or s_["cancelled"] or s_["refunded"] > 0 or s_["total"] <= 0:
                continue
            todo.append((nm, oid, s_["total"])); n_paid += 1
        G["cand"] += len(todo); G["skip_inv"] += n_inv; G["paid"] += n_paid
        print("\n══ %s ══  [%s %s]" % (dom, con.get("type"), con.get("id")))
        print("  comenzi xConnector: %d · cu factură: %d · fără factură: %d · din care PLĂTITE de facturat: %d" % (
            len(xorders), n_inv, len(uninvoiced), len(todo)))
        # SAFETY: dacă aproape NICIUNA din comenzile plătite găsite în xConnector n-are factură ÎN xConnector,
        # magazinul facturează probabil ALTUNDE (SmartBill direct) → facturile nu apar aici → risc de DUBLĂ factură.
        matched = n_inv + len(todo)
        if matched >= 20 and (n_inv / matched) < 0.10:
            print("  🚩 DOAR %.0f%% din comenzile plătite din xConnector au factură ÎN xConnector → %s facturează probabil ALTUNDE" % (
                100.0 * n_inv / matched, dom))
            print("     (SmartBill direct / alt sistem). Facturile existente NU apar aici ⇒ RISC DE DUBLĂ FACTURĂ.")
            if a.apply and not a.force:
                print("     ⛔ SKIP emitere (fără --force). Verifică întâi în SmartBill, apoi `--apply --force` dacă chiar trebuie.")
                continue
        done = 0
        for name, oid, total in todo:
            if a.limit and done >= a.limit:
                print("  … oprit la --limit %d (mai sunt %d)" % (a.limit, len(todo) - done)); break
            if not a.apply:
                print("  • DRY factură %-12s orderId=%s total=%.2f" % (name, oid, total)); done += 1; continue
            body = {"orderId": oid, "connectorId": con["id"]}
            if getattr(a, "lang", None):
                body["languageCode"] = a.lang
            ok, s, d, limited = _create_invoice_rl(xc, body)
            if ok:
                inv = next((i for i in (d.get("invoices") or []) if i.get("success")), {})
                print("  ✅ %-12s → %s %s" % (name, inv.get("invoiceSerie") or "", inv.get("invoiceNumber") or "")); G["inv"] += 1
            else:
                em = ""
                if isinstance(d, dict):
                    invs = d.get("invoices") or []
                    em = (invs[0].get("errorMessage") if invs and isinstance(invs[0], dict) else None) or d.get("errorMessage") or ""
                em = (em or _err_text(s, d)).strip()[:90]
                errmsgs[em] = errmsgs.get(em, 0) + 1
                print("  ❌ %-12s → %s" % (name, em)); G["err"] += 1
            if limited:
                # am atins penalizarea SmartBill chiar și după retries → încetinim GLOBAL (persistă între magazine)
                old = pace; pace = min(pace + 0.7, 4.0)
                if pace != old:
                    print("  🐢 încetinesc la %.1fs/factură (≈%d/min) ca să nu mai lovesc limita SmartBill" % (pace, round(60 / pace)), flush=True)
                time.sleep(90)   # cooldown suplimentar ca să se ridice penalizarea lipicioasă
            done += 1
            time.sleep(pace)   # pacing adaptiv ≈28/min, SUB limita reală SmartBill de 30/fereastră
    print("\n" + "═" * 64)
    print("TOTAL: candidați plătite-fără-factură=%d · %s · deja facturate(xConnector)=%d · erori=%d" % (
        G["cand"], ("FACTURATE=%d" % G["inv"]) if a.apply else "DRY-RUN (0 emise)", G["skip_inv"], G["err"]))
    if errmsgs:
        print("ERORI DE BUSINESS (NU rate-limit — necesită fix config SmartBill/produs, NU se rezolvă prin retry):")
        for msg, n in sorted(errmsgs.items(), key=lambda kv: -kv[1]):
            print("  %4d×  %s" % (n, msg))
    if not a.apply and G["cand"]:
        print("→ Rulează din nou cu --apply ca să emiți cele %d facturi." % G["cand"])


# ── CAPTURE COD: PENDING + LIVRAT → mark paid · REFUZAT → tag · ÎN CURS → verifică DPD ──
DELIVERED_ST = {"delivered"}   # COLECTAT + plătit COD. „customer_pickup" = pregătit la locker, NU încă ridicat → în curs.
REFUSED_ST = {"back_to_sender", "returning_to_sender", "refused", "lost", "lost_in_transit"}
PROGRESS_ST = {"in_transit", "waiting_for_courier", "deferred_delivery", "redirected", "on_hold", "customer_pickup",
               "fulfilled", "not_fulfilled", "unsuccessful_delivery", "awaiting_shipment_generation_initialization", None}
# {incorrect_address, errors_incorrect_shipping_address, cancelled} = NU le atingem (CS / deja anulate)


def awbprint_batch(names):
    """{order_number: (aggregated_status, tracking_number, courier_name)} dintr-o singură conexiune AWBprint."""
    out = {}
    if not names:
        return out
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return out
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return out
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select order_number, aggregated_status, tracking_number, courier_name "
                       "from orders where order_number = any(:ns) order by id desc", ns=list(names))
        for nm, st, trk, cur in rows:
            if nm not in out:   # prima = cea mai nouă (id desc)
                out[nm] = (st, trk, cur)
    except Exception:
        pass
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass
    return out


def _dpd_creds():
    u = os.environ.get("DPD_RO_USERNAME"); p = os.environ.get("DPD_RO_PASSWORD")
    if not u:
        try: u = subprocess.run(["uv", "run", KB, "secret-get", "DPD_RO_USERNAME"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception: u = ""
    if not p:
        try: p = subprocess.run(["uv", "run", KB, "secret-get", "DPD_RO_PASSWORD"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception: p = ""
    return (u, p) if (u and p) else (None, None)


def dpd_track_sync(awbs):
    """{awb: latest_description} via api.dpd.ro/v1/track (batch de 10). {} fără creds/AWB."""
    out = {}
    u, p = _dpd_creds()
    uniq = [a for a in dict.fromkeys(awbs) if a]
    if not (u and p) or not uniq:
        return out
    for i in range(0, len(uniq), 10):
        batch = uniq[i:i + 10]
        body = {"userName": u, "password": p, "language": "EN", "lastOperationOnly": True,
                "parcels": [{"id": a} for a in batch]}
        try:
            s, b = http("POST", "https://api.dpd.ro/v1/track", {"Content-Type": "application/json"}, body)
            d = json.loads(b)
            if not isinstance(d, dict) or d.get("error"):
                continue
            for awb, parcel in zip(batch, d.get("parcels") or []):
                if not isinstance(parcel, dict) or parcel.get("error"):
                    continue
                ops = parcel.get("operations") or []
                if not ops:
                    continue
                latest = max(ops, key=lambda o: o.get("dateTime", ""))
                out[awb] = (latest.get("description") or "").strip()
        except Exception:
            pass
        time.sleep(0.2)
    return out


def _dpd_state(desc):
    """Mapează descrierea DPD (EN) în delivered / refused / progress. CONSERVATOR: doar stările FINALE clare.
    'Returned to Office'/'Prepared for Self-collecting' = ÎN CURS (poate fi redlivrat/ridicat), NU refuz/livrare."""
    d = (desc or "").lower()
    if any(k in d for k in ("not deliver", "undeliver", "unsuccess", "failed deliver")):
        return "progress"
    if ("delivered" in d) or ("collected by" in d) or ("self-collected" in d) or ("picked up by" in d):
        return "delivered"
    if ("refus" in d) or ("reject" in d) or ("returned to sender" in d) or ("return to sender" in d) \
       or ("returning to sender" in d) or ("returned to consignor" in d):
        return "refused"
    return "progress"


def shopify_pending_orders(shop, token, since_date, max_pages=40):
    """Comenzi cu payment status PENDING, neanulate, total>0, în fereastră. [(name, gid, total)]. None la auth fail."""
    out, cursor = [], None
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:100%s, sortKey:CREATED_AT, reverse:true, query:"financial_status:pending AND created_at:>=%s"){ '
             'edges{ cursor node{ id name cancelledAt test displayFinancialStatus '
             'currentTotalPriceSet{ shopMoney{ amount } } } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and not out and d.get("errors"):
            return None
        for e in edges:
            n = e["node"]
            if n.get("cancelledAt") or n.get("test"):
                continue
            if (n.get("displayFinancialStatus") or "").upper() != "PENDING":
                continue
            total = float((((n.get("currentTotalPriceSet") or {}).get("shopMoney")) or {}).get("amount") or 0)
            if total <= 0:
                continue
            out.append((n.get("name"), n.get("id"), total))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    return out


def shopify_mark_paid(shop, token, gid):
    d = shopify_gql(shop, token, 'mutation($id:ID!){ orderMarkAsPaid(input:{id:$id}){ order{ displayFinancialStatus } userErrors{ field message } } }', {"id": gid})
    r = ((d.get("data") or {}).get("orderMarkAsPaid")) or {}
    ue = r.get("userErrors") or []
    return (not ue and not d.get("errors")), (ue or d.get("errors") or (r.get("order") or {}).get("displayFinancialStatus"))


def shopify_add_tags(shop, token, gid, tags):
    d = shopify_gql(shop, token, 'mutation($id:ID!,$t:[String!]!){ tagsAdd(id:$id, tags:$t){ userErrors{ field message } } }', {"id": gid, "t": tags})
    r = ((d.get("data") or {}).get("tagsAdd")) or {}
    ue = r.get("userErrors") or []
    return (not ue and not d.get("errors")), (ue or d.get("errors"))


def cmd_capture(a):
    """Pt comenzile COD PENDING din ultimele --days zile:
      LIVRATE → mark paid (orderMarkAsPaid) · REFUZATE/întoarse → tag 'refuzata' · ÎN CURS → verific live DPD → resolv.
    Apoi `inv-bulk` facturează cele plătite. Sursa status = AWBprint (aggregated_status), cross-check DPD pe cele în curs.
    Dry-run by default; scrie în Shopify DOAR cu --apply."""
    import datetime
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    toks = {t.get("shopDomain"): t.get("adminToken") for t in load_shopify_tokens()}
    targets = _resolve_target_shops(a.shop, shops)
    if not targets:
        print("Niciun magazin potrivit pt --shop=%r." % a.shop); return
    print("═" * 64)
    print("CAPTURE COD · de la %s · %s" % (dfrom, "APPLY (scrie în Shopify)" if a.apply else "DRY-RUN"))
    print("PENDING → livrat=mark paid · refuzat/întors=tag 'refuzata' · în curs=verific DPD live → resolv.")
    print("═" * 64)
    G = dict(pend=0, paid=0, ref=0, prog=0, err=0, skip=0)
    for sh in targets:
        dom = sh["shopDomain"]; st = toks.get(dom)
        if not st:
            print("\n══ %s ══  ⚠ fără token Shopify → skip" % dom); continue
        pend = shopify_pending_orders(dom, st, dfrom)
        if pend is None:
            print("\n══ %s ══  ⚠ Shopify auth fail → skip" % dom); continue
        G["pend"] += len(pend)
        awb = awbprint_batch([p[0] for p in pend])
        # 1) clasific din AWBprint; strâng cele „în curs" pe DPD (doar curier DPD + are tracking)
        actions = {}   # name -> ('paid'|'refuzata'|'leave')
        dpd_check = {}  # name -> tracking
        for name, gid, total in pend:
            stt, trk, cur = awb.get(name, (None, None, None))
            if stt in DELIVERED_ST:
                actions[name] = "paid"
            elif stt in REFUSED_ST:
                actions[name] = "refuzata"
            elif stt in ("incorrect_address", "errors_incorrect_shipping_address", "cancelled"):
                actions[name] = "leave"
            else:  # în curs / fără status
                if trk and cur and "dpd" in (cur or "").lower():
                    dpd_check[name] = trk
                else:
                    actions[name] = "leave"
        # 2) DPD live pe cele în curs
        if dpd_check:
            res = dpd_track_sync(list(dpd_check.values()))
            inv = {v: k for k, v in dpd_check.items()}
            for trk, desc in res.items():
                nm = inv.get(trk)
                if not nm:
                    continue
                stt = _dpd_state(desc)
                actions[nm] = "paid" if stt == "delivered" else ("refuzata" if stt == "refused" else "leave")
            for nm in dpd_check:
                actions.setdefault(nm, "leave")
        n_paid = sum(1 for v in actions.values() if v == "paid")
        n_ref = sum(1 for v in actions.values() if v == "refuzata")
        n_leave = sum(1 for v in actions.values() if v == "leave")
        print("\n══ %s ══  PENDING: %d → de marcat PAID(livrate): %d · de tag-uit 'refuzata': %d · lăsate(în curs/CS): %d  [DPD verificate: %d]" % (
            dom, len(pend), n_paid, n_ref, n_leave, len(dpd_check)))
        done = 0
        for name, gid, total in pend:
            act = actions.get(name, "leave")
            if act == "leave":
                continue
            if a.limit and done >= a.limit:
                print("  … oprit la --limit %d" % a.limit); break
            if not a.apply:
                print("  • DRY %-9s %-12s total=%.2f" % (act.upper(), name, total)); done += 1; continue
            if act == "paid":
                ok, info = shopify_mark_paid(dom, st, gid)
                print("  %s %-12s → PAID" % ("✅" if ok else "❌", name) if ok else "  ❌ %-12s mark-paid: %s" % (name, info))
                G["paid" if ok else "err"] += 1
            else:  # refuzata
                ok, info = shopify_add_tags(dom, st, gid, ["refuzata"])
                print("  %s %-12s → tag 'refuzata'" % ("🏷️" if ok else "❌", name) if ok else "  ❌ %-12s tag: %s" % (name, info))
                G["ref" if ok else "err"] += 1
            done += 1
            time.sleep(0.15)
    print("\n" + "═" * 64)
    print("TOTAL: pending=%d · %s · %s · erori=%d" % (
        G["pend"],
        ("PAID=%d · tag refuzata=%d" % (G["paid"], G["ref"])) if a.apply else "DRY (0 scrise)",
        "—", G["err"]))
    if not a.apply:
        print("→ --apply ca să scrii în Shopify, apoi `inv-bulk --apply` ca să facturezi cele plătite.")


# ── Setare adresă comandă (Shopify orderUpdate.shippingAddress) → opțional AWB ──
# Pt comenzi COD adresa SE poate modifica (line items NU — ăla e cancel+replace). Dry-run by default.
def shopify_order_address(shop, token, name):
    """(gid, shippingAddress curentă) a comenzii după nume. (None, {}) dacă negăsită."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ id shippingAddress{ '
         'address1 address2 city zip province provinceCode countryCodeV2 firstName lastName phone company } } } } }'
         ) % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return None, {}
    n = edges[0]["node"]
    return n.get("id"), (n.get("shippingAddress") or {})


def cmd_addr_set(a):
    """Setează adresa de livrare în Shopify la valorile date (păstrează restul), opțional face AWB (--make-awb).
    Pt COD adresa e modificabilă. Dry-run by default; orderUpdate real DOAR cu --apply."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    st = {t.get("shopDomain"): t for t in load_shopify_tokens()}.get(sh["shopDomain"])
    if not st:
        print("  fără token Shopify pt %s." % sh["shopDomain"]); return
    gid, cur = shopify_order_address(st["shopDomain"], st["adminToken"], a.order)
    if not gid:
        print("  Comanda %s negăsită în Shopify (%s)." % (a.order, sh["shopDomain"])); return
    given = {"address1": a.address1, "address2": a.address2, "city": a.city,
             "zip": a.zip, "province": a.province, "phone": a.phone}
    if not any(v for v in given.values()):
        print("  Nu ai dat niciun câmp (--address1/--address2/--city/--zip/--province/--phone)."); return
    new = {"countryCode": (a.country or cur.get("countryCodeV2") or "RO")}
    for k in ("address1", "address2", "city", "zip", "province", "phone"):
        v = given.get(k) if given.get(k) is not None else cur.get(k)
        if v is not None:
            new[k] = v
    for k in ("firstName", "lastName", "company"):
        if cur.get(k):
            new[k] = cur.get(k)
    if not given.get("province") and cur.get("provinceCode"):
        new["provinceCode"] = cur.get("provinceCode")
    print("═" * 60)
    print("  ADRESĂ set · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  curent: %s, %s %s (%s)" % (cur.get("address1"), cur.get("city"), cur.get("zip"), cur.get("province")))
    print("  nou   : %s, %s %s (%s)" % (new.get("address1"), new.get("city"), new.get("zip"), new.get("province")))
    if not a.apply:
        print("  DRY-RUN — aș orderUpdate shippingAddress%s." % ("  + apoi awb-make" if a.make_awb else "")); return
    m = "mutation($input: OrderInput!){ orderUpdate(input:$input){ order{ id } userErrors{ field message } } }"
    d = shopify_gql(st["shopDomain"], st["adminToken"], m, {"input": {"id": gid, "shippingAddress": new}})
    errs = (((d.get("data") or {}).get("orderUpdate") or {}).get("userErrors")) or d.get("errors")
    if errs:
        print("  ❌ Shopify orderUpdate: %s" % errs); return
    print("  ✅ adresă actualizată în Shopify")
    if a.make_awb:
        print("  → aștept ca xConnector să resincronizeze adresa nouă, apoi fac AWB...")
        target = (str(new.get("zip") or ""), (new.get("city") or "").lower(), (new.get("address1") or "").lower())
        synced = False
        for _ in range(10):  # ~30s
            time.sleep(3)
            ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
            if (str(ad.get("zip") or ""), (ad.get("city") or "").lower(), (ad.get("address1") or "").lower()) == target:
                synced = True
                break
        if not synced:
            print("  ⚠ xConnector n-a resincronizat încă adresa nouă → NU fac AWB acum (risc adresă veche).")
            print("     Rulează peste câteva minute: awb-make --order %s --apply" % a.order)
            return
        cmd_awb_make(a, _resolved=(sh, xc, o))


def cmd_not_downloaded(a):
    """Comenzi cu AWB a cărui ETICHETĂ nu a fost descărcată (document SHIPPING_LABEL, downloaded=false).
    = coadă de printat / etichete uitate (cele vechi = potențial ghost). Read-only. --min-age-hours filtrează vechi."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    min_age = getattr(a, "min_age_hours", 0) or 0
    grand = 0
    for sh in load_shops():
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        rows = []
        nd_filters = orders_filters(a)  # permite `--sort fulfillmentDate` (coadă de print ordonată) + sku/cantitate
        for o in xc.orders(dfrom, dto, nd_filters):
            doc = awb_doc(o)
            if not doc or doc.get("downloaded") is not False:
                continue
            if min_age:
                age = order_age_hours(xc, o.get("orderId"))
                if age is not None and age < min_age:
                    continue
            rows.append((o.get("orderName"), doc_tracking(doc), doc.get("connectorName")))
        grand += len(rows)
        print("═" * 60)
        print("  %s — %d AWB cu eticheta NEDESCĂRCATĂ%s" % (sh["shopDomain"], len(rows), " (>%dh vechime)" % min_age if min_age else ""))
        for nm, trk, carrier in rows[:40]:
            print("    %-10s %-14s %s" % (nm, trk or "—", carrier or ""))
        if len(rows) > 40:
            print("    … +%d" % (len(rows) - 40))
    print("─" * 60)
    print("  TOTAL etichete nedescărcate: %d" % grand)


def _csv_list(v):
    """argument repetat (--sku A --sku B) și/sau CSV (--sku A,B) → listă plată."""
    if not v:
        return []
    items = v if isinstance(v, (list, tuple)) else [v]
    out = []
    for it in items:
        out += [x.strip() for x in str(it).split(",") if x.strip()]
    return out


def orders_filters(a):
    """Construiește dict-ul de filtre server-side getOrders din argumentele CLI (gol dacă niciunul)."""
    f = {}
    sku = _csv_list(getattr(a, "sku", None))
    if sku:
        f["sku"] = sku
    if getattr(a, "sku_mode", None):
        f["skuMode"] = a.sku_mode
    exsku = _csv_list(getattr(a, "exclude_sku", None))
    if exsku:
        f["excludeSku"] = exsku
    if getattr(a, "total_items", None):
        f["totalItemsCount"] = a.total_items
    if getattr(a, "line_items", None):
        f["lineItemsCount"] = a.line_items
    if getattr(a, "sort", None):
        f["sort"] = a.sort
    if getattr(a, "sort_dir", None):
        f["sortDir"] = a.sort_dir
    return f


def cmd_orders(a):
    """READ: listează/filtrează comenzi cu filtrele server-side getOrders (sku/cantitate/sortare).
    Ex: `orders --shop ix5bxc-hr --total-items 1 --sort fulfillmentDate` (mono-bucată, ordonate de livrare),
    `orders --sku ABC123` (comenzi cu SKU-ul), `orders --total-items 2,3,4 --shop n12w89-yy` (multi-bucată Grandia)."""
    shops = load_shops()
    if not shops:
        print("Nicio configurație xConnector (KB XCONNECTOR_SHOPS sau ~/.aac/input.json)."); return
    flt = orders_filters(a)
    if not flt and not a.shop:
        print("Dă cel puțin un filtru (--sku/--total-items/--line-items/--sort) sau --shop. Vezi --help."); return
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    grand = 0
    for sh in shops:
        if skip_shop(sh, a):   # suportă --shop cu LISTĂ comma + prefix (combinație de magazine)
            continue
        try:
            rows = XC(sh["apiKey"]).orders(dfrom, dto, flt)
        except Exception as e:
            print("  %s — eroare: %s" % (sh["shopDomain"], e)); continue
        if not rows:
            continue
        grand += len(rows)
        print("═" * 60)
        print("  %s — %d comenzi%s" % (sh["shopDomain"], len(rows), (" · filtre %s" % json.dumps(flt)) if flt else ""))
        print("  (DTO-ul getOrders întoarce doar nume/status/AWB/expediat — cantitatea/SKU-ul sunt filtre & sortare server-side, nu câmpuri.)")
        for o in rows[:50]:
            awb = "AWB" if has_awb(o) else "—"
            disp = "expediat" if o.get("dispatched") else ""
            print("    %-11s %-9s %-4s %s"
                  % (o.get("orderName") or o.get("orderId"), o.get("addressStatus") or "", awb, disp))
        if len(rows) > 50:
            print("    … +%d" % (len(rows) - 50))
    print("─" * 60)
    print("  TOTAL: %d comenzi" % grand)


# ── PRINT depozit: descarcă etichetele NEDESCĂRCATE (downloaded=false), grupate pe produs/cantitate/dată ──
def _norm_date(s):
    """Acceptă yyyy-MM-dd (API), DD/MM/YYYY (dashboard) sau DD.MM.YYYY → întoarce yyyy-MM-dd."""
    if not s:
        return None
    import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date().isoformat()
        except Exception:
            pass
    return s.strip()


def date_window(a):
    """(dfrom, dto) din --from/--to (orice format uzual) dacă date, altfel din --days."""
    import datetime
    fd, td = _norm_date(getattr(a, "from_date", None)), _norm_date(getattr(a, "to_date", None))
    if fd or td:
        return (fd or (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat(),
                td or datetime.date.today().isoformat())
    return ((datetime.date.today() - datetime.timedelta(days=a.days)).isoformat(),
            datetime.date.today().isoformat())


def _print_dialog(path, printer=None):
    """Deschide PDF-ul batch pt print, cross-platform. Depozitul e pe WINDOWS, printa în CHROME (ca xConnector).
    Windows: SumatraPDF `-print-dialog`/`-print-to` dacă există → altfel deschide în CHROME (operatorul apasă Ctrl+P).
    macOS: Preview + Cmd+P. Linux: xdg-open. `printer` (opțional, doar SumatraPDF) = printare DIRECTĂ fără dialog."""
    if os.name == "nt":   # Windows (depozit)
        import shutil
        sumatra = next((p for p in (shutil.which("SumatraPDF"), shutil.which("SumatraPDF.exe"),
                                    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                                    os.path.expandvars(r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe"))
                        if p and os.path.exists(p)), None)
        if sumatra:
            if printer:
                subprocess.Popen([sumatra, "-print-to", printer, "-silent", path])
                print("  → SumatraPDF: trimis DIRECT pe imprimanta %s." % printer)
            else:
                subprocess.Popen([sumatra, "-print-dialog", path])
                print("  → SumatraPDF: dialog de print deschis.")
            return
        # Chrome (așa deschidea xConnector etichetele în depozit) → operatorul apasă Ctrl+P pt dialog
        chrome = next((p for p in (shutil.which("chrome"), shutil.which("chrome.exe"),
                                   r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                   os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"))
                       if p and os.path.exists(p)), None)
        try:
            if chrome:
                subprocess.Popen([chrome, path])
                print("  → deschis în Chrome (ca xConnector). Apasă Ctrl+P pentru dialogul de print.")
            else:
                os.startfile(path)
                print("  → deschis în viewer-ul PDF default. Apasă Ctrl+P pentru dialogul de print.")
        except Exception:
            print("  📄 PDF batch: %s (deschide-l și Ctrl+P)." % path)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-a", "Preview", path], check=False)
        try:
            time.sleep(1.5)
            subprocess.run(["osascript", "-e", 'tell application "Preview" to activate',
                            "-e", 'delay 0.4',
                            "-e", 'tell application "System Events" to keystroke "p" using command down'],
                           check=False, timeout=15)
        except Exception:
            pass
        print("  → deschis în Preview + dialog de print (dacă nu apare, apasă Cmd+P).")
    elif sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", path], check=False)
        print("  → deschis în viewer (printează de acolo).")
    else:
        print("  📄 PDF batch: %s (deschide-l și printează)." % path)


def pending_order_lines(pending):
    """{orderName: [(sku, qty), ...]} pt comenzile din coadă — line items din Shopify (batch, DOAR comenzile pending).
    xConnector NU întoarce SKU/cantitate în DTO. Bază pt: filtrare (--sku-prefix), numărare (--by-sku) și
    SORTAREA PDF-ului pe (magazin → SKU → cantitate)."""
    toks = {t.get("shopDomain"): t for t in load_shopify_tokens()}
    by_shop = {}
    for row in pending:
        by_shop.setdefault(row[0], []).append(row[1])   # row = (shopDomain, orderName, ...)
    res = {}
    for dom, names in by_shop.items():
        st = toks.get(dom)
        if not st:
            continue
        for i in range(0, len(names), 40):
            chunk = [n for n in names[i:i + 40] if n]
            if not chunk:
                continue
            q = ('query{ orders(first:%d, query:"%s"){ edges{ node{ name lineItems(first:20){ edges{ node{ sku quantity } } } } } } }'
                 % (len(chunk), " OR ".join("name:%s" % n.replace('"', "") for n in chunk)))
            d = shopify_gql(st["shopDomain"], st["adminToken"], q)
            for e in (((d.get("data") or {}).get("orders") or {}).get("edges") or []):
                res[e["node"].get("name")] = [(li["node"].get("sku"), li["node"].get("quantity") or 1)
                                              for li in ((e["node"].get("lineItems") or {}).get("edges") or [])
                                              if li["node"].get("sku")]
    return res


def pending_order_skus(pending, olines=None):
    """{orderName: set(SKU-uri)} — derivat din pending_order_lines (o singură pasă Shopify dacă olines e dat)."""
    ol = olines if olines is not None else pending_order_lines(pending)
    return {nm: {s for s, _ in lines} for nm, lines in ol.items()}


def pending_sku_counts(pending, olines=None):
    """[(sku, nr_etichete)] descrescător. O comandă cu mai multe SKU-uri contează la fiecare."""
    from collections import Counter
    cnt = Counter()
    for skus in pending_order_skus(pending, olines).values():
        for s in skus:
            cnt[s] += 1
    return cnt.most_common()


def order_group_key(name, olines, a):
    """Cheia de grupare a unei comenzi în PDF: (SKU principal, cantitate). SKU principal = linia care
    respectă filtrul (--sku-prefix / --sku) cu cea mai mare cantitate; altfel linia dominantă (qty max).
    Comenzile fără SKU rezolvat merg la final."""
    lines = olines.get(name) or []
    if not lines:
        return ("~~~~~", 0)
    pref = (getattr(a, "sku_prefix", None) or "").upper()
    exact = {x.strip().upper() for x in str(getattr(a, "sku", "") or "").split(",") if x.strip()}
    cands = lines
    if pref:
        m = [(s, q) for s, q in lines if (s or "").upper().startswith(pref)]
        if m:
            cands = m
    elif exact:
        m = [(s, q) for s, q in lines if (s or "").upper() in exact]
        if m:
            cands = m
    s, q = max(cands, key=lambda x: x[1] or 0)
    return ((s or "~~~~~").upper(), q or 0)


def cmd_print_batch(a):
    """Coadă de PRINT depozit: etichetele NEDESCĂRCATE (downloaded=false), GRUPATE pe produs (sort sku),
    filtrabile pe produs (--sku), cantitate (--total-items) și interval (--from/--to). Descarcă PDF-urile,
    le pune într-un batch (în ordinea grupată), LOGHEAZĂ timestamp-ul, deschide dialogul de print.
    Dry-run by default (listează, NU descarcă). --apply DESCARCĂ → flip `downloaded` (ies din coada de print!).
    Rulează LOCAL (mașina cu imprimanta — are uv + acces la secrete)."""
    import datetime, os, csv
    dfrom, dto = date_window(a)
    flt = orders_filters(a)
    flt.setdefault("sort", "sku")     # grupare implicită pe produs: 1×SKU1 împreună, apoi 1×SKU2…
    flt.setdefault("sortDir", "asc")
    test = bool(getattr(a, "test", False))        # TEST: etichete deja descărcate (verificare sigură, zero impact pe coadă)
    reprint = bool(getattr(a, "printed", False))  # RE-PRINT: etichete DEJA printate (downloaded=true) — re-printare reală
    target_dl = test or reprint                   # ambele țintesc downloaded=true; normal = downloaded=false (nedescărcate)
    wants = [w.strip() for w in (a.shop or "").split(",") if w.strip()]  # doar pt afișaj; filtrarea o face skip_shop (listă + prefix)
    pending = []
    for sh in load_shops():
        if skip_shop(sh, a):   # suportă --shop listă/prefix (magazine la un loc) + --exclude
            continue
        xc = XC(sh["apiKey"])
        for o in xc.orders(dfrom, dto, flt):
            doc = awb_doc(o)
            if not doc or doc.get("downloaded") is not target_dl or not doc.get("url"):
                continue
            pending.append((sh["shopDomain"], o.get("orderName"), doc.get("connectorId"),
                            doc_tracking(doc), doc.get("url"), xc.h.get("Authorization", "")))
    # SKU+cantitate per comandă (UNA singură pasă Shopify) — pt filtrare (--sku-prefix), numărare (--by-sku)
    # și mai ales SORTAREA PDF-ului pe magazin→SKU→cantitate. Plătită doar când chiar e nevoie.
    olines = pending_order_lines(pending) if (pending and (getattr(a, "sku_prefix", None) or getattr(a, "by_sku", False) or a.apply)) else {}
    if getattr(a, "sku_prefix", None):   # „toate comenzile cu HA" → păstrez doar comenzile care au un SKU pe prefixul dat
        from collections import Counter
        pref = a.sku_prefix.upper()
        oskus = pending_order_skus(pending, olines)
        before = len(pending)
        resolved = sum(1 for r in pending if r[1] in oskus)   # câte comenzi din coadă au avut SKU-urile rezolvate (Shopify) — dacă << before = subnumărare (token lipsă/KB)
        pending = [r for r in pending if any((sk or "").upper().startswith(pref) for sk in oskus.get(r[1], ()))]
        per_shop = Counter(r[0] for r in pending)
        print("  🔎 filtru SKU prefix %s: %d etichete în coadă · %d cu SKU rezolvat (%.0f%%) → %d cu %s*"
              % (a.sku_prefix, before, resolved, 100.0 * resolved / max(1, before), len(pending), pref))
        if resolved < before:
            print("     ⚠️ %d comenzi FĂRĂ SKU rezolvat (token magazin lipsă / KB instabil) — posibil subnumărate." % (before - resolved))
        for dom, n in per_shop.most_common():
            print("       %-30s %d" % (dom, n))
    if getattr(a, "by_sku", False):   # doar ARATĂ coada pe SKU (cele mai multe etichete primele) ca să alegi ce printezi
        ranking = pending_sku_counts(pending, olines)
        print("═" * 60)
        print("  COADĂ PE SKU — %d etichete %s, SKU-urile cu CELE MAI MULTE primele:"
              % (len(pending), "deja printate" if target_dl else "nedescărcate"))
        for sku, n in ranking[:30]:
            print("    %-18s %d etichete" % (sku, n))
        if len(ranking) > 30:
            print("    … +%d SKU-uri" % (len(ranking) - 30))
        if ranking:
            print("  → printează SKU-ul cu cele mai multe: print-batch --sku %s --apply" % ranking[0][0])
        return
    # ORDINEA în PDF = grupat pe MAGAZIN → SKU → CANTITATE (toate „1×HA-0001" împreună, apoi „2×HA-0001"…),
    # NU pe ordinea brută de la xConnector (care lasă cantitățile amestecate: 1×, 2×, 1×).
    pending.sort(key=lambda r: (r[0],) + order_group_key(r[1], olines, a) + (r[1] or "",))
    total_pending = len(pending)
    lim = a.limit if getattr(a, "limit", None) else 250   # MAX 250 AWB/batch (default) — restul, la rularea următoare
    skip = max(0, getattr(a, "offset", 0) or 0)           # paginare: sare primele `skip` (pt re-print în batch-uri succesive — re-printul NU scoate din coadă)
    pending = pending[skip:skip + lim]
    remaining = total_pending - skip - len(pending)
    lbl = {k: v for k, v in flt.items() if k not in ("sort", "sortDir")}
    print("═" * 60)
    if test:
        print("  🧪 TEST — folosesc etichete DEJA descărcate (downloaded=true), NU ating coada reală de print.")
    elif reprint:
        print("  🔁 RE-PRINT — etichete DEJA printate (downloaded=true). Le re-descarc pt re-printare.")
    print("  PRINT BATCH — %d etichete %s%s · %s→%s%s%s · grupat MAGAZIN→SKU→CANTITATE"
          % (len(pending), "DEJA descărcate (test)" if test else ("DEJA printate (re-print)" if reprint else "nedescărcate"),
             ((" [%d–%d] din %d%s" % (skip + 1, skip + len(pending), total_pending,
               (" · rest %d → --offset %d" % (remaining, skip + len(pending))) if remaining else "")) if (skip or remaining) else ""),
             dfrom, dto,
             (" · magazine: " + ",".join(wants)) if wants else " · toate magazinele",
             (" · " + json.dumps(lbl)) if lbl else ""))
    if olines:   # rezumat grupat exact ca în PDF: magazin → SKU × cantitate → câte etichete
        from collections import Counter
        groups = Counter((r[0],) + order_group_key(r[1], olines, a) for r in pending)
        last_dom, shown = None, 0
        for (dom, sk, q), n in sorted(groups.items()):
            if shown >= 60:
                print("    … +%d grupuri" % (len(groups) - shown)); break
            if dom != last_dom:
                print("    ── %s" % dom); last_dom = dom; shown += 1
            print("       %-16s ×%-3d  %d etichete" % (sk, q, n)); shown += 1
    else:
        for dom, nm, cid, trk, _, _ in pending[:60]:
            print("    %-12s %-11s AWB %s" % (nm, dom, trk or "—"))
        if len(pending) > 60:
            print("    … +%d" % (len(pending) - 60))
    if not a.apply:
        print("  → [DRY-RUN] aș descărca %d PDF-uri (în ordinea de mai sus) + aș deschide dialogul de print." % len(pending))
        if not test:
            print("  ⚠️ --apply MARCHEAZĂ etichetele `downloaded` (ies din coada de print) — fă-o DOAR când chiar printezi.")
        else:
            print("  (test: --apply e SIGUR — etichetele sunt deja descărcate, nu se schimbă nimic în coadă.)")
        return
    if not pending:
        print("  Nimic de printat."); return
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    outdir = os.path.join(os.path.expanduser(getattr(a, "out", None) or "."), "print-batch")
    os.makedirs(outdir, exist_ok=True)
    import time as _time
    pdfs, log_rows, failed = [], [], []
    for dom, nm, cid, trk, url, auth in pending:
        b, err = None, None
        for attempt in range(3):   # retry pe blip de rețea (IncompleteRead/timeout) — la print de depozit NU pierdem eticheta unui client tăcut
            try:
                req = urllib.request.Request(url, headers=({"Authorization": auth} if auth else {}))
                with urllib.request.urlopen(req, timeout=45) as r:
                    data = r.read()
                if data[:5] != b"%PDF-":
                    err = "răspuns non-PDF"; break   # nu e tranzitoriu — nu reîncerca
                b, err = data, None
                break
            except Exception as e:
                err = str(e)[:80]
                if attempt < 2:
                    _time.sleep(1.5 * (attempt + 1))   # 1.5s, 3s
        if b is None:
            failed.append((nm, err or "necunoscut")); continue
        try:
            fp = os.path.join(outdir, "%s_%s.pdf" % (nm, trk or "noawb"))
            with open(fp, "wb") as f:
                f.write(b)
            if os.path.getsize(fp) < 100:
                failed.append((nm, "fișier gol")); continue
            pdfs.append(fp)
            log_rows.append([datetime.datetime.now().isoformat(timespec="seconds"), dom, nm, trk, cid, fp])
        except Exception as e:
            failed.append((nm, str(e)[:80]))
    merged = os.path.join(outdir, "batch_%s.pdf" % ts)
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        for fp in pdfs:
            w.append(fp)
        with open(merged, "wb") as f:
            w.write(f)
    except Exception as e:
        merged = None
        print("  (merge PDF indisponibil: %s — fișiere individuale în %s)" % (str(e)[:50], outdir))
    logp = os.path.join(outdir, "batch_%s.csv" % ts)
    with open(logp, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["downloaded_at", "shop", "order", "awb", "connectorId", "file"])
        wr.writerows(log_rows)
    print("  ✅ descărcate %d · eșuate %d · log %s" % (len(pdfs), len(failed), logp))
    if merged:
        print("  📄 batch: %s" % merged)
    if failed:
        print("  ⚠️ EȘUATE (NU s-au salvat — dacă s-au flip-uit pe server, recuperează manual din dashboard):")
        for nm, why in failed[:25]:
            print("      %s — %s" % (nm, why))
    target = merged or (pdfs[0] if pdfs else None)
    if target and not getattr(a, "no_print", False):
        _print_dialog(target, getattr(a, "printer", None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "address-issues", "recheck", "correct", "connectors", "fulfill",
                                    "not-downloaded", "orders", "links", "print-batch",
                                    "awb-make", "awb-void", "awb-regen", "awb-label", "order-cancel",
                                    "inv-make", "inv-cancel", "inv-storno", "inv-regen", "inv-doc", "inv-bulk", "capture", "addr-set",
                                    "awb-create", "awb-cancel", "awb-hold", "awb-auto"])
    ap.add_argument("--shop"); ap.add_argument("--order"); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-age-hours", type=int, default=0, dest="min_age_hours",
                    help="correct: sare comenzile mai noi de N ore (validarea xConnector e async/batch — multe se auto-validează). 0 = oprit.")
    ap.add_argument("--exclude", default="",
                    help="domenii myshopify de SĂRIT (separate prin virgulă) — ex magazinele externe (Bonhaus CZ/PL/BG) pe care validatorul RO nu le acoperă.")
    ap.add_argument("--connector", help="awb-make/void/regen: connectorId curier (din `connectors`). Obligatoriu dacă sunt mai mulți curieri activi.")
    ap.add_argument("--parcels", type=int, default=None, help="awb-make/regen: FORȚEAZĂ nr de colete (parcelCount). Implicit = AUTO din metafield Shopify (order xconnector.parcel-count, altfel custom.nr_cutii|nr_produse, ceil pe decimal). Parfumurile = 1.")
    ap.add_argument("--type", default="PARCEL", help="awb-make/regen: parcelType (PARCEL/ENVELOPE). Default PARCEL.")
    ap.add_argument("--notify", action="store_true", help="awb-make/regen/order-cancel: notifyCustomer.")
    ap.add_argument("--force", action="store_true", help="order-cancel: încearcă anularea chiar dacă statusul de curier zice PLECAT (xConnector dă eroare dacă chiar a plecat).")
    ap.add_argument("--refund", action="store_true", help="order-cancel: returnează banii la anulare (OFF by default — COD n-are nevoie; comenzi plătite = decizie explicită).")
    ap.add_argument("--no-restock", action="store_true", dest="no_restock", help="order-cancel: NU repune stocul la anulare (restock ON by default).")
    ap.add_argument("--max-age-min", type=int, default=15, dest="max_age_min", help="fulfill: vârsta minimă în minute a comenzii unfulfilled ca să-i facă AWB (default 15).")
    ap.add_argument("--lang", help="inv-make/regen: languageCode pt factură (ex ro/en).")
    ap.add_argument("--refund-id", dest="refund_id", help="inv-storno: Shopify refund ID (storno parțial pe un refund).")
    ap.add_argument("--address1"); ap.add_argument("--address2"); ap.add_argument("--city")
    ap.add_argument("--zip"); ap.add_argument("--province"); ap.add_argument("--phone"); ap.add_argument("--country")
    ap.add_argument("--make-awb", action="store_true", dest="make_awb", help="addr-set: după setarea adresei, fă AWB.")
    ap.add_argument("--correct", action="store_true", help="awb-auto: corectează conservator adresele proaste (xConnector ai-correct-address)")
    # Filtre server-side getOrders (xConnector, adăugate 2026-06) — pt comanda `orders` (+ `--sort` pe not-downloaded):
    ap.add_argument("--sku", action="append", help="orders: SKU exact (repetabil sau CSV). Mai multe → vezi --sku-mode.")
    ap.add_argument("--sku-mode", dest="sku_mode", choices=["ANY", "ALL"], help="orders: ANY (oricare, implicit) / ALL (toate SKU-urile).")
    ap.add_argument("--exclude-sku", dest="exclude_sku", action="append", help="orders: exclude comenzile cu acest SKU (repetabil/CSV; cere un filtru pozitiv alături).")
    ap.add_argument("--total-items", dest="total_items", help="orders: nr TOTAL bucăți (CSV, ex 1 sau 1,2). =1 → mono-bucată.")
    ap.add_argument("--line-items", dest="line_items", help="orders: nr LINII din comandă (CSV). =1 → o singură linie.")
    ap.add_argument("--sort", choices=["sku", "totalItemsCount", "lineItemsCount", "date", "fulfillmentDate"], help="orders/not-downloaded: câmp de sortare.")
    ap.add_argument("--sort-dir", dest="sort_dir", choices=["asc", "desc"], help="orders: direcția sortării (implicit desc).")
    # links (CS „du-mă la comanda X" — totul prin xConnector, fără rația Shopify):
    ap.add_argument("--awb", help="links: caută comanda după AWB/tracking (xConnector by-tracking-number).")
    ap.add_argument("--open", action="store_true", help="links: deschide linkurile în browser.")
    # print-batch (PRINT depozit: descarcă etichete nedescărcate, grupate pe produs/cantitate/dată, deschide print):
    ap.add_argument("--from", dest="from_date", help="print-batch/orders: data de început (yyyy-MM-dd sau DD/MM/YYYY).")
    ap.add_argument("--to", dest="to_date", help="print-batch/orders: data de sfârșit (yyyy-MM-dd sau DD/MM/YYYY).")
    ap.add_argument("--out", help="print-batch: folderul unde salvez PDF-urile + log (default: ./print-batch).")
    ap.add_argument("--no-print", action="store_true", dest="no_print", help="print-batch: NU deschide dialogul de print (doar salvează/merge).")
    ap.add_argument("--test", action="store_true", help="print-batch: TEST pe etichete DEJA descărcate (downloaded=true) — zero impact pe coada reală.")
    ap.add_argument("--printed", action="store_true", help="print-batch: RE-PRINT pe etichete DEJA printate (downloaded=true) — re-printare reală a unor AWB-uri deja descărcate.")
    ap.add_argument("--by-sku", action="store_true", dest="by_sku", help="print-batch: NU printează — arată coada GRUPATĂ pe SKU (câte etichete/SKU), cele mai multe primele, ca să alegi ce produs printezi.")
    ap.add_argument("--sku-prefix", dest="sku_prefix", help="print-batch: păstrează DOAR comenzile care au un SKU pe prefixul dat (ex `HA` = toate comenzile cu produse HA-*).")
    ap.add_argument("--limit", type=int, help="print-batch: max AWB-uri/batch (implicit 250). Restul rămâne pt rularea următoare.")
    ap.add_argument("--offset", type=int, default=0, help="print-batch: sare primele N etichete (paginare batch-cu-batch la RE-PRINT, ex --offset 250 = batch 2). În producție (downloaded=false) nu e nevoie — fiecare batch iese din coadă.")
    ap.add_argument("--printer", help="print-batch (Windows+SumatraPDF): printează DIRECT pe imprimanta dată, fără dialog (batch rapid).")
    a = ap.parse_args()
    if a.cmd in ("awb-make", "awb-void", "awb-regen", "awb-label", "order-cancel",
                 "inv-make", "inv-cancel", "inv-storno", "inv-regen", "inv-doc", "addr-set"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        {"awb-make": cmd_awb_make, "awb-void": cmd_awb_void, "awb-regen": cmd_awb_regen,
         "awb-label": cmd_awb_label, "order-cancel": cmd_order_cancel,
         "inv-make": cmd_inv_make, "inv-cancel": cmd_inv_cancel, "inv-storno": cmd_inv_storno,
         "inv-regen": cmd_inv_regen, "inv-doc": cmd_inv_doc, "addr-set": cmd_addr_set}[a.cmd](a)
        return
    if a.cmd == "connectors":
        cmd_connectors(a); return
    if a.cmd in ("awb-create", "awb-cancel", "awb-hold"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        cmd_awb(a); return
    if a.cmd == "awb-auto":
        cmd_awb_auto(a); return
    if a.cmd == "correct":
        cmd_correct(a); return
    if a.cmd == "fulfill":
        cmd_fulfill(a); return
    if a.cmd == "not-downloaded":
        cmd_not_downloaded(a); return
    if a.cmd == "orders":
        cmd_orders(a); return
    if a.cmd == "links":
        cmd_links(a); return
    if a.cmd == "print-batch":
        cmd_print_batch(a); return
    if a.cmd == "recheck":
        cmd_recheck(a); return
    if a.cmd == "inv-bulk":
        cmd_inv_bulk(a); return
    if a.cmd == "capture":
        cmd_capture(a); return
    import datetime
    a.dto = datetime.date.today().isoformat()
    a.dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    if not shops:
        print("Nicio configurație xConnector (KB XCONNECTOR_SHOPS sau ~/.aac/input.json)."); sys.exit(1)
    if a.cmd == "summary":
        cmd_summary(shops, a)
    else:
        cmd_issues(shops, a)


if __name__ == "__main__":
    main()
