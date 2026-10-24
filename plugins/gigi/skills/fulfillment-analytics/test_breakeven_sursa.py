"""breakeven.py pe Order Hub (S1): implicit oh; un magazin fără colete în OH rămâne pe AWBprint și o spune."""
import breakeven as B


class _Cur:
    def __init__(self):
        self.n = 0

    def execute(self, sql, params=None):
        self.n += 1

    def fetchone(self):
        return (90, 10, 150.0, 13.0)            # AWBprint: livrate, refuzate, AOV, transport median cu TVA

    def fetchall(self):
        return [([{"inventory_item": {"sku": "A"}, "quantity": 1}], 150.0)]


def _rul(monkeypatch, oh):
    monkeypatch.setattr(B, "_shopify_cost_map", lambda p: {"A": 30.0})
    monkeypatch.setattr(B, "oh_livrare", lambda p, d: oh)
    monkeypatch.setattr(B, "oh_comenzi", lambda p, d: [(150.0, [("A", 1)])])
    monkeypatch.setattr(B, "_shop_domain", lambda p: None)
    return B.analyze(_Cur(), "esteban", 60, 100, {"RON": 1.0}, source="oh")


def test_oh_cand_are_colete(monkeypatch):
    r = _rul(monkeypatch, (80, 20, 140.0, 10.0))
    assert r["sursa"] == "oh" and r["D"] == 0.8 and r["t_ex"] == 10.0


def test_fara_colete_in_oh_ramane_pe_awb(monkeypatch):
    r = _rul(monkeypatch, (0, 0, 0.0, 0.0))
    assert r["sursa"] == "awb" and r["D"] == 0.9 and round(r["t_ex"], 2) == round(13.0 / 1.21, 2)


def test_cogs_pct_din_orders_fara_awbprint(monkeypatch):
    # AWBprint faza 2: pe Orders, COGS% vine din SKU-urile comenzilor din `livrabilitate`; cursorul AWBprint nu există
    monkeypatch.setattr(B, "_shopify_cost_map", lambda p: {})
    monkeypatch.setattr(B, "oh_livrare", lambda p, d: (80, 20, 100.0, 10.0))
    monkeypatch.setattr(B, "oh_comenzi", lambda p, d: [(100.0, [("A", 1), ("B", 1)]), (0, [("A", 1)])])
    monkeypatch.setattr(B, "_shop_domain", lambda p: "x.myshopify.com")
    monkeypatch.setattr(B, "_loturi_oh", lambda c, m: {"A": 12.1, "B": 18.15})
    r = B.analyze(None, "esteban", 60, 100, {"RON": 1.0}, source="oh")
    assert r["sursa"] == "oh" and round(r["cogs_pct"], 4) == 0.3025 and r["match"] == 1.0


def test_cogs_pct_din_pur():
    assert B.cogs_pct_din([(100.0, [("a", 1), ("X", 1)])], {"A": 30.0}) == (0.3, 0.5)
    # cantitatea contează (2+1): trei bucăți pe linie = de trei ori costul
    assert B.cogs_pct_din([(100.0, [("A", 3)])], {"A": 10.0})[0] == 0.3
