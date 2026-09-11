# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "python-barcode", "pillow"]
# ///
"""
Genereaza un/mai multe EAN-13 GARANTAT libere pe TOATE magazinele Arona.

Ce face:
  1. Trage LIVE toate barcode-urile de pe cele ~21 magazine Shopify (sursa de
     adevar; warehouse-ul metrics acopera doar 14 branduri si-i lipseste Magdeal
     = master-ul de sync HA, deci NU e de incredere pentru unicitate).
  2. Alege urmatoarele numere libere din SERIA INTERNA 200xxxxxxxxxx (prefix 200 =
     gama pe care GS1 o rezerva pt uz intern/in-store => nu se ciocneste niciodata
     cu un GTIN real de furnizor), continuand seria existenta a echipei.
  3. Optional: verifica un EAN dat (--check) sau randeaza PNG de eticheta (--image).

Exemple:
  uv run next_barcode.py                    # 1 barcode liber
  uv run next_barcode.py --count 5          # 5 libere consecutive
  uv run next_barcode.py --image --out .    # + PNG de eticheta
  uv run next_barcode.py --check 2000000000510   # e liber DA/NU + pe ce magazine

NB: verificarea reflecta starea de ACUM. Ruleaza chiar inainte sa scrii barcode-ul
pe produs daca vrei siguranta maxima.
"""
import sys, os, glob, json, time, argparse, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests

VER = "2026-01"

def find_kb():
    pats = [
        os.path.expanduser("~/.claude/plugins/cache/team-intelligence/core/*/scripts/kb.py"),
        os.path.expanduser("~/.claude/plugins/*/team-intelligence/core/*/scripts/kb.py"),
    ]
    cands = []
    for p in pats:
        cands += glob.glob(p)
    if not cands:
        sys.exit("kb.py nu a fost gasit (plugin core). Ruleaza onboarding-ul.")
    return max(cands, key=os.path.getmtime)

def get_stores():
    kb = find_kb()
    r = subprocess.run(["uv", "run", kb, "secret-get", "SHOPIFY_STORES_CSV"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("Nu pot lua SHOPIFY_STORES_CSV din KB: " + r.stderr[:300])
    stores = []
    for i, line in enumerate(r.stdout.strip().splitlines()):
        p = [x.strip() for x in line.split(",")]
        if i == 0 and p and p[0].lower() == "prefix":
            continue
        if len(p) >= 3 and p[1] and p[2]:
            stores.append({"prefix": p[0], "shop": p[1], "token": p[2]})
    return stores

Q = """query($cur:String){ productVariants(first:250, after:$cur){
  edges{ node{ barcode } } pageInfo{ hasNextPage endCursor } } }"""

def fetch_all_barcodes(stores, verbose=True):
    bc_to_stores = {}
    failed = []
    for s in stores:
        shop, token, pfx = s["shop"], s["token"], s["prefix"]
        cur = None; n = 0; ok = True
        while True:
            try:
                r = requests.post(
                    f"https://{shop}/admin/api/{VER}/graphql.json",
                    headers={"X-Shopify-Access-Token": token,
                             "Content-Type": "application/json"},
                    json={"query": Q, "variables": {"cur": cur}}, timeout=40)
                j = r.json()
            except Exception as e:
                if verbose: print(f"  {pfx}: EROARE {e}", file=sys.stderr)
                ok = False; break
            pv = (j.get("data") or {}).get("productVariants")
            if pv is None:
                if verbose: print(f"  {pfx}: {json.dumps(j)[:180]}", file=sys.stderr)
                ok = False; break
            for e in pv["edges"]:
                b = (e["node"].get("barcode") or "").strip()
                if b:
                    bc_to_stores.setdefault(b, []).append(pfx)
                    n += 1
            thr = (j.get("extensions", {}).get("cost", {})
                     .get("throttleStatus", {}))
            if thr and thr.get("currentlyAvailable", 1000) < 200:
                time.sleep(1.0)
            if pv["pageInfo"]["hasNextPage"]:
                cur = pv["pageInfo"]["endCursor"]
            else:
                break
        if not ok:
            failed.append(pfx)
        if verbose:
            print(f"{pfx:8} {shop:32} barcodes={n} ok={ok}", flush=True)
    return bc_to_stores, failed

def check_digit(d12):
    s = sum(int(x) * (1 if i % 2 == 0 else 3) for i, x in enumerate(d12))
    return str((10 - s % 10) % 10)

def is_valid_ean13(code):
    return (len(code) == 13 and code.isdigit()
            and check_digit(code[:12]) == code[12])

# Seria interna a echipei = "2000000000" (10 cifre) + contor de 3 cifre + control.
WINDOW_PREFIX = "2000000000"

def next_free(used, count):
    """Continua seria MONOTON (dupa cel mai mare numar deja folosit din serie),
    ca sa nu reutilizam un barcode retras dintr-un gol vechi."""
    used_bases = [int(c[:12]) for c in used
                  if len(c) == 13 and c.isdigit() and c[:10] == WINDOW_PREFIX]
    window_min = int(WINDOW_PREFIX + "000")   # 200000000000
    base = (max(used_bases) + 1) if used_bases else window_min
    out = []
    while len(out) < count and base < 10**12:
        d12 = f"{base:012d}"
        ean = d12 + check_digit(d12)
        if ean not in used:          # aproape sigur liber (suntem peste max), dar verificam
            out.append(ean)
        base += 1
    return out

def render_png(ean, outdir):
    from barcode import EAN13
    from barcode.writer import ImageWriter
    path = os.path.join(outdir, f"barcode_{ean}")
    # EAN13 expects the 12-digit base; it recomputes the check digit.
    full = EAN13(ean[:12], writer=ImageWriter()).save(path)
    return full

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="cate barcode-uri libere")
    ap.add_argument("--check", help="verifica un EAN anume (liber DA/NU)")
    ap.add_argument("--image", action="store_true", help="randeaza PNG de eticheta")
    ap.add_argument("--out", default=".", help="folder pt PNG (--image)")
    ap.add_argument("--quiet", action="store_true", help="fara log per magazin")
    args = ap.parse_args()

    stores = get_stores()
    print(f"Verific LIVE {len(stores)} magazine...\n", file=sys.stderr)
    bc, failed = fetch_all_barcodes(stores, verbose=not args.quiet)
    used = set(bc.keys())

    print("\n=== SUMAR ===")
    print("magazine verificate :", len(stores))
    print("barcode-uri distincte:", len(used))
    if failed:
        print("!! MAGAZINE ESUATE (neverificate):", ", ".join(failed))
        print("   NU garanta unicitatea pana nu reusesc si astea.")

    if args.check:
        code = args.check.strip()
        valid = is_valid_ean13(code)
        where = bc.get(code, [])
        print(f"\nCHECK {code}")
        print("  EAN-13 valid :", "DA" if valid else "NU (cifra de control gresita)")
        if where:
            print("  LIBER        : NU — exista pe:", ", ".join(sorted(set(where))))
        else:
            print("  LIBER        : DA — nu apare pe niciun magazin")
        return

    free = next_free(used, args.count)
    print(f"\n{args.count} barcode(uri) LIBERE (serie interna 200...):")
    for e in free:
        print("  ", e)

    if args.image:
        os.makedirs(args.out, exist_ok=True)
        for e in free:
            try:
                p = render_png(e, args.out)
                print("  PNG:", p)
            except Exception as ex:
                print("  (nu am putut randa PNG:", ex, ")", file=sys.stderr)

if __name__ == "__main__":
    main()
