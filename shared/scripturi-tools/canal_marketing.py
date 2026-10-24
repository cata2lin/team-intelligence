"""Canalul de marketing al unei comenzi Shopify: tiktok / meta / google / direct / altul / necunoscut.

De ce (24-oct-2026): rata de livrare se judecă pe canal („refuzurile vin din TikTok”), iar costul pe comandă livrată se
compară între platforme. O singură regulă, folosită și de analiză și de câmpul permanent din Order Hub.

Intrarea = comanda din Shopify Admin GraphQL, cu câmpurile (orice lipsă e tolerată):
  customerJourneySummary { firstVisit { source referrerUrl landingPage utmParameters { source medium campaign } }
                           lastVisit  { … aceleași … } }
  customAttributes { key value }      # note_attributes: formularul Releasit pune aici utm_* și id-urile de click
  app { name }                        # cine a creat comanda (Releasit, Shopify Online Store, draft …)

Ordinea regulilor (prima care se potrivește câștigă), pe ULTIMA vizită, apoi pe prima, apoi pe atributele comenzii:
  1. id de click în URL-ul de aterizare / atribute: ttclid → tiktok; fbclid → meta; gclid / gbraid / wbraid → google;
  2. utm_source: tiktok / tt → tiktok; facebook / fb / ig / instagram / meta → meta; google / youtube → google;
     altă valoare → altul;
  3. domeniul referrer-ului: tiktok.com → tiktok; facebook.com / instagram.com / fb.com / l.facebook.com → meta;
     google.* / youtube.com → google; alt domeniu → altul;
  4. există o vizită, dar fără sursă și fără referrer → direct;
  5. nicio dată de vizită și niciun atribut → necunoscut.
Întoarce (canal, sursa_atribuirii), unde sursa spune DE UNDE vine decizia: „click_id:ttclid”, „utm:last”, „referrer:first”,
„releasit:utm” etc. — ca să se poată măsura cât de sigură e atribuirea.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# Marketplace-urile au canalul lor (ownerul, 24-oct): decis după MAGAZINUL comenzii din Orders, nu după UTM.
MARKETPLACE = ("emag", "trendyol", "olx", "okazii", "pepita", "cel")
# „necunoscut” pe două rânduri: fără UTM vs magazin fără token Shopify (închis), ca să nu se amestece cauzele
NECUNOSCUT_INCHIS = "necunoscut_magazin_inchis"
# Portal parteneri (B2B, fără reclame) are canalul lui, nu e „magazin închis” (Orchestrator, 24-oct)
PORTAL = "portal"
DOMENII_PORTAL = ("portal-parteneri.arona.invalid",)
CANALE = ("tiktok", "meta", "google", "direct", "altul", "necunoscut") + MARKETPLACE + (PORTAL, NECUNOSCUT_INCHIS)
CLICK_ID = (("ttclid", "tiktok"), ("fbclid", "meta"), ("gclid", "google"), ("gbraid", "google"), ("wbraid", "google"))
_UTM = (
    (re.compile(r"^(tiktok|tt|tik[-_ ]?tok)", re.I), "tiktok"),
    (re.compile(r"^(facebook|fb|ig|instagram|meta|an|msg)\b", re.I), "meta"),
    (re.compile(r"^(google|youtube|yt|adwords|gads)", re.I), "google"),
)
_REF = (
    (re.compile(r"(^|\.)tiktok\.com$", re.I), "tiktok"),
    (re.compile(r"(^|\.)(facebook\.com|fb\.com|instagram\.com|fb\.me|messenger\.com)$", re.I), "meta"),
    (re.compile(r"(^|\.)(google\.[a-z.]+|youtube\.com|googleadservices\.com|googlesyndication\.com)$", re.I), "google"),
)


def _utm_canal(src: Optional[str]) -> Optional[str]:
    s = (src or "").strip()
    if not s:
        return None
    for rx, c in _UTM:
        if rx.match(s):
            return c
    return "altul"


def _ref_canal(url: Optional[str]) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None
    host = (urlparse(u if "://" in u else "http://" + u).hostname or "").lower()
    if not host:
        return None
    for rx, c in _REF:
        if rx.search(host):
            return c
    return "altul"


def _click_id(url: Optional[str]) -> Optional[Tuple[str, str]]:
    u = (url or "").strip()
    if not u:
        return None
    q = parse_qs(urlparse(u if "://" in u else "http://x/" + u.lstrip("/")).query)
    for k, c in CLICK_ID:
        if q.get(k):
            return c, k
    return None


def _atribute(o: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for a in o.get("customAttributes") or o.get("note_attributes") or []:
        # Releasit scrie cheile „UTM source”, „UTM medium” … → „utm_source”
        k = re.sub(r"[\s-]+", "_", str(a.get("key") or a.get("name") or "").strip().lower())
        if k:
            out[k] = str(a.get("value") or "").strip()
    return out


def canal_marketplace(marketplace: Any) -> Optional[Tuple[str, str]]:
    """(canal, „marketplace”) pentru o comandă de pe un marketplace cunoscut (după magazin), altfel None."""
    m = str(marketplace or "").strip().lower()
    return (m, "marketplace") if m in MARKETPLACE else None


def canal_din_comanda(o: Dict[str, Any]) -> Tuple[str, str]:
    """(canal, sursa_atribuirii) pentru o comandă Shopify (dict GraphQL); vezi regulile din docstring-ul modulului."""
    cj = o.get("customerJourneySummary") or {}
    vizite = [("last", cj.get("lastVisit")), ("first", cj.get("firstVisit"))]
    vizite = [(n, v) for n, v in vizite if v]
    attrs = _atribute(o)

    # 1. id de click: în URL-ul de aterizare al vizitelor, apoi în atributele comenzii (Releasit)
    for n, v in vizite:
        cid = _click_id(v.get("landingPage"))
        if cid:
            return cid[0], "click_id:%s:%s" % (cid[1], n)
    for k, c in CLICK_ID:
        if attrs.get(k):
            return c, "releasit:%s" % k
    for k in ("landing_page", "landing_site", "landingpage", "page_url", "url"):
        cid = _click_id(attrs.get(k))
        if cid:
            return cid[0], "releasit:%s" % cid[1]

    # 2. utm_source
    for n, v in vizite:
        c = _utm_canal((v.get("utmParameters") or {}).get("source"))
        if c:
            return c, "utm:%s" % n
    c = _utm_canal(attrs.get("utm_source"))
    if c:
        return c, "releasit:utm"

    # 3. referrer
    for n, v in vizite:
        c = _ref_canal(v.get("referrerUrl"))
        if c:
            return c, "referrer:%s" % n
    c = _ref_canal(attrs.get("referrer") or attrs.get("referring_site"))
    if c:
        return c, "releasit:referrer"

    # 3b. sursa clasificată de Shopify pe vizită (ex. „social”, „search”) fără alte detalii
    for n, v in vizite:
        s = str(v.get("source") or "").strip().lower()
        if s in ("tiktok",):
            return "tiktok", "shopify_source:%s" % n
        if s in ("facebook", "instagram"):
            return "meta", "shopify_source:%s" % n
        if s in ("google",):
            return "google", "shopify_source:%s" % n

    # 4. vizită fără sursă = direct; 5. nimic = necunoscut
    if vizite:
        return "direct", "vizita_fara_sursa"
    return "necunoscut", "fara_date"


def numara(comenzi: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Câte comenzi pe canal (ajutor pentru rapoarte și teste)."""
    out = {c: 0 for c in CANALE}
    for o in comenzi:
        out[canal_din_comanda(o)[0]] += 1
    return out
