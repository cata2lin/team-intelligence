"""
Normalizare text: lowercase, eliminare diacritice, spații multiple, caractere speciale.
Folosit pentru matching între invoice și packing list.
"""
import re
from unidecode import unidecode


def normalize_text(text: str) -> str:
    """Normalizează textul: lowercase, fără diacritice, spații compactate."""
    if not text:
        return ""
    result = unidecode(text)         # Elimină diacritice
    result = result.lower().strip()  # Lowercase
    result = re.sub(r'[^\w\s]', ' ', result)   # Punctuație → spațiu
    result = re.sub(r'\s+', ' ', result)        # Spații multiple → 1
    return result.strip()


def normalize_unit(text: str) -> str:
    """Normalizează unitatea de măsură pentru lookup în config."""
    if not text:
        return ""
    result = text.lower().strip()
    result = re.sub(r'[.\s]', '', result)  # Elimină puncte și spații
    return result


def clean_numeric_string(text: str) -> str:
    """Curăță un string numeric: elimină spații, virgule ca separator mii."""
    if not text:
        return "0"
    text = text.strip()
    # Dacă are și virgulă și punct, virgula e separator de mii
    if ',' in text and '.' in text:
        text = text.replace(',', '')
    # Dacă are doar virgulă, tratăm ca separator de zecimale
    elif ',' in text:
        text = text.replace(',', '.')
    # Elimină tot ce nu e cifră sau punct
    text = re.sub(r'[^\d.]', '', text)
    return text or "0"


def extract_numeric(text: str) -> float:
    """Extrage valoarea numerică dintr-un string."""
    try:
        return float(clean_numeric_string(text))
    except (ValueError, TypeError):
        return 0.0
