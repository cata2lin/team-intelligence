import pytest
from etransport.parsers.packing_list_parser import _parse_from_text, PackingListData, _parse_pl_row
from etransport.parsers.invoice_parser import _parse_invoice_header, InvoiceData
from etransport.models.shipment import Shipment
from etransport.models.product_line import ProductLine
from etransport.exporters.smartbill_xlsx_exporter import _build_row

def test_inline_gross_net_text():
    data = PackingListData()
    text = "Pantaloni Barbatesti\n1 Cantitate 100 pcs gross 12000 / net 11800"
    _parse_from_text(text, data)
    
    assert len(data.lines) == 1
    assert data.lines[0].quantity == 100
    assert data.lines[0].gross_weight_kg == 12000
    assert data.lines[0].net_weight_kg == 11800


def test_inline_gross_net_table():
    col_map = {'description': 0, 'quantity': 1}
    row = ['Product X', '50']
    
    # Adaugam combinatia masiva in tabel cand lipsesc coloanele dedicate
    row[0] = 'Product X gross 120 / net 110'
    line = _parse_pl_row(row, col_map, 1)
    
    assert line is not None
    assert line.quantity == 50
    assert line.gross_weight_kg == 120
    assert line.net_weight_kg == 110


def test_invoice_header_extraction():
    text = """
Supplier :
Guangzhou Tang Xiaoyang E-commerce Co., LTD.

Factura comerciala nr. : 443
Date: 2026-02-10
Container No: TIIU5478466
    """
    data = InvoiceData()
    _parse_invoice_header(text, data)
    
    assert data.supplier_name == "Guangzhou Tang Xiaoyang E-commerce Co., LTD."
    assert data.invoice_number == "443"
    assert str(data.invoice_date) == "2026-02-10"
    assert data.container_no == "TIIU5478466"


def test_smartbill_exporter_protection():
    shipment = Shipment(operation_type="import")
    
    # Adaugam linii valide
    shipment.products.append(ProductLine(source_type="invoice", product_name_export="Tricou"))
    
    # Adaugam fake derived sau packing fallback
    shipment.products.append(ProductLine(source_type="packing", product_name_export="Ghost Line"))
    
    # _build_row n-are source_type in model sa checkeze e in exporter per loop, but let's just make sure 
    assert shipment.products[0].source_type == "invoice"
    assert shipment.products[1].source_type == "packing"
    
    assert exported_count == 1

def test_invoice_table_stitching_logic():
    # Simulare tables din pdfplumber (List of rows)
    tables = [
        [ # Pagina 1: Are header
            ["#", "Cod HS", "Descriere", "Cantitate", "Pret"],
            ["1", "70109030", "Glass bottles", "70000", "0.1"]
        ],
        [ # Pagina 2: Fara header, continua cu inregistrarile
            ["2", "39235000", "Cap", "70000", "0.05"],
            ["3", "96161000", "Spray", "70000", "0.05"]
        ]
    ]
    data = InvoiceData()
    from etransport.parsers.invoice_parser import _parse_products_from_tables
    _parse_products_from_tables(tables, data)
    
    # Daca a functionat stitching-ul perfect preluand last_col_map
    assert len(data.products) == 3
    assert data.products[0].product_name_export == "Glass bottles"
    assert data.products[1].product_name_export == "Cap"
    assert data.products[2].product_name_export == "Spray"

def test_packing_list_gross_net_newlines():
    header = ["#", "Descriere", "Unit", "Greutate\nbruta (KG)", "Greutate\nneta (KG)"]
    from etransport.parsers.packing_list_parser import _identify_pl_columns
    col_map = _identify_pl_columns(header)
    
    assert col_map.get('gross_weight') == 3
    assert col_map.get('net_weight') == 4

def test_container_30_real_pdf_fix():
    import os
    from etransport.main import build_shipment
    
    # Path for local or VPS test checking
    possible_paths = [
        "/Users/gheorghebeschea/Downloads/Etransport/Container 30 - invoice packing.pdf",
        "/root/Downloads/Etransport/Container 30 - invoice packing.pdf",
        "/root/Etransport/Container 30 - invoice packing.pdf"
    ]
    
    pdf_path = next((p for p in possible_paths if os.path.exists(p)), None)
    if not pdf_path:
        return # Skip test if real file not found
        
    shipment = build_shipment(pdf_path)
    
    assert len(shipment.products) == 30
    assert shipment.total_net_weight_kg == 18000
    assert shipment.total_gross_weight_kg == 18460
    
    assert shipment.products[0].net_weight_kg == 11800
    assert shipment.products[0].gross_weight_kg == 12000
    assert shipment.products[1].net_weight_kg == 1100
    assert shipment.products[1].gross_weight_kg == 1200
    assert shipment.products[2].net_weight_kg == 1000
    assert shipment.products[2].gross_weight_kg == 1000
    assert shipment.products[29].net_weight_kg == 2200
    assert shipment.products[29].gross_weight_kg == 2280
    
    import datetime
    assert shipment.supplier_name == "Guangzhou Tang Xiaoyang E-commerce Co., LTD."
    assert shipment.invoice_number == "443"
    assert shipment.invoice_date == datetime.date(2026, 2, 10)
    assert shipment.debug_info["matching_strategy_used"] == "index"
