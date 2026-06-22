"""
profit_config.py — modelul de COSTURI al afacerii pentru profitability.py.
Astea sunt cifrele TALE autoritative (agenție, OPEX, abonamente). Editează-le aici.
Sursele tranzacționale (Incasari/COGS/Transport/Marketing) NU sunt aici — alea vin din AWB Arona + Shopify + metrics.

⚠️ Marcat `# TODO` = valoare pe care trebuie să mi-o confirmi (nu o știu din date).
"""

# ── Agenție (comision) per brand ────────────────────────────────────────────────
# base = (Incasari − Transport), cu TVA (ca în sheet-ul „Comision": Suma = rate×(Incasari−Transport)).
#   pct: procent pe (Incasari−Transport).  fixed_extra: fee fix lunar al agenției 2 (Google), lei/lună.
AGENCY = {
    "_default":  {"pct": 0.025},                            # agenția Meta/TikTok: 2,5%×(Incasari−Transport)
    "grandia":   {"pct": 0.015, "fixed_extra": 13000.0},    # 1,5%×(Incasari−Transport) prima agenție + 13.000 lei/lună fix agenția 2 (Google)
}
# Branduri care NU plătesc comision agenție (rulate 100% in-house). Adaugă după caz.
AGENCY_NONE = {"carpetto", "gento", "bonhaus_cz", "bonhaus_pl", "bonhaus_bg"}  # TODO: confirmă

# ── Abonamente per brand (lei/lună) ─────────────────────────────────────────────
# Klaviyo Esteban = 720 USD/lună (convertit la RON din fx_rates la runtime).
SUBSCRIPTIONS_USD = {
    "esteban": {"Klaviyo": 720.0},
}
# Shopify + Google Workspace per brand (lei/lună) — din „Costuri fixe" (ian 2026).
SUBSCRIPTIONS_RON = {
    "rossi":        {"Shopify": 1061.16, "Workspace": 34.84},
    "nocturnalux":  {"Shopify": 1015.75, "Workspace": 17.42},
    "casaofertelor":{"Shopify": 458.06,  "Workspace": 34.84},
    "belasil":      {"Shopify": 313.74,  "Workspace": 34.84},
    # esteban Shopify era gol în ian — TODO completează restul brandurilor
}

# ── Consumabile (ambalaj), lei per COLET expediat ───────────────────────────────
CONSUMABILE_PER_PARCEL = 0.0   # TODO: cât costă ambalajul/colet (cutie+folie+etichetă)?

# ── OPEX comun (nivel GRUP, lei/lună): salarii, chirii, contabilitate, SaaS comun ─
# Din „Costuri fixe" (ian 2026): salarii ~18.905 + chirii/utilități depozit+birou ~5.549 +
# contabilă 2.400 + SaaS comun (Smartbill/ChatGPT/Adobe/Canva/Workspace) ~482 + agenție retainer 2.485.
OPEX_SHARED_MONTHLY = 29820.0  # se scade la nivel de GRUP → EBITDA (nu per-brand)

# ── Mapare cheie brand (profitability) → nume brand în metrics (pt marketing auto) ─
BRAND_NAME = {
    "esteban": "Esteban", "belasil": "Belasil", "carpetto": "Carpetto", "gento": "Gento",
    "gt": "George Talent ", "grandia": "Grandia", "ofertele": "Ofertele Zilei", "magdeal": "Magdeal ",
    "reduceri": "Reduceri bune", "casaofertelor": "Bonhaus", "covoria": "Covoria", "apreciat": "Apreciat",
    "nocturnalux": "Nocturna Lux", "rossi": "Rossi Nails", "nubra": "Nubra",
    "bonhaus_cz": "Bonhaus CZ", "bonhaus_pl": "Bonhaus PL", "bonhaus_bg": "Bonhaus BG",
}

# ── Override marketing (lei pe perioadă) când maparea din metrics e incompletă ────
# Maparea brand→cont din metrics e parțială (doar Esteban/Gento/Grandia/Nubra au conturi legate,
# și alea parțial — Esteban n-are Google mapat). Pune aici spend-ul real pe perioadă unde lipsește.
MARKETING_OVERRIDE = {}   # ex: {"esteban": 95000, "gt": 40000}
