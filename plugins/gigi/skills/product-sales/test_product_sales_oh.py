"""fetch_store_oh: fereastra [from, to] = W(azi−from+1) − W(azi−to); gross = cu anulate, net = fără (fără rețea)."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import product_sales as ps  # noqa: E402


class FakeOh:
    class OhEroare(Exception):
        pass

    def __init__(self):
        self.cereri = []

    def vanzari_pe_sku(self, zile, magazin, cu_anulate=False):
        self.cereri.append((zile, magazin, cu_anulate))
        # 10 buc/zi plasate, 2 dintre ele anulate → net 8/zi; o comandă pe zi
        per = 10 if cu_anulate else 8
        return {"randuri": [{"sku": "HA-1", "bucati": per * zile, "comenzi": zile}], "taiat": False}


def test_fereastra_prin_diferenta(monkeypatch):
    f = FakeOh()
    monkeypatch.setattr(ps, "_oh", lambda: f)
    monkeypatch.setattr(ps, "resolve_store", lambda p: ("x.myshopify.com", "t"))
    monkeypatch.setattr(ps, "fetch_titles", lambda p: {"ha-1": "Produs"})
    prod, n = ps.fetch_store_oh("EST", "2026-10-01", "2026-10-10", today=date(2026, 10, 24))
    p = prod[("EST", "ha-1")]
    assert (p["gross"], p["net"], p["orders"]) == (100, 80, 10)
    assert {c[0] for c in f.cereri} == {24, 14}


def test_peste_400_zile_refuza(monkeypatch):
    monkeypatch.setattr(ps, "_oh", lambda: FakeOh())
    monkeypatch.setattr(ps, "resolve_store", lambda p: ("x.myshopify.com", "t"))
    try:
        ps.fetch_store_oh("EST", "2025-01-01", "2025-02-01", today=date(2026, 10, 24))
    except SystemExit as e:
        assert "400" in str(e)
    else:
        raise AssertionError("trebuia SystemExit")
