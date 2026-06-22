#!/usr/bin/env python3
"""
sheets_labels.py — Generare label-uri + barcode-uri din Google Sheets.

Citește spreadsheet-ul, pentru rândurile fără barcode/label:
1. Generează barcode EAN13
2. Generează imagine label
3. Salvează label-ul pe server
4. Scrie barcode-ul și link-ul label-ului înapoi în spreadsheet

Utilizare:
    python sheets_labels.py
    python sheets_labels.py --sheet "TOM - WINNER_WORK"
    python sheets_labels.py --dry-run
"""

import argparse
import os
import random
import sys
import textwrap
import time
from io import BytesIO
from pathlib import Path

import gspread
import httpx
from PIL import Image, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
from unidecode import unidecode

# ─── Config ───────────────────────────────────────────────────

SPREADSHEET_ID = "10eSCKItlCHMl8S5A2YGjBZBZwRe506HH0ETpgR7BV7A"
CREDENTIALS_FILE = Path(__file__).parent / "google_credentials.json"
BASE_URL = "http://84.46.242.181:8080"
LABELS_DIR = Path(__file__).parent / "data" / "labels"

SHEET_NAMES = [
    "✅ TOM - WINNER_WORK",
    "✅ TOM - TO BE VERIFIED_WORK",
]

# Column indices (0-based) from headers
COL_SKU = 0           # A - SKU
COL_TITLE = 1         # B - Product title
COL_IMAGE_URL = 3     # D - Image URL
COL_LABEL = 16        # Q - Label
COL_BARCODE = 17      # R - Barcode


def log(msg: str) -> None:
    print(msg, flush=True)


# ─── Barcode Generation ──────────────────────────────────────

def generate_ean13() -> str:
    """Generate a random EAN13 barcode (200xxx prefix for internal use)."""
    base = "200" + "".join([str(random.randint(0, 9)) for _ in range(9)])
    dummy = barcode.get('ean13', base)
    return dummy.get_fullcode()


# ─── Label Drawing ────────────────────────────────────────────

