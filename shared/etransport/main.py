"""
CLI entry point pentru generatorul SmartBill e-Transport.
Poate fi folosit pentru automatizare sau testare fără UI.

Utilizare:
    python -m etransport.main \
        --invoice invoice.pdf \
        --packing-list packing_list.pdf \
        --transport "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel" \
        --carrier "ANDI TRANS SRL RO5607012" \
        --operation import \
        --currency USD \
        --exchange-rate 4.65 \
        --customs-office ROCT0900 \
        --dest-county Brasov \
        --dest-city Brasov \
        --dest-street Bazaltului \
        --dest-number 11 \
        --dest-postal 507225
"""
import argparse
import os
import sys
from datetime import date
from typing import Optional, Dict, Any, List

from etransport import config
from etransport.models.shipment import Shipment
from etransport.models.product_line import ProductLine
from etransport.parsers.invoice_parser import parse_invoice_pdf
from etransport.parsers.packing_list_parser import parse_packing_list_pdf
from etransport.parsers.transport_parser import parse_transport_text, parse_carrier_text
from etransport.services.matching_service import (
    match_invoice_to_packing,
    apply_matching_results,
)
from etransport.services.weight_distribution_service import distribute_weights
from etransport.services.currency_service import convert_currency, get_exchange_rate
from etransport.services.aggregation_service import aggregate_lines
from etransport.services.validation_service import validate_shipment
from etransport.exporters.smartbill_xlsx_exporter import export_smartbill_xlsx
from etransport.exporters.audit_json_exporter import export_audit_json


def load_catalog(filename: str) -> list:
    import json
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "config", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def resolve_customs_office(input_val: Optional[str]) -> Optional[dict]:
    if not input_val:
        input_val = "232901"
        
    for item in load_catalog("customs_offices.json"):
        if item["code"] == input_val or item.get("full_display") == input_val:
            return item
            
    # Default absolut
    if input_val == "232901":
        return {
            "code": "232901",
            "label": "BVF Otopeni Calatori",
            "full_display": "232901 - BVF Otopeni Calatori (ROBU1030)",
            "aux_code": "ROBU1030"
        }
    return {"code": str(input_val), "label": "Unknown", "full_display": str(input_val), "aux_code": ""}

def resolve_ptf(input_val: Optional[str]) -> Optional[dict]:
    if not input_val:
        input_val = "37"
        
    for item in load_catalog("ptf_codes.json"):
        if item["code"] == input_val or item.get("full_display") == input_val:
            return item
            
    if input_val == "37":
        return {
            "code": "37",
            "label": "Nadlac 2 - A1",
            "full_display": "37 - Nadlac 2 - A1 (HU)",
            "country_hint": "HU"
        }
    return {"code": str(input_val), "label": "Unknown", "full_display": str(input_val), "country_hint": ""}


