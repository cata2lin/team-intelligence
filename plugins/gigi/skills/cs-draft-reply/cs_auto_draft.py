# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000"]
# ///
"""
cs_auto_draft.py — FLOW de auto-DRAFT + triaj/escaladare + acțiuni propuse, pe coada Richpanel.

Pentru fiecare tichet OPEN care AȘTEAPTĂ RĂSPUNS DE LA NOI (ultimul mesaj e de la client):
  1. IDENTIFICĂ problema (pas LLM de triaj — NU doar regex): problemă concretă, categorie,
     limbă, severitate, dacă trebuie ESCALADAT (+motiv), acțiune sugerată pt CS, și eventuala
     acțiune executabilă (modify/cancel/swap/resend) cu parametri.
  2. Pune TOTUL cap la cap: platformă (channel→stil), identitate + TOATE comenzile
     (customer-identity), UNDE A MAI SCRIS (convos cross-canal), sentiment.
  3. NORMAL → DRAFT în vocea agenților, adaptat platformei (public FB/IG = scurt, fără date
     personale, invită în privat; email = complet+semnătură).
  4. ESCALADARE (ANPC/juridic, refund promis-neefectuat, client foarte supărat/repetat, VIP) →
     NU rezolvă automat: draft scurt de AȘTEPTARE + rutare în Richpanel ca s-o vadă CS ușor —
     prioritate HIGH + tag escaladare/de-sunat + NOTĂ-BRIEF cu tot contextul + acțiune sugerată.
  5. ACȚIUNE (modify/cancel/swap/resend) — model PROPUNE+APROBĂ: verifică PRE-FULFILLMENT,
     reconciliază comanda referită cu comenzile reale, rulează cs-actions DRY-RUN. NU aplică
     până nu aprobi cu --approve. Draftul confirmă acțiunea ca FĂCUTĂ doar după aplicare.

⚠️ Răspunsul către client rămâne DOAR DRAFT (niciodată trimis). Rutarea de escaladare e internă
(prioritate/tag/notă — niciun mesaj la client). Scrierile în Richpanel se fac doar cu --create-draft.

  uv run cs_auto_draft.py                          # DRY-RUN: identifică+draft+escaladări+propuneri, nimic scris
  uv run cs_auto_draft.py --channel email --limit 8
  uv run cs_auto_draft.py --actions modify,cancel  # ce acțiuni sunt active (restul doar draft)
  uv run cs_auto_draft.py --create-draft           # scrie DRAFTURI + rutare escaladare (NU trimite, NU aplică acțiuni)
  uv run cs_auto_draft.py --approve 273790 --agent Oana   # aplică acțiunea propusă la un tichet + draft

LLM: ANTHROPIC_API_KEY (Claude) dacă există în KB, altfel OPENAI_API_KEY. Model: env DRAFT_MODEL.
"""
import os, re, sys, json, base64, unicodedata, subprocess, urllib.request, urllib.parse, urllib.error, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")

# cs-photo = modulul CANONIC de „vedere" a pozelor (poza CLIENTULUI + poza RECLAMEI de la comentariu, cu registru).
# Acest flow îl REFERENȚIAZĂ. Fallback pe logica locală (describe_photos) dacă nu e pe path (ex. VPS înainte de deploy).
try:
    sys.path.insert(0, os.path.join(HERE, "..", "cs-photo"))
    import cs_photo as _csp
except Exception:
    _csp = None
CI = os.path.join(HERE, "..", "customer-identity", "customer_identity.py")
CSA = os.path.join(HERE, "..", "cs-actions", "scripts", "cs_actions.py")
QUEUE = os.path.join(HERE, ".auto_draft_proposals.json")
MCP_URL = "https://mcp.richpanel.com/mcp"

PLATFORM = {
    "email": ("Email", "răspuns COMPLET cu salut + semnătură; date de comandă OK (privat)."),
    "chat": ("Chat live pe site", "conversațional, scurt; date de comandă OK (privat)."),
    "messenger": ("Facebook Messenger", "conversațional, prietenos; privat → date OK."),
    "instagram_dm": ("Instagram DM", "conversațional, prietenos; privat → date OK."),
    "sms": ("SMS", "FOARTE scurt, fără semnătură lungă; privat → date OK."),
    "facebook_feed_comment": ("Comentariu public Facebook", "PUBLIC, SCURT (1-2 fraze), cald, maximum 1-2 emoji DOAR daca in context scrie EMOJI: permise, POLITICOS in registrul formal AL LIMBII (vezi REGISTRU in context — pl/hu NU sunt la plural); NU expune comanda/AWB/telefon. Pozitiv/lauda -> multumire calda. Intrebare/reclamatie -> raspuns scurt + INVITA CLIENTUL sa ne SCRIE in privat (inbox/Messenger) SAU sa ne SUNE la TELEFON_COMANDA (daca e dat). NU spune 'v-am scris noi in privat' (nu trimitem noi DM)."),
    # Richpanel trimite `facebook_message` / `instagram_message` / `email_from_widget`, NU
    # `messenger` / `instagram_dm` / `email`. Fără aliasurile astea, TREI din cele cinci canale ale
    # cronului cădeau pe fallback-ul generic („ton prietenos, la obiect") — adică fără regula de
    # canal privat care spune că datele de comandă se pot da.
    "facebook_message": ("Facebook Messenger", "conversațional, prietenos; privat → date OK."),
    "instagram_message": ("Instagram DM", "conversațional, prietenos; privat → date OK."),
    "email_from_widget": ("Email (formular site)", "răspuns COMPLET cu salut + semnătură; date de comandă OK (privat)."),
    "instagram_comment": ("Comentariu public Instagram", "PUBLIC, SCURT (1-2 fraze), cald, maximum 1-2 emoji DOAR daca in context scrie EMOJI: permise, POLITICOS in registrul formal AL LIMBII (vezi REGISTRU in context — pl/hu NU sunt la plural); NU expune comanda/AWB/telefon. Pozitiv/lauda -> multumire calda. Intrebare/reclamatie -> raspuns scurt + INVITA CLIENTUL sa ne SCRIE in privat (DM) SAU sa ne SUNE la TELEFON_COMANDA (daca e dat). NU spune 'v-am scris noi in privat' (nu trimitem noi DM)."),
}
PAGE_STORE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
    "61586834387211": "Lab Noir",
    # Piețele străine. Cele trei Duppo de mai jos NU sunt în tokenul nostru Meta (eroare #10 =
    # pagina există dar nu e acordată system-userului), deci nu le putem citi postarea sau ascunde
    # comentarii — DAR maparea aici e tot ce-i trebuie draftului ca să știe brandul și limba.
    "814175968452902": "Duppo BG",        # 189 tichete, prefix DUPBG — cea mai mare sursă străină
    "516792924847762": "Duppo SK",        # 24 tichete
    "425006144024872": "Duppo HU",        # 20 tichete
    "622653260933284": "Duppo Hungary",
    "617080764812731": "Duppo Czechia",
    "575422458989566": "Duppo Moldova",
    "103675612509107": "Bonhaus",
    "425122607349368": "Nocturna Lux",
    # Găsite 15-sep numărând comentariile OPEN pe pagină: apăreau ca „(nemapat)", deci ieșeau
    # din AI cu store_name = "magazinul nostru" — fără limbă, fără telefon, fără brand în semnătură.
    "575484808985734": "Covoria",          # 26 comentarii OPEN
    "628544790345906": "Genti Promo", "704271086093669": "Ce-Pat-Ai",
    "506398435900401": "Produse Bisericesti", "132189989971450": "Stemma",
    "606625622532373": "Super Detergent", "104553898590313": "Unelte Potrivite",
    "115983611500696": "Manscout", "122095975544011424": "Rossi Nails",
}
# Piețele pe care AI-ul NU răspunde (decizie de owner, 15-sep-2026): Moldova și Cehia.
# Restul piețelor străine — BG, HU, SK, PL, HR — sunt ACTIVE.
# Se poate anula cu --include-skipped, dar atunci spune-i ownerului.
AI_SKIP_STORES = {"Duppo Moldova", "Duppo Czechia", "Bonhaus CZ"}
ORDER_PFX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento", "GRAN": "Grandia",
             "GRAND": "Grandia", "BELA": "Belasil", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
             "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "CARP": "Carpetto",
             "COV": "Covoria", "APR": "Apreciat", "ROSSI": "Rossi Nails",
             "DUPBG": "Duppo BG", "NOC": "Nocturna",
             # adaugate 15-sep-2026, DERIVATE DIN DATE (AWBprint: `orders.order_number` + `stores.name`),
             # nu din memorie. Doar cele al caror brand se poate confrunta cu dictionarele de mai sus.
             "LUX": "Nocturna Lux", "LAB": "Lab Noir", "BG": "Nocturna BG", "MD": "Duppo Moldova"}
# INVENTARUL REAL de prefixe, derivat din AWBprint (`SELECT DISTINCT substring(order_number from '^[A-Za-z]+')`),
# nu din memorie. Lipseau 10 din 27 — 49.264 de comenzi reale (LUX 18.338, NUBRA 14.657, PAT 5.409, BG 5.077,
# LAB 4.325, SK/ESTP/ORC/HU/MD) pe care motorul nu le vedea DELOC: nici redactate public, nici cautate in DB,
# nici legate de registrul de incidente. Ordinea e LONGEST-FIRST pe radacini comune (ESTP>EST, NUBRA>NUB,
# GRAND>GRAN, BONBG>BON) ca group(1) sa iasa brandul corect, nu doar sa se potriveasca ceva.
# ⚠️ Cand se lanseaza un magazin nou, prefixul lui TREBUIE adaugat aici — altfel comenzile lui ies
# nedetectate public. Testul `r3_test_prefixe-si-awb.py` semnaleaza automat prefixele noi din AWBprint.
ORDER_PFX_RE = ("ESTP|EST|GT|NUBRA|NUB|GRAND|GRAN|MAG|OFER|RED|DUPBG|BONBG|BON|NOC|LUX|LAB|PAT|ORC"
                "|ROSSI|BELA|GEN|CARP|COV|APR|BG|CZ|PL|SK|HU|MD")
# `(?<!\w)(?<!\w-)` in loc de `\b`: `\b` lasa sa treaca codurile de facturare marketplace de forma
# „C-MKTP-HU-123456" (18 aparitii reale in tichete) ca si cum ar fi comenzi HU. Masurat pe corpusul
# real de 265.195 tichete: garda costa 1 potrivire adevarata si scoate toata clasa aia.
ORDER_RE = re.compile(r"(?<!\w)(?<!\w-)(" + ORDER_PFX_RE + r")[ -]?(\d{4,7})\b", re.I)
# granita de CIFRA, nu de CUVANT: `\b` nu exista intre litera si cifra, deci „AWB0123456789" / „tel0721234567"
# treceau NEATINSE prin redactarea publica. Masurat pe comentariile publice reale: 21 siruri de 10-16 cifre
# ramaneau in text dupa redactare, acum 0. Costa +1,9% candidati pe calea de lookup (un AWB inexistent
# nu se gaseste in DB, deci nu schimba nimic functional).
AWB_RE = re.compile(r"(?<!\d)\d{10,16}(?!\d)")   # număr de AWB menționat în mesaj (curier) — pt căutare în profit_orders
# Expeditori care NU sunt CLIENȚI (curier/finanțe/sistem) → NU draftăm răspuns de client; se exclud (ca spam).
# Ex: backline-tichet@dpd.ro (confirmare automată), alina.cenuse@dpd.ro („Acord de compensare COD"). Clienții nu scriu de pe @dpd.ro.
NON_CUSTOMER_SENDER_RE = re.compile(
    r"@(dpd\.[a-z]{2,3}|sameday\.ro|econt\.(com|bg)|packeta\.(com|ro)|cargus\.ro|fancourier\.ro|posta-romana\.ro|gls-group\.[a-z]+|nemo-express\.ro"   # curieri
    r"|([a-z0-9-]+\.)?omegatheme\.com|consentik\.com|mailchimp(app)?\.com|klaviyomail\.com|sendgrid\.net)\b",   # app-uri Shopify / notificări automate (rapoarte) — niciodată clienți
    re.I)
# Notificări automate judge.me (recenzii) — subiect „... left a N star review" (NU și replica clientului „Re: ⭐ ... cum ți s-a părut")
JUDGEME_NOTIF_RE = re.compile(r"left (a|the following) \d+ star review", re.I)
# Subiecte-template de rapoarte/notificări SaaS/social — niciodată mesaje reale de client, indiferent de model LLM
SAAS_NOISE_SUBJ_RE = re.compile(
    r"weekly .*(report|performance)|performance report is ready|where to optimize next|track what.?s working"
    r"|consent banner performed|cookie banner|how your .* performed|your .* (report|summary) is ready"
    r"|mentioned you on|liked your|started following|new follower|tagged you|răspunde pe (tiktok|instagram|facebook)", re.I)
# Bounce / eșec livrare email (mailer-daemon) — NU e client
BOUNCE_RE = re.compile(
    r"address not found|wasn.?t delivered|delivery status notification|undelivered mail|mail delivery (failed|subsystem)"
    r"|delivery has failed|message (couldn.?t|could not) be delivered|returned to sender|nu a putut fi livrat", re.I)


def _padded_noise(s):
    """Mesaj format majoritar din caractere invizibile/padding (newslettere/notificări SaaS) — nu e text real de client."""
    if not s or len(s) < 60:
        return False
    junk = sum(1 for c in s if c.isspace() or c in "͏​‌‍­⁠﻿" or unicodedata.category(c) == "Cf")
    return junk / len(s) > 0.55


ANGER_RE = re.compile(r"!!!|\b(escroc|hoti|hotie|hotilor|teap[ăa]|tepui|inselat|inselaciune|bataie de joc|rusine|jignit|inadmisibil|nesimti|incompeten|scandalos|dezgustator)\b"
                      # aceleași insulte pe piețele străine — fără ele „измамници" / „csalás" /
                      # „podvodníci" / „oszuści" treceau ca mesaj calm. Textul vine prin deacc()
                      # (lowercase + diacritice pliate), deci latina se scrie FĂRĂ diacritice.
                      # NU pune aici „klam" simplu: „zklamaná"/„sklamaný" (dezamăgit) l-ar prinde.
                      r"|\b(?:измам|крадц|крадец|подигра|срам|безобрази|недопустим|отврат|нагло|лъжет|лъжц|скандал"
                      r"|csalas|csalok|atverte|atveres|szegyen|felhaborito|botranyos|undorito|hazud|hazugsag|pofatlan|gunyolod"
                      r"|podvod|okrad|hanba|nehorazn|skandal|znechuc|klamete|klamiecie|klamstw|lzete|drzost|nechutne"
                      r"|oszust|zlodziej|wstyd|zenada|kpin|kpicie|obrzydliw|bezczelno|oburzajac"
                      r"|prevar|lopov|sramot|nepostenj|gnusn|odvratn|bezobrazluk|sprdnj)", re.I)


def _real_escalation(text):
    """Semnal REAL de escaladare: ANPC/juridic (ESCAL) SAU furie explicită (insulte/„țeapă") SAU mesaj majoritar MAJUSCULE."""
    dt = deacc(text or "")
    if ESCAL.search(dt) or ANGER_RE.search(dt):
        return True
    letters = [c for c in (text or "") if c.isalpha()]
    return len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7
# brand -> limba pieței (semnal SIGUR de limbă, mai fiabil decât detecția LLM pe comentarii scurte)
STORE_LANG = {
    "Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg", "Bonhaus HU": "hu",
    "Bonhaus SK": "sk", "Bonhaus HR": "hr",
    "Duppo BG": "bg", "Duppo HU": "hu", "Duppo Hungary": "hu", "Duppo SK": "sk",
    "Duppo Czechia": "cz", "Duppo Moldova": "ro",
    "Nocturna BG": "bg", "Nocturna PL": "pl",
}
# Piețele ROMÂNEȘTI n-aveau plasă. Lanțul de limbă e
#   detect_lang(cust_txt) or STORE_LANG.get(store_name) or idn["language"] or "ro"
# iar pe brandurile de mai jos pasul 2 LIPSEA: când clientul scria fără diacritice (cum scrie de
# pe telefon), detect_lang întoarce None și limba o decidea GHICITUL LLM-ului de triaj. Măsurat pe
# oglinda REALĂ de tichete (265.195): 173.873 de tichete pe 27 de branduri fără intrare, din care
# 56.702 (32,6%) cu detect_lang NEDECIS — adică 56.702 tichete în care piața CUNOSCUTĂ nu conta.
# În plus enumerarea cerută LLM-ului la triaj e „ro|cz|pl|bg|en" (fără hu/sk/hr), deci pe jumătate
# din limbi ghicitul nici n-avea ce răspunde corect.
# Lista NU e din memorie: fiecare brand are fie domeniu .ro (AWBprint `stores.name` / STORE_URL /
# EMAIL_BRAND), fie trafic MĂSURAT în oglindă cu diacritice românești dominante și ZERO chirilic
# (ex. pagina „Bonhaus" 86/157, „Nocturna Lux" 4.896/7.305, „Genti Promo" 464/709).
# ⚠️ Plasa asta e motivul pentru care `nocturna.bg` TREBUIE mapat explicit în EMAIL_BRAND (mai jos):
# altfel „Nocturna BG" cădea pe brandul ROMÂNESC „Nocturna" și primea, acum, registru ROMÂNESC.
# ⚠️ Intră DOAR brandurile care au și RUTĂ DE CONTACT (STORE_URL sau STORE_PHONE). Poarta de
# pornire (`poarta_pornire.py`, verificarea C4) cere ca orice brand din `EMAIL_BRAND ∪ STORE_LANG`
# să aibă rută — deci un brand fără rută adăugat aici ar face poarta ROȘIE. Cele 8 pagini
# rămase pe dinafară (Bonhaus, Ce-Pat-Ai, Genti Promo, Super Detergent, Produse Bisericesti,
# Unelte Potrivite, Stemma, Manscout) n-au NICI telefon, NICI site: ele au nevoie întâi de o rută
# verificată LIVE, nu de o limbă. „Orasul Verde" e deja pe lista de excepții acceptate a porții.
_BRANDURI_RO = (
    "Esteban", "George Talent", "Nubra", "Grandia", "Belasil", "Gento", "Apreciat",
    "Carpetto", "Covoria", "Nocturna", "Nocturna Lux", "Casa Ofertelor", "Bonhaus RO",
    "Ofertele Zilei", "Reduceri bune", "Magdeal", "Rossi Nails", "Lab Noir", "Orasul Verde",
)
STORE_LANG.update(dict.fromkeys(_BRANDURI_RO, "ro"))
# brand (store_name) -> linia de CS/comenzi PUBLICATĂ de magazin. Reverificată 15-sep-2026 din
# `tel:` din bara „Sună-ne la …" + pagina de contact, confruntată cu numărul pe care îl dau agenții
# în răspunsurile reale (oglinda CS). Numerele vechi erau greșite pe ROL, nu doar învechite:
# `0732 781 468` e WhatsApp-ul (`wa.me/40732781468`) și telefonul de pe adresa de retur — nu o linie  pii-ok: număr public al firmei, citat ca exemplu de număr GREȘIT
# de voce; seria `0729 748 9xx` e cea VECHE, înlocuită peste tot cu `0376 300 xxx` (singurul magazin
# care o mai publică e Rossi Nails). Un număr FORMABIL dar al altei linii trece de `phone_ok` —
# de-aia sursa e site-ul magazinului, nu memoria.
# pii-ok: linii de Customer Service PUBLICATE de magazine (bara de contact), nu date de client.
STORE_PHONE = {
    "Esteban": "0376 300 843", "George Talent": "031 630 6855",
    "Nubra": "0376 300 509", "Grandia": "0376 300 844",
    "Belasil": "0376 300 586", "Gento": "0376 300 585", "Apreciat": "0376 300 585",
    "Carpetto": "0376 300 464", "Covoria": "0376 300 464", "Rossi Nails": "0729 748 943",  # pii-ok: numere publice de CS
    # prefixul BON („Bonhaus RO") e magazinul Casa Ofertelor → aceeași linie.
    "Casa Ofertelor": "0376 300 554", "Bonhaus RO": "0376 300 554",
    "Magdeal": "0376 300 376", "Ofertele Zilei": "0376 300 573",
    "Reduceri bune": "0376 300 355", "Lab Noir": "0376 300 592",
    "Nocturna": "0376 300 533", "Nocturna Lux": "0376 300 533",
    "Bonhaus BG": "0885493926", "Bonhaus CZ": "+420 724 216 967",
    # ⛔ „Bonhaus PL" ȘTERS. Numărul publicat pe bonhaus.pl (`tel:0376300646`) e serie ROMÂNEASCĂ
    # 037x (alocare ANCOM): nu se poate forma din Polonia sub nicio interpretare, iar în capătul
    # lui răspunde echipa din RO. Nu există linie poloneză reală, deci nici „+40 376 300 646 (tarif
    # internațional)" nu e un răspuns — l-ar costa pe client ca să ajungă la cineva care nu-i
    # vorbește limba. Fără telefon → deflectăm în privat (Messenger) + pe site (STORE_URL).
    "Nocturna BG": "0876813240",
}
# brand (store_name) -> țara pieței (aceleași chei ca STORE_LANG). Restul brandurilor = RO.
# Țara NU se deduce din limbă: Duppo Moldova scrie românește, dar piața lui e MD.
STORE_CC = {
    "Bonhaus CZ": "CZ", "Bonhaus PL": "PL", "Bonhaus BG": "BG", "Bonhaus HU": "HU",
    "Bonhaus SK": "SK", "Bonhaus HR": "HR",
    "Duppo BG": "BG", "Duppo HU": "HU", "Duppo Hungary": "HU", "Duppo SK": "SK",
    "Duppo Czechia": "CZ", "Duppo Moldova": "MD",
    "Nocturna BG": "BG", "Nocturna PL": "PL",
}
# Prefixele poloneze se ENUMERĂ, pentru că acolo discriminantul e PERECHEA de cifre: Polonia n-are
# prefix de trunchi, are fix 9 cifre, iar seria 37x nu există deloc. O regulă de tip „prima cifră
# 4-8" descrie doar seriile MOBILE și ar fi lăsat să treacă exact numărul care ne-a ars.
_PL_PFX = frozenset((
    "12 13 14 15 16 17 18 22 23 24 25 29 32 33 34 41 42 43 44 46 48 52 54 55 56 58 59 "
    "61 62 63 65 67 68 71 74 75 76 77 81 82 83 84 85 86 87 89 91 94 95 "     # geografice
    "45 50 51 53 57 60 66 69 72 73 78 79 88").split())                       # mobile
# țară -> (prefix internațional, prefix de trunchi, verificator pe numărul NAȚIONAL)
# HU are trunchiul „06" (nu „0"), iar PL și CZ n-au trunchi deloc — de aia e șir, nu boolean.
PHONE_PLAN = {
    "RO": ("40", "0", lambda n: len(n) == 9 and n[0] in "237"),
    "BG": ("359", "0", lambda n: (len(n) == 9 and n[:2] in ("87", "88", "89", "98", "99"))
                                 or (len(n) == 8 and n[0] in "23456789")),
    "CZ": ("420", "", lambda n: len(n) == 9 and n[0] in "23456789"),
    "SK": ("421", "0", lambda n: len(n) == 9 and n[0] in "23459"),
    "HU": ("36", "06", lambda n: (len(n) == 8 and n[0] in "123456789")
                                 or (len(n) == 9 and n[:2] in ("20", "30", "31", "50", "70"))),
    "PL": ("48", "", lambda n: len(n) == 9 and n[:2] in _PL_PFX),
    "HR": ("385", "0", lambda n: (len(n) == 8 and n[0] == "1")
                                 or (len(n) in (8, 9) and "20" <= n[:2] <= "53")
                                 or (len(n) == 9 and n[:2] in ("91", "92", "95", "97", "98", "99"))),
    "MD": ("373", "0", lambda n: len(n) == 8 and n[0] in "23678"),
}


def _nsn(raw, cc):
    """Numărul NAȚIONAL: fără separatori, fără prefix internațional, fără prefixul de trunchi.
    Prefixul internațional al ALTEI țări → „" (un +40 nu e număr formabil local în Polonia)."""
    d = re.sub(r"[^\d+]", "", raw or "")
    pfx, trunk, _ = PHONE_PLAN.get(cc, ("", "", None))
    if d.startswith("00"):
        d = "+" + d[2:]
    if d.startswith("+"):
        return d[1 + len(pfx):] if d[1:].startswith(pfx) else ""
    return d[len(trunk):] if (trunk and d.startswith(trunk)) else d


def tel_block(store, phone_order, wants_contact, wants_callback):
    """Linia TELEFON_COMANDĂ din prompt. „NU are linie telefonică" e adevărat DOAR pe piețele străine
    fără număr local (Duppo BG/HU/SK, Bonhaus PL). Un magazin ROMÂNESC căruia nu i-am trecut numărul în
    STORE_PHONE ARE telefon — măsurat: Magdeal, Ofertele Zilei, Reduceri bune, Nocturna, Nocturna Lux și
    Lab Noir primeau afirmația asta FALSĂ pe orice comentariu public, doar fiindcă lipsea o cheie."""
    if not wants_contact or wants_callback:
        return ""
    if phone_order:
        return "\nTELEFON_COMANDĂ: %s" % phone_order
    if STORE_CC.get(store, "RO") != "RO":
        return ("\nTELEFON_COMANDĂ: nu avem — magazinul NU are linie telefonică pe piața lui. NU da niciun "
                "număr și NU scrie „sunați-ne”: invită clientul să ne scrie în privat (Messenger/DM/e-mail).")
    return ("\nTELEFON_COMANDĂ: nu apare în context — NU inventa și NU scrie niciun număr de telefon. "
            "Îndrumă clientul pe site sau invită-l să ne scrie în privat (Messenger/DM/e-mail).")


def phone_ok(store, phone):
    """Se poate FORMA numărul din piața magazinului? (planul de numerotație al țării)"""
    cc = STORE_CC.get(store, "RO")
    plan = PHONE_PLAN.get(cc)
    return bool(plan and plan[2](_nsn(phone, cc)))


# GARDA care ar fi prins defectul din start: un număr care nu respectă planul pieței NU ajunge în
# prompt (și se logează). Fără ea am dat unei cliente poloneze un număr ROMÂNESC de la care aștepta
# să fie sunată.
_STORE_PHONE_BAD = {s: p for s, p in STORE_PHONE.items() if not phone_ok(s, p)}
for _s, _p in _STORE_PHONE_BAD.items():
    print("⚠️  STORE_PHONE[%s] = %s nu respectă planul de numerotație %s → NU se injectează în prompt"
          % (_s, _p, STORE_CC.get(_s, "RO")), file=sys.stderr)
STORE_PHONE = {s: p for s, p in STORE_PHONE.items() if s not in _STORE_PHONE_BAD}
# Numerele NOASTRE de firmă NU sunt halucinație — agenții reali le dau zilnic clienților. Lista albă =
# telefoanele din STORE_PHONE + seria de call-center ARONA „0376 300 xxx" (vezi nota de la Bonhaus PL:
# 037x e alocare ANCOM, iar în capătul ei răspunde echipa din RO). Măsurat pe 137 de răspunsuri REALE
# de agent în română: 9 fals-pozitive „telefon inventat" dispar, niciun număr de CLIENT nu e albit.
_OUR_PHONE_NSN = {re.sub(r"\D", "", p)[-9:] for p in STORE_PHONE.values()}
_OUR_PHONE_PFX = ("376300",)


def our_phone(raw):
    """Numărul e unul DE-AL NOSTRU (publicat pe site / call-center), deci legitim într-un răspuns."""
    d = re.sub(r"\D", "", raw or "")[-9:]
    return bool(d) and (d in _OUR_PHONE_NSN or d.startswith(_OUR_PHONE_PFX))
# brand (store_name) -> domeniul public. Verificat 15-sep-2026: `<shop>.myshopify.com` → redirect
# 301 + <link canonical> (registrul de magazine = `gigi:shopify-stores`, stores.csv). Fără harta
# asta, la „vreau să comand" draftul spunea „comandați de pe site" FĂRĂ link — modelul n-avea de
# unde să știe domeniul.
STORE_URL = {
    "Esteban": "esteban.ro", "George Talent": "george-talent.ro", "Nubra": "nubra.ro",
    "Grandia": "grandia.ro", "Belasil": "belasil.ro", "Gento": "gento.ro",
    "Carpetto": "carpetto.ro", "Covoria": "covoria.ro", "Rossi Nails": "rossinails.ro",
    "Apreciat": "apreciat.ro", "Magdeal": "magdeal.ro", "Ofertele Zilei": "ofertelezilei.ro",
    "Reduceri bune": "reduceribune.ro", "Nocturna": "nocturna.ro",
    "Nocturna Lux": "nocturnalux.ro", "Lab Noir": "labnoir.ro",
    # ⚠️ prefixul BON („Bonhaus RO") e magazinul CASA OFERTELOR — „bonhaus.ro" nici nu există.
    "Casa Ofertelor": "casaofertelor.ro", "Bonhaus RO": "casaofertelor.ro",
    "Bonhaus BG": "bonhaus.bg", "Bonhaus CZ": "bonhaus.cz", "Bonhaus PL": "bonhaus.pl",
    "Bonhaus HU": "bonhaus.hu", "Bonhaus SK": "bonhaus.sk",
    # Duppo: vitrina maghiară s-a MUTAT de pe duppo.hu (DNS mort azi) pe hu.duppo.eu, cea cehă pe
    # cz.duppo.eu, iar bg.duppo.eu redirectează pe duppo.bg. Ambele pagini FB maghiare → aceeași vitrină.
    "Duppo BG": "duppo.bg", "Duppo HU": "hu.duppo.eu", "Duppo Hungary": "hu.duppo.eu",
    "Duppo Czechia": "cz.duppo.eu", "Duppo Moldova": "duppo.md",
}
# Fără domeniu VERIFICAT nu inventăm unul: Bonhaus HR, Orasul Verde, Nocturna BG/PL și Duppo SK nu
# rezolvă DNS / sunt închise → rămân fără link, iar promptul nu trimite clientul „pe site".
# brand din domeniul adresei magazinului (pt email, când nu avem 360/orders) — ex. contact@esteban.ro → Esteban
EMAIL_BRAND = {
    "esteban.ro": "Esteban", "george-talent.ro": "George Talent", "nubra.ro": "Nubra",
    "grandia.ro": "Grandia", "labnoir.ro": "Lab Noir", "belasil.ro": "Belasil",
    "gento.ro": "Gento", "carpetto.ro": "Carpetto", "covoria.ro": "Covoria",
    "rossinails.ro": "Rossi Nails", "apreciat.ro": "Apreciat", "casaofertelor.ro": "Casa Ofertelor",
    "magdeal.ro": "Magdeal", "ofertele-zilei.ro": "Ofertele Zilei", "reduceribune.ro": "Reduceri bune",
    "orasulverde.ro": "Orasul Verde", "nocturna.ro": "Nocturna",
    # Cutiile străine. ⚠️ 23 de adrese cad pe 17 cutii fizice (aliasuri) — brandul se ia din
    # adresa DIN ANTET (Delivered-To), nu din cutia-gazdă, altfel bonhaus.hu apare ca trynocturna.
    "bonhaus.bg": "Bonhaus BG", "bonhaus.cz": "Bonhaus CZ", "bonhaus.pl": "Bonhaus PL",
    "bonhaus.hu": "Bonhaus HU", "bonhaus.sk": "Bonhaus SK", "bonhaus.hr": "Bonhaus HR",
    "trynocturna.eu": "Nocturna", "nocturna.pl": "Nocturna PL", "duppo.md": "Duppo Moldova",
    # Cutiile DUPPO străine. Domeniile de MAIL nu-s cele ale vitrinelor — citite LIVE (15-sep-2026)
    # de pe pagina de contact a fiecărei vitrine: duppo.bg ȘI bg.duppo.eu publică `contact@duppo.eu`,
    # hu.duppo.eu publică `contact@duppo.hu`, cz.duppo.eu publică `contact@duppo.cz`. În oglinda LIVE
    # există deja `bulgaria@duppo.eu`. Trec și domeniile vitrinelor, ca să nu depindem de care adresă
    # e scrisă în antet.
    "duppo.eu": "Duppo BG", "bg.duppo.eu": "Duppo BG", "duppo.bg": "Duppo BG",
    "duppo.hu": "Duppo HU", "hu.duppo.eu": "Duppo HU",
    "duppo.cz": "Duppo Czechia", "cz.duppo.eu": "Duppo Czechia",
    # ⚠️ `nocturna.bg` LIPSEA, iar `root.title()` îl ducea tăcut pe brandul ROMÂNESC „Nocturna":
    # 409 tichete reale în oglindă, din care 331 scrise BULGĂREȘTE și 62 pe care detect_lang nu le
    # decide. Fără linia asta, plasa RO de mai sus le-ar fi dat celor 62 registru ROMÂNESC pe o
    # piață bulgară — adică ar fi înrăutățit exact ce repară.
    "nocturna.bg": "Nocturna BG",
    # varianta FĂRĂ cratimă e cea publicată azi (ofertelezilei.ro, HTTP 200); fără ea ieșea
    # brandul inventat „Ofertelezilei", care nu e în STORE_PHONE/STORE_URL/STORE_LANG.
    "ofertelezilei.ro": "Ofertele Zilei",
}
def brand_from_email(addr):
    m = re.search(r"@([\w.-]+)", (addr or "").lower())
    if not m:
        return None
    dom = m.group(1)
    if dom in EMAIL_BRAND:
        return EMAIL_BRAND[dom]
    if any(b in dom for b in ("shopify", "gmail", "yahoo", "ymail", "icloud", "hotmail", "outlook", "anaf", "judgeme", "facebook", ".tech")):
        return None
    root = dom.split(".")[0].replace("-", " ").strip()
    # ⛔ Un subdomeniu de DOUĂ litere e un COD DE ȚARĂ, nu un brand: „hu.duppo.eu" ieșea „Hu",
    # „cz.duppo.eu" → „Cz", „bg.duppo.eu" → „Bg". Un brand INVENTAT nu se regăsește în
    # STORE_LANG / STORE_PHONE / STORE_URL / STORE_CC, deci cade tăcut din limbă, telefon, link și
    # țară — mai bine NICIUN brand (rămâne „magazinul nostru") decât unul inventat.
    if len(root) <= 2:
        return None
    return root.title() if root else None
