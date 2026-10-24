"""Venitul MTD din Order Hub: magazinul absent din OH = „—" (nu eroare), orice altă eroare OH = oprire (fără rețea)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spend_pacing as sp  # noqa: E402

sys.path.insert(0, sp.CORE)
import oh_citire as oh  # noqa: E402


def test_magazin_absent_si_eroare(monkeypatch):
    def fake(luna, shop):
        if shop == oh.PUBLIC_TO_SHOP["esteban.ro"]:
            return [{"total": 100, "moneda": "RON"}, {"total": 50, "moneda": "EUR"}, {"total": 20, "moneda": None}]
        raise oh.OhEroare("Order Hub /api/cs/livrabilitate → HTTP 500 eroare: Necunoscuta")
    monkeypatch.setattr(oh, "livrabilitate", fake)
    assert sp.revenue_oh("2026-10", ["esteban.ro", "belasil.ro", "tt.cont"]) == {"esteban.ro": 120.0}

    monkeypatch.setattr(oh, "livrabilitate", lambda l, s: (_ for _ in ()).throw(oh.OhEroare("HTTP 502")))
    try:
        sp.revenue_oh("2026-10", ["esteban.ro"])
    except oh.OhEroare:
        pass
    else:
        raise AssertionError("trebuia OhEroare")
