"""
Funcții de rotunjire cu păstrarea exactă a totalurilor.
Folosește metoda "largest remainder" pentru distribuirea erorilor.
"""
from typing import List


def round_preserve_total(
    values: List[float],
    total: float,
    decimals: int = 3,
) -> List[float]:
    """
    Rotunjește o listă de valori păstrând exact totalul specificat.
    
    Algoritmul „largest remainder":
    1. Rotunjește toate valorile în jos
    2. Calculează eroarea (diferența față de total)
    3. Distribuie eroarea pe elementele cu cel mai mare „rest" fracționar
    
    Args:
        values: Lista de valori brute
        total: Totalul exact care trebuie păstrat
        decimals: Nr. de zecimale
        
    Returns:
        Lista de valori rotunjite care se adună exact la total
    """
    if not values:
        return []
    
    if len(values) == 1:
        return [round(total, decimals)]
    
    factor = 10 ** decimals
    
    # Rotunjim în jos
    floored = [int(v * factor) / factor for v in values]
    remainders = [(v * factor) - int(v * factor) for v in values]
    
    # Diferența față de total (în unități de zecimale)
    current_sum = sum(floored)
    diff_units = round((total - current_sum) * factor)
    
    if diff_units == 0:
        return floored
    
    # Sortăm indexurile după remainder descrescător
    indices_by_remainder = sorted(
        range(len(values)),
        key=lambda i: remainders[i],
        reverse=True,
    )
    
    step = 1.0 / factor
    for i in range(min(abs(int(diff_units)), len(values))):
        idx = indices_by_remainder[i]
        if diff_units > 0:
            floored[idx] = round(floored[idx] + step, decimals)
        else:
            floored[idx] = round(floored[idx] - step, decimals)
    
    return floored


def safe_round(value: float, decimals: int = 2) -> float:
    """Rotunjire sigură cu nr. specificat de zecimale."""
    return round(value, decimals)
