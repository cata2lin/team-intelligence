#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pii_guard — refuza commitul cand in continutul ADAUGAT apar date reale de client.

Repo-ul team-intelligence e PUBLIC. De trei ori la rand (PR #591, PR #592, commit
a1ca5cc) au intrat in el numere de comanda, AWB-uri, telefoane si emailuri de clienti
reali. Disciplina n-a tinut; asta e verificarea mecanica.

Ce refuza, DOAR pe liniile ADAUGATE (`+` din diff-ul staged):
  - numar de comanda: <PREFIX magazin><4-7 cifre>        (EST123456)
  - AWB: 10-13 cifre, dar NUMAI pe o linie care vorbeste despre expediere
         (awb/tracking/colet/curier/expedi/livr) sau care are si un nr de comanda
  - telefon RO/MD/BG/CZ de client: 07xxxxxxxx / +407xxxxxxxx / 00407xxxxxxxx
  - email personal: orice adresa care NU e pe un domeniu al firmei, al unui furnizor
         cunoscut sau pe un domeniu rezervat documentatiei

Ce NU refuza (documentatia TREBUIE sa poata vorbi despre FORMATUL unui identificator):
  - serii evident fictive: cifre care incep cu 0 (EST000001), serii 123/1234/12345…
  - AWB numai din zerouri + contor (00000000001), sau 1234567890
  - telefoane pe prefixul 070 — NEALOCAT in Romania, deci nu poate fi al nimanui
  - placeholdere: <nr>, NNN, xxx, {order}, $ORDER, %s, …
  - emailuri pe example.com/org/net, test., localhost, invalid (RFC 2606/6761)
  - id-uri care NU sunt AWB: `customers/1234567890` (Google Ads), gid://shopify/…,
    id de pagina Facebook (14-16 cifre), timestamp/epoch, hash-uri
  - orice linie marcata explicit cu `pii-ok: <motiv>`

Utilizare:
  pii_guard.py --staged            # hook de git pre-commit (implicit)
  pii_guard.py --paths F1 F2 …     # verifica fisiere intregi
  pii_guard.py --hook              # hook PreToolUse (Claude Code): citeste JSON pe stdin
