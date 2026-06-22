import sys
import json
import argparse
import requests
from requests.auth import HTTPBasicAuth
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--cif", required=True)
    args = parser.parse_args()
    
    print("Testare conexiune SmartBill API...")
    url = "https://ws.smartbill.ro/SBORO/api/tax"
    # Testam apelul catre noi insine ca validare de conexiune
    clean_cif = str(args.cif).strip().upper().replace("RO", "").replace(" ", "")

    try:
        resp = requests.get(
            url,
            params={"cif": clean_cif, "vatcode": clean_cif},
            auth=HTTPBasicAuth(args.email, args.token),
            headers={"Accept": "application/json"},
            timeout=15
        )
        if resp.status_code == 200:
            print("✅ Conexiune reușită! Credențialele sunt perfect valide.")
            data = resp.json()
            if isinstance(data, dict):
                print(f"Identificat compania: {data.get('name') or data.get('companyName')} (RO{data.get('cif') or data.get('vatCode')})")
        elif resp.status_code == 401 or resp.status_code == 403:
            print(f"❌ Eroare HTTP {resp.status_code} Autentificare. Verifică email-ul și token-ul furnizat!")
            sys.exit(1)
        else:
            print(f"⚠️ Eroare API: {resp.status_code} - {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Excepție la conectare: {e}")
        sys.exit(1)
        
    # Salveaza cu succes
    config_dir = Path(__file__).parent.parent.parent / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "smartbill.json"
    
    data = {
        "email": args.email,
        "token": args.token,
        "cif": clean_cif
    }
    with open(config_file, "w") as f:
        json.dump(data, f, indent=2)
        
    print(f"\n✅ Configurare salvată cu succes. E-Transport va prelua automat setările la viitoarele rulări!")

if __name__ == "__main__":
    main()
