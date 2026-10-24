"""grandia_pnl pe Orders + metrics (AWBprint faza 2, 24-oct-2026): costul pe lot la data comenzii și cursul umplut."""
from datetime import date

import grandia_pnl as G


def test_lotul_valabil_la_data_comenzii():
    loturi = [{"data_lot": "2026-10-01", "cost_lei_buc": 30.0}, {"data_lot": "2026-08-01", "cost_lei_buc": 20.0}]
    assert G.cost_la_data(loturi, date(2026, 9, 15)) == 20.0
    assert G.cost_la_data(loturi, date(2026, 10, 5)) == 30.0
    # toate loturile mai noi decât comanda → cel mai vechi (costul cunoscut cel mai apropiat)
    assert G.cost_la_data(loturi, date(2026, 7, 1)) == 20.0 and G.cost_la_data([], date(2026, 7, 1)) is None


def test_cursul_din_fx_rates_se_umple_in_weekend_fara_multiplier():
    rows = [("USD", date(2026, 10, 2), 4.40), ("USD", date(2026, 10, 5), 4.45)]   # vineri, luni
    fx = G.fx_umplut(rows, date(2026, 10, 3), date(2026, 10, 5), date(2026, 9, 23))
    assert fx[("USD", date(2026, 10, 3))] == 4.40 and fx[("USD", date(2026, 10, 4))] == 4.40
    assert fx[("USD", date(2026, 10, 5))] == 4.45
