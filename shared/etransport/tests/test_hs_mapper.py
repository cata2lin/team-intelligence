"""Teste pentru hs_mapper: derivare NC8 din coduri HS brute."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etransport.utils.hs_mapper import derive_nc8, clean_hs_code


def test_clean_hs_code():
    """Testează curățarea codului HS."""
    assert clean_hs_code("7010 9030 00") == "7010903000"
    assert clean_hs_code("70.10.90.30.00") == "7010903000"
    assert clean_hs_code("") == ""
    assert clean_hs_code(None) == ""
    assert clean_hs_code("  3923500000  ") == "3923500000"


def test_hs10_to_nc8_standard():
    """Testează derivarea NC8 din coduri HS pe 10 cifre (exemplele reale)."""
    # Exemplu 1: 7010903000 → 70109030
    _, nc8, warnings = derive_nc8("7010903000")
    assert nc8 == "70109030", f"Expected 70109030, got {nc8}"
    assert len(warnings) == 0
    
    # Exemplu 2: 3923500000 → 39235000
    _, nc8, warnings = derive_nc8("3923500000")
    assert nc8 == "39235000", f"Expected 39235000, got {nc8}"
    assert len(warnings) == 0
    
    # Exemplu 3: 9616100000 → 96161000
    _, nc8, warnings = derive_nc8("9616100000")
    assert nc8 == "96161000", f"Expected 96161000, got {nc8}"
    assert len(warnings) == 0


def test_hs8_to_nc8():
    """Testează un cod HS deja pe 8 cifre."""
    _, nc8, warnings = derive_nc8("70109030")
    assert nc8 == "70109030"
    assert len(warnings) == 0


def test_hs_short_code():
    """Testează un cod HS sub 8 cifre — trebuie warning + padding."""
    _, nc8, warnings = derive_nc8("701090")
    assert nc8 == "70109000"
    assert any("sub 8 cifre" in w for w in warnings)


def test_hs_empty():
    """Testează cod HS gol sau invalid."""
    _, nc8, warnings = derive_nc8("")
    assert nc8 == ""
    assert any("lipsă" in w for w in warnings)


def test_hs_with_spaces():
    """Testează cod HS cu spații."""
    cleaned, nc8, warnings = derive_nc8("7010 9030 00")
    assert cleaned == "7010903000"
    assert nc8 == "70109030"


if __name__ == "__main__":
    test_clean_hs_code()
    test_hs10_to_nc8_standard()
    test_hs8_to_nc8()
    test_hs_short_code()
    test_hs_empty()
    test_hs_with_spaces()
    print("✅ Toate testele hs_mapper au trecut!")
