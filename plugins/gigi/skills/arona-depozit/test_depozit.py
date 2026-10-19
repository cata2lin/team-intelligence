# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest>=8", "paramiko>=3.4"]
# ///
"""Teste fără rețea pentru depozit.py: gărzile de scriere (nimic nu pleacă fără apply), permisiunile păstrate,
stația fizică a unei etichete. Rulare: uv run --with pytest pytest -q test_depozit.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import pytest  # noqa: E402

import depozit as D  # noqa: E402
import depozit_sql as S  # noqa: E402

USERI = [{"id": 7, "name": "Ana", "username": "ana-cont1", "role": "user", "approved": True, "email_verified": True,
          "stations": ["Uzina 2"], "permissions": {"scanner": ["stock"], "allowed_modules": ["video"]}},
         {"id": 8, "name": "Ana", "username": "ana-cont2", "role": "user", "approved": True, "email_verified": False,
          "stations": None, "permissions": {}}]


@pytest.fixture
def fals(monkeypatch):
    """Înlocuiește HTTP-ul: înregistrează scrierile, răspunde la citiri din memorie."""
    scrieri = []

    def scan(cale, metoda="GET", corp=None, **q):
        if metoda != "GET":
            scrieri.append((metoda, cale, corp))
            return {"success": True}
        if cale == "/api/auth/users":
            return USERI
        if cale == "/api/stations":
            return {"stations": [{"station": s} for s in ("Bartolomeu", "Uzina 2", "Parfumuri", "Teliu - stoc suspect")]}
        raise AssertionError(cale)

    def oh(cale, metoda="GET", corp=None, **q):
        if metoda != "GET":
            scrieri.append((metoda, cale, corp))
            return {"ok": True}
        if cale == "/api/print/logs":
            return {"logs": [{"id": 5}]}
        return {"id": 5, "name": "Statie: Bartolomeu / HA", "created_at": "2026-10-19T05:00:00",
                "comenzi": [{"awb": "1", "comanda": "A1", "printata": True}, {"awb": "2", "comanda": "A2", "printata": True}],
                "loturi": []}

    monkeypatch.setattr(D, "scan", scan)
    monkeypatch.setattr(D, "oh", oh)
    return scrieri


def test_proba_nu_scrie(fals):
    D.scanner_aproba(8)
    D.scanner_aloca_statie(7, "Bartolomeu")
    D.scanner_acces(7, "stock,locations")
    D.print_reset(print_id=5)
    D.print_reset(awb="1")
    assert fals == []


def test_reset_cere_motiv_si_parola(fals):
    with pytest.raises(D.Eroare):
        D.print_reset(print_id=5, apply=True)
    with pytest.raises(D.Eroare):
        D.print_reset(print_id=5, motiv="pdf nedeschis", apply=True)
    assert fals == []
    D.print_reset(print_id=5, motiv="pdf nedeschis", parola="x", apply=True)
    assert fals == [("POST", "/api/print/logs/5/reset", {"parola": "x", "motiv": "pdf nedeschis"})]


def test_reset_awb_merge_pe_ruta_awb(fals):
    D.print_reset(awb="2", motiv="m", parola="p", apply=True)
    assert fals[0][1] == "/api/print/awb/neprintat" and fals[0][2]["awb"] == "2"


def test_reset_un_singur_tinta():
    with pytest.raises(D.Eroare):
        D.print_reset(print_id=5, awb="1")
    with pytest.raises(D.Eroare):
        D.print_reset()


def test_permisiuni_pastreaza_restul():
    p = D.permisiuni_noi({"scanner": ["stock"], "allowed_modules": ["video"]}, "locations, stock")
    assert p == {"scanner": ["locations", "stock"], "allowed_modules": ["video"]}
    with pytest.raises(D.Eroare):
        D.permisiuni_noi({}, "admin")


def test_statie_necunoscuta_si_pseudo(fals):
    with pytest.raises(D.Eroare):
        D.scanner_aloca_statie(7, "Uzina3")
    with pytest.raises(D.Eroare):     # „Teliu - stoc suspect" e un bucket, nu o stație de om
        D.scanner_aloca_statie(7, "Teliu - stoc suspect")
    D.scanner_aloca_statie(7, "uzina 2, bartolomeu", apply=True)
    assert fals == [("PUT", "/api/auth/users/7", {"stations": ["Uzina 2", "Bartolomeu"]})]


def test_aproba_avertizeaza_email_neconfirmat(fals):
    assert "EMAIL NECONFIRMAT" in D.scanner_aproba(8)


def test_dubluri_semnalate(fals):
    assert "#7, #8" in D.scanner_utilizatori()


def test_statie_fizica():
    assert S.statie_fizica("Bartolomeu", {"Uzina 2"}) == "Bartolomeu"
    assert S.statie_fizica("Neplecate", {"Uzina 2"}) == "Uzina 2"
    assert S.statie_fizica("Neplecate", {"Uzina 2", "Bartolomeu"}) == "Bartolomeu + Uzina 2"
    assert S.statie_fizica("Neplecate", set()) == "Neplecate"


def test_erorile_devin_text(monkeypatch):
    def boom(**kw):
        raise D.Eroare("HTTP 403: x")
    monkeypatch.setitem(D.FUNCTII, "print_coada", boom)
    assert D.ruleaza("print_coada") == "Eroare: HTTP 403: x"
