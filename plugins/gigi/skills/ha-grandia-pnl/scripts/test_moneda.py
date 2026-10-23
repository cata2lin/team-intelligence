# -*- coding: utf-8 -*-
"""Venitul se converteste in lei, COGS-ul NU — pe un produs vindut in TREI monede.

De ce testul exista: `profit_orders.revenue` e in MONEDA MAGAZINULUI, iar `cogs` in LEI (unitati
amestecate in aceeasi tabela). Fara conversie, raportul HA vs Grandia dadea HA la 44,5% marja in loc
de 27,5%, si net 2.051.617 lei in loc de 970.056 — fiindca Bonhaus CZ (CZK) e cel mai mare
contribuitor la gaaleata HA, iar 1 CZK = 0,22 lei face sumele brute sa para uriase.

Rulare: uv run --with pytest python -m pytest test_moneda.py -q
"""
import re
import sqlite3
from pathlib import Path

import pytest

SQL = Path(__file__).with_name("ha_grandia_pnl.py").read_text(encoding="utf-8")


def _baza():
    """Acelasi produs, acelasi pret real (~67 lei), vindut pe trei magazine cu monede diferite."""
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE profit_orders (month TEXT, prefix TEXT, order_name TEXT, revenue REAL,
                 cogs REAL, currency TEXT, status_category TEXT, skus TEXT, tags TEXT)""")
    c.execute("CREATE TABLE profit_exchange_rates (month TEXT, currency TEXT, rate_to_ron REAL)")
    c.executemany("INSERT INTO profit_exchange_rates VALUES (?,?,?)",
                  [("2026-10", "RON", 1.0), ("2026-10", "CZK", 0.2189), ("2026-10", "EUR", 5.3353)])
    c.executemany("INSERT INTO profit_orders VALUES (?,?,?,?,?,?,?,?,?)", [
        ("2026-10", "OFER", "OFER1", 67.00,    5.40, "RON", "Livrata", "HA-0001", ""),
        ("2026-10", "CZ",   "CZ1",   306.08,   5.40, "CZK", "Livrata", "HA-0001", ""),
        ("2026-10", "BONBG", "BG1",  12.56,    5.40, "EUR", "Livrata", "HA-0001", ""),
    ])
    return c


def _interogarea():
    """Scoatem din script interogarea de linii, exact cum o construieste el."""
    prefixes = ["GRAN"]
    mlist = "('2026-10')"
    no_test = "LOWER(COALESCE(tags,'')) NOT LIKE '%test%'"
    m = re.search(r'lines_sql = f"""(.*?)"""', SQL, re.S)
    assert m, "nu am gasit lines_sql in script"
    return eval('f"""' + m.group(1) + '"""')


def test_venitul_se_converteste_costul_nu():
    c = _baza()
    rinduri = list(c.execute(_interogarea()))
    assert len(rinduri) == 1, "o singura eticheta (HA) si un singur status"
    _lbl, _st, nr, venit, cost = rinduri[0]
    assert nr == 3
    # 67 + 306,08 x 0,2189 + 12,56 x 5,3353 = 67 + 67,00 + 67,01 → ~201 lei, nu 385,64 brut
    assert venit == pytest.approx(201.0, abs=1.0), "venitul trebuie adunat IN LEI"
    assert venit < 250, "daca iese ~386, conversia lipseste (CZK adunat brut)"
    # COGS-ul e deja in lei pe toate magazinele: 3 x 5,40, NU inmultit cu cursul
    assert cost == pytest.approx(16.2, abs=0.01), "COGS-ul NU se converteste"


def test_moneda_fara_curs_nu_arunca():
    """Un rand cu moneda necunoscuta cade pe 1.0 (COALESCE), nu rupe raportul."""
    c = _baza()
    c.execute("INSERT INTO profit_orders VALUES (?,?,?,?,?,?,?,?,?)",
              ("2026-10", "XX", "X1", 10.0, 1.0, "XYZ", "Livrata", "HA-0001", ""))
    rinduri = list(c.execute(_interogarea()))
    assert rinduri and rinduri[0][3] == pytest.approx(211.0, abs=1.0)


def test_eticheta_nu_s_a_schimbat():
    """Garda: reparatia era DOAR pe moneda. Definitia gaaletii HA rămâne `skus LIKE 'HA-%'`."""
    assert "skus LIKE 'HA-%'" in SQL
    assert "o.revenue * COALESCE(fx.rate_to_ron, 1.0)" in SQL
    assert "SUM(cogs)" in SQL or "o.cogs" in SQL
