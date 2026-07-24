# /// script
# requires-python = ">=3.10"
# ///
"""tt_attrib — atribuirea CANONICĂ a campaniilor TikTok pe brand, pentru conturi PARTAJATE.
Sursa unică de adevăr pentru cine deține o campanie TikTok (token / owner / regulă-cont), importată de
ad_spend_live.py (per-SKU) ȘI build_cache.run_daily_ad_spend (per-brand din warehouse). Verificată la
sursa API TikTok 2026-07: contul partajat 'ROSSI Nails Romania' se împarte corect GT/Magdeal/Reduceri
(token), iar 'Belasil' → Belasil owner + Esteban pe 'new tiktok'. Fără asta, un brand fără token înghite
tot contul (phantom): Reduceri lua 72k din 78k în loc de 8k reali.

Reguli (independente de ordinea de iterare a brandurilor):
  - cont PARTAJAT (împrumutat = are token-filter în Mapping): brand după regula-cont specifică →
    token GLOBAL din numele campaniei (cel mai lung câștigă) → owner-ul contului → owner explicit.
    Fără nimic = orfan (se raportează, NU se inventează).
  - cont DEDICAT: brandul owner.
"""
import re

# regulă-cont specifică (prioritate maximă): pe 'Belasil', campaniile 'NEW TIKTOK …' fără token = Esteban
ACCT_BRAND_RULES = {"belasil": [("new tiktok", "Esteban")]}
# owner explicit pt conturi partajate de MAI MULTE branduri cu token (campaniile fără token = brandul de mai jos)
ACCT_DEFAULT_OWNER = {"rossi nails romania": "Rossi Nails", "carpetto": "Rossi Nails",
                      "nocturna.ro": "Nocturna", "nocturna europa": "Ofertele Zilei"}


def is_test(campaign: str) -> bool:
    return bool(re.search(r"\btest\b", (campaign or "").lower()))


def build_maps(brands, tiktok_accounts_fn):
    """brands = listă de nume-brand; tiktok_accounts_fn(brand) -> [{name, campaign_filter}].
    Returnează M = {token2brand, owner, shared, tokens}."""
    token2brand, owner, shared = {}, {}, set()
    for b in brands:
        try:
            accs = tiktok_accounts_fn(b)
        except Exception:
            continue
        for e in accs or []:
            nm = (e.get("name") or "").strip().lower()
            f = (e.get("campaign_filter") or "").strip()
            if not nm:
                continue
            if f:                                   # b împrumută contul nm cu token f
                token2brand[f.lower()] = b
                shared.add(nm)
            else:                                   # b deține contul nm (dedicat)
                owner.setdefault(nm, b)
    tokens = sorted(token2brand, key=len, reverse=True)   # cel mai specific (lung) întâi
    return {"token2brand": token2brand, "owner": owner, "shared": shared, "tokens": tokens}


def attribute(acct_name, campaign, M):
    """Contul + campania -> nume-brand (sau None dacă orfan). M din build_maps."""
    acct_l = (acct_name or "").strip().lower()
    cl = (campaign or "").lower()
    if acct_l in M["shared"]:
        return (next((br for kw, br in ACCT_BRAND_RULES.get(acct_l, []) if kw in cl), None)
                or next((M["token2brand"][t] for t in M["tokens"] if t in cl), None)
                or M["owner"].get(acct_l)
                or ACCT_DEFAULT_OWNER.get(acct_l))
    return M["owner"].get(acct_l)                   # cont dedicat -> owner
