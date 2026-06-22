"""
Serviciu de distribuire a greutăților.
Dacă packing list-ul are greutăți pe linie, le folosește direct.
Dacă are doar totaluri, le distribuie proporțional pe baza cantității sau valorii.
Rotunjirea păstrează exact totalurile.
"""
from typing import Optional
from etransport.models.product_line import ProductLine
from etransport.parsers.packing_list_parser import PackingListData
from etransport.utils.rounding import round_preserve_total
from etransport import config


def distribute_weights(
    products: list[ProductLine],
    packing_data: Optional[PackingListData],
) -> list[ProductLine]:
    """
    Distribuie greutățile pe liniile de produs.
    
    Strategia:
    1. Dacă packing list are greutăți per linie (deja aplicate prin matching),
       verifică consistența cu totalurile
    2. Dacă nu are greutăți per linie, distribuie totalurile proporțional
    3. Dacă nu avem nici totaluri, adaugă warning
    
    Args:
        products: Lista de produse (cu greutăți din matching)
        packing_data: Date din packing list (totaluri)
        
    Returns:
        Lista actualizată de produse cu greutăți
    """
    if not products:
        return products
    
    # Verificăm dacă avem deja greutăți pe linie
    has_line_weights = any(p.net_weight_kg > 0 for p in products)
    
    if has_line_weights:
        # Avem greutăți per linie — verificăm consistența
        _validate_weight_totals(products, packing_data)
        return products
    
    # Nu avem greutăți per linie — distribuim din totaluri
    if packing_data and packing_data.total_net_weight_kg > 0:
        _distribute_from_totals(products, packing_data)
    else:
        # Nici totaluri nu avem
        for p in products:
            p.warnings.append(
                "Greutate nedisponibilă: packing list lipsă sau fără date"
            )
    
    return products


def _validate_weight_totals(
    products: list[ProductLine],
    packing_data: Optional[PackingListData],
):
    """Verifică dacă suma greutăților pe linie coincide cu totalurile."""
    if not packing_data:
        return
    
    sum_net = sum(p.net_weight_kg for p in products)
    sum_gross = sum(p.gross_weight_kg for p in products)
    
    if packing_data.total_net_weight_kg > 0:
        diff_pct = abs(sum_net - packing_data.total_net_weight_kg) / \
                   packing_data.total_net_weight_kg * 100
        if diff_pct > 1:
            products[0].warnings.append(
                f"Suma greutăților nete pe linie ({sum_net:.3f} kg) diferă "
                f"de totalul din packing list "
                f"({packing_data.total_net_weight_kg:.3f} kg) cu {diff_pct:.1f}%"
            )
    
    if packing_data.total_gross_weight_kg > 0:
        diff_pct = abs(sum_gross - packing_data.total_gross_weight_kg) / \
                   packing_data.total_gross_weight_kg * 100
        if diff_pct > 1:
            products[0].warnings.append(
                f"Suma greutăților brute pe linie ({sum_gross:.3f} kg) diferă "
                f"de totalul din packing list "
                f"({packing_data.total_gross_weight_kg:.3f} kg) cu {diff_pct:.1f}%"
            )


def _distribute_from_totals(
    products: list[ProductLine],
    packing_data: PackingListData,
):
    """
    Distribuie greutățile totale proporțional pe linii.
    Proporția se calculează pe baza valorii liniei (sau cantității ca fallback).
    """
    total_net = packing_data.total_net_weight_kg
    total_gross = packing_data.total_gross_weight_kg
    
    # Dacă nu avem gross, estimăm ca 1.1 × net
    if total_gross == 0 and total_net > 0:
        total_gross = total_net * 1.1
        products[0].warnings.append(
            f"Greutate brută estimată ca 110% din greutate netă "
            f"({total_gross:.3f} kg)"
        )
    
    # Calculăm baza de proporționalitate (preferăm valoare, altfel cantitate)
    bases = []
    use_value = all(p.line_value_original > 0 for p in products)
    
    for p in products:
        if use_value:
            bases.append(p.line_value_original)
        else:
            bases.append(max(p.quantity, 1))
    
    total_base = sum(bases)
    if total_base == 0:
        total_base = len(products)
        bases = [1.0] * len(products)
    
    # Calculăm proporțiile brute
    raw_net = [total_net * (b / total_base) for b in bases]
    raw_gross = [total_gross * (b / total_base) for b in bases]
    
    # Rotunjim păstrând totalurile
    decimals = config.WEIGHT_DECIMALS
    rounded_net = round_preserve_total(raw_net, total_net, decimals)
    rounded_gross = round_preserve_total(raw_gross, total_gross, decimals)
    
    # Aplicăm
    for i, p in enumerate(products):
        p.net_weight_kg = rounded_net[i]
        p.gross_weight_kg = rounded_gross[i]
    
    # Adăugăm warning pe prima linie
    method = "valoare" if use_value else "cantitate"
    products[0].warnings.append(
        f"Greutăți distribuite proporțional din totaluri "
        f"(baza: {method}, NW total: {total_net:.3f} kg, "
        f"GW total: {total_gross:.3f} kg)"
    )
