"""
Serviciu de agregare a liniilor de produs.
Agregă liniile cu aceeași cheie compusă:
(nc_code_8, product_name_export, display_unit, standard_unit_code, operation_purpose_code)
"""
from typing import Optional
from etransport.models.product_line import ProductLine
from etransport.utils.rounding import safe_round
from etransport import config


def aggregate_lines(
    products: list[ProductLine],
    aggregate: Optional[bool] = None,
) -> list[ProductLine]:
    """
    Agregă liniile de produs dacă este activat.
    
    Args:
        products: Lista de produse
        aggregate: Override explicit (True/False/None=config)
        
    Returns:
        Lista (eventual) agregată de produse
    """
    should_aggregate = aggregate if aggregate is not None else config.AGGREGATE_LINES
    
    if not should_aggregate or not products:
        return products
    
    # Grupăm pe cheia de agregare
    groups: dict[tuple, list[ProductLine]] = {}
    
    for product in products:
        key = _aggregation_key(product)
        if key not in groups:
            groups[key] = []
        groups[key].append(product)
    
    # Dacă nu s-a consolidat nimic, returnăm originalul
    if len(groups) == len(products):
        return products
    
    # Construim linii agregate
    aggregated = []
    for key, group in groups.items():
        if len(group) == 1:
            aggregated.append(group[0])
        else:
            merged = _merge_group(group)
            aggregated.append(merged)
    
    return aggregated


def _aggregation_key(product: ProductLine) -> tuple:
    """Generează cheia de agregare pentru un produs."""
    return (
        product.nc_code_8,
        product.product_name_export,
        product.display_unit,
        product.standard_unit_code,
        product.operation_purpose_code,
    )


def _merge_group(group: list[ProductLine]) -> ProductLine:
    """Combină un grup de linii într-una singură."""
    base = group[0].model_copy()
    
    # Colectăm liniile originale
    original_lines = []
    for p in group:
        if p.source_line_no:
            original_lines.append(p.source_line_no)
    base.aggregated_from_lines = original_lines
    
    # Sumăm cantitățile
    base.quantity = sum(p.quantity for p in group)
    
    # Sumăm valorile
    base.line_value_original = safe_round(
        sum(p.line_value_original for p in group),
        config.VALUE_DECIMALS,
    )
    base.line_value_ron_without_vat = safe_round(
        sum(p.line_value_ron_without_vat for p in group),
        config.VALUE_DECIMALS,
    )
    
    # Recalculăm prețul unitar
    if base.quantity > 0:
        base.unit_price_original = safe_round(
            base.line_value_original / base.quantity,
            config.VALUE_DECIMALS + 2,
        )
        base.unit_price_ron = safe_round(
            base.line_value_ron_without_vat / base.quantity,
            config.VALUE_DECIMALS + 2,
        )
    
    # Sumăm greutățile
    base.net_weight_kg = safe_round(
        sum(p.net_weight_kg for p in group),
        config.WEIGHT_DECIMALS,
    )
    base.gross_weight_kg = safe_round(
        sum(p.gross_weight_kg for p in group),
        config.WEIGHT_DECIMALS,
    )
    
    # Warning-uri
    base.warnings.append(
        f"Linie agregată din {len(group)} linii originale: "
        f"{original_lines}"
    )
    
    # Colectăm toate warning-urile
    for p in group[1:]:
        base.warnings.extend(p.warnings)
    
    # Match info: setăm la "aggregated"
    base.match_method = "aggregated"
    base.match_confidence = None
    base.matched_packing_line_no = None
    
    return base