STORE_NORM = {"GRAND": "GRAN"}
CH_LABEL = {"facebook_feed_comment": "FB comentariu", "facebook_message": "FB mesaj", "messenger": "Messenger",
            "instagram_comment": "IG comentariu", "instagram_message": "IG mesaj", "instagram_dm": "IG DM",
            "email": "Email", "email_from_widget": "Email widget", "chat": "Chat", "sms": "SMS"}

# Pe lângă diacriticele RO pliem și pe cele CENTRAL-EUROPENE (pl/cz/sk/hu): clasificatoarele de
# mai jos (ESCAL/ANGER/NEG/POS) sunt scrise fără diacritice, iar clientul scrie de pe telefon la
# fel de des fără ca și cu. Chirilica NU se atinge — .lower() o normalizează corect singură.
DEACC = str.maketrans("ăâîșşțţ" + "ąáäćčďęéěíłĺľńňóôöőřŕśšťúůüűýźżž",
                      "aaissttt"[0:7] + "aaaccdeeeilllnnoooorrsstuuuuyzzz")
def deacc(s): return (s or "").lower().translate(DEACC)
def _f(v, d=0.0):
    """float defensiv — LLM-ul poate întoarce confidence ca string non-numeric; nu lăsăm să crape tot lotul."""
    try:
        return float(v)
    except Exception:
        return d

_CYR = re.compile(r"[Ѐ-ӿ]")
_HU_VOWELS = set("öüóúíéá")
_HU_WORDS = re.compile(r"\b(nem|hogy|egy|van|meg|m\u00e1r|csak|nagyon|k\u00f6sz\u00f6n\u00f6m|k\u00e9rem|rendel(?:\u00e9s|tem)|sz\u00e1ll\u00edt\u00e1s|term\u00e9k|ez|az|de|is)\b", re.I)


def detect_lang(text):
    """Detectează limba în care a scris CLIENTUL, după script/diacritice specifice. None dacă e ambiguu (ASCII)."""
    t = (text or "")
    if _CYR.search(t):
        return "bg"                       # chirilic → bulgară
    low = t.lower()
    # Ordinea CONTEAZĂ: fiecare set conține DOAR litere unice limbii, ca să nu se confunde între
    # ele. Cehă și slovacă împart č/š/ž/á/í/é — le separă ř/ů/ě (cz) vs ľ/ĺ/ŕ/ô/ä (sk).
    # Măsurat pe 877 de tichete unde piața e cunoscută din pagină/cutie: 99,3% corect
    # (613 corecte + 258 nedecise care cad pe STORE_LANG, 6 greșite — și alea sunt mesaje chiar
    # scrise în altă limbă decât piața, ex. un client care scrie cehește pe pagina slovacă).
    if "đ" in low:
        return "hr"                       # „đ" = literă croată/sârbă, nu apare în celelalte piețe
    if any(c in low for c in "łąężśźń"):
        return "pl"                       # litere specific poloneze („ć" scos: e și croată — decide pe cuvinte)
    if any(c in low for c in "řůě"):
        return "cz"                       # litere specific cehe
    if any(c in low for c in "ľĺŕôä"):
        return "sk"                       # litere specific slovace
    if any(c in low for c in "őű"):
        return "hu"                       # litere specific maghiare
    if any(c in low for c in "ăâîșțşţ"):
        return "ro"                       # diacritice românești
    # Maghiara fără ő/ű: é/á/ö/ü sunt împărțite cu alte limbi, deci singure nu decid. DAR vocalele
    # maghiare + cel puțin DOUĂ cuvinte funcționale maghiare sunt un semnal sigur. Măsurat pe
    # aceleași 877 de tichete: +5 detectate corect, ZERO greșeli noi (0,7% rămâne 0,7%).
    # Contează pentru un client maghiar care scrie pe o pagină NEmaghiară — acolo brandul nu-l salvează.
    if (set(low) & _HU_VOWELS) and len(_HU_WORDS.findall(low)) >= 2:
        return "hu"
    return lang_by_words(deacc(t))        # fără diacritice → cuvinte funcționale; None = ambiguu


# Cuvinte FUNCȚIONALE, pe limbă — semnalul care rămâne când clientul scrie FĂRĂ diacritice (scrisul
# real de pe telefon: pe tichetele noastre, ZERO% din mesajele cu diacritice mai erau recunoscute
# după ce le tăiam diacriticele). Se aleg DISCRIMINANȚI: ceha și slovaca deaccentuate sunt aproape
# identice, deci intră doar perechile care le separă (jsem/som, nebo/alebo, zbozi/tovar, kdy/kedy).
# Engleza are listă DOAR de cuvinte de om care scrie („hello", „i would like"), NU substantive de
# magazin („order/delivery/shipping"): alea apar și în textul preformatat al widget-ului de chat de
# pe magazinele ROMÂNEȘTI (15 tichete măsurate) și ar fi trimis un român la un răspuns în engleză.
LANG_WORDS = {
    "ro": r"\b(?:buna|multumesc|multumim|comanda|comand|comenzi|coletul|colet|astept|dumneavoastra|va rog|"
          r"pentru|este|sunt|cand|unde|doresc|vreau|as vrea|as dori|livrare|livrat|primit|produsul|produse|"
          r"salut|imi|mie|factura|retur|adresa|pret|costa|marimea|culoare|aveti|foarte|nu am)\b",
    "cz": r"\b(?:dekuji|dekuju|dekujeme|zbozi|nebo|jsem|jste|jsou|jak|kdy|neni|muzu|muzete|jeste|chci|budu|"
          r"vraceni|zasilka|zasilku|zasilky|dobry den|kolik|proc|velikost|prosim vas|mate|objednal jsem)\b",
    "sk": r"\b(?:dakujem|dakujeme|tovar|alebo|som|ste|su|ako|kedy|mozem|mozete|este|chcem|budem|vratenie|"
          r"zasielka|zasielku|zasielky|kolko|preco|velkost|mate|objednal som|dobry den)\b",
    "hr": r"\b(?:hvala|molim|narudzba|narudzbu|narudzbe|posiljka|posiljku|gdje|kada|nije|zelim|dostava|"
          r"racun|placanje|koliko|zasto|velicina|dobar dan|imate|narucio sam|narucila sam)\b",
    "pl": r"\b(?:dziekuje|dzieki|prosze|zamowienie|zamowienia|zamowien|zamowilam|zamowilem|przesylka|"
          r"przesylke|paczka|paczke|kiedy|gdzie|moge|jestem|czy|bardzo|witam|dzien dobry|zwrot|dostawa|"
          r"ile|dlaczego|rozmiar|pozdrawiam|sie|juz|kupilam|kupilem|wiadomosc|numer zamowienia)\b",
    "hu": r"\b(?:koszonom|koszonjuk|koszi|kerem|kerdes|rendeles|rendelest|rendelesem|rendeltem|csomag|"
          r"csomagot|szallitas|termek|mikor|hol|hogy|nem|van|meg|mar|csak|vissza|szamla|mennyibe|meret|"
          r"szeretnek|elnezest|udvozlettel|jo napot)\b",
    "en": r"\b(?:hello|hi there|thank you|thanks|please|i want|i would like|i'd like|could you|can you|"
          r"where is my|when will my|do you have|i have not|i haven't|i didn't|my name is|best regards|"
          r"kind regards|i am|i'm|we are|dear sir|dear madam)\b",
}
LANG_WORDS = {k: re.compile(v, re.I) for k, v in LANG_WORDS.items()}


def lang_by_words(low):
    """Limba după cuvinte funcționale distincte (text DEJA deaccentuat). None dacă nu e clar.
    Prag: cel puțin 2 cuvinte distincte ȘI strict mai multe decât limba următoare — comentariile
    sunt scurte, un prag mai mare le lăsa nedecise. Măsurat pe 7.943 de tichete reale de pe canale
    sociale (piața = magazinul): decise 68,3% → 77,0%; dezacordurile cu piața 20 → 26, dar toate
    cele 9 NOI sunt mesaje chiar SCRISE în engleză pe un magazin românesc (B2B/influenceri), iar 3
    dezacorduri vechi s-au reparat. Pe același mesaj cu diacriticele tăiate (cum scrie clientul de pe
    telefon): ro 0% → 58,5%, cz 0% → 28,9%, pl 0% → 13,5%."""
    sc = {k: len(set(m.group(0).lower() for m in rx.finditer(low))) for k, rx in LANG_WORDS.items()}
    best, second = sorted(sc.items(), key=lambda kv: -kv[1])[:2]
    if best[1] < 2 or best[1] <= second[1]:
        return None
    if best[0] == "en" and sum(v for k, v in sc.items() if k != "en"):
        return None                       # engleză DOAR când nimic altceva nu marchează
    return best[0]
# regex DOAR ca hint/fallback — identificarea reală o face LLM-ul (identify)
RULES = [
    ("spam_automat", r"left a \d star review|left the following|judge\.?me|chargeflow|do[- ]?not[- ]?reply|newsletter|unsubscribe"),
    ("recenzie_feedback", r"recomand|ce parere|review|feedback|multumesc pentru|miroase (foarte )?bine|am lesinat|super produs"),
    ("anulare", r"anulez|anulare|anulati|renunt la comanda|nu mai vreau comanda|cancel|storno"),
    ("modificare_comanda", r"adresa gresita|alta adresa|schimb (nr|numarul|adresa|telefonul)|modific(a|are)? (comanda|adresa|telefon)|wrong address|change.*address|modific datele"),
    ("retur", r"\bretur|returnez|returna|banii inapoi|refund|vreau banii|\breturn\b|sa le returnez|nu mi plac"),
    ("schimb_swap", r"schimb produs|alt model|alta marime|alta culoare|inlocui|exchange|sa il schimb"),
    ("problema_produs", r"defect|stricat|nu functioneaza|nu aspira|lipseste|deteriorat|spart|teapa|nu corespunde|am primit (alt|gresit)|altceva|damaged|broken|missing"),
    ("livrare_wismo", r"unde (e|este|imi)|cand ajunge|coletul|nu a ajuns|nu am primit|awb|curier|tracking|intarzi|where is my order|track.*order|kde je|gdzie"),
    ("refuz_livrare", r"refuz|nu primesc coletul|nu accept coletul"),
    ("plata_factura", r"factura|am platit de doua|card.*(debitat|taxat)|chitanta|invoice"),
    ("presale_intrebare", r"aveti (pe |in )?stoc|cat costa|ce pret|livrati in|cand revine|dimensiuni|mai aveti|disponibil|how much|in stock"),
    ("comanda_noua", r"vreau sa comand|cum comand|doresc sa cumpar|i want to order"),
]
# Autoritățile de protecția consumatorului + termenii juridici uzuali pe FIECARE piață. Garda era
# doar în română, deci „Ще подам жалба в КЗП и НАП" NU escalada, iar același text în română da.
# Surse autorități: kzp.bg (КЗП) · nkfh.gov.hu + bekeltet.bkik.hu (fogyasztóvédelem / békéltető
# testület) · soi.sk (Slovenská obchodná inšpekcia) · coi.gov.cz (Česká obchodní inspekce) ·
# uokik.gov.pl (UOKiK / rzecznik konsumentów / Inspekcja Handlowa).
# ⚠️ „reklamace/reklamacia/reklamacja" (cz/sk/pl) NU intră aici: e cererea de GARANȚIE normală, nu
# echivalentul lui „reclamație la ANPC" — ar escalada jumătate din tichetele piețelor alea.
# ⚠️ Nici „съд" simplu (bg): pe un magazin de bucătărie înseamnă „vas", nu „tribunal". Nici „soi"
# simplu (sk) — e cuvânt românesc uzual; se cere „na SOI" sau numele întreg al inspecției.
ESCAL = re.compile(r"anpc|protectia consumator|dau in judecat|instanta|avocat|denunt|reclamatie|chargeback|politi[ae]"
                   r"|\bкзп\b|\bкзк\b|\bнап\b|за защита на потребител|жалб[аи]|адвокат|прокуратура"
                   r"|заведа дело|дело срещу|съдебн|ще ви съдя|сигнал до|сигнал в|полици"
                   r"|fogyasztovedelem|fogyasztovedelmi|bekelteto testulet|\bnkfh\b|\bgvh\b|\bnav\b"
                   r"|ugyved|birosag|beperel|perelni|feljelentes|rendorseg|\bpanasz"
                   r"|\bna soi\b|\bsoi\.sk\b|obchodn\w+ inspekc|\bna coi\b|\bdtest\b|ochrana spotrebitel"
                   r"|advokat|pravnik|trestn[ei] oznamen|\bzalob[au]\b|staznost|stiznost"
                   r"|\buokik\b|rzecznik[a]? konsumentow|inspekcja handlowa|adwokat|prokuratura"
                   r"|\bpozew\b|\bpozwe\b|\bdo sadu\b|zawiadomienie o przestepstwie|\bskarg[aei]\b"
                   r"|zastit\w+ potrosac\w*|drzavn\w+ inspektorat\w*|odvjetnik|\btuzb[aeu]\b|\bna sud\b|sudski spor"
                   r"|kaznenu prijavu|prijavu policiji"
                   r"|\bpolic(?:ie|ia|ja|ji|je)\b|consumer protection|legal action|small claims", re.I)
ACTION_CATS = {"modificare_comanda", "anulare", "schimb_swap", "problema_produs", "refuz_livrare"}
# CERERE DE CALLBACK — clientul cere să-l SUNĂM NOI (INVERSUL regulii „vreau să comand", unde îi
# recomandăm să sune el la TELEFON_COMANDĂ). Fără ramura asta draftul întorcea cererea: o clientă
# poloneză care își lăsase mobilul a fost trimisă să sune ea la un număr care nici nu era polonez.
# Se caută pe text BRUT (nu pe deacc — deacc nu atinge ł/ę/í/ě/ä), deci fiecare formă are clasă de
# caractere cu și fără diacritice. Măsurat pe oglinda de tichete: 139 cereri (ro 112, pl 15, bg 18, cz 2).
CALLBACK_RE = re.compile(
    r"suna[tțţ]i[- ]?m[aă]|s[aă] m[aă] suna[tțţ]i|m[aă] pute[tțţ]i suna|un telefon v[aă] rog|"
    r"contacta[tțţ]i[- ]?m[aă] (telefonic|la num)|a[sș]tept (un )?telefon|"                                 # ro
    r"prosz[eę] o (kontakt telefoniczn|telefon|oddzwonienie)|prosz[eę] (o )?zadzwoni|oddzwo[nń]|czekam na (telefon|kontakt)|"   # pl
    r"обадете ми се|моля за обаждане|очаквам обаждане|звъннете ми|свържете се с мен по телефон|"           # bg
    r"h[ií]vjanak|h[ií]vjon (vissza|fel)|visszah[ií]v[aá]st k[eé]rek|k[eé]rek egy telefonh[ií]v[aá]st|"    # hu
    r"zavolajte mi|pros[ií]m o telefon[aá]t|sp[aä]tn[eé] volanie|"                                          # sk
    r"zavolejte mi|zp[eě]tn[eé] vol[aá]n|[cč]ek[aá]m na telefon[aá]t|"                                      # cz
    r"nazovite me|molim (?:vas )?(?:da me )?nazov|o[čc]ekujem (?:vaš |vas )?poziv|javite mi se na (?:broj|telefon)|"  # hr
    r"call me back|please call me|give me a call", re.I)                                                    # en
# Date personale scrise de CLIENT (telefon/adresă/nr comandă). Pe canal PUBLIC ele intră în prompt
# prin transcript NEFILTRATE (redactarea de mai jos acoperă doar datele din sistemul NOSTRU), deci
# draftul le-ar putea repeta public — și oricum stau expuse sub postare.
CUST_PHONE_RE = re.compile(r"(?<![\w/])(?:\+|00)?\d(?:[ .\u00a0\-]?\d){8,11}(?![\w/])")
DATE_LIKE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")   # 21.01.2025 NU e telefon
# adresă: UN semnal tare (stradă/bulevard/adresa mea) SAU DOUĂ slabe. „nr 35" / „judet" singure NU sunt
# adresă — la parfumuri numărul e numele produsului (măsurat: 122 fals-pozitive pe „nr N"/„judet").
ADDR_RE = re.compile(r"\b(str\.? \w|strada |bd\.|b-?dul|bulevard|aleea |[sș]oseaua|calea \w|ul\. \w|ulica |улица|adresa (mea|de livrare))", re.I)
ADDR_WEAK_RE = re.compile(r"\b(sat |comuna |localitatea |jude[tțţ]|bloc \w|sc\.? ?\d|ap\.? ?\d|cod po[sș]tal|nr\.? ?\d)", re.I)
def cust_phones(text):
    """Numerele de telefon scrise ÎN TEXT (ale clientului), fără datele calendaristice."""
    return [m.group(0).strip() for m in CUST_PHONE_RE.finditer(DATE_LIKE_RE.sub(" ", text or ""))]
# --- r4:pii-public ---
# EMAIL: clasa lipsea COMPLET — nici public_pii (care propune ascunderea comentariului), nici
# public_pii_leaks (care redactează draftul) nu o testau, deci adresa clientului ieșea intactă în
# textul FINAL public. Măsurat pe oglinda reală de tichete: 675 din 2.688 de mesaje conțin un email.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# AWB scris în GRUPE de cifre („1234 5678 9012"): AWB_RE cere cifrele LIPITE, iar CUST_PHONE_RE se
# oprește la 12 cifre — forma grupată de 13-16 cifre (Speedy/BG) nu era prinsă de nimeni ca ÎNTREG.
# ONEST: pe oglinda reală sunt 63 de forme grupate, dar aproape toate ≤12 cifre, deci detectorul de
# TELEFON le scotea din întâmplare; doar 2 texte au 13-16 cifre. Pe formele lungi redactarea veche
# lăsa FRAGMENTE („AWB 2410 " din „2410 1234 56789") — reprodus sintetic, nu pe corpus.
AWB_GROUP_RE = re.compile(r"(?<![\w/])\d{2,}(?:[ .\u00a0\-]\d{2,}){1,5}(?![\w/])")
# ADRESA, ca SPAN (ancoră + restul frazei), nu doar ca ancoră: ADDR_RE spune DACĂ există o adresă,
# nu unde se termină, deci cu ea nu se poate face redactare prin înlocuire de șir.
# Ancorele de STRADĂ sunt urmate întotdeauna de valoare → span-ul se scoate mereu. „adresa mea /
# adresa de livrare" e ambiguu: în 23 din 91 de mesaje REALE cu ancoră e CEREREA agentului („îmi
# spuneți adresa de livrare?"), fără nicio valoare — acolo n-ai ce redacta, deci span-ul se scoate
# doar dacă fraza conține o cifră (adică o valoare).
ADDR_STRADA_RE = r"(?:str\.?\s|strada\b|bd\.|b-?dul\b|bulevard\w*|aleea\b|[sș]oseaua\b|calea\s|ul\.\s|ulica\b|улица)"
ADDR_CERERE_RE = r"adresa\s+(?:mea|de\s+livrare)"
# Sfârșitul frazei: punctul încheie span-ul DOAR dacă e urmat de majusculă — altfel „str. Zorilor
# nr. 12, bloc B2" s-ar tăia la „nr." (abreviere), lăsând restul adresei în text.
ADDR_FINAL_RE = r"(?=[!?;]\s|\.\s+[A-ZĂÂÎȘȚ]|[.!?;]?$|\n)"
ADDR_SPAN_RE = re.compile(r"(?:%s|%s)[^\n]{0,140}?%s" % (ADDR_STRADA_RE, ADDR_CERERE_RE, ADDR_FINAL_RE), re.I)
# Nume propriu (două cuvinte capitalizate la rând) — folosit DOAR pe canal public, pe câmpurile
# libere din registrul de incidente, unde direcția sigură e să tai prea mult.
NUME_PROPRIU_RE = re.compile(r"\b[A-ZĂÂÎȘȚ][a-zăâîșț]{2,}\s+[A-ZĂÂÎȘȚ][a-zăâîșț]{2,}\b")


def addr_spans(text):
    """Intervalele de ADRESĂ cu VALOARE (nu simpla cerere „îmi dați adresa de livrare?")."""
    out = []
    for g in ADDR_SPAN_RE.finditer(text or ""):
        s = g.group(0)
        if re.match(ADDR_STRADA_RE, s, re.I) or re.search(r"\d", s):
            out.append(s)
    return out


def awb_groups(text, phone_ok=""):
    """AWB scris în grupe de cifre (10-16 cifre în total), fără numărul NOSTRU de CS."""
    out = []
    for g in AWB_GROUP_RE.finditer(DATE_LIKE_RE.sub(" ", text or "")):
        s = g.group(0)
        n = sum(ch.isdigit() for ch in s)
        if 10 <= n <= 16 and norm_phone(s) != phone_ok:
            out.append(s)
    return out


def name_tokens(nume):
    """Bucățile căutabile din numele clientului (≥4 litere, fără diacritice)."""
    return [deacc(w) for w in re.split(r"[^\w]+", str(nume or "")) if len(w) >= 4]


def name_hits(text, nume):
    """Numele clientului așa cum apare ÎN TEXT: fără diacritice și cu terminație flexionară
    („Popescu" / „Popescule"). deacc() e 1:1 pe caractere, deci pozițiile se păstrează."""
    t = text or ""
    d = deacc(t)
    out = []
    for w in name_tokens(nume):
        for mt in re.finditer(r"\b%s\w{0,3}\b" % re.escape(w), d):
            out.append(t[mt.start():mt.end()])
    return out


# Un șir de 10-16 cifre e AWB DOAR dacă lângă el se vorbește despre expediere. Fără poarta asta,
# regula naivă „orice AWB_RE = AWB" e mai rea decât lipsa ei: măsurat pe 99.379 de comentarii publice
# REALE, ea producea 22 de propuneri noi de ascundere din care ~14 erau id-uri din LINKURI
# (`story_fbid=…`, `/item/…`, `goods_id=…`) și ZERO AWB-uri adevărate. Aceeași disciplină ca
# `plugins/core/scripts/pii_guard.py` (CTX_AWB + CTX_ALT_ID), ca să nu ascundem comentarii cinstite.
AWB_CTX_RE = re.compile(r"awb|tracking|\btrack\b|colet|curier|expedi|livr|shipment|parcel|"
                        r"dpd|sameday|fan ?courier|cargus|econt|packeta|urmari", re.I)
# Telefon LIPIT de un cuvânt sau scris cu „O"/„⁰" în loc de zero („…tel0712345678", „Nr telefon ⁰7…"):
# CUST_PHONE_RE îl REFUZĂ din cauza lui `(?<![\w/])`. Măsurat pe aceleași 99.379 de comentarii:
# 9 conțin numărul clientului în forma asta, iar 8 dintre ele nu produceau NICIO propunere de
# ascundere — date de client rămase la vedere pe o postare publică.
TEL_LIPIT_RE = re.compile(r"(?<=[^\W\d_])\+?4?0?7\d{8}(?!\d)")


def _cifre_in_link(t, start, end):
    """Cifrele fac parte dintr-un LINK / id de resursă (nu sunt date de client)."""
    tok = t[t.rfind(" ", 0, start) + 1:end]
    return "://" in tok or "www." in tok or "=" in tok or "/" in tok


def public_pii(text):
    """Ce date personale conține un comentariu PUBLIC (listă goală = nimic de ascuns)."""
    t = text or ""
    kinds = []
    # Aceeași poartă și pe telefon: măsurat, 8 din cele 184 de comentarii publice pe care le
    # semnalam ca „telefon" (4,35%) erau semnalate DOAR pentru cifrele dintr-un LINK. O propunere
    # de ascundere în care CS n-are încredere ajunge să fie aprobată/refuzată la nimereală.
    _fara_date = DATE_LIKE_RE.sub(" ", t)
    _tel = [_m.group(0).strip() for _m in CUST_PHONE_RE.finditer(_fara_date)
            if not _cifre_in_link(_fara_date, _m.start(), _m.end())]
    _lipit = [_m for _m in TEL_LIPIT_RE.finditer(t) if not _cifre_in_link(t, _m.start(), _m.end())]
    if _tel or _lipit: kinds.append("telefon")
    if ORDER_RE.search(t): kinds.append("nr comandă")
    # clasa `email` vine din cheia `pii-public` (675 din 2.688 de mesaje reale conțin o adresă);
    # păstrată aici la contopirea celor două patch-uri care rescriu amândouă public_pii.
    if EMAIL_RE.search(t): kinds.append("email")
    # AWB_RE era cablat (runda 3) în redactarea DRAFTULUI NOSTRU, dar NU și aici, unde se decide dacă
    # PROPUNEM ascunderea comentariului CLIENTULUI. Nu se numără de două ori ce e deja raportat ca
    # telefon (un AWB de 10 cifre e prins și de CUST_PHONE_RE).
    _cifre_tel = {"".join(c for c in p if c.isdigit()) for p in _tel}
    if any(_m.group(0) not in _cifre_tel
           and not _cifre_in_link(t, _m.start(), _m.end())
           and AWB_CTX_RE.search(t[max(0, _m.start() - 30): _m.end() + 30])
           for _m in AWB_RE.finditer(t)):
        kinds.append("AWB")
    if ADDR_RE.search(t) or len(set(m.group(1)[:4].lower() for m in ADDR_WEAK_RE.finditer(t))) >= 2:
        kinds.append("adresă")
    return kinds


def public_pii_leaks(draft, phone_order="", cust_name=""):
    """Datele personale rămase ÎN DRAFT pe canal public: telefon / nr. comandă / AWB (inclusiv scris
    în grupe de cifre) / EMAIL / ADRESĂ (ca interval) / NUMELE clientului.

    AWB_RE e cablat AICI (până acum exista, dar nu-l chema nimeni): un AWB de 13 cifre — forma
    Speedy/BG — nu e prins de CUST_PHONE_RE (care se oprește la 12 cifre), deci trecea intact prin
    redactare chiar și pe un prefix de comandă acoperit. Numărul NOSTRU de CS rămâne permis:
    pe comentariu public exact acolo îl invităm pe client să sune."""
    d = draft or ""
    if not d or d.startswith("(eroare"):
        return []
    ok = norm_phone(phone_order)
    out = [p for p in cust_phones(d) if norm_phone(p) != ok]
    out += [m.group(0) for m in ORDER_RE.finditer(d)]
    out += [m.group(0) for m in AWB_RE.finditer(d) if norm_phone(m.group(0)) != ok]
    out += awb_groups(d, ok)                 # AWB în grupe de cifre (nu e nici telefon, nici AWB lipit)
    out += [m.group(0) for m in EMAIL_RE.finditer(d)]
    out += addr_spans(d)                     # adresa, ca interval — ancora singură nu e redactabilă
    out += name_hits(d, cust_name)           # numele clientului (public = nu confirmăm cine e)
    return out


def public_pii_rest(draft, cust_name=""):
    """PLASA de siguranță: ce clase TARI au rămas în draftul public DUPĂ redactare. Nevidă =
    draftul NU se salvează deloc. Există fiindcă redactarea e prin înlocuire de șir și poate eșua
    (span fragil, adresă fără ancoră — doar „bloc/ap/cod poștal", nume scris altfel). Mai bine
    niciun draft decât unul care scurge. Măsurat pe 229 de mesaje REALE de agent: plasa ar suprima
    1 (0,4%) după ce redactarea și-a făcut treaba."""
    d = draft or ""
    if not d or d.startswith("(eroare"):
        return []
    rest = []
    if EMAIL_RE.search(d): rest.append("email")
    if addr_spans(d) or len(set(m.group(1)[:4].lower() for m in ADDR_WEAK_RE.finditer(d))) >= 2:
        rest.append("adresă")
    if name_hits(d, cust_name): rest.append("numele clientului")
    return rest


def redact_public_pii(draft, phone_order="", cust_name=""):
    """Scoate din draft datele personale interzise public. Întoarce (draft, câte au fost scoase).
    Se cheamă pe textul FINAL: orice cale care regenerează draftul (canal reclamat, confirmare după
    acțiune aplicată) trebuie să treacă prin ea, altfel ocolește garda."""
    total = 0
    # PÂNĂ LA PUNCT FIX: normalizarea de spații de la finalul unei pase LIPEȘTE liniile, iar o adresă
    # scrisă pe mai multe rânduri formează abia atunci un interval nou. Măsurat pe mesajele REALE de
    # agent: 5 texte mai scurgeau după o singură pasă, zero după a doua.
    for _ in range(3):
        leaks = public_pii_leaks(draft, phone_order, cust_name)
        if not leaks:
            break
        # cele mai lungi întâi: altfel „str. Zorilor nr. 12" rămâne ciuntit după ce i s-a scos „nr. 12"
        for p in sorted(set(leaks), key=len, reverse=True):
            draft = draft.replace(p, "")
        draft = " ".join(draft.split())
        total += len(leaks)
    return draft, total
