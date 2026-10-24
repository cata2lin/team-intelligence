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
