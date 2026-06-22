# /// script
# requires-python = ">=3.10"
# ///
"""
profit_core — funcțiile CANONICE de profitabilitate ARONA, importate de TOATE motoarele (engine
api/profitability.py, profit_by_sku.py, trendyol_profitability.py) ca să dea ACEEAȘI cifră pe aceeași
comandă. Pur funcții (fără I/O) — apelantul pasează datele. Regula: Contribuție = Venit − COGS − Transport
− Marketing, ex-TVA pe venit/COGS/transport (TVA deductibil), marketing NET, doar comenzi LIVRATE.

Din auditul celor 5 scripturi (2026-06-20): înainte fiecare trata TVA/COGS/transport/marketing/bundle diferit.
"""
from collections import defaultdict

# ---- TVA per ȚARĂ (nu per prefix arbitrar) ----
VAT_BY_COUNTRY = {"RO": 0.21, "BG": 0.20, "CZ": 0.21, "PL": 0.23, "HU": 0.27, "SK": 0.23, "HR": 0.25}  # SK 23% din 2025
PREFIX_COUNTRY = {"BG": "BG", "BONBG": "BG", "CZ": "CZ", "PL": "PL", "LUX": "RO", "NOC": "RO"}  # restul = RO

def vat_for_country(country: str) -> float:
    return VAT_BY_COUNTRY.get((country or "RO").upper(), 0.21)

def vat_for_prefix(prefix: str) -> float:
    return vat_for_country(PREFIX_COUNTRY.get(prefix, "RO"))

# ---- COGS în RON (override are prioritate, ca engine-ul; conversie din moneda magazinului) ----
def cogs_ron(qty=0, line_cogs_store=None, unit_cost_store=None, rate_store=1.0, override=None, fx=None) -> float:
    """COGS în RON. Prioritate: override=(cost,currency) per-unit × qty (ca engine l.1509);
    altfel line_cogs_store (total pe linie, în moneda magazinului) × rate_store [profit_by_sku];
    altfel unit_cost_store (per-unit) × rate_store × qty [engine/per-linie].
    fx={currency:rate_to_ron} pt moneda override-ului; rate_store=rate_to_ron al monedei comenzii."""
    if override:
        oc, ocur = override
        return (oc or 0) * ((fx or {}).get(ocur, 1.0)) * (qty or 0)
    if line_cogs_store is not None:
        return (line_cogs_store or 0) * (rate_store or 1.0)
    return (unit_cost_store or 0) * (rate_store or 1.0) * (qty or 0)

# ---- Transport pe COLET — cascadă: REAL per-AWB (AWBprint) → media DPD pe SKU-urile coletului → flat ----
def parcel_transport(order_skus, dpd, fallback, real_cost=None) -> tuple:
    """(cost, source). Prioritate:
    1. real_cost = transport_cost_fara_tva REAL per comandă din AWBprint (cel mai precis) → 'awb';
    2. media dpd_nomenclator pe SKU-urile coletului → 'dpd';
    3. fallback = cost_per_parcel/magazin → 'estimat'.
    dpd = {SKU_UPPER: avg_transport_cost real}."""
    if real_cost is not None and real_cost > 0:
        return float(real_cost), "awb"
    reals = [dpd[s.upper()] for s in order_skus if s and s.upper() in dpd]
    if reals:
        return sum(reals) / len(reals), "dpd"
    return (fallback or 0), "estimat"

# ---- prefix magazin → domeniul AWBprint (stores.name); pt transport REAL per-AWB. Sursă: fulfillment STORES ----
PREFIX_AWB_DOMAIN = {
    "EST": "esteban.ro", "BELA": "belasil.ro", "CARP": "carpetto.ro", "GEN": "gento.ro", "GT": "georgetalent.ro",
    "GRAN": "grandia.ro", "OFER": "ofertelezilei.ro", "MAG": "magdeal.ro", "RED": "reduceribune.ro",
    "BON": "casaofertelor.ro", "COV": "covoria.ro", "APR": "apreciat.ro", "LUX": "nocturnalux.ro",
    "ROSSI": "rossinails.ro", "NUB": "nubra", "CZ": "bonhaus.cz", "PL": "bonhaus.pl", "BONBG": "bonhaus.bg",
    "NOC": "nocturna.ro", "PAT": "cepatai.ro", "BG": "nocturna.bg",
}