# post-filtru ANTI-HALUCINARE: tipare de FABRICARE (lookup/status/termen/dimensiune inventate) când NU avem
# datele în context. ACELEAȘI clase în TOATE limbile în care răspundem (ro/bg/hu/sk/cz/pl/en) — un filtru scris
# doar în română e mort pe piețele străine (BG/HU/SK/PL/CZ), unde drafturile treceau nefiltrate.
HALLU = re.compile(
    # LOOKUP inventat („am verificat / am căutat / (nu) am găsit")
    r"am verificat|am c[ăa]utat|nu am g[ăa]sit|n-?am g[ăa]sit|nu (am )?identificat|am identificat comanda|"
    r"nu exist[ăa] (nicio|o) comand|comanda (dumneavoastr[ăa]|nr|#)?\s*[A-Z]{2,5}\d+ (este|a fost|nu)|"
    r"провери(х|хме)|не (я |го )?намерих|намерих (вашата )?поръчка|"
    r"ellen[őo]riztem|ellen[őo]rizt[üu]k|megn[ée]ztem|nem tal[áa]ltam|megtal[áa]ltam|"
    r"skontroloval|overil(a)? som|nena[šs]iel som|nena[šs]la som|na[šs]iel som|"
    r"zkontroloval|ov[ěe][řr]il jsem|nena[šs]el jsem|nena[šs]la jsem|na[šs]el jsem|"
    r"sprawdzi[łl](em|am|[śs]my)|nie znalaz[łl](em|am)|znalaz[łl](em|am)|"
    r"i (have )?(just )?checked|i (couldn'?t|could not|didn'?t) find|i found (your|the) order|"
    # STATUS inventat („este în procesare / a fost predat curierului")
    # Ramura e un grup NUMIT (`status`) ca garda de incident să poată deosebi o afirmație de
    # STATUS de un termen/preț inventat. Conținutul e NESCHIMBAT — doar devine identificabilă.
    r"(?P<status>este în procesare|a fost predat|nu a fost predat[ăa]|urmeaz[ăa] s[ăa] fie preluat|"
    r"в процес на обработка|обработва се|"
    # bg/pl/cz/sk/hr: participiul are GEN și ordinea e LIBERĂ — „Поръчката е предадена" și „предадена е"
    # sunt aceeași afirmație, iar „zamówienie" e NEUTRU („zostało wysłane"), nu feminin. Lista de
    # participii e cea REALĂ din legendele de status publicate pe piață (sportdepot.bg: „Приключена —
    # поръчката е обработена и ще бъде предадена на куриер"): „обработена / доставена / анулирана" sunt
    # statusuri la fel de inventate ca „предадена" și treceau NEFILTRATE. Copula include și „ще бъде"
    # (viitor), iar „куриер" apare pe sursă și NEarticulat (regexul cerea „куриера").
    r"\b(?:е|бе|беше|бъде)\s+(?:предаден|изпратен|подаден|обработен|доставен|анулиран|отказан|върнат)[аио]?\b|"
    r"\b(?:предаден|изпратен|подаден|обработен|доставен|анулиран)[аио]?\s+е\b|"
    r"\b(?:предаден|изпратен)[аио]?\s+на\s+куриер\w*|"
    # maghiara e AGLUTINANTĂ: forma de dicționar nu apare niciodată în text. Statusurile reale sunt
    # „a futár kiszállította", „a feladást követő munkanapon kerül kiszállításra" (GLS Hungary) și
    # „feldolgozunk" (bonhaus.hu), deci se enumeră sufixele conjugate, nu tulpina goală.
    # Construcția perifrastică „-ásra/-ésre kerül" e forma OFICIALă de status pe piața HU și lipsea
    # aproape complet: regexul avea doar varianta lipită de „kiszállítás". VERBATIM: „Csomagod
    # átadásra került az általad választott futárszolgálatnak" (curlmission.hu), „visszaszállításra
    # kerül", „kerül átirányításra" (foxpost.hu/gyik) — ambele topici apar pe aceeași pagină.
    r"\b\w{3,}(?:[áa]sra|[ée]sre)\s+ker[üu]l\w*|\bker[üu]l\w*\s+\w{3,}(?:[áa]sra|[ée]sre)\b|"
    # persoana I PLURAL la prezent e o PROMISIUNE de acțiune, nu o descriere: „akár egy munkanapon
    # belül kiszállítjuk" (foxpost.hu), „legkésőbb másnap átadjuk az általad választott
    # futárszolgálatnak" (curlmission.hu). Trecutul era acoperit, prezentul nu.
    r"kisz[áa]ll[ií]tjuk|kisz[áa]ll[ií]tj[áa]k|[áa]tadjuk|[áa]tadj[áa]k|elk[üu]ldj[üu]k|elk[üu]ldik|"
    r"feladjuk|k[ée]zbes[ií]tj[üu]k|visszak[üu]ldj[üu]k|"
    # „úton van / úton vannak" = afirmație de status pură („a következő szállítmány már úton van",
    # curlmission.hu; „A csomagjaid biztonságban úton vannak", gls-group.com/HU)
    r"\b[úu]ton van(?:nak)?\b|"
    r"feldolgoz[áa]s alatt|feldolgozt(?:uk|a|am|[áa]k)|kisz[áa]ll[ií]t[áa]s alatt|"
    r"kisz[áa]ll[ií]tott(?:uk|a|am|[áa]k)|kisz[áa]ll[ií]t[áa]sra ker[üu]l|ker[üu]l kisz[áa]ll[ií]t[áa]sra|"
    r"k[ée]zbes[ií]tett(?:[üu]k|e|[ée]k)|[áa]tadt(?:uk|a|[áa]k)|elk[üu]ldt(?:[üu]k|e|em|[ée]k)|"
    r"feladt(?:uk|a|[áa]k)|"
    r"sa spracov[áa]va|\bbol[aoi]?\s+(?:odovzdan\w*|odoslan\w*|spracovan\w*|doru[čc]en\w*|expedovan\w*)|"
    r"\bje na ceste\b|"
    # „bude odoslaná / bude doručený / bude zaslaná" — VERBATIM de pe obchodné podmienky reale
    # (kuracllctn.sk, beecomb.sk, nila-shop.sk). Perechea trecut/viitor era ruptă: `bol` exista,
    # `bude` nu, deși tocmai viitorul e promisiunea pe care un draft o fabrică.
    r"\b(?:bude|bud[úu])\s+(?:\w+\s+){0,3}"
    r"(?:odoslan\w*|odovzdan\w*|doru[čc]en\w*|predan\w*|expedovan\w*|vyexpedovan\w*|"
    r"zaslan\w*|spracovan\w*|pripraven\w*|vráten\w*|vraten\w*)|"
    r"se zpracov[áa]v[áa]|\bbyl[aoy]?\s+(?:p[řr]ed[áa]n\w*|odesl[áa]n\w*|doru[čc]en\w*|zpracov[áa]n\w*|expedov[áa]n\w*)|"
    r"\bje na cest[ěe]\b|"
    # „Zboží bude doručeno na dodací adresu" (4kluciodkol.cz), „Vaše zásilka bude odeslána do 48
    # hodin" (talode.cz), „bude vyexpedováno" — la fel ca la sk, doar trecutul era acoperit.
    # Lista de participii e închisă intenționat: „bude dosaženo účelu zpracování" (text de
    # confidențialitate, dpd.com/cz) NU e status de colet și rămâne neprins.
    r"\b(?:bude|budou)\s+(?:\w+\s+){0,3}"
    r"(?:odesl[áa]n\w*|doru[čc]en\w*|p[řr]ed[áa]n\w*|expedov[áa]n\w*|vyexpedov[áa]n\w*|"
    r"zasl[áa]n\w*|zpracov[áa]n\w*|p[řr]ipraven\w*|vr[áa]cen\w*)|"
    r"w trakcie (realizacji|przetwarzania)|zosta[łl](?:a|o|y)?\s+(?:przekazan\w*|wys[łl]an\w*|nadan\w*|dostarczon\w*)|"
    r"w dor[ęe]czeniu|jest w drodze|"
    # VIITORUL PASIV lipsea din pl/cz/sk/hr, deși bg („ще бъде") și hr („bit će") îl aveau.
    # E chiar forma oficială de status pe piața poloneză: „Zamówienie zostanie przekazane do
    # realizacji" (empik.com/pomoc/statusy-zamowienia), „paczka zostanie przekazana kurierowi"
    # (qactus.pl), „zostanie przekazana do doręczenia" (inpost.pl/pomoc). Se lasă loc pentru 0-2
    # cuvinte între auxiliar și participiu („zostanie już dzisiaj wysłane").
    r"\bzosta(?:nie|n[ąa])\s+(?:\w+\s+){0,2}"
    r"(?:przekazan\w*|wys[łl]an\w*|odes[łl]an\w*|nadan\w*|dostarczon\w*|dor[ęe]czon\w*|"
    r"zrealizowan\w*|wydan\w*|odebran\w*|spakowan\w*)|"
    # CROATA lipsea COMPLET din HALLU (0/8 pe statusurile reale), deși e limbă cablată peste tot
    # altundeva: detect_lang, STORE_LANG „Bonhaus HR", REGISTER, SAFE_GREET/SAFE_BODY, NEG/POS, ESCAL.
    # Bonhaus HR n-are nici măcar domeniu (bonhaus.hr NU rezolvă DNS), deci vocabularul e luat de pe
    # piață: a1.hr, hco.hr, sveisvasta.hr, lijepa.hr, kapitalac.com, posta.hr, dpd.hr.
    r"\b(?:je|su|bila?|bilo|bit [ćc]e|biti)\s+(?:predan|poslan|otpremljen|uru[čc]en|isporu[čc]en|dostavljen|zaprimljen|preuzet)\w*|"
    # ordinea INVERSĂ a viitorului („narudžba će biti stornirana", „paket će biti poslan",
    # „kupac će biti obaviješten") era complet nevăzută: regexul cerea auxiliarul ÍNAINTE.
    # În croată ambele topici sunt corecte și apar amestecat pe aceeași pagină (rockalica.com,
    # inter-land.hr, sveisvasta.hr, amelie.studio).
    r"\b(?:[ćc]e\s+biti|bit\s+[ćc]e|bi[ćc]e)\s+(?:\w+\s+){0,2}"
    r"(?:predan|poslan|otpremljen|uru[čc]en|isporu[čc]en|dostavljen|zaprimljen|preuzet|"
    r"spreman|spremn|stornir|otkazan|obavije[šs]ten|vra[ćc]en|zapakiran|upakiran)\w*|"
    r"\b(?:predan|poslan|otpremljen|uru[čc]en|isporu[čc]en|dostavljen|zaprimljen)\w*\s+je\b|"
    r"se obra[đd]uj[eu]|\bu obradi\b|\bu dostavi\b|\bna putu do\b|"
    r"is being processed|has been (handed over|dispatched|shipped))|"
    # TERMEN inventat („în N zile") — cuvântul „zile" pe fiecare piață. Cifra NU e lipită de
    # substantiv: limbile slave bagă adjectivul „lucrătoare" între ele („2 работни дни", „do 2
    # pracovných dní"), iar maghiara e AGLUTINANTĂ — sufixul se lipește de cuvânt („3 munkanapon",
    # „2-3 munkanapot"), deci forma de dicționar + graniță de cuvânt rata 8 din 16 fraze reale.
    # ⚠️ MERGE runda 3 (coordonator): ramura de mai jos e FUZIUNEA a două chei care au atins
    # ACEEAȘI expresie — `acoperire-straina` (croata „dana", en-dash, singularul bulgar
    # „работен") și `termen-si-emoji` (cele de mai jos). Ambele intenții sunt păstrate.
    # „de" între cifră și unitate e obligatoriu în română („24 de ore", „30 de zile"), iar zilele
    # „calendaristice" sunt tot un termen: lista de adjective avea doar „работни", deci „7
    # календарни дни" trecea nefiltrat.
    r"\b\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?(?:de\s+)?"
    r"(?:(?:lucr[ăa]toare|работ(?:ни|ен)|календарни|pracovn\w*|robocz\w*|kalendarz\w*|kalend[áa][řr]n\w*|"
    r"kalendarsk\w*|radn\w*|munka|napt[áa]ri|working|business|calendar)[\s.-]*)?"
    r"(?:zile?|дена|дни|дня|ден|(?:munka)?nap(?:okat|okon|ok|ot|on|ig|ra|ja)?|"
    r"dn[iíyůaeě]\w{0,3}|d[ňn][aáouí]\w{0,3}|dan[ai]?|dzie[nń]\w*|days?)\b|"
    # ORE — clasa lipsea în TOATE limbile: „în 24 de ore", „24 órán belül", „w ciągu 24 godzin",
    # „do 24 hodin", „в рамките на 24 часа", „u roku od 24 sata" treceau toate. Se cere marcajul de
    # TERMEN-LIMITĂ lipit de cifră (prepoziția înainte, „belül" după) și se leagă de unitatea
    # limbii respective, altfel PROGRAMUL DE LUCRU real („od 9 do 20 sati", „8-18 óra között")
    # ar fi raportat drept termen inventat.
    r"\b[îi]n\s+(?:termen\s+de\s+|maxim\w*\s+|decurs\s+de\s+)?\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?(?:de\s+)?or[ea]\b|"
    r"\b\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?[óo]r[áa]n\s+bel[üu]l|"
    r"\b(?:w\s+ci[ąa]gu|w\s+przeci[ąa]gu|do)\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?godzin\w*|"
    r"\b(?:do|b[ěe]hem|v\s+pr[ůu]b[ěe]hu|v\s+priebehu)\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?hod[íi]n\w*|"
    # „za N hodin/godžin" și „w N godzin" — prepozițiile lipseau, deși „Dostawa w 24 godziny"
    # (sendit.pl, epaka.pl, furgonetka.pl) e formularea standard a pieței poloneze, iar cz/sk
    # spun „za 24 hodín". Rămâne CERUTĂ prepoziția lipită de cifră: programul de lucru
    # („9:00 - 16:00", „od 9 do 20 sati") nu are prepoziție de termen și rămâne neprins.
    r"\bza\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?(?:hod[íi]n\w*|godzin\w*)|"
    r"\bw\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?godzin\w*|"
    # maghiara pune marcajul DUPĂ cifră: „24 óra alatt", „12 óra után", „2 óra múlva".
    # „óra között" (interval de program) NU e în listă, deci „8-18 óra között" rămâne neprins.
    r"\b\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?[óo]ra\s+(?:alatt|ut[áa]n|m[úu]lva|bel[üu]l)\b|"
    r"(?:в\s+рамките\s+на|в\s+срок\s+от|до)\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?час[аове]*\b|"
    r"\b(?:u\s+roku\s+od|unutar|za)\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?sat[aiu]\b|"
    # croata exprimă termenul și cu COPULĂ, fără nicio prepoziție: „Rok za uplatu je 24 sata".
    # Se cere cuvântul „rok/vrijeme/termin" înainte, ca „Radno vrijeme je 8 sati" (program) să nu
    # fie raportat drept termen inventat.
    r"\b(?:rok|vrijeme|termin|isporuka|dostava)\b[^.!?\n]{0,40}?\bje\s+\d{1,2}\s*sat[ai]\b|"
    r"\bwithin\s+\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?hours?\b|"
    # TERMEN fără cifră — „zilele următoare" / „a következő munkanapon" promit la fel de concret ca
    # „în 2 zile", dar nu aveau nicio cifră de prins.
    r"[îi]n zilele urm[ăa]toare|zilele urm[ăa]toare|ziua urm[ăa]toare|urm[ăa]toarele zile|"
    r"a k[öo]vetkez[őo] munkanap\w*|k[öo]vetkez[őo] munkanap\w*|"
    r"\b(?:w\s+)?ci[ąa]gu doby\b|"
    r"nast[ęe]pnego dnia roboczego|w nast[ęe]pnym dniu roboczym|kolejnego dnia roboczego|"
    r"nast[ęe]pny dzie[nń] roboczy|"
    r"n[áa]sleduj[íu]c[íi](?:ho)?\s+pracovn[íý](?:ho)?\s+d(?:en|ne|e[ňn])|"
    r"(?:на\s+)?следващия\s+работен\s+ден|"
    r"sljede[ćc]i\s+radni\s+dan|idu[ćc]i\s+radni\s+dan|"
    r"next\s+(?:business|working)\s+day",
    re.I)
# DIMENSIUNEA se verifică în CONTEXT, exact ca prețul: catalogul reclamei dă dimensiuni REALE, iar un
# regex necondiționat marca drept fabricat tocmai „20x20 cm" scris în blocul de catalog.
HALLU_DIM = re.compile(r"\b\d{1,3}\s?[x×х]\s?\d{1,3}\b|\b\d{2,3}\s?cm\b", re.I)
# TERMENE de POLITICĂ, nu invenții: rambursarea „în maximum 14 zile de la ajungerea coletului" e
# prescrisă VERBATIM în SYSTEM (procedura RETUR), iar livrarea standard e de 1-3 zile lucrătoare pe
# toate magazinele. ORICE alt termen (5/7/10 zile) rămâne prins.
# DISCRIMINANTUL e CONTEXTUL FRAZEI, nu forma numărului. Forma („interval" vs „cifră unică") NU poate
# fi discriminant, fiindcă adjectivul stă altundeva față de substantiv de la o limbă la alta: aceeași
# afirmație ieșea SCUZATĂ în ro/pl/bg („2-3 zile lucrătoare", „2-3 dni roboczych", „2-3 дни") și
# PRINSĂ în hu/sk („2-3 munkanapon", „2-3 pracovných dní") — și tot pe formă erau scuzate
# „Vă returnăm banii în 2-3 zile" și „Curierul vă contactează în 2-3 zile", care sunt promisiuni
# despre comanda ACESTUI client. Regula corectă: e scuzat DOAR ce e scris ca POLITICĂ GENERALĂ
# (impersonal: „de obicei/standard/livrarea se face în…"), cu cifre care sunt constante de politică.
# REFUND_CTX pastreaza largirea adusa de cheia `acoperire-straina`: croata (povrat/vraca/vratit)
# si verbul polonez „zwrocimy”, care facea fals-pozitiv pe politica VERBATIM de pe bonhaus.pl.
REFUND_CTX = re.compile(r"returnat|returnare|rambursare|rambursat|retur|refund|vissza|vr[áa]cen|vr[áa]tenie"
                        r"|zwrot|zwr[oó]c|povrat|vra[ćc]a|vratit|возврат|възстанов", re.I)
# marcaj de GENERALITATE — fraza vorbește despre TOATE comenzile, nu despre coletul clientului
_POLICY_CTX = re.compile(
    r"de obicei|de regul[ăa]|[îi]n general|conform politicii|politica (?:noastr[ăa]|de)|"
    r"livrarea (?:se face|se va face|dureaz[ăa]|standard)|termenul de livrare|comenzile|coletele|"
    r"standard\w*|[šs]tandardn\w*|standardow\w*|"
    r"zwykle|zazwyczaj|zam[oó]wienia s[ąa]|przesy[łl]ki s[ąa]|"
    r"obvykle|zvy[čc]ajne|objedn[áa]vky (?:jsou|s[úu])|"
    r"об[ий]кновено|поръчките|пратките|"
    r"[áa]ltal[áa]ban|szok[áa]sos|minden rendel[ée]s|"
    r"obi[čc]no|naj[čc]e[šs][ćc]e|u pravilu|narud[žz]be se|"
    r"usually|as a rule|"
    # termenul PRESCRIS DE LEGE e tot o regulă generală, nu o promisiune („termenul legal de răspuns
    # la sesizări este de până la 30 de zile" — frază reală din coada de propuneri)
    r"termen\w* legal|conform legisla\w*|potrivit legii|zakonsk\w* rok|"
    r"ze z[áa]kona|pod[ľl]a z[áa]kona|zgodnie z (?:prawem|ustaw)|по закон\w*|"
    r"t[öo]rv[ée]ny szerint|zakonom propisan\w*|by law", re.I)
_TERM_NUM = re.compile(r"\d{1,2}")
_CONST_LIVRARE = (1, 2, 3)      # livrarea standard a magazinelor
_CONST_RETUR = (14, 30)         # retur/rambursare, prescrise în SYSTEM și de lege


def _term_policy_const(g, permise):
    """Cifrele din termenul prins sunt CONSTANTE de politică? Verificarea e pe CIFRE, nu pe forma
    șirului — altfel rezultatul depinde de topica limbii (unde stă adjectivul față de substantiv)."""
    nums = [int(x) for x in _TERM_NUM.findall(g)]
    return bool(nums) and all(n in permise for n in nums)


_SFARSIT_PROP = ".!?\n"


def propozitia_din_jur(txt, a, b):
    """Propoziția care conține intervalul [a,b) — tăiată la . ! ? și la linia nouă.

    Ancora de generalitate trebuie să stea în ACEEAȘI frază cu termenul. Cu fereastra fixă de
    -140/+80 caractere de dinainte, o frază de politică „împrumuta" scuza propoziției următoare:
    „Livrarea standard se face în 1-3 zile lucrătoare. Coletul dumneavoastră ajunge în 2 zile."
    trecea întreagă, deși a doua propoziție e o promisiune despre coletul ACESTUI client."""
    st = max([txt.rfind(c, 0, a) for c in _SFARSIT_PROP] or [-1]) + 1
    en = min([x for x in (txt.find(c, b) for c in _SFARSIT_PROP) if x != -1] or [len(txt)])
    return txt[st:en + 1]


def term_excused(draft, m):
    """Termenul prins e POLITICA generală a magazinului, nu o promisiune despre coletul ACESTUI client?
    Scuzat DOAR dacă fraza e scrisă impersonal, ca regulă generală, ȘI cifrele sunt constante de
    politică (sau e rambursarea prescrisă în SYSTEM). Termenele fără cifră („zilele următoare") se
    judecă după ACELAȘI context. Orice altceva rămâne halucinație, în ORICE limbă."""
    g = " ".join(m.group(0).split())
    seg = propozitia_din_jur(draft, m.start(), m.end())
    if _term_policy_const(g, _CONST_RETUR) and REFUND_CTX.search(seg):
        return True
    if not _POLICY_CTX.search(seg):
        return False
    return not _TERM_NUM.search(g) or _term_policy_const(g, _CONST_LIVRARE + _CONST_RETUR)
# preț concret, în TOATE monedele piețelor noastre (lei/ron, EUR/€, лв/BGN, Ft/HUF, zł/PLN, Kč/CZK).
# NU e fabricat dacă numărul apare în CONTEXT (catalogul reclamei dă prețuri REALE) → se verifică, nu se sare filtrul.
HALLU_PRICE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{1,2})?)\s*(?:de\s+)?"
    # Monedele scrise în LITERE lipseau pe fiecare piață: „120 лева" (cookiearte.com), „300 korun"
    # (ct24.ceskatelevize.cz), „300 Forint" (foxpost.hu), „4 eura" (amelie.studio), „kilkuset
    # złotych" (sky-shop.pl). Prioritate „евро": Bulgaria e pe euro din ian-2026, deci prețul
    # inventat acolo se scrie tocmai așa. „eura" nu se prindea nici măcar ca „euro", fiindcă
    # granița `(?!\w)` cădea pe litera de flexiune.
    r"(?:lei|ron|euro|eur|eura|€|лв\.?|лев(?:а|ове|ата)?|евро|bgn|ft|huf|forint\w*|"
    r"z[łl]|z[łl]ot\w*|pln|k[čc]|korun\w*|czk)(?!\w)|"
    r"€\s?(\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{1,2})?)", re.I)
# telefon inventat: promptul interzice explicit numerele pe care nu le avem, dar nicio limbă nu-l prindea
_PHONE_RE = re.compile(r"(?<![\d+])\+?\d[\d\s.()\-]{7,17}\d(?!\d)")
_DATE_LIKE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
def _num_int(s):
    """Partea ÎNTREAGĂ a unui număr scris în orice format (1.299,00 / 1 299 / 129) — pt comparație cu contextul."""
    t = re.sub(r"[^\d.,]", "", s or "")
    t = re.sub(r"[.,]\d{1,2}$", "", t)
    return re.sub(r"\D", "", t)
def _status_din_registru(inc_blk):
    """Registrul de incidente afirmă o RETRIMITERE deja plecată pentru comanda asta?
    Doar atunci o frază de STATUS („a fost expediată / a fost livrată") e un FAPT VERIFICAT, nu o
    invenție — exact ce scrie blocul însuși în prompt: „faptele de mai sus … NU intră sub
    anti-halucinare". Un incident NEremediat („retrimis: NU") nu scuză nimic."""
    return "retrimis: DA" in (inc_blk or "")


def hallu_hits(draft, ctx="", inc_blk=""):
    """Motivele de FABRICARE din draft ([] = curat): lookup/status/termen/dimensiune inventate, plus preț sau
    telefon care NU se regăsesc în CONTEXT. Prețul din catalogul reclamei și telefonul brandului sunt REALE.
    `inc_blk` = blocul de FAPTE VERIFICATE (registru de incidente) al tichetului, dacă există: o afirmație
    de STATUS acoperită de o retrimitere confirmată acolo NU e halucinație. Termenele/prețurile/AWB-urile
    rămân judecate ca până acum (au cifre → trec prin `incident_backed` la locul de apel)."""
    d = draft or ""
    hits = []
    _st_ok = _status_din_registru(inc_blk)
    for m in HALLU.finditer(d):
        if term_excused(d, m):
            continue
        if _st_ok and m.group("status"):
            continue
        hits.append(" ".join(m.group(0).split()))
        break
    nctx = re.sub(r"\s+", "", deacc(ctx or ""))
    for md in HALLU_DIM.finditer(d):
        if re.sub(r"\s+", "", deacc(md.group(0))) not in nctx:
            hits.append("dimensiune inventată: " + " ".join(md.group(0).split()))
    nums = {_num_int(x) for x in _NUM_RE.findall(ctx or "")}
    for mp in HALLU_PRICE.finditer(d):
        val = _num_int(mp.group(1) or mp.group(2))
        if val and val not in nums:
            hits.append("preț inventat: " + " ".join(mp.group(0).split()))
    dctx = re.sub(r"\D", "", ctx or "")
    for mt in _PHONE_RE.finditer(d):
        raw = " ".join(mt.group(0).split())
        dg = re.sub(r"\D", "", raw)
        if not (9 <= len(dg) <= 15) or _DATE_LIKE.match(raw):
            continue
        if dg[-9:] not in dctx and not our_phone(raw):
            hits.append("telefon inventat: " + raw)
    return hits
# ȘABLOANE SIGURE (când LLM-ul tot fabrică) — pe LIMBA tichetului; în română un client bulgar primea text românesc.
# Salutul stă separat: pe comentarii publice regula CS interzice salutul de deschidere (vezi SYSTEM).
SAFE_GREET = {"ro": "Bună ziua!", "bg": "Здравейте!", "hu": "Jó napot kívánok!", "sk": "Dobrý deň!",
              "cz": "Dobrý den!", "pl": "Dzień dobry!", "hr": "Dobar dan!", "en": "Hello!"}
SAFE_BODY = {   # (presale/comandă nouă, orice ține de o comandă)
    "ro": ("Vă mulțumim pentru interes. Vă revin cu detaliile exacte cât mai curând; între timp puteți vedea informațiile actualizate și pe site. Vă mulțumim!",
           "Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat? Revin imediat cu detaliile. Vă mulțumesc!"),
    "bg": ("Благодарим Ви за интереса! Ще се върна с точните детайли възможно най-скоро; междувременно можете да видите актуалната информация и на сайта.",
           "За да проверя точно Вашата поръчка, бихте ли ми казали номера на поръчката или телефонен номер, свързан с нея? Веднага се връщам с детайлите."),
    "hu": ("Köszönjük az érdeklődést! Hamarosan jelentkezem a pontos részletekkel; addig is a weboldalunkon megtalálja a friss információkat.",
           "Hogy pontosan utána tudjak nézni a rendelésének, megadná a rendelés számát vagy egy hozzá tartozó telefonszámot? Azonnal jelentkezem a részletekkel."),
    "sk": ("Ďakujeme za záujem! Čoskoro sa Vám ozvem s presnými informáciami; medzitým nájdete aktuálne údaje aj na našej stránke.",
           "Aby som mohol presne preveriť Vašu objednávku, napíšete mi prosím číslo objednávky alebo telefónne číslo, ktoré je k nej priradené? Hneď sa Vám ozvem s detailmi."),
    "cz": ("Děkujeme za zájem! Brzy se Vám ozvu s přesnými informacemi; mezitím najdete aktuální údaje i na našich stránkách.",
           "Abych mohl přesně prověřit Vaši objednávku, napíšete mi prosím číslo objednávky nebo telefonní číslo, které k ní patří? Hned se Vám ozvu s detaily."),
    "pl": ("Dziękujemy za zainteresowanie! Wkrótce wrócę z dokładnymi informacjami; w międzyczasie aktualne dane znajdą Państwo na naszej stronie.",
           "Abym mógł dokładnie zweryfikować Państwa zamówienie, proszę o numer zamówienia lub powiązany z nim numer telefonu. Wracam od razu ze szczegółami."),
    "hr": ("Hvala na interesu! Uskoro se javljam s točnim informacijama; u međuvremenu aktualne podatke možete vidjeti i na našoj stranici.",
           "Kako bih točno provjerio Vašu narudžbu, možete li mi reći broj narudžbe ili telefonski broj vezan uz nju? Odmah se javljam s detaljima."),
    "en": ("Thank you for your interest! I will come back with the exact details as soon as possible; in the meantime you can also see the updated information on our website.",
           "So that I can look into your order precisely, could you tell me the order number or a phone number linked to it? I will come back with the details right away."),
}
def safe_template(lang, cat, is_public=False):
    """Șablonul onest, în limba tichetului. Pe canal PUBLIC fără salut de deschidere (regula CS de la comentarii)."""
    l = lang if lang in SAFE_BODY else "en"
    body = SAFE_BODY[l][0 if cat in ("presale_intrebare", "comanda_noua") else 1]
    return body if is_public else (SAFE_GREET[l] + " " + body)
def has_order_data(od_ctx):
    """True dacă în context CHIAR avem comenzi (nu lean / nu redactat)."""
    s = (od_ctx or "")
    return bool(s) and "nicio comandă" not in s and "ascunse" not in s and "lean" not in s.lower()

# ---- SEMNALE DE INCIDENT (ce primește modelul pe canal PUBLIC în locul comenzilor) ----
# Pe comentariu FB/IG datele de comandă rămân ascunse: cine comentează poate să NU fie clientul.
# Dar un INCIDENT (ex. colete plecate cu o bucată în loc de mai multe) e informație despre NOI,
# nu despre o persoană → se poate spune public fără să confirme nimic despre comentator.
# ⛔ Semnalele despre PERSOANĂ („are comandă la noi: DA/NU”) NU trec public, deliberat: răspunsul
# ar arăta vizibil altfel pe DA față de NU, iar diferența singură confirmă relația de client către
# oricine comentează — inclusiv către cineva care nu e clientul. Public = DOAR despre incident.
INCIDENT_SOURCE = None   # hook: f(store_name, cat, text) -> dict; None = fără semnale → context ca azi
# orice ar identifica o persoană/comandă e scos ÎNAINTE de prompt (sursa se scrie separat, nu ne bazăm pe ea)
# `(?<!\d)\d{4,}(?!\d)` in loc de `\b\d{4,}\b`: granita de CUVANT nu exista intre litera si cifra,
# deci „BG00042" scris de om intr-un camp liber din registru trecea NEATINS in promptul public.
# Granita de CIFRA vede blocul de cifre si lipit de litere.
INCIDENT_PII = re.compile(r"[\w.+-]+@[\w-]+\.\w+|(?<!\d)\d{4,}(?!\d)|\b\d{2,4}\s*(lei|ron|bgn|huf|pln|czk|eur)\b", re.I)

def incident_signals(store_name, cat, text=""):
    """Semnale NE-IDENTIFICABILE despre un incident cunoscut, pt contextul draftului.

    Sursa e pluggable (`INCIDENT_SOURCE`, completată separat): f(store_name, cat, text) -> dict cu
    {cunoscut: bool, remediat: bool, descriere: str, ce_facem: str}. Întoarce {} când nu există
    sursă, când sursa crapă sau când incidentul nu e cunoscut → comportamentul de azi, neschimbat.
    Câmpurile de text care conțin PII (email, nr comandă, AWB/telefon, sume) sunt ARUNCATE, nu
    trunchiate: sursa nu are voie să strecoare date de client în ceva ce se poate spune public."""
    src = INCIDENT_SOURCE
    if not callable(src):
        return {}
    try:
        raw = src(store_name, cat, text) or {}
    except Exception:
        return {}
    if not isinstance(raw, dict) or not raw.get("cunoscut"):
        return {}
    sig = {"cunoscut": True, "remediat": bool(raw.get("remediat"))}
    for k in ("descriere", "ce_facem"):
        v = " ".join(str(raw.get(k) or "").split())[:180]
        if v and not INCIDENT_PII.search(v) and not ORDER_RE.search(v):
            sig[k] = v
    return sig

def incident_block(sig, is_public):
    """Blocul de context cu semnalele de incident. '' fără semnale → contextul rămâne ca azi."""
    if not sig:
        return ""
    ln = ["", "", "SEMNALE INCIDENT (despre NOI, NU despre client — NU confirmă că cel care scrie e clientul nostru):",
          "    • incident cunoscut la noi: %s" % ("DA" if sig.get("cunoscut") else "NU"),
          "    • deja remediat: %s" % ("DA" if sig.get("remediat") else "NU")]
    if sig.get("descriere"):
        ln.append("    • despre ce e vorba: %s" % sig["descriere"])
    if sig.get("ce_facem"):
        ln.append("    • ce facem: %s" % sig["ce_facem"])
    if is_public:
        ln.append("    ⚠️ PUBLIC: recunoaște DESCHIS incidentul (e despre noi) și spune dacă e remediat, dar NU spune "
                  "„comanda dumneavoastră”/„sunteți afectat”/„am găsit comanda” și NU confirma că persoana e clientul "
                  "— invit-o în privat cu numărul comenzii ca să verificăm exact cazul ei.")
    return "\n".join(ln)
def categorize_hint(blob, channel=None):
    t = deacc(blob)
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    if channel in ("facebook_feed_comment", "instagram_comment"):
        return "comentariu_social"
    return "altele"

NEG = ["teapa", "prost", "groaznic", "dezamagit", "nu functioneaza", "nu aspira", "stricat", "defect", "rusine",
       "inadmisibil", "scandal", "anpc", "oribil", "jignit", "nervos", "furios", "niciodata", "reclamatie", "escroc",
       "bataie de joc", "hoti", "inselat", "nu am primit", "intarzi", "nemultumit", "awful", "broken", "scam", "refuz",
       "nu mi plac", "nu corespunde"]
# „calitate" SINGUR nu e pozitiv — e substantiv NEUTRU: „calitate foarte proastă"/„calitate zero" îl
# conțin la fel de bine, iar pe tichete reale asta scotea reclamația drept „pozitiv". Se cere
# calificativul, iar formele negative intră în NEG (la fel „jakość" în poloneză, mai jos).
POS = ["recomand", "multumesc", "super", "excelent", "minunat", "perfect", "iubesc", "frumos", "rapid",
       "de calitate", "buna calitate", "calitate buna", "calitate excelenta", "calitate superioara",
       "raport calitate", "am lesinat", "bravo", "felicitari", "ador", "miroase foarte bine", "multumita"]
NEG += ["calitate proasta", "proasta calitate", "calitate slaba", "calitatea slaba", "calitate zero",
        "proast", "nu recomand", "porcarie", "zla jakosc", "slaba jakosc", "kiepska jakosc",
        "okropna jakosc", "fatalna jakosc",
        # notificarea Judge.me de recenzie 1-2 stele e text FIX, deci semnal sigur de reclamație
        "following 1 star review", "following 2 star review"]
# Listele de mai sus sunt doar în română, deci TOATE tichetele străine ieșeau „neutru" — iar pe
# comentariu public regula de platformă cere emoji, adică puneam un emoji vesel sub o acuzație de
# înșelătorie. Aceleași categorii, pe bg/hu/sk/cz/pl (text trecut prin deacc()).
# Potrivirea e pe SUBȘIR (t.count), deci pozitivele care se prefixează cu negație („nespokojna"
# conține „spokojn") se scriu cu SPAȚIU în față, altfel se anulează reciproc cu perechea negativă.
NEG += ["не работи", "не върши", "счупен", "счупи", "дефект", "не получих", "не пристигна", "забавя",
        "недоволен", "недоволна", "недопустим", "ужасно", "разочарован", "не отговаря", "връщам",
        "боклук", "никаква полза", "лошо качество", "жалба", "адвокат", "прокуратура", "заведа дело",
        "измам", "подигра", "срам",
        "nem mukodik", "torott", "hibas", "nem kaptam", "nem erkezett", "kesik", "keses", "csalodott",
        "szornyu", "visszakuldom", "elegedetlen", "rossz minoseg", "hasznalhatatlan", "panasz",
        "ugyved", "beperel", "feljelentes", "csalas", "szegyen", "atverte", "felhaborito",
        "nefunguje", "rozbit", "poskozen", "poskoden", "pokazen", "vadn", "neobdrz", "nedosta",
        "nedorazil", "zpozden", "omeskan", "zklaman", "sklaman", "hrozne", "vracim", "nespokojen",
        "neodpovida", "nepouzitelne", "podvod", "hanba", "okrad", "nehorazn", "advokat", "zalob",
        "trestn", "stiznost", "staznost",
        "nie dziala", "zepsut", "uszkodzon", "wadliw", "nie otrzyma", "nie dostal", "nie dotarl",
        "opoznien", "rozczarowan", "okropne", "zwracam", "niezadowolon", "nie odpowiada",
        "bezuzyteczne", "oszust", "wstyd", "skandal", "skarg", "uokik", "adwokat", "pozew", "policj",
        "ne radi", "pokvaren", "ostecen", "nisam dobio", "nisam primila", "nije stiglo", "kasni",
        "razocaran", "uzasno", "vracam", "nezadovoljan", "nezadovoljna", "losa kvaliteta", "prevara",
        "lopov", "sramota", "odvjetnik", "tuzba", "zalba"]
