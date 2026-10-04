"""
api/profitability.py — Monthly Profitability Report Module.

Fetches all orders from Shopify for a given month, computes COGS,
tracks courier status, maps statuses to categories, and generates
deliverability + profitability reports.

Endpoints:
  POST /api/profitability/run          — SSE: fetch + process orders for a month
  GET  /api/profitability/report       — JSON report (livrabilitate + profitabilitate)
  GET  /api/profitability/months       — available months
  GET  /api/profitability/transport-costs  — transport costs per store/month
  POST /api/profitability/transport-costs  — save transport costs
  GET  /api/profitability/settings     — settings
  POST /api/profitability/settings     — save settings
  GET  /api/profitability/cogs-missing — missing COGS list
  POST /api/profitability/cogs-override — save manual COGS
  GET  /api/profitability/status-mapping — status mapping
  POST /api/profitability/status-mapping — save status mapping
"""

import asyncio
import csv
import json
import logging
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import BASE_DIR, DATA_DIR, COURIER_CONFIG
from core.greu import greu
from core.stores import list_stores, get_store

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

router = APIRouter()
log = logging.getLogger("profitability")

DB_PATH = DATA_DIR / "profitability.db"
DB_WRITE_LOCK = threading.Lock()
STATUS_MAPPING_CSV = BASE_DIR / "data" / "status_mapping.csv"

DEFAULT_API_VERSION = "2024-10"
DPD_TRACK_URL = "https://api.dpd.ro/v1/track"
DPD_ACCOUNTS = ["dpd-ro", "dpd-jg", "dpd-px"]
PACKETA_DEFAULT_BASE_URL = "https://www.zasilkovna.cz/api/rest"

# Brand names in daily_perf.db → prefix codes in profitability
# Sursa unica: core/brands.py (consolidat din duplicarea profitability/perfume_stock)
from core.brands import BRAND_TO_PREFIX

# ═══════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════

class RunRequest(BaseModel):
    month: str  # YYYY-MM
    force: bool = False
    resync_shopify: bool = False

class TransportCostItem(BaseModel):
    month: str
    prefix: str
    cost_per_parcel: float
    vat_included: bool = False

class TransportCostsBulk(BaseModel):
    items: List[TransportCostItem]

class SettingsModel(BaseModel):
    exclude_test: bool = True
    country_map: Dict[str, str] = {}
    vat_rates: Dict[str, float] = {}

class CogsOverride(BaseModel):
    sku: str
    unit_cost: float
    currency: str = "RON"

class StatusMappingItem(BaseModel):
    courier_status: str
    category: str  # Livrata, Anulata, Refuzata, Netrimisa, In curs de livrare

class StatusMappingBulk(BaseModel):
    items: List[StatusMappingItem]

class MarketingOverrideItem(BaseModel):
    month: str
    prefix: str
    amount: float

class MarketingOverrideBulk(BaseModel):
    items: List[MarketingOverrideItem]

class CommissionOverrideItem(BaseModel):
    month: str
    prefix: str
    amount: Optional[float] = None   # None = șterge override-ul, revino la calcul

class CommissionOverrideBulk(BaseModel):
    items: List[CommissionOverrideItem]

class CommissionRateItem(BaseModel):
    prefix: str
    from_month: str
    rate: Optional[float] = None   # None = șterge regula

class CommissionRateBulk(BaseModel):
    items: List[CommissionRateItem]


# ═══════════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════════

