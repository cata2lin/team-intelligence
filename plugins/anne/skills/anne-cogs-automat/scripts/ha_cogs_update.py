# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "google-auth", "google-auth-oauthlib", "google-api-python-client"]
# ///
"""
ha-cogs-update — Adaugare COGS automat HA pe cele 4 magazine deals.

Formula: (TOM Real COGS $ + TOM Shipping Cost $) x USD x 1.10 x 1.21

Magazine: OFER (ofertelezilei) | RED (audusp-rf) | BON (bonhaus) | MAG (covoareauto-ro)
"""
import sys
import argparse
import requests
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Configurare magazine deals HA ---
STORES = [
    {"prefix": "OFER", "domain": "ofertelezilei.myshopify.com",   "token_key": "SHOPIFY_TOKEN_OFER"},
    {"prefix": "RED",  "domain": "audusp-rf.myshopify.com",        "token_key": "SHOPIFY_TOKEN_RED"},
    {"prefix": "BON",  "domain": "bonhaus.myshopify.com",          "token_key": "SHOPIFY_TOKEN_BON"},
    {"prefix": "MAG",  "domain": "covoareauto-ro.myshopify.com",   "token_key": "SHOPIFY_TOKEN_MAG"},
]

TOM_SPREADSHEET_ID = "10eSCKItlCHMl8S5A2YGjBZBZwRe506HH0ETpgR7BV7A"
TOKEN_PATH = Path.home() / ".config/gcp/sheets-token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API_VERSION = "2026-04"

