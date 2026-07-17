#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nvr.py — Toolkit de recuperare NVR + camere IP (Hikvision / Safer-OEM / ONVIF).

Rezolvă cazul clasic: camere adăugate pe NVR dar toate `errorUserNameOrPasswd` /
offline; camere „lipsă" care nu apar la scanare; NVR mutat pe alt subnet.
Merge de pe Mac SAU Linux, chiar dacă NVR-ul + camerele sunt pe un segment
izolat (switch PoE, ex. 172.16.0.x) — adaugi un IP secundar și le prinzi.

Doar stdlib (urllib, socket, hashlib, base64, struct, subprocess). Zero dependințe.
Rulează: `python3 nvr.py <subcomanda> --help`

Subcomenzi:
  reach      comanda de adăugat un IP secundar ca să ajungi la segmentul camerelor
  probe      ce servicii/porturi are o cameră (fără parolă) — e vie? bootează? e stricată?
  status     canalele NVR: care-s online / ce eroare dau
  channels   lista canalelor NVR (ip / protocol / user)
  findpass   testează parole pe o cameră prin ONVIF + LAPI (detectează UserLocked!)
  setpass    setează parola camerei pe un CANAL EXISTENT (repară errorUserNameOrPasswd)
  addcam     adaugă o cameră NOUĂ ca un canal pe NVR (prin ONVIF)
  discover   găsește camere pe subnet: ARP sweep + ONVIF WS-Discovery + SADP
  sniff      găsește camere ASCUNSE „pe fir": tcpdump pe OUI-ul brandului -> IP din ARP (sudo)

REGULI DE AUR (învățate pe teren):
  * Parolele din documentație pot fi GREȘITE. Verifică pe cameră cu `findpass`.
  * NVR-ul cu protocol HIKVISION cere parolă >=8 caractere. Parolă scurtă -> folosește ONVIF.
  * `findpass` repetat BLOCHEAZĂ camera ~30 min (UserLocked). Testează puțin, oprește la prima OK.
    Lockout-ul se ridică singur în ~30 min sau la power-cycle. NU „hamera" camera.
  * Cameră vie pe L2 (apare în ARP) dar cu TOATE porturile închise = blocată în boot / defectă
    -> power-cycle curat, apoi factory reset (buton 15-20s), apoi RMA.
  * `GetSystemDateAndTime` (ONVIF) și `probe` NU cer parolă -> nu blochează camera. Folosește-le des.
