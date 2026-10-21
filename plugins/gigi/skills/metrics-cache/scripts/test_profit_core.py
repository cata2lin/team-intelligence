# /// script
# requires-python = ">=3.10"
# ///
"""Teste pe regulile din profit_core — rulează oriunde, fără DB, fără rețea.

  uv run test_profit_core.py

De ce există: regulile de aici decid cifre de bani (ce e COGS, ce e marketing, ce intră în venit).
Fiecare test pornește dintr-un caz REAL, măsurat, cu data lui — ca să se vadă de unde vine regula.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profit_core as pc


class MarfaDataGratis(unittest.TestCase):
    """Comandă livrată cu venit 0 = marketing, nu COGS. Măsurat 22-oct-2026 pe sep+oct."""

    def test_livrata_cu_venit_zero_e_mostra(self):
        self.assertTrue(pc.is_sample_order("Livrata", 0))
        self.assertTrue(pc.is_sample_order("Livrata", 0.0))
        self.assertTrue(pc.is_sample_order("Livrata", None))

    def test_rotunjirea_nu_face_din_vanzare_o_mostra(self):
        self.assertTrue(pc.is_sample_order("Livrata", 0.004))      # jumătate de ban = zero
        self.assertFalse(pc.is_sample_order("Livrata", 0.01))       # un ban încasat = vânzare

    def test_doar_livratele_pot_fi_mostre(self):
        # O comandă anulată sau refuzată n-a dat marfa nimănui, deci nu e „dată gratis".
        for st in ("Anulata", "Refuzata", "Netrimisa", "In curs de livrare", "Lipsa awb"):
            self.assertFalse(pc.is_sample_order(st, 0), st)

    def test_cazul_artevita_140_la_suta(self):
        """ART, sep+oct 2026: 63 livrate, din care 40 cu venit 0 (mostre).

        Cu mostrele în COGS ieșea 140,5% și „pierdere"; fără ele, 36,6% — normal.
        Cifrele sunt cele reale din motor, la 22-oct-2026.
        """
        orders = [("Livrata", 0.0, 3523.40 / 40)] * 40 + [("Livrata", 3388.51 / 23, 1239.10 / 23)] * 23
        venit, cogs, mostre, n = pc.split_cogs_vs_samples(orders)
        self.assertEqual(n, 40)
        self.assertAlmostEqual(venit, 3388.51, places=2)
        self.assertAlmostEqual(cogs, 1239.10, places=2)
        self.assertAlmostEqual(mostre, 3523.40, places=2)
        # înainte: 4762.50/3388.51 = 140,5% · acum: 36,6%
        self.assertAlmostEqual(100 * (cogs + mostre) / venit, 140.5, places=1)
        self.assertAlmostEqual(100 * cogs / venit, 36.6, places=1)

    def test_statusurile_care_nu_dau_venit_nu_intra_nicaieri(self):
        orders = [("Livrata", 100.0, 30.0), ("Refuzata", 100.0, 30.0), ("Anulata", 100.0, 30.0)]
        venit, cogs, mostre, n = pc.split_cogs_vs_samples(orders)
        self.assertEqual((venit, cogs, mostre, n), (100.0, 30.0, 0.0, 0))

    def test_lista_goala(self):
        self.assertEqual(pc.split_cogs_vs_samples([]), (0.0, 0.0, 0.0, 0))


class TvaPeTara(unittest.TestCase):
    def test_vat_pe_prefix(self):
        self.assertAlmostEqual(pc.vat_for_prefix("OFER"), 0.21)     # RON, implicit
        self.assertAlmostEqual(pc.vat_for_prefix("BONBG"), 0.20)    # Bulgaria
        self.assertAlmostEqual(pc.vat_for_prefix("CZ"), 0.21)
        self.assertAlmostEqual(pc.vat_for_prefix("HU"), 0.27)       # lipsea din hartă până pe 22-oct
        self.assertAlmostEqual(pc.vat_for_prefix("MD"), 0.20)       # Moldova, nu RO

    def test_prefixul_multi_tara_se_decide_pe_moneda(self):
        """Duppo EU (DUPBG) are comenzi în EUR, HUF și CZK sub ACELAȘI prefix (măsurat 22-oct-2026).

        Pe prefix, o comandă maghiară ar primi TVA 20% (Bulgaria) sau 21% (implicit RO). Pe monedă,
        primește 27%, cât e în Ungaria.
        """
        self.assertAlmostEqual(pc.vat_for_order("DUPBG", currency="HUF"), 0.27)
        self.assertAlmostEqual(pc.vat_for_order("DUPBG", currency="CZK"), 0.21)
        self.assertAlmostEqual(pc.vat_for_order("DUPBG", currency="EUR"), 0.20)
        self.assertAlmostEqual(pc.vat_for_order("MD", currency="MDL"), 0.20)

    def test_tara_explicita_bate_moneda_si_prefixul(self):
        self.assertAlmostEqual(pc.vat_for_order("OFER", currency="RON", country="HU"), 0.27)

    def test_fara_nimic_cade_pe_romania(self):
        self.assertAlmostEqual(pc.vat_for_order(""), 0.21)
        self.assertAlmostEqual(pc.vat_for_order("NECUNOSCUT", currency="XYZ"), 0.21)


class ColetPlecat(unittest.TestCase):
    def test_transportul_se_plateste_doar_pe_coletele_plecate(self):
        for st in ("Livrata", "In curs de livrare", "Refuzata"):
            self.assertEqual(pc.refusal_transport_multiplier(st), 1.0, st)
        for st in ("Anulata", "Netrimisa", "Lipsa awb"):
            self.assertEqual(pc.refusal_transport_multiplier(st), 0.0, st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
