"""
Matcher history-first pentru coduri tarifare.

Ordinea de prioritate:
1. product_tariff_memory status=confirmed pe produs
2. hs_code_memory status=confirmed pe HS brut + produs / HS brut
3. legacy override pe produs
4. istoric XML exact pe produs + HS brut
5. istoric XML exact pe produs
6. istoric XML exact pe HS brut / NC8
7. catalog HS (normalizare raw -> hs8) + catalog NC8
8. catalog NC8 exact pe hs8_candidate
9. fallback controlat (cu reason)
"""
from __future__ import annotations

import unicodedata
from typing import Optional

from etransport.db import get_db_connection
from etransport.catalogs.tariff_code_repository import (
    find_best_hs_catalog_match,
    find_tariff_by_code,
    get_product_override,
    hs6,
    hs8_candidate,
    normalize_code_digits,
)
from etransport.services.tariff_memory_service import (
    get_confirmed_hs_memory,
    get_confirmed_tariff,
    get_confirmed_tariff_by_code,
    get_hs_memory_candidates,
    save_hs_memory,
    save_tariff_suggestion,
)


def _normalize(name: str) -> str:
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_str.lower().split())


def _build(status: str, code: str = "", label: str = "", method: str = "none",
           source: str = "needs_review", confidence: float = 0.0,
           source_document: Optional[str] = None, candidates: Optional[list] = None,
           needs_review_reason: str = "") -> dict:
    return {
        "status": status,
        "code": code,
        "label": label,
        "method": method,
        "source": source,
        "confidence": confidence,
        "source_document": source_document,
        "candidates": candidates or [],
        "needs_review_reason": needs_review_reason,
        "hs6": hs6(code),
        "hs8_candidate_valid": bool(code and len(normalize_code_digits(code)) >= 8),
    }


def find_historical_match(product_name_normalized: str, hs_code_raw: str = None,
                          nc_code_8: str = None) -> dict:
    norm_name = _normalize(product_name_normalized)
    hs_raw_digits = normalize_code_digits(hs_code_raw)
    hs8 = normalize_code_digits(nc_code_8) or hs8_candidate(hs_raw_digits)

    # 1. confirmed by product name
    if norm_name:
        confirmed = get_confirmed_tariff(norm_name)
        if confirmed:
            return _build(
                "matched",
                code=confirmed["tariff_code"],
                label=confirmed.get("tariff_label", ""),
                method="confirmed_memory",
                source="confirmed_memory",
                confidence=confirmed.get("confidence", 100.0),
            )

    # 2a. confirmed hs raw + name / raw only
    hs_confirmed = get_confirmed_hs_memory(hs_raw_digits, norm_name)
    if hs_confirmed:
        return _build(
            "matched",
            code=hs_confirmed["nc8_code"],
            label=hs_confirmed.get("tariff_label", ""),
            method="confirmed_hs_memory",
            source="confirmed_hs_memory",
            confidence=hs_confirmed.get("confidence", 99.0),
            source_document=hs_confirmed.get("last_seen_document"),
        )

    # 2b. old confirmed by raw code in product tariff memory
    if hs_raw_digits:
        confirmed_hs = get_confirmed_tariff_by_code(hs_raw_digits)
        if confirmed_hs:
            return _build(
                "matched",
                code=confirmed_hs["tariff_code"],
                label=confirmed_hs.get("tariff_label", ""),
                method="confirmed_memory_by_hs",
                source="confirmed_memory",
                confidence=confirmed_hs.get("confidence", 98.0),
            )

    # 3. legacy override
    if norm_name:
        override = get_product_override(norm_name)
        if override:
            return _build(
                "matched",
                code=override["matched_tariff_code"],
                label=override.get("matched_tariff_label", ""),
                method="override",
                source="override",
                confidence=override.get("confidence", 100.0) or 100.0,
            )

    # 4-6. historical XML search
    historical = _search_history(norm_name, hs_raw_digits, hs8)
    if historical:
        return historical

    # 7. hs raw memory suggestions
    hs_candidates = get_hs_memory_candidates(hs_raw_digits, norm_name, include_suggested=True)
    if hs_candidates:
        top = hs_candidates[0]
        if top.get("status") == "confirmed" or float(top.get("confidence", 0)) >= 95.0:
            return _build(
                "matched",
                code=top["nc8_code"],
                label=top.get("tariff_label", ""),
                method="hs_memory",
                source="hs_memory",
                confidence=float(top.get("confidence", 90.0)),
                source_document=top.get("last_seen_document"),
                candidates=hs_candidates[:5],
            )

    # 8. HS catalog lookup -> hs8 candidate -> NC8 catalog
    hs_catalog = find_best_hs_catalog_match(hs_raw_digits)
    if hs_catalog:
        candidate = hs_catalog.get("hs8_candidate") or hs8
        tc = find_tariff_by_code(candidate)
        if tc:
            return _build(
                "matched",
                code=tc["code"],
                label=tc.get("label", hs_catalog.get("description", "")),
                method=hs_catalog.get("lookup_method", "hs_catalog"),
                source="hs_catalog",
                confidence=84.0 if hs_catalog.get("lookup_method") == "hs_catalog_exact" else 80.0,
            )
        if candidate and len(candidate) == 8:
            return _build(
                "matched",
                code=candidate,
                label=hs_catalog.get("description", ""),
                method=hs_catalog.get("lookup_method", "hs_catalog"),
                source="hs_catalog",
                confidence=78.0,
                needs_review_reason="hs_catalog_candidate_without_tariff_label",
            )

    # 9. NC8 exact candidate in local catalog
    if hs8:
        tc = find_tariff_by_code(hs8)
        if tc:
            return _build(
                "matched",
                code=tc["code"],
                label=tc.get("label", ""),
                method="smartbill_db_exact",
                source="smartbill_db",
                confidence=82.0,
            )

    # 10. fallback controlat
    if hs8 and len(hs8) == 8:
        return _build(
            "matched",
            code=hs8,
            label="",
            method="fallback_nc8",
            source="fallback_nc8",
            confidence=50.0,
            candidates=hs_candidates[:5] if hs_candidates else [],
            needs_review_reason="no_strong_candidate",
        )

    return _build(
        "not_found",
        code=hs8,
        method="none",
        source="needs_review",
        confidence=0.0,
        candidates=hs_candidates[:5] if hs_candidates else [],
        needs_review_reason="invalid_or_missing_hs_code",
    )


