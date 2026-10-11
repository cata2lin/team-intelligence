# /// script
# requires-python = ">=3.10"
# ///
r"""tt_attrib — atribuirea CANONICĂ a campaniilor TikTok pe brand, pentru conturi PARTAJATE.
Sursa unică de adevăr pentru cine deține o campanie TikTok (token / owner / regulă-cont), importată de
ad_spend_live.py (per-SKU) ȘI build_cache.run_daily_ad_spend (per-brand din warehouse). Verificată la
sursa API TikTok 2026-07: contul partajat 'ROSSI Nails Romania' se împarte corect GT/Magdeal/Reduceri
(token), iar 'Belasil' → Belasil owner + Esteban pe 'new tiktok'. Fără asta, un brand fără token înghite
tot contul (phantom): Reduceri lua 72k din 78k în loc de 8k reali.

Tokenul din Mapping col F („Campanie") poate fi LITERAL („ESTEBAN", „MAGDEAL", „GT" — substring, ca
până acum) sau REGEX cu alternative, ca să prindă ȘI codul SCURT pe care agenția îl pune în numele
campaniei din 2026-08 („{HAC} - CO - C46 - HA-1439", „{HAC} - HA-0434 - RB ALESSIO"):
    CO=Casa Ofertelor · RB=Reduceri Bune · OZ/OF=Ofertele Zilei · MD=Magdeal
Codurile de 2 litere se scriu OBLIGATORIU cu graniță de cuvânt, în forma `(^|[^A-Z0-9])CO([^A-Z0-9]|$)`
— NU `\bCO\b`: formula din Apps Script face `REGEXMATCH(UPPER(campanie); UPPER(token))`, iar UPPER
transformă `\b` în `\B` (graniță NEGATĂ) și inversează tăcut regula. Forma cu clasă de caractere e
deja majusculă, deci imună. Fără graniță, „CO" prinde în „COVORIA"/„CONTENT".

Reguli (independente de ordinea de iterare a brandurilor):
  - cont PARTAJAT (împrumutat = are token-filter în Mapping): brand după regula-cont specifică →
    token GLOBAL din numele campaniei (cel mai lung câștigă) → owner-ul contului → owner explicit.
    Fără nimic = orfan (se raportează, NU se inventează).
  - cont DEDICAT: brandul owner.
"""
import re

_RE_META = set("|()[]\\^$*+?{}")


def compile_token(tok):
    """Tokenul din Mapping col F -> pattern compilat. Regex dacă are metacaractere, altfel literal
    (substring, comportamentul de dinainte). Un regex stricat cade pe literal, nu aruncă."""
    t = (tok or "").strip()
    if not t:
        return None
    if any(ch in _RE_META for ch in t):
        try:
            return re.compile(t, re.I)
        except re.error:
            pass
    return re.compile(re.escape(t), re.I)


def token_specificity(tok):
    """Cât de specific e un token = cea mai lungă secvență LITERALĂ din el. La regex, len(pattern)
    minte: „(^|[^A-Z0-9])CO([^A-Z0-9]|$)" e lung dar prinde 2 caractere, deci trebuie să piardă în
    fața lui „CASA OFERTELOR". Așa „cel mai specific câștigă" rămâne valabil și cu alternative."""
    return max((len(x.strip()) for x in re.split(r"[^A-Za-z0-9 ]+", tok or "") if x.strip()), default=0)


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
    tokens = sorted(token2brand, key=token_specificity, reverse=True)   # cel mai specific întâi
    pats = [(compile_token(t), token2brand[t]) for t in tokens]
    pats = [(rx, b) for rx, b in pats if rx is not None]
    return {"token2brand": token2brand, "owner": owner, "shared": shared, "tokens": tokens, "pats": pats}


def attribute(acct_name, campaign, M):
    """Contul + campania -> nume-brand (sau None dacă orfan). M din build_maps."""
    acct_l = (acct_name or "").strip().lower()
    cl = (campaign or "").lower()
    if acct_l in M["shared"]:
        return (next((br for kw, br in ACCT_BRAND_RULES.get(acct_l, []) if kw in cl), None)
                or next((br for rx, br in M["pats"] if rx.search(campaign or "")), None)
                or M["owner"].get(acct_l)
                or ACCT_DEFAULT_OWNER.get(acct_l))
    return M["owner"].get(acct_l)                   # cont dedicat -> owner
