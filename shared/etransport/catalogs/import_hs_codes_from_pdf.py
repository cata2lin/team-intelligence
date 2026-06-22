"""
Import nomenclator coduri HS din PDF în DB.

Utilizare:
    python -m etransport.catalogs.import_hs_codes_from_pdf
sau:
    python etransport/catalogs/import_hs_codes_from_pdf.py --pdf ~/Downloads/HSCode\ Master\ BPS.pdf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import pdfplumber

# Ensure project root is in path when run as script
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from etransport.db import init_db
from etransport.catalogs.tariff_code_repository import normalize_code_digits, upsert_hs_code

DEFAULT_PDF = os.path.expanduser("~/Downloads/HSCode Master BPS.pdf")
DEFAULT_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "hs_codes_catalog.json")
_ROW_RE = re.compile(r"^\s*(\d+)\s+(\d{4,12})\s+(.+?)\s*$")
_CODE_START_RE = re.compile(r"^(\d{4,12})\b\s*(.*)$")


def extract_rows_from_pdf(pdf_path: str) -> list[dict]:
    rows: list[dict] = []
    pending = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = " ".join(raw_line.split())
                if not line:
                    continue
                if "HS Code" in line and "Description" in line:
                    continue
                if line.lower().startswith("no "):
                    continue

                m = _ROW_RE.match(line)
                if m:
                    if pending and pending.get("description"):
                        rows.append(pending)
                    pending = {
                        "row_no": m.group(1),
                        "hs_code": m.group(2),
                        "description": m.group(3).strip(),
                        "page": page_no,
                    }
                    continue

                # Fallback: line starts directly with an HS code, often after table extraction broke.
                m2 = _CODE_START_RE.match(line)
                if m2 and pending is None:
                    pending = {
                        "row_no": "",
                        "hs_code": m2.group(1),
                        "description": m2.group(2).strip(),
                        "page": page_no,
                    }
                    continue

                # Continuation line for previous description.
                if pending is not None:
                    pending["description"] = f"{pending['description']} {line}".strip()

    if pending and pending.get("description"):
        rows.append(pending)
    return rows


def clean_rows(rows: list[dict]) -> list[dict]:
    cleaned = []
    seen = set()
    for r in rows:
        code = normalize_code_digits(r.get("hs_code"))
        desc = " ".join((r.get("description") or "").split())
        if not code or not desc:
            continue
        key = (code, desc)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "hs_code": code,
            "description": desc,
            "page": r.get("page"),
        })
    return cleaned


def import_rows(rows: list[dict], source_file: str, source_version: str = "HSCode Master BPS") -> dict:
    inserted = 0
    updated = 0
    valid = 0
    serializable = []

    init_db()
    for row in rows:
        code = row["hs_code"]
        desc = row["description"]
        if not code.isdigit() or not desc:
            continue
        valid += 1
        ok = upsert_hs_code(code, desc, source_file=source_file, source_type="pdf_import", source_version=source_version)
        if ok:
            inserted += 1  # SQLite upsert does not distinguish cheaply here; keep as processed count.
        serializable.append({"hs_code": code, "description": desc})

    os.makedirs(os.path.dirname(DEFAULT_JSON), exist_ok=True)
    with open(DEFAULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(serializable, fh, ensure_ascii=False, indent=2)

    return {
        "source_file": source_file,
        "detected": len(rows),
        "valid": valid,
        "processed": inserted,
        "updated": updated,
        "json_backup": DEFAULT_JSON,
    }


def main():
    parser = argparse.ArgumentParser(description="Import HS code catalog from PDF")
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="Path către PDF")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"❌ PDF-ul nu există: {args.pdf}")
        raise SystemExit(1)

    raw_rows = extract_rows_from_pdf(args.pdf)
    cleaned = clean_rows(raw_rows)
    result = import_rows(cleaned, source_file=os.path.basename(args.pdf))

    print("=" * 60)
    print("RAPORT IMPORT HS CODES")
    print("=" * 60)
    print(f"PDF sursă: {result['source_file']}")
    print(f"Total rânduri detectate: {result['detected']}")
    print(f"Total coduri valide: {result['valid']}")
    print(f"Total procesate (upsert): {result['processed']}")
    print(f"Backup JSON: {result['json_backup']}")


if __name__ == "__main__":
    main()