def draw_label(sku: str, title: str, image_bytes: bytes, barcode_str: str, out_path: str):
    """Generate a product label image."""
    W, H = 600, 900
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    try:
        font_regular = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font_regular = ImageFont.load_default()
        font_bold = font_regular
        font_small = font_regular

    # SKU
    draw.text((40, 30), sku, fill="black", font=font_bold)
    draw.line([(30, 80), (W - 30, 80)], fill="black", width=3)

    # Title
    clean_title = unidecode(title) if title else sku
    if len(clean_title) > 45:
        clean_title = clean_title[:42] + "..."
    wrapped = textwrap.fill(clean_title, width=35)
    draw.multiline_text((40, 100), wrapped, fill="black", font=font_regular, spacing=8)
    lines = wrapped.split("\n")
    title_bottom = 100 + len(lines) * 35 + 40

    draw.line([(30, title_bottom), (W - 30, title_bottom)], fill="black", width=3)

    # Importator
    y_mid = title_bottom + 30
    imp_text = "Importator: ARONA SRL,\nStr. Dunărea 9, BL. M11,\nloc. Calarasi, jud\nCălărași, România.\ntel: 0745285476"
    draw.multiline_text((40, y_mid), imp_text, fill="black", font=font_small, spacing=6)

    # Product image — old style, bigger
    if image_bytes:
        try:
            prod_img = Image.open(BytesIO(image_bytes)).convert("RGB")
            prod_img.thumbnail((250, 250), Image.Resampling.LANCZOS)
            pw, ph = prod_img.size
            img.paste(prod_img, (W - pw - 30, y_mid))
        except Exception:
            pass

    # Bottom section — right after content
    y_bottom = y_mid + 320
    draw.line([(30, y_bottom), (W - 30, y_bottom)], fill="black", width=3)
    draw.text((40, y_bottom + 20), "Fabricat în\nChina", fill="black", font=font_regular)

    # Barcode
    EAN = barcode.get_barcode_class('ean13')
    my_ean = EAN(barcode_str, writer=ImageWriter())
    opts = {
        "module_width": 0.35, "module_height": 15.0,
        "font_size": 12, "text_distance": 5.0, "quiet_zone": 2.0,
        "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    }
    bc_io = BytesIO()
    my_ean.write(bc_io, options=opts)
    bc_io.seek(0)
    bc_img = Image.open(bc_io).convert("RGBA")
    bw, bh = bc_img.size
    img.paste(bc_img, (W - bw - 20, y_bottom + 10), bc_img)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


# ─── Main Logic ───────────────────────────────────────────────

def process_sheet(gc: gspread.Client, sheet_name: str, dry_run: bool = False) -> dict:
    """Process a single sheet — generate labels for rows missing barcode."""
    log(f"\n📋 Sheet: {sheet_name}")

    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        log(f"  ❌ Sheet negăsit: {sheet_name}")
        return {"processed": 0, "skipped": 0, "errors": 0}

    all_rows = ws.get_all_values()
    if not all_rows:
        log("  ⚠ Sheet gol")
        return {"processed": 0, "skipped": 0, "errors": 0}

    headers = all_rows[0]
    log(f"  {len(all_rows) - 1} rânduri, {len(headers)} coloane")

    # Find column indices dynamically
    col_sku = col_title = col_img = col_label = col_barcode = col_qty = -1
    for i, h in enumerate(headers):
        hl = h.strip().lower()
        if hl == "sku":
            col_sku = i
        elif hl == "product title":
            col_title = i
        elif hl == "image url":
            col_img = i
        elif hl == "label":
            col_label = i
        elif hl == "barcode":
            col_barcode = i
        elif hl == "quantity est":
            col_qty = i

    if col_sku < 0 or col_barcode < 0:
        log(f"  ❌ Coloane SKU/Barcode negăsite. Headers: {headers}")
        return {"processed": 0, "skipped": 0, "errors": 0}

    processed = 0
    skipped = 0
    errors = 0

    # Batch updates for efficiency
    barcode_updates = []
    label_updates = []

    with httpx.Client() as client:
        for row_idx, row in enumerate(all_rows[1:], start=2):  # row_idx = sheet row (1-indexed, +1 for header)
            try:
                # Pad row if needed
                while len(row) <= max(col_barcode, col_label if col_label >= 0 else 0):
                    row.append("")

                sku = row[col_sku].strip() if col_sku < len(row) else ""
                title = row[col_title].strip() if col_title >= 0 and col_title < len(row) else ""
                img_url = row[col_img].strip() if col_img >= 0 and col_img < len(row) else ""
                existing_barcode = row[col_barcode].strip() if col_barcode < len(row) else ""
                existing_label = row[col_label].strip() if col_label >= 0 and col_label < len(row) else ""

                if not sku:
                    continue

                # Skip if Quantity Est is 0 or blank
                qty_str = row[col_qty].strip() if col_qty >= 0 and col_qty < len(row) else ""
                if not qty_str or qty_str == "0":
                    continue

                if existing_barcode and existing_label:
                    skipped += 1
                    continue

                # Generate barcode if missing
                bc = existing_barcode
                if not bc:
                    bc = generate_ean13()

                # Generate label if missing
                label_url = existing_label
                if not label_url:
                    # Download product image
                    img_bytes = None
                    if img_url:
                        try:
                            resp = client.get(img_url, timeout=10, follow_redirects=True)
                            if resp.status_code == 200:
                                img_bytes = resp.content
                        except Exception:
                            pass

                    # Generate label image
                    safe_sku = sku.replace("/", "_").replace(" ", "_").replace("\\", "_")
                    label_filename = f"label_{safe_sku}.png"
                    label_path = LABELS_DIR / label_filename

                    if not dry_run:
                        draw_label(sku, title, img_bytes, bc, str(label_path))
                    label_url = f"{BASE_URL}/labels/{label_filename}"

                if not dry_run:
                    # Queue batch updates (gspread uses A1 notation)
                    bc_cell = gspread.utils.rowcol_to_a1(row_idx, col_barcode + 1)
                    barcode_updates.append({"range": bc_cell, "values": [[bc]]})

                    if col_label >= 0:
                        lbl_cell = gspread.utils.rowcol_to_a1(row_idx, col_label + 1)
                        label_updates.append({"range": lbl_cell, "values": [[label_url]]})

                processed += 1

                # Batch write every 50 rows to avoid rate limits
                if len(barcode_updates) >= 50:
                    ws.batch_update(barcode_updates + label_updates)
                    barcode_updates.clear()
                    label_updates.clear()
                    time.sleep(1)

            except Exception as e:
                log(f"  ❌ Rând {row_idx} ({sku}): {e}")
                errors += 1

    # Final batch write
    if not dry_run and (barcode_updates or label_updates):
        try:
            ws.batch_update(barcode_updates + label_updates)
        except Exception as e:
            log(f"  ❌ Eroare batch update: {e}")
            errors += 1

    tag = "🏷️" if not dry_run else "🔍 DRY-RUN"
    log(f"  {tag} {processed} generate, {skipped} deja setate, {errors} erori")
    return {"processed": processed, "skipped": skipped, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generare label-uri și barcode-uri din Google Sheets")
    parser.add_argument("--sheet", default="", help="Nume sheet specific (altfel procesează toate)")
    parser.add_argument("--dry-run", action="store_true", help="Doar afișează ce ar face, nu scrie")
    parser.add_argument("--credentials", default="", help="Cale către fișier credentials JSON")
    args = parser.parse_args()

    creds_path = Path(args.credentials) if args.credentials else CREDENTIALS_FILE
    if not creds_path.exists():
        # Try in current dir
        creds_path = Path.cwd() / "google_credentials.json"
    if not creds_path.exists():
        log("❌ Fișier credentials negăsit. Pune google_credentials.json lângă script.")
        return 1

    # Ensure labels directory exists
    LABELS_DIR.mkdir(parents=True, exist_ok=True)

    # Authenticate
    gc = gspread.service_account(filename=str(creds_path))
    log(f"✅ Autentificat cu Google Sheets API")

    # Process sheets
    sheets = [args.sheet] if args.sheet else SHEET_NAMES
    total = {"processed": 0, "skipped": 0, "errors": 0}

    for sheet_name in sheets:
        result = process_sheet(gc, sheet_name, dry_run=args.dry_run)
        total["processed"] += result["processed"]
        total["skipped"] += result["skipped"]
        total["errors"] += result["errors"]

    log(f"\n{'='*50}")
    log(f"✅ Total: {total['processed']} generate, {total['skipped']} deja setate, {total['errors']} erori")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
