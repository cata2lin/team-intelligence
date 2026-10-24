"""Importul TOM → OH nu rescrie costul de transport maritim real (facturile GOPET) cu estimarea TOM."""
import importlib.util
import os

_P = os.path.join(os.path.dirname(__file__), "lot_marfa_tom.py")
_spec = importlib.util.spec_from_file_location("lot_marfa_tom", _P)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_lot_cu_transport_real_nu_primeste_estimarea():
    loturi = [{"cod": "C48", "linii": [{"cod_sursa": "2299522", "transport_buc": "1.75"}, {"cod_sursa": "X"}]},
              {"cod": "C70", "linii": [{"cod_sursa": "BAIE-GRI", "transport_buc": "2.10"}]}]
    foaie = {"C48": {"transport_real": True}, "C70": {"transport_real": False}}
    assert m.pastreaza_transport_real(loturi, foaie) == 1
    assert "transport_buc" not in loturi[0]["linii"][0]
    assert loturi[1]["linii"][0]["transport_buc"] == "2.10"


def test_lot_necunoscut_in_oh_pastreaza_estimarea():
    loturi = [{"cod": "C99", "linii": [{"cod_sursa": "A", "transport_buc": "1"}]}]
    assert m.pastreaza_transport_real(loturi, {}) == 0
    assert loturi[0]["linii"][0]["transport_buc"] == "1"