"""
import json
import os
import pathlib
import re
import subprocess
import sys

# --- prefixele de magazin, tinute in sincron cu ORDER_PFX din cs_auto_draft.py -------
PREFIXE = ["EST", "GT", "NUB", "NUBRA", "GEN", "GRAND", "GRAN", "BELA", "MAG", "OFER",
           "RED", "BON", "BONBG", "CARP", "COV", "APR", "ROSSI", "DUPBG", "NOC", "LAB"]

ORDER_RE = re.compile(r"\b(" + "|".join(sorted(PREFIXE, key=len, reverse=True)) + r")[ -]?(\d{4,7})\b")
CIFRE_RE = re.compile(r"(?<![\d.\-/])(\d{10,13})(?![\d.\-/])")
TEL_RE = re.compile(r"(?<![\d+])(?:\+?40|0040)?[ .\-]?0?7\d{2}[ .\-]?\d{3}[ .\-]?\d{3}(?![\d])")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# linia vorbeste despre o expediere -> abia atunci un sir de 10-13 cifre e un AWB
CTX_AWB = re.compile(r"awb|tracking|track|colet|curier|expedi|livr|shipment|parcel|dpd|sameday|fan ?courier|cargus|econt|packeta", re.I)
# contexte in care un sir lung de cifre e ALTCEVA (id de cont/pagina/resursa)
CTX_ALT_ID = re.compile(
    r"customers?/|customer_?id|gid://|account_?id|page_?id|site_?id|siteId|ad_?id|campaign_?id"
    r"|merchant|timestamp|epoch|millis|nanos|_ms\b|sha|hash|checksum|siruta|cod ?postal|postal_?code"
    r"|--cid\b|\bcid\b|--customer\b|mcc|ga4|property|measurement"
    r"|\b[KMGT]B\b|bytes|octe|\bsize\b|marime|limit|offset|port\b", re.I)

# valori evident FICTIVE — documentatia are voie sa arate formatul
SECVENTA = "1234567890"   # o serie care e prefix din asta e evident un placeholder
PLACEHOLDER = re.compile(r"[<>{}]|\bN{2,}\b|\bx{3,}\b|\$[A-Z_]|%[sd]\b|\.\.\.|…", re.I)
DOMENII_OK = re.compile(
    r"@(example\.(com|org|net)|test(\.[a-z]+)?|invalid|localhost|"
    r"arona\.ro|aronagroup\.ro|overheat\.agency|"                        # firma
    r"[a-z0-9-]*\.?(dpd|sameday|econt|packeta|cargus|fancourier|gls-group)\.[a-z.]+|"  # curieri
    r"(users\.)?noreply\.?[a-z.]*|noreply@?[a-z.]*|"
    r"esteban\.ro|grandia\.ro|magdeal\.ro|nocturna\.ro|bonhaus\.[a-z]+|nubra\.ro|georgetalent\.ro|"
    r"s\.whatsapp\.net|g\.us|myshopify\.com|shopify\.com|anthropic\.com|github\.com|"
    r"casaofertelor\.ro|ofertelezilei\.ro|nocturna\.[a-z]+|trynocturna\.[a-z]+|belasil\.ro|"
    r"gento\.ro|carpetto\.ro|covoria\.ro|apreciat\.ro|reduceribune\.ro|oriceredus\.ro|"
    r"labnoir\.ro|artevita\.ro|duppo\.[a-z]+|rossinails\.ro)$", re.I)
LOCAL_OK = re.compile(r"^(client|customer|user|nume|prenume|test|demo|exemplu|example|email|"
                      r"contact|office|info|support|hello|no-?reply|admin|facturi|comenzi|"
                      r"cz\d+|ro\d+|bg\d+|prenume\.?\.?nume)\d*$", re.I)

SCUTIRE = re.compile(r"pii-ok\s*:", re.I)

# cai in care nu are rost sa cautam (vendor, binar, date publice de nomenclator)
CAI_SARITE = re.compile(
    r"(^|/)(node_modules|\.venv|venv|dist|build|__pycache__|\.rembg|canvas-fonts|"
    r"site-packages|schemas)/|"
    r"\.(png|jpe?g|gif|svg|ico|pdf|zip|gz|xlsx?|woff2?|ttf|mp4|webp|db|sqlite|lock|dylib|so)$|"
    r"(^|/)(uv\.lock|package-lock\.json|dpd_nomenclator\.json)$", re.I)


def e_fictiv_order(serie):
    # serie cu 0 in fata (conventia de redactare) sau prefix din "1234567890"
    return serie.startswith("0") or SECVENTA.startswith(serie)


def e_fictiv_cifre(v):
    # zerouri in fata, sir numai de zerouri, >=6 zerouri la rand (00000000001,
    # 81100000001 — AWB de test), sau o secventa crescatoare 1234567890…
    return (v.startswith("0") or v.lstrip("0") == "" or "000000" in v
            or "123456" in v or "234567" in v)


def e_fictiv_tel(v):
    d = re.sub(r"\D", "", v)
    if d.startswith("0040"):
        d = d[4:]
    elif d.startswith("40"):
        d = d[2:]
    d = d.lstrip("0")
    # 70… = prefixul 070, NEALOCAT in Romania (mobilele sunt 072-079) -> nu e al nimanui;
    # cifre identice; sau o secventa crescatoare (0748 123 456, 0712345678) = placeholder
    return (d.startswith("70") or len(set(d)) <= 1
            or "123456" in d or "234567" in d or "345678" in d
            or not (9 <= len(d) <= 10))


def verifica_linie(linie, cale):
    """Intoarce lista de (tip, motiv) pentru o singura linie ADAUGATA."""
    if SCUTIRE.search(linie):
        return []
    gasiri = []

    for m in ORDER_RE.finditer(linie):
        pfx, serie = m.group(1), m.group(2)
        if e_fictiv_order(serie) or PLACEHOLDER.search(linie[max(0, m.start() - 3):m.end() + 3]):
            continue
        gasiri.append(("numar de comanda", "%s + serie reala (%d cifre)" % (pfx, len(serie))))

    are_comanda = bool(gasiri)
    if CTX_AWB.search(linie) or are_comanda:
        for m in CIFRE_RE.finditer(linie):
            v = m.group(1)
            ctx = linie[max(0, m.start() - 40):m.end() + 20]
            if e_fictiv_cifre(v) or CTX_ALT_ID.search(ctx) or len(v) > 13:
                continue
            gasiri.append(("AWB", "sir de %d cifre pe o linie despre expediere" % len(v)))

    for m in TEL_RE.finditer(linie):
        v = m.group(0).strip(" .-")   # regexul poate inghiti separatorul din fata
        brut = re.sub(r"\D", "", v)
        # fara prefix national explicit, un sir de 9 cifre e la fel de bine un numar de octeti
        if not (v.lstrip("+").startswith(("0", "40")) and (brut.startswith("0") or brut.startswith("40"))):
            continue
        if e_fictiv_tel(v) or CTX_ALT_ID.search(linie[max(0, m.start() - 40):m.end() + 20]):
            continue
        gasiri.append(("telefon", "numar mobil care arata real"))

    for m in EMAIL_RE.finditer(linie):
        e = m.group(0)
        local = e.split("@")[0]
        if DOMENII_OK.search(e) or LOCAL_OK.match(local):
            continue
        gasiri.append(("email personal", "adresa pe un domeniu care nu e al firmei"))

    return gasiri


def diff_staged(repo):
    out = subprocess.run(["git", "-C", repo, "diff", "--cached", "--unified=0", "--no-color"],
                         capture_output=True, text=True).stdout
    cale, adaugate = None, []
    for l in out.split("\n"):
        if l.startswith("+++ b/"):
            cale = l[6:]
        elif l.startswith("+") and not l.startswith("+++") and cale:
            adaugate.append((cale, l[1:]))
    return adaugate


def linii_fisiere(cai):
    # ⚠️ Un DIRECTOR dat ca argument facea `open()` sa arunce IsADirectoryError, prins de `except`
    # si SARIT TACUT — deci `--paths <director>` scana ZERO fisiere si iesea cu 0 = „curat".
    # Masurat 16-sep-2026: verificarea de dinainte de un commit in repo-ul PUBLIC primea doua
    # directoare si n-a citit nicio linie din ele; separat, pe 143 de fisiere dintr-un container
    # a dat „curat" desi contineau 15 numere de comanda reale. O garda care tace e mai rea decat
    # niciuna. Acum: directoarele se parcurg recursiv, iar ce nu se poate citi se RAPORTEAZA.
    out, nereusite = [], []
    fisiere = []
    for c in cai:
        p = pathlib.Path(c)
        if p.is_dir():
            fisiere += [q for q in sorted(p.rglob("*")) if q.is_file()]
        elif p.exists():
            fisiere.append(p)
        else:
            nereusite.append("%s: nu exista" % c)
    for q in fisiere:
        try:
            with open(q, encoding="utf-8", errors="replace") as f:
                for l in f:
                    out.append((str(q), l.rstrip("\n")))
        except (OSError, UnicodeError) as e:
            nereusite.append("%s: %s" % (q, e.__class__.__name__))
    if nereusite:
        sys.stderr.write("⚠️ pii_guard NU a putut citi %d cale/cai — NU sunt verificate:\n  %s\n"
                         % (len(nereusite), "\n  ".join(nereusite[:20])))
    return out


def ruleaza(perechi):
    probleme = []
    for cale, linie in perechi:
        if CAI_SARITE.search(cale):
            continue
        for tip, motiv in verifica_linie(linie, cale):
            probleme.append((cale, tip, motiv, linie.strip()[:60]))
    return probleme


def raporteaza(probleme):
    if not probleme:
        return 0
    sys.stderr.write("\n⛔ pii_guard: repo-ul e PUBLIC — continutul adaugat pare sa aiba DATE REALE de client.\n\n")
    vazute = set()
    for cale, tip, motiv, _frag in probleme:
        k = (cale, tip, motiv)
        if k in vazute:
            continue
        vazute.add(k)
        sys.stderr.write("  %-58s %-18s %s\n" % (cale, tip, motiv))
    sys.stderr.write(
        "\nCe faci:\n"
        "  1. Inlocuieste valoarea cu una evident fictiva, care pastreaza FORMA:\n"
        "     comanda EST000001 · AWB 00000000001 · telefon 0700000000 (070 nu e alocat in RO)\n"
        "     · email client@example.com\n"
        "  2. Daca valoarea NU e a unui client (id de cont Google Ads, id de pagina Facebook,\n"
        "     numar public al firmei), pune pe linia aia comentariul `pii-ok: <motivul>`.\n"
        "  3. NU ocoli cu --no-verify. Daca a ajuns pe origin, stergerea cere GitHub Support.\n\n")
    return 1


def main():
    argv = sys.argv[1:]
    repo = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip() or "."

    if "--hook" in argv:
        # PreToolUse (Claude Code): blocheaza `git commit` daca staged-ul e murdar
        try:
            ev = json.load(sys.stdin)
        except Exception:
            sys.exit(0)
        cmd = (ev.get("tool_input") or {}).get("command", "")
        if not re.search(r"\bgit\b[^|;&]*\bcommit\b", cmd) or "--no-verify" in cmd:
            if "--no-verify" not in cmd:
                sys.exit(0)
        probleme = ruleaza(diff_staged(repo))
        if probleme or "--no-verify" in cmd:
            raporteaza(probleme)
            if "--no-verify" in cmd:
                sys.stderr.write("  (`--no-verify` ocoleste hook-ul de git — refuzat.)\n")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "pii_guard: continut adaugat cu date reale de client intr-un repo PUBLIC."}}))
            sys.exit(0)
        sys.exit(0)

    if "--paths" in argv:
        cai = argv[argv.index("--paths") + 1:]
        sys.exit(raporteaza(ruleaza(linii_fisiere(cai))))

    sys.exit(raporteaza(ruleaza(diff_staged(repo))))


main()
