# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "psycopg[binary]", "pillow"]
# ///
"""
seap_fisa.py — fisa de catalog SEAP/SICAP pentru un produs ARONA, gata de copiat.

Ia produsul din Shopify (dupa SKU), costul REAL de transport din AWBprint
(orders.transport_cost pe comenzile livrate cu SKU-ul ala) si calculeaza
pretul unitar fara TVA = pret site / 1.21 + transport / 1.21, rotunjit in sus.

Exemple:
  uv run seap_fisa.py --sku GD-BR-1313741
  uv run seap_fisa.py --sku GD-BR-1313741 --cpv "38434560-9 Analizoare chimice"
  uv run seap_fisa.py --sku HA-0002 --prefix MAG --transport 14.62 --out fisa.txt --img
"""
import argparse, csv, glob, html, io, json, math, os, re, subprocess, sys
from pathlib import Path
from statistics import median
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VAT = 1.21
API_VERSION = "2026-04"
FALLBACK_TRANSPORT = 13.01          # DPD colet standard, cu TVA (verificat sep-2026)
IBAN_TREZ = "RO19TREZ2015069XXX009130"

COND_LIVRARE = ("Livrare prin curier DPD in 24-48 ore lucratoare de la confirmarea comenzii, "
                "pe intreg teritoriul Romaniei. Transportul este inclus in pret.")
COND_PLATA = (f"Plata prin ordin de plata in contul de Trezorerie {IBAN_TREZ}, "
              "in termen de 30 de zile de la data facturii. Factura se emite la livrare.")


# ---------- secrete din KB ----------
def _kb_py():
    if os.getenv("KB_PY") and os.path.exists(os.getenv("KB_PY")):
        return os.getenv("KB_PY")
    cands = glob.glob(str(Path.home() / ".claude/plugins/cache/team-intelligence/core/*/scripts/kb.py"))
    cands += glob.glob(str(Path.home() / "team-intelligence*/plugins/core/scripts/kb.py"))
    return next((c for c in cands if os.path.exists(c)), None)


def secret(key):
    if os.getenv(key):
        return os.getenv(key)
    kb = _kb_py()
    if not kb:
        sys.exit(f"Nu gasesc kb.py; seteaza {key} in env sau KB_PY.")
    r = subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"Secretul {key} lipseste din KB.")
    return r.stdout.strip()


def resolve_store(prefix):
    txt = secret("SHOPIFY_STORES_CSV")
    if "\n" not in txt and os.path.exists(txt):
        txt = open(txt, encoding="utf-8-sig").read()
    for row in csv.DictReader(io.StringIO(txt)):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            return row["shop"].strip().replace("https://", "").strip("/"), row["token"].strip()
    sys.exit(f"Prefixul {prefix} nu e in SHOPIFY_STORES_CSV.")


# ---------- Shopify ----------
Q = """
query($q: String!) {
  productVariants(first: 5, query: $q) {
    nodes {
      sku barcode price
      product {
        title handle onlineStoreUrl descriptionHtml status
        featuredImage { url }
        images(first: 3) { nodes { url } }
        nrCutii: metafield(namespace: "custom", key: "nr_cutii") { value }
      }
    }
  }
}"""


def shopify_product(prefix, sku):
    shop, token = resolve_store(prefix)
    r = requests.post(f"https://{shop}/admin/api/{API_VERSION}/graphql.json",
                      headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                      json={"query": Q, "variables": {"q": f"sku:{sku}"}}, timeout=40)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        sys.exit(f"Shopify: {body['errors']}")
    nodes = body["data"]["productVariants"]["nodes"]
    nodes = [n for n in nodes if (n.get("sku") or "").upper() == sku.upper()] or nodes
    if not nodes:
        sys.exit(f"SKU {sku} nu exista pe {prefix}.")
    return nodes[0], shop


