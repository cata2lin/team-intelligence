"""basket_pairs = aceeași aritmetică ca AWB_SQL (support/lift/confidence), fără rețea."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cross_sell as cs  # noqa: E402


def test_basket_pairs():
    cosuri = {"1": {"a", "b"}, "2": {"a", "b"}, "3": {"a"}, "4": {"c"}, "5": {"b", "c"}}
    r = cs.basket_pairs(cosuri, min_prod=2, min_co=2)
    assert len(r) == 1
    sa, sb, co, na, nb, lift, cab, cba = r[0]
    assert (sa, sb, co, na, nb) == ("a", "b", 2, 3, 3)
    assert abs(lift - 2 * 5 / 9) < 1e-9 and abs(cab - 2 / 3) < 1e-9


def test_luni():
    from datetime import date
    assert list(cs._luni(date(2026, 11, 20), date(2027, 1, 3))) == ["2026-11", "2026-12", "2027-01"]
