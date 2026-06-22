import concurrent.futures
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Tuple
import openai

class OpenAIMatchItem(BaseModel):
    id: int = Field(description="ID-ul liniei primit în comandă (foarte important să coincidă)")
    suggested_code: str = Field(description="Cel mai bun cod tarifar (NC8, TARIC sau HS)")
    suggested_label: str = Field(description="O etichetă scurtă care descrie categoria")
    confidence: float = Field(description="Nivelul de încredere (0-100)")
    rationale: str = Field(description="Scurtă explicație pentru codul ales")
    source_preference: str = Field(description="Sursa preferinței (ex: 'historical_strong')")
    whether_needs_review: bool = Field(description="True dacă decizia necesită atenție")

class OpenAIMatchBatchResponse(BaseModel):
    items: List[OpenAIMatchItem]


def _lookup_catalog_candidates(hs_code_raw: str) -> List[Dict[str, str]]:
    """Look up valid NC8 codes from SmartBill's tariff_codes table for the given HS prefix."""
    if not hs_code_raw:
        return []
    
    import re
    digits = re.sub(r'\D', '', hs_code_raw)
    if len(digits) < 4:
        return []
    
    from etransport.db import get_db_connection
    candidates = []
    
    # Search SmartBill codes by HS6 prefix (first 6 digits)
    hs6 = digits[:6]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        rows = cursor.execute("""
            SELECT DISTINCT code, label 
            FROM tariff_codes 
            WHERE code LIKE ? AND is_active = 1
            ORDER BY code
            LIMIT 30
        """, (hs6 + "%",)).fetchall()
        
        for row in rows:
            candidates.append({
                "code": row["code"],
                "description": row["label"][:80]
            })
        
        # If no results with HS6, try HS4
        if not candidates:
            hs4 = digits[:4]
            rows = cursor.execute("""
                SELECT DISTINCT code, label 
                FROM tariff_codes 
                WHERE code LIKE ? AND is_active = 1
                ORDER BY code
                LIMIT 30
            """, (hs4 + "%",)).fetchall()
            
            for row in rows:
                candidates.append({
                    "code": row["code"],
                    "description": row["label"][:80]
                })
    
    return candidates


def _validate_code_against_catalog(code: str) -> str:
    """Validate an NC8 code against SmartBill's tariff_codes. Returns the code if valid, or the closest valid code."""
    import re
    digits = re.sub(r'\D', '', code)
    if len(digits) < 6:
        return code
    
    from etransport.db import get_db_connection
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check exact match in SmartBill codes
        d8 = digits[:8]
        row = cursor.execute("SELECT code FROM tariff_codes WHERE code = ? AND is_active = 1", (d8,)).fetchone()
        if row:
            return d8
        
        # Try to find closest code under same HS6 prefix
        hs6 = digits[:6]
        row = cursor.execute("""
            SELECT code FROM tariff_codes 
            WHERE code LIKE ? AND is_active = 1
            ORDER BY code
            LIMIT 1
        """, (hs6 + "%",)).fetchone()
        
        if row:
            return row["code"]
    
    return code  # Return original if nothing found


