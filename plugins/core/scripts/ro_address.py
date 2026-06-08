"""Romanian address parser for Cristina's UGC `Comenzi` sheet.

Takes Cristina's free-form, comma-separated address (with phones / emails
inline, judet abbreviations, sector shorthands, etc.) and returns a
Shopify-shaped dict: {address1, address2, city, province, zip, countryCode}.

Strategy:
  1. Normalize: strip inline phones / emails so they don't pollute matches.
  2. For each segment, try to classify it (zip, sector, judet, known city,
     street, apartment-detail). Classified segments are "claimed".
  3. Unclaimed plain-text segments → city if still empty.
  4. Fall back to a county-/sector-center ZIP when the input has no zip.

Leaves `city`/`province` empty rather than guessing — the caller refuses
to ship a row with an incomplete address.
"""

from __future__ import annotations
import re

RO_COUNTIES: dict[str, str] = {
    'alba': 'Alba', 'arad': 'Arad', 'arges': 'Argeș', 'bacau': 'Bacău',
    'bihor': 'Bihor', 'bistrita-nasaud': 'Bistrița-Năsăud',
    'botosani': 'Botoșani', 'brasov': 'Brașov', 'braila': 'Brăila',
    'buzau': 'Buzău', 'caras-severin': 'Caraș-Severin',
    'calarasi': 'Călărași', 'cluj': 'Cluj', 'constanta': 'Constanța',
    'covasna': 'Covasna', 'dambovita': 'Dâmbovița', 'dolj': 'Dolj',
    'galati': 'Galați', 'giurgiu': 'Giurgiu', 'gorj': 'Gorj',
    'harghita': 'Harghita', 'hunedoara': 'Hunedoara',
    'ialomita': 'Ialomița', 'iasi': 'Iași', 'ilfov': 'Ilfov',
    'maramures': 'Maramureș', 'mehedinti': 'Mehedinți', 'mures': 'Mureș',
    'neamt': 'Neamț', 'olt': 'Olt', 'prahova': 'Prahova',
    'satu mare': 'Satu Mare', 'salaj': 'Sălaj', 'sibiu': 'Sibiu',
    'suceava': 'Suceava', 'teleorman': 'Teleorman', 'timis': 'Timiș',
    'tulcea': 'Tulcea', 'vaslui': 'Vaslui', 'valcea': 'Vâlcea',
    'vrancea': 'Vrancea', 'bucuresti': 'Bucharest',
}

# 2-letter county codes Cristina sometimes writes as `jud.DJ`.
RO_COUNTY_CODES: dict[str, str] = {
    'ab': 'Alba', 'ar': 'Arad', 'ag': 'Argeș', 'bc': 'Bacău', 'bh': 'Bihor',
    'bn': 'Bistrița-Năsăud', 'bt': 'Botoșani', 'bv': 'Brașov', 'br': 'Brăila',
    'bz': 'Buzău', 'cs': 'Caraș-Severin', 'cl': 'Călărași', 'cj': 'Cluj',
    'ct': 'Constanța', 'cv': 'Covasna', 'db': 'Dâmbovița', 'dj': 'Dolj',
    'gl': 'Galați', 'gr': 'Giurgiu', 'gj': 'Gorj', 'hr': 'Harghita',
    'hd': 'Hunedoara', 'il': 'Ialomița', 'is': 'Iași', 'if': 'Ilfov',
    'mm': 'Maramureș', 'mh': 'Mehedinți', 'ms': 'Mureș', 'nt': 'Neamț',
    'ot': 'Olt', 'ph': 'Prahova', 'sm': 'Satu Mare', 'sj': 'Sălaj',
    'sb': 'Sibiu', 'sv': 'Suceava', 'tr': 'Teleorman', 'tm': 'Timiș',
    'tl': 'Tulcea', 'vs': 'Vaslui', 'vl': 'Vâlcea', 'vn': 'Vrancea',
    'b': 'Bucharest',
}