"""
import sys, os, socket, struct, time, re, uuid, hashlib, base64, subprocess, argparse
import urllib.request, urllib.error

ONVIF_PATH = "/onvif/device_service"
NS = 'xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
TDS = 'xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
TT = 'xmlns:tt="http://www.onvif.org/ver10/schema"'


# --------------------------- HTTP digest (NVR ISAPI + LAPI) ---------------------------

def _opener(host, user, pw):
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, f"http://{host}/", user, pw)
    return urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))


def http_get(host, path, user, pw, timeout=15):
    op = _opener(host, user, pw)
    return op.open(f"http://{host}{path}", timeout=timeout).read().decode("utf-8", "replace")


def http_put(host, path, body, user, pw, timeout=20):
    op = _opener(host, user, pw)
    req = urllib.request.Request(f"http://{host}{path}", data=body.encode(),
                                 method="PUT", headers={"Content-Type": "application/xml"})
    return op.open(req, timeout=timeout).read().decode("utf-8", "replace")


def http_post_xml(host, path, body, user, pw, timeout=20):
    op = _opener(host, user, pw)
    req = urllib.request.Request(f"http://{host}{path}", data=body.encode(),
                                 method="POST", headers={"Content-Type": "application/xml"})
    return op.open(req, timeout=timeout).read().decode("utf-8", "replace")


# --------------------------- ONVIF WS-Security ---------------------------

def _wss(user, pw):
    nonce = os.urandom(16)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + pw.encode()).digest()).decode()
    n64 = base64.b64encode(nonce).decode()
    return (
        '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken>'
        f"<Username>{user}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">{n64}</Nonce>'
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>'
        "</UsernameToken></Security>"
    )


def onvif(ip, inner_body, user=None, pw=None, timeout=10):
    """Trimite un apel ONVIF. Dacă user+pw date -> cu WS-Security (autentificat)."""
    header = f"<s:Header>{_wss(user, pw)}</s:Header>" if (user and pw is not None) else ""
    env = (f'<?xml version="1.0"?><s:Envelope {NS}>{header}'
           f"<s:Body>{inner_body}</s:Body></s:Envelope>")
    req = urllib.request.Request(f"http://{ip}{ONVIF_PATH}", data=env.encode(),
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        return 200, urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def onvif_device_info(ip, user, pw):
    code, txt = onvif(ip, f"<tds:GetDeviceInformation {TDS}/>", user, pw)
    if "GetDeviceInformationResponse" in txt:
        f = dict(re.findall(r"<tds:(Manufacturer|Model|FirmwareVersion|SerialNumber)>([^<]*)</tds:", txt))
        return True, f
    reason = "NotAuthorized" if ("NotAuthorized" in txt or code in (400, 401)) else txt[:120]
    return False, reason


# --------------------------- utilitare ---------------------------

def is_mac_os():
    return sys.platform == "darwin"


def port_open(ip, port, timeout=2):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((ip, port)); s.close(); return True
    except Exception:
        return False


def arp_lookup(ip):
    try:
        out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"([0-9a-fA-F]{1,2}(:[0-9a-fA-F]{1,2}){5})", out)
        return m.group(1) if m else None
    except Exception:
        return None


# =========================== SUBCOMENZI ===========================

def cmd_reach(a):
    ip, mask = a.ip, a.mask
    print(f"# Ca să ajungi la segmentul camerelor ({ip}) de pe {a.iface}:")
    if is_mac_os():
        print(f"sudo ifconfig {a.iface} alias {ip} {mask}")
        print(f"# (scoatere ulterioară:)  sudo ifconfig {a.iface} -alias {ip}")
        print(f"# GUI (îți cere parola într-o fereastră):")
        print(f"""osascript -e 'do shell script "ifconfig {a.iface} alias {ip} {mask}" with administrator privileges'""")
    else:
        pref = a.prefix or "16"
        print(f"sudo ip addr add {ip}/{pref} dev {a.iface}")
        print(f"# (scoatere:)  sudo ip addr del {ip}/{pref} dev {a.iface}")
    print("\n# Apoi verifică:  ping <ip_nvr>   sau   python3 nvr.py probe --cam <ip_cam>")


def cmd_probe(a):
    ip = a.cam
    print(f"=== probe {ip} ===")
    mac = arp_lookup(ip)
    ports = {}
    for p in [80, 554, 8000, 8080, 8899, 2020, 443, 37777]:
        ports[p] = port_open(ip, p, a.timeout)
    openp = [p for p, v in ports.items() if v]
    # ONVIF viu? (fără parolă)
    code, txt = onvif(ip, f"<tds:GetSystemDateAndTime {TDS}/>", timeout=a.timeout + 3)
    onvif_up = "Year" in txt
    print(f"  ARP/L2 : {'viu (' + mac + ')' if mac else 'nu apare în ARP'}")
    print(f"  porturi deschise: {openp or '(niciunul)'}")
    print(f"  ONVIF  : {'DA (servicii sus)' if onvif_up else 'nu răspunde'}")
    if mac and not openp and not onvif_up:
        print("  >>> VIE pe L2 dar FĂRĂ servicii = blocată în boot / defectă -> power-cycle, apoi factory reset.")
    elif onvif_up or openp:
        print("  >>> servicii SUS -> rulează:  python3 nvr.py findpass --cam " + ip)
    else:
        print("  >>> nu răspunde deloc -> verifică alimentarea/cablul/IP-ul (poate alt subnet: nvr.py sniff).")


_STATUS_RE = re.compile(
    r"<id>(\d+)</id>.*?<ipAddress>([^<]*)</ipAddress>.*?<online>(\w+)</online>"
    r".*?<chanDetectResult>(\w+)</chanDetectResult>", re.S)


def cmd_status(a):
    txt = http_get(a.nvr, "/ISAPI/ContentMgmt/InputProxy/channels/status", a.user, a.password)
    onl = tot = 0
    for cid, ip, on, det in _STATUS_RE.findall(txt):
        tot += 1; onl += on == "true"
        flag = "OK" if on == "true" else "XX"
        print(f"  [{flag}] ch{cid:<2} {ip:<16} online={on:<5} detect={det}")
    print(f"  >>> {onl}/{tot} online")


def cmd_channels(a):
    txt = http_get(a.nvr, "/ISAPI/ContentMgmt/InputProxy/channels", a.user, a.password)
    for block in re.findall(r"<InputProxyChannel.*?</InputProxyChannel>", txt, re.S):
        def g(t):
            m = re.search(rf"<{t}>([^<]*)</{t}>", block); return m.group(1) if m else "?"
        print(f"  ch{g('id'):<2} {g('ipAddress'):<16} proto={g('proxyProtocol'):<8} "
              f"user={g('userName'):<8} port={g('managePortNo')}")


def cmd_findpass(a):
    ip = a.cam
    pwlist = a.passwords or ["12345", "123456", "1234", "admin", "Admin123"]
    print(f"=== findpass {ip} (user={a.user}) — mă opresc la prima corectă ===")
    print("    (ATENȚIE: prea multe încercări greșite -> UserLocked ~30 min)")
    good = None
    for p in pwlist:
        # LAPI (dacă e cameră cu LAPI) — util că distinge UserLocked de Unauthorized
        lapi = ""
        try:
            t = http_get(ip, "/LAPI/V1.0/System/DeviceBasicInfo", a.user, p, timeout=8)
            sc = re.search(r'"StatusString":\s*"([^"]+)"', t)
            lapi = sc.group(1) if sc else "?"
        except urllib.error.HTTPError as e:
            lapi = f"HTTP{e.code}"
        except Exception:
            lapi = "-"
        ok, info = onvif_device_info(ip, a.user, p)
        print(f"  '{p}': ONVIF={'OK ' + str(info) if ok else info} | LAPI={lapi}")
        if lapi == "UserLocked":
            print("  >>> UserLocked: parola POATE fi corectă dar contul e blocat. OPREȘTE-TE, "
                  "power-cycle camera, reîncearcă peste câteva minute DOAR parola bună.")
            return
        if ok:
            good = p; break
    if good:
        print(f"  >>> PAROLA: '{good}'  ->  setează pe NVR:  "
              f"python3 nvr.py setpass --nvr <NVR> --nvrpass <pw> --ch <id> --campass '{good}'")
    else:
        print("  >>> niciuna din listă. Dă alte parole cu --passwords p1 p2 ... (câte puține odată).")


def _inject_password(channel_xml, campass):
    xml = re.sub(r"<password>[^<]*</password>", "", channel_xml)
    xml = re.sub(r"(<userName>[^<]*</userName>)",
                 r"\1\n<password>" + campass + "</password>", xml, count=1)
    return xml


def cmd_setpass(a):
    path = f"/ISAPI/ContentMgmt/InputProxy/channels/{a.ch}"
    xml = http_get(a.nvr, path, a.user, a.nvrpass)
    xml = _inject_password(xml, a.campass)
    try:
        resp = http_put(a.nvr, path, xml, a.user, a.nvrpass)
        st = re.search(r"<statusString>([^<]*)</statusString>", resp)
        print(f"  ch{a.ch} PUT -> {st.group(1) if st else resp[:120]}")
    except urllib.error.HTTPError as e:
        print(f"  ch{a.ch} PUT ERR {e.code}: {e.read().decode()[:160]}"); return
    print("  aștept 15s să reconecteze...")
    time.sleep(15)
    txt = http_get(a.nvr, "/ISAPI/ContentMgmt/InputProxy/channels/status", a.user, a.nvrpass)
    for cid, ipx, on, det in _STATUS_RE.findall(txt):
        if cid == str(a.ch):
            print(f"  ch{cid} {ipx}: online={on} detect={det}")


def cmd_addcam(a):
    # descoperă câte canale sunt și ia următorul id
    txt = http_get(a.nvr, "/ISAPI/ContentMgmt/InputProxy/channels", a.user, a.nvrpass)
    ids = [int(x) for x in re.findall(r"<id>(\d+)</id>", txt)]
    cid = a.ch or (max(ids) + 1 if ids else 1)
    body = (
        f'<InputProxyChannel xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        f"<id>{cid}</id><sourceInputPortDescriptor>"
        f"<proxyProtocol>{a.protocol}</proxyProtocol>"
        f"<addressingFormatType>ipaddress</addressingFormatType>"
        f"<ipAddress>{a.cam}</ipAddress><managePortNo>{a.port}</managePortNo>"
        f"<srcInputPort>1</srcInputPort><userName>{a.camuser}</userName>"
        f"<password>{a.campass}</password><streamType>auto</streamType>"
        f"</sourceInputPortDescriptor></InputProxyChannel>")
    try:
        resp = http_post_xml(a.nvr, "/ISAPI/ContentMgmt/InputProxy/channels", body, a.user, a.nvrpass)
        st = re.search(r"<statusString>([^<]*)</statusString>", resp)
        print(f"  addcam ch{cid} {a.cam} ({a.protocol}) -> {st.group(1) if st else resp[:150]}")
    except urllib.error.HTTPError as e:
        print(f"  addcam ERR {e.code}: {e.read().decode()[:200]}")


def cmd_discover(a):
    sub = a.subnet.rstrip(".")
    local = a.local_ip
    print(f"=== discover pe {sub}.0/24 (local={local}) ===")
    # 1) ARP sweep
    procs = []
    for i in range(1, 255):
        procs.append(subprocess.Popen(["ping", "-c1", "-W" if not is_mac_os() else "-t", "1",
                                       f"{sub}.{i}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        try: p.wait(timeout=3)
        except Exception: p.kill()
    arp = subprocess.run(["arp", "-an"], capture_output=True, text=True).stdout
    cams = re.findall(rf"\(({re.escape(sub)}\.\d+)\) at ([0-9a-f:]{{11,17}})", arp, re.I)
    print("  --- ARP (dispozitive pe subnet) ---")
    for ip, mac in sorted(cams, key=lambda x: int(x[0].split(".")[-1])):
        tag = f"  OUI={a.oui}" if a.oui and mac.lower().startswith(a.oui.lower()) else ""
        print(f"    {ip:<16} {mac}{tag}")
    # 2) ONVIF WS-Discovery
    print("  --- ONVIF WS-Discovery ---")
    for ip in _wsdiscovery(local):
        print(f"    ONVIF: {ip}")
    # 3) SADP
    print("  --- SADP (UDP 37020) ---")
    for d in _sadp([local]):
        print(f"    SADP: ip={d.get('ip')} mac={d.get('mac')} type={d.get('type')}")


def _wsdiscovery(local, wait=5):
    msg = (f'<?xml version="1.0" encoding="UTF-8"?><e:Envelope '
           'xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
           'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
           'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
           'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header>'
           f"<w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>"
           "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
           "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
           "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>"
           "</d:Probe></e:Body></e:Envelope>")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: s.bind((local, 0))
    except Exception: pass
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    s.settimeout(2); s.sendto(msg.encode(), ("239.255.255.250", 3702))
    found = set(); t = time.time()
    while time.time() - t < wait:
        try:
            data, addr = s.recvfrom(65535); found.add(addr[0])
        except socket.timeout:
            break
        except Exception:
            break
    return sorted(found)


def _sadp(locals_, wait=8):
    MCAST, PORT = "239.255.255.250", 37020
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except Exception: pass
    r.bind(("", PORT))
    for local in locals_:
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(MCAST), socket.inet_aton(local))
            r.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception:
            pass
    r.settimeout(2)
    probe = (f'<?xml version="1.0" encoding="utf-8"?><Probe><Uuid>{{{uuid.uuid4()}}}</Uuid>'
             "<Types>inquiry</Types></Probe>")
    for local in locals_:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind((local, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            s.sendto(probe.encode(), (MCAST, PORT)); s.close()
        except Exception:
            pass
    devs = {}; t = time.time()

    def g(tag, txt):
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", txt, re.I); return m.group(1) if m else None
    while time.time() - t < wait:
        try:
            data, addr = r.recvfrom(65535); txt = data.decode(errors="ignore")
            mac = g("MAC", txt); ip = g("IPv4Address", txt) or g("IPAddress", txt)
            if mac or ip:
                devs[mac or addr[0]] = {"ip": ip or addr[0], "mac": mac or "?",
                                        "type": g("DeviceType", txt) or g("DeviceDescription", txt) or "?"}
        except socket.timeout:
            continue
        except Exception:
            continue
    return list(devs.values())


def cmd_sniff(a):
    """Găsește camere ASCUNSE care au un IP pe alt subnet: ascultă tot traficul cu OUI-ul
    brandului și le scoate IP-ul din ARP. TREBUIE rulat cu sudo (tcpdump)."""
    oui = a.oui.replace(":", "").replace("-", "").lower()
    if len(oui) != 6:
        print("  --oui trebuie să fie 3 octeți, ex. e4:f1:4c"); return
    b = [oui[0:2], oui[2:4], oui[4:6]]
    flt = f"(ether[6]=0x{b[0]} and ether[7]=0x{b[1]} and ether[8]=0x{b[2]}) or arp"
    cmd = ["tcpdump", "-i", a.iface, "-n", "-e", "-l", flt]
    print(f"# tcpdump {a.seconds}s pe {a.iface}, OUI {a.oui} ...")
    if os.geteuid() != 0:
        print("  ATENȚIE: nu rulezi ca root. Rulează cu sudo:")
        print("  sudo " + " ".join([sys.executable, os.path.abspath(__file__), "sniff",
                                     "--iface", a.iface, "--oui", a.oui, "--seconds", str(a.seconds)]))
        return
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.seconds)
        out = r.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
    macs = {}
    for line in out.splitlines():
        ml = re.search(rf"({re.escape(a.oui.lower())}:[0-9a-f:]+)\s*>", line)
        if ml:
            mac = ml.group(1)
            ipm = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\.\d+\s*>", line)
            tell = re.search(r"tell (\d+\.\d+\.\d+\.\d+)", line)
            ip = (ipm.group(1) if ipm else None) or (tell.group(1) if tell else None)
            macs.setdefault(mac, set())
            if ip: macs[mac].add(ip)
        else:
            tell = re.search(rf"who-has .* tell (\d+\.\d+\.\d+\.\d+).*({re.escape(a.oui.lower())}[0-9a-f:]+)", line)
    print(f"  {len(macs)} camere (MAC-uri distincte) văzute pe fir:")
    for mac, ips in sorted(macs.items()):
        print(f"    {mac}  IP: {sorted(ips) or '(doar L2 — repetă sniff sau vezi ARP tell)'}")
    print("  >>> IP-uri necunoscute = camere pe alt subnet. Adaugă subnetul (nvr.py reach) și probe-uiește.")


# =========================== argparse ===========================

def build_parser():
    p = argparse.ArgumentParser(description="Toolkit recuperare NVR + camere IP (Hikvision/Safer/ONVIF).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("reach", help="comanda de adăugat un IP secundar")
    s.add_argument("--iface", default="en0" if is_mac_os() else "eth0")
    s.add_argument("--ip", required=True, help="ex. 172.16.0.138")
    s.add_argument("--mask", default="255.255.0.0")
    s.add_argument("--prefix", help="(Linux) ex. 16")
    s.set_defaults(func=cmd_reach)

    s = sub.add_parser("probe", help="ce servicii are o cameră (fără parolă)")
    s.add_argument("--cam", required=True)
    s.add_argument("--timeout", type=int, default=2)
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("status", help="canalele NVR: online / erori")
    s.add_argument("--nvr", required=True)
    s.add_argument("--user", default="admin")
    s.add_argument("--password", required=True)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("channels", help="lista canalelor NVR")
    s.add_argument("--nvr", required=True)
    s.add_argument("--user", default="admin")
    s.add_argument("--password", required=True)
    s.set_defaults(func=cmd_channels)

    s = sub.add_parser("findpass", help="testează parole pe o cameră (ONVIF+LAPI)")
    s.add_argument("--cam", required=True)
    s.add_argument("--user", default="admin")
    s.add_argument("--passwords", nargs="*", help="listă parole de testat (implicit: uzuale)")
    s.set_defaults(func=cmd_findpass)

    s = sub.add_parser("setpass", help="setează parola camerei pe un canal existent")
    s.add_argument("--nvr", required=True)
    s.add_argument("--user", default="admin")
    s.add_argument("--nvrpass", required=True)
    s.add_argument("--ch", type=int, required=True)
    s.add_argument("--campass", required=True)
    s.set_defaults(func=cmd_setpass)

    s = sub.add_parser("addcam", help="adaugă o cameră nouă ca un canal pe NVR")
    s.add_argument("--nvr", required=True)
    s.add_argument("--user", default="admin")
    s.add_argument("--nvrpass", required=True)
    s.add_argument("--cam", required=True, help="IP-ul camerei")
    s.add_argument("--camuser", default="admin")
    s.add_argument("--campass", required=True)
    s.add_argument("--protocol", default="ONVIF", choices=["ONVIF", "HIKVISION"])
    s.add_argument("--port", type=int, default=80)
    s.add_argument("--ch", type=int, help="id canal (implicit: următorul liber)")
    s.set_defaults(func=cmd_addcam)

    s = sub.add_parser("discover", help="ARP + ONVIF WS-Discovery + SADP pe un subnet")
    s.add_argument("--subnet", required=True, help="ex. 172.16.0")
    s.add_argument("--local-ip", required=True, help="IP-ul tău pe acel subnet (pt multicast)")
    s.add_argument("--oui", help="OUI de evidențiat, ex. e4:f1:4c")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("sniff", help="găsește camere ascunse pe fir (tcpdump pe OUI) — sudo")
    s.add_argument("--iface", default="en0" if is_mac_os() else "eth0")
    s.add_argument("--oui", required=True, help="OUI-ul brandului, ex. e4:f1:4c")
    s.add_argument("--seconds", type=int, default=20)
    s.set_defaults(func=cmd_sniff)
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        print(f"Rețea: {e} — ești pe subnetul camerelor? (vezi:  python3 nvr.py reach ...)")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
