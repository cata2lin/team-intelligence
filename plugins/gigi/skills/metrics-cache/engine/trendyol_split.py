#!/usr/bin/env python3
"""
Trendyol Package Splitter
──────────────────────────
Automatically splits multi-quantity shipment packages into individual packages.
This is needed because DPD charges by weight and multi-item packages get
incorrectly weighed.

Usage:
    python trendyol_split.py                    # Dry run (show what would be split)
    python trendyol_split.py --execute          # Actually perform splits
    python trendyol_split.py --days 30          # Check last 30 days
    python trendyol_split.py --order 11120810910  # Split specific order
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

# ─── Trendyol API Config (din .env — fără secrete hardcodate) ───────
SELLER_ID = os.environ.get("TRENDYOL_SELLER_ID", "")
API_KEY = os.environ.get("TRENDYOL_API_KEY", "")
API_SECRET = os.environ.get("TRENDYOL_API_SECRET", "")
TOKEN = os.environ.get("TRENDYOL_TOKEN", "")
STORE_FRONT_CODE = os.environ.get("TRENDYOL_STORE_FRONT_CODE", "RO")

BASE_URL = "https://apigw.trendyol.com/integration"
HEADERS = {
    "Authorization": f"Basic {TOKEN}",
    "User-Agent": f"{SELLER_ID} - SelfIntegration",
    "Content-Type": "application/json",
    "storeFrontCode": STORE_FRONT_CODE,
}

RATE_LIMIT_DELAY = 0.3
# split-packages endpoint works on all statuses (Invoiced, Picking, Created, etc.)
SPLITTABLE_STATUSES = {"Picking", "Invoiced", "Created"}
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trendyol_split_log.json")


# ─── API Helpers ─────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)


def api_get(url, params=None, label=""):
    time.sleep(RATE_LIMIT_DELAY)
    try:
        r = session.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            print(f"  ⏳ Rate limited, waiting 10s...")
            time.sleep(10)
            return api_get(url, params, label)
        else:
            print(f"  ✗ {label}: HTTP {r.status_code} - {r.text[:200]}")
            return None
    except Exception as e:
        print(f"  ✗ {label}: {e}")
        return None


def api_post(url, payload, label=""):
    time.sleep(RATE_LIMIT_DELAY)
    try:
        r = session.post(url, json=payload, timeout=30)
        if r.status_code in (200, 201, 202):
            return {"ok": True, "status": r.status_code, "body": r.text[:500]}
        else:
            return {"ok": False, "status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


# ─── Find Multi-Quantity Packages ───────────────────────────────────
def find_multi_qty_packages(days=14, order_number=None):
    """Find all shipment packages with quantity > 1 that can be split."""
    print(f"🔍 Searching for multi-quantity packages...")

    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)

    results = []
    seen_packages = set()

    for page in range(100):
        params = {
            "startDate": epoch_ms(start),
            "endDate": epoch_ms(end),
            "page": page,
            "size": 200,
            "orderByField": "PackageLastModifiedDate",
            "orderByDirection": "DESC",
        }
        if order_number:
            params["orderNumber"] = order_number

        data = api_get(
            f"{BASE_URL}/order/sellers/{SELLER_ID}/orders",
            params=params,
            label=f"orders p{page}",
        )

        if not data or not data.get("content"):
            break

        for order in data["content"]:
            pkg_id = order.get("shipmentPackageId")
            if pkg_id in seen_packages:
                continue
            seen_packages.add(pkg_id)

            status = order.get("status", "")
            lines = order.get("lines", [])

            for line in lines:
                qty = line.get("quantity", 1)
                if qty > 1:
                    entry = {
                        "orderNumber": order.get("orderNumber"),
                        "packageId": pkg_id,
                        "status": status,
                        "lineId": line.get("id"),
                        "quantity": qty,
                        "barcode": line.get("barcode"),
                        "title": (line.get("productName") or "")[:60],
                        "lineStatus": line.get("orderLineItemStatusName"),
                        "splittable": status in SPLITTABLE_STATUSES and status != "Delivered" and status != "Shipped",
                    }
                    results.append(entry)

        total_pages = data.get("totalPages", 1)
        if page >= total_pages - 1:
            break

    # Sort: splittable first, then by qty descending
    results.sort(key=lambda r: (0 if r["splittable"] else 1, -r["quantity"]))
    return results


# ─── Split Package ──────────────────────────────────────────────────
def split_package(package_id, line_id, quantity=1):
    """
    Split a package by quantity using the split-packages endpoint.
    Works on Invoiced, Picking, Created statuses.
    
    POST /integration/order/sellers/{sellerId}/shipment-packages/{packageId}/split-packages
    Body: {
        "splitPackages": [{
            "packageDetails": [{
                "orderLineId": lineId,
                "quantities": quantity
            }]
        }],
        "shouldKeepPreviousStatus": true
    }
    """
    url = f"{BASE_URL}/order/sellers/{SELLER_ID}/shipment-packages/{package_id}/split-packages"
    payload = {
        "splitPackages": [
            {
                "packageDetails": [
                    {
                        "orderLineId": line_id,
                        "quantities": quantity,
                    }
                ]
            }
        ],
        "shouldKeepPreviousStatus": True,
    }
    return api_post(url, payload, label=f"split pkg={package_id}")


def log_split(entry):
    """Append a split result to the JSON log file."""
    log = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                log = json.load(f)
        except:
            log = []
    log.insert(0, entry)
    log = log[:500]  # Keep last 500
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def split_all_multi_qty(packages, execute=False, quiet=False):
    """
    Split all multi-quantity packages into singles.
    For qty=2: 1 split → 2 packages of 1
    For qty=N: (N-1) splits → N packages of 1
    
    NOTE: API split on Picking moves the entire orderLine to a new package
    (not quantity-based). For single-line packages with qty>1, each split
    call creates a new package. We need to re-check after each split.
    """
    splittable = [p for p in packages if p["splittable"]]

    if not splittable:
        if not quiet:
            print("\n✅ Nu sunt pachete de splituit.")
        return 0

    if not quiet:
        print(f"\n{'🚀 EXECUT' if execute else '🔍 SIMULARE'} split pe {len(splittable)} pachete:\n")

    total_splits = 0
    errors = 0

    for pkg in splittable:
        qty = pkg["quantity"]

        if not quiet:
            print(f"  📦 #{pkg['orderNumber']} | pkg={pkg['packageId']} | {pkg['barcode']}")
            print(f"     {pkg['title']}")
            print(f"     Qty: {qty}")

        if execute:
            result = split_package(pkg["packageId"], pkg["lineId"], quantity=1)

            if result["ok"]:
                total_splits += 1
                if not quiet:
                    print(f"     ✅ Split OK (HTTP {result['status']})")
                log_split({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "orderNumber": pkg["orderNumber"],
                    "packageId": pkg["packageId"],
                    "barcode": pkg["barcode"],
                    "qty_original": qty,
                    "status": "OK",
                })
                # Wait for async processing before next split
                time.sleep(2)
            else:
                errors += 1
                if not quiet:
                    print(f"     ❌ FAILED (HTTP {result['status']})")
                    print(f"        {result['body'][:200]}")
                log_split({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "orderNumber": pkg["orderNumber"],
                    "packageId": pkg["packageId"],
                    "barcode": pkg["barcode"],
                    "qty_original": qty,
                    "status": "FAILED",
                    "error": result["body"][:200],
                })
        else:
            total_splits += 1

    if not quiet:
        print(f"\n{'═' * 50}")
        if execute:
            print(f"✅ Splits executate: {total_splits}")
            print(f"❌ Erori: {errors}")
        else:
            print(f"📋 Total splits necesare: {total_splits}")
            print(f"   Folosește --execute pentru a le executa")

    return total_splits


# ─── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Trendyol Package Splitter")
    parser.add_argument("--days", type=int, default=14, help="Days to search back (default: 14)")
    parser.add_argument("--order", type=str, help="Split specific order number")
    parser.add_argument("--execute", action="store_true", help="Actually execute splits (default: dry run)")
    parser.add_argument("--cron", action="store_true", help="Cron mode: quiet, auto-execute, short window")
    args = parser.parse_args()

    if args.cron:
        # Cron mode: check last 2 days, auto-execute, minimal output
        packages = find_multi_qty_packages(days=2, order_number=None)
        splittable = [p for p in packages if p["splittable"]]
        if splittable:
            n = split_all_multi_qty(packages, execute=True, quiet=True)
            if n > 0:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Split {n} packages")
        return

    print("═" * 50)
    print("  📦 TRENDYOL PACKAGE SPLITTER")
    print(f"  Seller ID: {SELLER_ID}")
    print(f"  Mode: {'🚀 EXECUTE' if args.execute else '🔍 DRY RUN'}")
    print(f"  Period: last {args.days} days")
    print(f"  API split works on: {', '.join(sorted(SPLITTABLE_STATUSES))}")
    print("═" * 50)

    # Find packages
    packages = find_multi_qty_packages(days=args.days, order_number=args.order)

    splittable = [p for p in packages if p["splittable"]]
    not_splittable = [p for p in packages if not p["splittable"]]

    print(f"\n📊 Rezultate:")
    print(f"  Total multi-qty: {len(packages)}")
    print(f"  Splittable ({', '.join(SPLITTABLE_STATUSES)}): {len(splittable)}")
    print(f"  Not splittable via API: {len(not_splittable)}")

    if packages:
        print(f"\n{'─' * 80}")
        print(f"{'Comanda':<15} {'PkgID':<14} {'Qty':>4} {'Status':<12} {'Barcode':<18} {'Produs'}")
        print(f"{'─' * 80}")
        for p in packages[:30]:
            mark = "✅" if p["splittable"] else "⬜"
            print(f"  {mark} {p['orderNumber']:<13} {p['packageId']:<14} {p['quantity']:>3} {p['status']:<12} {p['barcode']:<18} {p['title'][:30]}")
        if len(packages) > 30:
            print(f"  ... și încă {len(packages) - 30}")

    # Split
    split_all_multi_qty(packages, execute=args.execute)

    # JSON output for frontend integration
    output = {
        "total": len(packages),
        "splittable": len(splittable),
        "packages": packages,
    }
    print(f"\n###SPLIT_RESULTS###{json.dumps(output, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
