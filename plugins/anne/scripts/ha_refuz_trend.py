# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.0", "psycopg2-binary>=2.9"]
# ///
"""
Analiza evolutiei ratei de Refuz COD si Retur Real per produs HA,
impartita pe ferestre de timp pentru a detecta trendul.

Ferestre de timp:
  - Ultimele 7 zile    (recent / curent)
  - 8–30 zile in urma  (luna trecuta)
  - 31–90 zile in urma (trimestrul trecut)
  - 91+ zile in urma   (vechi / baseline)

Usage:
  uv run ha_refuz_trend.py [--min-orders N] [--sort refuz|retur] [--top N] [--sku HA-XXXX]
"""
import argparse, json, os, sys
import psycopg2, paramiko

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REMOTE_PYTHON = "/root/Scripturi/.venv/bin/python"
SSH_HOST_KEY  = "PROFIT_SSH_HOST"
SSH_USER_KEY  = "PROFIT_SSH_USER"
SSH_PASS_KEY  = "PROFIT_SSH_PASS"

QUERY_SCRIPT = r"""
import sqlite3, json, os
from collections import defaultdict
os.chdir('/root/Scripturi')
db = sqlite3.connect('data/profitability.db')

REFUZ_SHOPIFY = {'NOT_DELIVERED', 'OUT_FOR_DELIVERY', 'LABEL_PRINTED', 'IN_TRANSIT', ''}
RETUR_SHOPIFY = {'FULFILLED', 'DELIVERED'}

PERIODS = [
    ("7d",   "date('now', '-7 days')",  None),
    ("30d",  "date('now', '-30 days')", "date('now', '-7 days')"),
    ("90d",  "date('now', '-90 days')", "date('now', '-30 days')"),
    ("old",  None,                      "date('now', '-90 days')"),
]

results = {}

for period_name, date_from, date_to in PERIODS:
    where_parts = ["skus LIKE 'HA-%'",
                   "status_category IN ('Refuzata', 'Livrata', 'Anulata')"]
    if date_from:
        where_parts.append(f"DATE(created_at) >= {date_from}")
    if date_to:
        where_parts.append(f"DATE(created_at) < {date_to}")

    where_clause = " AND ".join(where_parts)
    rows = db.execute(f'''
        SELECT skus, shopify_delivery_status, status_category, COUNT(*) as cnt
        FROM profit_orders
        WHERE {where_clause}
        GROUP BY skus, shopify_delivery_status, status_category
    ''').fetchall()

    data = {}
    for skus_val, shopify_st, cat, cnt in rows:
        sku = skus_val.strip()
        s = (shopify_st or '').strip()
        if sku not in data:
            data[sku] = {"livrata": 0, "refuz": 0, "retur": 0, "anulata": 0}
        if cat == 'Livrata':
            data[sku]["livrata"] += cnt
        elif cat == 'Anulata':
            data[sku]["anulata"] += cnt
        elif cat == 'Refuzata':
            if s in RETUR_SHOPIFY:
                data[sku]["retur"] += cnt
            else:
                data[sku]["refuz"] += cnt

    period_out = {}
    for sku, v in data.items():
        liv, ref, ret, an = v["livrata"], v["refuz"], v["retur"], v["anulata"]
        tot_r = liv + ref + an
        tot_t = liv + ret
        total = liv + ref + an
        period_out[sku] = {
            "livrata": liv, "refuz": ref, "retur": ret, "anulata": an,
            "total": total,
            "refuz_pct": round(ref / tot_r * 100, 1) if tot_r else None,
            "retur_pct": round(ret / tot_t * 100, 1) if tot_t else None,
        }
    results[period_name] = period_out

print(json.dumps(results))
"""


def kb_get(key: str) -> str | None:
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        return None
    try:
        conn = psycopg2.connect(url, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM secrets WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def run_remote() -> dict:
    host = kb_get(SSH_HOST_KEY) or "84.46.242.181"
    user = kb_get(SSH_USER_KEY) or "root"
    pwd  = kb_get(SSH_PASS_KEY)
    if not pwd:
        sys.exit(f"EROARE: secretul '{SSH_PASS_KEY}' lipseste din KB.")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=pwd, timeout=30)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_ha_trend_q.py", "w") as f:
        f.write(QUERY_SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"{REMOTE_PYTHON} /tmp/_ha_trend_q.py", timeout=120
    )
    raw  = stdout.read().decode()
    errs = stderr.read().decode().strip()
    client.close()

    if errs:
        print(f"[remote stderr]: {errs[:400]}", file=sys.stderr)
    return json.loads(raw)


