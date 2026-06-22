import argparse
import csv
import os
import sys
import time
from pathlib import Path

import requests

# Use core.stores if available, fallback to manual CSV
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from core.stores import get_store as _core_get_store
except ImportError:
    _core_get_store = None

STORES_CSV = "stores.csv"
API_VERSION = "2026-01"
PRODUCTS_PER_PAGE = 100
TIMEOUT = 30

# GraphQL query to fetch products with their handle, variants (SKUs), and online store URL
GRAPHQL_QUERY = """
query GetProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        handle
        onlineStoreUrl
        status
        tags
        featuredImage {
          url
        }
        variants(first: 100) {
          nodes {
            sku
            barcode
            title
            price
            inventoryItem {
              unitCost {
                amount
                currencyCode
              }
            }
          }
        }
      }
    }
  }
}
"""

# All extractable fields
ALL_FIELDS = ["barcode", "url", "price", "cost", "image"]


def load_store(prefix: str, csv_path: str = STORES_CSV) -> dict:
    """Load store config — uses core.stores if available."""
    if _core_get_store is not None:
        store = _core_get_store(prefix)
        if store:
            return store
        raise ValueError(f"Prefixul '{prefix}' nu a fost găsit")

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Nu exista fisierul {csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("prefix") or "").strip().upper() == prefix.upper():
                shop = (row.get("shop") or "").strip().replace("https://", "").replace("http://", "").strip("/")
                token = (row.get("token") or "").strip()
                if not shop or not token:
                    raise ValueError(f"Magazinul {prefix} fara shop/token in stores.csv")
                return {"prefix": prefix.upper(), "shop": shop, "token": token}
    raise ValueError(f"Prefixul '{prefix}' nu a fost gasit in {csv_path}")

def load_all_stores(csv_path: str = STORES_CSV) -> list[dict]:
    """Load all stores from the CSV file."""
    stores = []
    if not os.path.exists(csv_path):
        return stores
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            prefix = (row.get("prefix") or "").strip().upper()
            shop = (row.get("shop") or "").strip().replace("https://", "").replace("http://", "").strip("/")
            token = (row.get("token") or "").strip()
            if prefix and shop and token:
                stores.append({"prefix": prefix, "shop": shop, "token": token})
    return stores


