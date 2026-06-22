"""Teste pentru weight_distribution_service: distribuire greutăți proporționale."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etransport.models.product_line import ProductLine
from etransport.parsers.packing_list_parser import PackingListData
from etransport.services.weight_distribution_service import distribute_weights


def _make_product(qty, value):
    """Helper: creează un produs cu cantitate și valoare."""
    return ProductLine(
        product_name_export="Test",
        quantity=qty,
        line_value_original=value,
        nc_code_8="12345678",
    )


def test_distribute_proportional_by_value():
    """Testează distribuirea proporțională pe baza valorii."""
    products = [
        _make_product(100, 1000),  # 50% din valoare
        _make_product(200, 1000),  # 50% din valoare
    ]
    
    packing = PackingListData(
        total_net_weight_kg=100.0,
        total_gross_weight_kg=120.0,
    )
    
    result = distribute_weights(products, packing)
    
    # 50/50 split
    assert result[0].net_weight_kg == 50.0
    assert result[1].net_weight_kg == 50.0
    assert result[0].gross_weight_kg == 60.0
    assert result[1].gross_weight_kg == 60.0
    
    # Totalurile se păstrează exact
    total_net = sum(p.net_weight_kg for p in result)
    total_gross = sum(p.gross_weight_kg for p in result)
    assert abs(total_net - 100.0) < 0.001
    assert abs(total_gross - 120.0) < 0.001


def test_distribute_uneven():
    """Testează distribuirea cu valori inegale."""
    products = [
        _make_product(100, 3000),  # 75%
        _make_product(100, 1000),  # 25%
    ]
    
    packing = PackingListData(
        total_net_weight_kg=1000.0,
        total_gross_weight_kg=1200.0,
    )
    
    result = distribute_weights(products, packing)
    
    assert result[0].net_weight_kg == 750.0
    assert result[1].net_weight_kg == 250.0
    
    total_net = sum(p.net_weight_kg for p in result)
    assert abs(total_net - 1000.0) < 0.001


def test_no_packing_data():
    """Testează când nu avem date packing list."""
    products = [_make_product(100, 1000)]
    
    result = distribute_weights(products, None)
    
    assert result[0].net_weight_kg == 0.0
    assert any("nedisponibilă" in w for w in result[0].warnings)


def test_existing_line_weights():
    """Testează că greutățile existente per linie sunt păstrate."""
    products = [
        _make_product(100, 1000),
    ]
    products[0].net_weight_kg = 50.0
    products[0].gross_weight_kg = 60.0
    
    packing = PackingListData(
        total_net_weight_kg=50.0,
        total_gross_weight_kg=60.0,
    )
    
    result = distribute_weights(products, packing)
    
    # Greutățile existente sunt păstrate
    assert result[0].net_weight_kg == 50.0
    assert result[0].gross_weight_kg == 60.0


def test_distribute_three_lines():
    """Testează distribuirea pe 3 linii cu rounding corect."""
    products = [
        _make_product(100, 1000),
        _make_product(100, 1000),
        _make_product(100, 1000),
    ]
    
    packing = PackingListData(
        total_net_weight_kg=100.0,
        total_gross_weight_kg=120.0,
    )
    
    result = distribute_weights(products, packing)
    
    # Totalul trebuie să fie exact
    total_net = sum(p.net_weight_kg for p in result)
    assert abs(total_net - 100.0) < 0.001


if __name__ == "__main__":
    test_distribute_proportional_by_value()
    test_distribute_uneven()
    test_no_packing_data()
    test_existing_line_weights()
    test_distribute_three_lines()
    print("✅ Toate testele weight_distribution au trecut!")
