# -*- coding: utf-8 -*-
"""Venitul pe categorie se adună în LEI, costul NU se converteste — pe un produs vindut in 3 monede.

De ce testul exista: `profit_orders.revenue` e in MONEDA MAGAZINULUI, iar `cogs` in LEI. Raportul
agrega pe CATEGORIE, deci peste magazine, si aduna brut: pe august raporta 11.381.900 lei venit in loc
de 8.479.046 (+34,2%), pe septembrie +19,3%, pe octombrie +17,6%. Marja bruta pe categorie era deci
greșita pe orice categorie vinduta si pe magazine straine.

Rulare: uv run --with pytest python -m pytest test_profit_by_category_moneda.py -q
"""
import re
import sqlite3
from pathlib import Path

import pytest

SURSA = Path(__file__).with_name("profit_by_category.py").read_text(encoding="utf-8")


def _baza():
    """Acelasi produs (~67 lei real), vindut pe trei magazine cu monede diferite."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE profit_orders (month TEXT, revenue REAL, cogs REAL, currency TEXT,
                 status_category TEXT, skus TEXT)""")
    c.execute("CREATE TABLE profit_exchange_rates (month TEXT, currency TEXT, rate_to_ron REAL)")
    c.executemany("INSERT INTO profit_exchange_rates VALUES (?,?,?)",
                  [("2026-10", "RON", 1.0), ("2026-10", "CZK", 0.2189), ("2026-10", "EUR", 5.3353)])
    c.executemany("INSERT INTO profit_orders VALUES (?,?,?,?,?,?)", [
        ("2026-10", 67.00,  5.40, "RON", "Livrata", "HA-0001"),
        ("2026-10", 306.08, 5.40, "CZK", "Livrata", "HA-0001"),
        ("2026-10", 12.56,  5.40, "EUR", "Livrata", "HA-0001"),
        ("2026-10", 999.00, 9.99, "CZK", "Refuzata", "HA-0001"),   # nelivrata: nu intra
    ])
    return c


def _interogarea():
    """Scoatem interogarea exact cum o scrie raportul, ca testul sa cada daca cineva o schimba."""
    m = re.search(r'cx\.execute\("""(.*?)"""', SURSA, re.S)
    assert m, "nu am gasit interogarea in profit_by_category.py"
    return m.group(1)


def test_venitul_in_lei_costul_neatins():
    c = _baza()
    rinduri = list(c.execute(_interogarea(), ("2026-10",)))
    assert len(rinduri) == 3, "doar comenzile livrate"
    venit = sum(r["rev_lei"] or 0 for r in rinduri)
    cost = sum(r["cogs"] or 0 for r in rinduri)
    # 67 + 306,08x0,2189 + 12,56x5,3353 = ~201 lei. Brut ar fi 385,64.
    assert venit == pytest.approx(201.0, abs=1.0), "venitul trebuie adunat IN LEI"
    assert venit < 250, "daca iese ~386, conversia lipseste"
    assert cost == pytest.approx(16.2, abs=0.01), "COGS-ul e deja in lei, nu se converteste"


def test_moneda_fara_curs_se_semnaleaza_nu_se_tace():
    """Un rand cu moneda necunoscuta intra 1:1 — dar raportul trebuie sa AVERTIZEZE, nu sa tacă."""
    c = _baza()
    c.execute("INSERT INTO profit_orders VALUES (?,?,?,?,?,?)",
              ("2026-10", 100.0, 1.0, "XYZ", "Livrata", "HA-0001"))
    rinduri = list(c.execute(_interogarea(), ("2026-10",)))
    fara = [r for r in rinduri if r["rate_to_ron"] is None and (r["currency"] or "RON") != "RON"]
    assert len(fara) == 1, "randul fara curs trebuie sa fie detectabil din rezultat"
    assert "fara_curs" in SURSA and "ATENTIE" in SURSA, "raportul trebuie sa avertizeze in stderr"
