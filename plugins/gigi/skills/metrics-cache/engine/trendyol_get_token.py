#!/usr/bin/env python3
"""
Trendyol Seller Panel - Token Extractor
Deschide un browser vizibil, tu faci login manual (rezolvi CAPTCHA),
apoi scriptul captează automat tokenul pentru Q&A API.
"""
from playwright.sync_api import sync_playwright
import json, time, os

TOKEN_FILE = "/tmp/trendyol_tokens.json"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Browser VIZIBIL
        context = browser.new_context()
        page = context.new_page()
        
        tokens = {}
        
        # Captează toate răspunsurile API relevante
        def handle_response(response):
            url = response.url
            if "apigw.trendyol.com" in url and response.status == 200:
                if any(x in url for x in ["login-without-otp", "login/v2", "auth"]):
                    try:
                        body = response.json()
                        if "token" in body:
                            tokens["auth"] = body["token"]
                            print(f"  ✅ Auth token capturat! ({len(body['token'])} chars)")
                        if "refreshToken" in body:
                            tokens["refresh"] = body["refreshToken"]
                            print(f"  ✅ Refresh token capturat!")
                    except:
                        pass
        
        page.on("response", handle_response)
        
        print("=" * 60)
        print("🔐 Trendyol Seller Panel - Login")
        print("=" * 60)
        print()
        print("1. Browserul se deschide automat")
        print("2. Logheaza-te manual (rezolvă CAPTCHA)")
        print("3. După login, scriptul captează automat tokenul")
        print()
        
        page.goto("https://partner.trendyol.com/account/login", wait_until="networkidle", timeout=30000)
        
        # Așteaptă ca userul să facă login (max 5 minute)
        print("⏳ Aștept să faci login... (max 5 min)")
        for i in range(60):  # 60 x 5s = 5 min
            time.sleep(5)
            
            # Verifică dacă am capturat tokenuri din network
            if tokens:
                break
            
            # Verifică localStorage
            try:
                auth = page.evaluate("() => localStorage.getItem('auth_token')")
                partner = page.evaluate("() => localStorage.getItem('partner_auth_token')")
                refresh = page.evaluate("() => localStorage.getItem('refresh_token')")
                
                if auth:
                    tokens["auth"] = auth
                if partner:
                    tokens["partner"] = partner
                if refresh:
                    tokens["refresh"] = refresh
                
                if tokens:
                    print(f"  ✅ Tokenuri găsite în localStorage!")
                    break
            except:
                pass
            
            # Verifică URL-ul - dacă nu mai e pe login, probabil a reușit
            if "/auth/login" not in page.url and "/account/login" not in page.url:
                time.sleep(3)  # Așteaptă puțin să se seteze tokenurile
                try:
                    auth = page.evaluate("() => localStorage.getItem('auth_token')")
                    partner = page.evaluate("() => localStorage.getItem('partner_auth_token')")
                    refresh = page.evaluate("() => localStorage.getItem('refresh_token')")
                    if auth: tokens["auth"] = auth
                    if partner: tokens["partner"] = partner
                    if refresh: tokens["refresh"] = refresh
                except:
                    pass
                
                # Și cookie-urile
                cookies = context.cookies()
                for c in cookies:
                    if "auth" in c["name"].lower():
                        tokens[f"cookie_{c['name']}"] = c["value"]
                break
        
        if tokens:
            print()
            print("=" * 60)
            print("🎉 LOGIN REUȘIT!")
            print("=" * 60)
            for k, v in tokens.items():
                print(f"  {k}: {v[:30]}... ({len(v)} chars)")
            
            with open(TOKEN_FILE, "w") as f:
                json.dump(tokens, f, indent=2)
            print(f"\n💾 Tokenuri salvate în {TOKEN_FILE}")
            
            # Test Q&A endpoint
            print("\n🔍 Testez endpoint-ul Q&A...")
            import requests
            auth_token = tokens.get("auth") or tokens.get("partner")
            if auth_token:
                r = requests.get(
                    "https://apigw.trendyol.com/partner/social-ugc-partnerquestionanswergw-service/questions/products",
                    params={"page": 0, "size": 50, "culture": "ro"},
                    headers={
                        "Authorization": f"Bearer {auth_token}",
                        "User-Agent": "Mozilla/5.0",
                    },
                    timeout=15,
                )
                print(f"  Q&A Status: {r.status_code}")
                if r.status_code == 200:
                    d = r.json()
                    print(f"  ✅ SUCCES! Total întrebări: {d.get('totalElements', '?')}")
                    print(f"  Items pe pagină: {len(d.get('content', []))}")
                else:
                    print(f"  Body: {r.text[:200]}")
        else:
            print("\n❌ Timeout - nu s-a detectat loginul. Încearcă din nou.")
        
        input("\nApasă Enter pentru a închide browserul...")
        browser.close()

if __name__ == "__main__":
    main()
