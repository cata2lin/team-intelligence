#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg[binary]"]
# ///
"""
rp.py — CLI unificat Richpanel, în tiparul `gigi:xconnector`: citiri libere, scrieri
DRY-RUN by default (`--apply` execută). Zero tokeni LLM — pur JSON-RPC peste MCP.

  CITIRE
    uv run rp.py find --phone 0700000000          # client + comenzi + tichete
    uv run rp.py conv 312879                      # o conversație (id / nr / URL)
    uv run rp.py list --status OPEN --limit 20    # coada
    uv run rp.py agents | tags | teams

  ACȚIUNI (dry-run; adaugă --apply)
    uv run rp.py tag 312879 --add wismo --apply
    uv run rp.py close 312879 --apply
    uv run rp.py assign 312879 --to raluca@... --apply
    uv run rp.py note 312879 --body "sunat, refuză" --apply
    uv run rp.py snooze 312879 --until 2026-08-20 --apply
    uv run rp.py draft 312879 --body "Bună ziua, ..." --apply

  PUNTE AWBprint → fișa CS (Custom Connector; vezi memoria richpanel-connector-write-api)
    uv run rp.py push --order GT54203             # dry-run: arată exact ce s-ar scrie
    uv run rp.py push --order GT54203 --apply
    uv run rp.py push --since 2026-08-15 --limit 50 --apply
    uv run rp.py verify --email client@x.ro       # citește fișa înapoi (confirmare)

⚠️ `send_message` (răspuns LIVE la client) e BLOCAT intenționat — regula echipei e „mod
   testare: doar create_draft". Vezi memoria richpanel-tickets-access.
"""
import argparse, base64, hashlib, json, os, re, subprocess, sys, time, urllib.request, uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # depozitul/CS = Windows cp1252

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
CONNECTOR_URL = "https://api.richpanel.com/v3/t"
CRED_FILE = os.path.expanduser("~/Downloads/credentials/credentiale api richpanel.txt")
# casaofertelor.ro E magazinul „bonhaus" (shop=bonhaus.myshopify.com) — nu există bonhaus.ro separat.
# nocturna.bg și cepatai.ro sunt ÎNCHISE. duppo.md și oriceredus.ro: n-au widget, nu le punem încă.
DEFAULT_DOMAINS = [
    "esteban.ro", "george-talent.ro", "nubra.ro", "grandia.ro", "magdeal.ro", "nocturna.ro",
    "nocturnalux.ro", "ofertelezilei.ro", "reduceribune.ro", "bonhaus.cz", "bonhaus.pl",
    "bonhaus.bg", "belasil.ro", "gento.ro", "carpetto.ro", "covoria.ro", "rossinails.ro",
    "apreciat.ro", "labnoir.ro", "casaofertelor.ro",
]

TRACK_URL = {
    "DPD": "https://tracking.dpd.ro/?shipmentNumber={awb}",
    "Sameday": "https://sameday.ro/#awb={awb}",
    "fan_courier_ro": "https://www.fancourier.ro/awb-tracking/?awb={awb}",
}
# aggregated_status AWBprint -> ce vede agentul CS
STATUS_RO = {
    "delivered": "livrat", "in_transit": "în tranzit", "waiting_for_courier": "așteaptă curier",
    "back_to_sender": "retur la expeditor", "returning_to_sender": "se întoarce",
    "unsuccessful_delivery": "livrare eșuată", "refused": "REFUZAT", "cancelled": "anulat",
    "incorrect_address": "adresă greșită", "on_hold": "în așteptare",
    "deferred_delivery": "livrare amânată", "customer_pickup": "ridicare personală",
    "redirected": "redirecționat", "not_fulfilled": "neprocesat", "fulfilled": "procesat",
}

_cache = {}