def html_to_text(h):
    h = re.sub(r"<(br|/p|/h\d|/li|/div)[^>]*>", "\n", h or "", flags=re.I)
    h = re.sub(r"<li[^>]*>", "- ", h, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# ---------- AWBprint: transport real ----------
def real_transport(sku, days=120):
    import psycopg
    sql = """
      SELECT transport_cost FROM orders
      WHERE line_items::text ILIKE %s
        AND transport_cost IS NOT NULL AND transport_cost > 0
        AND courier_name = 'DPD' AND COALESCE(package_count,1) = 1
        AND frisbo_created_at > now() - (%s || ' days')::interval
      ORDER BY frisbo_created_at DESC LIMIT 60"""
    try:
        with psycopg.connect(secret("DATABASE_URL_AWBPRINT"), connect_timeout=15) as c:
            rows = [r[0] for r in c.execute(sql, (f"%{sku}%", str(days))).fetchall()]
    except Exception as e:
        print(f"! AWBprint indisponibil ({e}); folosesc {FALLBACK_TRANSPORT} lei", file=sys.stderr)
        return FALLBACK_TRANSPORT, 0
    if not rows:
        return FALLBACK_TRANSPORT, 0
    return round(median(rows), 2), len(rows)


def download_image(url, sku, folder):
    from PIL import Image
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    raw = requests.get(url, timeout=60).content
    p = folder / f"{sku}.jpg"
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((1600, 1600))
    for q in (90, 80, 70, 60):
        im.save(p, "JPEG", quality=q, optimize=True)
        if p.stat().st_size <= 1024 * 1024:
            break
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku", required=True)
    ap.add_argument("--prefix", default="GRAN", help="prefix magazin din SHOPIFY_STORES_CSV (default GRAN)")
    ap.add_argument("--price", type=float, help="pret site CU TVA; default = pretul din Shopify")
    ap.add_argument("--transport", type=float, help="transport CU TVA per colet; default = median real AWBprint")
    ap.add_argument("--cpv", default="", help='ex "38434560-9 Analizoare chimice"')
    ap.add_argument("--garantie", type=int, default=24, help="luni garantie (default 24)")
    ap.add_argument("--round", choices=["leu", "ban"], default="leu", help="rotunjire in sus la leu (default) sau la ban")
    ap.add_argument("--out", help="salveaza fisa in fisier .txt")
    ap.add_argument("--img", action="store_true", help="descarca poza principala <1MB (JPG) langa --out sau in cwd")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    v, shop = shopify_product(a.prefix, a.sku)
    p = v["product"]
    price = a.price or float(v["price"])
    transport, n = (a.transport, -1) if a.transport else real_transport(a.sku)

    price_ex = price / VAT
    transport_ex = transport / VAT
    total = price_ex + transport_ex
    total_r = math.ceil(total) if a.round == "leu" else math.ceil(total * 100) / 100
    nr_cutii = (p.get("nrCutii") or {}).get("value")
    per_box = round(1 / float(nr_cutii)) if nr_cutii and float(nr_cutii) > 0 else None
    url = p.get("onlineStoreUrl") or f"https://{shop.replace('.myshopify.com', '')}/products/{p['handle']}"
    desc = html_to_text(p.get("descriptionHtml")) + f"\nProdus nou, sigilat. Garantie {a.garantie} luni."
    images = [i["url"] for i in (p.get("images") or {}).get("nodes", [])]

    img_path = None
    if a.img and p.get("featuredImage"):
        folder = Path(a.out).parent if a.out else Path.cwd()
        img_path = download_image(p["featuredImage"]["url"], a.sku, folder)

    if a.json:
        print(json.dumps({"image_file": str(img_path) if img_path else None, "sku": a.sku, "title": p["title"], "price_vat": price, "price_ex": round(price_ex, 2),
                          "transport_vat": transport, "transport_ex": round(transport_ex, 2), "n_orders": n,
                          "unit_price_seap": total_r, "per_box": per_box, "url": url, "barcode": v.get("barcode"),
                          "images": images}, ensure_ascii=False, indent=2))
        return

    src = ("dat manual" if n == -1 else
           f"median real AWBprint, {n} comenzi DPD" if n else "fallback, fara comenzi in AWBprint")
    stoc = "In stoc" if p.get("status") == "ACTIVE" else "(produs inactiv in Shopify!)"
    lines = [
        f"=== FISA SEAP - {a.sku} ({a.prefix}) ===", "",
        "CALCUL PRET (fara TVA):",
        f"  pret site {price:.2f} cu TVA / 1.21          = {price_ex:.2f}",
        f"  transport DPD {transport:.2f} cu TVA / 1.21   = {transport_ex:.2f}   ({src})",
        f"  total {total:.2f} -> PRET UNITAR SICAP:         {total_r:.2f} lei fara TVA  ({total_r * VAT:.2f} cu TVA)",
        f"  bucati per cutie master (nr_cutii): {per_box or 'necunoscut'}", "",
        "NUMAR REFERINTA:   " + a.sku,
        "DENUMIRE:          " + p["title"],
        "COD GTIN:          " + (v.get("barcode") or "(gol)"),
        "SITE PREZENTARE:   " + url,
        "COD CPV:           " + (a.cpv or "(completeaza - vezi lista din SKILL.md)"),
        "PRET UNITAR RON:   " + f"{total_r:.2f}",
        "UNITATE MASURA:    bucata",
        "STOC:              " + stoc,
        "", "DESCRIERE:", desc, "",
        "CONDITII DE LIVRARE:", COND_LIVRARE, "",
        "CONDITII DE PLATA:", COND_PLATA, "",
        "IMAGINI (max 1024 KB fiecare):",
    ] + [f"  {u}" for u in images]
    text = "\n".join(lines)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"\n-> salvat {a.out}")
    if img_path:
        print(f"-> poza: {img_path}")


if __name__ == "__main__":
    main()
