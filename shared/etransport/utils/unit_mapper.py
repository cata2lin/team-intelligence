"""
Mapare unități de măsură din invoice/packing list → 
(display_unit, standard_unit_code) conform config.
"""
from typing import Tuple
from etransport.utils.text_normalizer import normalize_unit
from etransport import config


def map_unit(raw_unit: str) -> Tuple[str, str, list[str]]:
    """
    Mapează o unitate de măsură brută la (display_unit, standard_unit_code).
    
    Args:
        raw_unit: Unitatea de măsură din document (ex: "pcs", "PCS", "pieces")
        
    Returns:
        Tuple de (display_unit, standard_unit_code, warnings)
    """
    warnings = []
    normalized = normalize_unit(raw_unit)
    
    if not normalized:
        warnings.append("Unitate de măsură lipsă. Se folosește default: buc / H87")
        return (
            config.DEFAULT_UNIT["display_unit"],
            config.DEFAULT_UNIT["standard_unit_code"],
            warnings,
        )
    
    mapping = config.UNIT_MAPPINGS.get(normalized)
    
    if mapping:
        return mapping["display_unit"], mapping["standard_unit_code"], warnings
    
    # Fallback: unitate necunoscută
    warnings.append(
        f"Unitate de măsură necunoscută: '{raw_unit}' (normalizat: '{normalized}'). "
        f"Se folosește default: {config.DEFAULT_UNIT['display_unit']} / "
        f"{config.DEFAULT_UNIT['standard_unit_code']}"
    )
    return (
        config.DEFAULT_UNIT["display_unit"],
        config.DEFAULT_UNIT["standard_unit_code"],
        warnings,
    )
