"""
Parser pentru facturi comerciale PDF (Commercial Invoice).
Extrage: linii de produs cu denumire, cod HS, cantitate, unitate, preț, valoare.
Extrage și date la nivel de factură: furnizor, nr. factură, dată, monedă.

Notă: Formatul facturilor variază semnificativ între furnizori.
Acest parser gestionează layout-uri tabulare comune.
Pentru formate noi, se adaugă strategii suplimentare de parsing.
"""
import re
from typing import Optional, Tuple
from datetime import date

import pdfplumber
from dateutil import parser as date_parser

from etransport.models.product_line import ProductLine
from etransport.utils.text_normalizer import normalize_text, extract_numeric
from etransport.utils.hs_mapper import derive_nc8
from etransport.utils.unit_mapper import map_unit
from etransport import config


class InvoiceData:
    """Date extrase din invoice, înainte de construcția Shipment."""

    def __init__(self):
        self.supplier_name: str = ""
        self.supplier_country: str = ""
        self.supplier_tax_code: Optional[str] = None
        self.invoice_number: Optional[str] = None
        self.invoice_date: Optional[date] = None
        self.container_no: Optional[str] = None
        self.currency: str = config.DEFAULT_CURRENCY
        self.products: list[ProductLine] = []
        self.warnings: list[str] = []
        self.raw_text: str = ""


def parse_invoice_pdf(pdf_path: str, allowed_pages: list[int] = None) -> InvoiceData:
    """
    Parsează un PDF de commercial invoice.
    
    Strategia:
    1. Extrage tot textul din PDF
    2. Identifică header-ul (furnizor, nr. factură, dată, monedă)
    3. Identifică tabelul de produse
    4. Parsează fiecare linie de produs
    
    Args:
        pdf_path: Calea către fișierul PDF
        
    Returns:
        InvoiceData cu toate datele extrase
    """
    data = InvoiceData()
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Extragem textul și tabelele din toate paginile
            full_text = ""
            all_tables = []
            
            for idx, page in enumerate(pdf.pages):
                if allowed_pages is not None and idx not in allowed_pages:
                    continue
                text = page.extract_text() or ""
                full_text += text + "\n"
                
                # IMPORTANT: Only extract tables if they represent invoice content!
                tables = page.extract_tables() or []
                all_tables.extend(tables)
            
            data.raw_text = full_text
            
            # Parsează header-ul
            _parse_invoice_header(full_text, data)
            
            # Parsează produsele din tabele
            if all_tables:
                _parse_products_from_tables(all_tables, data)
            else:
                # Fallback: parsare din text
                _parse_products_from_text(full_text, data)
            
            if not data.products:
                data.warnings.append(
                    "Nu s-au putut extrage linii de produs din invoice. "
                    "Verificați formatul PDF-ului."
                )
    
    except Exception as e:
        data.warnings.append(f"Eroare la parsarea invoice PDF: {str(e)}")
    
    return data


