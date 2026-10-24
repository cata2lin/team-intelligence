"""Rata de livrare pe canal: săptămâni mature și alarma de scădere peste 5 puncte."""
from datetime import date

import livrare_pe_canal as L


def _r(zi, stare, canal="tiktok"):
    return {"zi": zi, "stare": stare, "canal": canal}


def test_saptamana_imatura_nu_intra():
    azi = date(2026, 10, 24)
    s = L.pe_saptamana([_r("2026-10-20", "Livrata"), _r("2026-09-28", "Refuzata")], azi=azi)
    assert ("2026-10-19", "tiktok") not in s and s[("2026-09-28", "tiktok")] == [0, 1]


def test_alarma_cand_canalul_scade_peste_prag():
    sapt = {}
    for i, lun in enumerate(["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21"]):
        sapt[(lun, "tiktok")] = [80, 20]
        sapt[(lun, "meta")] = [80, 20]
    sapt[("2026-09-28", "tiktok")] = [70, 30]     # 70% vs 80%: −10 pp
    sapt[("2026-09-28", "meta")] = [78, 22]       # −2 pp: sub prag
    al, _ = L.alarme(sapt)
    assert [a[0] for a in al] == ["tiktok"] and al[0][3] == -10.0


def test_rata():
    assert L.rata(3, 1) == 75.0 and L.rata(0, 0) is None


def test_comision_real_regula_estimat():
    assert L.comision("trendyol", {"trendyol": 0.134}) == (0.134, "real")
    assert L.comision("emag") == (0.17, "regulă") and L.comision("trendyol") == (0.18, "regulă")
    assert L.comision("olx") == (0.17, "estimat") and L.comision("cel")[1] == "estimat"
