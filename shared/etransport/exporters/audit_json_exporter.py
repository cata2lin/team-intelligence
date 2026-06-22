"""
Exporter JSON de audit.
Generează un fișier JSON complet cu toate datele extrase,
regulile aplicate, warning-uri, matching info, și valori originale/calculate.
"""
import json
import os
from datetime import date, datetime
from typing import Any

from etransport.models.shipment import Shipment
from etransport.services.validation_service import ValidationReport


class DateEncoder(json.JSONEncoder):
    """Encoder JSON care suportă obiecte date/datetime."""
    
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


def export_audit_json(
    shipment: Shipment,
    validation_report: ValidationReport,
    output_path: str,
    extra_metadata: dict = None,
) -> str:
    """
    Generează fișierul JSON de audit.
    
    Conținut:
    - Date extrase din documente
    - Regulile aplicate
    - Warning-uri
    - Matching invoice ↔ packing list
    - HS original și NC8 derivat
    - Valori originale și calculate
    
    Args:
        shipment: Modelul de expediere complet
        validation_report: Raportul de validare
        output_path: Calea fișierului de output
        extra_metadata: Metadate suplimentare (opțional)
        
    Returns:
        Calea absolută a fișierului generat
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    audit = {
        "generated_at": datetime.now().isoformat(),
        "version": "1.0",
        
        # ── Metadata ──
        "metadata": {
            "operation_type": shipment.operation_type,
            "operation_type_code": shipment.operation_type_code,
            "currency_original": shipment.currency,
            "exchange_rate_to_ron": shipment.exchange_rate_to_ron,
            **(extra_metadata or {}),
        },
        
        # ── Expediere ──
        "shipment": {
            "supplier": {
                "name": shipment.supplier_name,
                "tax_code": shipment.supplier_tax_code,
                "country": shipment.supplier_country,
            },
            "carrier": {
                "name": shipment.carrier_name,
                "vat": shipment.carrier_vat,
            },
            "invoice": {
                "number": shipment.invoice_number,
                "date": shipment.invoice_date,
            },
            "document": {
                "type": shipment.document_type,
                "number": shipment.document_number,
                "date": shipment.document_date,
            },
            "transport": {
                "date": shipment.transport_date,
                "container_no": shipment.container_no,
                "vehicle_no": shipment.vehicle_no,
                "trailer_no": shipment.trailer_no,
                "driver_name": shipment.driver_name,
                "driver_phone": shipment.driver_phone,
            },
            "start_location": {
                "customs_office_code": shipment.start_customs_office_code,
                "ptf_code": shipment.start_ptf_code,
            },
            "destination": {
                "country": shipment.destination.country,
                "county": shipment.destination.county,
                "city": shipment.destination.city,
                "street": shipment.destination.street,
                "number": shipment.destination.number,
                "postal_code": shipment.destination.postal_code,
                "block": shipment.destination.block,
                "staircase": shipment.destination.staircase,
                "floor": shipment.destination.floor,
                "apartment": shipment.destination.apartment,
                "other_info": shipment.destination.other_info,
            },
        },
        
        # ── Produse ──
        "products": [
            _product_to_audit_dict(p) for p in shipment.products
        ],
        
        # ── Sumar ──
        "summary": {
            "total_lines": len(shipment.products),
            "total_quantity": sum(p.quantity for p in shipment.products),
            "total_value_original": round(
                sum(p.line_value_original for p in shipment.products), 2
            ),
            "total_value_ron": round(
                sum(p.line_value_ron_without_vat for p in shipment.products), 2
            ),
            "total_net_weight_kg": round(
                sum(p.net_weight_kg for p in shipment.products), 3
            ),
            "total_gross_weight_kg": round(
                sum(p.gross_weight_kg for p in shipment.products), 3
            ),
        },
        
        # ── Validare ──
        "validation": {
            "is_valid": validation_report.is_valid,
            "is_draft": validation_report.is_draft,
            "errors": validation_report.errors,
            "warnings": validation_report.warnings,
            "info": validation_report.info,
        },
        
        # ── Warning-uri la nivel de expediere ──
        "shipment_warnings": shipment.warnings,
        
        # ── Debug Metrics & Logs ──
        "debug_info": shipment.debug_info,
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(audit, f, cls=DateEncoder, ensure_ascii=False, indent=2)
    
    return os.path.abspath(output_path)


def _product_to_audit_dict(product) -> dict:
    """Convertește un ProductLine într-un dict detaliat pentru audit."""
    return {
        "source_line_no": product.source_line_no,
        "matched_packing_line_no": product.matched_packing_line_no,
        
        "names": {
            "raw": product.product_name_raw,
            "normalized": product.product_name_normalized,
            "export": product.product_name_export,
        },
        "product_code": product.product_code,
        
        "hs_codes": {
            "raw": product.hs_code_raw,
            "hs6": getattr(product, "hs6_code", None),
            "nc8_derived": product.nc_code_8,
            "hs8_candidate_valid": getattr(product, "hs8_candidate_valid", None),
            "rule": "Primele 8 cifre din codul HS brut",
        },
        
        "quantity": {
            "value": product.quantity,
            "display_unit": product.display_unit,
            "standard_unit_code": product.standard_unit_code,
        },
        
        "values_original": {
            "currency": product.currency,
            "unit_price": product.unit_price_original,
            "line_value": product.line_value_original,
        },
        "values_ron": {
            "unit_price_ron": product.unit_price_ron,
            "line_value_ron_without_vat": product.line_value_ron_without_vat,
        },
        
        "weights_total": {
            "net_kg": product.net_weight_kg,
            "gross_kg": product.gross_weight_kg,
        },
        
        "export_smartbill_mapping": {
            "export_weight_net_unit": product.export_weight_net_unit,
            "export_weight_gross_unit": product.export_weight_gross_unit,
            "export_unit_price_written": product.export_unit_price_written,
            "export_currency_written": product.export_currency_written,
            "tariff_code_input": product.hs_code_raw or product.nc_code_8,
            "tariff_code_db_match": getattr(product, "tariff_code_db_match", None),
            "tariff_code_match_label": getattr(product, "tariff_code_match_label", None),
            "tariff_code_match_method": getattr(product, "tariff_code_match_method", None),
            "tariff_code_match_confidence": getattr(product, "tariff_code_match_confidence", None),
            "tariff_code_match_source": getattr(product, "tariff_code_match_source", None),
            "tariff_code_candidates": getattr(product, "tariff_code_candidates", []),
            "tariff_needs_review_reason": getattr(product, "tariff_needs_review_reason", None),
            "historical_match_found": getattr(product, "historical_match_found", False),
            "historical_source_document": getattr(product, "historical_source_document", None),
            "used_same_shipment_propagation": getattr(product, "used_same_shipment_propagation", False),
            "same_shipment_source_is_strong": getattr(product, "same_shipment_source_is_strong", None),
            "same_shipment_source_method": getattr(product, "same_shipment_source_method", None),
            "same_shipment_source_confidence": getattr(product, "same_shipment_source_confidence", None),
            "tariff_code_override_applied": product.nc_code_override_applied,
            "tariff_code_written_to_xlsx": product.export_nc_code_written,
            "export_nc_code_written": product.export_nc_code_written,
            "nc_code_override_applied": product.nc_code_override_applied,
            "nc_code_status": product.nc_code_status,
        },
        
        "operation_purpose": {
            "code": product.operation_purpose_code,
            "label": product.operation_purpose_label,
        },
        
        "matching": {
            "confidence": product.match_confidence,
            "method": product.match_method,
        },
        
        "aggregated_from_lines": product.aggregated_from_lines,
        "warnings": product.warnings,
    }
