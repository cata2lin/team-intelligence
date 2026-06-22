"""
Serviciu de matching între liniile de invoice și packing list.
Strategia pe 4 niveluri:
1. Match pe index/ordine (dacă nr. linii și cantități sunt compatibile)
2. Match pe text normalizat + cantitate
3. Match fuzzy cu rapidfuzz
4. Warning pentru linii nematchuite
"""
from typing import Optional
from rapidfuzz import fuzz

from etransport.models.product_line import ProductLine
from etransport.parsers.packing_list_parser import PackingListLine
from etransport.utils.text_normalizer import normalize_text
from etransport import config


class MatchResult:
    """Rezultatul matching-ului pentru o linie de invoice."""
    
    def __init__(
        self,
        invoice_line_no: int,
        packing_line_no: Optional[int],
        confidence: float,
        method: str,
        warnings: list[str] = None,
    ):
        self.invoice_line_no = invoice_line_no
        self.packing_line_no = packing_line_no
        self.confidence = confidence
        self.method = method
        self.warnings = warnings or []


def match_invoice_to_packing(
    invoice_lines: list[ProductLine],
    packing_lines: list[PackingListLine],
) -> list[MatchResult]:
    """
    Realizează matching-ul între liniile de invoice și packing list.
    
    Args:
        invoice_lines: Linii de produs din invoice
        packing_lines: Linii din packing list
        
    Returns:
        Lista de MatchResult, câte unul per linie de invoice
    """
    results = []
    
    if not packing_lines:
        # Nu avem packing list, returnăm warning-uri
        for inv_line in invoice_lines:
            results.append(MatchResult(
                invoice_line_no=inv_line.source_line_no or 0,
                packing_line_no=None,
                confidence=0.0,
                method="none",
                warnings=["Packing list lipsă, greutăți nedisponibile pe linie"],
            ))
        return results
    
    # Track packing lines deja matchuite
    matched_packing = set()
    
    # ── Nivel 1: Match pe index (dacă nr. linii coincide) ──
    if _can_index_match(invoice_lines, packing_lines):
        for i, inv_line in enumerate(invoice_lines):
            pl_line = packing_lines[i]
            results.append(MatchResult(
                invoice_line_no=inv_line.source_line_no or (i + 1),
                packing_line_no=pl_line.line_no,
                confidence=100.0,
                method="index",
            ))
            matched_packing.add(i)
        return results
    
    # ── Nivelurile 2-4: Match textual ──
    for inv_line in invoice_lines:
        match = _find_best_match(
            inv_line, packing_lines, matched_packing
        )
        results.append(match)
        if match.packing_line_no is not None:
            # Găsim indexul din packing_lines
            for idx, pl in enumerate(packing_lines):
                if pl.line_no == match.packing_line_no:
                    matched_packing.add(idx)
                    break
    
    return results


def _can_index_match(
    invoice_lines: list[ProductLine],
    packing_lines: list[PackingListLine],
) -> bool:
    """
    Verifică dacă putem match-ui pe index:
    - Același număr de linii
    - Cantitățile sunt compatibile (aceleași sau similare)
    """
    if len(invoice_lines) != len(packing_lines):
        return False
    
    # Daca invoice-ul a fost corect decupat si avem 30-30, atunci cu siguranta e ordinea perfecta
    # Mai punem o mica verificare pe prima linie (sa zicem ca are cat de cat cantitate proportionata sau nume relativ inrudit)
    # dar userul zice "dacă invoice și packing au același număr de linii... atunci matching-ul trebuie să fie invoice[i] -> packing[i]"
    # Fara exceptii:
    return True


def _find_best_match(
    inv_line: ProductLine,
    packing_lines: list[PackingListLine],
    matched_indices: set,
) -> MatchResult:
    """Găsește cel mai bun match pentru o linie de invoice."""
    inv_text = inv_line.product_name_normalized
    inv_no = inv_line.source_line_no or 0
    
    best_score = 0.0
    best_idx = None
    best_method = "none"
    
    for idx, pl_line in enumerate(packing_lines):
        if idx in matched_indices:
            continue
        
        pl_text = pl_line.description_normalized
        
        # ── Nivel 2: Match exact text normalizat + cantitate ──
        if inv_text and pl_text and inv_text == pl_text:
            score = 100.0
            if inv_line.quantity > 0 and pl_line.quantity > 0:
                if abs(inv_line.quantity - pl_line.quantity) < 0.01:
                    score = 100.0
                else:
                    score = 90.0
            if score > best_score:
                best_score = score
                best_idx = idx
                best_method = "text_exact"
            continue
        
        # ── Nivel 3: Match fuzzy ──
        if inv_text and pl_text:
            fuzzy_score = fuzz.token_sort_ratio(inv_text, pl_text)
            
            # Boost dacă cantitățile se potrivesc perfect
            qty_match = False
            if inv_line.quantity > 0 and pl_line.quantity > 0:
                if abs(inv_line.quantity - pl_line.quantity) < 0.01:
                    fuzzy_score = min(100.0, fuzzy_score + 15)
                    qty_match = True
            
            # D. Validare: Sanity Check absurd
            if not qty_match:
                # Daca linia din factura are 70k, nu ii dam net-ul pentru un pachet de 100
                if inv_line.quantity > 1000 and pl_line.quantity < inv_line.quantity * 0.1:
                    continue  # Mismatch evident de scala
                # Nu lasam match text-fuzzy slab daca nu e sustinut de qty
                if fuzzy_score < 70:
                    continue

            # Prag strict marit la 80
            if fuzzy_score > best_score and fuzzy_score >= 80:
                best_score = fuzzy_score
                best_idx = idx
                best_method = "fuzzy"
    
    if best_idx is not None:
        return MatchResult(
            invoice_line_no=inv_no,
            packing_line_no=packing_lines[best_idx].line_no,
            confidence=best_score,
            method=best_method,
        )
    
    # ── Nivel 4: Nu s-a găsit match ──
    return MatchResult(
        invoice_line_no=inv_no,
        packing_line_no=None,
        confidence=0.0,
        method="none",
        warnings=[
            f"Linia {inv_no} din invoice nu a putut fi matchuită "
            f"cu nicio linie din packing list"
        ],
    )


def apply_matching_results(
    invoice_lines: list[ProductLine],
    packing_lines: list[PackingListLine],
    match_results: list[MatchResult],
) -> list[ProductLine]:
    """
    Aplică rezultatele matching-ului: transferă greutățile 
    din packing list pe liniile de invoice matchuite.
    """
    pl_by_line_no = {pl.line_no: pl for pl in packing_lines}
    
    for inv_line, match in zip(invoice_lines, match_results):
        inv_line.matched_packing_line_no = match.packing_line_no
        inv_line.match_confidence = match.confidence
        inv_line.match_method = match.method
        inv_line.warnings.extend(match.warnings)
        
        if match.packing_line_no is not None:
            pl = pl_by_line_no.get(match.packing_line_no)
            if pl:
                inv_line.net_weight_kg = pl.net_weight_kg
                inv_line.gross_weight_kg = pl.gross_weight_kg
    
    return invoice_lines
