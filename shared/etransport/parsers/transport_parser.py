"""
Parser pentru date de transport și transportator din text liber.

Formate suportate:
  Transport: "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel"
  Transport: "AKKU4029009 CT57ADT/CT19ADT 0728870264 Cumpănașu Gheorghe"
  Carrier:   "ANDI TRANS SRL RO5607012"
"""
import re
from typing import Optional

from etransport.models.transport_info import TransportInfo, CarrierInfo


# ── Pattern-uri regex ──

# Container: 4 litere + 7 cifre (format ISO 6346)
CONTAINER_PATTERN = re.compile(r'\b([A-Z]{4}\d{7})\b')

# Număr de înmatriculare RO: 2 litere județ + 2-3 cifre + 3 litere
# sau format european similar
PLATE_PATTERN = re.compile(
    r'\b([A-Z]{1,2}\d{2,3}[A-Z]{2,3})\b'
)

# Telefon RO: 07xx xxx xxx (10 cifre)
PHONE_PATTERN = re.compile(r'\b(0\d{9})\b')

# VAT/CUI: RO urmat de cifre
VAT_PATTERN = re.compile(r'\b(RO\d{2,10})\b', re.IGNORECASE)

# CUI fără prefix RO
CUI_PATTERN = re.compile(r'\b(\d{6,10})\b')


def parse_transport_text(text: str) -> TransportInfo:
    """
    Parsează text liber cu date de transport.
    
    Formate acceptate:
    - "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel"
    - "AKKU4029009 CT57ADT CT19ADT 0728870264 Cumpănașu Gheorghe"
    - "CT64ADT/CT01AOT 0761283435 Nichei Pavel" (fără container)
    
    Args:
        text: Text liber cu date de transport
        
    Returns:
        TransportInfo cu câmpurile populate
    """
    if not text or not text.strip():
        return TransportInfo()
    
    text = text.strip()
    remaining = text
    
    # 1. Extrage container
    container = None
    container_match = CONTAINER_PATTERN.search(text)
    if container_match:
        container = container_match.group(1)
        remaining = remaining.replace(container, '', 1).strip()
    
    # 2. Extrage numere de înmatriculare (vehicul / remorcă)
    # Suportă format "CT64ADT/CT01AOT" sau "CT64ADT CT01AOT"
    vehicle = None
    trailer = None
    
    # Încearcă formatul cu /
    slash_plate_match = re.search(
        r'([A-Z]{1,2}\d{2,3}[A-Z]{2,3})\s*/\s*([A-Z]{1,2}\d{2,3}[A-Z]{2,3})',
        remaining,
    )
    if slash_plate_match:
        vehicle = slash_plate_match.group(1)
        trailer = slash_plate_match.group(2)
        remaining = remaining.replace(slash_plate_match.group(0), '', 1).strip()
    else:
        # Caută plăcuțe individuale
        plates = PLATE_PATTERN.findall(remaining)
        if len(plates) >= 2:
            vehicle = plates[0]
            trailer = plates[1]
            remaining = remaining.replace(vehicle, '', 1)
            remaining = remaining.replace(trailer, '', 1)
            remaining = remaining.strip()
        elif len(plates) == 1:
            vehicle = plates[0]
            remaining = remaining.replace(vehicle, '', 1).strip()
    
    # 3. Extrage telefon
    phone = None
    phone_match = PHONE_PATTERN.search(remaining)
    if phone_match:
        phone = phone_match.group(1)
        remaining = remaining.replace(phone, '', 1).strip()
    
    # 4. Ce rămâne este numele șoferului
    driver_name = _clean_driver_name(remaining)
    
    return TransportInfo(
        container_no=container,
        vehicle_no=vehicle,
        trailer_no=trailer,
        driver_name=driver_name or None,
        driver_phone=phone,
    )


def parse_carrier_text(text: str) -> CarrierInfo:
    """
    Parsează text liber cu date despre transportator.
    
    Formate acceptate:
    - "ANDI TRANS SRL RO5607012"
    - "TRANSPORT RAPID SRL 12345678"
    
    Args:
        text: Text liber cu date transportator
        
    Returns:
        CarrierInfo cu câmpurile populate
    """
    if not text or not text.strip():
        return CarrierInfo()
    
    text = text.strip()
    remaining = text
    
    # 1. Extrage VAT/CUI
    carrier_vat = None
    vat_match = VAT_PATTERN.search(text)
    if vat_match:
        carrier_vat = vat_match.group(1).upper()
        remaining = remaining.replace(vat_match.group(0), '', 1).strip()
    else:
        # Caută CUI numeric la final
        cui_match = re.search(r'\b(\d{6,10})\s*$', remaining)
        if cui_match:
            carrier_vat = cui_match.group(1)
            remaining = remaining[:cui_match.start()].strip()
    
    # 2. Ce rămâne este denumirea transportatorului
    carrier_name = remaining.strip()
    
    # Curățăm caractere reziduale
    carrier_name = re.sub(r'\s+', ' ', carrier_name).strip()
    carrier_name = carrier_name.rstrip('/')
    
    return CarrierInfo(
        carrier_name=carrier_name or None,
        carrier_vat=carrier_vat,
    )


def _clean_driver_name(text: str) -> str:
    """Curăță și normalizează numele șoferului."""
    if not text:
        return ""
    # Elimină separatori reziduali
    text = re.sub(r'[/,;]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Elimină cifre rămase (care nu fac parte din nume)
    text = re.sub(r'\b\d+\b', '', text).strip()
    text = re.sub(r'\s+', ' ', text).strip()
    return text
