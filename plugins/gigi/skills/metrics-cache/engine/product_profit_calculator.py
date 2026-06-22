#!/usr/bin/env python3
import os
import sys
import csv
import time
import argparse
import requests

try:
    import pandas as pd
except ImportError:
    print("❌ EROARE: Modulul 'pandas' lipsește. (pip install pandas openpyxl)")
    sys.exit(1)

# Ensure script runs from project root for imports if needed
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

try:
    from core.stores import get_store as _core_get_store
except ImportError:
    _core_get_store = None

STORES_CSV = "stores.csv"
API_VERSION = "2024-04"
TIMEOUT = 30
PRODUCTS_PER_PAGE = 50

GRAPHQL_QUERY = """
query getProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        handle
        title
        status
        tags
        onlineStoreUrl
        featuredImage {
          url
        }
        variants(first: 100) {
          nodes {
            sku
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

def load_store(prefix: str, csv_path: str = STORES_CSV) -> dict:
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


def graphql_request(shop: str, token: str, query: str, variables: dict) -> dict:
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


def iter_all_products(shop: str, token: str):
    after = None
    while True:
        variables = {"first": PRODUCTS_PER_PAGE, "after": after}
        data = graphql_request(shop, token, GRAPHQL_QUERY, variables)
        products = data["data"]["products"]

        for edge in products["edges"]:
            yield edge["node"]

        if not products["pageInfo"]["hasNextPage"]:
            break
        after = products["pageInfo"]["endCursor"]


def parse_skus(raw: str) -> list[str]:
    raw = raw.replace(",", "\n").replace(";", "\n")
    skus = []
    for line in raw.splitlines():
        s = line.strip()
        if s:
            skus.append(s)
    return skus

def main():
    parser = argparse.ArgumentParser(description="Product Profitability Calculator")
    parser.add_argument("--store", required=True)
    parser.add_argument("--skus", default="")
    parser.add_argument("--export-all", action="store_true")
    parser.add_argument("--match-partial", action="store_true")
    parser.add_argument("--exclude-tags", default="test, sample")
    parser.add_argument("--price-multiplier", type=int, default=1)
    parser.add_argument("--cogs-multiplier", type=int, default=1)
    parser.add_argument("--transport-cost", type=float, default=15.0)
    parser.add_argument("--client-shipping-cost", type=float, default=0.0)
    parser.add_argument("--free-shipping-threshold", type=float, default=0.0)
    parser.add_argument("--marketing-cost", type=float, default=30.0)
    # SIMULATOR pre-lansare/prag CPA (unit-economics din catalog, NU profit realizat). TVA RO = 21% din 2025
    # (era 19% — învechit). Cotele canonice per țară: profit_core.VAT_BY_COUNTRY.
    parser.add_argument("--vat-rate", type=float, default=21.0)
    parser.add_argument("--cogs-has-vat", action="store_true")
    parser.add_argument("--bundle", default="")
    parser.add_argument("--output", default="profit_calculator.xlsx")
    parser.add_argument("--stores-csv", default=STORES_CSV)
    
    args = parser.parse_args()
    
    # Bundle presets override multipliers
    if args.bundle == "2+1":
        args.price_multiplier = 2
        args.cogs_multiplier = 3
        print(f"🎁 Ofertă 2+1 Free: Preț ×2, COGS ×3")
    
    exclude_tags = [t.strip().lower() for t in args.exclude_tags.split(",") if t.strip()]
    vat_multiplier = 1 + (args.vat_rate / 100)
    
    store = load_store(args.store, args.stores_csv)
    shop = store["shop"]
    token = store["token"]
    prefix = store["prefix"]

    target_skus = []
    if args.skus:
        target_skus = parse_skus(args.skus)

    if not target_skus and not args.export_all:
        print("❌ Niciun SKU furnizat! Foloseste --skus sau --export-all.")
        sys.exit(1)

    target_set = {sku.strip().upper() for sku in target_skus}
    
    print(f"🏪 Magazin: {prefix} ({shop})")
    print(f"💸 Calcul: Încasat Transp. {args.client_shipping_cost} RON | Plătit Transp. {args.transport_cost} RON | Marketing {args.marketing_cost} RON | TVA {args.vat_rate}%")
    print(f"🏷️ Tag-uri ignorate: {', '.join(exclude_tags)}")
    print()
    
    t0 = time.time()
    results = []
    total_products = 0
    total_variants = 0
    
    # Load DPD Audit data
    dpd_nomenclator = {}
    dpd_file = os.path.join(script_dir, "data", "dpd_nomenclator.json")
    if os.path.exists(dpd_file):
        try:
            import json
            with open(dpd_file, "r", encoding="utf-8") as f:
                # Key dictionary by uppercase SKU for case-insensitive matching
                raw_dpd = json.load(f)
                if isinstance(raw_dpd, list):
                    dpd_nomenclator = {str(item.get("sku", "")).upper(): item for item in raw_dpd if item.get("sku")}
                else:
                    dpd_nomenclator = {str(k).upper(): v for k, v in raw_dpd.items()}
        except Exception as e:
            print(f"⚠️ Nu am putut încărca DPD Nomenclator: {e}")
            
    print(f"📦 Scanez produsele...")
    
    for product in iter_all_products(shop, token):
        total_products += 1
        
        # Check Tags
        prod_tags = [t.strip().lower() for t in product.get("tags", [])]
        skip_due_to_tag = False
        for ext in exclude_tags:
            if ext in prod_tags:
                skip_due_to_tag = True
                break
                
        if skip_due_to_tag:
            continue
            
        title = product.get("title", "")
        status = product.get("status", "ACTIVE")
        variants = product.get("variants", {}).get("nodes", []) or []
        
        for variant in variants:
            total_variants += 1
            sku = (variant.get("sku") or "").strip()
            if not sku:
                continue
            sku_upper = sku.upper()
            
            # Match logic
            is_match = False
            if args.export_all:
                is_match = True
            elif args.match_partial:
                for t in target_set:
                    if t in sku_upper:
                        is_match = True
                        break
            else:
                if sku_upper in target_set:
                    is_match = True
                    
            if is_match:
                price_str = variant.get("price", "0")
                try:
                    price = float(price_str)
                except:
                    price = 0.0
                    
                inv_item = variant.get("inventoryItem") or {}
                unit_cost = inv_item.get("unitCost") or {}
                cost_str = unit_cost.get("amount", "0")
                try:
                    cogs = float(cost_str)
                except:
                    cogs = 0.0
                    
                # Shipping from DPD nomenclature
                sursa_transport = "Manual"
                dpd_net_cost = 0.0
                if sku_upper in dpd_nomenclator:
                    dpd_net_cost = float(dpd_nomenclator[sku_upper].get("avg_transport_cost", 0.0))

                def calc_metrics(p_mult, c_mult):
                    gross_product_price = price * p_mult
                    
                    gross_client_shipping = args.client_shipping_cost
                    if args.free_shipping_threshold > 0 and gross_product_price >= args.free_shipping_threshold:
                        gross_client_shipping = 0.0
                        
                    gross_total_revenue = gross_product_price + gross_client_shipping
                    gross_cogs = cogs * c_mult
                    gross_marketing = args.marketing_cost
                    
                    if dpd_net_cost > 0:
                        net_courier_transport = dpd_net_cost
                        gross_courier_transport = net_courier_transport * vat_multiplier
                        s_trans = "DPD Audit"
                    else:
                        gross_courier_transport = args.transport_cost
                        net_courier_transport = gross_courier_transport / vat_multiplier
                        s_trans = "Manual"
                    
                    net_product_price = gross_product_price / vat_multiplier
                    net_client_shipping = gross_client_shipping / vat_multiplier
                    net_total_revenue = gross_total_revenue / vat_multiplier
                    
                    if args.cogs_has_vat:
                        net_cogs = gross_cogs / vat_multiplier
                    else:
                        net_cogs = gross_cogs
                        
                    net_marketing = args.marketing_cost
                    
                    tva_colectat = gross_total_revenue - net_total_revenue
                    tva_deductibil = 0.0
                    if args.cogs_has_vat:
                        tva_deductibil += (gross_cogs - net_cogs)
                    tva_deductibil += (gross_courier_transport - net_courier_transport)
                    tva_deductibil += (gross_marketing - net_marketing)
                    
                    tva_plata = tva_colectat - tva_deductibil
                    
                    net_profit = net_total_revenue - net_cogs - net_courier_transport - net_marketing
                    margin_pct = (net_profit / net_total_revenue * 100) if net_total_revenue > 0 else 0
                    
                    cpa_20_net = net_total_revenue * (1 - 0.20) - net_cogs - net_courier_transport
                    cpa_20_gross = cpa_20_net * vat_multiplier
                    if cpa_20_gross < 0: cpa_20_gross = 0.0
                    
                    cpa_30_net = net_total_revenue * (1 - 0.30) - net_cogs - net_courier_transport
                    cpa_30_gross = cpa_30_net * vat_multiplier
                    if cpa_30_gross < 0: cpa_30_gross = 0.0
                    
                    return {
                        "gross_product_price": gross_product_price,
                        "gross_client_shipping": gross_client_shipping,
                        "gross_total_revenue": gross_total_revenue,
                        "gross_cogs": gross_cogs,
                        "gross_courier_transport": gross_courier_transport,
                        "gross_marketing": gross_marketing,
                        "tva_plata": tva_plata,
                        "net_profit": net_profit,
                        "margin_pct": margin_pct,
                        "cpa_20": cpa_20_gross,
                        "cpa_30": cpa_30_gross,
                        "sursa_transport": s_trans
                    }
                
                # Base multipliers from args (default 1, 1 if not overridden via old bundle param)
                base = calc_metrics(args.price_multiplier, args.cogs_multiplier)
                
                # 2+1 Free is Price x 2, COGS x 3
                bundle = calc_metrics(args.price_multiplier * 2, args.cogs_multiplier * 3)
                
                merita = "✅ Da" if bundle["net_profit"] > base["net_profit"] else "❌ Nu"
                
                results.append({
                    "SKU": sku,
                    "Titlu Produs": title,
                    "Status": status,
                    "Preț Brut (1 buc)": round(base["gross_product_price"], 2),
                    "Preț Brut (2+1)": round(bundle["gross_product_price"], 2),
                    "Trans. Client Brut": round(base["gross_client_shipping"], 2),
                    "Total Încasat (1 buc)": round(base["gross_total_revenue"], 2),
                    "Total Încasat (2+1)": round(bundle["gross_total_revenue"], 2),
                    "COGS Brut (1 buc)": round(base["gross_cogs"], 2),
                    "COGS Brut (2+1)": round(bundle["gross_cogs"], 2),
                    "Sursă Transport": base["sursa_transport"],
                    "Trans. Curier Brut": round(base["gross_courier_transport"], 2),
                    "Marketing Brut": round(base["gross_marketing"], 2),
                    "TVA (1 buc)": round(base["tva_plata"], 2),
                    "TVA (2+1)": round(bundle["tva_plata"], 2),
                    "PROFIT NET (1 buc)": round(base["net_profit"], 2),
                    "PROFIT NET (2+1)": round(bundle["net_profit"], 2),
                    "Marjă Profit (1 buc) %": round(base["margin_pct"], 2),
                    "Marjă Profit (2+1) %": round(bundle["margin_pct"], 2),
                    "CPA 20% (1 buc)": round(base["cpa_20"], 2),
                    "CPA 20% (2+1)": round(bundle["cpa_20"], 2),
                    "CPA 30% (1 buc)": round(base["cpa_30"], 2),
                    "CPA 30% (2+1)": round(bundle["cpa_30"], 2),
                    "Merită 2+1?": merita
                })
                
        if total_products % 500 == 0:
            print(f"   ... {total_products} produse scanate, {len(results)} matches găsite")
            
    elapsed = time.time() - t0
    print(f"   ✅ Scanat {total_products} produse in {elapsed:.1f}s")
    
    if not results:
        print("⚠️ Nu s-au găsit produse care să corespundă criteriilor.")
        # Create an empty excel anyway
        df = pd.DataFrame(columns=[
            "SKU", "Titlu Produs", "Status",
            "Preț Brut (1 buc)", "Trans. Client Brut", "Total Încasat Brut",
            "COGS Brut (1 buc)", "Sursă Transport", "Trans. Curier Brut",
            "Marketing Brut", "TVA de Plată (1 buc)", "PROFIT NET (1 buc)",
            "Marjă Profit (1 buc) %", "Max CPA 20% (1 buc)", "Max CPA 30% (1 buc)",
            "---", "Preț Brut (2+1)", "Total Încasat (2+1)", "COGS Brut (2+1)",
            "TVA de Plată (2+1)", "PROFIT NET (2+1)", "Marjă Profit (2+1) %",
            "Max CPA 20% (2+1)", "Max CPA 30% (2+1)", "Merită 2+1?"
        ])
        df.to_excel(args.output, index=False)
        return
        
    df = pd.DataFrame(results)
    # Sort descending by Profit Net
    df = df.sort_values(by="PROFIT NET (1 buc)", ascending=False)
    
    try:
        df.to_excel(args.output, index=False)
        print(f"💾 Salvat cu succes în: {args.output}")
    except Exception as e:
        print(f"❌ Eroare la salvarea XLSX: {e}")
        # Fallback to CSV
        df.to_csv(args.output.replace(".xlsx", ".csv"), index=False)
        print(f"💾 Salvat ca CSV în schimb.")
        
    # Output JSON for UI rendering
    import json
    print("###PROFIT_CALC_RESULTS###" + json.dumps(results))

if __name__ == "__main__":
    main()
