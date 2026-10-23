# -*- coding: utf-8 -*-
"""Regula S2 într-un singur loc: lotul din Order Hub se folosește DOAR dacă are navlu.

De ce: un lot cu transport = 0 are doar marfa. Preferat orbește, a scăzut COGS-ul cu −22.084 lei pe
10 branduri (23-oct-2026), ascuns în agregat de Grandia +17.127 și DUPBG +4.674. Engine-ul, P&L-ul
Grandia și profit_lines_sync citesc toate prin profit_core — testul ține regula pentru toți.

Rulare: uv run --with pytest python -m pytest test_profit_core_lot_navlu.py -q
"""
import datetime as dt

import profit_core as pc

D = dt.date


def test_lot_cu_navlu_se_foloseste_la_data_comenzii():
    loturi = [(D(2026, 10, 1), 30.0, True), (D(2026, 8, 1), 20.0, True)]
    assert pc.cost_lot_la(loturi, D(2026, 9, 15)) == 20.0
    assert pc.cost_lot_la(loturi, "2026-10-05") == 30.0       # text ISO, ca în engine


def test_lot_fara_navlu_nu_se_foloseste_si_nu_sare_pe_unul_mai_vechi():
    loturi = [(D(2026, 10, 1), 30.0, False), (D(2026, 8, 1), 20.0, True)]
    # lotul valabil (1-oct) n-are navlu: cădem pe costul vechi, NU pe lotul din august (prețul altei luni)
    assert pc.cost_lot_la(loturi, D(2026, 10, 5)) is None
    assert pc.cost_lot_la(loturi, D(2026, 9, 1)) == 20.0
    assert pc.cost_lot_la(loturi, D(2026, 10, 5), doar_cu_navlu=False) == 30.0


def test_lot_vechi_fara_campul_navlu_conteaza_fara_navlu():
    assert pc.cost_lot_la([(D(2026, 9, 1), 25.0)], D(2026, 9, 2)) is None   # „nu știu" ≠ „are"


def test_comanda_mai_veche_decat_primul_lot_si_fara_data():
    loturi = [(D(2026, 10, 1), 30.0, True)]
    assert pc.cost_lot_la(loturi, D(2026, 9, 1)) is None
    assert pc.cost_lot_la(loturi, None) == 30.0
    assert pc.cost_lot_la([], D(2026, 9, 1)) is None


def test_precedenta_pe_linie_ca_engine_ul():
    loturi = {"ha-1": [(D(2026, 9, 1), 10.0, True)], "ha-2": [(D(2026, 9, 1), 10.0, False)]}
    ov = {"HA-3": 7.0}
    assert pc.cost_unitar_linie("HA-1", "2026-09-10", loturi, ov, 99.0) == (10.0 * 1.21, "lot")
    assert pc.cost_unitar_linie("HA-2", "2026-09-10", loturi, ov, 99.0) == (99.0, "shopify")
    assert pc.cost_unitar_linie("HA-3", "2026-09-10", loturi, ov, 99.0) == (7.0, "override")
    assert pc.cost_unitar_linie("HA-4", "2026-09-10", loturi, ov, None) == (None, "lipsa")
    assert pc.cost_unitar_linie("HA-1", "2026-09-10", {}, ov, 99.0) == (99.0, "shopify")  # sursa OH oprită


def test_403_nu_se_inghite_ca_fara_navlu(monkeypatch):
    """O cheie fără scopul cs.citire (403) trebuie să OPREASCĂ, nu să dea „fără loturi" (= Shopify tăcut)."""
    import io
    import urllib.error
    import urllib.request

    import pytest

    def _403(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", _403)
    with pytest.raises(PermissionError):
        pc.cost_loturi_oh(["HA-1"], "cheie-gresita", "x.myshopify.com")
