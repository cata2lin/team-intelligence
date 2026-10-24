"""deliverability_monitor: Order Hub implicit; AWBprint/VPS doar explicit; fără cheie OH = eroare, nu fallback."""
import pytest
import deliverability_monitor as D


def test_sursa_implicita(monkeypatch):
    monkeypatch.delenv("DELIVERABILITY_SURSA", raising=False)
    assert D.sursa_implicita(None) == "oh"
    monkeypatch.setenv("DELIVERABILITY_SURSA", "awb")
    assert D.sursa_implicita(None) == "awb"
    monkeypatch.setenv("DELIVERABILITY_SURSA", "altceva")
    assert D.sursa_implicita(None) == "oh"
    assert D.sursa_implicita("vps") == "vps"


def test_cheia_in_ordinea_buna(monkeypatch):
    for k in D.OH_CHEI:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OH_CS_SB_TOKEN", "c")
    monkeypatch.setenv("OH_TEAM_READ_TOKEN", "a")
    assert D._token_oh() == "a"


def test_fara_cheie_e_eroare(monkeypatch):
    for k in D.OH_CHEI:
        monkeypatch.delenv(k, raising=False)

    class _R:
        stdout = ""
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: _R())
    with pytest.raises(SystemExit):
        D._token_oh()
