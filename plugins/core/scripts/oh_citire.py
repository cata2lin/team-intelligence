"""oh_citire — citirile DOAR-GET din Order Hub pentru skill-urile echipei care rulează pe laptop.

De ce (24-oct-2026, „AWBprint fără cititori”): baza AWBprint rămâne doar pentru servere; skill-urile de vânzări /
produse citeau `orders.line_items` de acolo. Order Hub e sursa de adevăr a comenzilor (statusurile intl din AWBprint
sunt înghețate din 27-sep). Modulul ăsta e singurul loc unde se face cererea: cheia, erorile, gzip-ul, paginarea.

Regula (principiul din PR #667): fără cheie sau OH care nu răspunde = EROARE clară (`OhEroare`), niciodată cifra
veche. Mesajele nu conțin cheia.

Cheia, în ordinea asta: env OH_TEAM_READ_TOKEN → env OH_PROFIT_CITIRE_TOKEN → env OH_CS_SB_TOKEN → KB OH_TEAM_READ_TOKEN.
Folosire (din plugins/<autor>/skills/<skill>/[scripts/]):
    sys.path.insert(0, <...>/plugins/core/scripts); import oh_citire as oh
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
KB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb.py")
ORDINE_CHEI = ("OH_TEAM_READ_TOKEN", "OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN")

_cheie: Optional[str] = None


class OhEroare(RuntimeError):
    """Order Hub nu a dat răspunsul (cheie lipsă, HTTP ≠ 200, rețea). Fără cheie în mesaj."""


def cheie() -> str:
    global _cheie
    if _cheie:
        return _cheie
    for k in ORDINE_CHEI:
        v = (os.environ.get(k) or "").strip()
        if v:
            _cheie = v
            return v
    try:
        v = subprocess.run(["uv", "run", KB, "secret-get", "OH_TEAM_READ_TOKEN"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120).stdout.strip()
    except Exception:
        v = ""
    if not v:
        raise OhEroare("lipsește cheia Order Hub (env OH_TEAM_READ_TOKEN / OH_PROFIT_CITIRE_TOKEN / OH_CS_SB_TOKEN "
                       "sau KB OH_TEAM_READ_TOKEN)")
    _cheie = v
    return v


def get(cale: str, params: Optional[Dict[str, Any]] = None, timeout: int = 300) -> Dict[str, Any]:
    """GET pe o rută OH → JSON. Ridică OhEroare la orice nereușită (inclusiv `ok: false`)."""
    p = {k: ("true" if v is True else "false" if v is False else v) for k, v in (params or {}).items() if v is not None}
    url = OH_BAZA + cale + (("?" + urllib.parse.urlencode(p)) if p else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cheie(), "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            corp = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                corp = gzip.decompress(corp)
    except urllib.error.HTTPError as e:
        try:
            detaliu = e.read()[:300].decode("utf-8", "replace")
        except Exception:
            detaliu = ""
        raise OhEroare("Order Hub %s → HTTP %s %s" % (cale, e.code, detaliu)) from None
    except Exception as e:
        raise OhEroare("Order Hub %s nu răspunde (%s)" % (cale, type(e).__name__)) from None
    d = json.loads(corp.decode("utf-8"))
    if isinstance(d, dict) and d.get("ok") is False:
        raise OhEroare("Order Hub %s → %s" % (cale, d.get("mesaj") or d.get("rezultat") or "ok=false"))
    return d


def livrabilitate(luna: str, magazin: Optional[str] = None) -> List[Dict[str, Any]]:
    """O comandă pe rând (dict pe coloanele rutei): comanda, total, moneda, judet, tara, stare, curier, ultim_status,
    transport_fara_tva, skus (`;`, fără cantitate), zi. stare ∈ Livrata/Refuzata/In curs de livrare/Netrimisa/Anulata."""
    d = get("/api/cs/livrabilitate", {"luna": luna, "magazin": magazin})
    col = d["coloane"]
    return [dict(zip(col, r)) for r in d["randuri"]]


def vanzari_pe_sku(zile: int, magazin: Optional[str] = None, cu_anulate: bool = False) -> Dict[str, Any]:
    """Bucăți PLASATE pe (SKU, magazin) în ultimele `zile` zile de București (azi inclus), ≤400."""
    return get("/api/cs/vanzari-pe-sku", {"zile": int(zile), "magazin": magazin, "cu_anulate": cu_anulate})


def comenzi_deschise(zile: int, cu_anulate: bool = False) -> List[Dict[str, Any]]:
    """Toate liniile comenzilor create în ultimele `zile` zile (≤120), toate paginile: {comanda, status, creata,
    fulfilled, sku, buc}; status în vocabularul aggregated_status AWBprint (cancelled doar cu cu_anulate)."""
    linii: List[Dict[str, Any]] = []
    dupa = None
    while True:
        d = get("/api/cs/comenzi-deschise", {"zile": int(zile), "cu_anulate": cu_anulate, "dupa": dupa,
                                             "limita": 50000}, timeout=600)
        linii.extend(d.get("linii") or [])
        dupa = d.get("urmator")
        if dupa is None:
            return linii


def produse(coduri: Iterable[str]) -> Dict[str, Any]:
    """`/api/produs?coduri=A,B` — produsul canonic pe fiecare cod (în loturi de 100)."""
    coduri = [c for c in dict.fromkeys(str(x).strip() for x in coduri) if c]
    out: Dict[str, Any] = {}
    for i in range(0, len(coduri), 100):
        d = get("/api/produs", {"coduri": ",".join(coduri[i:i + 100])})
        out.update(d if isinstance(d, dict) else {})
    return out
