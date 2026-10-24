"""Garda push-ului de stoc (awb_to_stocksync): o tură care ar scrie stoc stricat NU scrie nimic.

De ce (24-oct-2026): după o repornire Orders (10:21–10:27 UTC), scanerul n-a răspuns la 1.137 de SKU-uri, iar stoc_unic a
ținut 5 minute în cache un instantaneu cu 377 de bucăți pe raft în loc de ~381.000. Un push care l-ar fi citit atunci ar fi
trimis ~0 pe toate magazinele. Plasele existente ale jobului (--max-delta pe SKU, --max-total-pct pe rulare) prind o
parte, dar nu un instantaneu degradat marcat ca atare și nici o scădere generală de 10–25%.

Activă pe AMBELE surse de rezervări (awbprint și oh). Refuză tura când:
  · răspunsul Orders e degradat (`ok: false` / `degradat: true` / `raft_esuat > 0`) sau e un instantaneu vechi din cache
    (`din_cache_vechi: true` cu `varsta_s` > 30 min);
  · raftul total sau ținta totală scade cu peste 10% față de ultima tură SCRISĂ;
  · peste 5% din mastere scad brusc: la 0 de la ≥ 10 buc, sau cu peste 50% de la ≥ 10 buc.
La refuz: nu se scrie nimic (stock-sync rămâne cu ultimele valori), pleacă alerta (briefing + log) și jobul iese cu cod ≠ 0,
ca heartbeat-ul să lipsească. O scădere reală (inventar) trece doar cu override explicit, cu motiv:
`--garda-override "motiv"` sau env `GARDA_PUSH_OVERRIDE="motiv"`.
"""
import json
import os
from datetime import datetime

STARE = "/root/Scripturi/data/garda_push_ultima.json"
PRAG_TOTAL = 0.10          # scădere maximă pe raft / pe ținta totală, față de ultima tură scrisă
PRAG_MASTERE = 0.05        # cota maximă de mastere care scad brusc
MIN_BUC_BRUSC = 10         # sub atâtea bucăți, o scădere nu e „bruscă" (zgomot de vânzare)
CACHE_VARSTA_MAX_S = 1800
COD_REFUZ = 4


class GardaRefuz(RuntimeError):
    """Tura nu scrie: `motive` spune de ce."""

    def __init__(self, motive):
        super().__init__("; ".join(motive))
        self.motive = list(motive)


def verifica_raspuns_oh(d):
    """Contractul Orders pentru instantaneu / rezervări (scripturi-ed, 24-oct, eecc6a5c): `ok: false` / `degradat: true` /
    `raft_esuat: N` / `motiv: "RAFT_NECITIT"` / `citire_degradata: {mastere, din, pct, degradat}`; snapshot din cache:
    `din_cache_vechi: true` + `varsta_s`. Ridică GardaRefuz pe degradat sau pe cache mai vechi de 30 min."""
    d = d or {}
    motive = []
    cd = d.get("citire_degradata") or {}
    if isinstance(cd, dict) and cd.get("degradat"):
        motive.append("Orders: citire degradată (%s mastere, %s%% din %s)" % (cd.get("mastere"), cd.get("pct"), cd.get("din")))
    if d.get("ok") is False or d.get("degradat"):
        motive.append("Orders a răspuns degradat (ok=%s, degradat=%s, raft_esuat=%s, motiv=%s)"
                      % (d.get("ok"), d.get("degradat"), d.get("raft_esuat"), d.get("motiv")))
    elif int(d.get("raft_esuat") or 0) > 0:
        motive.append("Orders: %s SKU-uri n-au putut fi citite din scaner" % d.get("raft_esuat"))
    if d.get("din_cache_vechi") and int(d.get("varsta_s") or 0) > CACHE_VARSTA_MAX_S:
        motive.append("Orders a servit un instantaneu vechi de %ss (max %ss)" % (d.get("varsta_s"), CACHE_VARSTA_MAX_S))
    if motive:
        raise GardaRefuz(motive)


def instantaneu(F, tinte):
    """Ce se compară între ture: raftul total și ținta pe fiecare master."""
    raft = sum(int(v.get("bart", 0)) + int(v.get("uz2", 0)) + int(v.get("teliu", 0)) for v in (F or {}).values())
    return {"raft_total": raft, "tinta_total": sum(int(x) for x in (tinte or {}).values()),
            "tinte": {str(k): int(v) for k, v in (tinte or {}).items()}}


def brusc(inainte, acum):
    return inainte >= MIN_BUC_BRUSC and (acum <= 0 or acum < inainte * 0.5)


def compara(prev, cur):
    """Motivele de refuz (listă goală = tura trece). Funcție pură. Fără tură anterioară = nimic de comparat."""
    if not prev:
        return []
    motive = []
    for cheie, nume in (("raft_total", "raftul total"), ("tinta_total", "ținta totală")):
        a, b = int(prev.get(cheie) or 0), int(cur.get(cheie) or 0)
        if a > 0 and (a - b) / a > PRAG_TOTAL:
            motive.append("%s scade cu %.1f%% (%d → %d; prag %d%%)" % (nume, 100.0 * (a - b) / a, a, b, PRAG_TOTAL * 100))
    pt, ct = prev.get("tinte") or {}, cur.get("tinte") or {}
    comune = [m for m in pt if int(pt[m]) > 0]
    if comune:
        jos = [m for m in comune if brusc(int(pt[m]), int(ct.get(m, 0)))]
        if len(jos) / len(comune) > PRAG_MASTERE:
            motive.append("%d din %d mastere (%.1f%%) scad brusc (la 0 sau cu peste 50%%, de la ≥ %d buc; prag %d%%)"
                          % (len(jos), len(comune), 100.0 * len(jos) / len(comune), MIN_BUC_BRUSC, PRAG_MASTERE * 100))
    return motive


def citeste_stare(cale=STARE):
    try:
        with open(cale) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def salveaza_stare(cur, eticheta, override=None, cale=STARE):
    """Doar după o tură SCRISĂ: următoarea se compară cu ce e acum în stock-sync, nu cu un dry-run."""
    d = dict(cur, eticheta=eticheta, cand=datetime.now().isoformat(timespec="seconds"), override=override)
    tmp = cale + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, cale)


def override_din(cli_motiv=None):
    m = (cli_motiv or os.environ.get("GARDA_PUSH_OVERRIDE") or "").strip()
    return m or None


def alerta(eticheta, motive, aplica):
    titlu = "Push stoc OPRIT de gardă (%s)" % eticheta
    corp = "\n".join(["Tura NU a scris nimic; stock-sync rămâne cu ultimele valori.", ""] + ["- " + m for m in motive]
                     + ["", "Dacă scăderea e reală (inventar), rulează din nou cu --garda-override \"motiv\"."])
    print("\n⛔ GARDA PUSH: " + "; ".join(motive))
    if aplica:
        try:
            import briefing_alert
            briefing_alert.scrie(titlu, corp)
        except Exception as e:  # noqa: BLE001 — alerta nu are voie să ascundă refuzul
            print("  ! briefing_alert: %s" % e)