POS += ["препоръчвам", "благодаря", "отлич", "прекрас", "перфект", "обожавам", "красив", "качествен",
        "добро качество", "бърз", "браво", "страхот", "чудес", "харесва", " доволен", " доволна",
        "ajanlom", "koszonom", "kivalo", "csodalatos", "tokeletes", "imadom", "gyonyoru", "minosegi",
        "gyors", "gratulalok", "nagyon szep", "remek", " elegedett",
        "doporucuji", "odporucam", "dekuji", "dakujem", "vynikajici", "vynikajuce", "uzasne",
        "perfektni", "perfektne", "miluji", "krasne", "kvalitn", "rychle", "skvele", "paradni",
        " spokojn",
        "polecam", "dziekuje", "swietn", "doskonal", "wspanial", "idealn", "uwielbiam", "piekn",
        "dobra jakosc", "swietna jakosc", "wysoka jakosc", "super jakosc", "szybk", "rewelacyj", " zadowolon",
        "preporucujem", "hvala", "odlicno", "savrseno", "predivno", "obozavam", "lijepo", "kvalitetno",
        "brzo", " zadovoljan", " zadovoljna"]
# Potrivirea era pe SUBȘIR BRUT (`t.count(w)`), deci un termen pozitiv bifa și în interiorul unei
# fraze NEGATIVE: „nie polecam" bifa „polecam", „nedoporučuji" bifa „doporucuji", „nu recomand" bifa
# „recomand". Se potrivește pe ÎNCEPUT DE CUVÂNT (termenii sunt rădăcini, deci sufixul rămâne liber)
# și se anulează pozitivul precedat imediat de negație — caz în care afirmația e, de fapt, NEGATIVĂ.
# Căutarea rămâne `str.find` (C, rapid — un regex cu ~190 de alternative e de 13x mai lent pe
# aceleași tichete), dar cu verificarea că termenul începe la ÎNCEPUT DE CUVÂNT.
def _potriviri(t, lista):
    """[(pozitie, termen)] pentru termenii din listă care apar la ÎNCEPUT DE CUVÂNT în t (deja deacc)."""
    out = []
    for w in lista:
        w = w if w.startswith(" ") else w.strip()
        if not w:
            continue
        i = t.find(w)
        while i >= 0:
            # granița e „nu în interiorul unui CUVÂNT"; o CIFRĂ înainte nu face parte din rădăcină,
            # iar clienții scriu lipit („…am primit doar 1bataie de joc") — măsurat pe tichete reale
            if w[0] == " " or i == 0 or not (t[i - 1].isalpha() or t[i - 1] == "_"):
                out.append((i, w))
            i = t.find(w, i + 1)
    return out


# negația trebuie să fie LIPITĂ de termenul pozitiv (cel mult un cuvânt scurt între), altfel un „nu"
# din altă propoziție ar anula un „recomand" care chiar e pozitiv.
_NEGATIE = re.compile(r"(?:\bnu\b|\bn-|\bnici\b|\bdeloc\b|\bnie\b|\bne\b|\bnem\b|\bsem\b|\bне\b|"
                      r"\bnije\b|\bnot\b|\bno\b|\bdon'?t\b)[\s\w'-]{0,8}$", re.I)


def neg_hits(t):
    """Marcajele NEGATIVE din text (deja trecut prin deacc), pe cuvânt întreg."""
    return [w for _, w in _potriviri(t, NEG)]


def sentiment(text):
    t = deacc(text)
    n = len(neg_hits(t))
    p = 0
    for i, w in _potriviri(t, POS):
        if _NEGATIE.search(t[max(0, i - 12): i]):
            n += 1                 # „nu recomand" = afirmație NEGATIVĂ, nu pozitivă
        else:
            p += 1
    excl = t.count("!"); caps = sum(1 for c in text if c.isupper())
    lab = "negativ" if n > p else ("pozitiv" if p > n else "neutru")
    score = n + p + excl // 2 + (1 if caps > 15 else 0)
    inten = "puternic" if (score >= 3 or excl >= 2 or caps > 20) else ("mediu" if score >= 1 else "slab")
    return lab, inten

# ---- REGISTRU de politețe, PE LIMBĂ ----
# „FORMAL, la PLURAL" e adevărat doar pe ro/bg/cz/sk/hr. În POLONEZĂ formalul e „Pan/Pani" + verb la
# persoana a III-a SINGULAR, iar persoana a II-a plural („wy/używacie") către un singur adult sună
# arhaic-PRL, aproape nepoliticos — un draft real a ieșit cu „często go używacie" către o clientă care
# scrisese despre ea la singular. Maghiara la fel: „Ön" + persoana a III-a singular.
REGISTER = {
    "ro": 'română — plural de politețe: „dumneavoastră/vă" („Vă rugăm", „Vă informăm"). NICIODATĂ „tu/ție/te".',
    "bg": 'bulgară — persoana a II-a plural, „Вие/Ви/Ваш" cu majusculă („Моля, изпратете ни…"). NU „ти".',
    "cz": 'cehă — vykání, persoana a II-a plural: „Vy/Vám/Vás" („Můžete nám prosím poslat…"). NU „ty".',
    "sk": 'slovacă — vykanie, persoana a II-a plural: „Vy/Vám/Vás" („Môžete nám prosím poslať…"). NU „ty".',
    "hr": 'croată — persoana a II-a plural, „Vi/Vam/Vas" cu majusculă. NU „ti".',
    "pl": 'poloneză — ATENȚIE, formalul NU e persoana a II-a plural: „Pan/Pani" + verb la persoana a III-a SINGULAR („Czy Pani często go używa?", „Prosimy Pana o numer zamówienia"). „wy/używacie/wasze" către o singură persoană e GREȘIT (sună arhaic-PRL, aproape nepoliticos).',
    "hu": 'maghiară — ATENȚIE, formalul NU e persoana a II-a plural: „Ön" + verb la persoana a III-a SINGULAR („Ön mikor rendelte?", „Kérjük, adja meg a rendelés számát"). „ti/rendeltetek" către o singură persoană e GREȘIT.',
    "en": 'engleză — ton formal-politicos („Could you please…"), fără familiarisme.',
}
REGISTER_DEFAULT = ("registrul politicos standard AL LIMBII — NU presupune persoana a II-a plural, "
                    "verifică forma corectă a limbii respective; fără familiarisme.")
NO_CALQUE = ('LIMBA ≠ TRADUCERE DIN ROMÂNĂ: scrie direct în limba clientului, cu termenul UZUAL al pieței; NU calchia '
             'expresii românești. Ex. în poloneză un obiect de bucătărie e „przyrząd/przybory kuchenne", NU „instrument" '
             '(acolo „instrument" = instrument muzical / aparat de precizie). Dacă nu ești sigur de termenul local, '
             'folosește o formulare simplă și uzuală, nu o traducere literală.')


def register_rule(lang):
    """Regula de POLITEȚE a limbii în care se scrie draftul (pl/hu NU sunt la plural)."""
    return REGISTER.get((lang or "").strip().lower(), REGISTER_DEFAULT)


# Regula de registru era DOAR în prompt — spre deosebire de emoji și de canalul reclamat, nimic nu
# verifica DUPĂ generare că draftul polonez chiar zice „Pan/Pani" și nu „wy/…cie". Aici se caută
# exact formele INFORMALE, enumerate: un „\w+cie" generic ar fi prins substantive uzuale („zdjęcie",
# „życie"), iar un „\w+sz" maghiar ar fi prins „rész"/„egész". Trecutul polonez la persoana a II-a
# plural se termină însă MEREU în „-liście/-łyście", deci ăla merge ca tipar (cu ≥3 litere înainte,
# ca să nu prindă substantivul „liście").
INFORMAL_RE = {
    # ROMÂNA — limba a 456 din 520 de drafturi, și singura care n-avea gardă. Măsurat pe 516 drafturi
    # RO reale din coadă: 48 (9,3%) se adresează informal („Îți mulțumim", „Te rog", „răspunsul tău"),
    # iar garda întorcea gol pe TOATE. Se caută pe textul FĂRĂ diacritice, cu două capcane tratate:
    # „ține" (verb) devine „tine" la de-accentuare — de aia „tine" se numără DOAR după prepoziție
    # („pentru tine"), altfel „vă vom ține la curent" ieșea informal (3 fals-pozitive pe cele 516);
    # iar „ai" e și articol genitival („utilizatori ai Serviciilor", „ai noștri") — exclus prin ce
    # urmează după el. Rezultat: 48/48 prinse, 0 fals-pozitive pe cele 468 de drafturi formale.
    "ro": re.compile(
        r"\b(?:tu|tie|te|ti|iti|tau|ta|tale|tai|esti|vrei|poti|doresti|primesti|stii|vei)\b"
        r"|\bne-ai\b|\bte-|\bti-"
        r"|\bai\b(?!\s+(?:nostri|tai|sai|lor|acelor|acestor|unor|multor|\w+(?:lor|ilor|urilor)\b))"
        r"|(?:la|pentru|cu|de|despre|langa|catre|asupra|in|pe|fara|intre|dintre)\s+tine\b"
        r"|\bnu ezita\b"
        r"|\b(?:scrie|spune|trimite|contacteaza|anunta|arata|lasa|suna|da|zi)-(?:ne|mi|ti)\b", re.I),
    # POLONEZA — pluralul „wy/…cie" (mai jos) era deja prins, dar modelul nu-l produce: el calchiază
    # „tu" din română și iese SINGULARUL informal („masz", „użyłeś", „Ci", „Twój", „poinformuj").
    # Măsurat pe text REAL de piață (politica de retur bonhaus.pl, inpost.pl, dpd.com.pl, allegro,
    # ceneo, regulaminele InPost): 0/8 înainte. Terminația generică „-esz/-asz/-isz/-ysz" e sigură în
    # poloneză (singurele non-verbe din 66 de forme găsite în corpus: „nasz"/„wasz" — în stop-listă).
    "pl": re.compile(
        r"\b(?:wy|was|wam|wami|wasz(?:a|e|ego|emu|ej|ym|ych|ymi|y)?)\b"
        # „macie" e și locativul lui „mata" („na macie" = pe covoraș) — la un magazin de casă apare real
        r"|\b(?:jeste[sś]cie|(?<!\bna )(?<!\bpo )(?<!\bw )(?<!\bwe )(?<!\bo )(?<!\bprzy )macie|"
        r"mo[zż]ecie|chcecie|u[zż]ywacie|wiecie|pami[eę]tacie|dostaniecie|"
        r"otrzymacie|podacie|sprawdzicie|czekacie|widzicie|robicie|b[eę]dziecie|piszecie|dzwonicie|"
        r"kupujecie|zamawiacie|przepraszacie)\b"
        r"|\b\w{3,}(?:li[sś]cie|[łl]y[sś]cie)\b"
        r"|\b(?:napiszcie|podajcie|sprawd[zź]cie|prze[sś]lijcie|wy[sś]lijcie|zadzwo[nń]cie|"
        r"pami[eę]tajcie|zr[oó]bcie|dajcie|we[zź]cie|poczekajcie|wybaczcie|powiedzcie|kupcie|"
        r"zam[oó]wcie)\b"
        # ↓ SINGULARUL informal (partea nouă)
        r"|\b(?:ty|ciebie|tobie|ci[eę]|tw[oó]j|twoja|twoje|twojego|twojemu|twoj[aą]|twoim|twoimi|"
        r"twoich|twojej|twoi)\b"
        r"|\bCi\b"
        r"|\b(?:masz|mo[zż]esz|chcesz|jeste[sś]|wiesz|musisz|potrzebujesz|korzystasz|zgadzasz|"
        r"pami[eę]taj)\b"
        r"|\b\w{3,}(?:esz|asz|isz|ysz)\b"
        r"|\b\w{2,}[łl](?:e[sś]|a[sś])\b"
        r"|\b\w{2,}[łl]by[sś]\b"
        r"|\b(?:sprawd[zź]|wybierz|podaj|napisz|zaloguj|wype[łl]nij|pobierz|poznaj|odkryj|zam[oó]w|"
        r"skorzystaj|znajd[zź]|odbierz|wy[sś]lij|prze[sś]lij|zadzwo[nń]|kliknij|wpisz|wprowad[zź]|"
        r"zobacz|dowiedz|skontaktuj|poinformuj|zapisz|dodaj|zaznacz|potwierd[zź]|zr[oó]b|poczekaj|"
        r"[sś]led[zź]|nadaj|chro[nń]|zg[łl]o[sś]|od[sś]wie[zż]|zapoznaj)\b", re.I),
    # MAGHIARA — la fel: „ti/…tek" (plural) nu e ce produce modelul; el scrie „te/…sz/…od".
    # Terminațiile sunt ambigue în maghiară, deci fiecare tipar e verificat pe corpus real
    # (bonhaus.hu ÁSZF, posta.hu, GLS, FoxPost, Packeta): „-sz" prinde și substantive („panasz",
    # „válasz" — stop-listă, plus sufixele -ász/-ész/-esz/-usz), „-tál/-tél" fără accent ar prinde
    # postpoziția „által" și cazul instrumental („szolgálattal") — de aia se cere ACCENTUL, iar
    # „-nál/-nél" generic (condiționalul) a fost SCOS: prindea „azonnal", „használ", „Postánál".
    "hu": re.compile(
        r"\b(?:te|ti|t[eé]ged|titeket|neked|nektek|veled|veletek|ti[eé]d|ti[eé]tek|n[aá]lad|hozz[aá]d|"
        r"r[oó]lad|t[oő]led|benned|[eé]rted|magad|magadat)\b"
        r"|\b(?:tudod|tudsz|kapsz|kapod|kapt[aá]l|rendelt[eé]l|rendelsz|[ií]rt[aá]l|[ií]rsz|[ií]rd|"
        r"k[üu]ldesz|k[üu]ldd|k[üu]ldt[eé]l|n[eé]zd|n[eé]zted|megkaptad|elk[üu]ldted|akarsz|"
        r"szeretn[eé]l|k[eé]rsz|k[eé]rlek|v[aá]rj|leszel|l[aá]tod|l[aá]tsz)\b"
        r"|\b\w{3,}(?:tetek|tatok|j[eé]tek)\b"
        # ↓ SINGULARUL informal (partea nouă)
        r"|\b\w{3,}sz\b"
        r"|\b\w{2,}(?:od|ed|öd)(?:at|et|ra|re|r[oó]l|r[oő]l|ban|ben|ba|be|hoz|hez|höz|t[oó]l|t[oő]l|"
        r"b[oó]l|b[oő]l|ig)?\b"
        r"|\b\w{3,}(?:aid|eid|jaid|jeid|aidat|eidet)\b"
        r"|\b\w{3,}(?:sd|dd|zd|ld)\b"
        r"|\b\w{3,}t[áé]l\b"
        r"|\b(?:k[eé]rn[eé]l|tudn[aá]l|adn[aá]l|j[oö]nn[eé]l|menn[eé]l|lenn[eé]l|k[üu]lden[eé]l|"
        r"[ií]rn[aá]l|v[aá]lasztan[aá]l)\b"
        r"|\b(?:kattints|keresd|olvasd|jelentkezz|l[eé]pj|h[ií]vj|figyelj|t[oö]ltsd|nyisd|menj|gyere|"
        r"fogadd|iratkozz|add|vedd|n[eé]zz|pr[oó]b[aá]ld|h[ií]vd|gondold|csukd|fizesd|l[aá]togass|"
        r"ne habozz|tedd|mondd|k[eé]rd|z[aá]rd|vidd|hozd|indulj|regisztr[aá]lj|ellen[oő]rizd|adj|"
        r"k[eé]rj|k[üu]ldj|[ií]rj|olvass|v[aá]lassz)\b", re.I),
    # BULGARA / CEHA / SLOVACA / CROATA — REGISTER (regula din prompt) exista pe 8 limbi, dar
    # verificarea DUPĂ generare exista doar pe ro/pl/hu. Adică pe 4 din 7 limbi vii NIMENI nu
    # verifica adresarea, iar tabelul de acoperire raporta „n/a" — ceea ce se citește ca „nu se
    # aplică", nu ca „gaură". Formele de mai jos sunt ÎNCHISE (enumerate), nu tipare generale:
    # în limbile slave terminația de persoana a II-a singular „-š" e omonimă cu substantive uzuale
    # („člověk vs …"), iar un tipar generic ar fi repetat exact greșeala din pl/hu.
    # Fiecare excludere de mai jos vine dintr-un fals-pozitiv VĂZUT pe text real de piață:
    #   bg: „те" e și „ei" (persoana a III-a plural) — SCOS, altfel orice „те са" ieșea informal;
    #   cz: „ty" e și demonstrativ („ty knihy") — SCOS, se merge pe cazurile oblice și pe verbe;
    #   sk: „si" e și reflexivul dativ din adresarea FORMALĂ („môžete si vybrať") — SCOS.
    "bg": re.compile(
        r"\b(?:ти|теб|тебе|тво[йяеи]\w{0,3})\b"
        r"|\b(?:имаш|можеш|искаш|знаеш|видиш|трябваш|получиш|напишеш|"
        r"изпратиш|потърсиш|провериш|свържеш|ще получиш|си получил)\b"
        r"|\b(?:напиши|изпрати|провери|обади|кажи|изчакай|посочи|свържи)"
        r"\s+(?:ми|ни|се)\b", re.I),
    "cz": re.compile(
        r"\b(?:tebe|tob[ěe]|tebou|tv[ůu]j|tvoje|tvoji|tv[áa]|tv[ée]|tv[ýy]m|tv[ýy]ch|tv[ée]mu|tv[ée]ho)\b"
        r"|\b(?:m[áa][šs]|m[ůu][žz]e[šs]|chce[šs]|jsi|v[íi][šs]|mus[íi][šs]|bude[šs]|dostane[šs]|"
        r"po[šs]le[šs]|napi[šs]e[šs]|najde[šs]|obdr[žz][íi][šs]|potvrd[íi][šs]|po[šs]li)\b"
        # terminația generică de persoana a II-a singular. Cere ș REAL (š): varianta „s" a prins
        # „cookies" pe 14 fraze FORMALE de pe ozbrojtese.cz — orice cuvânt în „-es" ieșea informal.
        r"|\b\w{3,}(?:[áa]š|[íi]š|[eě]š)\b"
        r"|\b(?:napi[šs]|za[šs]li|zkontroluj|ov[ěe][řr]|zavolej|po[čc]kej|pod[íi]vej|klikni|zadej|"
        r"uveď|sd[ěe]l|omluv)\b", re.I),
    "sk": re.compile(
        r"\b(?:ty|teba|tebe|tebou|tvoj|tvoja|tvoje|tvoju|tvojho|tvojej|tvoji|tvojim|tvojich|tvojmu)\b"
        r"|\b(?:m[áa][šs]|m[ôo][žz]e[šs]|chce[šs]|vie[šs]|mus[íi][šs]|bude[šs]|dostane[šs]|"
        r"po[šs]le[šs]|nap[íi][šs]e[šs]|n[áa]jde[šs]|potvrd[íi][šs])\b"
        r"|\b(?:nap[íi][šs]|po[šs]li|za[šs]li|skontroluj|zavolaj|po[čc]kaj|pozri|klikni|zadaj|"
        r"uveď|ozn[áa]m|prep[áa][čc])\b", re.I),
    "hr": re.compile(
        r"\b(?:ti|tebe|tebi|tobom|tvoj|tvoja|tvoje|tvog|tvoga|tvojem|tvojoj|tvoji|tvojim|tvojih|tvoju)\b"
        r"|\b(?:ima[šs]|mo[žz]e[šs]|ho[ćc]e[šs]|[žz]eli[šs]|jesi|zna[šs]|mora[šs]|vidi[šs]|"
        r"dobije[šs]|dobit [ćc]e[šs]|po[šs]alje[šs]|napi[šs]e[šs]|na[đd]e[šs]|treba[šs])\b"
        r"|\b(?:napi[šs]i|po[šs]alji|provjeri|nazovi|pri[čc]ekaj|pogledaj|klikni|odaberi|unesi|"
        r"javi|prijavi|oprosti)\b", re.I),
}
# Cuvinte care conțin ÎNTÂMPLĂTOR secvența căutată, dar nu sunt adresare informală. Fiecare intrare
# vine dintr-un fals-pozitiv VĂZUT pe corpusul real de piață, nu din presupuneri.
INFORMAL_STOP = {
    "pl": {"nasz", "wasz", "tomasz", "towarzysz", "kapelusz", "grosz", "kosz", "klosz"},
    # cehă: substantive/adverbe care se termină în „-áš/-íš/-eš" fără să fie persoana a II-a
    "cz": {"příliš", "tomáš", "lukáš", "mikuláš", "guláš", "aleš", "mateš", "kuleš"},
    "hu": {"panasz", "válasz", "tavasz", "szakasz", "kamasz", "arasz", "vigasz",
           "expressz", "lesz", "vesz", "tesz", "hisz", "visz", "kiterjed", "terjed", "szenved",
           "enged", "ered", "reped", "éled", "feed", "protected", "occurred", "specified",
           "portál", "ügyfélportál", "elítél", "ítél", "kötél", "hotel",
           "mód", "kód", "jód", "eredet", "eredetet",
           "küld", "föld", "hold", "kezd", "old", "zöld"},
}
# sufixe de SUBSTANTIV maghiar peste care „-sz" nu e persoana a II-a („egész", „adathalász", „plusz")
INFORMAL_SUF_STOP = {"hu": re.compile(r"(?:ász|ész|esz|isz|osz|ősz|usz|üsz)$")}
# marcajul INTERN pus pe draftul rămas informal (vezi mark_registru) — liniile lui NU se verifică
MARK_REGISTRU = "⚠️ INTERN — NU SE TRIMITE AȘA:"


# FORMULELE PROPRII ale firmei, publicate VERBATIM în bara de contact a magazinelor („Sună-ne la
# 0376 300 843" pe esteban.ro, „Sună-ne la 0376 300 844" pe grandia.ro, „Sună-ne la 0376 300 585" pe
# gento.ro — citate din paginile live, 15-sep-2026). Sunt imperative de persoana a II-a singular, deci
# ramura imperativă le prindea ca „informale" și trimitea draftul la regenerare / îl marca INTERN.
# Exceptarea e îngustă: formula trebuie să trimită către o cale de contact A NOASTRĂ (numărul nostru,
# WhatsApp/Messenger/privat/e-mail/site) în ACEEAȘI propoziție. „Scrie-ne ce ai pățit" rămâne informal.
_FORMULA_PROPRIE = {"suna-ne", "scrie-ne", "contacteaza-ne"}
_CANAL_PROPRIU_RE = re.compile(
    r"whatsapp|messenger|\bdm\b|in privat|pe privat|mesaj privat|inbox|chat"
    r"|e-?mail|mail-?ul|pe site|pe website|formularul de contact", re.I)


def _formula_proprie(txt, m):
    """Potrivirea e formula de contact PUBLICATĂ de noi (nu o adresare informală a clientului)?"""
    if m.group(0).lower().replace("ă", "a").replace("â", "a") not in _FORMULA_PROPRIE:
        return False
    seg = propozitia_din_jur(txt, m.start(), m.end())
    if _CANAL_PROPRIU_RE.search(seg):
        return True
    return any(our_phone(t.group(0)) for t in _PHONE_RE.finditer(seg))


def informal_register_hits(draft, lang):
    """Formele INFORMALE de adresare din draft, pe limbile cu regulă proprie (ro „dumneavoastră",
    pl „Pan/Pani", hu „Ön"). [] = curat sau limbă fără regulă."""
    lg = (lang or "").strip().lower()
    rx = INFORMAL_RE.get(lg)
    if not rx or not draft:
        return []
    # marcajul intern conține chiar formele reclamate — nu se re-numără (altfel marcajul e „informal")
    txt = "\n".join(l for l in draft.splitlines() if not l.lstrip().startswith(MARK_REGISTRU))
    if lg == "ro":
        txt = deacc(txt)          # „ține"→„tine" e tratat în regex prin prepoziția obligatorie
    stop = INFORMAL_STOP.get(lg, ())
    suf = INFORMAL_SUF_STOP.get(lg)
    out = set()
    for m in rx.finditer(txt):
        w = m.group(0).lower()
        if w in stop or (suf and " " not in w and suf.search(w)):
            continue
        if lg == "ro" and _formula_proprie(txt, m):
            continue
        out.add(w)
    return sorted(out)


def mark_registru(draft, hits):
    """Marchează VIZIBIL un draft rămas în registrul informal (regenerarea a eșuat sau n-a existat).
    Fără marcaj, agentul CS vede un draft normal și îl trimite așa. Idempotent."""
    if not draft or draft.lstrip().startswith(MARK_REGISTRU):
        return draft
    return ("%s draftul de mai jos i se adresează clientului INFORMAL (%s), iar rescrierea automată "
            "nu a reușit. Corectați adresarea în forma politicoasă a limbii înainte de trimitere.\n\n%s"
            % (MARK_REGISTRU, ", ".join(hits[:6]), draft))


def ctx_lang(ctx):
    """Limba draftului, citită din linia REGISTRU a contextului (singurul loc unde e scrisă)."""
    m = re.search(r"REGISTRU \(([a-z]{2})\)", ctx or "")
    return m.group(1) if m else ""


def ctx_inc_blk(ctx):
    """Blocul de FAPTE VERIFICATE (registru de incidente) așa cum a fost lipit în context.

    Pe calea de regenerare din coadă (do_approve) nu mai avem `inc_blk` ca variabilă, iar
    `hallu_hits` era chemat FĂRĂ el — adică exceptarea de STATUS acoperită de o retrimitere
    confirmată nu se aplica acolo și un draft CORECT conform registrului era respins. Blocul stă
    lipit la coada COMENZILOR CLIENTULUI, deci se poate citi înapoi din ctx."""
    c = ctx or ""
    i = c.find("INCIDENT CUNOSCUT")
    if i < 0:
        return ""
    j = c.find("\n\nA MAI SCRIS PE:", i)
    return c[i:] if j < 0 else c[i:j]


# ---- EMOJI condiționat ----
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3\u2049\u203C]")
# Semnale de FRAUDĂ/acuzație publică pe piețele străine — ESCAL/ANGER_RE sunt scrise în română, deci moarte
# pe BG/HU/SK/PL/CZ/HR. Garda de emoji nu are voie să depindă doar de clasificatoarele RO.
FRAUD_INTL_RE = re.compile(
    r"измам|мошени|некоректн|не поръчвайте|не купувайте|крадц"
    r"|oszust|naciag|naciąg|zlodziej|złodziej|nie zamawiajcie|nie kupujcie"
    r"|podvod|zlodej|zloděj|nekupujte|neobjednavejte|neobjednávejte|neobjednávajte"
    r"|csalas|csalás|atver|átver|szelhamos|szélhámos|ne rendelj"
    r"|prevara|lopov|ne narucujte|ne naručujte"
    r"|\bscam\b|\bfraud\b|rip[- ]?off|do(?:n.?t| not) order", re.I)


# ANGER_RE conține „!!!" — corect ca semnal de FURIE, dar în română „Mulțumesc!!!" e ENTUZIASM.
# Pe sentiment POZITIV semnele de exclamare nu mai sunt motiv de interdicție; cuvintele de furie, da.
ANGER_WORDS_RE = re.compile(ANGER_RE.pattern.replace("!!!|", "", 1), re.I)


def emoji_allowed(sent_lab, is_esc, text):
    """Emoji DOAR pe teren neutru/pozitiv. Măsurat pe piața BG: 😊 pus sub „Коректност 0!!! Не поръчвайте!!!"
    și 🙏 sub o acuzație publică de înșelătorie se citesc ca BATJOCURĂ."""
    if is_esc:
        return False
    lab = (sent_lab or "").strip().lower()
    if lab.startswith("negativ"):
        return False
    d = deacc(text or "")
    if ESCAL.search(d) or FRAUD_INTL_RE.search(d):
        return False
    # Client POZITIV și fără niciun marcaj NEG → „!!!" nu mai e veto, doar cuvintele de furie sunt
    # (măsurat pe 3.416 comentarii RO cu sentiment pozitiv: 129 blocate → 6; „Foarte bune, recomand
    # cu încredere!!!" primea „emoji INTERZIS"). Dacă textul ARE totuși un marcaj NEG, sentimentul pe
    # numărare de cuvinte a greșit („Nu sunt bune deloc!!! … calitate") → „!!!" rămâne veto (7 cazuri).
    curat = lab.startswith("pozitiv") and not neg_hits(d)
    return not (ANGER_WORDS_RE if curat else ANGER_RE).search(d)


def strip_emoji(s):
    """Scoate emoji-urile și curăță spațiile rămase — NU rescrie textul."""
    out = EMOJI_RE.sub("", s or "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?;:])", r"\1", out)
    out = "\n".join(ln.strip() for ln in out.splitlines())
    return out.strip()


# ---- CANAL RECLAMAT (gardă de după generare, nu doar regulă în prompt) ----
NO_ANSWER_RE = re.compile(
    # „nu răspunde" e ambiguu în română: „nu răspunde DESCRIERII" = nu corespunde, nu e canal mort.
    r"nu (?:imi |ne |mi )?raspund(?:eti|eau|em|ea|e)?\b(?! ?(?:la )?(?:descrierii|descrierea|asteptarilor|cerintelor|standardelor|realitatii|adevarului|calitatii|nevoilor|pozei|imaginii|anuntului))"
    r"|nimeni nu raspunde|niciun raspuns|fara raspuns|nu se raspunde|nu ma suna nimeni"
    r"|no (?:one |body )?(?:answer|repl|respon)|nobody (?:answers|replies|picks up)|never (?:answer|repl)"
    r"|никой не (?:отговаря|вдига)|не отговаряте|без отговор|няма отговор"
    r"|nikt nie (?:odpowiada|odbiera|odpisuje)|brak odpowiedzi|nie odpowiadacie"
    r"|nikdo neodpov|nikto neodpov|bez odpovedi|bez odpovede|neodpovidate|neodpovídáte|neodpovedate"
    r"|senki nem (?:valaszol|válaszol|veszi fel)|nem valaszol|nem válaszol|nincs valasz|nincs válasz"
    r"|nitko ne odgovara|bez odgovora|ne odgovarate", re.I)
CHANNEL_HINT = {
    "telefon": r"telefon|sun de|am sunat|va sun|suna nimeni|apel|\bphone\b|\bcall(?:ed|ing)?\b|телефон|звъня|обажда"
               r"|dzwoni|telefonu|volal|volam|volám|hiv|hív|zovem|nazvao",
    "email": r"e-?mail|mail-?ul|mailuri|имейл|мейл|maila|mailu|levelet|e-?mailt|mailom",
    "privat": r"in privat|pe privat|mesaj(?:e)? priv|inbox|messenger|\bdm\b|лично съобщение|инбокс"
              r"|prywatn|wiadomo|zprav|zpráv|sprav|správ|uzenet|üzenet|poruk",
}


def complained_channels(text, window=90):
    """Canalele pe care CLIENTUL le reclamă ca nefuncționale („sun de zile și nu răspunde nimeni").
    Multilingv — exact pe piețele străine o gardă scrisă în română nu prinde nimic."""
    t = deacc(text or "")
    out = set()
    for m in NO_ANSWER_RE.finditer(t):
        seg = t[max(0, m.start() - window): m.end() + window]
        for ch, pat in CHANNEL_HINT.items():
            if re.search(pat, seg, re.I):
                out.add(ch)
    return out


REOFFER = {
    "telefon": r"suna[tț]i-?ne|ne pute[tț]i suna|la (?:numarul|telefon)|apela[tț]i|call us|обадете|позвънете"
               r"|zadzwo|prosimy o (?:kontakt )?telefon|zavolajte|zavolejte|hivjon|hívjon|nazovite"
               r"|\b0\d[\d ().-]{7,}\b|\+\d[\d ().-]{8,}",
    "email": r"scrie[tț]i-?ne (?:un )?(?:e-?mail|mail)|pe (?:e-?mail|mail)|trimite[tț]i.{0,20}(?:e-?mail|mail)|email us"
             r"|(?:на|по) (?:имейл|мейл)|напишете ни.{0,15}(?:имейл|мейл)|na (?:e-?mail|maila)|napisz.{0,15}mail"
             r"|e-?mailben|mailom",
    "privat": r"in privat|pe privat|mesaj privat|inbox|messenger|\bdm\b|write us (?:in |on )?(?:private|dm)"
              r"|на лично|prywatn|sukromn|súkromn|uzenetben|üzenetben|privatno",
}


def reoffered_channels(draft, claimed):
    """Canalele RECLAMATE pe care draftul le re-oferă totuși. Promptul singur nu ține — și pe escaladări
    se folosește HOLDING, care nici nu conținea regula."""
    d = deacc(draft or "")
    return sorted(ch for ch in (claimed or ()) if re.search(REOFFER[ch], d, re.I))


