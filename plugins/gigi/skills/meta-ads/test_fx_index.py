# -*- coding: utf-8 -*-
"""Cursul pe zi din meta.py și tiktok.py: sursa implicită e metrics.fx_rates, AWBprint doar la FX_SURSA=awb.

De ce: AWBprint rămâne fără cititori de pe laptopuri. Testul nu lovește nicio bază: înlocuiește
cititoarele de serii și verifică alegerea sursei + forward-fill-ul peste weekend/sărbătoare.

Rulare (din plugins/gigi/skills): uv run --with pytest --with psycopg2-binary --with requests python -m pytest meta-ads/test_fx_index.py -q
"""
import datetime as dt
import importlib
import sys
from pathlib import Path

import pytest

AICI = Path(__file__).resolve().parent


def _mod(nume, dosar):
    sys.path.insert(0, str(AICI.parent / dosar))
    try:
        return importlib.import_module(nume)
    finally:
        sys.path.pop(0)


MODULE = [("meta", "meta-ads"), ("tiktok", "tiktok-ads")]
# vineri 29-mai, luni 1-iun = Rusalii (fără curs BNR), marți 2-iun
SERIE = [("USD", dt.date(2026, 5, 29), 4.5099), ("USD", dt.date(2026, 6, 2), 4.5142)]


@pytest.mark.parametrize("nume,dosar", MODULE)
def test_implicit_metrics_si_forward_fill(nume, dosar, monkeypatch):
    m = _mod(nume, dosar)
    chemat = []
    monkeypatch.delenv("FX_SURSA", raising=False)
    monkeypatch.setattr(m, "_fx_series_metrics", lambda need, lo, hi: chemat.append("metrics") or SERIE)
    monkeypatch.setattr(m, "_fx_series_awb", lambda need, lo, hi: pytest.fail("AWBprint citit fără FX_SURSA=awb"))
    idx = m.fx_index(["USD", "RON"], "2026-05-30", "2026-06-02")
    assert chemat == ["metrics"]
    assert idx[("USD", dt.date(2026, 5, 30))] == 4.5099   # sâmbătă
    assert idx[("USD", dt.date(2026, 6, 1))] == 4.5099    # sărbătoare
    assert idx[("USD", dt.date(2026, 6, 2))] == 4.5142
    assert m.conv(10, "USD", dt.date(2026, 6, 1), idx) == pytest.approx(45.099)
    assert m.conv(10, "RON", dt.date(2026, 6, 1), idx) == 10


@pytest.mark.parametrize("nume,dosar", MODULE)
def test_awb_doar_la_cerere(nume, dosar, monkeypatch):
    m = _mod(nume, dosar)
    monkeypatch.setenv("FX_SURSA", "awb")
    monkeypatch.setattr(m, "_fx_series_metrics", lambda need, lo, hi: pytest.fail("metrics citit pe FX_SURSA=awb"))
    monkeypatch.setattr(m, "_fx_series_awb", lambda need, lo, hi: SERIE)
    assert m.fx_index(["USD"], "2026-06-01", "2026-06-01") == {("USD", dt.date(2026, 6, 1)): 4.5099}


@pytest.mark.parametrize("nume,dosar", MODULE)
def test_metrics_cazut_e_eroare_nu_curs_fix(nume, dosar, monkeypatch):
    m = _mod(nume, dosar)
    monkeypatch.delenv("FX_SURSA", raising=False)

    def pica():
        raise RuntimeError("conexiune refuzată")
    monkeypatch.setattr(m, "_db", pica)
    with pytest.raises(SystemExit) as e:
        m.fx_index(["USD"], "2026-06-01", "2026-06-02")
    assert "FX_SURSA=awb" in str(e.value) and "conexiune" not in str(e.value)