def process_openai_chunk(chunk_rows: List[Dict[str, Any]], api_key: str, model: str = "gpt-4o-mini") -> Tuple[List[OpenAIMatchItem], int]:
    """Procesează un chunk de rânduri folosind modelul OpenAI furnizat."""
    if not chunk_rows:
        return [], 0
        
    client = openai.OpenAI(api_key=api_key)
    user_prompt = "Evaluează următoarele produse și alege codul NC8 CORECT din lista de candidați valizi.\n"
    user_prompt += "IMPORTANT: Trebuie să alegi DOAR coduri din lista de 'Coduri NC8 valide' furnizată. NU inventa coduri noi!\n\n"
    
    for row in chunk_rows:
        user_prompt += f"--- ID Produs: {row.get('id', 'N/A')} ---\n"
        user_prompt += f"Nume pe factură: {row.get('product_name_raw', '')} {row.get('product_name_export', '')}\n"
        user_prompt += f"Cod Brut: {row.get('hs_code_raw', '')} | Metoda curentă: {row.get('method', '')}\n"
        user_prompt += f"Sugestie curentă slabă: {row.get('suggested_code', '')} - {row.get('suggested_label', '')}\n"
        if row.get('hint'):
            user_prompt += f"INDICAȚIE STRICTĂ UTILIZATOR: {row.get('hint')}\n"
        
        # Add historical candidates
        hist_candidates = row.get('candidates', [])
        if hist_candidates:
            user_prompt += "Candidați din istoric:\n"
            for c in hist_candidates[:5]:
                user_prompt += f" - {c.get('nc8_code')} ({c.get('tariff_label')})\n"
        
        # Add valid catalog candidates
        catalog_candidates = row.get('catalog_candidates', [])
        if catalog_candidates:
            user_prompt += "Coduri NC8 valide (ALEGE DOAR DIN ACESTEA!):\n"
            for c in catalog_candidates:
                user_prompt += f" ✓ {c.get('code')} — {c.get('description')}\n"
        
        user_prompt += "\n"
        
    system_prompt = (
        "Ești un expert vamal român. Asignează cel mai corect cod tarifar NC8 pentru fiecare produs. "
        "REGULĂ CRITICĂ: Trebuie să alegi EXCLUSIV coduri din lista de 'Coduri NC8 valide' furnizată pentru fiecare produs. "
        "NU genera și NU inventa coduri noi — SmartBill va respinge orice cod care nu e în nomenclatorul oficial."
    )
    
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format=OpenAIMatchBatchResponse,
    )
    
    parsed_items = completion.choices[0].message.parsed.items
    
    # Track usage
    usage = completion.usage
    usage_info = {
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }
    # GPT-4o-mini: $0.15/1M input, $0.60/1M output
    cost = (usage_info["prompt_tokens"] * 0.15 + usage_info["completion_tokens"] * 0.60) / 1_000_000
    usage_info["cost_usd"] = round(cost, 6)
    
    # Save to DB
    try:
        from etransport.services.review_queue_service import get_app_setting, set_app_setting
        import json
        stats_raw = get_app_setting("openai_usage_stats", '{"total_requests": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_cost_usd": 0}')
        stats = json.loads(stats_raw)
        stats["total_requests"] = stats.get("total_requests", 0) + 1
        stats["total_prompt_tokens"] = stats.get("total_prompt_tokens", 0) + usage_info["prompt_tokens"]
        stats["total_completion_tokens"] = stats.get("total_completion_tokens", 0) + usage_info["completion_tokens"]
        stats["total_cost_usd"] = round(stats.get("total_cost_usd", 0) + cost, 6)
        set_app_setting("openai_usage_stats", json.dumps(stats))
    except Exception:
        pass
    
    # Post-validation: ensure all codes exist in SmartBill
    validated_items = []
    for item in parsed_items:
        validated_code = _validate_code_against_catalog(item.suggested_code)
        if validated_code != item.suggested_code:
            print(f"  🔧 Corectat cod invalid: {item.suggested_code} → {validated_code} (produs ID {item.id})")
            item.suggested_code = validated_code
        validated_items.append(item)
    
    return validated_items, len(chunk_rows)

def process_openai_batch_parallel(rows: List[Dict[str, Any]], api_key: str, model: str = "gpt-4o-mini", chunk_size: int = 20, max_workers: int = 5) -> List[OpenAIMatchItem]:
    """
    Procesează asincron via ThreadPoolExecutor listele mari de dicționare.
    Fiecare dicționar trebuie să aibă populat câmpul 'id' pentru ca răspunsul să fie mapat corect.
    """
    all_items = []
    
    # Creează grupurile
    groups = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_chunk = {
            executor.submit(process_openai_chunk, group, api_key, model): group
            for group in groups
        }
        
        for future in concurrent.futures.as_completed(future_to_chunk):
            try:
                items, _ = future.result()
                all_items.extend(items)
            except Exception as e:
                import traceback
                print(f"Eroare procesare GPT chunk: {e}")
                traceback.print_exc()
                
    return all_items
