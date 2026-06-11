# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
awb_track.py — Tracker live multi-curier pentru AWB-uri (DPD, Sameday, Econt, Packeta).

Lipești unul sau mai multe numere AWB și primești statusul curent al fiecărui colet,
cu auto-detectare a curierului din forma AWB-ului. Statusul brut de la curier este
normalizat în 4 stări: delivered / in_transit / returned / refused (+ unknown/error),
iar coletele cu probleme (returnate/refuzate/eroare) sunt marcate.

Read-only: doar interogări de tracking, nu scrie nicăieri.

Curieri implementați LIVE: DPD (api.dpd.ro/v1/track), Sameday (api.sameday.ro),
Econt (ee.econt.com), Packeta (zasilkovna XML). DPD + Sameday sunt cei mai folosiți
în volum; toți patru sunt funcționali dacă există credențiale.

Credențiale: din KB, secretul COURIER_CREDS_JSON (un singur JSON cu dpd_creds /
sameday_creds / econt_creds / packeta_creds). Fallback pe variabile de mediu
COURIER_CREDS_JSON, sau DPD_RO_USERNAME / DPD_RO_PASSWORD pentru DPD.

Folosire:
  uv run awb_track.py --awb "81304028147,1ONBLN504748204,Z4944525695"
  uv run awb_track.py --awb "81302362807" --courier dpd      # forțează curierul
  uv run awb_track.py --awb-file lista.txt                    # un AWB pe linie / separate prin , ; newline
  uv run awb_track.py --awb "..." --json                      # output JSON
  uv run awb_track.py --awb "..." --problems                  # doar coletele cu probleme
