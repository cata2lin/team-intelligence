"""
Servicii pentru memoria TARIC și memoria HS brut -> NC8.

- product_tariff_memory: sugestii / confirmări pe produs
- hs_code_memory: memorie de legătură HS brut + produs -> NC8 confirmat/sugerat
"""
from typing import Optional

from etransport.db import get_db_connection
from etransport.catalogs.tariff_code_repository import normalize_code_digits, hs6, hs8_candidate


# ──────────────────────────────────────────────────────────────────────────────
# Product TARIC memory
# ──────────────────────────────────────────────────────────────────────────────

def save_tariff_suggestion(product_name_normalized: str, tariff_code: str,
                           tariff_label: str = "", hs_code_raw: str = "",
                           nc_code_8: str = "", source: str = "historical",
                           confidence: float = 0.0):
    tariff_code = normalize_code_digits(tariff_code)
    if not product_name_normalized or not tariff_code:
        return
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO product_tariff_memory
                (product_name_normalized, hs_code_raw, nc_code_8, tariff_code, tariff_label, status, source, confidence)
            VALUES (?, ?, ?, ?, ?, 'suggested', ?, ?)
            ON CONFLICT(product_name_normalized, tariff_code) DO UPDATE SET
                hs_code_raw = COALESCE(NULLIF(excluded.hs_code_raw, ''), product_tariff_memory.hs_code_raw),
                nc_code_8 = COALESCE(NULLIF(excluded.nc_code_8, ''), product_tariff_memory.nc_code_8),
                tariff_label = COALESCE(NULLIF(excluded.tariff_label, ''), product_tariff_memory.tariff_label),
                confidence = MAX(excluded.confidence, product_tariff_memory.confidence),
                source = excluded.source
            """,
            (product_name_normalized, hs_code_raw, nc_code_8, tariff_code, tariff_label, source, confidence),
        )
        conn.commit()


def confirm_tariff(product_name_normalized: str, tariff_code: str, notes: str = "",
                   hs_code_raw: str = "", nc_code_8: str = "", supplier_name: str = "",
                   tariff_label: str = ""):
    tariff_code = normalize_code_digits(tariff_code)
    hs_code_raw = normalize_code_digits(hs_code_raw)
    nc_code_8 = normalize_code_digits(nc_code_8) or hs8_candidate(hs_code_raw) or tariff_code
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE product_tariff_memory
            SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP,
                notes = CASE WHEN ? != '' THEN ? ELSE notes END,
                hs_code_raw = COALESCE(NULLIF(?, ''), hs_code_raw),
                nc_code_8 = COALESCE(NULLIF(?, ''), nc_code_8),
                tariff_label = COALESCE(NULLIF(?, ''), tariff_label)
            WHERE product_name_normalized = ? AND tariff_code = ?
            """,
            (notes, notes, hs_code_raw, nc_code_8, tariff_label, product_name_normalized, tariff_code),
        )
        updated = cur.rowcount > 0
        if updated and hs_code_raw:
            save_hs_memory(
                hs_code_raw=hs_code_raw,
                product_name_normalized=product_name_normalized,
                nc8_code=tariff_code,
                tariff_label=tariff_label,
                source="user_confirmed",
                confidence=100.0,
                status="confirmed",
                supplier_name=supplier_name,
                notes=notes,
            )
        conn.commit()
        return updated