@contextmanager
def _db():
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                prefix TEXT NOT NULL,
                shop TEXT NOT NULL,
                order_name TEXT NOT NULL,
                created_at TEXT,
                revenue REAL DEFAULT 0,
                currency TEXT DEFAULT 'RON',
                cogs REAL DEFAULT 0,
                cogs_missing INTEGER DEFAULT 0,
                cogs_missing_skus TEXT DEFAULT '',
                payment_status TEXT DEFAULT '',
                fulfillment_status TEXT DEFAULT '',
                awb TEXT DEFAULT '',
                courier_key TEXT DEFAULT '',
                courier_status TEXT DEFAULT '',
                status_category TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                UNIQUE(month, prefix, order_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_transport_costs (
                month TEXT NOT NULL,
                prefix TEXT NOT NULL,
                cost_per_parcel REAL DEFAULT 13,
                vat_included INTEGER DEFAULT 0,
                PRIMARY KEY (month, prefix)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_cogs_override (
                sku TEXT PRIMARY KEY,
                unit_cost REAL NOT NULL,
                currency TEXT DEFAULT 'RON'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Denumirea REALĂ a magazinului, citită din Shopify la fiecare rulare. Înainte era o hartă
        # scrisă de mână în UI, care rămăsese în urmă: BON și GRAN erau inversate (BON e
        # CasaOfertelor.ro, nu Bonhaus), iar LAB/ORC/SK/HU/MD/DUPBG lipseau cu totul.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_store_names (
                prefix TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                domain TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_status_mapping (
                courier_status TEXT PRIMARY KEY,
                category TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_exchange_rates (
                month TEXT NOT NULL,
                currency TEXT NOT NULL,
                rate_to_ron REAL NOT NULL,
                PRIMARY KEY (month, currency)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_marketing_override (
                month TEXT NOT NULL,
                prefix TEXT NOT NULL,
                amount REAL DEFAULT 0,
                PRIMARY KEY (month, prefix)
            )
        """)
        # Comisionul de agenție. Calculul implicit e `rată × (încasări − transport)`, dar rata NU e
        # aceeași peste tot (Grandia e 1.25%, restul 2.5%) și uneori se facturează o sumă negociată.
        # Deci suma manuală, când există, SUPRASCRIE calculul — vezi `_commission_for`.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_commission_override (
                month TEXT NOT NULL,
                prefix TEXT NOT NULL,
                amount REAL NOT NULL,
                PRIMARY KEY (month, prefix)
            )
        """)
        # Rata de comision NU e aceeaşi peste tot şi se schimbă în timp (Grandia: 1.25% → 0% din
        # iulie 2026). O ținem ca REGULĂ cu lună de start, nu ca sume pe fiecare lună — altfel o lună
        # viitoare fără rând ar recădea tăcut pe rata globală. Se aplică regula cu cel mai mare
        # `from_month <= luna cerută`.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_commission_rate (
                prefix TEXT NOT NULL,
                from_month TEXT NOT NULL,
                rate REAL NOT NULL,
                PRIMARY KEY (prefix, from_month)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_exclusion_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_type TEXT NOT NULL,
                value TEXT NOT NULL,
                reason TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(rule_type, value)
            )
        """)
        # Add skus column to profit_orders if missing
        try:
            conn.execute("ALTER TABLE profit_orders ADD COLUMN skus TEXT DEFAULT ''")
        except Exception:
            pass
        # Add shopify_delivery_status column if missing
        try:
            conn.execute("ALTER TABLE profit_orders ADD COLUMN shopify_delivery_status TEXT DEFAULT ''")
        except Exception:
            pass
        # SKU → product title cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profit_sku_titles (
                sku TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                image_url TEXT DEFAULT ''
            )
        """)
        # Add image_url column if missing
        try:
            conn.execute("ALTER TABLE profit_sku_titles ADD COLUMN image_url TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute("""
            INSERT OR IGNORE INTO profit_exclusion_rules (rule_type, value, reason)
            VALUES ('tag', 'test', 'Comenzi de test')
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_po_month ON profit_orders(month)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_po_prefix ON profit_orders(month, prefix)")
        # Perf: tracking-ul actualizeaza/cauta repetat dupa (month, awb) si (month, status_category)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_po_month_awb ON profit_orders(month, awb)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_po_month_status ON profit_orders(month, status_category)")

_ensure_db()


# ═══════════════════════════════════════════════════════════════
# Settings helpers
# ═══════════════════════════════════════════════════════════════

DEFAULT_COUNTRY_MAP = {
    "APR": "RO", "BELA": "RO", "BON": "RO", "CARP": "RO", "COV": "RO",
    "EST": "RO", "GEN": "RO", "GRAND": "RO", "GT": "RO", "LUX": "RO",
    "MAG": "RO", "NOC": "RO", "NUB": "RO", "OFER": "RO", "PAT": "RO",
    "RED": "RO", "ROSSI": "RO", "CZ": "CZ", "PL": "PL", "BG": "BG",
    "BONBG": "BG",
    "ORC": "RO",   # Orice Redus (oriceredus.ro) — RON, TVA 21%
    "SK": "SK",    # Bonhaus.sk  — EUR, TVA 23%
    "HU": "HU",    # Bonhaus.hu  — HUF, TVA 27%
    "MD": "MD",    # Duppo Moldova — MDL, TVA 20%
    "DUPBG": "BG", # Duppo BG — țara BG, dar magazinul vinde în EUR
}

DEFAULT_VAT_RATES = {"RO": 0.21, "CZ": 0.21, "PL": 0.23, "BG": 0.20, "SK": 0.23,
                     "HU": 0.27, "MD": 0.20}  # RO trecut la 21% (aug 2025)

CURRENCY_BY_COUNTRY = {"RO": "RON", "CZ": "CZK", "PL": "PLN", "BG": "BGN", "SK": "EUR",
                       "HU": "HUF", "MD": "MDL"}
# ⚠️ E doar un DEFAULT per țară — conversia reală folosește `profit_orders.currency` (moneda din
# Shopify, per comandă). Ex: Duppo BG (prefix DUPBG, țara BG) vinde în EUR, nu BGN.


def _get_settings() -> dict:
    with _db() as conn:
        rows = conn.execute("SELECT key, value FROM profit_settings").fetchall()
    d = {r["key"]: r["value"] for r in rows}
    return {
        "exclude_test": json.loads(d.get("exclude_test", "true")),
        "country_map": json.loads(d.get("country_map", json.dumps(DEFAULT_COUNTRY_MAP))),
        "vat_rates": json.loads(d.get("vat_rates", json.dumps(DEFAULT_VAT_RATES))),
    }


def _save_settings(settings: dict):
    with _db() as conn:
        for k, v in settings.items():
            conn.execute(
                "INSERT OR REPLACE INTO profit_settings (key, value) VALUES (?, ?)",
                (k, json.dumps(v))
            )


# ═══════════════════════════════════════════════════════════════
# Status Mapping
# ═══════════════════════════════════════════════════════════════

def _load_status_mapping() -> Dict[str, str]:
    """Load status mapping from DB, falling back to CSV file."""
    with _db() as conn:
        rows = conn.execute("SELECT courier_status, category FROM profit_status_mapping").fetchall()
    if rows:
        return {r["courier_status"]: r["category"] for r in rows}

    # Fallback: load from CSV and save to DB
    mapping = {}
    csv_path = BASE_DIR / "data" / "status_mapping.csv"
    if not csv_path.exists():
        # Try original location
        csv_path2 = BASE_DIR.parent / "Mapare statusuri.csv"
        if csv_path2.exists():
            csv_path = csv_path2
    if csv_path.exists():
        try:
            with open(str(csv_path), "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cs = (row.get("courier_status") or "").strip()
                    cat = (row.get("mapare") or row.get("category") or "").strip()
                    if cs and cat:
                        mapping[cs] = cat
        except Exception as e:
            log.warning("Cannot read status mapping CSV: %s", e)

    if mapping:
        _save_status_mapping(mapping)

    return mapping


def _save_status_mapping(mapping: Dict[str, str]):
    with _db() as conn:
        conn.execute("DELETE FROM profit_status_mapping")
        conn.executemany(
            "INSERT INTO profit_status_mapping (courier_status, category) VALUES (?, ?)",
            [(k, v) for k, v in mapping.items()]
        )


# Pseudo-statusuri scrise de tracker: a le re-interoga n-are rost (AWB inexistent / date șterse
# de curier), deci închid comanda la fel ca un verdict real.
_NO_RETRY_STATUSES = {"awb invalid", "sameday expirat"}


def _confirmed_courier_statuses(mapping: Dict[str, str]) -> Set[str]:
    """Statusurile (lowercase) care reprezintă un VERDICT DE CURIER terminal.

    Doar astea scot un AWB din coada de verificat. „Închis" dedus din Shopify NU se califică:
    DPD închide și un retur cu un eveniment de livrare („Delivered Back to Sender"), pe care
    Shopify îl raportează tot ca `DELIVERED` — vezi _map_status.
    """
    return {k.strip().lower() for k, v in mapping.items()
            if v in ("Livrata", "Refuzata", "Anulata")} | _NO_RETRY_STATUSES


def _has_tag(tags: str, tag: str) -> bool:
    """Tag EXACT, nu subșir (`refuzata` nu trebuie să prindă `swap_request_bi` sau `nerefuzata`)."""
    return tag in {t.strip().lower() for t in (tags or "").split(",")}


def _map_status(courier_status: str, fulfillment_status: str, payment_status: str,
                awb: str, mapping: Dict[str, str],
                shopify_delivery_status: str = "",
                unmapped_collector: Optional[Dict[str, int]] = None,
                tags: str = "") -> str:
    """
    Map raw courier status to category.
    Logic mirrors user's Excel formula exactly:
      - AWB Invalid / Sameday expirat → fallback to fulfillment/payment
      - Mapped → use mapping
      - No AWB + fulfilled → "Lipsa awb"
      - No AWB + unfulfilled + voided/refunded → "Anulata"
      - No AWB + unfulfilled → "Netrimisa"
      - Has AWB + no mapping + fulfilled → "In curs de livrare"
      - Has AWB + no mapping + voided/refunded → "Anulata"
      - Has AWB + no mapping → "Netrimisa"
    """
    cs = (courier_status or "").strip()
    cs_lower = cs.lower().strip()
    fs = (fulfillment_status or "").upper().strip()
    ps = (payment_status or "").upper().strip()
    awb_val = (awb or "").strip()

    is_voided = ps in ("VOIDED", "REFUNDED", "PARTIALLY_REFUNDED")
    is_paid = ps in ("PAID", "PARTIALLY_PAID")
    is_unfulfilled = ("UNFULFILLED" in fs) or (fs == "") or (fs == "UNFULFILLED")
    is_fulfilled = ("FULFILLED" in fs) and ("UNFULFILLED" not in fs)

    # 1) AWB Invalid / Sameday expirat → fallback to fulfillment/payment
    #    Sameday purges tracking data after ~45 days, so we must
    #    infer the status from Shopify payment/fulfillment fields.
    if cs_lower in ("awb invalid", "sameday expirat"):
        if is_unfulfilled:
            return "Anulata" if is_voided else "Netrimisa"
        else:
            if is_voided:
                return "Refuzata"
            if is_paid:
                return "Livrata"
            return "In curs de livrare"

    # 2) Try mapping (direct + case-insensitive)
    if cs in mapping:
        return mapping[cs]
    for k, v in mapping.items():
        if k.lower().strip() == cs_lower:
            return v

    # 2b) Shopify zice că fulfillment-ul e livrat.
    #     ⚠️ NU e dovadă de livrare: DPD închide și un RETUR cu un eveniment de livrare
    #     („Delivered Back to Sender"), iar Shopify îl raportează tot `DELIVERED`. Un COD chiar
    #     livrat ajunge PAID; dacă plata a rămas PENDING sau OrderHub a pus tag-ul `refuzata`,
    #     coletul s-a întors. Verificat la DPD pe 1.599 AWB (aug-2026): regula are 100% precizie
    #     pe iulie și 96% pe august, fără să retrogradeze nicio livrare reală.
    #     E doar o plasă de siguranță până vine verdictul curierului — care are prioritate (pasul 2).
    sds = (shopify_delivery_status or "").upper().strip()
    if sds == "DELIVERED":
        if ps == "PENDING" or _has_tag(tags, "refuzata"):
            return "Refuzata"
        return "Livrata"

    # 3) No courier status at all
    if not cs:
        if not awb_val:
            # No AWB
            if is_fulfilled:
                return "Lipsa awb"
            if is_voided:
                return "Anulata"
            return "Netrimisa"  # unfulfilled pending/paid = netrimisa
        else:
            # Has AWB but courier didn't return status
            if is_fulfilled:
                return "In curs de livrare"
            if is_voided:
                return "Anulata"
            return "Netrimisa"

    # 4) Has courier status but NOT in mapping → unmapped!
    if cs and unmapped_collector is not None:
        unmapped_collector[cs] = unmapped_collector.get(cs, 0) + 1

    # Fallback: if has AWB + status but no mapping
    if not awb_val:
        if is_fulfilled:
            return "Lipsa awb"
        if is_voided:
            return "Anulata"
        return "Netrimisa"
    else:
        if is_fulfilled:
            return "In curs de livrare"
        if is_voided:
            return "Anulata"
        return "Netrimisa"


# ═══════════════════════════════════════════════════════════════
# Curs valutar — sursa canonică: BNR (warehouse metrics.fx_rates + XML oficial curs.bnr.ro)
# ═══════════════════════════════════════════════════════════════

def _bnr_month_rates(month: str) -> Dict[str, float]:
    """Media LUNARĂ <VAL>→RON din `metrics.fx_rates` (feed BNR, 37 monede, sincronizat zilnic de
    fx_rates_sync.py). Sursa CANONICĂ de curs: frankfurter.app — folosit înainte — întoarce 403
    din aug-2026, iar fallback-ul hardcodat (EUR 4.97 vs 5.27 real) subevalua venitul intl ~6%."""
    try:
        import os as _os, re as _re
        import psycopg2 as _pg

        def _cl(d):
            d = _re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
            return _re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")

        conn = _pg.connect(_cl(_os.environ["DATABASE_URL_METRICS"]))
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT "fromCurrency", AVG(rate) FROM fx_rates
                   WHERE "toCurrency" = 'RON' AND to_char("rateDate", 'YYYY-MM') = %s
                   GROUP BY 1""", (month,))
            out = {c: float(r) for c, r in cur.fetchall() if r and float(r) > 0}
        finally:
            conn.close()
        if out:
            log.info("FX %s: %d monede din BNR (metrics.fx_rates)", month, len(out))
        return out
    except Exception as e:
        log.warning("FX BNR lookup failed for %s: %s", month, e)
        return {}


# Rata IREVOCABILĂ de conversie BGN→EUR (Bulgaria a intrat în euro pe 1-ian-2026). BNR nu mai
# publică BGN după decembrie 2025, dar mai avem comenzi în BGN (33, toate din 1-ian-2026), deci
# cursul lor se derivă din EUR cu pegul — NU dintr-o valoare hardcodată.
BGN_PER_EUR = 1.95583

_BNR_XML_CACHE: Dict[int, Dict[str, Dict[str, float]]] = {}


async def _bnr_xml_year(year: int) -> Dict[str, Dict[str, float]]:
    """Medii lunare <VAL>→RON dintr-un an, din XML-ul OFICIAL BNR (curs.bnr.ro).

    Sursa canonică de curs: acoperă tot istoricul (warehouse-ul `metrics.fx_rates` începe abia în
    2026-04) și BGN până la adoptarea euro. ⚠️ `www.bnr.ro/files/xml/...` dă 302 → **curs.bnr.ro**.
    """
    if year in _BNR_XML_CACHE:
        return _BNR_XML_CACHE[year]
    acc: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(f"https://curs.bnr.ro/files/xml/years/nbrfxrates{year}.xml")
            r.raise_for_status()
        for cube in ET.fromstring(r.content).iter():
            if not cube.tag.endswith("Cube"):
                continue
            day = cube.get("date")
            if not day:
                continue
            for node in cube:
                cur = node.get("currency")
                if not cur:
                    continue
                try:
                    acc[day[:7]][cur].append(float(node.text) / float(node.get("multiplier") or 1))
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        log.warning("BNR XML %s indisponibil: %s", year, e)
        return {}
    out = {m: {c: sum(v) / len(v) for c, v in d.items() if v} for m, d in acc.items()}
    if out:
        _BNR_XML_CACHE[year] = out
    return out


def _is_open_month(month: str) -> bool:
    """Luna curentă (încă în desfășurare) — cursul ei se REÎMPROSPĂTEAZĂ, nu se îngheață."""
    try:
        now = datetime.now(ZoneInfo("Europe/Bucharest")) if ZoneInfo else datetime.now()
        return month >= now.strftime("%Y-%m")
    except Exception:
        return False


async def _canonical_rates(month: str) -> Dict[str, float]:
    """Cursul ADEVĂRAT al lunii: warehouse BNR, completat din XML-ul BNR, plus BGN din peg."""
    out = {c: v for c, v in _bnr_month_rates(month).items() if c != "RON" and v > 0}
    xml = (await _bnr_xml_year(int(month[:4]))).get(month, {})
    for c, v in xml.items():
        if v > 0:
            out.setdefault(c, v)
    # BGN: BNR nu-l mai publică după adoptarea euro → rata irevocabilă față de EUR
    if "BGN" not in out and out.get("EUR"):
        out["BGN"] = out["EUR"] / BGN_PER_EUR
    return out


async def _fetch_exchange_rates(month: str) -> Dict[str, float]:
    """Curs {monedă: rată_în_RON} pentru o lună.

    Ordinea: **BNR** (warehouse `metrics.fx_rates` → XML oficial curs.bnr.ro → peg BGN), apoi cache.
    Reguli, învăţate dintr-un bug care a costat bani:
    - **Luna DESCHISĂ se reîmprospătează**: BNR suprascrie cache-ul. Înainte, cursul scris în prima
      zi a lunii (când BNR n-avea încă zile şi se cădea pe fallback) rămânea lipit toată luna —
      august 2026 stătea pe EUR 4,97 în loc de 5,2469, adică ~17.500 lei venit intl neraportat.
    - **Luna ÎNCHISĂ rămâne îngheţată**: cache-ul e adevărul cu care s-a raportat deja P&L-ul; BNR
      completează doar monedele care lipsesc (ex. o monedă apărută cu un magazin nou).
    - **Fallback-ul hardcodat NU se mai scrie niciodată în cache** — o valoare aproximativă lipită
      acolo nu mai putea fi corectată de nicio sursă. Se foloseşte doar în memorie, cu warning.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT currency, rate_to_ron FROM profit_exchange_rates WHERE month=?", (month,)
        ).fetchall()
    cached = {r["currency"]: r["rate_to_ron"] for r in rows if r["rate_to_ron"] and r["rate_to_ron"] > 0}

    canonical = await _canonical_rates(month)
    is_open = _is_open_month(month)
    fresh = {c: v for c, v in canonical.items()
             if is_open or c not in cached}
    fresh = {c: v for c, v in fresh.items() if abs(v - cached.get(c, 0)) > 1e-9}

    if fresh:
        with _db() as conn:
            for c, v in fresh.items():
                conn.execute("INSERT OR REPLACE INTO profit_exchange_rates (month,currency,rate_to_ron) "
                             "VALUES (?,?,?)", (month, c, v))
        log.info("FX %s: %d cursuri actualizate din BNR (%s)", month, len(fresh),
                 "lună deschisă" if is_open else "goluri completate")
        cached.update(fresh)

    # Ultimă plasă: valori aproximative, DOAR în memorie, ca raportul să nu iasă cu zero pe o monedă.
    FALLBACK = {"EUR": 4.97, "CZK": 0.21, "PLN": 1.16, "BGN": 2.54}
    missing = [c for c, v in FALLBACK.items() if c not in cached]
    if missing:
        log.warning("FX %s: fără curs BNR pentru %s — folosesc valori APROXIMATIVE, nesalvate",
                    month, ",".join(missing))
        for c in missing:
            cached[c] = FALLBACK[c]

    cached["RON"] = 1.0
    return cached


# ═══════════════════════════════════════════════════════════════
# Shopify GraphQL — Order Fetch (from orders_month_report.py)
# ═══════════════════════════════════════════════════════════════

ORDERS_GQL = """
query($q: String!, $cursor: String) {
  orders(first: 250, query: $q, after: $cursor, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id name createdAt currencyCode tags
        totalPriceSet { shopMoney { amount currencyCode } }
        currentTotalPriceSet { shopMoney { amount } }
        totalRefundedSet { shopMoney { amount } }
        displayFinancialStatus displayFulfillmentStatus
        metafield(namespace: "custom", key: "awb") { value }
        fulfillments(first: 10) {
          createdAt updatedAt displayStatus
          trackingInfo { company number url }
        }
        lineItems(first: 100) {
          edges { node { quantity variant { id } } }
        }
      }
    }
  }
}
"""

VARIANTS_COST_GQL = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id sku
      product { title featuredImage { url } }
      inventoryItem { unitCost { amount currencyCode } }
    }
  }
}
"""


SHOP_NAME_GQL = "{ shop { name myshopifyDomain primaryDomain { host } } }"


def _get_store_names() -> Dict[str, dict]:
    """{prefix: {"name": "Maison d'Esteban", "domain": "esteban.ro"}} — gol dacă n-a rulat încă."""
    try:
        with _db() as conn:
            return {r["prefix"]: {"name": r["name"] or "", "domain": r["domain"] or ""}
                    for r in conn.execute("SELECT prefix, name, domain FROM profit_store_names")}
    except Exception:
        return {}


async def _refresh_store_names(stores: List[dict]) -> None:
    """Ia denumirea reală a fiecărui magazin DIN SHOPIFY și o salvează.

    Rulează la fiecare `run` ca lista să nu mai rămână niciodată în urmă când se adaugă un magazin
    nou. Nu are voie să strice rularea: orice eroare se înghite (raportul cade înapoi pe prefix).
    """
    async def _one(client, st):
        try:
            r = await client.post(
                f"https://{st['shop']}/admin/api/{DEFAULT_API_VERSION}/graphql.json",
                headers={"X-Shopify-Access-Token": st["token"], "Content-Type": "application/json"},
                json={"query": SHOP_NAME_GQL}, timeout=20.0)
            shop = ((r.json().get("data") or {}).get("shop") or {})
            name = str(shop.get("name") or "").strip()
            domain = str(((shop.get("primaryDomain") or {}).get("host")) or "").strip()
            if name:
                return st["prefix"], name, domain
        except Exception as e:
            log.warning("nume magazin %s: %s", st.get("prefix"), e)
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await asyncio.gather(*[_one(client, st) for st in stores], return_exceptions=True)
        rows = [r for r in res if isinstance(r, tuple)]
        if rows:
            now = datetime.now(timezone.utc).isoformat()
            with _db() as conn:
                conn.executemany(
                    "INSERT INTO profit_store_names (prefix, name, domain, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(prefix) DO UPDATE SET name=excluded.name, domain=excluded.domain, updated_at=excluded.updated_at",
                    [(p, nm, dom, now) for p, nm, dom in rows])
                conn.commit()
        log.info("denumiri magazine actualizate: %d/%d", len(rows), len(stores))
    except Exception as e:
        log.warning("refresh denumiri magazine esuat: %s", e)


def _parse_month_range(month: str, tz_name: str = "Europe/Bucharest"):
    """Return (start_utc, end_utc) for a YYYY-MM month."""
    if not ZoneInfo:
        raise RuntimeError("zoneinfo unavailable")
    tz = ZoneInfo(tz_name)
    y, m = month.split("-")
    year, mo = int(y), int(m)
    start = datetime(year, mo, 1, 0, 0, 0, tzinfo=tz)
    if mo == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        end = datetime(year, mo + 1, 1, 0, 0, 0, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _parse_metafield_awb(val: str) -> Tuple[str, str]:
    s = (val or "").strip()
    if not s:
        return "", ""
    if s[:1] in "{[":
        try:
            j = json.loads(s)
            if isinstance(j, dict):
                return str(j.get("awb") or "").strip(), str(j.get("courier") or "").strip()
            if isinstance(j, list) and j:
                last = j[-1]
                if isinstance(last, dict):
                    return (str(last.get("awb") or last.get("number") or "").strip(),
                            str(last.get("courier") or last.get("company") or "").strip())
                if isinstance(last, str):
                    return last.strip(), ""
        except Exception:
            pass
    return s, ""


def _guess_courier(awb: str) -> str:
    a = (awb or "").strip().upper()
    if a.startswith("Z"):
        return "packeta"
    if a.startswith("8"):
        return "dpd-ro"
    if a.startswith("1O"):
        return "sameday"
    if a.lower().startswith("ee") or a.startswith("10"):
        return "econt"
    return "unknown"


def _pick_latest_fulfillment_awb(fulfillments):
    """Return (awb, company, displayStatus) from the most recent fulfillment."""
    if not fulfillments or not isinstance(fulfillments, list):
        return "", "", ""
    best_dt = datetime.min.replace(tzinfo=timezone.utc)
    best_awb, best_comp, best_ds = "", "", ""
    # Also track the latest fulfillment overall (even without trackingInfo)
    latest_ds = ""
    latest_dt = datetime.min.replace(tzinfo=timezone.utc)
    for f in fulfillments:
        if not isinstance(f, dict):
            continue
        ts = str(f.get("updatedAt") or f.get("createdAt") or "")
        ds = str(f.get("displayStatus") or "").strip()
        try:
            f_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            f_dt = datetime.min.replace(tzinfo=timezone.utc)
        # Track latest fulfillment displayStatus
        if f_dt >= latest_dt:
            latest_dt = f_dt
            if ds:
                latest_ds = ds
        for ti in (f.get("trackingInfo") or []):
            num = str(ti.get("number") or "").strip()
            comp = str(ti.get("company") or "").strip()
            if num and f_dt >= best_dt:
                best_dt, best_awb, best_comp, best_ds = f_dt, num, comp, ds
    # Use the displayStatus from the fulfillment with tracking, or fallback to latest
    return best_awb, best_comp, best_ds or latest_ds


async def _fetch_orders_for_store(
    client: httpx.AsyncClient, shop: str, token: str,
    start_utc: datetime, end_utc: datetime, prefix: str,
    progress_cb=None
) -> List[dict]:
    """Fetch all orders for a store in a month via GraphQL."""
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    # Exact boundaries for post-fetch filtering
    start_iso = start_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end_iso = end_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # Widen Shopify query by 1 day on each side (Shopify's filter is approximate)
    q_start = (start_utc - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    q_end = (end_utc + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    q = f"status:any created_at:>={q_start} created_at:<{q_end}"

    cursor = None
    orders = []
    page = 0

    while True:
        page += 1
        payload = {"query": ORDERS_GQL, "variables": {"q": q, "cursor": cursor}}

        for attempt in range(5):
            try:
                r = await client.post(url, headers=headers, json=payload, timeout=60.0)
                if r.status_code == 429:
                    await asyncio.sleep(2.0)
                    continue
                break
            except Exception:
                await asyncio.sleep(1.0)
        else:
            break

        if r.status_code != 200:
            log.error("%s: HTTP %s", shop, r.status_code)
            break

        try:
            j = r.json()
        except Exception:
            break

        # Throttle
        try:
            ts = ((j.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
            cur = int(ts.get("currentlyAvailable", 999))
            rr = float(ts.get("restoreRate", 50))
            if cur < 50 and rr > 0:
                await asyncio.sleep(min((50 - cur) / rr, 3.0))
        except Exception:
            pass

        data = (j.get("data") or {}).get("orders") or {}
        edges = data.get("edges") or []

        for ed in edges:
            node = (ed or {}).get("node") or {}
            name = str(node.get("name") or "").strip()
            tags_raw = node.get("tags") or []
            tags = ",".join(tags_raw) if isinstance(tags_raw, list) else str(tags_raw)

            total_set = (node.get("totalPriceSet") or {}).get("shopMoney") or {}
            currency = str(total_set.get("currencyCode") or node.get("currencyCode") or "").strip()
            # 8-sep-2026 (decizia ownerului): venitul e NET. `currentTotalPriceSet` e singurul camp
            # care acopera corect ambele cazuri, verificat pe tranzactiile reale din Shopify:
            #   * REFUNDED  -> SALE + CAPTURE + REFUND, current = 0  (banii s-au dat inapoi)
            #   * VOIDED    -> SALE PENDING + VOID,     current = 0  (COD-ul nu s-a incasat niciodata)
            #   * COD livrat dar inca necapturat in Shopify -> current = total (venitul se pastreaza,
            #     spre deosebire de netPaymentSet care ar fi 0 si ar sterge venit real)
            # Cade inapoi pe totalPriceSet daca magazinul nu intoarce campul.
            _cur_set = (node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
            _cur_amt = _cur_set.get("amount")
            revenue = float(_cur_amt) if _cur_amt is not None else float(total_set.get("amount") or 0)

            # AWB from metafield + fulfillments
            mf = node.get("metafield") or {}
            mf_val = str(mf.get("value") or "") if isinstance(mf, dict) else ""
            awb, courier_raw = _parse_metafield_awb(mf_val) if mf_val else ("", "")

            f_awb, f_comp, f_ds = _pick_latest_fulfillment_awb(node.get("fulfillments"))
            shopify_delivery_status = f_ds or ""
            if f_awb:
                awb = f_awb
                if f_comp:
                    courier_raw = f_comp

            courier_key = _guess_courier(awb) if awb else "unknown"

            # Variant quantities for COGS
            variant_qty = {}
            for led in (node.get("lineItems") or {}).get("edges") or []:
                ln = (led or {}).get("node") or {}
                qty = int(ln.get("quantity") or 0)
                vid = str((ln.get("variant") or {}).get("id") or "").strip()
                if qty > 0 and vid:
                    variant_qty[vid] = variant_qty.get(vid, 0) + qty

            orders.append({
                "prefix": prefix,
                "shop": shop,
                "order_name": name,
                "created_at": str(node.get("createdAt") or ""),
                "revenue": revenue,
                "currency": currency,
                "payment_status": str(node.get("displayFinancialStatus") or ""),
                "fulfillment_status": str(node.get("displayFulfillmentStatus") or ""),
                "awb": awb,
                "courier_key": courier_key,
                "courier_raw": courier_raw,
                "shopify_delivery_status": shopify_delivery_status,
                "variant_qty": variant_qty,
                "tags": tags,
            })

        pi = data.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")

        if progress_cb and page % 3 == 0:
            await progress_cb(f"{prefix}: pagina {page}, {len(orders)} comenzi")

    # Post-fetch filter: Shopify's query filter is approximate,
    # so we must verify each order's createdAt is within exact UTC range
    before_count = len(orders)
    filtered = []
    for o in orders:
        ca = o.get("created_at", "")
        if ca >= start_iso and ca < end_iso:
            filtered.append(o)
    if before_count != len(filtered):
        log.info("%s: filtered %d/%d orders outside month boundary",
                 prefix, before_count - len(filtered), before_count)
    return filtered


async def _fetch_variant_costs(
    client: httpx.AsyncClient, shop: str, token: str, variant_ids: List[str]
) -> Dict[str, dict]:
    """Fetch unit costs for variants."""
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    result = {}

    for i in range(0, len(variant_ids), 80):
        batch = variant_ids[i:i + 80]
        payload = {"query": VARIANTS_COST_GQL, "variables": {"ids": batch}}

        for attempt in range(5):
            try:
                r = await client.post(url, headers=headers, json=payload, timeout=60.0)
                if r.status_code == 429:
                    await asyncio.sleep(2.0)
                    continue
                break
            except Exception:
                await asyncio.sleep(1.0)
        else:
            continue

        if r.status_code != 200:
            continue

        try:
            j = r.json()
        except Exception:
            continue

        # Throttle
        try:
            ts = ((j.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
            cur = int(ts.get("currentlyAvailable", 999))
            rr = float(ts.get("restoreRate", 50))
            if cur < 50 and rr > 0:
                await asyncio.sleep(min((50 - cur) / rr, 3.0))
        except Exception:
            pass

        nodes = (j.get("data") or {}).get("nodes") or []
        for vid, node in zip(batch, nodes):
            if not node or not isinstance(node, dict):
                result[vid] = {"unit_cost": None, "sku": ""}
                continue
            sku = str(node.get("sku") or "").strip()
            prod = node.get("product") or {}
            prod_title = str(prod.get("title") or "").strip()
            prod_image = str((prod.get("featuredImage") or {}).get("url") or "").strip()
            inv = (node.get("inventoryItem") or {})
            uc = (inv.get("unitCost") or {}) if isinstance(inv, dict) else {}
            amount = uc.get("amount") if isinstance(uc, dict) else None
            unit_cost = float(amount) if amount is not None else None
            result[vid] = {"unit_cost": unit_cost, "sku": sku, "title": prod_title, "image_url": prod_image}

    return result



# ═══════════════════════════════════════════════════════════════
# Rate Limiter + Request Retry (from orders_month_report.py)
# ═══════════════════════════════════════════════════════════════

class SimpleRateLimiter:
    """Sliding-window rate limiter."""
    def __init__(self, max_calls: int, per_seconds: float):
        self.max_calls = max(1, int(max_calls))
        self.per_seconds = float(per_seconds)
        self._times: List[float] = []
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._times = [t for t in self._times if now - t < self.per_seconds]
            if len(self._times) >= self.max_calls:
                earliest = self._times[0]
                sleep_for = self.per_seconds - (now - earliest) + 0.01
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < self.per_seconds]
            self._times.append(time.monotonic())


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Any] = None,
    content: Optional[bytes] = None,
    timeout: float = 45.0,
    limiter: Optional[SimpleRateLimiter] = None,
    max_retries: int = 7,
    base_sleep: float = 0.2,
) -> httpx.Response:
    """HTTP request with retry + backoff + rate limiting."""
    last_exc: Optional[Exception] = None
    r = None
    for attempt in range(1, max_retries + 1):
        if limiter:
            await limiter.wait()
        try:
            if method.upper() == "GET":
                r = await client.get(url, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                if content is not None:
                    r = await client.post(url, headers=headers, content=content, timeout=timeout)
                else:
                    r = await client.post(url, headers=headers, json=json_body, timeout=timeout)
            else:
                raise ValueError("Unsupported method")
        except Exception as e:
            last_exc = e
            if attempt >= max_retries:
                raise
            await asyncio.sleep(min(2 ** attempt, 10))
            continue

        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            try:
                sleep_s = float(ra) if ra else min(2 ** attempt, 10)
            except Exception:
                sleep_s = min(2 ** attempt, 10)
            await asyncio.sleep(max(base_sleep, sleep_s))
            continue

        if r.status_code in (500, 502, 503, 504):
            if attempt >= max_retries:
                return r
            await asyncio.sleep(min(2 ** attempt, 10))
            continue

        return r

    if last_exc:
        raise last_exc
    return r


# ═══════════════════════════════════════════════════════════════
# DPD Bulk Tracking — download pre-generated tracking data files
# ═══════════════════════════════════════════════════════════════

async def _dpd_bulk_track(
    client: httpx.AsyncClient,
    account_key: str,
    creds: Dict[str, Any],
    target_awbs: Set[str],
    *,
    month: str = "",
    status_mapping: Dict[str, str] = None,
    progress_q: asyncio.Queue = None,
) -> Dict[str, str]:
    """
    Use DPD Bulk Tracking Data Files API to get tracking for all parcels at once.
    Downloads files concurrently for speed. Optionally pushes incremental progress.
    Returns {awb: description} for any AWBs in target_awbs found in the bulk data.
    """
    username = (creds.get("username") or "").strip()
    password = (creds.get("password") or "").strip()
    if not username or not password or not target_awbs:
        return {}

    # Bypass entirely because DPD Bulk Tracking API only retains files for the last 5 days.
    # It cannot be used for ad-hoc monthly reporting (e.g. for a month ago) because 
    # it always returns 0 matches for older AWBs, wasting time downloading huge archives.
    return {}

    results: Dict[str, str] = {}
    try:
        # Step 1: Get file list
        r = await client.post(
            "https://api.dpd.ro/v1/track/bulk",
            json={
                "userName": username,
                "password": password,
                "language": "RO",
                "lastProcessedFileId": 0,
            },
            timeout=30.0,
        )
        if r.status_code != 200:
            log.warning(f"DPD bulk [{account_key}]: HTTP {r.status_code}")
            return {}

        data = r.json()
        if data.get("error"):
            log.warning(f"DPD bulk [{account_key}]: {data['error']}")
            return {}

        files = data.get("files") or []
        if not files:
            return {}

        log.warning(f"DPD bulk [{account_key}]: downloading {len(files)} tracking files for {len(target_awbs)} AWBs")

        # Step 2: Download files concurrently in batches of 25
        parcel_latest: Dict[str, tuple] = {}
        sem = asyncio.Semaphore(25)

        async def _download_file(finfo):
            file_url = finfo.get("url")
            if not file_url:
                return []
            async with sem:
                try:
                    fr = await client.get(file_url, timeout=30.0, follow_redirects=True)
                    if fr.status_code != 200:
                        return []
                    parcels_data = fr.json()
                    if not isinstance(parcels_data, list):
                        return []
                    return parcels_data
                except Exception:
                    return []

        # Process in chunks of 40 files — update results incrementally
        chunk_size = 40
        for chunk_start in range(0, len(files), chunk_size):
            chunk = files[chunk_start:chunk_start + chunk_size]
            tasks = [asyncio.create_task(_download_file(f)) for f in chunk]
            file_results = await asyncio.gather(*tasks, return_exceptions=True)

            new_matches = 0
            for parcels_data in file_results:
                if isinstance(parcels_data, Exception) or not parcels_data:
                    continue
                for p in parcels_data:
                    pid = str(p.get("parcelId") or "")
                    if not pid or pid not in target_awbs:
                        continue
                    ops = p.get("operations") or []
                    if not ops:
                        continue
                    latest_op = max(ops, key=lambda o: str(o.get("dateTime") or ""))
                    dt = str(latest_op.get("dateTime") or "")
                    desc = (latest_op.get("description") or "").strip()
                    if not desc:
                        desc = f"Operation {latest_op.get('operationCode', '?')}"
                    prev = parcel_latest.get(pid)
                    if prev is None or dt > prev[0]:
                        parcel_latest[pid] = (dt, desc)
                        if pid not in results:
                            new_matches += 1
                        results[pid] = desc

            # Incremental DB update + progress if available
            if new_matches > 0 and month and status_mapping is not None:
                batch_results = {awb: results[awb] for awb in results if awb in target_awbs}
                _update_tracking_in_db(month, batch_results, status_mapping)
                if progress_q:
                    await progress_q.put(new_matches)

        log.warning(f"DPD bulk [{account_key}]: matched {len(results)}/{len(target_awbs)} AWBs")
    except Exception as e:
        log.warning(f"DPD bulk [{account_key}] error: {e}")

    return results


# ═══════════════════════════════════════════════════════════════
# Courier Tracking (robust, from orders_month_report.py)
# ═══════════════════════════════════════════════════════════════

def _is_dpd_too_many(status: str) -> bool:
    return "too many tracking requests" in (status or "").strip().lower()


def _is_dpd_not_accessible(status: str) -> bool:
    s = (status or "").strip().lower()
    return ("not accessible" in s) or ("shipment is not accessible" in s)


def _dpd_status_from_parcel(parcel: Any) -> str:
    if not parcel or not isinstance(parcel, dict):
        return "Fara date DPD"
    if parcel.get("error"):
        perr = parcel["error"]
        if isinstance(perr, dict):
            return str(perr.get("message") or "Eroare DPD parcel")
        return str(perr)
    ops = parcel.get("operations") or []
    if not ops:
        return "AWB Generat"
    op = max(ops, key=lambda o: str(o.get("dateTime") or ""))
    desc = (op.get("description") or "").strip() if op else ""
    return desc or "Status necunoscut"


async def _dpd_track_account(
    client: httpx.AsyncClient,
    account_key: str,
    creds: Dict[str, Any],
    awbs: List[str],
    limiter: SimpleRateLimiter,
    batch_size: int = 50,
    transient_passes: int = 3,
    *,
    progress_q: Optional[asyncio.Queue] = None,
    month: str = "",
    status_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Robust DPD tracking with retry on TooMany + not-accessible, and incremental DB/Queue updates."""
    results: Dict[str, str] = {}
    if not creds:
        for a in awbs:
            results[a] = f"Cont DPD '{account_key}' neconfigurat"
        return results

    seen: Set[str] = set()
    pending: List[str] = []
    for a in awbs:
        a = (a or "").strip()
        if a and a not in seen:
            seen.add(a)
            pending.append(a)
    if not pending:
        return results

    bs = max(1, int(batch_size))

    for pass_idx in range(1, max(1, int(transient_passes)) + 1):
        total = len(pending)
        total_batches = math.ceil(total / bs)
        next_pending: List[str] = []
        
        sem = asyncio.Semaphore(15)

        async def _fetch_batch(batch):
            async with sem:
                payload = {
                    "userName": creds.get("username"),
                    "password": creds.get("password"),
                    "language": "EN",
                    "lastOperationOnly": True,
                    "parcels": [{"id": a} for a in batch],
                }
                try:
                    r = await _request_with_retry(
                        client, "POST", DPD_TRACK_URL,
                        headers={"Accept": "application/json"},
                        json_body=payload, timeout=20.0,
                        limiter=limiter, max_retries=5, base_sleep=0.05,
                    )
                    if r.status_code != 200:
                        return batch, {"error": f"DPD HTTP {r.status_code}"}
                    return batch, r.json()
                except Exception as e:
                    return batch, {"error": f"DPD error: {str(e)[:80]}"}

        tasks = []
        for bi in range(total_batches):
            batch = pending[bi * bs: (bi + 1) * bs]
            tasks.append(asyncio.create_task(_fetch_batch(batch)))

        for coro in asyncio.as_completed(tasks):
            batch, data = await coro
            batch_updates = {}
            if isinstance(data, dict) and data.get("error"):
                msg = data["error"]
                if isinstance(msg, dict):
                    msg = msg.get("message") or "DPD error"
                for a in batch:
                    results[a] = str(msg)
                    batch_updates[a] = str(msg)
                    if _is_dpd_too_many(results[a]):
                        next_pending.append(a)
            else:
                parcels_data = (data.get("parcels") or []) if isinstance(data, dict) else []
                parcel_by_id: Dict[str, Any] = {}
                if isinstance(parcels_data, list):
                    for p in parcels_data:
                        if isinstance(p, dict):
                            pid = str(p.get("id") or p.get("parcelId") or "").strip()
                            if pid:
                                parcel_by_id[pid] = p

                for idx_b, awb_id in enumerate(batch):
                    parcel = parcel_by_id.get(awb_id)
                    if not parcel and isinstance(parcels_data, list) and idx_b < len(parcels_data):
                        parcel = parcels_data[idx_b]
                    st = _dpd_status_from_parcel(parcel)
                    results[awb_id] = st
                    batch_updates[awb_id] = st
                    if _is_dpd_too_many(st):
                        next_pending.append(awb_id)
            
            if progress_q and pass_idx == 1 and month and status_mapping is not None:
                # Fire and forget DB update and progress Queue so we dont block network loop
                asyncio.create_task(asyncio.to_thread(_update_tracking_in_db, month, batch_updates, status_mapping))
                asyncio.create_task(progress_q.put(len(batch)))
            elif month and status_mapping is not None and pass_idx > 1:
                # Still update DB on retries, but don't increment progress_q
                asyncio.create_task(asyncio.to_thread(_update_tracking_in_db, month, batch_updates, status_mapping))

        if not next_pending:
            break
        sleep_s = min(2.0 * pass_idx, 10.0)
        log.info("DPD %s: TooMany for %d AWBs -> sleep %.1fs", account_key, len(next_pending), sleep_s)
        await asyncio.sleep(sleep_s)
        pending = next_pending

    # If there are still remaining TooMany after transient passes,
    # we just accept them as is to avoid hanging the UI with thousands of individual reqs.
    for account_key, msg in list(results.items()):
        if _is_dpd_too_many(msg):
            log.warning(f"DPD {account_key}: AWB {msg} abandoned after {transient_passes} passes.")

    return results


