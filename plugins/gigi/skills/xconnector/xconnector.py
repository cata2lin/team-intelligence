# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
xconnector.py — punte READ-ONLY spre xConnector (curierat) pt fluxul ARONA.

Ce poate AZI (prin cheia API, durabil):
  • address-issues — comenzile NEPORNITE (fără AWB) cu adresă WRONG/UNKNOWN la xConnector,
    cu adresa curentă + ce zice validatorul (candidat + scor) + verdict auto/manual.
    = semnal de „confirmă/corectează adresa ÎNAINTE de AWB" (prevenție refuzuri), pereche cu gigi:cs-address-guard.
  • summary — câte comenzi pe fiecare status, câte fără AWB, per magazin.

Ce NU se poate (încă) prin cheia API: creare AWB / dispatch / facturi — alea-s pe dashboard-ul xConnector
(cookie+CSRF), cheia API dă 403. Când xConnector le expune în API (sau activează /mcp), adăugăm aici.

Auth: cheia API xConnector per magazin. Sursă (în ordine): secret KB `XCONNECTOR_SHOPS` (JSON
[{shopDomain,apiKey}]), altfel `~/.aac/input.json`. NICIODATĂ printată.

  uv run xconnector.py summary
  uv run xconnector.py address-issues [--shop ix5bxc-hr.myshopify.com] [--days 60] [--json]
Read-only. Nu scrie nimic în xConnector.
"""
import os, sys, json, re, time, hashlib, argparse, subprocess, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
XBASE = "https://xconnector.app"
VBASE = "https://address-validator.xconnector.app"


def load_shops():
    """[{shopDomain, apiKey}] din KB (XCONNECTOR_SHOPS) sau ~/.aac/input.json. Secret — nu se printează."""
    raw = os.environ.get("XCONNECTOR_SHOPS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "XCONNECTOR_SHOPS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
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

    def orders(self, dfrom, dto):
        """toate comenzile în fereastră (paginat), cu addressStatus + documents."""
        out, seen = [], set()
        for page in range(0, 12):
            s, d = self.get("/api/orders", "fromOrderDate=%s&toOrderDate=%s&page=%d&size=200" % (dfrom, dto, page))
            if s != 200:
                break
            arr = d if isinstance(d, list) else (d.get("content") or d.get("orders") or [])
            if not arr:
                break
            for o in arr:
                oid = o.get("orderId")
                if oid not in seen:
                    seen.add(oid); out.append(o)
            if len(arr) < 200:
                break
        return out

    def by_id(self, oid):
        s, d = self.get("/api/orders/by-id", "orderId=%s" % oid)
        return d if s == 200 and isinstance(d, dict) else {}

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


# ── Shopify Admin (pt declanșat Shopify Flow prin tag = create/cancel AWB via xConnector) ──
SHOPIFY_API = "2026-04"
AWB_TAGS = {"create": "xc-create-awb", "cancel": "xc-cancel-awb"}


def load_shopify_tokens():
    """[{prefix, shopDomain, adminToken}] din KB SHOPIFY_ADMIN_TOKENS (sau env)."""
    raw = os.environ.get("SHOPIFY_ADMIN_TOKENS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_ADMIN_TOKENS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
    try:
        return json.loads(raw) if raw.startswith("[") else []
    except Exception:
        return []


def shopify_gql(shop, token, query):
    url = "https://%s/admin/api/%s/graphql.json" % (shop, SHOPIFY_API)
    h = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    s, b = http("POST", url, h, {"query": query})
    try:
        return json.loads(b)
    except Exception:
        return {"_status": s, "_raw": b[:200]}


def find_order(shop, token, name):
    """order GID + tags curente, după orderName (ex GT44004)."""
    q = 'query{ orders(first:1, query:"name:%s"){ edges{ node{ id name tags displayFulfillmentStatus } } } }' % name.replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return (edges[0]["node"] if edges else None)


def cmd_awb(a):
    """tag-uiește comanda → declanșează Shopify Flow care cheamă acțiunea xConnector (create/cancel AWB)."""
    action = a.cmd.split("-")[1]  # create | cancel
    tag = AWB_TAGS[action]
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    pref = re.match(r"^([A-Za-z]+)", a.order)
    pref = pref.group(1).upper() if pref else ""
    sh = toks.get(pref)
    if not sh:
        print("Niciun token Shopify pt prefixul '%s' (am: %s). Adaugă-l în KB SHOPIFY_ADMIN_TOKENS." % (pref, list(toks))); return
    node = find_order(sh["shopDomain"], sh["adminToken"], a.order)
    if not node:
        print("Comanda %s negăsită în Shopify (%s)." % (a.order, sh["shopDomain"])); return
    cur = node.get("tags") or []
    print("Comandă %s | fulfillment: %s | taguri curente: %s" % (a.order, node.get("displayFulfillmentStatus"), cur))
    if tag in cur:
        print("  Tagul '%s' există deja → Flow-ul a fost deja declanșat. Nimic de făcut." % tag); return
    if not a.apply:
        print("  DRY-RUN: aș adăuga tagul '%s' → ar declanșa Flow-ul de %s AWB." % (tag, action))
        print("  → rulează cu --apply ca să tag-uiești (asigură-te că Flow-ul Shopify e configurat pe acest tag).")
        return
    m = 'mutation{ tagsAdd(id:"%s", tags:["%s"]){ node{id} userErrors{field message} } }' % (node["id"], tag)
    d = shopify_gql(sh["shopDomain"], sh["adminToken"], m)
    errs = (((d.get("data") or {}).get("tagsAdd") or {}).get("userErrors")) or d.get("errors")
    if errs:
        print("  ⚠️ eroare la tagsAdd:", errs)
    else:
        print("  ✅ tag '%s' adăugat pe %s → Flow-ul de %s AWB declanșat." % (tag, a.order, action))


def has_awb(o):
    return any((d.get("documentType") == "SHIPPING_LABEL") for d in (o.get("documents") or []) if isinstance(d, dict))


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


def cmd_summary(shops, a):
    for sh in shops:
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
        if a.shop and sh["shopDomain"] != a.shop:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "address-issues", "awb-create", "awb-cancel"])
    ap.add_argument("--shop"); ap.add_argument("--order"); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("awb-create", "awb-cancel"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        cmd_awb(a); return
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
