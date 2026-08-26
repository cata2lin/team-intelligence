# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
awb_track.py — Tracker live multi-curier pentru AWB-uri (DPD, Sameday, Econt, Packeta, Dragon Star).

Lipești unul sau mai multe numere AWB și primești statusul curent al fiecărui colet,
cu auto-detectare a curierului din forma AWB-ului. Statusul brut de la curier este
normalizat în categorii: delivered / in_transit / returned / refused / canceled / stopped /
closed / generated (+ unknown / not_found / invalid / error pentru ce NU s-a putut verifica).

⚠️ REGULA CENTRALĂ (fix 2026-08-26): **un AWB fără răspuns VERIFICAT de la curier nu primește
niciun status.** Fail-closed, nu fallback pe „în tranzit". Vezi de ce mai jos.

⚠️ `canceled` (AWB anulat/void) a fost ADĂUGAT 2026-08-18 — până atunci textul brut „Canceled" nu se
potrivea cu nicio listă și cădea pe fallback-ul `in_transit`, deci o etichetă ANULATĂ se raporta ca
un colet ÎN TRANZIT (măsurat pe EST240256: 4 AWB-uri anulate afișate „IN TRANZIT", cu rezumatul
„✓ Niciun colet cu probleme"). Anulatele NU intră în `--problems` (void-ul e adesea normal: CS reface
eticheta cu alt nr de colete), dar apar cu ⊘ ANULAT în tabel și sunt numărate explicit în rezumat.

⚠️⚠️ FIX 2026-08-26 — patru găuri prin care coletele primeau statusuri FABRICATE. Măsurat pe DPD LIVE,
pe 945 AWB reale din Shopify (Belasil/Esteban/GT, fără AWBprint), cu adevăr de referință construit
separat (mapare pe `parcelId` + retry: 945/945 rezolvate):
  ÎNAINTE, 3 rulări CONCURENTE × 315 AWB (= CS + cron + al doilea operator pe același cont DPD):
    **221/945 = 23,4% statusuri FALSE, toate „IN TRANZIT"**, din care **32 RETURURI REALE** raportate
    ca fiind pe drum + 1 `Administrative Closure`; toate 3 rulările EXIT=0.
  ÎNAINTE, o SINGURĂ rulare secvențială (fără concurență de la noi, doar contenția producției pe contul
    partajat): **461/945 = 48,8% FALSE**, 65 de retururi reale ascunse.
  DUPĂ, exact aceleași scenarii: **0/945 statusuri false** (distribuția raportată = adevărul, la colet:
    779 livrate / 156 retururi / 9 anulate / 1 închis administrativ), și cu 40% mai rapid (12s vs 20s).
  1. Eroarea top-level DPD (rate-limit `method_threshold.too_many_requests.tracking.second`, dar și
     „Unable to find user to authenticate!" = credențială moartă) era pusă ca STATUS BRUT al tuturor
     coletelor din lot, iar `normalize_status` nu potrivea textul cu nicio listă → `in_transit`.
     Acum: eroarea rămâne EROARE (`ok=False`), niciodată status.
  2. `zip(batch, parcels)` mapa POZIȚIONAL. DPD OMITE tăcut coletele pe care nu le cunoaște (nu dă
     eroare, nu dă element gol), deci de la primul colet lipsă încolo fiecare AWB primea statusul
     VECINULUI. Măsurat: lot de 10 cu UN AWB inexistent → 6/10 greșite, în AMBELE direcții (două
     retururi reale raportate LIVRAT). Acum: mapare pe `parcelId`; AWB cerut și absent → `not_found`.
  3. Limita DPD e **5 CERERI/secundă pe contul PARTAJAT cu producția (VPS/xConnector)**, NU 5 colete:
     coletele per cerere sunt practic nelimitate (200/cerere trec). Loturile de 10 însemnau MAI MULTE
     cereri, deci mai multe ocazii de a lovi limita, și nu exista niciun retry. Acum: lot mare (50),
     ritm global limitat (~2 cereri/s), execuție DPD strict secvențială, RETRY cu backoff pe
     rate-limit, iar după retry-uri epuizate → EROARE, nu status.
  4. `return "in_transit"` implicit la finalul `normalize_status` transforma ORICE text nerecunoscut
     în „în tranzit" (9 căi măsurate, inclusiv „In transit" HARDCODAT la Econt și „AWB Generat" la
     Packeta pentru AWB inexistent). Acum: text nerecunoscut → `unknown`, numărat explicit în rezumat.

DPD se clasifică pe `operationCode` (câmp stabil), nu pe text liber — tabel construit din 33.000+
operații reale. Două corecții cu efect vizibil în rapoarte: `Administrative Closure` (cod 129) e
TERMINAL (`closed`), nu tranzit; `Returned to Office` (cod 38) e INTERMEDIAR (coletul se reîncearcă),
nu retur — 424 apariții în corpus raportau fals RETURNAT.

Read-only: doar interogări de tracking, nu scrie nicăieri.

Curieri implementați LIVE: DPD (api.dpd.ro/v1/track), Sameday (api.sameday.ro),
Econt (ee.econt.com), Packeta (zasilkovna XML), Dragon Star (pagina publică).

Credențiale: din KB, secretul COURIER_CREDS_JSON (un singur JSON cu dpd_creds /
sameday_creds / econt_creds / packeta_creds). Fallback pe variabile de mediu
COURIER_CREDS_JSON, sau DPD_RO_USERNAME / DPD_RO_PASSWORD pentru DPD.

Folosire:
  uv run awb_track.py --awb "81304028147,1ONBLN504748204,Z4944525695"
  uv run awb_track.py --awb "81302362807" --courier dpd      # forțează curierul
  uv run awb_track.py --awb-file lista.txt                    # un AWB pe linie / separate prin , ; newline
  uv run awb_track.py --awb "..." --json                      # output JSON
  uv run awb_track.py --awb "..." --problems                  # doar coletele cu probleme
  uv run awb_track.py --awb-file lista.txt --allow-partial    # exit 0 chiar dacă rămân AWB neverificate

