"""
Serviciu de validare pre-export.
Verifică completitudinea și corectitudinea datelor înainte de generarea XLSX.
"""
from typing import Tuple
from etransport.models.shipment import Shipment


class ValidationReport:
    """Raport de validare cu erori și warning-uri."""
    
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.is_valid: bool = True
        self.is_draft: bool = False
    
    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)
        self.is_draft = True
    
    def add_info(self, msg: str):
        self.info.append(msg)
    
    def summary(self) -> str:
        """Generează un rezumat text al validării."""
        lines = []
        lines.append("=" * 60)
        lines.append("RAPORT DE VALIDARE e-Transport")
        lines.append("=" * 60)
        
        if self.is_valid and not self.is_draft:
            lines.append("✅ VALID — Gata pentru export SmartBill")
        elif self.is_valid and self.is_draft:
            lines.append("⚠️ DRAFT — Exportabil cu warning-uri")
        else:
            lines.append("❌ INVALID — Erori critice")
        
        if self.errors:
            lines.append(f"\n🔴 ERORI ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  • {e}")
        
        if self.warnings:
            lines.append(f"\n🟡 WARNING-URI ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  • {w}")
        
        if self.info:
            lines.append(f"\n🔵 INFO ({len(self.info)}):")
            for i in self.info:
                lines.append(f"  • {i}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def validate_shipment(shipment: Shipment) -> ValidationReport:
    """
    Validează un shipment complet înainte de export.
    
    Verificări:
    - Fiecare linie are câmpurile obligatorii
    - Greutăți coerente (brut >= net)
    - Date de transport complete
    - Curs valutar valid
    
    Returns:
        ValidationReport cu erori/warning-uri
    """
    report = ValidationReport()
    
    # ── Validare la nivel de expediere ──
    if not shipment.products:
        report.add_error("Nu există linii de produs")
        return report
    
    report.add_info(f"Nr. linii de produs: {len(shipment.products)}")
    report.add_info(f"Tip operațiune: {shipment.operation_type} "
                    f"(cod {shipment.operation_type_code})")
    
    # Transport date
    if not shipment.transport_date:
        report.add_warning("Data transportului lipsă")
    
    # Carrier
    if not shipment.carrier_name:
        report.add_warning("Transportator lipsă")
    
    # Vehicle
    if not shipment.vehicle_no and not shipment.container_no:
        report.add_warning("Vehicul/container lipsă")
    
    # Locație start
    if shipment.operation_type_code == 40:  # Import
        if not shipment.start_customs_office_code:
            report.add_warning("Birou vamal lipsă (obligatoriu pentru import)")
    elif shipment.operation_type_code == 10:  # AIC
        if not shipment.start_ptf_code:
            report.add_warning("Cod PTF lipsă (obligatoriu pentru AIC)")
    
    # Destinație
    if not shipment.destination.city:
        report.add_warning("Oraș destinație lipsă")
    
    # Curs valutar
    if shipment.currency != "RON" and shipment.exchange_rate_to_ron <= 0:
        report.add_warning("Curs valutar invalid sau lipsă")
    
    # ── Validare la nivel de linie ──
    total_net = 0.0
    total_gross = 0.0
    total_value_ron = 0.0
    
    for i, product in enumerate(shipment.products, start=1):
        prefix = f"Linia {i}"
        
        # Câmpuri obligatorii
        if not product.product_name_export:
            report.add_error(f"{prefix}: Denumire produs lipsă")
        
        if not product.nc_code_8:
            report.add_error(f"{prefix}: Cod NC8 lipsă")
        else:
            from etransport import config
            c = product.nc_code_8
            if c in config.NC_CODE_OVERRIDES:
                c = config.NC_CODE_OVERRIDES[c]
                report.add_info(f"{prefix}: Cod NC8 suprascris ({product.nc_code_8} -> {c})")
            
            if len(c) != 8 or not c.isdigit():
                report.add_error(
                    f"{prefix}: Cod NC8 invalid, imposibil de acceptat de SmartBill. "
                    f"Așteptat 8 cifre numerice: '{product.nc_code_8}'. "
                    f"Setează in config.NC_CODE_OVERRIDES corectura manuală!"
                )
        
        if product.quantity <= 0:
            report.add_error(f"{prefix}: Cantitate invalidă ({product.quantity})")
        
        if not product.display_unit:
            report.add_warning(f"{prefix}: U.M. lipsă")
        
        if not product.standard_unit_code:
            report.add_warning(f"{prefix}: Cod standard U.M. lipsă")
        
        # Greutăți
        if product.net_weight_kg <= 0:
            report.add_warning(f"{prefix}: Greutate netă lipsă sau zero")
        if product.gross_weight_kg <= 0:
            report.add_warning(f"{prefix}: Greutate brută lipsă sau zero")
        
        if (product.net_weight_kg > 0 and product.gross_weight_kg > 0 and
                product.gross_weight_kg < product.net_weight_kg):
            report.add_error(
                f"{prefix}: Greutate brută ({product.gross_weight_kg} kg) "
                f"mai mică decât greutate netă ({product.net_weight_kg} kg)"
            )
        
        # Valori
        if product.unit_price_ron <= 0:
            report.add_warning(
                f"{prefix}: Preț unitar RON lipsă sau zero"
            )
        
        total_net += product.net_weight_kg
        total_gross += product.gross_weight_kg
        total_value_ron += product.line_value_ron_without_vat
    
    # Rezumat
    report.add_info(f"Greutate netă totală: {total_net:.3f} kg")
    report.add_info(f"Greutate brută totală: {total_gross:.3f} kg")
    report.add_info(f"Valoare totală RON (fără TVA): {total_value_ron:.2f}")
    
    return report