"""
import argparse
import asyncio
import html
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx


# ─────────────────────────── credențiale (KB / env) ───────────────────────────

def _kb_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "..", "core", "scripts", "kb.py")


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
    return "necunoscut"


_COURIER_ALIASES = {
    "dpd": "dpd-ro", "dpd-ro": "dpd-ro", "dpdro": "dpd-ro",
    "dpd-jg": "dpd-jg", "dpd-px": "dpd-px",
    "sameday": "sameday", "sd": "sameday",
    "econt": "econt",
    "packeta": "packeta", "zasilkovna": "packeta",
}


# ─────────────────────────── normalizare status ───────────────────────────
# Mapăm textul brut (RO/EN) într-una din: delivered / in_transit / returned / refused

_DELIVERED = [
    "delivered", "livrat", "livrare efectuata", "livrare finalizata",
    "delivery successful", "colet livrat", "predat destinatarului",
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
# stări care NU sunt livrare deși conțin cuvinte ambigue (ridicare eșuată etc.)
_NOT_DELIVERED = [
    "ridicarea nu a avut loc", "nu a fost predat", "nu a fost preluat",
    "nu a putut fi livrat", "livrare esuata", "livrarea a esuat",
]
_GENERATED = [
    "awb generat", "shipment data received", "shipment registered",
    "order received", "registered", "awb creat", "data received",
    "informatii primite",
]


def normalize_status(raw: str) -> str:
    """Întoarce: delivered / returned / refused / in_transit / generated / unknown / error."""
    s = _deacc((raw or "").strip().lower())
    if not s:
        return "unknown"
    if s.startswith("eroare") or s.startswith("error") or "credentiale" in s or "lipsa" in s:
        return "error"
    if "awb invalid" in s or "invalid" in s:
        return "invalid"
    # ordinea contează: refused & returned înainte de delivered/in_transit
    if any(k in s for k in _REFUSED):
        return "refused"
    if any(k in s for k in _RETURNED):
        return "returned"
    if any(k in s for k in _DELIVERED):
        return "delivered"
    if any(k in s for k in _GENERATED):
        return "generated"
    return "in_transit"


_PROBLEM = {"refused", "returned", "error", "invalid"}


def _deacc(s: str) -> str:
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"),
                 ("ț", "t"), ("ţ", "t"), ("Ă", "a"), ("Â", "a"), ("Î", "i")):
        s = s.replace(a, b)
    return s


# ─────────────────────────── trackere live (async) ───────────────────────────

async def track_dpd(client: httpx.AsyncClient, awbs, creds) -> dict:
    """Track DPD în batch-uri de 10 prin api.dpd.ro/v1/track."""
    out = {}
    if not creds or not creds.get("username") or not creds.get("password"):
        for a in awbs:
            out[a] = "Credentiale DPD lipsa"
        return out
    uniq = list(dict.fromkeys(awbs))
    for i in range(0, len(uniq), 10):
        batch = uniq[i:i + 10]
        try:
            r = await client.post("https://api.dpd.ro/v1/track", json={
                "userName": creds["username"], "password": creds["password"],
                "language": "EN", "lastOperationOnly": False,
                "parcels": [{"id": a} for a in batch],
            }, timeout=30.0)
            if r.status_code != 200:
                for a in batch:
                    out[a] = f"Eroare HTTP DPD {r.status_code}"
                continue
            data = r.json()
            if data.get("error"):
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                for a in batch:
                    out[a] = msg or "Eroare DPD"
                continue
            parcels = data.get("parcels") or []
            for awb, parcel in zip(batch, parcels):
                if parcel.get("error"):
                    pe = parcel["error"]
                    out[awb] = (pe.get("message", "") if isinstance(pe, dict) else str(pe)) or "Eroare DPD"
                    continue
                ops = parcel.get("operations") or []
                if not ops:
                    out[awb] = "AWB Generat"
                    continue
                filt = [o for o in ops if not (o.get("description") or "").startswith("Too many")]
                if not filt:
                    filt = ops
                latest = max(filt, key=lambda o: o.get("dateTime", ""))
                out[awb] = (latest.get("description") or "").strip() or "Status necunoscut"
            for awb in batch[len(parcels):]:
                out.setdefault(awb, "Fara date DPD")
        except Exception:
            for a in batch:
                out[a] = "Eroare API DPD"
        await asyncio.sleep(0.2)
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


async def track_sameday(client, awb, creds) -> str:
    token = await _sameday_auth(client, creds)
    if not token:
        return "Eroare autentificare Sameday"
    try:
        r = await client.get(f"https://api.sameday.ro/api/client/awb/{awb}/status",
                             headers={"X-AUTH-TOKEN": token}, timeout=20.0)
        if r.status_code == 404:
            return "AWB Invalid"
        r.raise_for_status()
        data = r.json()
        hist = data.get("expeditionHistory") or []
        if not hist:
            return "AWB Generat"
        last = max(hist, key=lambda e: e.get("statusDate") or e.get("date") or "")
        return last.get("statusLabel") or "Status Necunoscut"
    except Exception:
        return "Eroare API Sameday"


async def track_econt(client, awb, creds) -> str:
    if not creds or not creds.get("username"):
        return "Credentiale Econt lipsa"
    try:
        r = await client.post(
            "https://ee.econt.com/services/Shipments/ShipmentService.getShipmentStatuses.json",
            json={"username": creds.get("username"), "password": creds.get("password"),
                  "shipmentNumbers": [awb]}, timeout=20.0)
        txt = r.text or ""
        if r.status_code in (517, 400) and "ExInvalidShipmentNum" in txt:
            return "AWB Invalid"
        r.raise_for_status()
        data = r.json()
        st = ((data.get("shipmentStatuses") or [{}])[0].get("status") or {})
        desc = st.get("shortDeliveryStatusEn") or st.get("shortDeliveryStatusRo") or "In transit"
        return str(desc).strip().title()
    except Exception:
        return "Eroare API Econt"


async def track_packeta(client, awb, creds) -> str:
    api_pw = (creds or {}).get("api_password", "").strip()
    if not api_pw:
        return "Credentiale Packeta lipsa"
    base = (creds.get("base_url") or "").strip() or "https://www.zasilkovna.cz/api/rest"
    pid = awb[1:] if awb.upper().startswith("Z") else awb
    body = (f'<?xml version="1.0" encoding="utf-8"?>\n<packetTracking>'
            f'<apiPassword>{api_pw}</apiPassword><packetId>{pid}</packetId></packetTracking>')
    try:
        r = await client.post(base, content=body.encode("utf-8"),
                             headers={"Content-Type": "text/xml; charset=utf-8",
                                      "Accept-Language": "ro_RO"}, timeout=20.0)
        if r.status_code != 200:
            return f"Eroare HTTP Packeta {r.status_code}"
        txt = r.text or ""
        if "<fault>" in txt.lower() and "wrong password" in txt.lower():
            return "Credentiale Packeta invalide"
        statuses = re.findall(r'<statusText[^>]*>(.*?)</statusText>', txt, re.DOTALL)
        if statuses:
            return html.unescape(statuses[-1].strip())
        # fallback: numele statusului
        names = re.findall(r'<statusName[^>]*>(.*?)</statusName>', txt, re.DOTALL)
        if names:
            return html.unescape(names[-1].strip())
        return "AWB Generat"
    except Exception:
        return "Eroare API Packeta"


async def track_all(items, creds):
    """items: listă de dict {awb, courier_key}. Întoarce {awb: status_raw}."""
    dpd_creds = creds.get("dpd_creds") or {}
    sd_creds = creds.get("sameday_creds") or {}
    ec_creds = creds.get("econt_creds") or {}
    pk_creds = creds.get("packeta_creds") or {}

    dpd_groups = defaultdict(list)
    sd, ec, pk, unknown = [], [], [], []
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
        else:
            unknown.append(a)

    out = {}
    async with httpx.AsyncClient(timeout=45.0) as client:
        tasks = []

        # DPD pe profile (batch-uri secvențiale în interiorul fiecărui profil)
        for dpd_key, awbs in dpd_groups.items():
            dc = dpd_creds.get(dpd_key) or dpd_creds.get("dpd-ro") or {}
            tasks.append(_dpd_into(out, client, awbs, dc))

        sem = asyncio.Semaphore(20)

        async def _one(awb, fn, c):
            async with sem:
                out[awb] = await fn(client, awb, c)

        for a in sd:
            tasks.append(_one(a, track_sameday, sd_creds))
        for a in ec:
            tasks.append(_one(a, track_econt, ec_creds))
        for a in pk:
            tasks.append(_one(a, track_packeta, pk_creds))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    for a in unknown:
        out[a] = "Curier necunoscut"
    return out


async def _dpd_into(out, client, awbs, creds):
    res = await track_dpd(client, awbs, creds)
    out.update(res)


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
    "error": "EROARE", "invalid": "AWB INVALID",
}
_FLAG = {"refused": "⚠", "returned": "⚠", "error": "✗", "invalid": "✗"}


def build_rows(items, status_map):
    rows = []
    for it in items:
        awb = it["awb"]
        ck = it["courier_key"]
        raw = status_map.get(awb, "—") if ck != "necunoscut" else "Curier necunoscut"
        norm = normalize_status(raw) if ck != "necunoscut" else "unknown"
        rows.append({
            "awb": awb,
            "courier": ("?" if ck == "necunoscut" else ck.upper()),
            "courier_key": ck,
            "status_raw": raw,
            "status": norm,
            "status_ro": _NORM_RO.get(norm, norm.upper()),
            "problem": norm in _PROBLEM,
        })
    return rows


def print_table(rows):
    if not rows:
        print("Niciun AWB de procesat.")
        return
    print("=== AWB tracker — %d colete ===" % len(rows))
    print("%-17s %-9s %-13s %-7s %s" % ("AWB", "CURIER", "STATUS", "FLAG", "detaliu curier"))
    print("-" * 92)
    for r in rows:
        flag = _FLAG.get(r["status"], "")
        print("%-17s %-9s %-13s %-7s %s" % (
            r["awb"][:17], r["courier"][:9], r["status_ro"][:13], flag,
            (r["status_raw"] or "")[:42]))
    # rezumat
    by = defaultdict(int)
    for r in rows:
        by[r["status"]] += 1
    probs = [r for r in rows if r["problem"]]
    print("-" * 92)
    summary = "  ".join("%s=%d" % (_NORM_RO.get(k, k.upper()), v)
                        for k, v in sorted(by.items(), key=lambda x: -x[1]))
    print("Rezumat: " + summary)
    if probs:
        print("⚠ %d colete cu probleme:" % len(probs))
        for r in probs:
            print("   - %s [%s] -> %s (%s)" % (r["awb"], r["courier"],
                                               r["status_ro"], r["status_raw"]))
    else:
        print("✓ Niciun colet cu probleme (returnat/refuzat/eroare).")


def main():
    ap = argparse.ArgumentParser(description="Tracker live multi-curier AWB (DPD/Sameday/Econt/Packeta).")
    ap.add_argument("--awb", default="", help="Unul sau mai multe AWB-uri separate prin , ; spațiu / newline.")
    ap.add_argument("--awb-file", default="", help="Fișier cu AWB-uri (unul pe linie / separate).")
    ap.add_argument("--courier", default="", help="Forțează curierul pentru TOATE AWB-urile (dpd|sameday|econt|packeta).")
    ap.add_argument("--problems", action="store_true", help="Afișează doar coletele cu probleme.")
    ap.add_argument("--json", action="store_true", help="Output JSON în loc de tabel.")
    a = ap.parse_args()

    awbs = parse_awbs(a.awb)
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
    status_map = asyncio.run(track_all(items, creds))
    rows = build_rows(items, status_map)

    if a.problems:
        rows = [r for r in rows if r["problem"]]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