SAMEDAY_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
SAMEDAY_TOKEN_LOCK = asyncio.Lock()


async def _get_sameday_token(client, creds, limiter):
    username = (creds.get("username") or "").strip()
    password = (creds.get("password") or "").strip()
    if not username or not password:
        return ""
    async with SAMEDAY_TOKEN_LOCK:
        cached = SAMEDAY_TOKEN_CACHE.get(username)
        if cached and datetime.now(timezone.utc) < cached["expires_at"]:
            return cached["token"]
        try:
            r = await _request_with_retry(
                client, "POST", "https://api.sameday.ro/api/authenticate",
                headers={"X-AUTH-USERNAME": username, "X-AUTH-PASSWORD": password},
                timeout=20.0, limiter=limiter, max_retries=6, base_sleep=0.2,
            )
            if r.status_code != 200:
                return ""
            token = str(r.json().get("token") or "").strip()
            if token:
                SAMEDAY_TOKEN_CACHE[username] = {
                    "token": token,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=55),
                }
            return token
        except Exception:
            return ""


async def _track_sameday_one(client, awb, creds, limiter):
    token = await _get_sameday_token(client, creds, limiter)
    if not token:
        return "Sameday auth error"
    try:
        r = await _request_with_retry(
            client, "GET", f"https://api.sameday.ro/api/client/awb/{awb}/status",
            headers={"X-AUTH-TOKEN": token}, timeout=25.0, limiter=limiter,
        )
    except Exception as e:
        return f"Sameday error: {str(e)[:60]}"
    if r.status_code == 404:
        return "Sameday expirat"  # Sameday purges data after ~45 days
    if r.status_code != 200:
        return f"Sameday HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return "Sameday non-JSON"
    hist = data.get("expeditionHistory") or []
    if not hist:
        return "Sameday expirat"  # No history = data purged or genuinely new; fallback to payment/fulfillment
    def _pdt(val):
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    last_ev = max(hist, key=lambda e: _pdt(e.get("statusDate") or e.get("date")))
    return str(last_ev.get("statusLabel") or "Status Necunoscut").strip()


async def _track_sameday_many(client, awbs, creds, rps=4, concurrency=8):
    results: Dict[str, str] = {}
    if not awbs:
        return results
    limiter = SimpleRateLimiter(max_calls=max(1, rps), per_seconds=1.0)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def worker(a):
        async with sem:
            results[a] = await _track_sameday_one(client, a, creds, limiter)

    tasks = [asyncio.create_task(worker(a)) for a in dict.fromkeys(awbs)]
    for coro in asyncio.as_completed(tasks):
        try:
            await coro
        except Exception as e:
            log.warning("Sameday task error: %s", e)
    return results


