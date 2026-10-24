"""Regresie 13-oct-2026: un magazin cu comenzi în Order Hub și zero în profit_orders trebuie să iasă la gardă."""
import garda_oh_profit_orders as G


def test_prefix_disparut_din_profit_orders_iese():
    oh = ["HU2092", "HU2093", "SK3001", "EST1", "EST2"]
    po = ["EST1", "EST2"]
    r = G.compara(oh, po)
    assert r["HU"] == (2, 2) and r["SK"] == (1, 1) and r["EST"] == (2, 0)


def test_luna_veche_ramane_cu_toate_prefixele_chiar_daca_magazinul_e_inchis_azi():
    # un magazin închis azi NU scoate comenzile lui din lunile trecute: garda le cere înapoi
    oh = ["PL%d" % i for i in range(30)]
    assert G.compara(oh, [])["PL"] == (30, 30)
    assert G.compara(oh, oh)["PL"] == (30, 0)
