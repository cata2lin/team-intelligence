# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""Test de regresie pentru awb_track.py — pe fixture ÎNREGISTRATE din rulări reale (DPD live,
Econt/Packeta/Dragon Star pe AWB inexistent), fără nicio cerere de rețea.

ASERȚIUNEA CENTRALĂ, din care ies toate testele: **niciun AWB nu primește `delivered` /
`in_transit` / `returned` fără o operație reală pe ACEL `parcelId`.** Fiecare test de mai jos
reproduce una din căile prin care asta se încălca înainte de fixul din 2026-08-26.

  uv run test_awb_track.py
"""
import asyncio
import sys

import awb_track as T


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self.status_code = status
        self._p = payload
        self.text = text

    def json(self):
        if self._p is None:
            raise ValueError("nu e JSON")
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


class _Client:
    """Client fals: întoarce pe rând răspunsurile din listă (ultimul se repetă)."""

    def __init__(self, *responses):
        self._r = list(responses)
        self.calls = 0

    async def post(self, *a, **k):
        self.calls += 1
        return self._r[min(self.calls - 1, len(self._r) - 1)]

    async def get(self, *a, **k):
        self.calls += 1
        return self._r[min(self.calls - 1, len(self._r) - 1)]


CREDS = {"username": "u", "password": "p"}
FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _op(code, desc, dt="2026-08-20T10:00:00+0300"):
    return {"operationCode": code, "description": desc, "dateTime": dt}


# ── fixture 1: rate-limit (HTTP 200 + error top-level + parcels GOL) ─────────────────
# Aici pica raportul: mesajul devenea status brut pentru TOT lotul și cădea pe in_transit.
RATE_LIMIT = {"error": {"context": "method_threshold.too_many_requests.tracking.second",
                        "message": "Too many tracking requests (5) per second."},
              "parcels": []}
# ── fixture 2: credențială moartă (tot HTTP 200 + error top-level) ──────────────────
DEAD_CREDS = {"error": {"context": "authentication", "message": "Unable to find user to authenticate!"},
              "parcels": []}


def t_rate_limit_never_becomes_status():
    T._DPD_STATS.update(requests=0, rate_limited=0, recovered=0, exhausted=0)
    cl = _Client(_Resp(RATE_LIMIT))
    T._dpd_throttle = None
    out = asyncio.run(T.track_dpd(cl, ["81100000001", "81100000002"], CREDS, batch_size=50, rps=50))
    cats = {a: T.classify(r) for a, r in out.items()}
    check("rate-limit epuizat -> EROARE, nu status", set(cats.values()) == {"error"}, str(cats))
    check("rate-limit: s-a retrimis (retry cu backoff)", cl.calls == T.DPD_MAX_TRIES,
          "cereri=%d, asteptat=%d" % (cl.calls, T.DPD_MAX_TRIES))
    check("rate-limit: loturi pierdute contorizate", T._DPD_STATS["exhausted"] == 1)


def t_rate_limit_then_success():
    T._DPD_STATS.update(requests=0, rate_limited=0, recovered=0, exhausted=0)
    good = {"parcels": [{"parcelId": "81100000001", "operations": [_op(-14, "Delivered")]}]}
    cl = _Client(_Resp(RATE_LIMIT), _Resp(good))
    T._dpd_throttle = None
    out = asyncio.run(T.track_dpd(cl, ["81100000001"], CREDS, batch_size=50, rps=50))
    check("retry recuperează lotul", T.classify(out["81100000001"]) == "delivered")
    check("recuperarea e contorizată", T._DPD_STATS["recovered"] == 1)


def t_dead_creds_never_becomes_status():
    cl = _Client(_Resp(DEAD_CREDS))
    T._dpd_throttle = None
    out = asyncio.run(T.track_dpd(cl, ["81100000001", "81100000002"], CREDS, batch_size=50, rps=50))
    check("credențială moartă -> EROARE, nu status",
          all(T.classify(r) == "error" for r in out.values()))
    check("credențială moartă: NU se retrimite (cont partajat)", cl.calls == 1,
          "cereri=%d" % cl.calls)


def t_missing_parcel_in_the_middle():
    """DPD OMITE tăcut coletul necunoscut. `zip(batch, parcels)` decala statusurile de-aici încolo."""
    payload = {"parcels": [
        {"parcelId": "81100000001", "operations": [_op(-14, "Delivered")]},
        # 81100000002 (necunoscut de DPD) LIPSEȘTE — fără eroare, fără element gol
        {"parcelId": "81100000003", "operations": [_op(124, "Delivered Back to Sender")]},
    ]}
    cl = _Client(_Resp(payload))
    T._dpd_throttle = None
    out = asyncio.run(T.track_dpd(cl, ["81100000001", "81100000002", "81100000003"],
                                  CREDS, batch_size=50, rps=50))
    cats = {a: T.classify(r) for a, r in out.items()}
    check("colet lipsă la MIJLOC -> not_found", cats["81100000002"] == "not_found", str(cats))
    check("vecinii NU alunecă (mapare pe parcelId)",
          cats["81100000001"] == "delivered" and cats["81100000003"] == "returned", str(cats))
    check("AWB inexistent NU fură statusul altui colet",
          out["81100000002"]["raw"] is None)


def t_operation_codes():
    check("129 Administrative Closure = TERMINAL (closed), nu tranzit", T._DPD_OP[129] == "closed")
    check("closed cere ochi de om (în _PROBLEM)", "closed" in T._PROBLEM)
    check("121 Stopped by sender = stopped", T._DPD_OP[121] == "stopped")
    check("38 Returned to Office = TRANZIT (intermediar), nu retur", T._DPD_OP[38] == "in_transit")
    check("124 Delivered Back to Sender = retur", T._DPD_OP[124] == "returned")
    check("codul BATE textul: 'Administrative Closure' pe cod 129",
          T.classify(T._ok("Administrative Closure", 129, "dpd")) == "closed")
    check("text 'Returned to Office' fără cod -> tranzit, nu retur",
          T.normalize_status("Returned to Office") == "in_transit")


def t_no_implicit_in_transit():
    for txt in ["Too many tracking requests (5) per second.",
                "Unable to find user to authenticate!",
                "Fara date DPD", "Status necunoscut", "—", "wibble wobble"]:
        check("text nerecunoscut -> unknown, nu in_transit: %r" % txt[:34],
              T.normalize_status(txt) == "unknown", T.normalize_status(txt))
    check("fără rezultat de transport -> error", T.classify(None) == "error")
    check("unknown/not_found/error/invalid sunt PROBLEME",
          {"unknown", "not_found", "error", "invalid"} <= T._PROBLEM)


def t_other_couriers():
    T._dpd_throttle = None
    # Econt: răspuns fără status (AWB inexistent). Era `or "In transit"` HARDCODAT.
    r = asyncio.run(T.track_econt(_Client(_Resp({"shipmentStatuses": [{"status": {}}]})),
                                  "1050000000", {"username": "u", "password": "p"}))
    check("Econt fără status -> not_found, nu 'In transit'", T.classify(r) == "not_found", str(r))
    # Packeta: XML fără statusText. Era `return "AWB Generat"`.
    r = asyncio.run(T.track_packeta(_Client(_Resp(None, 200, "<result><status>ok</status></result>")),
                                    "Z9999999999", {"api_password": "x"}))
    check("Packeta fără statusText -> not_found, nu 'AWB Generat'", T.classify(r) == "not_found", str(r))
    # Dragon Star: regexul nu potrivește (AWB inexistent SAU pagină schimbată).
    r = asyncio.run(T.track_dragonstar(_Client(_Resp(None, 200, "<html>nimic</html>")), "99999999"))
    check("Dragon Star fără status -> not_found, nu in_transit", T.classify(r) == "not_found", str(r))
    # Sameday: operație fără statusLabel. Era „Status Necunoscut" -> in_transit.
    T._sameday_token["u"] = {"token": "t", "exp": T.datetime.now(T.timezone.utc) + T.timedelta(minutes=5)}
    r = asyncio.run(T.track_sameday(_Client(_Resp({"expeditionHistory": [{"statusDate": "2026-08-01"}]})),
                                    "1ONBLN1", {"username": "u", "password": "p"}))
    check("Sameday fără statusLabel -> error, nu in_transit", T.classify(r) == "error", str(r))


def t_summary_is_honest():
    """Rezumatul nu are voie să tacă atunci când au existat erori."""
    items = [{"awb": "A", "courier_key": "dpd-ro"}, {"awb": "B", "courier_key": "dpd-ro"}]
    res = {"A": T._ok("Delivered", -14, "dpd"), "B": T._fail("error", "Rate-limit DPD", "dpd")}
    rows = T.build_rows(items, res)
    s = T.summarize(rows)
    check("AWB cu eroare intră în NEVERIFICATE", len(s["unresolved"]) == 1)
    check("AWB cu eroare intră în PROBLEME (deci nu se mai scrie '✓ Niciun colet cu probleme')",
          len(s["problems"]) == 1)
    # AWB fără nicio intrare în res_map (excepție înghițită de gather) -> error, nu „—" -> in_transit
    rows2 = T.build_rows([{"awb": "C", "courier_key": "dpd-ro"}], {})
    check("AWB fără rezultat intern -> error", rows2[0]["status"] == "error")
    check("JSON păstrează cheile vechi + adaugă ok/err/source",
          {"status", "status_ro", "problem", "ok", "err", "source"} <= set(rows2[0]))


def main():
    for fn in (t_rate_limit_never_becomes_status, t_rate_limit_then_success,
               t_dead_creds_never_becomes_status, t_missing_parcel_in_the_middle,
               t_operation_codes, t_no_implicit_in_transit, t_other_couriers,
               t_summary_is_honest):
        print(fn.__name__)
        fn()
    print("\n%s — %d test(e) picate" % ("PICAT" if FAILS else "TOATE TRECUTE", len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
