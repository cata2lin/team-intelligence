"""Teste pentru unit_mapper: mapare unități de măsură."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etransport.utils.unit_mapper import map_unit


def test_pcs_variants():
    """Testează toate variantele pentru bucăți."""
    for raw in ["pcs", "PCS", "Pcs", "piece", "pieces", "buc", "bucati", "ea", "each"]:
        display, code, warnings = map_unit(raw)
        assert display == "buc", f"Failed for '{raw}': display={display}"
        assert code == "H87", f"Failed for '{raw}': code={code}"
        assert len(warnings) == 0, f"Unexpected warnings for '{raw}': {warnings}"


def test_kg_variants():
    """Testează toate variantele pentru kilograme."""
    for raw in ["kg", "KG", "Kg", "kgs", "KGS", "kilogram", "kilograms"]:
        display, code, warnings = map_unit(raw)
        assert display == "kg", f"Failed for '{raw}': display={display}"
        assert code == "KGM", f"Failed for '{raw}': code={code}"


def test_box_variants():
    """Testează variantele pentru cutii/cartoane."""
    for raw in ["box", "boxes", "carton", "cartons", "cutie", "cutii"]:
        display, code, warnings = map_unit(raw)
        assert display == "cutie", f"Failed for '{raw}': display={display}"
        assert code == "XBX", f"Failed for '{raw}': code={code}"


def test_unknown_unit():
    """Testează o unitate necunoscută — trebuie warning + default."""
    display, code, warnings = map_unit("gallons")
    assert display == "buc"  # default
    assert code == "H87"     # default
    assert len(warnings) > 0
    assert any("necunoscută" in w for w in warnings)


def test_empty_unit():
    """Testează unitate goală — warning + default."""
    display, code, warnings = map_unit("")
    assert display == "buc"
    assert code == "H87"
    assert len(warnings) > 0


def test_unit_with_spaces():
    """Testează unități cu spații și puncte."""
    display, code, warnings = map_unit(" PCS ")
    assert display == "buc"
    assert code == "H87"


if __name__ == "__main__":
    test_pcs_variants()
    test_kg_variants()
    test_box_variants()
    test_unknown_unit()
    test_empty_unit()
    test_unit_with_spaces()
    print("✅ Toate testele unit_mapper au trecut!")
