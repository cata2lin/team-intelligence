"""
Serviciu pentru clasificarea paginilor din PDF-uri complexe.
Detectează automat secțiunile de Invoice și Packing List dintr-un singur PDF,
precum și variantele bilingve (RO/EN), pentru a returna paginile stricte
care trebuie parcurse, eliminând duplicările.
"""
import re
from dataclasses import dataclass
import pdfplumber

@dataclass
class PageInfo:
    index: int
    text_snippet: str
    doc_type: str = "UNKNOWN"  # 'INVOICE' sau 'PACKING'
    lang: str = "UNKNOWN"      # 'RO' sau 'EN'


class SelectedPages:
    def __init__(self):
        self.invoice_pages: list[int] = []
        self.packing_pages: list[int] = []
        self.detected_languages: dict[str, str] = {"invoice": "NONE", "packing": "NONE"}


def classify_document_pages(pdf_path: str) -> SelectedPages:
    """
    Scanează un PDF cap-coadă, determină tipul fiecărei pagini (Invoice/Packing)
    și limba (RO/EN). Reunește paginile contigue în blocuri.
    La final, selectează cea mai bună variantă (preferă RO) pentru fiecare tip
    și elimină celelalte variante traduse.
    """
    pages_info = []
    
    # ── 1. Analiza de bază per pagină ──
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages):
                # Extragem text brut, limitat la primele ~1500 caractere 
                text = (page.extract_text() or "")[:1500].lower()
                
                info = PageInfo(index=idx, text_snippet=text)
                
                # Factură Comercială / Commercial Invoice
                if re.search(r'\bfactur[aă]\s*(comercial[aă]|proforma)?\b', text) or \
                   re.search(r'\bcommercial\s*invoice\b', text) or \
                   re.search(r'\binvoice\b', text[:500]): 
                    info.doc_type = "INVOICE"
                
                if re.search(r'\b(packing\s*list|lista\s*de\s*ambalare|coletar|lista\s*de\s*colisaj)\b', text):
                    info.doc_type = "PACKING"
                
                # Check language scores
                ro_keywords = ["furnizor", "cumpărător", "cumparator", "cantitate", "greutate", "pret", "valoare", "denumire"]
                en_keywords = ["supplier", "buyer", "quantity", "weight", "price", "value", "description"]
                
                ro_score = sum(1 for kw in ro_keywords if kw in text)
                en_score = sum(1 for kw in en_keywords if kw in text)
                
                if ro_score > en_score and ro_score > 0:
                    info.lang = "RO"
                elif en_score > ro_score and en_score > 0:
                    info.lang = "EN"
                
                if ro_score > 0 and ro_score == en_score:
                    info.lang = "RO"
                    
                pages_info.append(info)
    except Exception as e:
        print(f"Eroare la clasificarea paginilor: {e}")
        return SelectedPages()

    # ── 2. Gruparea paginilor și propagarea stării din inerție ──
    current_type = "UNKNOWN"
    current_lang = "UNKNOWN"
    
    for info in pages_info:
        if info.doc_type == "UNKNOWN" and current_type != "UNKNOWN":
            info.doc_type = current_type
        else:
            current_type = info.doc_type
            
        if info.lang == "UNKNOWN" and current_lang != "UNKNOWN":
            info.lang = current_lang
        else:
            current_lang = info.lang

    # ── 3. Crearea "variantelor" / dedup ──
    variants = {}
    for info in pages_info:
        ds = info.doc_type if info.doc_type != "UNKNOWN" else "INVOICE"
        dl = info.lang if info.lang != "UNKNOWN" else "EN"
        
        if ds not in variants:
            variants[ds] = {}
        if dl not in variants[ds]:
            variants[ds][dl] = []
        
        variants[ds][dl].append(info.index)

    # ── 4. Selecția variantelor optime ──
    result = SelectedPages()
    
    def select_best_variant(doc_category: str) -> tuple[list[int], str]:
        if doc_category not in variants:
            return [], "NONE"
        langs = variants[doc_category]
        if not langs:
            return [], "NONE"
            
        if "RO" in langs and len(langs["RO"]) > 0:
            return langs["RO"], "RO"
        elif "EN" in langs and len(langs["EN"]) > 0:
            return langs["EN"], "EN"
        
        first_lang = list(langs.keys())[0]
        return langs[first_lang], first_lang

    inv_pages, inv_lang = select_best_variant("INVOICE")
    result.invoice_pages = inv_pages
    result.detected_languages["invoice"] = inv_lang

    pack_pages, pack_lang = select_best_variant("PACKING")
    result.packing_pages = pack_pages
    result.detected_languages["packing"] = pack_lang
    
    if not result.invoice_pages and not result.packing_pages and pages_info:
        result.invoice_pages = [p.index for p in pages_info]
        result.detected_languages["invoice"] = pages_info[0].lang if pages_info[0].lang != "UNKNOWN" else "EN"

    return result
