# -*- coding: utf-8 -*-
"""Comutator pe Order Hub + cheie lipsă = eroare, nu cifra veche.

De ce: pe 24-oct-2026, profit_by_sku pornit doar cu `run.env` (fără cheie OH) a întors transportul pe cascada
veche, 517.805 în loc de 591.679 lei pe octombrie, cu un singur rând de avertisment în stderr.

Rulare: uv run --with pytest python -m pytest test_oh_cheie_lipsa.py -q
(testul pe profit_lines_sync rulează doar pe VPS, unde există `core.stores` și `api.profitability`)
"""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

import profit_core as pc

AICI = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def fara_chei(monkeypatch):
    for v in pc.OH_CHEI_CITIRE:
        monkeypatch.delenv(v, raising=False)


def _db(tmp_path, **setari):
    p = str(tmp_path / "profit.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE profit_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.executemany("INSERT INTO profit_settings VALUES (?, ?)", setari.items())
    c.commit(); c.close()
    return p


def test_cere_cheie_ridica_fara_cheie(fara_chei):
    with pytest.raises(pc.CheieOHLipsa, match="OH_PROFIT_CITIRE_TOKEN"):
        pc.cere_cheie_oh("test")


def test_cheia_dedicata_are_prioritate(fara_chei, monkeypatch):
    monkeypatch.setenv("OH_COST_TOKEN", "veche")
    assert pc.cere_cheie_oh("test") == "veche"
    monkeypatch.setenv("OH_PROFIT_CITIRE_TOKEN", "dedicata")
    assert pc.cere_cheie_oh("test") == "dedicata"


def _profit_by_sku(db):
    env = {k: v for k, v in os.environ.items() if k not in pc.OH_CHEI_CITIRE}
    return subprocess.run([sys.executable, os.path.join(AICI, "profit_by_sku.py"), "2026-10", "--db", db],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("setari", [{"transport_sursa": "oh"}, {"cogs_sursa": "oh"}])
def test_profit_by_sku_iese_cu_eroare_fara_cheie(tmp_path, setari):
    r = _profit_by_sku(_db(tmp_path, **setari))
    assert r.returncode != 0
    assert "EROARE" in r.stderr and "Nu cad pe sursa veche" in r.stderr
    assert "PROFIT PER CATEGORIE" not in r.stdout


def test_profit_by_sku_pe_sursa_veche_nu_cere_cheie(tmp_path):
    # fără comutator pe OH, garda nu intervine (raportul trece de ea și cade abia mai jos, pe env-ul lipsă)
    r = _profit_by_sku(_db(tmp_path, cogs_sursa="engine", transport_sursa="awbprint"))
    assert "Nu cad pe sursa veche" not in r.stderr


def test_profit_lines_sync_iese_cu_eroare_fara_cheie(tmp_path, fara_chei, monkeypatch):
    pytest.importorskip("core.stores")
    import profit_lines_sync as pls
    for v in pc.OH_CHEI_CITIRE:            # importul încarcă /root/Scripturi/.env (core.config): le scoatem iar
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(pls, "DB", _db(tmp_path, cogs_sursa="oh"))
    monkeypatch.setattr(pls, "list_stores", lambda: [])
    with pytest.raises(SystemExit, match="Nu cad pe sursa veche"):
        asyncio.run(pls.main("2026-10", "all"))
