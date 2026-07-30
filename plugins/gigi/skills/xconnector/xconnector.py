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
import address_rules as _AR   # 📕 RULEBOOK dicționare într-un singur loc
import os, sys, json, re, time, datetime, hashlib, argparse, subprocess, urllib.parse, urllib.request, urllib.error

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


def load_blocklist():
    """Blocklist de CLIENȚI serial-refuseri/fraudă: {shopDomain: [customerGID, ...]} din KB
    (`XCONNECTOR_CUSTOMER_BLOCKLIST`, override cu env). `fulfill` NU face AWB pt aceștia și le anulează
    comanda (ca duplicat). Cheia = customer GID (stabil per magazin, non-PII — singurul identificator
    pe care cronul îl poate citi LIVE; magazinele-s pe plan Basic → email/telefon = PII blocat). Vezi
    memoria [[serial-refuser-blocklist]]. Gol/eroare → {} (fail-open: nu blochează nimic din greșeală)."""
    raw = os.environ.get("XCONNECTOR_CUSTOMER_BLOCKLIST")
    if not raw:
        raw, ok = _kb_secret("XCONNECTOR_CUSTOMER_BLOCKLIST")
        if not ok:
            return {}, set(), set(), set(), set()
    try:
        d = json.loads(raw) if raw else {}
        # {shopDomain: set(gids)} + două liste GLOBALE speciale: `_phones` / `_addresses`. Astea din urmă
        # prind același om care schimbă emailul/numele dar refoloseste telefonul sau adresa (ex. Jefferson
        # pe „Str. Libertății 123"). Cheile `_`… nu sunt magazine → nu intră în shopmap.
        phones = {p for p in (d.get("_phones") or []) if p}
        addrs = {a for a in (d.get("_addresses") or []) if a}
        phones_hold = {p for p in (d.get("_phones_hold") or []) if p}
        addrs_hold = {a for a in (d.get("_addresses_hold") or []) if a}
        shopmap = {k: set(v) for k, v in d.items() if not k.startswith("_") and isinstance(v, list)}
        return shopmap, phones, addrs, phones_hold, addrs_hold
    except Exception:
        return {}, set(), set(), set(), set()


def _bl_phone(p):
    """Telefon normalizat pt blocklist = ultimele 9 cifre (ignoră prefix țară / 0). '' dacă prea scurt."""
    d = re.sub(r"\D", "", p or "")
    return d[-9:] if len(d) >= 9 else ""


def _bl_addr(ad):
    """Adresă normalizată pt blocklist: 'strada|oras|zip' (fără diacritice/punctuație). '' dacă prea scurtă.
    TREBUIE să producă exact ce a scris detect_refusers.py în secret, altfel nu se potrivește."""
    import unicodedata

    def fold(x):
        x = unicodedata.normalize("NFKD", x or "")
        return "".join(ch for ch in x if not unicodedata.combining(ch))

    base = re.sub(r"[^a-z0-9]+", " ",
                  fold("%s %s" % (ad.get("address1") or "", ad.get("address2") or "")).lower()).strip()
    ct = re.sub(r"[^a-z0-9]+", " ", fold(ad.get("city") or "").lower()).strip()
    z = re.sub(r"\D", "", ad.get("zip") or "")
    if len(base) < 8:
        return ""
    return "%s|%s|%s" % (base, ct, z)


def shopify_append_note(shop, token, name, text):
    """Adaugă un rând la câmpul Note al comenzii (APPEND, nu suprascrie). Idempotent. Best-effort."""
    q = 'query{ orders(first:1, query:"name:%s"){ edges{ node{ id note } } } }' % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return False
    node = edges[0]["node"]
    cur = (node.get("note") or "").strip()
    line = (text or "").strip()
    if not line or line in cur:
        return True
    newnote = ((cur + "\n" + line).strip() if cur else line)[-1900:]
    m = 'mutation{ orderUpdate(input:{id:"%s", note:%s}){ userErrors{ message } } }' % (node["id"], json.dumps(newnote))
    r = shopify_gql(shop, token, m)
    return not ((((r.get("data") or {}).get("orderUpdate") or {}).get("userErrors")) or [])


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


# App-uri Shopify cu grant `client_credentials` (emit token per magazin unde-s instalate). Fiecare acoperă un set
# de magazine; încercăm fiecare app → prima care emite (200) = aia e instalată. Adaugi un app nou = doar o pereche
# de secrete KB aici. (ARONA Assistant, „ro-deals" = Orice Redus/Ofertele/Rossi, „bonhaus-intl" = Bonhaus HU/SK/CZ/BG.)
_SHOPIFY_APPS = [
    ("SHOPIFY_ARONA_CLIENT_ID", "SHOPIFY_ARONA_CLIENT_SECRET"),
    ("SHOPIFY_RODEALS_CLIENT_ID", "SHOPIFY_RODEALS_CLIENT_SECRET"),
    ("SHOPIFY_BONHAUSINTL_CLIENT_ID", "SHOPIFY_BONHAUSINTL_CLIENT_SECRET"),
]
_ARONA_TOK = {}
def _shopify_mint(shop):
    """Token Shopify admin ON-DEMAND pt magazinele fără token static în CSV (client_credentials, ~24h, cache pe
    proces). Încearcă fiecare app din `_SHOPIFY_APPS` → prima care emite (app instalat pe magazin) câștigă. None
    dacă niciun app nu-i instalat (400 app_not_installed) sau lipsesc credentialele. NU se printează tokenul."""
    import time as _t
    c = _ARONA_TOK.get(shop)
    if c and c[1] > _t.time() + 300:
        return c[0]
    for cid_key, csec_key in _SHOPIFY_APPS:
        cid, _ = _kb_secret(cid_key)
        csec, _ = _kb_secret(csec_key)
        if not (cid and csec):
            continue
        try:
            s, b = http("POST", "https://%s/admin/oauth/access_token" % shop, {"Content-Type": "application/json"},
                        {"client_id": cid, "client_secret": csec, "grant_type": "client_credentials"})
            d = json.loads(b) if b else {}
            if s == 200 and d.get("access_token"):
                _ARONA_TOK[shop] = (d["access_token"], _t.time() + (d.get("expires_in") or 86400))
                return d["access_token"]
        except Exception:
            continue
    return None


def _prefix_for_domain(dom):
    """Prefixul orderName pt un domeniu (reverse PREFIX_DOMAIN). '' dacă necunoscut."""
    for pref, sub in PREFIX_DOMAIN.items():
        if sub and sub in (dom or ""):
            return pref
    return ""


def load_shopify_tokens():
    """[{prefix, shopDomain, adminToken}] pt TOATE magazinele: bază din SHOPIFY_STORES_CSV (canonic),
    suprascris de SHOPIFY_ADMIN_TOKENS (env/KB) pt override-uri/tokenuri proaspete. Pt magazinele ARONA-only
    din XCONNECTOR_SHOPS fără token static → EMITE token via ARONA Assistant (client_credentials). NU se printează."""
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
    # ARONA-only (Lab Noir etc.): magazin în XCONNECTOR_SHOPS fără token static → mint on-demand.
    try:
        for sh in load_shops():
            dom = sh.get("shopDomain")
            if dom and dom not in by_dom:
                tk = _shopify_mint(dom)
                if tk:
                    by_dom[dom] = {"prefix": _prefix_for_domain(dom), "shopDomain": dom, "adminToken": tk}
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
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ id name displayFulfillmentStatus cancelledAt '
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


