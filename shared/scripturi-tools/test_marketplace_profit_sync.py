"""marketplace_profit_sync: prefixul pe țară, liniile din Orders și împărțirea comisionului Trendyol pe pachete."""
import marketplace_profit_sync as M


def test_prefixul_poarta_tara():
    assert M.prefix("trendyol", "Romania", "RON") == "TY"
    assert M.prefix("trendyol", "Bulgaria", "EUR") == "TYBG"
    assert M.prefix("trendyol", "Greece", "EUR") == "TYGR"
    assert M.prefix("emag", "", "RON") == "EMAG"
    assert M.prefix("emag", None, "HUF") == "EMAGHU"
    assert M.prefix("olx", "Romania", "RON") == "OLX"


def test_liniile_din_orders():
    assert M.linii("HA-0001×2;GT-33×1") == [("HA-0001", 2), ("GT-33", 1)]
    assert M.linii("") == []


def test_comisionul_merge_pe_pachetele_livrate_dupa_venit():
    comenzi = [("TY1", "1", 100.0, True), ("TY1-0002", "1", 300.0, True), ("TY1-0003", "1", 50.0, False),
               ("TY2", "2", 80.0, True)]
    r = M.comision_trendyol(comenzi, {"1": 40.0})
    assert r == {"TY1": 10.0, "TY1-0002": 30.0}   # pachetul nelivrat nu ia comision; TY2 fără decontare lipsește


def test_facturile_de_transport_merg_pe_perioada_coletelor():
    colete = {("2026-04", "TY"): 1500, ("2026-07", "TY"): 100, ("2026-08", "TY"): 230, ("2026-08", "TYBG"): 21,
              ("2026-10", "TY"): 8}
    facturi = {"2026-03": 999.0, "2026-04": 5778.0, "2026-05": 22997.0, "2026-06": 175.0, "2026-07": 181.0,
               "2026-08": 2574.0, "2026-09": 459.0, "2026-10": 11.0}
    r = M.perioade_transport(colete, facturi)
    assert r["2026-04..2026-04"] == ({"2026-04"}, 5778.0 + 22997.0 + 175.0)   # martie e înaintea ferestrei
    assert r["2026-07..2026-08"] == ({"2026-07", "2026-08"}, 181.0 + 2574.0 + 459.0)
    assert r["2026-10..2026-10"] == ({"2026-10"}, 11.0)