def _search_history(norm_name: str, hs_code_raw: str, hs8: str) -> Optional[dict]:
    with get_db_connection() as conn:
        cur = conn.cursor()

        if norm_name and hs_code_raw:
            cur.execute(
                """
                SELECT h.*, d.uit_code, d.source_file, d.document_number
                FROM etransport_product_history h
                JOIN etransport_documents d ON d.id = h.document_id
                WHERE h.product_name_normalized = ? AND (
                    h.hs_code_raw = ? OR h.nc_code_8 = ? OR h.tariff_code_final = ?
                )
                ORDER BY d.transport_date DESC, d.imported_at DESC
                LIMIT 1
                """,
                (norm_name, hs_code_raw, hs8, hs8),
            )
            row = cur.fetchone()
            if row:
                r = dict(row)
                save_tariff_suggestion(norm_name, r["tariff_code_final"], r.get("tariff_label_final", ""),
                                       hs_code_raw=hs_code_raw, nc_code_8=r.get("nc_code_8", hs8),
                                       source="historical", confidence=96.0)
                save_hs_memory(hs_code_raw, r["tariff_code_final"], product_name_normalized=norm_name,
                               tariff_label=r.get("tariff_label_final", ""), source="historical_exact",
                               confidence=96.0, status="suggested", document_no=r.get("document_number") or r.get("uit_code") or "")
                return _build(
                    "matched",
                    code=r["tariff_code_final"],
                    label=r.get("tariff_label_final", ""),
                    method="historical_name_hs",
                    source="historical",
                    confidence=96.0,
                    source_document=r.get("uit_code") or r.get("source_file", ""),
                )

        if norm_name:
            cur.execute(
                """
                SELECT h.*, d.uit_code, d.source_file, d.document_number
                FROM etransport_product_history h
                JOIN etransport_documents d ON d.id = h.document_id
                WHERE h.product_name_normalized = ?
                ORDER BY d.transport_date DESC, d.imported_at DESC
                LIMIT 1
                """,
                (norm_name,),
            )
            row = cur.fetchone()
            if row:
                r = dict(row)
                save_tariff_suggestion(norm_name, r["tariff_code_final"], r.get("tariff_label_final", ""),
                                       hs_code_raw=hs_code_raw or "", nc_code_8=r.get("nc_code_8", hs8),
                                       source="historical", confidence=92.0)
                if hs_code_raw:
                    save_hs_memory(hs_code_raw, r["tariff_code_final"], product_name_normalized=norm_name,
                                   tariff_label=r.get("tariff_label_final", ""), source="historical_name",
                                   confidence=92.0, status="suggested",
                                   document_no=r.get("document_number") or r.get("uit_code") or "")
                return _build(
                    "matched",
                    code=r["tariff_code_final"],
                    label=r.get("tariff_label_final", ""),
                    method="historical_name",
                    source="historical",
                    confidence=92.0,
                    source_document=r.get("uit_code") or r.get("source_file", ""),
                )

        if hs_code_raw:
            cur.execute(
                """
                SELECT h.*, d.uit_code, d.source_file, d.document_number
                FROM etransport_product_history h
                JOIN etransport_documents d ON d.id = h.document_id
                WHERE h.hs_code_raw = ? OR h.nc_code_8 = ? OR h.tariff_code_final = ?
                ORDER BY d.transport_date DESC, d.imported_at DESC
                LIMIT 1
                """,
                (hs_code_raw, hs8, hs8),
            )
            row = cur.fetchone()
            if row:
                r = dict(row)
                save_hs_memory(hs_code_raw, r["tariff_code_final"], product_name_normalized=norm_name or "",
                               tariff_label=r.get("tariff_label_final", ""), source="historical_hs",
                               confidence=86.0, status="suggested",
                               document_no=r.get("document_number") or r.get("uit_code") or "")
                return _build(
                    "matched",
                    code=r["tariff_code_final"],
                    label=r.get("tariff_label_final", ""),
                    method="historical_hs",
                    source="historical",
                    confidence=86.0,
                    source_document=r.get("uit_code") or r.get("source_file", ""),
                )

    return None
