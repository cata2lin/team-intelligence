"""Regula de comutare pe Order Hub: fără cheie = eroare clară, nu cifra veche (fără rețea, fără producție)."""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oh_citire as oh  # noqa: E402


def test_fara_cheie_e_eroare(monkeypatch):
    for k in oh.ORDINE_CHEI:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(oh, "_cheie", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    try:
        oh.cheie()
    except oh.OhEroare as e:
        assert "lipsește cheia" in str(e)
    else:
        raise AssertionError("trebuia OhEroare")


def test_ordinea_cheilor(monkeypatch):
    monkeypatch.setattr(oh, "_cheie", None)
    monkeypatch.setenv("OH_CS_SB_TOKEN", "c")
    monkeypatch.setenv("OH_PROFIT_CITIRE_TOKEN", "b")
    monkeypatch.setenv("OH_TEAM_READ_TOKEN", "a")
    assert oh.cheie() == "a"
    monkeypatch.setattr(oh, "_cheie", None)
    monkeypatch.delenv("OH_TEAM_READ_TOKEN")
    assert oh.cheie() == "b"


def test_ok_false_e_eroare(monkeypatch):
    import io, json, urllib.request

    class R(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(oh, "_cheie", "x")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R(json.dumps({"ok": False, "mesaj": "m"}).encode()))
    try:
        oh.get("/api/cs/ceva")
    except oh.OhEroare as e:
        assert "m" in str(e) and "x" not in str(e).replace("/api/cs/ceva", "")
    else:
        raise AssertionError("trebuia OhEroare")
