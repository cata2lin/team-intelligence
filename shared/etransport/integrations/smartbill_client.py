"""
Client pentru integrarea cu SmartBill REST API.
Responsabil pentru lookup-ul transportatorilor.
"""
import os
import re
import json
import requests
from requests.auth import HTTPBasicAuth
from pathlib import Path
from typing import Optional, Dict

def _load_config():
    cfg_path = Path(__file__).parent.parent.parent / "config" / "smartbill.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except Exception:
            pass
    return {
        "email": os.environ.get("SMARTBILL_EMAIL"),
        "token": os.environ.get("SMARTBILL_TOKEN"),
        "cif": os.environ.get("SMARTBILL_CIF")
    }

BASE_URL = "https://ws.smartbill.ro/SBORO/api"

def find_carrier_by_vat(vat_code: str) -> Optional[Dict]:
    """
    Caută compania după CIF folosind endpoint-ul SmartBill taxit.
    Acest endpoint (documentat oficial pentru decodarea CIF-urilor) consultă datele asociate CIF-ului furnizat.
    """
    conf = _load_config()
    sb_email = conf.get("email")
    sb_token = conf.get("token")
    sb_cif = conf.get("cif")
    
    if not sb_email or not sb_token or not sb_cif:
        return None
    if not vat_code:
        return None
        
    # Standardize VAT format for payload
    clean_vat = str(vat_code).strip().upper().replace("RO", "").replace(" ", "")
    
    url = f"{BASE_URL}/tax"
    params = {
        "cif": sb_cif,
        "vatcode": clean_vat
    }
    
    try:
        resp = requests.get(
            url, 
            params=params,
            auth=HTTPBasicAuth(sb_email, sb_token),
            headers={"Accept": "application/json"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, dict):
                name = data.get("name") or data.get("companyName")
                cif = data.get("cif") or data.get("vatCode") or clean_vat
                if name:
                    return {
                        "carrier_name": name,
                        "carrier_vat": f"RO{cif}" if str(cif).isdigit() else cif
                    }
    except Exception:
        pass
    return None

def find_carrier_by_name(name: str) -> Optional[Dict]:
    """
    Încearcă extragerea entității. SmartBill nu deține un route simplu și public
    de /search?name= pentru parteneri în iteratiile clasice fara GET /client ce necesită
    paginare masivă. Se va detecta in schimb dacă in denumire exista un CUI ascuns.
    """
    if not name:
        return None
    vat_match = re.search(r'\b(?:RO)?(\d{6,10})\b', name, re.IGNORECASE)
    if vat_match:
        return find_carrier_by_vat(vat_match.group(1))
    return None

def search_carriers(query: str) -> list:
    """Flux combinat folosit de aplicația principală."""
    if not query:
        return []
    
    # 1. Prioritate VAT curat
    vat_match = re.search(r'\b(?:RO)?(\d{6,10})\b', query, re.IGNORECASE)
    if vat_match:
        res = find_carrier_by_vat(vat_match.group(1))
        if res:
            return [res]
            
    # 2. Fallback căutare după nume
    res_name = find_carrier_by_name(query)
    if res_name:
        return [res_name]
        
    return []
