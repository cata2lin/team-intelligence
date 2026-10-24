import oh_livrabilitate_cache as O


def test_prefix_ca_in_metrics_si_randuri_fara_zi_sarite():
    r = O.randuri_db([{"comanda": "GRAND123", "zi": "2026-10-01", "stare": "Livrata", "total": 10, "moneda": "RON",
                       "transport_fara_tva": 9.2},
                      {"comanda": "#EST1", "zi": "2026-10-02", "stare": "Refuzata"},
                      {"comanda": "EST2", "zi": None, "stare": "Livrata"}])
    assert r == [("GRAND123", "GRAND", "2026-10-01", "Livrata", 10, "RON", 9.2)]
