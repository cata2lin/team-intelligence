#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COD→PAID watchdog — prinde comenzile plasate RAMBURS care au fost platite si cu CARDUL la cateva
ore dupa (dubla incasare: curierul mai cere o data cash la livrare).

Semnal (Shopify): gateway COD ('Plată ramburs'/'Ramburs'/'Cash on Delivery') IN paymentGatewayNames
  + o tranzactie SALE/CAPTURE SUCCESS pe card (shopify_payments/stripe/netopia/...) + financial_status=PAID.
Clasificare (AWBprint aggregated_status):
  - NEPLECAT + are AWB  -> RECUPERABIL: se poate anula AWB-ul si reface FARA ramburs (comanda e platita
                          => xConnector pune COD din soldul neplatit = 0). Cu --recall executa void+make.
  - PLECAT/LIVRAT       -> prea tarziu: curierul incaseaza cash -> REFUND pe card, ramane la CS/finante.
  - fara AWB inca       -> fulfill-ul il va face oricum PREPAID (comanda e platita) -> doar semnalam.

Ruleaza la 6h din cron. DRY-RUN by default. --email trimite un raport la CS DOAR daca e ceva de raportat.
--recall (implicit OFF) activeaza void+refacere automata pt cele recuperabile (dupa ce s-a validat live)."""
import argparse, datetime, json, os, re, subprocess, sys, urllib.parse as up

sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import xconnector as X

COD_GW = ("ramburs", "cash on delivery", "cod", "numerar", "plata la livrare", "plată ramburs")
CARD_GW = ("shopify_payments", "stripe", "netopia", "mobilpay", "librapay", "paypal", "card",
           "twispay", "euplatesc", "payu", "revolut")
XCDIR = "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector"


def _shops():
    return json.loads(os.environ["XCONNECTOR_SHOPS"])


def _toks():
    return {t["shopDomain"]: t for t in X._stores_csv_tokens()}


def detect(since_iso):
    """Comenzile COD platite si cu cardul, din toate magazinele RO, create dupa since_iso."""
    INTL = set(X.HERE_COUNTRY)
    toks = _toks()
    Q = ('query($q:String!, $c:String){ orders(first:100, query:$q, after:$c, '
         'sortKey:CREATED_AT, reverse:true){ '
         'edges{ cursor node{ name createdAt cancelledAt displayFinancialStatus displayFulfillmentStatus '
         'paymentGatewayNames totalPriceSet{ shopMoney{ amount currencyCode } } '
         'transactions{ kind status gateway } } } pageInfo{ hasNextPage } } }')
    hits = []
    for t in toks.values():
        dom = t["shopDomain"]
        if dom in INTL:
            continue
        cursor = None
        for _ in range(80):  # plafon generos; sortarea desc + created_at>=since termina de la sine
            try:
                d = X.shopify_gql(dom, t["adminToken"], Q,
                                  {"q": "financial_status:paid AND created_at:>=%s" % since_iso, "c": cursor})
            except Exception:
                break
            conn = ((d.get("data") or {}).get("orders") or {})
            edges = conn.get("edges") or []
            for e in edges:
                n = e["node"]
                if n.get("cancelledAt"):
                    continue
                gws = [g.lower() for g in (n.get("paymentGatewayNames") or [])]
                if not any(any(c in g for c in COD_GW) for g in gws):
                    continue
                txns = n.get("transactions") or []
                card_ok = any(tx.get("kind") in ("SALE", "CAPTURE") and tx.get("status") == "SUCCESS"
                              and any(c in (tx.get("gateway") or "").lower() for c in CARD_GW) for tx in txns)
                if not card_ok:
                    continue
                hits.append({"shop": dom, "name": n["name"], "created": n["createdAt"][:16],
                             "total": n["totalPriceSet"]["shopMoney"]["amount"],
                             "cur": n["totalPriceSet"]["shopMoney"]["currencyCode"],
                             "fulfil": n["displayFulfillmentStatus"], "gw": gws})
            if not conn.get("pageInfo", {}).get("hasNextPage"):
                break
            cursor = edges[-1]["cursor"]
    return hits


def classify(h):
    """RECUPERABIL / PLECAT / FARA-AWB dupa AWBprint aggregated_status."""
    ship = X.awbprint_status(h["name"])
    h["ship"] = ship or "?"
    if ship in X.PLECATA:
        return "PLECAT"
    # are AWB? (AWBprint spune ceva de tip created_awb/waiting/ready) — daca nu stim, tratam ca fara-awb
    if ship in ("created_awb", "waiting_for_courier", "ready_to_ship", "picked_up", "awb_created"):
        return "RECUPERABIL"
    return "FARA_AWB" if ship in (None, "?", "new", "processing", "unfulfilled") else "RECUPERABIL"


def _dispatched_live(name, shop):
    """Colet PLECAT? Re-verific PROASPAT AWBprint `aggregated_status in PLECATA` chiar inainte de void
    (statusul curier se poate schimba intre clasificare si recall). AWBprint = sursa de status curier
    (NU folosim `dispatched` din xConnector). None/eroare -> conservator: considera plecat, NU rechem."""
    stt = X.awbprint_status(name)
    if stt is None:
        return True
    return stt in X.PLECATA


def recall(name, shop):
    """Void AWB + refacere (comanda e platita => AWB nou fara ramburs). Reutilizeaza CLI-ul testat.
    GARDA: re-verific LIVE ca NU a plecat inainte de void (AWBprint e desincronizat)."""
    if _dispatched_live(name, shop):
        return "PLECAT-skip"          # a plecat intre timp -> nu-l ating, ramane la email/CS
    env = dict(os.environ)
    def run(args):
        # 300s: void/make lovesc xConnector cu poll — 120s era prea strâns și un void lent (EST221087,
        # 27-iul) arunca TimeoutExpired care omora tot batch-ul înainte să ajungă la comenzile următoare.
        return subprocess.run([sys.executable, "xconnector.py"] + args, cwd=XCDIR,
                              capture_output=True, text=True, timeout=300, env=env)
    run(["awb-void", "--order", name, "--apply"])
    import time; time.sleep(4)
    run(["awb-make", "--order", name, "--apply"])
    time.sleep(4)
    L = run(["links", "--order", name]).stdout
    m = re.search(r"AWB (\d{9,})", L)
    return m.group(1) if m else None


def send_email(to, sender, subject, body):
    """Trimite prin SMTP (Gmail) folosind SMTP_USER/SMTP_PASS din env (facturi@aronagroup.ro).
    Parola NU se logheaza niciodata; vine din env, nu din cod."""
    import smtplib
    from email.mime.text import MIMEText
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER") or sender
    pw = os.environ.get("SMTP_PASS")
    if not pw:
        raise RuntimeError("SMTP_PASS lipseste din env")
    msg = MIMEText(body, _charset="utf-8")
    msg["To"] = to; msg["From"] = sender; msg["Subject"] = subject
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(); s.login(user, pw)
        s.sendmail(sender, [x.strip() for x in to.split(",")], msg.as_string())


def main():
    ap = argparse.ArgumentParser(description="COD platit si cu cardul (dubla incasare) — watchdog 6h.")
    ap.add_argument("--hours", type=int, default=48, help="fereastra de creare a comenzii (ore)")
    ap.add_argument("--apply", action="store_true", help="executa (altfel dry-run)")
    ap.add_argument("--recall", action="store_true", help="anuleaza+reface AWB-urile RECUPERABILE (gated)")
    ap.add_argument("--email", help="trimite raport la aceasta adresa DOAR daca e ceva")
    ap.add_argument("--from", dest="sender", default="facturi@aronagroup.ro")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    since = (datetime.datetime.utcnow() - datetime.timedelta(hours=a.hours)).date().isoformat()
    hits = detect(since)
    for h in hits:
        h["klass"] = classify(h)
    rec = [h for h in hits if h["klass"] == "RECUPERABIL"]
    ship = [h for h in hits if h["klass"] == "PLECAT"]
    noawb = [h for h in hits if h["klass"] == "FARA_AWB"]

    print("COD→PAID watchdog · fereastra %dh (creat >= %s) · %d cazuri" % (a.hours, since, len(hits)))
    for k, arr in (("RECUPERABIL", rec), ("PLECAT/LIVRAT", ship), ("FARA_AWB", noawb)):
        for h in arr:
            print("  [%s] %-24s %-11s %6s %s · %s · ship=%s" %
                  (k, h["shop"][:24], h["name"], h["total"], h["cur"], h["fulfil"], h["ship"]))

    # RECUPERABIL: void+reface (doar cu --apply --recall). Rezultatul (AWB nou / plecat / esuat) intra
    # in email ca sa VALIDAM prima refacere reala (rambursul se vede pe eticheta DPD).
    recalled_ok, recall_note = [], {}
    if a.apply and a.recall:
        for h in rec:
            try:
                awb = recall(h["name"], h["shop"])
            except Exception as e:
                # IZOLARE PER-COMANDĂ: un timeout/eroare pe O comandă NU mai abortează restul buclei.
                # (27-iul: void-ul EST221087 a dat TimeoutExpired → a omorât rularea ÎNAINTE de NUBRA10929,
                #  care a plecat între timp cu ramburs = dublă încasare.) Comanda e platită → fulfill-ul de
                #  15min reface AWB-ul prepaid oricum; plasa de siguranță (email refund CS) rămâne.
                recall_note[h["name"]] = "recall EȘUAT (%s) → fulfill-ul de 15min reface prepaid" % type(e).__name__
                print("    ⚠️ %s recall a dat eroare (%s) → continui cu următoarele" % (h["name"], type(e).__name__))
                continue
            if awb == "PLECAT-skip":
                recall_note[h["name"]] = "a plecat între timp → NU refăcut (rămâne refund CS)"
                print("    ⛔ %s a plecat între timp → nu rechem" % h["name"])
            elif awb:
                recalled_ok.append(h); recall_note[h["name"]] = "AWB refăcut %s (verificați ramburs pe eticheta DPD)" % awb
                print("    ↻ %s → AWB nou %s (verificați prepaid pe eticheta DPD)" % (h["name"], awb))
            else:
                recall_note[h["name"]] = "refacere EȘUATĂ (fulfill-ul de 15min o reface prepaid)"
                print("    ⚠️ %s refacere eșuată" % h["name"])

    # email la CS: plecatele (refund) + toate recuperabilele (fie „refac OFF", fie rezultatul recall-ului,
    # ca sa validam prima refacere reala).
    to_cs = ship + rec
    # DEDUP: fiecare comanda se raporteaza O SINGURA data (altfel cronul la 6h trimite EST… repetat
    # cat timp e in fereastra). State file cu numele deja raportate.
    seen_path = os.environ.get("COD_SEEN_FILE", "/root/Scripturi/logs/cod_paid_seen.json")
    try:
        seen = set(json.load(open(seen_path)))
    except Exception:
        seen = set()
    fresh = [h for h in to_cs if h["name"] not in seen]
    if a.email and to_cs and not fresh:
        print("  (toate %d cazurile deja raportate → nu retrimit)" % len(to_cs))
    to_cs = fresh
    if a.email and to_cs:
        lines = ["Comenzi RAMBURS platite si cu cardul (risc de dubla incasare la livrare):", ""]
        for h in to_cs:
            if h in ship:
                stare = "LIVRAT/plecat → refund pe card (nu se mai poate reface)"
            elif h["name"] in recall_note:
                stare = recall_note[h["name"]]        # rezultatul recall-ului (AWB refacut / plecat / esuat)
            else:
                stare = "neplecat → se poate reface AWB fara ramburs"
            lines.append("• %s (%s) — %s %s — %s [ship=%s]" %
                         (h["name"], h["shop"], h["total"], h["cur"], stare, h["ship"]))
        lines += ["", "Generat automat de watchdog-ul COD→PAID (la 6h)."]
        body = "\n".join(lines)
        if a.apply:
            try:
                send_email(a.email, a.sender, "[COD dublu] %d comenzi ramburs platite cu cardul" % len(to_cs),
                           body)
                print("  ✉️  raport trimis la %s (from %s)" % (a.email, a.sender))
                try:
                    seen |= {h["name"] for h in to_cs}
                    os.makedirs(os.path.dirname(seen_path), exist_ok=True)
                    json.dump(sorted(seen), open(seen_path, "w"))
                except Exception:
                    pass
            except Exception as e:
                print("  ✉️  EROARE email: %s" % (str(e)[:160]))
        else:
            print("  [DRY-RUN] as trimite email la %s:\n----\n%s\n----" % (a.email, body))


if __name__ == "__main__":
    main()