# County → primary city, used as last-resort city when province is known
# but no city was provided. DPD will street-correct anyway.
COUNTY_CAPITAL: dict[str, str] = {
    'Alba': 'Alba Iulia', 'Arad': 'Arad', 'Argeș': 'Pitești',
    'Bacău': 'Bacău', 'Bihor': 'Oradea', 'Bistrița-Năsăud': 'Bistrița',
    'Botoșani': 'Botoșani', 'Brașov': 'Brașov', 'Brăila': 'Brăila',
    'Buzău': 'Buzău', 'Caraș-Severin': 'Reșița', 'Călărași': 'Călărași',
    'Cluj': 'Cluj-Napoca', 'Constanța': 'Constanța', 'Covasna': 'Sfântu Gheorghe',
    'Dâmbovița': 'Târgoviște', 'Dolj': 'Craiova', 'Galați': 'Galați',
    'Giurgiu': 'Giurgiu', 'Gorj': 'Târgu Jiu', 'Harghita': 'Miercurea Ciuc',
    'Hunedoara': 'Deva', 'Ialomița': 'Slobozia', 'Iași': 'Iași',
    'Ilfov': 'Voluntari', 'Maramureș': 'Baia Mare', 'Mehedinți': 'Drobeta-Turnu Severin',
    'Mureș': 'Târgu Mureș', 'Neamț': 'Piatra Neamț', 'Olt': 'Slatina',
    'Prahova': 'Ploiești', 'Satu Mare': 'Satu Mare', 'Sălaj': 'Zalău',
    'Sibiu': 'Sibiu', 'Suceava': 'Suceava', 'Teleorman': 'Alexandria',
    'Timiș': 'Timișoara', 'Tulcea': 'Tulcea', 'Vaslui': 'Vaslui',
    'Vâlcea': 'Râmnicu Vâlcea', 'Vrancea': 'Focșani',
}

ZIP_FALLBACK: dict[str, str] = {
    'București-Sector 1': '010001', 'București-Sector 2': '020001',
    'București-Sector 3': '030001', 'București-Sector 4': '040001',
    'București-Sector 5': '050001', 'București-Sector 6': '060001',
    'Alba': '510001', 'Arad': '310001', 'Argeș': '110001',
    'Bacău': '600001', 'Bihor': '410001', 'Bistrița-Năsăud': '420001',
    'Botoșani': '710001', 'Brașov': '500001', 'Brăila': '810001',
    'Buzău': '120001', 'Caraș-Severin': '320001', 'Călărași': '910001',
    'Cluj': '400001', 'Constanța': '900001', 'Covasna': '520001',
    'Dâmbovița': '130001', 'Dolj': '200001', 'Galați': '800001',
    'Giurgiu': '080001', 'Gorj': '210001', 'Harghita': '530001',
    'Hunedoara': '330001', 'Ialomița': '920001', 'Iași': '700001',
    'Ilfov': '077001', 'Maramureș': '430001', 'Mehedinți': '220001',
    'Mureș': '540001', 'Neamț': '610001', 'Olt': '230001',
    'Prahova': '100001', 'Satu Mare': '440001', 'Sălaj': '450001',
    'Sibiu': '550001', 'Suceava': '720001', 'Teleorman': '140001',
    'Timiș': '300001', 'Tulcea': '820001', 'Vaslui': '730001',
    'Vâlcea': '240001', 'Vrancea': '620001',
}

CITY_TO_COUNTY: dict[str, tuple[str, str]] = {
    'focsani': ('Focșani', 'Vrancea'),
    'macin': ('Măcin', 'Tulcea'),
    'smardan': ('Smârdan', 'Galați'),
    'galati': ('Galați', 'Galați'),
    'brasov': ('Brașov', 'Brașov'),
    'cluj-napoca': ('Cluj-Napoca', 'Cluj'),
    'cluj napoca': ('Cluj-Napoca', 'Cluj'),
    'iasi': ('Iași', 'Iași'),
    'constanta': ('Constanța', 'Constanța'),
    'timisoara': ('Timișoara', 'Timiș'),
    'ploiesti': ('Ploiești', 'Prahova'),
    'pitesti': ('Pitești', 'Argeș'),
    'sibiu': ('Sibiu', 'Sibiu'),
    'oradea': ('Oradea', 'Bihor'),
    'craiova': ('Craiova', 'Dolj'),
    'arad': ('Arad', 'Arad'),
    'baia mare': ('Baia Mare', 'Maramureș'),
    'targu mures': ('Târgu Mureș', 'Mureș'),
    'buzau': ('Buzău', 'Buzău'),
    'braila': ('Brăila', 'Brăila'),
    'suceava': ('Suceava', 'Suceava'),
    'magurele': ('Măgurele', 'Ilfov'),
    'chiajna': ('Chiajna', 'Ilfov'),
    'mosnita noua': ('Moșnița Nouă', 'Timiș'),
    'varbilau': ('Vărbilău', 'Prahova'),
    'tautii magheraus': ('Tăuții-Măgherăuș', 'Maramureș'),
    'manarade': ('Mănărade', 'Alba'),
    'simeria': ('Simeria', 'Hunedoara'),
    'otelu rosu': ('Oțelu Roșu', 'Caraș-Severin'),
    'selimbar': ('Șelimbăr', 'Sibiu'),
    'chisineu-cris': ('Chișineu-Criș', 'Arad'),
    'chisineu cris': ('Chișineu-Criș', 'Arad'),
    'giroc': ('Giroc', 'Timiș'),
    'floresti': ('Florești', 'Cluj'),
    'bistrita': ('Bistrița', 'Bistrița-Năsăud'),
    # Ilfov satellite localities (Cristina's UGC list often hits these)
    'pantelimon': ('Pantelimon', 'Ilfov'),
    'rosu': ('Roșu', 'Ilfov'),
    'voluntari': ('Voluntari', 'Ilfov'),
    'buftea': ('Buftea', 'Ilfov'),
    'otopeni': ('Otopeni', 'Ilfov'),
    'jilava': ('Jilava', 'Ilfov'),
    'popesti-leordeni': ('Popești-Leordeni', 'Ilfov'),
    'popesti leordeni': ('Popești-Leordeni', 'Ilfov'),
    'bragadiru': ('Bragadiru', 'Ilfov'),
    'dudu': ('Dudu', 'Ilfov'),
    # Other Dolj localities seen in UGC
    'bechet': ('Bechet', 'Dolj'),
    'filiasi': ('Filiași', 'Dolj'),
    # Prahova
    'valea calugareasca': ('Valea Călugărească', 'Prahova'),
}

