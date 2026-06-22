"""
Model Pydantic pentru informații de transport și transportator.
Parsate din text liber introdus de utilizator.
"""
from typing import Optional
from pydantic import BaseModel, Field


class TransportInfo(BaseModel):
    """Date de transport parsate din text liber."""

    container_no: Optional[str] = Field(
        None, description="Număr container (ex: TIIU5478466)"
    )
    vehicle_no: Optional[str] = Field(
        None, description="Număr vehicul (ex: CT64ADT)"
    )
    trailer_no: Optional[str] = Field(
        None, description="Număr remorcă (ex: CT01AOT)"
    )
    driver_name: Optional[str] = Field(
        None, description="Nume șofer"
    )
    driver_phone: Optional[str] = Field(
        None, description="Telefon șofer (ex: 0761283435)"
    )


class CarrierInfo(BaseModel):
    """Date despre transportator parsate din text liber."""

    carrier_name: Optional[str] = Field(
        None, description="Denumire transportator (ex: ANDI TRANS SRL)"
    )
    carrier_vat: Optional[str] = Field(
        None, description="CUI/VAT transportator (ex: RO5607012)"
    )
