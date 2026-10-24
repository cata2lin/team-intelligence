"""Umbra ponderilor: cine ia restul alocării (ca în stock-sync) și cât se mută cota."""
import ponderi_umbra as P

NUME = {"a": "A", "b": "B", "c": "C"}


def test_castigatorul_e_cel_mai_vandut_pe_30_zile():
    assert P.castigator({"a", "b"}, {"a": (10, 50), "b": (12, 20)}, None, NUME) == "b"


def test_histerezis_pastreaza_detinatorul_la_avans_mic():
    # b conduce cu 2 < 3 buc: a (deținătorul) păstrează bonusul, ca alocarea
    assert P.castigator({"a", "b"}, {"a": (10, 0), "b": (12, 0)}, "a", NUME) == "a"
    assert P.castigator({"a", "b"}, {"a": (10, 0), "b": (13, 0)}, "a", NUME) == "b"


def test_compara_marcheaza_mutarea_bonusului_si_cota():
    vechi = {"a": (30, 90), "b": (10, 30)}
    nou = {"a": (10, 30), "b": (30, 90)}
    randuri, tv, tn, mutat = P.compara(vechi, nou, {"a", "b"}, "a", 25, NUME)
    assert (tv, tn, mutat) == ("a", "b", 25)
    pp = {r["magazin"]: r["pp"] for r in randuri}
    assert pp == {"a": -50.0, "b": 50.0}


def test_fara_schimbare_nimic_mutat():
    v = {"a": (5, 5)}
    _, tv, tn, mutat = P.compara(v, dict(v), {"a"}, "a", 40, NUME)
    assert tv == tn == "a" and mutat == 0
