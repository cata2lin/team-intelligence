"""Fail-safe-ul lui sync_marketing_daily_costs: o citire esuata a foii CPA nu sterge nimic.
Rulare: uv run --with psycopg2-binary --with pytest pytest test_marketing_daily_costs.py"""
import datetime
import build_cache as bc


class FakeCur:
    def __init__(self, existing=0):
        self.sql, self.existing = [], existing
    def execute(self, sql, params=None):
        self.sql.append(sql)
    def fetchone(self):
        return (self.existing,)


def _no_delete(cur):
    return not any("DELETE" in s for s in cur.sql)


def test_foaia_nu_raspunde_nu_scrie(monkeypatch):
    monkeypatch.setattr(bc, "_fetch_cpa_tab", lambda name: None)
    cur = FakeCur(existing=700)
    assert bc.sync_marketing_daily_costs(cur) is False
    assert _no_delete(cur)


def test_foaia_pe_jumatate_nu_scrie(monkeypatch):
    today = datetime.date.today().isoformat()
    rows = [["Data", "Brand", "Facebook", "Tiktok"], [today, "Esteban", "1,00", "0,00"]]
    monkeypatch.setattr(bc, "_fetch_cpa_tab", lambda name: rows)
    cur = FakeCur(existing=700)
    assert bc.sync_marketing_daily_costs(cur) is False
    assert _no_delete(cur)


def test_un_tab_lipsa_doar_actualizeaza(monkeypatch):
    today = datetime.date.today().isoformat()
    rz2 = [["Data", "Brand", "Facebook", "Tiktok"], [today, "Esteban", "4.113,62", "0,00"]]
    monkeypatch.setattr(bc, "_fetch_cpa_tab", lambda name: rz2 if name == "Raport Zilnic 2" else None)
    written = []
    monkeypatch.setattr("psycopg2.extras.execute_values", lambda cur, sql, rows, **k: written.extend(rows))
    monkeypatch.setattr(bc, "log_refresh", lambda *a: None)
    cur = FakeCur(existing=0)
    assert bc.sync_marketing_daily_costs(cur) is True
    assert _no_delete(cur)
    assert written[0][1:5] == ("esteban.ro", 4113.62, 0.0, 0.0)
