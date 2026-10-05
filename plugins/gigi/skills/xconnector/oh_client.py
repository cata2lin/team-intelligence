"""Order Hub ÎNTÂI pentru acțiunile CS pe comandă și etichetă: anulare, oprire (hold), refacere de AWB.

DE CE: Order Hub hotărăște pe toate magazinele și face etichetele — direct la curier (`dpd-ro-arona`), unde xConnector
nu le vede, sau prin xConnector (conturile `xconnector-<magazin>`). O anulare cerută prin xConnector pe o comandă cu
eticheta făcută de Order Hub anulează comanda în Shopify și lasă eticheta VIE la curier (cinci comenzi,
29-sep-2026). Aplicația de scanare a fost mutată atunci pe Order Hub; comenzile CS nu.

CUM: aceleași rute de serviciu ca scannerul — POST /api/depozit/anulare și POST /api/depozit/refa — cu cheia de
serviciu a CS-ului (`OH_CS_TOKEN`, forma `ohsvc.<id>.<secret>`, scopul „depozit"). Order Hub anulează eticheta la
cine a emis-o și abia apoi comanda, cu gărzile butoanelor lui.

REGULA (contractul rutelor): anularea, oprirea și refacerea pe calea veche, prin xConnector, sunt permise DOAR când
Order Hub răspunde 404 cu `detail.rezultat == "necunoscuta"`. Orice altceva — hotărâre, refuz, eroare, cheie lipsă —
NU dă voie la o asemenea scriere prin xConnector. Singura excepție e un AWB NOU (awb-make, awb-create, addr-set
--make-awb) pe o comandă pe care Order Hub o cunoaște fără AWB viu (`fara_awb`): se face prin xConnector, fără să i se
elibereze hold-urile. Un 404 fără `necunoscuta` (rută greșită, proxy) e EROARE. Adresa Order Hub e fixă, nu vine din
mediu: un alt server care răspunde „necunoscuta" ar redeschide calea veche pe comenzi reale.

CE SE TRIMITE: `cod` = numărul comenzii, repetat în `comanda` (Order Hub caută întâi după etichetă; `comanda` îl
face să refuze dacă eticheta e a altei comenzi). `awb` = eticheta pe care apelantul o crede a comenzii: dacă Order
Hub n-o cunoaște, refuză (`eticheta_necunoscuta`) în loc să anuleze comanda cu o etichetă pe care n-o vede; la
refacere, o etichetă deja înlocuită e refuzată (`eticheta_anulata`), deci o cerere repetată nu face a treia etichetă.
`dry_run` se trimite mereu explicit: Order Hub tratează lipsa lui ca probă. O probă nu scrie nimic.

CE NU ÎNSEAMNĂ RĂSPUNSUL: `ok: false` nu e „nu s-a întâmplat nimic" (`partial`, `shopify_refuz`: eticheta e deja
anulată la curier); `ok: true` nu e „toți pașii au reușit" (vezi `pasi`); iar un răspuns pierdut după o cerere de
execuție nu e un refuz — cererea poate să fi fost executată (`Raspuns.incert`).
"""
import json
import re
import urllib.error
import urllib.request

BASE = "https://orderhub.arona.ro"
TOKEN_ENV = "OH_CS_TOKEN"
UI_COMENZI = BASE + "/app/orders"

DECIS = "decis"                # Order Hub cunoaște comanda și a răspuns (ok sau refuz) — xConnector nu se atinge
NECUNOSCUTA = "necunoscuta"    # 404 necunoscuta — singurul caz în care e permisă calea xConnector
FARA_CHEIE = "fara_cheie"      # OH_CS_TOKEN lipsește sau n-are forma unei chei de serviciu: nu s-a trimis nimic
EROARE = "eroare"              # orice altceva: 400/401/403/409/5xx, rețea, răspuns necitit

