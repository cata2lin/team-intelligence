---
name: nvr-camera-recovery
description: Repară și recuperează un sistem de supraveghere NVR + camere IP (Hikvision / Safer-OEM / ONVIF / LAPI) de la linia de comandă. Folosește când camerele apar pe NVR dar sunt offline / `errorUserNameOrPasswd`, când o cameră „lipsește" / nu apare la scanare, când NVR-ul a fost mutat pe alt subnet, sau când trebuie găsită parola reală a unei camere. Include tool-ul `scripts/nvr.py` (status canale, găsire parolă prin ONVIF+LAPI, reparare canal, adăugare cameră, discovery ARP/ONVIF/SADP, și sniffing tcpdump pe OUI pentru camere ascunse pe alt subnet). Merge de pe Mac sau Linux, inclusiv când NVR-ul + camerele sunt pe un segment PoE izolat.
---

# NVR Camera Recovery — reparăm supravegherea de la CLI

Metodologie + tool (`scripts/nvr.py`, doar stdlib Python) pentru a diagnostica și repara un
DVR/NVR cu camere IP fără să deschizi interfața grafică. Testat pe **Hikvision
DS-7xxxNXI** + camere **Safer** (Hikvision-OEM, ONVIF + LAPI). Se aplică oricărui NVR
Hikvision-compatibil și oricăror camere ONVIF.

> Toate credențialele se dau ca argumente în linia de comandă — **NU pune parole de
> dispozitiv în cod, în skill sau în git.** Tool-ul nu le loghează.

## Simptome pe care le rezolvă
- Camere adăugate pe NVR dar **toate offline** cu `errorUserNameOrPasswd`.
- „Am 8 camere dar NVR-ul vede doar 7" — o cameră nu apare la scanare.
- NVR mutat pe alt IP/subnet și nu mai vezi camerele.
- Nu știi parola reală a camerelor (sau documentația are parola **greșită**).
- Cameră care pornește dar nu dă imagine / „Rețeaua este inaccesibilă".

---

## Pasul 0 — ajungi la rețeaua camerelor
De multe ori NVR-ul + camerele sunt pe un **segment PoE izolat** (ex. `172.16.0.x`), separat
de rețeaua ta (`192.168.x`). Laptopul tău e pe rețeaua principală → nu le prinzi până nu-ți
adaugi un **IP secundar** pe placa cablată în switch:

```
python3 scripts/nvr.py reach --iface en9 --ip 172.16.0.138          # îți afișează comanda (Mac/Linux)
sudo ifconfig en9 alias 172.16.0.138 255.255.0.0                    # macOS
# sudo ip addr add 172.16.0.138/16 dev eth0                         # Linux
```
Pe macOS, dacă vrei fereastră grafică pentru parolă:
`osascript -e 'do shell script "ifconfig en9 alias 172.16.0.138 255.255.0.0" with administrator privileges'`.
Verifică: `ping <ip_nvr>` sau `nvr.py probe --cam <ip_cam>`. **La final scoate aliasul**
(`sudo ifconfig en9 -alias 172.16.0.138`).

> Identifică placa cablată: `networksetup -listallhardwareports` (macOS) — caută portul
> „USB LAN / Ethernet" cu `status: active`, NU Wi-Fi. Switch-ul în mod **DEFAULT** (nu
> EXTENDED/VLAN) e transparent L2, deci ajungi la subnetul camerelor prin el.

## Pasul 1 — vezi starea NVR-ului
```
python3 scripts/nvr.py status   --nvr 172.16.0.112 --password '<NVR_PW>'
python3 scripts/nvr.py channels --nvr 172.16.0.112 --password '<NVR_PW>'
```
`status` listează fiecare canal: `online=true/false` + `chanDetectResult`
(`connect` = bine, `errorUserNameOrPasswd` = parolă greșită, `netUnreachable` = camera nu
răspunde/bootează, `unknownError` = de obicei lockout/reconectare).

## Pasul 2 — găsește parola REALĂ a camerei (nu te baza pe documentație)
```
python3 scripts/nvr.py probe    --cam 172.16.0.45                    # e vie? ce porturi/ONVIF?
python3 scripts/nvr.py findpass --cam 172.16.0.45 --passwords 12345 123456 1234
```
`findpass` testează prin **ONVIF** (GetDeviceInformation, WS-Security) **și LAPI** și se
oprește la prima corectă. Distinge cele 3 stări cruciale:
- **Succeed / GetDeviceInformationResponse** = parola bună.
- **Unauthorized (401 / NotAuthorized)** = parolă greșită.
- **`UserLocked` (StatusCode 364)** = parola POATE fi corectă dar **contul e blocat** din prea
  multe încercări. ⚠️ **OPREȘTE-TE** — power-cycle camera și reîncearcă peste câteva minute
  DOAR parola bună. Fiecare test greșit prelungește lockout-ul (~30 min).

> 🔴 **Lecția #1 (dovedită pe teren):** documentația precedentă zicea că parola e `1234` —
> era **greșită**. Reală: `xsQR6YYA!` pe majoritate, `123456` pe una singură. **Verifică
> mereu pe cameră**, nu presupune.

## Pasul 3 — repară canalul (cazul cel mai frecvent)
Dacă `errorUserNameOrPasswd`, camerele sunt bune, doar NVR-ul are parola greșită salvată:
```
python3 scripts/nvr.py setpass --nvr 172.16.0.112 --nvrpass '<NVR_PW>' --ch 4 --campass '123456'
```
Câmpul `<password>` din canal e **write-only** (nu apare la GET); tool-ul îl injectează în XML-ul
canalului și face PUT. După ~15s canalul trece `online`.