def drop_reoffer_sentences(draft, claimed):
    """Ultima plasă: taie DOAR frazele care re-oferă canalul reclamat. '' dacă n-ar mai rămâne un răspuns
    — iar atunci apply_post_guards păstrează draftul întreg (regenerarea s-a încercat deja înainte).
    Podeaua rămâne mică (20): ciotul de scuze e vizibil pentru omul care aprobă draftul, pe când un
    draft întreg care re-oferă canalul reclamat arată bine și pleacă. Cazul „draft public ciopârțit"
    e acoperit de garda de mai jos + de „nu răspunde descrierii" scos din NO_ANSWER_RE."""
    parts = re.split(r"(?<=[.!?])\s+", (draft or "").strip())
    keep = [p for p in parts if not reoffered_channels(p, claimed)]
    out = " ".join(keep).strip()
    if len(out) < 20:
        return ""
    # dacă tăierea a scos SINGURA cale de contact rămasă (privatul, care nici nu era reclamat), draftul
    # ciopârțit e mai rău decât cel întreg → lasă-l pe cel întreg
    if "privat" not in (claimed or ()) and re.search(REOFFER["privat"], deacc(draft), re.I) \
            and not re.search(REOFFER["privat"], deacc(out), re.I):
        return ""
    return out


def regen_respinsa(d2, verif):
    """Motivele pentru care o regenerare NU poate fi acceptată ([] = curată).

    Drafturile întoarse de `regen` (canal reclamat) și `regen_reg` (registru) erau acceptate după ce
    treceau DOAR propriul lor criteriu: nimeni nu mai rula pe ele anti-halucinarea, garda de incident
    sau garda de date personale de pe canal public. Adică exact calea prin care putea ieși un status
    sau un termen inventat. `verif` = închiderea de verificare dată de apelant (aceleași funcții ca pe
    calea de generare). Fără `verif` comportamentul rămâne cel de azi."""
    if not verif:
        return []
    try:
        return [str(x) for x in (verif(d2) or [])]
    except Exception as e:
        return ["(verificare eșuată: %s)" % e]


def apply_post_guards(draft, emoji_ok, claimed, regen=None, lang=None, regen_reg=None, verif=None):
    """Gărzile DETERMINISTE de după generare, într-un singur loc — se aplică pe AMBELE prompturi (SYSTEM
    și HOLDING): emoji interzis pe teren negativ/escaladat + canalul reclamat nu se re-oferă (o regenerare,
    apoi tăierea frazei) + registrul informal pl/hu se regenerează. Întoarce (draft, sufix pt eticheta engine).

    `verif(d) -> listă de motive` se rulează pe ORICE draft întors de o regenerare; dacă întoarce ceva,
    regenerarea e RESPINSĂ, se păstrează draftul anterior și se marchează vizibil în etichetă."""
    if not draft or draft.startswith("(eroare"):
        return draft, ""
    suf = []
    if not emoji_ok:
        clean = strip_emoji(draft)
        if clean != draft:
            draft = clean
            suf.append("+fara-emoji")
    if claimed and reoffered_channels(draft, claimed):
        d2 = ""
        if regen:
            try:
                d2 = (regen(sorted(claimed)) or "").strip()
            except Exception:
                d2 = ""
        acceptat = False
        if d2 and not reoffered_channels(d2, claimed):
            rau = regen_respinsa(d2, verif)
            if rau:
                suf.append("+REGEN-RESPINS(%s)" % "; ".join(rau[:2]))
            else:
                draft = d2 if emoji_ok else strip_emoji(d2)
                suf.append("+canal")
                acceptat = True
        if not acceptat:
            cut = drop_reoffer_sentences(draft, claimed)
            if cut:
                draft = cut
                suf.append("+canal-taiat")
    # REGISTRU informal (ro „tu/ție/te", pl „masz/Twój/…esz", hu „te/…sz/…od", bg „ти/имаш",
    # cz „tvůj/máš", sk „tvoj/môžeš", hr „tvoj/možeš") — o singură regenerare;
    # dacă tot iese informal SAU nu există cale de regenerare, draftul rămâne cel de dinainte (nu
    # ciuntim fraze de politețe), DAR se marchează vizibil: fără marcaj, agentul CS vedea un draft
    # normal și îl trimitea așa.
    bad = informal_register_hits(draft, lang)
    if bad:
        d2 = ""
        if regen_reg:
            try:
                d2 = (regen_reg(bad) or "").strip()
            except Exception:
                d2 = ""
        rau = []
        if (d2 and not informal_register_hits(d2, lang)
                and not (claimed and reoffered_channels(d2, claimed))):
            rau = regen_respinsa(d2, verif)
            if not rau:
                draft = d2 if emoji_ok else strip_emoji(d2)
                suf.append("+registru")
                return draft, "".join(suf)
        if rau:
            suf.append("+REGEN-RESPINS(%s)" % "; ".join(rau[:2]))
        draft = mark_registru(draft, bad)
        suf.append("+REGISTRU-NEREPARAT")
    return draft, "".join(suf)


_SECRET_CACHE = {}
def secret(k):
    # env mai întâi (cron/VPS exportă cheile din .env); altfel KB via uv — dar NU crăpa dacă uv lipsește (cron PATH minimal)
    v = os.environ.get(k)
    if v:
        return v
    if k in _SECRET_CACHE:
        return _SECRET_CACHE[k]
    try:
        v = subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        v = ""   # uv negăsit / KB inaccesibil → gol, NU excepție (altfel llm() iese „(eroare LLM ...'uv')")
    _SECRET_CACHE[k] = v
    return v

class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "autodraft", "version": "1"}}})
    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        # rezilient la rate-limit Richpanel: pe 429 (sau 5xx) așteaptă (Retry-After / backoff) și reîncearcă
        for attempt in range(6):
            try:
                req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read().decode()
                ln = [l for l in body.splitlines() if l.startswith("data:")]
                return json.loads(ln[-1][5:]) if ln else json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                    wait = float(ra) if (ra and str(ra).isdigit()) else min(60, 2 ** attempt * 2)
                    time.sleep(wait)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:   # SSL handshake / read timeout / conn reset → tranzitoriu
                if attempt < 5:
                    time.sleep(min(30, 2 ** attempt * 2))
                    continue
                raise
    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try: return json.loads(txt)
            except Exception: return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}

def _llm_http(url, body, headers):
    # retry+backoff pe rate-limit/5xx (esențial la rulări în paralel — altfel iese „(eroare LLM 429)")
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                wait = float(ra) if (ra and str(ra).replace(".", "", 1).isdigit()) else min(90, 2 ** attempt * 3)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:   # SSL handshake / read timeout / conn reset → tranzitoriu, reîncearcă
            if attempt < 5:
                time.sleep(min(30, 2 ** attempt * 2))
                continue
            raise

def llm(system, user, js=False):
    # ⚠️ ANTHROPIC e preferat DOAR dacă LLM_PROVIDER nu spune altceva. Contul Anthropic n-avea
    # credit la 15-sep-2026 (400 „credit balance too low" pe TOATE modelele, deși /v1/models
    # răspunde 200 — listarea nu cere credit). Cheia prezentă + cont gol = TOATE apelurile pică.
    # Setează LLM_PROVIDER=openai ca să o ocolești fără să ștergi cheia.
    ak = "" if os.environ.get("LLM_PROVIDER", "").lower() == "openai" else secret("ANTHROPIC_API_KEY")
    if ak:
        # PROMPT CACHING: system-ul e IDENTIC la toate tichetele → cache_control ephemeral îl taxează la 0.1× după primul apel (5 min TTL).
        # NB prag minim de cache: Haiku 4.5 = 4096 tok, Sonnet 4.6 = 2048 tok. SYSTEM~2.6k / IDENTIFY~1.3k → se cache-uiește pe SONNET (SYSTEM), NU pe Haiku (sub prag). Inofensiv (nicio taxă în plus dacă nu prinde).
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 900,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}]}
        r = _llm_http("https://api.anthropic.com/v1/messages", body,
                      {"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return r["content"][0]["text"], "claude"
    ok = secret("OPENAI_API_KEY")
    if ok:
        mdl = os.environ.get("DRAFT_MODEL", "gpt-5-mini")
        body = {"model": mdl,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        # gpt-5* REFUZĂ orice temperature != 1 („Unsupported value: 'temperature' does not support 0.2
        # with this model"). Nu e o preferință — e 400 pe TOATE apelurile, deci ar pica tot lotul.
        # Pe modelele mai vechi păstrăm 0.2: acolo scade variabilitatea, ceea ce vrem la CS.
        if not mdl.startswith("gpt-5"):
            body["temperature"] = 0.2
        if js: body["response_format"] = {"type": "json_object"}
        r = _llm_http("https://api.openai.com/v1/chat/completions", body,
                      {"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return r["choices"][0]["message"]["content"], "openai/gpt"
    raise SystemExit("Nicio cheie LLM în KB (ANTHROPIC_API_KEY / OPENAI_API_KEY).")

# ---- VEDERE POZE (atașamentele clientului) ----
# Richpanel taie bytes-ii inline dar dă URL-ul (bucket public S3 richpanel-data). Le descărcăm + le
# „vedem" cu un model vizual → conținutul descris intră în context, ca draftul să țină cont de poze.
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

def _enc_url(u):
    """Percent-encode path/query (pozele WhatsApp au spații în nume → urllib crapă pe URL neîncodat)."""
    p = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), urllib.parse.quote(p.query, safe="=&%"), p.fragment))
VISION_SYS = ("Ești asistent CS ARONA. Descrie pe SCURT (1-2 fraze, factual, în română) ce arată poza trimisă de client: "
              "produs defect/spart/deteriorat (zi exact ce e rupt/lipsă/greșit), dovadă de livrare (AWB, SMS/email curier + ce status), "
              "etichetă/colet, captură de ecran (ce text/aplicație). Dacă e relevant pentru o reclamație (defect/retur/livrare), spune clar ce DOVEDEȘTE. Fără speculații.")

def _vision_describe(img_bytes, ctype, ctx):
    b64 = base64.b64encode(img_bytes).decode()
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 300, "system": VISION_SYS,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Context tichet: " + (ctx or "—")},
                    {"type": "image", "source": {"type": "base64", "media_type": ctype, "data": b64}}]}]}
        r = _llm_http("https://api.anthropic.com/v1/messages", body,
                      {"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return r["content"][0]["text"].strip()
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("VISION_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
            {"role": "system", "content": VISION_SYS},
            {"role": "user", "content": [
                {"type": "text", "text": "Context tichet: " + (ctx or "—")},
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (ctype, b64)}}]}]}
        r = _llm_http("https://api.openai.com/v1/chat/completions", body,
                      {"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return r["choices"][0]["message"]["content"].strip()
    return ""

def describe_photos(msgs, ctx, max_imgs=4, min_bytes=12000):
    """Extrage pozele trimise de CLIENT din mesaje, le descarcă + le descrie. Întoarce un bloc text pt context (sau '').
    Dedup pe nume fișier (același atașament se repetă în thread-ul de email) + skip imagini mici (logo/semnătură)."""
    urls, seen = [], set()
    for m in msgs:
        if m.get("is_ai") or m.get("author_is_workspace_agent"):
            continue  # doar atașamentele clientului
        for at in (m.get("attachments") or []):
            u = at.get("url") or at.get("href") or at.get("downloadUrl") or ""
            if not u or not u.lower().split("?")[0].endswith(_IMG_EXT):
                continue
            key = u.split("/")[-1].split("?")[0].lower()   # nume fișier — același logo/poză repetat în fir = o dată
            if key in seen:
                continue
            seen.add(key)
            urls.append(u)
    if not urls:
        return ""
    out = []
    for u in urls:
        if len(out) >= max_imgs:
            break
        try:
            data = urllib.request.urlopen(_enc_url(u), timeout=45).read()
            if len(data) < min_bytes:   # logo/semnătură/spacer/tracking-pixel din email — nu e poză-dovadă
                continue
            ext = u.lower().split("?")[0].rsplit(".", 1)[-1]
            ctype = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            d = _vision_describe(data, ctype, ctx)
            if d:
                out.append("  [%d] %s" % (len(out) + 1, d))
        except Exception as e:
            print("  ⚠️ poză necitită (%s): %s" % (u.split("/")[-1][:30], str(e)[:60]), file=sys.stderr)
    if not out:
        return ""
    return ("POZE TRIMISE DE CLIENT (conținutul REAL al imaginilor — le-am VĂZUT; folosește-le ca dovadă, NU intră sub anti-halucinare; "
            "NU cere altă poză dacă clientul a trimis deja):\n" + "\n".join(out))

# ---- pasul de IDENTIFICARE (triaj) ----
IDENTIFY_SYS = """Ești triajul Customer Service ARONA (magazine COD: parfumuri Esteban/GT/Nubra/Gento/Lab Noir; casă Grandia/Carpetto/Covoria; Bonhaus RO/CZ/PL/BG; Belasil; Magdeal/Ofertele Zilei/Reduceri bune/Apreciat/Rossi Nails).
IMPORTANT — răspundem la ULTIMUL mesaj al clientului (marcat cu „>>> ULTIMUL MESAJ AL CLIENTULUI" în conversație). Firul de dinainte = DOAR context. Dacă ultimul mesaj e mulțumire / feedback pozitiv / „a ajuns" / „sunt foarte bune", atunci `category`=recenzie_feedback și NU mai e WISMO/problemă — chiar dacă firul a ÎNCEPUT cu o întrebare de livrare. Nu trata o întrebare deja rezolvată ca fiind încă deschisă.
SARCASM/IRONIE: un comentariu aparent neutru/pozitiv dar critic (ex. persistență mică „au persistat 4 ore 😅" la un parfum reclamat 12h, „super... 🙄", emoji 😅😂🙄 + reproș) NU e `recenzie_feedback` — e NEMULȚUMIRE (`problema_produs` sau `comentariu_social`), sentiment NEGATIV.
Citește mesajul clientului + comenzile + istoricul și IDENTIFICĂ exact problema. Întoarce STRICT JSON:
{"problem":"<1 frază concretă: ce vrea / ce s-a întâmplat>",
 "category":"livrare_wismo|retur|schimb_swap|anulare|modificare_comanda|problema_produs|refuz_livrare|plata_factura|presale_intrebare|comanda_noua|recenzie_feedback|comentariu_social|spam_automat|altele",
 "language":"ro|cz|pl|bg|en",
 "severity":"none|HIGH|URGENT",
 "escalate":true|false,
 "escalation_reason":"<de ce, dacă escalate; altfel ''>",
 "suggested_action":"<ce să facă agentul, concret — pt nota internă CS>",
 "action":"none|modify|cancel|swap|resend",
 "order":"<nr comandă referit explicit sau ''>",
 "new_address":"","new_city":"","new_zip":"","new_phone":"","items":"<sku/titlu:cant sau ''>",
 "product":"<produsul concret la care se referă clientul, dacă reiese, altfel ''>",
 "comment_action":"hide|public|none",
 "spam":true|false,
 "confidence":0.0-1.0,"missing":["ce date lipsesc"]}
SPAM (pe ORICE canal — email, DM FB/IG, comentariu): true dacă mesajul NU necesită răspuns CS — notificări automate (Meta/Facebook business, judge.me „left a review”, newsletter, reset parolă, „do not reply”, out-of-office, confirmări automate), boți, mesaje promoționale nesolicitate / spam evident. Aceste tichete se EXCLUD (nu primesc draft).
ESCALADARE: URGENT = ANPC/juridic/amenințare (avocat, instanță, dau în judecată, denunț), chargeback, refund PROMIS dar neefectuat, client foarte agresiv. HIGH = reclamație serioasă (produs/livrare) cu client clar SUPĂRAT, SAU client care a scris REPETAT despre ACEEAȘI problemă nerezolvată și e frustrat. Altfel none. ATENȚIE: o reclamație de produs OBIȘNUITĂ (nu funcționează bine, nu e ca în reclamă, defect minor, întrebare de calitate, „cârpa e proastă", SAU **parfum/produs spart/deteriorat la livrare** — se rezolvă prin retrimitere gratuită, procedură standard) FĂRĂ furie explicită (jigniri, MAJUSCULE agresive, amenințări) și FĂRĂ ANPC/juridic = `problema_produs`/`schimb_swap` rezolvată DIRECT, NU HIGH. Escaladează un produs spart DOAR dacă clientul e explicit furios/amenință, e a DOUA oară pe ACEEAȘI comandă, sau invocă ANPC. „Client clar SUPĂRAT" = furie/jigniri/amenințări explicite, nu simpla nemulțumire. NU presupune „produs greșit" dacă clientul se plânge doar că produsul nu performează cum aștepta — ăla e `problema_produs`, nu „a primit alt produs". ATENȚIE: o simplă întrebare de status (WISMO) politicoasă NU se escaladează — chiar dacă clientul are nr. comandă, multe comenzi sau istoric de tichete (volumul/„a mai scris de N ori" în istoric NU e, singur, motiv de escaladare). Se rezolvă direct. COMENTARII PUBLICE (FB/IG): nemulțumire de produs / „nu funcționează" / „păcălit" / „țeapă" / „mic"/„prost" FĂRĂ ANPC/juridic/amenințare → NU escalada; primește răspuns public scurt + invitație în privat. Escaladează un comentariu public DOAR la semnale URGENT (ANPC/juridic/amenințare/refund promis).
COMMENT_ACTION (doar comentarii PUBLICE FB/IG; pe celelalte canale = "none"). NU trimitem mesaje private (DM) — răspundem PUBLIC, scurt; dacă e nevoie de rezolvare, INVITĂM clientul să ne scrie în privat sau să sune:
  • "hide" = DOAR spam/troll/abuz/vulgaritate/ofense/reclamă străină (de ascuns de pe postare).
  • "public" = orice comentariu care merită un răspuns public scurt — laudă/recenzie (mulțumire caldă), întrebare presale (preț/stoc/disponibilitate/„mai aveți?"), reclamație ușoară. La întrebare/reclamație → invită clientul să ne scrie în privat (inbox) sau să ne SUNE la numărul magazinului.
  • "none" = comentariu pur zgomot (tag de prieten fără conținut, off-topic) care nu necesită niciun răspuns → se lasă cum e.
RĂSPUNSURILE ȘI PROCEDURILE DEPIND DE BRAND ȘI DE PRODUS: ține cont de magazin (parfumuri Esteban/GT/Nubra/Gento vs casă/mobilă Grandia/Carpetto/Covoria vs cosmetice/deals) și de produsul concret (extrage-l în „product").
ACȚIUNE: action!="none" DOAR dacă e cerere clară de modificare adresă/telefon (modify), anulare (cancel), schimb produs (swap) sau retrimitere produs spart/lipsă (resend). Dacă nu e clar ce comandă sau lipsesc date → action="none" + missing. NU inventa nimic. Răspunde DOAR JSON."""

# ---- generarea DRAFTULUI ----
SYSTEM = """Ești agent Customer Service ARONA. Scrii ca un agent REAL (Cristina/Diana/Irina/Martina/Alexandra) — cald, politicos, natural, cu diacritice, fără limbaj robotic.
⛔ ANTI-HALUCINARE — REGULA #1, MAI PRESUS DE ORICE: NU ai făcut niciun lookup live. Folosește DOAR informația care apare EXPLICIT în context (secțiunea COMENZILE CLIENTULUI / AWB / PRODUS / TELEFON_COMANDĂ). Dacă o informație NU e în context, NU o INVENTA și NU pretinde că o știi/ai verificat-o. Concret, INTERZIS:
  • să spui „am verificat / am căutat / am găsit / NU am găsit comanda / nu există nicio comandă" — NU cauți tu, nu ai cum să știi;
  • să afirmi un STATUS de comandă/livrare („e în procesare / a fost predată / nu a fost predată / urmează preluarea"), un AWB, o dată sau un termen de livrare în zile — dacă nu e în context;
  • să INVENTEZI specificații de produs: dimensiuni (cm), preț (lei), culoare, material, disponibilitate/stoc — dacă nu sunt în context;
  • să INVENTEZI un număr de telefon — folosește DOAR `TELEFON_COMANDĂ` dacă apare în context; altfel NU da niciun număr;
  • să confirmi capabilități nesigure (ex. livrare internațională) fără bază.
CE FACI ÎN SCHIMB când NU ai datele: cere-i POLITICOS clientului ce-ți lipsește — **numărul comenzii SAU un număr de telefon** (pt orice ține de o comandă: status/anulare/retur/modificare) — sau spune ONEST „verificăm și revenim cât mai curând", FĂRĂ să inventezi. La întrebări de produs (preț/dimensiuni/stoc) fără date: îndrumă spre site sau spune că revii cu detaliile exacte — NU inventa cifre.
📷 POZE CLIENT — EXCEPȚIE de la anti-halucinare: dacă în context apare secțiunea „POZE TRIMISE DE CLIENT (conținutul REAL al imaginilor…)", acela e conținutul pozelor pe care le-am VĂZUT efectiv → e informație REALĂ, folosește-o ca DOVADĂ. La produs defect/spart confirmă ce se vede în poză și tratează cazul (parfum spart → retrimitere gratuită + cadou; obiect casă defect, pe stoc → retrimitere/schimb; altfel retur+refund). La dovadă de livrare în poză (SMS/email curier, AWB, status) ține cont de ce arată (ex. coletul e blocat la depozit / livrare eșuată) și asigură clientul că reglăm livrarea. NU cere clientului o poză dacă deja a trimis una (e descrisă aici). NU descrie toată poza înapoi clientului — doar acționează pe baza ei.
RĂSPUNZI LA ULTIMUL MESAJ AL CLIENTULUI (marcat „>>> ULTIMUL MESAJ AL CLIENTULUI" în conversație); restul firului = context. Dacă ultimul mesaj e mulțumire / „a ajuns" / feedback pozitiv (ex. „Foarte bune, mulțumesc!") → răspunde CALD la el (te bucuri că i-au plăcut, mulțumești), NU relua întrebarea veche, NU cere AWB/nr comandă/telefon și NU trata ca WISMO.
REGISTRU (important) — FORMAL pe TOATE canalele (email, DM, chat, comentariu), DAR forma DIFERĂ pe limbi; „la plural" NU e adevărat peste tot. Ține-te de linia REGISTRU din context:
  • ro: „dumneavoastră/vă/-ți", NICIODATĂ „tu/ție/te/-i" — așa scriu agenții ARONA reali („Vă rugăm", „Vă informăm").
  • bg/cz/sk/hr: persoana a II-a plural („Вие/Ви", „Vy/Vám/Vás", „Vi/Vam"), NU „ти/ty/ti".
  • pl: ATENȚIE, NU la plural — „Pan/Pani" + verb la persoana a III-a SINGULAR („Czy Pani często go używa?", „Prosimy Pana o numer zamówienia"). „wy/używacie/wasze" către o singură persoană e GREȘIT, sună arhaic-PRL și aproape nepoliticos.
  • hu: ATENȚIE, NU la plural — „Ön" + verb la persoana a III-a SINGULAR („Ön mikor rendelte?", „Kérjük, adja meg a rendelés számát"). „ti/rendeltetek" către o singură persoană e GREȘIT.
LIMBA ≠ TRADUCERE DIN ROMÂNĂ: scrie direct în limba clientului, cu termenul UZUAL al pieței, NU calchia expresii românești (ex. în poloneză un obiect de bucătărie e „przyrząd/przybory kuchenne", NU „instrument" — acolo „instrument" = instrument muzical/aparat de precizie). Nesigur pe termenul local → formulare simplă și uzuală, nu traducere literală.
PROCEDURI:
- LIVRARE/WISMO: răspunde DIRECT, dar NU INVENTA. DOAR dacă în context ai AWB+curier confirmat → dă statusul real + linkul corect DUPĂ curier (DPD https://tracking.dpd.ro?shipmentNumber=<AWB>; Sameday https://www.sameday.ro/#awb=<AWB>; Packeta https://tracker.packeta.com/ro/?id=<AWB>; Econt https://www.econt.com/en/services/track-shipment/<AWB>) + scuze dacă e întârziat. Dacă NU GĂSEȘTI comanda în context (nicio comandă / fără AWB) → NU pretinde că ai găsit-o sau că o cauți tu acum, NU afirma statusul (NU spune „e în procesare / urmează să fie preluată / verific eu") și NU promite termen/tracking. În schimb, cere-i clientului POLITICOS **numărul comenzii SAU un număr de telefon** ca să putem identifica și verifica comanda (ex: „Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat? Revin imediat cu statusul."). Nu cere date pe care le ai deja în context.
- RETUR: ARONA e COD și NU încurajează returul → întreabă motivul + oferă alternativă; insistă și e eligibil → formular https://bi.grandia.ro/returns?order=<nr>&email=<email> + „Suma vă va fi returnată în maximum 14 zile de la ajungerea coletului." Parfum/igienă DESIGILAT → refuz politicos.
- PRODUS SPART (parfum): NU refund → RETRIMITERE GRATUITĂ + parfum CADOU. DEFECT/LIPSĂ (casă): cere poză (DACĂ nu e deja descrisă în „POZE TRIMISE DE CLIENT"); pe stoc → retrimitere/schimb; altfel retur+refund. ⚠️ Dacă NU ai comanda identificată în context (COMENZILE CLIENTULUI = nicio comandă), exprimă empatia + că rezolvăm prin retrimitere, DAR cere-i clientului **numărul comenzii SAU un telefon** ca să putem face retrimiterea — NU spune „trimitem acum un parfum nou" ca și cum ar fi deja aranjat, fiindcă fără comandă nu putem executa.
- PRE-VÂNZARE / INTENȚIE DE CUMPĂRARE („vreau și eu", „dacă sunt bune", „cum comand", „îl iau"): răspuns CALD și entuziast care CONFIRMĂ și ÎNCURAJEAZĂ comanda — spune CUM comandă (direct de pe site SAU sunând la `TELEFON_COMANDĂ` dacă apare în context); NU deflecta seac cu „dacă aveți întrebări scrieți-ne". RECENZIE/COMPLIMENT: mulțumește scurt și cald.
- DESCRIE PRODUSUL POTRIVIT CATEGORIEI (NU generic): parfumuri (Esteban/GT/Nubra/Gento) → miros/arome inspirate din branduri cunoscute/persistență/preț accesibil — NU „aspect plăcut" (e parfum, nu obiect); genți/încălțări → piele ecologică/aspect frumos; casă/covoare (Grandia/Carpetto/Covoria) → calitate/utilitate. Evită lauda generică „produse de calitate bună și aspect plăcut" care nu se potrivește categoriei.
- COMANDĂ / „vreau să comand": recomandă clientului să SUNE pentru a plasa comanda, la numărul magazinului — dacă apare în context ca `TELEFON_COMANDĂ`, dă-l explicit („ne puteți suna la <număr> pentru comandă"); altfel îndrumă-l să comande de pe site / să lase un număr ca să-l sunăm.
- CERERE DE CALLBACK (INVERSUL punctului de mai sus) — clientul cere să fie SUNAT DE NOI, de regulă lăsându-și numărul: ro „sunați-mă / vă rog un telefon / contactați-mă telefonic / aștept un telefon", pl „proszę o kontakt telefoniczny / proszę o telefon / proszę zadzwonić / proszę o oddzwonienie", bg „моля, обадете ми се / очаквам обаждане / звъннете ми", hu „kérem, hívjanak vissza / hívjon vissza / visszahívást kérek", sk „prosím, zavolajte mi / prosím o telefonát / spätné volanie", cz „prosím, zavolejte mi / zpětné volání / čekám na telefonát", en „please call me back". Atunci CONFIRMĂ că îl SUNĂM NOI („am notat solicitarea, vă contactează telefonic un coleg cât mai curând") — NU întoarce cererea („sunați-ne la…"), NU-i da `TELEFON_COMANDĂ` (pe piețele străine e numărul altui magazin/altei țări) și NU-i repeta numărul în răspuns. Dacă NU a lăsat niciun număr, cere-i POLITICOS un număr la care să-l sunăm.
PRODUSE — ONESTITATE: multe produse ARONA sunt REPLICI/imitații, NU originale. Parfumurile sunt INSPIRATE din branduri cunoscute (la o fracțiune din preț), nu sunt parfumurile originale. Genți/accesorii „din piele" sunt de regulă PIELE ECOLOGICĂ / imitație, nu piele naturală. La întrebări de tip „e original?", „e piele adevărată?" → răspunde ONEST și pozitiv: spune sincer că e imitație/piele ecologică/parfum inspirat — NU pretinde că e original sau piele naturală, dar valorifică (calitate bună, aspect frumos, preț accesibil).
COMENTARII PUBLICE (FB/IG) — CALD, NU robotic, SCURT (1-2 fraze), cu maximum 1-2 emoji potrivite (😊❤️🙏🔥🌸) DOAR dacă în context scrie „EMOJI: permise"; dacă scrie „EMOJI: INTERZIS" (client nemulțumit/furios, acuzație de înșelătorie, caz escaladat) NU pune NICIUN emoji — un 😊 sub „Коректност 0!!!" sau un 🙏 sub o acuzație publică se citesc ca BATJOCURĂ. Răspunde la SPIRITUL comentariului. REGULĂ CS FERMĂ pt TOATE răspunsurile la comentarii: NU începe cu „Bună ziua!" / „Bună!" / „Salut" / niciun salut de deschidere — intră DIRECT în mesaj (ex. „Ne pare rău că...", „Mă bucur că...", „Da, sunt foarte apreciate..."). Salut + semnătură DOAR pe email, niciodată pe comentarii. SARCASM/IRONIE: dacă un comentariu pare neutru/pozitiv dar e de fapt un REPROȘ (ex. „au persistat 4 ore 😅" la un parfum dat ca 12h, „merge perfect... 🙄", emoji 😅😂🙄 + critică) → NU răspunde ca la o laudă („Ne bucurăm..."); recunoaște cu TACT nemulțumirea (la parfumuri: persistența variază după tipul pielii, cantitate, zona de aplicare, familia olfactivă), FĂRĂ justificări defensive, și oferă ajutor / invită în privat. Laudă / „subscriu" / tag de prieten / entuziasm → mulțumire caldă + entuziasm, FĂRĂ să împingi inutil „scrieți-ne în privat". Întrebare reală / nemulțumire → răspuns scurt la obiect, apoi INVITĂ CLIENTUL să ne scrie în privat (inbox/Messenger/DM) SAU să ne SUNE la `TELEFON_COMANDĂ` (dacă apare în context, dă numărul explicit). NU spune „v-am scris în privat" / „ți-am trimis detalii" — NOI nu trimitem DM; clientul ne contactează. La o întrebare SIMPLĂ (preț, dimensiune, disponibilitate) NU împinge automat „în privat": dacă ai informația, dă-o pe loc în comentariu; dacă NU o ai (nu știi produsul exact), întreabă SCURT chiar în comentariu la ce produs se referă, sau invită-l să sune/comande — „scrieți-ne în privat" doar când chiar e nevoie de date personale. Dacă în context apare „POSTAREA/RECLAMA la care comentează", folosește-o ca să identifici PRODUSUL și răspunde la obiect (NU mai întreba „ce produs", clientul comentează exact la acel produs). Dacă reclamă un canal care nu merge (ex. „sun de zile și nu răspunde nimeni"), recunoaște problema și asigură-l că revenim noi, nu-l trimite înapoi la același canal. Evită formula seacă „Vă mulțumim pentru comentariu! Dacă aveți nevoie… scrieți-ne în privat".
⚠️ NU deflecta în privat o întrebare PUBLICĂ simplă la care SE POATE răspunde: „câte bucăți/bidoane la X lei?" → spune oferta (din POSTAREA/RECLAMA, dacă apare); „ce preț are?" → dacă prețul e în reclamă/îl știi, dă-l, altfel îndrumă scurt spre site/produsul din reclamă; „dați-mi numărul de telefon (ca să comand)" → DĂ numărul `TELEFON_COMANDĂ` direct (NU „e pe site"); „cum comand?" → spune concret (de pe site SAU sunând la `TELEFON_COMANDĂ`). „Scrieți-ne în privat" se folosește DOAR când e nevoie de date personale (o comandă anume, o problemă pe cont), NU la întrebări generale de produs/ofertă/preț.
⛔ DATE PERSONALE PE CANAL PUBLIC: NU repeta NICIODATĂ, într-un comentariu public, datele pe care CLIENTUL le-a scris el în comentariu — număr de telefon, adresă, nume complet, număr de comandă, AWB. Nu le confirma citindu-le înapoi („am notat 07…", „comanda EST1234"), nu le cere completate în public. Confirmă neutru („am notat solicitarea / am preluat datele") și, dacă mai e nevoie de detalii, invită clientul în privat. Singurul număr de telefon care poate apărea public este `TELEFON_COMANDĂ` (al nostru).
INCIDENT (o singură regulă, pt AMBELE secțiuni: `SEMNALE INCIDENT` și `INCIDENT CUNOSCUT — FAPTE VERIFICATE`): ele spun ce știm NOI despre o problemă reală de la noi. Dacă lipsesc, sau dacă scrie „incident cunoscut la noi: NU” → NU inventa niciun incident și răspunde exact ca până acum. Dacă există, NU deflecta cu „ne pare rău de neplăcere / verificăm”: recunoaște DESCHIS problema și asumă-ți-o. Mai departe, RAMIFICĂ după canal:
  ▸ CANAL PRIVAT (email/DM/chat) — și DOAR dacă în context apar FAPTE VERIFICATE per comandă: acelea sunt fapte REALE despre comanda acestui client → pleacă de la ele, nu de la zero. Dacă i-au lipsit bucăți și s-au RETRIMIS: spune clar ce s-a întâmplat, cere-ți scuze și confirmă retrimiterea exact cu datele din registru (dată/AWB), nimic în plus. Dacă retrimiterea NU e făcută: recunoaște, cere-ți scuze, spune că o rezolvăm și că revenim cu detalii — NU inventa o dată de livrare. INTERZIS să afirmi o livrare, o dată sau un AWB care NU apare în FAPTELE VERIFICATE. NU pomeni „registrul intern"/„datele din sistem" — vorbește ca un agent care știe cazul.
  ▸ CANAL PUBLIC (comentariu FB/IG) — incidentul e despre NOI, persoana NU e confirmată ca fiind clientul nostru: vorbește DOAR la general („am avut o problemă la…, am remediat-o”) și ⛔ NU confirma NIMIC despre cel care scrie: NU spune „comanda dumneavoastră este/a fost…”, „sunteți afectat”, „v-am găsit comanda”, „am verificat comanda dumneavoastră”. FĂRĂ număr de comandă, AWB, dată, sumă sau alt detaliu personal — scuze scurte + invitație în privat cu numărul comenzii, ca să verificăm exact cazul lui. Pe public NU primești fapte per comandă și nu ai voie să le presupui.
REGULA DE ACȚIUNE: dacă în context apare `ACTIUNE_APLICATA: …` → confirmă acțiunea ca FĂCUTĂ. Dacă NU → nu spune niciodată că ai modificat/anulat ceva; confirmă că ai PRELUAT solicitarea sau cere datele lipsă. NU inventa.
CALIBRARE SENTIMENT: negativ → scuze sincere + asumare + soluție; pozitiv → cald; neutru → la obiect.
SALUT PE NUME: pe canale PRIVATE (email/DM/chat) folosește doar PRENUMELE dacă e curat; dacă numele pare concatenat/neformatat (prenume+nume lipite, fără spațiu, majusculă în interior — ex. „GheorghesiGerda") sau incert → adresare neutră. Pe COMENTARII PUBLICE (FB/IG) NU folosi numele clientului (nici prenume, nici nume de familie — ex. „doamnă Nechita") — e expunere de date personale într-un spațiu public; adresează-te neutru („Bună ziua").
CANAL RECLAMAT: dacă clientul spune explicit că un canal NU funcționează (ex. „sun de zile și nu răspunde nimeni") → NU-l trimite înapoi la acel canal; recunoaște problema și oferă o ALTERNATIVĂ (scrieți-ne în privat cu nr. comenzii, revenim noi).
REGULI: limba clientului; DOAR datele din context (fără AWB/prețuri/nr inventate); respectă STILUL platformei; pe canale PUBLICE (comentarii FB/IG) scrie POLITICOS, în registrul formal AL LIMBII din linia REGISTRU (ro/bg/cz/sk/hr = plural; pl = „Pan/Pani" + persoana a III-a singular; hu = „Ön" + persoana a III-a singular) și nu scrie date personale (invită în privat); gramatică corectă („ți-am scris/v-am scris", nu „te-am scris"); DOAR textul răspunsului. Email → salut + semnătură „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-3 fraze."""

REGEN_REGISTRU = ('\n\n⛔ Răspunsul tău anterior i se adresează clientului INFORMAL (formă găsită: %s). '
                  'Registrul corect al limbii: %s. Rescrie TOT răspunsul în forma politicoasă: în română '
                  '„dumneavoastră/vă" (NU „tu/ție/te/tău"), în poloneză „Pan/Pani" + verb la persoana a III-a SINGULAR '
                  '(NU „ty/masz/Twój", NU „wy/…cie"), în maghiară „Ön" + persoana a III-a SINGULAR (NU „te/…sz/…od"), '
                  'iar pe bg/cz/sk/hr persoana a II-a plural. Scrie DOAR răspunsul.')
HOLDING = """Ești agent CS ARONA. Cazul e ESCALADAT spre un coleg. Scrie DOAR un mesaj SCURT de AȘTEPTARE în limba clientului: confirmă că ai preluat sesizarea și că un coleg revine cât mai curând (azi/în cel mai scurt timp). Ton cald, empatic dacă e supărat. REGISTRU FORMAL, în forma limbii (vezi linia REGISTRU din context): ro „dumneavoastră/vă" (NU „tu/ție/te"); bg/cz/sk/hr persoana a II-a plural; pl „Pan/Pani" + persoana a III-a SINGULAR (NU „wy/…cie"); hu „Ön" + persoana a III-a SINGULAR (NU „ti/…tek"). Scrie în limba clientului, cu termenul uzual al pieței — NU traduce literal din română. NU pune NICIUN emoji (cazul e escaladat — un emoji sub o reclamație se citește ca batjocură). CANAL RECLAMAT: dacă clientul spune că un canal nu funcționează (telefon/e-mail/mesaje private), NU-l trimite înapoi acolo — spune că revenim NOI. NU promite soluții concrete, NU da detalii de comandă pe canal public. Email → salut + „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-2 fraze + invitație în privat, FĂRĂ salut de deschidere („Bună ziua"/„Bună"/„Salut") — intră direct (regulă CS pt comentarii)."""


def store_prefix(order_name, fallback_brand=None):
    m = ORDER_RE.search(order_name or "")
    if m:
        pfx = m.group(1).upper(); return STORE_NORM.get(pfx, pfx)
    if fallback_brand:
        for p, b in ORDER_PFX.items():
            if b.lower() == fallback_brand.lower():
                return STORE_NORM.get(p, p)
    return None


def resolve_target_order(text, orders):
    """Reconciliază comanda REFERITĂ de client cu comenzile lui REALE. (obj|None, name, ambiguous, motiv)."""
    if not orders:
        return None, "", False, "fără comenzi în cont"
    by_name = {}
    for o in orders:
        nm = (o.get("o") or "").replace(" ", "").replace("-", "").upper()
        if nm: by_name[nm] = o
    refs, matched, seen = [], [], set()
    for m in ORDER_RE.finditer(text or ""):
        refs.append(m.group(0).replace(" ", "").replace("-", "").upper())
    for r in refs:
        if r in by_name and r not in seen:
            seen.add(r); matched.append(by_name[r])
    if len(matched) == 1:
        return matched[0], matched[0].get("o") or "", False, "comandă referită explicit"
    if len(matched) > 1:
        return None, "", True, "clientul a referit mai multe comenzi"
    if refs:
        # clientul a referit o comandă pe care NU o putem confirma → niciodată substitui tăcut altă comandă
        return None, "", True, ("o singură comandă dar referința nu se potrivește" if len(orders) == 1
                                else "referință necunoscută + mai multe comenzi")
    if len(orders) == 1:
        return orders[0], orders[0].get("o") or "", False, "o singură comandă în cont"
    return None, "", True, "mai multe comenzi, niciuna referită clar"


PRE_STATES = {"netrimisa", "comanda noua", "noua", "plasata", "in asteptare", "draft",
              "unfulfilled", "open", "neexpediat", "de expediat", "nefinalizata"}
def fulfillment_state(order):
    """FAIL-SAFE: 'pre' DOAR dacă e clar neexpediat (fără AWB + status explicit pre).
    Necunoscut/gol/'?'/in-curs/lipsă-awb → 'post' (blochează modify/cancel — nu ghicim pe colete posibil plecate)."""
    awb = (order or {}).get("awb") or ""
    deliv = deacc((order or {}).get("deliv") or "").strip()
    if not awb and deliv in PRE_STATES:
        return "pre"
    return "post"


def customer_ident(conv_no):
    try:
        out = subprocess.run(["uv", "run", CI, "--conv", str(conv_no), "--json"], capture_output=True, text=True, timeout=90).stdout
        return json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception:
        return {}


def norm_phone(p):
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    return d[-9:] if len(d) >= 9 else ""

PROFIT_DB = os.environ.get("PROFIT_DB", "/root/Scripturi/data/profitability.db")
def lookup_orders(email, phone, order_names=(), awbs=()):
    """GROUNDING self-contained (pt VPS, fără SSH/uv): comenzile clientului din DB metrics (pg8000, după email/telefon/nr-comandă)
    + status livrare/AWB/curier din profitability.db (sqlite local), inclusiv căutare DUPĂ AWB. [] dacă nu merge (fail-safe)."""
    try:
        import pg8000.dbapi, sqlite3
    except Exception:
        return []
    byname = {}
    # 1) metrics (comenzi Shopify) după email/telefon/nr-comandă
    url = secret("DATABASE_URL_METRICS")
    if url and (email or phone or order_names):
        try:
            u = urllib.parse.urlparse(url)
            conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                        password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                        port=u.port or 5432, database=(u.path or "/").lstrip("/"))
            cur = conn.cursor()
            cols = 'name,"totalPrice","financialStatus","shopifyCreatedAt"'
            rows = []
            if email:
                cur.execute('SELECT %s FROM orders WHERE lower(email)=lower(%%s) ORDER BY "shopifyCreatedAt" DESC LIMIT 12' % cols, (email,))
                rows += cur.fetchall()
            ph = norm_phone(phone)
            if ph:
                cur.execute('SELECT %s FROM orders WHERE phone LIKE %%s OR "shippingPhone" LIKE %%s ORDER BY "shopifyCreatedAt" DESC LIMIT 12' % cols, ("%" + ph, "%" + ph))
                rows += cur.fetchall()
            ons = [o for o in dict.fromkeys(order_names) if o]
            if ons:
                cur.execute('SELECT %s FROM orders WHERE name IN (%s)' % (cols, ",".join(["%s"] * len(ons))), ons)
                rows += cur.fetchall()
            conn.close()
            for r in rows:
                byname[r[0]] = {"o": r[0], "total": float(r[1] or 0), "fin": r[2], "date": str(r[3])[:10],
                                "brand": store_prefix(r[0]) or "?", "deliv": "?", "awb": "", "courier": "", "skus": ""}
        except Exception:
            pass
    # 2) profit_orders (sqlite LOCAL): status/AWB/curier — pe nume (îmbogățire) ȘI pe AWB (găsește comanda din AWB-ul din mesaj)
    aws = [a for a in dict.fromkeys(awbs) if a]
    if os.path.exists(PROFIT_DB) and (byname or aws):
        try:
            c = sqlite3.connect(PROFIT_DB)
            def _apply(r, create=False):
                nm = r[0]
                o = byname.get(nm)
                if o is None and create:
                    o = {"o": nm, "total": 0, "fin": "", "date": "", "brand": store_prefix(nm) or "?",
                         "deliv": "?", "awb": "", "courier": "", "skus": ""}
                    byname[nm] = o
                if o is not None:
                    o["deliv"] = r[1] or "?"; o["skus"] = r[2] or ""; o["awb"] = r[3] or ""; o["courier"] = r[4] or ""
            if byname:
                names = list(byname.keys())
                q = "SELECT order_name,status_category,skus,awb,courier_key FROM profit_orders WHERE order_name IN (%s)" % ",".join("?" * len(names))
                for r in c.execute(q, names):
                    _apply(r)
            if aws:   # căutare DUPĂ AWB → găsește comanda chiar dacă n-avem email/nr-comandă (WISMO cu AWB)
                qa = "SELECT order_name,status_category,skus,awb,courier_key FROM profit_orders WHERE awb IN (%s)" % ",".join("?" * len(aws))
                for r in c.execute(qa, aws):
                    _apply(r, create=True)
            c.close()
        except Exception:
            pass
    return list(byname.values())


_TAG_CACHE = {}
AI_TAG = "ai-draft"   # tag-ul pus pe tichetele tratate de AI; suprascris de --tag (ex. ai-live la rulări live)
def tag_id(mcp, name):
    """Rezolvă nume tag → ID (add_tags_to_conversation acceptă DOAR UUID). Caută; dacă nu există, creează. Cache."""
    if name in _TAG_CACHE:
        return _TAG_CACHE[name]
    tid = None
    r = mcp.call("list_tags", {"query": name})
    for t in (r.get("tags") or []) if isinstance(r, dict) else []:
        if t.get("name") == name:
            tid = t.get("id"); break
    if not tid:
        c = mcp.call("create_tag", {"name": name})
        tid = c.get("id") if isinstance(c, dict) else None
    if tid:
        _TAG_CACHE[name] = tid
    return tid

def add_tags(mcp, cid, names):
    """Atașează tag-uri (după NUME) rezolvându-le în ID-uri — fiindcă MCP cere UUID."""
    ids = [i for i in (tag_id(mcp, n) for n in names) if i]
    if ids and cid:
        mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": ids})


def load_queue():
    try:
        with open(QUEUE) as f: return json.load(f)
    except Exception:
        return {}

def save_queue(q):
    try:
        with open(QUEUE, "w") as f: json.dump(q, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_playbook():
    """Încarcă playbook-ul ÎNVĂȚAT din tichete reale (cs-procedures → .learned_playbook.md),
    parsat pe categorii. Conține procedura de-facto + replici-șablon REALE ale agenților (=voce)."""
    path = os.path.join(HERE, ".learned_playbook.md")
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return {}
    out, cur, buf = {}, None, []
    for line in txt.splitlines():
        m = re.match(r"^##\s+([A-Za-z_]+)\b", line)
        if m:
            if cur:
                out[cur.lower()] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur.lower()] = "\n".join(buf).strip()
    return out

LEARNED = load_playbook()  # gol până rulează cs-procedures → fallback pe playbook-ul din SYSTEM


# ---- REGISTRU DE INCIDENTE (fapte VERIFICATE per comandă) ----
# De ce: la un incident de expediere (ex. colete plecate cu mai puține bucăți decât s-au comandat)
# AI-ul nu știe CE am trimis, deci deflectează generic în loc să spună adevărul. Registrul leagă
# comanda → fapte verificate, ca draftul să plece de la ce ȘTIM despre acel client.
# ⚠️ Repo PUBLIC: aici stă DOAR structura + exemplul anonimizat (incidents.example.json).
# Datele REALE (nr. comenzi, AWB, date) stau ÎN AFARA repo-ului, la calea din env CS_INCIDENTS_FILE
# (implicit ~/.arona/cs_incidents.json pe stație; pe VPS pune-l în /root/Scripturi/data/).
INCIDENTS_SCHEMA = "cs-incidents/1"
INCIDENTS_FILE = os.environ.get("CS_INCIDENTS_FILE") or os.path.expanduser("~/.arona/cs_incidents.json")


def _ord_key(s):
    """Normalizează un nr. de comandă pt potrivire (fără spații/liniuțe, majuscule) — ca resolve_target_order."""
    return re.sub(r"[\s-]", "", str(s or "")).upper()


def load_incidents(path=None):
    """Registrul de incidente → {nr_comandă normalizat: (incident, fapte)}.
    FAIL-SAFE: {} dacă fișierul lipsește / e stricat / are altă schemă → rămâne comportamentul de azi."""
    p = path or INCIDENTS_FILE
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("schema") != INCIDENTS_SCHEMA:
        print("⚠️ registru de incidente IGNORAT (%s): schemă %r, aștept %s" % (
            p, (data.get("schema") if isinstance(data, dict) else "?"), INCIDENTS_SCHEMA), file=sys.stderr)
        return {}
    idx = {}
    for inc in data.get("incidents") or []:
        if not inc.get("active", True):   # dezactivezi un incident fără să-l ștergi
            continue
        for name, ent in (inc.get("orders") or {}).items():
            k = _ord_key(name)
            if k and isinstance(ent, dict):
                idx[k] = (inc, dict(ent, order=name))
    if idx:
        print("📒 registru incidente: %d comenzi afectate (%s)" % (len(idx), p), file=sys.stderr)
    return idx


INCIDENTS = load_incidents()


def load_active_incidents(path=None):
    """Incidentele ACTIVE ca LISTĂ (fără harta pe comenzi) — sursa recunoașterii pe canal PUBLIC, care
    ține de MAGAZIN, nu de comanda persoanei. FAIL-SAFE: [] dacă fișierul lipsește/e stricat/altă schemă."""
    p = path or INCIDENTS_FILE
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, dict) or data.get("schema") != INCIDENTS_SCHEMA:
        return []
    active = [i for i in (data.get("incidents") or []) if isinstance(i, dict) and i.get("active", True)]
    for inc in active:   # ZGOMOTOS: un registru care tace când nu se potrivește e mai rău decât unul absent
        for s in _store_names(inc):
            w = _incident_topic_warn(inc, s)
            if w:
                print(w, file=sys.stderr)
    return active


