#!/usr/bin/env python3
"""
sync_barcodes.py — Sincronizează toate barcode-urile din toate magazinele Shopify.

Trece prin fiecare magazin din stores.csv, extrage toate variantele cu barcode,
și salvează totul într-un JSON centralizat (barcodes_db.json).

Utilizare CLI:
    python sync_barcodes.py
    python sync_barcodes.py --stores stores.csv --output barcodes_db.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

# Use core.stores if available (when run from dashboard), fallback to manual CSV
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from core.stores import list_stores as _core_list_stores
except ImportError:
    _core_list_stores = None

GRAPHQL_API_VERSION = "2024-01"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_stores(stores_path: Path) -> List[Dict[str, str]]:
    """Load all stores — uses core.stores if available, otherwise CSV."""
    if _core_list_stores is not None:
        return _core_list_stores()
    import csv
    stores = []
    with open(str(stores_path), "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            prefix = (row.get("prefix") or "").strip()
            shop = (row.get("shop") or "").strip()
            token = (row.get("token") or "").strip()
            if prefix and shop and token:
                stores.append({"prefix": prefix, "shop": shop, "token": token})
    return stores


def fetch_graphql(shop: str, token: str, query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query against Shopify Admin API."""
    url = f"https://{shop}/admin/api/{GRAPHQL_API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}
    resp = httpx.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


VARIANTS_QUERY = """
query getVariants($cursor: String) {
  productVariants(first: 250, after: $cursor) {
    edges {
      node {
        id
        sku
        barcode
        title
        product {
          title
          status
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def fetch_all_variants(shop: str, token: str) -> List[Dict[str, Any]]:
    """Fetch ALL product variants from a Shopify store (paginated)."""
    variants = []
    cursor = None
    page = 0

    while True:
        page += 1
        variables = {"cursor": cursor} if cursor else {}

        try:
            result = fetch_graphql(shop, token, VARIANTS_QUERY, variables)
        except Exception as e:
            log(f"    ⚠ Eroare GraphQL pagina {page}: {e}")
            break

        data = result.get("data", {}).get("productVariants", {})
        edges = data.get("edges", [])
        page_info = data.get("pageInfo", {})

        for edge in edges:
            node = edge["node"]
            sku = (node.get("sku") or "").strip()
            barcode_val = (node.get("barcode") or "").strip()
            product_title = node.get("product", {}).get("title", "")
            variant_title = node.get("title", "")
            product_status = node.get("product", {}).get("status", "")

            if sku:  # only include variants with SKU
                variants.append({
                    "sku": sku,
                    "barcode": barcode_val,
                    "variant_id": node["id"],
                    "product_title": product_title,
                    "variant_title": variant_title,
                    "status": product_status,
                })

        log(f"    Pagina {page}: {len(edges)} variante (total: {len(variants)})")

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")
        time.sleep(0.3)  # rate limiting

    return variants


def sync_all_stores(stores_path: Path, output_path: Path, store_filter: str = "") -> Dict[str, Any]:
    """Sync barcodes from all stores (or a single store) and save to JSON."""
    all_stores = load_stores(stores_path)

    if not all_stores:
        log("❌ Nu am găsit magazine în stores.csv")
        return {}

    # Filter to single store if requested
    if store_filter:
        stores = [s for s in all_stores if s["prefix"].upper() == store_filter]
        if not stores:
            log(f"❌ Magazin negăsit: {store_filter}")
            log(f"   Magazine disponibile: {', '.join(s['prefix'] for s in all_stores)}")
            return {}
        log(f"🔄 Sincronizez magazinul {store_filter}...\n")
    else:
        stores = all_stores
        log(f"🔄 Sincronizez {len(stores)} magazine...\n")

    # When syncing a single store, load existing DB to preserve other stores' data
    db: Dict[str, Any] = {
        "last_sync": None,
        "stores": {},
        "barcodes": {},  # sku -> {barcode, stores: [...]}
        "stats": {},
    }

    if store_filter and output_path.exists():
        try:
            with open(str(output_path), "r", encoding="utf-8") as f:
                db = json.load(f)
            # Remove old data for the filtered store from barcodes
            for sku in list(db.get("barcodes", {}).keys()):
                entry = db["barcodes"][sku]
                entry["stores"] = [
                    s for s in entry.get("stores", [])
                    if s.get("prefix", "").upper() != store_filter
                ]
                if not entry["stores"]:
                    del db["barcodes"][sku]
            # Remove old store entry
            db["stores"].pop(store_filter, None)
            for k in list(db.get("stores", {}).keys()):
                if k.upper() == store_filter:
                    db["stores"].pop(k, None)
            log(f"  ℹ DB existent încărcat — actualizez doar {store_filter}\n")
        except Exception:
            pass

    total_variants = 0
    total_with_barcode = 0
    total_without_barcode = 0

    for store in stores:
        prefix = store["prefix"]
        shop = store["shop"]
        token = store["token"]

        log(f"📦 [{prefix}] {shop}...")

        try:
            variants = fetch_all_variants(shop, token)
        except Exception as e:
            log(f"  ❌ Eroare: {e}")
            db["stores"][prefix] = {"shop": shop, "error": str(e), "count": 0}
            continue

        store_with_bc = 0
        store_without_bc = 0

        for v in variants:
            sku = v["sku"]
            barcode_val = v["barcode"]

            if barcode_val:
                store_with_bc += 1
            else:
                store_without_bc += 1

            # Merge into global barcodes dict
            if sku not in db["barcodes"]:
                db["barcodes"][sku] = {
                    "barcode": barcode_val or "",
                    "product_title": v["product_title"],
                    "stores": [],
                }

            # Add store reference (includes per-store barcode for push comparison)
            db["barcodes"][sku]["stores"].append({
                "prefix": prefix,
                "variant_id": v["variant_id"],
                "variant_title": v["variant_title"],
                "status": v["status"],
                "barcode": barcode_val,
            })

            # Update barcode if we found one and existing is empty
            if barcode_val and not db["barcodes"][sku]["barcode"]:
                db["barcodes"][sku]["barcode"] = barcode_val

        db["stores"][prefix] = {
            "shop": shop,
            "total": len(variants),
            "with_barcode": store_with_bc,
            "without_barcode": store_without_bc,
        }

        total_variants += len(variants)
        total_with_barcode += store_with_bc
        total_without_barcode += store_without_bc

        log(f"  ✓ {len(variants)} variante ({store_with_bc} cu barcode, {store_without_bc} fără)")
        time.sleep(0.5)

    # Stats
    import datetime
    db["last_sync"] = datetime.datetime.now().isoformat()
    if store_filter:
        db["last_sync_store"] = store_filter
    db["stats"] = {
        "total_stores": len(db["stores"]),
        "total_skus": len(db["barcodes"]),
        "total_variants": total_variants,
        "with_barcode": total_with_barcode,
        "without_barcode": total_without_barcode,
    }

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output_path), "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    log(f"\n{'='*50}")
    if store_filter:
        log(f"✅ Sync complet pentru {store_filter}!")
    else:
        log(f"✅ Sync complet!")
    log(f"   Magazine: {len(db['stores'])}")
    log(f"   SKU-uri unice: {len(db['barcodes'])}")
    log(f"   Cu barcode: {total_with_barcode}")
    log(f"   Fără barcode: {total_without_barcode}")
    log(f"   Salvat în: {output_path}")

    return db


def detect_duplicates(barcodes_db: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect duplicate barcodes (same barcode assigned to different SKUs)."""
    barcode_to_skus: Dict[str, List[Dict[str, str]]] = {}

    for sku, info in barcodes_db.get("barcodes", {}).items():
        bc = (info.get("barcode") or "").strip()
        if not bc:
            continue
        if bc not in barcode_to_skus:
            barcode_to_skus[bc] = []
        barcode_to_skus[bc].append({
            "sku": sku,
            "product_title": info.get("product_title", ""),
            "stores": [s.get("prefix", "") for s in info.get("stores", [])],
        })

    duplicates = []
    for bc, sku_list in sorted(barcode_to_skus.items()):
        if len(sku_list) >= 2:
            duplicates.append({"barcode": bc, "skus": sku_list})

    return duplicates


