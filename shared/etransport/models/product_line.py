"""
Model Pydantic pentru o linie de produs din expediere.
Conține toate câmpurile necesare de la extragere brută până la export final.
"""
from typing import Optional
from pydantic import BaseModel, Field


class ProductLine(BaseModel):
    """O linie de produs individuală, de la invoice până la export SmartBill."""

    # ── Identificare sursă ──
    source_type: str = Field(
        "invoice", description="Sursa de date: invoice, packing, derived"
    )
    source_line_no: Optional[int] = Field(
        None, description="Nr. linie din invoice (1-indexed)"
    )
    matched_packing_line_no: Optional[int] = Field(
        None, description="Nr. linie din packing list (1-indexed)"
    )

    # ── Denumire produs ──
    product_name_raw: str = Field(
        "", description="Denumire originală din invoice"
    )
    product_name_normalized: str = Field(
        "", description="Denumire normalizată (lowercase, fără diacritice)"
    )
    product_name_export: str = Field(
        "", description="Denumire finală pentru export SmartBill"
    )
    product_code: Optional[str] = Field(
        None, description="Cod produs intern (dacă există)"
    )

    # ── Cod HS / NC ──
    hs_code_raw: str = Field(
        "", description="Cod HS/TARIC original (poate fi 10 cifre)"
    )
    nc_code_8: str = Field(
        "", description="Cod NC derivat pe 8 cifre pentru e-Transport"
    )

    # ── Cantitate și unitate de măsură ──
    quantity: float = Field(0.0, description="Cantitate")
    display_unit: str = Field(
        "buc", description="Unitate de măsură afișată (buc, kg, cutie)"
    )
    standard_unit_code: str = Field(
        "H87", description="Cod standard ANAF pentru U.M. (H87, KGM, etc.)"
    )

    # ── Valori în moneda originală ──
    currency: str = Field("USD", description="Moneda originală din invoice")
    unit_price_original: float = Field(
        0.0, description="Preț unitar în moneda originală"
    )
    line_value_original: float = Field(
        0.0, description="Valoare totală linie în moneda originală"
    )

    # ── Valori convertite în RON ──
    unit_price_ron: float = Field(
        0.0, description="Preț unitar în RON fără TVA"
    )
    line_value_ron_without_vat: float = Field(
        0.0, description="Valoare totală linie în RON fără TVA"
    )

    # ── Greutăți ──
    net_weight_kg: float = Field(
        0.0, description="Greutate netă în kg"
    )
    gross_weight_kg: float = Field(
        0.0, description="Greutate brută în kg"
    )

    # ── Scop operațiune ──
    operation_purpose_code: str = Field(
        "9999", description="Cod scop operațiune"
    )
    operation_purpose_label: str = Field(
        "Același cu operațiunea", description="Descriere scop operațiune"
    )

    # ── Matching info ──
    match_confidence: Optional[float] = Field(
        None, description="Scor de încredere al matching-ului (0-100)"
    )
    match_method: Optional[str] = Field(
        None, description="Metoda folosită la matching (index, text, fuzzy)"
    )

    # ── Warning-uri ──
    warnings: list[str] = Field(
        default_factory=list,
        description="Warning-uri specifice acestei linii"
    )

    # ── Linii originale agregate (pentru trasabilitate) ──
    aggregated_from_lines: list[int] = Field(
        default_factory=list,
        description="Nr. linii originale agregate în aceasta"
    )

    # ── Parametri specifici exportului SmartBill (calculate la export/validare) ──
    export_weight_net_unit: float = Field(0.0, description="Greutate neta calculata pe unitate (q / cant)")
    export_weight_gross_unit: float = Field(0.0, description="Greutate bruta calculata pe unitate (q / cant)")
    export_unit_price_written: float = Field(0.0, description="Preț exact trimis in XLS")
    export_currency_written: str = Field("", description="Moneda exactă trimisă in XLS")
    export_nc_code_written: str = Field("", description="Codul NC8 final aplicat in XLS (inclusiv override-uri)")
    nc_code_override_applied: bool = Field(False, description="Daca s-a aplicat un override de cod NC general sau db")
    nc_code_status: str = Field("", description="Statusul de validare a codului NC (valid, problematic, override)")

    # ── Database Tariff Code Match (history-first) ──
    tariff_code_db_match: Optional[str] = Field(None, description="Cod tarifar gasit din istoric/DB local")
    tariff_code_match_label: Optional[str] = Field(None, description="Descrierea codului tarifar DB")
    tariff_code_match_method: Optional[str] = Field(None, description="Metoda maparii (confirmed_memory, historical_name, smartbill_db, fallback_nc8, ...)")
    tariff_code_match_confidence: Optional[float] = Field(0.0, description="Increderea maparii")
    tariff_code_match_source: Optional[str] = Field(None, description="Sursa: historical, confirmed_memory, smartbill_db, fallback_nc8, needs_review")
    tariff_code_candidates: list[dict] = Field(default_factory=list, description="Candidati TARIC/NC8 evaluati")
    tariff_needs_review_reason: Optional[str] = Field(None, description="Motivul pentru care linia necesita review")
    historical_match_found: bool = Field(False, description="A fost gasit un match istoric relevant")
    historical_source_document: Optional[str] = Field(None, description="Sursa istorica folosita")
    used_same_shipment_propagation: bool = Field(False, description="A fost aplicata propagare in acelasi shipment")
    hs6_code: Optional[str] = Field(None, description="Primele 6 cifre din codul HS brut")
    hs8_candidate_valid: Optional[bool] = Field(None, description="hs8 derivat valid in nomenclator")

    same_shipment_source_is_strong: Optional[bool] = Field(None, description="Sursa a fost strong (nu bypassata)")
    same_shipment_source_method: Optional[str] = Field(None, description="Metoda finala a sursei")
    same_shipment_source_confidence: Optional[float] = Field(None, description="Increderea sursei")