async def _track_econt_batch(client, awbs, creds, limiter):
    results: Dict[str, str] = {}
    if not awbs:
        return results
    if not creds or not creds.get("username"):
        return {a: "Econt neconfigurat" for a in awbs}
    url = "https://ee.econt.com/services/Shipments/ShipmentService.getShipmentStatuses.json"
    body = {"username": creds["username"], "password": creds.get("password"), "shipmentNumbers": awbs}
    try:
        r = await _request_with_retry(client, "POST", url, headers={"Accept": "application/json"},
                                       json_body=body, timeout=45.0, limiter=limiter)
    except Exception as e:
        return {a: f"Econt error: {str(e)[:60]}" for a in awbs}
    if r.status_code != 200:
        return {a: f"Econt HTTP {r.status_code}" for a in awbs}
    try:
        data = r.json()
    except Exception:
        return {a: "Econt non-JSON" for a in awbs}
    sts = data.get("shipmentStatuses") or []
    for awb_id, item in zip(awbs, sts):
        si = ((item or {}).get("status") or {}) if isinstance(item, dict) else {}
        desc = si.get("shortDeliveryStatusEn") or si.get("shortDeliveryStatusRo") or "In transit"
        results[awb_id] = str(desc).strip().title()
    for awb_id in awbs[len(sts):]:
        results.setdefault(awb_id, "Fara date Econt")
    return results


async def _track_econt_many(client, awbs, creds, rps=3):
    results: Dict[str, str] = {}
    if not awbs:
        return results
    limiter = SimpleRateLimiter(max_calls=max(1, rps), per_seconds=1.0)
    for i in range(0, len(awbs), 30):
        batch = awbs[i:i + 30]
        res = await _track_econt_batch(client, batch, creds, limiter)
        results.update(res)
    return results


async def _track_packeta_one(client, awb, creds, limiter):
    api_pw = (creds.get("api_password") or creds.get("password") or "").strip()
    if not api_pw:
        return "Packeta neconfigurat"
    base_url = (creds.get("base_url") or PACKETA_DEFAULT_BASE_URL).rstrip("/")
    accept_lang = (creds.get("accept_language") or "ro_RO").strip() or "ro_RO"
    xml = f"<packetTracking><apiPassword>{api_pw}</apiPassword><barcode>{awb}</barcode></packetTracking>".encode("utf-8")
    headers = {"Content-Type": "application/xml", "Accept": "application/xml", "Accept-Language": accept_lang}
    try:
        r = await _request_with_retry(client, "POST", base_url, headers=headers, content=xml,
                                       timeout=45.0, limiter=limiter, max_retries=6, base_sleep=0.15)
    except Exception as e:
        return f"Packeta error: {str(e)[:60]}"
    if r.status_code != 200:
        return f"Packeta HTTP {r.status_code}"
    txt = r.text or ""
    import re as _re
    codes = _re.findall(r"<codeText>(.*?)</codeText>", txt)
    if codes:
        return codes[-1]
    sc = _re.findall(r"<statusCode>(.*?)</statusCode>", txt)
    if sc:
        return sc[-1]
    return "Unknown"


async def _track_packeta_many(client, awbs, creds, rps=60, concurrency=60):
    results: Dict[str, str] = {}
    if not awbs:
        return results
    limiter = SimpleRateLimiter(max_calls=max(1, rps), per_seconds=1.0)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def worker(a):
        async with sem:
            results[a] = await _track_packeta_one(client, a, creds, limiter)

    tasks = [asyncio.create_task(worker(a)) for a in dict.fromkeys(awbs)]
    for coro in asyncio.as_completed(tasks):
        try:
            await coro
        except Exception as e:
            log.warning("Packeta task error: %s", e)
    return results


def _update_tracking_in_db(month: str, awb_status_map: Dict[str, str], status_mapping: dict):
    """Update courier_status + status_category for tracked AWBs in DB."""
    if not awb_status_map:
        return
    # Error prefixes that should NOT overwrite real courier statuses
    _error_prefixes = ("DPD error", "DPD HTTP", "DPD non-JSON", "Sameday error", "GLS error", "Fan error")
    with DB_WRITE_LOCK:
        with _db() as conn:
            for awb, status in awb_status_map.items():
                # Skip error statuses — don't overwrite real data with transient errors
                if any(status.startswith(ep) for ep in _error_prefixes):
                    continue

                # Skip transient DPD errors — "too many tracking requests" / "not accessible"
                # should never overwrite a real courier status already in the DB
                if _is_dpd_too_many(status) or _is_dpd_not_accessible(status):
                    # Check if there's already a real status saved
                    existing = conn.execute(
                        "SELECT courier_status FROM profit_orders WHERE month=? AND awb=? LIMIT 1",
                        (month, awb)
                    ).fetchone()
                    if existing and existing["courier_status"] and not _is_dpd_too_many(existing["courier_status"]) and not _is_dpd_not_accessible(existing["courier_status"]):
                        # Already has a real status — don't overwrite with error
                        continue

                row = conn.execute(
                    "SELECT payment_status, fulfillment_status, shopify_delivery_status, tags FROM profit_orders WHERE month=? AND awb=? LIMIT 1",
                    (month, awb)
                ).fetchone()
                if not row:
                    continue
                category = _map_status(status, row["fulfillment_status"], row["payment_status"], awb, status_mapping,
                                       shopify_delivery_status=row["shopify_delivery_status"] or "",
                                       tags=row["tags"] or "")
                conn.execute(
                    "UPDATE profit_orders SET courier_status=?, status_category=? WHERE month=? AND awb=?",
                    (status, category, month, awb)
                )


# ═══════════════════════════════════════════════════════════════
# Main Run Logic (SSE streaming)
# ═══════════════════════════════════════════════════════════════