# Ce face agentul mai departe, pe rezultatele unde mesajul Order Hub nu o spune.
URMEAZA = {
    "fara_awb": "Comanda n-are AWB viu de refăcut. AWB-ul îl face Order Hub (singur sau cu „Ship now”).",
    "oprita": "Comanda e pe HOLD și nu pleacă. Se eliberează din Order Hub, care îi face și AWB-ul — nu awb-make.",
    "eticheta_necunoscuta": "Shopify arată pe comandă o etichetă pe care Order Hub n-o cunoaște (încă). Reîncearcă "
                            "peste un minut; dacă rămâne așa, vezi în Order Hub — nu anula nimic din xConnector.",
    "eticheta_anulata": "Eticheta asta s-a refăcut deja: rulează proba ca să vezi AWB-ul curent.",
    "shopify_refuz": "Eticheta e anulată la curier, dar comanda a rămas DESCHISĂ în Shopify. Urmărește tichetul CS "
                     "din Order Hub; nu o anula și din altă parte.",
    "partial": "Acțiune incompletă: vezi în Order Hub ce a rămas viu.",
    "eroare": "Stare necunoscută: vezi comanda în Order Hub înainte de orice altă încercare.",
}

_FORMA_CHEIE = re.compile(r"ohsvc\.[a-z0-9][a-z0-9_-]{1,31}\.[\x21-\x7e]{32,}")


class Raspuns:
    def __init__(self, stare, http=None, corp=None, mesaj="", incert=False):
        corp = corp if isinstance(corp, dict) else {}
        self.stare, self.http, self.corp = stare, http, corp
        self.ok = bool(corp.get("ok")) if stare == DECIS else False
        self.rezultat = str(corp.get("rezultat") or "")
        self.mesaj = str(mesaj or corp.get("mesaj") or "")
        self.plan = [str(x) for x in (corp.get("plan") or [])]
        self.pasi = [p for p in (corp.get("pasi") or []) if isinstance(p, dict)]
        self.proba = self.rezultat == "previzualizare"
        self.magazin = str(corp.get("magazin") or "")
        self.incert = incert      # cererea de EXECUȚIE a plecat, răspunsul nu a venit: poate să fi fost executată


class _FaraRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):     # un redirect ar duce cheia (antetul Authorization) la altă adresă
        return None


def _http(method, url, headers, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_FaraRedirect).open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e2:      # antetele au venit, corpul nu: codul HTTP rămâne valabil
            return e.code, "corp necitit (%s)" % type(e2).__name__
    except Exception as e:
        return "ERR", "%s: %s" % (type(e).__name__, str(e)[:120])


def _cere(cale, corp, token, http=None):
    token = (token or "").strip()
    if not token:
        return Raspuns(FARA_CHEIE, mesaj="%s nu e setat (nici în env, nici în KB)" % TOKEN_ENV)
    if not _FORMA_CHEIE.fullmatch(token):    # fără s-o tipărim: o cheie cu spații sau rânduri ar ajunge în textul erorii
        return Raspuns(FARA_CHEIE, mesaj="%s n-are forma ohsvc.<id>.<secret>" % TOKEN_ENV)
    # o execuție poate dura (lacăt, curier, rambursare); o probă e doar citire
    s, b = (http or _http)("POST", BASE + cale,
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"}, corp,
                           90 if corp.get("dry_run") is False else 30)
    try:
        d = json.loads(b) if isinstance(b, str) else b
    except Exception:
        d = None
    if s == 200 and isinstance(d, dict) and "rezultat" in d:
        return Raspuns(DECIS, s, d)
    det = d.get("detail") if isinstance(d, dict) else None
    if s == 404 and isinstance(det, dict) and det.get("rezultat") == "necunoscuta":
        return Raspuns(NECUNOSCUTA, s, det)
    if isinstance(det, dict):
        det = det.get("mesaj") or det.get("rezultat")
    # 4xx = Order Hub a respins cererea înainte de orice acțiune. Rețea / 5xx / 200 necitit, pe o cerere de execuție:
    # nu se știe dacă a apucat să o execute.
    incert = corp.get("dry_run") is False and (s == "ERR" or (isinstance(s, int) and (s >= 500 or s == 200)))
    return Raspuns(EROARE, s, mesaj="HTTP %s: %s" % (s, str(det if det else b)[:200]), incert=incert)


