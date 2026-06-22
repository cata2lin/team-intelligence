"""
Mapare coduri HS/TARIC → NC8 (primele 8 cifre).
Regulă: codurile HS din documente sunt adesea pe 10 cifre,
dar e-Transport folosește primele 8 cifre.
"""
import re
from typing import Tuple


def clean_hs_code(raw_code: str) -> str:
    """Elimină spații, puncte și caractere non-numerice din codul HS."""
    if not raw_code:
        return ""
    return re.sub(r'[^\d]', '', str(raw_code).strip())


def derive_nc8(hs_code_raw: str) -> Tuple[str, str, list[str]]:
    """
    Derivă codul NC8 din codul HS brut.
    
    Args:
        hs_code_raw: Codul HS original (poate fi 4-10+ cifre)
        
    Returns:
        Tuple de (hs_cleaned, nc8_code, warnings)
        
    Exemple:
        7010903000 → 70109030
        3923500000 → 39235000
        9616100000 → 96161000
    """
    warnings = []
    cleaned = clean_hs_code(hs_code_raw)
    
    if not cleaned:
        warnings.append("Cod HS lipsă sau invalid")
        return "", "", warnings
    
    if len(cleaned) < 4:
        warnings.append(
            f"Cod HS prea scurt ({len(cleaned)} cifre): '{cleaned}'. "
            f"Minim 4 cifre recomandate."
        )
    
    if len(cleaned) < 8:
        warnings.append(
            f"Cod HS sub 8 cifre ({len(cleaned)}): '{cleaned}'. "
            f"Se va folosi completat cu zerouri."
        )
        nc8 = cleaned.ljust(8, '0')
    else:
        nc8 = cleaned[:8]
    
    return cleaned, nc8, warnings


def format_nc8_display(nc8: str) -> str:
    """Formatează NC8 pentru afișare (fără formatare specială, rămâne string)."""
    return nc8