PERIOD_LABELS = {
    "7d":  "Ultimele 7 zile",
    "30d": "8–30 zile in urma",
    "90d": "31–90 zile in urma",
    "old": "91+ zile in urma",
}
PERIODS_ORDER = ["old", "90d", "30d", "7d"]


def trend_arrow(old_val, new_val, lower_is_better=True):
    if old_val is None or new_val is None:
        return "  ?"
    diff = new_val - old_val
    if abs(diff) < 0.5:
        return " ->"
    if lower_is_better:
        return " vv" if diff < 0 else " !!"
    return " ^^ " if diff > 0 else " vv"


def format_pct(val):
    if val is None:
        return "   —  "
    return f"{val:>5.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-orders", type=int, default=20,
                    help="Minim comenzi pe perioada (default 20)")
    ap.add_argument("--sort", choices=["refuz", "retur"], default="refuz",
                    help="Sorteaza dupa refuz_pct sau retur_pct (default refuz)")
    ap.add_argument("--top", type=int, default=20,
                    help="Top N produse (default 20)")
    ap.add_argument("--sku", metavar="SKU",
                    help="Afiseaza doar un SKU specific")
    ap.add_argument("--save", metavar="PATH",
                    help="Salveaza JSON complet la PATH")
    args = ap.parse_args()

    print("Se conecteaza si ruleaza interogarea pe 4 ferestre de timp...", file=sys.stderr)
    data = run_remote()  # {period: {sku: {livrata, refuz, retur, anulata, total, refuz_pct, retur_pct}}}

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"JSON salvat la: {args.save}", file=sys.stderr)

    # Collect all SKUs
    all_skus = set()
    for period_data in data.values():
        all_skus.update(period_data.keys())

    if args.sku:
        all_skus = {s for s in all_skus if s == args.sku}
        if not all_skus:
            sys.exit(f"SKU '{args.sku}' nu a fost gasit in date.")

    # Build per-SKU summary: use the most recent non-empty period for sorting
    def get_recent(sku, metric):
        for p in ["7d", "30d", "90d", "old"]:
            v = data.get(p, {}).get(sku, {}).get(metric)
            if v is not None:
                return v
        return 0

    # Filter: SKU must have at least min_orders total across all periods
    def total_orders(sku):
        return sum(data.get(p, {}).get(sku, {}).get("total", 0) for p in PERIODS_ORDER)

    skus_filtered = [s for s in all_skus if not args.sku and total_orders(s) >= args.min_orders * 2]
    if args.sku:
        skus_filtered = list(all_skus)

    sort_key = f"{args.sort}_pct"
    skus_sorted = sorted(skus_filtered, key=lambda s: get_recent(s, sort_key) or 0, reverse=True)
    skus_top = skus_sorted[:args.top]

    # ── SECTIUNEA 1: Tabel agregat per perioada ──────────────────────────────
    print("\n" + "=" * 70)
    print("  AGREGAT HA — Evolutie pe ferestre de timp")
    print("=" * 70)
    print(f"{'Perioada':<22} {'Comenzi':>8} {'Refuz%':>8} {'Retur%':>8}  Trend refuz")
    print("-" * 70)

    agg = {}
    for p in PERIODS_ORDER:
        pd = data.get(p, {})
        tot_liv = tot_ref = tot_ret = tot_an = 0
        for sku, v in pd.items():
            if ";" in sku:
                continue
            tot_liv += v["livrata"]
            tot_ref += v["refuz"]
            tot_ret += v["retur"]
            tot_an  += v["anulata"]
        tot_r = tot_liv + tot_ref + tot_an
        tot_t = tot_liv + tot_ret
        agg[p] = {
            "total": tot_r,
            "refuz_pct": round(tot_ref / tot_r * 100, 1) if tot_r else None,
            "retur_pct": round(tot_ret / tot_t * 100, 1) if tot_t else None,
        }

    prev_refuz = None
    for p in PERIODS_ORDER:
        a = agg[p]
        label = PERIOD_LABELS[p]
        arr = trend_arrow(prev_refuz, a["refuz_pct"]) if prev_refuz is not None else "   "
        print(f"{label:<22} {a['total']:>8}  {format_pct(a['refuz_pct'])}  {format_pct(a['retur_pct'])}  {arr}")
        prev_refuz = a["refuz_pct"]

    # ── SECTIUNEA 2: Per-produs, top N ───────────────────────────────────────
    print("\n")
    print("=" * 100)
    print(f"  TOP {args.top} produse HA — Evolutie refuz/retur per produs  (sort: {sort_key})")
    print("=" * 100)

    # Header
    col_w = 9
    print(f"{'SKU':<12}", end="")
    for p in PERIODS_ORDER:
        label = PERIOD_LABELS[p][:8]
        print(f"  {label:^18}", end="")
    print(f"  {'Trend':^6}")
    print(f"{'':12}", end="")
    for _ in PERIODS_ORDER:
        print(f"  {'Rfz%':>5} {'Rtr%':>5} {'N':>5}", end="")
    print()
    print("-" * 100)

    for sku in skus_top:
        print(f"{sku:<12}", end="")
        vals_refuz = []
        for p in PERIODS_ORDER:
            v = data.get(p, {}).get(sku)
            if v:
                rfp = format_pct(v["refuz_pct"])
                rtp = format_pct(v["retur_pct"])
                n = v["total"]
                print(f"  {rfp} {rtp} {n:>5}", end="")
                vals_refuz.append(v["refuz_pct"])
            else:
                print(f"  {'—':>5} {'—':>5} {'—':>5}", end="")
                vals_refuz.append(None)

        # Trend overall (oldest with data vs newest with data)
        non_null = [(i, v) for i, v in enumerate(vals_refuz) if v is not None]
        if len(non_null) >= 2:
            oldest_val = non_null[0][1]
            newest_val = non_null[-1][1]
            diff = newest_val - oldest_val
            if abs(diff) < 1.0:
                trend = " -- stabil"
            elif diff < 0:
                trend = f" vv  -{abs(diff):.1f}pp"
            else:
                trend = f" ^^  +{diff:.1f}pp"
        else:
            trend = "  ?"
        print(f"  {trend}")

    print("-" * 100)
    print(f"\nTotal SKU-uri analizate: {len(skus_filtered)}  |  "
          f"Min comenzi totale: {args.min_orders * 2}  |  Sort: {sort_key}")

    # ── SECTIUNEA 3: Produse cu imbunatatire / inrautatire semnificativa ──────
    print("\n")
    print("=" * 60)
    print("  SCHIMBARI SEMNIFICATIVE (>5 pp fata de baseline)")
    print("=" * 60)

    improved, worsened = [], []
    for sku in skus_filtered:
        vals = []
        for p in PERIODS_ORDER:
            v = data.get(p, {}).get(sku, {}).get(f"{args.sort}_pct")
            vals.append(v)
        non_null = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(non_null) < 2:
            continue
        oldest_val = non_null[0][1]
        newest_val = non_null[-1][1]
        diff = newest_val - oldest_val
        if diff <= -5:
            improved.append((sku, oldest_val, newest_val, diff))
        elif diff >= 5:
            worsened.append((sku, oldest_val, newest_val, diff))

    improved.sort(key=lambda x: x[3])
    worsened.sort(key=lambda x: x[3], reverse=True)

    print(f"\n  IMBUNATATITE (rata {args.sort} a scazut cu ≥5pp):")
    if improved:
        for sku, old_v, new_v, diff in improved:
            print(f"    {sku:<12}  {old_v:.1f}% → {new_v:.1f}%  ({diff:+.1f}pp)  vv")
    else:
        print("    — niciun produs cu imbunatatire semnificativa —")

    print(f"\n  INRAUTATITE (rata {args.sort} a crescut cu ≥5pp):")
    if worsened:
        for sku, old_v, new_v, diff in worsened:
            print(f"    {sku:<12}  {old_v:.1f}% → {new_v:.1f}%  ({diff:+.1f}pp)  ^^")
    else:
        print("    — niciun produs cu inrautatire semnificativa —")


if __name__ == "__main__":
    main()