def reject_tariff(product_name_normalized: str, tariff_code: str, notes: str = ""):
    tariff_code = normalize_code_digits(tariff_code)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE product_tariff_memory
            SET status = 'rejected', notes = ?
            WHERE product_name_normalized = ? AND tariff_code = ?
            """,
            (notes, product_name_normalized, tariff_code),
        )
        conn.commit()
        return cur.rowcount > 0


def get_confirmed_tariff(product_name_normalized: str) -> Optional[dict]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM product_tariff_memory
            WHERE product_name_normalized = ? AND status = 'confirmed'
            ORDER BY confidence DESC, confirmed_at DESC
            LIMIT 1
            """,
            (product_name_normalized,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_confirmed_tariff_by_code(hs_code_raw: str) -> Optional[dict]:
    hs_code_raw = normalize_code_digits(hs_code_raw)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM product_tariff_memory
            WHERE hs_code_raw = ? AND status = 'confirmed'
            ORDER BY confidence DESC, confirmed_at DESC
            LIMIT 1
            """,
            (hs_code_raw,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_suggestions_for_product(product_name_normalized: str) -> list:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM product_tariff_memory
            WHERE product_name_normalized = ? AND status != 'rejected'
            ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'suggested' THEN 1 ELSE 2 END,
                     confidence DESC, created_at DESC
            """,
            (product_name_normalized,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_all_pending_suggestions() -> list:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM product_tariff_memory WHERE status = 'suggested' ORDER BY created_at DESC"
        )
        return [dict(row) for row in cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
# HS raw -> NC8 memory
# ──────────────────────────────────────────────────────────────────────────────

def save_hs_memory(hs_code_raw: str, nc8_code: str, product_name_normalized: str = "",
                   tariff_label: str = "", source: str = "historical", confidence: float = 0.0,
                   status: str = "suggested", supplier_name: str = "", document_no: str = "",
                   notes: str = ""):
    hs_digits = normalize_code_digits(hs_code_raw)
    nc8_code = normalize_code_digits(nc8_code)
    if not hs_digits or not nc8_code:
        return
    name = product_name_normalized or ""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO hs_code_memory
                (hs_code_raw, hs_code_raw_digits, hs6, hs8_candidate, product_name_normalized,
                 nc8_code, tariff_label, status, source, confidence, supplier_name,
                 first_seen_document, last_seen_document, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hs_code_raw_digits, product_name_normalized, nc8_code) DO UPDATE SET
                tariff_label = COALESCE(NULLIF(excluded.tariff_label, ''), hs_code_memory.tariff_label),
                confidence = MAX(excluded.confidence, hs_code_memory.confidence),
                source = excluded.source,
                supplier_name = COALESCE(NULLIF(excluded.supplier_name, ''), hs_code_memory.supplier_name),
                status = CASE
                    WHEN hs_code_memory.status = 'confirmed' THEN 'confirmed'
                    ELSE excluded.status
                END,
                last_seen_document = COALESCE(NULLIF(excluded.last_seen_document, ''), hs_code_memory.last_seen_document),
                times_seen = hs_code_memory.times_seen + 1,
                updated_at = CURRENT_TIMESTAMP,
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE hs_code_memory.notes END
            """,
            (
                hs_digits,
                hs_digits,
                hs6(hs_digits),
                hs8_candidate(hs_digits),
                name,
                nc8_code,
                tariff_label,
                status,
                source,
                confidence,
                supplier_name,
                document_no,
                document_no,
                notes,
            ),
        )
        conn.commit()


def get_confirmed_hs_memory(hs_code_raw: str, product_name_normalized: str = "") -> Optional[dict]:
    hs_digits = normalize_code_digits(hs_code_raw)
    name = product_name_normalized or ""
    if not hs_digits:
        return None
    with get_db_connection() as conn:
        cur = conn.cursor()
        if name:
            cur.execute(
                """
                SELECT * FROM hs_code_memory
                WHERE hs_code_raw_digits = ? AND product_name_normalized = ? AND status = 'confirmed'
                ORDER BY confidence DESC, times_seen DESC, updated_at DESC
                LIMIT 1
                """,
                (hs_digits, name),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        cur.execute(
            """
            SELECT * FROM hs_code_memory
            WHERE hs_code_raw_digits = ? AND status = 'confirmed'
            ORDER BY confidence DESC, times_seen DESC, updated_at DESC
            LIMIT 1
            """,
            (hs_digits,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_hs_memory_candidates(hs_code_raw: str, product_name_normalized: str = "", include_suggested: bool = True) -> list:
    hs_digits = normalize_code_digits(hs_code_raw)
    if not hs_digits:
        return []
    name = product_name_normalized or ""
    statuses = ["confirmed"] + (["suggested"] if include_suggested else [])
    placeholders = ",".join("?" for _ in statuses)
    with get_db_connection() as conn:
        cur = conn.cursor()
        if name:
            cur.execute(
                f"""
                SELECT * FROM hs_code_memory
                WHERE hs_code_raw_digits = ? AND product_name_normalized IN (?, '') AND status IN ({placeholders})
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END,
                         confidence DESC, times_seen DESC, updated_at DESC
                """,
                (hs_digits, name, *statuses),
            )
        else:
            cur.execute(
                f"""
                SELECT * FROM hs_code_memory
                WHERE hs_code_raw_digits = ? AND status IN ({placeholders})
                ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END,
                         confidence DESC, times_seen DESC, updated_at DESC
                """,
                (hs_digits, *statuses),
            )
        return [dict(row) for row in cur.fetchall()]
