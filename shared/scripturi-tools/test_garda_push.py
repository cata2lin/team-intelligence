"""garda_push: o tură cu instantaneu stricat sau stoc prăbușit nu scrie; o tură normală trece; override-ul trece."""
import pytest

import garda_push as G


def _inst(n_mastere=100, buc=400):
    F = {"sku%d" % i: {"bart": buc, "uz2": 0, "teliu": 0} for i in range(n_mastere)}
    return G.instantaneu(F, {"m%d" % i: buc for i in range(n_mastere)})


def test_instantaneu_stricat_de_24_oct_raft_377_e_refuzat():
    prev = _inst()                                  # ~40.000 pe raft
    F = {"sku0": {"bart": 377, "uz2": 0, "teliu": 0}}
    cur = G.instantaneu(F, {"m0": 377})
    motive = G.compara(prev, cur)
    assert any("raftul total" in m for m in motive) and any("ținta totală" in m for m in motive)
    assert any("mastere" in m for m in motive)       # 99 din 100 de mastere la 0


def test_scadere_de_12_la_suta_pe_total_e_refuzata():
    prev = _inst(buc=400)
    cur = _inst(buc=352)                             # −12% pe fiecare, nicio scădere „bruscă"
    motive = G.compara(prev, cur)
    assert len(motive) == 2 and all("12.0%" in m for m in motive)


def test_6_la_suta_din_mastere_la_zero_e_refuzat_chiar_daca_totalul_tine():
    prev = _inst(n_mastere=100, buc=400)
    cur = _inst(n_mastere=100, buc=400)
    for i in range(6):
        cur["tinte"]["m%d" % i] = 0
    cur["tinta_total"] = sum(cur["tinte"].values())  # −6% pe total: sub pragul de 10%
    motive = G.compara(prev, cur)
    assert motive == [m for m in motive if "mastere" in m] and len(motive) == 1


def test_tura_normala_trece():
    prev = _inst(buc=400)
    cur = _inst(buc=395)                             # vânzări obișnuite
    cur["tinte"]["m1"] = 0                           # un master epuizat: 1% din mastere
    assert G.compara(prev, cur) == []


def test_fara_tura_anterioara_nu_refuza():
    assert G.compara(None, _inst()) == []


def test_override(monkeypatch):
    monkeypatch.delenv("GARDA_PUSH_OVERRIDE", raising=False)
    assert G.override_din(None) is None
    assert G.override_din("inventar Bartolomeu 27-oct") == "inventar Bartolomeu 27-oct"
    monkeypatch.setenv("GARDA_PUSH_OVERRIDE", "inventar")
    assert G.override_din(None) == "inventar"


@pytest.mark.parametrize("d", [{"ok": False, "degradat": True, "raft_esuat": 1137},
                               {"ok": True, "raft_esuat": 3},
                               {"ok": True, "din_cache_vechi": True, "varsta_s": 2400},
                               {"ok": False, "degradat": True, "raft_esuat": 1137, "motiv": "RAFT_NECITIT"},
                               {"ok": True, "citire_degradata": {"mastere": 1137, "din": 2012, "pct": 56.5, "degradat": True}}])
def test_raspuns_orders_degradat_sau_vechi_e_refuzat(d):
    with pytest.raises(G.GardaRefuz):
        G.verifica_raspuns_oh(d)


def test_raspuns_orders_bun_sau_cache_proaspat_trece():
    G.verifica_raspuns_oh({"ok": True, "linii": []})
    G.verifica_raspuns_oh({"ok": True, "din_cache_vechi": True, "varsta_s": 600,
                           "citire_degradata": {"mastere": 0, "din": 2012, "pct": 0, "degradat": False}})


def test_stare_se_scrie_si_se_citeste(tmp_path):
    cale = str(tmp_path / "s.json")
    G.salveaza_stare(_inst(), "uzina2", None, cale=cale)
    s = G.citeste_stare(cale)
    assert s["eticheta"] == "uzina2" and s["raft_total"] == 40000