def graphql_request(shop: str, token: str, query: str, variables: dict) -> dict:
    """Execute a Shopify GraphQL request."""
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    response = requests.post(
        url, headers=headers,
        json={"query": query, "variables": variables},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data


def get_store_domain(shop: str, token: str) -> str:
    """
    Try to find the primary domain from Shopify.
    Falls back to the myshopify.com domain.
    """
    query = """
    {
      shop {
        primaryDomain {
          url
          host
        }
        myshopifyDomain
      }
    }
    """
    try:
        data = graphql_request(shop, token, query, {})
        shop_data = data.get("data", {}).get("shop", {})
        primary = shop_data.get("primaryDomain", {})
        if primary.get("url"):
            # primary domain URL e.g. "https://rossinails.ro"
            return primary["url"].rstrip("/")
        return f"https://{shop_data.get('myshopifyDomain', shop)}"
    except Exception:
        return f"https://{shop}"


def iter_all_products(shop: str, token: str):
    """Iterate over all products from the store using pagination."""
    after = None
    while True:
        variables = {"first": PRODUCTS_PER_PAGE, "after": after}
        try:
            data = graphql_request(shop, token, GRAPHQL_QUERY, variables)
            products = data["data"]["products"]
        except Exception as e:
            print(f"   ❌ Eroare API ({shop}): {e}")
            return

        for edge in products["edges"]:
            yield edge["node"]

        if not products["pageInfo"]["hasNextPage"]:
            break
        after = products["pageInfo"]["endCursor"]


def parse_skus(raw: str) -> list[str]:
    """Parse SKUs from a string (newline, comma, or semicolon separated)."""
    # Replace commas and semicolons with newlines, then split
    raw = raw.replace(",", "\n").replace(";", "\n")
    skus = []
    for line in raw.splitlines():
        s = line.strip()
        if s:
            skus.append(s)
    return skus


def main():
    parser = argparse.ArgumentParser(description="SKU → Product URL Finder")
    parser.add_argument("--store", default="ALL", help="Store prefix (e.g., CARP, BON) or ALL to search in all stores")
    parser.add_argument("--skus", default="", help="SKUs separated by newline/comma")
    parser.add_argument("--skus-file", default="", help="File with SKUs (one per line)")
    parser.add_argument("--export-all", action="store_true", help="Export all products (ignore SKUs)")
    parser.add_argument("--match-partial", action="store_true", help="Match SKUs partially (contains)")
    parser.add_argument("--find-all", action="store_true", help="Găsește toate variantele/produsele pentru un SKU, nu doar primul")
    parser.add_argument("--export-csv", action="store_true", help="Export results to CSV")
    parser.add_argument("--output", default="", help="Output CSV file name")
    parser.add_argument("--stores-csv", default=STORES_CSV, help="Path to stores.csv")
    parser.add_argument("--fields", default="url", help="Comma-separated fields to extract: url, price, cost")
    args = parser.parse_args()

    # Parse requested fields
    requested_fields = [f.strip().lower() for f in args.fields.split(",") if f.strip()]
    if not requested_fields:
        requested_fields = ["url"]
    want_url = "url" in requested_fields
    want_price = "price" in requested_fields
    want_cost = "cost" in requested_fields
    want_image = "image" in requested_fields
    want_barcode = "barcode" in requested_fields
    want_sku = "sku" in requested_fields

    # Load stores
    if args.store.upper() == "ALL":
        stores_list = load_all_stores(args.stores_csv)
    else:
        stores_list = [load_store(args.store, args.stores_csv)]

    if not stores_list:
        print("❌ Niciun magazin găsit!")
        sys.exit(1)
        
    if len(stores_list) > 1 and "store" not in requested_fields:
        requested_fields.insert(0, "store")

    # Parse SKUs
    target_skus = []
    if args.skus:
        target_skus = parse_skus(args.skus)
    if args.skus_file and os.path.exists(args.skus_file):
        with open(args.skus_file, "r", encoding="utf-8") as f:
            target_skus.extend(parse_skus(f.read()))

    if not target_skus and not args.export_all:
        print("❌ Niciun SKU furnizat! Foloseste --skus, --skus-file sau --export-all.")
        sys.exit(1)

    # Normalize SKUs for case-insensitive matching
    target_set = {sku.strip().upper() for sku in target_skus}
    if args.export_all:
        print("🔍 Extrag TOATE produsele din magazin...")
    elif args.match_partial:
        print(f"🔍 Caut {len(target_set)} termeni (potrivire parțială)...")
    else:
        print(f"🔍 Caut {len(target_set)} SKU-uri exacte...")
    print()

    fields_label = ", ".join(requested_fields)
    print(f"📦 Incarc produsele din Shopify... (câmpuri: {fields_label})")
    t0 = time.time()

    # Build SKU → product mapping
    sku_map: dict[str, list[dict]] = {}
    total_products = 0
    total_variants = 0

    for store in stores_list:
        shop = store["shop"]
        token = store["token"]
        prefix = store["prefix"]
        
        base_url = get_store_domain(shop, token)
        print(f"🏪 Caut în: {prefix} ({shop}) -> {base_url}")
        
        store_found = 0
        
        for product in iter_all_products(shop, token):
            total_products += 1
            raw_gid = product.get("id", "")
            # Extract numeric product ID from gid://shopify/Product/123456
            product_id = raw_gid.split("/")[-1] if raw_gid else ""
            handle = product.get("handle", "")
            title = product.get("title", "")
            status = product.get("status", "ACTIVE")
            tags = product.get("tags", [])
            online_url = product.get("onlineStoreUrl", "")
            featured_image = product.get("featuredImage") or {}
            image_url = featured_image.get("url", "")

            variants = product.get("variants", {}).get("nodes", []) or []
            for variant in variants:
                total_variants += 1
                sku = (variant.get("sku") or "").strip()
                barcode = (variant.get("barcode") or "").strip()
                if not sku and not barcode:
                    continue
                
                sku_upper = sku.upper()
                barcode_upper = barcode.upper()
                handle_upper = handle.upper()
                pid_upper = product_id.upper()

                is_match = False
                matched_terms = []
            
                if args.export_all:
                    is_match = True
                    matched_terms.append(sku_upper if sku_upper else barcode_upper)
                elif args.match_partial:
                    for t in target_set:
                        if (sku_upper and t in sku_upper) or (barcode_upper and t in barcode_upper) or (handle_upper and t in handle_upper) or (pid_upper and t in pid_upper):
                            is_match = True
                            matched_terms.append(t)
                else:
                    if sku_upper and sku_upper in target_set:
                        is_match = True
                        matched_terms.append(sku_upper)
                    if barcode_upper and barcode_upper in target_set:
                        is_match = True
                        matched_terms.append(barcode_upper)
                    # Also match by handle
                    if handle_upper and handle_upper in target_set:
                        is_match = True
                        matched_terms.append(handle_upper)
                    # Also match by product ID
                    if pid_upper and pid_upper in target_set:
                        is_match = True
                        matched_terms.append(pid_upper)

                if is_match:
                    # Build URL: prefer onlineStoreUrl, fallback to constructed URL
                    if online_url:
                        product_url = online_url
                    elif handle:
                        product_url = f"{base_url}/products/{handle}"
                    else:
                        product_url = "N/A"

                    # Extract price
                    price = variant.get("price", "")

                    # Extract cost (COGS) from inventoryItem.unitCost
                    inv_item = variant.get("inventoryItem") or {}
                    unit_cost = inv_item.get("unitCost") or {}
                    cost = unit_cost.get("amount", "")
                    cost_currency = unit_cost.get("currencyCode", "")

                    info = {
                        "sku_original": sku,
                        "barcode": barcode,
                        "product_id": product_id,
                        "title": title,
                        "variant_title": variant.get("title", ""),
                        "handle": handle,
                        "url": product_url,
                        "image": image_url,
                        "status": status,
                        "tags": ", ".join(tags) if tags else "",
                        "price": price,
                        "cost": cost,
                        "cost_currency": cost_currency,
                        "store": prefix,
                    }
                    
                    for t in matched_terms:
                        if t not in sku_map:
                            sku_map[t] = []
                        if args.find_all:
                            sku_map[t].append(info)
                            store_found += 1
                        elif len(sku_map[t]) == 0:
                            sku_map[t].append(info)
                            store_found += 1

            # Progress every 500 products
            if total_products % 500 == 0:
                print(f"   ... {total_products} produse scanate, {len(sku_map)}/{len(target_set)} gasite")

        # Early exit if we found all (and we don't want to find all occurrences)
        if not args.export_all and not args.match_partial and not args.find_all and len(sku_map) >= len(target_set):
            break

    elapsed = time.time() - t0
    print(f"   ✅ Scanat {total_products} produse / {total_variants} variante in {elapsed:.1f}s")
    print()

    # Output results
    found = 0
    not_found = 0
    not_found_skus = []
    results = []

    if args.export_all or args.match_partial:
        # Output everything we found
        target_skus = []
        for t, infos in sku_map.items():
            target_skus.append(t)
            
    # We will iterate over target_skus. For export_all/match_partial, target_skus contains the keys of sku_map.

    for sku in target_skus:
        sku_upper = sku.strip().upper()
        infos = sku_map.get(sku_upper)

        if infos:
            for info in infos:
                found += 1
                row = {}
                if len(stores_list) > 1:
                    row["store"] = info.get("store", "")
                if want_sku:
                    row["sku"] = info["sku_original"] or sku
                if want_barcode:
                    row["barcode"] = info["barcode"]
                row.update({
                    "product_id": info.get("product_id", ""),
                    "title": info["title"],
                    "variant": info["variant_title"],
                    "handle": info["handle"],
                    "url": info["url"] if want_url else "",
                    "image": info["image"] if want_image else "",
                    "status": info["status"],
                    "tags": info.get("tags", ""),
                })
                if want_price:
                    row["price"] = info["price"]
                if want_cost:
                    row["cost"] = info["cost"]
                    row["cost_currency"] = info["cost_currency"]
                results.append(row)
        else:
            not_found += 1
            not_found_skus.append(sku)
            row = {}
            if len(stores_list) > 1:
                row["store"] = ""
            if want_sku:
                row["sku"] = sku
            if want_barcode:
                row["barcode"] = ""
            row.update({
                "product_id": "",
                "title": "",
                "variant": "",
                "handle": "",
                "url": "NEGASIT",
                "image": "",
                "status": "",
                "tags": "",
            })
            if want_price:
                row["price"] = ""
            if want_cost:
                row["cost"] = ""
                row["cost_currency"] = ""
            results.append(row)

    # Only show problems
    if not_found_skus:
        print(f"❌ Negăsite ({not_found}): {', '.join(not_found_skus)}")

    print(f"📊 Rezultate: {found} găsite, {not_found} negăsite din {len(target_skus)} total")

    # Send results as JSON for frontend table rendering
    import json as _json
    meta = {"fields": requested_fields, "results": results}
    print(f"###SKU_URL_RESULTS###{_json.dumps(meta, ensure_ascii=False)}")

    # Save CSV output (only if requested)
    if args.export_csv:
        if args.output:
            output_path = args.output
        else:
            if args.store == "ALL":
                output_path = "sku_urls_ALL.csv"
            else:
                output_path = f"sku_urls_{args.store}.csv"

        fieldnames = []
        if len(stores_list) > 1:
            fieldnames.append("store")
        if want_sku:
            fieldnames.append("sku")
        if want_barcode:
            fieldnames.append("barcode")
        fieldnames.extend(["product_id", "title", "variant", "handle"])
        
        if want_url:
            fieldnames.append("url")
        if want_image:
            fieldnames.append("image")
        fieldnames.append("status")
        if want_price:
            fieldnames.append("price")
        if want_cost:
            fieldnames.extend(["cost", "cost_currency"])

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)

        print(f"💾 Salvat: {output_path}")


if __name__ == "__main__":
    main()
