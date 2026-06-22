"""
Parser pentru Packing List PDF.
Extrage greutăți (net/brut) pe linie sau ca totaluri,
plus denominări de produs pentru matching cu invoice-ul.
"""
import re
from typing import Optional
from dataclasses import dataclass, field

import pdfplumber

from etransport.utils.text_normalizer import normalize_text, extract_numeric


@dataclass
class PackingListLine:
    """O linie din packing list."""
    line_no: int = 0
    description: str = ""
    description_normalized: str = ""
    quantity: float = 0.0
    unit: str = ""
    net_weight_kg: float = 0.0
    gross_weight_kg: float = 0.0
    cartons: int = 0


@dataclass
class PackingListData:
    """Date extrase din packing list."""
    lines: list[PackingListLine] = field(default_factory=list)
    total_net_weight_kg: float = 0.0
    total_gross_weight_kg: float = 0.0
    total_cartons: int = 0
    has_per_line_weights: bool = False
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_packing_list_pdf(pdf_path: str, allowed_pages: list[int] = None) -> PackingListData:
    """
    Parsează un PDF de packing list.
    
    Strategia:
    1. Extrage tabele cu pdfplumber
    2. Identifică coloanele de greutate net/brut
    3. Extrage greutăți pe linie sau doar totaluri
    
    Args:
        pdf_path: Calea către fișierul PDF
        
    Returns:
        PackingListData cu greutăți per linie sau totaluri
    """
    data = PackingListData()
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            all_tables = []
            
            for idx, page in enumerate(pdf.pages):
                if allowed_pages is not None and idx not in allowed_pages:
                    continue
                text = page.extract_text() or ""
                full_text += text + "\n"
                tables = page.extract_tables() or []
                all_tables.extend(tables)
            
            data.raw_text = full_text
            
            # Încercăm din tabele
            if all_tables:
                _parse_from_tables(all_tables, data)
            
            # Dacă nu am găsit din tabele, parsăm din text
            if not data.lines and not data.total_net_weight_kg:
                _parse_from_text(full_text, data)
            
            # Extragie totaluri din text dacă nu le avem din tabel
            if not data.total_net_weight_kg:
                _extract_totals_from_text(full_text, data)
            
            # Determinăm dacă avem greutăți pe linie
            data.has_per_line_weights = any(
                line.net_weight_kg > 0 or line.gross_weight_kg > 0 
                for line in data.lines
            )
            
            if not data.lines and not data.total_net_weight_kg:
                data.warnings.append(
                    "Nu s-au putut extrage date din packing list. "
                    "Verificați formatul PDF-ului."
                )
    
    except Exception as e:
        data.warnings.append(f"Eroare la parsarea packing list PDF: {str(e)}")
    
    return data


def _parse_from_tables(tables: list[list[list[str]]], data: PackingListData):
    """Parsează packing list din tabele (MULTI-PAGE STITCHING)."""
    last_col_map = None
    global_row_idx = 1
    
    for table in tables:
        if not table or len(table) < 1:
            continue
        
        header = table[0]
        col_map = _identify_pl_columns(header)
        
        start_idx = 1
        if not col_map.get('description'):
            if last_col_map is not None:
                col_map = last_col_map
                start_idx = 0
            else:
                continue
        else:
            last_col_map = col_map
            start_idx = 1
        
        for row in table[start_idx:]:
            if not row or all(not cell or not str(cell).strip() for cell in row):
                continue
            
            # Verificăm să nu fie footer total sau gunoi
            if not re.match(r'^\d+$', str(row[0]).strip()):
                desc_str = str(row[2] if len(row) > 2 else row[1]).lower()
                if any(kw in desc_str for kw in ['total', 'subtotal', 'grand']):
                    line = _parse_pl_row(row, col_map, global_row_idx)
                    if line:
                        data.total_net_weight_kg = max(data.total_net_weight_kg, line.net_weight_kg)
                        data.total_gross_weight_kg = max(data.total_gross_weight_kg, line.gross_weight_kg)
                continue
            
            line = _parse_pl_row(row, col_map, global_row_idx)
            if line:
                data.lines.append(line)
                global_row_idx += 1


def _identify_pl_columns(header: list[str]) -> dict:
    """Găsește indecșii coloanelor importante dintr-un rând de antet de packing list."""
    col_map = {}
    
    aliases = {
        'description': [
            'description', 'product', 'item', 'goods', 'descriere',
            'articol', 'denumire', 'descrierea', 'product name',
            'descriere produs'
        ],
        'quantity': [
            'quantity', 'qty', 'cantitate', 'cant.', 'bucati', 'buc'
        ],
        'net_weight': [
            'net weight', 'n.w.', 'nw', 'net', 'greutate neta',
            'net wt', 'n.w', 'greutate neta (kg)', 'net weight (kg)'
        ],
        'gross_weight': [
            'gross weight', 'g.w.', 'gw', 'gross', 'greutate bruta',
            'gross wt', 'g.w', 'greutate bruta (kg)', 'gross weight (kg)'
        ],
        'cartons': [
            'cartons', 'ctns', 'box', 'boxes', 'cutii', 'colete',
            'ambalaj', 'packaging', 'ambalare', 'no. of cartons'
        ]
    }
    
    for i, cell in enumerate(header):
        if not cell:
            continue
        
        # Elimina enter-uri din mijlocul celulei si unificam
        cell_clean = re.sub(r'\s+', ' ', str(cell).lower().strip())
        cell_clean = cell_clean.replace('ă', 'a').replace('â', 'a').replace('ț', 't').replace('ș', 's').replace('î', 'i')
        
        for key, possible_names in aliases.items():
            if key not in col_map:
                if any(name in cell_clean for name in possible_names):
                    col_map[key] = i
                    break
    
    return col_map