> 🔴 **Lecția #2:** NVR-ul cu protocol **HIKVISION cere parolă ≥8 caractere**. Dacă parola
> camerei e scurtă (ex. `123456`, `1234`), adaug-o cu **ONVIF** (acceptă parole scurte):
> `nvr.py addcam ... --protocol ONVIF`. ONVIF-ul e și mai tolerant la firmware OEM.

## Pasul 4 — camera „lipsă" / a N-a cameră
Dacă lipsește o cameră, mergi în ordine:
```
python3 scripts/nvr.py discover --subnet 172.16.0 --local-ip 172.16.0.138 --oui e4:f1:4c
```
`discover` = ARP sweep + ONVIF WS-Discovery + SADP (UDP 37020). Dacă tot n-o găsești, camera
are probabil un **IP pe alt subnet** (default de fabrică) și nu răspunde la scanări normale.
Atunci **asculți firul** — cel mai puternic instrument:
```
sudo python3 scripts/nvr.py sniff --iface en9 --oui e4:f1:4c --seconds 20
```
`sniff` prinde cu tcpdump **tot traficul cu OUI-ul brandului** (primele 3 octeți din MAC) și
scoate IP-ul fiecărei camere din ARP („who-has X **tell IP_CAMERĂ**"). Așa găsești o cameră
oriunde ar fi pe L2, chiar dacă are un IP „ciudat". Odată aflat IP-ul → `probe` → `findpass`
→ `addcam`.

> 🔴 **Lecția #3:** o cameră poate fi **vie pe L2 (apare în ARP) dar cu TOATE porturile
> închise** — e blocată în boot / boot-loop (face „router solicitation" + caută gateway-ul
> inexistent, dar web/ONVIF/RTSP nu urcă). Software nu ai ce să-i faci. Fix fizic:
> power-cycle curat → dacă tot moartă, **factory reset** (buton 15-20s cât e alimentată) →
> dacă nici așa, e **defectă** (RMA). Nu confunda un LED de link (aprins la nivel fizic) cu o
> cameră funcțională.

## Pasul 5 — adaugă o cameră nouă / mută rețeaua
```
python3 scripts/nvr.py addcam --nvr 172.16.0.112 --nvrpass '<NVR_PW>' \
    --cam 172.16.0.14 --campass '<CAM_PW>' --protocol ONVIF
```
Mutarea NVR/camere pe rețeaua principală (ca să vezi de pe telefon/birou) se face schimbând
IP-urile prin ISAPI (`/ISAPI/System/Network/interfaces/1` PUT pe NVR; IP-urile camerelor din
web-ul lor), apoi reactualizezi canalele. **Decizie de arhitectură:** dacă vezi camerele
doar pe monitorul HDMI al NVR-ului, lasă-le izolate pe `172.16.0.x` (mai sigur). Dacă vrei
acces din rețea/telefon, mută tot pe subnetul principal.

---

## Capcane care au stricat/încurcat lucruri (citește)
1. **Nu „hamera" o cameră cu parole** — `findpass` repetat o blochează (UserLocked ~30 min).
   Testează puțin, oprește la prima corectă; `probe`/`GetSystemDateAndTime` NU cer parolă și
   NU blochează — folosește-le pentru diagnostic.
2. **`ONVIF SetUser` deseori NU schimbă parola de sistem** pe camere OEM (creează doar un user
   ONVIF). Ca să uniformizezi parola, fă-o din **web-ul camerei** sau la un **factory reset +
   reactivare**, nu presupune că SetUser a mers.
3. **Camerele Safer/OEM nu vorbesc SADP clasic** — doar NVR-ul răspunde la SADP. Pentru camere
   folosește ONVIF WS-Discovery + ARP + `sniff` (tcpdump pe OUI).
4. **`ping` nu e de încredere** — multe camere filtrează ICMP dar au porturile deschise.
   Verifică cu port-scan/ONVIF (`probe`), nu doar cu ping.
5. **Switch în mod EXTENDED/VLAN** izolează porturile (și limitează la 10Mbps) → camerele par
   invizibile. Trebuie **DEFAULT/STANDARD**.
6. **Nu scoate/băga camera repetat** cât diagnostichezi — fiecare repornire bruscă resetează
   starea și, împreună cu testele de parolă, o ține blocată. Un singur power-cycle, apoi las-o
   ~2 min să booteze complet, netulburată.

## Endpoint-uri utile (Hikvision ISAPI, digest auth)
- `GET  /ISAPI/ContentMgmt/InputProxy/channels/status` — online + `chanDetectResult` per canal.
- `GET  /ISAPI/ContentMgmt/InputProxy/channels[/{id}]` — config canale.
- `PUT  /ISAPI/ContentMgmt/InputProxy/channels/{id}` — modifică (injectează `<password>`).
- `POST /ISAPI/ContentMgmt/InputProxy/channels` — adaugă canal.
- `GET/PUT /ISAPI/System/Network/interfaces/1` — IP-ul NVR-ului.
- Cameră ONVIF: `POST /onvif/device_service` (GetSystemDateAndTime = fără auth; GetDeviceInformation = WS-Security).
- Cameră LAPI (Safer/OEM nou): `GET /LAPI/V1.0/System/DeviceBasicInfo` (digest; JSON cu `StatusString`).