def print_duplicates_report(duplicates: List[Dict[str, Any]]) -> None:
    """Print a human-readable report of duplicate barcodes."""
    if not duplicates:
        log("\n✅ Nu s-au găsit barcode-uri duplicate.")
        return

    log(f"\n{'='*55}")
    log(f"⚠ BARCODE-URI DUPLICATE ({len(duplicates)} barcode-uri pe SKU-uri diferite):")
    log(f"{'─'*55}")

    for dup in duplicates:
        log(f"\n  Barcode: {dup['barcode']}")
        for entry in dup["skus"]:
            stores_str = ", ".join(entry["stores"]) if entry["stores"] else "—"
            log(f"    → SKU: {entry['sku']}  ({entry['product_title']})  [{stores_str}]")

    log(f"\n{'─'*55}")
    log(f"  Total: {len(duplicates)} barcode-uri duplicate")
    log(f"{'='*55}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincronizează barcode-uri din toate magazinele Shopify")
    parser.add_argument("--stores", default="stores.csv", help="Fișier stores.csv")
    parser.add_argument("--output", default="barcodes_db.json", help="Fișier de ieșire JSON")
    parser.add_argument("--copy-to", default="", help="Copie suplimentară (path global pt alte scripturi)")
    parser.add_argument("--store", default="", help="Prefix magazin (ex: GEN) — sincronizează doar acest magazin")

    args = parser.parse_args()
    workdir = Path.cwd()

    stores_path = Path(args.stores)
    if not stores_path.is_absolute():
        stores_path = workdir / stores_path
    if not stores_path.exists():
        stores_path = Path(__file__).parent / "stores.csv"

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = workdir / output_path

    store_filter = args.store.strip().upper() if args.store else ""
    db = sync_all_stores(stores_path, output_path, store_filter=store_filter)

    # Detect and report duplicates
    if db:
        duplicates = detect_duplicates(db)
        db["duplicates"] = duplicates
        # Re-save with duplicates included
        with open(str(output_path), "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        print_duplicates_report(duplicates)

    # Copy to global location so other scripts (generate_labels) can find it
    if args.copy_to:
        import shutil
        copy_dest = Path(args.copy_to)
        try:
            shutil.copy2(str(output_path), str(copy_dest))
            log(f"📋 Copie salvată: {copy_dest}")
        except Exception as e:
            log(f"⚠ Nu am putut copia la {copy_dest}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
