"""
Model Pydantic pentru expediere (Shipment).
Conține toate datele la nivel de expediere plus lista de produse.
"""
from typing import Optional
from datetime import date
from pydantic import BaseModel, Field

from .product_line import ProductLine


class StartCustomsOffice(BaseModel):
    code: str
    label: str
    full_display: str
    aux_code: Optional[str] = None


class StartPTF(BaseModel):
    code: str
    label: str
    full_display: str
    country_hint: Optional[str] = None


class DestinationInfo(BaseModel):
    final_type: str = Field("Pe teritoriul national", description="Tipul destinației")
    country: str = Field("RO", description="Țara destinație")
    county: str = Field("", description="Județ destinație")
    city: str = Field("", description="Oraș destinație")
    street: str = Field("", description="Stradă destinație")
    number: str = Field("", description="Număr adresă destinație")
    postal_code: str = Field("", description="Cod poștal destinație")
    block: str = Field("", description="Bloc (opțional)")
    staircase: str = Field("", description="Scară (opțional)")
    floor: str = Field("", description="Etaj (opțional)")
    apartment: str = Field("", description="Apartament (opțional)")
    other_info: str = Field("", description="Alte informații (opțional)")


class Shipment(BaseModel):
    """Model complet pentru o expediere e-Transport."""

    # ── Tip operațiune ──
    operation_type: str = Field(
        "import", description="Tip operațiune: import / achizitie_intracomunitara"
    )
    operation_type_code: int = Field(
        40, description="Cod operațiune ANAF (40=Import, 10=AIC)"
    )

    # ── Date transport ──
    transport_date: Optional[date] = Field(
        None, description="Data transportului"
    )

    # ── Furnizor ──
    supplier_name: str = Field(
        "", description="Denumire furnizor/partener comercial"
    )
    supplier_tax_code: Optional[str] = Field(
        None, description="Cod fiscal furnizor (poate lipsi la import non-UE)"
    )
    supplier_country: str = Field(
        "", description="Țara furnizorului (ISO 2 litere)"
    )

    # ── Transportator ──
    carrier_name: Optional[str] = Field(
        None, description="Denumire transportator"
    )
    carrier_vat: Optional[str] = Field(
        None, description="CUI/VAT transportator"
    )

    # ── Document comercial (invoice) ──
    invoice_number: Optional[str] = Field(
        None, description="Număr factură"
    )
    invoice_date: Optional[date] = Field(
        None, description="Data facturii"
    )

    # ── Document de transport ──
    document_type: Optional[str] = Field(
        None, description="Tip document transport (ex: Commercial Invoice, CMR)"
    )
    document_number: Optional[str] = Field(
        None, description="Număr document transport"
    )
    document_date: Optional[date] = Field(
        None, description="Data document transport"
    )

    # ── Vehicul / Container ──
    container_no: Optional[str] = Field(
        None, description="Număr container"
    )
    vehicle_no: Optional[str] = Field(
        None, description="Număr înmatriculare vehicul"
    )
    trailer_no: Optional[str] = Field(
        None, description="Număr înmatriculare remorcă"
    )
    driver_name: Optional[str] = Field(
        None, description="Nume șofer"
    )
    driver_phone: Optional[str] = Field(
        None, description="Telefon șofer"
    )

    # ── Locație start ──
    start_customs_office_code: Optional[str] = Field(
        None, description="[DEPRECATED] Cod birou vamal raw"
    )
    start_ptf_code: Optional[str] = Field(
        None, description="[DEPRECATED] Cod PTF raw"
    )
    start_customs_office: Optional[StartCustomsOffice] = Field(
        None, description="Obiect complet birou vamal (pentru import)"
    )
    start_ptf: Optional[StartPTF] = Field(
        None, description="Obiect complet PTF (pentru achiziție intracomunitară)"
    )

    # ── Destinație ──
    destination: DestinationInfo = Field(
        default_factory=DestinationInfo, description="Loc final traseu"
    )

    # ── Valută ──
    currency: str = Field(
        "USD", description="Moneda originală din invoice"
    )
    exchange_rate_to_ron: float = Field(
        1.0, description="Cursul valutar folosit pentru conversie în RON"
    )

    # ── Produse ──
    products: list[ProductLine] = Field(
        default_factory=list,
        description="Lista de linii de produs"
    )

    # ── Warning-uri la nivel de expediere ──
    warnings: list[str] = Field(
        default_factory=list,
        description="Warning-uri la nivel de expediere"
    )

    # ── Debug Info ──
    debug_info: dict = Field(
        default_factory=dict,
        description="Statistici si loguri (linii raw, dupes) pentru audit QA"
    )
