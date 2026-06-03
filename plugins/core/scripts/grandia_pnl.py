#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "psycopg2-binary>=2.9",
#     "requests>=2.31",
#     "google-api-python-client>=2.0",
#     "google-auth>=2.0",
#     "google-auth-oauthlib>=1.0",
# ]
# ///
"""
Grandia monthly P&L (Shopify-sourced).

Sources
-------
  Orders + revenue + line items + refunds : Shopify Admin GraphQL (LIVE)
  COGS (per-SKU unit cost)                : AWBprint.sku_costs
  Transport actuals                       : AWBprint.order_awbs (joined by order_number)
  Ad spend                                : Meta, Google Ads, TikTok APIs (LIVE)
  FX (USD→RON daily)                      : AWBprint.exchange_rates
  Transport spot-check                    : DPD Romania API (N random AWBs)

See skills/grandia-pnl.md for the per-field formula / source mapping.

Usage
-----
  .venv/bin/python scripts/grandia_pnl.py --start 2026-04-01 --end 2026-04-30
  .venv/bin/python scripts/grandia_pnl.py --start 2026-04-01 --end 2026-04-30 \\
      --sheet-id 1n9Pl-yCaTse-acdvXtiLHtrSxq5mD2iD72y9Z3Am4mk
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests

# ──────────────────────────────────────────────────────────────────────────────
# Constants — keep in sync with skills/grandia-pnl.md
# ──────────────────────────────────────────────────────────────────────────────
GRANDIA_STORE_UID = "8a438d7e-fed5-4114-8ef9-61d9ceaed6a9-1765442303-RZF1BEIFMY"   # AWBprint.orders.store_uid
GRANDIA_BRAND_ID = "cmo5ulyl80003h1w2xlzfzhvh"                                     # metrics.brands.id
GRANDIA_SHOPIFY_DOMAIN = "n12w89-yy.myshopify.com"                                 # metrics.shopify_stores.shopifyDomain

VAT_RATE = 0.21
VAT_DIVISOR = 1.0 + VAT_RATE

# Shopify financial statuses that represent real revenue (money was collected
# at some point — even if later refunded). currentTotalPriceSet already nets
# the refunded amount out, so REFUNDED orders contribute ~0 to revenue while
# still letting us see the original SKU mix.
REVENUE_STATUSES = {"PAID", "PARTIALLY_REFUNDED", "REFUNDED"}

META_API_VERSION = "v23.0"
GOOGLE_ADS_API_VERSION = "v20"
TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

# ──────────────────────────────────────────────────────────────────────────────
# Secrets / DSN helpers
# ──────────────────────────────────────────────────────────────────────────────
# Secrets now live in the SharedClaude knowledge base, not in a .env file. The
# shim populates os.environ from $KB_DATABASE_URL (DSNs, ad-platform tokens,
# DPD creds, Google OAuth settings). It is a no-op if the KB is unreachable, so
# anything already exported in the environment still works.
import os

from kb_env import load_secrets_into_env

load_secrets_into_env()

def load_env() -> dict[str, str]:
    """Back-compat shim: secrets already live in os.environ (loaded above from
    the knowledge base). Diag scripts call this for its historical side effect."""
    load_secrets_into_env()
    return dict(os.environ)

ENV = os.environ

def _require(key: str) -> str:
    v = ENV.get(key)
    if not v:
        sys.exit(f"missing {key} in the knowledge base secret store (run: "
                 f"kb.py secret-list to see what's populated)")
    return v

_PG_OK_PARAMS = {
    "host", "hostaddr", "port", "dbname", "user", "password", "passfile",
    "channel_binding", "connect_timeout", "client_encoding", "options",
    "application_name", "fallback_application_name", "keepalives",
    "keepalives_idle", "keepalives_interval", "keepalives_count", "tty",
    "replication", "gssencmode", "sslmode", "requiressl", "sslcompression",
    "sslcert", "sslkey", "sslpassword", "sslrootcert", "sslcrl", "requirepeer",
    "krbsrvname", "gsslib", "service", "target_session_attrs",
}

def _clean_dsn(dsn: str) -> str:
    """Strip Prisma-only query params like ?schema=public that psycopg2 rejects."""
    p = urlsplit(dsn)
    if not p.query:
        return dsn
    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() in _PG_OK_PARAMS]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(kept), p.fragment))

def pg(dsn_key: str):
    conn = psycopg2.connect(_clean_dsn(_require(dsn_key)))
    conn.set_session(readonly=True)
    return conn

def fetchall(conn, sql: str, params: tuple | None = None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

# ──────────────────────────────────────────────────────────────────────────────
# FX (AWBprint.exchange_rates) — forward-filled across weekends
# ──────────────────────────────────────────────────────────────────────────────
def build_fx_index(awb_conn, currencies: list[str], start: date, end: date) -> dict[tuple[str, date], float]:
    if not currencies:
        return {}
    pad_start = start - timedelta(days=10)
    rows = fetchall(awb_conn,
        """SELECT currency, rate_date, rate, multiplier
             FROM exchange_rates
            WHERE currency = ANY(%s) AND rate_date BETWEEN %s AND %s
            ORDER BY currency, rate_date""",
        (currencies, pad_start, end))
    by_cur: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for r in rows:
        by_cur[r["currency"]].append((r["rate_date"], r["rate"] / r["multiplier"]))
    out: dict[tuple[str, date], float] = {}
    for cur, series in by_cur.items():
        last = None; i = 0; d = pad_start
        while d <= end:
            while i < len(series) and series[i][0] <= d:
                last = series[i][1]; i += 1
            if last is not None and start <= d <= end:
                out[(cur, d)] = last
            d += timedelta(days=1)
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Shopify — auth + order pull
# ──────────────────────────────────────────────────────────────────────────────
def shopify_credentials(metrics_conn, domain: str) -> dict:
    r = fetchall(metrics_conn,
        """SELECT "shopifyDomain","shopifyClientId","shopifyClientSecret","shopifyApiVersion"
             FROM shopify_stores WHERE "shopifyDomain" = %s""",
        (domain,))
    if not r:
        sys.exit(f"shopify_stores: no row for {domain}")
    return r[0]

def shopify_mint_token(creds: dict) -> str:
    resp = requests.post(
        f"https://{creds['shopifyDomain']}/admin/oauth/access_token",
        json={"client_id": creds["shopifyClientId"],
              "client_secret": creds["shopifyClientSecret"],
              "grant_type": "client_credentials"},
        timeout=20)
    resp.raise_for_status()
    return resp.json()["access_token"]

SHOPIFY_ORDERS_QUERY = """
query($cursor: String, $q: String!) {
  orders(first: 100, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id
      name
      createdAt
      cancelledAt
      displayFinancialStatus
      displayFulfillmentStatus
      currentTotalPriceSet      { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet   { shopMoney { amount } }
      currentTotalDiscountsSet  { shopMoney { amount } }
      currentTotalTaxSet        { shopMoney { amount } }
      totalShippingPriceSet     { shopMoney { amount } }
      totalRefundedSet          { shopMoney { amount } }
      lineItems(first: 100) {
        edges { node {
          sku
          variant { sku }
          quantity
          currentQuantity
        } }
      }
    } }
  }
}
"""

@dataclass
class ShopifyOrder:
    name: str
    created_at: datetime
    financial_status: str
    fulfillment_status: str | None
    cancelled: bool
    currency: str
    current_total: float
    current_subtotal: float
    current_discounts: float
    current_tax: float
    shipping_charged: float
    refunded: float
    line_items: list[dict]

STORE_TZ = ZoneInfo("Europe/Bucharest")

def fetch_shopify_orders(creds: dict, access_token: str, start: date, end: date) -> list[ShopifyOrder]:
    url = f"https://{creds['shopifyDomain']}/admin/api/{creds['shopifyApiVersion']}/graphql.json"
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
    # Use store TZ (Europe/Bucharest) boundaries so the API window matches the Shopify
    # admin dashboard's date filter. Bare 'YYYY-MM-DD' in Shopify search syntax is
    # interpreted loosely (often UTC-end-of-day), which over-counts by ~3h of next-day
    # orders. Explicit ISO+TZ is strict.
    start_iso = datetime.combine(start, datetime.min.time(), STORE_TZ).isoformat()
    end_iso   = datetime.combine(end + timedelta(days=1), datetime.min.time(), STORE_TZ).isoformat()
    q = f"created_at:>={start_iso} created_at:<{end_iso}"
    cursor = None
    out: list[ShopifyOrder] = []
    while True:
        resp = requests.post(url, headers=headers,
            json={"query": SHOPIFY_ORDERS_QUERY,
                  "variables": {"cursor": cursor, "q": q}},
            timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            raise RuntimeError(f"Shopify GraphQL errors: {payload['errors']}")
        data = payload["data"]["orders"]
        for edge in data["edges"]:
            n = edge["node"]
            items = []
            for le in n["lineItems"]["edges"]:
                ln = le["node"]
                sku = ln.get("sku") or (ln.get("variant") or {}).get("sku")
                items.append({"sku": sku, "qty": int(ln.get("currentQuantity") or 0)})
            out.append(ShopifyOrder(
                name=n["name"],
                created_at=datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")),
                financial_status=n["displayFinancialStatus"],
                fulfillment_status=n.get("displayFulfillmentStatus"),
                cancelled=bool(n.get("cancelledAt")),
                currency=n["currentTotalPriceSet"]["shopMoney"]["currencyCode"],
                current_total=float(n["currentTotalPriceSet"]["shopMoney"]["amount"]),
                current_subtotal=float(n["currentSubtotalPriceSet"]["shopMoney"]["amount"]),
                current_discounts=float(n["currentTotalDiscountsSet"]["shopMoney"]["amount"]),
                current_tax=float(n["currentTotalTaxSet"]["shopMoney"]["amount"]),
                shipping_charged=float(n["totalShippingPriceSet"]["shopMoney"]["amount"]),
                refunded=float(n["totalRefundedSet"]["shopMoney"]["amount"]),
                line_items=items,
            ))
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
        time.sleep(0.2)
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Revenue / COGS aggregation
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class RevenueResult:
    included_orders: list[ShopifyOrder]
    excluded: dict[str, dict]
    gross_revenue: float
    gross_subtotal: float
    gross_discounts: float
    shipping_charged: float
    refunded_total: float
    tax_total: float

def summarize_revenue(orders: list[ShopifyOrder]) -> RevenueResult:
    included: list[ShopifyOrder] = []
    excluded: dict[str, dict] = defaultdict(lambda: {"count": 0, "gross": 0.0})
    for o in orders:
        if o.financial_status in REVENUE_STATUSES:
            included.append(o)
        else:
            excluded[o.financial_status]["count"] += 1
            excluded[o.financial_status]["gross"] += o.current_total
    return RevenueResult(
        included_orders=included, excluded=dict(excluded),
        gross_revenue=sum(o.current_total for o in included),
        gross_subtotal=sum(o.current_subtotal for o in included),
        gross_discounts=sum(o.current_discounts for o in included),
        shipping_charged=sum(o.shipping_charged for o in included),
        refunded_total=sum(o.refunded for o in included),
        tax_total=sum(o.current_tax for o in included),
    )

@dataclass
class CogsResult:
    line_count: int
    units_sold: int
    gross_cogs: float
    lines_missing_cost: int
    units_missing_cost: int
    distinct_skus_missing_cost: list[str]

def compute_cogs(awb_conn, included_orders: list[ShopifyOrder]) -> CogsResult:
    skus_needed = sorted({li["sku"] for o in included_orders for li in o.line_items
                          if li["sku"] and li["qty"] > 0})
    cost_map: dict[str, tuple[float, str]] = {}
    if skus_needed:
        rows = fetchall(awb_conn,
            "SELECT sku, cost, currency FROM sku_costs WHERE sku = ANY(%s)",
            (skus_needed,))
        for r in rows:
            cost_map[r["sku"]] = (float(r["cost"]), r["currency"] or "RON")
    line_count = 0; units_sold = 0; gross_cogs = 0.0
    lines_missing = 0; units_missing = 0
    missing_skus: set[str] = set()
    for o in included_orders:
        for li in o.line_items:
            qty = li["qty"]
            if qty <= 0:
                continue
            line_count += 1
            units_sold += qty
            cm = cost_map.get(li["sku"])
            if cm is None:
                lines_missing += 1
                units_missing += qty
                if li["sku"]:
                    missing_skus.add(li["sku"])
                continue
            unit_cost, ccy = cm
            if ccy not in ("RON", "", None):
                print(f"WARN: SKU {li['sku']} unit cost in {ccy} — not converted", file=sys.stderr)
            gross_cogs += qty * unit_cost
    return CogsResult(line_count, units_sold, gross_cogs, lines_missing, units_missing, sorted(missing_skus))

# ──────────────────────────────────────────────────────────────────────────────
# Transport — AWBprint joined by order_number → Shopify order.name
# Backfill missing costs from Grandia.courier_shipments (original DPD response)
# Estimate the rest from same-courier mean cost per AWB.
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class AwbRow:
    courier: str
    tracking: str | None
    gross: float | None      # transport_cost (incl VAT)
    net: float | None        # transport_cost_fara_tva
    source: str              # 'awbprint' | 'grandia_shipments' | 'estimated'

@dataclass
class TransportResult:
    matched_order_count: int
    awb_count: int
    gross_total: float
    net_total: float
    by_courier: dict[str, dict[str, float]]
    tracking_for_spotcheck: list[str]
    unmatched_order_names: list[str]
    orders_missing_awb: list[str]
    # backfill / estimation transparency:
    awbs_measured: int
    awbs_backfilled: int
    awbs_estimated: int
    net_measured: float
    net_backfilled: float
    net_estimated: float

def _fetch_awb_rows(awb_conn, included_order_names: list[str]):
    """Return (matched_order_ids, matched_names_set, awb_rows_with_order_number, orders_missing_awb)."""
    if not included_order_names:
        return [], set(), [], []
    matched_orders = fetchall(awb_conn,
        """SELECT id, order_number FROM orders
            WHERE store_uid = %s AND order_number = ANY(%s)""",
        (GRANDIA_STORE_UID, included_order_names))
    id_to_name = {r["id"]: r["order_number"] for r in matched_orders}
    matched_names = set(id_to_name.values())
    if not matched_orders:
        return [], set(), [], list(included_order_names)
    awb_rows = fetchall(awb_conn,
        """SELECT order_id, courier_name, tracking_number, transport_cost, transport_cost_fara_tva
             FROM order_awbs WHERE order_id = ANY(%s)""",
        (list(id_to_name.keys()),))
    enriched = []
    orders_with_awb: set[int] = set()
    for r in awb_rows:
        orders_with_awb.add(r["order_id"])
        enriched.append({**r, "order_number": id_to_name.get(r["order_id"])})
    orders_missing_awb = sorted(id_to_name[oid] for oid in id_to_name if oid not in orders_with_awb)
    return list(id_to_name.keys()), matched_names, enriched, orders_missing_awb

def _backfill_from_grandia(grandia_conn, rows: list[AwbRow]) -> int:
    """Look up missing-cost rows by tracking in Grandia.courier_shipments → fill net+gross.
    Returns count of rows backfilled."""
    needs = [r for r in rows if r.tracking and (r.net is None or r.net == 0)]
    if not needs:
        return 0
    trks = [r.tracking for r in needs]
    found_rows = fetchall(grandia_conn,
        """SELECT "shipmentId",
                  ("dpdResponse"->'price'->>'total')::float AS gross,
                  ("dpdResponse"->'price'->>'vat')::float   AS vat
             FROM courier_shipments
            WHERE "shipmentId" = ANY(%s) AND "dpdResponse" IS NOT NULL""",
        (trks,))
    lookup = {f["shipmentId"]: (f["gross"], f["vat"]) for f in found_rows}
    n = 0
    for r in needs:
        hit = lookup.get(r.tracking)
        if hit is None:
            continue
        gross, vat = hit
        if gross is None:
            continue
        r.gross = float(gross)
        r.net = float(gross) - float(vat or 0.0)
        r.source = "grandia_shipments"
        n += 1
    return n

def _estimate_missing(rows: list[AwbRow]) -> int:
    """For rows still missing cost, fill from same-courier mean (gross+net).
    Returns count of rows estimated."""
    by_cur: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        if r.net is not None and r.net > 0:
            by_cur[r.courier].append((r.gross or (r.net * VAT_DIVISOR), r.net))
    # Global fallback for couriers with no measurements
    all_gross = [g for series in by_cur.values() for g, _ in series]
    all_net   = [n for series in by_cur.values() for _, n in series]
    global_gross = sum(all_gross) / len(all_gross) if all_gross else 0.0
    global_net   = sum(all_net)   / len(all_net)   if all_net   else 0.0
    n = 0
    for r in rows:
        if r.net is not None and r.net > 0:
            continue
        series = by_cur.get(r.courier)
        if series:
            r.gross = sum(g for g, _ in series) / len(series)
            r.net   = sum(s for _, s in series) / len(series)
        else:
            r.gross = global_gross
            r.net   = global_net
        r.source = "estimated"
        n += 1
    return n

def fetch_transport(awb_conn, grandia_conn, included_order_names: list[str]) -> TransportResult:
    if not included_order_names:
        return TransportResult(0, 0, 0.0, 0.0, {}, [], [], [], 0, 0, 0, 0.0, 0.0, 0.0)
    matched_ids, matched_names, awb_db_rows, orders_missing_awb = _fetch_awb_rows(awb_conn, included_order_names)
    unmatched = [n for n in included_order_names if n not in matched_names]

    rows: list[AwbRow] = []
    for r in awb_db_rows:
        courier = (r["courier_name"] or "Unknown").strip() or "Unknown"
        net = float(r["transport_cost_fara_tva"]) if r["transport_cost_fara_tva"] is not None else None
        gross = float(r["transport_cost"]) if r["transport_cost"] is not None else None
        source = "awbprint" if (net is not None and net > 0) else "missing"
        rows.append(AwbRow(courier=courier, tracking=r["tracking_number"],
                           gross=gross if (gross and gross > 0) else None,
                           net=net if (net and net > 0) else None,
                           source=source))

    backfilled = _backfill_from_grandia(grandia_conn, rows)
    estimated  = _estimate_missing(rows)

    by_courier: dict[str, dict[str, float]] = defaultdict(lambda: {"awbs": 0.0, "gross": 0.0, "net": 0.0})
    dpd_trk: list[str] = []
    measured_n = backfilled_n = estimated_n = 0
    net_measured = net_backfilled = net_estimated = 0.0
    for r in rows:
        by_courier[r.courier]["awbs"]  += 1
        by_courier[r.courier]["gross"] += float(r.gross or 0.0)
        by_courier[r.courier]["net"]   += float(r.net or 0.0)
        if r.courier.upper() == "DPD" and r.tracking:
            dpd_trk.append(r.tracking)
        if r.source == "awbprint":
            measured_n += 1;   net_measured   += r.net or 0.0
        elif r.source == "grandia_shipments":
            backfilled_n += 1; net_backfilled += r.net or 0.0
        elif r.source == "estimated":
            estimated_n += 1;  net_estimated  += r.net or 0.0

    return TransportResult(
        matched_order_count=len(matched_ids), awb_count=len(rows),
        gross_total=sum(v["gross"] for v in by_courier.values()),
        net_total=sum(v["net"]   for v in by_courier.values()),
        by_courier=dict(by_courier), tracking_for_spotcheck=dpd_trk,
        unmatched_order_names=unmatched,
        orders_missing_awb=orders_missing_awb,
        awbs_measured=measured_n, awbs_backfilled=backfilled_n, awbs_estimated=estimated_n,
        net_measured=net_measured, net_backfilled=net_backfilled, net_estimated=net_estimated,
    )

# ──────────────────────────────────────────────────────────────────────────────
# DPD spot-check
# ──────────────────────────────────────────────────────────────────────────────
def dpd_spotcheck(trackings: list[str], n: int, db_lookup: dict[str, float]) -> dict:
    if not trackings or n <= 0:
        return {"checked": 0, "agree": 0, "disagree": 0, "details": [], "skipped": True}
    user = _require("DPD_RO_USERNAME"); pwd = _require("DPD_RO_PASSWORD")
    base = ENV.get("DPD_API_BASE", "https://api.dpd.ro/v1").rstrip("/")
    sample = random.sample(trackings, min(n, len(trackings)))
    url = f"{base}/track"
    agree = 0; disagree = 0; details = []
    for trk in sample:
        body = {"userName": user, "password": pwd, "language": "EN",
                "parcels": [{"id": trk}], "lastOperationOnly": True}
        try:
            r = requests.post(url, json=body, timeout=15); r.raise_for_status()
            data = r.json()
        except Exception as e:
            details.append({"tracking": trk, "error": str(e)[:200]}); continue
        api_price = None
        try:
            par = (data.get("parcels") or [{}])[0]
            for op in par.get("operations") or []:
                for k in ("priceWithVat", "price", "totalPrice", "deliveryPrice"):
                    if op.get(k) is not None:
                        api_price = float(op[k]); break
                if api_price is not None: break
        except Exception:
            api_price = None
        db_price = db_lookup.get(trk)
        if api_price is None:
            details.append({"tracking": trk, "db_gross": db_price, "api_gross": None, "note": "no price"})
            continue
        if db_price is not None and abs(db_price - api_price) < 0.05:
            agree += 1
        else:
            disagree += 1
        details.append({"tracking": trk, "db_gross": db_price, "api_gross": api_price})
    return {"checked": len(sample), "agree": agree, "disagree": disagree, "details": details}

# ──────────────────────────────────────────────────────────────────────────────
# Ad spend — Meta
# ──────────────────────────────────────────────────────────────────────────────
def meta_spend(metrics_conn, start: date, end: date) -> dict:
    rows = fetchall(metrics_conn,
        """SELECT a."metaAccountId" AS account_id, a.currency AS account_currency,
                  t."accessToken" AS access_token
             FROM meta_ad_accounts a
             JOIN meta_access_tokens t ON t.id = a."tokenId"
             JOIN brand_meta_ad_accounts ba ON ba."adAccountId" = a.id
            WHERE ba."brandId" = %s AND ba."isActive" = true
              AND a."isActive" = true AND t."isActive" = true""",
        (GRANDIA_BRAND_ID,))
    by_account = []; total = 0.0
    for r in rows:
        url = f"https://graph.facebook.com/{META_API_VERSION}/{r['account_id']}/insights"
        params = {"fields": "spend",
                  "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
                  "level": "account", "access_token": r["access_token"]}
        resp = requests.get(url, params=params, timeout=30); resp.raise_for_status()
        spend = sum(float(d.get("spend", 0)) for d in resp.json().get("data", []))
        by_account.append({"account_id": r["account_id"], "currency": r["account_currency"],
                           "spend_native": spend, "spend_ron": spend})
        total += spend
    return {"total_ron": total, "accounts": by_account}

# ──────────────────────────────────────────────────────────────────────────────
# Ad spend — Google Ads
# ──────────────────────────────────────────────────────────────────────────────
def _google_ads_refresh(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = requests.post("https://oauth2.googleapis.com/token",
        data={"grant_type": "refresh_token", "client_id": client_id,
              "client_secret": client_secret, "refresh_token": refresh_token},
        timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def google_ads_spend(metrics_conn, start: date, end: date) -> dict:
    rows = fetchall(metrics_conn,
        """SELECT c."customerId" AS customer_id, c."currencyCode" AS currency,
                  conn."developerToken" AS dev_token, conn."loginCustomerId" AS login_cid,
                  conn."oauthClientId" AS client_id, conn."oauthClientSecret" AS client_secret,
                  conn."refreshToken" AS refresh_token
             FROM google_ads_customer_accounts c
             JOIN google_ads_connections conn ON conn.id = c."connectionId"
             JOIN brand_google_ads_accounts ba ON ba."customerAccountId" = c.id
            WHERE ba."brandId" = %s AND ba."isActive" = true
              AND c."isActive" = true AND conn."isActive" = true""",
        (GRANDIA_BRAND_ID,))
    by_account = []; total = 0.0
    for r in rows:
        access = _google_ads_refresh(r["client_id"], r["client_secret"], r["refresh_token"])
        url = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}/customers/{r['customer_id']}/googleAds:search"
        headers = {"Authorization": f"Bearer {access}", "developer-token": r["dev_token"],
                   "login-customer-id": r["login_cid"], "Content-Type": "application/json"}
        body = {"query": ("SELECT segments.date, metrics.cost_micros FROM customer "
                          f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")}
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Google Ads API {resp.status_code}: {resp.text[:500]}")
        cost = 0.0
        for row in resp.json().get("results", []) or []:
            cost += float(row.get("metrics", {}).get("costMicros", 0)) / 1_000_000.0
        by_account.append({"customer_id": r["customer_id"], "currency": r["currency"],
                           "cost_native": cost, "cost_ron": cost})
        total += cost
    return {"total_ron": total, "accounts": by_account}

# ──────────────────────────────────────────────────────────────────────────────
# Ad spend — TikTok
# ──────────────────────────────────────────────────────────────────────────────
def tiktok_spend(metrics_conn, fx: dict[tuple[str, date], float], start: date, end: date) -> dict:
    rows = fetchall(metrics_conn,
        """SELECT a."tikTokAccountId" AS advertiser_id, a.currency AS account_currency,
                  t."accessToken" AS access_token
             FROM tiktok_ad_accounts a
             JOIN tiktok_access_tokens t ON t.id = a."tokenId"
             JOIN brand_tiktok_ad_accounts ba ON ba."adAccountId" = a.id
            WHERE ba."brandId" = %s AND ba."isActive" = true
              AND a."isActive" = true AND t."isActive" = true""",
        (GRANDIA_BRAND_ID,))
    by_account = []; total = 0.0
    for r in rows:
        url = f"{TIKTOK_API_BASE}/report/integrated/get/"
        params = {"advertiser_id": r["advertiser_id"], "report_type": "BASIC",
                  "data_level": "AUCTION_ADVERTISER",
                  "dimensions": json.dumps(["stat_time_day"]),
                  "metrics": json.dumps(["spend"]),
                  "start_date": start.isoformat(), "end_date": end.isoformat(),
                  "page_size": 1000}
        headers = {"Access-Token": r["access_token"]}
        resp = requests.get(url, headers=headers, params=params, timeout=60); resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"TikTok API error: {payload}")
        native = 0.0; ron = 0.0
        cur = r["account_currency"] or "USD"
        for d in payload.get("data", {}).get("list", []) or []:
            day_str = d.get("dimensions", {}).get("stat_time_day")
            spend_d = float(d.get("metrics", {}).get("spend", 0))
            native += spend_d
            day = datetime.fromisoformat(day_str.replace("Z", "").replace(" ", "T")[:10]).date()
            if cur == "RON":
                ron += spend_d
            else:
                rate = fx.get((cur, day))
                if rate is None:
                    raise RuntimeError(f"missing FX rate for {cur} on {day}")
                ron += spend_d * rate
        by_account.append({"advertiser_id": r["advertiser_id"], "currency": cur,
                           "spend_native": native, "spend_ron": ron})
        total += ron
    return {"total_ron": total, "accounts": by_account}

# ──────────────────────────────────────────────────────────────────────────────
# P&L
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PnL:
    period_start: date
    period_end: date
    rev: RevenueResult
    cogs: CogsResult
    transport: TransportResult
    meta: dict
    google_ads: dict
    tiktok: dict
    dpd_check: dict
    raw_order_count: int

    @property
    def gross_revenue(self): return self.rev.gross_revenue
    @property
    def net_revenue(self):   return self.gross_revenue / VAT_DIVISOR
    @property
    def vat_collected(self): return self.gross_revenue - self.net_revenue
    @property
    def net_cogs(self):      return self.cogs.gross_cogs / VAT_DIVISOR
    @property
    def gross_margin(self):  return self.net_revenue - self.net_cogs
    @property
    def net_transport(self): return self.transport.net_total
    @property
    def total_ad_spend(self):return self.meta["total_ron"] + self.google_ads["total_ron"] + self.tiktok["total_ron"]
    @property
    def contribution_margin(self): return self.gross_margin - self.net_transport - self.total_ad_spend
    @property
    def mer(self): return (self.net_revenue / self.total_ad_spend) if self.total_ad_spend else None

# ---------------------------------------------------------------------------
# Visual style (matches reference sheet 1Tnb1XZX…)
#   Font:  Verdana 11 throughout
#   Layout: 9 columns, with narrow spacers in A/B/D/F
#     A(0)=14, B(1)=30, C(2)=236, D(3)=19, E(4)=310, F(5)=14,
#     G(6)=160 value, H(7)=100 %, I(8)=420 notes
#   Palette (RGB 0–1 float):
#     darkblue1  = (0.071, 0.212, 0.659)  # brand header band
#     darkblue2  = (0.020, 0.063, 0.459)  # totals band (dark)
#     midblue    = (0.110, 0.373, 0.859)  # "Consolidated/Subsidiary" tag
#     gray_bar   = (0.750, 0.750, 0.750)  # "Continuing operations" gray bar
#     text_gray  = (0.471, 0.471, 0.471)  # body text
#     white      = (1, 1, 1)
#   Number formats: money = '#,##0;(#,##0)'  pct = '0%;(0%)'
# Each rendered row is a (tag, payload-dict) tuple. push_sheet then maps tags
# to the appropriate Sheets formatting requests.
# ---------------------------------------------------------------------------

STYLE_W = 9
STYLE_COL_LABEL       = 1    # "Subsidiary" / "Consolidated" / "Methodology" tag
STYLE_COL_LINENAME    = 3    # line item label
STYLE_COL_VALUE       = 5    # value ex. VAT (net)
STYLE_COL_VALUE_GROSS = 6    # value incl. VAT (gross) — only where applicable
STYLE_COL_PCT         = 7    # % of net revenue
STYLE_COL_NOTE        = 8    # notes / breakdown

MONTH_NAMES = ["","January","February","March","April","May","June",
               "July","August","September","October","November","December"]

def _row(tag: str, **kw) -> tuple[str, dict]:
    cells = [""] * STYLE_W
    for k, idx in (("label", STYLE_COL_LABEL), ("line", STYLE_COL_LINENAME),
                   ("value", STYLE_COL_VALUE), ("value_gross", STYLE_COL_VALUE_GROSS),
                   ("pct", STYLE_COL_PCT), ("note", STYLE_COL_NOTE)):
        if kw.get(k) is not None:
            cells[idx] = kw[k]
    return (tag, {"cells": cells, **kw})

def render_rows(p: PnL) -> list[tuple[str, dict]]:
    period_label = f"{MONTH_NAMES[p.period_start.month]} {p.period_start.year}"
    R: list[tuple[str, dict]] = []

    R.append(("spacer", {"cells": [""] * STYLE_W}))                # row 1 — thin top margin
    R.append(_row("title_bar", label="Grandia", line="", value=period_label, note=""))
    R.append(_row("subheader", line="Lei / RON",
                  value="ex. VAT", value_gross="incl. VAT",
                  pct="% of net revenue", note="Notes"))
    R.append(_row("gray_section", line="Continuing operations"))
    R.append(_row("brand_header", label="Consolidated", line="Grandia DTC"))

    inc = len(p.rev.included_orders)
    def pct(n, d): return (n / d) if d else 0.0
    V = VAT_DIVISOR  # 1.21

    # KPI: paid orders
    R.append(_row("kpi_bold", line="Paid Orders",
                  value=inc, note=f"raw {p.raw_order_count}; PAID+PART./REFUNDED"))
    R.append(_row("line", line="Excluded — PENDING (COD not yet collected)",
                  value=p.rev.excluded.get("PENDING", {}).get("count", 0),
                  note=f"gross excluded: {p.rev.excluded.get('PENDING', {}).get('gross', 0):,.0f} RON"))
    R.append(_row("line", line="Excluded — VOIDED (cancelled)",
                  value=p.rev.excluded.get("VOIDED", {}).get("count", 0),
                  note="zero revenue impact"))

    # Revenue
    R.append(_row("kpi_bold", line="Revenue (post-refund)",
                  value=p.net_revenue, value_gross=p.gross_revenue, pct=1.0,
                  note="Shopify Admin GraphQL · currentTotalPriceSet"))
    R.append(_row("line", line="  Subtotal (post-refund)",
                  value=p.rev.gross_subtotal / V, value_gross=p.rev.gross_subtotal))
    R.append(_row("line", line="  Discounts",
                  value=p.rev.gross_discounts / V, value_gross=p.rev.gross_discounts))
    R.append(_row("line", line="  Shipping charged to customer",
                  value=p.rev.shipping_charged / V, value_gross=p.rev.shipping_charged))
    R.append(_row("line", line="  Refunds issued (already deducted)",
                  value=p.rev.refunded_total / V, value_gross=p.rev.refunded_total))
    R.append(_row("line", line="  VAT collected on revenue",
                  value=p.vat_collected, pct=pct(p.vat_collected, p.net_revenue),
                  note="VAT divisor 1.21"))

    # COGS
    R.append(_row("line", line="COGS", value=p.net_cogs, value_gross=p.net_cogs * V,
                  pct=pct(p.net_cogs, p.net_revenue),
                  note=f"AWBprint.sku_costs × lineItems.currentQuantity · {p.cogs.units_sold} units"))
    if p.cogs.distinct_skus_missing_cost:
        R.append(_row("line", line="  missing cost record",
                      value=p.cogs.lines_missing_cost,
                      note=f"{len(p.cogs.distinct_skus_missing_cost)} SKU(s) absent from sku_costs"))

    # Last-mile / transport
    t = p.transport
    R.append(_row("line", line="Last Mile Delivery",
                  value=t.net_total, value_gross=t.net_total * V,
                  pct=pct(t.net_total, p.net_revenue),
                  note=f"AWBprint.order_awbs · {t.awb_count} AWBs across {len(t.by_courier)} courier(s)"))
    R.append(_row("line", line="  measured (AWBprint cost populated)",
                  value=t.net_measured, value_gross=t.net_measured * V,
                  note=f"{t.awbs_measured} AWBs"))
    R.append(_row("line", line="  backfilled (Grandia.courier_shipments)",
                  value=t.net_backfilled, value_gross=t.net_backfilled * V,
                  note=f"{t.awbs_backfilled} AWBs · original DPD create response"))
    R.append(_row("line", line="  estimated (same-courier mean / AWB)",
                  value=t.net_estimated, value_gross=t.net_estimated * V,
                  note=f"{t.awbs_estimated} AWBs · DPD API does not expose price for foreign-created parcels"))
    for c, v in sorted(t.by_courier.items(), key=lambda kv: -kv[1]["awbs"]):
        R.append(_row("line", line=f"    • {c}",
                      value=v["net"], value_gross=v["net"] * V,
                      note=f"{int(v['awbs'])} AWBs"))

    # Marketing — reverse-charge VAT in RO, so no incl. VAT figure on the invoice
    R.append(_row("line", line="Marketing (ad spend total)",
                  value=p.total_ad_spend, pct=pct(p.total_ad_spend, p.net_revenue),
                  note="live Meta + Google Ads + TikTok APIs · reverse-charge VAT (no incl. figure)"))
    for a in p.meta["accounts"]:
        R.append(_row("line", line=f"  • Meta {a['account_id']}",
                      value=a["spend_ron"], note="Meta Marketing API v23.0"))
    for a in p.google_ads["accounts"]:
        R.append(_row("line", line=f"  • Google Ads {a['customer_id']}",
                      value=a["cost_ron"], note=f"Google Ads REST {GOOGLE_ADS_API_VERSION}"))
    for a in p.tiktok["accounts"]:
        R.append(_row("line", line=f"  • TikTok {a['advertiser_id']}",
                      value=a["spend_ron"],
                      note=f"native {a['currency']} {a['spend_native']:,.2f} · USD→RON per-day BNR"))

    # Gross / Contribution margin
    R.append(_row("total_bold", line="Gross Margin",
                  value=p.gross_margin, pct=pct(p.gross_margin, p.net_revenue),
                  note="net revenue − net COGS"))
    R.append(_row("percent", line="% gross margin", pct=pct(p.gross_margin, p.net_revenue)))
    R.append(_row("total_bold", line="Contribution Margin",
                  value=p.contribution_margin, pct=pct(p.contribution_margin, p.net_revenue),
                  note="gross margin − last-mile − marketing"))
    R.append(_row("percent", line="% contribution margin", pct=pct(p.contribution_margin, p.net_revenue)))
    R.append(_row("kpi_bold", line="MER (net revenue / ad spend)",
                  value=round(p.mer, 3) if p.mer else 0, note="marketing efficiency ratio"))

    # Methodology + caveats footer (kept in the same visual language)
    R.append(("spacer", {"cells": [""] * STYLE_W}))
    R.append(_row("gray_section", line="Methodology"))
    method = [
        "Revenue — Shopify Admin GraphQL. Orders where financial_status ∈ {PAID, PARTIALLY_REFUNDED, REFUNDED}. "
            "Amounts use currentTotalPriceSet (= original total minus refunds), so revenue is already net of refunds.",
        "Excluded — PENDING (COD not yet collected) and VOIDED (cancelled) are listed for transparency "
            "but excluded from revenue, COGS and last-mile. AWBprint financial_status is not used (Frisbo doesn't sync 'paid' back).",
        "COGS — AWBprint.sku_costs.cost (RON, gross) × Shopify lineItems.currentQuantity (post-refund units). "
            "Net = gross / 1.21. Lines whose SKU has no cost record are flagged.",
        "Last Mile Delivery — AWBprint.order_awbs.transport_cost_fara_tva (net), joined by order_number = Shopify order name. "
            "Missing-cost AWBs are first backfilled from Grandia.courier_shipments.dpdResponse, then estimated as the same-courier mean cost per AWB for the period.",
        "Marketing — live Meta / Google Ads / TikTok APIs. No cached metrics tables. TikTok USD→RON converted per-day from AWBprint.exchange_rates.",
        "Gross Margin = Net revenue − Net COGS. Contribution Margin = Gross margin − Last-mile − Marketing. MER = Net revenue / Ad spend total.",
    ]
    for line in method:
        R.append(_row("methodology", note=line))

    caveats: list[str] = []
    if p.cogs.distinct_skus_missing_cost:
        caveats.append(
            f"{p.cogs.lines_missing_cost} line(s) across {len(p.cogs.distinct_skus_missing_cost)} "
            f"SKU(s) have no cost record in sku_costs — COGS slightly under-reported.")
    if t.orders_missing_awb:
        caveats.append(
            f"{len(t.orders_missing_awb)} included order(s) have no AWB row at all — no last-mile cost counted for them.")
    if t.awbs_estimated:
        pct_est = (t.net_estimated / t.net_total * 100.0) if t.net_total else 0.0
        caveats.append(
            f"{t.awbs_estimated} of {t.awb_count} AWBs ({pct_est:.1f}% of last-mile €) had their cost estimated, not measured. "
            "The DPD API does not expose historical price for parcels we did not create (e.g. Frisbo-generated AWBs).")
    if t.unmatched_order_names:
        caveats.append(
            f"{len(t.unmatched_order_names)} included Shopify orders did not match any AWBprint.orders row (sync lag).")
    if caveats:
        R.append(("spacer", {"cells": [""] * STYLE_W}))
        R.append(_row("gray_section", line="Caveats"))
        for c in caveats:
            R.append(_row("caveat", note=c))

    R.append(("spacer", {"cells": [""] * STYLE_W}))
    R.append(_row("footer", note=f"Generated {datetime.now().isoformat(timespec='seconds')} · "
                                  f"VAT divisor {VAT_DIVISOR} · source: Shopify + AWBprint + live ad APIs"))
    return R

# ---------------------------------------------------------------------------
# Sheet writer — styled to match reference sheet (Verdana 11, hidden gridlines,
# dark-blue header bands, gray body text, narrow spacer columns)
# ---------------------------------------------------------------------------
DARKBLUE1 = {"red": 0.071, "green": 0.212, "blue": 0.659}
DARKBLUE2 = {"red": 0.020, "green": 0.063, "blue": 0.459}
MIDBLUE   = {"red": 0.110, "green": 0.373, "blue": 0.859}
GRAY_BAR  = {"red": 0.750, "green": 0.750, "blue": 0.750}
TEXT_GRAY = {"red": 0.471, "green": 0.471, "blue": 0.471}
WHITE     = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
CAVEAT_BG = {"red": 1.0,   "green": 0.95,  "blue": 0.88}

FMT_MONEY = "#,##0;(#,##0)"
FMT_PCT   = "0%;(0%)"
FMT_RATIO = "0.00"

def push_sheet(sheet_id: str, tab: str, tagged_rows: list[tuple[str, dict]]) -> None:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_file = _require("GOOGLE_OAUTH_TOKEN_FILE")
    scopes = _require("GOOGLE_OAUTH_SCOPES").split()
    creds = Credentials.from_authorized_user_file(token_file, scopes)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            sys.exit(f"OAuth token at {token_file} is invalid; re-auth via scripts/write_databases_to_sheet.py first")
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sid = None
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab:
            sid = s["properties"]["sheetId"]; break
    if sid is None:
        resp = svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
        sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Hide gridlines for the destination sheet + unmerge any prior merges
    svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
        {"updateSheetProperties": {
            "properties": {"sheetId": sid,
                           "gridProperties": {"hideGridlines": True, "frozenRowCount": 3}},
            "fields": "gridProperties.hideGridlines,gridProperties.frozenRowCount"}},
        {"unmergeCells": {"range": {"sheetId": sid}}},
    ]}).execute()

    # --- write values ------------------------------------------------------
    svc.spreadsheets().values().clear(spreadsheetId=sheet_id, range=f"'{tab}'").execute()
    values = [r[1]["cells"] for r in tagged_rows]
    # USER_ENTERED so percent strings (if any) auto-convert, but we mostly use floats
    svc.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"'{tab}'!A1",
        valueInputOption="USER_ENTERED", body={"values": values}).execute()

    # --- collect formatting requests --------------------------------------
    requests: list[dict] = []
    def rng(r0, r1, c0=0, c1=STYLE_W):
        return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1}
    def cell_fmt(**fmt):
        return {"userEnteredFormat": fmt}
    def base_text(**over):
        tf = {"fontFamily": "Verdana", "fontSize": 11}
        tf.update(over); return tf

    # Column widths (9 cols: label-B, name-D, ex.VAT-G, incl.VAT-H, pct-I, note-J)
    widths = [30, 236, 19, 310, 14, 160, 160, 100, 420]
    for col, px in enumerate(widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})

    # Row 1 thin spacer
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 10}, "fields": "pixelSize"}})

    # Default font everywhere
    requests.append({"repeatCell": {"range": rng(0, len(values)),
        "cell": cell_fmt(textFormat=base_text(foregroundColor=TEXT_GRAY),
                          backgroundColor=WHITE,
                          horizontalAlignment="LEFT",
                          verticalAlignment="MIDDLE"),
        "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,verticalAlignment)"}})

    # Per-row formatting
    for i, (tag, payload) in enumerate(tagged_rows):

        if tag == "spacer":
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": 10}, "fields": "pixelSize"}})
            continue

        if tag == "title_bar":
            # Merge B..E left band (label) and F..H right band (period)
            requests.append({"mergeCells": {"range": rng(i, i + 1, 1, 5), "mergeType": "MERGE_ALL"}})
            requests.append({"mergeCells": {"range": rng(i, i + 1, 5, 9), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 1, 9),
                "cell": cell_fmt(backgroundColor=DARKBLUE1,
                                  textFormat=base_text(bold=True, foregroundColor=WHITE),
                                  verticalAlignment="MIDDLE"),
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 1, 5),
                "cell": cell_fmt(horizontalAlignment="LEFT", padding={"left": 8}),
                "fields": "userEnteredFormat(horizontalAlignment,padding)"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 5, 9),
                "cell": cell_fmt(horizontalAlignment="RIGHT", padding={"right": 8}),
                "fields": "userEnteredFormat(horizontalAlignment,padding)"}})
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": 28}, "fields": "pixelSize"}})
            continue

        if tag == "subheader":
            requests.append({"repeatCell": {"range": rng(i, i + 1),
                "cell": cell_fmt(textFormat=base_text(bold=True, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat.textFormat"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 5, 8),
                "cell": cell_fmt(horizontalAlignment="RIGHT"),
                "fields": "userEnteredFormat.horizontalAlignment"}})
            continue

        if tag == "gray_section":
            requests.append({"mergeCells": {"range": rng(i, i + 1, 1, 9), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 1, 9),
                "cell": cell_fmt(backgroundColor=GRAY_BAR,
                                  textFormat=base_text(bold=True, foregroundColor=WHITE),
                                  horizontalAlignment="LEFT", padding={"left": 8}),
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,padding)"}})
            continue

        if tag == "brand_header":
            # Label col B — mid-blue italic centered
            requests.append({"repeatCell": {"range": rng(i, i + 1, 1, 2),
                "cell": cell_fmt(backgroundColor=MIDBLUE,
                                  textFormat=base_text(italic=True, foregroundColor=WHITE),
                                  horizontalAlignment="CENTER"),
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}})
            # D..H dark-blue band with brand name
            requests.append({"mergeCells": {"range": rng(i, i + 1, 3, 5), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 9),
                "cell": cell_fmt(backgroundColor=DARKBLUE1,
                                  textFormat=base_text(bold=True, foregroundColor=WHITE),
                                  horizontalAlignment="LEFT", padding={"left": 8}),
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,padding)"}})
            continue

        # ---- data rows ----------------------------------------------------
        requests.append({"repeatCell": {"range": rng(i, i + 1, 5, 7),
            "cell": cell_fmt(numberFormat={"type": "NUMBER", "pattern": FMT_MONEY},
                              horizontalAlignment="RIGHT"),
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"}})
        requests.append({"repeatCell": {"range": rng(i, i + 1, 7, 8),
            "cell": cell_fmt(numberFormat={"type": "NUMBER", "pattern": FMT_PCT},
                              horizontalAlignment="RIGHT"),
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)"}})
        requests.append({"repeatCell": {"range": rng(i, i + 1, 8, 9),
            "cell": cell_fmt(wrapStrategy="WRAP",
                              textFormat=base_text(foregroundColor=TEXT_GRAY, fontSize=10)),
            "fields": "userEnteredFormat(wrapStrategy,textFormat)"}})

        if tag == "kpi_bold":
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 8),
                "cell": cell_fmt(textFormat=base_text(bold=True, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat.textFormat"}})
        elif tag == "line":
            pass
        elif tag == "percent":
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 4),
                "cell": cell_fmt(textFormat=base_text(italic=True, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat.textFormat"}})
        elif tag == "total_bold":
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 8),
                "cell": cell_fmt(textFormat=base_text(bold=True, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat.textFormat"}})
            requests.append({"updateBorders": {
                "range": rng(i, i + 1, 3, 8),
                "top": {"style": "SOLID", "color": TEXT_GRAY}}})
        elif tag == "methodology":
            requests.append({"mergeCells": {"range": rng(i, i + 1, 3, 9), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 9),
                "cell": cell_fmt(wrapStrategy="WRAP", verticalAlignment="TOP",
                                  textFormat=base_text(fontSize=10, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,textFormat)"}})
        elif tag == "caveat":
            requests.append({"mergeCells": {"range": rng(i, i + 1, 3, 9), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 3, 9),
                "cell": cell_fmt(wrapStrategy="WRAP", verticalAlignment="TOP",
                                  backgroundColor=CAVEAT_BG,
                                  textFormat=base_text(fontSize=10, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,backgroundColor,textFormat)"}})
        elif tag == "footer":
            requests.append({"mergeCells": {"range": rng(i, i + 1, 1, 9), "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": rng(i, i + 1, 1, 9),
                "cell": cell_fmt(horizontalAlignment="LEFT",
                                  textFormat=base_text(italic=True, fontSize=9, foregroundColor=TEXT_GRAY)),
                "fields": "userEnteredFormat(horizontalAlignment,textFormat)"}})

    # Send in chunks
    CHUNK = 100
    for k in range(0, len(requests), CHUNK):
        svc.spreadsheets().batchUpdate(spreadsheetId=sheet_id,
            body={"requests": requests[k:k + CHUNK]}).execute()
# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end",   required=True)
    p.add_argument("--sheet-id", default=None)
    p.add_argument("--tab", default=None)
    p.add_argument("--spot-check", type=int, default=50)
    p.add_argument("--transport-flat-per-order", type=float, default=None,
        help="Override transport: assume this many RON (net) per included order. "
             "Useful for partial months where AWBs aren't invoiced yet.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    start = date.fromisoformat(args.start); end = date.fromisoformat(args.end)

    print(f"== Grandia P&L {start} → {end} (Shopify-sourced) ==", file=sys.stderr)
    awb = pg("DATABASE_URL_AWBPRINT")
    met = pg("DATABASE_URL_METRICS")

    print("[1/7] Shopify mint token + pull orders…", file=sys.stderr)
    creds = shopify_credentials(met, GRANDIA_SHOPIFY_DOMAIN)
    access = shopify_mint_token(creds)
    orders = fetch_shopify_orders(creds, access, start, end)
    rev = summarize_revenue(orders)
    by_status = Counter(o.financial_status for o in orders)
    print(f"  raw orders: {len(orders)}  by status: {dict(by_status)}", file=sys.stderr)
    print(f"  INCLUDED: {len(rev.included_orders)} orders / gross {rev.gross_revenue:,.2f} RON", file=sys.stderr)

    print("[2/7] COGS…", file=sys.stderr)
    cogs = compute_cogs(awb, rev.included_orders)
    print(f"  units: {cogs.units_sold}  gross COGS: {cogs.gross_cogs:,.2f} RON  missing: {cogs.lines_missing_cost} lines / {len(cogs.distinct_skus_missing_cost)} SKUs", file=sys.stderr)

    print("[3/7] transport (with backfill + estimation)…", file=sys.stderr)
    names = [o.name for o in rev.included_orders]
    grn = pg("DATABASE_URL_GRANDIA")
    transport = fetch_transport(awb, grn, names)
    if args.transport_flat_per_order is not None:
        flat = float(args.transport_flat_per_order)
        flat_net_total = flat * len(names)
        print(f"  OVERRIDE: flat transport = {flat:.2f} RON/order × {len(names)} = {flat_net_total:,.2f} RON", file=sys.stderr)
        transport = TransportResult(
            matched_order_count=transport.matched_order_count,
            awb_count=transport.awb_count,
            gross_total=flat_net_total * VAT_DIVISOR,
            net_total=flat_net_total,
            by_courier={"FLAT (override)": {"awbs": float(len(names)), "gross": flat_net_total * VAT_DIVISOR, "net": flat_net_total}},
            tracking_for_spotcheck=[],
            unmatched_order_names=transport.unmatched_order_names,
            orders_missing_awb=transport.orders_missing_awb,
            awbs_measured=0, awbs_backfilled=0, awbs_estimated=len(names),
            net_measured=0.0, net_backfilled=0.0, net_estimated=flat_net_total,
        )
    print(f"  matched orders: {transport.matched_order_count}/{len(names)}  AWBs: {transport.awb_count}", file=sys.stderr)
    print(f"  net transport: {transport.net_total:,.2f} RON  "
          f"(measured {transport.awbs_measured} / backfilled {transport.awbs_backfilled} / estimated {transport.awbs_estimated})", file=sys.stderr)
    print(f"  net breakdown: measured {transport.net_measured:,.2f}  backfilled {transport.net_backfilled:,.2f}  estimated {transport.net_estimated:,.2f}", file=sys.stderr)
    if transport.orders_missing_awb:
        print(f"  WARN: {len(transport.orders_missing_awb)} included orders have NO AWB row at all (no transport counted)", file=sys.stderr)

    print(f"[4/7] DPD spot-check (n={args.spot_check})…", file=sys.stderr)
    db_lookup_gross = {}
    if args.spot_check > 0 and transport.tracking_for_spotcheck:
        match_rows = fetchall(awb,
            "SELECT id FROM orders WHERE store_uid = %s AND order_number = ANY(%s)",
            (GRANDIA_STORE_UID, names))
        ids = [r["id"] for r in match_rows]
        if ids:
            gross_rows = fetchall(awb,
                "SELECT tracking_number, transport_cost FROM order_awbs WHERE order_id = ANY(%s)",
                (ids,))
            db_lookup_gross = {r["tracking_number"]: float(r["transport_cost"] or 0) for r in gross_rows}
    dpd_check = dpd_spotcheck(transport.tracking_for_spotcheck, args.spot_check, db_lookup_gross) \
                if args.spot_check > 0 else {"checked": 0, "agree": 0, "disagree": 0, "details": [], "skipped": True}
    print(f"  checked={dpd_check.get('checked',0)} agree={dpd_check.get('agree',0)} disagree={dpd_check.get('disagree',0)}", file=sys.stderr)

    print("[5/7] FX index…", file=sys.stderr)
    fx = build_fx_index(awb, ["USD"], start, end)

    print("[6/7] ad spend (live)…", file=sys.stderr)
    meta = meta_spend(met, start, end);       print(f"  Meta:       {meta['total_ron']:,.2f} RON", file=sys.stderr)
    gads = google_ads_spend(met, start, end); print(f"  GoogleAds:  {gads['total_ron']:,.2f} RON", file=sys.stderr)
    tt   = tiktok_spend(met, fx, start, end)
    tt_native = sum(a['spend_native'] for a in tt['accounts'])
    tt_cur = tt['accounts'][0]['currency'] if tt['accounts'] else ''
    print(f"  TikTok:     {tt['total_ron']:,.2f} RON (native: {tt_native:,.2f} {tt_cur})", file=sys.stderr)

    pnl = PnL(start, end, rev, cogs, transport, meta, gads, tt, dpd_check, len(orders))

    print("\n== SUMMARY ==", file=sys.stderr)
    print(f"  Gross revenue   : {pnl.gross_revenue:>14,.2f} RON  ({len(rev.included_orders)} orders)", file=sys.stderr)
    print(f"  Net revenue     : {pnl.net_revenue:>14,.2f} RON", file=sys.stderr)
    print(f"  Net COGS        : {pnl.net_cogs:>14,.2f} RON", file=sys.stderr)
    if pnl.net_revenue:
        print(f"  Gross margin    : {pnl.gross_margin:>14,.2f} RON  ({pnl.gross_margin/pnl.net_revenue*100:.1f}%)", file=sys.stderr)
        print(f"  Net transport   : {pnl.net_transport:>14,.2f} RON", file=sys.stderr)
        print(f"  Ad spend total  : {pnl.total_ad_spend:>14,.2f} RON", file=sys.stderr)
        print(f"  Contrib. margin : {pnl.contribution_margin:>14,.2f} RON  ({pnl.contribution_margin/pnl.net_revenue*100:.1f}%)", file=sys.stderr)
        if pnl.mer:
            print(f"  MER             : {pnl.mer:.3f}", file=sys.stderr)
    print(f"\n  excluded (informational):", file=sys.stderr)
    for st, agg in sorted(rev.excluded.items(), key=lambda kv: -kv[1]["gross"]):
        print(f"    {st:<22} {agg['count']:>4} orders   gross {agg['gross']:>12,.2f} RON", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "period": [start.isoformat(), end.isoformat()],
            "summary": {
                "gross_revenue": pnl.gross_revenue, "net_revenue": pnl.net_revenue,
                "net_cogs": pnl.net_cogs, "gross_margin": pnl.gross_margin,
                "net_transport": pnl.net_transport, "ad_spend_total": pnl.total_ad_spend,
                "contribution_margin": pnl.contribution_margin, "mer": pnl.mer,
                "included_orders": len(rev.included_orders),
                "refunds_issued": rev.refunded_total,
            },
            "excluded": rev.excluded, "cogs": cogs.__dict__,
            "meta": meta, "google_ads": gads, "tiktok": tt,
            "dpd_check": {k: v for k, v in dpd_check.items() if k != "details"},
        }, indent=2, default=str))

    if args.sheet_id:
        tab = args.tab or f"P&L {start.strftime('%b %Y')}"
        print(f"\n[push] Sheet {args.sheet_id} tab='{tab}'", file=sys.stderr)
        push_sheet(args.sheet_id, tab, render_rows(pnl))
        print("  done", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