def secret(name):
    if name in _cache:
        return _cache[name]
    v = os.environ.get(name)
    if not v:
        r = subprocess.run(["uv", "run", os.path.abspath(KB), "secret-get", name],
                           capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(KB)))
        if r.returncode != 0:
            sys.exit(f"kb.py secret-get {name}: {r.stderr[-300:]}")
        v = r.stdout.strip().splitlines()[-1].strip()
    _cache[name] = v
    return v


# ---------------------------------------------------------------- MCP JSON-RPC
class MCP:
    def __init__(self):
        self.tok = secret("RICHPANEL_MCP_TOKEN")
        self.sid = None
        self.n = 0
        self._init()

    def _post(self, body, tries=5):
        h = {"Authorization": f"Bearer {self.tok}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), headers=h)
        for i in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.sid = r.headers.get("Mcp-Session-Id") or self.sid
                    txt = r.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                # 429 = exportul/alt job consumă rata; 5xx = tranzitoriu. Backoff exponențial.
                if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                    raise
                time.sleep(2 ** i * 1.5)
            except (TimeoutError, OSError):
                if i == tries - 1:
                    raise
                time.sleep(2 ** i * 1.5)
        out = None
        for line in txt.splitlines():
            if line.startswith("data:"):
                out = json.loads(line[5:].strip())
        return out if out is not None else (json.loads(txt) if txt.strip() else None)

    def _init(self):
        self.n += 1
        self._post({"jsonrpc": "2.0", "id": self.n, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "rp-cli", "version": "1.0"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, tool, args=None):
        self.n += 1
        res = self._post({"jsonrpc": "2.0", "id": self.n, "method": "tools/call",
                          "params": {"name": tool, "arguments": args or {}}})
        if res is None:
            return None
        if "error" in res:
            raise RuntimeError(f"{tool}: {res['error']}")
        parts = []
        for c in res.get("result", {}).get("content", []):
            if c.get("type") == "text":
                try:
                    parts.append(json.loads(c["text"]))
                except Exception:
                    parts.append(c["text"])
        return parts[0] if len(parts) == 1 else parts


# ------------------------------------------------------------------- AWBprint
def awb_orders(where_sql, params):
    import psycopg
    # join pe `stores` ca să știm DOMENIUL magazinului -> cheia de connector corectă.
    # Fără asta am împinge datele unui magazin cu cheia altuia (contaminare între magazine).
    sql = f"""SELECT o.order_number, o.customer_email, o.customer_name, o.tracking_number,
                     o.courier_name, o.aggregated_status, o.total_price, o.currency,
                     o.payment_gateway, o.transport_cost, o.shipping_address, o.store_uid,
                     o.shopify_order_id, o.frisbo_created_at, s.name AS store_domain
              FROM orders o LEFT JOIN stores s ON s.uid = o.store_uid
              WHERE {where_sql}"""
    with psycopg.connect(secret("DATABASE_URL_AWBPRINT")) as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


SHOP_BY_DOMAIN = {}  # domeniu magazin -> <x>.myshopify.com, populat din connector_keys.json


def build_payload(o):
    """AWBprint -> properties pt evenimentul `order` (nume de câmp verificate empiric)."""
    if not SHOP_BY_DOMAIN:
        _load_shops()
    addr = o["shipping_address"] or {}
    if isinstance(addr, str):
        addr = json.loads(addr)
    awb, courier = o["tracking_number"], o["courier_name"]
    # address1/address2 se suprapun des în datele reale (clientul rescrie strada în al doilea
    # câmp, cu mici diferențe) — containment simplu NU prinde asta, deci compar pe TOKENI:
    # dacă ≥60% din cuvintele lui address2 sunt deja în address1, îl sar.
    def toks(s):
        return {t for t in re.split(r"[^\wăâîșțĂÂÎȘȚ]+", (s or "").lower()) if len(t) > 1}

    a1, a2 = (addr.get("address1") or "").strip(), (addr.get("address2") or "").strip()
    t1, t2 = toks(a1), toks(a2)
    if a2 and t2 and len(t2 & t1) / len(t2) >= 0.6:
        a2 = ""
    parts = [p.strip() for p in (a1, a2, addr.get("city"), addr.get("province"),
                                 addr.get("zip"), addr.get("country")) if (p or "").strip()]
    # ⚠️ `id` TREBUIE să fie partea numerică (ex NUBRA11203 -> "11203"), fiindcă exact așa scrie
    # integrarea Shopify în `lastOrderId`. Cu numărul complet, Richpanel vede o comandă NOUĂ
    # și incrementează ordersCount/LTV (dublare). Verificat pe clienți reali, 17-aug-2026.
    num = re.sub(r"^[A-Za-z]+", "", o["order_number"] or "")
    props = {
        "id": num or o["order_number"],
        "order_name": o["order_number"],                     # -> lastOrderNumber + orderIds
        "amount": f"{o['total_price']:.2f}" if o["total_price"] is not None else None,
        "status": STATUS_RO.get(o["aggregated_status"], o["aggregated_status"]),
        "shipping_address": ", ".join(p for p in parts if p),  # STRING, nu obiect
        "payment_method": o["payment_gateway"],
    }
    # ⚠️ OBLIGATORIU la backfill de istoric: Richpanel decide „ultima comandă" după DATA trimisă,
    # nu după ordinea de push (verificat 17-aug: o comandă veche împinsă ultima NU devine lastOrder).
    # Fără `created_at`, o comandă din 2025 ar putea ateriza ca „ultima" în fișa agentului.
    if o.get("frisbo_created_at"):
        props["created_at"] = int(o["frisbo_created_at"].timestamp() * 1000)
    if o["transport_cost"] is not None:
        props["shipping_amount"] = f"{o['transport_cost']:.2f}"
    if awb:
        track = TRACK_URL.get(courier, "").format(awb=awb) if courier in TRACK_URL else ""
        props["shipping_method"] = courier or ""
        props["fulfillments"] = [{k: v for k, v in {
            "tracking_number": awb, "tracking_company": courier,
            "tracking_url": track or None}.items() if v}]
    # `status_url` = singurul link apăsabil din fișă. NU pune tracking-ul aici (ăla stă în
    # `fulfillments.tracking_url`): agenții îl folosesc ca să sară ÎN COMANDĂ. Valoarea nativă era
    # pagina de status a clientului; noi punem linkul de ADMIN, care e ce-i trebuie agentului.
    # Construit, nu citit — `statusPageUrl` din Shopify cere aprobare de date personale.
    shop = (SHOP_BY_DOMAIN.get((o.get("store_domain") or "").lower()) or "")
    if shop and o.get("shopify_order_id"):
        props["status_url"] = (f"https://admin.shopify.com/store/"
                               f"{shop.replace('.myshopify.com', '')}/orders/{o['shopify_order_id']}")
    return {k: v for k, v in props.items() if v not in (None, "")}


KEYS_FILE = os.path.join(HERE, "connector_keys.json")
PUSH_LOG = os.path.join(HERE, "push_log.json")
_plog = {}


def _load_shops():
    """Mapare domeniu -> handle myshopify, cu aceleași 3 nivele de potrivire ca la chei
    ('nubra' în AWBprint vs 'nubra.ro' în widget)."""
    if not os.path.exists(KEYS_FILE):
        return
    keys = json.load(open(KEYS_FILE, encoding="utf-8"))
    stems = {}
    for d, v in keys.items():
        shop = v.get("shop") or ""
        SHOP_BY_DOMAIN[d.lower()] = shop
        SHOP_BY_DOMAIN.setdefault(d.lower().replace("-", ""), shop)
        stem = re.sub(r"\.(ro|bg|cz|pl|md|hu|sk|com)$", "", d.lower()).replace("-", "")
        stems.setdefault(stem, set()).add(shop)
    for stem, shops in stems.items():          # trunchi doar dacă e UNIC (bonhaus.cz/.pl/.bg!)
        if len(shops) == 1:
            SHOP_BY_DOMAIN.setdefault(stem, next(iter(shops)))


def _push_log():
    """Jurnal de reluare: order_number -> semnătura payload-ului. O rulare întreruptă se reia
    fără să retrimită ce n-a schimbat. (Push-ul e oricum idempotent — asta doar economisește timp.)"""
    global _plog
    if os.path.exists(PUSH_LOG):
        try:
            _plog = json.load(open(PUSH_LOG, encoding="utf-8"))
        except Exception:
            _plog = {}
    return _plog


def _push_log_set(order, sig, awb=None):
    _plog[order] = {"sig": sig, "awb": awb}
    if len(_plog) % 50 == 0:  # flush periodic, ca o întrerupere să nu piardă tot progresul
        json.dump(_plog, open(PUSH_LOG, "w", encoding="utf-8"))


def _log_entry(order):
    """Compatibil cu formatul vechi (order -> sig simplu) și cu cel nou (order -> {sig, awb})."""
    v = _plog.get(order)
    if isinstance(v, str):
        return {"sig": v, "awb": None}
    return v or {}
_KEY_PAT = re.compile(r"appClientId=([A-Za-z0-9_-]+)&tenantId=([A-Za-z0-9_-]+)&shop=([A-Za-z0-9.-]+)")


def harvest_keys(domains):
    """appClientId-ul fiecărui magazin e PUBLIC — stă în query string-ul scriptului de widget
    din propria pagină. Deci nu-l cerem nimănui, îl citim. Unele teme îl pun într-un blob JSON
    (URL escapat \\/ și \\u0026) → normalizăm înainte de match."""
    out = {}
    for d in domains:
        try:
            req = urllib.request.Request(f"https://{d}/", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            t = raw.decode("utf-8", "replace")
            t = t.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
            m = _KEY_PAT.search(t)
            if m:
                out[d] = {"appClientId": m.group(1), "tenantId": m.group(2), "shop": m.group(3)}
                print(f"  ✅ {d:20s} {m.group(1)}")
            else:
                print(f"  ⚠️  {d:20s} widget Richpanel absent")
        except Exception as e:
            print(f"  ❌ {d:20s} {type(e).__name__}")
    return out


def load_keys():
    if not os.path.exists(KEYS_FILE):
        sys.exit(f"lipsește {KEYS_FILE} — rulează întâi:  uv run rp.py keys --refresh")
    return json.load(open(KEYS_FILE, encoding="utf-8"))


def _index(keys):
    """Numele magazinului în AWBprint nu e mereu domeniul din widget: 'nubra' vs 'nubra.ro',
    'georgetalent.ro' vs 'george-talent.ro'. Construim 3 nivele de potrivire, de la strict la lax.
    Nivelul „trunchi" (fără TLD) se folosește DOAR dacă trunchiul e unic — altfel bonhaus.cz/.pl/.bg
    ar colapsa într-unul singur și am scrie datele unui magazin sub identitatea altuia."""
    exact, nohy, stems = {}, {}, {}
    for d, v in keys.items():
        k = v["appClientId"]
        exact[d.lower()] = k
        nohy[d.lower().replace("-", "")] = k
        stem = re.sub(r"\.(ro|bg|cz|pl|md|hu|sk|com)$", "", d.lower()).replace("-", "")
        stems.setdefault(stem, set()).add(k)
    stem_unique = {s: next(iter(ks)) for s, ks in stems.items() if len(ks) == 1}
    return exact, nohy, stem_unique


def key_for(store_domain, keys):
    """Cheia magazinului comenzii. Refuz explicit dacă nu-l știm — mai bine sărim comanda
    decât să scriem datele unui magazin sub identitatea altuia."""
    if not store_domain:
        return None
    exact, nohy, stem_unique = _index(keys)
    d = store_domain.strip().lower()
    stem = re.sub(r"\.(ro|bg|cz|pl|md|hu|sk|com)$", "", d).replace("-", "")
    return exact.get(d) or nohy.get(d.replace("-", "")) or stem_unique.get(stem)


def push(o, key, apply_):
    props = build_payload(o)
    addr = o["shipping_address"] or {}
    if isinstance(addr, str):
        addr = json.loads(addr)
    email = (o["customer_email"] or addr.get("email") or "").strip().lower()
    if not email:
        return "fără email", props
    up = {"uid": email, "email": email, "name": o["customer_name"] or addr.get("name") or ""}
    if addr.get("phone"):
        up["phone"] = addr["phone"]
    if not apply_:
        return "DRY-RUN", props
    data = {"event": "order", "properties": props, "userProperties": up,
            "context": {"timezone": "Europe/Bucharest"}, "appClientId": key,
            "did": str(uuid.uuid4()), "sid": str(uuid.uuid4()),
            "time": {"sentAt": int(time.time() * 1000)}, "version": "2.0.0.js",
            "eventId": str(uuid.uuid4())}
    body = json.dumps({"h": base64.b64encode(json.dumps(data).encode()).decode()}).encode()
    req = urllib.request.Request(CONNECTOR_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        code = r.status
    # ⚠️ 200 nu garantează ingestia (firehose Kinesis acceptă orice) — confirmă cu `rp.py verify`
    return f"trimis ({code})", props


# ----------------------------------------------------------------------- CLI
def _action(a):
    """Mapează subcomanda de acțiune -> (unealtă MCP, argumente). Folosit identic de
    dry-run și de execuție, ca dry-run-ul să arate EXACT ce s-ar trimite."""
    tool, args = {
        "tag": ("add_tags_to_conversation", {"conversation_id": a.ref}),
        "close": ("update_conversation_status", {"conversation_id": a.ref, "status": "CLOSED"}),
        "reopen": ("update_conversation_status", {"conversation_id": a.ref, "status": "OPEN"}),
        "assign": ("assign_conversation", {"conversation_id": a.ref}),
        "note": ("add_private_note", {"conversation_id": a.ref}),
        "snooze": ("snooze_conversation", {"conversation_id": a.ref}),
        "draft": ("create_draft", {"conversation_id": a.ref}),
    }[a.cmd]
    if a.cmd == "tag":
        if a.remove:
            tool = "remove_tags_from_conversation"
            args["tags"] = [t.strip() for t in a.remove.split(",") if t.strip()]
        else:
            args["tags"] = [t.strip() for t in (a.add or "").split(",") if t.strip()]
        if not args["tags"]:
            sys.exit("tag: dă --add sau --remove")
    for attr, key in (("to", "assignee"), ("body", "body"), ("until", "snoozed_till")):
        if getattr(a, attr, None):
            args[key] = getattr(a, attr)
    for need, cmd in (("body", "note"), ("body", "draft"), ("assignee", "assign"),
                      ("snoozed_till", "snooze")):
        if a.cmd == cmd and need not in args:
            sys.exit(f"{cmd}: lipsește --{'to' if need == 'assignee' else need.split('_')[0]}")
    return tool, args


def show(x):
    print(json.dumps(x, ensure_ascii=False, indent=2, default=str))


def main():
    p = argparse.ArgumentParser(description="Richpanel CLI (citiri libere; scrieri dry-run)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, *args, **kw):
        s = sub.add_parser(name, **kw)
        for a, k in args:
            s.add_argument(a, **k)
        return s

    f = sub.add_parser("find"); f.add_argument("--email"); f.add_argument("--phone")
    c = sub.add_parser("conv"); c.add_argument("ref")
    l = sub.add_parser("list")
    l.add_argument("--status", default="OPEN"); l.add_argument("--channel")
    l.add_argument("--from", dest="dfrom"); l.add_argument("--to", dest="dto")
    l.add_argument("--limit", type=int, default=25)
    sub.add_parser("agents"); sub.add_parser("tags"); sub.add_parser("teams")

    for name, extra in [("tag", [("--add",), ("--remove",)]), ("close", []), ("reopen", []),
                        ("assign", [("--to",)]), ("note", [("--body",)]),
                        ("snooze", [("--until",)]), ("draft", [("--body",)])]:
        s = sub.add_parser(name); s.add_argument("ref")
        for (a,) in extra:
            s.add_argument(a)
        s.add_argument("--apply", action="store_true")

    ps = sub.add_parser("push")
    ps.add_argument("--order"); ps.add_argument("--since"); ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--apply", action="store_true")
    ps.add_argument("--rate", type=float, default=2.0, help="cereri/secundă (implicit 2)")
    ks = sub.add_parser("keys")
    ks.add_argument("--refresh", action="store_true", help="re-culege cheile publice de pe site-uri")
    ks.add_argument("--domains", help="listă separată prin virgulă (implicit: toate magazinele)")
    v = sub.add_parser("verify"); v.add_argument("--email", required=True)

    a = p.parse_args()

    # push/verify nu au nevoie de MCP pt trimitere, dar verify da
    if a.cmd == "push":
        if a.order:
            rows = awb_orders("o.order_number = %s", (a.order,))
        elif a.since:
            # ⚠️ NU filtra pe tracking_number IS NOT NULL: când un AWB e ANULAT, AWBprint îi
            # șterge tracking-ul, iar comanda ar dispărea din lot — lăsând în fișa CS un AWB
            # mort pe care agentul l-ar citi ca valid. Trimițându-le fără `fulfillments`,
            # Richpanel golește tracking-ul (verificat) și rămâne statusul corect („anulat").
            rows = awb_orders(
                "o.frisbo_created_at >= %s AND o.customer_email IS NOT NULL"
                " AND o.customer_email <> ''"
                " ORDER BY o.frisbo_created_at DESC LIMIT %s",
                (a.since, a.limit))
        else:
            sys.exit("push: dă --order sau --since")
        if not rows:
            sys.exit("nicio comandă găsită")
        keys = load_keys()
        verbose = len(rows) <= 20
        print(f"{'DRY-RUN — nu se scrie nimic' if not a.apply else 'APLIC'} · {len(rows)} comenzi"
              f" · ritm {a.rate}/s\n")
        log = _push_log()
        n_ok = n_skip = n_same = n_err = n_awb = 0
        awb_changes = []
        t0 = time.time()
        for i, o in enumerate(rows, 1):
            key = key_for(o.get("store_domain"), keys)
            if not key:
                n_skip += 1
                if verbose:
                    print(f"  {o['order_number']:<12} ⏭️  SĂRIT — magazin necunoscut "
                          f"({o.get('store_domain') or 'fără domeniu'})")
                continue
            props = build_payload(o)
            sig = hashlib.sha1(json.dumps(props, sort_keys=True).encode()).hexdigest()[:12]
            prev = _log_entry(o["order_number"])
            if a.apply and prev.get("sig") == sig:
                n_same += 1  # deja împins cu EXACT aceleași date → nu mai batem serverul
                continue
            awb_now = o["tracking_number"]
            if prev.get("sig") and prev.get("awb") != awb_now:
                # AWB anulat/refăcut de la ultima rulare — exact cazul pentru care rulăm periodic
                n_awb += 1
                awb_changes.append((o["order_number"], prev.get("awb"), awb_now))
            try:
                st, _ = push(o, key, a.apply)
                n_ok += 1
                if a.apply:
                    _push_log_set(o["order_number"], sig, awb_now)
            except Exception as e:
                n_err += 1
                st = f"EROARE {type(e).__name__}"
                print(f"  {o['order_number']:<12} ❌ {st}")
            if verbose:
                print(f"  {o['order_number']:<12} {o['customer_email'] or '-':<32} {st}  [{key}]")
                if len(rows) <= 5:
                    print("      " + json.dumps(props, ensure_ascii=False)[:400])
            elif i % 250 == 0:
                el = time.time() - t0
                print(f"  … {i}/{len(rows)} · {n_ok} trimise · {n_same} nemodificate ·"
                      f" {n_skip} sărite · {n_err} erori · {el/60:.0f} min", flush=True)
            if a.apply and a.rate:
                time.sleep(1.0 / a.rate)
        if a.apply:
            json.dump(_plog, open(PUSH_LOG, "w", encoding="utf-8"))
        if awb_changes:
            print(f"\n📦 {len(awb_changes)} AWB-uri SCHIMBATE de la ultima rulare:")
            for on, old, new in awb_changes[:25]:
                print(f"     {on:<12} {old or '(fără)'} → {new or '(anulat)'}")
            if len(awb_changes) > 25:
                print(f"     … și încă {len(awb_changes)-25}")
        print(f"\n✅ {n_ok} {'trimise' if a.apply else 'de trimis (dry-run)'} · {n_same} nemodificate (sărite din jurnal) ·"
              f" {n_skip} magazin necunoscut · {n_err} erori · {n_awb} AWB schimbate · {(time.time()-t0)/60:.0f} min")
        if a.apply:
            print("⚠️ 200 ≠ ingerat. Fișele apar în ~5 min → verifică:  rp.py verify --email <...>")
        return

    if a.cmd == "keys":
        if a.refresh:
            doms = a.domains.split(",") if a.domains else DEFAULT_DOMAINS
            print(f"Culeg cheile publice de pe {len(doms)} magazine:\n")
            found = harvest_keys(doms)
            old = json.load(open(KEYS_FILE, encoding="utf-8")) if os.path.exists(KEYS_FILE) else {}
            old.update(found)  # pur upsert — un site picat nu șterge o cheie bună
            json.dump(old, open(KEYS_FILE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            print(f"\n→ {len(found)} găsite · {len(old)} în total în {KEYS_FILE}")
        else:
            for d, v in sorted(load_keys().items()):
                print(f"  {d:20s} {v['appClientId']:15s} {v['shop']}")
        return

    # DRY-RUN nu deschide conexiune: arată ce s-ar executa și iese (util și când MCP e la 429)
    if a.cmd in ("tag", "close", "reopen", "assign", "note", "snooze", "draft") and not a.apply:
        tool, args = _action(a)
        print(f"DRY-RUN · {tool}\n  " + json.dumps(args, ensure_ascii=False))
        print("\n→ adaugă --apply ca să execute")
        return

    m = MCP()
    if a.cmd == "find":
        if not (a.email or a.phone):
            sys.exit("find: dă --email sau --phone")
        arg = {"email": a.email} if a.email else {"phone": a.phone}
        show({"customer": m.call("get_customer_by_email_or_phone", arg),
              "conversations": m.call("search_conversations_by_customer", arg)})
    elif a.cmd == "verify":
        show(m.call("get_customer_by_email_or_phone", {"email": a.email}))
    elif a.cmd == "conv":
        show(m.call("get_conversation", {"conversation_id": a.ref}))
    elif a.cmd == "list":
        args = {"status": a.status, "per_page": min(a.limit, 50)}
        for k, v in (("channel", a.channel), ("startDate", a.dfrom), ("endDate", a.dto)):
            if v:
                args[k] = v
        show(m.call("list_conversations", args))
    elif a.cmd in ("agents", "tags", "teams"):
        show(m.call({"agents": "list_users", "tags": "list_tags", "teams": "list_teams"}[a.cmd]))
    else:
        tool, args = _action(a)
        show(m.call(tool, args))


if __name__ == "__main__":
    main()
