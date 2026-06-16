# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.0", "psycopg2-binary>=2.9"]
# ///
"""
Calculeaza rata de Refuz COD si Retur Real per produs HA.

Refuz COD  = comanda in categoria 'Refuzata' cu shopify_delivery_status
             NOT_DELIVERED / OUT_FOR_DELIVERY / LABEL_PRINTED / IN_TRANSIT / ''
             (curier a incercat livrarea, clientul a refuzat la usa)

Retur Real = comanda in categoria 'Refuzata' cu shopify_delivery_status
             FULFILLED sau DELIVERED
             (produsul a ajuns, clientul l-a trimis inapoi)

Formule:
  refuz_pct = refuz / (livrate + refuz + anulate) * 100
  retur_pct = retur / (livrate + retur)           * 100

Usage:
  uv run ha_refuz_retur.py [--top N] [--min-orders N] [--sort refuz|retur] [--save path.json]
"""
import argparse, json, os, sys, textwrap
import psycopg2
import paramiko

REMOTE_PYTHON = "/root/Scripturi/.venv/bin/python"
REMOTE_SCRIPT = "/tmp/_ha_refuz_q.py"
SSH_HOST_KEY  = "PROFIT_SSH_HOST"
SSH_USER_KEY  = "PROFIT_SSH_USER"
SSH_PASS_KEY  = "PROFIT_SSH_PASS"

QUERY_SCRIPT = r"""
import sqlite3, json, os
os.chdir('/root/Scripturi')
prof = sqlite3.connect('data/profitability.db')

# Refuz = orice colet care s-a intors (Refuzata), indiferent de shopify_delivery_status.
# Numitor = colete care au plecat efectiv = Livrate + Refuzate (Anulate n-au plecat).
# refuz_pct = Refuzate / (Livrate + Refuzate) * 100

rows = prof.execute('''
    SELECT skus, status_category, COUNT(*) as cnt
    FROM profit_orders
    WHERE skus LIKE 'HA-%'
      AND status_category IN ('Refuzata', 'Livrata', 'Anulata')
    GROUP BY skus, status_category
''').fetchall()

data = {}
for skus_val, cat, cnt in rows:
    sku = skus_val.strip()
    if sku not in data:
        data[sku] = {"livrata": 0, "refuz": 0, "anulata": 0}
    if cat == 'Livrata':
        data[sku]["livrata"] += cnt
    elif cat == 'Anulata':
        data[sku]["anulata"] += cnt
    elif cat == 'Refuzata':
        data[sku]["refuz"] += cnt

out = {}
for sku, v in data.items():
    liv, ref, an = v["livrata"], v["refuz"], v["anulata"]
    trimise = liv + ref
    out[sku] = {
        "livrata": liv, "refuz": ref, "anulata": an,
        "refuz_pct": round(ref / trimise * 100, 1) if trimise else None,
    }
print(json.dumps(out))
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
        sys.exit(f"EROARE: secretul '{SSH_PASS_KEY}' lipseste din KB.\n"
                 f"Adauga-l cu:  kb.py secret-set {SSH_PASS_KEY} <parola>")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=pwd, timeout=30)

    sftp = client.open_sftp()
    with sftp.open(REMOTE_SCRIPT, "w") as f:
        f.write(QUERY_SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"{REMOTE_PYTHON} {REMOTE_SCRIPT}", timeout=120
    )
    raw  = stdout.read().decode()
    errs = stderr.read().decode().strip()
    client.close()

    if errs:
        print(f"[remote stderr]: {errs[:300]}", file=sys.stderr)
    return json.loads(raw)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top",        type=int, default=20,    help="Afiseaza top N produse (default 20)")
    ap.add_argument("--min-orders", type=int, default=30,    help="Minim comenzi totale (default 30)")
    ap.add_argument("--sort",       choices=["refuz"], default="refuz",
                    help="Sorteaza dupa refuz_pct (default refuz)")
    ap.add_argument("--bundles",    action="store_true",      help="Include si SKU-uri bundle (cu ;)")
    ap.add_argument("--save",       metavar="PATH",           help="Salveaza JSON complet la PATH")
    args = ap.parse_args()

    print("Se conecteaza la server si ruleaza interogarea...", file=sys.stderr)
    raw = run_remote()

    # Filtrare: min-orders pe colete trimise (Livrate + Refuzate)
    results = []
    for sku, v in raw.items():
        if not args.bundles and ";" in sku:
            continue
        trimise = v["livrata"] + v["refuz"]
        if trimise < args.min_orders:
            continue
        results.append((sku, v, v.get("refuz_pct") or 0))

    results.sort(key=lambda x: x[2], reverse=True)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        print(f"JSON complet salvat la: {args.save}", file=sys.stderr)

    # Afisare tabel
    top = results[:args.top]
    print(f"\n{'SKU':<12} {'Livrate':>8} {'Intorse':>8} {'Anulate':>8} {'Refuz%':>8}"
          f"  (Refuz% = intorse / trimise)")
    print("-" * 60)
    for sku, v, _ in top:
        rfp = f"{v['refuz_pct']:.1f}%" if v['refuz_pct'] is not None else "  —"
        print(f"{sku:<12} {v['livrata']:>8} {v['refuz']:>8} {v['anulata']:>8} {rfp:>8}")

    print(f"\nTotal produse analizate: {len(results)}  |  Min comenzi trimise: {args.min_orders}")


if __name__ == "__main__":
    main()