def shopify_our_bad_holds(shop, token, since_date, max_pages=8):
    """Numele comenzilor open + ON_HOLD puse de CRONUL NOSTRU pe bad-address / awb-esec-repetat (după
    `reasonNotes` = 'xc-hold:bad-address' / 'xc-hold:awb-esec…'). NU include hold-uri CS/business
    (dup-necomparabil, dup-suma-diferita, 'valoare mare', Flow, fraudă/stoc/plată) — alea rămân pe hold.
    Ținta held-sweep-ului: comenzile pe care regulile noi le-ar putea debloca."""
    OURS = ("xc-hold:bad-address", "xc-hold:awb-esec")
    out, cursor = [], None
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:100%s, query:"fulfillment_status:on_hold AND status:open AND created_at:>=%s"){ '
             'edges{ cursor node{ name fulfillmentOrders(first:10){ edges{ node{ status '
             'fulfillmentHolds{ reasonNotes } } } } } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and d.get("errors"):
            break
        for e in edges:
            n = e["node"]
            ours = False
            for fe in ((n.get("fulfillmentOrders") or {}).get("edges") or []):
                fn = fe["node"]
                if fn.get("status") != "ON_HOLD":
                    continue
                for h in (fn.get("fulfillmentHolds") or []):
                    rn = h.get("reasonNotes") or ""
                    if any(rn.startswith(sig) for sig in OURS):
                        ours = True
            if ours:
                out.append(n.get("name"))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    return out


def shopify_hold(shop, token, name, notes="xc-hold"):
    """Pune pe HOLD fulfillment order-ele OPEN ale comenzii (ca să NU se expedieze automat). Întoarce n_puse_pe_hold.
    Idempotent: sare peste cele care nu-s OPEN (deja on-hold/închise). Reason OTHER + reasonNotes = motivul nostru."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ fulfillmentOrders(first:10){ edges{ node{ '
         'id status } } } } } } }') % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return 0
    fos = ((edges[0]["node"].get("fulfillmentOrders") or {}).get("edges")) or []
    held = 0
    safe_notes = (notes or "xc-hold").replace('"', "'")[:120]
    for fo in fos:
        n = fo["node"]
        if n.get("status") != "OPEN":
            continue
        m = ('mutation{ fulfillmentOrderHold(fulfillmentHold:{reason:OTHER, reasonNotes:"%s"}, id:"%s"){ '
             'userErrors{ message } } }') % (safe_notes, n["id"])
        r = shopify_gql(shop, token, m)
        errs = (((r.get("data") or {}).get("fulfillmentOrderHold") or {}).get("userErrors")) or []
        if not errs:
            held += 1
    return held


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
    _t = "".join(c for c in unicodedata.normalize("NFKD", (s or "")) if not unicodedata.combining(c)).lower().strip()
    return _t.replace("ı", "i")   # i fara punct (turcesc/encoding) -> i

def _foldi(s):
    """Fold pt comparație STRADĂ care unifică â/î cu i — românii scriu 'i' unde oficialul are 'â/î'
    ('Birsei'≡'Bârsei', 'Tirgului'≡'Târgului'). Mapează â/î→i ÎNAINTE de strip diacritice (care ar da â→a)."""
    return _fold((s or "").replace("â", "i").replace("Â", "i").replace("î", "i").replace("Î", "i"))


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



def _lev(a, b):
    """Distanta de editare (Levenshtein). SQL-ul metrics n-are pg_trgm/fuzzystrmatch -> fuzzy in Python."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]



_ST_TYPE_RE = None


def _tok_words(s):
    """Cuvintele unui text: cifrele lipite de litere separate ("1mai" -> ["1","mai") si tipul de
    artera lipit de nume desprins ("bulevardulstefan" -> ["bulevardul","stefan"])."""
    global _ST_TYPE_RE
    if _ST_TYPE_RE is None:
        _ST_TYPE_RE = "|".join(sorted(_ST_TYPE.split("|"), key=len, reverse=True))
    out = []
    for t in re.findall(r"[a-z]+|\d+", _fold(s)):
        m = re.match(r"^(" + _ST_TYPE_RE + r")(.{3,})$", t)
        if m:
            out.extend([m.group(1), m.group(2)])
        else:
            out.append(t)
    return out


def _glued_ok(otok, sttok):
    """Clientul a scris cuvintele strazii LIPITE ("Ionmihalache" = "Mihalache Ion").
    Cerem concatenare EXACTA a TUTUROR cuvintelor sugerate — nu simplu substring, care accepta
    "rosu" in "drosu" si schimba strada (masurat: "Aviatorului Nicolae Drosu" -> "Rosu Nicolae")."""
    import itertools
    if not (2 <= len(sttok) <= 3):
        return False
    joins = {"".join(p) for p in itertools.permutations(sttok)}
    return any(o in joins for o in otok)


_FOREIGN_DIA = {"ı": "i", "ﬁ": "fi", "ﬂ": "fl", "\u200b": "",
                "ā": "a", "ē": "e", "ī": "i", "ō": "o", "ū": "u",  # macron (encoding stricat pt ă/î)
                "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u",
                "ä": "a", "ë": "e", "ï": "i", "ö": "o", "ü": "u"}


def _clean_chars(s):
    """Repara caracterele care fac validatorul orb: i fara punct ("Prıncıpala"), macron ("Lāpușneanu"
    = Lăpușneanu mis-encodat), ligaturi, zero-width. NU atinge diacriticele RO corecte (ă â î ș ț)."""
    s = s or ""
    for k, v in _FOREIGN_DIA.items():
        if k in s:
            s = s.replace(k, v)
    return s


_DECL_SUF = ["ului", "ilor", "elor", "lui", "lor", "ei", "ea", "ul", "le", "a", "i", "e"]


def _stem(w):
    """Taie terminatia de declinare/articol RO (cea mai lunga) daca ramane o tulpina >=4 litere."""
    for suf in _DECL_SUF:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _same_street_token(a, b):
    """Acelasi cuvant de strada, tolerand DECLINAREA RO (Grivitei=Grivita, Viteazu=Viteazul) + max 1 typo
    in TULPINA. Crinului≠Cornului (tulpini crin/corn diferite) → False. Conservator la singular/plural."""
    if a == b:
        return True
    sa, sb = _stem(a), _stem(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    return (sa[0] == sb[0] and abs(len(sa) - len(sb)) <= 1
            and _lev(sa, sb) <= 1 and min(len(sa), len(sb)) >= 5)


def _typo_ok(t, o, strict=False):
    """`o` (scris de client) = acelasi cuvant ca `t` (canonic), cu o greseala mica?
    Garzi ca sa NU inlocuim o strada cu ALTA (regula owner): prima litera identica, lungimi apropiate,
    cuvinte lungi. strict=True (ruta NOMENCLATOR, fara context de adresa) = maxim O litera diferenta.
    Masurat pe 356 strazi reale din 10 orase: 88% recuperate, 0 strazi gresite."""
    if t == o:
        return True
    if not t or not o or t[0] != o[0] or abs(len(t) - len(o)) > 2:
        return False
    d = _lev(t, o)
    if strict:
        return d <= 1 and len(t) >= 6
    return (d <= 1 and len(t) >= 6) or (d <= 2 and len(t) >= 9)


_ST_RANK = {"general", "gen", "doctor", "dr", "profesor", "prof", "inginer", "ing", "maior", "capitan",
            "colonel", "locotenent", "aviator", "pictor", "poet", "scriitor", "academician", "acad",
            "sfantul", "sf", "sfanta", "parintele", "preot", "episcop", "marele", "cel", "si", "de", "la"}
_ST_TYPE = (r"strada|stradela|str|soseaua|sos|bulevardul|bulevard|bdul|bd|blvd|aleea|alee|al|"
            r"calea|cale|intrarea|intrare|intr|drumul|drum|piata|pta|piateta|prelungirea|prelungire|"
            r"splaiul|splai|fundacul|fundac|pasajul|pasaj|cartierul|cartier|cart")


def _street_phrase(a1):
    """NUMELE strazii asa cum l-a scris clientul: fara tip (Str./Bd.), fara nr/bloc/scara/ap, fara cifre."""
    s = _fold(a1)
    s = re.sub(r"\bb-?dul\b", " bulevardul ", s)
    s = re.sub(r"[.,;/()\-]", " ", s)
    s = re.sub(r"\b(nr|numarul|nrul|no)\b.*$", " ", s)
    s = re.sub(r"\b(bl|bloc|sc|scara|ap|apt|apartament|et|etaj|parter|corp|tronson|jud|judet|"
               r"loc|localitatea|sat|comuna|com|sector|sectorul|interfon|cod)\b.*$", " ", s)
    s = re.sub(r"\b(" + _ST_TYPE + r")\b", " ", s)
    s = re.sub(r"\d+", " ", s)
    return " ".join(s.split())


def _street_type_of(a1):
    m = re.search(r"\b(" + _ST_TYPE + r")\b", _fold(a1))
    if not m:
        return None
    return {"str": "Strada", "strada": "Strada", "sos": "Soseaua", "soseaua": "Soseaua",
            "bd": "Bulevardul", "bdul": "Bulevardul", "blvd": "Bulevardul", "bulevard": "Bulevardul",
            "bulevardul": "Bulevardul", "al": "Aleea", "alee": "Aleea", "aleea": "Aleea",
            "cale": "Calea", "calea": "Calea", "intr": "Intrarea", "intrare": "Intrarea",
            "intrarea": "Intrarea", "drum": "Drumul", "drumul": "Drumul", "piata": "Piata",
            "pta": "Piata"}.get(m.group(1), m.group(1).title())


_NOM_TIP = {"strada": "Strada", "stradela": "Stradela", "bulevard": "Bulevardul", "alee": "Aleea",
            "cale": "Calea", "sosea": "Soseaua", "piata": "Piata", "intrare": "Intrarea",
            "drum": "Drumul", "fundac": "Fundacul", "splai": "Splaiul", "pasaj": "Pasajul",
            "prelungire": "Prelungirea", "cartier": "Cartierul", "curte": "Curtea", "parc": "Parcul"}


def _nomen_street_typo(cur, ad, city, judet_norm):
    """Repara TYPO-ul din numele strazii din nomenclatorul localitatii ("Rebrenu" -> "Rebreanu").
    Intoarce (address1_nou, zip_unic|None) sau None. GARZI (owner: „sa nu schimbe strada"):
      · acoperire in AMBELE sensuri intre ce-a scris clientul si numele canonic (fara asta
        "Pacurari"->"Pacurariu PETRE", "Alexandru cel Bun"->"INDRIES Alexandra");
      · EXACT o litera gresita, intr-un singur cuvant;
      · cifrele din numele canonic trebuie scrise de client ("1 Decembrie 1918" != "22 Decembrie");
      · potrivire UNICA in localitate;
      · forma scrisa de client sa NU existe ca strada nicaieri in judet (atunci localitatea e suspecta)."""
    a1 = ad.get("address1") or ""
    cph = _street_phrase(a1)
    ct = [t for t in cph.split() if len(t) >= 4]
    if not ct:
        return None
    cnums = set(re.findall(r"\d+", a1))
    try:
        cur.execute("SELECT nume_strada, tip_artera, cod_postal FROM public.romania_addresses "
                    "WHERE localitate_norm = %s AND judet_norm = %s AND nume_strada IS NOT NULL",
                    (city, judet_norm))
        rows = cur.fetchall()
        cur.execute("SELECT DISTINCT nume_strada FROM public.romania_addresses "
                    "WHERE judet_norm = %s AND nume_strada IS NOT NULL", (judet_norm,))
        county = {_fold(r[0]) for r in cur.fetchall()}
    except Exception:
        return None
    if not rows or cph in county:
        return None                      # scrisa corect undeva in judet -> nu e typo, e alta localitate
    hits = {}
    for nm, tip, z in rows:
        if not set(re.findall(r"\d+", nm or "")) <= cnums:
            continue
        nt = [t for t in re.sub(r"[.,;/()]", " ", _fold(nm or "")).split() if len(t) >= 3]
        core = [t for t in nt if t not in _ST_RANK] or nt
        if not core:
            continue
        typos, ok = 0, True
        for c in ct:
            hit = None
            for t in core:
                if c == t:
                    hit = 0; break
                if _typo_ok(t, c, strict=True):
                    hit = 1
            if hit is None:
                ok = False; break
            typos += hit
        if ok:
            for t in core:
                if not any(c == t or _typo_ok(t, c, strict=True) for c in ct):
                    ok = False; break
        if ok and typos == 1:
            h = hits.setdefault(_fold(nm), {"nm": nm, "tip": tip, "zips": set()})
            h["zips"].add(z)
    if len(hits) != 1:
        return None
    h = list(hits.values())[0]
    stype = _street_type_of(a1) or _NOM_TIP.get(_fold(h["tip"] or ""), "Strada")
    nums = re.findall(r"\d+[A-Za-z]?", a1)
    new_a1 = ("%s %s%s" % (stype, str(h["nm"]).title(), ((" " + nums[0]) if nums else ""))).strip()
    zips = {z for z in h["zips"] if z}
    return (new_a1, (list(zips)[0] if len(zips) == 1 else None))





_ZIP_CACHE = {}


def _zip_real(z):
    """Codul postal exista in nomenclatorul RO? (adica e un cod REAL, dat corect de client).
    Daca nu putem verifica (DB indisponibil) raspundem True = conservator: nu-l suprascriem."""
    z = (str(z or "")).strip().strip("-").strip()
    if not re.fullmatch(r"\d{6}", z):
        return False
    if z in _ZIP_CACHE:
        return _ZIP_CACHE[z]
    try:
        cur = metrics_cursor_live()
        if not cur:
            return True
        cur.execute("SELECT 1 FROM public.romania_addresses WHERE cod_postal = %s LIMIT 1", (z,))
        _ZIP_CACHE[z] = bool(cur.fetchall())
    except Exception:
        return True
    return _ZIP_CACHE[z]


def _city_denoise(city):
    """Curata ZGOMOTUL din campul ORAS ca sa reziste la lookup-ul de nomenclator — NU schimba
    localitatea, doar taie gunoiul lipit (observat pe held-backlog: 'Focsani, Jud Vrancea',
    '11849#zalau', 'Curtesti.sat M Doamnei.', 'Spantov(Stancea)'). Conservator: se declanseaza
    DOAR pe cele 4 tipare de gunoi; orasele curate trec neatinse (Constanta, Cluj-Napoca, orase CZ).
    Intoarce (curatat, motiv) sau (city, None) daca n-a schimbat nimic. NICIODATA nu inventeaza alt oras."""
    if not city:
        return city, None
    c = city.strip(); orig = c; why = []
    # 0) prefix admin lipit în față ("com. Roșiile"->"Roșiile", "loc. Hîrlău"->"Hîrlău", "Sat Lipia"->"Lipia").
    #    NU atinge "Satu Mare" (\b după 'sat' → 'satu' n-are boundary) sau orașe curate.
    m0 = re.match(r"(?i)^\s*(?:comuna|com|satul|sat|localitatea|loc)\.?\s+([A-Za-zăâîșțĂÂÎȘȚ].*)$", c)
    if m0 and m0.group(1).strip():
        c = m0.group(1).strip(" ,.-"); why.append("prefix-admin")
    # 1) prefix numeric lipit ("11849#zalau" / "11849 zalau" -> "zalau") — numarul e gunoi (strada isi are nr ei)
    m = re.match(r"^\s*\d{3,}\s*#?\s*(.+)$", c)
    if m and re.search(r"[a-zA-ZăâîșțĂÂÎȘȚ]", m.group(1)):
        c = m.group(1).strip(); why.append("prefix-numeric")
    # 2) sufix judet ("Focsani, Jud Vrancea" / "X jud. Y" / "Zalău JUDEȚ Sălaj" -> "Focsani"). Diacritice: ț/Ț/ţ
    #    (județ scris cu Ț/ţ nu se prindea → rămânea gunoi). Case-fold + variantele de t-virgulă.
    parts = re.split(r"(?i)[,.]?\s*\bjud(?:\.|e[tțţ]|e[tțţ]ul)?\b", c)
    if len(parts) > 1 and parts[0].strip():
        c = parts[0].strip(" ,.-"); why.append("sufix-judet")
    # 2b) sufix admin Google/autocomplete ("Drobeta…, Municipality Municipiul…, Region Mehedinti" -> "Drobeta…")
    parts = re.split(r",\s*(?:municipality|municipiul|region|regiune|county|okres|kraj|oras(?:ul)?)\b", c, flags=re.IGNORECASE)
    if len(parts) > 1 and parts[0].strip():
        c = parts[0].strip(" ,.-"); why.append("sufix-admin")
    # 3) nota "sat/com(una) X" lipita dupa localitatea principala ("Curtesti.sat M Doamnei." -> "Curtesti")
    parts = re.split(r"[.,]\s*(?:sat|com|comuna)\b", c, flags=re.IGNORECASE)
    if len(parts) > 1 and parts[0].strip():
        c = parts[0].strip(" ,.-"); why.append("nota-sat")
    # 3b) sufix "Romania/Rominia" lipit în oraș ("Podul Iloaie Rominiao" -> "Podul Iloaie") — junk, țara se știe
    c2 = re.sub(r"(?i)[,\s]+rom[aâîi]ni[aei]o?\.?\s*$", "", c).strip(" ,.-")
    if c2 and c2 != c:
        c = c2; why.append("sufix-romania")
    # 4) paranteza in oras ("Spantov(Stancea)" -> "Spantov")
    if "(" in c:
        c2 = re.sub(r"\([^)]*\)", "", c).strip(" ,.-")
        if c2 and c2 != c:
            c = c2; why.append("paranteza")
    c = re.sub(r"\s{2,}", " ", c).strip(" ,.-")
    # 4b) "Sector N" (1-6) = sector al Bucureștiului → orașul e București ("unde zice Sector e București").
    #     Prinde și cazul în care tot restul adresei e înghesuit în oraș ('Sector 2 Ion berindei nr 5 bl od21b').
    if re.search(r"(?i)\bsector(?:ul)?\.?\s*[1-6]\b", c) and _fold(c) != "bucuresti":
        c = "București"; why.append("sector→bucurești")
    # 5) abreviere NEAMBIGUA de oras ("Dr tr severin"->Drobeta-Turnu Severin, "M ciuc"->Miercurea Ciuc, "Tg Mures"->…)
    ab = _city_abbrev(c)
    if ab and _fold(ab) != _fold(c):
        c = ab; why.append("abreviere")
    # 6) varianta de Bucuresti (unic in RO -> normalizare sigura: "BUCHARSTI"/"Bucuresit"/"Bucharest" -> Bucuresti)
    bu = _city_bucuresti(c)
    if bu and _fold(bu) != _fold(c):
        c = bu; why.append("bucuresti")
    return (c, "+".join(why)) if (why and c and c.lower() != orig.lower()) else (city, None)


# Abrevieri de oras NEAMBIGUE (multi-cuvant) -> forma canonica. Cheile = folded (fara diacritice/punct).
_CITY_ABBREV = _AR.CITY_ABBREV       # → address_rules.py (RULEBOOK)
def _city_abbrev(city):
    """Expandeaza o abreviere de oras NEAMBIGUA. Potrivire EXACTA pe harta curata (fold+fara punct) →
    zero risc de alt oras. 'Dr tr severin'→'Drobeta-Turnu Severin', 'M ciuc'→'Miercurea Ciuc'."""
    if not city:
        return None
    k = re.sub(r"\s+", " ", re.sub(r"[.\-]", " ", _fold(city))).strip()
    return _CITY_ABBREV.get(k)
def _city_bucuresti(city):
    """Varianta de Bucuresti (localitate UNICA in RO → fuzzy larg SIGUR). Prefixe caracteristice
    Bucureștiului ('bucur…','buchar…','bucar…'); satele care încep doar cu 'buc' (Bucium, Bucecea) NU se ating."""
    if not city:
        return None
    f = re.sub(r"[^a-z]", "", _fold(city))
    if f[:5] in ("bucur", "bucar", "bukur") or f[:6] == "buchar":
        return "București"
    return None


# Markeri de STRADA / LOCALITATE pt detectia campurilor INVERSATE (a1=localitate, city=strada).
_ST_MARK = re.compile(r"(?i)\b(str|strada|bd|b-dul|bdul|bulevardul|blvd|calea|aleea|soseaua|sos|splaiul|intrarea|drumul|piata|prelungirea)\b")
_LOC_MARK = re.compile(r"(?i)^\s*(comuna|com|sat|satul|localitatea|loc)\b")
def _maybe_swap_fields(a1, city):
    """Detecteaza câmpuri INVERSATE: city conține un marker de STRADĂ + un NUMĂR, iar a1 arată ca o LOCALITATE
    (începe cu Comuna/Sat SAU e un nume simplu fără marker de stradă și fără cifră). Întoarce (a1_nou, city_nou,
    True) sau (a1, city, False). Ex: a1='Comuna Sangeru', city='Strada Barbu Lautaru Nr 32' →
    a1='Strada Barbu Lautaru Nr 32', city='Sangeru'. Gardă dublă (city=stradă+nr ȘI a1=localitate) → sigur."""
    if not a1 or not city:
        return a1, city, False
    city_has_street = bool(_ST_MARK.search(city)) and bool(re.search(r"\d", city))
    a1_is_loc = bool(_LOC_MARK.match(a1)) or (not _ST_MARK.search(a1) and not re.search(r"\d", a1))
    if city_has_street and a1_is_loc:
        loc = re.sub(r"(?i)^\s*(comuna|com|sat(ul)?|localitatea|loc)\b\.?\s*", "", a1).strip(" ,.")
        return city.strip(), loc, True
    return a1, city, False


def _pull_artery_prefix(a1, a2):
    """Landmark/POI SCRIS ÎN FAȚA străzii → mută-l în address2 (păstrat pt curier), ține strada de la marker.
    'Las Vegas Calea Unirii nr 27B'→a1='Calea Unirii nr 27B' a2='…; Las Vegas' (HERE 0.74→1.00); 'Bebe Tei Calea
    bucuresti nr 85'→'Calea bucuresti nr 85' (0.78→1.00); 'Cartier Bariera Vâlcii strada Agrișului Nr 46'→'strada
    Agrișului Nr 46' (0.65→~0.9). Fire DOAR când: marker de arteră la poziție>0; prefixul (dinainte) FĂRĂ cifră
    (nu pierdem nr casă), ≤4 cuvinte, FĂRĂ alt marker în el (altfel e 'Str Calea'=dublu, tratat în deglue); iar
    DUPĂ marker urmează un NUME (≥3 litere) — nu 'Str 3' (stradă numerică, ambiguă)."""
    if not a1:
        return a1, a2
    m = _ST_MARK.search(a1)
    if not m or m.start() == 0:
        return a1, a2
    pref = a1[:m.start()].strip(" ,.-;")
    rest = a1[m.start():].strip()
    if (not pref or re.search(r"\d", pref) or len(pref.split()) > 4 or _ST_MARK.search(pref)
            or not re.search(r"[a-zăâîșț]{3,}", rest[m.end() - m.start():], re.I)):
        return a1, a2
    a2n = (a2.strip() + "; " + pref) if (a2 and a2.strip()) else pref
    return rest, a2n


def _pull_landmark(a1, a2):
    """MUTĂ nota-landmark din paranteză din STRADĂ în address2 (nu o pierde — e indiciu real pt curier):
    'Str Aprodu Purice nr 11 (magazin Profi)' → a1='Str Aprodu Purice nr 11', a2='…; magazin Profi'.
    Paranteza cu DETALIU de adresă (ap/bl/sc/nr/et/interfon/parter/demisol) RĂMÂNE în a1 (nu se mută).
    Întoarce (a1_nou, a2_nou)."""
    if not a1 or "(" not in a1:
        return a1, a2
    notes = []
    def _take(m):
        inner = (m.group(1) or "").strip()
        if not inner or re.search(r"(?i)\b(ap|apt|bl|bloc|sc|scara|nr|no|et|etaj|interfon|parter|demisol)\b", inner):
            return m.group(0)            # detaliu de adresă → rămâne în a1
        notes.append(inner)
        return " "
    new_a1 = re.sub(r"\s{2,}", " ", re.sub(r"\s*\(([^)]*)\)", _take, a1)).strip(" ,;")
    if not notes:
        return a1, a2
    note = "; ".join(notes)
    new_a2 = (a2.strip() + "; " + note) if (a2 and a2.strip()) else note
    return new_a1, new_a2


_INST_KW = re.compile(
    r"(?i)\b(pia[țtţ]a|hala|incinta|incint[ăa]|box|magazin|kaufland|carrefour|lidl|profi|penny|mega\s*image|"
    r"selgros|metro|dedeman|brico|mall|complex(?:ul)?|federa[țtţ]ia|direc[țtţ]ia|departament(?:ul)?|minister(?:ul)?|"
    r"spital(?:ul)?|policlinic|dispensar|scoala|școala|gradinita|grădinița|primaria|primăria|prim[ăa]ria|"
    r"sta[țtţ]ia|langa|l[âa]ng[ăa]|vizavi|vis-?a-?vis|in\s*spate|deasupra|punct\s*de\s*reper|reper[:\s])\b")


def _pull_institution_tail(a1, a2):
    """Coadă free-text cu REPER/INSTITUȚIE scrisă DUPĂ numărul casei → address2 (păstrată pt curier), ca HERE să
    geocodeze strada curată. Ex: 'Ziduri Moși nr 4 piata obor hala nouă'→a1='Ziduri Moși nr 4' a2+='piata obor…';
    'Basarabiei nr 39 Federația Romana de…'→a1='Basarabiei nr 39' a2+='Federația…'. SIGUR (keyword-gated): fire DOAR
    când reperul e precedat de un NUMĂR în a1 (altfel reperul e strada — 'Piața Unirii nr 5' NU se atinge, n-are nr
    înaintea 'Piața')."""
    if not a1:
        return a1, a2
    m = _INST_KW.search(a1)
    if not m or not re.search(r"\d", a1[:m.start()]):
        return a1, a2
    kept = a1[:m.start()].strip(" ,.-;")
    tail = a1[m.start():].strip(" ,.-;")
    if not kept or not tail or len(kept) < 4:
        return a1, a2
    a2n = (a2.strip() + "; " + tail) if (a2 and a2.strip()) else tail
    return kept, a2n


def _pull_block_details(a1, a2):
    """Mută detaliile de BLOC/SCARĂ/ETAJ/APARTAMENT din STRADĂ în address2 — PĂSTRATE pt curier (owner: „le lași
    în A2, să ajute curierul"), NU șterse. 'Bulevardul Gării nr 10 bl 3 ap 2 sc b' → a1='Bulevardul Gării nr 10',
    a2='…; bl 3 ap 2 sc b'; 'Str Tineretului 5 sc 2' → a1='Str Tineretului 5', a2='…; sc 2'. Fire DOAR când există
    un NUMĂR de casă ÎNAINTE de primul marker de bloc (altfel 'bl e 67' cu numărul DUPĂ bloc → a1 ar rămâne fără
    număr → nu ating). Curăță a1 (rezolvă mai bine) fără să piardă indiciile de livrare."""
    if not a1:
        return a1, a2
    sm = re.search(r"(?i)\bsector(?:ul)?\.?\s*([1-6])\b", a1)   # 'sector N' = district București, NU parte din stradă → mereu în a2
    if sm:
        a2 = (a2.strip() + "; sector " + sm.group(1)) if (a2 and a2.strip()) else ("sector " + sm.group(1))
        a1 = re.sub(r"\s{2,}", " ", re.sub(r"(?i)[,\s]*\bsector(?:ul)?\.?\s*[1-6]\b", " ", a1)).strip(" ,")
    # 'bl <literă> <număr>' = bloc E + nr 67 (numărul DUPĂ bloc): mută 'bl <literă>' în a2 și ȚINE numărul în a1
    # (owner: 'Bl enescu george bl e 67' → a1='...enescu george nr 67', a2='bl e'). Doar fără alt număr înainte.
    mb = re.search(r"(?i)\bbl(?:oc)?\.?\s*([a-z])\s+(\d+[a-z]?)\s*$", a1)
    if mb and not re.search(r"\d", a1[:mb.start()]):
        a2 = (a2.strip() + "; bl " + mb.group(1)) if (a2 and a2.strip()) else ("bl " + mb.group(1))
        a1 = (a1[:mb.start()].strip(" ,") + " nr " + mb.group(2)).strip()
    m = re.search(r"(?i)[,\s]+(bl|bloc|sc|scara|scări|et|etaj|ap|apt|apartament|interfon|tronson|corp|mansard[ăa]|demisol)\.?\s*[a-z0-9]",
                  a1)
    if not m:
        return a1, a2
    head = a1[:m.start()]
    if not re.search(r"\d", head):        # numărul casei e DUPĂ bloc (ex 'bl e 67') → nu tai, ar rămâne fără număr
        return a1, a2
    tail = a1[m.start():].strip(" ,")
    a1n = head.strip(" ,")
    a2n = (a2.strip() + "; " + tail) if (a2 and a2.strip()) else tail
    return a1n, a2n


_ST_ABBREV = _AR.ST_ABBREV           # → address_rules.py

# Străzi CLASICE scrise cu inițiala prenumelui (după deglue punctele dispar: 'N.Titulescu'→'N Titulescu') →
# nume complet. HERE cere forma completă (măsurat: 'N Titulescu' 0.95→'Nicolae Titulescu' 1.00; 'C Brancoveanu'
# 0.86→1.00; 'Al Lapusneanu' 0.92→1.00). Cheie = inițiale+nume folded, spațiu-separat. WHITELIST = zero false-match.
_STREET_INITIAL = _AR.STREET_INITIAL # → address_rules.py
def _expand_street_initial(a1):
    """Inițială(e) de prenume la stradă clasică → nume complet, pe forma DEGLUATĂ fără punct: 'N Titulescu'→
    'Nicolae Titulescu', 'C Brancoveanu'→'Constantin Brâncoveanu', 'A I Cuza'→'Alexandru Ioan Cuza'. Ia 1-3
    inițiale (1-2 litere) în față + nume (≥4 litere), după marker opțional (Str/Bd). WHITELIST → inițiala
    singură ('N') e prea ambiguă altfel. Fără asta, HERE dă <0.9 și zip-fill/valid nu se declanșează."""
    if not a1:
        return a1
    m = re.match(r"(?i)^\s*(?:str(?:ada)?|bd|b-dul|bdul|bulevardul|calea|[sș]oseaua|sos|aleea|intrarea|drumul)?\.?\s*"
                 r"((?:[a-zăâîșț]{1,2}\.?\s+){1,3})([a-zăâîșț]{4,})", a1)   # \.? = tolerează 'Al.'/'N.' cu punct
    if not m:
        return a1
    key = re.sub(r"[.\s]+", " ", _fold(m.group(1)) + " " + _fold(m.group(2))).strip()  # scot ȘI punctele ('al. lapusneanu'→'al lapusneanu')
    full = _STREET_INITIAL.get(key)
    if not full:
        return a1
    return a1.replace(m.group(1) + m.group(2), full, 1)
_RO_NUMWORD = {"unu": "1", "una": "1", "doi": "2", "doua": "2", "două": "2", "trei": "3", "patru": "4",
               "cinci": "5", "sase": "6", "șase": "6", "sapte": "7", "șapte": "7", "opt": "8", "noua": "9",
               "nouă": "9", "zece": "10", "unsprezece": "11", "doisprezece": "12", "treisprezece": "13",
               "paisprezece": "14", "cincisprezece": "15", "saisprezece": "16", "saptesprezece": "17",
               "optsprezece": "18", "nouasprezece": "19", "douazeci": "20", "douăzeci": "20"}
_NUMWORD_RE = re.compile(r"(?i)\b(nr\.?|num[ăa]rul?|no\.?)\s+(" + "|".join(sorted(_RO_NUMWORD, key=len, reverse=True)) + r")\b")


def _numword_nr(a1):
    """Numărul casei scris în LITERE după 'nr' → cifră: 'Dragoni vodă nr opt' → 'Dragoni vodă nr 8'. Gated pe
    markerul 'nr/numărul/no' (nu atinge cuvinte libere: 'strada Trei Brazi' rămâne)."""
    if not a1:
        return a1
    return _NUMWORD_RE.sub(lambda m: "nr " + _RO_NUMWORD[_fold(m.group(2))], a1)


def _expand_street_abbrev(a1):
    """Expandează abrevieri NEAMBIGUE din numele străzii (whole-word, per-cheie ca să prindă și cratimă):
    'Str ctin lecca'→'Str Constantin lecca', 'G-ral Magheru'→'General Magheru', 'Cpt X'→'Căpitan X'. + typo
    'Ăleia/Aleia'→'Aleea' (Ă în loc de A). WHITELIST scurt = zero false-match pe cuvinte comune (fără dr/col/cap)."""
    if not a1:
        return a1
    s = re.sub(r"(?i)\b[ăa]le+ia\b", "Aleea", a1)               # typo Ăleia/Aleia/Aleeia → Aleea
    s = re.sub(r"(?i)\bne\b\.?\s+(\d)", r"nr \1", s)             # typo 'Ne 76'/'ne 140' → 'nr 76' (înainte de număr)
    s = re.sub(r"(?i)\bb(?:vd|[-\s.]*dul)\b", "Bulevardul", s)  # 'B.dul'/'B dul'/'Bdul'/'B-dul'/'bvd' → Bulevardul
    s = re.sub(r"(?i)\bbudul\b", "Bulevardul", s)               # typo 'Budul Basarabiei' → Bulevardul
    s = re.sub(r"(?i)\bf(?:dt|nd)(?:\.\s*|\s+)", "Fundătura ", s)  # 'Fdt.topliceni'/'Fnd Xyz' → Fundătura (tip arteră)
    s = re.sub(r"(?i)^\s*bl\.?\s+([a-zăâîșț]{4,})\b", r"Bulevardul \1", s)  # 'Bl garii/Mamaia/enescu' → Bulevardul (owner: cuvânt LUNG după Bl = bulevard; cod scurt 'Bl A6a/14' = bloc, neatins)
    s = re.sub(r"(?i)\b(\d{1,2})\s*dec\.?(?=[\s,]|$)", r"\1 Decembrie", s)  # '1dec'/'1 dec' → '1 Decembrie' (an opțional)
    s = re.sub(r"(?i)\brev\.?\s+din\s+dec\w*", "Revoluției din Decembrie 1989", s)  # 'Rev din dec' → strada completă
    s = re.sub(r"(?i)\bice(\s+br[aă]tianu)\b", r"I C\1", s)        # typo 'ICE Bratianu' → 'I C Bratianu' → whitelist inițiale → Ion C. Brătianu (verificat owner + sursă: 'Brătianu Constantin Ion' Târgoviște)
    s = re.sub(r"(?i)\b(\w{3,}(?:\s+\w{3,})?)\s+\1\b", r"\1", s)   # nume/frază IMEDIAT dublată: 'Aurel Suciu Aurel Suciu 49' → 'Aurel Suciu 49'
    for k, v in _ST_ABBREV.items():
        s = re.sub(r"(?i)(?<![a-zăâîșț])" + re.escape(k) + r"(?![a-zăâîșț])", v, s)
    return s


# Străzi CLASICE cu inițiale (cele mai comune nume de stradă din RO) → nume complet. Client scrie 'A.I.Cuza',
# nomenclatorul/HERE le vor la formă COMPLETĂ ('Alexandru Ioan Cuza'→HERE 0.97 vs 'A.I.Cuza'→0.38). Cheie = folded fără punct/spații.
_STREET_FULL = _AR.STREET_FULL       # → address_rules.py
def _expand_fullname(a1):
    """Expandează o stradă clasică cu inițiale la nume complet ('A.I.Cuza'→'Alexandru Ioan Cuza', 'Gh.Doja'→
    'Gheorghe Doja'). WHITELIST = doar străzile cunoscute din `_STREET_FULL` → străzi necunoscute cu inițiale NU
    se ating (sigur). Nomenclatorul/HERE prind forma completă (măsurat: A.I.Cuza HERE 0.38 → Alexandru Ioan Cuza 0.97)."""
    if not a1:
        return a1
    m = re.search(r"(?<![\wăâîșț])((?:[A-ZĂÂÎȘȚ][a-zăâîșț]?\.\s*){1,3}[A-ZĂÂÎȘȚ][a-zăâîșț]{3,})", a1)
    if not m:
        return a1
    full = _STREET_FULL.get(re.sub(r"[.\s]", "", _fold(m.group(1))))
    return a1.replace(m.group(1), full, 1) if full else a1


def _street_from_a2(a1, a2):
    """Strada e în ADDRESS2, iar a1 e gunoi/landmark/nume-localitate → PROMOVEAZĂ a2 la stradă, mută a1 în notă.
    Fire DOAR când a2 are marker de arteră (Str/Bd/Calea/Șos/Aleea…) și a1 NU. Nomenclatorul validează strada din
    a2 → dacă e greșită, needs-geocoder (fără AWB greșit), deci sigur. Ex: a1='La King Bar', a2='Bulevardul Cloșca
    11' → a1='Bulevardul Cloșca 11', a2='La King Bar'; a1='Nadisu hododului', a2='Strada Bogdanului nr 51' →
    a1='Strada Bogdanului nr 51'. Întoarce (a1_nou, a2_nou)."""
    if not a2 or not a2.strip():
        return a1, a2
    if _ST_MARK.search(_fold(a1 or "")):   # a1 are DEJA marker de stradă → a1 e strada, nu ating (fold: prinde 'Șoseaua')
        return a1, a2
    if not _ST_MARK.search(_fold(a2)):     # a2 n-are marker → nu-i clar stradă
        return a1, a2
    new_a1 = a2.strip()
    if not _hist_has_num(new_a1):          # a2 fără număr dar a1 are un nr de casă → îl adaug
        n = _hist_house_num(a1)
        if n:
            new_a1 = new_a1 + " nr " + n
    return new_a1, (a1.strip() if (a1 and a1.strip()) else "")


def _street_deglue(a1):
    """Reformatează SPAȚIEREA în stradă — NU schimbă numele/numărul: (a) punct-separator direct între
    alfanumerice → spațiu ('Str.Spitalului.Bl.28'→'Str Spitalului Bl 28', 'I.C.Bratianu'→'I C Bratianu';
    'Str. Aurora' cu punct+spațiu rămâne); (b) camelCase lipit ('AleeaGalati'→'Aleea Galati', '2Aleea'→'2 Aleea';
    NU sparge '11B'); (c) 'NR' lipit între litere și cifre ('TECUCINR.191'→'TECUCI nr 191'); (d) 'STR.' rămas la
    început → 'Str'. (Nota-landmark în paranteză se MUTĂ în a2 de `_pull_landmark`, ÎNAINTE.)"""
    if not a1:
        return a1
    s = a1.replace("ı", "i").replace("İ", "I")                       # (0) ı/İ fără punct (tastatură TR) → i/I ('Prıncıpala'→'Principala')
    s = re.sub(r"[.․]{2,}", " ", s)                             # (0b) puncte MULTIPLE lipite → spațiu ('Bd...Alexandru...Obregia'→'Bd Alexandru Obregia')
    s = re.sub(r"(?<=\w)\.(?=\w)", " ", s)                           # (a) punct-separator (zero-width → prinde și 'I.C.Bratianu')
    s = re.sub(r"([a-zăâîșț])([A-ZĂÂÎȘȚ])", r"\1 \2", s)            # (b) camelCase minusculă→Majusculă
    s = re.sub(r"(\d)([A-ZĂÂÎȘȚ][a-zăâîșț])", r"\1 \2", s)          # (b) cifră→Cuvânt-capitalizat (nu '11B')
    s = re.sub(r"(?i)([a-z0-9])(bl|sc|ap|et)\.?(\d)", r"\1 \2 \3", s)  # (c'') detaliu-bloc lipit — ÎNAINTE de word-digit: '5sc2'→'5 sc 2', '69bsc1'→'69b sc 1'
    s = re.sub(r"(?i)\bsect(?:or)?\.?\s*([1-6])(?![0-9])", r"sector \1", s)  # 'sect1'/'sect 1'/'sect1nr' → 'sector 1' (București)
    s = re.sub(r"(?i)(\d)\s*nr\.?\s*(\d)", r"\1 nr \2", s)           # '1nr 20' (sect1nr) → '1 nr 20'
    s = re.sub(r"([a-zăâîșțA-ZĂÂÎȘȚ]{3,})(\d)", r"\1 \2", s)        # (b) CUVÂNT(3+ litere)→cifră ('Vladimirescu5'→'Vladimirescu 5'; NU sparge 'U5'/'M2'/'F14')
    s = re.sub(r"(\d)([a-zăâîșț]{3,})", r"\1 \2", s)                 # (b') cifră→cuvânt-minuscule(3+) ('109baneasa'→'109 baneasa'; NU '5b'/'12A')
    s = re.sub(r"(?i)([a-zăâîșț])\.?nr\.?\s*(\d)", r"\1 nr \2", s)   # (c) 'TECUCINR 191' -> 'TECUCI nr 191'
    s = re.sub(r"(?i)\bnr\.?(\d)", r"nr \1", s)                      # (c') 'nr330'/'nr36' (nr lipit de cifre) -> 'nr 330'
    s = re.sub(r"(?i)^\s*str\.\s*([a-zăâîșț])", r"Str \1", s)         # (d) 'STR.X' -> 'Str X' (Strada rămâne)
    s = re.sub(r"(?i)^\s*str(?:ada)?\.?\s+(calea|aleea|aleia|bulevardul|b-?dul|[sș]oseaua|splaiul|drumul|intrarea|pia[țt]a)\b",
               r"\1", s)                                             # (e) marker dublu în față: 'Str Calea Targu Jiului'->'Calea …', 'Str Aleea …'->'Aleea …'
    return re.sub(r"\s{2,}", " ", s).strip()


def _strip_loc_prefix(a1, city):
    """Scoate prefixul de LOCALITATE lipit în față la stradă. Cazuri: (a) 'București [sector N]';
    (b) tot ce e ÎNAINTE de primul marker de arteră, DACĂ prefixul e doar localitate (numele orașului +
    cuvinte admin com/sat/jud, fără cifră = nu pierdem nr casă) — 'Iasi, str. Salciilor…'→'str. Salciilor…',
    'Preutești, Sat Preutești, Strada Mănăstioara…'→'Strada Mănăstioara…'; (c) numele EXACT al orașului la
    început (spațiu sau virgulă). Conservator — 'Strada București' (stradă reală) rămâne; prefix cu cifră
    (bloc/nr) NU se atinge. Rămâne DOAR dacă ce rezultă pare stradă (marker de arteră sau cifră)."""
    if not a1:
        return a1
    orig = a1
    s = re.sub(r"(?i)^\s*bucure[sș]ti\s*(?:sector(?:ul)?\.?\s*[1-6])?\s*[,\-]?\s*", "", a1)   # (a)
    m = _ST_MARK.search(s)                                                                     # (b)
    if m and m.start() > 0 and not re.search(r"\d", s[:m.start()]):
        pf = _fold(s[:m.start()])
        pf = re.sub(r"\bjud(et|etul)?\.?\s+\S+", " ", pf)     # 'Jud Cluj' (jud + nume județ) — întreg ('Jud Cluj Turda Str…'→'Str…')
        pf = re.sub(r"\b(com|comuna|sat|satul|localitatea|loc|jud|judet|judetul)\b", " ", pf)
        if city:
            pf = pf.replace(_fold(city), " ")
        if len(re.sub(r"[\s,.\-]+", " ", pf).strip()) <= 2:   # nu mai rămâne decât oraș+admin → e prefix de localitate
            s = s[m.start():].strip(" ,")
    if s == orig and city and re.match(r"(?i)^\s*" + re.escape(city.strip()) + r"\s*[,\s]", a1):   # (c)
        cand = re.sub(r"(?i)^\s*" + re.escape(city.strip()) + r"\s*[,\s]+", "", a1).strip(" ,")
        if cand and (_ST_MARK.search(cand) or re.search(r"\d", cand)):
            s = cand
    s = s.strip(" ,-")
    if s and s != orig and (_ST_MARK.search(s) or re.search(r"\d", s)):
        return s
    return orig


def _street_tail(a1):
    """Curăță COADA străzii: (a) sufix Google ', <zip> <City>, Romania' / ', Romania' →
    'Oprisita, Strada Vasile Alecsandri, 737421 Vaslui, Romania' → '… Vasile Alecsandri'; (b) segment final
    DUPLICAT ('Nr. 21, Nr. 21'→'Nr. 21', '205, 205'→'205', '1/a, 1/a'→'1/a'). Nu schimbă strada/numărul."""
    if not a1:
        return a1
    s = re.sub(r"(?i),\s*\d{5,6}\s+[^,]+,\s*rom[aâ]nia\s*$", "", a1)
    s = re.sub(r"(?i),\s*rom[aâ]nia\s*$", "", s)
    s = re.sub(r"(?i)\b(nr\.?\s*\S+)\s*,\s*\1\s*$", r"\1", s)
    s = re.sub(r"(\S+)\s*,\s*\1\s*$", r"\1", s)
    s = _collapse_repeats(s)
    return s.strip(" ,-")


def _street_core(a1):
    """Miezul străzii pt QUERY-ul HERE: stradă + număr, TĂIAT la primul bloc/scară/etaj/ap/interfon.
    Detaliile de apartament ÎNEACĂ scorul geocoderului — măsurat: 'Bdul Magheru nr 9 bloc Eva sc 3 et 3
    ap 99, interfon NOTARIAT'→HERE 0.00 vs 'Bdul Magheru nr 9'→1.00; 'Aleea Tudor Vladimirescu bl5 scbap 93'
    →0.77 vs 'Aleea Tudor Vladimirescu'→1.00. Cuvinte-cheie complete (bloc/scara/etaj/apartament/interfon…)
    SAU prescurtat+cod (bl5 / sc B / ap 99 / et 3). NU taie strada reală: 'Blanari'/'Scarlat'/'Apahida'
    (bl/sc/ap urmat de LITERĂ, nu cifră) rămân întregi. Adresa STOCATĂ nu se schimbă — doar interogarea HERE."""
    if not a1:
        return a1
    m = re.search(r"(?i)\b(bloc|scara|etaj|apartament|interfon|corp|tronson|demisol|mansarda)\b"
                  r"|\b(bl|sc|et|ap|apt)\.?\s*[a-z]?\d", a1)
    if not m:
        return a1
    core = a1[:m.start()].strip(" ,.-")
    return core if len(core) >= 4 else a1


def _collapse_repeats(s):
    """Colaps token/frază REPETATĂ (spam client): 'Deleni 112 112 112 112, 112' → 'Deleni 112';
    'Nr 67a Nr 67a Nr 67a' → 'Nr 67a'. Nu schimbă conținutul, doar scoate repetițiile."""
    if not s:
        return s
    s = re.sub(r"(\b[\w/]+\b)(\s*,?\s*\1\b)+", r"\1", s, flags=re.I)          # token repetat
    s = re.sub(r"(\b\w+\s+[\w/]+\b)(\s*,?\s*\1\b)+", r"\1", s, flags=re.I)    # frază scurtă repetată
    return re.sub(r"\s{2,}", " ", s).strip(" ,")


def _delabel(a1):
    """Format cu ETICHETE + ';' ('Str: Victoriei; nr 28; bloc: Union; sc: A; loc: Pitesti; jud: Arges') →
    'Victoriei nr 28 bloc Union sc A' (localitatea/județul se aruncă — câmpul oraș le are). Doar când sunt ≥2 ';'."""
    if not a1 or a1.count(";") < 2:
        return a1
    out = []
    for p in re.split(r"\s*;\s*", a1):
        m = re.match(r"(?i)^\s*(str|strada|nr|numar|bloc|bl|scara|sc|etaj|et|apartament|ap|apt|interfon|loc|localitate|jud|judet|oras)\s*:?\s*(.*)$", p)
        if m:
            lab, val = m.group(1).lower(), (m.group(2) or "").strip()
            if lab in ("loc", "localitate", "jud", "judet", "oras"):
                continue
            if lab in ("str", "strada"):
                out.append(val)
            elif val:
                out.append(lab + " " + val)
        elif p.strip():
            out.append(p.strip())
    return re.sub(r"\s{2,}", " ", " ".join(out)).strip(" ,")


def _adresa_junk(a1):
    """Adresa fara continut plauzibil de strada: fara cifre, fara tip de artera si fara niciun
    cuvant de >=5 litere ("Nici eu nu", "Sc B", "Casa", "A", "Jud. Olt"). NU o corectam — am
    face-o doar sa PARA valida (validatorul da streetName scor 1.0 si pe text aiurea) si am
    trimite un colet in gol. Ramane la CS, care suna clientul."""
    f = _fold(a1)
    if re.search(r"\d", f):
        return False
    if re.search(r"\b(" + _ST_TYPE + r")\b", f):
        return False
    return not any(len(w) >= 5 for w in re.findall(r"[a-z]+", f))


def _numar_casa(tok, a1, street_digits):
    """Numarul casei, cu grija sa NU punem apartamentul/blocul/scara drept numar si sa nu repetam
    cifra din numele strazii ("1 Decembrie 1918" -> "…1"). Daca nu-l stim sigur, mai bine FARA
    numar (curierul suna oricum) decat cu unul gresit — masurat pe comenzi reale: "Ap. 49" ajungea
    "Nr 49", iar "Ogorului B1, Ap. 15" ajungea "Ogorului 15"."""
    num = str(tok.get("streetNumber") or "").strip()
    bad = {str(tok.get(k) or "").strip().lower() for k in ("apartment", "building", "staircase", "floor")}
    bad.discard("")
    if num and num.lower() not in bad and num not in street_digits:
        return num
    m = re.search(r"\b(?:nr|numarul|nrul|no)\.?\s*(\d+\s*(?:[A-Za-z](?![A-Za-z]))?(?:\s*-\s*\d+)?)", a1, re.I)
    if m:
        c = re.sub(r"\s+", "", m.group(1))
        if c.lower() not in bad and c not in street_digits:
            return c
    # numar la FINAL ("Str. Oprescu Dumitru 1") — doar daca adresa n-are bloc/scara/apartament,
    # altfel finalul e apartamentul ("…,AP.49,INTERF.49").
    if not _TRAIL_NUM.search(a1):
        m = re.search(r"(?<![\w])(\d+[A-Za-z]?)\s*$", a1.strip())
        if m:
            c = m.group(1)
            if c.lower() not in bad and c not in street_digits:
                return c
    return ""


_TRAIL_NUM = re.compile(r"\b(bl|bloc|sc|scara|ap|apt|apartament|et|etaj|interf|interfon)\b", re.I)


def _valid_fix(d, ad):
    """Corectie MINIMALA din validarea stocata xConnector (latestAddressValidation), cu garzi:
      - completeaza ZIP-ul gol/gresit cu cel gasit de validator (DOAR scor 1.0);
      - completeaza CITY gresit (scor 1.0);
      - curata STRADA cu zgomot (scor <0.9) DOAR daca aceeasi strada apare in textul original
        (NU schimba strada; pastreaza tipul Strada/Aleea/... si numarul din original).
    Intoarce `applied` doar daca a schimbat ceva; altfel None. Gate general: overall >= 0.85."""
    ms = (d.get("latestAddressValidation") or {}).get("addressMatchers") or []
    if not ms or _adresa_junk(ad.get("address1") or ""):
        return None
    m = ms[0]
    def gv(f):
        x = m.get(f) or {}
        return (x.get("value"), (x.get("score") or 0))
    st_v, st_s = gv("streetName")
    city_v, city_s = gv("city")
    zip_v, zip_s = gv("zipCode")
    ov = m.get("score") or 0
    tok = m.get("tokenizedAddress") or {}
    if ov < 0.85:
        return None
    applied = dict(ad)
    applied["country"] = "Romania"
    changed = False
    for _k in ("address1", "address2", "city"):
        _cv = _clean_chars(ad.get(_k))
        if _cv != (ad.get(_k) or "") and _cv.strip():
            applied[_k] = _cv; changed = True   # "Prıncıpala" (i fara punct) orbea validatorul
    cur_zip = (str(ad.get("zip") or "")).strip().strip("-").strip()
    if zip_v and zip_s >= 0.99 and cur_zip != str(zip_v):
        # Un cod postal VALID dat de client NU se muta in alta localitate (validatorul cauta strada
        # in tot judetul cand orasul e scris gresit si intoarce alt sat cu scor 1.0). Completam liber
        # doar codul lipsa/nereal; rafinam segmentul doar in ACEEASI localitate.
        _cv = _fold(city_v or "")
        _cc = _fold(ad.get("city") or "")
        _same_loc = bool(_cv) and (_cv == _cc or _typo_ok(_cv, _cc) or _typo_ok(_cc, _cv))
        if (not _zip_real(cur_zip)) or _same_loc:
            applied["zip"] = str(zip_v); changed = True
        else:
            zip_v = None   # nu ancoram nici orasul/judetul pe un zip pe care l-am refuzat
    # ZIP-ul confirmat (scor 1.0) ANCOREAZA locatia -> orasul/judetul corect = ce zice validatorul,
    # chiar daca scorul lor per-camp e mic (prefix "Com", typo "Buvuresti", oras duplicat in camp).
    zip_anchored = bool(zip_v and zip_s >= 0.99)
    if city_v and _fold(city_v) != _fold(ad.get("city") or "") \
       and (city_s >= 0.99 or (zip_anchored and ov >= 0.95 and city_s >= 0.6)):
        applied["city"] = str(city_v).title(); changed = True
    county_v, county_s = gv("county")
    if county_v and _fold(county_v) != _fold(ad.get("province") or "") \
       and (county_s >= 0.99 or (zip_anchored and ov >= 0.95)):
        applied["province"] = str(county_v).title(); changed = True
    if st_v and 0.5 <= st_s < 0.99:
        # scoate lamuririle din paranteze ale CLIENTULUI ("Mihai Viteazu(Caminele I.M.R.)") — adauga
        # tokeni de zgomot care sparg acoperirea; strada e in afara parantezei.
        orig_a1 = re.sub(r"\([^)]*\)", " ", _fold(ad.get("address1") or ""))
        # lamuririle din nomenclator ("aviatorilor (cartier veteranilor)") nu fac parte din nume
        _stv = re.sub(r"\([^)]*\)", " ", _fold(st_v))
        _sall = [t for t in re.split(r"[^a-z0-9]+", _stv) if t]
        # cifrele se verifica SEPARAT (regula cifrelor), nu se cer ca "cuvinte" de potrivit
        _sttok = [t for t in _sall if not t.isdigit() and len(t) >= 3]
        # Compara DOAR cu ce a parsat validatorul ca fiind STRADA (tokenizedAddress.streetName),
        # nu cu tot textul adresei: altfel sugestia se "potrivea" pe alt cuvant din adresa si
        # SCHIMBA strada — masurat pe comenzi reale: "Nicolina str prof. Ion Inculet" devenea
        # "Strada Nicolina" (cartier), iar "aviatorului nicolae DROSU" -> "ROSU nicolae".
        _base = _fold(tok.get("streetName") or "") or orig_a1
        _otok = _tok_words(_base)
        # CIFRELE fac parte din numele strazii: "Orizont 9" NU e "Orizont 1", "1 Decembrie 1918"
        # nu e "22 Decembrie". Daca sugestia are o cifra pe care clientul n-a scris-o -> alta strada.
        _sdig = {t for t in _sall if t.isdigit()}
        _odig = {t for t in _otok if t.isdigit()}
        _stw = list(_sttok)

        # potrivirea FUZZY (typo/declinare/initiala) doar cand validatorul e SIGUR pe strada (scor >=0.85);
        # sub asta, o diferenta de 1 litera poate fi ALTA strada reala (Cringului=Crangului ≠ Crinului).
        _fuzzy_ok = st_s >= 0.85
        def _hit(t):
            if t in _otok:
                return True                                    # potrivire EXACTA -> mereu ok
            if not _fuzzy_ok:
                return False                                   # scor mic -> NU accept typo/declinare (risc alta strada)
            if any(_typo_ok(t, o) for o in _otok):
                return True
            if any(_same_street_token(t, o) for o in _otok):   # declinare RO (Grivitei=Grivita)
                return True
            # initiala: clientul scrie "M.Basarab" pentru "Matei Basarab"
            return len(t) > 1 and any(len(o) == 1 and o == t[0] for o in _otok)

        if _sttok and _sdig <= _odig and (_glued_ok(_otok, _stw) or all(_hit(t) for t in _sttok)):
            pass
        else:
            _sttok = []
        if _sttok:
            # numarul casei = cel parsat de validator ("Aleea 3 Fulger nr 5" -> 5, nu 3;
            # "1mai nr 68" -> 68, nu "1m"); doar daca lipseste cadem pe prima cifra din text.
            num = _numar_casa(tok, ad.get("address1") or "", _sdig)
            stype = (str(tok.get("streetType") or "Strada")).strip().title() or "Strada"
            _stname = re.sub(r"\s*\([^)]*\)", "", str(st_v)).strip()
            new_a1 = ("%s %s%s" % (stype, _stname.title(), ((" " + num) if num else ""))).strip()
            if _fold(new_a1) != orig_a1:
                applied["address1"] = new_a1; changed = True
    return applied if changed else None


def _nomen_fix(ad):
    """Corectie din nomenclatorul RO (metrics.public.romania_addresses) pt UNKNOWN (validator = 0).
    OMONIMIA e problema centrala in RO (Bradu = 3 judete, Corni = 6) -> JUDETUL comenzii dezambiguizeaza:
      1) cauta localitatea IN JUDETUL comenzii -> daca e acolo, judetul e BUN (completeaza doar zip-ul);
      2) daca NU e in judetul comenzii -> judetul e probabil GRESIT: cauta in toata tara, accepta doar UNIC.
    Zip: al localitatii daca e UNIC (in acel judet); altfel potriveste STRADA pe TOKENI (prinde
    "General Dascalescu" <-> "Dascalescu General") -> zip UNIC. Curata "ROMANIA" din oras si ia
    localitatea din adresa cand e scrisa acolo ("Comuna Corni ...") iar campul oras are alt oras."""
    try:
        cur = metrics_cursor()
    except Exception:
        cur = None
    if not cur:
        return None
    a1 = _clean_chars(ad.get("address1") or "")
    if _adresa_junk(a1):
        return None
    a1f = _fold(a1)
    cands = []
    m = re.search(r"\b(?:comuna|com\.|sat)\s+([^\d,\.]{3,32})", a1, re.I)
    if m:
        lc = _fold(m.group(1)).strip(" .,-")
        if len(lc) >= 3:
            cands.append(lc)
    c = _fold(ad.get("city") or "")
    c = re.sub(r"\bromania\b", "", c)
    c = re.split(r"\d", c)[0].strip()
    c = re.sub(r"\s+(sector|sectorul|com|comuna|sat|jud)\b.*$", "", c).strip(" .,-")
    if len(c) >= 3:
        cands.append(c)
        # orasele cu CRATIMA se scriu si cu spatiu (si invers) — incearca ambele variante,
        # altfel "Piatra-neamt" nu prinde "piatra neamt" din nomenclator (clasa intreaga: Cluj-Napoca,
        # Targu-Jiu, Drobeta-Turnu Severin...).
        for alt in (c.replace("-", " "), c.replace(" ", "-")):
            alt = " ".join(alt.split())
            if alt != c and len(alt) >= 3 and alt not in cands:
                cands.append(alt)
    prov = _fold(ad.get("province") or "")
    for city in cands:
        judet = judet_norm = corr_city = None
        try:
            if prov:
                cur.execute("SELECT judet, judet_norm FROM public.romania_addresses "
                            "WHERE localitate_norm = %s AND judet_norm = %s LIMIT 1", (city, prov))
                r = cur.fetchall()
                if r:
                    judet, judet_norm = r[0]
            if judet is None:
                cur.execute("SELECT DISTINCT judet, judet_norm FROM public.romania_addresses "
                            "WHERE localitate_norm = %s", (city,))
                rows = cur.fetchall()
                if len(rows) == 1:
                    judet, judet_norm = rows[0]
                elif not rows and prov and len(city) >= 5:
                    # TYPO de localitate: romanii scriu "i" unde nomenclatorul are "â/î"
                    # (Dirlos/Dârlos, Ramnicu/Râmnicu). Cautam O SINGURA localitate la o litera
                    # distanta IN JUDETUL comenzii (judetul dezambiguizeaza).
                    cur.execute("SELECT DISTINCT localitate, localitate_norm, judet FROM "
                                "public.romania_addresses WHERE judet_norm = %s", (prov,))
                    near = [r for r in cur.fetchall() if r[1] and _lev(r[1], city) == 1]
                    if len({r[1] for r in near}) != 1:
                        continue
                    city = near[0][1]
                    judet, judet_norm = near[0][2], prov
                    corr_city = near[0][0]
                else:
                    continue
        except Exception:
            return None
        corr = {}
        if corr_city:
            corr["city"] = corr_city
        elif _fold(city) != _fold(ad.get("city") or ""):
            # localitatea rezolvata (des din adresa: "comuna Bradu") difera de campul oras
            # (reședința de județ pusa gresit) -> aliniaza si orasul, nu doar zip-ul, altfel
            # zip 117140 (Bradu) + oras Pitesti = nepotrivire care poate ruta gresit coletul.
            try:
                cur.execute("SELECT localitate FROM public.romania_addresses "
                            "WHERE localitate_norm = %s AND judet_norm = %s "
                            "AND localitate IS NOT NULL LIMIT 1", (city, judet_norm))
                _r = cur.fetchall()
                if _r:
                    corr["city"] = _r[0][0]
            except Exception:
                pass
        if _fold(ad.get("province") or "") != _fold(judet_norm or judet or ""):
            corr["province"] = judet
        cur_zip = (str(ad.get("zip") or "")).strip().strip("-").strip()
        if not cur_zip:
            try:
                cur.execute("SELECT DISTINCT cod_postal FROM public.romania_addresses "
                            "WHERE localitate_norm = %s AND judet_norm = %s "
                            "AND cod_postal IS NOT NULL AND cod_postal <> ''", (city, judet_norm))
                zips = [z[0] for z in cur.fetchall()]
            except Exception:
                zips = []
            if len(zips) == 1:
                corr["zip"] = zips[0]
            elif len(zips) > 1:
                try:
                    cur.execute("SELECT nume_strada, cod_postal FROM public.romania_addresses "
                                "WHERE localitate_norm = %s AND judet_norm = %s "
                                "AND cod_postal IS NOT NULL AND cod_postal <> ''", (city, judet_norm))
                    # Nomenclatorul scrie strada "NUME PRENUME [rang]" (ex. "DASCALESCU GENERAL"),
                    # clientul scrie "Bulevardul General Dascalescu" -> cerand TOATE cuvintele ratam.
                    # Folosim cuvantul DISTINCTIV (cel mai lung, >=5 litere); siguranta = zip UNIC.
                    hits = {}
                    for nm, z in cur.fetchall():
                        toks = sorted([t for t in _fold(nm or "").split() if len(t) >= 5], key=len, reverse=True)
                        if toks and toks[0] in a1f:
                            hits[z] = nm
                    if len(hits) == 1:
                        corr["zip"] = list(hits.keys())[0]
                except Exception:
                    pass
        stfix = _nomen_street_typo(cur, ad, city, judet_norm)
        if stfix:                                  # typo de strada -> nume canonic (+ zip daca e unic)
            corr["address1"] = stfix[0]
            if stfix[1] and not cur_zip:
                corr["zip"] = stfix[1]
        corr = {k: v for k, v in corr.items() if _fold(str(v or "")) != _fold(str(ad.get(k) or ""))}
        if corr:
            return corr
    return None


def _awbprint_pii(order_name):
    """firstName/lastName/phone/company din AWBprint (shipping_address JSON) — sursa NE-redactata a PII.
    None daca lipseste DB/order. Nu scriem in Shopify fara PII (altfel le-am goli)."""
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return None
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select shipping_address from orders where order_number = :n order by id desc limit 1", n=order_name)
        if not rows or not rows[0][0]:
            return None
        sa = rows[0][0]
        sa = json.loads(sa) if isinstance(sa, str) else sa
        pii = {"firstName": sa.get("first_name"), "lastName": sa.get("last_name"),
               "phone": sa.get("phone"), "company": sa.get("company"),
               "_a1": sa.get("address1") or "", "_city": sa.get("city") or ""}
        return pii if (pii["firstName"] or pii["lastName"] or pii["phone"]) else None
    except Exception:
        return None
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass


_INTL_CC = {"CZ": "420", "CZE": "420", "PL": "48", "POL": "48", "BG": "359", "BGR": "359", "HU": "36", "HUN": "36", "SK": "421", "SVK": "421"}


def _intl_phone(country, ph):
    """Telefon INTERNATIONAL corect per tara (CZ+420/PL+48/BG+359). RO/altele -> neatins. Incert -> neatins."""
    if not ph:
        return ph
    p = str(ph).strip()
    dg = re.sub(r"\D", "", p)
    cc = _INTL_CC.get((country or "").upper())
    if not cc or len(dg) < 8:
        return ph
    if p.startswith("+"):
        return "+" + dg
    if dg.startswith("00" + cc):
        return "+" + dg[2:]
    if dg.startswith(cc) and len(dg) == len(cc) + 9:
        return "+" + dg
    if dg.startswith("0") and len(dg) == 10:
        return "+" + cc + dg[1:]
    if len(dg) == 9 and not dg.startswith("0"):
        return "+" + cc + dg
    return ph


def _awbprint_customer_email(order_name):
    """customer_email REAL din AWBprint (ne-redactat). None daca lipseste/invalid."""
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return None
    import os as _os
    url = _os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select customer_email from orders where order_number = :n order by id desc limit 1", n=order_name)
        e = rows[0][0] if rows and rows[0] else None
        return e if (e and "@" in e) else None
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def shopify_push_corrected(shop_domain, order_name, applied):
    """Scrie adresa corectata SI in Shopify (orderUpdate shippingAddress) — sursa de adevar din care
    xConnector/Frisbo/SmartBill re-sincronizeaza. PCD-safe: nume/telefon din AWBprint (Shopify le redacteaza
    la citire). FAIL-SAFE: fara PII din AWBprint NU scriem (ca sa nu golim nume/telefon). Best-effort."""
    if not order_name:
        return False
    try:
        st = {t.get("shopDomain"): t for t in load_shopify_tokens()}.get(shop_domain)
        if not st:
            return False
        pii = _awbprint_pii(order_name)
        if not pii:
            return False   # fara PII ne-redactat -> risc sa golim nume/telefon -> NU scriu
        gid, cur = shopify_order_address(st["shopDomain"], st["adminToken"], order_name)
        if not gid:
            return False
        # GARDA "ACEEASI ADRESA" (cerinta owner): scriu DOAR daca e ACELASI adresa curatata, nu una
        # DIFERITA. Orasul e readable (nu-l redacteaza PCD) -> daca localitatea corectiei NU se potriveste
        # cu cea din Shopify ACUM (client/CS a schimbat adresa in alt oras), NU suprascriu.
        _ac, _sc = _fold(applied.get("city") or ""), _fold(cur.get("city") or "")
        if _ac and _sc and not (_ac == _sc or _typo_ok(_ac, _sc) or _typo_ok(_sc, _ac) or _ac in _sc or _sc in _ac):
            return False
        # GARDA STRADA: Shopify REDACTEAZA strada (PCD) -> o compar cu strada ORIGINALA din AWBprint.
        # Corectia trebuie sa fie o CURATARE a aceleiasi strazi (token distinctiv >=5 comun). Daca xConnector
        # si AWBprint NU sunt de acord pe strada (client/CS a schimbat DOAR strada, acelasi oras) -> NU scriu.
        import re as _re
        _at = {t for t in _re.findall(r"[a-z]+", _fold(applied.get("address1") or "")) if len(t) >= 5 and t not in _ST_RANK}
        _ot = {t for t in _re.findall(r"[a-z]+", _fold(pii.get("_a1") or "")) if len(t) >= 5 and t not in _ST_RANK}
        if _at and _ot and not any(a == o or _typo_ok(a, o) or _same_street_token(a, o) for a in _at for o in _ot):
            return False
        new = {"countryCode": cur.get("countryCodeV2") or "RO"}
        changed = False
        for k in ("address1", "address2", "city", "zip", "province"):
            v = applied.get(k)
            if v not in (None, "") and _fold(str(v)) != _fold(str(cur.get(k) or "")):
                new[k] = v; changed = True
            elif cur.get(k) not in (None, ""):
                new[k] = cur.get(k)
        if not changed:
            return False
        for k in ("firstName", "lastName", "phone", "company"):
            if pii.get(k):
                new[k] = _intl_phone(cur.get("countryCodeV2"), pii[k]) if k == "phone" else pii[k]
        if not new.get("province") and cur.get("provinceCode"):
            new["provinceCode"] = cur.get("provinceCode")
        m = "mutation($input: OrderInput!){ orderUpdate(input:$input){ order{ id } userErrors{ field message } } }"
        d = shopify_gql(st["shopDomain"], st["adminToken"], m, {"input": {"id": gid, "shippingAddress": new}})
        errs = (((d.get("data") or {}).get("orderUpdate") or {}).get("userErrors")) or d.get("errors")
        return not errs
    except Exception:
        return False


def _hist_street_name(a1):
    s = _fold(a1)
    s = re.sub(r"\b(bl|bloc|sc|scara|ap|apt|et|etaj|nr|numarul|no|parter|interfon|corp|tronson)\b.*$", " ", s)
    s = re.sub(r"\b(strada|str|bulevardul|bulevard|bd|bdul|calea|cale|aleea|alee|al|soseaua|sos|drumul|drum|intrarea|intrare|piata)\b", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(w for w in s.split() if len(w) >= 3)


def _hist_real_street(a1):
    a1f = _fold(a1)
    has_type = bool(re.search(r"\b(strada|str|bulevard|bd|calea|aleea|al|soseaua|sos|drumul|drum|intrarea|piata|splai)\b", a1f))
    sn = _hist_street_name(a1); words = sn.split()
    if any(h in a1f for h in ("comuna", "sat", "oras", "municipiul", "judet")) and not has_type:
        return False
    return (has_type and len(sn) >= 4) or (len(words) >= 2 and len(sn) >= 7)


def _hist_block(a1):
    m = re.search(r"\b(?:bl|bloc)\.?\s*([a-z]?\d+[a-z]?|\d+[a-z]?)\b", _fold(a1))
    return m.group(1) if m else None


# Repere care sunt STRĂZI (formă genitiv) când urmează un NUMĂR — NU instituții numerotate. WHITELIST strict:
# EXCLUS deliberat școala/grădinița/liceul/spitalul/secția/postul/căminul/colegiul/etc. — acolo „nr X" e numărul
# INSTITUȚIEI, nu al casei („Școala nr 5" = Școala Gimnazială Nr. 5 = instituție, NU Strada Școlii). Reperele de aici
# nu se numerotează niciodată ca instituții → „+ număr" = numărul casei pe strada omonimă.
_LANDMARK_GEN = {
    "biserica": "Bisericii", "biserika": "Bisericii", "gara": "Gării", "moara": "Morii",
    "podul": "Podului", "pod": "Podului", "parcul": "Parcului", "parc": "Parcului",
    "cimitirul": "Cimitirului", "cimitir": "Cimitirului", "fantana": "Fântânii", "fintana": "Fântânii",
    "izvorul": "Izvorului", "izvor": "Izvorului", "dealul": "Dealului", "deal": "Dealului",
    "stadionul": "Stadionului", "stadion": "Stadionului", "lacul": "Lacului",
}
def _landmark_genitive(a1):
    """Reper-NOMINATIV + număr la ÎNCEPUTUL adresei ('Biserica nr 10') → forma GENITIV a străzii ('Bisericii nr 10').
    DOAR reperele din `_LANDMARK_GEN` (nu-s instituții numerotate). Necesită un NUMĂR după reper (e adresă, nu reper
    pur). None dacă nu se aplică. Nomenclatorul confirmă strada oricum → dacă nu există, needs-geocoder, fără scriere."""
    if not a1:
        return None
    m = re.match(r"(?i)^\s*([a-zăâîșț]+)\b(.*)$", a1)
    if not m:
        return None
    gen = _LANDMARK_GEN.get(_fold(m.group(1)))
    if gen and re.search(r"\d", m.group(2)):
        return gen + m.group(2)
    return None


def _hist_street_part(a1):
    """Partea de STRADĂ (până la primul bl/bloc/sc/ap/et), folded — unde stă numărul casei."""
    return re.split(r"\b(bl|bloc|sc|scara|ap|apt|et|etaj|interfon)\b", _fold(a1))[0]


def _hist_house_num(a1):
    """Numărul casei din partea de stradă (nr X / X, ÎNAINTE de bloc/scară/ap). None dacă lipsește."""
    sp = _hist_street_part(a1)
    m = re.search(r"\bnr\.?\s*(\d+[a-z]?)\b", sp) or re.search(r"\b(\d+[a-z]?)\b", sp)
    return m.group(1) if m else None


def _hist_has_num(a1):
    """True dacă partea de stradă are ORICE cifră (deci un număr — sau un nume cu cifră gen '1 Decembrie')."""
    return bool(re.search(r"\d", _hist_street_part(a1)))


def customer_history_addr(order_name, ad):
    """Completează adresa din ISTORICUL clientului (AWBprint, telefon), din comenzi LIVRATE anterioare (owner: mai
    sigur LIVRATĂ decât doar plecată — coletul a AJUNS → adresa sigur funcționează):
    (1) comanda curentă N-ARE stradă → reutilizez STRADA; (2) ARE stradă dar N-ARE NUMĂR casă → completez
    NUMĂRUL de la o comandă livrată pe ACEEAȘI stradă (număr UNIC în istoric). Întoarce {'address1':..,'zip':..}
    sau None. SIGUR: același telefon + același oraș + LIVRATĂ + aceeași stradă."""
    _cura1 = ad.get("address1") or ""
    _has_street = _hist_real_street(_cura1)
    _need_number = _has_street and not _hist_has_num(_cura1)   # are stradă dar fără cifră în partea de stradă
    if _has_street and not _need_number:
        return None   # adresă completă (stradă + număr) → nu mă bag
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
        import json as _json
    except Exception:
        return None
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select shipping_address from orders where order_number = :n order by id desc limit 1", n=order_name)
        if not rows or not rows[0][0]:
            return None
        sa = rows[0][0]; sa = _json.loads(sa) if isinstance(sa, str) else sa
        phone = re.sub(r"\D", "", sa.get("phone") or "")
        if len(phone) < 9:
            return None
        p9 = phone[-9:]
        city = _fold(sa.get("city") or ad.get("city") or "")
        blk = _hist_block(sa.get("address1") or ad.get("address1") or "")
        # comandă anterioară LIVRATĂ — owner (după reflecție): mai sigur LIVRATĂ decât doar plecată (coletul a
        # AJUNS → adresa sigur funcționează; plecată-dar-refuzată putea fi adresă proastă). delivered = livrat.
        prior = con.run("select o.order_number, o.shipping_address, o.aggregated_status from orders o "
                        "where regexp_replace(o.shipping_address->>'phone','\D','','g') like :p "
                        "and o.order_number <> :n and o.aggregated_status = 'delivered' "
                        "order by o.id desc limit 40",
                        p="%" + p9, n=order_name)
        cand = []
        for onm, osa, _st in prior:
            osa = _json.loads(osa) if isinstance(osa, str) else osa
            if not _hist_real_street(osa.get("address1")):
                continue
            cc = _fold(osa.get("city") or "")
            if city and cc != city and not (cc in city or city in cc):
                continue
            cand.append((osa.get("address1"), osa.get("zip"), _hist_street_name(osa.get("address1")),
                         _hist_block(osa.get("address1")), _hist_house_num(osa.get("address1"))))
        if not cand:
            return None
        # ── CAZUL (2): are stradă, lipsește NUMĂRUL → completez de la o comandă livrată pe ACEEAȘI stradă ──
        if _need_number:
            cur_sn = _hist_street_name(_cura1)
            nums = {c[4] for c in cand if c[2] == cur_sn and c[4]}   # numere de casă de pe ACEEAȘI stradă
            if len(nums) != 1:
                return None   # 0 = n-am de unde · 2+ = ambiguu (s-a mutat pe stradă?) → CS
            num = nums.pop()
            parts = re.split(r"(?i)(\b(?:bl|bloc)\b)", _cura1, 1)
            street = parts[0].rstrip(" ,")
            rest = "".join(parts[1:]).strip()
            new_a1 = (street + " nr " + num + (" " + rest if rest else "")).strip()
            out = {"address1": new_a1}
            _zp = next((c[1] for c in cand if c[2] == cur_sn and c[1] and re.fullmatch(r"\d{6}", str(c[1]).strip())), None)
            cz = str(ad.get("zip") or "").strip().strip("-")
            if not re.fullmatch(r"\d{6}", cz) and _zp:
                out["zip"] = str(_zp).strip()
            return out
        # ── CAZUL (1): fără stradă → reutilizez STRADA (bloc identic SAU o singură stradă livrată) ──
        streets = {x[2] for x in cand}
        bm = [x for x in cand if blk and x[3] == blk]
        pick = bm[0] if bm else (cand[0] if len(streets) == 1 else None)
        if not pick or _hist_street_name(pick[0]) == _hist_street_name(ad.get("address1") or ""):
            return None
        _prior = pick[0] or ""
        _m = re.search(r"\b(?:bl|bloc)\b", _fold(_prior))
        _street_part = (_prior[:_m.start()] if _m else _prior).strip(" ,.")
        _cur = (ad.get("address1") or "").strip()
        new_a1 = ("%s, %s" % (_street_part, _cur)) if _cur else _street_part
        out = {"address1": new_a1}
        cz = str(ad.get("zip") or "").strip().strip("-")
        if not re.fullmatch(r"\d{6}", cz) and pick[1] and re.fullmatch(r"\d{6}", str(pick[1]).strip()):
            out["zip"] = str(pick[1]).strip()
        return out
    except Exception:
        return None
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass


def correct_address(xc, o, shop_domain, apply=False):
    """CONSERVATOR (după aac): corectează adresa DOAR dacă există UN candidat cu toate core ≥0.95
    + zip confirmat la /zip-code + numărul casei păstrat. Întoarce (status, applied|None, detalii).
    status: would-correct | corrected | manual | error:<code>."""
    oid = o["orderId"]
    d = xc.by_id(oid)
    for _ in range(4):                       # by_id GOL = rate-limit → retry (altfel corectia nu vede adresa)
        if d and d.get("shippingAddress"): break
        time.sleep(3); d = xc.by_id(oid)
    d = d or {}
    ad = d.get("shippingAddress") or {}
    # RO-ONLY: tot ce urmeaza (match "Romania", nomenclatorul RO, country="Romania") ar transforma
    # o adresa CZ/PL/BG intr-una "romaneasca". Intl are propria cale (HERE + nomenclatoare intl).
    if shop_domain in HERE_COUNTRY or _fold(ad.get("country") or "romania") not in ("", "romania", "ro", "rou"):
        return "manual", None, "non-RO (intl are cale proprie)"
    ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                   "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
    msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
    # zip/oraș/județ ≥0.95 + stradă ≥0.90 (relaxat — există plasă de siguranță DPD/client la preluare).
    # UN singur candidat (fără competitor) = nu riscăm o adresă validă-dar-greșită.
    strong = [m for m in msl if all((fscore(m, f)[1] or 0) >= 0.95 for f in ("zipCode", "county", "city"))
              and (fscore(m, "streetName")[1] or 0) >= 0.90]
    def _fallback_fix(why):
        """Lantul de REZERVA (validarea stocata xConnector -> nomenclator RO). Se apeleaza din
        TOATE iesirile "manual", nu doar cand lipseste candidatul tare: un candidat tare care pica
        pe "numar casa nesigur" nu inseamna ca adresa nu se poate repara altfel."""
        vapplied = _valid_fix(d, ad)
        if vapplied is None:
            ncorr = _nomen_fix(ad)
            if ncorr:
                vapplied = dict(ad); vapplied["country"] = "Romania"; vapplied.update(ncorr)
        if vapplied is None:
            _hz = here_zip_fill(ad, here_key())
            if _hz:
                vapplied = dict(ad); vapplied["country"] = "Romania"; vapplied.update(_hz)
        if vapplied is None:
            # ISTORIC client: adresa fara strada -> reutilizez strada dintr-o comanda LIVRATA anterioara.
            _hist = customer_history_addr(d.get("orderName"), ad)
            if _hist:
                vapplied = dict(ad); vapplied["country"] = "Romania"; vapplied.update(_hist)
        if vapplied is None:
            return "manual", None, why
        vdetail = "[valid-suggest] %s, %s %s" % (vapplied.get("address1"), vapplied.get("city"), vapplied.get("zip"))
        if not apply:
            return "would-correct", vapplied, vdetail
        vbody = {"orderId": oid,
                 "idempotencyKey": "vsug-%s-%s-%s" % (_digest(shop_domain, 8), oid, _digest(vapplied, 12)),
                 "appliedShippingAddress": vapplied,
                 "expectedAddressHash": d.get("addressHash"), "expectedStatusHash": d.get("statusHash"),
                 "expectedEvidenceHash": d.get("evidenceHash"), "agentClaimedConfidence": 0.9,
                 "agentRationale": "xConnector stored validation identified the street (noise lowered score); cleaned same street, number preserved.",
                 "modelName": "gigi-xconnector", "mcpClientId": "gigi-xconnector"}
        vs, _vb = http("POST", XBASE + "/api/orders/ai-correct-address", xc.h, vbody)
        if vs == 200:
            shopify_push_corrected(shop_domain, d.get("orderName"), vapplied)   # tine Shopify sincron
        return ("corrected" if vs == 200 else "error:%s" % vs), vapplied, vdetail

    if len(strong) != 1:
        return _fallback_fix("%d candidați (zip/oraș/județ≥0.95, stradă≥0.90)" % len(strong))
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
        return _fallback_fix("număr casă nesigur")
    if not zip_confirm(xc, czip):
        return _fallback_fix("zip neconfirmat")
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
    if s == 200:
        shopify_push_corrected(shop_domain, d.get("orderName"), applied)   # tine Shopify sincron
    return ("corrected" if s == 200 else "error:%s" % s), applied, detail


def metrics_cursor():
    """Cursor DBAPI (pg8000) pe metrics warehouse — nomenclatorul RO `public.romania_addresses`. None dacă lipsește.
    DOAR SELECT-uri (read-only pe metrics): nomenclatorul se CITEȘTE; corecția se SCRIE în comandă via xConnector."""
    try:
        import pg8000
        from urllib.parse import urlparse, unquote
    except Exception:
        return None
    url = os.environ.get("DATABASE_URL_METRICS") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_METRICS"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url)
    for use_ssl in (True, False):
        try:
            kw = dict(user=unquote(u.username or ""), password=unquote(u.password or ""),
                      host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"))
            if use_ssl:
                kw["ssl_context"] = True
            con = pg8000.connect(**kw)
            con.autocommit = True
            return con.cursor()
        except Exception:
            continue
    return None


_MCUR = {"cur": None}
def metrics_cursor_live():
    """Cursor metrics REZILIENT pt nomenclatoare (RO/CZ/PL/BG). Reutilizează conexiunea DAR reconectează dacă a
    murit. CRITIC: pe rulările lungi de cron (~13 min, ~15 magazine) conexiunea idle pică; cursorul mort făcea
    intl_nomen/nomenclator să dea None → comenzi BUNE cădeau pe here_nogo → CS (ex. BG: 82/86 valid trimise greșit la CS)."""
    c = _MCUR.get("cur")
    if c is not None:
        try:
            c.execute("SELECT 1"); c.fetchone(); return c
        except Exception:
            try: c.connection.close()
            except Exception: pass
            _MCUR["cur"] = None
    _MCUR["cur"] = metrics_cursor()
    return _MCUR["cur"]


def _nomen_write(xc, oid, d, ad, applied, shop_domain):
    """POST ai-correct-address cu adresa `applied` + push în Shopify. True dacă 200."""
    body = {"orderId": oid,
            "idempotencyKey": "nom-%s-%s-%s-%s" % (_digest(shop_domain, 8), oid,
                              _digest({k: _fold(str(v)) for k, v in ad.items()}, 12), _digest(applied, 12)),
            "appliedShippingAddress": applied,
            "expectedAddressHash": d.get("addressHash"), "expectedStatusHash": d.get("statusHash"),
            "expectedEvidenceHash": d.get("evidenceHash"), "agentClaimedConfidence": 0.95,
            "agentRationale": "RO nomenclature (romania_addresses v8.3.1): deterministic ZIP<->street reconciliation, "
                              "house number preserved, real customer streets never overwritten.",
            "modelName": "gigi-nomenclator", "mcpClientId": "gigi-xconnector"}
    s, b = http("POST", XBASE + "/api/orders/ai-correct-address", xc.h, body)
    if s == 200:
        shopify_push_corrected(shop_domain, d.get("orderName"), applied)   # ține Shopify sincron
    return s == 200


def ro_city_general_zip(mcur, city, judet=None):
    """Codul poștal GENERAL al orașului = MIN(cod_postal) pe localitate în nomenclator. Fallback pt ZIP LIPSĂ când
    strada e REALĂ dar nu-i indexată în nomenclator (owner: 'zip-ul e pentru străzile care nu-s indexate altfel')
    iar orașul E valid → DPD acceptă cu codul general + textul străzii (ex. Buzău→120001). Vezi garda din
    _addr_has_street_and_number: la orașe mari trebuie STRADĂ+NUMĂR, altfel coletul nu-i livrabil.
    ⚠️ FILTREAZĂ pe JUDEȚ (nume ambigue: 'Satu Mare' orașul vs sate omonime — fără județ MIN pica pe un sat
    din alt județ). Cu județ întâi; dacă nu leagă (județ greșit/gol) → fallback fără județ."""
    if mcur is None or not (city or "").strip():
        return None
    c = (city or "").strip()
    try:
        cf = _fold(c)
    except Exception:
        cf = c.lower()
    try:
        if judet and str(judet).strip():
            try:
                jf = _fold(judet)
            except Exception:
                jf = str(judet).strip().lower()
            mcur.execute("SELECT min(cod_postal) FROM public.romania_addresses "
                         "WHERE (localitate_norm = %s OR lower(localitate) = lower(%s)) "
                         "AND (judet_norm = %s OR lower(judet) = lower(%s))", (cf, c, jf, judet))
            r = mcur.fetchone()
            if r and r[0]:
                return r[0]
        mcur.execute("SELECT min(cod_postal) FROM public.romania_addresses "
                     "WHERE localitate_norm = %s OR lower(localitate) = lower(%s)", (cf, c))
        r = mcur.fetchone()
        return (r[0] if r and r[0] else None)
    except Exception:
        return None


def ro_judet_from_city(mcur, city):
    """Județul UNIC al orașului din nomenclator (None dacă ambiguu/necunoscut). ORAȘUL e mai de încredere decât
    câmpul `province` — des defaultat GREȘIT (mai ales pe „București": Slatina/Constanța/Câmpina/Voluntari puse
    ca București). Owner: „orice câmp poate fi în orice câmp" → re-derivă județul din oraș. Doar când e UNIC
    (oraș omonim în mai multe județe = ambiguu → nu ghicim, lăsăm province-ul dat)."""
    if mcur is None or not (city or "").strip():
        return None
    try:
        cf = _fold(city)
    except Exception:
        cf = (city or "").strip().lower()
    try:
        mcur.execute("SELECT judet, count(distinct cod_postal) AS n FROM public.romania_addresses "
                     "WHERE localitate_norm = %s OR lower(localitate) = lower(%s) "
                     "GROUP BY judet ORDER BY n DESC", (cf, city))
        js = [(r[0], r[1]) for r in mcur.fetchall() if r[0]]
        if not js:
            return None
        if len(js) == 1:
            return js[0][0]
        # AMBIGUU (oraș omonim în mai multe județe): alege MUNICIPIUL = județul cu net mai multe coduri poștale
        # (ex. Slatina: Olt=zeci de coduri vs sate omonime=1). Doar dacă domină CLAR (≥5 coduri ȘI ≥3× următorul).
        if js[0][1] >= 5 and js[0][1] >= 3 * js[1][1]:
            return js[0][0]
        return None
    except Exception:
        return None


def _zip_from_fields(*vals):
    """ZIP RO (6 cifre) găsit în ORICE câmp (owner: „orice câmp poate fi în orice câmp"). Clienții pun zip-ul în
    a1/a2/city, nu în câmpul zip (ex. a1=„905800, 17", city=„305600"). Primul token STANDALONE de 6 cifre, sau None.
    (Numărul de casă e 1-4 cifre; telefonul e 9-10 cifre lipite → niciunul nu dă fals-pozitiv pe \\b\\d{6}\\b.)"""
    for v in vals:
        if not v:
            continue
        m = re.search(r"\b(\d{6})\b", str(v))
        if m:
            return m.group(1)
    return None


def _zip_matches_city(mcur, zip_, city):
    """GARDĂ: zip-ul extras din câmpuri e CONSISTENT cu orașul dat? (evită să suprascrii Buzău cu 905800=Constanța).
    True dacă orașul e gol (nimic de contrazis) SAU localitatea zip-ului din nomenclator se potrivește cu orașul."""
    if not (city or "").strip():
        return True
    if mcur is None or not zip_:
        return False
    try:
        cf = _fold(city)
    except Exception:
        cf = (city or "").strip().lower()
    try:
        mcur.execute("SELECT localitate_norm, lower(localitate) FROM public.romania_addresses WHERE cod_postal = %s", (zip_,))
        for a, b in mcur.fetchall():
            if cf == (a or "") or cf == (b or "") or (a and (cf in a or a in cf)):
                return True
        return False
    except Exception:
        return False


# Termeni maghiari de adresă (RO din Harghita/Covasna/Mureș scriu ungurește) → RO. Numele localităților maghiare
# sunt deja în _CITY_ABBREV (address_rules). Aici doar TIPUL de arteră + markerii nr/județ.
# DOAR termeni UNIVOC maghiari (fără „ter"/„ut" simple — ambigue/rare în RO → risc de mângâiat adrese bune).
_HU_STREET = [(re.compile(r"(?i)\butca\b\.?"), "Strada"), (re.compile(r"(?i)\btér\b\.?"), "Piața"),
              (re.compile(r"(?i)\b(köz|koz)\b\.?"), "Aleea"), (re.compile(r"(?i)\b(körút|krt)\b\.?"), "Bulevardul")]
_HU_NUM = re.compile(r"(?i)\b(\d+)\s*szám\b\.?")
_HU_MEGYE = re.compile(r"(?i)[,.]?\s*\b\w+\s+megye\b\.?")
_HU_TRIGGER = re.compile(r"(?i)\b(utca|tér|köz|koz|körút|krt|szám|megye)\b")


def _translate_hungarian(s):
    """„Palás köz 7 szám" → „Aleea Palás nr 7". Tip arteră HU → RO (des e SUFIX în maghiară: 'X utca' → 'Strada X'),
    'N szám' → 'nr N', taie 'Y megye' (județul se derivă din oraș). Conservator: DOAR dacă apare un termen UNIVOC HU."""
    if not s or not _HU_TRIGGER.search(s):
        return s
    t = _HU_MEGYE.sub("", s)
    t = _HU_NUM.sub(r"nr \1", t)
    for rx, ro in _HU_STREET:
        m = rx.search(t)
        if m:
            # în maghiară tipul e SUFIX: „Palás utca" → nume=„Palás" + tip → „Strada Palás"
            name = (t[:m.start()] + t[m.end():]).strip(" ,.-")
            t = (ro + " " + name).strip()
            break
    return re.sub(r"\s{2,}", " ", t).strip(" ,.-")


def ro_city_is_big(mcur, city, judet=None):
    """Oraș MARE = municipiu/reședință cu multe artere indexate (>25 coduri poștale distincte pe localitate).
    La orașe mari codul general NU e suficient — coletul cere stradă+număr real (owner). La sate/comune mici
    (puține coduri) orașul + număr ajunge curierului. Filtrează pe județ (nume ambigue) cu fallback fără."""
    if mcur is None or not (city or "").strip():
        return False
    try:
        cf = _fold(city)
    except Exception:
        cf = (city or "").strip().lower()
    try:
        if judet and str(judet).strip():
            try:
                jf = _fold(judet)
            except Exception:
                jf = str(judet).strip().lower()
            mcur.execute("SELECT count(distinct cod_postal) FROM public.romania_addresses "
                         "WHERE (localitate_norm = %s OR lower(localitate) = lower(%s)) "
                         "AND (judet_norm = %s OR lower(judet) = lower(%s))", (cf, city, jf, judet))
            r = mcur.fetchone()
            if r and r[0]:
                return bool(r[0] > 25)
        mcur.execute("SELECT count(distinct cod_postal) FROM public.romania_addresses "
                     "WHERE localitate_norm = %s OR lower(localitate) = lower(%s)", (cf, city))
        r = mcur.fetchone()
        return bool(r and r[0] and r[0] > 25)
    except Exception:
        return False


def _addr_has_street_and_number(parts, city):
    """True dacă UNDEVA în câmpuri (a1/a2/oraș/județ/hint — 'adresa poate fi oriunde', owner) există o stradă
    REALĂ + un NUMĂR de casă. Codul general al orașului se aplică DOAR atunci: 'zip-ul e pentru străzile care
    nu-s indexate altfel', iar la orașe mari 'să fie și stradă și număr'. Fără stradă+număr (doar oraș / 'undefined'
    / nume de firmă) → NU e livrabil pe cod general → CS."""
    blob = " ".join(str(p) for p in parts if p and str(p).strip())
    if not blob.strip():
        return False
    try:
        bf = _fold(blob)
        cityf = _fold(city or "")
    except Exception:
        bf = blob.lower(); cityf = (city or "").lower()
    resid = bf
    for tok in (cityf.split() if cityf else []):        # scoate numele orașului ca să nu-l iei drept 'conținut de stradă'
        if len(tok) >= 3:
            resid = re.sub(r"\b" + re.escape(tok) + r"\b", " ", resid)
    has_num = bool(re.search(r"\b\d{1,4}\b", blob))     # număr casă/bloc (zip LIPSEȘTE aici → cifrele = nr, nu cod poștal)
    has_marker = bool(_ST_MARK.search(bf))              # 'str/bd/calea/șoseaua/aleea/…' = semnal tare de stradă
    alpha_words = [w for w in re.findall(r"[a-zăâîșşţț]+", resid) if len(w) >= 3]   # nume de stradă fără marker ('Mihai Eminescu')
    has_named = len(alpha_words) >= 2
    return has_num and (has_marker or has_named)


def _rural_street(a1, fields):
    """La RURAL, dacă adresa n-are stradă → 'Strada Principală' (owner: convenția satelor mici). Păstrează strada
    dacă există deja (marker sau nume) și numărul de casă dacă e undeva în câmpuri. Întoarce address1 de scris."""
    a1s = (a1 or "").strip()
    try:
        a1f = _fold(a1s)
    except Exception:
        a1f = a1s.lower()
    words = [w for w in re.findall(r"[a-zăâîșşţț]+", a1f) if len(w) >= 3 and w != "undefined"]
    if _ST_MARK.search(a1f) or len(words) >= 2:         # are deja stradă reală → păstrează
        return a1s
    blob = " ".join(str(f) for f in fields if f and str(f).strip())
    m = re.search(r"\bnr\.?\s*(\d{1,4}[A-Za-z]?)\b", blob, re.I) or re.search(r"\b(\d{1,4}[A-Za-z]?)\b", blob)
    return "Strada Principală" + ((" nr " + m.group(1)) if m else "")


def courier_street_snap(d, cur_street, min_score=0.80, min_sim=0.6):
    """Validatorul CURIERULUI (xConnector `latestAddressValidation.addressMatchers[].streetName`) sugerează strada
    cea mai apropiată din baza LUI. Când strada clientului nu-i în nomenclatorul curierului dar sugestia are scor
    mare ȘI e string-similară cu ce a scris clientul → o folosim (snap la baza curierului) ca eticheta să treacă.
    Owner: „dacă mai igienizăm". Întoarce numele sugerat sau None. GARDĂ anti-'stradă greșită cu scor mare' (ex.
    „Minovici"→„caloian vasile" 0.80): sugestia trebuie să semene string cu strada clientului (difflib/prefix)."""
    import difflib
    lv = (d or {}).get("latestAddressValidation") or {}
    best = None
    for m in (lv.get("addressMatchers") or []):
        if not isinstance(m, dict):
            continue
        sn = m.get("streetName")
        if isinstance(sn, dict) and sn.get("suggestionType") == "STREET_NAME" and sn.get("value"):
            sc = sn.get("score") or 0
            if best is None or sc > best[1]:
                best = (str(sn["value"]).strip(), sc)
    if not best or best[1] < min_score:
        return None
    sugg, _sc = best
    # nucleul străzii clientului: scoate tip-arteră + număr + bloc/ap, apoi fold
    core = re.sub(r"(?i)\b(str|strada|bd|b-dul|bdul|bulevardul|blvd|calea|aleea|alee|soseaua|sos|splaiul|intrarea|drumul|prelungirea|nr|bl|sc|ap|et)\b\.?", " ", cur_street or "")
    core = re.sub(r"\d+[A-Za-z]?", " ", core)
    try:
        cf = _fold(core).strip(); sg = _fold(sugg).strip()
    except Exception:
        cf = core.lower().strip(); sg = sugg.lower().strip()
    if not cf or not sg:
        return None
    c0 = cf.split()[0] if cf.split() else cf
    s0 = sg.split()[0] if sg.split() else sg
    sim = difflib.SequenceMatcher(None, cf, sg).ratio()
    if sim >= min_sim or (len(c0) >= 4 and (sg.startswith(c0) or cf.startswith(s0) or c0 in sg)):
        return sugg
    return None


def nomenclator_correct(xc, o, shop_domain, mcur, apply=False):
    """STRAT 1 (RO): corectează adresa pe NOMENCLATOR (metrics.public.romania_addresses, v8.3.1 portat).
    Determinist, ZERO fabricare — completează strada din ZIP DOAR când e gunoi, altfel corectează ZIP-ul (invers);
    NICIODATĂ nu suprascrie o stradă reală. Întoarce (status, applied|None, detalii).
    status: valid | would-correct | corrected | needs-geocoder | cs | error:<code> | skip:<reason>.
    PRE-CLEAN determinist (sigur, înainte de lookup): (a) câmpuri INVERSATE (a1=localitate, city=stradă) → swap;
    (b) deglue stradă ('STR.TECUCINR.191'→'Str TECUCI nr 191'); (c) canon oraș (zgomot 'Focșani, Jud Vrancea',
    abreviere 'Dr tr severin'→Drobeta-Turnu Severin, variantă 'BUCHARSTI'→București). Când pre-clean-ul schimbă
    ceva și adresa curățată validează/corectează pe nomenclator → SCRIU forma curățată (altfel DPD ia gunoiul)."""
    try:
        import address_nomenclator as N
    except Exception:
        return "skip:no-module", None, "address_nomenclator lipsă"
    if mcur is None:
        return "skip:no-db", None, "fără cursor metrics"
    oid = o["orderId"]
    d = xc.by_id(oid)
    for _ in range(4):                       # by_id GOL = rate-limit → retry (fără adresă, nomen n-are ce corecta)
        if d and d.get("shippingAddress"):
            break
        time.sleep(3); d = xc.by_id(oid) or {}
    ad = d.get("shippingAddress") or {}
    # ── PRE-CLEAN determinist: delabel → câmpuri inversate → prefix localitate → deglue → canon oraș ──
    _a1dl = _delabel(_translate_hungarian(ad.get("address1")))  # HU→RO ('Palás köz 7 szám'→'Aleea Palás nr 7'), apoi delabel
    _a1z, _a2z = _street_from_a2(_a1dl, ad.get("address2"))  # strada e în a2, a1=gunoi/landmark → promovează a2 la stradă
    _a1s, _citys, _swapped = _maybe_swap_fields(_a1z, ad.get("city"))
    _a1p = _strip_loc_prefix(_a1s, _citys)                # 'București sector 2 Ion Berindei…' → 'Ion Berindei…' (folosește orașul BRUT)
    _a1ap, _a2ap = _pull_artery_prefix(_a1p, _a2z)        # landmark ÎN FAȚA străzii ('Las Vegas Calea Unirii')→a1='Calea Unirii', a2+='Las Vegas'
    # localitatea scrisă ÎNAINTE de stradă ('Ipatele, str principala') e mutată de strip/artery-prefix — o dau
    # nomenclatorului ca `loc_hint` (o încearcă ca SAT-pur; dacă nu e localitate ('Las Vegas') → pur și simplu nu se leagă).
    _loc_hint = ""
    if _a1p != _a1s and _a1s.endswith(_a1p):              # strip_loc_prefix a scos un prefix
        _loc_hint = _a1s[:len(_a1s) - len(_a1p)].strip(" ,.-")
    elif _a2ap and _a2ap != (_a2z or ""):                # artery_prefix a mutat prefixul în a2
        _loc_hint = (_a2ap[len(_a2z):] if _a2z else _a2ap).strip(" ;,.-")
    if not (_loc_hint and not re.search(r"\d", _loc_hint) and 1 <= len(_loc_hint.split()) <= 3
            and not _ST_MARK.search(_fold(_loc_hint))):
        _loc_hint = ""
    _a1L, _a2L = _pull_landmark(_a1ap, _a2ap)             # mută nota-landmark '(magazin Profi)' din a1 în a2
    _a1t = _street_tail(_a1L)                             # coadă: sufix Google ', ZIP City, Romania' + segment duplicat
    _a1typo = re.sub(r"(?i)\bstada\b", "Strada", _a1t)   # typo frecvent 'Stada'→'Strada' (altfel tipul arterei nu se prinde)
    _a1e = _expand_street_initial(_expand_fullname(_expand_street_abbrev(_numword_nr(_street_deglue(_a1typo)))))  # deglue + nr-în-litere (nr opt→nr 8) + abrevieri (ctin→Constantin) + nume complet (A.I.Cuza / 'N Titulescu'→Nicolae Titulescu)
    _a1c, _a2L = _pull_block_details(_a1e, _a2L)          # bloc/scară/etaj/ap din stradă → address2 (păstrate pt curier): 'Bd Gării nr 10 bl 3 ap 2 sc b'→a1='Bd Gării nr 10'
    _a1c, _a2L = _pull_institution_tail(_a1c, _a2L)       # coadă reper/instituție DUPĂ număr → address2 ('…nr 4 piata obor hala nouă'→a1='…nr 4')
    _cityc, _cwhy = _city_denoise(_citys)                 # include Sector→București
    pre_notes = []
    if _a1dl != (ad.get("address1") or ""):
        pre_notes.append("delabel")
    if _a1z != (_a1dl or ""):
        pre_notes.append("stradă-din-a2")
    if _swapped:
        pre_notes.append("swap-câmpuri")
    if _a1p != (_a1s or ""):
        pre_notes.append("prefix-localitate")
    if _a2L != (ad.get("address2") or ""):
        pre_notes.append("landmark→a2")
    if _a1t != (_a1L or ""):
        pre_notes.append("coadă-stradă")
    if _a1c != (_a1t or ""):
        pre_notes.append("deglue-stradă")
    if _cwhy:
        pre_notes.append("oraș:" + _cwhy)
    base = dict(ad); base["address1"] = _a1c; base["city"] = _cityc; base["address2"] = _a2L   # adresa de scris = forma CURĂȚATĂ (+ landmark în a2)
    # JUDEȚ din ORAȘ (owner: „orice câmp poate fi în orice câmp"): câmpul `province` e des defaultat GREȘIT
    # (Slatina/Constanța/Voluntari/Câmpina puse ca „București"). Dacă orașul e NE-ambiguu în nomenclator și
    # județul diferă → OVERRIDE cu județul orașului. Face lookup-ul să scopeze corect (fixează bug-ul „Slatina →
    # a matchuit o stradă în București"). Doar UNIC (oraș omonim în mai multe județe = lăsăm province-ul dat).
    _jd = ro_judet_from_city(mcur, _cityc)
    if _jd:
        try:
            _diff = _fold(_jd) != _fold(base.get("province") or "")
        except Exception:
            _diff = (_jd.lower() != (base.get("province") or "").lower())
        if _diff:
            base["province"] = _jd
            pre_notes.append("județ-din-oraș")
    # ZIP din ORICE câmp (owner „orice câmp poate fi în orice câmp"): dacă câmpul zip e gol/gunoi dar un cod de 6
    # cifre stă în a1/a2/oraș/județ → îl folosim (zip-ul dă localitate+județ autoritativ în nomenclator).
    if not re.fullmatch(r"\d{6}", (base.get("zip") or "").strip()):
        _zf2 = _zip_from_fields(_a1c, _a2L, _cityc, base.get("province"),
                                ad.get("address1"), ad.get("address2"), ad.get("city"))
        if _zf2 and _zip_matches_city(mcur, _zf2, _cityc):   # GARDĂ: doar dacă zip-ul NU contrazice orașul
            base["zip"] = _zf2
            pre_notes.append("zip-din-câmpuri")
    pre_changed = bool(pre_notes)
    try:
        r = N.validate_and_correct(mcur, base.get("province"), _cityc, base.get("zip"), _a1c, base.get("address2"), _loc_hint)
    except Exception as e:
        return "error:nomen", None, str(e)[:120]
    if r.get("status") not in ("valid", "corrected"):
        # reper-nominativ ('Biserica nr 10') → stradă-genitiv ('Bisericii nr 10'). Doar whitelist non-instituții.
        _gen = _landmark_genitive(_a1c)
        if _gen:
            try:
                rg = N.validate_and_correct(mcur, base.get("province"), _cityc, base.get("zip"), _gen, base.get("address2"))
            except Exception:
                rg = None
            if rg and rg.get("status") in ("valid", "corrected"):
                r = rg; _a1c = _gen; base["address1"] = _gen
                pre_notes.append("reper→genitiv"); pre_changed = True
    stt = r.get("status")
    # INSTITUȚIE pură fără stradă ('Spitalul Nicolae Malaxa', 'Scoala nr 5') → HERE Discover cu 5 gărzi. AUTO doar
    # dacă trece TOATE gărzile (tip+oraș+număr+place+stradă); altfel → CS (HERE flaky, nu ghicim). Loghez fiecare.
    if stt not in ("valid", "corrected") and _is_institution(_a1c):
        _poi = here_poi_resolve(_a1c, _cityc, here_key())
        if _poi and _poi.get("address1"):
            applied = dict(base); applied["country"] = "Romania"; applied["address1"] = _poi["address1"]
            applied["address2"] = "; ".join(x for x in (base.get("address2"), _poi["address2"]) if x)   # numele instituției în a2
            if _poi.get("zip"):
                applied["zip"] = _poi["zip"]
            detail = "instituție→HERE-POI: %s [%s]" % (applied["address1"], _poi.get("poi"))
            if not apply:
                return "would-correct", applied, detail
            ok = _nomen_write(xc, oid, d, ad, applied, shop_domain)
            awb_event(kind="inst-poi", store=shop_domain, order=d.get("orderName"),
                      result="ok" if ok else "fail", detail=(_poi.get("poi") or "")[:80])
            return ("corrected" if ok else "error:write"), applied, detail
    if stt == "cs":
        # genuin incomplet (fără nr casă) — DAR încearcă să completeze NUMĂRUL din istoricul clientului
        # (comandă livrată anterioară pe aceeași stradă). Doar dacă istoricul dă un număr UNIC.
        _h = customer_history_addr(d.get("orderName"), dict(ad, address1=_a1c, city=_cityc))
        if _h and _h.get("address1"):
            applied = dict(base); applied.update(_h); applied["country"] = "Romania"
            detail = "istoric client (nr casă din comandă livrată): %s" % applied.get("address1")
            if not apply:
                return "would-correct", applied, detail
            return ("corrected" if _nomen_write(xc, oid, d, ad, applied, shop_domain) else "error:write"), applied, detail
        return "cs", None, r.get("note", "la CS")
    # Adresa finală: corecția nomenclatorului (peste forma curățată) > forma curățată confirmată > swap unic.
    if stt == "corrected" and r.get("address"):
        corr = r["address"]; applied = dict(base); applied["country"] = "Romania"
        for k_ad, k_corr in (("province", "province"), ("city", "city"), ("zip", "zip"), ("address1", "address1")):
            if corr.get(k_corr):
                applied[k_ad] = corr[k_corr]
        note = r.get("note", "")
    elif stt == "valid" and pre_changed:
        # nomenclatorul CONFIRMĂ adresa curățată ca validă → scriu forma curățată (canon oraș/deglue/swap)
        applied = dict(base); applied["country"] = "Romania"; note = "pre-clean[%s]" % "+".join(pre_notes)
    elif stt == "valid":
        # nomenclatorul RO zice VALID (stradă păstrată), DAR curierul (xConnector) poate zice WRONG fiindcă strada
        # nu-i în baza LUI. Owner „dacă mai igienizăm": dacă curierul sugerează o stradă apropiată (scor≥0.80 +
        # string-similară) → o folosim ca eticheta DPD să treacă (ex. „Bulevardul.Tineretului"→„Tineretului").
        if o.get("addressStatus") in ("WRONG", "UNKNOWN"):
            _snap = courier_street_snap(d, _a1c)
            if _snap:
                try:
                    _same = _fold(_snap) == _fold(re.sub(r"(?i)\b(str|strada|bd|bulevardul|calea|soseaua|aleea)\b\.?", "", _a1c or "").strip())
                except Exception:
                    _same = False
                if not _same:
                    _num = ""
                    _mn = re.search(r"(?i)\bnr\.?\s*(\d+[A-Za-z]?)\b", _a1c or "") or re.search(r"\b(\d+[A-Za-z]?)\b", _a1c or "")
                    if _mn:
                        _num = " nr " + _mn.group(1)
                    applied = dict(base); applied["country"] = "Romania"; applied["address1"] = _snap + _num
                    note = "snap stradă curier: %s" % applied["address1"]
                    detail = "%s, %s %s (%s) [%s]" % (applied.get("address1"), applied.get("city"),
                                                      applied.get("zip"), applied.get("province"), note)
                    if not apply:
                        return "would-correct", applied, detail
                    ok = _nomen_write(xc, oid, d, ad, applied, shop_domain)
                    awb_event(kind="courier-snap", store=shop_domain, order=d.get("orderName"),
                              result="ok" if ok else "fail", detail=_snap[:60])
                    return ("corrected" if ok else "error:write"), applied, detail
        return "valid", None, "bună pe nomenclator (%s)" % r.get("note", "")
    elif pre_changed and (_swapped or _cwhy):
        # nomenclatorul NU confirmă complet (ex. ZIP nederivabil), DAR câmpurile erau inversate SAU orașul a fost
        # canonizat la o localitate RECUNOSCUTĂ ('BUCHARSTI'→București, 'Dr tr severin'→Drobeta-Turnu Severin).
        # Scriu forma curățată — e strict mai bună; xConnector re-validează, iar dacă tot lipsește ZIP-ul, HERE-ul
        # din bucla următoare completează pe orașul CURAT (self-heal). (Deglue-ul SINGUR neconfirmat NU se scrie:
        # dacă nomenclatorul, care știe toate străzile RO, tot nu confirmă, strada e treabă de HERE/CS.)
        applied = dict(base); applied["country"] = "Romania"
        note = "pre-clean[%s] (nomen incert — oraș recunoscut)" % "+".join(pre_notes)
    else:
        # SNAP LA CURIER (owner „dacă mai igienizăm"): strada clientului nu-i în nomenclatorul curierului, DAR
        # validatorul lui sugerează o stradă apropiată cu scor mare + string-similară (ex. „Bulevardul.Tineretului"→
        # „Tineretului", „Leghes"→„Legheșului", „Mureșului"→„Mureș"). O folosim → eticheta DPD trece. Gardă anti-
        # stradă-greșită-cu-scor-mare în courier_street_snap. Păstrez numărul de casă (extras din strada curentă).
        _snap = courier_street_snap(d, _a1c)
        if _snap:
            _num = ""
            _mn = re.search(r"(?i)\bnr\.?\s*(\d+[A-Za-z]?)\b", _a1c or "") or re.search(r"\b(\d+[A-Za-z]?)\b", _a1c or "")
            if _mn:
                _num = " nr " + _mn.group(1)
            applied = dict(base); applied["country"] = "Romania"; applied["address1"] = _snap + _num
            note = "snap stradă curier: %s" % applied["address1"]
            detail = "%s, %s %s (%s) [%s]" % (applied.get("address1"), applied.get("city"),
                                              applied.get("zip"), applied.get("province"), note)
            if not apply:
                return "would-correct", applied, detail
            ok = _nomen_write(xc, oid, d, ad, applied, shop_domain)
            awb_event(kind="courier-snap", store=shop_domain, order=d.get("orderName"),
                      result="ok" if ok else "fail", detail=_snap[:60])
            return ("corrected" if ok else "error:write"), applied, detail
        # NOMENCLATORUL nu confirmă (strict), DAR strada curățată poate fi REALĂ (segment zip / rural / lipsă din
        # nomenclator). HERE (geocoder tolerant) o prinde — măsurat: ~11/15 din reziduul held au stradă curată pe
        # care HERE o validează. Fără asta, held-sweep-ul le lasă blocate degeaba (el nu cheamă HERE, doar nomenclator).
        _hk = here_key()
        _adh = {"address1": _a1c, "city": _cityc, "zip": base.get("zip")} if _hk else None
        _zf = None
        if _hk:
            try:
                _zf = here_zip_fill(_adh, _hk)          # completează ZIP-ul lipsă din HERE (stradă+oraș confirmate)
            except Exception:
                _zf = None
        if _zf:
            applied = dict(base); applied.update(_zf); applied["country"] = "Romania"
            note = "HERE zip-fill %s" % _zf.get("zip", "")
        elif _hk and pre_changed and here_street_ok(_adh, _hk):
            applied = dict(base); applied["country"] = "Romania"; note = "HERE valid (stradă+oraș confirmate)"
        else:
            # FALLBACK owner: dacă validarea + HERE pică, împrumută strada/numărul dintr-o COMANDĂ ANTERIOARĂ
            # LIVRATĂ a clientului (același telefon+oraș, adresa care a funcționat deja). Doar dacă istoricul dă UNIC.
            _h2 = customer_history_addr(d.get("orderName"), dict(ad, address1=_a1c, city=_cityc))
            if _h2 and _h2.get("address1"):
                applied = dict(base); applied.update(_h2); applied["country"] = "Romania"
                note = "istoric (comandă anterioară livrată)"
            else:
                # ZIP LIPSĂ + oraș valid, iar strada e REALĂ dar neindexată în nomenclator/HERE → cod GENERAL al
                # orașului (MIN cod_postal). DPD acceptă cu codul general + textul străzii (ex. Buzău→120001). Owner.
                # GARDĂ (owner): codul general e DOAR pt 'străzi care nu-s indexate altfel' → adresa trebuie să AIBĂ
                # stradă+număr undeva în câmpuri (a1/a2/oraș/județ — 'adresa poate fi oriunde'). La orașe MARI e
                # OBLIGATORIU ('să fie și stradă și număr'); la sate mici, oraș+număr ajunge. Fără → CS.
                _jud = base.get("province") or ad.get("province")
                _gz = ro_city_general_zip(mcur, _cityc, _jud) if not (base.get("zip") or "").strip() else None
                _fields = [_a1c, base.get("address2"), _cityc, base.get("province"), _loc_hint,
                           ad.get("address1"), ad.get("address2"), ad.get("city"), ad.get("province")]
                _has_sn = _addr_has_street_and_number(_fields, _cityc)   # stradă(marker/nume) + număr, în ORICE câmp
                _big = ro_city_is_big(mcur, _cityc, _jud)                # municipiu/reședință = multe artere
                # Oraș MARE → OBLIGATORIU stradă+număr (owner). RURAL/sat mic → merge și FĂRĂ număr (owner: numele
                # localității ajunge curierului); dacă n-are stradă → 'Strada Principală' (owner). NU suprascrie zip.
                if _gz and (_has_sn or not _big):
                    applied = dict(base); applied["zip"] = _gz; applied["city"] = _cityc; applied["country"] = "Romania"
                    if _big:
                        note = "cod general oraș %s (oraș mare: stradă+nr)" % _gz
                    else:
                        _rs = _rural_street(_a1c, _fields)
                        if _rs and _rs != (_a1c or "").strip():
                            applied["address1"] = _rs
                            note = "cod general %s (rural → '%s')" % (_gz, _rs)
                        else:
                            note = "cod general %s (rural)" % _gz
                elif _gz:
                    return "needs-geocoder", None, "oraș mare fără stradă+număr real (adresă oriunde în câmpuri) → CS"
                else:
                    return "needs-geocoder", None, r.get("note", "necorectabil")
    detail = "%s, %s %s (%s) [%s]" % (applied.get("address1"), applied.get("city"),
                                      applied.get("zip"), applied.get("province"), note)
    if not apply:
        return "would-correct", applied, detail
    ok = _nomen_write(xc, oid, d, ad, applied, shop_domain)
    return ("corrected" if ok else "error:write"), applied, detail


# ── Nomenclatoare NAȚIONALE intl (CZ pe metrics.public.cz_addresses; PL/BG de adăugat) ──
def intl_nomen(country, cur, ad):
    """Validează/corectează o adresă intl pe nomenclatorul național (CZ = RÚIAN localitate-driven; PL = PRG ZIP-driven).
    Întoarce {status: valid|corrected|cs|needs_geocoder, address, note} sau None dacă țara n-are nomenclator încă."""
    if cur is None or not ad:
        return None
    if country == "CZE":
        try:
            import cz_nomenclator as CZ
            return CZ.cz_validate_and_correct(cur, ad.get("city"), ad.get("zip"), ad.get("address1"), ad.get("address2"))
        except Exception:
            return None
    if country == "POL":
        try:
            import pl_nomenclator as PL
            return PL.pl_validate_and_correct(cur, ad.get("city"), ad.get("zip"), ad.get("address1"), ad.get("address2"))
        except Exception:
            return None
    if country == "BGR":
        try:
            import bg_nomenclator as BG
            return BG.bg_validate_and_correct(cur, ad.get("city"), ad.get("zip"), ad.get("address1"), ad.get("address2"))
        except Exception:
            return None
    if country in ("HUN", "SVK"):
        try:
            import geonames_nomenclator as GN
            return GN.gn_validate_and_correct(cur, country, ad.get("city"), ad.get("zip"), ad.get("address1"), ad.get("address2"))
        except Exception:
            return None
    return None


def intl_correct_write(xc, o, shop_domain, corr):
    """Scrie corecția intl (city/zip/address1/address2) în comandă via ai-correct-address. True dacă 200."""
    try:
        oid = o["orderId"]; d = xc.by_id(oid); ad = d.get("shippingAddress") or {}
        applied = dict(ad)
        for k in ("city", "zip", "address1", "address2", "firstName", "lastName", "phone"):
            if corr.get(k) is not None:
                applied[k] = corr[k]
        body = {"orderId": oid,
                "idempotencyKey": "intlnom-%s-%s-%s" % (_digest(shop_domain, 8), oid, _digest(applied, 12)),
                "appliedShippingAddress": applied, "expectedAddressHash": d.get("addressHash"),
                "expectedStatusHash": d.get("statusHash"), "expectedEvidenceHash": d.get("evidenceHash"),
                "agentClaimedConfidence": 0.95,
                "agentRationale": "National address nomenclature (RÚIAN/PRG) reconciliation: postal code + locality confirmed.",
                "modelName": "gigi-intl-nomen", "mcpClientId": "gigi-xconnector"}
        s, b = http("POST", XBASE + "/api/orders/ai-correct-address", xc.h, body)
        if s == 200:
            shopify_push_corrected(shop_domain, (d or {}).get("orderName") or (o or {}).get("orderName"), applied)
        return s == 200
    except Exception:
        return False


# ── Sanitizer câmpuri INTL pentru DPD: adresa e VALIDĂ (nomenclatorul o confirmă), dar DPD o respinge pe FORMAT ──
# Limitele REALE (din erorile DPD): addressLine1/2 max 35, city/siteName max 35, cod poștal strict pe țară.
# Tiparul măsurat: clienții lipesc gunoi în ZIP („79312katkacervenkova", „42-700Lubliniec", „43004už") și
# pastează adrese de 100+ caractere → ~85% din eșecurile CZ și ~100% din cele PL sunt DOAR format, nu adrese rele.
DPD_MAX_ADDR = 35
DPD_MAX_CITY = 35
_ZIP_PAT = {"CZE": r"(\d{3})\s*(\d{2})", "POL": r"(\d{2})\s*-?\s*(\d{3})", "BGR": r"(\d{4})", "HUN": r"(\d{4})", "SVK": r"(\d{3})\s*(\d{2})"}


def _zip_candidates(country, z):
    """TOATE codurile poștale plauzibile din ce a tastat clientul (nu doar primul) — „50002 503 11" dă
    ['50002','50311']. Nu ghicim care e bun: fiecare se confirmă în nomenclator înainte de a fi scris."""
    pat = _ZIP_PAT.get(country or "")
    if not pat or not z:
        return []
    out = []
    for m in re.finditer(pat, str(z)):
        if country == "CZE":
            c = m.group(1) + m.group(2)             # „710 42" / „43004už" → „71042"
        elif country == "POL":
            c = m.group(1) + "-" + m.group(2)       # „42-700Lubliniec" → „42-700"
        else:
            c = m.group(1)
        if c not in out:
            out.append(c)
    return out


def _split_addr(a1, a2):
    """addressLine1 > 35 → mută restul în addressLine2, tăiat pe graniță de CUVÂNT.
    Întoarce (a1, a2, lost) — `lost` = a rămas text pe dinafară (nu încape în 2×35) ⇒ NU rescriem
    (am pierde numărul casei ⇒ colet nelivrabil). Ăla e caz de CS, nu de auto-corecție."""
    a1 = (a1 or "").strip(); a2 = (a2 or "").strip()
    if len(a1) <= DPD_MAX_ADDR:
        return a1, a2[:DPD_MAX_ADDR], len(a2) > DPD_MAX_ADDR
    cut = a1.rfind(" ", 0, DPD_MAX_ADDR + 1)
    if cut <= 0:
        cut = DPD_MAX_ADDR
    head = a1[:cut].strip()
    tail = a1[cut:].strip()
    merged = (tail + (" " + a2 if a2 else "")).strip()
    return head, merged[:DPD_MAX_ADDR], len(merged) > DPD_MAX_ADDR


_PHONE_CC = {"CZE": ("+420", 9), "POL": ("+48", 9), "BGR": ("+359", 9), "HUN": ("+36", 9), "SVK": ("+421", 9)}


def _phone_norm(country, ph):
    """Curăță telefonul + adaugă prefixul de țară dacă e număr național gol-goluț (DPD cere format valid).
    NU inventează cifre: dacă nu iese lungimea națională, întoarce None (lăsăm cum e)."""
    cc = _PHONE_CC.get(country or "")
    if not cc or not ph:
        return None
    raw = str(ph).strip()
    keep = "+" if raw.startswith("+") else ""
    digits = re.sub(r"\D", "", raw)
    if keep == "+" or digits.startswith(cc[0].lstrip("+")):
        return None                                   # are deja prefix → nu-l atingem
    if len(digits) == cc[1]:
        return cc[0] + digits                         # „774822669" (CZ) → „+420774822669"
    if digits.startswith("0") and len(digits) == cc[1] + 1:
        return cc[0] + digits[1:]                     # „0774822669" → „+420774822669"
    return None


def ro_phone_norm(ph):
    """Telefon RO in format national DPD 07xxxxxxxx. NU inventeaza cifre (altfel colet la nr gresit)."""
    if not ph:
        return None
    d = re.sub(r"\D", "", str(ph))
    if d.startswith("0040"):
        d = d[2:]
    if d.startswith("40"):
        d = d[2:]
    if len(d) == 9 and d.startswith("7"):
        d = "0" + d
    return d if (len(d) == 10 and d.startswith("07")) else None


def ro_phone_fix(xc, o, shop_domain):
    """Normalizeaza telefonul RO la 07xxxxxxxx via ai-correct-address. True daca a schimbat ceva."""
    try:
        ad = (xc.by_id(o.get("orderId")) or {}).get("shippingAddress") or {}
    except Exception:
        return False
    cur = (ad.get("phone") or "").strip()
    nph = ro_phone_norm(cur)
    if nph and nph != cur:
        return intl_correct_write(xc, o, shop_domain, {"phone": nph})
    return False


def ro_genzip_fallback(xc, o, shop_domain):
    """Owner: „dacă nu merge [zip-ul specific] să faci AWB la DPD, pune codul general". Când AWB-ul RO pică și
    adresa are un zip SPECIFIC pe care DPD îl respinge → pune codul GENERAL al orașului (MIN cod_postal pe
    localitate) → AWB-ul trece pe cod general + textul străzii. True dacă a schimbat zip-ul (altfel n-are rost retry)."""
    try:
        ad = (xc.by_id(o.get("orderId")) or {}).get("shippingAddress") or {}
    except Exception:
        return False
    city = (ad.get("city") or "").strip()
    cur_zip = (ad.get("zip") or "").strip()
    if not city:
        return False
    mcur = metrics_cursor()
    if not mcur:
        return False
    gz = ro_city_general_zip(mcur, city, ad.get("province"))
    if gz and gz != cur_zip:
        return intl_correct_write(xc, o, shop_domain, {"zip": gz})
    return False


def intl_genzip_fallback(xc, o, shop_domain, country):
    """REACTIV — DPD a respins LOCALITATEA (valid-locality-id) pt o comandă INTL cu ORAȘ bun dar ZIP greșit
    (ex BG Шумен + 9750 = de fapt satul Мадара; DPD BG rezolvă PE COD → respinge codul care nu-i al localității).
    Pune zip-ul CANONIC al localității din nomenclatorul național → DPD îl rezolvă pe cod. SIGUR fiindcă e REACTIV:
    se declanșează DOAR pe respingerea DPD, deci NU atinge zip-uri VALIDE (ex BG 8127/Ветрен pe care DPD îl acceptă
    — comanda aia nici nu ajunge aici). Complementar cu `dpd_fix_locality` (care face invers: zip valid → corectează
    orașul). True dacă a schimbat zip-ul (altfel retry n-are rost)."""
    try:
        ad = (xc.by_id(o.get("orderId")) or {}).get("shippingAddress") or {}
    except Exception:
        return False
    city = (ad.get("city") or "").strip(); cur_zip = (ad.get("zip") or "").strip()
    if not city:
        return False
    mcur = metrics_cursor()
    if not mcur:
        return False
    canon = None
    try:
        if country == "BG":
            import bg_nomenclator as BG
            loc = BG.find_locality(mcur, BG.city_candidates(city))
            if loc and loc[1]:
                canon = BG.pc4(loc[1])
        elif country == "CZ":
            import cz_nomenclator as CZ
            from collections import Counter
            rows = CZ.load_by_locality(mcur, CZ._cz_city_denoise(city))
            pscs = Counter(r["psc"] for r in rows if r.get("psc"))
            if pscs:
                canon = pscs.most_common(1)[0][0]
    except Exception:
        return False
    if canon and canon != cur_zip:
        return intl_correct_write(xc, o, shop_domain, {"zip": canon})
    return False


_PRIMARY_DOMAIN = {}


def shopify_primary_domain(shop, token):
    """Domeniul PUBLIC al magazinului (ex. `bonhaus.cz`), cache pe proces. Fallback = domeniul myshopify."""
    if shop in _PRIMARY_DOMAIN:
        return _PRIMARY_DOMAIN[shop]
    host = shop
    try:
        d = shopify_gql(shop, token, "{ shop { primaryDomain { host } } }")
        h = (((d.get("data") or {}).get("shop") or {}).get("primaryDomain") or {}).get("host")
        if h:
            host = h
    except Exception:
        pass
    _PRIMARY_DOMAIN[shop] = host
    return host


def shopify_set_order_email(shop, token, name, email):
    """Setează email pe comanda Shopify — DPD îl cere obligatoriu pe intl, iar xConnector nu-l are în adresă.
    Scrie DOAR dacă e gol (nu atingem NICIODATĂ un email real de client). True dacă a scris."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ id email } } } }') % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return False
    node = edges[0]["node"]
    if (node.get("email") or "").strip():
        return False                                  # are email real → NU-l suprascriem
    m = ('mutation{ orderUpdate(input:{id:"%s", email:"%s"}){ userErrors{ field message } } }'
         % (node["id"], email))
    r = shopify_gql(shop, token, m)
    errs = (((r.get("data") or {}).get("orderUpdate") or {}).get("userErrors")) or []
    return not errs


def dpd_intl_sanitize(xc, o, shop_domain, name, country, st=None):
    """Repară FORMATUL câmpurilor pentru DPD pe o comandă intl: zip curățat de gunoi (+ city canonic din
    nomenclatorul național), addressLine1>35 împărțit în a1/a2 pe cuvânt, city scurtat la 35.
    Scrie o singură dată via ai-correct-address. Întoarce True dacă a schimbat ceva."""
    try:
        ad = (xc.by_id(o.get("orderId")) or {}).get("shippingAddress") or {}
    except Exception:
        return False
    if not ad:
        return False
    corr = {}
    z0 = (ad.get("zip") or "").strip()
    city = (ad.get("city") or "").strip()
    # 1) ZIP — scriem DOAR un cod pe care nomenclatorul național îl CONFIRMĂ (zero ghicit: un zip greșit
    #    = colet la adresă greșită). Prima variantă confirmată câștigă; dacă niciuna nu trece, lăsăm zip-ul.
    cands = _zip_candidates(country, z0)
    if cands and not (len(cands) == 1 and cands[0] == z0):
        cur = metrics_cursor_live()
        for cand in cands:
            try:
                probe = dict(ad); probe["zip"] = cand
                nres = intl_nomen(country, cur, probe)
            except Exception:
                continue
            if (nres or {}).get("status") in ("valid", "corrected"):
                if cand != z0:
                    corr["zip"] = cand
                cnl = ((nres or {}).get("address") or {}).get("city")
                if cnl and _fold(cnl) != _fold(city):
                    corr["city"] = cnl          # numele canonic e și mai scurt, și corect
                break
    # 2) CITY — dacă tot e peste limita DPD, scurtează
    ccity = corr.get("city", city)
    if len(ccity) > DPD_MAX_CITY:
        corr["city"] = ccity[:DPD_MAX_CITY].strip()
    # 3) ADRESĂ — >35 → împarte pe cuvânt în a1/a2, DAR doar dacă nu se pierde text
    na1, na2, lost = _split_addr(ad.get("address1"), ad.get("address2"))
    if not lost:
        if na1 != (ad.get("address1") or "").strip():
            corr["address1"] = na1
        if na2 != (ad.get("address2") or "").strip():
            corr["address2"] = na2
    # 4) NUME — DPD cere ≥2 cuvinte, iar adresa are firstName/lastName. Dacă unul lipsește → DUBLEZ numele
    #    existent (nu inventez un nume străin de client).
    fn = (ad.get("firstName") or "").strip()
    ln = (ad.get("lastName") or "").strip()
    if len((fn + " " + ln).split()) < 2:
        if fn and not ln:
            corr["lastName"] = fn
        elif ln and not fn:
            corr["firstName"] = ln
    # 5) TELEFON — adaugă prefixul de țară dacă e număr național fără el
    nph = _phone_norm(country, ad.get("phone"))
    if nph:
        corr["phone"] = nph
    ok_addr = intl_correct_write(xc, o, shop_domain, corr) if corr else False
    # 6) EMAIL — DPD îl cere obligatoriu, dar NU e în adresa xConnector → îl punem pe comanda Shopify,
    #    unic per comandă (derivat din numărul comenzii), pe DOMENIUL magazinului (ex. cz80526@bonhaus.cz).
    ok_mail = False
    if st and st.get("adminToken"):
        try:
            real = _awbprint_customer_email(name)   # emailul REAL din AWBprint (ne-redactat)
            if real and "@bonhaus." not in real.lower():
                ok_mail = shopify_set_order_email(st["shopDomain"], st["adminToken"], name, real)
            # FARA email real in AWBprint -> NU sintetizam (nu suprascriem emailul real al clientului)
        except Exception:
            ok_mail = False
    return bool(ok_addr or ok_mail)


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
    "ROSSI": "1d2bce-2", "LAB": "31k0py-bi", "ORC": "oriceredus", "HU": "63e901-2f", "SK": "16w7xv-0w",
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
GRANDIA_HOLD_TYPES = {"magazii de grădină"}   # EXCLUS de la AWB (plată parțială) → HOLD, le duce CS manual


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


def order_connector_id(shop, token, name):
    """ConnectorId ales de STOREFRONT pe comandă (note attribute), pt rutare locker (ex Easybox→Sameday).
    Widget-ul xConnector scrie pe comandă ConnectorId/ConnectorType/LocationId când clientul alege un locker.
    Întoarce str(ConnectorId) sau None. (xConnector reține lockerul LocationId server-side → nu-l trecem noi în body.)"""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ customAttributes{ key value } } } } }') % (name or "").replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    if not edges:
        return None
    for a in (edges[0]["node"].get("customAttributes") or []):
        if a.get("key") == "ConnectorId" and (a.get("value") or "").strip():
            return a["value"].strip()
    return None


def _store_has_alt_courier(cons):
    """Magazinul are un curier ALTERNATIV activ (altul decât DPD/Dragon Star)? — ex ROSSI cu Sameday pt Easybox.
    Doar atunci merită un lookup de note-attributes per comandă; altfel (doar DPD) rutăm direct default, fără cost."""
    for c in cons:
        if not c.get("active"):
            continue
        t = (c.get("type") or "").upper()
        if t in BILLING_TYPES or t in ("HERE_GEOCODING", "POSTIS", "DPD", "DRAGON_STAR"):
            continue
        return True
    return False


def route_connector(sh, st, order_name, cons, default_con):
    """Rutare per-comandă:
      • Grandia cu produs voluminos → Dragon Star;
      • ORICE magazin cu curier alternativ activ (ex ROSSI/Easybox): dacă storefront-ul a ales deja un connector
        pe comandă (ConnectorId în note attributes), RESPECTĂ-l (nu forța DPD peste un locker) — „ține cont de ce e pus";
      • altfel default (DPD)."""
    if not st:
        return default_con
    if sh and sh.get("shopDomain") == GRANDIA_DOMAIN:
        if order_product_types(st["shopDomain"], st["adminToken"], order_name) & GRANDIA_BULKY_TYPES:
            ds = [c for c in cons if (c.get("type") or "").upper() == "DRAGON_STAR" and c.get("active")]
            if ds:
                return ds[0]
        return default_con
    # locker / curier ales de client pe storefront (Easybox→Sameday etc.) → folosește connectorul comenzii dacă e ACTIV.
    if _store_has_alt_courier(cons):
        cid = order_connector_id(st["shopDomain"], st["adminToken"], order_name)
        if cid:
            m = [c for c in cons if str(c.get("id")) == str(cid) and c.get("active")]
            if m:
                return m[0]
            # connectorul ales nu mai e activ (ex Econt scos) → NU face AWB home-delivery greșit pe un locker:
            # lasă create-label pe DPD să pice → prins de giveup (awb-esec-repetat, după 3 ture) → CS îl re-rutează.
    return default_con


# ── Validare adrese INTERNAȚIONALE prin HERE Geocoding (validatorul RO al xConnector dă fals WRONG/UNKNOWN) ──
# KPI = AWB făcut. Externe au adrese bune; HERE le validează → AWB cu curierul local (home delivery, ~100% din ele).
HERE_COUNTRY = {"vthuzq-7j.myshopify.com": "CZE", "f0yrmh-ia.myshopify.com": "POL", "ux1x6n-n2.myshopify.com": "BGR",
                "63e901-2f.myshopify.com": "HUN", "16w7xv-0w.myshopify.com": "SVK"}
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


# INSTITUȚIE pură (nume de instituție, FĂRĂ marker de stradă) — 'Spitalul Nicolae Malaxa', 'Scoala nr 5',
# 'Cafenea Nobless'. Dacă a1 ARE marker de stradă → NU e instituție pură (Caz B: nota se mută în a2 de _pull_landmark).
_INST_RE = re.compile(r"(?i)\b(spital|clinic|policlin|maternit|dispensar|scoal|liceu|colegiu|gimnazi|gradinit|"
                      r"universit|facultat|camin|cămin|federati|primari|comisariat|penitenciar|cofetar|cafenea|restaurant|"
                      r"pizzeri|hotel|pensiun|muzeu|teatru|biblioteca|cabinet|farmaci)")   # prefix-match (fără \b final: 'scoal' prinde 'scoala')
_ST_TYPE_RE2 = re.compile(r"(?i)\b(strada|str|bulevard|b-?dul|bdul|calea|aleea|alee|soseaua|sos|splaiul|intrarea|drumul|prelungirea|fundatura)\b")
def _is_institution(a1):
    if not a1 or _ST_TYPE_RE2.search(a1):
        return False
    return bool(_INST_RE.search(_fold(a1)))


def here_poi_resolve(a1, city, key):
    """Rezolvă o INSTITUȚIE (nume, fără stradă) la adresa reală prin HERE Discover, constrâns geografic la oraș
    (rază 18km). CINCI GĂRZI: resultType=place + are stradă + TIPUL în titlu + ORAȘ-match + NUMĂR-match (dacă a1
    are un număr de instituție). Întoarce {'address1':strada+nr, 'address2':<numele instituției>, 'zip', 'poi'}
    sau None (→ CS). HERE e FLAKY pe instituții prost-indexate → gărzile resping tot ce nu-i sigur ('Scoala 5
    Cluj' → Catena/altă școală → respins). Nu ghicește: dacă nu trece TOATE gărzile, e treaba CS-ului."""
    if not a1 or not city or not key:
        return None
    try:
        gu = ("https://geocode.search.hereapi.com/v1/geocode?q=%s&in=countryCode:ROU&apiKey=%s"
              % (urllib.parse.quote(city + ", Romania"), urllib.parse.quote(key)))
        _gs, gb = http("GET", gu, {})
        gi = (json.loads(gb).get("items") or [{}])[0]; pos = gi.get("position") or {}
        lat, lng = pos.get("lat"), pos.get("lng")
        if lat is None or lng is None:
            return None
        du = ("https://discover.search.hereapi.com/v1/discover?q=%s&in=circle:%s,%s;r=18000&limit=1&apiKey=%s"
              % (urllib.parse.quote(a1), lat, lng, urllib.parse.quote(key)))
        _ds, db = http("GET", du, {})
        it = (json.loads(db).get("items") or [{}])[0]
    except Exception:
        return None
    a = it.get("address") or {}
    title = it.get("title") or ""; street = a.get("street"); rcity = a.get("city") or ""
    if it.get("resultType") != "place" or not street:            # (1) place + (2) stradă
        return None
    fa1, ft = _fold(a1), _fold(title)
    m = _INST_RE.search(fa1)
    if m and m.group(1)[:5] not in ft:                            # (3) TIP: substantivul-instituție în titlu
        return None
    fc, frc = _fold(city), _fold(rcity)
    if not (fc == frc or fc in frc or frc in fc):                 # (4) ORAȘ
        return None
    n1 = re.search(r"\d+", a1)
    if n1:                                                        # (5a) NUMĂR: instituție numerotată → titlul are același număr
        nt = re.search(r"\d+", title)
        if not nt or nt.group() != n1.group():
            return None
    else:                                                         # (5b) fără număr → cere OVERLAP DE NUME (token distinctiv în titlu)
        typ5 = m.group(1)[:5] if m else ""                        #      altfel „Spital neurochirurgie" prinde „Spital Recuperare" (același oraș, alt spital)
        stop = {"clinic", "spital", "spitalul", "scoala", "gradinita", "liceul", "urgenta", "strada", "romania",
                "municipal", "orasenesc"} | set(re.findall(r"[a-zăâîșț]{4,}", fc))
        dist = [t for t in re.findall(r"[a-zăâîșț]{4,}", fa1) if not (typ5 and t.startswith(typ5)) and t not in stop]
        if not dist or not any(t in ft for t in dist):
            return None
    hn = a.get("houseNumber")
    return {"address1": street + ((" " + str(hn)) if hn else ""), "address2": a1.strip(),
            "zip": a.get("postalCode"), "poi": title}


_ST_TYPE_RE = re.compile(r"(?i)^\s*(strada|str|stra|bulevardul|bdul|b-dul|bd|blvd|calea|cal|[sș]oseaua|sos|aleea|alee|"
                         r"intrarea|intr|drumul|splaiul|prelungirea|fundatura|pia[țt]a)\.?\s+")
def _strip_street_type(s):
    """Scoate tipul de arteră din față ('Str/Bd/Aleea/Calea X'→'X') — pt fallback-ul HERE pe NUME când clientul
    a scris tipul GREȘIT (client 'Str Tineretului' dar e 'Aleea Tineretului'; 'STR Brătianu' dar e Bulevard)."""
    return _ST_TYPE_RE.sub("", s or "").strip()

def _here_geocode_q(q, country, key):
    if not q:
        return None
    url = ("https://geocode.search.hereapi.com/v1/geocode?q=%s&in=countryCode:%s&apiKey=%s"
           % (urllib.parse.quote(q), country, urllib.parse.quote(key)))
    s, b = http("GET", url, {})
    try:
        items = json.loads(b).get("items") or []
    except Exception:
        return None
    if not items:
        return None
    it = items[0]; a = it.get("address") or {}
    return {"score": float((it.get("scoring") or {}).get("queryScore", 0)), "rt": it.get("resultType"),
            "zip": a.get("postalCode"), "street": a.get("street") or "", "city": a.get("city") or ""}

def here_geocode(addr, country, key):
    """Geocodare HERE completa (nu doar scorul): {score, rt, zip, street, city}. None la eroare."""
    if not key or not country:
        return None
    _z = str(addr.get("zip") or "").strip()
    _z = _z if re.fullmatch(r"\d{6}", _z) else ""   # zip gunoi (ex "-") strica scorul HERE -> il scot din query
    core = _street_core(addr.get("address1"))
    g = _here_geocode_q(", ".join([x for x in [core, addr.get("address2"), addr.get("city"), _z] if x]), country, key)
    # FALLBACK (owner: „dacă n-o găsești, scoți prefixul de stradă"): scor mic = poate tip-ul de arteră e GREȘIT
    # (client 'Str' dar e Bulevard/Aleea) → caută pe NUME. Măsurat: 'STR Ion C. Brătianu' 0.86 → fără 'STR' 0.96.
    # Garda street-match (aval, în here_zip_fill) verifică că e ACEEAȘI stradă → un match greșit pe nume e respins.
    if not g or g["score"] < HERE_MIN_SCORE:
        core2 = _strip_street_type(core)
        if core2 and core2 != core:
            g2 = _here_geocode_q(", ".join([x for x in [core2, addr.get("address2"), addr.get("city"), _z] if x]), country, key)
            if g2 and (not g or g2["score"] > g["score"]):
                return g2
    return g


def _here_street_match(client_a1, here_street):
    """Strada HERE = aceeasi cu ce a scris clientul? Un token distinctiv (>=5 litere, non-rang) comun
    (exact sau 1 typo). Fara asta nu completam zip-ul (poate fi alta strada la alt cod postal)."""
    ct = [t for t in re.findall(r"[a-z]+", _fold(client_a1)) if len(t) >= 5 and t not in _ST_RANK]
    ht = [t for t in re.findall(r"[a-z]+", _fold(here_street)) if len(t) >= 5 and t not in _ST_RANK]
    if not ct or not ht:
        return False
    # `_same_street_token` tolerează DECLINAREA RO (Griviței↔Grivița, Viteazu↔Viteazul) — HERE dă forma
    # nominativă a străzii, clientul o scrie articulat/genitiv; fără asta zip-fill-ul le respingea ca „altă stradă".
    return any(c == h or _same_street_token(c, h) or _typo_ok(h, c) or _typo_ok(c, h) for c in ct for h in ht)


def _here_street_iexact(client_a1, here_street):
    """Strada clientului = strada HERE EXACT modulo â/î↔i ('Birsei'≡'Bârsei'). Semnal PUTERNIC (identitate, nu doar
    overlap) → permite accept sub 0.9 (clientul a scris 'i' pt 'â/î', HERE dă forma corectă). Toți tokenii
    distinctivi ai clientului au corespondent EXACT (după `_foldi`) în strada HERE."""
    ct = [t for t in re.findall(r"[a-z]+", _foldi(client_a1)) if len(t) >= 5 and t not in _ST_RANK]
    ht = {t for t in re.findall(r"[a-z]+", _foldi(here_street)) if len(t) >= 5}
    return bool(ct) and all(c in ht for c in ct)


def here_street_ok(ad, key):
    """HERE confirmă STRADA+ORAȘUL (scor≥0.9, casă/stradă, street-match, oraș-match) — chiar dacă n-are ZIP unic
    de completat → acceptăm adresa AS-IS. CRITIC: fără street-match, fallback-ul fără-tip ar accepta o stradă
    GREȘITĂ cu scor mare (ex 'Str Tineretului'→HERE '21 Decembrie' 0.99). Aici o respinge (Tineretului≠21 Decembrie)."""
    g = here_geocode(ad, "ROU", key)
    if not g or g["score"] < HERE_MIN_SCORE or g["rt"] not in ("houseNumber", "street"):
        return False
    if not _here_street_match(ad.get("address1") or "", g["street"]):
        return False
    _cc, _hc = _fold(ad.get("city") or ""), _fold(g["city"] or "")
    if _cc and _hc and not (_cc == _hc or _typo_ok(_cc, _hc) or _typo_ok(_hc, _cc) or _cc in _hc or _hc in _cc):
        return False
    return True


def here_zip_fill(ad, key):
    """Completeaza zip-ul LIPSA din HERE (>=0.9, casa/strada, strada confirmata). Prefera zip-ul
    autoritativ din nomenclator (strada HERE in orasul HERE, daca iese UNIC). Intoarce {'zip':..} sau None.
    NU suprascrie un zip valid dat de client; NU atinge strada."""
    cur_zip = str(ad.get("zip") or "").strip().strip("-").strip()
    if re.fullmatch(r"\d{6}", cur_zip):
        return None
    g = here_geocode(ad, "ROU", key)
    if not g or g["rt"] not in ("houseNumber", "street"):
        return None
    # i↔â: dacă strada HERE e IDENTICĂ cu a clientului modulo â/î ('Birsei'≡'Bârsei', HERE știe forma corectă dar
    # scorul e mic fiindcă clientul a scris 'i') → prag coborât 0.75; altfel prag normal 0.9.
    _iexact = _here_street_iexact(ad.get("address1") or "", g["street"])
    if g["score"] < (0.75 if _iexact else HERE_MIN_SCORE):
        return None
    if not (g["zip"] and re.fullmatch(r"\d{6}", g["zip"])):
        return None
    if not _here_street_match(ad.get("address1") or "", g["street"]):
        return None
    # GARDA ORAS: zip-ul HERE sa fie din ACELASI oras cu ce a scris clientul (masurat: fara ea, ~1/60
    # completari veneau dintr-un ALT oras = misroute; cu ea = 0). Typo/substring tolerat.
    _cc, _hc = _fold(ad.get("city") or ""), _fold(g["city"] or "")
    if _cc and _hc and not (_cc == _hc or _typo_ok(_cc, _hc) or _typo_ok(_hc, _cc) or _cc in _hc or _hc in _cc):
        return None
    zc = g["zip"]
    # rafinare: zip autoritativ din nomenclator pt strada+oras HERE (daca e unic)
    try:
        cur = metrics_cursor_live()
        if cur and g["street"] and g["city"]:
            stoks = [t for t in re.split(r"[^a-z0-9]+", _fold(g["street"])) if len(t) >= 4 and t not in _ST_RANK]
            if stoks:
                cur.execute("SELECT DISTINCT cod_postal, nume_strada FROM public.romania_addresses "
                            "WHERE localitate_norm=%s AND cod_postal ~ '^[0-9]{6}$'", (_fold(g["city"]),))
                zz = {z for z, nm in cur.fetchall()
                      if all(any(st == t or _typo_ok(t, st, strict=True) for t in _fold(nm).split()) for st in stoks)}
                if len(zz) == 1:
                    zc = list(zz)[0]
    except Exception:
        pass
    return {"zip": zc}


def here_validate(addr, country, key):
    """queryScore HERE Geocoding (0-1) pt o adresă, restrâns pe țară. 0.0 la eroare/fără rezultat/fără cheie."""
    if not key or not country:
        return 0.0
    q = ", ".join([x for x in [_street_core(addr.get("address1")), addr.get("address2"), addr.get("city"), addr.get("zip")] if x])
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


# Cache HERE (RO + internațional CZ/PL/BG) — decizia se ia O SINGURĂ DATĂ per comandă, apoi NU se mai apelează HERE:
#   `.here_ro_ok`   = HERE a validat ≥0.9 → o expediez as-is; NU o re-iau (altfel intru în buclă cu corecția
#                     async a lui Frisbo/xConnector care flip-uie statusul). Odată cu AWB, `has_awb` o scoate.
#   `.here_ro_nogo` = HERE <0.9 (adresă chiar proastă) → CS; NU re-interoghez (altfel ~25k apeluri HERE/zi pe backlog).
# RO: citite pe ramura WRONG/UNKNOWN (o comandă reparată de CS devine VALID și pleacă prin ramura de sus).
# INTL: citite pe ramura `if intl` — comenzile externe rămân mereu „WRONG" pt validatorul RO, deci FĂRĂ cache
# re-validam tot backlog-ul (sute de comenzi CZ) la fiecare rulare. Caveat: o adresă intl reparată de CS rămâne
# în nogo → golește `.here_ro_nogo` dacă vrei re-verificare. Numele comenzii = cheie globală (prefix/magazin unic).
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
HERE_RO_OK_FILE = os.path.join(_HERE_DIR, ".here_ro_ok")
HERE_RO_NOGO_FILE = os.path.join(_HERE_DIR, ".here_ro_nogo")
def _here_state_load(fname):
    try:
        return set(open(fname, encoding="utf-8").read().split())
    except FileNotFoundError:
        return set()
def _here_state_add(fname, name):
    try:
        with open(fname, "a", encoding="utf-8") as f:
            f.write((name or "") + "\n")
    except Exception:
        pass
def _here_state_del(fname, name):
    """Scoate o intrare din cache-ul de stare (rescrie fișierul fără ea) — pt INVALIDARE pe eșec."""
    try:
        s = _here_state_load(fname)
        if name in s:
            s.discard(name)
            with open(fname, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(s)) + ("\n" if s else ""))
    except Exception:
        pass
def load_here_ok():    return _here_state_load(HERE_RO_OK_FILE)
def here_ok_add(name):    _here_state_add(HERE_RO_OK_FILE, name)
def here_ok_del(name):    _here_state_del(HERE_RO_OK_FILE, name)   # „HERE valid" dar curier respinge → re-validează
def here_nogo_add(name):  _here_state_add(HERE_RO_NOGO_FILE, name)

# TTL nogo: intrările SIMPLE (RO) rămân PERMANENTE — măsurat, cache-ul RO e corect (re-testul a picat 20/20),
# deci re-validarea ar arde apeluri HERE pe bani degeaba. Intrările cu DATĂ (intl) EXPIRĂ: nomenclatoarele
# naționale se completează + bug-uri trecute (cursor mort) au condamnat comenzi BUNE la CS pe veci
# (măsurat: 266/366 comenzi CZ blocate, ~60% ar trece azi).
HERE_NOGO_TTL_DAYS = 14


def here_nogo_add_ttl(name):
    """Intl: marchează nogo CU DATĂ, ca să expire după HERE_NOGO_TTL_DAYS și comanda să fie re-evaluată."""
    _here_state_add(HERE_RO_NOGO_FILE, "%s|%s" % (name, datetime.date.today().isoformat()))


def load_here_nogo():
    """Nogo ACTIV: intrările simple = permanente (RO); cele cu dată (intl) expiră după TTL → se re-validează."""
    today = datetime.date.today()
    out = set()
    for e in _here_state_load(HERE_RO_NOGO_FILE):
        nm, sep, ds = e.partition("|")
        if not sep:
            out.add(e)
            continue
        try:
            if (today - datetime.date.fromisoformat(ds)).days < HERE_NOGO_TTL_DAYS:
                out.add(nm)
        except Exception:
            out.add(nm)
    return out

# HOLD state (ciclu anti-loop): comenzi pe care CRONUL le-a pus pe hold (bad-address) + cele abandonate la CS.
CRON_HELD_FILE   = os.path.join(_HERE_DIR, ".cron_held")     # cronul a pus HOLD → dacă REAPARE (CS a scos-o) = AWB direct
CRON_GIVEUP_FILE = os.path.join(_HERE_DIR, ".cron_giveup")   # scoasă de CS dar AWB tot pică → CS manual, cronul n-o mai atinge
def load_cron_held():    return _here_state_load(CRON_HELD_FILE)
def cron_held_add(n):    _here_state_add(CRON_HELD_FILE, n)
def load_cron_giveup():  return _here_state_load(CRON_GIVEUP_FILE)
def cron_giveup_add(n):  _here_state_add(CRON_GIVEUP_FILE, n)

# HELD SWEEP: cronul de fulfill re-trece periodic peste comenzile pe care LE-A pus pe HOLD (bad-address /
# awb-esec-repetat) — ele NU reapar în `unf` (on_hold != unfulfilled), deci fără asta rămâneau blocate PE
# VECI chiar dacă regulile de corecție s-au îmbunătățit între timp. Gât per-magazin (nu la fiecare 15 min):
# un timestamp/magazin în .held_sweep → sweep doar dacă au trecut ≥ HELD_SWEEP_H ore (round-robin acoperă
# toate magazinele în ~o zi). Evită churn: cele ÎNCĂ proaste rămân pe hold, nu se eliberează degeaba.
HELD_SWEEP_FILE = os.path.join(_HERE_DIR, ".held_sweep")   # {shopDomain: last_iso_utc}
HELD_SWEEP_DEFAULT_H = 6
def _held_sweep_state():
    try:
        return json.load(open(HELD_SWEEP_FILE, encoding="utf-8"))
    except Exception:
        return {}
def held_sweep_due(shop, hours):
    h = HELD_SWEEP_DEFAULT_H if hours is None else hours   # 0 = forțează (always due); None = default
    last = _held_sweep_state().get(shop)
    if not last:
        return True
    try:
        t = datetime.datetime.fromisoformat(last)
    except Exception:
        return True
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() >= h * 3600
def held_sweep_mark(shop):
    d = _held_sweep_state()
    d[shop] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        json.dump(d, open(HELD_SWEEP_FILE, "w", encoding="utf-8"))
    except Exception:
        pass
DPD_CORRECTED_FILE = os.path.join(_HERE_DIR, ".dpd_corrected")   # comenzi cărora le-am corectat localitatea din DPD (o dată)
_DPD_CORRECTED = None
def _dpd_corrected():
    global _DPD_CORRECTED
    if _DPD_CORRECTED is None:
        _DPD_CORRECTED = _here_state_load(DPD_CORRECTED_FILE)
    return _DPD_CORRECTED
def dpd_corrected_add(n):
    _here_state_add(DPD_CORRECTED_FILE, n); _dpd_corrected().add(n)
INTL_SANITIZED_FILE = os.path.join(_HERE_DIR, ".intl_sanitized")   # comenzi intl cărora le-am reparat FORMATUL (o dată)
_INTL_SANITIZED = None
def _intl_sanitized():
    global _INTL_SANITIZED
    if _INTL_SANITIZED is None:
        _INTL_SANITIZED = _here_state_load(INTL_SANITIZED_FILE)
    return _INTL_SANITIZED
def intl_sanitized_add(n):
    _here_state_add(INTL_SANITIZED_FILE, n); _intl_sanitized().add(n)
RO_PHONE_FIXED_FILE = os.path.join(_HERE_DIR, ".ro_phone_fixed")
_RO_PHONE_FIXED = None
def _ro_phone_fixed():
    global _RO_PHONE_FIXED
    if _RO_PHONE_FIXED is None:
        _RO_PHONE_FIXED = _here_state_load(RO_PHONE_FIXED_FILE)
    return _RO_PHONE_FIXED
def ro_phone_fixed_add(n):
    _here_state_add(RO_PHONE_FIXED_FILE, n); _ro_phone_fixed().add(n)


RO_GENZIP_FIXED_FILE = os.path.join(_HERE_DIR, ".ro_genzip_fixed")   # comenzi RO cărora le-am pus codul GENERAL după eșec DPD pe zip specific (o dată)
_RO_GENZIP_FIXED = None
def _ro_genzip_fixed():
    global _RO_GENZIP_FIXED
    if _RO_GENZIP_FIXED is None:
        _RO_GENZIP_FIXED = _here_state_load(RO_GENZIP_FIXED_FILE)
    return _RO_GENZIP_FIXED
def ro_genzip_fixed_add(n):
    _here_state_add(RO_GENZIP_FIXED_FILE, n); _ro_genzip_fixed().add(n)


INTL_GENZIP_FIXED_FILE = os.path.join(_HERE_DIR, ".intl_genzip_fixed")   # comenzi INTL cărora le-am pus zip-ul CANONIC al localității după respingerea DPD pe localitate (o dată)
_INTL_GENZIP_FIXED = None
def _intl_genzip_fixed():
    global _INTL_GENZIP_FIXED
    if _INTL_GENZIP_FIXED is None:
        _INTL_GENZIP_FIXED = _here_state_load(INTL_GENZIP_FIXED_FILE)
    return _INTL_GENZIP_FIXED
def intl_genzip_fixed_add(n):
    _here_state_add(INTL_GENZIP_FIXED_FILE, n); _intl_genzip_fixed().add(n)


# Contor de eșecuri AWB per comandă (persistent, JSON). Un AWB care pică N ture la rând (latență sync care nu se
# mai rezolvă SAU adresă chiar-moartă) NU trebuie reîncercat la infinit — după prag → HOLD cu motiv → CS îl preia
# (regula „dacă nu merge, rămâne unfulfilled / CS, fără buclă"). Cazul tipic: eroarea 422 „was not created" pe care
# o tratam mereu ca tranzitorie și buclă la nesfârșit (măsurat: comenzi picate 7 ture la rând, ne-hold-uite).
AWB_FAILCOUNT_FILE = os.path.join(_HERE_DIR, ".awb_failcount")
AWB_GIVEUP_AFTER = 4   # atâtea eșecuri „tranzitorii" cumulate → HOLD la CS (la fails==2 rulăm întâi corecția agentică aac, apoi 2 ture să se sincronizeze)
def _awb_failcount_load():
    try:
        with open(AWB_FAILCOUNT_FILE) as f:
            return json.load(f)
    except Exception:
        return {}
def _awb_failcount_bump(name):
    d = _awb_failcount_load(); d[name] = int(d.get(name, 0)) + 1
    try:
        with open(AWB_FAILCOUNT_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass
    return d[name]

# cursor de MAGAZIN pt fulfill: rularea round-robin cu buget de timp — dacă un tick depășește --max-run-min,
# se oprește curat și SALVEAZĂ indexul magazinului următor, ca tura viitoare să CONTINUE cu cele rămase (nu de la capăt).
FULFILL_CURSOR_FILE = os.path.join(_HERE_DIR, ".fulfill_cursor")
def load_fulfill_cursor():
    try:
        return int((open(FULFILL_CURSOR_FILE).read().strip() or "0"))
    except Exception:
        return 0
def save_fulfill_cursor(i):
    try:
        with open(FULFILL_CURSOR_FILE, "w") as f:
            f.write(str(int(i)))
    except Exception:
        pass


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


def awbprint_recent_dup(order_number, window_hours=24):
    """Gardă DUPLICAT independentă de tag: întoarce numărul unei comenzi ANTERIOARE (≤window_hours) a ACELUIAȘI
    client, pe același magazin, cu ACELAȘI set de produse (SKU), care are DEJA AWB (fulfilled_at) și NU e anulată
    → `order_number` e un duplicat scăpat de tag-uire. None dacă nu găsește / lipsă DB. Sursă AWBprint (Frisbo)."""
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
    def _skus(lij):
        try:
            s = {((it.get("inventory_item") or {}).get("sku") or "").strip() for it in json.loads(lij) if it}
            return frozenset(x for x in s if x)
        except Exception:
            return frozenset()
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        r = con.run("select store_uid, customer_email, customer_name, frisbo_created_at, line_items::text "
                    "from orders where order_number = :n order by id desc limit 1", n=order_number)
        if not r or not r[0][3]:
            return None
        suid, em, nm, created, li = r[0]
        my = _skus(li)
        if not my:
            return None
        cand = con.run(
            "select order_number, line_items::text from orders "
            "where store_uid = :s and order_number <> :n and fulfilled_at is not null "
            "and coalesce(aggregated_status,'') <> 'cancelled' "
            "and frisbo_created_at between :t::timestamp - interval '%d hours' and :t::timestamp "
            "and ( (:e <> '' and lower(customer_email) = lower(:e)) or (:e = '' and lower(customer_name) = lower(:nm)) ) "
            "order by frisbo_created_at desc" % int(window_hours),
            s=suid, n=order_number, t=created, e=(em or ""), nm=(nm or ""))
        for onum, li2 in cand:
            if _skus(li2) == my:
                return onum
        return None
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def awbprint_identity(order_number):
    """(cheie_client, frozenset(SKU)) din AWBprint pt o comandă. ('', frozenset()) dacă lipsă.

    Aceeași sursă ca awbprint_recent_dup (Frisbo), dar întoarce IDENTITATEA comenzii curente — o
    folosim ca amprentă în garda de tură (in-run), fiindcă AWBprint nu reflectă instant AWB-ul pe
    care tocmai l-am făcut, iar awbprint_recent_dup (care cere fratele să aibă DEJA AWB) l-ar rata.
    """
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return "", frozenset()
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return "", frozenset()
    u = urlparse(url); con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        r = con.run("select customer_email, customer_name, line_items::text "
                    "from orders where order_number = :n order by id desc limit 1", n=order_number)
        if not r:
            return "", frozenset()
        em, nm, li = r[0]
        try:
            skus = frozenset(x for x in (((it.get("inventory_item") or {}).get("sku") or "").strip()
                                         for it in json.loads(li) if it) if x)
        except Exception:
            skus = frozenset()
        key = ((em or "").strip().lower() or (nm or "").strip().lower())
        return key, skus
    except Exception:
        return "", frozenset()
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


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
    if not getattr(a, "force", False):  # GARDĂ DUPLICAT: comandă anterioară ≤24h, același client + aceleași produse, cu AWB → probabil duplicat scăpat de tag
        dup_of = awbprint_recent_dup(a.order)
        if dup_of:
            print("  ⛔ %s pare DUPLICAT al %s (același client + aceleași produse, <24h, iar %s are DEJA AWB) → NU fac AWB." % (a.order, dup_of, dup_of))
            print("     Dacă e comandă reală (nu duplicat), rulează cu --force. Altfel anuleaz-o (order-cancel).")
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
    # Comandă ANULATĂ în Shopify → fulfillment order CLOSED → 422 „no open fulfillment orders",
    # care arată IDENTIC cu o eroare de adresă. 69% din comenzile pe care xConnector le listează ca
    # „unfulfilled + adresă proastă" sunt de fapt anulate. Verifică ÎNAINTE, nu corecta adrese moarte.
    if st:
        _node = find_order(st["shopDomain"], st["adminToken"], a.order)
        if _node and _node.get("cancelledAt"):
            print("  ⛔ %s e ANULATĂ în Shopify (%s) → nu fac AWB." % (a.order, _node["cancelledAt"][:10]))
            return
    # Adresă WRONG/UNKNOWN → corecție conservatoare înainte (best-effort; AWB-ul poate merge și fără).
    if o.get("addressStatus") in ("WRONG", "UNKNOWN"):
        cstt, _, _ = correct_address(xc, o, sh["shopDomain"], apply=True)
        if cstt == "corrected":
            print("  ✎ adresă corectată înainte de AWB")
            time.sleep(3)  # lasa xConnector sa propage adresa corectata inainte de create-label
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
        print("  → profil client + alte comenzi: gigi:cs-360 customer · tichete: gigi:cs-tickets (din DB/Richpanel, fără Shopify)")
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


def awbprint_compare(a, b):
    """(same_total, same_skus) pentru două comenzi, dintr-o singură interogare AWBprint.
    REGULA de dedup (decisă cu userul): se ANULEAZĂ doar dublura care are ȘI aceleași produse ȘI
    aceeași sumă = dublură tehnică sigură. Orice altceva (alt conținut sau altă valoare) poate fi
    o comandă REALĂ → HOLD, decide CS.
    (None, None) dacă nu pot determina (lipsă DB / o comandă lipsă) → apelantul tratează conservator."""
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return None, None
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return None, None

    def _skus(lij):
        try:
            s = {((it.get("inventory_item") or {}).get("sku") or "").strip() for it in json.loads(lij) if it}
            return frozenset(x for x in s if x)
        except Exception:
            return frozenset()

    u = urlparse(url)
    con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select order_number, total_price, currency, line_items::text from orders "
                       "where order_number in (:a, :b) order by id desc", a=a, b=b)
        got = {}
        for onum, tot, cur, li in rows:
            if onum not in got and tot is not None:
                got[onum] = ((round(float(tot), 2), (cur or "").upper()), _skus(li))
        if len(got) < 2:
            return None, None
        (ta, ska), (tb, skb) = got[a], got[b]
        # SKU-uri goale (line_items nesincronizate) = nu pot compara conținutul → nu declar „identic"
        same_skus = None if not (ska and skb) else (ska == skb)
        return (ta == tb), same_skus
    except Exception:
        return None, None
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
DUP_OK = "duplicata-verificata"   # keeper CONFIRMAT de fulfill: skip dedup → fă-i AWB (îl pune fulfill când decide keeper-ul)
MISSING_XC_TAG = "i"   # tag pus pe comenzile unfulfilled care NU apar în xConnector (nesincronizate) — cerut de owner,
                       # ca să fie găsibile în Shopify (filtru pe tag). Idempotent: se pune o dată, apoi se sare.
_TAGSADD_MUT = 'mutation($id:ID!,$tags:[String!]!){ tagsAdd(id:$id,tags:$tags){ userErrors{ field message } } }'


def cs_corrected_note(note):
    """CS pune marker-ul 't' în NOTE după ce corectează manual o comandă → e de TRIMIS (forțează AWB, nu la CS).
    Match pe 't' ca token de sine stătător (case-insensitive): „t", „corectat t", „t ok" → DA; „trimite", „test" → NU."""
    if not note:
        return False
    return "t" in [tok for tok in re.split(r"[\s,;.]+", str(note).lower()) if tok]


def tag_missing_xc(st, name):
    """Pune tag-ul MISSING_XC_TAG pe o comandă unfulfilled care nu e în xConnector. Best-effort (nu ridică)."""
    try:
        node = find_order(st["shopDomain"], st["adminToken"], name)
        if node and node.get("id"):
            shopify_gql(st["shopDomain"], st["adminToken"], _TAGSADD_MUT, {"id": node["id"], "tags": [MISSING_XC_TAG]})
    except Exception:
        pass
# Comenzi PLASATE/gestionate de CS (replasare COD, swap, resend, modify) — cs-actions le taghează cu agentul CS.
# fulfill NU le atinge (nici AWB, nici dedup): le gestionează CS, sunt diferite de comenzile clientului.
CS_AGENT_TAGS = {"raluca", "oana", "andra", "anna", "oanao", "stefan", "delia", "mihaela", "lisa"}
# Comenzi de tip cadou UGC/influencer (100% discount, flux separat) — NU li se face AWB automat din cron.
INFLUENCER_TAGS = ("influencer",)
SWAP_TAGS = ("swap",)   # schimb produs → HOLD (nu se expediază prin cron; îl duce CS/flux separat)


def shopify_unfulfilled(shop, token, since_date, max_pages=12):
    """Comenzi open + unfulfilled: [(name, createdAt, financialStatus, tags[], customerGid, sourceName, discountCodes[])]. None la auth fail."""
    out, cursor = [], None
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:250%s, query:"fulfillment_status:unfulfilled AND status:open AND created_at:>=%s"){ '
             'edges{ cursor node{ name createdAt displayFinancialStatus tags sourceName discountCodes note '
             'fulfillments(first:5){ trackingInfo{ number } } customer{ id } } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and not out and d.get("errors"):
            return None
        for e in edges:
            n = e["node"]
            # `shipped` = are DEJA tracking (chiar dacă fulfillment-ul e CANCELLED). O comandă livrată căreia i s-a
            # anulat fulfillment-ul reapare ca unfulfilled → fără garda asta, cronul i-ar face AWB NOU = dublă-expediere.
            shipped = any((t or {}).get("number") for f in (n.get("fulfillments") or []) for t in (f.get("trackingInfo") or []))
            out.append((n.get("name"), n.get("createdAt"), n.get("displayFinancialStatus"),
                        [str(t).lower() for t in (n.get("tags") or [])], (n.get("customer") or {}).get("id"),
                        n.get("sourceName"),
                        [str(dc).lower() for dc in (n.get("discountCodes") or [])],
                        n.get("note"), shipped))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    return out


def customer_is_newest(shop, token, customer_gid, this_created, since_date):
    """(is_newest, newest_name) din comenzile NEANULATE ale clientului în fereastră.
    is_newest = `this_created` e cea mai NOUĂ (regula „păstrează cea mai nouă"); None dacă fără
    client / nu pot determina (apelantul tratează conservator).
    newest_name = numărul comenzii PĂSTRATE — apelantul compară conținutul și suma cu ea înainte
    să anuleze ceva."""
    if not customer_gid:
        return None, None
    q = ('query{ customer(id:"%s"){ orders(first:50, query:"created_at:>=%s"){ edges{ node{ '
         'name createdAt cancelledAt } } } } }') % (customer_gid, since_date)
    d = shopify_gql(shop, token, q)
    edges = ((((d.get("data") or {}).get("customer") or {}).get("orders") or {}).get("edges")) or []
    live = [e["node"] for e in edges if e.get("node") and not e["node"].get("cancelledAt")]
    if not live:
        return None, None
    newest = max(live, key=lambda n: n.get("createdAt") or "")
    is_newest = (this_created or "") >= (newest.get("createdAt") or "")   # ISO: lexicografic = cronologic
    return is_newest, newest.get("name")


def resolve_duplicate(sh, xc, o, st, name, keeper, apply):
    """Ce facem cu dublura `name` față de comanda păstrată `keeper`.
    ANULEZ doar dacă are ȘI aceleași produse ȘI aceeași sumă = dublură tehnică sigură. Altfel HOLD:
    alt conținut sau altă valoare poate fi o comandă REALĂ, iar o anulare automată taie o vânzare.
    Întoarce (rezultat, motiv) — rezultat: cancelled | would-cancel | shipped-skip | failed | held."""
    same_total, same_skus = awbprint_compare(name, keeper) if keeper else (None, None)
    if same_total and same_skus:
        return cancel_duplicate(sh, xc, o, st, name, apply), "identice"
    if same_skus is False:
        reason = "dup-produse-diferite"
    elif same_total is False:
        reason = "dup-suma-diferita"
    else:
        reason = "dup-necomparabil"
    if has_awb(o) and awbprint_status(name) in PLECATA:
        return "shipped-skip", reason
    hold_and_log(st, sh["shopDomain"], name, reason, apply)
    if apply:
        cron_held_add(name)
    return "held", reason



def blocklist_gid_sweep(sh, st, xc, xmap, shop_block, since_date, apply):
    """Anuleaza comenzile clientilor BLOCKLIST-ati care au SCAPAT de bucla unfulfilled fiindca au apucat
    deja un AWB (Shopify Flow / cale rapida) -> au devenit `fulfilled` si ies din `shopify_unfulfilled`.
    Le prindem interogand DUPA customer_id (orice status fulfillment, necancelate) si le anulam daca NU
    au plecat (cancel_duplicate voideaza AWB-ul + anuleaza comanda; sare peste cele deja plecate)."""
    n = 0
    for gid in (shop_block or set()):
        num = str(gid).split("/")[-1]
        if not num.isdigit():
            continue
        q = ('query{ orders(first:20, query:"customer_id:%s AND -status:cancelled AND created_at:>=%s"){ '
             'edges{ node{ name displayFulfillmentStatus } } } }') % (num, since_date)
        try:
            d = shopify_gql(st["shopDomain"], st["adminToken"], q)
        except Exception:
            continue
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        for e in edges:
            name = e["node"]["name"]
            o = xmap.get(name)
            if not o:
                oid = shopify_order_id(name, st)
                o = xc.by_id(oid) if oid else None
            if not o:
                continue
            res = cancel_duplicate(sh, xc, o, st, name, apply)
            if res in ("cancelled", "would-cancel"):
                n += 1
                awb_event(kind="blocklist-gid-sweep", store=sh["shopDomain"], order=name, result=res)
                print("    ⛔ SWEEP %s = BLOCKLIST GID %s (fulfilled dar neplecat) → %s" % (name, gid, res))
    return n


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


# ── Log per-comandă al încercărilor de AWB (JSONL) — măsurabilitate cron (câte făcute/eșuate + eroarea REALĂ) ──
AWB_EVENT_LOG = os.environ.get("XC_AWB_EVENT_LOG") or os.path.join(HERE, ".awb_events.jsonl")


def awb_event(**rec):
    """Append o linie JSONL cu o încercare/decizie de AWB. Best-effort — NU aruncă (nu rupe fulfill-ul)."""
    try:
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(AWB_EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


NOTE_LABELS = {
    "bad-address": "adresă greșită",
    "awb-esec-repetat": "AWB eșuat de mai multe ori",
    "tag-influencer": "comandă cadou",
    "discount-cristinau": "comandă cadou",
    "grandia-magazii": "magazii de grădină",
    "locker-fara-curier": "punct de ridicare",
}


def hold_and_log(st, store, name, reason, apply):
    """Pune comanda pe HOLD în Shopify (doar dacă `apply`) + loghează event kind=hold. Best-effort — NU aruncă."""
    n = 0
    if apply and st:
        try:
            n = shopify_hold(st["shopDomain"], st["adminToken"], name, notes="xc-hold:" + reason)
        except Exception:
            n = 0
        try:   # cerut: la ORICE hold, motivul și în câmpul Note al comenzii (unde se uită CS/depozitul)
            shopify_append_note(st["shopDomain"], st["adminToken"], name, NOTE_LABELS.get(reason, reason))
        except Exception:
            pass
    awb_event(kind="hold", store=store, order=name, reason=reason, held=n, applied=bool(apply))
    return n


def _create_label(xc, body, tries=3, _ctx=None):
    """POST create-shipping-label cu retry scurt pe eșec TRANZITORIU (throttle DPD pe rafală:
    429/5xx sau 422 generic 'Shipping label was not created'). O adresă real-proastă (HERE a trecut-o
    dar curierul o respinge) eșuează toate cele `tries` → rămâne la CS. Întoarce (ok, status, data).
    Loghează per-comandă în AWB_EVENT_LOG (best-effort; `_ctx` = {store, order} pt context)."""
    ctx = _ctx or {}
    s = d = None
    for i in range(tries):
        s, d = xc.post("/api/actions/create-shipping-label", body)
        if s == 200 and isinstance(d, dict) and d.get("accepted") and \
           any(L.get("success") for L in (d.get("shippingLabels") or [])):
            trk = next((L.get("trackingNumber") for L in (d.get("shippingLabels") or []) if L.get("success")), None)
            awb_event(kind="awb", result="ok", store=ctx.get("store"), order=ctx.get("order"),
                      orderId=body.get("orderId"), connector=body.get("connectorId"), tracking=trk)
            return True, s, d
        msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
        transient = s in (429, 500, 502, 503, 504) or (s == 422 and "was not created" in msg)
        if not transient or i == tries - 1:
            break
        time.sleep(1.5 * (i + 1))  # backoff: 1.5s, 3s
    awb_event(kind="awb", result="fail", store=ctx.get("store"), order=ctx.get("order"),
              orderId=body.get("orderId"), connector=body.get("connectorId"),
              http=s, error=((d.get("errorMessage") if isinstance(d, dict) else str(d)) or "")[:300])
    return False, s, d


# ── DPD nomenclator (findSite după COD POȘTAL) — repară „Localitate nevalida": clientul pune city greșit pt zip-ul lui.
# DPD dă localitatea canonică după zip (RO=642, BG=100, ambele siteNomen=1). Corectăm city → AWB trece. Creds: COURIER_CREDS_JSON.
_DPD_AUTH = None
_DPD_SITE_CACHE = {}
DPD_COUNTRY_ID = {"ROMANIA": 642, "ROMÂNIA": 642, "RO": 642, "BULGARIA": 100, "BG": 100}


def _dpd_auth():
    global _DPD_AUTH
    if _DPD_AUTH is not None:
        return _DPD_AUTH or None
    raw = os.environ.get("COURIER_CREDS_JSON")
    if not raw:
        v, ok = _kb_secret("COURIER_CREDS_JSON")
        raw = v if ok else ""
    try:
        dpd = (json.loads(raw).get("dpd_creds") or {}).get("dpd-ro") or {}
        if dpd.get("username"):
            _DPD_AUTH = {"userName": dpd["username"], "password": dpd["password"], "language": "EN"}
            return _DPD_AUTH
    except Exception:
        pass
    _DPD_AUTH = {}
    return None


def dpd_site_by_zip(country_id, zipc):
    """Localitatea canonică DPD după cod poștal (cache pe (țară,zip)). None dacă DPD n-are zip-ul."""
    key = (country_id, zipc)
    if key in _DPD_SITE_CACHE:
        return _DPD_SITE_CACHE[key]
    auth = _dpd_auth()
    site = None
    if auth and zipc:
        try:
            req = urllib.request.Request("https://api.dpd.ro/v1/location/site/",
                  data=json.dumps({**auth, "countryId": country_id, "postCode": zipc}).encode(),
                  headers={"Content-Type": "application/json"})
            r = urllib.request.urlopen(req, timeout=30)
            sites = json.loads(r.read()).get("sites") or []
            site = sites[0] if sites else None
        except Exception:
            site = None
    _DPD_SITE_CACHE[key] = site
    return site


_CYR_LAT = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l",
            "м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch",
            "ш":"sh","щ":"sht","ъ":"a","ь":"y","ю":"yu","я":"ya"}


def _cyr2lat(s):
    """Transliterare chirilic (bulgar) → latin. Lasă latinul/cifrele neatinse."""
    out = []
    for ch in (s or ""):
        rep = _CYR_LAT.get(ch.lower())
        out.append(ch if rep is None else (rep.upper() if ch.isupper() else rep))
    return "".join(out)


def _looks_like_street(address1):
    """Adresa numește o STRADĂ (nu doar un număr/bloc)? Satele n-au străzi indexate, deci o adresă
    cu nume de stradă nu poate fi livrată decât într-o localitate cu nomenclator de străzi."""
    a = _fold(address1)
    a = re.sub(r"\b(nr|no|bl|sc|ap|et)\b\.?", " ", a)
    return len(re.findall(r"[a-z]{3,}", a)) > 0


def dpd_site_by_city(country_id, city, province, street=None):
    """Sit DPD după NUMELE localității — pentru comenzile FĂRĂ cod poștal (checkout-ul Shopify RO îl are OPȚIONAL).
    STRICT, fiindcă în RO există zeci de localități omonime (`Dumbrava` = 10 situri, 3 doar în ALBA):
    nume EXACT + județ (`region`) care se potrivește cu `province` din comandă + rezultat UNIC. Altfel None.
    Nu ghicim niciodată: ambiguu ⇒ lăsăm comanda la CS.
    Notă: DPD rutează pe `siteId`+`streetId`, nu pe cod poștal — deci zip-ul canonic al localității e suficient
    ca să rezolve situl; codul poștal al STRĂZII nu e obținabil (DPD nu-l expune, iar nomenclatorul nostru
    are `numar` NULL ⇒ nu se poate alege pe intervale de numere).
    Întoarce (site|None, motiv) — motivul e LOGAT ca să putem identifica de ce a picat fiecare caz, în special
    `necunoscut` = clientul a scris un SAT pe care DPD îl are indexat doar sub COMUNĂ (nu-l reparăm preventiv;
    îl strângem din log când chiar apare)."""
    auth = _dpd_auth()
    if not (auth and city):
        return None, "fara-oras"
    # DPD caută pe nume EXACT, FĂRĂ diacritice: „Buzău" → 0 rezultate, „Buzau" → BUZAU/120001.
    # Măsurat pe cazuri reale (Buzău ×4, Târgu Jiu) care picau ca „necunoscut" degeaba.
    query_name = _fold(city)
    try:
        req = urllib.request.Request("https://api.dpd.ro/v1/location/site/",
              data=json.dumps({**auth, "countryId": country_id, "name": query_name}).encode(),
              headers={"Content-Type": "application/json"})
        sites = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("sites") or []
    except Exception:
        return None, "eroare-api"
    want = _fold(city)
    exact = [s for s in sites if _fold(s.get("name") or "") == want]
    if not exact:
        # DPD nu are localitatea sub numele ăsta. Cazul tipic: SAT scris de client, DPD indexează COMUNA.
        near = ";".join(sorted({"%s/%s" % (s.get("name"), s.get("region")) for s in sites})[:3])
        return None, ("necunoscut" + (" (aproape: %s)" % near if near else " (0 sugestii)"))
    if province:
        pr = _fold(province)
        byreg = [s for s in exact if _fold(s.get("region") or "") == pr]
        if byreg:
            exact = byreg
    if len(exact) == 1:
        return exact[0], "ok"
    # ⛔ NU alege „singura localitate omonimă care are străzi". Regula asta se declanșează exact când
    # județul nu se potrivește cu NICIUN candidat — adică pe adrese CONTRADICTORII, unde câmpul greșit
    # poate fi la fel de bine ORAȘUL, nu județul. Caz real GT50711: „Slatina" + județ „București",
    # Str. Văilor — iar Str. Văilor există ȘI în București (031878) ȘI în Slatina/Olt (230057/230121).
    # Alegerea ar fi fost 50/50, nu deducție. Adresele contradictorii merg la CS.
    return None, "ambiguu (%d judete: %s)" % (len(exact), ",".join(sorted({(s.get("region") or "?") for s in exact})[:4]))


def dpd_fix_locality(xc, o, shop_domain, name):
    """DPD refuză localitatea → findSite după zip → corectează city la forma canonică DPD + transliterează strada
    (chirilic→latin, ca xConnector/DPD s-o accepte). True dacă a scris corecția."""
    ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
    zipc = (ad.get("zip") or "").strip()
    cid = DPD_COUNTRY_ID.get((ad.get("country") or "").strip().upper())
    if not cid:
        return False
    if not zipc:
        # FĂRĂ cod poștal (permis de checkout-ul Shopify RO) → încearcă să-l afli din numele localității.
        # Marcat în log ca `zip-city-fill` ca să putem URMĂRI dacă vreuna se întoarce cu „adresă greșită".
        _city, _prov = (ad.get("city") or "").strip(), (ad.get("province") or "").strip()
        site, why = dpd_site_by_city(cid, _city, _prov, ad.get("address1"))
        zc = (site or {}).get("postCode")
        if not zc:
            # NU ghicim. Logăm de ce, ca să identificăm cazurile REALE când pică (ex. sat vs comună).
            awb_event(kind="zip-city-fill", store=shop_domain, order=name, result="miss",
                      city=_city, region=_prov, zip="", detail=why)
            return False
        try:
            intl_correct_write(xc, o, shop_domain, {"city": (site.get("name") or "").title(), "zip": zc})
            awb_event(kind="zip-city-fill", store=shop_domain, order=name, result="ok",
                      city=site.get("name"), region=site.get("region"), zip=zc, how=why)
            return True
        except Exception:
            return False
    site = dpd_site_by_zip(cid, zipc)
    canon = (site or {}).get("name")
    if not canon:
        return False
    try:
        intl_correct_write(xc, o, shop_domain, {"city": canon.title(), "zip": zipc, "address1": _cyr2lat(ad.get("address1"))})
        return True
    except Exception:
        return False


def _do_awb(xc, sh, st, cons, con, name, o, notify):
    """Rutează curier (Grandia→Dragon Star) + parcel-count + face AWB. Întoarce (ok, permanent_fail, hold_reason).
    Pe „Localitate nevalida" (zip↔city greșit): DPD findSite după zip → corectează city → reîncearcă O DATĂ.
    permanent_fail = curierul a respins DEFINITIV → candidat de HOLD; hold_reason = motivul afișat la CS."""
    ocon = route_connector(sh, st, name, cons, con)
    if not ocon:
        return False, False, None
    pcount = order_parcel_count(st["shopDomain"], st["adminToken"], name)  # nr colete din metafield
    body = {"orderId": o.get("orderId"), "connectorId": ocon["id"], "parcelCount": pcount,
            "parcelType": "PARCEL", "notifyCustomer": bool(notify)}
    ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
    if ok:
        return True, False, None
    # PRIMA dată pe RO/BG: findSite pe zip → corectează localitatea (+ transliterează strada) → reîncearcă.
    # dpd_fix_locality se auto-limitează (doar RO/BG cu zip pe care DPD îl are). Dacă a corectat dar AWB-ul tot
    # pică (latență sync Shopify→xConnector), NU marca permanent → rămâne unfulfilled, pleacă tura viitoare.
    if name not in _dpd_corrected() and dpd_fix_locality(xc, o, sh["shopDomain"], name):
        dpd_corrected_add(name)
        time.sleep(3.0)
        ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
        if ok:
            awb_event(kind="dpd-locality-fix", store=sh["shopDomain"], order=name, result="ok")
            return True, False, None
        return False, False, None   # corectat, dar încă nesincronizat → reîncearcă tura viitoare (NU hold)
    # INTL (CZ/PL/BG): adresa e VALIDĂ dar DPD o respinge pe FORMAT (addressLine1>35, zip cu gunoi lipit, city>35).
    # Sanitizează câmpurile O SINGURĂ dată → reîncearcă. Măsurat: ~85% din eșecurile CZ, ~100% din PL sunt format.
    _ctry = HERE_COUNTRY.get(sh["shopDomain"])
    if _ctry and name not in _intl_sanitized() and dpd_intl_sanitize(xc, o, sh["shopDomain"], name, _ctry, st):
        intl_sanitized_add(name)
        time.sleep(3.0)
        ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
        if ok:
            awb_event(kind="intl-format-fix", store=sh["shopDomain"], order=name, result="ok")
            return True, False, None
        return False, False, None   # sanitizat, dar încă nesincronizat → reîncearcă tura viitoare (NU hold)
    msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
    transient = s in (429, 500, 502, 503, 504) or (s == 422 and "was not created" in msg)
    # RO: telefon in format gresit (bare 9-cifre "751842097" / "+400..." malformat) -> DPD RO il respinge.
    # Normalizeaza la 07xxxxxxxx O SINGURA data -> reincearca. (RO nu trece prin dpd_intl_sanitize.)
    if not _ctry and name not in _ro_phone_fixed():
        _rochg = ro_phone_fix(xc, o, sh["shopDomain"])
        ro_phone_fixed_add(name)
        if _rochg:
            time.sleep(3.0)
            ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
            if ok:
                awb_event(kind="ro-phone-fix", store=sh["shopDomain"], order=name, result="ok")
                return True, False, None
            return False, False, None
    # RO: LOCALITATE/zip respins PERMANENT de DPD (non-tranzitoriu, ex. valid-locality-id) → owner: „pune codul
    # general" → MIN cod_postal pe localitate → AWB-ul trece pe cod general + textul străzii. O SINGURĂ dată/comandă.
    # ⚠️ NU se declanșează pe „was not created" (tranzitoriu): măsurat pe reziduul Esteban, acele eșecuri sunt
    # `streetName NO_MATCH` (strada nu-i în nomenclatorul CURIERULUI) — codul general NU ajută (problema e strada,
    # nu zip-ul), iar suprascrierea DEGRADEAZĂ un zip bun (validatorul găsea zip corect). Alea → CS.
    if not _ctry and not transient and name not in _ro_genzip_fixed():
        _gzchg = ro_genzip_fallback(xc, o, sh["shopDomain"])
        ro_genzip_fixed_add(name)
        if _gzchg:
            time.sleep(3.0)
            ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
            if ok:
                awb_event(kind="ro-genzip-fallback", store=sh["shopDomain"], order=name, result="ok")
                return True, False, None
            msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
            transient = s in (429, 500, 502, 503, 504) or (s == 422 and "was not created" in msg)
    # INTL (BG/CZ): DPD a respins LOCALITATEA (valid-locality-id) cu ORAȘ bun dar ZIP greșit (ex BG Шумен+9750 = de
    # fapt satul Мадара) → pune zip-ul CANONIC al localității din nomenclator → DPD rezolvă pe cod → reîncearcă.
    # REACTIV (doar pe respingerea DPD) = ZERO fals-pozitiv (nu atinge zip-uri valide ca 8127). O SINGURĂ dată/comandă.
    if _ctry and not transient and ("localit" in msg.lower()) and name not in _intl_genzip_fixed():
        _igchg = intl_genzip_fallback(xc, o, sh["shopDomain"], _ctry)
        intl_genzip_fixed_add(name)
        if _igchg:
            time.sleep(3.0)
            ok, s, d = _create_label(xc, body, _ctx={"store": sh["shopDomain"], "order": name})
            if ok:
                awb_event(kind="intl-genzip-fallback", store=sh["shopDomain"], order=name, result="ok")
                return True, False, None
            msg = (d.get("errorMessage") if isinstance(d, dict) else str(d)) or ""
            transient = s in (429, 500, 502, 503, 504) or (s == 422 and "was not created" in msg)
    fails = _awb_failcount_bump(name)   # câte ture la rând a picat AWB-ul acestei comenzi
    # A PICAT DPD de 2 ori → rulează VALIDAREA/CORECȚIA AGENTICĂ xConnector (match-address + ai-correct-address,
    # conservator: 1 candidat, core ≥0.95, zip confirmat, nr casă păstrat). RO-only (match e pe „Romania").
    # Corectată → adresa devine VALID → reîncearcă tura viitoare (după sync), NU hold. O SINGURĂ dată (la fails==2).
    if fails == 2 and sh["shopDomain"] not in HERE_COUNTRY:
        try:
            cstat, _capplied, cdet = correct_address(xc, o, sh["shopDomain"], apply=True)
        except Exception as e:
            cstat, cdet = "error", str(e)[:100]
        # `correct_address` (xc.match) e conservator → întoarce „manual" pe multe adrese reparabile. Dacă n-a
        # corectat, încearcă NOMENCLATORUL BOGAT (pre-clean/deglue/fix-JUDEȚ/localitate/general-zip). Măsurat pe
        # reziduul „was not created": ~44% (19/43) sunt would-correct de nomenclator dar ratate de match — ajungeau
        # via-HERE la DPD (respins) fiindcă erau prinse în cache-ul `.here_ok`. Aici le recuperăm.
        if cstat != "corrected":
            try:
                nstat, _, ndet = nomenclator_correct(xc, o, sh["shopDomain"], metrics_cursor_live(), apply=True)
                if nstat == "corrected":
                    cstat, cdet = nstat, "nomen: " + str(ndet)[:80]
                    # corecția face addressStatus→VALID → calea RO din clasificare face AWB direct pe VALID (nu mai
                    # trece prin cache-ul `.here_ok`), deci comanda se expediază tura viitoare fără invalidare de cache.
            except Exception:
                pass
        awb_event(kind="aac-correct", store=sh["shopDomain"], order=name, result=cstat, detail=cdet)
        if cstat == "corrected":
            return False, False, None   # adresă corectată → reîncearcă tura viitoare (după sync), NU hold
    if transient and fails >= AWB_GIVEUP_AFTER:
        # „tranzitoriu" dar buclează de prea multe ture (latență sync care nu se mai rezolvă / adresă chiar-moartă)
        # → oprim bucla: HOLD cu motiv → CS. (Regula: dacă nu merge de N ori, rămâne la CS, fără buclă la infinit.)
        here_ok_del(name)   # FIX cron: invalidez „HERE valid" ca la revenire (CS reparat / sweep) să re-corecteze
        return False, True, "awb-esec-repetat"
    # non-tranzitoriu = curier a respins PERMANENT (după corecție = chiar nelivrabil) → HOLD bad-address
    if not transient:
        # curierul respinge o adresă cache-uită drept „HERE valid" → cache-ul e GREȘIT. Îl invalidez ca fulfill-ul
        # următor s-o re-claseze (nomenclator/snap) în loc s-o retrimită orb la DPD. (Cauza „cronului care rulează prost".)
        here_ok_del(name)
    return False, (not transient), ("bad-address" if not transient else None)


def sweep_held_orders(xc, sh, st, xmap, cron_giveup, mcur, intl, dfrom, apply):
    """HELD SWEEP — comenzile pe care CRONUL le-a pus pe HOLD (bad-address / awb-esec-repetat) NU reapar în
    `unf` (on_hold != unfulfilled), deci pipeline-ul normal nu le re-încearcă cu regulile noi de corecție.
    Aici le luăm separat: dacă adresa e ACUM livrabilă (VALID în xConnector — corectată între timp de cronul
    `correct` sau de reguli noi — SAU devine validă printr-o corecție conservatoare acum) → ELIBEREZ hold-ul
    (DOAR hold-urile NOASTRE, non-protejate) ca fulfill-ul din rularea următoare să-i facă AWB-ul cu toată
    toleranța lui de propagare/retry. Cele ÎNCĂ proaste rămân pe hold (fără churn). NU fac AWB inline (evit
    marcarea prematură ca eșec pe adresă doar-corectată-neîncă-propagată). Întoarce (eliberate, rămase_pe_hold).
    RO → correct_address (validator agentic, toate gărzile). Extern → intl_nomen (nomenclator determinist, fără
    cost HERE); intl ne-confirmat de nomenclator rămâne pe hold (se analizează separat)."""
    rel = left = 0
    try:
        names = shopify_our_bad_holds(st["shopDomain"], st["adminToken"], dfrom)
    except Exception:
        names = []
    for hname in names:
        if hname in cron_giveup:      # scoasă cândva de CS + AWB tot pică → CS manual, n-o mai ating
            left += 1; continue
        o = xmap.get(hname)
        if not o:                     # în afara ferestrei xConnector → o prinde `correct`/o rulare viitoare
            left += 1; continue
        status = (o.get("addressStatus") or "").upper()
        deliverable = status in ("VALID", "AUTOCORRECTED", "AUTO_CORRECTED")
        if not deliverable and apply:
            if intl:
                country = HERE_COUNTRY[sh["shopDomain"]]
                _bd = xc.by_id(o.get("orderId")) or {}
                for _ in range(4):                       # by_id GOL = rate-limit → retry (altfel nomen vede adresă goală → skip greșit)
                    if _bd.get("shippingAddress"):
                        break
                    time.sleep(3); _bd = xc.by_id(o.get("orderId")) or {}
                ad = _bd.get("shippingAddress") or {}
                nres = intl_nomen(country, mcur, ad)
                if nres is not None and nres.get("status") in ("valid", "corrected"):
                    if nres.get("status") == "corrected" and nres.get("address"):
                        intl_correct_write(xc, o, sh["shopDomain"], nres["address"])
                    deliverable = True
            else:
                # RO — încearcă FRESH ambele motoare (ignoră cache-ul here_nogo, care e permanent pe intrări RO
                # vechi): întâi corecția agentică (aac, cele mai noi reguli), apoi nomenclatorul determinist.
                changed = False
                try:
                    cstt, _, _ = correct_address(xc, o, sh["shopDomain"], apply=True)
                    changed = (cstt == "corrected")
                except Exception:
                    pass
                if not changed:
                    try:
                        nstt, _, _ = nomenclator_correct(xc, o, sh["shopDomain"], mcur, apply=True)
                        changed = (nstt == "corrected")
                    except Exception:
                        pass
                # CONFIRM statusul REAL după corecție: un motor poate raporta „corrected" doar pe zip iar adresa să
                # rămână UNKNOWN (ex. stradă fără nr casă). Eliberez DOAR dacă addressStatus a devenit cu adevărat
                # livrabil → main loop-ul îl expediază direct (fără să treacă prin cache-ul here_nogo). Altfel rămâne
                # pe hold (evit să eliberez comenzi încă-proaste care ar face bounce release→re-hold).
                if changed:
                    try:
                        deliverable = ((xc.by_id(o.get("orderId")) or {}).get("addressStatus") or "").upper() in (
                            "VALID", "PERFECT", "AUTOCORRECTED", "AUTO_CORRECTED")
                    except Exception:
                        deliverable = False
        if not deliverable:
            left += 1; continue
        if not apply:
            rel += 1; continue        # dry-run: AR elibera
        try:
            nrel, _reasons, skipped = shopify_release_holds(st["shopDomain"], st["adminToken"], hname)
        except Exception:
            nrel, skipped = 0, []
        if skipped:                   # are ȘI un hold LEGITIM (fraudă/stoc/plată) → NU expediez peste el
            left += 1; continue
        if nrel:
            rel += 1
            print("    ♻️ %s = hold vechi, adresă acum livrabilă → eliberat (→ AWB tura următoare)" % hname)
        else:
            left += 1
    return rel, left


def cmd_fulfill(a):
    """Safety-net peste Shopify Flow: comenzi open+unfulfilled mai vechi de --max-age-min (Flow a ratat AWB-ul) →
    VALID → fă AWB (create-shipping-label, DPD default); WRONG/UNKNOWN → corecție conservatoare → dacă devine
    VALID, fă AWB; altfel → CS. Sare cele cu AWB (Flow le-a făcut). DUPLICATE (tag duplicata/duplicata3/duplicat4):
    DECIDENT UNIC (fost dup_guard, unificat aici 2026-07-15) — default păstrează cea mai NOUĂ comandă (→ AWB),
    EXCEPȚIE: dacă cea VECHE are deja AWB (pleacă) → anulează cea nouă (nu dubla); CARDUL (PAID) nu se anulează
    NICIODATĂ (protecție dublă-încasare). Keeper-ul primește tag `duplicata-verificata` (+ scoate `duplicata`) → la
    rulările următoare e shield: skip dedup, i se face AWB. Anulare = reason OTHER, restock ON, fără refund/notify,
    protecție livrare (nu anulez ce-a plecat). Dry-run by default (--apply scrie).
    RO — POARTA HERE (faza 1): dacă validatorul RO nu poate corecta o adresă WRONG/UNKNOWN dar HERE Geocoding
    o dă ≥0.9, o expediez AS-IS (a-2-a opinie — validatorul RO supra-respinge, ~55% se livrează oricum); respinsele
    de HERE se cache-uiesc în `.here_ro_nogo` (nu re-interoghez). Exclude externe (validator RO) + --exclude Grandia."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    max_age = getattr(a, "max_age_min", 15) or 15
    dto = now.date().isoformat()
    dfrom = (now - datetime.timedelta(days=a.days)).date().isoformat()
    toks_dom = {t["shopDomain"]: t for t in load_shopify_tokens()}
    since7 = (now - datetime.timedelta(days=7)).date().isoformat()
    blocklist, bl_phones, bl_addrs, bl_ph_hold, bl_ad_hold = load_blocklist()  # GID + BAN(3+) + HOLD(2) telefon/adresă
    hkey_here = here_key()        # cheia HERE (RO + CZ/PL/BG) — o iau o dată
    here_ok = load_here_ok()      # comenzi RO deja VALIDATE (nomenclator/HERE) → nu le re-validez (evit bucla cu corecția Frisbo/xc)
    here_nogo = load_here_nogo()  # comenzi RO pe care nomenclator+HERE le-au picat → nu re-interoghez
    cron_held = load_cron_held()      # comenzi pe care cronul le-a pus pe hold (bad-address) → dacă REAPAR = CS le-a scos
    cron_giveup = load_cron_giveup()  # scoase de CS dar AWB tot pică → CS manual, nu re-atinge
    mcur = metrics_cursor_live()  # cursor read-only pe metrics = nomenclatoare (RO/CZ/PL/BG) STRAT 1 — REZILIENT (reconectează)
    run_start = time.time()
    budget_s = (getattr(a, "max_run_min", 0) or 0) * 60   # buget de timp/rulare; peste el → opresc + reiau de la magazinul următor
    _all_shops = load_shops()
    _n = len(_all_shops)
    _use_rot = bool(budget_s) and not getattr(a, "shop", None) and _n > 0   # round-robin+buget DOAR la rularea completă (nu la --shop)
    _start_idx = (load_fulfill_cursor() % _n) if _use_rot else 0
    _ordered = (_all_shops[_start_idx:] + _all_shops[:_start_idx]) if _use_rot else _all_shops
    _stopped_early = False
    # Gardă de DUPLICAT în aceeași tură: {(magazin, cheie_client, set_SKU)} cărora le-am făcut deja AWB.
    # AWBprint (oglinda Frisbo) reflectă cu întârziere → fără asta, două dubluri din aceeași tură ies AMBELE.
    made_ident = set()
    for _pos, sh in enumerate(_ordered):
        if _use_rot and _pos > 0 and (time.time() - run_start) > budget_s:
            save_fulfill_cursor((_start_idx + _pos) % _n)   # tura viitoare CONTINUĂ de la acest magazin (cele rămase)
            print("═" * 72)
            print("  ⏱️ buget %d min atins → mă opresc; tura viitoare continuă de la %s (%d magazine rămase)"
                  % (getattr(a, "max_run_min", 0), sh["shopDomain"], _n - _pos))
            _stopped_early = True
            break
        if skip_shop(sh, a):
            continue
        print("═" * 72)
        st = toks_dom.get(sh["shopDomain"])
        if not st:
            print("  %s — fără token Shopify → skip" % sh["shopDomain"]); continue
        xc = XC(sh["apiKey"])
        mcur = metrics_cursor_live()  # REÎMPROSPĂTEAZĂ per-magazin: conexiunea idle poate muri între magazine pe tura lungă
        xmap = {o.get("orderName"): o for o in xc.orders(dfrom, dto)}
        unf = shopify_unfulfilled(st["shopDomain"], st["adminToken"], dfrom)
        if unf is None:
            print("  %s — Shopify auth FAIL (OAuth-rotation?) → skip" % sh["shopDomain"]); continue
        con, cons = pick_connector(xc, a)  # DPD Romania default — și pt externe (DPD livrează cross-border CZ/PL/BG)
        intl = sh["shopDomain"] in HERE_COUNTRY
        hkey = hkey_here
        ready = fixable = hard = had_awb = noxc = made = fixed = failed = team_n = infl = here_ready = held_n = swap_n = 0
        already_shipped_n = 0   # comenzi cu tracking dar fulfillment anulat → NU re-expediez (gardă anti-dublă-expediere)
        dup_keep = dup_cancel = dup_shipped = dup_unknown = dup_untag = blocked = dup_hold = 0
        bad_addr = []   # bad-address (hard sau curier-respins-permanent) → HOLD la finalul magazinului
        awb_fail_reason = {}   # name -> motiv HOLD specific (awb-esec-repetat după 3 ture), altfel bad-address
        shop_block = blocklist.get(sh["shopDomain"], set())
        # SWEEP anti-evadare: clientii banati care au apucat deja un AWB (fulfilled) nu apar in `unf`
        # (shopify_unfulfilled filtreaza unfulfilled) -> ii prindem separat dupa customer_id si ii anulam.
        if shop_block:
            blocked += blocklist_gid_sweep(sh, st, xc, xmap, shop_block, dfrom, a.apply)
        # HELD SWEEP (o dată la ~HELD_SWEEP_H ore/magazin, gât per-magazin ca să nu batem xConnector la fiecare
        # 15 min): eliberează hold-urile vechi bad-address/awb-esec devenite livrabile → rularea următoare le face
        # AWB. Round-robin-ul acoperă toate magazinele în ~o zi. --no-held-sweep îl oprește.
        if not getattr(a, "no_held_sweep", False) and held_sweep_due(sh["shopDomain"], getattr(a, "held_sweep_hours", HELD_SWEEP_DEFAULT_H)):
            _hrel, _hleft = sweep_held_orders(xc, sh, st, xmap, cron_giveup, mcur, intl, dfrom, a.apply)
            if _hrel or _hleft:
                print("  ♻️ held-sweep: %d %s → AWB · %d rămân pe hold"
                      % (_hrel, "AR fi eliberate" if not a.apply else "eliberate", _hleft))
            if a.apply:
                held_sweep_mark(sh["shopDomain"])
        for name, created, fin, tags, cust, source, disc, note, shipped in unf:
            c = parse_iso(created)
            if not c or (now - c).total_seconds() / 60.0 <= max_age:
                continue
            # DEJA EXPEDIATĂ (are tracking) dar fulfillment-ul e anulat → reapare ca unfulfilled. NU re-expedia
            # (dublă-expediere a unei comenzi livrate = bani pierduți). CS/owner re-pune fulfillment-ul (vezi memoria).
            if shipped:
                already_shipped_n += 1; continue
            o = xmap.get(name)
            if not o:
                noxc += 1
                # NU e în xConnector (nesincronizată) → n-are cum să primească AWB. O TAG-uiesc ca să fie găsibilă
                # în Shopify (owner: „când nu apar, pus tag"). Idempotent: doar dacă n-are deja tag-ul.
                if a.apply and MISSING_XC_TAG not in tags:
                    tag_missing_xc(st, name)
                continue
            # Tag de AGENT CS pe comandă (stefan/mihaela/raluca/delia/lisa…) = CS și-a asumat-o → TRECE de
            # blocklist (chiar dacă clientul e listat). Cerut de user.
            cs_override = any(t in CS_AGENT_TAGS for t in tags)
            # BLOCKLIST client (serial-refuser/fraudă din KB): NU expediez + anulez (ca duplicat vechi). ÎNAINTE
            # de has_awb, ca să anulez inclusiv o comandă căreia Flow-ul i-a apucat un AWB (dacă nu a plecat).
            if cust and cust in shop_block and not cs_override:
                res = cancel_duplicate(sh, xc, o, st, name, a.apply)
                print("    ⛔ %s = BLOCKLIST (client %s) → %s (NU se expediază)" % (name, cust, res))
                blocked += 1; continue
            if has_awb(o):
                had_awb += 1; continue
            # `swap` (schimb) = IGNOR complet — nici AWB, nici hold; îl duce fluxul de swap separat.
            if any(tg in tags for tg in SWAP_TAGS):
                swap_n += 1; continue
            # `influencer` (cadou UGC) → HOLD (nu doar skip) ca să iasă din pool + să-l preia fluxul separat.
            # ÎNAINTE de team_placed, fiindcă multe influencer sunt draft orders (altfel ar primi AWB ca team_placed).
            if any(tg in tags for tg in INFLUENCER_TAGS):
                hold_and_log(st, sh["shopDomain"], name, "tag-influencer", a.apply); infl += 1; continue
            # discount `CristinaU` (comenzi Cristina UGC) → HOLD. Substring (prinde și variante CristinaU10 etc.).
            if any("cristinau" in dc for dc in disc):
                hold_and_log(st, sh["shopDomain"], name, "discount-cristinau", a.apply); held_n += 1; continue
            # Grandia `magazii de grădină` = EXCLUS de la AWB (au plată parțială) → HOLD, le duce CS manual.
            if sh["shopDomain"] == GRANDIA_DOMAIN and (order_product_types(st["shopDomain"], st["adminToken"], name) & GRANDIA_HOLD_TYPES):
                hold_and_log(st, sh["shopDomain"], name, "grandia-magazii", a.apply); held_n += 1; continue
            # CICLU HOLD anti-loop: giveup (scoasă de CS + AWB picat) → n-o mai ating. Comandă pe care CRONUL a pus-o
            # pe hold (bad-address) dar a REAPĂRUT în unfulfilled = CS a scos-o de pe hold → o girează → AWB DIRECT
            # (fără re-validare/dedup). Pică → giveup (CS manual, nu re-hold, fără buclă).
            if name in cron_giveup:
                continue
            if name in cron_held:
                if a.apply:
                    okr, _perm, _hr = _do_awb(xc, sh, st, cons, con, name, o, a.notify)
                    awb_event(kind="release-awb", store=sh["shopDomain"], order=name, result="ok" if okr else "fail")
                    if okr:
                        made += 1
                    else:
                        failed += 1; cron_giveup_add(name)
                continue
            # PLASATĂ DE CS (tag agent) sau prin DRAFT ORDER (replasare COD/swap/resend, UGC) → NU aplic dedup
            # (ar părea fals duplicat al comenzii vechi a clientului), DAR le fac AWB normal — sunt legitime de expediat.
            team_placed = any(t in CS_AGENT_TAGS for t in tags) or source == "shopify_draft_order"
            if team_placed:
                team_n += 1
            elif DUP_OK in tags:
                dup_keep += 1  # KEEPER confirmat (duplicata-verificata, pus de o rulare anterioară) → skip dedup, fă-i AWB
            elif any(tg in tags for tg in DUP_TAGS):
                # REGULA (decisă cu userul): default = păstrează cea mai NOUĂ comandă; EXCEPȚIE = dacă cea VECHE
                # are deja AWB (pleacă) → anulează cea nouă (nu dubla AWB); CARDUL nu se anulează NICIODATĂ.
                x_paid = (fin or "").upper() == "PAID"
                sibling = None if x_paid else awbprint_recent_dup(name)
                if sibling:
                    # frate mai vechi ≤24h (același client+produse) are DEJA AWB = deja pleacă → X (cash) = dublura.
                    res, why = resolve_duplicate(sh, xc, o, st, name, sibling, a.apply)
                    if res == "shipped-skip":
                        dup_shipped += 1
                    elif res in ("cancelled", "would-cancel"):
                        dup_cancel += 1
                    elif res == "held":
                        dup_hold += 1
                        print("    ⏸️ %s vs %s: %s → HOLD (nu anulez) → CS" % (name, sibling, why))
                    else:
                        failed += 1
                    continue
                if not x_paid:
                    # Fereastra trebuie să acopere ȘI comanda însăși: pe una mai veche de 7 zile, clientul n-are
                    # nicio comandă în ultimele 7 zile → dates=[] → None → rămânea blocată PE VECI (nici AWB, nici
                    # anulare). Măsurat: 25 comenzi CZ înțepenite din 27. Coborâm pragul la data comenzii.
                    dup_floor = min(since7, (created or "")[:10] or since7)
                    newest, keeper = customer_is_newest(st["shopDomain"], st["adminToken"], cust, created, dup_floor)
                    if newest is False:
                        # există o comandă mai nouă → X = dublura veche; o anulez DOAR dacă e identică cu ea
                        res, why = resolve_duplicate(sh, xc, o, st, name, keeper, a.apply)
                        if res == "shipped-skip":
                            dup_shipped += 1
                        elif res in ("cancelled", "would-cancel"):
                            dup_cancel += 1
                        elif res == "held":
                            dup_hold += 1
                            print("    ⏸️ %s vs %s: %s → HOLD (nu anulez) → CS" % (name, keeper, why))
                        else:
                            failed += 1
                        continue
                    if newest is None:
                        dup_unknown += 1; continue  # fără client → nu pot decide → NU expediez, NU anulez
                # X = KEEPER (card protejat / cea mai nouă fără frate-cu-AWB) → marchez `duplicata-verificata`,
                # scot `duplicata*` (audit + deblocare), apoi cade prin la logica de AWB (i se face AWB-ul aici).
                if a.apply:
                    node = find_order(st["shopDomain"], st["adminToken"], name)
                    if node and node.get("id"):
                        shopify_add_tags(st["shopDomain"], st["adminToken"], node["id"], [DUP_OK])
                        shopify_remove_tags(st["shopDomain"], st["adminToken"], node["id"], list(DUP_TAGS))
                dup_keep += 1
            else:
                # NETAG-UIT + non-CS → gardă duplicat INDEPENDENTĂ de tag (cazul BELA34579: tag-ul „duplicata" n-a apucat
                # înainte de AWB). Dacă același client are o comandă ANTERIOARĂ ≤24h cu ACELEAȘI produse care are DEJA AWB
                # → NU fac AWB; îl las la CS (nu anulez automat — poate fi comandă reală).
                dup_of = awbprint_recent_dup(name)
                if dup_of:
                    dup_untag += 1
                    print("    ⚠️ %s = DUPLICAT netag-uit al %s (același client + aceleași produse <24h, %s are AWB) → NU fac AWB (la CS)" % (name, dup_of, dup_of))
                    continue
            # BLOCKLIST după TELEFON / ADRESĂ (serial-refuseri care schimbă emailul, ex. Jefferson pe „Str.
            # Libertății 123"). Sursă LIVE = xConnector (AWBprint e ~4h în urmă → nu vede comanda proaspătă).
            # Un singur `by_id`, aici, pe candidații REALI de expediere (după toate skip-urile ieftine + dedup),
            # nu pe tot backlogul. Tag de agent CS = trece. Hit → anulez (ca blocklistul de GID) + notez.
            if (bl_phones or bl_addrs or bl_ph_hold or bl_ad_hold) and not cs_override:
                _ad = (xc.by_id(o.get("orderId")) or {}).get("shippingAddress") or {}
                _p = _bl_phone(_ad.get("phone"))
                _aa = _bl_addr(_ad)
                # BAN (3+ refuzuri, 0 livrări) — anulez.
                _hitp = bool(bl_phones) and _p in bl_phones
                _hita = bool(bl_addrs) and _aa in bl_addrs
                if _hitp or _hita:
                    res = cancel_duplicate(sh, xc, o, st, name, a.apply)
                    why = "telefon" if _hitp else "adresă"
                    if a.apply:
                        try:
                            shopify_append_note(st["shopDomain"], st["adminToken"], name, "3+ refuzuri – anulat")
                        except Exception:
                            pass
                    awb_event(kind="blocklist-pa", store=sh["shopDomain"], order=name, reason=why, result=res)
                    print("    ⛔ %s = BLOCKLIST %s (serial-refuzuri, 0 livrări) → %s (NU se expediază)" % (name, why, res))
                    blocked += 1; continue
                # HOLD (2 refuzuri, 0 livrări) — NU expediez automat; CS confirmă la telefon întâi.
                _holdp = bool(bl_ph_hold) and _p in bl_ph_hold
                _holda = bool(bl_ad_hold) and _aa in bl_ad_hold
                if _holdp or _holda:
                    hold_and_log(st, sh["shopDomain"], name, "2 refuzuri – confirmă", a.apply)
                    awb_event(kind="hold-2ref", store=sh["shopDomain"], order=name,
                              reason=("telefon" if _holdp else "adresă"))
                    print("    ⏸️ %s = 2 refuzuri/0 livrări (%s) → HOLD de confirmat (CS sună înainte)" % (
                        name, "telefon" if _holdp else "adresă"))
                    held_n += 1; continue
            if cs_corrected_note(note):
                # CS a pus 't' în note = a corectat manual comanda → DE TRIMIS. Dar poate n-a verificat exact (ex. zip),
                # deci aplic TOTUȘI corecția automată (nomenclator RO / intl → repară zip/localitate), apoi FORȚEZ AWB
                # (nu-l trimit la CS, chiar dacă rămâne „WRONG" — CS și-a asumat). Dedup rămâne activ (a rulat mai sus).
                if a.apply:
                    try:
                        if intl:
                            _ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
                            _nr = intl_nomen(HERE_COUNTRY[sh["shopDomain"]], metrics_cursor_live(), _ad)
                            if _nr and _nr.get("status") == "corrected" and _nr.get("address"):
                                intl_correct_write(xc, o, sh["shopDomain"], _nr["address"])
                        else:
                            nomenclator_correct(xc, o, sh["shopDomain"], metrics_cursor_live(), apply=True)
                    except Exception:
                        pass
                do_awb = True; ready += 1
            elif intl:
                # extern (CZ/PL/BG): validatorul RO dă fals WRONG. STRAT 1 = nomenclator NAȚIONAL — CZ pe RÚIAN
                # (`cz_addresses`), PL pe PRG (`pl_*`), BG pe OSM (`bg_localities`/`bg_streets`, 11k localități,
                # cirilic+transliterare Latină, detecție oficiu Еконт/Спиди) — DETERMINIST, gratis, confirmă livrabil
                # (sate/orașe) + curăță orașul; STRAT 2 = HERE (cu CACHE: fără el re-validam sute de comenzi/rulare = €30).
                country = HERE_COUNTRY[sh["shopDomain"]]
                ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
                nres = intl_nomen(country, metrics_cursor_live(), ad)  # cursor viu/reconectat per comandă (nu masca moartea drept „no match")
                if nres is not None and nres.get("status") in ("valid", "corrected", "cs"):
                    if nres["status"] == "cs":
                        hard += 1; bad_addr.append(name); continue   # chiar fără număr casă → HOLD (bad-address)
                    if nres["status"] == "corrected" and a.apply and nres.get("address"):
                        intl_correct_write(xc, o, sh["shopDomain"], nres["address"])   # scriu orașul/PSČ corectat (best-effort)
                    do_awb = True; ready += 1                   # nomenclator confirmă livrabil → AWB
                else:
                    # țară fără nomenclator (PL/BG) SAU needs_geocoder → HERE cu cache (decizie o dată/comandă)
                    if name in here_nogo:
                        hard += 1; bad_addr.append(name); continue
                    if name in here_ok:
                        do_awb = True; ready += 1
                    elif here_validate(ad, country, hkey) >= HERE_MIN_SCORE:
                        do_awb = True; ready += 1
                        here_ok.add(name)
                        if a.apply: here_ok_add(name)
                    else:
                        here_nogo.add(name)
                        if a.apply: here_nogo_add_ttl(name)   # intl → cu dată, ca să EXPIRE (nu condamna pe veci)
                        hard += 1; bad_addr.append(name); continue
            else:
                ast = o.get("addressStatus")
                do_awb = ast in ("VALID", "PERFECT")
                if do_awb:
                    ready += 1
                else:
                    # DECIZIE O SINGURĂ DATĂ per comandă (evit bucla cu corecția async Frisbo/xConnector + validatorul
                    # RO ~517ms re-rulat pe tot backlog-ul blocat la fiecare tură). Ambele cache citite doar aici (WRONG):
                    if name in here_nogo:
                        hard += 1; bad_addr.append(name); continue   # nomenclator+HERE au picat-o → HOLD (bad-address)
                    if name in here_ok:
                        do_awb = True; here_ready += 1                 # deja VALIDATĂ → doar fac AWB, NU re-validez
                    else:
                        # STRAT 1 = NOMENCLATOR RO (metrics romania_addresses, v8.3.1 portat). Determinist, nu fabrică:
                        # completez strada din ZIP DOAR când e gunoi, altfel corectez ZIP-ul (invers) — nicio stradă reală
                        # suprascrisă. Înlocuiește stratul xConnector-RO (măsurat 0% util pe RO — redundant, supra-respinge).
                        nst, _, _ = nomenclator_correct(xc, o, sh["shopDomain"], metrics_cursor_live(), apply=False)
                        if nst == "valid":
                            do_awb = True; here_ready += 1             # bună pe nomenclator (rural/sector) → AWB as-is
                            here_ok.add(name)
                            if a.apply: here_ok_add(name)
                        elif nst == "would-correct":
                            fixable += 1
                            if a.apply:
                                st2, _, _ = nomenclator_correct(xc, o, sh["shopDomain"], metrics_cursor_live(), apply=True)
                                if st2 == "corrected":
                                    fixed += 1; do_awb = True
                                    here_ok.add(name); here_ok_add(name)
                                else:
                                    # scrierea corecției a eșuat la xConnector → nu buclez: la CS
                                    here_nogo.add(name); here_nogo_add_ttl(name); hard += 1; bad_addr.append(name); continue
                        else:
                            # STRAT 2 = HERE PESTE (needs-geocoder/cs): a-2-a opinie ~173ms — nomenclatorul supra-respinge
                            # golurile lui (străzi/localități noi). HERE ≥0.9 → expediez AS-IS + NOTEZ, altfel CS.
                            ad = xc.by_id(o.get("orderId")).get("shippingAddress") or {}
                            # STRAT 2b: zip LIPSA dar HERE geocodeaza strada reala → completez zip-ul din
                            # HERE (nu ating strada) ca DPD sa accepte eticheta. Deblocheaza adrese fara cod postal.
                            _hzip = here_zip_fill(ad, hkey)
                            if _hzip:
                                if a.apply:
                                    intl_correct_write(xc, o, sh["shopDomain"], dict(ad, country="Romania", **_hzip))
                                do_awb = True; here_ready += 1
                                here_ok.add(name)
                                if a.apply: here_ok_add(name)
                            elif here_validate(ad, "ROU", hkey) >= HERE_MIN_SCORE:
                                do_awb = True; here_ready += 1
                                here_ok.add(name)
                                if a.apply: here_ok_add(name)          # NOTEZ „validat" → nu re-validez tura viitoare
                            else:
                                here_nogo.add(name)
                                if a.apply: here_nogo_add_ttl(name)    # nomenclator+HERE au picat → CS; TTL → re-verific peste HERE_NOGO_TTL_DAYS
                                hard += 1; bad_addr.append(name); continue
            if a.apply and do_awb:
                # GARDĂ DE TURĂ: comenzile plasate de CS / draft / keeper confirmat sunt scutite (au voie
                # să semene cu o comandă veche a clientului — replasare COD, swap, resend). Pentru rest,
                # dacă (client + produse) a primit DEJA AWB în ACEASTĂ tură → e dublură pe care AWBprint
                # n-a apucat s-o arate → NU fac AWB (o las la CS; NU anulez, poate fi comandă reală).
                _exempt = any(t in CS_AGENT_TAGS for t in tags) or source == "shopify_draft_order" or DUP_OK in tags
                _ckey = (cust or "").strip()
                _skus = frozenset()
                if not _exempt:
                    _ident, _skus = awbprint_identity(name)
                    _ckey = _ckey or _ident
                    _runkey = (sh["shopDomain"], _ckey, _skus)
                    if _ckey and _skus and _runkey in made_ident:
                        dup_untag += 1
                        awb_event(kind="dup-in-run", store=sh["shopDomain"], order=name, result="blocked")
                        print("    ⚠️ %s = DUPLICAT în aceeași tură (același client + produse tocmai expediate) → NU fac AWB (la CS)" % name)
                        continue
                ok, perm, hreason = _do_awb(xc, sh, st, cons, con, name, o, a.notify)
                if ok:
                    made += 1
                    if not _exempt and _ckey and _skus:
                        made_ident.add((sh["shopDomain"], _ckey, _skus))
                else:
                    failed += 1
                    if perm:
                        bad_addr.append(name)   # curier respins PERMANENT → HOLD (nu mai reîncerca la fiecare tură)
                        if hreason: awb_fail_reason[name] = hreason
        for bn in set(bad_addr):
            hold_and_log(st, sh["shopDomain"], bn, awb_fail_reason.get(bn, "bad-address"), a.apply)
            if a.apply: cron_held_add(bn)
        print("  %s — unfulfilled >%dmin: AWB %d gata + %d via-HERE + %d corectabile + %d grele→CS  ·  DUP: %d păstrate, %d de-anulat(identice), %d diferite→HOLD, %d plecate(protejate), %d fără-client, %d netag-uite→CS  ·  CS/draft (AWB fără dedup): %d · influencer-skip: %d · blocklist: %d  (aveau AWB %d, fără xc %d, deja-expediat-fulfillment-anulat %d)"
              % (sh["shopDomain"], max_age, ready, here_ready, fixable, hard, dup_keep, dup_cancel, dup_hold, dup_shipped, dup_unknown, dup_untag, team_n, infl, blocked, had_awb, noxc, already_shipped_n))
        if held_n:
            print("  ⏸️ %d magazii Grandia pe HOLD" % held_n)
        if swap_n:
            print("  ⏭️ %d swap ignorate" % swap_n)
        if bad_addr:
            print("  ⏸️ %d bad-address pe HOLD (grele + curier-respins)" % len(set(bad_addr)))
        if a.apply:
            print("  → APLICAT: AWB %d (din care %d după corecție) · duplicate anulate %d (identice) · duplicate pe HOLD %d · eșuate %d" % (made, fixed, dup_cancel, dup_hold, failed))
        else:
            print("  → [DRY-RUN] AWB la %d gata + %d via-HERE (RO as-is) + până la %d corectabile · aș anula %d duplicate identice · %d duplicate diferite→HOLD · %d → CS" % (ready, here_ready, fixable, dup_cancel, dup_hold, hard))
    if _use_rot and not _stopped_early:
        save_fulfill_cursor(0)   # am parcurs TOATE magazinele în buget → resetez cursorul la început


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


def shopify_remove_tags(shop, token, gid, tags):
    d = shopify_gql(shop, token, 'mutation($id:ID!,$t:[String!]!){ tagsRemove(id:$id, tags:$t){ userErrors{ field message } } }', {"id": gid, "t": tags})
    r = ((d.get("data") or {}).get("tagsRemove")) or {}
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


# ── Imprimantă „aleasă o dată, ținută minte" ────────────────────────────────
# Config LOCAL pe mașină (imprimanta e specifică mașinii din depozit, NU în KB partajat).
PRINTER_CFG = os.path.join(os.path.expanduser("~"), ".arona_printbatch.json")

def _load_saved_printer():
    try:
        with open(PRINTER_CFG, encoding="utf-8") as f:
            return (json.load(f) or {}).get("printer") or None
    except Exception:
        return None

def _save_printer(name):
    try:
        with open(PRINTER_CFG, "w", encoding="utf-8") as f:
            json.dump({"printer": name}, f)
    except Exception:
        pass

def _list_printers():
    """(names, default_name) pe platforma curentă. ([], None) dacă nu pot lista."""
    try:
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-Printer | Select-Object -ExpandProperty Name"],
                                 capture_output=True, text=True, timeout=25).stdout
            names = [l.strip() for l in out.splitlines() if l.strip()]
            dflt = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "(Get-CimInstance Win32_Printer -Filter 'Default=True').Name"],
                                  capture_output=True, text=True, timeout=25).stdout.strip()
            return names, (dflt or None)
        out = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=25).stdout
        names = [l.split()[1] for l in out.splitlines() if l.startswith("printer ")]
        d = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=25).stdout.strip()
        return names, (d.split(":")[-1].strip() if ":" in d else None)
    except Exception:
        return [], None

def _pick_printer_interactive():
    """Listează imprimantele și lasă operatorul să aleagă UNA (o dată) → o salvează. None ⇒ cade pe dialog."""
    if not sys.stdin.isatty():
        return None
    names, dflt = _list_printers()
    if not names:
        print("  ⚠️ N-am putut lista imprimantele — cade pe dialogul normal (Ctrl+P).")
        return None
    print("  🖨️  Alege imprimanta (o dată — o țin minte data viitoare):")
    for i, n in enumerate(names, 1):
        print("      %2d) %s%s" % (i, n, "   [default]" if n == dflt else ""))
    print("       0) fără imprimantă fixă — deschide dialogul normal (Chrome/Ctrl+P)")
    try:
        raw = input("     Nr [%s]: " % ("Enter=default" if dflt else "1")).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw == "0":
        return None
    if not raw:
        chosen = dflt or names[0]
    elif raw.isdigit() and 1 <= int(raw) <= len(names):
        chosen = names[int(raw) - 1]
    elif raw in names:
        chosen = raw
    else:
        print("  ⚠️ Alegere invalidă — dialog normal."); return None
    _save_printer(chosen)
    print("  ✅ Reținut «%s» → data viitoare merge DIRECT în coadă (schimbi cu --choose-printer / --printer NAME)." % chosen)
    return chosen

def _resolve_printer(a):
    """Ce imprimantă folosim: --print-dialog⇒None · --printer NAME (salvează) · --choose-printer (re-alege) ·
    salvat (validat) · prima-oară te întreabă. None ⇒ comportament vechi (dialog/Chrome)."""
    if getattr(a, "force_dialog", False):
        return None
    name = getattr(a, "printer", None)
    if name:
        _save_printer(name)      # alegerea explicită devine implicita de data viitoare
        return name
    if getattr(a, "choose_printer", False):
        return _pick_printer_interactive()
    saved = _load_saved_printer()
    if saved:
        names, _ = _list_printers()
        if names and saved not in names:
            print("  ⚠️ Imprimanta reținută «%s» nu mai există — alege din nou." % saved)
            return _pick_printer_interactive()
        return saved
    return _pick_printer_interactive()

def _silent_print(path, printer):
    """Trimite PDF-ul DIRECT în coada imprimantei date (fără dialog). True = trimis.
    Cascadă Windows (fără să obligăm la vreo instalare): SumatraPDF (silent, oricare imprimantă) →
    verbul shell „printto" (Edge/Adobe, oricare) → verbul „print" (doar imprimanta DEFAULT, zero-install)."""
    try:
        import shutil
        if os.name == "nt":
            sumatra = next((p for p in (shutil.which("SumatraPDF"), shutil.which("SumatraPDF.exe"),
                                        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                                        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
                                        os.path.expandvars(r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe"))
                            if p and os.path.exists(p)), None)
            if sumatra:
                subprocess.Popen([sumatra, "-print-to", printer, "-silent", path])
                return True
            try:                        # verbul shell „printto" (Edge/Adobe), Python 3.10+ acceptă argumente
                os.startfile(path, "printto", '"%s"' % printer)
                return True
            except (TypeError, OSError):
                pass
            _, dflt = _list_printers()  # ultim fallback zero-install: dacă e chiar imprimanta DEFAULT → verbul „print"
            if dflt and printer == dflt:
                try:
                    os.startfile(path, "print")
                    return True
                except Exception:
                    return False
            return False
        if shutil.which("lp"):          # macOS/Linux CUPS
            subprocess.run(["lp", "-d", printer, path], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return False
    except Exception:
        return False

def _print_dialog(path, printer=None):
    """`printer` dat ⇒ trimite DIRECT în coadă (silent). Altfel deschide dialogul:
    Windows: SumatraPDF `-print-dialog` dacă există → altfel CHROME (operatorul apasă Ctrl+P).
    macOS: Preview + Cmd+P. Linux: xdg-open."""
    if printer:
        if _silent_print(path, printer):
            print("  🖨️  → trimis DIRECT în coada imprimantei «%s»." % printer)
            return
        print("  ⚠️ N-am putut trimite direct pe «%s» (instalează SumatraPDF pt print silent) — deschid dialogul." % printer)
    if os.name == "nt":   # Windows (depozit)
        import shutil
        sumatra = next((p for p in (shutil.which("SumatraPDF"), shutil.which("SumatraPDF.exe"),
                                    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
                                    os.path.expandvars(r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe"))
                        if p and os.path.exists(p)), None)
        if sumatra:
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


def _olines_from_dto(dto_info):
    """MODE A: construiește {orderName: [(sku, qty)]} din câmpurile DTO xConnector (skus + totalItemsCount +
    lineItemsCount, #2140) — FĂRĂ Shopify. Qty exactă când comanda are 1 SKU (=totalItemsCount); la multi-SKU
    qty per-sku nu e în DTO → 1/sku (unitățile totale reale rămân în dto_units pt complexity-split)."""
    out = {}
    for nm, d in dto_info.items():
        skus = [s for s in (d.get("skus") or []) if s]
        total = d.get("total")
        if len(skus) == 1 and total:
            out[nm] = [(skus[0], int(total))]
        else:
            out[nm] = [(s, 1) for s in skus]
    return out


def _dl_merge_batch(pending, outdir, ts, suffix=""):
    """Descarcă etichetele din `pending` (retry 3×), le îmbină într-un batch PDF (batch_<ts><suffix>.pdf) + log CSV,
    șterge individualele după merge. Întoarce (merged_path|None, n_ok, failed_list, log_path)."""
    import time as _time, csv, datetime as _dt
    pdfs, log_rows, failed = [], [], []
    for dom, nm, cid, trk, url, auth in pending:
        b, err = None, None
        for attempt in range(3):   # retry pe blip de rețea — NU pierdem tăcut eticheta unui client
            try:
                req = urllib.request.Request(url, headers=({"Authorization": auth} if auth else {}))
                with urllib.request.urlopen(req, timeout=45) as r:
                    data = r.read()
                if data[:5] != b"%PDF-":
                    err = "răspuns non-PDF"; break
                b, err = data, None; break
            except Exception as e:
                err = str(e)[:80]
                if attempt < 2:
                    _time.sleep(1.5 * (attempt + 1))
        if b is None:
            failed.append((nm, err or "necunoscut")); continue
        try:
            fp = os.path.join(outdir, "%s_%s.pdf" % (nm, trk or "noawb"))
            with open(fp, "wb") as f:
                f.write(b)
            if os.path.getsize(fp) < 100:
                failed.append((nm, "fișier gol")); continue
            pdfs.append(fp)
            log_rows.append([_dt.datetime.now().isoformat(timespec="seconds"), dom, nm, trk, cid, fp])
        except Exception as e:
            failed.append((nm, str(e)[:80]))
    merged = os.path.join(outdir, "batch_%s%s.pdf" % (ts, suffix))
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        for fp in pdfs:
            w.append(fp)
        with open(merged, "wb") as f:
            w.write(f)
        w.close()
        for fp in pdfs:   # individualele (AWB cu AWB) = intermediare → rămâne DOAR batch-ul grupat
            try:
                os.remove(fp)
            except Exception:
                pass
    except Exception as e:
        merged = None
        print("  (merge PDF indisponibil: %s — păstrez individualele în %s)" % (str(e)[:50], outdir))
    logp = os.path.join(outdir, "batch_%s%s.csv" % (ts, suffix))
    with open(logp, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["downloaded_at", "shop", "order", "awb", "connectorId", "file"])
        wr.writerows(log_rows)
    return merged, len(pdfs), failed, logp


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
    dto_info = {}   # MODE A: skus+totalItemsCount direct din DTO xConnector (#2140) → fără Shopify
    for sh in load_shops():
        if skip_shop(sh, a):   # suportă --shop listă/prefix (magazine la un loc) + --exclude
            continue
        xc = XC(sh["apiKey"])
        for o in xc.orders(dfrom, dto, flt):
            doc = awb_doc(o)
            if not doc or doc.get("downloaded") is not target_dl or not doc.get("url"):
                continue
            nm = o.get("orderName")
            pending.append((sh["shopDomain"], nm, doc.get("connectorId"),
                            doc_tracking(doc), doc.get("url"), xc.h.get("Authorization", "")))
            if o.get("skus") is not None:   # DTO expune SKU-urile (#2140 merge-uit)
                dto_info[nm] = {"skus": o.get("skus") or [], "total": o.get("totalItemsCount"), "lines": o.get("lineItemsCount")}
    # SKU+cantitate per comandă — pt filtrare (--sku-prefix), numărare (--by-sku) și SORTAREA PDF-ului.
    # MODE A (DTO xConnector are skus) → construiesc din DTO, ZERO Shopify. MODE B → pending_order_lines (Shopify).
    need = bool(pending and (getattr(a, "sku_prefix", None) or getattr(a, "by_sku", False) or a.apply))
    dto_units = {}
    if need and dto_info and len(dto_info) >= 0.5 * len(pending):
        olines = _olines_from_dto(dto_info)
        dto_units = {nm: d["total"] for nm, d in dto_info.items() if d.get("total") is not None}
        print("  ⚡ MODE A — SKU+cantitate direct din DTO xConnector (fără rezolvare Shopify, %d comenzi)" % len(dto_info))
    elif need:
        olines = pending_order_lines(pending)
    else:
        olines = {}
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
    # complexity-split cere nr PRODUSE FIZICE. `totalItemsCount` din DTO numără și articolele FĂRĂ SKU
    # (protecție colet/Releaseit) → ar clasa greșit HA-0001×1 + protecție drept „multi". Dacă am filtrat cu MODE A,
    # re-rezolv DOAR pending-ul filtrat (mic, ~sute) din Shopify → unități fizice exacte. (Filtrul a rulat deja rapid pe DTO.)
    if getattr(a, "complexity_split", False) and dto_units and pending:
        olines = pending_order_lines(pending)
        dto_units = {}
        print("  ↳ complexity-split: nr PRODUSE FIZICE din Shopify (%d comenzi filtrate) — DTO numără și articolele fără SKU" % len(pending))
    pending.sort(key=lambda r: (r[0],) + order_group_key(r[1], olines, a) + (r[1] or "",))
    # complexity-split: separă comenzile cu 1 PRODUS (mono) de cele cu MAI MULTE (multi) — ca la pick&pack.
    # „unități/comandă" = Σ cantitate pe toate liniile (din olines). Fără olines → nu pot împărți.
    if getattr(a, "complexity_split", False) and olines:
        def _units(nm):
            if dto_units.get(nm) is not None:   # MODE A: totalItemsCount exact din DTO
                return dto_units[nm]
            return sum(q for _, q in olines.get(nm, [])) or 1
        buckets = [("_mono", "1 PRODUS (mono)", [r for r in pending if _units(r[1]) == 1]),
                   ("_multi", "MAI MULTE PRODUSE (multi)", [r for r in pending if _units(r[1]) > 1])]
        buckets = [b for b in buckets if b[2]]
        if not buckets:
            print("  Nimic de printat."); return
    else:
        buckets = [("", None, pending)]
    lbl = {k: v for k, v in flt.items() if k not in ("sort", "sortDir")}
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    outdir = os.path.join(os.path.expanduser(getattr(a, "out", None) or "~/Downloads"), "print-batch")   # implicit în Downloads (ușor de găsit la print); override cu --out
    print("═" * 60)
    if test:
        print("  🧪 TEST — etichete DEJA descărcate (downloaded=true), NU ating coada reală de print.")
    elif reprint:
        print("  🔁 RE-PRINT — etichete DEJA printate (downloaded=true). Le re-descarc pt re-printare.")
    if len(buckets) > 1:  # numărătoarea „câte-s de fiecare fel", vizibilă din start
        print("  🔀 COMPLEXITY-SPLIT — %s (PDF-uri separate)"
              % " · ".join("%s: %d comenzi" % (bl, len(bp)) for _, bl, bp in buckets))
    targets = []
    from collections import Counter
    for suffix, blabel, bpending in buckets:
        total_b = len(bpending)
        lim = a.limit if getattr(a, "limit", None) else 250   # MAX 250 AWB/batch — restul, la rularea următoare
        skip = max(0, getattr(a, "offset", 0) or 0)           # paginare batch-cu-batch (re-print)
        cur = bpending[skip:skip + lim]
        remaining = total_b - skip - len(cur)
        print("  " + ("── %s " % blabel if blabel else "PRINT BATCH ") + "─" * 6)
        print("     %d etichete %s%s · %s→%s%s%s · grupat MAGAZIN→SKU→CANTITATE"
              % (len(cur), "DEJA descărcate (test)" if test else ("DEJA printate (re-print)" if reprint else "nedescărcate"),
                 ((" [%d–%d] din %d%s" % (skip + 1, skip + len(cur), total_b,
                   (" · rest %d → --offset %d" % (remaining, skip + len(cur))) if remaining else "")) if (skip or remaining) else ""),
                 dfrom, dto,
                 (" · magazine: " + ",".join(wants)) if wants else " · toate magazinele",
                 (" · " + json.dumps(lbl)) if lbl else ""))
        if olines:
            groups = Counter((r[0],) + order_group_key(r[1], olines, a) for r in cur)
            last_dom, shown = None, 0
            for (dom, sk, q), n in sorted(groups.items()):
                if shown >= 40:
                    print("       … +%d grupuri" % (len(groups) - shown)); break
                if dom != last_dom:
                    print("    ── %s" % dom); last_dom = dom; shown += 1
                print("       %-16s ×%-3d  %d etichete" % (sk, q, n)); shown += 1
        else:
            for dom, nm, cid, trk, _, _ in cur[:40]:
                print("    %-12s %-11s AWB %s" % (nm, dom, trk or "—"))
            if len(cur) > 40:
                print("    … +%d" % (len(cur) - 40))
        if a.apply and cur:
            os.makedirs(outdir, exist_ok=True)
            merged, nok, failed, logp = _dl_merge_batch(cur, outdir, ts, suffix)
            print("     ✅ descărcate %d · eșuate %d · log %s" % (nok, len(failed), logp))
            if merged:
                print("     📄 batch: %s" % merged); targets.append(merged)
            for nm, why in failed[:15]:
                print("        ⚠️ %s — %s" % (nm, why))
    if not a.apply:
        print("  → [DRY-RUN] atâtea sunt de printat%s. Adaugă --apply ca să descarci + trimiți în coada imprimantei (prima oară o alegi, apoi o ține minte)."
              % (" (câte un PDF per bucket)" if len(buckets) > 1 else ""))
        if not test:
            print("  ⚠️ --apply MARCHEAZĂ etichetele `downloaded` (ies din coada de print) — DOAR când chiar printezi.")
        return
    if not targets:
        print("  Nimic de printat."); return
    if getattr(a, "no_print", False):
        return
    printer = _resolve_printer(a)      # alege o dată → reține; None ⇒ dialog normal
    if printer:
        for t in targets:              # silent ⇒ trimit TOATE batch-urile (mono+multi) direct în coadă
            _print_dialog(t, printer)
    else:
        _print_dialog(targets[0], None)
        if len(targets) > 1:
            print("  ℹ️ %d batch-uri (mono+multi): am deschis primul; deschide-le pe rând pt print." % len(targets))


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
    ap.add_argument("--max-run-min", type=int, dest="max_run_min", help="fulfill: buget de timp/rulare în minute. Peste el se oprește curat și SALVEAZĂ cursorul → tura viitoare CONTINUĂ cu magazinele rămase (round-robin). Fără el = fără limită. Ignorat cu --shop.")
    ap.add_argument("--held-sweep-hours", type=int, default=HELD_SWEEP_DEFAULT_H, dest="held_sweep_hours", help="fulfill: la câte ore/magazin trece peste comenzile pe HOLD (bad-address/awb-esec) puse de cron → le eliberează pe cele devenite livrabile, ca regulile noi să le deblocheze. Default 6.")
    ap.add_argument("--no-held-sweep", action="store_true", dest="no_held_sweep", help="fulfill: dezactivează sweep-ul peste comenzile pe hold.")
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
    ap.add_argument("--out", help="print-batch: folderul unde salvez PDF-urile + log (default: ~/Downloads/print-batch).")
    ap.add_argument("--no-print", action="store_true", dest="no_print", help="print-batch: NU deschide dialogul de print (doar salvează/merge).")
    ap.add_argument("--test", action="store_true", help="print-batch: TEST pe etichete DEJA descărcate (downloaded=true) — zero impact pe coada reală.")
    ap.add_argument("--printed", action="store_true", help="print-batch: RE-PRINT pe etichete DEJA printate (downloaded=true) — re-printare reală a unor AWB-uri deja descărcate.")
    ap.add_argument("--by-sku", action="store_true", dest="by_sku", help="print-batch: NU printează — arată coada GRUPATĂ pe SKU (câte etichete/SKU), cele mai multe primele, ca să alegi ce produs printezi.")
    ap.add_argument("--sku-prefix", dest="sku_prefix", help="print-batch: păstrează DOAR comenzile care au un SKU pe prefixul dat (ex `HA` = toate comenzile cu produse HA-*).")
    ap.add_argument("--complexity-split", action="store_true", dest="complexity_split", help="print-batch: separă comenzile cu 1 PRODUS (mono) de cele cu MAI MULTE (multi) — PDF-uri + loguri separate (batch_<ts>_mono/_multi), ca la pick&pack.")
    ap.add_argument("--limit", type=int, help="print-batch: max AWB-uri/batch (implicit 250). Restul rămâne pt rularea următoare.")
    ap.add_argument("--offset", type=int, default=0, help="print-batch: sare primele N etichete (paginare batch-cu-batch la RE-PRINT, ex --offset 250 = batch 2). În producție (downloaded=false) nu e nevoie — fiecare batch iese din coadă.")
    ap.add_argument("--printer", help="print-batch: trimite DIRECT în coada imprimantei date (fără dialog) + o REȚINE ca implicită. Fără el, prima oară te întreabă și ține minte alegerea.")
    ap.add_argument("--choose-printer", action="store_true", dest="choose_printer", help="print-batch: re-alege imprimanta (listează + salvează din nou), chiar dacă e una reținută.")
    ap.add_argument("--print-dialog", action="store_true", dest="force_dialog", help="print-batch: ignoră imprimanta reținută și deschide dialogul normal (Chrome/Ctrl+P) o singură dată.")
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
