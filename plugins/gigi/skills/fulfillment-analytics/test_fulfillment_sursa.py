"""fulfillment_analytics pe Order Hub: implicit oh, AWBprint doar explicit; rapoartele fără câmp în OH cer --source awb."""
import fulfillment_analytics as F

COL = ["comanda", "total", "moneda", "judet", "tara", "stare", "curier", "ultim_status", "transport_fara_tva", "skus", "zi"]
RANDURI = [
    ["EST1", 100.0, "RON", "Cluj", "Romania", "Livrata", "dpd romania", "Delivered", 10.0, "a;b", "2026-09-02"],
    ["EST2", 50.0, "RON", "Cluj", "Romania", "Refuzata", "dpd romania", "Refused", 11.0, "a", "2026-09-03"],
    ["GRAND3", 70.0, "RON", "Iasi", "Romania", "Livrata", "dpd romania", "Delivered", None, "c", "2026-09-04"],
    ["BONBG4", 20.0, "EUR", "?", "Bulgaria", "Netrimisa", "", "", None, "d", "2026-09-05"],
    ["EST5", 80.0, "RON", "Cluj", "Romania", "Livrata", "dpd romania", "Delivered", 9.0, "a", "2026-10-01"],
]


def test_sursa_implicita(monkeypatch):
    monkeypatch.delenv("FULFILLMENT_SURSA", raising=False)
    assert F.sursa_implicita(None) == "oh"
    monkeypatch.setenv("FULFILLMENT_SURSA", "awb")
    assert F.sursa_implicita(None) == "awb"
    assert F.sursa_implicita("oh") == "oh"


def test_rapoarte_fara_camp_in_oh():
    assert F.lipsa_oh("cod", "brand") and F.lipsa_oh("repeat", "brand")
    assert F.lipsa_oh("refuse", "payment") and F.lipsa_oh("geo", "city")
    assert F.lipsa_oh("refuse", "brand") is None and F.lipsa_oh("geo", "province") is None


def test_oh_rows_si_refuz(monkeypatch):
    monkeypatch.setattr(F, "_oh_get", lambda path: {"coloane": COL, "randuri": RANDURI})
    R = F.oh_rows("2026-09-01", "2026-10-01", None)
    assert [r["comanda"] for r in R] == ["EST1", "EST2", "GRAND3", "BONBG4"]       # fereastra pe zi
    assert {r["brand"] for r in R} == {"esteban.ro", "grandia.ro", "bonhaus.bg"}   # GRAND→GRAN, BONBG→BG
    h, rows, _ = F.oh_rep_refuse(R, "brand", None, 10)
    est = next(r for r in rows if r[0] == "esteban.ro")
    assert est[2:6] == (2, 1, 0, 1) and est[-1] == 50.0
    R = F.oh_rows("2026-09-01", "2026-10-01", ["GRAN"])
    assert [r["comanda"] for r in R] == ["GRAND3"]
    h, rows, _ = F.oh_rep_transport(F.oh_rows("2026-09-01", "2026-10-01", ["EST"]), 10)
    assert rows[0][2:5] == (2, 10.5, 21.0)