def _awb_txt(v):
    return ", ".join(str(x) for x in v if x) if isinstance(v, (list, tuple)) else str(v or "")


def _safe_desc(v, is_public):
    """Text liber din registru, pregătit pt prompt. Pe canal PUBLIC câmpul se ARUNCĂ întreg dacă are ceva
    identificabil (email / nr. comandă / AWB / sumă), exact ca în incident_signals: registrul e scris de om
    și un câmp liber nu are voie să strecoare public date de client."""
    s = " ".join(str(v or "").split())[:180]
    if not s:
        return ""
    # +r4: și ancora de ADRESĂ și un NUME PROPRIU aruncă descrierea pe canal public — azi treceau
    # verbatim în promptul public, deși registrul e text liber scris de om.
    if is_public and (INCIDENT_PII.search(s) or ORDER_RE.search(s) or ADDR_RE.search(s) or NUME_PROPRIU_RE.search(s)):
        return ""
    return s


# Cod care ARATĂ a identitate, chiar dacă prefixul nu e în ORDER_RE: magazine noi, AWB scris fără
# spații, orice „litere+cifre". Pe public preferăm să tăiem prea mult decât să lăsăm o potrivire pe om.
PUBLIC_IDENT_RE = re.compile(r"\b[a-z]{2,6}[ -]?\d{3,9}\b|\b\d{3,}\b", re.I)


def public_safe_text(s):
    """Textul clientului curățat de IDENTITATE (nr. comandă, email, AWB/cifre lungi, sumă) — atât cât
    să se poată potrivi SUBIECTUL reclamației, niciodată persoana. Se folosește pe canal public înainte
    de orice potrivire de incident: altfel potrivirea ar putea fi făcută pe numărul de comandă, adică
    exact oracolul pe care îl închidem."""
    return PUBLIC_IDENT_RE.sub(" ", INCIDENT_PII.sub(" ", ORDER_RE.sub(" ", s or "")))


def _store_names(inc):
    """Magazinele pe care le acoperă un incident: `store` (șir SAU listă) + `store_aliases` explicite."""
    raw = inc.get("store") or []
    if isinstance(raw, str):
        raw = [raw]
    al = inc.get("store_aliases") or []
    if isinstance(al, str):
        al = [al]
    return [str(s).strip() for s in list(raw) + list(al) if str(s or "").strip()]


def _store_match(a, b):
    """Potrivire EXACTĂ magazin(registru) ↔ magazin(pagină), fără diacritice/majuscule. `a` = numele
    din registru: un șir SAU lista nume+aliasuri (vezi _store_names).

    De ce EXACT și nu pe subșir: „Duppo" prindea toate cele ȘASE pagini Duppo — inclusiv piețele pe
    care NU răspundem (Duppo Moldova, Duppo Czechia) — iar „Bonhaus" prindea BG, CZ și PL deodată.
    Adică un incident dintr-o țară se anunța public în alta, pe limba altcuiva. Un incident care chiar
    atinge mai multe magazine se scrie ACUM explicit: `"store": ["Duppo BG", "Duppo SK"]` sau
    `"store_aliases": [...]` — o listă pe care o vede și o aprobă omul, nu una ghicită din prefix."""
    y = deacc(str(b or "")).strip()
    if not y:
        return False
    names = a if isinstance(a, (list, tuple)) else [a]
    return any(deacc(str(x or "")).strip() == y for x in names)


def _topic_lang(store_name):
    """Limba în care scrie piața magazinului — același semnal (sigur) folosit și pentru limba
    răspunsului: mai fiabil decât detecția pe un comentariu de trei cuvinte."""
    return STORE_LANG.get(store_name) or "ro"


def _pub_topics(inc, store_name=""):
    """Termenii prin care un incident se potrivește cu CE DESCRIE clientul în comentariul lui public.

    `topic` se scrie PE LIMBĂ: {"bg": [...], "sk": [...], "*": [...]}, unde „*" = termeni fără limbă
    (cifre, „2+1", nume de produs). Se folosesc DOAR termenii pe limba PIEȚEI magazinului plus „*".
    O listă sau un șir simplu (registrele vechi) se tratează ca „*", ca să nu rupem ce există.

    De ce s-a schimbat: fără `topic`, derivam cuvintele din DESCRIEREA incidentului, care e scrisă în
    ROMÂNĂ. Un comentariu în bulgară nu se potrivește cu cuvinte românești, deci brațul public — tot
    scopul pentru care a fost construit — era un no-op TĂCUT pe piața străină: măsurat pe 133 de
    comentarii publice REALE de pe paginile Duppo (BG/SK/HU), din care 46 reclamă exact incidentul de
    colete incomplete, se aprindea pe 0. Fallback-ul pe descriere e SCOS: fără termeni pe limba pieței
    nu recunoaștem public nimic, iar `load_active_incidents` STRIGĂ la încărcare (_incident_topic_warn)
    în loc să tacă — un registru care tace când nu se potrivește e mai rău decât unul absent, fiindcă
    ownerul crede că merge.

    Tokenii care arată a date personale (nr. comandă, AWB, sumă, email) sunt ARUNCAȚI: altfel un
    registru scris în grabă ar recrea exact oracolul, potrivind pe identitate, nu pe subiect."""
    raw = inc.get("topic") or inc.get("topics") or []
    if isinstance(raw, dict):
        raw = list(raw.get(_topic_lang(store_name)) or []) + list(raw.get("*") or [])
    if isinstance(raw, str):
        raw = raw.split(",")
    out = []
    for k in raw:
        k = deacc(str(k or "")).strip()
        if len(k) >= 3 and not INCIDENT_PII.search(k) and not ORDER_RE.search(k) and not PUBLIC_IDENT_RE.search(k):
            out.append(k)
    return out


def _incident_topic_warn(inc, store):
    """Mesajul de avertizare pentru un incident ACTIV care n-are termeni pe limba pieței magazinului.
    '' dacă e în regulă. Se tipărește la ÎNCĂRCARE (vezi load_active_incidents), nu la prima potrivire
    ratată: ownerul trebuie să afle că brațul public e mort ÎNAINTE să se bazeze pe el."""
    lang = _topic_lang(store)
    raw = inc.get("topic") or inc.get("topics") or []
    if isinstance(raw, dict) and raw.get(lang):
        return ""
    eff = _pub_topics(inc, store)
    if not eff:
        return ("⚠️ REGISTRU INCIDENTE: brațul PUBLIC e MORT pentru %r (incident %r, limba pieței %r) — "
                "zero termeni de subiect. Scrie topic: {\"%s\": [\"…\", \"…\"]} cu fraze EXACT cum le "
                "scrie clientul pe piața aia; până atunci nu se recunoaște public NIMIC acolo." % (
                    store, inc.get("id") or "?", lang, lang))
    return ("⚠️ REGISTRU INCIDENTE: %r (incident %r) n-are termeni pe limba pieței %r — se potrivesc doar "
            "cei fără limbă (%d). Dacă sunt scriși românește, brațul public NU se va aprinde acolo." % (
                store, inc.get("id") or "?", lang, len(eff)))


# Se încarcă AICI, nu la definiția funcției: avertismentul de mai sus are nevoie de _pub_topics și
# _store_names, deci lista activă se materializează abia după ce toate trei există.
INCIDENTS_ACTIVE = load_active_incidents()


def public_incident_facts(store_name, text="", active=None):
    """Recunoașterea PUBLICĂ a unui incident — ține de MAGAZIN + incident activ (și, opțional, de ce
    descrie clientul în propriul comentariu), NICIODATĂ de comanda lui.

    De ce: dacă blocul ar apărea doar când numărul de comandă citat e în registru, răspunsul public ar
    diferi între o comandă afectată și una neafectată, iar oricine ar putea afla, postând un număr, dacă
    acea comandă e printre cele lovite. Așa, toți cei care comentează pe pagina magazinului primesc
    ACELAȘI text, deci răspunsul nu spune nimic despre nimeni. Adevărul per comandă rămâne pe PRIVAT,
    unde clientul e identificat — exact ce cere și invitația „scrieți-ne în privat".

    Textul clientului e curățat de nr. comandă / PII înainte de potrivire: subiectul poate conta, identitatea NU."""
    active = INCIDENTS_ACTIVE if active is None else active
    if not active or not store_name or store_name == "magazinul nostru":
        return ""
    t = deacc(public_safe_text(text))
    descs = []
    for inc in active:
        if not _store_match(_store_names(inc), store_name):
            continue
        # Potrivire pe SUBIECT (ce a scris omul public), niciodată pe identitate — și pe LIMBA pieței
        # magazinului: un comentariu în bulgară nu se potrivește cu cuvinte românești. Fără termeni pe
        # limba aia nu recunoaștem nimic public (și s-a strigat deja la încărcare, vezi _incident_topic_warn).
        if not any(k in t for k in _pub_topics(inc, store_name)):
            continue
        d = _safe_desc(inc.get("description"), True)
        if d:
            descs.append(d)
    if not descs:
        return ""
    return ("INCIDENT CUNOSCUT (DESPRE NOI, nu despre persoana care scrie): %s\n"
            "REGULI CANAL PUBLIC: recunoaște DESCHIS incidentul, la general, și cere-ți scuze — dar NU confirma că "
            "persoana care scrie e clientul nostru și NU spune „comanda dumneavoastră”/„v-am găsit comanda”/"
            "„am verificat comanda dumneavoastră”. FĂRĂ număr de comandă, AWB, dată, sumă sau alt detaliu personal. "
            "Invit-o în privat cu numărul comenzii ca să verificăm exact cazul ei." % " | ".join(sorted(set(descs))))


PUBLIC_PERSON_REDACT = "(canal public — nu legăm persoana de comenzile/tichetele noastre)"


def person_ctx(is_public, elsewhere, hist_txt):
    """Câmpurile care descriu PERSOANA („A MAI SCRIS PE" / „ALTE TICHETE"). Pe canal PUBLIC întorc
    ACELAȘI text pentru oricine: „grounded — 2 comenzi găsite în DB" față de „grounded — nicio comandă
    găsită în DB" e tot un oracol de apartenență, la fel ca blocul de incident. Pe privat, neschimbate."""
    if not is_public:
        return elsewhere, hist_txt
    return PUBLIC_PERSON_REDACT, "    " + PUBLIC_PERSON_REDACT


def incident_facts(order_names, is_public=False, idx=None, store_name="", text="", active=None):
    """Blocul de FAPTE VERIFICATE pt comenzile atinse de un incident cunoscut.
    '' dacă nu știm nimic despre clientul ăsta → comportamentul de azi (nimic injectat).
    Pe canal PUBLIC nu se ating DELOC comenzile: recunoașterea ține de MAGAZIN + incident activ
    (public_incident_facts), deci blocul e IDENTIC pentru oricine comentează acolo — o comandă afectată
    și una neafectată dau exact același prompt. Altfel simpla PREZENȚĂ a blocului răspundea la întrebarea
    „e comanda asta printre cele lovite?" oricui postează un număr găsit. Adevărul per comandă rămâne pe
    PRIVAT, unde clientul e identificat. Câmpurile libere ies public doar curățate de INCIDENT_PII."""
    if is_public:
        # PUBLIC: nici nu citim indexul pe comenzi — apartenența persoanei nu are voie să schimbe nimic.
        return public_incident_facts(store_name, text, active)
    idx = INCIDENTS if idx is None else idx
    if not idx:
        return ""
    seen, lines, incs = set(), [], {}
    for nm in order_names or []:
        k = _ord_key(nm)
        if not k or k in seen or k not in idx:
            continue
        seen.add(k)
        inc, e = idx[k]
        incs[inc.get("id") or "?"] = inc
        rem = bool(e.get("remediated"))
        lines.append("  • %s: comandate %s buc, plecat(e) inițial %s buc; retrimis: %s%s%s.%s" % (
            e.get("order"), e.get("ordered_qty", "?"), e.get("shipped_qty", "?"),
            "DA" if rem else "NU (încă nu s-a retrimis)",
            (", la data %s" % e.get("remediated_at")) if (rem and e.get("remediated_at")) else "",
            (", AWB %s" % _awb_txt(e.get("remediation_awb"))) if (rem and e.get("remediation_awb")) else "",
            (" Retrimiterea a fost livrată: %s." % ("DA" if e.get("remediation_delivered") else "nu încă")) if rem else ""))
        if e.get("fact"):
            lines.append("    adevăr verificat: %s" % e.get("fact"))
    if not lines:
        return ""
    desc = " | ".join(sorted(set(d for d in (_safe_desc(i.get("description"), False) for i in incs.values()) if d)))
    head = "INCIDENT CUNOSCUT — FAPTE VERIFICATE (registru intern %s): %s" % (", ".join(sorted(incs)), desc or "—")
    tail = ("REGULI: faptele de mai sus sunt REALE și VERIFICATE (NU intră sub anti-halucinare) — folosește-le, sunt SINGURELE "
            "livrări/date/AWB pe care ai voie să le afirmi. Dacă i-au lipsit bucăți și S-AU retrimis: spune-i CLAR asta și cere-ți scuze. "
            "Dacă NU s-au retrimis încă: recunoaște problema, cere-ți scuze și spune că revenim cu detalii — FĂRĂ să promiți o dată pe care n-o avem. "
            "Tradu faptele în limba clientului (nu le cita în română).")
    return head + "\n" + "\n".join(lines) + "\n" + tail


# Garda DURĂ a incidentului: draftul nu poate afirma un AWB sau o dată care nu vine din context
# (registru + comenzi). Verificarea e pe CIFRE, deci merge identic pe orice limbă/piață.
# Datele se compară pe COMPONENTE, nu pe format: modelul scrie „10.09.2026" pt un registru care zice
# „2026-09-10" — e ACEEAȘI dată, nu o invenție (măsurat: altfel suprimam drafturi corecte).
# Liniuța e separator de dată DOAR în forma ISO, ca „2-3 zile" să nu fie citit ca dată.
LONG_NUM_RE = re.compile(r"\b\d{9,16}\b")
DATE_CLAIM_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b|\b\d{4}-\d{1,2}-\d{1,2}\b")


def _date_parts(tok):
    return set(str(int(x)) for x in re.split(r"[./-]", tok) if x.isdigit())


def _is_date_token(tok):
    """True doar dacă ce a prins DATE_CLAIM_RE e chiar o DATĂ, nu o ZECIMALĂ. „3.5" (cm), „4.8" (notă),
    „12.99" (preț) NU sunt date; „10.09", „3.09", „10.09.2026", „2026-09-10" sunt. Forma scurtă cere luna
    scrisă pe două cifre — așa se scriu datele, nu zecimalele. Fără asta ORICE zecimală din draft trecea
    drept dată inventată: regenerare și, dacă modelul insista, draft SUPRIMAT pe tichetele cu incident."""
    parts = [p for p in re.split(r"[./-]", tok or "") if p != ""]
    if not all(p.isdigit() for p in parts):
        return False
    if len(parts) >= 3:
        return True
    if len(parts) != 2:
        return False
    d, mth = parts
    return len(mth) == 2 and 1 <= int(d) <= 31 and 1 <= int(mth) <= 12


def incident_backed(hit, inc_blk):
    """True dacă un motiv de anti-halucinare e acoperit de FAPTELE VERIFICATE: fiecare cifră din el e un
    număr ÎNTREG din registru, de cel puțin 4 cifre (AWB/an — ce chiar vine de acolo). Exceptarea ține
    strict de CIFRELE registrului, nu de tot tichetul: un „în 3 zile" sau un „30 cm" inventat trecea
    nefiltrat pe orice tichet atins de un incident, doar fiindcă „3" apare undeva în fapte."""
    nums = re.findall(r"\d+", hit or "")
    tok = set(re.findall(r"\d+", inc_blk or ""))
    return bool(nums) and all(len(n) >= 4 and n in tok for n in nums)


def unbacked_claims(draft, allowed):
    """AWB-uri/date afirmate în draft care NU se regăsesc în contextul dat. Listă goală = curat."""
    allowed = allowed or ""
    a_digits = re.sub(r"\D", "", allowed)
    a_dates = [_date_parts(m.group(0)) for m in DATE_CLAIM_RE.finditer(allowed) if _is_date_token(m.group(0))]
    out = []
    for m in LONG_NUM_RE.finditer(draft or ""):
        if m.group(0) not in a_digits and m.group(0) not in out:
            out.append(m.group(0))
    for m in DATE_CLAIM_RE.finditer(draft or ""):
        if not _is_date_token(m.group(0)):
            continue
        p = _date_parts(m.group(0))
        if p and not any(p <= ad for ad in a_dates) and m.group(0) not in out:
            out.append(m.group(0))
    return out


def guard_reasons(draft, ctx, inc_blk="", has_orders=False, is_public=False, phone_order="",
                  cust_name=""):
    """Motivele pentru care un draft NU poate fi acceptat — exact gărzile de conținut de pe calea de
    generare, într-un singur loc, ca să poată fi rulate ȘI pe drafturile REGENERATE:
      • anti-halucinare (sărită când chiar avem datele comenzii, ca în main),
      • cifrele din afara registrului de incidente,
      • date personale rămase într-un draft de canal PUBLIC.
    [] = curat."""
    d = draft or ""
    if not d or d.startswith("(eroare"):
        return []
    bad = []
    if not has_orders:
        bad += [h for h in hallu_hits(d, ctx, inc_blk) if not (inc_blk and incident_backed(h, inc_blk))]
    if inc_blk:
        bad += unbacked_claims(d, ctx)
    if is_public:
        # +r4/merge: `cust_name` — public_pii_leaks a fost lărgit (email/adresă/NUME) de cheia
        # `pii-public` DUPĂ ce verificatorul ăsta a fost scris. Fără el, o regenerare pe canal
        # public era verificată doar pe telefon/comandă/AWB.
        bad += public_pii_leaks(d, phone_order, cust_name)
    return bad


