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


def test_rapoarte_cu_client():
    assert F.cere_client("cod", "brand") and F.cere_client("repeat", "brand")
    assert F.cere_client("refuse", "payment") and F.cere_client("geo", "city")
    assert not F.cere_client("refuse", "brand") and not F.cere_client("geo", "province")


def test_cod_si_reducere():
    for p in ("Cash on Delivery (COD), xConnector", "Plată ramburs", "Numerar la livrare", "Plată ramburs la livrare"):
        assert F.este_cod(p)
    for p in ("shopify_payments", "xConnector", "manual", ""):
        assert not F.este_cod(p)
    assert F.grup_reducere("") == "fără cod"
    assert F.grup_reducere("quantity discount -8.50").startswith("quantity")
    assert F.grup_reducere("персонализирана отстъпка") == "personalizat"
    assert F.norm_oras(" București ") == F.norm_oras("bucuresti") == "bucuresti"


def test_repeat_refuz_noi_vs_revenit(monkeypatch):
    col = COL + ["canal", "magazin", "plata", "reducere", "oras", "client_email", "client_telefon"]
    ist = [["EST0", 1, "RON", "", "Romania", "Livrata", "", "", None, "", "2026-08-10", "", "m1", "", "", "", "", "T1"]]
    monkeypatch.setattr(F, "_oh_luni", lambda luni, cu_client=False: iter([{"coloane": col, "randuri": ist}]))
    mk = lambda n, st, tel: {"brand": "esteban.ro", "magazin": "m1", "telefon": tel,
                             "bucket": F.OH_BUCKET[st], "zi": "2026-09-05"}
    R = {"fereastra": [mk("a", "Livrata", "T1"), mk("b", "Refuzata", "T2"), mk("c", "Livrata", "T3")], "inainte": []}
    h, rows, _ = F.oh_rep_repeat(R, "2026-09-01", 1, 10)
    assert rows[0][1:5] == (3, 3, 2, 1) and rows[0][-2:] == (50.0, 0.0)


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