@router.post("/api/profitability/run")
async def run_profitability(req: RunRequest):
    """Fetch orders, compute COGS, save to DB, then track AWBs. SSE streaming."""
    month = req.month.strip()
    if not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(400, "Format invalid. Foloseste YYYY-MM.")

    settings = _get_settings()
    status_mapping = _load_status_mapping()

    async def _stream():
        try:
            start_utc, end_utc = _parse_month_range(month)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        stores = list_stores()

        try:
            await sync_marketing_from_daily_perf(month, None, None, 1)
            log.info(f"Marketing sync complete for {month}")
        except Exception as e:
            log.error(f"Eroare preluare marketing din daily_perf.db: {e}")

        if not stores:
            yield f"data: {json.dumps({'type': 'error', 'message': 'stores.csv gol'})}\n\n"
            return

        total_stores = len(stores)

        # Denumirile reale, direct din Shopify (magazin nou adăugat => apare cu numele lui, singur).
        await _refresh_store_names(stores)

        # Check if orders already cached in DB
        with _db() as conn:
            existing_cnt = conn.execute("SELECT COUNT(*) FROM profit_orders WHERE month=?", (month,)).fetchone()[0]

        if existing_cnt > 0 and not req.resync_shopify:
            # Orders exist — skip Shopify+COGS, go straight to tracking
            msg = f'{existing_cnt} comenzi deja in DB. Skip Shopify.'
            if req.force:
                msg = f'{existing_cnt} comenzi in DB. Re-tracking AWB-uri...'
            yield f"data: {json.dumps({'type': 'phase', 'phase': 1, 'message': msg, 'progress': total_stores, 'total': total_stores})}\n\n"
            yield f"data: {json.dumps({'type': 'phase', 'phase': 2, 'message': 'COGS deja calculat.', 'progress': total_stores, 'total': total_stores})}\n\n"
        else:
            # Phase 1: Fetch orders from Shopify
            yield f"data: {json.dumps({'type': 'phase', 'phase': 1, 'message': f'Descarcare comenzi din {total_stores} magazine...', 'progress': 0, 'total': total_stores})}\n\n"

            all_orders = []
            completed_stores = 0

            async with httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_connections=40)) as client:
                sem = asyncio.Semaphore(5)

                async def fetch_store(store_info):
                    prefix = store_info["prefix"]
                    shop = store_info["shop"]
                    token = store_info["token"]

                    async with sem:
                        try:
                            orders = await _fetch_orders_for_store(client, shop, token, start_utc, end_utc, prefix)
                        except Exception as e:
                            log.error("Eroare Shopify %s: %s", shop, e)
                            orders = []

                        return prefix, orders

                # Fetch all stores concurrently (max 5 at a time via semaphore)
                tasks = [asyncio.create_task(fetch_store(s)) for s in stores]
                for coro in asyncio.as_completed(tasks):
                    try:
                        pfx, orders = await coro
                        all_orders.extend(orders)
                        completed_stores += 1
                        yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': completed_stores, 'total': total_stores, 'message': f'{pfx}: {len(orders)} comenzi'})}\n\n"
                    except Exception as e:
                        completed_stores += 1
                        log.error("Store fetch error: %s", e)

                # Phase 2: COGS
                yield f"data: {json.dumps({'type': 'phase', 'phase': 2, 'message': 'Calcul COGS...', 'progress': 0, 'total': total_stores})}\n\n"

                cogs_overrides = {}
                with _db() as conn:
                    for r in conn.execute("SELECT sku, unit_cost FROM profit_cogs_override").fetchall():
                        cogs_overrides[r["sku"]] = r["unit_cost"]

                cogs_done = 0
                by_shop = defaultdict(list)
                for o in all_orders:
                    by_shop[o["shop"]].append(o)

                for store_info in stores:
                    shop = store_info["shop"]
                    prefix = store_info["prefix"]
                    orders = by_shop.get(shop, [])
                    if not orders:
                        cogs_done += 1
                        continue

                    vset = set()
                    for o in orders:
                        vset.update(o.get("variant_qty", {}).keys())

                    vinfo = {}
                    if vset:
                        vinfo = await _fetch_variant_costs(client, shop, store_info["token"], list(vset))

                    # Save SKU → title + image mapping
                    sku_titles = [(info["sku"], info.get("title", ""), info.get("image_url", ""))
                                  for info in vinfo.values()
                                  if info.get("sku") and (info.get("title") or info.get("image_url"))]
                    if sku_titles:
                        with _db() as conn:
                            conn.executemany(
                                "INSERT OR REPLACE INTO profit_sku_titles (sku, title, image_url) VALUES (?, ?, ?)",
                                sku_titles
                            )

                    for o in orders:
                        cogs = 0.0
                        missing_qty = 0
                        missing_skus = []
                        all_skus = []
                        for vid, qty in o.get("variant_qty", {}).items():
                            info = vinfo.get(vid, {"unit_cost": None, "sku": ""})
                            sku = info.get("sku") or ""
                            if sku:
                                all_skus.append(sku)
                            if sku and sku in cogs_overrides:
                                cogs += cogs_overrides[sku] * qty
                            elif info["unit_cost"] is not None:
                                cogs += info["unit_cost"] * qty
                            else:
                                if sku:
                                    missing_qty += qty
                                    missing_skus.append(f"{sku}x{qty}" if qty > 1 else sku)
                        o["cogs"] = round(cogs, 4)
                        o["cogs_missing"] = missing_qty
                        o["cogs_missing_skus"] = "; ".join(missing_skus)
                        o["skus"] = "; ".join(all_skus)

                    cogs_done += 1
                    yield f"data: {json.dumps({'type': 'progress', 'phase': 2, 'progress': cogs_done, 'total': total_stores, 'message': f'{prefix}: COGS calculat'})}\n\n"

            # SAVE to DB immediately (before tracking!)
            yield f"data: {json.dumps({'type': 'progress', 'phase': 2, 'progress': total_stores, 'total': total_stores, 'message': f'Salvare {len(all_orders)} comenzi in DB...'})}\n\n"

            # Statusul de la curier e singura DOVADĂ de livrare pe care o avem; rescrierea lunii
            # din Shopify nu are voie să-l piardă. (Până la fix-ul din 2026-08-21 se scria "" la
            # fiecare rulare → 120 de retururi confirmate de DPD au fost re-etichetate „Livrata"
            # într-o singură noapte.) Îl păstrăm doar dacă AWB-ul a rămas același — la un AWB refăcut
            # statusul vechi nu mai descrie coletul care a plecat.
            with _db() as conn:
                prev_courier = {
                    (r["prefix"], r["order_name"]): (r["awb"] or "", r["courier_status"] or "")
                    for r in conn.execute(
                        "SELECT prefix, order_name, awb, courier_status FROM profit_orders "
                        "WHERE month=? AND COALESCE(courier_status,'')!=''", (month,))
                }
                # Ștergem DOAR prefixele pe care chiar le sincronizăm acum. Un magazin închis și
                # scos din sincronizare (marcat `active=0` în stores.csv, ex. BG/nocturna.bg în
                # aug-2026) și-ar pierde altfel TOT istoricul la prima re-sincronizare a unei luni
                # vechi — ștergerea era pe toată luna, iar re-inserarea aducea doar magazinele active.
                _synced = sorted({st["prefix"] for st in stores})
                _pp = ",".join("?" * len(_synced))
                conn.execute(f"DELETE FROM profit_orders WHERE month=? AND prefix IN ({_pp})",
                             [month] + _synced)
                for o in all_orders:
                    awb = (o.get("awb") or "").strip()
                    _prev = prev_courier.get((o["prefix"], o["order_name"]))
                    prev_cs = _prev[1] if (_prev and _prev[0] == awb and awb) else ""
                    conn.execute("""
                        INSERT OR REPLACE INTO profit_orders
                        (month, prefix, shop, order_name, created_at, revenue, currency,
                         cogs, cogs_missing, cogs_missing_skus, payment_status,
                         fulfillment_status, awb, courier_key, courier_status,
                         status_category, tags, skus, shopify_delivery_status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        month, o["prefix"], o["shop"], o["order_name"],
                        o.get("created_at", ""), o.get("revenue", 0), o.get("currency", "RON"),
                        o.get("cogs", 0), o.get("cogs_missing", 0), o.get("cogs_missing_skus", ""),
                        o.get("payment_status", ""), o.get("fulfillment_status", ""),
                        awb, o.get("courier_key", ""), prev_cs,
                        _map_status(prev_cs, o.get("fulfillment_status", ""), o.get("payment_status", ""),
                                    awb, status_mapping, shopify_delivery_status=o.get("shopify_delivery_status", ""),
                                    tags=o.get("tags", "")),
                        o.get("tags", ""), o.get("skus", ""), o.get("shopify_delivery_status", ""),
                    ))

            yield f"data: {json.dumps({'type': 'progress', 'phase': 2, 'progress': total_stores, 'total': total_stores, 'message': f'{len(all_orders)} comenzi salvate!'})}\n\n"

        # Phase 3: Tracking (reads AWBs from DB)
        # If orders already existed (re-sync), only track non-closed orders
        # „Închis" = are VERDICT DE CURIER terminal. O comandă pe care doar Shopify o dă livrată
        # rămâne în coadă până confirmă DPD — altfel un retur raportat de Shopify ca `DELIVERED`
        # nu mai e verificat niciodată (cauza celor 1.225 retururi numărate ca livrări în iulie).
        confirmed = sorted(_confirmed_courier_statuses(status_mapping))
        _ph = ",".join("?" * len(confirmed))
        with _db() as conn:
            if existing_cnt > 0:
                rows = conn.execute(
                    f"SELECT DISTINCT awb, courier_key FROM profit_orders "
                    f"WHERE month=? AND awb!='' AND LOWER(TRIM(COALESCE(courier_status,''))) NOT IN ({_ph})",
                    [month] + confirmed
                ).fetchall()
                closed_cnt = conn.execute(
                    f"SELECT COUNT(DISTINCT awb) FROM profit_orders "
                    f"WHERE month=? AND awb!='' AND LOWER(TRIM(COALESCE(courier_status,''))) IN ({_ph})",
                    [month] + confirmed
                ).fetchone()[0]
            else:
                # First run: track all
                rows = conn.execute(
                    "SELECT DISTINCT awb, courier_key FROM profit_orders WHERE month=? AND awb!=''",
                    (month,)
                ).fetchall()
                closed_cnt = 0

        config = COURIER_CONFIG
        dpd_creds = config.get("dpd_creds") or {}
        sameday_creds = config.get("sameday_creds") or {}
        econt_creds = config.get("econt_creds") or {}
        packeta_creds = config.get("packeta_creds") or config.get("packeta") or {}

        dpd_awbs_by_acc: Dict[str, List[str]] = defaultdict(list)
        sameday_awbs: List[str] = []
        econt_awbs: List[str] = []
        packeta_awbs: List[str] = []

        for r in rows:
            awb = r["awb"]
            ck = (r["courier_key"] or "").lower()
            if ck in DPD_ACCOUNTS:
                dpd_awbs_by_acc[ck].append(awb)
            elif ck == "sameday":
                sameday_awbs.append(awb)
            elif ck == "econt":
                econt_awbs.append(awb)
            elif ck == "packeta":
                packeta_awbs.append(awb)
            elif awb.startswith(("Z", "z")):
                packeta_awbs.append(awb)
            elif awb.startswith("8"):
                dpd_awbs_by_acc["dpd-ro"].append(awb)

        sameday_awbs = list(dict.fromkeys(sameday_awbs))
        econt_awbs = list(dict.fromkeys(econt_awbs))
        packeta_awbs = list(dict.fromkeys(packeta_awbs))

        total_awbs = sum(len(v) for v in dpd_awbs_by_acc.values()) + len(sameday_awbs) + len(econt_awbs) + len(packeta_awbs)
        tracked_awbs = 0

        skip_msg = f' ({closed_cnt} închise, skip)' if closed_cnt > 0 else ''
        yield f"data: {json.dumps({'type': 'phase', 'phase': 3, 'message': f'{total_awbs} AWB-uri de verificat{skip_msg}...', 'progress': 0, 'total': total_awbs})}\n\n"

        async with httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=30)
        ) as client:

            # --- All couriers run concurrently via asyncio.gather ---
            dpd_limiter = SimpleRateLimiter(max_calls=1, per_seconds=0.25)
            courier_results = {}

            progress_q: asyncio.Queue = asyncio.Queue()

            async def track_all_packeta():
                if not packeta_awbs:
                    return "Packeta", 0
                res = await _track_packeta_many(client, packeta_awbs, packeta_creds, rps=60, concurrency=60)
                _update_tracking_in_db(month, res, status_mapping)
                await progress_q.put(len(packeta_awbs))
                return "Packeta", len(packeta_awbs)

            # Wrap DPD to use BULK tracking first, then fallback for missing
            async def track_all_dpd_progressive():
                count = 0
                for acc, awbs_list in dpd_awbs_by_acc.items():
                    if not awbs_list:
                        continue
                    creds = dpd_creds.get(acc) or {}
                    if not creds:
                        await progress_q.put(len(awbs_list))
                        count += len(awbs_list)
                        continue
                    unique = list(dict.fromkeys(awbs_list))
                    target_set = set(unique)

                    # Phase 1: Bulk tracking (fast — downloads pre-generated files)
                    bulk_results = await _dpd_bulk_track(
                        client, acc, creds, target_set,
                        month=month, status_mapping=status_mapping, progress_q=progress_q,
                    )
                    # If bulk tracked with incremental progress, skip re-update
                    if bulk_results:
                        log.warning(f"DPD bulk [{acc}]: {len(bulk_results)}/{len(unique)} AWBs resolved via bulk")

                # Phase 2: Per-AWB fallback for any missing
                    missing = [a for a in unique if a not in bulk_results]
                    if missing:
                        log.warning(f"DPD [{acc}]: {len(missing)} AWBs not in bulk, falling back to per-AWB")
                        res = await _dpd_track_account(
                            client, acc, creds, missing, dpd_limiter, batch_size=50, transient_passes=100,
                            progress_q=progress_q, month=month, status_mapping=status_mapping
                        )

                    else:
                        await progress_q.put(len(unique) - len(bulk_results))

                    count += len(unique)
                return "DPD", count

            async def track_sameday_progressive():
                if not sameday_awbs:
                    return "Sameday", 0
                res = await _track_sameday_many(client, sameday_awbs, sameday_creds, rps=4, concurrency=8)
                _update_tracking_in_db(month, res, status_mapping)
                await progress_q.put(len(sameday_awbs))
                return "Sameday", len(sameday_awbs)

            async def track_econt_progressive():
                if not econt_awbs:
                    return "Econt", 0
                res = await _track_econt_many(client, econt_awbs, econt_creds, rps=3)
                _update_tracking_in_db(month, res, status_mapping)
                await progress_q.put(len(econt_awbs))
                return "Econt", len(econt_awbs)

            # Launch all as tasks
            tasks = [
                asyncio.create_task(track_all_dpd_progressive()),
                asyncio.create_task(track_sameday_progressive()),
                asyncio.create_task(track_econt_progressive()),
                asyncio.create_task(track_all_packeta()),
            ]

            # Monitor progress via queue
            import time
            last_heartbeat = time.time()
            while True:
                all_done = all(t.done() for t in tasks)
                while not progress_q.empty():
                    batch_cnt = progress_q.get_nowait()
                    tracked_awbs += batch_cnt

                    yield f"data: {json.dumps({'type': 'progress', 'phase': 3, 'progress': tracked_awbs, 'total': total_awbs, 'message': f'Verificare AWB-uri: {tracked_awbs:,} / {total_awbs:,}'})}\n\n"
                    last_heartbeat = time.time()

                # Cloudflare 100-sec timeout prevention
                if time.time() - last_heartbeat > 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()

                if all_done:
                    break
                await asyncio.sleep(0.5)

            # Collect results
            for t in tasks:
                try:
                    r = t.result()
                    if isinstance(r, tuple):
                        name, cnt = r
                        if cnt > 0:
                            courier_results[name] = cnt
                except Exception as e:
                    log.error("Courier tracking error: %s", e)

            yield f"data: {json.dumps({'type': 'progress', 'phase': 3, 'progress': tracked_awbs, 'total': total_awbs, 'message': f'Tracking complet — {tracked_awbs:,} AWB-uri verificate'})}\n\n"

            # DPD retry not-accessible on other accounts
            with _db() as conn:
                not_acc_rows = conn.execute(
                    "SELECT DISTINCT awb FROM profit_orders WHERE month=? AND courier_status LIKE '%not accessible%'",
                    (month,)
                ).fetchall()
            if not_acc_rows:
                not_acc_awbs = [r["awb"] for r in not_acc_rows]
                tried_accs = set(dpd_awbs_by_acc.keys())
                for alt_acc in DPD_ACCOUNTS:
                    if alt_acc in tried_accs or not not_acc_awbs:
                        continue
                    alt_creds = dpd_creds.get(alt_acc) or {}
                    if not alt_creds:
                        continue
                    yield f"data: {json.dumps({'type': 'progress', 'phase': 3, 'progress': tracked_awbs, 'total': total_awbs, 'message': f'Re-verificare {len(not_acc_awbs):,} AWB-uri...'})}\n\n"
                    res = await _dpd_track_account(client, alt_acc, alt_creds, not_acc_awbs, dpd_limiter, batch_size=50, transient_passes=100)
                    resolved = {a: st for a, st in res.items() if not _is_dpd_not_accessible(st)}
                    if resolved:
                        _update_tracking_in_db(month, resolved, status_mapping)
                        not_acc_awbs = [a for a in not_acc_awbs if a not in resolved]

        # Phase 4: Final mapping for orders without AWB
        yield f"data: {json.dumps({'type': 'phase', 'phase': 4, 'message': 'Mapare statusuri finale...', 'progress': 0, 'total': 1})}\n\n"

        unmapped = {}
        with _db() as conn:
            no_awb_rows = conn.execute(
                "SELECT id, payment_status, fulfillment_status, awb, courier_status, shopify_delivery_status, tags FROM profit_orders WHERE month=? AND (awb='' OR awb IS NULL)",
                (month,)
            ).fetchall()
            for row in no_awb_rows:
                category = _map_status("", row["fulfillment_status"], row["payment_status"], "", status_mapping,
                                       shopify_delivery_status=row["shopify_delivery_status"] or "",
                                       unmapped_collector=unmapped, tags=row["tags"] or "")
                conn.execute("UPDATE profit_orders SET status_category=? WHERE id=?", (category, row["id"]))

        with _db() as conn:
            final_cnt = conn.execute("SELECT COUNT(*) FROM profit_orders WHERE month=?", (month,)).fetchone()[0]

        unmapped_count = sum(unmapped.values())
        yield f"data: {json.dumps({'type': 'done', 'count': final_cnt, 'cached': False, 'unmapped_count': unmapped_count, 'unmapped_statuses': dict(sorted(unmapped.items(), key=lambda x: -x[1]))})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════
# Report Endpoint
# ═══════════════════════════════════════════════════════════════

def _window_day_fraction(month_list, win_from, win_to):
    """Fracția de zile ale lunilor selectate acoperite de fereastra inclusivă [win_from, win_to].
    Folosită DOAR la pro-ratarea unui override de marketing LUNAR pe o fereastră parțială când brandul
    n-are semnal zilnic de spend. 1.0 când nu există fereastră."""
    import calendar
    from datetime import date as _d2
    if not (win_from or win_to):
        return 1.0
    def _d(s):
        y, m, d = (s[:10].split("-") + ["1", "1"])[:3]
        return _d2(int(y), int(m), int(d))
    lo = _d(win_from) if win_from else None
    hi = _d(win_to) if win_to else None
    total = covered = 0
    for ml in month_list:
        parts = (ml.split("-") + ["1"])[:2]
        y, m = int(parts[0]), int(parts[1])
        ndays = calendar.monthrange(y, m)[1]
        mstart, mend = _d2(y, m, 1), _d2(y, m, ndays)
        total += ndays
        s = max(mstart, lo) if lo else mstart
        e = min(mend, hi) if hi else mend
        if e >= s:
            covered += (e - s).days + 1
    return (covered / total) if total else 1.0


@router.get("/api/profitability/report")
@greu
def get_report(
    month: str = Query(...),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Generate deliverability + profitability report for one or multiple months (comma-separated)."""
    settings = _get_settings()
    country_map = settings.get("country_map", DEFAULT_COUNTRY_MAP)
    vat_rates = settings.get("vat_rates", DEFAULT_VAT_RATES)

    # Support comma-separated months: "2026-04" or "2026-04,2026-03"
    month_list = [m.strip() for m in month.split(",") if m.strip()]
    if not month_list:
        return {"error": "No month specified", "month": month, "deliverability": [], "profitability": []}

    with _db() as conn:
        placeholders = ",".join("?" for _ in month_list)
        query = f"SELECT * FROM profit_orders WHERE month IN ({placeholders})"
        params = list(month_list)
        if from_date:
            query += " AND created_at >= ?"
            params.append(from_date)
        if to_date:
            query += " AND created_at <= ?"
            params.append(to_date + "T23:59:59")
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return {"error": "No data", "month": month, "deliverability": [], "profitability": []}

    orders = [dict(r) for r in rows]


    # Apply exclusion rules (includes tag-based rules like "test")
    with _db() as conn:
        excl_rows = conn.execute("SELECT rule_type, value FROM profit_exclusion_rules").fetchall()
    excl_tags = [r["value"].lower() for r in excl_rows if r["rule_type"] == "tag"]
    excl_skus = [r["value"].lower() for r in excl_rows if r["rule_type"] == "sku"]
    if excl_tags or excl_skus:
        def _is_excluded(o):
            tags = (o.get("tags") or "").lower()
            for t in excl_tags:
                if t in tags:
                    return True
            if excl_skus:
                skus_str = (o.get("skus") or o.get("cogs_missing_skus") or "").lower()
                for s in excl_skus:
                    if s in skus_str:
                        return True
            return False
        orders = [o for o in orders if not _is_excluded(o)]

    # Exchange rates (use first month for rates)
    # corpul ruleaza in threadpool (@greu): cursul se ia pe o bucla PROPRIE a firului. `anyio.from_thread.run`
    # l-ar fi rulat pe bucla principala (conectarea la metrics + SQLite = 1,35 s de scanari blocate, recenzia V90)
    rates = asyncio.run(_fetch_exchange_rates(month_list[0]))

    # Transport costs (merge across selected months, latest month wins per prefix)
    with _db() as conn:
        tc_placeholders = ",".join("?" for _ in month_list)
        tc_rows = conn.execute(
            f"SELECT prefix, cost_per_parcel, vat_included FROM profit_transport_costs WHERE month IN ({tc_placeholders}) ORDER BY month ASC",
            month_list
        ).fetchall()
    transport_costs = {r["prefix"]: {"cost": r["cost_per_parcel"], "vat_included": bool(r["vat_included"])}
                       for r in tc_rows}

    # Fereastra PARȚIALĂ de zile (din from_date/to_date). Marketingul e o mărime ZILNICĂ → pe fereastră
    # se sumează DOAR pe [win_from, win_to], NU pe toată luna (altfel profit fals pe ferestre parțiale —
    # ex. Nubra 1-15 iun avea spend pe toată luna). Fără fereastră → comportament legacy (lună întreagă).
    win_from = (from_date or "")[:10] or None
    win_to = (to_date or "")[:10] or None

    # Marketing: PREFERĂ cache.product_ad_spend (CANONIC, window-aware); fallback daily_perf. marketing_fullmonth
    # = lunile ÎNTREGI (numitor pentru pro-ratarea unui override LUNAR pe o fereastră parțială).
    marketing = {}; marketing_fullmonth = {}
    try:
        import sys as _sys, os as _os, re as _re
        import psycopg2 as _pg
        from datetime import date as _date, timedelta as _td
        if "/root/Scripturi" not in _sys.path:
            _sys.path.insert(0, "/root/Scripturi")
        import profit_core as _pc
        def _cl(d):
            d = _re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
            return _re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")
        _mc = _pg.connect(_cl(_os.environ["DATABASE_URL_METRICS"])); _cur = _mc.cursor()
        _cur.execute("SELECT id, name FROM brands"); _n2i = {n.strip().lower(): i for i, n in _cur.fetchall()}
        _b2p = {}
        for _pfx, _bid in _pc.prefix_brandid(_n2i).items():
            _b2p.setdefault(_bid, _pfx)
        def _spend_pas(lo, hi_excl):
            """SUM(spend_ron) per prefix din cache.daily_ad_spend_ron pe [lo, hi_excl).
            ⚠ SURSA per-brand = daily_ad_spend_ron (AUTORITATIV: Meta=Graph, Google, TikTok=warehouse-token),
            NU product_ad_spend (care e per-PRODUS: acumulează chei SKU stale + pull parțial → total per-brand
            greșit ±100k; ex CZ Meta 151k vs 94k real). Verificat la sursa API 2026-07. Vezi [[profit-data-sources-truth]]."""
            out = {}
            _cur.execute("SELECT brand_id, SUM(spend_ron) FROM cache.daily_ad_spend_ron "
                         "WHERE date >= %s AND date < %s GROUP BY brand_id",
                         (lo, hi_excl))
            for _bid, _sp in _cur.fetchall():
                _pfx = _b2p.get(_bid)
                if _pfx:
                    out[_pfx] = out.get(_pfx, 0) + float(_sp or 0)
            return out
        for ml in month_list:   # luni ÎNTREGI (numitor pt override)
            _y, _m = ml.split("-"); _m = int(_m)
            _nx = "%d-%02d-01" % (int(_y) + (1 if _m == 12 else 0), 1 if _m == 12 else _m + 1)
            for _pfx, _v in _spend_pas(ml + "-01", _nx).items():
                marketing_fullmonth[_pfx] = marketing_fullmonth.get(_pfx, 0) + _v
        if win_from or win_to:   # fereastră → spend DOAR pe [win_from, win_to] (hi exclusiv = ziua de după win_to)
            _lo = win_from or (month_list[0] + "-01")
            if win_to:
                _wy, _wm, _wd = (win_to.split("-") + ["1", "1"])[:3]
                _hi = (_date(int(_wy), int(_wm), int(_wd)) + _td(days=1)).isoformat()
            else:
                _ly, _lm = month_list[-1].split("-"); _lm = int(_lm)
                _hi = "%d-%02d-01" % (int(_ly) + (1 if _lm == 12 else 0), 1 if _lm == 12 else _lm + 1)
            marketing = _spend_pas(_lo, _hi)
        else:
            marketing = dict(marketing_fullmonth)
        _mc.close()
        log.info("marketing din cache.daily_ad_spend_ron (per-brand autoritativ): %d prefixe (fereastra=%s)", len(marketing), bool(win_from or win_to))
    except Exception as e:
        log.warning("cache.daily_ad_spend_ron marketing indisponibil (%s); fallback daily_perf", e)
        marketing = {}; marketing_fullmonth = {}
    if not marketing and not marketing_fullmonth:
        try:
            dp_db = DATA_DIR / "daily_perf.db"
            if dp_db.exists():
                conn2 = sqlite3.connect(str(dp_db)); conn2.row_factory = sqlite3.Row
                def _dp(conds, prms):
                    rows = conn2.execute(f"SELECT brand, SUM(total_spend) as m FROM daily_perf WHERE {' OR '.join(conds)} GROUP BY brand", prms).fetchall()
                    return {BRAND_TO_PREFIX.get(r["brand"], r["brand"]): round(r["m"], 2) for r in rows}
                try:
                    _fmc = []; _fmp = []
                    for ml in month_list:
                        y, m = ml.split("-"); _fmc.append("(date >= ? AND date <= ?)"); _fmp.extend([f"{y}-{m}-01", f"{y}-{m}-31"])
                    marketing_fullmonth = _dp(_fmc, _fmp)
                    if win_from or win_to:
                        lo = win_from or (month_list[0] + "-01"); hi = win_to or (month_list[-1] + "-31")
                        marketing = _dp(["(date >= ? AND date <= ?)"], [lo, hi])
                    else:
                        marketing = dict(marketing_fullmonth)
                finally:
                    conn2.close()
        except Exception as e:
            log.warning("Cannot read daily_perf for marketing: %s", e)

    # Group by prefix
    by_prefix = defaultdict(list)
    for o in orders:
        by_prefix[o["prefix"]].append(o)

    # Transport REAL per comandă din AWBprint = `orders.transport_cost` (costul AUTORITATIV la nivel de comandă,
    # exact cum e urcat în AWB Arona: UN AWB principal per comandă). E gross (TVA transport = RO 21% mereu,
    # curierul e RO) → /1.21 = ex-TVA. NU se sumează order_awbs (rândurile multiple sunt în mare DUPLICATE —
    # același cost pe fiecare rând; sumarea le multiplica). orders.transport_cost prinde corect și duplicatul
    # (= AWB principal) și split-ul real (= sumă). Fallback: MAX(transport_cost_fara_tva) = principalul deduplicat
    # (ex-TVA), unde orders.transport_cost lipsește. Final fallback: cost_per_parcel flat (în blocul de transport).
    real_transport_eng = {}
    try:
        import sys as _ts, os as _to, re as _tr
        import psycopg2 as _tpg
        if "/root/Scripturi" not in _ts.path:
            _ts.path.insert(0, "/root/Scripturi")
        import profit_core as _tpc
        def _tcl(d):
            d = _tr.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
            return _tr.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")
        _ac = _tpg.connect(_tcl(_to.environ["DATABASE_URL_AWBPRINT"])); _acur = _ac.cursor()
        _acur.execute("SELECT uid, name FROM stores")
        _dom2uid = {(n or "").strip().lower(): u for u, n in _acur.fetchall()}
        _PLECAT = ("Livrata", "In curs de livrare", "Refuzata")
        for _pfx, _ords in by_prefix.items():
            _uid = _dom2uid.get((_tpc.PREFIX_AWB_DOMAIN.get(_pfx) or "").lower())
            if not _uid:
                continue
            _names = [o["order_name"] for o in _ords if o.get("status_category") in _PLECAT]
            if not _names:
                continue
            # 8-sep-2026: SUMA tuturor liniilor de AWB ale comenzii (outbound + RETUR + colete
            # suplimentare), nu o singură valoare. `orders.transport_cost` ține UN cost/comandă și
            # pierde piciorul de retur internațional și coletele în plus. Însumarea e sigură de când
            # importul de CSV curier se face din Scripturi (api/courier_import.py): 0 perechi
            # (order_id, tracking_number) duplicate pe iun-aug 2026. Lipsa față de vechea regulă,
            # măsurată: iunie 8.261 / iulie 17.119 / august 9.799 RON ex-TVA (CZ/PL/BONBG/HU/SK = retur).
            # Fallback păstrat pe orders.transport_cost dacă nu există nicio linie costată.
            _acur.execute(
                "SELECT o.order_number, "
                "COALESCE(SUM(COALESCE(a.transport_cost_fara_tva, a.transport_cost / 1.21)), 0) AS awb_sum_exvat, "
                "MAX(o.transport_cost) AS otc_gross "
                "FROM orders o JOIN order_awbs a ON a.order_id = o.id "
                "WHERE o.store_uid = %s AND o.order_number = ANY(%s) "
                "GROUP BY o.order_number",
                (_uid, _names))
            for _on, _sum, _otc in _acur.fetchall():
                _t = float(_sum or 0)
                if _t <= 0 and _otc and _otc > 0:
                    _t = float(_otc) / 1.21
                if _t > 0:
                    real_transport_eng[(_pfx, _on)] = _t   # ex-TVA, toate AWB-urile comenzii
        _ac.close()
        log.info("transport REAL AWBprint: %d comenzi mapate", len(real_transport_eng))
    except Exception as e:
        log.warning("transport real AWBprint indisponibil (%s); fallback cost_per_parcel flat", e)
        real_transport_eng = {}

    # Collect unmapped statuses across all orders
    all_unmapped = defaultdict(int)
    status_mapping = _load_status_mapping()
    # Statuses handled by special-case logic in _map_status (not via mapping table)
    SPECIAL_CASE_STATUSES = {"awb invalid", "sameday expirat"}
    for o in orders:
        cs = (o.get("courier_status") or "").strip()
        if cs and cs.lower() not in SPECIAL_CASE_STATUSES:
            cs_lower = cs.lower()
            found = False
            if cs in status_mapping:
                found = True
            else:
                for k in status_mapping:
                    if k.lower().strip() == cs_lower:
                        found = True
                        break
            if not found:
                all_unmapped[cs] += 1

    # Build deliverability report
    store_names = _get_store_names()
    deliverability = []
    for prefix in sorted(by_prefix.keys()):
        prefix_orders = by_prefix[prefix]
        cats = defaultdict(int)
        for o in prefix_orders:
            cat = o.get("status_category") or "Necunoscut"
            cats[cat] += 1

        plecate = cats.get("Livrata", 0) + cats.get("In curs de livrare", 0) + cats.get("Refuzata", 0)

        deliverability.append({
            "prefix": prefix,
            "name": (store_names.get(prefix) or {}).get("name") or prefix,
            "domain": (store_names.get(prefix) or {}).get("domain") or "",
            "livrata": cats.get("Livrata", 0),
            "in_curs": cats.get("In curs de livrare", 0),
            "refuzata": cats.get("Refuzata", 0),
            "anulata": cats.get("Anulata", 0),
            "netrimisa": cats.get("Netrimisa", 0),
            "lipsa_awb": cats.get("Lipsa awb", 0),
            "plecate": plecate,
            "total": len(prefix_orders),
        })

    # Load COGS overrides to apply on-the-fly (so report reflects saved overrides
    # without requiring a full Shopify re-sync)
    cogs_overrides = {}
    with _db() as conn:
        for r in conn.execute("SELECT sku, unit_cost FROM profit_cogs_override").fetchall():
            cogs_overrides[r["sku"]] = r["unit_cost"]

    # Build profitability report
    profitability = []
    total_cogs_missing = 0
    missing_skus_all = []

    for prefix in sorted(by_prefix.keys()):
        prefix_orders = by_prefix[prefix]
        country = country_map.get(prefix, "RO")
        vat_rate = vat_rates.get(country, 0.21)

        # Revenue: only delivered orders, converted to RON
        incasari_ron = 0.0
        cogs_total = 0.0
        cogs_missing_count = 0

        for o in prefix_orders:
            cat = o.get("status_category", "")

            # Apply COGS overrides on-the-fly for orders with missing COGS
            if o.get("cogs_missing", 0) > 0 and cogs_overrides and o.get("cogs_missing_skus"):
                resolved_cogs = 0.0
                still_missing = []
                still_missing_qty = 0
                for part in o["cogs_missing_skus"].split("; "):
                    part = part.strip()
                    if not part:
                        continue
                    if "x" in part and part.rsplit("x", 1)[-1].isdigit():
                        sku, qty_s = part.rsplit("x", 1)
                        qty = int(qty_s)
                    else:
                        sku, qty = part, 1
                    if sku in cogs_overrides:
                        resolved_cogs += cogs_overrides[sku] * qty
                    else:
                        still_missing.append(part)
                        still_missing_qty += qty
                if resolved_cogs > 0:
                    o["cogs"] = (o.get("cogs", 0) or 0) + resolved_cogs
                    o["cogs_missing"] = still_missing_qty
                    o["cogs_missing_skus"] = "; ".join(still_missing)

            if cat == "Livrata":
                # Convert revenue to RON
                currency = o.get("currency", "RON")
                rev = o.get("revenue", 0)
                rate = rates.get(currency, 1.0)
                incasari_ron += rev * rate

                # COGS = unit_cost Shopify care e INTRODUS ÎN RON pe TOATE magazinele (landed cost, cu TVA
                # RO 21%), chiar dacă Shopify îl etichetează cu moneda magazinului (CZK/PLN/EUR). Deci NU se
                # convertește cu cursul — se adună direct ca RON. (Bug 2026-07: un "fix" anterior îl înmulțea
                # cu `rate` → CZ/PL/BG ieșeau ~5× mai mici, ex. CZ iun 2.677 vs real 15.581. Costul e gross;
                # ex-TVA se scoate downstream la `cogs_fara_tva = cogs_total/(1+vat_rate)`.)
                cogs_val = o.get("cogs", 0)
                cogs_total += cogs_val

            # Track missing COGS
            if o.get("cogs_missing", 0) > 0:
                cogs_missing_count += o["cogs_missing"]
                if o.get("cogs_missing_skus"):
                    missing_skus_all.append({"prefix": prefix, "order": o["order_name"],
                                             "skus": o["cogs_missing_skus"]})

        total_cogs_missing += cogs_missing_count

        # Transport: costul REAL per comandă din AWBprint (suma AWB-urilor = ce plătim, incl. partidă/colete/
        # retur intl), fallback cost_per_parcel flat unde lipsește în AWBprint. Orice colet PLECAT (Livrata/
        # În curs/Refuzat). cost_per_parcel intl e deja mai mare (CZ 22.5/PL 25 vs RO 13); RO n-are cost retur.
        # TVA: `transport_cost_fara_tva` e DEJA ex-TVA → îl iau DIRECT; flat cost_per_parcel e GROSS (TVA
        # transport = RO 21% mereu, curierul e RO) → /1.21. Reconstruiesc gross = ex-TVA*(1+vat_rate) ca linia
        # downstream `transport_fara_tva = transport_total/(1+vat_rate)` să recupereze EXACT ex-TVA (fără dublă scoatere).
        tc = transport_costs.get(prefix, {"cost": 13, "vat_included": False})
        plecate = 0; _t_exvat = 0.0
        for o in prefix_orders:
            if o.get("status_category") not in ("Livrata", "In curs de livrare", "Refuzata"):
                continue
            plecate += 1
            _rt = real_transport_eng.get((prefix, o.get("order_name")))
            _t_exvat += _rt if (_rt and _rt > 0) else (tc["cost"] / 1.21)
        transport_total = _t_exvat * (1 + vat_rate)

        # Marketing: override takes priority, else daily_perf
        with _db() as conn:
            # 8-sep-2026: caută pe month_list, nu pe șirul BRUT `month`. Pe o selecție de două luni
            # cheia devenea "2026-07,2026-08", nu se potrivea nimic și TOATE magazinele cădeau tacit
            # pe warehouse (ex. BONBG rămânea cu marketing 0).
            _mk_marks = ",".join("?" for _ in month_list)
            mkt_row = conn.execute(
                f"SELECT SUM(amount) AS amount FROM profit_marketing_override WHERE month IN ({_mk_marks}) AND prefix=?",
                (*month_list, prefix)
            ).fetchone()
        if mkt_row is not None and mkt_row["amount"] is not None:
            marketing_total = mkt_row["amount"]
            # Override-ul e o cifră LUNARĂ → pe fereastră parțială se pro-ratează cu ponderea spend-ului
            # din fereastră (cel mai bun semnal), altfel cu fracția de zile.
            if win_from or win_to:
                full = marketing_fullmonth.get(prefix, 0)
                if full > 0:
                    marketing_total *= marketing.get(prefix, 0) / full
                else:
                    marketing_total *= _window_day_fraction(month_list, win_from, win_to)
        else:
            marketing_total = marketing.get(prefix, 0)

        # TVA calculations
        incasari_fara_tva = round(incasari_ron / (1 + vat_rate), 2)
        cogs_fara_tva = round(cogs_total / (1 + vat_rate), 2)
        # Transportul se introduce CU TVA → scădem TVA mereu, exact ca la încasări și COGS
        # (retroactiv pe toate lunile; flag-ul vat_included rămâne doar informativ)
        transport_fara_tva = round(transport_total / (1 + vat_rate), 2)

        profitability.append({
            "prefix": prefix,
            "name": (store_names.get(prefix) or {}).get("name") or prefix,
            "domain": (store_names.get(prefix) or {}).get("domain") or "",
            "country": country,
            "vat_rate": vat_rate,
            "incasari_ron": round(incasari_ron, 2),
            "cogs": round(cogs_total, 2),
            "transport": round(transport_total, 2),
            "marketing": round(marketing_total, 2),
            "incasari_fara_tva": incasari_fara_tva,
            "cogs_fara_tva": cogs_fara_tva,
            "transport_fara_tva": transport_fara_tva,
            "marketing_fara_tva": round(marketing_total, 2),  # marketing is usually net
            "plecate": plecate,
            "cost_transport": tc["cost"],
            "transport_vat_included": tc["vat_included"],
            "cogs_missing_count": cogs_missing_count,
        })

    return {
        "month": month,
        "total_orders": len(orders),
        "deliverability": deliverability,
        "profitability": profitability,
        "exchange_rates": rates,
        "cogs_missing_total": total_cogs_missing,
        "cogs_missing_details": missing_skus_all[:100],
        "unmapped_statuses": dict(sorted(all_unmapped.items(), key=lambda x: -x[1])),
        "unmapped_count": sum(all_unmapped.values()),
    }


# ═══════════════════════════════════════════════════════════════
# Sync SKU Titles & Images from Shopify
# ═══════════════════════════════════════════════════════════════

SKU_LOOKUP_GQL = """
query($query: String!, $cursor: String) {
  productVariants(first: 50, query: $query, after: $cursor) {
    edges {
      node {
        sku
        product { title featuredImage { url } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


@router.post("/api/profitability/sync-sku-titles")
async def sync_sku_titles():
    """Populate profit_sku_titles by searching Shopify for all unique SKUs in orders."""
    # Get all unique SKUs from orders
    with _db() as conn:
        rows = conn.execute("SELECT DISTINCT skus FROM profit_orders WHERE skus != '' AND skus IS NOT NULL").fetchall()
    all_skus = set()
    for row in rows:
        for s in (row["skus"] or "").split(";"):
            s = s.strip()
            if s and s != "(fără SKU)":
                all_skus.add(s)

    if not all_skus:
        return {"ok": True, "found": 0, "message": "Nu sunt SKU-uri în baza de date"}

    # Check which SKUs already have title+image
    with _db() as conn:
        existing = conn.execute("SELECT sku FROM profit_sku_titles WHERE title != '' OR image_url != ''").fetchall()
    existing_skus = {r["sku"] for r in existing}
    missing_skus = all_skus - existing_skus

    if not missing_skus:
        return {"ok": True, "found": 0, "already": len(existing_skus), "message": "Toate SKU-urile au deja titlu/poză"}

    stores = list_stores()
    if not stores:
        return {"error": "Nu sunt magazine configurate (stores.csv)"}

    found_map = {}  # sku -> {title, image_url}

    async with httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=10)) as client:
        for store_info in stores:
            shop = store_info["shop"]
            token = store_info["token"]
            if not shop or not token:
                continue

            # Search for missing SKUs in this store
            still_missing = [s for s in missing_skus if s not in found_map]
            if not still_missing:
                break

            # Batch search: query multiple SKUs at once using OR
            batch_size = 20
            for i in range(0, len(still_missing), batch_size):
                batch = still_missing[i:i + batch_size]
                # Build Shopify search query: sku:X OR sku:Y OR ...
                query_parts = [f"sku:{sku}" for sku in batch]
                search_query = " OR ".join(query_parts)

                url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
                headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

                cursor = None
                pages = 0
                while pages < 5:  # safety limit
                    pages += 1
                    payload = {"query": SKU_LOOKUP_GQL, "variables": {"query": search_query, "cursor": cursor}}

                    for attempt in range(3):
                        try:
                            r = await client.post(url, json=payload, headers=headers)
                            if r.status_code == 429:
                                await asyncio.sleep(2.0)
                                continue
                            break
                        except Exception:
                            await asyncio.sleep(1.0)
                    else:
                        break

                    if r.status_code != 200:
                        break

                    try:
                        j = r.json()
                    except Exception:
                        break

                    # Throttle
                    try:
                        ts = ((j.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
                        cur = int(ts.get("currentlyAvailable", 999))
                        rr = float(ts.get("restoreRate", 50))
                        if cur < 50 and rr > 0:
                            await asyncio.sleep(min((50 - cur) / rr, 3.0))
                    except Exception:
                        pass

                    data = (j.get("data") or {}).get("productVariants") or {}
                    edges = data.get("edges") or []
                    for edge in edges:
                        node = edge.get("node") or {}
                        sku = (node.get("sku") or "").strip()
                        if not sku:
                            continue
                        prod = node.get("product") or {}
                        title = (prod.get("title") or "").strip()
                        img = ((prod.get("featuredImage") or {}).get("url") or "").strip()
                        if sku not in found_map and (title or img):
                            found_map[sku] = {"title": title, "image_url": img}

                    page_info = data.get("pageInfo") or {}
                    if not page_info.get("hasNextPage"):
                        break
                    cursor = page_info.get("endCursor")

    # Save to DB
    if found_map:
        with _db() as conn:
            for sku, info in found_map.items():
                conn.execute(
                    "INSERT OR REPLACE INTO profit_sku_titles (sku, title, image_url) VALUES (?, ?, ?)",
                    (sku, info["title"], info["image_url"])
                )

    return {
        "ok": True,
        "found": len(found_map),
        "total_skus": len(all_skus),
        "already_had": len(existing_skus),
        "still_missing": len(missing_skus) - len(found_map),
    }


# ═══════════════════════════════════════════════════════════════
# Product Stats (per-SKU delivery rates)
# ═══════════════════════════════════════════════════════════════

def _norm_sku(sku: str) -> str:
    """Cheia de potrivire a unui SKU: trim + colapsare spatii + lower.

    Identica cu `_norm_sku` din api/product_analytics.py — DELIBERAT: raportul per-produs
    si ratele de livrare trebuie sa se potriveasca pe ACEEASI cheie. Pe string brut,
    `profit_orders` (majuscule, din engine) nu se potrivea cu `analytics_sales` (minuscule,
    din cronul de livrari): 617 SKU-uri / 20.168 comenzi (11,6% din iulie 2026) ramaneau
    fara rata de livrare, iar UI-ul le afisa 100% — un scor perfect fabricat.
    """
    return " ".join((sku or "").strip().lower().split())


@router.get("/api/profitability/product-stats")
@greu
def get_product_stats(
    month: str = Query(...),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Aggregate delivery/refusal rates per SKU for selected months."""
    month_list = [m.strip() for m in month.split(",") if m.strip()]
    if not month_list:
        return {"products": []}

    with _db() as conn:
        placeholders = ",".join("?" for _ in month_list)
        query = f"SELECT month, prefix, order_name, skus, status_category, created_at FROM profit_orders WHERE month IN ({placeholders})"
        params = list(month_list)

        if from_date:
            query += " AND created_at >= ?"
            params.append(from_date + "T00:00:00")
        if to_date:
            query += " AND created_at < ?"
            params.append(to_date + "T23:59:59")

        rows = conn.execute(query, params).fetchall()

        # `profit_orders.skus` se construieste din varianta Shopify si e GRESIT pentru pachetele
        # unicat: toata familia `surpriza` primea acelasi SKU inventat (`surpriza-EST 143`), care
        # NU exista in Shopify. In iulie 2026: 3.066 comenzi gresite din 3.083. Consecinta — toate
        # produsele surpriza ramaneau fara rata de livrare, iar unul singur le inghitea pe toate.
        #
        # `profit_order_lines` are SKU-urile reale: verificate la unitate fata de analytics_sales
        # (sursa Shopify) pe toata luna iulie — 1.344 vs 1.342, 379 vs 379, 311 vs 311. Acoperire
        # 99,9% din comenzi; unde lipsesc linii se cade pe vechiul camp, ca sa nu dispara nimic.
        linii: Dict[tuple, list] = {}
        for lr in conn.execute(
                f"SELECT month, prefix, order_name, sku FROM profit_order_lines WHERE month IN ({placeholders})",
                list(month_list)):
            if lr["sku"]:
                linii.setdefault((lr["month"], lr["prefix"], lr["order_name"]), []).append(lr["sku"])

    # Aggregate by SKU (global + per-store)
    sku_stats: Dict[str, dict] = {}
    for row in rows:
        cat = row["status_category"] or "Necunoscut"
        prefix = row["prefix"] or ""

        sku_list = linii.get((row["month"], prefix, row["order_name"]))
        if not sku_list:
            sku_list = [s.strip() for s in (row["skus"] or "").split(";") if s.strip()]
        if not sku_list:
            sku_list = ["(fără SKU)"]

        for sku in sku_list:
            key = _norm_sku(sku)
            if key not in sku_stats:
                sku_stats[key] = {
                    "sku": sku, "key": key, "stores": set(),
                    "total": 0, "livrata": 0, "refuzata": 0,
                    "in_curs": 0, "anulata": 0, "netrimisa": 0, "lipsa_awb": 0,
                    "per_store": {},
                }
            s = sku_stats[key]
            s["stores"].add(prefix)
            s["total"] += 1

            # Per-store breakdown
            if prefix not in s["per_store"]:
                s["per_store"][prefix] = {
                    "total": 0, "livrata": 0, "refuzata": 0,
                    "in_curs": 0, "anulata": 0, "netrimisa": 0, "lipsa_awb": 0,
                }
            ps = s["per_store"][prefix]
            ps["total"] += 1

            if cat == "Livrata":
                s["livrata"] += 1
                ps["livrata"] += 1
            elif cat == "Refuzata":
                s["refuzata"] += 1
                ps["refuzata"] += 1
            elif cat == "In curs de livrare":
                s["in_curs"] += 1
                ps["in_curs"] += 1
            elif cat == "Anulata":
                s["anulata"] += 1
                ps["anulata"] += 1
            elif cat == "Netrimisa":
                s["netrimisa"] += 1
                ps["netrimisa"] += 1
            elif cat == "Lipsa awb":
                s["lipsa_awb"] += 1
                ps["lipsa_awb"] += 1

    # Calculate rates and build result
    products = []
    for s in sku_stats.values():
        t = s["total"]
        plecate = s["livrata"] + s["refuzata"] + s["in_curs"]
        s["plecate"] = plecate
        s["rata_livrare"] = round(s["livrata"] / plecate * 100, 1) if plecate > 0 else 0
        s["rata_refuz"] = round(s["refuzata"] / plecate * 100, 1) if plecate > 0 else 0
        s["rata_in_curs"] = round(s["in_curs"] / plecate * 100, 1) if plecate > 0 else 0
        s["stores"] = ", ".join(sorted(s["stores"]))

        # Calculate per-store rates
        for pfx, ps in s["per_store"].items():
            ps_plecate = ps["livrata"] + ps["refuzata"] + ps["in_curs"]
            ps["plecate"] = ps_plecate
            ps["rata_livrare"] = round(ps["livrata"] / ps_plecate * 100, 1) if ps_plecate > 0 else 0
            ps["rata_refuz"] = round(ps["refuzata"] / ps_plecate * 100, 1) if ps_plecate > 0 else 0

        products.append(s)

    # Lookup product titles
    all_skus = [p["sku"] for p in products if p["sku"] and p["sku"] != "(fără SKU)"]
    title_map = {}
    image_map = {}
    if all_skus:
        with _db() as conn:
            placeholders = ",".join("?" for _ in all_skus)
            rows = conn.execute(
                f"SELECT sku, title, image_url FROM profit_sku_titles WHERE sku IN ({placeholders})",
                all_skus
            ).fetchall()
            title_map = {_norm_sku(r["sku"]): r["title"] for r in rows}
            image_map = {_norm_sku(r["sku"]): r["image_url"] for r in rows}
    for p in products:
        p["title"] = title_map.get(p["key"], "")
        p["image_url"] = image_map.get(p["key"], "")

    # Sort by total descending
    products.sort(key=lambda x: -x["total"])

    return {"products": products, "total_skus": len(products)}


# ═══════════════════════════════════════════════════════════════
# CRUD Endpoints
# ═══════════════════════════════════════════════════════════════

@router.post("/api/profitability/refresh-store-names")
async def refresh_store_names_endpoint():
    """Reîmprospătează denumirile reale ale magazinelor din Shopify (rulează și singur la fiecare run)."""
    stores = list_stores()
    await _refresh_store_names(stores)
    names = _get_store_names()
    return {"ok": True, "stores": len(stores), "cu_nume": len(names), "names": names}


@router.get("/api/profitability/months")
@greu
def get_months():
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT month FROM profit_orders ORDER BY month DESC"
        ).fetchall()
    return [r["month"] for r in rows]


