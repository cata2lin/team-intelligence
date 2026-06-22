"""
Serviciu de conversie valutară.
Convertește valorile din moneda originală (USD, EUR etc.) în RON.
Păstrează și valorile originale pentru audit.
"""
from etransport.models.product_line import ProductLine
from etransport.utils.rounding import safe_round
from etransport import config


def convert_currency(
    products: list[ProductLine],
    currency: str,
    exchange_rate: float,
) -> list[ProductLine]:
    """
    Convertește valorile din moneda originală în RON.
    
    Args:
        products: Lista de produse
        currency: Moneda originală (ex: USD, EUR)
        exchange_rate: Cursul valutar (1 unitate monedă → X RON)
        
    Returns:
        Lista actualizată de produse cu valori în RON
    """
    if not products:
        return products
    
    for product in products:
        product.currency = currency
        
        if exchange_rate <= 0:
            product.warnings.append(
                f"Curs valutar invalid ({exchange_rate}). "
                f"Valorile RON nu au fost calculate."
            )
            continue
        
        # Conversie valoare totală linie
        product.line_value_ron_without_vat = safe_round(
            product.line_value_original * exchange_rate,
            config.VALUE_DECIMALS,
        )
        
        # Conversie preț unitar
        product.unit_price_ron = safe_round(
            product.unit_price_original * exchange_rate,
            config.VALUE_DECIMALS + 2,  # mai multă precizie la preț unitar
        )
        
        # Dacă nu avem preț unitar dar avem cantitate, îl derivăm
        if product.unit_price_ron == 0 and product.quantity > 0:
            product.unit_price_ron = safe_round(
                product.line_value_ron_without_vat / product.quantity,
                config.VALUE_DECIMALS + 2,
            )
    
    return products


def get_exchange_rate(currency: str, manual_rate: float = None) -> float:
    """
    Obține cursul valutar pentru o monedă.
    
    Prioritate:
    1. Cursul manual (dacă este furnizat)
    2. Cursul din config
    
    Args:
        currency: Codul monedei (ex: USD, EUR)
        manual_rate: Cursul introdus manual (opțional)
        
    Returns:
        Cursul valutar
    """
    if manual_rate and manual_rate > 0:
        return manual_rate
    
    rate = config.DEFAULT_EXCHANGE_RATES.get(currency.upper())
    if rate:
        return rate
    
    # Monedă necunoscută
    return 1.0