_DIACRITICS = str.maketrans('ăâîșşțţĂÂÎȘŞȚŢ', 'aaisstt' + 'AAISSTT')

_PHONE_RE = re.compile(r'\b(?:\+?40)?0?7\d{8}\b')
_EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')
_TRAIL_PUNCT_RE = re.compile(r'[\s.,;:!?]+$')


def _norm(s: str) -> str:
    return s.translate(_DIACRITICS).lower().strip()


def _clean(s: str) -> str:
    s = _PHONE_RE.sub('', s)
    s = _EMAIL_RE.sub('', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    s = _TRAIL_PUNCT_RE.sub('', s)
    return s


def _looks_like_street(s_norm: str) -> bool:
    return (
        re.search(r'\bnr\.?\s*\d', s_norm) is not None
        or re.match(
            r'(str\.|str |strada |drumul |drum |aleea |calea |b-dul|bd\.|'
            r'bulevardul |splaiul |sos\.|soseaua |piata |intrarea )', s_norm
        ) is not None
    )


def _looks_like_apt(s_norm: str) -> bool:
    return re.search(
        r'\b(bl\.|sc\.|et\.|ap\.|ap\s*\d|bis|scara|etaj|bloc\b|parter|apartame)',
        s_norm,
    ) is not None


def parse_address(raw: str) -> dict:
    if not raw:
        return {'address1': '', 'address2': None, 'city': '',
                'province': '', 'zip': None, 'countryCode': 'RO'}

    # Pre-process: drop "Recipient :" prefix when the LHS of the first colon
    # has no digits / street markers — Cristina sometimes writes the
    # influencer name twice (col C + inside col F).
    if ':' in raw:
        head, _, tail = raw.partition(':')
        head_norm = _norm(head)
        looks_like_name = (
            len(head.strip().split()) <= 4
            and not re.search(r'\d', head)
            and not _looks_like_street(head_norm)
            and 'judet' not in head_norm and not head_norm.startswith('jud')
            and 'cod' not in head_norm and 'sector' not in head_norm
        )
        if looks_like_name and tail.strip():
            raw = tail

    # Treat `/` as a comma separator (Cristina writes e.g. `Craiova/Dolj`).
    raw = raw.replace('/', ',')
    # Insert a comma before standalone `Judet[ul]?` / `Jud.` mid-string so
    # `Strada X nr Y Judetul Z W` splits cleanly.
    raw = re.sub(r'(?<=\S)\s+(?=(?:[Jj]udet(?:ul)?|[Jj]ud\.)\b)', ', ', raw)

    raw_segs = [s.strip() for s in raw.split(',') if s.strip()]
    segs = [_clean(s) for s in raw_segs]
    segs = [s for s in segs if s]

    address1 = ''
    addr2: list[str] = []
    city = ''
    province = ''
    zipc: str | None = None
    sector: str | None = None
    claimed: set[int] = set()

    # 1. zip
    for i, s in enumerate(segs):
        m = re.search(r'cod\s*p?(?:ostal|\.)?\s*[:\-]?\s*(\d{4,6})', _norm(s))
        if m:
            zipc = m.group(1)
            # Strip the `cod poștal NNN` phrase from the segment text so the
            # rest (which often contains street / city) can still be
            # classified by later passes. Don't claim the whole segment.
            stripped = re.sub(
                r'cod\s*p?(?:ostal|\.)?\s*[:\-]?\s*\d{4,6}', '', s,
                flags=re.I,
            ).strip(' ,.;:-')
            if stripped:
                segs[i] = stripped
            else:
                claimed.add(i)
            break
    if not zipc:
        for i, s in enumerate(segs):
            if i in claimed:
                continue
            if re.fullmatch(r'\d{6}', s.strip()):
                zipc = s.strip()
                claimed.add(i)
                break
            # "<6-digit zip> <city>" combined in one segment
            m = re.match(r'^\s*(\d{6})\s+(.+)$', s.strip())
            if m:
                zipc = m.group(1)
                rest = m.group(2).strip()
                # Replace the segment with just the city portion so later
                # passes can classify it (known-city / county / fallback).
                segs[i] = rest
                break

    # 2. București + sector
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if re.fullmatch(r'sec(?:tor|t)?\.?\s*\d', sl):
            m = re.search(r'\d', sl)
            if m:
                sector = m.group(0)
                if not city:
                    city = 'București'
                if not province:
                    province = 'Bucharest'
            addr2.append(s)
            claimed.add(i)
        elif 'bucuresti' in sl:
            city = 'București'
            province = 'Bucharest'
            stripped = re.sub(
                r'(bucuresti|sec(?:tor|t)?\.?\s*\d)', '', sl
            ).strip(' ,.')
            m = re.search(r'sec(?:tor|t)?\.?\s*(\d)', sl)
            if m and not sector:
                sector = m.group(1)
            if not stripped:
                claimed.add(i)

    # 3. Județ — explicit "jud..." prefix only.
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        m = re.match(r'(?:judetul|judet|jud)[\s\.]+(.+)', sl)
        if m:
            jname_raw = m.group(1).strip(' .,;')
            matched_county_key: str | None = None
            for cnty_key, cnty_name in RO_COUNTIES.items():
                if jname_raw.startswith(cnty_key):
                    province = cnty_name
                    matched_county_key = cnty_key
                    break
            if not matched_county_key:
                first = jname_raw.split()[0] if jname_raw else ''
                if first in RO_COUNTIES:
                    province = RO_COUNTIES[first]
                    matched_county_key = first
                elif first in RO_COUNTY_CODES:
                    province = RO_COUNTY_CODES[first]
                    matched_county_key = first
            if matched_county_key:
                # Trailing text after the county name (e.g. `judetul ilfov
                # pantelimon`) is a city candidate — re-inject as a new seg.
                trailing = jname_raw[len(matched_county_key):].strip(' .,;')
                if trailing:
                    # Keep original-case version from the source segment.
                    orig_after_jud = re.sub(
                        r'^(?:judetul|judet|jud)[\s\.]+', '', s,
                        flags=re.I,
                    ).strip(' .,;')
                    # Drop the county token from the original-case string.
                    parts = orig_after_jud.split(None, 1)
                    extra_seg = parts[1].strip(' .,;') if len(parts) > 1 else ''
                    if extra_seg:
                        segs.append(extra_seg)
                claimed.add(i)

    # 4. Known cities (whole-segment match) — preferred over plain county names.
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if sl in CITY_TO_COUNTY:
            cname, cnty = CITY_TO_COUNTY[sl]
            if not city:
                city = cname
            if not province:
                province = cnty
            claimed.add(i)
        elif sl.startswith(('orasul ', 'oras ', 'municipiul ', 'comuna ')):
            cname = re.sub(r'^(orasul|oras|municipiul|comuna)\s+', '',
                           s, flags=re.I).strip()
            if not city:
                city = cname
                cn = _norm(cname)
                if cn in CITY_TO_COUNTY and not province:
                    province = CITY_TO_COUNTY[cn][1]
            claimed.add(i)

    # 5. Standalone county name — sets province; sets city as last-resort
    #    only if no other city candidate is available.
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if sl in RO_COUNTIES:
            if not province:
                province = RO_COUNTIES[sl]
            # Defer claiming so step 9 (unclaimed → city) can pick a real city.
            # If no other unclaimed plain segment can fill city, fall back to
            # the county name as city.
            other_city_candidate = any(
                j not in claimed and j != i
                and not re.fullmatch(r'[\d\s.\-]+', t)
                and '@' not in t
                and 'judet' not in _norm(t) and not _norm(t).startswith('jud')
                and not _norm(t).startswith('cod')
                and not _looks_like_street(_norm(t))
                and not _looks_like_apt(_norm(t))
                for j, t in enumerate(segs)
            )
            if not other_city_candidate and not city:
                city = RO_COUNTIES[sl]
            claimed.add(i)

    # 5. Multi-token "City County" segments
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        tokens = sl.split()
        if len(tokens) >= 2 and tokens[-1] in RO_COUNTIES and not province:
            province = RO_COUNTIES[tokens[-1]]
            rest = ' '.join(tokens[:-1])
            if not city:
                if rest in CITY_TO_COUNTY:
                    city = CITY_TO_COUNTY[rest][0]
                else:
                    orig_tokens = s.split()
                    city = ' '.join(orig_tokens[:-1])
            claimed.add(i)

    # 6. Street
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if not address1 and _looks_like_street(sl):
            address1 = s
            claimed.add(i)

    # 7. Standalone street-number "nr.X" follow-on → append to address1
    if address1:
        for i, s in enumerate(segs):
            if i in claimed:
                continue
            sl = _norm(s)
            if re.fullmatch(r'nr\.?\s*\d.*', sl):
                address1 = (address1 + ' ' + s).strip()
                claimed.add(i)

    # 8. Apartment-detail follow-ons
    if address1:
        for i, s in enumerate(segs):
            if i in claimed:
                continue
            sl = _norm(s)
            if _looks_like_apt(sl):
                if s not in addr2:
                    addr2.append(s)
                claimed.add(i)

    # 9. Unclaimed → city if still empty (skip emails / digits / 'cod ...' / 'jud...')
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if (
            not city and sl
            and not re.fullmatch(r'[\d\s.\-]+', s)
            and 'judet' not in sl and not sl.startswith('jud')
            and not sl.startswith('cod')
            and '@' not in s
        ):
            city = s.strip(' .,')
            claimed.add(i)
            break

    # 10. Unclaimed plain segments → address1 (tail or initial)
    for i, s in enumerate(segs):
        if i in claimed:
            continue
        sl = _norm(s)
        if 'judet' in sl or sl.startswith('jud') or sl.startswith('cod'):
            continue
        if '@' in s:
            continue
        if re.fullmatch(r'[\d\s.\-]+', s):
            continue
        if not address1:
            address1 = s
        else:
            address1 = (address1 + ' ' + s).strip()
        claimed.add(i)

    # 10b. Province-only fallback: if we know the județ but no city, set the
    #      county capital. DPD corrects to street level on the label.
    if province and not city:
        cap = COUNTY_CAPITAL.get(province)
        if cap:
            city = cap

    # 11. ZIP fallback
    if not zipc:
        if city == 'București' and sector:
            zipc = ZIP_FALLBACK.get(f'București-Sector {sector}')
        elif province in ZIP_FALLBACK:
            zipc = ZIP_FALLBACK[province]
        elif city in ZIP_FALLBACK:
            zipc = ZIP_FALLBACK[city]

    return {
        'address1': address1,
        'address2': ', '.join(addr2) or None,
        'city': city,
        'province': province,
        'zip': zipc,
        'countryCode': 'RO',
    }


if __name__ == '__main__':
    import json
    cases = [
        'Bucuresti,sector 3,Drumul gura Făgetului 107,et.3,ap.18',
        'Brașov,str.Hărmanului nr.2,bl.2, bis scara B,ap 40.',
        'Orașul Măcin,județul Tulcea,str.Grigore  Moisil nr.5 A',
        'Vrancea,Focșani,str.Mare a Unirii 10, Salon Victoria,cod p.620011',
        'Galați, Smardan, str.Regina Maria 17',
        'Str.Frunzei nr.25, bl.F7,sc.3, et.1, ap.67',
        'Jud.Timis,Mosnita Noua,str.Berna 26. 0767337313, andatrif98@iclod.com',
        'str.Principala nr.260,oras Varbilau,Judetul Prahova. 0787547186',
        'Iasi,str.Ion Irimescu,nr.12,apartamet 1,parter. 0743116123',
        'Bucuresti,sector 4,str.Anton Bacalbasa nr.13,bloc 118,scara A,ap.26,etaj 4. 0770315885',
        ' Craiova,Dolj,Bulevardul Dacia nr 167 blocul 32 IV A1,scara 1,ap.14. 0736084664',
        'Cluj Napoca,Comuna Floresti,cod postal 407280, Tineretului 9D,stratulat370@gmail.com     0771039642',
        'Ploiești Prahova,str.al Strejnic, nr.3,bloc a13,scara A,ap.20',
        'București, sec.1, cod poștal 012432, str.Navigației 29                      0760809894',
    ]
    for c in cases:
        print(c)
        print(' ', json.dumps(parse_address(c), ensure_ascii=False))
        print()