def build_shipment(
    invoice_path: str = None,
    packing_list_path: str = None,
    transport_text: str = "",
    carrier_text: str = "",
    operation_type: str = "import",
    currency: str = None,
    exchange_rate: float = None,
    transport_date: date = None,
    customs_office: str = None,
    ptf_code: str = None,
    dest_county: str = "",
    dest_city: str = "",
    dest_street: str = "",
    dest_number: str = "",
    dest_postal: str = "",
    dest_final_type: str = "Pe teritoriul national",
    dest_block: str = "",
    dest_staircase: str = "",
    dest_floor: str = "",
    dest_apartment: str = "",
    dest_other_info: str = "",
    carrier_name: str = "",
    carrier_vat: str = "",
    mode: str = "draft",  # 'draft' sau 'final_ready'
    aggregate: bool = None,
    operation_purpose_code: str = None,
) -> Shipment:
    """
    Construiește un Shipment complet din toate sursele de date.
    
    Pașii:
    1. Parsează invoice PDF → produse + date factură
    2. Parsează packing list PDF → greutăți
    3. Parsează text transport → container, vehicul, șofer
    4. Parsează text carrier → transportator
    5. Matching invoice ↔ packing list
    6. Distribuire greutăți
    7. Conversie valutară
    8. Agregare (opțional)
    9. Setare scop operațiune
    
    Returns:
        Shipment complet populat
    """
    warnings = []
    
    # ── 0. Document Classification ──
    invoice_pages_to_parse = None
    packing_pages_to_parse = None
    
    debug_metrics = {
        "invoice_lines_count_raw": 0,
        "invoice_lines_count_after_dedup": 0,
        "packing_lines_count_raw": 0,
        "packing_lines_count_after_dedup": 0,
        "exported_lines_count": 0,
        "detected_languages": {"invoice": "NONE", "packing": "NONE"},
        "selected_invoice_variant": [],
        "selected_packing_variant": []
    }
    
    if invoice_path and os.path.exists(invoice_path):
        from etransport.services.document_classifier import classify_document_pages
        inv_class = classify_document_pages(invoice_path)
        invoice_pages_to_parse = inv_class.invoice_pages
        
        # Dacă e fișier unic, luăm și pack-urile detectate aici
        if not packing_list_path or packing_list_path == invoice_path:
            packing_pages_to_parse = inv_class.packing_pages
            debug_metrics["detected_languages"] = inv_class.detected_languages
        else:
            debug_metrics["detected_languages"]["invoice"] = inv_class.detected_languages.get("invoice", "NONE")

    if packing_list_path and os.path.exists(packing_list_path) and packing_list_path != invoice_path:
        from etransport.services.document_classifier import classify_document_pages
        pack_class = classify_document_pages(packing_list_path)
        packing_pages_to_parse = pack_class.packing_pages
        # Fallback in caz ca parserul s-a derutat
        if not packing_pages_to_parse and pack_class.invoice_pages:
            packing_pages_to_parse = pack_class.invoice_pages
        debug_metrics["detected_languages"]["packing"] = pack_class.detected_languages.get("packing", "NONE")
    
    debug_metrics["selected_invoice_variant"] = invoice_pages_to_parse or "ALL"
    debug_metrics["selected_packing_variant"] = packing_pages_to_parse or "ALL"
    debug_metrics["invoice_pages_used"] = len(invoice_pages_to_parse) if invoice_pages_to_parse else "ALL"
    debug_metrics["packing_pages_used"] = len(packing_pages_to_parse) if packing_pages_to_parse else "ALL"
    debug_metrics["table_stitching_applied"] = True
    debug_metrics["header_extraction_method"] = "anchors"

    # ── 1. Parsare invoice ──
    invoice_data = None
    products = []
    
    if invoice_path and os.path.exists(invoice_path):
        # Dacă e gol complet înseamnă că nu s-a putut clasifica - fall back to None
        allowed_inv = invoice_pages_to_parse if invoice_pages_to_parse else None
        invoice_data = parse_invoice_pdf(invoice_path, allowed_pages=allowed_inv)
        products = invoice_data.products
        
        debug_metrics["invoice_lines_count_raw"] = len(products)
        debug_metrics["invoice_lines_count_after_dedup"] = len(products) # Deduplicat prin pagini
        
        warnings.extend(invoice_data.warnings)
    else:
        warnings.append("Invoice PDF lipsă sau nu există")
    
    # ── 2. Parsare packing list (sau fallback pe factura) ──
    packing_data = None
    effective_packing_path = packing_list_path if packing_list_path else invoice_path
    
    if effective_packing_path and os.path.exists(effective_packing_path):
        allowed_pack = packing_pages_to_parse if packing_pages_to_parse else None
        packing_data = parse_packing_list_pdf(effective_packing_path, allowed_pages=allowed_pack)
        
        debug_metrics["packing_lines_count_raw"] = len(packing_data.lines)
        debug_metrics["packing_lines_count_after_dedup"] = len(packing_data.lines)
        
        warnings.extend(packing_data.warnings)
    else:
        warnings.append("Document sursă lipsă pentru extracția greutăților")
    
    # ── 3. Parsare transport ──
    transport_info = parse_transport_text(transport_text)
    
    # ── 4. Parsare carrier ──
    carrier_info = parse_carrier_text(carrier_text)
    if carrier_name:
        carrier_info.carrier_name = carrier_name
    if carrier_vat:
        carrier_info.carrier_vat = carrier_vat
        
    # --- Auto-completare Transportator via SmartBill API ---
    from etransport.integrations.smartbill_client import search_carriers
    lookup_source = "manual"
    lookup_query = None
    lookup_count = 0
    
    if not carrier_info.carrier_name or not carrier_info.carrier_vat:
        search_q = carrier_info.carrier_vat or carrier_info.carrier_name
        if search_q:
            lookup_query = search_q
            results = search_carriers(search_q)
            lookup_count = len(results)
            if lookup_count == 1:
                carrier_info.carrier_name = results[0]["carrier_name"]
                carrier_info.carrier_vat = results[0]["carrier_vat"]
                lookup_source = "smartbill_api"
                
    debug_metrics["carrier_lookup_source"] = lookup_source
    debug_metrics["carrier_lookup_query"] = lookup_query
    debug_metrics["carrier_lookup_match_count"] = lookup_count
    
    # ── 5. Matching ──
    if products and packing_data and packing_data.lines:
        match_results = match_invoice_to_packing(products, packing_data.lines)
        products = apply_matching_results(
            products, packing_data.lines, match_results
        )
        
        # Populate matching debug metrics
        methods = [m.method for m in match_results]
        unmatched = methods.count("none")
        debug_metrics["matching_strategy_used"] = "index" if all(m == "index" for m in methods) else "fuzzy_hybrid"
        debug_metrics["index_matching_possible"] = (len(products) == len(packing_data.lines))
        debug_metrics["unmatched_invoice_lines"] = unmatched
        debug_metrics["rejected_matches"] = unmatched
        
        if invoice_data:
            debug_metrics["invoice_header_parsed"] = {
                "supplier": invoice_data.supplier_name,
                "invoice_no": invoice_data.invoice_number,
                "date": str(invoice_data.invoice_date) if invoice_data.invoice_date else None
            }
    
    # ── 6. Distribuire greutăți ──
    products = distribute_weights(products, packing_data)
    
    # ── 7. Conversie valutară ──
    effective_currency = currency or (
        invoice_data.currency if invoice_data else config.DEFAULT_CURRENCY
    )
    effective_rate = get_exchange_rate(effective_currency, exchange_rate)
    products = convert_currency(products, effective_currency, effective_rate)
    
    # ── 8. Agregare ──
    products = aggregate_lines(products, aggregate)
    
    # ── 9. Scop operațiune ──
    op_config = config.OPERATION_TYPES.get(operation_type, config.OPERATION_TYPES["import"])
    
    purpose_code = operation_purpose_code or config.DEFAULT_OPERATION_PURPOSE_CODE
    purpose_label = config.DEFAULT_OPERATION_PURPOSE_LABEL
    
    if operation_type in config.OPERATION_PURPOSE_OVERRIDES:
        override = config.OPERATION_PURPOSE_OVERRIDES[operation_type]
        purpose_code = override["code"]
        purpose_label = override.get("label", purpose_label)
    
    import json
    from etransport.services.historical_tariff_matcher import find_historical_match
    from etransport.services.carrier_history_service import save_carrier
    from etransport.services.tariff_memory_service import save_hs_memory
    from etransport.catalogs.tariff_code_repository import normalize_code_digits, hs6, hs8_candidate

    needs_review = []
    
    # PASS 1: Base matching
    pass1_results = []
    
    for product in products:
        if operation_purpose_code:
            product.operation_purpose_code = operation_purpose_code
        else:
            product.operation_purpose_code = purpose_code
            product.operation_purpose_label = purpose_label

        product.hs6_code = hs6(product.hs_code_raw)
        product.hs8_candidate_valid = bool(product.nc_code_8 and len(normalize_code_digits(product.nc_code_8)) == 8)

        res = find_historical_match(
            product_name_normalized=product.product_name_normalized,
            hs_code_raw=product.hs_code_raw,
            nc_code_8=product.nc_code_8
        )
        pass1_results.append((product, res))

    # PASS 2: Same-shipment propagation
    def compute_overlap(a: str, b: str) -> float:
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b: return 0.0
        return len(set_a & set_b) / max(len(set_a), len(set_b))

    for idx, (product, res) in enumerate(pass1_results):
        hs_raw_digits = normalize_code_digits(product.hs_code_raw)
        hs8 = hs8_candidate(product.hs_code_raw)
        
        # Check if we need propagation (it fell back to NC8, needs review, or weak confidence)
        needs_propagation = (
            res.get("source") in ("fallback_nc8", "needs_review") 
            or float(res.get("confidence", 0.0)) < 84.0
        )
        
        if needs_propagation:
            best_prop = None
            best_rule = None
            best_score = -1
            
            # Compare with all OTHER lines (we process globally now)
            for s_idx, (s_prod, s_res) in enumerate(pass1_results):
                if s_idx == idx: continue
                
                s_reason = s_res.get("needs_review_reason", "")
                s_method = s_res.get("method", "")
                s_conf = float(s_res.get("confidence", 0.0))
                
                s_strong = (
                    s_res.get("status") == "matched"
                    and s_res.get("code")
                    and s_res.get("source") not in ("fallback_nc8", "needs_review")
                    and s_reason not in ("hs_catalog_candidate_without_tariff_label", "no_strong_candidate", "invalid_or_missing_hs_code")
                    and s_conf >= 84.0
                )
                
                if not s_strong:
                    # propagation is completely disallowed from weak sources, even for exact products
                    continue
                
                s_hs_raw = normalize_code_digits(s_prod.hs_code_raw)
                s_hs8 = hs8_candidate(s_prod.hs_code_raw)
                s_name = s_prod.product_name_normalized
                p_name = product.product_name_normalized
                
                # Regula A: exact same product + same HS raw
                if s_name == p_name and s_hs_raw == hs_raw_digits and hs_raw_digits:
                    if 100 > best_score:
                        best_score = 100
                        best_prop = s_res
                        best_rule = ("Rule_A_Exact_Product_HS", "same_shipment_exact_product_hs")
                        
                # Regula B: same HS raw + strong token overlap (>= 60%)
                overlap = compute_overlap(p_name, s_name)
                if s_hs_raw == hs_raw_digits and hs_raw_digits and overlap >= 0.6:
                    score = 90 + overlap * 5
                    if score > best_score:
                        best_score = score
                        best_prop = s_res
                        best_rule = (f"Rule_B_Token_Overlap_{overlap:.1f}", "same_shipment_similar_product")
                        
                # Regula C: same HS8 family + repeated exact title
                if s_name == p_name and s_hs8 == hs8 and hs8:
                    if 85 > best_score:
                        best_score = 85
                        best_prop = s_res
                        best_rule = ("Rule_C_Same_HS8_Exact_Title", "same_shipment_hs8_title")

            if best_prop:
                res = {
                    **res,
                    "status": "matched",
                    "code": best_prop.get("code", res.get("code", "")),
                    "label": best_prop.get("label", ""),
                    "method": best_rule[1],
                    "source": "same_shipment",
                    "confidence": max(float(best_prop.get("confidence", 0.0)), 93.0),
                    "source_document": best_prop.get("source_document"),
                    "candidates": best_prop.get("candidates", []),
                    "needs_review_reason": "",
                }
                product.used_same_shipment_propagation = True
                product.tariff_needs_review_reason = best_rule[0] # Using this to store the rule audit temporarily
                # Salveaza noile fielduri pentru audit
                setattr(product, "same_shipment_source_is_strong", True)
                setattr(product, "same_shipment_source_method", best_prop.get("method", ""))
                setattr(product, "same_shipment_source_confidence", float(best_prop.get("confidence", 0.0)))

        # SAVE TO PRODUCT LINE MODEL
        product.tariff_code_db_match = res.get("code") if res.get("status") == "matched" else None
        product.tariff_code_match_label = res.get("label", "")
        product.tariff_code_match_method = res.get("method", "none")
        product.tariff_code_match_confidence = res.get("confidence", 0.0)
        product.tariff_code_match_source = res.get("source", "")
        product.tariff_code_candidates = res.get("candidates", []) or []
        
        # Only override needs_review_reason if we didn't just populate it with the propagation rule
        if not product.used_same_shipment_propagation:
            product.tariff_needs_review_reason = res.get("needs_review_reason", "")
            
        product.historical_match_found = bool(res.get("source") in ("historical", "confirmed_memory", "confirmed_hs_memory", "hs_memory", "same_shipment"))
        product.historical_source_document = res.get("source_document")

        # Do not save to historical cache runtime memory if it was a propagated output
        if product.tariff_code_db_match and res.get("source") not in ("fallback_nc8", "needs_review") and not product.used_same_shipment_propagation:
            if hs_raw_digits:
                save_hs_memory(
                    hs_code_raw=hs_raw_digits,
                    product_name_normalized=product.product_name_normalized,
                    nc8_code=res.get("code"),
                    tariff_label=res.get("label", ""),
                    source=f"runtime_{res.get('source', 'match')}",

                    confidence=float(res.get("confidence", 0.0)),
                    status="suggested",
                    supplier_name=invoice_data.supplier_name if invoice_data else "",
                    document_no=invoice_data.invoice_number if invoice_data else "",
                )

        if res.get("status") != "matched" or res.get("source") in ("fallback_nc8", "needs_review"):
            needs_review.append({
                "product_name": product.product_name_export,
                "product_name_normalized": product.product_name_normalized,
                "hs_code_raw": product.hs_code_raw,
                "nc_code_8": product.nc_code_8,
                "method": res.get("method", "none"),
                "source": res.get("source", ""),
                "reason": res.get("needs_review_reason", ""),
                "candidates": res.get("candidates", []) or [],
            })
    
    # Auto-save carrier
    if carrier_info and carrier_info.carrier_name and carrier_info.carrier_vat:
        save_carrier(carrier_info.carrier_name, carrier_info.carrier_vat)
        
    # --- Intercreptie OpenAI Autopilot ---
    from etransport.db import get_app_setting
    openai_key = get_app_setting("openai_api_key", "")
    openai_enabled = get_app_setting("openai_enabled", "false") == "true"
    
    if openai_key and openai_enabled:
        from etransport.services.openai_tariff_service import process_openai_batch_parallel, _lookup_catalog_candidates, _validate_code_against_catalog
        weak_products = []
        for i, p in enumerate(products):
            is_strong = getattr(p, "same_shipment_source_is_strong", False) or (p.tariff_code_match_confidence >= 84.0 and getattr(p, "tariff_needs_review_reason", "") == "")
            if not is_strong:
                # Look up valid catalog candidates for this product's HS code
                catalog_candidates = _lookup_catalog_candidates(p.hs_code_raw)
                weak_products.append({
                    "id": i,
                    "product_name_raw": p.product_name_raw,
                    "product_name_export": p.product_name_export,
                    "hs_code_raw": p.hs_code_raw,
                    "method": p.tariff_code_match_method,
                    "suggested_code": p.tariff_code_db_match,
                    "suggested_label": p.tariff_code_match_label,
                    "candidates": p.tariff_code_candidates,
                    "catalog_candidates": catalog_candidates,
                })
        
        if weak_products:
            print(f"🔄 OpenAI Autopilot: Interoghează {len(weak_products)} produse slabe...")
            model = get_app_setting("openai_model", "gpt-4o-mini")
            try:
                ai_results = process_openai_batch_parallel(weak_products, api_key=openai_key, model=model)
                for ai_res in ai_results:
                    idx = ai_res.id
                    if 0 <= idx < len(products):
                        p = products[idx]
                        p.tariff_code_db_match = ai_res.suggested_code
                        p.tariff_code_match_label = ai_res.suggested_label
                        p.tariff_code_match_method = "openai_auto"
                        p.tariff_code_match_confidence = ai_res.confidence
                        p.tariff_needs_review_reason = ai_res.rationale
                        setattr(p, "same_shipment_source_is_strong", True)
                        
                        if p.hs_code_raw:
                            from etransport.catalogs.tariff_code_repository import normalize_code_digits
                            hs_raw_digits = normalize_code_digits(p.hs_code_raw)
                            from etransport.services.tariff_memory_service import save_hs_memory
                            save_hs_memory(
                                hs_code_raw=hs_raw_digits,
                                product_name_normalized=p.product_name_normalized,
                                nc8_code=ai_res.suggested_code,
                                tariff_label=ai_res.suggested_label,
                                source="openai_auto",
                                confidence=ai_res.confidence,
                                status="suggested",
                                supplier_name=invoice_data.supplier_name if invoice_data else "",
                                document_no=invoice_data.invoice_number if invoice_data else "",
                            )
                print("✅ OpenAI Autopilot a finalizat meciurile!")
            except Exception as e:
                import traceback
                print(f"❌ OpenAI Autopilot EROARE: {e}")
                traceback.print_exc()
    
    # --- Post-validation: validate ONLY AI-generated codes against TARIC catalog ---
    # Historical/confirmed memory codes are already proven to work in SmartBill,
    # so we must NOT override them with catalog guesses.
    try:
        from etransport.services.openai_tariff_service import _validate_code_against_catalog
        for p in products:
            if p.tariff_code_db_match and p.tariff_code_match_method == "openai_auto":
                validated = _validate_code_against_catalog(p.tariff_code_db_match)
                if validated != p.tariff_code_db_match:
                    print(f"  🔧 Validare TARIC: {p.tariff_code_db_match} → {validated} ({p.product_name_normalized[:30]})")
                    p.tariff_code_db_match = validated
    except Exception as e:
        print(f"  ⚠️ Validare TARIC skip: {e}")
            
    # Salvare pentru review manual in DB (replaces tariff_codes_needs_review.json)
    try:
        from etransport.services.review_queue_service import save_to_review_queue
        queue_items = []
        for p in products:
            is_strong = getattr(p, "same_shipment_source_is_strong", False) or (p.tariff_code_match_confidence >= 84.0 and getattr(p, "tariff_needs_review_reason", "") == "")
            if not is_strong:
                queue_items.append({
                    'invoice_number': invoice_data.invoice_number if invoice_data else "",
                    'document_date': invoice_data.invoice_date.isoformat() if invoice_data and invoice_data.invoice_date else None,
                    'source_line_no': p.source_line_no,
                    'supplier': invoice_data.supplier_name if invoice_data else "",
                    'product_name_raw': p.product_name_raw,
                    'product_name_export': p.product_name_export,
                    'hs_code_raw': p.hs_code_raw,
                    'hs6': getattr(p, "hs6_code", ""),
                    'nc8_candidate': p.nc_code_8,
                    'suggested_code': p.tariff_code_db_match,
                    'suggested_label': p.tariff_code_match_label,
                    'method': p.tariff_code_match_method,
                    'source': p.tariff_code_match_source,
                    'confidence': p.tariff_code_match_confidence,
                    'reason': p.tariff_needs_review_reason,
                    'is_strong': False,
                    'historical_match_found': p.historical_match_found,
                })
        if queue_items:
            save_to_review_queue(queue_items)
    except Exception as e:
        print(f"Error saving to review queue DB: {e}")
    
    debug_metrics["exported_lines_count"] = len([p for p in products if p.source_type == "invoice"])
        
    # ── Construcție Shipment ──
    shipment = Shipment(
        operation_type=operation_type,
        operation_type_code=op_config["code"],
        transport_date=transport_date,
        supplier_name=invoice_data.supplier_name if invoice_data else "",
        supplier_tax_code=invoice_data.supplier_tax_code if invoice_data else None,
        supplier_country=invoice_data.supplier_country if invoice_data else "",
        carrier_name=carrier_info.carrier_name,
        carrier_vat=carrier_info.carrier_vat,
        invoice_number=invoice_data.invoice_number if invoice_data else None,
        invoice_date=invoice_data.invoice_date if invoice_data else None,
        document_type="Commercial Invoice",
        document_number=invoice_data.invoice_number if invoice_data else None,
        document_date=invoice_data.invoice_date if invoice_data else None,
        container_no=invoice_data.container_no if (invoice_data and invoice_data.container_no) else transport_info.container_no,
        vehicle_no=transport_info.vehicle_no,
        trailer_no=transport_info.trailer_no,
        driver_name=transport_info.driver_name,
        driver_phone=transport_info.driver_phone,
        start_customs_office_code=customs_office if op_config["code"] == 40 else None,
        start_ptf_code=ptf_code if op_config["code"] == 10 else None,
        start_customs_office=resolve_customs_office(customs_office) if op_config["code"] == 40 else None,
        start_ptf=resolve_ptf(ptf_code) if op_config["code"] == 10 else None,
        destination={
            "final_type": dest_final_type,
            "country": "RO",
            "county": dest_county,
            "city": dest_city,
            "street": dest_street,
            "number": dest_number,
            "postal_code": dest_postal,
            "block": dest_block,
            "staircase": dest_staircase,
            "floor": dest_floor,
            "apartment": dest_apartment,
            "other_info": dest_other_info
        },
        currency=effective_currency,
        exchange_rate_to_ron=effective_rate,
        products=products,
        warnings=warnings,
        debug_info=debug_metrics,
    )
    
    return shipment