@router.get("/api/profitability/transport-costs")
async def get_transport_costs(month: str = Query(...)):
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM profit_transport_costs WHERE month=?", (month,)
        ).fetchall()

        # Auto-copy from previous month if empty
        if not rows:
            # compute previous month
            try:
                y, m = month.split("-")
                y, m = int(y), int(m)
                pm = m - 1
                py = y
                if pm < 1:
                    pm = 12
                    py -= 1
                prev_month = f"{py}-{pm:02d}"
                prev_rows = conn.execute(
                    "SELECT prefix, cost_per_parcel, vat_included FROM profit_transport_costs WHERE month=?",
                    (prev_month,)
                ).fetchall()
                if prev_rows:
                    for pr in prev_rows:
                        conn.execute("""
                            INSERT OR IGNORE INTO profit_transport_costs (month, prefix, cost_per_parcel, vat_included)
                            VALUES (?, ?, ?, ?)
                        """, (month, pr["prefix"], pr["cost_per_parcel"], pr["vat_included"]))
                    rows = conn.execute(
                        "SELECT * FROM profit_transport_costs WHERE month=?", (month,)
                    ).fetchall()
            except Exception:
                pass

    return [dict(r) for r in rows]


@router.post("/api/profitability/transport-costs")
async def save_transport_costs(data: TransportCostsBulk):
    with _db() as conn:
        for item in data.items:
            conn.execute("""
                INSERT OR REPLACE INTO profit_transport_costs (month, prefix, cost_per_parcel, vat_included)
                VALUES (?, ?, ?, ?)
            """, (item.month, item.prefix, item.cost_per_parcel, int(item.vat_included)))
    return {"ok": True, "count": len(data.items)}


