#!/usr/bin/env python3
"""Triage adrese HELD — o singură comandă pentru „ce din backlog-ul held e recuperabil vs CS real".

Ia comenzile ținute (hold `bad-address`/`awb-esec-repetat`) din event-log-ul cronului, le trece prin ACELAȘI
preclean + nomenclator + (opțional) HERE ca fulfill-ul, și le clasifică:
  RESOLVED  — nomenclatorul/regulile le fac AWB determinist (rural / loc-typo / city-is-zip / stradă din ZIP …)
  HERE      — needs-geocoder dar HERE le validează (≥0.9) — cronul le face în sweep
  CS        — reziduu genuin (fără număr casă / stradă necunoscută+zip greșit / test-fake)

Rulează pe VPS (are DATABASE_URL_AWBPRINT/METRICS + HERE_API_KEY în env):
  python3 address_triage.py --days 4                 # determinist, rapid
  python3 address_triage.py --days 4 --here          # + validează HERE reziduul (mai lent)
  python3 address_triage.py --days 7 --shop EST --fails   # doar Esteban, listează CS-ul
NU scrie nimic (read-only) — e DOAR analiză. Facerea reală de AWB rămâne `xconnector.py fulfill`.
"""
import sys, os, re, json, argparse, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xconnector as X
import address_nomenclator as N

EVENT_LOG = os.environ.get("XC_EVENT_LOG", "/root/Scripturi/logs/xc_awb_events.jsonl")


def _pg(url):
    import pg8000
    from urllib.parse import urlparse, unquote
    u = urlparse(url)
    c = pg8000.connect(user=unquote(u.username or ""), password=unquote(u.password or ""),
                       host=u.hostname, port=u.port or 5432, database=(u.path or "").lstrip("/"), ssl_context=True)
    c.autocommit = True
    return c


def held_names(days, shop):
    """Comenzi încă HELD (bad-address/awb-esec) din ultimele `days` zile — ținut minus făcut, din event-log."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    made, held = set(), {}
    try:
        for line in open(EVENT_LOG, encoding="utf-8"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            o, ts = e.get("order"), e.get("ts", "")
            if not o or ts < cutoff:
                continue
            if e.get("kind") in ("awb", "release-awb") and e.get("result") == "ok":
                made.add(o)
            if e.get("kind") == "hold" and e.get("reason") in ("bad-address", "awb-esec-repetat"):
                held[o] = 1
    except FileNotFoundError:
        print("event-log lipsă: %s" % EVENT_LOG, file=sys.stderr)
        return []
    intl = ("CZ", "PL", "BG", "BONBG", "DUP")   # nomenclator RO — internaționalele au validator separat
    names = [o for o in sorted(set(held) - made) if not o.startswith(intl)]
    if shop:
        names = [o for o in names if o.upper().startswith(shop.upper())]
    return names


def classify(cur, key, ad, use_here):
    """(categorie, detaliu) pt o adresă, prin ACELAȘI lanț de preclean + nomenclator ca fulfill-ul."""
    a1, a2 = ad.get("address1"), ad.get("address2")
    city, prov = ad.get("city"), (ad.get("province") or ad.get("province_code") or "")
    zp = (ad.get("zip") or "").strip()
    zp = zp if zp not in ("", "-") else ""
    a1dl = X._delabel(a1)
    a1z, a2z = X._street_from_a2(a1dl, a2)
    a1s, citys, sw = X._maybe_swap_fields(a1z, city)
    a1p = X._strip_loc_prefix(a1s, citys)
    a1ap, a2ap = X._pull_artery_prefix(a1p, a2z)
    a1L, a2L = X._pull_landmark(a1ap, a2ap)
    a1c = X._expand_fullname(X._expand_street_abbrev(X._street_deglue(X._street_tail(a1L))))
    cityc = X._city_denoise(citys)[0]
    pre_changed = (a1c != (a1 or "")) or (cityc != (city or "")) or sw
    r = N.validate_and_correct(cur, prov, cityc, zp, a1c, a2L or "")
    st, note = r.get("status"), r.get("note", "")
    if st in ("valid", "corrected"):
        tag = "rural" if "rural" in note else ("loc-typo" if "typo" in note or "jud/loc" in note else "nomen")
        return "RESOLVED", tag
    if st == "cs":
        return "CS", note
    if use_here and key:
        adh = {"address1": a1c, "city": cityc, "zip": zp}
        try:
            if X.here_zip_fill(adh, key):
                return "HERE", "zip-fill"
        except Exception:
            pass
        if X.here_validate(adh, "ROU", key) >= X.HERE_MIN_SCORE and pre_changed:
            return "HERE", "valid"
    return "CS" if not use_here else "CS", note or "needs-geocoder"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="0 = toate")
    ap.add_argument("--shop", default="", help="filtru prefix comandă (EST/GT/…)")
    ap.add_argument("--here", action="store_true", help="validează și HERE reziduul (mai lent)")
    ap.add_argument("--fails", action="store_true", help="listează comenzile CS")
    args = ap.parse_args()

    names = held_names(args.days, args.shop)
    if args.limit:
        names = names[:args.limit]
    if not names:
        print("0 comenzi held în fereastră.")
        return
    aw = _pg(os.environ["DATABASE_URL_AWBPRINT"])
    c = aw.cursor()
    c.execute("SELECT order_number, shipping_address FROM orders WHERE order_number = ANY(%s)", (names,))
    addr = {r[0]: (r[1] if isinstance(r[1], dict) else json.loads(r[1])) for r in c.fetchall()}
    cur = X.metrics_cursor_live()
    key = X.here_key() if args.here else None

    cats = collections.Counter()
    tags = collections.Counter()
    fails = []
    for i, onum in enumerate(names, 1):
        ad = addr.get(onum)
        if not ad:
            cats["NO-ADDR"] += 1
            continue
        try:
            cat, detail = classify(cur, key, ad, args.here)
        except Exception as e:
            cat, detail = "ERR", str(e)[:40]
        cats[cat] += 1
        tags["%s:%s" % (cat, detail.split("(")[0].strip()[:24])] += 1
        if cat in ("CS", "ERR"):
            fails.append((onum, ad.get("address1"), ad.get("city"), detail))
        if args.limit == 0 and i % 50 == 0:
            print("… %d/%d" % (i, len(names)), file=sys.stderr)

    tot = sum(cats.values())
    print("=== TRIAGE %d comenzi held (ultimele %d zile%s) ===" % (tot, args.days, ", shop=" + args.shop if args.shop else ""))
    for k in ("RESOLVED", "HERE", "CS", "NO-ADDR", "ERR"):
        if cats.get(k):
            print("  %-9s %4d  (%.0f%%)" % (k, cats[k], 100.0 * cats[k] / tot))
    print("\n  detaliu pe cauză:")
    for k, v in tags.most_common(20):
        print("    %-34s %d" % (k, v))
    if args.fails:
        print("\n--- CS (reziduu, %d) ---" % len(fails))
        for onum, a1, city, detail in fails:
            print("  %-11s %-42s | %-14s | %s" % (onum, (a1 or "")[:42], (city or "")[:14], detail[:30]))


if __name__ == "__main__":
    main()
