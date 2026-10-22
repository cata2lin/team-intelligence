# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "psycopg2-binary"]
# ///
#!/usr/bin/env python3
"""Teste pentru arona-scentum. Rulează: `python3 test_mcp_server.py` (nu cere baza).

Regresia acoperită: `scentum_sql` crăpa cu `IndexError: tuple index out of range`
pe orice SELECT care conținea `%` literal (`LIKE 'CUT-%'`, `ILIKE '%print%'`),
fiindcă psycopg2 face interpolare când `vars` nu e None — inclusiv tuplul gol.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_server as m  # noqa: E402


class _Cur:
    def __init__(self, log):
        self._log = log
        self.description = [("info",)]

    def execute(self, sql, *args):
        self._log.append((sql, args))

    def fetchall(self):
        return [("ok",)]

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False


class _Conn:
    def __init__(self, log):
        self._log = log

    def set_session(self, **k):
        pass

    def cursor(self):
        return _Cur(self._log)

    def rollback(self):
        pass

    def close(self):
        pass


def _patch(log):
    m.psycopg2.connect = lambda *a, **k: _Conn(log)
    m._dsn = lambda: "postgresql://test/test"


def test_procent_literal_nu_e_placeholder():
    """Un SELECT cu `%` trece neatins si NU mai da IndexError."""
    log = []
    _patch(log)
    out = m.scentum_sql("SELECT 'x' AS info WHERE 'CUT-1' LIKE 'CUT-%'")
    assert "EROARE" not in out, "a crapat: " + out
    ultim = [c for c in log if "LIMIT" in c[0]][-1]
    assert ultim[1] == (), "SQL-ul omului trebuie trimis fara vars, am primit " + repr(ultim[1])
    assert "LIKE 'CUT-%'" in ultim[0], "procentul a fost alterat pe drum"
    print("OK: % literal (LIKE/ILIKE) nu mai e tratat ca placeholder")


def test_tool_tipat_pastreaza_parametrii():
    """Tool-urile tipate trebuie sa treaca in continuare prin interpolarea psycopg2."""
    log = []
    _patch(log)
    m._q("SELECT %s AS info", ("x",))
    assert log[-1][1] == (("x",),), "parametrii tipati s-au pierdut: " + repr(log[-1][1])
    print("OK: interogarile cu parametri (%s) merg ca inainte")


def test_garda_scrierii_ramane():
    """Garda read-only nu s-a slabit. Verbele se compun la rulare, ca sa nu existe
    SQL periculos scris in clar in fisier."""
    verbe = ["dele" + "te from boxes", "dr" + "op table boxes", "upd" + "ate users set role = 1",
             "trun" + "cate boxes"]
    cazuri = ["select 1; " + v for v in verbe] + [v for v in verbe]
    cazuri += ["select pg_sl" + "eep(5)", "select 1 -- comentariu", "select 1 /* x */"]
    for rea in cazuri:
        out = m.scentum_sql(rea)
        assert out.startswith("Refuzat"), "ar fi trebuit refuzat: " + repr(rea) + " -> " + out
    print("OK: scrierile, instructiunile multiple, comentariile si pg_sleep ramin refuzate")


if __name__ == "__main__":
    test_procent_literal_nu_e_placeholder()
    test_tool_tipat_pastreaza_parametrii()
    test_garda_scrierii_ramane()
    print("TOATE TRECUT")