# --- Fetch tokens din KB sau fallback din env ---
def get_token(key: str) -> str:
    """Incearca kb.py secret-get, fallback la env."""
    import os, subprocess
    val = os.environ.get(key)
    if val:
        return val
    try:
        result = subprocess.run(
            ["uv", "run", "kb.py", "secret-get", key],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def load_stores():
    """Incarca tokenele pentru magazine din KB (secret-get) sau env."""
    for store in STORES:
        token = get_token(store["token_key"])
        if not token:
            print(f"EROARE: token lipsa pentru {store['prefix']} ({store['token_key']})")
            print(f"  Seteaza cu: kb.py secret-set {store['token_key']} <token>")
            sys.exit(1)
        store["token"] = token

# --- Google Sheets ---
def get_sheets_svc():
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return build("sheets", "v4", credentials=creds).spreadsheets()

def parse_dollar(s: str) -> float:
    """Parseaza '$1,30' sau '1.30' sau '1,30' -> float."""
    return float(str(s).replace("$", "").replace(",", ".").strip())

def find_col(header, keywords):
    """Gaseste coloana care contine toate keyword-urile (case insensitive)."""
    for i, h in enumerate(header):
        if all(k.lower() in str(h).lower() for k in keywords):
            return i
    for kw in keywords:
        for i, h in enumerate(header):
            if kw.lower() in str(h).lower():
                return i
    return None

def lookup_tom(skus: set, sheet_id: str) -> dict:
    """Cauta SKU-urile in toate tab-urile spreadsheet-ului TOM. Returneaza {sku: (cogs, ship, tab)}."""
    svc = get_sheets_svc()
    meta = svc.get(spreadsheetId=sheet_id, fields="sheets.properties").execute()
    results = {}

    for tab in meta["sheets"]:
        if len(results) == len(skus):
            break
        tab_title = tab["properties"]["title"]
        raw = svc.values().get(spreadsheetId=sheet_id, range=f"'{tab_title}'").execute()
        rows = raw.get("values", [])
        if not rows or len(rows) < 2:
            continue

        header = rows[0]
        sku_col  = find_col(header, ["sku"])
        cogs_col = find_col(header, ["real", "cogs"])
        ship_col = find_col(header, ["shipping", "cost"])

        if sku_col is None or (cogs_col is None and ship_col is None):
            continue

        for row in rows[1:]:
            if len(row) <= sku_col:
                continue
            sku = str(row[sku_col]).strip()
            if sku not in skus or sku in results:
                continue
            try:
                cogs = parse_dollar(row[cogs_col]) if cogs_col is not None and len(row) > cogs_col else 0.0
                ship = parse_dollar(row[ship_col]) if ship_col is not None and len(row) > ship_col else 0.0
                results[sku] = (cogs, ship, tab_title)
            except (ValueError, IndexError):
                continue

    return results

# --- Shopify GraphQL ---
FIND_Q = """query($sku: String!) {
  productVariants(first:5, query:$sku) {
    nodes { id sku product { id } inventoryItem { unitCost { amount } } }
  }
}"""

UPDATE_M = """mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants, allowPartialUpdates: true) {
    productVariants { id sku }
    userErrors { field message }
  }
}"""

def gql(domain, token, query, variables=None):
    r = requests.post(
        f"https://{domain}/admin/api/{API_VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}
    )
    r.raise_for_status()
    return r.json()

def scan_missing_cogs() -> list:
    """Returneaza lista de SKU-uri HA active fara COGS (fara tag test)."""
    print("Scanez magazinele pentru HA-uri fara COGS...", flush=True)
    SCAN_Q = """query($cursor: String) {
      productVariants(first: 250, after: $cursor, query: "sku:HA-*") {
        pageInfo { hasNextPage endCursor }
        nodes { sku product { tags } inventoryItem { unitCost { amount } } }
      }
    }"""
    missing_skus = set()
    for store in STORES:
        cursor = None
        while True:
            data = gql(store["domain"], store["token"], SCAN_Q, {"cursor": cursor})
            pv = data["data"]["productVariants"]
            for node in pv["nodes"]:
                sku = node.get("sku", "").strip()
                if not sku.startswith("HA-"):
                    continue
                tags = [t.lower() for t in node["product"].get("tags", [])]
                if "test" in tags:
                    continue
                cost = node.get("inventoryItem", {}).get("unitCost")
                cost_val = float(cost["amount"]) if cost else 0.0
                if cost_val == 0.0:
                    missing_skus.add(sku)
            if not pv["pageInfo"]["hasNextPage"]:
                break
            cursor = pv["pageInfo"]["endCursor"]
        print(f"  {store['prefix']}: scanat", flush=True)
    return sorted(missing_skus)

def push_cogs(skus: list, sheet_id: str, usd: float, apply: bool, stores: list):
    """Cauta in TOM, calculeaza si seteaza COGS pe Shopify."""
    print(f"\n{'[DRY RUN] ' if not apply else ''}Procesez {len(skus)} SKU-uri pe {[s['prefix'] for s in stores]}\n")

    print("Cautare in TOM spreadsheet...", flush=True)
    tom = lookup_tom(set(skus), sheet_id)

    found = {sku: tom[sku] for sku in skus if sku in tom}
    not_found = [sku for sku in skus if sku not in tom]

    for sku, (cogs, ship, tab) in found.items():
        ron = round((cogs + ship) * usd * 1.10 * 1.21, 2)
        print(f"  {sku}: ${cogs} + ${ship} → {ron} lei  [{tab}]")
    for sku in not_found:
        print(f"  {sku}: negasit in TOM — skip")

    if not found:
        print("\nNiciun SKU gasit in TOM. Verifica SKU-urile.")
        return

    print(f"\nRezultat {'(aplicat)' if apply else '(dry run)'}:")
    ok = err = 0
    for sku, (cogs, ship, _) in found.items():
        ron = str(round((cogs + ship) * usd * 1.10 * 1.21, 2))
        row = []
        for store in stores:
            nodes = gql(store["domain"], store["token"], FIND_Q, {"sku": f"sku:{sku}"})["data"]["productVariants"]["nodes"]
            match = next((n for n in nodes if n["sku"] == sku), None)
            if not match:
                row.append(f"{store['prefix']}: NOT FOUND")
                continue
            if not apply:
                row.append(f"{store['prefix']}: ar seta {ron} lei")
                continue
            upd = gql(store["domain"], store["token"], UPDATE_M, {
                "productId": match["product"]["id"],
                "variants": [{"id": match["id"], "inventoryItem": {"cost": ron}}]
            })
            errors = upd["data"]["productVariantsBulkUpdate"]["userErrors"]
            row.append(f"{store['prefix']}: {'ERR ' + errors[0]['message'] if errors else 'OK'}")

        all_ok = not apply or all("OK" in r for r in row)
        ok += all_ok; err += not all_ok
        print(f"  {sku}: {ron} lei | {' | '.join(row)}")

    if not_found:
        print(f"\nNegasite in TOM ({len(not_found)}): {', '.join(not_found)}")

    if apply:
        print(f"\nGata: {ok} OK, {err} erori.")
    else:
        print(f"\nRuleaza cu --apply ca sa aplici pe Shopify.")

ALL_PREFIXES = [s["prefix"] for s in STORES]

def main():
    parser = argparse.ArgumentParser(description="Adaugare COGS automat HA pe magazine deals")
    parser.add_argument("--skus", nargs="+", metavar="SKU", help="SKU-uri de procesat (ex: HA-0001 HA-0002)")
    parser.add_argument("--apply", action="store_true", help="Aplica efectiv pe Shopify (default: dry run)")
    parser.add_argument("--scan", action="store_true", help="Gaseste toate HA-urile fara COGS inainte")
    parser.add_argument("--usd", type=float, default=4.55, help="Cursul USD→RON (default: 4.55)")
    parser.add_argument("--sheet", default=TOM_SPREADSHEET_ID, help="Spreadsheet ID alternativ")
    parser.add_argument("--stores", nargs="+", metavar="STORE",
                        help=f"Magazine pe care sa pui COGS (default: toate). Optiuni: {ALL_PREFIXES}")
    args = parser.parse_args()

    load_stores()

    # Filtreaza magazinele daca s-a specificat --stores
    if args.stores:
        wanted = [p.upper() for p in args.stores]
        active_stores = [s for s in STORES if s["prefix"] in wanted]
        unknown = [p for p in wanted if p not in ALL_PREFIXES]
        if unknown:
            print(f"Magazine necunoscute: {unknown}. Disponibile: {ALL_PREFIXES}")
            sys.exit(1)
    else:
        active_stores = STORES

    skus = list(args.skus) if args.skus else []

    if args.scan:
        missing = scan_missing_cogs()
        print(f"\n{len(missing)} HA-uri fara COGS: {', '.join(missing)}\n")
        if not skus:
            skus = missing

    if not skus:
        print("Niciun SKU specificat. Foloseste --skus HA-0001 HA-0002 sau --scan.")
        parser.print_help()
        sys.exit(0)

    push_cogs(skus, args.sheet, args.usd, args.apply, active_stores)

if __name__ == "__main__":
    main()