def _parse_pl_row(
    row: list[str], col_map: dict, line_no: int
) -> Optional[PackingListLine]:
    """Parsează un rând din tabelul packing list."""
    
    def get_cell(field_name: str) -> str:
        idx = col_map.get(field_name)
        if idx is not None and idx < len(row):
            return str(row[idx] or "").strip()
        return ""
    
    description = get_cell('description')
    if not description:
        return None
    
    # Hack inteligent: Daca gasim in orice cell combinatia gross x / net y
    full_row_text = " ".join([str(c) for c in row if c])
    gn_match = re.search(r'gross\s+([\d.,]+)\s*/\s*net\s+([\d.,]+)', full_row_text, re.IGNORECASE)
    
    net_w = extract_numeric(get_cell('net_weight'))
    gross_w = extract_numeric(get_cell('gross_weight'))
    
    if gn_match:
        gross_w = extract_numeric(gn_match.group(1))
        net_w = extract_numeric(gn_match.group(2))
        
    return PackingListLine(
        line_no=line_no,
        description=description,
        description_normalized=normalize_text(description),
        quantity=extract_numeric(get_cell('quantity')),
        unit=get_cell('unit'),
        net_weight_kg=net_w,
        gross_weight_kg=gross_w,
        cartons=int(extract_numeric(get_cell('cartons'))),
    )


def _parse_from_text(text: str, data: PackingListData):
    """Fallback: parsare packing list din text."""
    lines = text.split('\n')
    line_no = 0
    
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        
        gn_match = re.search(r'gross\s+([\d.,]+)\s*/\s*net\s+([\d.,]+)', raw_line, re.IGNORECASE)
        nw_val, gw_val, qty_val = 0.0, 0.0, 0.0
        
        if gn_match:
            gw_val = extract_numeric(gn_match.group(1))
            nw_val = extract_numeric(gn_match.group(2))
            clean_line = raw_line.replace(gn_match.group(0), '')
        else:
            clean_line = raw_line
            nw_m = re.search(r'(?:N\.?W\.?|net\s*weight|net)\s*:?\s*([\d.,]+)', clean_line, re.IGNORECASE)
            if nw_m: nw_val = extract_numeric(nw_m.group(1))
            gw_m = re.search(r'(?:G\.?W\.?|gross\s*weight|gross)\s*:?\s*([\d.,]+)', clean_line, re.IGNORECASE)
            if gw_m: gw_val = extract_numeric(gw_m.group(1))
        
        qty_m = re.search(r'\b(\d[\d.,]*)\s*(?:pcs|pc|pieces?|buc|set|sets|cartons?|boxes?|kg)\b', clean_line, re.IGNORECASE)
        if qty_m:
            qty_val = extract_numeric(qty_m.group(1))
            
        if gw_val > 0 or nw_val > 0:
            line_no += 1
            desc_match = re.match(r'^([A-Za-z0-9\s\-]+)', clean_line)
            desc = desc_match.group(1).strip() if desc_match else "UNKNOWN ITEM"
            data.lines.append(PackingListLine(
                line_no=line_no,
                description=desc,
                description_normalized=normalize_text(desc),
                quantity=qty_val,
                net_weight_kg=nw_val,
                gross_weight_kg=gw_val,
            ))


def _extract_totals_from_text(text: str, data: PackingListData):
    """Extrage totaluri de greutate din text."""
    # Caută pattern-uri de tip "Total N.W.: 1234.56 KGS"
    nw_patterns = [
        r'(?:Total\s+)?(?:Net\s+Weight|N\.?W\.?)\s*:?\s*([\d,.]+)\s*(?:KG|KGS)?',
        r'(?:Total\s+)?(?:Greutate\s+Neta)\s*:?\s*([\d,.]+)',
    ]
    gw_patterns = [
        r'(?:Total\s+)?(?:Gross\s+Weight|G\.?W\.?)\s*:?\s*([\d,.]+)\s*(?:KG|KGS)?',
        r'(?:Total\s+)?(?:Greutate\s+Bruta)\s*:?\s*([\d,.]+)',
    ]
    
    for pattern in nw_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data.total_net_weight_kg = max(
                data.total_net_weight_kg,
                extract_numeric(match.group(1)),
            )
            break
    
    for pattern in gw_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data.total_gross_weight_kg = max(
                data.total_gross_weight_kg,
                extract_numeric(match.group(1)),
            )
            break