def build_action_cmd(p, store, order):
    a = p.get("action")
    if a == "modify":
        # modify tratează DOAR adresa/telefonul (pre-fulfillment). Schimbările de PRODUS merg pe swap —
        # NU mapăm „adaugă produs" pe --set (care doar schimbă cantitatea unei linii deja existente).
        if not (p.get("new_address") and p.get("new_city") and p.get("new_zip")):
            return None
        return ["modify", "--order", order, "--store", store,
                "--address", p["new_address"], "--city", p["new_city"], "--zip", p["new_zip"]]
    if a == "cancel":
        return ["cancel", "--order", order, "--store", store, "--reason", "customer"]
    if a == "swap":
        return ["swap", "--from-order", order, "--store", store, "--items", p["items"]] if p.get("items") else None
    if a == "resend":
        return ["resend", "--from-order", order, "--store", store, "--items", p["items"]] if p.get("items") else None
    return None


def run_cs_action(cmd_args, apply=False, agent=None):
    """Întoarce (returncode, output). rc!=0 = eșec real (cs_actions face sys.exit pe erori)."""
    if not os.path.exists(CSA):
        return 1, "(cs_actions.py negăsit la %s)" % CSA
    full = ["uv", "run", CSA] + cmd_args
    if agent: full += ["--agent", agent]
    if apply: full += ["--apply"]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
    except Exception as e:
        return 1, "(eroare cs-actions: %s)" % e


_PAGE_TOK_CACHE = {}
def fb_page_token(page_id):
    """Token de PAGINĂ pt page_id. Încearcă pe rând tokenurile de sistem (un cont vede doar paginile lui).
    Întoarce token de pagină REAL doar dacă vreun token chiar are acces la pagină; altfel None (NU minți callerul)."""
    if not page_id:
        return None
    if page_id in _PAGE_TOK_CACHE:
        return _PAGE_TOK_CACHE[page_id]
    for key in ("META_PAGES_TOKEN", "META_SYSTEM_TOKEN_3", "META_SYSTEM_TOKEN", "META_SYSTEM_TOKEN_2", "META_SYSTEM_TOKEN_4", "META_USER_TOKEN"):
        sys_tok = secret(key)
        if not sys_tok or sys_tok.startswith("REVOKED"):
            continue
        try:
            u = "https://graph.facebook.com/v19.0/%s?fields=access_token&access_token=%s" % (page_id, urllib.parse.quote(sys_tok))
            r = json.loads(urllib.request.urlopen(u, timeout=30).read())
            if isinstance(r, dict) and r.get("access_token"):
                _PAGE_TOK_CACHE[page_id] = r["access_token"]
                return r["access_token"]
        except Exception:
            continue
    _PAGE_TOK_CACHE[page_id] = None
    return None


def fb_post_text(post_id, page_id):
    """Textul postării/reclamei la care comentează clientul (Graph, token de pagină). '' dacă nu avem acces."""
    tok = fb_page_token(page_id)
    if not tok or not post_id:
        return ""
    for cand in ("%s_%s" % (page_id, post_id), str(post_id)):
        try:
            u = "https://graph.facebook.com/v19.0/%s?fields=message,story&access_token=%s" % (urllib.parse.quote(cand), urllib.parse.quote(tok))
            r = json.loads(urllib.request.urlopen(u, timeout=30).read())
            msg = (r.get("message") or r.get("story") or "") if isinstance(r, dict) else ""
            if msg:
                return " ".join(msg.split())[:500]
        except Exception:
            continue
    return ""


def fb_hide_comment(comment_id, page_id, hide=True):
    """Ascunde/afișează un comentariu FB/IG prin Graph API (token de pagină). Raportează răspunsul."""
    tok = fb_page_token(page_id)
    if not tok:
        return "(fără token Meta în KB)"
    segs = str(comment_id).split("_")
    cands = []
    if len(segs) >= 2:
        cands += [segs[-2] + "_" + segs[-1], segs[-1]]
    cands.append(str(comment_id))
    last = ""
    for c in cands:
        try:
            u = "https://graph.facebook.com/v19.0/%s?is_hidden=%s&access_token=%s" % (
                urllib.parse.quote(c), "true" if hide else "false", urllib.parse.quote(tok))
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, method="POST"), timeout=30).read())
            if r is True or (isinstance(r, dict) and r.get("success")):
                return "✅ comentariu %s %s" % (c, "ascuns" if hide else "afișat")
            last = json.dumps(r)
        except Exception as e:
            last = str(e)
    return "⚠️ hide eșuat (scope token / format comment-id de validat): %s" % last[:200]


def fb_private_reply(comment_id, page_id, message):
    """Trimite un MESAJ PRIVAT (DM) ca răspuns la un comentariu public FB/IG (Graph private_replies).
    Constrângeri FB: 1 singur private reply / comentariu, în fereastra de 7 zile, token pagină cu scope de mesagerie."""
    tok = fb_page_token(page_id)
    if not tok:
        return "(fără token Meta în KB)"
    if not (message or "").strip():
        return "(fără mesaj de trimis)"
    segs = str(comment_id).split("_")
    cands = []
    if len(segs) >= 2:
        cands += [segs[-2] + "_" + segs[-1], segs[-1]]
    cands.append(str(comment_id))
    last = ""
    for c in cands:
        try:
            u = "https://graph.facebook.com/v19.0/%s/private_replies" % urllib.parse.quote(c)
            data = urllib.parse.urlencode({"message": message, "access_token": tok}).encode()
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=data, method="POST"), timeout=30).read())
            if isinstance(r, dict) and (r.get("id") or r.get("message_id")):
                return "✅ mesaj privat (DM) trimis pentru comentariul %s" % c
            last = json.dumps(r)
        except Exception as e:
            last = str(e)
    return "⚠️ private reply eșuat (scope token / fereastră 7 zile / format comment-id de validat): %s" % last[:200]


def do_approve(mcp, conv_no, agent):
    q = load_queue()
    p = q.get(str(conv_no))
    if not p:
        print("Nicio propunere salvată pentru #%s. Rulează întâi flow-ul (dry-run)." % conv_no); return
    if p.get("applied"):
        print("#%s a fost deja aplicat — nu reaplic (evit dublarea)." % conv_no); return
    applied_ok = False
    if p.get("cmd"):
        if not agent:
            print("Acțiunea necesită --agent <Raluca/Oana/Andra/Anna/OanaO>."); return
        print("APLIC acțiune #%s: %s" % (conv_no, " ".join(p["cmd"])))
        rc, out = run_cs_action(p["cmd"], apply=True, agent=agent); print(out)
        # succes = exit 0 ȘI marker ✅ ȘI fără avertisment de eșec parțial (⚠). NU mai ghicim din 'error'/'eroare'.
        if rc != 0 or "✅" not in out or "⚠" in out:
            print("⚠️ Acțiunea a EȘUAT / e incompletă — NU salvez draftul de confirmare (nu mint clientul). Entry-ul rămâne ne-aplicat (poți reîncerca)."); return
        applied_ok = True
        # confirmarea „FĂCUT" se generează DOAR acum, după aplicare reușită
        if p.get("ctx") and p.get("action_desc"):
            try:
                _ctx = p.get("ctx") or ""
                _d = llm(SYSTEM, p["ctx"] + "\nACTIUNE_APLICATA: %s la comanda %s." % (p["action_desc"], p.get("order") or ""))[0].strip()
                _lg = ctx_lang(_ctx)
                _inc = ctx_inc_blk(_ctx)
                # ACELEAȘI gărzi ca pe calea de generare, ȘI pe draftul întors de regenerare (`verif`):
                # altfel confirmarea regenerată aici ocolea și anti-halucinarea, și garda de incident,
                # și garda de date personale pe canal public.
                def _verif(_x, _c=_ctx, _i=_inc, _ho=p.get("has_orders"),
                           _pub=p.get("is_public"), _ph=p.get("phone_order") or "",
                           _nm=p.get("cust_name") or ""):
                    return guard_reasons(_x, _c, _i, _ho, _pub, _ph, _nm)
                _d = apply_post_guards(
                    _d, "EMOJI: INTERZIS" not in _ctx, complained_channels(_ctx), lang=_lg,
                    regen_reg=lambda hits: llm(SYSTEM, p["ctx"] + REGEN_REGISTRU % (", ".join(hits), register_rule(_lg)))[0],
                    verif=_verif)[0]
                if p.get("is_public"):
                    _d = redact_public_pii(_d, p.get("phone_order") or "", p.get("cust_name") or "")[0]
                    if public_pii_rest(_d, p.get("cust_name") or ""):
                        _d = ""   # tot scurge după redactare → păstrăm draftul verificat, nu-l înlocuim
                _bad = _verif(_d)
                if _d and not _d.startswith("(eroare") and not _bad:
                    p["draft"] = _d
                else:
                    print("⚠️ Confirmarea regenerată a fost RESPINSĂ de gărzi (%s) — păstrez draftul verificat."
                          % "; ".join(_bad[:3]))
            except Exception:
                pass
    if p.get("hide"):
        h = p["hide"]
        print("HIDE #%s:" % conv_no, fb_hide_comment(h.get("comment_id"), h.get("page_id"), hide=True))
        applied_ok = True
    # la hide NU salvăm draft (comentariul se ascunde); altfel salvăm draftul public
    _cmode = (p.get("hide") or {}).get("mode")
    if p.get("draft") and not p["draft"].lstrip().startswith("(eroare") and _cmode != "hide":
        res = mcp.call("create_draft", {"conversation_id": p["cid"], "body": p["draft"]})
        ok = not (isinstance(res, dict) and res.get("_error"))
        print("✅ DRAFT salvat (NU trimis)." if ok else "⚠️ create_draft: %s" % res)
    if applied_ok and p.get("cid"):  # marchează tichetul drept tratat de AI
        add_tags(mcp, p["cid"], [AI_TAG])
    if applied_ok:  # consumă acțiunile ca să nu se reaplice la o a doua rulare --approve
        p["cmd"] = None; p["hide"] = None; p["applied"] = True
        q[str(conv_no)] = p; save_queue(q)


# ── GARDĂ DE TRIMITERE LIVE ───────────────────────────────────────────────────────────────
# Motorul e DRAFT-ONLY, dar până acum asta era o CONVENȚIE: `--apply-send` (lot) și `--send`
# (per tichet) trimit răspunsul la client prin send_message și ÎNCHID tichetul — ireversibil.
# O greșeală de linie de comandă, sau o linie de cron copiată de altundeva, era de ajuns.
# De aici încolo trimiterea cere un consimțământ EXPLICIT, care NU stă în linia de comandă:
#     export CS_TRIMITERE_LIVE=DA                  (variabilă de mediu)
#   sau
#     echo DA > ~/.arona/cs_trimitere_live.ok      (fișier de confirmare; CS_TRIMITERE_FILE îl mută)
# Funcționalitatea rămâne întreagă pentru ziua în care se decide trimiterea — se armează în
# două secunde și se dezarmează la fel.
TRIMITERE_ENV = "CS_TRIMITERE_LIVE"
TRIMITERE_FILE = os.environ.get("CS_TRIMITERE_FILE") or os.path.expanduser("~/.arona/cs_trimitere_live.ok")


def trimitere_permisa():
    """Trimiterea LIVE către clienți e armată explicit (mediu SAU fișier de confirmare)?"""
    if (os.environ.get(TRIMITERE_ENV) or "").strip().upper() == "DA":
        return True
    try:
        with open(TRIMITERE_FILE, encoding="utf-8") as f:
            return f.read(64).strip().upper().startswith("DA")
    except OSError:
        return False


def garda_trimitere(cale):
    """Oprește procesul ÎNAINTE de orice apel dacă trimiterea live nu e armată."""
    if trimitere_permisa():
        print("⚠️  TRIMITERE LIVE ARMATĂ (%s) — răspunsurile pleacă la CLIENȚI, ireversibil." % cale)
        return True
    sys.stderr.write(
        "⛔ TRIMITERE LIVE BLOCATĂ (%s). Motorul rămâne DRAFT-ONLY până e armat EXPLICIT:\n"
        "     export %s=DA\n"
        "   sau\n"
        "     echo DA > %s\n"
        "   Fără asta nu pleacă niciun mesaj la client. Scoate flagul ca să scrii DRAFTURI.\n"
        % (cale, TRIMITERE_ENV, TRIMITERE_FILE))
    sys.exit(2)


def draftam_public(comments, no_comments, auto_hide):
    """Ce facem cu un tichet de pe canal PUBLIC (comentariu FB/IG).

    „draft"          — răspundem public (cerut explicit prin --comments)
    „doar-moderare"  — intră doar pentru ascunderea spamului (--auto-hide), FĂRĂ draft public
    „sarit"          — nu-l atingem deloc
    Implicit = sigur: canalul public e cel pe care o scurgere de date e vizibilă pentru toată
    lumea, deci răspunsul public se cere, nu se moștenește."""
    if no_comments:
        return "sarit"
    if comments:
        return "draft"
    return "doar-moderare" if auto_hide else "sarit"


def do_send(mcp, conv_no, agent):
    """TRIMITE LIVE răspunsul (draftul din coadă) la client prin send_message. Customer-facing, ireversibil.
    Doar per-tichet, explicit. Refuză escaladările (acelea cer om) și retrimiterea."""
    garda_trimitere("--send")
    q = load_queue()
    p = q.get(str(conv_no))
    if not p:
        print("Nicio propunere salvată pentru #%s. Rulează întâi flow-ul." % conv_no); return
    if p.get("sent"):
        print("#%s a fost deja TRIMIS — nu retrimit (evit dublarea)." % conv_no); return
    if p.get("escalate"):
        print("#%s e ESCALADAT → preia un om, NU trimit automat (draftul e doar mesaj de așteptare)." % conv_no); return
    if p.get("is_public"):
        # ACELAȘI refuz ca la --apply-send (`not is_public`): un comentariu FB/IG e vizibil pentru
        # oricine, deci răspunsul public rămâne al unui om. Fără linia asta, `--send <nr>` posta public.
        print("#%s e pe canal PUBLIC (comentariu FB/IG) → NU trimit automat: răspunsul public îl dă un om (draftul rămâne salvat)." % conv_no); return
    if (p.get("hide") or {}).get("mode") == "hide":
        print("#%s e propus la HIDE (spam) — nu e de trimis un răspuns." % conv_no); return
    draft = (p.get("draft") or "").strip()
    cid = p.get("cid")
    if not draft or not cid:
        print("#%s nu are draft/conversation_id de trimis." % conv_no); return
    res = mcp.call("send_message", {"conversation_id": cid, "body": draft})
    ok = not (isinstance(res, dict) and res.get("_error"))
    if ok:
        print("📤 TRIMIS LIVE la client #%s (send_message)." % conv_no)
        add_tags(mcp, cid, [AI_TAG, "ai-sent"])
        # tichetul a primit răspuns → îl ÎNCHIDEM (să nu rămână open)
        cres = mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
        print("   ✅ Tichet ÎNCHIS (CLOSED) după răspuns." if not (isinstance(cres, dict) and cres.get("_error")) else "   ⚠️ close eșuat: %s" % cres)
        p["sent"] = True; q[str(conv_no)] = p; save_queue(q)
    else:
        print("⚠️ send_message a EȘUAT pentru #%s: %s" % (conv_no, res))


def escalation_note(level, reason, problem, name, phone, email, order_line, elsewhere, sent, suggested):
    return ("⚠️ ESCALADARE [%s] — %s\n"
            "Problemă: %s\n"
            "Client: %s | tel: %s | email: %s\n"
            "Comandă: %s\n"
            "A mai scris pe: %s\n"
            "Sentiment: %s\n"
            "→ ACȚIUNE SUGERATĂ: %s\n"
            "(brief auto cs_auto_draft — verifică înainte de a acționa)" % (
                level, reason, problem, name or "?", phone or "—", email or "—",
                order_line or "—", elsewhere, sent, suggested or "preia și contactează clientul"))


