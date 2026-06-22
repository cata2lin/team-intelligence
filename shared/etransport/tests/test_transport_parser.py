"""Teste pentru transport_parser: parsare text liber date transport și carrier."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from etransport.parsers.transport_parser import parse_transport_text, parse_carrier_text


def test_parse_full_transport():
    """Testează parsarea completă cu container, vehicul, remorcă, telefon, nume."""
    result = parse_transport_text(
        "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel"
    )
    assert result.container_no == "TIIU5478466"
    assert result.vehicle_no == "CT64ADT"
    assert result.trailer_no == "CT01AOT"
    assert result.driver_phone == "0761283435"
    assert result.driver_name == "Nichei Pavel"


def test_parse_second_example():
    """Testează al doilea exemplu din spec."""
    result = parse_transport_text(
        "AKKU4029009 CT57ADT/CT19ADT 0728870264 Cumpănașu Gheorghe"
    )
    assert result.container_no == "AKKU4029009"
    assert result.vehicle_no == "CT57ADT"
    assert result.trailer_no == "CT19ADT"
    assert result.driver_phone == "0728870264"
    assert "Gheorghe" in result.driver_name


def test_parse_without_container():
    """Testează transport fără container."""
    result = parse_transport_text(
        "CT64ADT/CT01AOT 0761283435 Nichei Pavel"
    )
    assert result.container_no is None
    assert result.vehicle_no == "CT64ADT"
    assert result.trailer_no == "CT01AOT"
    assert result.driver_phone == "0761283435"


def test_parse_space_separated_plates():
    """Testează vehicule separate cu spațiu în loc de /."""
    result = parse_transport_text(
        "CT64ADT CT01AOT 0761283435 Nichei Pavel"
    )
    assert result.vehicle_no == "CT64ADT"
    assert result.trailer_no == "CT01AOT"


def test_parse_carrier_with_vat():
    """Testează parsarea transportator cu CUI/VAT standard."""
    result = parse_carrier_text("ANDI TRANS SRL RO5607012")
    assert result.carrier_name == "ANDI TRANS SRL"
    assert result.carrier_vat == "RO5607012"


def test_parse_carrier_with_numeric_cui():
    """Testează parsarea transportator cu CUI numeric."""
    result = parse_carrier_text("TRANSPORT RAPID SRL 12345678")
    assert result.carrier_name == "TRANSPORT RAPID SRL"
    assert result.carrier_vat == "12345678"


def test_parse_empty_transport():
    """Testează text gol."""
    result = parse_transport_text("")
    assert result.container_no is None
    assert result.vehicle_no is None


def test_parse_empty_carrier():
    """Testează carrier gol."""
    result = parse_carrier_text("")
    assert result.carrier_name is None
    assert result.carrier_vat is None


if __name__ == "__main__":
    test_parse_full_transport()
    test_parse_second_example()
    test_parse_without_container()
    test_parse_space_separated_plates()
    test_parse_carrier_with_vat()
    test_parse_carrier_with_numeric_cui()
    test_parse_empty_transport()
    test_parse_empty_carrier()
    print("✅ Toate testele transport_parser au trecut!")
