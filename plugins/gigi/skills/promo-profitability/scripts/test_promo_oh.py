"""Comutarea pe Order Hub: magazinul se rezolvă unic; peste 120 de zile = refuz clar (fără rețea)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_profit as pp  # noqa: E402


def test_shop_for():
    assert pp.shop_for("georgetalent") == "ix5bxc-hr.myshopify.com"
    assert pp.shop_for("grandia.ro") == "n12w89-yy.myshopify.com"
    for gresit in ("bonhaus", "nu-exista"):
        try:
            pp.shop_for(gresit)
        except SystemExit:
            pass
        else:
            raise AssertionError(gresit)


def test_peste_120_zile():
    try:
        pp.rows_oh("grandia.ro", 200, 1.21)
    except SystemExit as e:
        assert "120" in str(e)
    else:
        raise AssertionError("trebuia SystemExit")