def refusal_transport_multiplier(status_category: str, is_intl: bool = False) -> float:
    """Multiplicator pe cost_per_parcel (FALLBACK când nu există cost real AWBprint). Documentat:
    - Livrare / În curs de livrare → ×1 (dus-ul e plătit);
    - Refuz: **×2 doar pe INTERNAȚIONAL** (dus + retur); **×1 pe RO** (NU avem cost de retur pe RO);
    - altele (anulat etc.) → 0.
    Notă: dacă ai costul REAL per comandă din AWBprint (suma tuturor AWB-urilor, inclusiv retur), folosește-l
    direct — el conține deja retur-ul pe intl; multiplicatorul ăsta e doar pentru fallback."""
    if status_category in ("Livrata", "In curs de livrare"):
        return 1.0
    if status_category == "Refuzata":
        return 2.0 if is_intl else 1.0
    return 0.0

# ---- Status: venit + COGS doar pe LIVRATE ----
REVENUE_STATUSES = {"Livrata"}

def is_revenue(status_category: str) -> bool:
    return status_category in REVENUE_STATUSES

# ---- Marketing alocat pe COMENZI (CPA uniform pe categorie/brand) ----
def allocate_marketing_by_orders(cache_rows, sold_skus, sku_to_group, orders_count, brand_orders):
    """Alocă marketingul pe SKU. cache_rows=[(key, brand_id, spend)] din cache.product_ad_spend.
    - direct (key e SKU vândut) → exact pe el (HA-####/Google PMax);
    - grup (key e grup WMS cu SKU-uri vândute) → pe SKU-urile grupului ∝ nr. comenzi;
    - brand-level/UNMAPPED → pe SKU-urile brandului (brand_orders[brand_id]) ∝ nr. comenzi;
    - TEST exclus. orders_count={sku:nr_comenzi}; brand_orders={brand_id:{sku:nr_comenzi}}.
    Returns (mk={sku:spend}, leftover) — leftover = spend nealocabil (brand fără SKU vândut)."""
    mk = defaultdict(float); leftover = 0.0
    grp_members = defaultdict(list)
    for s in sold_skus:
        grp_members[sku_to_group.get(s)].append(s)
    for key, bid, sp in cache_rows:
        sp = float(sp or 0)
        if not sp or key == "TEST":
            continue
        if key in sold_skus:                                                       # DIRECT (per-SKU exact)
            mk[key] += sp; continue
        members = [s for s in grp_members.get(key, []) if orders_count.get(s, 0) > 0]   # GRUP (categorie)
        if members:
            tot = sum(orders_count[s] for s in members)
            for s in members:
                mk[s] += sp * orders_count[s] / tot
            continue
        bo = brand_orders.get(bid) if bid else None                                # BRAND-LEVEL / UNMAPPED
        if bo and sum(bo.values()) > 0:
            tot = sum(bo.values())
            for s, oc in bo.items():
                mk[s] += sp * oc / tot
        else:
            leftover += sp
    return dict(mk), leftover

# ---- prefix magazin → brand_id (brand_map['shopify'] e gol; prefixele sunt stabile) ----
PREFIX_BRAND = {
    "EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GRAN": "Grandia", "BELA": "Belasil",
    "CARP": "Carpetto", "COV": "Covoria", "OFER": "Ofertele Zilei", "MAG": "Magdeal", "NOC": "Nocturna",
    "APR": "Apreciat", "RED": "Reduceri bune", "GEN": "Gento", "PAT": "Ce Pat Ai", "ROSSI": "Rossi Nails",
    "BON": "Bonhaus", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "BG": "Bonhaus BG", "BONBG": "Bonhaus BG",
    "LUX": "Nocturna Lux",
}

def prefix_brandid(name2id: dict) -> dict:
    """prefix -> brand_id (metrics), via PREFIX_BRAND + brands(name->id), fallback pe primul cuvânt."""
    out = {}
    for pfx, name in PREFIX_BRAND.items():
        bid = name2id.get(name.strip().lower()) or name2id.get(name.split()[0].lower())
        if bid:
            out[pfx] = bid
    return out