def generate_outputs(
    shipment: Shipment,
    output_dir: str = None,
) -> dict:
    """
    Generează fișierele de output (XLSX + JSON).
    """
    output_dir = output_dir or config.DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    
    report = validate_shipment(shipment)
    print(report.summary())
    
    xlsx_path = os.path.join(output_dir, "smartbill_import.xlsx")
    export_smartbill_xlsx(shipment, xlsx_path)
    
    json_path = os.path.join(output_dir, "shipment_audit.json")
    export_audit_json(shipment, report, json_path)
    
    snapshot_path = os.path.join(output_dir, "shipment_input_snapshot.json")
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            f.write(shipment.model_dump_json(indent=2))
    except Exception as e:
        pass
    
    print(f"\n📄 XLSX generat: {xlsx_path}")
    print(f"📋 Audit JSON generat: {json_path}")
    print(f"📸 Snapshot JSON generat: {snapshot_path}")
    
    return {
        "xlsx_path": xlsx_path,
        "json_path": json_path,
        "snapshot_path": snapshot_path,
        "validation_report": report,
    }


def main():
    """Entry point CLI."""
    parser = argparse.ArgumentParser(
        description="Generator SmartBill e-Transport XLSX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemple de utilizare:

  # Import cu invoice și packing list PDF:
  python -m etransport.main \\
      --invoice factura.pdf \\
      --packing-list packing_list.pdf \\
      --transport "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel" \\
      --carrier "ANDI TRANS SRL RO5607012" \\
      --operation import \\
      --currency USD --exchange-rate 4.65 \\
      --customs-office ROCT0900

  # Achiziție intracomunitară:
  python -m etransport.main \\
      --invoice factura_ue.pdf \\
      --operation achizitie_intracomunitara \\
      --currency EUR --exchange-rate 4.97 \\
      --ptf ROND0200
        """,
    )
    
    parser.add_argument('--invoice', '-i', help='Calea către invoice PDF')
    parser.add_argument('--packing-list', '-p', help='Calea către packing list PDF')
    parser.add_argument('--transport', '-t', help='Text liber date transport')
    parser.add_argument('--carrier', '-c', help='Text liber transportator')
    parser.add_argument(
        '--operation', '-o',
        choices=['import', 'achizitie_intracomunitara'],
        default='import',
        help='Tip operațiune (default: import)',
    )
    parser.add_argument('--currency', default=None, help='Moneda invoice (USD, EUR)')
    parser.add_argument('--exchange-rate', type=float, default=None, help='Curs valutar')
    parser.add_argument('--transport-date', help='Data transport (YYYY-MM-DD)')
    parser.add_argument('--customs-office', help='Cod birou vamal (pentru import)')
    parser.add_argument('--ptf', help='Cod PTF (pentru AIC)')
    parser.add_argument('--dest-county', default='', help='Județ destinație')
    parser.add_argument('--dest-city', default='', help='Oraș destinație')
    parser.add_argument('--dest-street', default='', help='Stradă destinație')
    parser.add_argument('--dest-number', default='', help='Număr adresă')
    parser.add_argument('--dest-postal', default='', help='Cod poștal')
    parser.add_argument('--dest-final-type', default='Pe teritoriul national', help='Tip Destinație')
    parser.add_argument('--dest-block', default='', help='Bloc')
    parser.add_argument('--dest-staircase', default='', help='Scară')
    parser.add_argument('--dest-floor', default='', help='Etaj')
    parser.add_argument('--dest-apartment', default='', help='Apartament')
    parser.add_argument('--dest-other-info', default='', help='Alte Info')
    parser.add_argument('--carrier-name', default='', help='Nume transportator')
    parser.add_argument('--carrier-vat', default='', help='CUI transportator')
    parser.add_argument('--mode', default='draft', choices=['draft', 'final_ready'], help='Mod validare (draft sau final_ready)')
    parser.add_argument('--aggregate', action='store_true', help='Agregă liniile')
    parser.add_argument('--output-dir', default=config.DEFAULT_OUTPUT_DIR, help='Director output')
    parser.add_argument('--purpose-code', default=None, help='Cod scop operațiune override')
    
    args = parser.parse_args()
    
    # Parse transport date
    transport_date = None
    if args.transport_date:
        from dateutil import parser as date_parser
        transport_date = date_parser.parse(args.transport_date).date()
    
    # Build shipment
    shipment = build_shipment(
        invoice_path=args.invoice,
        packing_list_path=args.packing_list,
        transport_text=args.transport or "",
        carrier_text=args.carrier or "",
        operation_type=args.operation,
        currency=args.currency,
        exchange_rate=args.exchange_rate,
        transport_date=transport_date,
        customs_office=args.customs_office,
        ptf_code=args.ptf,
        dest_county=args.dest_county,
        dest_city=args.dest_city,
        dest_street=args.dest_street,
        dest_number=args.dest_number,
        dest_postal=args.dest_postal,
        dest_block=args.dest_block,
        dest_staircase=args.dest_staircase,
        dest_floor=args.dest_floor,
        dest_apartment=args.dest_apartment,
        dest_other_info=args.dest_other_info,
        dest_final_type=args.dest_final_type,
        carrier_name=args.carrier_name,
        carrier_vat=args.carrier_vat,
        aggregate=args.aggregate or None,
        operation_purpose_code=args.purpose_code,
        mode=args.mode
    )
    
    # Final_Ready validations
    required_missing = []
    if args.mode == "final_ready":
        # Rule 6: Debug explicitly read values from the final shipment object
        required_field_debug = {
            "supplier_name": shipment.supplier_name,
            "invoice_number": shipment.invoice_number,
            "invoice_date": shipment.invoice_date,
            "carrier_name": shipment.carrier_name,
            "carrier_vat": shipment.carrier_vat,
            "destination_county": shipment.destination.county,
            "destination_city": shipment.destination.city,
        }
        print(f"DEBUG FINAL READY: {required_field_debug}")

        if not shipment.transport_date: required_missing.append("transport_date")
        if not shipment.carrier_name: required_missing.append("carrier_name")
        if not shipment.carrier_vat: required_missing.append("carrier_vat")
        
        if args.operation == 'import':
            if not shipment.start_customs_office or not shipment.start_customs_office.code:
                required_missing.append("start_customs_office.code")
        elif args.operation == 'achizitie_intracomunitara':
            if not shipment.start_ptf or not shipment.start_ptf.code:
                required_missing.append("start_ptf.code")
                
        # Required operation addresses
        if not shipment.destination.county: required_missing.append("destination_county")
        if not shipment.destination.city: required_missing.append("destination_city")
        if not shipment.destination.street: required_missing.append("destination_street")
        if not shipment.destination.number: required_missing.append("destination_number")
        if not shipment.destination.postal_code: required_missing.append("destination_postal_code")
        
        # Required commercial properties
        if not shipment.invoice_number: required_missing.append("invoice_number")
        if not shipment.invoice_date: required_missing.append("invoice_date")
        if not shipment.supplier_name: required_missing.append("supplier_name")
        
        if required_missing:
            raise ValueError(f"Lipsesc campuri operationale obligatorii in modul final_ready: {', '.join(required_missing)}")
    
    # Generate outputs
    results = generate_outputs(shipment, output_dir=args.output_dir)
    
    if not results["validation_report"].is_valid:
        sys.exit(1)


if __name__ == "__main__":
    main()
