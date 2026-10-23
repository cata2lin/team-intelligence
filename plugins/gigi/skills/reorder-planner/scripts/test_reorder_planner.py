"""Teste pure pentru reorder_planner (rata de livrare). Rulare: uv run --with pytest --with psycopg2-binary pytest -q"""
import reorder_planner as rp


def test_rata_livrare_pe_sku_cand_are_destule_colete():
    assert rp.rata_livrare(90, 10, 0.5) == 0.9


def test_rata_livrare_cade_pe_medie_sub_10_colete():
    assert rp.rata_livrare(3, 1, 0.85) == 0.85
    assert rp.rata_livrare(0, 0, 0.85) == 0.85