def callback_note(phone, name, problem, store, platform, order_line):
    return ("📞 DE SUNAT — clientul cere să-l contactăm TELEFONIC (nu l-am trimis să sune el)\n"
            "Telefon: %s\n"
            "Client: %s | magazin: %s | canal: %s\n"
            "Comandă: %s\n"
            "Cerere: %s\n"
            "(notă internă auto cs_auto_draft — draftul doar confirmă că sunăm noi)" % (
                phone or "— (nu a lăsat număr; draftul îl cere)", name or "?", store, platform,
                order_line or "—", problem or "—"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--channel", default=None)
    ap.add_argument("--create-draft", action="store_true", help="scrie DRAFTURI + rutare escaladare (NU trimite, NU aplică acțiuni)")
    ap.add_argument("--approve", default=None, help="nr conversație: aplică acțiunea propusă + salvează draftul")
    ap.add_argument("--agent", default=os.environ.get("CS_AGENT"))
    ap.add_argument("--actions", default="modify,cancel,swap,resend", help="acțiuni ACTIVE (restul doar draft); ex --actions modify,cancel | none")
    ap.add_argument("--scan", type=int, default=150)
    ap.add_argument("--sleep", type=float, default=0.2, help="pauză (s) între tichete — crește pt rate-safety pe loturi mari (ex. 0.5)")
    ap.add_argument("--lean", action="store_true", help="proces REDUS pt volum mare: fără 360/SSH (comenzi), fără rutare escaladare (priority/notă) — doar transcript → draft → create_draft. Mult mai rapid + mai puține scrieri Richpanel.")
    ap.add_argument("--ground", action="store_true", help="GROUNDING self-contained (pt VPS/cron): caută comenzile clientului DIRECT din DB metrics + profitability.db (fără SSH/uv) → draftul are status/AWB real. Mai lent ca lean, dar fără halucinări de comandă.")
    ap.add_argument("--skip-tagged", action="store_true", help="sare tichetele care AU deja tag-ul AI (--tag) — pt cron/reluare: draftează DOAR tichetele noi, fără dubluri")
    ap.add_argument("--auto-hide", action="store_true", help="APLICĂ pe loc ascunderea comentariilor clasificate spam/abuz (Graph, reversibil), pe TOATE magazinele — inclusiv pe piețele unde nu răspundem. Fără el, hide-ul rămâne o propunere pentru --approve.")
    ap.add_argument("--include-skipped", action="store_true", help="răspunde ȘI pe piețele excluse din AI_SKIP_STORES (azi: Moldova și Cehia). Implicit sunt sărite — decizie de owner 15-sep-2026.")
    ap.add_argument("--no-comments", action="store_true", help="exclude complet canalele de comentarii (facebook_feed_comment/instagram_comment) — nu le draftează (ex. pt cron: comentariile rămân pt CS)")
    ap.add_argument("--comments", action="store_true", help="răspunde ȘI pe canalele PUBLICE de comentarii (facebook_feed_comment/instagram_comment). IMPLICIT NU: pe public o scurgere de date e vizibilă pentru toată lumea. Cu --auto-hide dar fără --comments, comentariile intră doar pentru MODERARE (ascundere spam), fără draft public.")
    ap.add_argument("--fast-triage", action="store_true", help="EFICIENȚĂ: sare apelul LLM de TRIAJ când categoria regex e sigură (non-'altele') → ~1 apel LLM/tichet în loc de 2. Gărzile de spam + escaladare rămân deterministe. Pierde doar extracția fină LLM (acțiuni/adresă) care oricum cere --approve.")
    ap.add_argument("--photos", action=argparse.BooleanOptionalAction, default=True, help="VEDE pozele atașate de client (descarcă + descrie vizual) → draftul ține cont de conținut (defect/dovadă livrare). --no-photos dezactivează. Costă o cerere vizuală DOAR pe tichetele cu poze (rare).")
    ap.add_argument("--apply-send", action="store_true", help="⚠️ LIVE: TRIMITE răspunsul la client (send_message) + închide tichetul, în loc de draft — DOAR pe ne-escaladate + ne-comentariu (escaladările/comentariile rămân draft, le ia un om). Customer-facing, IREVERSIBIL. Necesită --create-draft.")
    ap.add_argument("--json", action="store_true", help="emite drafturile structurat (audit/integrare) — JSON pe ultima linie după marcajul @@JSON@@")
    ap.add_argument("--close-spam", action="store_true", help="închide (CLOSED) + tag 'spam' tichetele detectate ca spam/notificare automată")
    ap.add_argument("--tag", default="ai-draft", help="tag-ul pus pe tichetele tratate de AI (ex. --tag ai-live pt o rulare live)")
    ap.add_argument("--only", default=None, help="procesează DOAR aceste numere de conversație (lista separată prin virgulă) — pt regenerare țintită")
    ap.add_argument("--send", default=None, help="nr conversație: TRIMITE LIVE răspunsul (draftul din coadă) la client via send_message — customer-facing, IREVERSIBIL (refuză escaladări/hide/retrimitere)")
    a = ap.parse_args()
    global AI_TAG
    AI_TAG = a.tag
    if a.apply_send or a.send:   # ÎNAINTE de secret()/MCP: o greșeală de linie de comandă nu trimite nimic
        garda_trimitere("--apply-send" if a.apply_send else "--send")
    ALLOWED = set(x.strip().lower() for x in a.actions.split(",") if x.strip() and x.strip().lower() != "none")
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    COURIER = {"dpd-ro": "DPD", "dpd": "DPD", "sameday": "Sameday", "packeta": "Packeta", "econt": "Econt"}

    if a.approve:
        do_approve(mcp, a.approve, a.agent); return
    if a.send:
        do_send(mcp, a.send, a.agent); return

    ONLY = [x.strip().lstrip("#") for x in (a.only or "").split(",") if x.strip()]
    picked = []
    if ONLY:
        # regenerare ȚINTITĂ: stub-uri (fără fetch upfront) — tichetul se ia INCREMENTAL în buclă (evită burst-ul de citiri)
        picked = [{"conversation_no": x, "_stub": True} for x in ONLY]
    else:
        page, seen = 1, set()
        while len(picked) < a.limit and len(seen) < a.scan:
            args = {"status": "open", "page": page, "per_page": 50, "sortKey": "last_message_at", "order": "desc"}
            if a.channel: args["channel"] = a.channel
            r = mcp.call("list_conversations", args)
            batch = (r.get("tickets") or r.get("conversations") or r.get("results") or []) if isinstance(r, dict) else []
            if not batch: break
            new_this_page = 0
            for t in batch:
                cid = t.get("id") or t.get("conversation_no")
                if cid in seen: continue
                seen.add(cid); new_this_page += 1
                if (t.get("last_message_sender_type") or "").lower() != "customer": continue
                picked.append(t)
                if len(picked) >= a.limit: break
            # API-ul nu setează 'has_more' fiabil → paginează cât timp apar tichete NOI; oprește când nu mai apar
            if new_this_page == 0: break
            page += 1
            time.sleep(0.15)  # pauză între pagini (rate-safe pe listare)

    head = "APLICAT — drafturi + rutare escaladare scrise (acțiuni NU)" if a.create_draft else "DRY-RUN — nimic scris"
    print("═" * 92)
    print("  CS AUTO-DRAFT  |  %s  |  %d tichete care așteaptă răspuns" % (head, len(picked)))
    print("═" * 92)
    queue = load_queue()
    rows = []
    n_spam = 0
    hidden_now = 0              # comentarii ascunse efectiv in rularea asta (--auto-hide)
    hide_fail = 0              # ascunderi incercate si esuate (token lipsa / comentariu sters)
    skipped_market = 0          # tichete sărite fiindcă brandul e pe o piață exclusă (AI_SKIP_STORES)
    sarite_public = 0           # comentarii publice neatinse (fără --comments)
    moderate_public = 0         # comentarii publice intrate DOAR pentru moderare (--auto-hide fără --comments)

    for i, t in enumerate(picked, 1):
        if t.get("_stub"):   # --only: ia tichetul ACUM (incremental), nu upfront → fără burst de citiri
            cv0 = mcp.call("get_conversation", {"conversation_number": str(t.get("conversation_no")), "mode": "compact"})
            tk = cv0.get("ticket") if isinstance(cv0, dict) else None
            if not tk:
                print("  [%d/%d] #%s negăsit (sărit)." % (i, len(picked), t.get("conversation_no"))); continue
            t = tk
        no = t.get("conversation_no"); cid = t.get("id")
        channel = (t.get("channel") or "").lower()
        plat_label, plat_rule = PLATFORM.get(channel, (channel or "necunoscut", "ton prietenos, la obiect."))
        is_public = channel in ("facebook_feed_comment", "instagram_comment")
        mod_public = draftam_public(a.comments, a.no_comments, a.auto_hide) if is_public else "draft"
        if mod_public == "sarit":   # canal public fără --comments → rămâne pt CS (implicit = sigur)
            sarite_public += 1
            continue
        if mod_public == "doar-moderare":
            moderate_public += 1
        cur_tags = list(t.get("tag_names") or [])   # pt --skip-tagged
        cust = t.get("customer") or {}
        name = cust.get("name") or ""
        email = (cust.get("email") or (t.get("from") or {}).get("email") or "").lower()
        raw_phone = str(cust.get("phone") or "")
        phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""
        subj = t.get("subject") or ""; first = t.get("first_message") or ""
        blob = subj + " " + first

        last_cust = " ".join(first.split())[:400]   # mesajul CURENT al clientului (la care răspundem); implicit = primul
        cust_msgs = []   # DOAR replicile clientului din fir (fără agenți/AI) — vezi `cust_txt` mai jos
        photo_blk = ""   # conținutul descris al pozelor trimise de client (umplut mai jos dacă există atașamente + --photos)
        if (t.get("comment_count") or 1) <= 1 and is_public:
            tr = "- [CLIENT] " + last_cust
        else:
            cv = mcp.call("get_conversation", {"conversation_number": str(no), "mode": "audit", "max_messages": 20})
            cur_tags = (cv.get("ticket") or {}).get("tag_names") or cur_tags   # tag_names reale (lista summary nu le are)
            msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []
            lines = []
            for m in msgs[-12:]:
                if m.get("is_private"): continue
                txt = " ".join((m.get("text") or "").split())
                if not txt: continue
                is_client = not m.get("is_ai") and not m.get("author_is_workspace_agent")
                who = "[AI]" if m.get("is_ai") else ("[AGENT]" if m.get("author_is_workspace_agent") else "[CLIENT]")
                lines.append("- %s %s" % (who, txt[:400]))
                if is_client:
                    last_cust = txt[:400]   # reține ULTIMUL mesaj al clientului din fir
                    cust_msgs.append(last_cust)
            tr = "\n".join(lines) or ("- [CLIENT] " + last_cust)
            if a.photos:   # VEDE pozele clientului prin cs-photo (modul canonic) → conținutul intră în context; fallback local
                try:
                    photo_blk = _csp.client_photos_block(msgs, subj + " " + first) if _csp else describe_photos(msgs, subj + " " + first)
                except Exception as _pe:
                    print("  ⚠️ vedere poze eșuată (#%s): %s" % (no, str(_pe)[:60]), file=sys.stderr)
        # marchează explicit ULTIMUL mesaj al clientului — la EL răspundem; restul firului = doar context
        tr = tr + "\n>>> ULTIMUL MESAJ AL CLIENTULUI (răspunde la ACESTA; restul firului = context): " + last_cust
        if photo_blk:   # pozele văzute → în transcript (le folosesc atât triajul cât și draftul)
            tr = tr + "\n" + photo_blk
        if a.skip_tagged and AI_TAG and AI_TAG in cur_tags:   # deja draftat (cron/reluare) → sări, fără dublu
            print("  [%d/%d] #%s · deja %s → sărit." % (i, len(picked), no, AI_TAG)); continue
        # pe comentarii publice: atașează textul POSTĂRII/reclamei la care comentează clientul (clientul o vede → și noi trebuie),
        # dacă avem token de pagină pt brandul respectiv (altfel '' → fallback la a întreba ce produs)
        _ad_has_catalog = False   # reclama a adus preț/stoc REAL din catalog → exceptat de la anti-halucinare
        if is_public:
            _pg = ((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else "") or ""
            _segs = str(cid).split("_")
            _post_txt = fb_post_text(_segs[1], _pg) if (len(_segs) >= 2 and _pg) else ""
            # poza RECLAMEI pe care comentează clientul — prin cs-photo (og:image, FĂRĂ token de pagină; cache în registru)
            _ad_blk = ""
            if a.photos and _csp:
                try:
                    _ad_blk = _csp.ad_block(t, store_hint=PAGE_STORE.get(_pg, ""))
                except Exception:
                    _ad_blk = ""
            _ad_has_catalog = "PRODUS în CATALOG" in (_ad_blk or "")
            _pre = []
            if _ad_blk:
                _pre.append(_ad_blk)
            if _post_txt:
                _pre.append("POSTAREA/RECLAMA la care comentează clientul (text): " + _post_txt)
            if _pre:   # identifică PRODUSUL din reclamă → răspunde la obiect (nu mai întreba „ce produs")
                tr = "\n".join(_pre) + "\n" + tr

        # TEXTUL CLIENTULUI, curat: subiect + primul mesaj + toate replicile LUI din fir. NU conține
        # blocurile NOASTRE (marcajul „ULTIMUL MESAJ", pozele descrise de cs_photo, reclama/catalogul)
        # și nici replicile agenților. Blocurile alea sunt scrise în ROMÂNĂ, iar detect_lang întoarce
        # „ro" la prima diacritică românească: măsurat pe 6.000 de tichete REALE de pe Bonhaus CZ/PL/BG,
        # marcajul singur ducea 1.172 (19,5%) pe „ro", dintre care 1.086 ar fi căzut corect pe STORE_LANG.
        # Pe mesaje reale în limbile fără magazin în export: hr 81%, sk 54%, hu 35% duse pe „ro".
        cust_txt = blob + ((" " + " ".join(cust_msgs)) if cust_msgs else "")
        # Sentimentul se citește pe textul CLIENTULUI, ca detect_lang și _real_escalation (runda 3).
        # `blob + " " + tr` băga mesajul de 3 ori (blob, linia din fir, marcajul „ULTIMUL MESAJ") ȘI
        # blocurile NOASTRE — iar marcajul e scris cu MAJUSCULE: „>>> ULTIMUL MESAJ AL CLIENTULUI
        # (răspunde la ACESTA…)" are peste 30 de litere mari, adică exact pragul `caps > 20` din
        # sentiment(). Rezultatul: intensitatea ieșea „puternic" pe 99.379 din 99.379 de comentarii
        # publice reconstruite EXACT (100%) — o CONSTANTĂ, nu o măsurătoare. Pe textul clientului:
        # 72,0% slab, 16,6% mediu, 11,4% puternic. ETICHETA nu se schimbă practic (5 din 99.379).
        sent_lab, sent_int = sentiment(cust_txt)
        store_name = PAGE_STORE.get(((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else None) or "") or "magazinul nostru"
        if store_name == "magazinul nostru":   # email: derivă brandul din domeniul adresei magazinului (ex. contact@esteban.ro)
            _to_email = (t.get("to") or {}).get("email") if isinstance(t.get("to"), dict) else ""
            store_name = brand_from_email(_to_email) or store_name

        orders, other = [], []
        elsewhere = "necunoscut (fără email/telefon real — pe comentarii publice nu se poate lega)"
        _gtxt = subj + " " + tr   # caută comanda/AWB în SUBIECT + TOT firul (inclusiv citate), nu doar ultimul mesaj
        if a.ground and (email or phone or ORDER_RE.search(_gtxt) or AWB_RE.search(_gtxt)):
            # GROUNDING self-contained (VPS/cron): comenzi reale din DB metrics + status/AWB din profitability.db (nume, nr-comandă, AWB)
            _onames = ["".join(m.group(0).split()).replace("-", "").upper() for m in ORDER_RE.finditer(_gtxt)]
            _awbs = AWB_RE.findall(_gtxt)
            orders = lookup_orders(email, phone, _onames, _awbs)
            # Pe PUBLIC magazinul vine DOAR din pagină (PAGE_STORE) sau din domeniul cutiei. Dacă s-ar
            # rafina din brandul comenzii găsite, promptul public ar depinde de APARTENENȚA persoanei:
            # limba (STORE_LANG), telefonul (STORE_PHONE), semnătura și poarta de piață (AI_SKIP_STORES)
            # s-ar schimba după cine e omul — adică exact oracolul închis pe blocul de incident, rămas
            # deschis pe altă cale. Cine postează public un număr de comandă nu are voie să afle nimic
            # din răspuns, nici măcar în ce limbă îi răspundem.
            if (not is_public) and (store_name == "magazinul nostru") and orders:
                store_name = orders[0].get("brand") or store_name
            elsewhere = ("grounded — %d comenzi găsite în DB" % len(orders)) if orders else "grounded — nicio comandă găsită în DB"
        elif a.lean:
            elsewhere = "(lean — fără context 360)"
        elif (not a.lean) and (email or phone):
            ci = customer_ident(no)
            orders = ci.get("orders", []) or []
            # vezi nota de mai sus: pe public magazinul NU se ia din comanda persoanei
            if (not is_public) and (store_name == "magazinul nostru") and orders:
                store_name = orders[0].get("brand") or store_name
            other = [c for c in (ci.get("convos") or []) if str(c.get("no")) != str(no)]
            ch_counts = {}
            for c in other:
                lab = CH_LABEL.get(c.get("channel"), c.get("channel") or "?")
                ch_counts[lab] = ch_counts.get(lab, 0) + 1
            elsewhere = ", ".join("%s×%d" % (k, v) for k, v in sorted(ch_counts.items(), key=lambda x: -x[1])) or "doar aici (niciun alt tichet)"

        # Piețele pe care AI-ul nu răspunde (azi: Moldova, Cehia — decizie de owner 15-sep-2026).
        # Poarta stă AICI, nu mai sus: `store_name` se rafinează în TREI trepte — pagina FB, apoi
        # domeniul cutiei, apoi brandul comenzii găsite. Pusă înainte de treapta a treia, ar fi
        # lăsat să treacă tichetele unde brandul se află abia din comandă.
        # Și nu la selecția de canal, fiindcă un tichet CZ poate veni pe orice canal și prin orice
        # alias de cutie (bonhaus.hu aterizează în cutia trynocturna.eu).
        # Pe piețele excluse NU răspundem — dar ASCUNDEM în continuare comentariile negative
        # (cerință de owner: moderarea se face pe TOATE magazinele, răspunsul doar pe unele).
        # Deci nu sărim tichetul: îl ducem prin triaj și prin moderare, și suprimăm doar draftul.
        skip_draft = store_name in AI_SKIP_STORES and not a.include_skipped
        if skip_draft:
            skipped_market += 1
            if not (is_public and a.auto_hide):
                print("  ⏭️  #%s — piață exclusă (%s); sar" % (no, store_name))
                continue
            print("  ⏭️  #%s — piață exclusă (%s); NU draftez, dar trec prin moderare" % (no, store_name))

        od = "\n".join("    • %s (%s): status=%s, curier=%s, AWB=%s, produse=%s" % (
            o.get("o"), o.get("brand", o.get("store", "?")), o.get("deliv", "?"),
            COURIER.get((o.get("courier") or "").lower(), o.get("courier") or "?"),
            o.get("awb", "") or "—", (o.get("skus") or "")[:40]) for o in orders[:6]) or "    (nicio comandă găsită)"
        hist_txt = "\n".join("    • #%s [%s] %s [%s]" % (c.get("no"), CH_LABEL.get(c.get("channel"), c.get("channel") or "?"),
                             " ".join((c.get("subject") or "").split())[:42], (c.get("status") or "")[:6]) for c in other[:8]) or "    (fără alte tichete)"

        # ---- 1) IDENTIFICARE (LLM triaj) ----
        idn = {"problem": "", "category": categorize_hint(blob, channel), "severity": "none", "escalate": False,
               "escalation_reason": "", "suggested_action": "", "action": "none", "order": "", "comment_action": "none",
               "product": "", "spam": False, "confidence": 0.0, "missing": []}
        # Același oracol, pe calea TRIAJULUI: pe public comenzile/istoricul persoanei nu au voie să intre
        # nici în promptul de triaj — ieșirea lui (problemă/produs/categorie) ajunge direct în draftul
        # PUBLIC, deci l-ar face să depindă de cine e clientul. Acțiunile pe comentarii sunt oricum
        # oprite (`cat in ACTION_CATS and not is_public`), deci triajul public nu pierde nimic util.
        _id_od, _id_else, _id_n = ("    (comenzi ascunse — canal public)", PUBLIC_PERSON_REDACT, 0) if is_public else (od, elsewhere, len(other))
        ident_user = ("PLATFORMĂ: %s\nMESAJ CLIENT:\n%s\n\nCOMENZILE LUI:\n%s\n\nA MAI SCRIS PE: %s | nr alte tichete: %d\nSENTIMENT euristic: %s/%s\nHINT categorie: %s" % (
            plat_label, tr, _id_od, _id_else, _id_n, sent_lab, sent_int, idn["category"]))
        _hint = idn["category"]
        if a.fast_triage and _hint and _hint not in ("altele", "spam_automat"):
            pass   # EFICIENȚĂ: triaj LLM SĂRIT — categorie regex sigură; gărzile spam + escaladare (deterministe) rămân active mai jos
        else:
            try:
                raw, _ = llm(IDENTIFY_SYS, ident_user, js=True)
                got = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
                idn.update({k: got.get(k, idn.get(k)) for k in idn})
                for k in ("new_address", "new_city", "new_zip", "new_phone", "items"):
                    idn[k] = got.get(k, "")
            except Exception as e:
                print("  ⚠️ triaj LLM eșuat (#%s) → fallback pe regex/euristici: %s" % (no, str(e)[:80]), file=sys.stderr)
        cat = idn.get("category") if (isinstance(idn.get("category"), str) and idn.get("category")) else "altele"

        # ---- EXCLUS din draft: spam/automat (LLM) SAU expeditor non-client (curier/app) SAU notificare judge.me (subiect) ----
        _non_customer = bool(NON_CUSTOMER_SENDER_RE.search(email or ""))
        _judgeme = bool(JUDGEME_NOTIF_RE.search(subj or ""))   # „... left a N star review" = notificare auto (NU replica „Re: ⭐...")
        _saas = bool(SAAS_NOISE_SUBJ_RE.search(subj or ""))     # subiect-template raport/notificare SaaS/social
        _bounce = bool(BOUNCE_RE.search(subj or "")) or bool(BOUNCE_RE.search(first or ""))   # bounce mailer-daemon
        _padded = _padded_noise(first)   # corp majoritar padding invizibil (newsletter/notificare)
        if bool(idn.get("spam")) or cat == "spam_automat" or _non_customer or _judgeme or _saas or _bounce or _padded:
            n_spam += 1
            print("\n" + "─" * 92)
            _tagn = ("EXPEDITOR NON-CLIENT (curier/app)" if _non_customer else
                     "NOTIFICARE judge.me (recenzie)" if _judgeme else
                     "RAPORT/NOTIFICARE SaaS/social" if _saas else
                     "BOUNCE email (mailer-daemon)" if _bounce else
                     "ZGOMOT (padding invizibil)" if _padded else "SPAM/automat")
            print("  [%d/%d] #%s · %s · %s · 🚫 %s → EXCLUS (fără draft)" % (i, len(picked), no, store_name, channel, _tagn))
            print("  motiv: %s" % (("expeditor non-client: %s" % email) if _non_customer else ("notificare recenzie judge.me" if _judgeme else (idn.get("problem") or " ".join((first or subj).split())[:70]))))
            if a.close_spam and cid:
                add_tags(mcp, cid, ["spam"])
                res = mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
                ok = not (isinstance(res, dict) and res.get("_error"))
                print("  🗑️ Închis + tag spam." if ok else "  ⚠️ close: %s" % res)
            else:
                print("  → ar închide+arhiva (rulează cu --close-spam ca să le închizi).")
            rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "spam": True, "draft": ""})
            continue

        # escaladare: identificată de LLM SAU semnale dure
        # GATE determinist anti-SUPRA-escaladare: gpt-4o-mini escaladează aproape orice reclamație/WISMO.
        # Pe categorii OBIȘNUITE escaladăm DOAR cu semnal REAL (ANPC/juridic/furie/CAPS); altfel = rezolvare directă.
        # pe textul CLIENTULUI: blocurile noastre diluau raportul de MAJUSCULE (testul „țipă") și
        # stingeau escaladări reale — 20 pierdute din 6.000, zero câștigate. ANPC/juridic contează
        # doar când îl invocă CLIENTUL, nu când un agent a scris „reclamație" în fir.
        _real_esc = _real_escalation(cust_txt)
        # Odată curățat textul, testul de MAJUSCULE din _real_escalation nu mai e diluat — și aprinde
        # și pe clienți MULȚUMIȚI care scriu cu caps-lock („FOARTE BUN, RECOMAND CU ÎNCREDERE”):
        # 42 fals-pozitive la 40.000 de tichete reale. Furia/ANPC rămân escaladare oricum.
        if _real_esc and sent_lab == "pozitiv" and not (ESCAL.search(deacc(cust_txt)) or ANGER_RE.search(deacc(cust_txt))):
            _real_esc = False
        _llm_esc = bool(idn.get("escalate")) or str(idn.get("severity")).upper() in ("HIGH", "URGENT")
        _ord_cat = cat in ("problema_produs", "livrare_wismo", "retur", "schimb_swap", "plata_factura",
                           "modificare_comanda", "anulare", "comanda_noua", "presale_intrebare", "recenzie_feedback", "altele")
        is_esc = _real_esc or (_llm_esc and not _ord_cat)
        level = "URGENT" if (_real_esc and (ESCAL.search(deacc(cust_txt)) or str(idn.get("severity")).upper() == "URGENT")) else "HIGH"

        # ---- comanda țintă (reconciliată) ----
        target, order_name, ambiguous, why_amb = resolve_target_order(blob + " " + tr, orders)
        target_line = None
        if target:
            target_line = "%s (%s) status=%s AWB=%s" % (target.get("o"), target.get("brand", "?"), target.get("deliv", "?"), target.get("awb") or "—")

        # CERERE DE CALLBACK: semnal DETERMINIST (nu doar prompt) — altfel rămânea doar în draft,
        # iar coada CS nu vedea că tichetul e de SUNAT.
        wants_callback = bool(CALLBACK_RE.search(blob + " " + tr))
        callback_phone = (cust_phones(last_cust) or cust_phones(blob) or [""])[0]

        action_note = ""; proposal_line = ""; cmd = None; action_desc = ""

        if is_esc:
            proposal_line = "⛳ ESCALADARE %s: %s → HIGH + tag escaladare%s + notă-brief (preia un coleg)." % (
                level, idn.get("escalation_reason") or idn.get("problem") or cat, "/de-sunat" if phone else "")
        elif cat in ACTION_CATS and not is_public:
            if ambiguous or not order_name:
                proposal_line = "❓ Comandă neclară (%s) → NICIO acțiune; draftul cere clientului să confirme comanda." % why_amb
            else:
                act = idn.get("action") or "none"
                if act not in ("none", None) and act not in ALLOWED:
                    proposal_line = "🔕 Acțiune '%s' dezactivată (--actions) → doar draft." % act; act = "none"
                st = fulfillment_state(target)
                store_pfx = store_prefix(order_name, store_name)
                if act in ("modify", "cancel") and st == "post":
                    proposal_line = "🚫 PRE-FULFILLMENT NU: %s pare deja expediată (AWB) → NU modific/anulez; draftul oferă alternativă curier." % order_name
                elif act != "none" and _f(idn.get("confidence")) >= 0.55 and store_pfx:
                    idn["order"] = order_name
                    cmd = build_action_cmd(idn, store_pfx, order_name)
                    if cmd:
                        _, dry = run_cs_action(cmd, apply=False)
                        proposal_line = "🔧 PROPUNERE %s pe %s (necesită aprobare):\n      cmd: %s\n      %s" % (
                            act.upper(), order_name, " ".join(cmd), " ".join(dry.split())[:280])
                        action_desc = {"modify": "adresa/datele actualizate", "cancel": "comanda anulată",
                                       "swap": "produs schimbat", "resend": "retrimitere gratuită inițiată"}.get(act, act)
                        # NU injectăm ACTIUNE_APLICATA acum (e doar PROPUNERE). Draftul rămâne „am preluat solicitarea";
                        # confirmarea „făcut" se regenerează în do_approve DOAR după aplicare reușită.
                    else:
                        proposal_line = "⚠️ %s — date insuficiente (%s) → draftul cere completarea." % (act, ",".join(idn.get("missing") or []))
                elif act != "none":
                    proposal_line = "ℹ️ Acțiune neclară/insuficientă → doar draft (cere date)."

        # ---- 2.5) Moderare comentariu public FB/IG (corectează replyzen): hide spam/abuz; restul = răspuns PUBLIC scurt (NU trimitem DM — invităm clientul în privat/telefon) ----
        hide_obj = None
        cact = idn.get("comment_action") or "none"
        page_id = ((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else "") or ""
        if is_public and cact == "hide":
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "hide"}
            if a.auto_hide:
                # Moderarea se aplică pe TOATE magazinele, inclusiv pe piețele unde nu răspundem.
                # E reversibilă (`is_hidden=false`) și e exact ce face ReplyZen azi.
                res = fb_hide_comment(cid, page_id, hide=True)
                hidden_now += 1 if res.startswith("✅") else 0
                hide_fail += 0 if res.startswith("✅") else 1
                print("  🙈 HIDE #%s: %s" % (no, res))
            else:
                proposal_line = (proposal_line + "\n  " if proposal_line else "") + "🙈 PROPUNERE HIDE (spam/abuz) — ascunde comentariul; aprobă cu --approve %s sau rulează cu --auto-hide" % no
        elif is_public and public_pii(last_cust):
            # Clientul și-a scris datele (telefon/adresă/nr comandă) sub o postare PUBLICĂ. Ascunderea
            # e reversibilă (`is_hidden=false`) și le scoate de sub ochii tuturor — dar rămâne
            # PROPUNERE: spre deosebire de spam, NU se aplică pe --auto-hide, fiindcă e un comentariu
            # legitim. Modul „hide_pii" NU suprimă draftul — clientul primește oricum răspuns.
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "hide_pii"}
            proposal_line = (proposal_line + "\n  " if proposal_line else "") + "🔒 PROPUNERE HIDE (date personale publice: %s) — ascunde comentariul (reversibil); aprobă cu --approve %s. Draftul se salvează oricum." % (", ".join(public_pii(last_cust)), no)
        if skip_draft:
            continue          # piață exclusă: moderarea s-a făcut, draftul NU

        # ---- 3) DRAFT ----
        sys_prompt = HOLDING if is_esc else SYSTEM
        # pe canale PUBLICE redactăm datele personale STRUCTURAL (nu doar prin prompt) — comentariile rămân publice, fără DM
        od_ctx = od if not is_public else "(comenzi ascunse — canal public, NU expune date personale)"
        # FAPTE VERIFICATE despre un incident cunoscut — pe PRIVAT injectate ATUNCI ȘI DOAR ATUNCI când
        # tichetul se leagă de o comandă afectată (comandă din DB sau nr. de comandă scris de client).
        # Pe PUBLIC nu se ating comenzile deloc: recunoașterea ține de MAGAZINUL PAGINII + incident activ,
        # deci blocul e identic pentru oricine comentează (fără oracol „comanda mea e printre cele lovite?").
        # Pe PUBLIC magazinul incidentului vine din PAGINA pe care s-a comentat (același pt oricine
        # comentează acolo), NU din `store_name`: ăla se rafinează și din comenzile PERSOANEI, ceea ce
        # ar reintroduce exact oracolul pe ușa din dos, pe paginile care nu sunt în PAGE_STORE.
        inc_store = (PAGE_STORE.get(page_id) or "") if is_public else store_name
        _inc_txt = blob + " " + tr
        inc_blk = incident_facts([o.get("o") for o in orders] + [m.group(0) for m in ORDER_RE.finditer(_gtxt)], is_public,
                                 store_name=inc_store, text=_inc_txt)
        if inc_blk:
            od_ctx = od_ctx + "\n\n" + inc_blk
        # canal public = fără date de comandă, DAR cu semnalele incidentului (ne-identificabile) —
        # altfel la o reclamație factuală reală modelul n-are nimic și amână generic
        # aceeași grijă pt sursa de SEMNALE: pe public, magazin din pagină + text fără nr. comandă/PII,
        # ca o sursă pluggable să nu poată potrivi pe identitatea persoanei (ar fi tot un oracol).
        od_ctx += incident_block(incident_signals(
            inc_store if is_public else store_name,
            cat,
            public_safe_text(_inc_txt) if is_public else _inc_txt), is_public)
        phone_ctx = (phone or "—") if not is_public else "—"
        email_ctx = (email or "—") if not is_public else "—"
        # Pe PUBLIC nici NUMELE nu intră în prompt: cu el în context draftul se adresează pe nume,
        # adică exact confirmarea „persoana care scrie e clientul nostru" pe care blocul public o
        # interzice. Telefonul și emailul erau deja ascunse; numele trecea NEredactat.
        name_ctx = (name or "?") if not is_public else "(client — canal public, fără nume)"
        learned = LEARNED.get(cat, "")
        learned_blk = ("\nPROCEDURA INVATATA + VOCEA AGENTILOR REALI pt '%s' (urmeaza procedura; imita tonul/structura replicilor; NU copia datele din exemple):\n%s\n" % (cat, learned[:1800])) if learned else ""
        # limba = ce a scris CLIENTUL (detecție pe text) → apoi piața brandului → apoi LLM → ro
        lang = detect_lang(cust_txt) or STORE_LANG.get(store_name) or idn.get("language") or "ro"
        # Registrul politicos DIFERĂ pe limbi (pl/hu nu-s la plural), iar emoji-ul n-are ce căuta sub un
        # comentariu furios — ambele se decid AICI, determinist, nu din regula generică a promptului.
        emoji_ok = emoji_allowed(sent_lab, is_esc, blob + " " + tr)
        claimed_ch = complained_channels(blob + " " + tr)
        learned_blk += "\nREGISTRU (%s): %s\n%s\nEMOJI: %s" % (
            lang, register_rule(lang), NO_CALQUE,
            "permise (maximum 1-2, potrivite)" if emoji_ok else "INTERZIS — niciun emoji (client nemulțumit/acuzație/escaladare)")
        if claimed_ch:
            learned_blk += ("\nCANAL RECLAMAT de client ca NEFUNCȚIONAL: %s → NU-l re-oferi în răspuns; "
                            "recunoaște problema și dă ALTĂ cale sau spune că revenim NOI." % ", ".join(sorted(claimed_ch)))
        # telefon de comandă (recomandă sunatul la comenzi/presale, dacă avem numărul brandului)
        phone_order = STORE_PHONE.get(store_name, "")
        # numărul e util la comenzi/presale ȘI la comentarii publice (invităm clientul să sune)
        wants_contact = is_public or cat in ("comanda_noua", "presale_intrebare")
        # Pe piețele fără linie locală (Duppo BG/HU/SK, Bonhaus PL) NU există număr de dat: spunem
        # explicit modelului să nu promită telefon, altfel completează singur placeholderul din stil.
        tel_blk = tel_block(store_name, phone_order, wants_contact, wants_callback)
        # ramura INVERSĂ: el cere să-l sunăm NOI → confirmăm callback-ul, NU-i dăm un număr la care să sune
        cb_blk = ("\nCERERE_CALLBACK: DA — clientul cere să fie SUNAT de noi%s. Numărul e deja notat intern (tag de-sunat + notă pentru CS): confirmă că îl SUNĂM NOI cât mai curând, NU-i cere să sune el, NU-i da niciun număr de telefon și NU-i repeta numărul lui în răspuns." % (
            " și a lăsat un număr" if callback_phone else ", dar NU a lăsat niciun număr — cere-i-l politicos")) if wants_callback else ""
        # linkul magazinului: „comandați de pe site" fără domeniu e un răspuns pe care clientul nu-l poate folosi
        site_blk = ("\nSITE: https://%s — dacă trimiți clientul pe site, scrie EXACT acest link (NU inventa alt domeniu)." % STORE_URL[store_name]) if (wants_contact and store_name in STORE_URL) else ""
        # „A MAI SCRIS PE" / „ALTE TICHETE" descriu PERSOANA → pe public același text pentru oricine.
        elsewhere_ctx, hist_ctx = person_ctx(is_public, elsewhere, hist_txt)
        ctx = ("PLATFORMĂ: %s — STIL: %s\nMAGAZIN/BRAND: %s\nCLIENT: %s | email=%s | tel=%s\n"
               "PROBLEMA IDENTIFICATĂ: %s\nPRODUS: %s\nCATEGORIE: %s | LIMBA: %s | SENTIMENT: %s/%s%s\n%s\n\n"
               "CONVERSAȚIA:\n%s\n\nCOMENZILE CLIENTULUI:\n%s\n\nA MAI SCRIS PE: %s\nALTE TICHETE:\n%s\n%s\n"
               "SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL în conversația de mai sus (orientativ: limba≈%s; ro/bg/hu/sk/pl/cz/hr/en). Brandurile pe piețe străine (Bonhaus și Duppo pe BG/HU/SK/PL/CZ/HR, Nocturna BG/PL) răspund de regulă în limba pieței, DAR dacă clientul a scris clar în altă limbă (ex. engleză), răspunde în limba LUI. Exemplele de procedură/voce pot fi în română — folosește-le DOAR pentru pași+ton, NU pentru limbă. Răspunsul depinde de brand+produs. Scrie DOAR textul răspunsului, respectând stilul platformei." % (
                   plat_label, plat_rule, store_name, name_ctx, email_ctx, phone_ctx,
                   idn.get("problem") or "(neclar)", idn.get("product") or "—", cat, lang, sent_lab, sent_int,
                   "  [ESCALAT — doar mesaj de așteptare]" if is_esc else "",
                   (("\n" + action_note) if action_note else "") + tel_blk + site_blk + cb_blk, tr, od_ctx, elsewhere_ctx, hist_ctx, learned_blk, lang))
        try:
            draft, engine = llm(sys_prompt, ctx)
        except Exception as e:
            draft, engine = "(eroare LLM: %s)" % e, "—"

        # GARDĂ DURĂ (incident): niciun AWB / nicio dată în draft care să nu vină din registru sau din context.
        if inc_blk and not draft.startswith("(eroare"):
            bad = unbacked_claims(draft, ctx)
            if bad:
                corr = ctx + ("\n\n⛔ Răspunsul tău anterior a afirmat date/AWB/cifre care NU sunt în FAPTELE VERIFICATE (%s). "
                              "Rescrie folosind DOAR faptele din context: nicio altă dată, niciun alt AWB, nicio altă livrare. Scrie DOAR răspunsul." % ", ".join(bad[:4]))
                try:
                    d2, _ = llm(sys_prompt, corr)
                    if d2 and not unbacked_claims(d2, ctx):
                        draft, engine = d2.strip(), engine + "+incident-corectat"
                except Exception:
                    pass
                if unbacked_claims(draft, ctx):   # tot fabrică → mai bine NICIUN draft decât unul care minte
                    draft = "(eroare: draft SUPRIMAT — afirmă date/AWB în afara registrului de incidente: %s)" % ", ".join(bad[:4])
                    engine = "suprimat"

        # POST-FILTRU ANTI-HALUCINARE: draftul afirmă lookup/status/termen/preț/telefon pe care NU le avem → corectează.
        # Se aplică ȘI pe ESCALADĂRI (cazul cel mai sensibil, nu cel mai puțin) ȘI pe tichetele cu catalog: prețul REAL
        # din catalog trece pentru că hallu_hits îl caută în CONTEXT — nu se mai sare filtrul tocmai unde apar prețuri.
        _hits = [] if draft.startswith("(eroare") else hallu_hits(draft, ctx, inc_blk)
        if _hits and inc_blk:   # exceptarea ține de CIFRELE din registru, nu de tot tichetul
            _hits = [h for h in _hits if not incident_backed(h, inc_blk)]
        if _hits and not has_order_data(od_ctx) and not photo_blk:
            corr = ctx + ("\n\n⛔ Răspunsul tău anterior CONȚINEA INFORMAȚIE INVENTATĂ (%s) — lookup/„am verificat/nu am găsit”, status comandă, termen de livrare, preț sau telefon pe care NU le ai în context. " % "; ".join(_hits[:4]) +
                          "Rescrie complet, FĂRĂ să inventezi NIMIC și FĂRĂ să spui că ai căutat/găsit/verificat ceva. "
                          "Cere clientului numărul comenzii SAU un număr de telefon (pt orice ține de o comandă), sau, la întrebări de produs, spune onest că revii cu detaliile exacte / poate verifica pe site. Scrie DOAR răspunsul.")
            try:
                d2, _ = llm(sys_prompt, corr)   # pe escaladare rămâne promptul de AȘTEPTARE, nu SYSTEM
                if d2 and not hallu_hits(d2, ctx, inc_blk):
                    draft, engine = d2.strip(), engine + "+corectat"
            except Exception:
                pass
            if hallu_hits(draft, ctx, inc_blk):   # tot fabrică → șablon SIGUR, onest (pe categorie ȘI pe limba tichetului)
                draft = safe_template(lang, cat, is_public)
                engine = "șablon-sigur"

        # GARDĂ DE CONFIDENȚIALITATE (structurală, nu doar prin prompt): pe canal PUBLIC draftul nu are
        # voie să repete datele scrise de client în comentariu — ele ajung în prompt prin transcript,
        # iar redactarea de mai sus acoperă doar datele din sistemul NOSTRU. Singurul număr permis
        # public e TELEFON_COMANDĂ (al nostru).
        if is_public and public_pii_leaks(draft, phone_order, name):
            try:
                d2, _ = llm(sys_prompt, ctx + "\n\n⛔ Răspunsul tău anterior REPETA PUBLIC date personale ale clientului (nume, adresă, e-mail, telefon, număr de comandă sau AWB). Rescrie-l FĂRĂ nicio astfel de dată și FĂRĂ să i te adresezi pe nume — confirmă neutru că am preluat solicitarea. Scrie DOAR răspunsul.")
                if d2 and not public_pii_leaks(d2, phone_order, name):
                    draft, engine = d2.strip(), engine + "+fără-date-publice"
            except Exception:
                pass

        print("\n" + "─" * 92)
        cmoji = {"hide": "🙈", "hide_pii": "🔒"}.get((hide_obj or {}).get("mode"), "") + ("📞" if wants_callback else "")
        # GĂRZI POST-GENERARE (deterministe, pe AMBELE prompturi — HOLDING nu trecea prin niciun filtru)
        # Verificatorul de conținut, rulat ȘI pe drafturile REGENERATE din apply_post_guards: gărzile
        # de mai sus judecaseră doar draftul INIȚIAL, iar o regenerare (canal reclamat / registru)
        # intra în coadă nevăzută de anti-halucinare, de registrul de incidente și de garda publică.
        _verif_regen = (lambda d, _c=ctx, _i=inc_blk, _ho=(has_order_data(od_ctx) or bool(photo_blk)),
                        _pub=is_public, _ph=phone_order, _nm=name:
                        guard_reasons(d, _c, _i, _ho, _pub, _ph, _nm))
        draft, _guard = apply_post_guards(
            draft, emoji_ok, claimed_ch,
            regen=lambda chs: llm(sys_prompt, ctx + (
                "\n\n⛔ Răspunsul tău anterior îl trimitea pe client ÎNAPOI pe canalul pe care tocmai l-a reclamat ca "
                "nefuncțional (%s). Rescrie: recunoaște problema și oferă ALTĂ cale (revenim NOI), fără să re-oferi acel "
                "canal. Scrie DOAR răspunsul." % ", ".join(chs)))[0],
            lang=lang,
            regen_reg=lambda hits: llm(sys_prompt, ctx + REGEN_REGISTRU % (", ".join(hits), register_rule(lang)))[0],
            verif=_verif_regen)
        engine += _guard
        # Garda STRUCTURALĂ de date personale se aplică pe textul FINAL: apply_post_guards poate cere un
        # draft NOU de la LLM (canal reclamat), iar acela ocolea complet garda de mai sus.
        if is_public:
            draft, _nred = redact_public_pii(draft, phone_order, name)
            if _nred:
                engine += "+redactat"
                print("  🔒 Date personale scoase din draftul PUBLIC (%d)." % _nred)
            # PLASĂ: dacă tot a rămas o clasă TARE (email / adresă / numele clientului), draftul NU
            # se salvează deloc. Marcajul „(eroare" e cel pe care garda de salvare îl sare deja.
            _rest = public_pii_rest(draft, name)
            if _rest:
                draft = "(eroare: draft SUPRIMAT — pe canal public a rămas %s după redactare)" % ", ".join(_rest)
                engine = "suprimat-public"
                print("  ⛔ %s" % draft)
        flag = ("⛳%s " % level if is_esc else "") + ("🔧" if cmd else "") + cmoji
        print("  [%d/%d] #%s · %s · %s · %s · sent=%s/%s %s" % (i, len(picked), no, store_name, plat_label, cat, sent_lab, sent_int, flag))
        print("  client: %s | comenzi: %d | a mai scris: %s" % (name or "?", len(orders), elsewhere))
        if photo_blk:
            print("  📷 %d poză(e) văzută(e) → folosite în draft" % photo_blk.count("\n  ["))
        print("  problemă: %s" % (idn.get("problem") or " ".join((first or subj).split())[:80]))
        if proposal_line:
            for ln in proposal_line.splitlines(): print("  " + ln)
        print("  ┌─ DRAFT%s (%s) " % (" AȘTEPTARE" if is_esc else "", engine) + "─" * 12)
        for ln in draft.strip().splitlines(): print("  │ " + ln)
        print("  └" + "─" * 42)

        queue[str(no)] = {"cid": cid, "draft": draft.strip(), "cmd": cmd, "hide": hide_obj,
                          "ctx": ctx, "action_desc": action_desc, "order": order_name,
                          "store": store_name, "cat": cat, "escalate": is_esc, "callback": wants_callback,
                          # calea de APROBARE regenerează draftul: fără astea n-ar putea ști că e canal
                          # public (deci ce are voie să scrie) și pe ce context să verifice halucinările.
                          "is_public": is_public, "phone_order": phone_order, "cust_name": name,
                          "has_orders": has_order_data(od_ctx)}
        rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "escalate": is_esc,
                     "language": lang, "cust_msg": (last_cust or first or subj or "")[:240],
                     "subject": subj[:120], "orders": (od[:280] if not is_public else ""),   # context grounding (audit: distinge comandă reală de inventată)
                     "comment_action": (hide_obj or {}).get("mode"), "callback": wants_callback, "draft": draft.strip()})
        if cmd or hide_obj:
            print("  → aprobă:  uv run cs_auto_draft.py --approve %s%s" % (no, " --agent <Nume>" if cmd else ""))

        # ---- scrieri în Richpanel (doar cu --create-draft) ----
        if a.create_draft and cid:
            # rutarea escaladării (priority/tag/notă) se face DOAR în modul complet; lean = doar draftul
            if is_esc and not a.lean:
                tags = ([AI_TAG] if AI_TAG else []) + ["escaladare", "esc-%s" % level.lower()] + (["de-sunat"] if phone else [])
                note = escalation_note(level, idn.get("escalation_reason") or "", idn.get("problem") or "", name, phone, email,
                                       target_line, elsewhere, "%s/%s" % (sent_lab, sent_int), idn.get("suggested_action"))
                mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
                add_tags(mcp, cid, tags)
                mcp.call("add_private_note", {"conversation_id": cid, "body": note})
                print("  ⛳ Rutat: HIGH + %s + notă-brief." % ("+".join(tags)))
            # CALLBACK: tichetul trebuie să se vadă în coada CS ca fiind de SUNAT. Nota e PRIVATĂ
            # (internă) — acolo numărul poate sta. Pe escaladare tag-ul „de-sunat" se pune deja mai sus.
            if wants_callback and not is_esc and not a.lean:
                add_tags(mcp, cid, [t for t in (AI_TAG, "de-sunat") if t])
                mcp.call("add_private_note", {"conversation_id": cid, "body": callback_note(
                    callback_phone or phone, name, idn.get("problem") or "", store_name, plat_label, target_line)})
                print("  📞 Rutat: tag de-sunat + notă cu numărul (clientul cere să-l sunăm NOI).")
            # la hide NU salvăm draft — comentariul se ascunde la --approve
            if (hide_obj or {}).get("mode") == "hide":
                print("  (hide → fără draft; se ascunde la --approve)")
            elif draft.strip().startswith("(eroare") or "(eroare LLM" in draft or len(draft.strip()) < 5:
                # GARDĂ: nu salva gunoi în Richpanel (LLM picat 429/5xx / draft gol) — sare tichetul (se reia next run)
                print("  ⛔ draft invalid (eroare LLM / gol) → NU salvez (sar tichetul).")
            elif mod_public == "doar-moderare":
                print("  🔇 comentariu PUBLIC: fără draft (lipsește --comments); moderarea a rulat normal.")
            elif a.apply_send and trimitere_permisa() and not is_esc and not is_public:
                # LIVE: trimite răspunsul la client + închide tichetul. NUMAI ne-escaladat + ne-comentariu
                # (escaladările + comentariile rămân DRAFT, le ia un om). Garda de mai sus a exclus deja draft invalid.
                res = mcp.call("send_message", {"conversation_id": cid, "body": draft.strip()})
                ok = not (isinstance(res, dict) and res.get("_error"))
                if ok:
                    add_tags(mcp, cid, [t for t in (AI_TAG, "ai-sent") if t])
                    mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
                    print("  📤 TRIMIS LIVE la client + tichet ÎNCHIS.")
                else:
                    print("  ⚠️ send_message EȘUAT → NU închid, NU marchez: %s" % res)
            else:
                res = mcp.call("create_draft", {"conversation_id": cid, "body": draft.strip()})
                ok = not (isinstance(res, dict) and res.get("_error"))
                if ok and AI_TAG:   # tag DOAR dacă a fost cerut (--tag ""  → fără tag)
                    add_tags(mcp, cid, [AI_TAG])
                print(("  ✅ DRAFT salvat%s (NU trimis)." % (" + tag %s" % AI_TAG if AI_TAG else "")) if ok else "  ⚠️ create_draft: %s" % res)
        time.sleep(a.sleep)

    save_queue(queue)
    if hidden_now or hide_fail:
        print("\n  🙈 Moderare: %d comentarii ascunse%s." % (
            hidden_now, (", %d eșuate (token de pagină lipsă sau comentariu șters)" % hide_fail) if hide_fail else ""))
    if sarite_public or moderate_public:
        print("\n  🔇 Canal PUBLIC (comentarii FB/IG): %d sărite, %d doar moderate. --comments ca să răspunzi public."
              % (sarite_public, moderate_public))
    if skipped_market:
        print("\n  ⏭️  Piețe excluse (%s): %d tichete sărite. --include-skipped ca să răspunzi și acolo."
              % (", ".join(sorted(AI_SKIP_STORES)), skipped_market))
    print("\n  🚫 Spam/automat: %d %s." % (n_spam, "închise (CLOSED+tag spam)" if a.close_spam else "excluse din draft (rulează --close-spam ca să le închizi)"))
    if a.json:
        print("@@JSON@@" + json.dumps(rows, ensure_ascii=False))
    if not a.create_draft:
        print("\n  → DRY-RUN. --create-draft: salvează drafturile + rutează escaladările (URGENT/tag/notă). Acțiunile rămân propuneri (--approve).")


if __name__ == "__main__":
    main()
