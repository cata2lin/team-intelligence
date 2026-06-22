"""
XML History Importer for e-Transport.

Scans folder for e-Transport XML files and imports them into local DB.
Extracts: documents, product lines, carriers.

Deduplication strategy: file SHA-256 hash (file_hash column, UNIQUE).
"""
import argparse
import hashlib
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from etransport.db import init_db, get_db_connection
from etransport.services.carrier_history_service import save_carrier
from etransport.services.tariff_memory_service import save_hs_memory, save_tariff_suggestion

DEFAULT_FOLDER = os.path.expanduser("~/Downloads/Etransport")
NS = {"etr": "mfp:anaf:dgti:eTransport:declaratie:v2"}

# Map codTipOperatiune to human-readable name
OP_MAP = {"10": "achizitie_intracomunitara", "20": "livrare_intracomunitara",
           "30": "export", "40": "import", "50": "tranzit", "60": "operatiune_nefiscala"}


def _normalize(name: str) -> str:
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_str.lower().split())


def _file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_uit(filename: str) -> str:
    """Extract UIT code from filename like e-Transport_0L4Q401952830170_2026-04-01.xml"""
    m = re.search(r"e-[Tt]ransport_([A-Za-z0-9]+)_", filename)
    return m.group(1) if m else ""


def _sanitize_xml(filepath: str) -> str:
    """Read XML file and fix common truncation issues (missing closing tags)."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Strip trailing whitespace
    content = content.rstrip()
    
    # If file is truncated mid-attribute or mid-tag, try to close it
    # Check if we have proper closing tags
    if not content.endswith("</eTransport>"):
        # Try to close any open attribute value
        # Count unclosed quotes
        in_tag = False
        last_gt = content.rfind(">")
        last_lt = content.rfind("<")
        
        # If last < is after last >, we're inside an unclosed tag
        if last_lt > last_gt:
            # Truncate the incomplete tag
            content = content[:last_lt]
        
        # Now ensure we have proper closing hierarchy
        if "</notificare>" not in content:
            content += "\n    </notificare>"
        if "</eTransport>" not in content:
            content += "\n</eTransport>"
    
    return content


def parse_xml(filepath: str) -> dict:
    """Parse a single e-Transport XML file and return structured data."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError:
        # Try sanitized version
        sanitized = _sanitize_xml(filepath)
        root = ET.fromstring(sanitized)

    # Handle namespace
    ns = NS
    notif = root.find("etr:notificare", ns)
    if notif is None:
        # Try without namespace
        notif = root.find("notificare")
    if notif is None:
        # Try stripping namespace from all tags
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]
        notif = root.find("notificare")

    if notif is None:
        raise ValueError(f"Nu s-a găsit elementul <notificare> în {filepath}")

    op_code = notif.get("codTipOperatiune", "")

    # Products
    products = []
    for b in notif.findall("bunuriTransportate", {}) or notif.findall("etr:bunuriTransportate", ns):
        products.append({
            "tariff_code": b.get("codTarifar", ""),
            "product_name": b.get("denumireMarfa", ""),
            "purpose_code": b.get("codScopOperatiune", ""),
            "quantity": float(b.get("cantitate", 0)),
            "uom": b.get("codUnitateMasura", ""),
            "net_weight": float(b.get("greutateNeta", 0)),
            "gross_weight": float(b.get("greutateBruta", 0)),
            "value_ron": float(b.get("valoareLeiFaraTva", 0)),
        })

    # Partner
    partner = notif.find("partenerComercial", {}) or notif.find("etr:partenerComercial", ns)
    supplier_name = partner.get("denumire", "") if partner is not None else ""
    supplier_country = partner.get("codTara", "") if partner is not None else ""
    supplier_code = partner.get("cod", "") if partner is not None else ""

    # Transport info
    transport = notif.find("dateTransport", {}) or notif.find("etr:dateTransport", ns)
    carrier_name = transport.get("denumireOrgTransport", "") if transport is not None else ""
    carrier_vat_raw = transport.get("codOrgTransport", "") if transport is not None else ""
    carrier_country = transport.get("codTaraOrgTransport", "") if transport is not None else ""
    carrier_vat = f"RO{carrier_vat_raw}" if carrier_country == "RO" and not carrier_vat_raw.startswith("RO") else carrier_vat_raw
    vehicle_no = transport.get("nrVehicul", "") if transport is not None else ""
    trailer_no = transport.get("nrRemorca1", "") if transport is not None else ""
    transport_date = transport.get("dataTransport", "") if transport is not None else ""

    # Route start
    start_vamal = notif.find("locStartTraseuRutier", {}) or notif.find("etr:locStartTraseuRutier", ns)
    customs_code = start_vamal.get("codBirouVamal", "") if start_vamal is not None else ""
    ptf_code = start_vamal.get("codPtf", "") if start_vamal is not None else ""

    # Route end
    loc_final = notif.find("locFinalTraseuRutier", {}) or notif.find("etr:locFinalTraseuRutier", ns)
    locatie = None
    if loc_final is not None:
        locatie = loc_final.find("locatie", {}) or loc_final.find("etr:locatie", ns)
    dest_county = locatie.get("codJudet", "") if locatie is not None else ""
    dest_city = locatie.get("denumireLocalitate", "") if locatie is not None else ""
    dest_street = locatie.get("denumireStrada", "") if locatie is not None else ""
    dest_postal = locatie.get("codPostal", "") if locatie is not None else ""
    dest_number = locatie.get("numar", "") if locatie is not None else ""

    # Document
    doc = notif.find("documenteTransport", {}) or notif.find("etr:documenteTransport", ns)
    doc_number = doc.get("numarDocument", "").strip() if doc is not None else ""
    doc_date = doc.get("dataDocument", "").strip() if doc is not None else ""

    return {
        "operation_type_code": op_code,
        "operation_type": OP_MAP.get(op_code, op_code),
        "transport_date": transport_date,
        "supplier_name": supplier_name,
        "supplier_country": supplier_country,
        "supplier_code": supplier_code,
        "carrier_name": carrier_name,
        "carrier_vat": carrier_vat,
        "vehicle_no": vehicle_no,
        "trailer_no": trailer_no,
        "customs_office_code": customs_code,
        "ptf_code": ptf_code,
        "dest_county": dest_county,
        "dest_city": dest_city,
        "dest_street": dest_street,
        "dest_postal": dest_postal,
        "dest_number": dest_number,
        "document_number": doc_number,
        "document_date": doc_date,
        "products": products,
    }


