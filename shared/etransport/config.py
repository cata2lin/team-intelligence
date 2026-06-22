"""
Configurare centrală pentru sistemul e-Transport SmartBill.
Toate valorile configurabile sunt definite aici pentru a permite
modificări rapide fără a atinge logica de business.
"""

# ──────────────────────────────────────────────────────────────
# TIPURI DE OPERAȚIUNI
# ──────────────────────────────────────────────────────────────
OPERATION_TYPES = {
    "import": {
        "code": 40,
        "label": "Import",
        "description": "Import din afara UE",
        "start_location_type": "customs_office",  # birou vamal
    },
    "achizitie_intracomunitara": {
        "code": 10,
        "label": "Achiziție Intracomunitară",
        "description": "Achiziție din interiorul UE",
        "start_location_type": "ptf",  # punct de trecere a frontierei
    },
}

# ──────────────────────────────────────────────────────────────
# SCOP OPERAȚIUNE (default)
# ──────────────────────────────────────────────────────────────
DEFAULT_OPERATION_PURPOSE_CODE = "9999"
DEFAULT_OPERATION_PURPOSE_LABEL = "Același cu operațiunea"

# Override-uri specifice per tip operațiune (opțional)
OPERATION_PURPOSE_OVERRIDES = {
    # "achizitie_intracomunitara": {"code": "101", "label": "Revânzare"},
}

# ──────────────────────────────────────────────────────────────
# MAPARE UNITĂȚI DE MĂSURĂ
# Cheile sunt forme normalizate (lowercase, fără spații)
# display_unit = ce se afișează în XLSX
# standard_unit_code = codul ANAF/UN-CEFACT
# ──────────────────────────────────────────────────────────────
UNIT_MAPPINGS = {
    # Bucăți
    "pcs": {"display_unit": "buc", "standard_unit_code": "H87"},
    "pc": {"display_unit": "buc", "standard_unit_code": "H87"},
    "piece": {"display_unit": "buc", "standard_unit_code": "H87"},
    "pieces": {"display_unit": "buc", "standard_unit_code": "H87"},
    "buc": {"display_unit": "buc", "standard_unit_code": "H87"},
    "bucata": {"display_unit": "buc", "standard_unit_code": "H87"},
    "bucati": {"display_unit": "buc", "standard_unit_code": "H87"},
    "unit": {"display_unit": "buc", "standard_unit_code": "H87"},
    "units": {"display_unit": "buc", "standard_unit_code": "H87"},
    "ea": {"display_unit": "buc", "standard_unit_code": "H87"},
    "each": {"display_unit": "buc", "standard_unit_code": "H87"},
    "set": {"display_unit": "buc", "standard_unit_code": "H87"},
    "sets": {"display_unit": "buc", "standard_unit_code": "H87"},
    # Kilograme
    "kg": {"display_unit": "kg", "standard_unit_code": "KGM"},
    "kgs": {"display_unit": "kg", "standard_unit_code": "KGM"},
    "kilogram": {"display_unit": "kg", "standard_unit_code": "KGM"},
    "kilograms": {"display_unit": "kg", "standard_unit_code": "KGM"},
    # Tone
    "t": {"display_unit": "t", "standard_unit_code": "TNE"},
    "ton": {"display_unit": "t", "standard_unit_code": "TNE"},
    "tons": {"display_unit": "t", "standard_unit_code": "TNE"},
    "tonne": {"display_unit": "t", "standard_unit_code": "TNE"},
    "tonnes": {"display_unit": "t", "standard_unit_code": "TNE"},
    # Cutii / Cartoane
    "box": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    "boxes": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    "carton": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    "cartons": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    "cutie": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    "cutii": {"display_unit": "cutie", "standard_unit_code": "XBX"},
    # Litri
    "l": {"display_unit": "l", "standard_unit_code": "LTR"},
    "ltr": {"display_unit": "l", "standard_unit_code": "LTR"},
    "litru": {"display_unit": "l", "standard_unit_code": "LTR"},
    "litri": {"display_unit": "l", "standard_unit_code": "LTR"},
    "liter": {"display_unit": "l", "standard_unit_code": "LTR"},
    "liters": {"display_unit": "l", "standard_unit_code": "LTR"},
    "litre": {"display_unit": "l", "standard_unit_code": "LTR"},
    "litres": {"display_unit": "l", "standard_unit_code": "LTR"},
    # Metri
    "m": {"display_unit": "m", "standard_unit_code": "MTR"},
    "meter": {"display_unit": "m", "standard_unit_code": "MTR"},
    "meters": {"display_unit": "m", "standard_unit_code": "MTR"},
    "metre": {"display_unit": "m", "standard_unit_code": "MTR"},
    "metres": {"display_unit": "m", "standard_unit_code": "MTR"},
    # Metri pătrați
    "sqm": {"display_unit": "m²", "standard_unit_code": "MTK"},
    "m2": {"display_unit": "m²", "standard_unit_code": "MTK"},
}