Cod de ieșire: 0 = toate AWB-urile au status verificat; 1 = au rămas AWB neverificate
(eroare / negăsit / status nerecunoscut). `--allow-partial` forțează 0 (comportamentul vechi).
"""
import argparse
import asyncio
import html
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

# Consolele CS sunt Windows cp1252 — fara asta, ✓/✗/⊘ dau UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ─────────────────────────── credențiale (KB / env) ───────────────────────────

def _kb_path():
    # Layout git: plugins/gigi/skills/awb-track -> plugins/core/scripts/kb.py
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "..", "..", "..", "core", "scripts", "kb.py")
    if os.path.exists(p):
        return p
    # Layout cache plugin instalat: team-intelligence/gigi/<hash>/skills/awb-track
    # -> team-intelligence/core/<hash>/scripts/kb.py (hash-ul core poate diferi)
    ti = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    core = os.path.join(ti, "core")
    if os.path.isdir(core):
        for h in sorted(os.listdir(core), reverse=True):
            cand = os.path.join(core, h, "scripts", "kb.py")
            if os.path.exists(cand):
                return cand
    return p


def load_creds():
    """Întoarce dict-ul de credențiale (dpd_creds/sameday_creds/econt_creds/packeta_creds).

    Ordine: env COURIER_CREDS_JSON -> KB COURIER_CREDS_JSON -> compus din DPD_* env/KB.
    """
    raw = os.environ.get("COURIER_CREDS_JSON")
    if not raw:
        kb = _kb_path()
        try:
            raw = subprocess.run(["uv", "run", kb, "secret-get", "COURIER_CREDS_JSON"],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            raw = ""
    creds = {}
    if raw:
        try:
            creds = json.loads(raw)
        except Exception:
            creds = {}

    # Fallback / completare DPD din DPD_RO_USERNAME / DPD_RO_PASSWORD
    dpd = creds.get("dpd_creds") or {}
    if not (dpd.get("dpd-ro") or {}).get("username"):
        u = os.environ.get("DPD_RO_USERNAME")
        p = os.environ.get("DPD_RO_PASSWORD")
        if not u or not p:
            kb = _kb_path()
            try:
                if not u:
                    u = subprocess.run(["uv", "run", kb, "secret-get", "DPD_RO_USERNAME"],
                                       capture_output=True, text=True, timeout=60).stdout.strip()
                if not p:
                    p = subprocess.run(["uv", "run", kb, "secret-get", "DPD_RO_PASSWORD"],
                                       capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception:
                pass
        if u and p:
            dpd.setdefault("dpd-ro", {})
            dpd["dpd-ro"]["username"] = u
            dpd["dpd-ro"]["password"] = p
            creds["dpd_creds"] = dpd
    return creds


# ─────────────────────────── auto-detectare curier ───────────────────────────

def guess_courier(awb: str) -> str:
    """Ghicește curierul din forma AWB-ului. Întoarce o cheie de tracking."""
    a = str(awb).strip().upper()
    if a.startswith("Z"):
        return "packeta"
    if a.startswith("8"):
        return "dpd-ro"
    if a.startswith("1O"):   # 1 + litera O  -> Sameday
        return "sameday"
    if a.startswith("10"):   # 1 + cifra 0   -> Econt
        return "econt"
    if a.isdigit() and a.startswith("9") and len(a) == 8:   # 9xxxxxxx (8 cifre) -> Dragon Star (DSC, doar Grandia)
        return "dragonstar"
    return "necunoscut"


_COURIER_ALIASES = {
    "dpd": "dpd-ro", "dpd-ro": "dpd-ro", "dpdro": "dpd-ro",
    "dpd-jg": "dpd-jg", "dpd-px": "dpd-px",
    "sameday": "sameday", "sd": "sameday",
    "econt": "econt",
    "packeta": "packeta", "zasilkovna": "packeta",
    "dragonstar": "dragonstar", "dragon": "dragonstar", "dsc": "dragonstar", "dragon-star": "dragonstar",
}


# ─────────────────────── rezultatul de la curier (transport) ───────────────────────
# Separăm TRANSPORTUL de STATUS. Un AWB are ori un status REAL de la curier (ok=True + raw),
# ori un EȘEC (ok=False + err + kind). Nu se mai îndeasă mesaje de eroare în câmpul de status —
# ăsta era mecanismul prin care rate-limitul și credențiala moartă deveneau „IN TRANZIT".

def _ok(raw, code=None, source=""):
    return {"ok": True, "raw": (raw or "").strip(), "err": None, "code": code,
            "kind": None, "source": source}


def _fail(kind, err, source=""):
    """kind ∈ {error, not_found, invalid, unknown} — devine direct categoria rândului."""
    return {"ok": False, "raw": None, "err": err, "code": None,
            "kind": kind, "source": source}


# ─────────────────────────── normalizare status ───────────────────────────
# Mapăm textul brut (RO/EN) într-una din categoriile de mai jos. Textul e FALLBACK: pentru DPD
# clasificarea primară e pe `operationCode` (vezi _DPD_OP), fiindcă textul e liber și se schimbă.

_DELIVERED = [
    "delivered", "livrat", "livrare efectuata", "livrare finalizata",
    "delivery successful", "colet livrat", "predat destinatarului",
    # Sameday COD: statusuri POST-livrare — rambursul se transferă/încasează DOAR după ce coletul
    # a fost livrat și clientul a plătit. „Rambursul a fost transferat" = livrat + ramburs încasat.
    # (Un colet chiar returnat spune „returnat" → prins de _RETURNED, care e verificat înainte.)
    "rambursul a fost transferat", "ramburs transferat", "ramburs incasat",
]
_REFUSED = [
    "refused", "refuz", "refuzat", "respins", "rejected",
    "destinatar refuza", "client refuza", "nepreluat", "nu a fost preluat",
]
_RETURNED = [
    "return to sender", "back to sender", "returnat", "retur", "returned", "return",
    "expediat inapoi", "trimis inapoi", "inapoi la expeditor", "redirected to sender",
    "returnam coletul", "returnat coletul", "returat expeditorului",
]
# Coletul e ÎNAPOI ÎN DEPOZITUL curierului și se REÎNCEARCĂ livrarea. Conține „return", deci trebuie
# verificat ÎNAINTEA lui _RETURNED, altfel raporta fals RETURNAT — 424 apariții în corpus.
_TRANSIT_OFFICE = [
    "returned to office", "return to office", "returnat in depozit",
]
# Încercare de livrare EȘUATĂ, fără motiv precizat = coletul încă circulă. Conține „livrat", deci se
# verifică înaintea lui _DELIVERED — dar DUPĂ _REFUSED/_RETURNED: un text COMPUS („Livrare esuata -
# client refuza coletul") descrie un refuz, nu un tranzit. Verificat înaintea lor, fabrica statusuri
# pozitive pe colete refuzate — exact clasa de bug pe care o repară fișierul ăsta.
# (Lista veche `_NOT_DELIVERED` exista dar nu era folosită nicăieri; asta o pune în funcțiune.)
_TRANSIT_ATTEMPT = [
    "ridicarea nu a avut loc", "nu a fost predat", "nu a putut fi livrat",
    "livrare esuata", "livrarea a esuat", "unsuccessful delivery", "failed delivery",
]
# AWB ANULAT (void). LIPSEA COMPLET → textul brut „Canceled" nu se potrivea cu nicio listă și cădea pe
# `return "in_transit"` implicit, adică o etichetă ANULATĂ se raporta ca un colet ÎN TRANZIT. Măsurat
# 2026-08-18 pe EST240256: 4 AWB-uri anulate (regenerate de CS) afișate toate „IN TRANZIT", iar rezumatul
# spunea „✓ Niciun colet cu probleme". CS ar fi crezut că 4 colete sunt pe drum.
_CANCELED = [
    "canceled", "cancelled", "anulat", "anulata", "anulare", "voided",
    "shipment canceled", "shipment cancelled", "awb anulat", "storno",
]
_CLOSED = [
    # Stare TERMINALĂ administrativă: coletul e închis în sistemul curierului (de regulă pierdut /
    # casat / dosar închis), nu mai circulă și nu se va livra. Vezi _DPD_OP[129].
    "administrative closure", "inchidere administrativa", "closed administratively",
]
_STOPPED = [
    # Expeditorul a oprit coletul din drum (CS a cerut oprirea). Nu e nici tranzit, nici retur
    # finalizat — coletul stă și de regulă intră pe traseul de retur. Vezi _DPD_OP[121].
    "stopped by sender", "oprit de expeditor",
]
_GENERATED = [
    "awb generat", "shipment data received", "shipment registered",
    "order received", "registered", "awb creat", "data received",
    "informatii primite", "generat",   # Dragon Star: status brut „Generat" = AWB făcut, încă nepreluat
    "inregistrat",                     # „AWB inregistrat, fara operatii" (curierul cunoaște coletul, zero scanări)
]


def normalize_status(raw) -> str:
    """Text brut de la curier -> categorie. Text nerecunoscut = `unknown`, NICIODATĂ `in_transit`.

    Fallback-ul vechi `return "in_transit"` transforma orice non-status (mesaj de API, placeholder
    intern, eroare) într-un colet „pe drum". Acum tranzitul se atribuie DOAR pe potrivire pozitivă.

    Acceptă și dict-ul întors azi de track_*(): consumatorii externi (courier_verify.py, rulat de
    cron cu --apply, flip_dpd_delivered.py, dpd_verify_all.py) importă funcția asta ca bibliotecă și
    o cheamă cu `track_dpd(...)[awb]`. Când valoarea a devenit dict, ei crăpau cu AttributeError.
    """
    if isinstance(raw, dict):
        if not raw.get("ok"):
            return raw.get("kind") or "unknown"
        raw = raw.get("raw")
    s = _deacc((raw or "").strip().lower())
    if not s:
        return "unknown"
    if any(k in s for k in _TRANSIT_OFFICE):
        return "in_transit"
    # ordinea contează: refused & returned înainte de delivered/in_transit
    if any(k in s for k in _REFUSED):
        return "refused"
    if any(k in s for k in _RETURNED):
        return "returned"
    if any(k in s for k in _TRANSIT_ATTEMPT):
        return "in_transit"
    if any(k in s for k in _DELIVERED):
        return "delivered"
    # ANULAT se verifică DUPĂ refuzat/returnat/livrat (alea descriu ce s-a întâmplat FIZIC cu un colet real
    # și au prioritate), dar ÎNAINTE de „generat" și de fallback — o etichetă void are de regulă DOAR textul
    # „Canceled", iar fără asta ajungea „in_transit".
    if any(k in s for k in _CANCELED):
        return "canceled"
    if any(k in s for k in _CLOSED):
        return "closed"
    if any(k in s for k in _STOPPED):
        return "stopped"
    if any(k in s for k in _TRANSIT):
        return "in_transit"
    if any(k in s for k in _GENERATED):
        return "generated"
    return "unknown"


# Tranzitul se atribuie DOAR pe potrivire pozitivă. Lista acoperă vocabularul măsurat pe 33.170 de
# operații DPD reale + formulările uzuale RO/EN ale celorlalți curieri.
_TRANSIT = [
    "in transit", "in tranzit", "arrival scan", "departure scan", "out for delivery",
    "courier pick-up", "courier pickup", "received in office", "processed in office",
    "returned to office",          # INTERMEDIAR: coletul e înapoi în depozit, se REÎNCEARCĂ livrarea
    "unsuccessful delivery", "failed delivery", "redirected", "routed to another",
    "prepared for self-collecting", "clarify shipment delivery", "predict",
    "unexpected delay", "deferred delivery", "preluat", "ridicat", "in curs de livrare",
    "sortare", "sorted", "expediat", "plecat", "sosit", "livrare in curs",
    # Dragon Star: vocabularul lui real de tranzit. Lipsea, iar cu fallback-ul nou pe `unknown`
    # colete care CIRCULĂ ieșeau NECUNOSCUT — codul vechi le clasifica corect ca tranzit.
    "spre livrare", "iesire agent", "intrare centru", "in centru destinatie",
    "centru destinatie", "intrare agent", "in livrare",
]

# Categorii care cer OCHI DE OM. `unknown` / `not_found` / `error` / `invalid` sunt AICI dinadins:
# până la fixul din 2026-08-26 un AWB neverificabil arăta ca un colet normal în tranzit, iar rezumatul
# scria „✓ Niciun colet cu probleme". `closed` (Administrative Closure) și `stopped` (oprit de
# expeditor) sunt TERMINALE și înseamnă bani neîncasați — intră și ele.
# `canceled` NU intră: void-ul e flux normal (CS reface eticheta cu alt nr de colete).
_PROBLEM = {"refused", "returned", "closed", "stopped", "error", "invalid", "not_found", "unknown"}

# Categorii pentru care NU avem un status verificat de la curier (raportul nu are voie să le numere
# nicăieri ca livrări/tranzit).
_UNRESOLVED = {"error", "invalid", "not_found", "unknown"}


def _deacc(s: str) -> str:
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"),
                 ("ț", "t"), ("ţ", "t"), ("Ă", "a"), ("Â", "a"), ("Î", "i")):
        s = s.replace(a, b)
    return s


# ─────────────────── DPD: clasificare pe operationCode (nu pe text) ───────────────────
# Textul descriptiv e liber și se schimbă; `operationCode` e stabil. Tabelul e construit din
# 33.170 de operații reale (6.525 colete, iul-aug 2026 + feb 2026 + oct 2025).
_DPD_OP = {
    -14: "delivered",     # Delivered
    124: "returned",      # Delivered Back to Sender — retur FINALIZAT la expeditor
    111: "returned",      # Return to Sender — retur ÎN CURS
    123: "refused",       # Refused by recipient
    128: "canceled",      # Canceled (etichetă void)
    129: "closed",        # Administrative Closure — TERMINAL, coletul e închis în sistemul DPD
    121: "stopped",       # Stopped by sender — expeditorul l-a oprit din drum
    148: "generated",     # Shipment data received (AWB creat, coletul încă nepreluat)
    # ── tranzit ──
    1: "in_transit",      # Arrival Scan
    2: "in_transit",      # Departure Scan
    11: "in_transit",     # Received in Office
    12: "in_transit",     # Out for Delivery
    21: "in_transit",     # Processed in Office
    38: "in_transit",     # Returned to Office — INTERMEDIAR (coletul se reîncearcă), NU retur.
                          # Textul conține „Return" → cădea pe _RETURNED și raporta fals RETURNAT;
                          # 424 apariții în corpus. Reclasificarea SCADE numărul de retururi raportat.
    39: "in_transit",     # Courier Pick-up
    44: "in_transit",     # Unsuccessful Delivery — tranzit, dar cu semnal (vezi _DPD_ATTENTION)
    69: "in_transit",     # Deferred delivery (+1 day)
    115: "in_transit",    # Redirected
    134: "in_transit",    # Prepared for Self-collecting by Consignee
    136: "in_transit",    # Clarify shipment delivery
    152: "in_transit",    # Routed to another DPD Location
    175: "in_transit",    # Predict
    181: "in_transit",    # Unexpected delay
}
# Coduri care rămân „în tranzit" dar semnalează o livrare care merge prost — se marchează cu ⚑ în
# tabel și se numără separat, ca să nu se piardă în masa de colete normale.
_DPD_ATTENTION = {44, 136, 181}


def classify(res) -> str:
    """Rezultatul de transport -> categorie. Fără răspuns verificat NU există status."""
    if not res or not res.get("ok"):
        kind = (res or {}).get("kind") or "error"
        return kind if kind in _NORM_RO else "error"
    code = res.get("code")
    if code is not None and code in _DPD_OP:
        return _DPD_OP[code]
    # Cod DPD nevăzut (colete internaționale / servicii noi) sau alt curier → potrivire pe text,
    # iar dacă nici textul nu se potrivește → `unknown`. NICIODATĂ in_transit implicit.
    return normalize_status(res.get("raw"))


# ─────────────────────────── trackere live (async) ───────────────────────────

class _Throttle:
    """Limitator global de CERERI/secundă, partajat de toate loturile DPD.

    Limita DPD e `method_threshold.too_many_requests.tracking.second` = 5 CERERI/s pe CONT, iar
    contul e PARTAJAT cu producția (VPS/xConnector) — bugetul e consumat și de alții. De-aia
    limităm ritmul (nu numărul de colete) și lăsăm loturile mari: mai puține cereri = mai puține
    ocazii de a lovi limita. `asyncio.sleep(0.2)` fix de dinainte nu era un limitator: nu ținea
    cont de durata cererii și nu se coordona între profile DPD.
    """

    def __init__(self, rps: float):
        self._gap = 1.0 / max(0.1, float(rps))
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            wait_s = self._last + self._gap - time.monotonic()
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            self._last = time.monotonic()


DPD_BATCH = 50            # colete per cerere (200 trec fără probleme; 50 = compromis blast-radius/viteză)
DPD_RPS = 2.0             # cereri/secundă, sub limita de 5/s a contului PARTAJAT
DPD_MAX_TRIES = 4         # încercări per lot la rate-limit (măsurat: limita se eliberează sub ~1s)
_DPD_BACKOFF = (0.8, 1.6, 3.2)

# Contorizăm rate-limitul fiindcă e INTERMITENT (măsurat: 15,1% din cereri într-o rulare, 0% în
# alte cinci) — fără contor nu se poate deosebi o rulare curată de una norocoasă, și exact asta a
# ținut bug-ul ascuns luni întregi. `recovered` = loturi salvate de retry, `exhausted` = loturi
# pierdute definitiv (coletele lor ies EROARE, nu status).
_DPD_STATS = {"requests": 0, "rate_limited": 0, "recovered": 0, "exhausted": 0}

_dpd_throttle = None


def _throttle(rps=None):
    global _dpd_throttle
    if _dpd_throttle is None:
        _dpd_throttle = _Throttle(rps or DPD_RPS)
    return _dpd_throttle


def _dpd_is_rate_limit(err) -> bool:
    """Rate-limitul DPD vine EXCLUSIV ca eroare top-level cu `parcels` gol (HTTP 200).

    Cele două gărzi vechi apărau mecanisme inexistente și de-aia bug-ul a supraviețuit:
    filtrul pe operații care încep cu „Too many" (0 apariții în 33.170 de operații scanate) și
    ramura `parcel.get("error")` (0 colete cu eroare per-colet). Ambele scoase.
    """
    if not isinstance(err, dict):
        return False
    ctx = str(err.get("context") or "")
    msg = str(err.get("message") or "")
    return "too_many_requests" in ctx or "too many tracking" in msg.lower()


async def _dpd_request(client, batch, creds, rps=None):
    """O cerere DPD. Întoarce ('ok', data) | ('rate_limit', msg) | ('error', msg)."""
    await _throttle(rps).wait()
    _DPD_STATS["requests"] += 1
    try:
        r = await client.post("https://api.dpd.ro/v1/track", json={
            "userName": creds["username"], "password": creds["password"],
            "language": "EN", "lastOperationOnly": False,
            "parcels": [{"id": a} for a in batch],
        }, timeout=45.0)
    except Exception as e:
        return "error", "Eroare retea DPD (%s)" % type(e).__name__
    if r.status_code != 200:
        # DPD RO trimite rate-limitul ca 200 + error.context, dar tratăm și 429 clasic.
        return ("rate_limit" if r.status_code == 429 else "error"), "Eroare HTTP DPD %d" % r.status_code
    try:
        data = r.json()
    except Exception:
        return "error", "Raspuns DPD ne-JSON (HTTP 200)"
    err = data.get("error")
    if err:
        msg = (err.get("message") or str(err)) if isinstance(err, dict) else str(err)
        if _dpd_is_rate_limit(err):
            return "rate_limit", msg or "Rate-limit DPD"
        return "error", msg or "Eroare DPD"
    return "ok", data


async def track_dpd(client, awbs, creds, batch_size=None, rps=None) -> dict:
    """Track DPD prin api.dpd.ro/v1/track. Întoarce {awb: rezultat_transport}."""
    out = {}
    if not creds or not creds.get("username") or not creds.get("password"):
        for a in awbs:
            out[a] = _fail("error", "Credentiale DPD lipsa", "dpd")
        return out
    bs = int(batch_size or DPD_BATCH)
    uniq = list(dict.fromkeys(awbs))
    for i in range(0, len(uniq), bs):
        batch = uniq[i:i + bs]
        data = None
        last_err = "Eroare DPD"
        for attempt in range(DPD_MAX_TRIES):
            kind, payload = await _dpd_request(client, batch, creds, rps)
            if kind == "ok":
                data = payload
                break
            last_err = payload
            if kind != "rate_limit":
                break   # credențială moartă / HTTP 5xx: retry-ul nu ajută și lovește contul partajat
            _DPD_STATS["rate_limited"] += 1
            if attempt < DPD_MAX_TRIES - 1:
                back = _DPD_BACKOFF[min(attempt, len(_DPD_BACKOFF) - 1)]
                await asyncio.sleep(back * (1.0 + random.random() * 0.3))
        if data is None:
            # Retry epuizat SAU eroare dură (auth/HTTP/rețea) → EROARE pe fiecare colet din lot.
            # Aici pica raportul înainte: mesajul ajungea status brut și devenea „IN TRANZIT".
            _DPD_STATS["exhausted"] += 1
            for a in batch:
                out[a] = _fail("error", last_err, "dpd")
            continue
        if attempt:
            _DPD_STATS["recovered"] += 1
        # Mapare pe `parcelId`, NU pozițional: DPD omite TĂCUT coletele necunoscute (fără eroare,
        # fără element gol), deci `zip(batch, parcels)` decala statusurile pe alte AWB-uri.
        by_id = {}
        for p in (data.get("parcels") or []):
            pid = str(p.get("parcelId") or "").strip()
            if pid:
                by_id[pid] = p
        for awb in batch:
            p = by_id.get(str(awb).strip())
            if p is None:
                out[awb] = _fail("not_found", "DPD nu cunoaste acest AWB", "dpd")
                continue
            ops = p.get("operations") or []
            if not ops:
                out[awb] = _ok("AWB inregistrat, fara operatii", None, "dpd")
                continue
            latest = max(ops, key=lambda o: o.get("dateTime") or "")
            desc = (latest.get("description") or "").strip()
            code = latest.get("operationCode")
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None
            if not desc and code is None:
                out[awb] = _fail("error", "Operatie DPD fara descriere si fara cod", "dpd")
                continue
            out[awb] = _ok(desc, code, "dpd")
    return out


_sameday_token = {}


async def _sameday_auth(client, creds) -> str:
    u = (creds or {}).get("username", "")
    if not u:
        return ""
    cached = _sameday_token.get(u)
    if cached and datetime.now(timezone.utc) < cached["exp"]:
        return cached["token"]
    try:
        r = await client.post("https://api.sameday.ro/api/authenticate",
                              headers={"X-AUTH-USERNAME": u,
                                       "X-AUTH-PASSWORD": creds.get("password", "")},
                              timeout=15.0)
        r.raise_for_status()
        token = r.json().get("token", "")
        if token:
            _sameday_token[u] = {"token": token,
                                 "exp": datetime.now(timezone.utc) + timedelta(minutes=55)}
        return token
    except Exception:
        return ""


async def track_sameday(client, awb, creds) -> dict:
    if not str(awb).strip():
        return _fail("not_found", "AWB gol", "sameday")
    token = await _sameday_auth(client, creds)
    if not token:
        return _fail("error", "Eroare autentificare Sameday", "sameday")
    try:
        r = await client.get(f"https://api.sameday.ro/api/client/awb/{awb}/status",
                             headers={"X-AUTH-TOKEN": token}, timeout=20.0)
        if r.status_code == 404:
            return _fail("not_found", "Sameday nu cunoaste acest AWB", "sameday")
        if r.status_code == 429:
            return _fail("error", "Rate-limit Sameday (HTTP 429)", "sameday")
        r.raise_for_status()
        data = r.json()
        hist = data.get("expeditionHistory") or []
        if not hist:
            return _ok("AWB inregistrat, fara operatii", None, "sameday")
        last = max(hist, key=lambda e: e.get("statusDate") or e.get("date") or "")
        label = (last.get("statusLabel") or "").strip()
        if not label:
            # era „Status Necunoscut" → in_transit
            return _fail("error", "Sameday: operatie fara statusLabel", "sameday")
        return _ok(label, None, "sameday")
    except Exception as e:
        return _fail("error", "Eroare API Sameday (%s)" % type(e).__name__, "sameday")


async def track_econt(client, awb, creds) -> dict:
    if not creds or not creds.get("username"):
        return _fail("error", "Credentiale Econt lipsa", "econt")
    if not str(awb).strip():
        return _fail("not_found", "AWB gol", "econt")
    try:
        r = await client.post(
            "https://ee.econt.com/services/Shipments/ShipmentService.getShipmentStatuses.json",
            json={"username": creds.get("username"), "password": creds.get("password"),
                  "shipmentNumbers": [awb]}, timeout=20.0)
        txt = r.text or ""
        if r.status_code in (517, 400) and "ExInvalidShipmentNum" in txt:
            return _fail("not_found", "Econt nu cunoaste acest AWB", "econt")
        r.raise_for_status()
        data = r.json()
        arr = data.get("shipmentStatuses") or []
        if not arr:
            return _fail("not_found", "Econt: niciun status pentru acest AWB", "econt")
        st = (arr[0].get("status") or {})
        desc = (st.get("shortDeliveryStatusEn") or st.get("shortDeliveryStatusRo") or "").strip()
        if not desc:
            # ERA `or "In transit"` HARDCODAT: un AWB inexistent — sau chiar GOL — primea „In Transit"
            # fabricat, reproductibil 100%. Acum lipsa statusului e lipsă de status.
            return _fail("not_found", "Econt: raspuns fara status (AWB necunoscut?)", "econt")
        return _ok(str(desc).strip().title(), None, "econt")
    except Exception as e:
        return _fail("error", "Eroare API Econt (%s)" % type(e).__name__, "econt")


async def track_packeta(client, awb, creds) -> dict:
    api_pw = (creds or {}).get("api_password", "").strip()
    if not api_pw:
        return _fail("error", "Credentiale Packeta lipsa", "packeta")
    pid = awb[1:] if str(awb).upper().startswith("Z") else str(awb)
    if not pid.strip():
        return _fail("not_found", "AWB gol", "packeta")
    base = (creds.get("base_url") or "").strip() or "https://www.zasilkovna.cz/api/rest"
    body = (f'<?xml version="1.0" encoding="utf-8"?>\n<packetTracking>'
            f'<apiPassword>{api_pw}</apiPassword><packetId>{pid}</packetId></packetTracking>')
    try:
        r = await client.post(base, content=body.encode("utf-8"),
                             headers={"Content-Type": "text/xml; charset=utf-8",
                                      "Accept-Language": "ro_RO"}, timeout=20.0)
        if r.status_code != 200:
            return _fail("error", "Eroare HTTP Packeta %d" % r.status_code, "packeta")
        txt = r.text or ""
        low = txt.lower()
        if "<status>fault</status>" in low or "<fault>" in low:
            if "password" in low:
                return _fail("error", "Credentiale Packeta invalide", "packeta")
            m = re.search(r'<string>(.*?)</string>', txt, re.DOTALL) or \
                re.search(r'<fault>(.*?)</fault>', txt, re.DOTALL)
            detail = html.unescape((m.group(1) if m else "")).strip()
            return _fail("not_found", "Packeta: %s" % (detail[:60] or "AWB necunoscut"), "packeta")
        statuses = re.findall(r'<statusText[^>]*>(.*?)</statusText>', txt, re.DOTALL)
        if statuses:
            return _ok(html.unescape(statuses[-1].strip()), None, "packeta")
        # fallback: numele statusului
        names = re.findall(r'<statusName[^>]*>(.*?)</statusName>', txt, re.DOTALL)
        if names:
            return _ok(html.unescape(names[-1].strip()), None, "packeta")
        # ERA `return "AWB Generat"`: orice răspuns fără status (inclusiv AWB inexistent) devenea
        # un colet „generat", deci un AWB inventat arăta ca unul real.
        return _fail("not_found", "Packeta: raspuns fara status", "packeta")
    except Exception as e:
        return _fail("error", "Eroare API Packeta (%s)" % type(e).__name__, "packeta")


async def track_dragonstar(client, awb, creds=None) -> dict:
    """Dragon Star Curier (DSC) — status randat server-side în pagina publică de tracking, FĂRĂ credentiale.
    Doar Grandia (connector xConnector 24257). AWBprint NU sincronizează DSC, deci asta e singura sursă de status real."""
    if not str(awb).strip():
        return _fail("not_found", "AWB gol", "dragonstar")
    try:
        r = await client.get("https://dragonstarcurier.ro/tracking-awb",
                             params={"awb": str(awb).strip()},
                             headers={"User-Agent": "Mozilla/5.0"},
                             follow_redirects=True, timeout=20.0)
        if r.status_code != 200:
            return _fail("error", "Eroare HTTP Dragon Star %d" % r.status_code, "dragonstar")
        m = re.search(r'>Status</div>\s*<div[^>]*>([^<]+)</div>', r.text or "")
        if not m:
            # Regexul neptrivit poate însemna AWB inexistent SAU pagină schimbată — în ambele cazuri
            # NU știm statusul. Înainte devenea „IN TRANZIT" tăcut, pentru toate coletele DSC.
            return _fail("not_found", "Dragon Star: AWB negasit / pagina schimbata", "dragonstar")
        return _ok(html.unescape(m.group(1).strip()), None, "dragonstar")
    except Exception as e:
        return _fail("error", "Eroare Dragon Star (%s)" % type(e).__name__, "dragonstar")


async def track_all(items, creds, dpd_batch=None, dpd_rps=None):
    """items: listă de dict {awb, courier_key}. Întoarce {awb: rezultat_transport}."""
    dpd_creds = creds.get("dpd_creds") or {}
    sd_creds = creds.get("sameday_creds") or {}
    ec_creds = creds.get("econt_creds") or {}
    pk_creds = creds.get("packeta_creds") or {}

    dpd_groups = defaultdict(list)
    sd, ec, pk, ds, unknown = [], [], [], [], []
    for it in items:
        ck = it["courier_key"]
        a = it["awb"]
        if ck.startswith("dpd-"):
            dpd_groups[ck].append(a)
        elif ck == "sameday":
            sd.append(a)
        elif ck == "econt":
            ec.append(a)
        elif ck == "packeta":
            pk.append(a)
        elif ck == "dragonstar":
            ds.append(a)
        else:
            unknown.append(a)

    out = {}
    async with httpx.AsyncClient(timeout=90.0) as client:
        async def _dpd_all():
            # STRICT SECVENȚIAL între profile: contul DPD e partajat, iar paralelismul măsurat
            # (5 cereri deodată) pierdea 25% din colete.
            for dpd_key, awbs in dpd_groups.items():
                dc = dpd_creds.get(dpd_key) or dpd_creds.get("dpd-ro") or {}
                out.update(await track_dpd(client, awbs, dc, dpd_batch, dpd_rps))

        tasks = [_dpd_all()] if dpd_groups else []
        sem = asyncio.Semaphore(20)

        async def _one(awb, fn, c, ck):
            async with sem:
                try:
                    out[awb] = await fn(client, awb, c)
                except Exception as e:
                    # Excepția nu mai dispare în gather(return_exceptions=True) lăsând AWB-ul fără
                    # intrare — de-acolo ieșea „—" din status_map.get() și devenea in_transit.
                    out[awb] = _fail("error", "Exceptie interna (%s)" % type(e).__name__, ck)

        for a in sd:
            tasks.append(_one(a, track_sameday, sd_creds, "sameday"))
        for a in ec:
            tasks.append(_one(a, track_econt, ec_creds, "econt"))
        for a in pk:
            tasks.append(_one(a, track_packeta, pk_creds, "packeta"))
        for a in ds:
            tasks.append(_one(a, track_dragonstar, None, "dragonstar"))   # Dragon Star: tracking public

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    for a in unknown:
        out[a] = _fail("unknown", "Curier necunoscut din forma AWB", "necunoscut")
    return out


# ─────────────────────────── parsare input ───────────────────────────

def parse_awbs(text: str):
    if not text:
        return []
    parts = re.split(r'[,;\s]+', text)
    return [p.strip() for p in parts if p.strip()]


# ─────────────────────────── output ───────────────────────────

_NORM_RO = {
    "delivered": "LIVRAT", "in_transit": "IN TRANZIT", "returned": "RETURNAT",
    "refused": "REFUZAT", "generated": "AWB GENERAT", "unknown": "NECUNOSCUT",
    "error": "EROARE", "invalid": "AWB INVALID", "canceled": "ANULAT",
    "closed": "INCHIS ADMIN", "not_found": "NEGASIT", "stopped": "OPRIT DE EXP",
}
# ANULAT primește flag vizibil, dar NU intră în `_PROBLEM`: void-ul e adesea normal (CS reface eticheta cu
# alt nr de colete → vechea se anulează). Ar umple `--problems` cu zgomot. Se vede în coloană + în rezumat.
_FLAG = {"refused": "⚠", "returned": "⚠", "closed": "⛔", "stopped": "⛔",
         "error": "✗", "invalid": "✗", "not_found": "✗", "unknown": "?", "canceled": "⊘"}


def build_rows(items, res_map):
    rows = []
    for it in items:
        awb = it["awb"]
        ck = it["courier_key"]
        res = res_map.get(awb)
        if res is None:
            # Nimic nu are voie să treacă fără rezultat: înainte `status_map.get(awb, "—")` producea
            # „—", care nu se potrivea cu nicio listă și devenea IN TRANZIT.
            res = _fail("error", "Fara raspuns intern pentru acest AWB", ck)
        norm = classify(res)
        raw = res["raw"] if res.get("ok") else (res.get("err") or "")
        rows.append({
            "awb": awb,
            "courier": ("?" if ck == "necunoscut" else ck.upper()),
            "courier_key": ck,
            "status_raw": raw,
            "status": norm,
            "status_ro": _NORM_RO.get(norm, norm.upper()),
            "problem": norm in _PROBLEM,
            # câmpuri ADĂUGATE (consumatorii vechi citesc mai departe status/status_ro/problem)
            "ok": bool(res.get("ok")),
            "err": res.get("err"),
            "source": res.get("source") or ck,
            "op_code": res.get("code"),
            "attention": bool(res.get("ok") and res.get("code") in _DPD_ATTENTION),
            "unrecognized": bool(res.get("ok") and norm == "unknown"),
        })
    return rows


def dpd_throttle_line():
    """Linia de ritm DPD — se tipărește DOAR când contul a fost efectiv limitat.

    Rate-limitul e tăcut (HTTP 200 + `parcels` gol) și intermitent, deci fără linia asta nu se poate
    spune dacă o rulare „curată" a fost corectă sau doar norocoasă.
    """
    st = _DPD_STATS
    if not st["rate_limited"]:
        return ""
    return ("↻ DPD rate-limit: %d cereri limitate din %d, %d loturi recuperate prin retry, "
            "%d loturi PIERDUTE (coletele lor ies EROARE, nu status)." %
            (st["rate_limited"], st["requests"], st["recovered"], st["exhausted"]))


def summarize(rows):
    """Cifrele pe care se bazează rezumatul ȘI codul de ieșire."""
    return {
        "unresolved": [r for r in rows if r["status"] in _UNRESOLVED],
        "unrecognized": [r for r in rows if r["unrecognized"]],
        "attention": [r for r in rows if r["attention"]],
        "problems": [r for r in rows if r["problem"]],
    }


def print_table(rows):
    if not rows:
        print("Niciun AWB de procesat.")
        return
    print("=== AWB tracker — %d colete ===" % len(rows))
    print("%-17s %-9s %-13s %-7s %s" % ("AWB", "CURIER", "STATUS", "FLAG", "detaliu curier"))
    print("-" * 92)
    for r in rows:
        flag = _FLAG.get(r["status"], "")
        if not flag and r["attention"]:
            flag = "⚑"
        print("%-17s %-9s %-13s %-7s %s" % (
            r["awb"][:17], r["courier"][:9], r["status_ro"][:13], flag,
            (r["status_raw"] or "")[:42]))
    # rezumat
    by = defaultdict(int)
    for r in rows:
        by[r["status"]] += 1
    s = summarize(rows)
    print("-" * 92)
    tl = dpd_throttle_line()
    if tl:
        print(tl)
    summary = "  ".join("%s=%d" % (_NORM_RO.get(k, k.upper()), v)
                        for k, v in sorted(by.items(), key=lambda x: -x[1]))
    print("Rezumat: " + summary)
    # Linia asta e motivul fixului: un AWB neverificat NU e un colet în tranzit.
    if s["unresolved"]:
        kinds = defaultdict(int)
        for r in s["unresolved"]:
            kinds[r["status_ro"]] += 1
        print("⚠ %d AWB NEVERIFICATE (nu stim statusul): %s" % (
            len(s["unresolved"]), ", ".join("%s=%d" % kv for kv in sorted(kinds.items()))))
    if s["unrecognized"]:
        print("⚠ %d statusuri NERECUNOSCUTE (text nou de la curier, nemapate): %s" % (
            len(s["unrecognized"]),
            ", ".join(sorted({(r["status_raw"] or "")[:40] for r in s["unrecognized"]})[:5])))
    if s["attention"]:
        print("⚑ %d colete in tranzit CU PROBLEME de livrare (livrare esuata / intarziere / clarificare)."
              % len(s["attention"]))
    if s["problems"]:
        print("⚠ %d colete cu probleme:" % len(s["problems"]))
        for r in s["problems"]:
            print("   - %s [%s] -> %s (%s)" % (r["awb"], r["courier"],
                                               r["status_ro"], r["status_raw"]))
    else:
        # „✓ Niciun colet cu probleme" nu se mai poate tipări cât timp există AWB neverificate:
        # error/not_found/unknown sunt în `_PROBLEM`, deci ar fi fost deja listate mai sus.
        n_canc = by.get("canceled", 0)
        if n_canc:
            print("✓ Niciun colet cu probleme (returnat/refuzat/eroare) — dar %d AWB ANULAT(E), "
                  "coletul NU e pe drum." % n_canc)
        else:
            print("✓ Niciun colet cu probleme (returnat/refuzat/eroare).")


def main():
    ap = argparse.ArgumentParser(description="Tracker live multi-curier AWB (DPD/Sameday/Econt/Packeta/Dragon Star).")
    # `action="append"` fiindcă MCP-ul CS construiește `--awb X --awb Y` (xconnector/mcp_server.py):
    # cu un `--awb` simplu se păstra DOAR ultimul, deci CS urmărea un singur colet din listă.
    ap.add_argument("--awb", action="append", default=[],
                    help="Unul sau mai multe AWB-uri separate prin , ; spațiu / newline. Repetabil.")
    ap.add_argument("--awb-file", default="", help="Fișier cu AWB-uri (unul pe linie / separate).")
    ap.add_argument("--courier", default="", help="Forțează curierul pentru TOATE AWB-urile (dpd|sameday|econt|packeta).")
    ap.add_argument("--problems", action="store_true", help="Afișează doar coletele cu probleme.")
    ap.add_argument("--json", action="store_true", help="Output JSON în loc de tabel.")
    ap.add_argument("--allow-partial", action="store_true",
                    help="Exit 0 chiar dacă rămân AWB neverificate (comportamentul de dinainte de 2026-08-26).")
    ap.add_argument("--dpd-batch", type=int, default=DPD_BATCH, help="Colete per cerere DPD (implicit %d)." % DPD_BATCH)
    ap.add_argument("--dpd-rps", type=float, default=DPD_RPS, help="Cereri DPD pe secundă (implicit %.1f)." % DPD_RPS)
    a = ap.parse_args()

    awbs = parse_awbs(" ".join(a.awb))
    if a.awb_file:
        try:
            with open(a.awb_file, "r", encoding="utf-8") as f:
                awbs += parse_awbs(f.read())
        except Exception as e:
            print("Nu pot citi fișierul %s: %s" % (a.awb_file, e), file=sys.stderr)
            sys.exit(2)
    # dedup păstrând ordinea
    awbs = list(dict.fromkeys(awbs))
    if not awbs:
        print("Niciun AWB dat. Folosește --awb '123,456' sau --awb-file lista.txt", file=sys.stderr)
        sys.exit(2)

    forced = _COURIER_ALIASES.get(a.courier.strip().lower()) if a.courier else None
    items = []
    for awb in awbs:
        ck = forced or guess_courier(awb)
        items.append({"awb": awb, "courier_key": ck})

    creds = load_creds()
    res_map = asyncio.run(track_all(items, creds, a.dpd_batch, a.dpd_rps))
    rows = build_rows(items, res_map)
    s = summarize(rows)   # pe TOATE rândurile, înainte de filtrarea --problems

    if a.problems:
        rows = [r for r in rows if r["problem"]]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        # Avertismentele merg pe stderr ca stdout-ul să rămână JSON curat pentru consumatori
        # (cs-360 WISMO, MCP-ul xconnector, awb-statii).
        if s["unresolved"]:
            print("⚠ %d/%d AWB NEVERIFICATE (nu stim statusul)." % (len(s["unresolved"]), len(res_map)),
                  file=sys.stderr)
        if s["unrecognized"]:
            print("⚠ %d statusuri nerecunoscute (text nou de la curier)." % len(s["unrecognized"]),
                  file=sys.stderr)
        tl = dpd_throttle_line()
        if tl:
            print(tl, file=sys.stderr)
    else:
        print_table(rows)

    # Exit != 0 când n-am putut verifica tot: un cron care se uita doar la exit code nu mai poate
    # confunda un raport fabricat cu unul real. `--allow-partial` păstrează comportamentul vechi.
    if (s["unresolved"] or s["unrecognized"]) and not a.allow_partial:
        sys.exit(1)


if __name__ == "__main__":
    main()
