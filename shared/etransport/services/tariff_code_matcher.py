from etransport.catalogs.tariff_code_repository import (
    find_tariff_by_code,
    search_tariff_by_label,
    get_product_override
)

def suggest_tariff_for_product(product_name_normalized: str, hs_code_raw: str = None, nc_code_8: str = None):
    """
    Încearcă să găsească cel mai bun cod tarifar validat în sistemul local
    Pentru audit va returna un dict:
    {
        "status": "matched" | "not_found",
        "code": "8 cifre...",
        "label": "descriere...",
        "method": "override" | "exact_raw_hs" | "exact_nc8" | "prefix" | "name_search",
        "confidence": float
    }
    """
    
    # 1. Check override
    override = get_product_override(product_name_normalized)
    if override:
        return {
            "status": "matched",
            "code": override["matched_tariff_code"],
            "label": override["matched_tariff_label"],
            "method": "override",
            "confidence": override["confidence"] or 100.0
        }
        
    candidates = []
    
    # 2. Match exact codes
    if nc_code_8:
        exact_nc8 = find_tariff_by_code(nc_code_8)
        if exact_nc8:
            return {
                "status": "matched",
                "code": exact_nc8["code"],
                "label": exact_nc8["label"],
                "method": "exact_nc8",
                "confidence": 100.0
            }

    if hs_code_raw:
        exact_hs = find_tariff_by_code(hs_code_raw)
        if exact_hs:
             return {
                "status": "matched",
                "code": exact_hs["code"],
                "label": exact_hs["label"],
                "method": "exact_raw_hs",
                "confidence": 100.0
            }
            
        # 3. Try fallback to prefix logic if raw_hs is longer than 4 chars
        if len(hs_code_raw) > 4:
            for cutoff in [len(hs_code_raw)-1, 8, 6, 4]:
                if cutoff >= len(hs_code_raw) or cutoff < 4:
                    continue
                prefix = hs_code_raw[:cutoff]
                matches = search_tariff_by_label(prefix, limit=1)
                # Ensure the match actually starts with the prefix
                valid_matches = [m for m in matches if m["code"].startswith(prefix)]
                if valid_matches:
                    return {
                        "status": "matched",
                        "code": valid_matches[0]["code"],
                        "label": valid_matches[0]["label"],
                        "method": "prefix",
                        "confidence": 80.0
                    }

    # 4. Search by product name as last resort
    if product_name_normalized:
        # Simplistic approach: split words, remove small words
        words = [w for w in product_name_normalized.split() if len(w) > 3]
        for w in words:
            matches = search_tariff_by_label(w, limit=5)
            if matches:
                # We return the first one but with low confidence
                return {
                    "status": "matched",
                    "code": matches[0]["code"],
                    "label": matches[0]["label"],
                    "method": "name_search",
                    "confidence": 40.0,
                    "suggestions": [m["full_display"] for m in matches]
                }
                
    return {
        "status": "not_found",
        "code": nc_code_8 or hs_code_raw, # keep fallback
        "label": "",
        "method": "none",
        "confidence": 0.0
    }