def import_file(filepath: str, conn) -> dict:
    """Import a single XML file into the database. Returns stats dict."""
    filename = os.path.basename(filepath)
    fhash = _file_hash(filepath)
    uit = _extract_uit(filename)

    cur = conn.cursor()

    # Check duplicate by file_hash
    cur.execute("SELECT id FROM etransport_documents WHERE file_hash = ?", (fhash,))
    if cur.fetchone():
        return {"status": "duplicate", "file": filename, "reason": "file_hash already exists"}

    # Check duplicate by UIT
    if uit:
        cur.execute("SELECT id FROM etransport_documents WHERE uit_code = ?", (uit,))
        if cur.fetchone():
            return {"status": "duplicate", "file": filename, "reason": f"UIT {uit} already exists"}

    try:
        data = parse_xml(filepath)
    except Exception as e:
        return {"status": "error", "file": filename, "reason": str(e)}

    # Insert document
    cur.execute("""
        INSERT INTO etransport_documents
            (uit_code, operation_type, operation_type_code, transport_date,
             supplier_name, supplier_country, supplier_code,
             carrier_name, carrier_vat, vehicle_no, trailer_no,
             customs_office_code, ptf_code,
             dest_county, dest_city, dest_street, dest_postal, dest_number,
             document_number, document_date, source_file, file_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (uit, data["operation_type"], data["operation_type_code"], data["transport_date"],
          data["supplier_name"], data["supplier_country"], data["supplier_code"],
          data["carrier_name"], data["carrier_vat"], data["vehicle_no"], data["trailer_no"],
          data["customs_office_code"], data["ptf_code"],
          data["dest_county"], data["dest_city"], data["dest_street"],
          data["dest_postal"], data["dest_number"],
          data["document_number"], data["document_date"], filename, fhash))

    doc_id = cur.lastrowid

    # Insert product lines
    lines_saved = 0
    for p in data["products"]:
        norm_name = _normalize(p["product_name"])
        tariff = p["tariff_code"]
        nc8 = tariff[:8] if len(tariff) >= 8 else tariff
        cur.execute("""
            INSERT INTO etransport_product_history
                (document_id, product_name_raw, product_name_normalized,
                 hs_code_raw, nc_code_8, tariff_code_final, tariff_label_final,
                 quantity, uom, net_weight, gross_weight, value_ron, purpose_code, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'xml_import')
        """, (doc_id, p["product_name"], norm_name,
              tariff, nc8,
              tariff, "",
              p["quantity"], p["uom"], p["net_weight"], p["gross_weight"],
              p["value_ron"], p["purpose_code"]))
        save_tariff_suggestion(norm_name, tariff, "", hs_code_raw=tariff, nc_code_8=nc8, source="xml_history", confidence=98.0)
        save_hs_memory(
            hs_code_raw=tariff,
            product_name_normalized=norm_name,
            nc8_code=nc8,
            tariff_label="",
            source="xml_history",
            confidence=98.0,
            status="confirmed",
            supplier_name=data.get("supplier_name", ""),
            document_no=data.get("document_number", "") or uit,
        )
        lines_saved += 1

    conn.commit()

    # Auto-save carrier
    if data["carrier_name"] and data["carrier_vat"]:
        save_carrier(data["carrier_name"], data["carrier_vat"], source_document_id=doc_id)

    return {
        "status": "imported",
        "file": filename,
        "uit": uit,
        "lines": lines_saved,
        "carrier": f"{data['carrier_name']} ({data['carrier_vat']})",
    }


def import_folder(folder: str) -> dict:
    """Scan folder recursively for e-Transport XML files and import them."""
    init_db()

    stats = {
        "files_found": 0,
        "files_imported": 0,
        "files_duplicate": 0,
        "files_error": 0,
        "total_product_lines": 0,
        "carriers_found": set(),
        "details": [],
    }

    for root_dir, dirs, files in os.walk(folder):
        for fname in sorted(files):
            if not fname.lower().endswith(".xml"):
                continue
            if "e-transport" not in fname.lower():
                continue

            stats["files_found"] += 1
            filepath = os.path.join(root_dir, fname)

            with get_db_connection() as conn:
                result = import_file(filepath, conn)

            stats["details"].append(result)

            if result["status"] == "imported":
                stats["files_imported"] += 1
                stats["total_product_lines"] += result.get("lines", 0)
                if result.get("carrier"):
                    stats["carriers_found"].add(result["carrier"])
            elif result["status"] == "duplicate":
                stats["files_duplicate"] += 1
            else:
                stats["files_error"] += 1

    stats["carriers_found"] = list(stats["carriers_found"])
    return stats


def main():
    parser = argparse.ArgumentParser(description="Import e-Transport XML history")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Folder cu XML-uri e-Transport")
    args = parser.parse_args()

    folder = args.folder
    if not os.path.isdir(folder):
        print(f"❌ Folderul nu există: {folder}")
        sys.exit(1)

    print(f"📂 Scanare: {folder}")
    result = import_folder(folder)

    print(f"\n{'='*60}")
    print(f"📊 RAPORT IMPORT ISTORIC e-Transport")
    print(f"{'='*60}")
    print(f"  Fișiere găsite:    {result['files_found']}")
    print(f"  Fișiere importate: {result['files_imported']}")
    print(f"  Duplicate (sărite):{result['files_duplicate']}")
    print(f"  Eșuate:           {result['files_error']}")
    print(f"  Linii produs:     {result['total_product_lines']}")
    print(f"  Transportatori:   {len(result['carriers_found'])}")

    if result["carriers_found"]:
        print(f"\n  Transportatori găsiți:")
        for c in sorted(result["carriers_found"]):
            print(f"    • {c}")

    print(f"\n📋 Detalii per fișier:")
    for d in result["details"]:
        status_icon = {"imported": "✅", "duplicate": "⏭️", "error": "❌"}.get(d["status"], "❓")
        msg = f"  {status_icon} {d['file']}"
        if d["status"] == "imported":
            msg += f" — UIT: {d.get('uit', 'N/A')}, {d.get('lines', 0)} linii"
        elif d["status"] == "duplicate":
            msg += f" — {d.get('reason', '')}"
        elif d["status"] == "error":
            msg += f" — EROARE: {d.get('reason', '')}"
        print(msg)


if __name__ == "__main__":
    main()
