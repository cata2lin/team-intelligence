import re
from etransport.catalogs.tariff_code_repository import upsert_tariff_code

def import_smartbill_tariff_codes(raw_input: str):
    """
    Parse HTML or plain text from SmartBill autocomplete and save to DB.
    """
    total_found = 0
    total_saved = 0
    
    # Check if raw_html
    if "<li" in raw_input or "<a" in raw_input:
        # Extract text from inside <a> tags
        matches = re.findall(r'<a[^>]*>(.*?)</a>', raw_input)
        lines = [m.strip() for m in matches]
    else:
        lines = [line.strip() for line in raw_input.splitlines() if line.strip()]

    for line in lines:
        if " - " not in line:
            continue
        
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            code = parts[0].strip()
            label = parts[1].strip()
            full_display = line
            
            # Simple validation: code must be digits
            if code.isdigit():
                upsert_tariff_code(code, label, full_display, source="smartbill")
                total_saved += 1
        total_found += 1
        
    return {"total_found": total_found, "total_saved": total_saved}