@router.get("/api/profitability/marketing-overrides")
async def get_marketing_overrides(month: str = Query(...)):
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM profit_marketing_override WHERE month=?", (month,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/profitability/marketing-overrides")
async def save_marketing_overrides(data: MarketingOverrideBulk):
    with _db() as conn:
        for item in data.items:
            conn.execute("""
                INSERT OR REPLACE INTO profit_marketing_override (month, prefix, amount)
                VALUES (?, ?, ?)
            """, (item.month, item.prefix, item.amount))
    return {"ok": True, "count": len(data.items)}

@router.get("/api/profitability/commission-overrides")
@greu
def get_commission_overrides(month: str = Query(...)):
    """Sumele de comision scrise MANUAL, pentru lunile cerute (una sau mai multe, separate prin virgulă).

    Întoarce {prefix: sumă}. Pe interval de mai multe luni sumele se ADUNĂ per magazin, ca raportul
    să rămână coerent cu restul coloanelor (care sunt tot cumulate pe fereastra selectată)."""
    months = [m.strip() for m in month.split(",") if m.strip()]
    if not months:
        return {}
    with _db() as conn:
        ph = ",".join("?" for _ in months)
        rows = conn.execute(
            f"SELECT prefix, SUM(amount) AS amount FROM profit_commission_override "
            f"WHERE month IN ({ph}) GROUP BY prefix", months
        ).fetchall()
    return {r["prefix"]: r["amount"] for r in rows}


@router.post("/api/profitability/commission-overrides")
async def save_commission_overrides(data: CommissionOverrideBulk):
    """Salvează/șterge sume manuale de comision. `amount: null` ȘTERGE override-ul (revine la calcul)."""
    saved = deleted = 0
    with _db() as conn:
        for item in data.items:
            if item.amount is None:
                cur = conn.execute(
                    "DELETE FROM profit_commission_override WHERE month=? AND prefix=?",
                    (item.month, item.prefix))
                deleted += cur.rowcount
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO profit_commission_override (month, prefix, amount) "
                    "VALUES (?, ?, ?)", (item.month, item.prefix, item.amount))
                saved += 1
    return {"ok": True, "saved": saved, "deleted": deleted}


@router.get("/api/profitability/commission-rates")
@greu
def get_commission_rates(month: str = Query(...)):
    """Ratele de comision per magazin.

    `effective` = rata în vigoare pentru ULTIMA lună din selecţie (regula cu cel mai mare
    `from_month <= lună`). `rules` = toate regulile, pentru administrare. Magazinele care nu apar
    merg pe rata globală din UI."""
    months = sorted(m.strip() for m in month.split(",") if m.strip())
    ref = months[-1] if months else ""
    with _db() as conn:
        rules = [dict(r) for r in conn.execute(
            "SELECT prefix, from_month, rate FROM profit_commission_rate "
            "ORDER BY prefix, from_month").fetchall()]
    effective = {}
    for r in rules:
        if ref and r["from_month"] <= ref:
            effective[r["prefix"]] = r["rate"]   # ordonat crescător → ultima potrivire câştigă
    return {"effective": effective, "rules": rules}


@router.post("/api/profitability/commission-rates")
async def save_commission_rates(data: CommissionRateBulk):
    """Setează/şterge reguli de rată. `rate: null` ȘTERGE regula pentru (prefix, from_month)."""
    saved = deleted = 0
    with _db() as conn:
        for it in data.items:
            if it.rate is None:
                cur = conn.execute(
                    "DELETE FROM profit_commission_rate WHERE prefix=? AND from_month=?",
                    (it.prefix, it.from_month))
                deleted += cur.rowcount
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO profit_commission_rate (prefix, from_month, rate) "
                    "VALUES (?, ?, ?)", (it.prefix, it.from_month, it.rate))
                saved += 1
    return {"ok": True, "saved": saved, "deleted": deleted}


@router.get("/api/profitability/marketing-sync")
async def sync_marketing_from_daily_perf(
    month: str = Query(...),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    persist: int = Query(0),
):
    """Marketing din daily_perf.db pentru luna/lunile/fereastra cerută, grupat pe brand.

    8-sep-2026, trei corecții:
      * acceptă mai multe luni și o fereastră de zile -> UI-ul nu mai suprascrie marketingul
        pro-ratat de get_report cu cel pe LUNA ÎNTREAGĂ (măsurat: Gento 1-15 iun apărea -838 RON
        în loc de +7.766);
      * SCRIE în profit_marketing_override doar cu persist=1 (butonul manual, o singură lună
        întreagă). Înainte, un simplu GET la randarea paginii rescria override-urile lunii cu
        valorile din sheet, peste cele din warehouse;
      * brandurile care nu sunt în BRAND_TO_PREFIX se SAR (înainte se scria numele ca prefix).
    """
    # Chemata si direct din Python (run_profitability), unde parametrii Query() sosesc ca OBIECTE,
    # nu ca None -> normalizeaza inainte de orice folosire (altfel: "type 'Query' is not supported").
    if not isinstance(from_date, str):
        from_date = None
    if not isinstance(to_date, str):
        to_date = None
    if not isinstance(persist, int):
        persist = 0
    _win = bool(from_date or to_date)
    marketing = {}
    breakdown = {}
    try:
        dp_db = DATA_DIR / "daily_perf.db"
        if not dp_db.exists():
            return {"error": "daily_perf.db not found", "marketing": {}, "breakdown": {}}

        _months = [x.strip() for x in month.split(",") if x.strip()] or [month]
        y, m = _months[0].split("-")
        y2, m2 = _months[-1].split("-")
        from_date = from_date or f"{y}-{m}-01"
        to_date = to_date or f"{y2}-{m2}-31"

        conn2 = sqlite3.connect(str(dp_db))
        conn2.row_factory = sqlite3.Row
        try:
            mk_rows = conn2.execute("""
                SELECT brand,
                       SUM(total_spend) as total_marketing,
                       SUM(fb_spend) as fb,
                       SUM(tk_spend) as tk,
                       SUM(google_spend) as google
                FROM daily_perf
                WHERE date >= ? AND date <= ?
                GROUP BY brand
            """, (from_date, to_date)).fetchall()
            for r in mk_rows:
                prefix = BRAND_TO_PREFIX.get(r["brand"])
                if not prefix:
                    log.warning("marketing-sync: brand nemapat in BRAND_TO_PREFIX, il sar: %r", r["brand"])
                    continue
                marketing[prefix] = round(r["total_marketing"], 2)
                breakdown[prefix] = {
                    "fb": round(r["fb"] or 0, 2),
                    "tk": round(r["tk"] or 0, 2),
                    "google": round(r["google"] or 0, 2),
                    "total": round(r["total_marketing"], 2),
                }
        finally:
            conn2.close()

        if marketing and persist and len(_months) == 1 and not _win:
            with _db() as conn:
                for pfx, val in marketing.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO profit_marketing_override (month, prefix, amount) VALUES (?, ?, ?)",
                        (_months[0], pfx, val)
                    )

    except Exception as e:
        log.warning("Cannot read daily_perf for marketing sync: %s", e)
        return {"error": str(e), "marketing": {}, "breakdown": {}}

    return {"marketing": marketing, "breakdown": breakdown}


# ═══════════════════════════════════════════════════════════════
# Exclusion Rules
# ═══════════════════════════════════════════════════════════════

class ExclusionRule(BaseModel):
    rule_type: str  # "tag" or "sku"
    value: str
    reason: str = ""

@router.get("/api/profitability/exclusion-rules")
async def list_exclusion_rules():
    with _db() as conn:
        rows = conn.execute("SELECT id, rule_type, value, reason, created_at FROM profit_exclusion_rules ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]

@router.post("/api/profitability/exclusion-rules")
async def add_exclusion_rule(rule: ExclusionRule):
    if rule.rule_type not in ("tag", "sku"):
        return {"error": "rule_type must be 'tag' or 'sku'"}
    if not rule.value.strip():
        return {"error": "value cannot be empty"}
    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO profit_exclusion_rules (rule_type, value, reason) VALUES (?,?,?)",
                (rule.rule_type, rule.value.strip(), rule.reason.strip())
            )
        except Exception:
            return {"error": "Rule already exists"}
    return {"ok": True}

@router.delete("/api/profitability/exclusion-rules/{rule_id}")
async def delete_exclusion_rule(rule_id: int):
    with _db() as conn:
        conn.execute("DELETE FROM profit_exclusion_rules WHERE id=?", (rule_id,))
    return {"ok": True}