def anulare(cod, utilizator, motiv, actiune="anulare", awb="", nota="", restock=False, aplica=False,
            token="", http=None):
    """`actiune`: anulare (definitiv) | hold (anulează eticheta, comanda rămâne, pe hold)."""
    return _cere("/api/depozit/anulare",
                 {"cod": cod, "comanda": cod, "awb": awb or "", "actiune": actiune, "motiv": motiv, "nota": nota or "",
                  "restock": bool(restock), "utilizator": utilizator, "dry_run": not aplica}, token, http)


def refa(cod, utilizator, colete, awb="", motiv="", aplica=False, token="", http=None):
    """Refă AWB cu `colete` colete, pe același cont de curier."""
    return _cere("/api/depozit/refa",
                 {"cod": cod, "comanda": cod, "awb": awb or "", "colete": int(colete), "motiv": motiv or "",
                  "utilizator": utilizator, "dry_run": not aplica}, token, http)


def stie(cod, utilizator, token="", http=None):
    """Ce știe Order Hub despre comandă, fără nicio scriere: proba unei refaceri. NECUNOSCUTA = n-o cunoaște;
    `previzualizare` / `plecat` = are AWB viu; `fara_awb` = o cunoaște, fără AWB viu; `anulata`; `pauza` = AWB-urile
    magazinului sunt pe pauză (și atunci nu spune dacă are AWB)."""
    return refa(cod, utilizator, 1, motiv="verificare, fără scriere", aplica=False, token=token, http=http)


def eticheta(cod, utilizator, awb="", token="", http=None):
    """Ce etichete vii are comanda, fără nicio scriere: proba unei opriri. Spre deosebire de `stie`, le arată și când
    AWB-urile magazinului sunt pe pauză. Cu `awb`: răspunde și dacă Order Hub cunoaște eticheta asta
    (`eticheta_necunoscuta` când n-o cunoaște)."""
    return anulare(cod, utilizator, "verificare, fără scriere", actiune="hold", awb=awb, aplica=False, token=token,
                   http=http)


def alte_etichete(cod, utilizator, awbs, token="", http=None):
    """O cerere poartă o singură etichetă (prima). Celelalte etichete vii pe care apelantul le vede pe comandă (în
    Shopify) se verifică aici, cu câte o probă → (cele pe care Order Hub nu le cunoaște, cele neverificate). O comandă pe
    care Order Hub n-o cunoaște deloc (404 „necunoscuta") nu oprește nimic: acolo hotărăște calea veche."""
    necunoscute, neverificate = [], []
    for x in list(awbs or [])[1:]:
        r = eticheta(cod, utilizator, awb=x, token=token, http=http)
        if r.stare == DECIS:
            if r.rezultat == "eticheta_necunoscuta":
                necunoscute.append(x)
        elif r.stare != NECUNOSCUTA:
            neverificate.append(x)
    return necunoscute, neverificate


def awb_vii(r):
    """AWB-urile vii din răspunsul unei probe: rândurile „AWB de anulat: …" ale planului, sau coletele plecate."""
    vii = [p.split(": ", 1)[1] for p in r.plan if p.startswith("AWB de anulat: ") and "niciunul" not in p]
    return vii or [str(x) for x in (r.corp.get("plecate") or [])]


def linii(r):
    """Răspunsul Order Hub ca linii de tipărit (mesajul, planul probei, pașii făcuți)."""
    out = [r.mesaj] if r.mesaj else []
    out += ["plan: " + p for p in r.plan]
    out += ["%s %s" % ("✅" if p.get("ok") else "❌", p.get("text") or p.get("pas") or "") for p in r.pasi]
    return out
