"""
Serviciu pentru memoria transportatorilor.
Transportatorii se salvează automat, fără confirmare.
"""
import unicodedata
from etransport.db import get_db_connection


def _normalize_name(name: str) -> str:
    """Normalizează un nume de transportator: uppercase, fără diacritice, fără spații extra."""
    if not name:
        return ""
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_str.upper().split())


def save_carrier(carrier_name: str, carrier_vat: str, source_document_id: int = None):
    """
    Salvează automat un transportator în istoric.
    Dacă există deja → incrementează times_seen și actualizează last_seen_at.
    """
    if not carrier_name or not carrier_vat:
        return
    
    normalized = _normalize_name(carrier_name)
    
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO carrier_history (carrier_name, carrier_name_normalized, carrier_vat, source_document_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(carrier_name_normalized, carrier_vat) DO UPDATE SET
                last_seen_at = CURRENT_TIMESTAMP,
                times_seen = carrier_history.times_seen + 1,
                source_document_id = COALESCE(excluded.source_document_id, carrier_history.source_document_id)
        """, (carrier_name.strip(), normalized, carrier_vat.strip(), source_document_id))
        conn.commit()


def search_carriers(query: str, limit: int = 10) -> list:
    """
    Caută transportatori după nume sau CUI.
    Returnează o listă de dicts [{carrier_name, carrier_vat, times_seen, last_seen_at}].
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        search = f"%{query.upper()}%"
        cur.execute("""
            SELECT carrier_name, carrier_vat, times_seen, last_seen_at
            FROM carrier_history
            WHERE carrier_name_normalized LIKE ? OR carrier_vat LIKE ?
            ORDER BY times_seen DESC, last_seen_at DESC
            LIMIT ?
        """, (search, search, limit))
        return [dict(row) for row in cur.fetchall()]


def get_all_carriers() -> list:
    """Returnează toți transportatorii din istoric, ordonați după frecvență."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT carrier_name, carrier_vat, times_seen, last_seen_at
            FROM carrier_history
            ORDER BY times_seen DESC, last_seen_at DESC
        """)
        return [dict(row) for row in cur.fetchall()]
