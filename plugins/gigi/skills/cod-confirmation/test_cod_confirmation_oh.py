"""cod_confirmation pe Order Hub (S1): aceeași formă {net, refz, red} ca varianta din profit_orders."""
import cod_confirmation as C


def _r(o, st, zi, skus="A", total=100):
    return {"comanda": o, "stare": st, "zi": zi, "skus": skus, "total": total}


def test_net_refz_si_prefixul_normalizat():
    rows = [_r("GRAND1", "Netrimisa", "2026-10-22"), _r("EST1", "Netrimisa", "2026-10-01"),
            _r("EST2", "Refuzata", "2026-09-01"), _r("EST3", "Refuzata", "2026-06-01")]
    d = C.date_din_oh(rows, nlo="2026-10-19", rlo="2026-07-26", rf="2026-08")
    assert [x["o"] for x in d["net"]] == ["GRAND1"] and d["net"][0]["p"] == "GRAN"
    assert d["refz"] == ["EST2"]
    assert C.date_din_oh(rows, "2026-10-19", "2026-07-26", "2026-08", prefix="EST")["net"] == []


def test_produs_risc():
    rows = [_r("EST%d" % i, "Refuzata", "2026-09-10", "RISC") for i in range(20)]
    rows += [_r("EST%d" % (100 + i), "Livrata", "2026-09-10", "RISC") for i in range(25)]
    rows += [_r("EST%d" % (200 + i), "Livrata", "2026-09-10", "BUN") for i in range(400)]
    assert C.date_din_oh(rows, "2026-10-19", "2026-07-26", "2026-08")["red"] == [["EST", "RISC"]]