@router.get("/api/profitability/excluded-orders")
async def list_excluded_orders(month: str = Query(...)):
    """Show which orders would be excluded by the current rules."""
    with _db() as conn:
        excl_rows = conn.execute("SELECT rule_type, value FROM profit_exclusion_rules").fetchall()
        orders = conn.execute(
            "SELECT order_name, prefix, tags, skus, cogs_missing_skus, revenue, currency FROM profit_orders WHERE month=?",
            (month,)
        ).fetchall()

    excl_tags = [r["value"].lower() for r in excl_rows if r["rule_type"] == "tag"]
    excl_skus = [r["value"].lower() for r in excl_rows if r["rule_type"] == "sku"]
    excluded = []
    for o in orders:
        reasons = []
        tags = (o["tags"] or "").lower()
        for t in excl_tags:
            if t in tags:
                reasons.append(f"tag: {t}")
        skus_str = (o["skus"] or o["cogs_missing_skus"] or "").lower()
        for s in excl_skus:
            if s in skus_str:
                reasons.append(f"sku: {s}")
        if reasons:
            excluded.append({
                "order_name": o["order_name"],
                "prefix": o["prefix"],
                "revenue": o["revenue"],
                "currency": o["currency"],
                "reasons": reasons,
            })
    return {"excluded": excluded, "count": len(excluded)}


@router.get("/api/profitability/settings")
async def get_settings():
    return _get_settings()


@router.post("/api/profitability/settings")
async def save_settings(data: SettingsModel):
    _save_settings({
        "exclude_test": data.exclude_test,
        "country_map": data.country_map,
        "vat_rates": data.vat_rates,
    })
    return {"ok": True}


@router.get("/api/profitability/cogs-missing")
async def get_cogs_missing(month: str = Query(...)):
    with _db() as conn:
        rows = conn.execute("""
            SELECT prefix, order_name, cogs_missing_skus
            FROM profit_orders
            WHERE month=? AND cogs_missing > 0 AND cogs_missing_skus != ''
            ORDER BY prefix, order_name
        """, (month,)).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/profitability/cogs-override")
async def save_cogs_override(data: CogsOverride):
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profit_cogs_override (sku, unit_cost, currency) VALUES (?,?,?)",
            (data.sku, data.unit_cost, data.currency)
        )
    return {"ok": True, "sku": data.sku}


@router.get("/api/profitability/cogs-overrides")
async def get_cogs_overrides():
    with _db() as conn:
        rows = conn.execute("SELECT * FROM profit_cogs_override").fetchall()
    return [dict(r) for r in rows]


@router.get("/api/profitability/status-mapping")
async def get_status_mapping():
    return _load_status_mapping()


@router.post("/api/profitability/status-mapping")
async def save_status_mapping_endpoint(data: StatusMappingBulk):
    # MERGE new mappings into existing ones (don't replace all)
    existing = _load_status_mapping()
    for item in data.items:
        existing[item.courier_status] = item.category
    _save_status_mapping(existing)
    return {"ok": True, "count": len(data.items)}


_ORDER_COLUMNS = (
    "order_name, prefix, shop, created_at, revenue, currency, cogs, "
    "payment_status, fulfillment_status, awb, courier_key, courier_status, "
    "status_category, tags"
)


def _orders_where(
    month: str,
    prefix: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    courier_status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Tuple[str, list]:
    """WHERE shared by the orders list and the orders CSV export, so the export returns
    exactly the rows shown in the tab (multiple months + the same filters)."""
    month_list = [m.strip() for m in (month or "").split(",") if m.strip()]
    if not month_list:
        return "", []

    sql = " WHERE month IN ({})".format(",".join("?" for _ in month_list))
    params: list = list(month_list)

    if prefix:
        sql += " AND prefix=?"
        params.append(prefix)
    if status:
        sql += " AND status_category=?"
        params.append(status)
    if q:
        sql += " AND (order_name LIKE ? OR awb LIKE ? OR courier_status LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    if from_date:
        sql += " AND created_at >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND created_at <= ?"
        params.append(to_date + "T23:59:59")
    if courier_status:
        sql += " AND courier_status LIKE ?"
        params.append(f"%{courier_status}%")

    return sql, params


@router.get("/api/profitability/orders")
async def list_orders(
    month: str = Query(...),
    prefix: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    courier_status: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(500),
    offset: int = Query(0),
):
    """List orders for one or more months (comma-separated) with optional filters."""
    where, params = _orders_where(month, prefix, status, q, courier_status, from_date, to_date)
    if not where:
        return {"orders": [], "total": 0}

    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM profit_orders{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, {_ORDER_COLUMNS} FROM profit_orders{where} ORDER BY prefix, order_name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "orders": [
            {
                "id": r[0], "order_name": r[1], "prefix": r[2], "shop": r[3],
                "created_at": r[4], "revenue": r[5], "currency": r[6], "cogs": r[7],
                "payment_status": r[8], "fulfillment_status": r[9], "awb": r[10],
                "courier_key": r[11], "courier_status": r[12], "status_category": r[13],
                "tags": r[14],
            }
            for r in rows
        ],
    }


class OrderStatusUpdate(BaseModel):
    ids: List[int]
    status_category: str


@router.post("/api/profitability/orders/update-status")
async def update_order_status(data: OrderStatusUpdate):
    """Update status category for specific orders."""
    with _db() as conn:
        conn.execute(
            f"UPDATE profit_orders SET status_category=? WHERE id IN ({','.join('?' * len(data.ids))})",
            [data.status_category] + data.ids,
        )
        conn.commit()
    return {"ok": True, "updated": len(data.ids)}


@router.get("/api/profitability/export")
async def export_orders_csv(
    month: str = Query(...),
    prefix: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    courier_status: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Export orders as CSV — same months (comma-separated) and filters as the Comenzi tab."""
    import io

    where, params = _orders_where(month, prefix, status, q, courier_status, from_date, to_date)
    if not where:
        raise HTTPException(status_code=400, detail="Nicio luna selectata")

    with _db() as conn:
        rows = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM profit_orders{where} ORDER BY prefix, order_name",
            params,
        ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Nicio comanda pentru {month} cu filtrele curente")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ORDER_NAME", "PREFIX", "SHOP", "CREATED_AT", "REVENUE", "CURRENCY",
        "COGS", "PAYMENT_STATUS", "FULFILLMENT_STATUS", "AWB", "COURIER_KEY",
        "COURIER_STATUS", "STATUS_CATEGORY", "TAGS"
    ])
    for r in rows:
        writer.writerow(list(r))

    filename = f"comenzi_{month.replace(',', '_')}.csv"

    from starlette.responses import Response
    return Response(
        # BOM: fara el Excel strica diacriticele din adrese/statusuri
        content=("\ufeff" + output.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/api/profitability/remap")
async def remap_statuses(month: str = Query(...)):
    """
    Re-apply status mapping to all orders for a month without re-tracking.
    Useful after adding new status mappings.
    """
    mapping = _load_status_mapping()
    unmapped = {}

    with _db() as conn:
        rows = conn.execute(
            "SELECT id, courier_status, fulfillment_status, payment_status, awb, shopify_delivery_status, tags "
            "FROM profit_orders WHERE month=?", (month,)
        ).fetchall()

        for r in rows:
            cat = _map_status(
                r["courier_status"], r["fulfillment_status"],
                r["payment_status"], r["awb"], mapping,
                shopify_delivery_status=r["shopify_delivery_status"] or "",
                unmapped_collector=unmapped, tags=r["tags"] or ""
            )
            conn.execute(
                "UPDATE profit_orders SET status_category=? WHERE id=?",
                (cat, r["id"])
            )

    return {
        "ok": True,
        "remapped": len(rows),
        "unmapped_count": sum(unmapped.values()),
        "unmapped_statuses": dict(sorted(unmapped.items(), key=lambda x: -x[1])),
    }


@router.post("/api/profitability/refresh")
async def refresh_open_orders(month: str = Query(...)):
    """
    Re-track ONLY open orders (not Livrata/Refuzata/Anulata).
    Then re-map statuses. SSE streaming.
    """
    CLOSED_CATEGORIES = {"Livrata", "Refuzata", "Anulata"}

    async def _stream():
        mapping = _load_status_mapping()

        with _db() as conn:
            rows = conn.execute(
                "SELECT id, prefix, shop, order_name, awb, courier_key, "
                "fulfillment_status, payment_status, status_category "
                "FROM profit_orders WHERE month=?", (month,)
            ).fetchall()

        open_orders = [dict(r) for r in rows if r["status_category"] not in CLOSED_CATEGORIES]
        closed_count = len(rows) - len(open_orders)

        if not open_orders:
            yield f"data: {json.dumps({'type': 'done', 'message': 'Nu sunt comenzi deschise de verificat.', 'refreshed': 0})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'phase', 'phase': 1, 'message': f'Re-tracking {len(open_orders)} comenzi deschise ({closed_count} închise, skip)...', 'progress': 0, 'total': len(open_orders)})}\n\n"

        # Group AWBs by courier
        config = COURIER_CONFIG
        dpd_creds = config.get("dpd_creds") or {}
        sameday_creds = config.get("sameday_creds") or {}
        econt_creds = config.get("econt_creds") or {}
        packeta_creds = config.get("packeta_creds") or config.get("packeta") or {}

        dpd_awbs_by_acc = defaultdict(list)
        sameday_awbs = []
        econt_awbs = []
        packeta_awbs = []

        for o in open_orders:
            awb = (o.get("awb") or "").strip()
            if not awb:
                continue
            ck = o.get("courier_key", "unknown")
            if ck in DPD_ACCOUNTS:
                dpd_awbs_by_acc[ck].append(awb)
            elif ck == "sameday":
                sameday_awbs.append(awb)
            elif ck == "econt":
                econt_awbs.append(awb)
            elif ck == "packeta":
                packeta_awbs.append(awb)
            elif awb.startswith(("Z", "z")):
                packeta_awbs.append(awb)
            elif awb.startswith("8"):
                dpd_awbs_by_acc["dpd-ro"].append(awb)

        sameday_awbs = list(dict.fromkeys(sameday_awbs))
        econt_awbs = list(dict.fromkeys(econt_awbs))
        packeta_awbs = list(dict.fromkeys(packeta_awbs))

        total_awbs = sum(len(v) for v in dpd_awbs_by_acc.values()) + len(sameday_awbs) + len(econt_awbs) + len(packeta_awbs)
        tracked = 0

        async with httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_connections=40)) as client:
            # DPD (bulk first, then per-AWB fallback)
            dpd_limiter = SimpleRateLimiter(max_calls=1, per_seconds=0.25)
            for acc, awbs_list in dpd_awbs_by_acc.items():
                if not awbs_list:
                    continue
                creds = dpd_creds.get(acc) or {}
                if not creds:
                    tracked += len(awbs_list)
                    continue
                unique = list(dict.fromkeys(awbs_list))
                target_set = set(unique)

                dpd_limiter = SimpleRateLimiter(max_calls=1, per_seconds=0.25)

                # Phase 1: Bulk tracking (fast — downloads pre-generated files)
                bulk_res = {}
                try:
                    bulk_res = await _dpd_bulk_track(
                        client, acc, creds, target_set,
                        month=month, status_mapping=mapping,
                    )
                    if bulk_res:
                        _update_tracking_in_db(month, bulk_res, mapping)
                        tracked += len(bulk_res)
                        yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'DPD {acc} bulk: {len(bulk_res)} AWB'})}\\n\\n"
                except Exception as e:
                    log.warning(f"DPD bulk [{acc}] eroare: {e}")

                # Phase 2: Per-AWB fallback for any missing
                missing = [a for a in unique if a not in bulk_res]
                if missing:
                    res = await _dpd_track_account(client, acc, creds, missing, dpd_limiter, batch_size=30, transient_passes=2)
                    _update_tracking_in_db(month, res, mapping)
                    tracked += len(missing)
                    yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'DPD {acc} fallback: {len(missing)} AWB'})}\n\n"

            # Sameday
            if sameday_awbs:
                res = await _track_sameday_many(client, sameday_awbs, sameday_creds, rps=4, concurrency=8)
                _update_tracking_in_db(month, res, mapping)
                tracked += len(sameday_awbs)
                yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'Sameday: {len(sameday_awbs)} AWB'})}\n\n"

            # Econt
            if econt_awbs:
                res = await _track_econt_many(client, econt_awbs, econt_creds, rps=3)
                _update_tracking_in_db(month, res, mapping)
                tracked += len(econt_awbs)
                yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'Econt: {len(econt_awbs)} AWB'})}\n\n"

            # Packeta
            if packeta_awbs:
                res = await _track_packeta_many(client, packeta_awbs, packeta_creds, rps=60, concurrency=60)
                _update_tracking_in_db(month, res, mapping)
                tracked += len(packeta_awbs)
                yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'Packeta: {len(packeta_awbs)} AWB'})}\n\n"

        # DPD retry: AWBs still stuck with "too many requests" / "not accessible"
        with _db() as conn:
            stuck_rows = conn.execute(
                "SELECT DISTINCT awb FROM profit_orders WHERE month=? "
                "AND (courier_status LIKE '%oo many tracking%' OR courier_status LIKE '%not accessible%')",
                (month,)
            ).fetchall()
        if stuck_rows:
            stuck_awbs = [r["awb"] for r in stuck_rows]
            yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'Re-verificare {len(stuck_awbs)} AWB-uri blocate...'})}\n\n"
            async with httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_connections=40)) as client2:
                dpd_limiter2 = SimpleRateLimiter(max_calls=1, per_seconds=0.25)
                for alt_acc in DPD_ACCOUNTS:
                    if not stuck_awbs:
                        break
                    alt_creds = dpd_creds.get(alt_acc) or {}
                    if not alt_creds:
                        continue
                    res = await _dpd_track_account(client2, alt_acc, alt_creds, stuck_awbs, dpd_limiter2, batch_size=10, transient_passes=5)
                    resolved = {a: st for a, st in res.items() if not _is_dpd_too_many(st) and not _is_dpd_not_accessible(st)}
                    if resolved:
                        _update_tracking_in_db(month, resolved, mapping)
                        stuck_awbs = [a for a in stuck_awbs if a not in resolved]
                        yield f"data: {json.dumps({'type': 'progress', 'phase': 1, 'progress': tracked, 'total': total_awbs, 'message': f'DPD {alt_acc}: rezolvat {len(resolved)} AWB-uri'})}\n\n"

        yield f"data: {json.dumps({'type': 'phase', 'phase': 2, 'message': 'Mapare statusuri...', 'progress': 0, 'total': 1})}\n\n"

        # Re-map orders without AWB
        unmapped = {}
        updated = 0

        with _db() as conn:
            no_awb = conn.execute(
                "SELECT id, payment_status, fulfillment_status, shopify_delivery_status, tags FROM profit_orders WHERE month=? AND (awb='' OR awb IS NULL)",
                (month,)
            ).fetchall()
            for r in no_awb:
                cat = _map_status("", r["fulfillment_status"], r["payment_status"], "", mapping,
                                  shopify_delivery_status=r["shopify_delivery_status"] or "",
                                  unmapped_collector=unmapped, tags=r["tags"] or "")
                conn.execute("UPDATE profit_orders SET status_category=? WHERE id=?", (cat, r["id"]))
                updated += 1

        unmapped_count = sum(unmapped.values())
        yield f"data: {json.dumps({'type': 'done', 'refreshed': tracked + updated, 'closed_skipped': closed_count, 'unmapped_count': unmapped_count, 'unmapped_statuses': dict(sorted(unmapped.items(), key=lambda x: -x[1]))})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/api/profitability/clear")
async def clear_month(month: str = Query(...)):
    with _db() as conn:
        conn.execute("DELETE FROM profit_orders WHERE month=?", (month,))
    return {"ok": True, "month": month}


# ═══════════════════════════════════════════════════════════════
# Open Orders — Manual Status Override
# ═══════════════════════════════════════════════════════════════

CLOSED_CATEGORIES = {"Livrata", "Refuzata", "Anulata"}


@router.get("/api/profitability/open-orders")
async def get_open_orders(month: str = Query(...), prefix: str = Query(default="")):
    """List orders that are NOT closed (not Livrata/Refuzata/Anulata)."""
    with _db() as conn:
        query = """
            SELECT id, prefix, order_name, awb, courier_key, courier_status,
                   status_category, payment_status, fulfillment_status, revenue, currency
            FROM profit_orders
            WHERE month=? AND status_category NOT IN ('Livrata','Refuzata','Anulata')
        """
        params = [month]
        if prefix:
            query += " AND prefix=?"
            params.append(prefix)
        query += " ORDER BY prefix, order_name"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


class ManualStatusItem(BaseModel):
    order_id: int
    status_category: str  # Livrata, Refuzata, Anulata, Netrimisa, In curs de livrare


class ManualStatusBulk(BaseModel):
    items: List[ManualStatusItem]


@router.post("/api/profitability/manual-status")
async def set_manual_status(data: ManualStatusBulk):
    """Manually override status_category for specific orders."""
    valid_cats = {"Livrata", "Refuzata", "Anulata", "Netrimisa", "In curs de livrare", "Lipsa awb"}
    updated = 0
    with _db() as conn:
        for item in data.items:
            if item.status_category not in valid_cats:
                continue
            conn.execute(
                "UPDATE profit_orders SET status_category=? WHERE id=?",
                (item.status_category, item.order_id)
            )
            updated += 1
    return {"ok": True, "updated": updated}