def _parse_invoice_header(text: str, data: InvoiceData):
    """Extrage informații din header-ul facturii."""
    lines = text.split('\n')
    
    # Căutăm nr. factură - luam primele numere de minim 3 digiti in proximitatea etichetei
    inv_block = re.search(r'(?:Invoice\s*(?:no|number|#|nr)\.?|Factura\s*comerciala\s*nr\.?)[\s\S]{1,50}', text, re.IGNORECASE)
    if inv_block:
        numbers = re.findall(r'\b(\d{3,8})\b', inv_block.group())
        for n in numbers:
            if not n.startswith('202'):
                data.invoice_number = n
                break
            
    # Daca n-am gasit, fallback curent
    if not data.invoice_number:
        inv_patterns = [
            r'(?:INV[.-]?\s*)(\d{3,})',
            r'Factura\s*FISCALA.*?\n.*?(\d{2,8})\n'
        ]
        for pattern in inv_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                val = re.sub(r'[^\w\-]', '', match.group(1))
                if len(val) >= 2 and not val.startswith("202"):
                    data.invoice_number = val
                    break
            
    # Extrage container
    cont_m = re.search(r'(?:Container(?: ID| No|\.|:)*\s*)([A-Z]{4}\d{7})\b', text, re.IGNORECASE)
    if not cont_m:
         cont_m = re.search(r'\b([A-Z]{4}\d{7})\b', text)
    if cont_m:
        data.container_no = cont_m.group(1)
    
    # Căutăm data facturii (foarte agresiv pe valoarea pură)
    date_patterns = [
        r'(?:Date|Data|Dated?)[\s:]+(\d{1,2}[\s./\-]\w{3,9}[\s./\-]\d{2,4})',
        r'(?:Date|Data|Dated?)[\s:]+(\d{4}[\s./\-]\d{1,2}[\s./\-]\d{1,2})',
        r'(?:Date|Data|Dated?).*?\n.*?(\d{1,2}[\s./\-]\w{3,9}[\s./\-]\d{2,4})',
        r'(?:Date|Data|Dated?).*?\n.*?(\d{4}[\s./\-]\d{1,2}[\s./\-]\d{1,2})'
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                parsed = date_parser.parse(match.group(1), dayfirst=True)
                data.invoice_date = parsed.date()
                break
            except (ValueError, TypeError):
                continue
                
    # Daca n-am gasit data curata, cautam bloc cu luna dispersata pe mai multe linii
    # Strategy: find Month+Year first, then look backwards for day
    if not data.invoice_date:
        month_year = re.search(
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*.{0,50}?(20\d\d)',
            text, re.IGNORECASE | re.DOTALL
        )
        if month_year:
            month_str = month_year.group(1)
            year_str = month_year.group(2)
            # Search backward from month position for a day number (1-31)
            pos = month_year.start()
            preceding = text[max(0, pos - 80):pos]
            # Find the last number 1-31 (word-bounded to avoid partial matches from e.g. '454')
            day_candidates = re.findall(r'\b(\d{1,2})\b', preceding)
            day_str = None
            for dc in reversed(day_candidates):
                dv = int(dc)
                if 1 <= dv <= 31:
                    day_str = dc
                    break
            if day_str:
                clean_dt = f"{day_str} {month_str} {year_str}"
                try:
                    parsed = date_parser.parse(clean_dt, dayfirst=True)
                    data.invoice_date = parsed.date()
                except:
                    pass
            else:
                # No day found, try just month + year (assume 1st)
                clean_dt = f"1 {month_str} {year_str}"
                try:
                    parsed = date_parser.parse(clean_dt, dayfirst=True)
                    data.invoice_date = parsed.date()
                except:
                    pass
    
    #... curenta neschimbata
    currency_patterns = [
        r'\b(USD|EUR|GBP|CNY|RON|CHF|JPY)\b',
        r'(?:Currency|Moneda)[\s:]+(\w{3})',
    ]
    for pattern in currency_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data.currency = match.group(1).upper()
            break
    
    # Furnizor: anchor logic as requested
    supplier_found = False
    supplier_lines = []
    for i, line in enumerate(lines[:30]):
        if re.match(r'^(?:supplier|furnizor|shipper|consignor|exporter)(?:\s*\(.*?\))?\s*:?$', line.strip(), re.IGNORECASE):
            for next_line in lines[i+1:]:
                n_str = next_line.strip()
                if not n_str or re.match(r'^(?:tel|fax|email|phone|client|to|invoice|date|termeni)', n_str, re.IGNORECASE):
                    break
                supplier_lines.append(n_str)
            break
            
    if supplier_lines:
        longest = max(supplier_lines, key=len)
        clean_name = longest.split(' No.')[0].split(' Str.')[0].strip()
        data.supplier_name = clean_name
        supplier_found = True
    
    if not supplier_found:
        supplier_lines = []
        for line in lines[:10]:
            line = line.strip()
            if line and not re.match(r'(?:invoice|date|no\.|tel|fax|email|phone)', 
                                      line, re.IGNORECASE):
                supplier_lines.append(line)
            if len(supplier_lines) >= 2:
                break
        if supplier_lines:
            data.supplier_name = supplier_lines[0]


def _parse_products_from_tables(
    tables: list[list[list[str]]], data: InvoiceData
):
    """Extrage produsele din tabele identificate (MULTI-PAGE STITCHING)."""
    last_col_map = None
    global_row_idx = 1
    
    for table in tables:
        if not table or len(table) < 1:
            continue
        
        header = table[0]
        col_map = _identify_columns(header)
        
        start_idx = 1
        if not col_map.get('description'):
            # Table Stitching: Dacă pagina curentă e o continuare a tabelului de dinainte
            if last_col_map is not None:
                col_map = last_col_map
                start_idx = 0  # Table-ul a început direct cu date (rândul 0 contine date)
            else:
                continue
        else:
            last_col_map = col_map # Salvăm harta de coloane viitoarelor pagini
            start_idx = 1 
        
        for row in table[start_idx:]:
            if not row or all(not cell or not str(cell).strip() for cell in row):
                continue
            
            # B. PARSARE RÂNDURI NUMEROTATE: folosim numărul ca ancoră de linie
            if not re.match(r'^\d+$', str(row[0]).strip()):
                # Excludem rândurile Total sau alte gunoaie care nu-s produse (sau linii incomplete)
                continue
            
            product = _parse_table_row(row, col_map, global_row_idx, data)
            if product:
                data.products.append(product)
                global_row_idx += 1


def _identify_columns(header: list[str]) -> dict:
    """Identifică coloanele relevante din header-ul tabelului."""
    col_map = {}
    
    patterns = {
        'description': [
            'description', 'denumire', 'produs', 'product', 'goods',
            'commodity', 'item', 'denumirea', 'name',
        ],
        'hs_code': [
            'hs', 'taric', 'tariff', 'nc', 'cod tarifar', 'hs code',
            'tariff code', 'commodity code', 'hts',
        ],
        'quantity': [
            'qty', 'quantity', 'cantitate', 'cant', 'pcs', 'amount',
        ],
        'unit': [
            'unit', 'u.m.', 'um', 'uom', 'measure',
        ],
        'unit_price': [
            'unit price', 'pret unitar', 'price', 'pret', 'rate',
        ],
        'total': [
            'total', 'amount', 'value', 'valoare', 'sum',
        ],
        'product_code': [
            'code', 'cod', 'item no', 'item code', 'art', 'sku',
        ],
    }
    
    claimed_cols = set()
    
    for col_idx, cell in enumerate(header):
        if not cell:
            continue
        cell_lower = str(cell).lower().strip()
        
        for field, keywords in patterns.items():
            if field in col_map:
                continue
            if col_idx in claimed_cols:
                continue
            for kw in keywords:
                if kw in cell_lower:
                    col_map[field] = col_idx
                    claimed_cols.add(col_idx)
                    break
    
    return col_map


def _parse_table_row(
    row: list[str],
    col_map: dict,
    line_no: int,
    data: InvoiceData,
) -> Optional[ProductLine]:
    """Parsează un rând din tabel într-un ProductLine."""
    
    def get_cell(field: str) -> str:
        idx = col_map.get(field)
        if idx is not None and idx < len(row):
            return str(row[idx] or "").strip()
        return ""
    
    description = get_cell('description')
    if not description:
        return None
    
    # Skip rânduri de total/subtotal
    if re.match(r'^(total|subtotal|grand total|sum)', description, re.IGNORECASE):
        return None
    
    # HS Code
    hs_raw = get_cell('hs_code')
    hs_cleaned, nc8, hs_warnings = derive_nc8(hs_raw)
    
    # Cantitate
    qty_str = get_cell('quantity')
    quantity = extract_numeric(qty_str)
    
    # Unitate de măsură
    unit_raw = get_cell('unit')
    # Dacă nu avem coloană de unitate, încearcă să extragi din cantitate
    if not unit_raw and qty_str:
        unit_match = re.search(r'[a-zA-Z]+', qty_str)
        if unit_match:
            unit_raw = unit_match.group()
    display_unit, standard_code, unit_warnings = map_unit(unit_raw)
    
    # Preț unitar
    unit_price = extract_numeric(get_cell('unit_price'))
    
    # Valoare totală linie
    total_val = extract_numeric(get_cell('total'))
    
    # Dacă avem cantitate și preț dar nu total, calculăm
    if total_val == 0 and quantity > 0 and unit_price > 0:
        total_val = round(quantity * unit_price, 2)
    # Dacă avem total și cantitate dar nu preț, calculăm
    if unit_price == 0 and quantity > 0 and total_val > 0:
        unit_price = round(total_val / quantity, 4)
    
    # Cod produs
    product_code = get_cell('product_code') or None
    
    # Monedă
    currency = data.currency
    
    # Construiește ProductLine
    product = ProductLine(
        source_line_no=line_no,
        product_name_raw=description,
        product_name_normalized=normalize_text(description),
        product_name_export=description,  # va fi rafinat ulterior
        product_code=product_code,
        hs_code_raw=hs_cleaned,
        nc_code_8=nc8,
        quantity=quantity,
        display_unit=display_unit,
        standard_unit_code=standard_code,
        currency=currency,
        unit_price_original=unit_price,
        line_value_original=total_val,
        warnings=hs_warnings + unit_warnings,
    )
    
    return product


def _parse_products_from_text(text: str, data: InvoiceData):
    """
    Fallback: parsează produsele din text simplu (fără tabele detectate).
    Caută pattern-uri de tip: descriere | cod HS | cantitate | preț.
    """
    lines = text.split('\n')
    line_no = 0
    
    # Pattern pentru linii de produs din text
    # Exemplu: "Glass bottles 7010903000 1000 PCS 0.50 500.00"
    product_pattern = re.compile(
        r'^(.+?)\s+'                  # Descriere
        r'(\d{6,10})\s+'             # Cod HS (6-10 cifre)
        r'(\d[\d,.]*)\s*'            # Cantitate
        r'(pcs|pc|pieces?|buc|kg|set|sets?|box|boxes|cartons?|units?)?\s*'  # Unitate
        r'([\d,.]+)\s+'              # Preț unitar
        r'([\d,.]+)',                 # Total
        re.IGNORECASE,
    )
    
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        
        match = product_pattern.match(raw_line)
        if match:
            line_no += 1
            desc = match.group(1).strip()
            hs_raw = match.group(2)
            qty = extract_numeric(match.group(3))
            unit_raw = match.group(4) or ""
            price = extract_numeric(match.group(5))
            total = extract_numeric(match.group(6))
            
            hs_cleaned, nc8, hs_warnings = derive_nc8(hs_raw)
            display_unit, standard_code, unit_warnings = map_unit(unit_raw)
            
            product = ProductLine(
                source_line_no=line_no,
                product_name_raw=desc,
                product_name_normalized=normalize_text(desc),
                product_name_export=desc,
                hs_code_raw=hs_cleaned,
                nc_code_8=nc8,
                quantity=qty,
                display_unit=display_unit,
                standard_unit_code=standard_code,
                currency=data.currency,
                unit_price_original=price,
                line_value_original=total,
                warnings=hs_warnings + unit_warnings,
            )
            data.products.append(product)
    
    if not data.products:
        data.warnings.append(
            "Parsare text fallback: nu s-au găsit linii de produs. "
            "Formatul invoice-ului poate necesita un parser specializat."
        )