# Fallback dacă unitatea nu e găsită
DEFAULT_UNIT = {"display_unit": "buc", "standard_unit_code": "H87"}

# ──────────────────────────────────────────────────────────────
# VALUTĂ ȘI CURS
# ──────────────────────────────────────────────────────────────
DEFAULT_CURRENCY = "USD"
OUTPUT_CURRENCY = "RON"

# Mod de export pentru sume și monede în SmartBill
# 'original' = pastrează moneda și prețul inițial de pe factură
# 'ron'      = exportă valorile convertite în RON
EXPORT_CURRENCY_MODE = "original"

# Cursuri implicite (pot fi suprascrise din UI)
DEFAULT_EXCHANGE_RATES = {
    "USD": 4.65,
    "EUR": 4.97,
    "GBP": 5.85,
    "CNY": 0.64,
    "RON": 1.0,
}

# ──────────────────────────────────────────────────────────────
# COLOANE XLSX SMARTBILL
# Ordinea și denumirile exacte ale coloanelor de export
# ──────────────────────────────────────────────────────────────
SMARTBILL_XLSX_COLUMNS = [
    "Denumire produs",
    "Greutate netă",
    "Greutate brută",
    "Cod produs",
    "U.M. produs",
    "Moneda",
    "Cantitate",
    "Cod standard pentru U.M.",
    "Pret unitar fara TVA",
    "Cod tarifar (N.C.)",
    "Scop operatiune",
]

# ──────────────────────────────────────────────────────────────
# AGREGARE
# ──────────────────────────────────────────────────────────────
AGGREGATE_LINES = False  # True = agregă liniile cu aceeași cheie

# Cheia de agregare
AGGREGATION_KEY_FIELDS = [
    "nc_code_8",
    "product_name_export",
    "display_unit",
    "standard_unit_code",
    "operation_purpose_code",
]

# ──────────────────────────────────────────────────────────────
# SUPRASCRIERI CODURI NC (PENTRU CODURI PROBLEMATICE/INVALIDE)
# ──────────────────────────────────────────────────────────────
# Mapare: {"cod_nevalid": "cod_valid"}
NC_CODE_OVERRIDES = {
    # Exemplu:
    # "39249000": "39241000",
}

# ──────────────────────────────────────────────────────────────
# DESTINAȚIE DEFAULT (poate fi suprascrisă din UI)
# ──────────────────────────────────────────────────────────────
DEFAULT_DESTINATION = {
    "country": "RO",
    "county": "",
    "city": "",
    "street": "",
    "number": "",
    "postal_code": "",
}

# ──────────────────────────────────────────────────────────────
# ROTUNJIRE
# ──────────────────────────────────────────────────────────────
WEIGHT_DECIMALS = 3       # nr. zecimale pentru greutăți
VALUE_DECIMALS = 2        # nr. zecimale pentru valori
QUANTITY_DECIMALS = 0     # nr. zecimale pentru cantități (0 = integer)

# ──────────────────────────────────────────────────────────────
# MATCHING
# ──────────────────────────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = 70  # scor minim rapidfuzz pentru match (0-100)

# ──────────────────────────────────────────────────────────────
# BIROURI VAMALE ȘI PTF-URI FRECVENTE
# ──────────────────────────────────────────────────────────────
CUSTOMS_OFFICES = {
    "ROCT0900": "Biroul Vamal Constanța Sud Agigea",
    "ROCT0100": "Biroul Vamal Constanța",
    "ROBV0100": "Biroul Vamal Brașov",
    "ROGL0100": "Biroul Vamal Galați",
    "ROCJ0100": "Biroul Vamal Cluj",
    "ROTM0100": "Biroul Vamal Timișoara",
}

PTF_CODES = {
    "ROBO0200": "PTF Borș",
    "ROBR0100": "PTF Borș II",
    "ROND0100": "PTF Nădlac",
    "ROND0200": "PTF Nădlac II",
    "ROPT0100": "PTF Petea",
    "ROSM0100": "PTF Siret",
    "ROGL0100": "PTF Galați",
    "ROCT0900": "PTF Constanța Sud",
}

# ──────────────────────────────────────────────────────────────
# EXPORTUL XLSX
# ──────────────────────────────────────────────────────────────
XLSX_SHEET_NAME = "Produse e-Transport"
DEFAULT_OUTPUT_DIR = "output"
