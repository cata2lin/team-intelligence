import re
from typing import Optional

from etransport.db import get_db_connection


_DIGITS_RE = re.compile(r"\D+")


def normalize_code_digits(code: Optional[str]) -> str:
    if not code:
        return ""
    return _DIGITS_RE.sub("", str(code))


def hs6(code: Optional[str]) -> str:
    digits = normalize_code_digits(code)
    return digits[:6] if len(digits) >= 6 else digits


def hs8_candidate(code: Optional[str]) -> str:
    digits = normalize_code_digits(code)
    return digits[:8] if len(digits) >= 8 else digits


def find_tariff_by_code(code: str):
    digits = normalize_code_digits(code)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tariff_codes WHERE code = ? AND is_active = 1", (digits,))
        row = cursor.fetchone()
        return dict(row) if row else None


def search_tariff_by_label(query: str, limit: int = 10):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        search_term = f"%{query}%"
        cursor.execute(
            """
            SELECT * FROM tariff_codes
            WHERE (label LIKE ? OR code LIKE ? OR full_display LIKE ?) AND is_active = 1
            ORDER BY LENGTH(code) ASC
            LIMIT ?
            """,
            (search_term, search_term, search_term, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_product_override(product_name_normalized: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM product_tariff_overrides WHERE product_name_normalized = ?",
            (product_name_normalized,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def save_product_override(product_name_normalized: str, matched_code: str, matched_label: str,
                          raw_hs: str = None, source: str = "manual", notes: str = None):
    matched_code = normalize_code_digits(matched_code)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO product_tariff_overrides
                (product_name_normalized, hs_code_raw, matched_tariff_code, matched_tariff_label, confidence, source, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_name_normalized) DO UPDATE SET
                hs_code_raw=excluded.hs_code_raw,
                matched_tariff_code=excluded.matched_tariff_code,
                matched_tariff_label=excluded.matched_tariff_label,
                confidence=excluded.confidence,
                source=excluded.source,
                notes=excluded.notes,
                created_at=CURRENT_TIMESTAMP
            """,
            (product_name_normalized, raw_hs, matched_code, matched_label, 100.0, source, notes),
        )
        conn.commit()


def upsert_tariff_code(code: str, label: str, full_display: str, source: str = "smartbill"):
    code = normalize_code_digits(code)
    if not code:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tariff_codes (code, label, full_display, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                label=excluded.label,
                full_display=excluded.full_display,
                source=excluded.source,
                is_active=1
            """,
            (code, label, full_display, source),
        )
        conn.commit()


def upsert_hs_code(code: str, description: str, source_file: str = "", source_type: str = "pdf_import",
                   source_version: str = ""):
    digits = normalize_code_digits(code)
    if not digits or not description:
        return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO hs_codes_catalog
                (hs_code, hs_digits, hs_length, chapter2, heading4, subheading6, hs8_candidate,
                 description, source_file, source_type, source_version, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(hs_code) DO UPDATE SET
                hs_digits=excluded.hs_digits,
                hs_length=excluded.hs_length,
                chapter2=excluded.chapter2,
                heading4=excluded.heading4,
                subheading6=excluded.subheading6,
                hs8_candidate=excluded.hs8_candidate,
                description=excluded.description,
                source_file=excluded.source_file,
                source_type=excluded.source_type,
                source_version=excluded.source_version,
                is_active=1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                digits,
                digits,
                len(digits),
                digits[:2] if len(digits) >= 2 else "",
                digits[:4] if len(digits) >= 4 else "",
                digits[:6] if len(digits) >= 6 else "",
                hs8_candidate(digits),
                description.strip(),
                source_file,
                source_type,
                source_version,
            ),
        )
        conn.commit()
        return True


def find_hs_catalog_exact(code: str):
    digits = normalize_code_digits(code)
    if not digits:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hs_codes_catalog WHERE hs_digits = ? AND is_active = 1 LIMIT 1", (digits,))
        row = cursor.fetchone()
        return dict(row) if row else None


def find_hs_catalog_by_hs8(code: str):
    hs8 = hs8_candidate(code)
    if not hs8:
        return []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM hs_codes_catalog WHERE hs8_candidate = ? AND is_active = 1 ORDER BY hs_length DESC, hs_digits ASC",
            (hs8,),
        )
        return [dict(row) for row in cursor.fetchall()]


def find_best_hs_catalog_match(code: str):
    exact = find_hs_catalog_exact(code)
    if exact:
        exact["lookup_method"] = "hs_catalog_exact"
        return exact
    matches = find_hs_catalog_by_hs8(code)
    if matches:
        m = matches[0]
        m["lookup_method"] = "hs_catalog_hs8"
        return m
    return None
