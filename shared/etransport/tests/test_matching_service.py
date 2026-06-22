"""Teste pentru matching_service: matching invoice ↔ packing list."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etransport.models.product_line import ProductLine
from etransport.parsers.packing_list_parser import PackingListLine
from etransport.services.matching_service import (
    match_invoice_to_packing,
    apply_matching_results,
)


def _make_invoice_line(line_no, name, qty):
    """Helper: creează o linie de invoice."""
    from etransport.utils.text_normalizer import normalize_text
    return ProductLine(
        source_line_no=line_no,
        product_name_raw=name,
        product_name_normalized=normalize_text(name),
        product_name_export=name,
        quantity=qty,
    )


def _make_packing_line(line_no, desc, qty, nw, gw):
    """Helper: creează o linie de packing list."""
    from etransport.utils.text_normalizer import normalize_text
    return PackingListLine(
        line_no=line_no,
        description=desc,
        description_normalized=normalize_text(desc),
        quantity=qty,
        net_weight_kg=nw,
        gross_weight_kg=gw,
    )


def test_index_match():
    """Testează matching pe index când nr. linii coincide."""
    inv = [
        _make_invoice_line(1, "Glass bottles", 1000),
        _make_invoice_line(2, "Plastic caps", 2000),
    ]
    pl = [
        _make_packing_line(1, "Glass bottles", 1000, 500, 600),
        _make_packing_line(2, "Plastic caps", 2000, 200, 250),
    ]
    
    results = match_invoice_to_packing(inv, pl)
    assert len(results) == 2
    assert results[0].method == "index"
    assert results[0].confidence == 100.0
    assert results[0].packing_line_no == 1
    assert results[1].packing_line_no == 2


def test_fuzzy_match():
    """Testează matching fuzzy cu denumiri diferite."""
    inv = [
        _make_invoice_line(1, "Glass bottles 500ml clear", 1000),
    ]
    pl = [
        _make_packing_line(1, "Clear glass bottles 500 ml", 1000, 500, 600),
    ]
    
    results = match_invoice_to_packing(inv, pl)
    assert len(results) == 1
    assert results[0].packing_line_no == 1
    assert results[0].confidence > 0


def test_no_packing_list():
    """Testează când nu avem packing list."""
    inv = [
        _make_invoice_line(1, "Product A", 100),
    ]
    
    results = match_invoice_to_packing(inv, [])
    assert len(results) == 1
    assert results[0].method == "none"
    assert len(results[0].warnings) > 0


def test_apply_matching():
    """Testează aplicarea rezultatelor matching-ului."""
    inv = [
        _make_invoice_line(1, "Glass bottles", 1000),
    ]
    pl = [
        _make_packing_line(1, "Glass bottles", 1000, 500.5, 620.3),
    ]
    
    results = match_invoice_to_packing(inv, pl)
    updated = apply_matching_results(inv, pl, results)
    
    assert updated[0].net_weight_kg == 500.5
    assert updated[0].gross_weight_kg == 620.3
    assert updated[0].match_method == "index"


def test_different_line_counts():
    """Testează când nr. linii diferă (nu se face index match)."""
    inv = [
        _make_invoice_line(1, "Glass bottles", 1000),
        _make_invoice_line(2, "Plastic caps", 2000),
        _make_invoice_line(3, "Metal lids", 500),
    ]
    pl = [
        _make_packing_line(1, "Glass bottles", 1000, 500, 600),
        _make_packing_line(2, "Plastic caps", 2000, 200, 250),
    ]
    
    results = match_invoice_to_packing(inv, pl)
    assert len(results) == 3
    # Prima și a doua ar trebui matchuite
    assert results[0].packing_line_no is not None
    assert results[1].packing_line_no is not None
    # A treia nu are match
    assert results[2].packing_line_no is None


if __name__ == "__main__":
    test_index_match()
    test_fuzzy_match()
    test_no_packing_list()
    test_apply_matching()
    test_different_line_counts()
    print("✅ Toate testele matching_service au trecut!")
