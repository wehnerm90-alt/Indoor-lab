"""Der 'lab'-Befehl: Tickets, Teile neu stecken/tauschen, USB-Stick, Eskalation, Bewertung, Abschluss.

Die Bewertung folgt den Inspur-Anleitungen aus Ticket Kompakt (Vor Ort, Red Lines, DIMM/GPU/fieldiag,
ECC-Tauschkriterium, Firmware, Abnahme).
"""
import os
import socket
import sys
import textwrap
from datetime import datetime

from . import core

B, R, G, Y, C, X = "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
if not sys.stdout.isatty():
    B = R = G = Y = C = X = ""

GPU_SZ = ("gpu_offbus", "gpu_remap_fail", "gpu_remap_pending", "nvswitch")
RESEAT_SZ = {"gpu_offbus": "gpu", "dimm_ue": "dimm", "fan_fail": "fan", "psu_fail": "psu"}


def eigene_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


HILFE = f"""{B}Inspur-Lab{X} – Übungsumgebung für Service-Tickets (alles simuliert)

{B}Tickets{X}
  lab neu [szenario] [server]   neues Ticket (zufällig oder gezielt)
  lab ticket                    Ticket nochmal lesen
  lab szenarien                 alle Szenarien
  lab tipp                      Hinweis (kostet 5 Punkte)
  lab pruefen                   Zwischenstand
  lab eskalieren "<grund>"      an Global Service eskalieren (z. B. SN passt nicht zum Ticket)
  lab abschluss                 Ticket abmelden + Maintenance Process Checklist
  lab verlauf                   bisherige Tickets und Punkte

{B}Am Server (vor Ort){X}
  lab server                    Server, BMC-Adressen, Zustand
  lab karte <server>            Service-Karte: Slots, Bus-IDs (BDF), DIMM-Belegung
  lab reseat <server> <teil> <nr>   Teil ausbauen und neu stecken (Sitz/Kontakt prüfen)
  lab tausch <server> <teil> <nr>   Teil gegen das Ersatzteil tauschen
                                Teile: gpu, dimm, psu, fan, nvme, hdd, baseboard
  lab stick <server> [rein|raus]    AI-PE-USB-Stick (Testtools, fieldiag) stecken/ziehen
  lab login <server>            Shell im Kunden-OS (wie SSH). Besser: KVM im BMC

{B}Sonstiges{X}
  lab firmware                  Firmware-Depot (Dateien, Prüfsummen)
  lab reset --ja                alles zurücksetzen
  lab update                    neueste Lab-Version holen

{B}Werkzeuge{X}
  BMC-Web (KVM, IDL, One-key Log, HPM-Update): http://<IP>:8001-8003   admin/admin
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel elist | fru print | mc info | raw …
  KVM: DEL = BIOS-Setup, F11 = Bootmenü (AI PE vom Stick, Login root / 111111)
"""


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "hilfe"
    fn = BEFEHLE.get(cmd)
    if not fn:
        print(f"Unbekannter Befehl: {cmd}\n")
        print(HILFE)
        sys.exit(1)
    fn(a[1:])


def cmd_hilfe(a):
    print(HILFE)


def cmd_szenarien(a):
    print(f"{B}Szenarien{X}")
    for name, d in core.SZENARIEN.items():
        srv = [n for n, i in core.SERVERS.items() if core.MODELS[i["modell"]]["typ"] in d["typen"]]
        print(f"  {name:<18} {d['titel']:<42} ({', '.join(srv)})")
    print("\nBeispiel: lab neu dimm_ue std01   oder einfach: lab neu")


def cmd_neu(a):
    force = "--force" in a
    a = [x for x in a if x != "--force"]
    name = a[0] if a else None
    srv = a[1] if len(a) > 1 else None
    if name in core.SERVERS:
        name, srv = None, name
    with core.zustand() as st:
        tk = st.get("ticket")
        if tk and not tk["abgeschlossen"] and not force:
            print(f"{Y}Es ist noch ein Ticket offen ({tk['nr']}).{X} Erst 'lab abschluss', oder mit 'lab neu --force' verwerfen.")
            return
        try:
            tk = core.szenario_starten(st, name, srv)
        except ValueError as e:
            print(f"{R}{e}{X}")
            return
    core.fw_depot_erzeugen()
    print(f"{G}Neues Ticket eingegangen.{X}\n")
    zeige_ticket(tk)


def zeige_ticket(tk):
    st = core.lesen()
    s = st["servers"][tk["server"]]
    w = 70
    print("┌" + "─" * w + "┐")
    kopf, case = f" {tk['nr']}", f"CASE: {tk['case']} "
    print("│" + kopf + " " * (w - len(kopf) - len(case)) + case + "│")
    print("├" + "─" * w + "┤")
    for k, v in [("Kunde", tk["kunde"]), ("Standort", f"RZ Frankfurt (fiktiv), Rack {s['rack']}"),
                 ("Server", f"{s['modell']}  SN {s['sn']}"), ("Betreff", tk["titel"]),
                 ("Erstellt", datetime.fromtimestamp(tk["erstellt"]).strftime("%d.%m.%Y %H:%M"))]:
        print(f"│ {k:<9} {v:<{w - 11}}│")
    print("├" + "─" * w + "┤")
    for z in textwrap.wrap("Fehler: " + tk["meldung"], w - 2):
        print(f"│ {z:<{w - 2}} │")
    for z in textwrap.wrap("Vorgeschlagene Arbeiten: " + tk["vorschlag"], w - 2):
        print(f"│ {z:<{w - 2}} │")
    for z in textwrap.wrap("Ersatzteile: " + (", ".join(tk["teile"]) or "keine"), w - 2):
        print(f"│ {z:<{w - 2}} │")
    print("├" + "─" * w + "┤")
    hinweis = ("SN am Gerät = SN im Ticket? FW-Stände und FRU vor dem Eingriff notieren, Onekey-Log vorher und "
               "nachher sichern. Nur die Teile aus dem Ticket tauschen.")
    if "Tencent" in tk["kunde"]:
        hinweis += " Tencent: rotes Klebeband am Altteil und Fotos."
    for z in textwrap.wrap(hinweis, w - 2):
        print(f"│ {Y}{z:<{w - 2}}{X} │")
    print("└" + "─" * w + "┘")
    print(f"(Server im Lab: {tk['server']})" + (f"   {G}abgeschlossen{X}" if tk["abgeschlossen"] else ""))


def cmd_ticket(a):
    st = core.lesen()
    if not st.get("ticket"):
        print("Kein Ticket. Starte eins mit: lab neu")
        return
    zeige_ticket(st["ticket"])


def phase_text(s):
    if s["power"] != "on":
        return f"{R}aus{X}"
    return {"post": f"{Y}POST{X}", "setup": f"{Y}BIOS-Setup{X}", "bootmenu": f"{Y}Bootmenü{X}",
            "noboot": f"{R}kein Bootgerät{X}", "os": f"{G}Kunden-OS{X}" if s.get("os") == "prod" else f"{C}AI PE{X}"}.get(s.get("phase"), "?")


def cmd_server(a):
    st = core.lesen()
    ip = eigene_ip()
    tk = st.get("ticket")
    print(f"{B}{'Server':<7} {'Modell':<9} {'Zustand':<12} {'BMC':<5} {'BMC-Web / Redfish':<24} {'ipmitool -H':<11} Stick{X}")
    for n, s in st["servers"].items():
        bmc = "ok" if core.bmc_bereit(s) else f"{Y}boot{X}"
        mark = f"  {Y}<- Ticket{X}" if tk and not tk["abgeschlossen"] and tk["server"] == n else ""
        zt = phase_text(s)
        sichtbar = len(zt) - (len(R) + len(X) if chr(27) in zt else 0)
        print(f"{n:<7} {s['modell']:<9} {zt}{' ' * max(1, 13 - sichtbar)}{bmc:<5} "
              f"http://{ip}:{s['port']:<10} bmc-{n:<7} {'ja' if s.get('stick') else '-'}{mark}")
    print(f"\nTicket-Portal & Firmware-Depot: http://{ip}:8000      BMC-Login: admin / admin")


def cmd_login(a):
    """Lokale Konsole (tty1) des Servers – dieselbe emulierte Konsole wie in der BMC-KVM."""
    import json as _json
    import select
    import shutil
    import termios
    import threading as _th
    import tty as _tty
    import urllib.request
    if not a or a[0] not in core.SERVERS:
        print("Welcher Server? z. B.: lab login gpu01")
        return
    name = a[0]
    s = core.lesen()["servers"][name]
    if s["power"] != "on" or s.get("phase") != "os":
        print(f"{Y}Kein Betriebssystem aktiv – Zustand: {phase_text(s)}{Y}. POST/BIOS/Bootmenü siehst du in der KVM im BMC.{X}")
        return
    basis = f"http://127.0.0.1:{core.SERVERS[name]['port']}"
    cookie = {}

    def req(pfad, daten, timeout=10):
        r = urllib.request.Request(basis + pfad, data=_json.dumps(daten).encode(), method="POST",
                                   headers={"Content-Type": "application/json", **({"Cookie": cookie["c"]} if cookie else {})})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            c = resp.headers.get("Set-Cookie")
            if c:
                cookie["c"] = c.split(";")[0]
            return _json.loads(resp.read() or b"{}")
    try:
        req("/api/session", {"username": core.BMC_USER, "password": core.BMC_PASS})
        cols, rows = shutil.get_terminal_size((100, 36))
        att = req("/api/kvm/tty/attach", {"cols": cols, "rows": rows})
    except Exception as e:  # noqa: BLE001
        print(f"Konsole nicht erreichbar ({e}). Läuft der Lab-Dienst? systemctl status inspur-lab")
        return
    gen, pos = att["gen"], att["pos"]
    print(f"{Y}Konsole {name} (tty1) – trennen mit Strg+]{X}")
    fd = sys.stdin.fileno()
    alt = termios.tcgetattr(fd)
    ende = _th.Event()

    def lesen():
        p = pos
        while not ende.is_set():
            try:
                d = req("/api/kvm/tty/poll", {"gen": gen, "pos": p}, timeout=15)
            except Exception:  # noqa: BLE001
                ende.set()
                break
            if d.get("data"):
                sys.stdout.write(d["data"])
                sys.stdout.flush()
            p = d.get("pos", p)
    try:
        _tty.setraw(fd)
        t = _th.Thread(target=lesen, daemon=True)
        t.start()
        while not ende.is_set():
            r, _, _ = select.select([fd], [], [], 0.3)
            if not r:
                continue
            daten = os.read(fd, 1024).decode("utf-8", "replace")
            if "\x1d" in daten:
                break
            try:
                req("/api/kvm/tty/input", {"gen": gen, "data": daten})
            except Exception:  # noqa: BLE001
                break
    finally:
        ende.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, alt)
        print(f"\n{Y}Konsole getrennt.{X}")


def cmd_karte(a):
    if not a or a[0] not in core.SERVERS:
        print("Welcher Server? z. B.: lab karte gpu01")
        return
    s = core.lesen()["servers"][a[0]]
    m = core.MODELS[s["modell"]]
    print(f"{B}Service-Karte {s['modell']} ({a[0]}) – Lab-Version{X}\n")
    if m["gpus"]:
        print(f"{B}GPUs{X}  (Slot = Nummer am Gehäuse, BDF = PCIe-Adresse im OS/IDL)")
        for i in range(m["gpus"]):
            print(f"  GPU{i}  Slot {i}  BDF {core.GPU_BUS[i]}:00.0   {'SXM4-Modul auf dem HGX-Baseboard' if m['typ'] == 'hgx' else 'PCIe-Karte im Riser'}")
    if m["nvswitch"]:
        print(f"\n{B}HGX-Baseboard{X}: {m['nvswitch']} NVSwitches (BDF " + ", ".join(f"{b}:00.0" for b in core.NVSWITCH_BUS) +
              ") fest verlötet → Tausch nur als Baseboard")
    print(f"\n{B}DIMMs{X}  (CPUx_CyDz = CPU, Kanal, DIMM)")
    namen = [d["name"] for d in s["comp"]["dimm"]]
    for i in range(0, len(namen), 4):
        print("  " + "   ".join(f"{j:>2}={namen[j]}" for j in range(i, min(i + 4, len(namen)))))
    print(f"\n{B}Netzteile{X}: PSU0–PSU{m['psus'] - 1} (hinten, von links)   {B}Lüfter{X}: FAN0–FAN{m['fans'] - 1} (von links)")
    if m["nvme"]:
        print(f"{B}NVMe{X}: NVMe0–NVMe{m['nvme'] - 1} (vorne)")
    if m["hdd"]:
        print(f"{B}HDD{X}: Slot 0–{m['hdd'] - 1} (vorne), Enclosure 252 – SN steht auf der Platte (storcli64 /c0 show)")
    print(f"\n{B}Hot-Swap{X}: Netzteil, Lüfter, NVMe, HDD.  {B}Nur ausgeschaltet{X}: GPU, DIMM, Baseboard.")
    if m["polling"]:
        print(f"{Y}NF5688M6: vor fieldiag BMC-Polling aus (ipmitool raw 0x3c 0x4c 0x01 0x10 0x0), danach wieder an (… 0x1).{X}")


def _teil_finden(s, teil, nr):
    liste = s["comp"].get(teil)
    if not liste:
        return None, f"Dieser Server hat kein Bauteil '{teil}'."
    if teil == "dimm" and not nr.isdigit():
        treffer = [i for i, d in enumerate(liste) if d["name"].lower() == nr.lower()]
        return (treffer[0], None) if treffer else (None, f"DIMM '{nr}' gibt es nicht. Siehe: lab karte {s['name']}")
    try:
        idx = int(nr)
        liste[idx]
        return idx, None
    except (ValueError, IndexError):
        return None, f"Nummer {nr} gibt es nicht ({teil} 0–{len(liste) - 1})."


def _vorbereiten(st, a, verb):
    if len(a) < 3:
        print(f"So geht's: lab {verb} <server> <teil> <nr>   z. B. lab {verb} gpu01 gpu 3")
        return None
    srv, teil, nr = a[0], a[1].lower(), a[2]
    if srv not in st["servers"]:
        print(f"Unbekannter Server: {srv}")
        return None
    s = st["servers"][srv]
    idx, err = _teil_finden(s, teil, nr)
    if err:
        print(err)
        return None
    if teil not in core.HOTSWAP and s["power"] == "on":
        print(f"{R}Stopp!{X} {core.TEIL_NAMEN[teil]} ist nicht hot-swap-fähig. Server erst herunterfahren lassen "
              f"(BMC: Power Control oder ipmitool … chassis power soft).")
        return None
    return s, teil, idx


def cmd_reseat(a):
    with core.zustand() as st:
        r = _vorbereiten(st, a, "reseat")
        if not r:
            return
        s, teil, idx = r
        d = s["comp"][teil][idx]
        label = d.get("name") or f"{teil.upper()}{idx}"
        if d.get("ursache") in ("kontakt", "kabel"):
            d["status"], d["ursache"] = "ok", None
            if teil == "gpu":
                d["lost_boot"] = 0
        tk = st.get("ticket")
        if tk and not tk["abgeschlossen"]:
            tk["reseat"].append({"t": core.now(), "server": s["name"], "teil": teil, "idx": idx})
        core.aktion(st, f"{label} neu gesteckt")
        core.eingriff(st)
        core.markiere_fix(st)
    extra = " (Netzkabel und Sitz geprüft)" if teil == "psu" else ""
    print(f"{label} aus- und wieder eingebaut{extra}. Ist der Alarm weg? → prüfen (SEL/IDL, Sensoren, nach dem Einschalten POST/OS).")


def cmd_tausch(a):
    with core.zustand() as st:
        r = _vorbereiten(st, a, "tausch")
        if not r:
            return
        s, teil, idx = r
        m = core.MODELS[s["modell"]]
        d = s["comp"][teil][idx]
        alt_sn, alt_status = d["sn"], d["status"]
        neu_sn = core.sn(alt_sn[:2], len(alt_sn) - 2)
        d["sn"] = neu_sn
        if teil == "hdd":
            d.update(status="rebuild", rebuild_start=core.now())
        else:
            d["status"] = "ok"
            d.pop("ursache", None)
        if teil == "gpu":
            d.update(vbios=m["vbios_neu"], vbios_pending=None, ecc_vol=0, ecc_agg=0, remap_corr=0, remap_unc=0, lost_boot=0)
        if teil == "baseboard":
            d["nvswitch_bad"] = None
        label = d.get("name") or f"{teil.upper()}{idx}"
        tk = st.get("ticket")
        if tk and not tk["abgeschlossen"]:
            tk["tausch"].append({"t": core.now(), "server": s["name"], "teil": teil, "idx": idx, "label": label,
                                 "alt_sn": alt_sn, "neu_sn": neu_sn, "war_defekt": alt_status != "ok", "boot_id": s["boot_id"]})
        core.aktion(st, f"{label} getauscht ({alt_sn} -> {neu_sn})")
        core.eingriff(st)
        core.sel_add(s, "Entity Presence", f"{label}_Presence", "Device Inserted/Device Present")
        core.markiere_fix(st)
    print(f"{G}{label} getauscht.{X} Altteil SN {alt_sn} → Neuteil SN {neu_sn}   (PN/QN/SN für die Checkliste notieren)")
    if teil == "hdd":
        print("Rebuild läuft automatisch (ca. 2 Minuten): storcli64 /c0/eall/sall show rebuild")
    if teil == "gpu":
        print("Hinweis: Nach jedem GPU-Tausch prüfen, ob alle VBIOS-Versionen gleich sind (nvflash --version in der AI PE).")


def cmd_stick(a):
    if not a or a[0] not in core.SERVERS:
        print("So geht's: lab stick <server> [rein|raus]")
        return
    with core.zustand() as st:
        s = st["servers"][a[0]]
        rein = not (len(a) > 1 and a[1] in ("raus", "ab", "ziehen", "out"))
        s["stick"] = rein
        core.aktion(st, f"AI-PE-Stick {'gesteckt' if rein else 'gezogen'} ({a[0]})")
    if rein:
        print(f"AI-PE-Stick (USB 3.0) an {a[0]} gesteckt. Booten: KVM öffnen, Neustart, beim POST F11 → USB-Eintrag.\n"
              "Login AI PE: root / 111111. Tools in /iei, fieldiag-Pakete auf der NTFS-Partition unter /media.")
    else:
        print(f"Stick von {a[0]} abgezogen.")


def cmd_eskalieren(a):
    grund = " ".join(a).strip()
    if not grund:
        print('So geht\'s: lab eskalieren "Platten-SN im Slot passt nicht zum Ticket"')
        return
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        tk["eskaliert"] = {"t": core.now(), "grund": grund}
        core.aktion(st, f"Eskaliert an GS: {grund}")
        core.markiere_fix(st)
    print(f"An Global Service eskaliert: „{grund}“. Übergabe dokumentiert. Danach: lab abschluss")


TIPPS = {
    "gpu_offbus": ["Wie viele GPUs zeigt nvidia-smi -L, wie viele lspci | grep -i nvidia? (rev ff) = vom Bus gefallen.",
                   "BDF aus IDL/dmesg mit 'lab karte' einem Slot zuordnen. Inspur: Strom prüfen, neu stecken → Alarm weg? Sonst tauschen.",
                   "Nach der Reparatur fieldiag aus der AI PE (Stick, F11). Danach BIOS zurück, Treiber/OS normal booten."],
    "gpu_remap_fail": ["Nicht gleich tauschen: erst das OS neu starten lassen, dann nvidia-smi -q -d ROW_REMAPPER.",
                       "Steht nach dem Neustart 'Remapping Failure Occurred: Yes' → GPU tauschen (NVIDIA-RMA-Kriterium).",
                       "Nach dem Tausch: VBIOS angleichen (AI PE: Treiber raus, Menü 29 Schreibschutz, nvflash --save / -i), dann fieldiag."],
    "gpu_remap_pending": ["Das Ticket will einen Tausch – prüf erst das NVIDIA-Kriterium: Neustart, dann nvidia-smi -q -d ROW_REMAPPER.",
                          "Pending ist nach dem Neustart 'No' und kein Failure → nicht tauschen. fieldiag --test gpumem und skucheck.",
                          "PASS → ECC-Zähler löschen: nvidia-smi -p 0 und nvidia-smi -p 1. Ersatzteil geht als 'Good – Not Used' zurück."],
    "nvswitch": ["systemctl status nvidia-fabricmanager, lspci | grep -i nvidia (wie viele NVSwitches?), nvidia-smi -q | grep -i -A2 fabric.",
                 "Fehlt ein NVSwitch → der sitzt auf dem HGX-Baseboard. Baseboard tauschen (Server aus).",
                 "Danach fabricmanager läuft, nvlink -s alle Links aktiv, fieldiag aus der AI PE."],
    "dimm_ue": ["IDL/SEL lesen: welcher Slot (CPUx_CyDz)? Onekey-Log → AnalyProcess.log nennt die Position.",
                "Inspur-Ablauf: Server aus, DIMM neu stecken (lab reseat …), einschalten → Alarm weg? (POST, IDL, Memory-Anzahl)",
                "Bleibt 'Memory Device Disabled' → DIMM tauschen. Danach alle DIMMs erkannt, keine neuen IDL-Einträge."],
    "psu_fail": ["IDL: 'AC lost' oder 'Failure detected'? Bei AC lost zuerst Netzkabel/PDU prüfen.",
                 "Erst Sitz/Kontakt prüfen (lab reseat … psu n) → Alarm weg? Sonst Netzteil tauschen (hot-swap).",
                 "Danach PSU-Sensoren ok, Redundanz wieder da."],
    "fan_fail": ["Sensoren: welcher Lüfter steht auf 0 RPM?", "Hot-swap: erst neu stecken, dann ggf. tauschen.",
                 "Danach alle Lüfter normal, keine neue Warnung."],
    "nvme_fail": ["nvme list und lspci: welche NVMe fehlt? Slot und SN mit dem Ticket abgleichen.",
                  "NVMe ist hot-swap: tauschen, danach nvme list.", "Bootmodus nicht verändern (UEFI)."],
    "raid_disk": ["storcli64 /c0 show: welche Platte steht auf Fail? Slot UND SN prüfen.",
                  "Hot-swap: lab tausch std01 hdd <slot>, dann Rebuild abwarten (show rebuild).",
                  "Abschluss erst bei VD 'Optl'."],
    "sn_mismatch": ["Ticket nennt Slot und Platten-SN. Stimmen sie mit storcli64 /c0 show bzw. BMC überein?",
                    "Passt die SN nicht oder ist eine andere Platte defekt: stoppen, nichts tauschen.",
                    'lab eskalieren "…" mit Slot/SN laut Ticket und laut Gerät.'],
    "fw_update": ["Vorher Stände notieren (BMC-Startseite oder ipmitool mc info) und Onekey-Log sichern.",
                  "Nur die freigegebene Version (Ticket) flashen – nicht die neueste. Prüfsummen kontrollieren.",
                  "BMC zuerst (HPM-Update), Neustart abwarten, dann Host aus, BIOS, Host an. Nachher Stände vergleichen."],
}


def cmd_tipp(a):
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        liste = TIPPS[tk["szenario"]]
        n = min(tk["tipps"], len(liste) - 1)
        if tk["tipps"] < len(liste):
            tk["tipps"] += 1
    print(f"{C}Tipp {n + 1}/{len(liste)}:{X} {liste[n]}")


def bewerten(st):
    tk = st["ticket"]
    s = st["servers"][tk["server"]]
    m = core.MODELS[s["modell"]]
    sz = tk["szenario"]
    core.markiere_fix(st)
    fix = tk.get("fix_zeit")
    P = []  # (ok, text, abzug, pflicht)

    def p(ok, text, abzug=0, pflicht=False):
        P.append((bool(ok), text, abzug, pflicht))

    # Pflicht
    if sz == "sn_mismatch":
        falsch = [t for t in tk["tausch"] if t["teil"] == "hdd"]
        p(tk.get("eskaliert") and not falsch, "Abweichung erkannt und eskaliert (nichts getauscht)", 50, True)
    elif sz == "raid_disk" and any(d["status"] == "rebuild" for d in s["comp"]["hdd"]):
        p(False, "RAID-Rebuild läuft noch – erst abschließen, wenn das Volume 'Optl' ist", 0, True)
    else:
        p(core.fehler_behoben(st), "Fehler behoben", 40, True)
    p(s["power"] == "on" and s.get("phase") == "os" and s.get("os") == "prod", "Server bootet wieder ins Kunden-OS", 15, True)
    if sz in GPU_SZ:
        nach = [f for f in tk["fieldiag"] if fix and f["t"] >= fix and f["pass"]]
        voll = [f for f in nach if f["voll"]]
        tests = {t for f in nach for t in f["tests"]}
        ok = voll or (sz == "gpu_remap_pending" and {"gpumem", "skucheck"} <= tests)
        p(ok, "fieldiag nach der Reparatur: PASS" + (" (oder gpumem + skucheck)" if sz == "gpu_remap_pending" else ""), 20, True)

    # Vor Ort / Logs
    p(tk.get("fw_vorher"), "FW-Stände vor dem Eingriff notiert", 5)
    p(tk.get("fru_vorher"), "FRU vor dem Eingriff gesichert (fru print)", 5)
    ok_vor = [o for o in tk.get("onekey", []) if not o["nach_fix"]]
    p(ok_vor, "Onekey-Log vor der Reparatur gesichert", 5)
    if sz not in ("fw_update", "sn_mismatch"):
        p([o for o in tk.get("onekey", []) if o["nach_fix"]], "Onekey-Log nach der Reparatur gesichert", 5)
    if tk.get("sel_ohne_log"):
        p(False, "SEL gelöscht, bevor die Logs gesichert waren", 10)

    # Ablauf am Bauteil
    teil = RESEAT_SZ.get(sz)
    if teil:
        eigene = [t for t in tk["tausch"] if t["server"] == tk["server"] and t["teil"] == teil and t["idx"] == tk["idx"]]
        reseat = [r for r in tk["reseat"] if r["server"] == tk["server"] and r["teil"] == teil and r["idx"] == tk["idx"]]
        if eigene and (not reseat or reseat[0]["t"] > eigene[0]["t"]):
            if tk["ursache"] != "defekt":
                p(False, "Getauscht ohne vorher neu zu stecken – war nur ein Kontaktproblem (Teil unnötig verbraucht)", 15)
            else:
                p(False, "Inspur-Reihenfolge: erst neu stecken, dann tauschen", 5)
    if sz == "gpu_remap_pending":
        if any(t["teil"] == "gpu" for t in tk["tausch"]):
            p(False, "GPU getauscht, obwohl Pending nach Neustart 'No' war (kein RMA-Kriterium)", 25)
        g = s["comp"]["gpu"][tk["idx"]]
        p(g["ecc_agg"] == 0 and g["ecc_vol"] == 0, "ECC-Zähler gelöscht (nvidia-smi -p 0 / -p 1)", 10)
    if sz == "gpu_remap_fail":
        t = [x for x in tk["tausch"] if x["teil"] == "gpu" and x["idx"] == tk["idx"]]
        if t:
            p(t[0]["boot_id"] > tk["boot_bei_start"], "Vor dem Tausch neu gestartet und Remap-Status erneut geprüft", 5)
    if sz in GPU_SZ and s["comp"]["gpu"]:
        p(len({g["vbios"] for g in s["comp"]["gpu"] if g["status"] != "offbus"}) == 1, "Alle GPUs mit gleicher VBIOS-Version", 10)
        vorher = [f for f in tk["fieldiag"] if not fix or f["t"] < fix]
        if sz != "gpu_remap_pending":
            p(any(not f["pass"] for f in vorher) or sz == "gpu_offbus", "Fehler vor der Reparatur mit fieldiag nachgewiesen", 5)

    # Teile aus dem Ticket
    for t in tk["tausch"]:
        richtig = t["server"] == tk["server"] and t["teil"] == tk["teil"] and t["idx"] == tk["idx"]
        if sz in ("gpu_remap_pending", "sn_mismatch"):
            continue
        if not richtig:
            grund = "nicht im Ticket" if t["teil"] != tk["teil"] or t["server"] != tk["server"] else "falscher Steckplatz"
            p(False, f"{t['label']} auf {t['server']} getauscht ({grund})", 15)

    # Zustand wiederherstellen (Abnahme)
    p(s["bios"] == tk["bios_start"], "BIOS-Einstellungen wie vorher (VT-d, SR-IOV, Bootmodus)", 10)
    if m["polling"]:
        p(s.get("polling", True), "BMC-Polling wieder an (Lüfter auf Automatik)", 15)
    p(not s.get("stick") and not s.get("settings", {}).get("vmedia", {}).get("mounted"), "USB-Stick/Virtual Media wieder entfernt", 5)
    boot = s.get("settings", {}).get("boot", {})
    p(boot.get("device", "Hard Disk") == "Hard Disk" or boot.get("persist") != "Permanent", "Bootreihenfolge nicht dauerhaft verstellt", 10)

    # Firmware
    if sz == "fw_update":
        geflasht = {(t["komponente"], t["version"]) for t in s["tasks"] if t["state"] == "Completed"}
        neuer = [f"{k} {v}" for k, v in geflasht if v == core.FW_NEUER.get(k)]
        if neuer:
            p(False, f"Nicht freigegebene FW geflasht ({', '.join(neuer)}) – nur die Kundenvorgabe!", 20)
    if any("verification" in f for f in tk.get("fw_fehler", [])):
        p(False, "Beschädigtes Image hochgeladen – Prüfsumme vorher kontrollieren", 5)

    if tk["tipps"]:
        p(False, f"{tk['tipps']} Tipp(s) genutzt", 5 * tk["tipps"])
    score = max(0, 100 - sum(ab for ok, _, ab, _ in P if not ok))
    fertig = all(ok for ok, _, _, pfl in P if pfl)
    return P, score, fertig


def cmd_pruefen(a):
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        P, score, fertig = bewerten(st)
    print(f"{B}Prüfung {tk['nr']}{X}")
    for ok, txt, ab, pfl in P:
        mark = f"{G}✔{X}" if ok else f"{R}✘{X}"
        print(f"  {mark} {txt}" + (f"  {B}(Pflicht){X}" if pfl and not ok else "") + ("" if ok or not ab else f"  ({Y}-{ab}{X})"))
    print(f"\nPunkte bisher: {B}{score}/100{X}")
    print(f"{G}Bereit zum Abschluss: lab abschluss{X}" if fertig else f"{Y}Noch nicht fertig.{X}")


def cmd_abschluss(a):
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        P, score, fertig = bewerten(st)
        if not fertig and "--trotzdem" not in a:
            print(f"{Y}Das Ticket ist noch nicht fertig.{X} Siehe 'lab pruefen'. (Abmelden ohne Lösung: lab abschluss --trotzdem)")
            return
        s = st["servers"][tk["server"]]
        tk["abgeschlossen"], tk["score"] = True, score
        st["verlauf"].append({"nr": tk["nr"], "case": tk["case"], "titel": tk["titel"], "server": tk["server"],
                              "score": score, "t": core.now(), "geloest": fertig})
        text = checkliste(tk, s, P, score)
    os.makedirs(os.path.join(core.VAR, "abschluesse"), exist_ok=True)
    pfad = os.path.join(core.VAR, "abschluesse", f"{tk['case']}.txt")
    with open(pfad, "w") as f:
        f.write(text)
    print(text)
    print(f"Gespeichert unter: {pfad}")


def checkliste(tk, s, P, score):
    w = 72
    fd = tk["fieldiag"]
    z = ["=" * w]
    titel, case = "MAINTENANCE PROCESS CHECKLIST (Lab)", f"CASE: {tk['case']}"
    z.append(titel + " " * (w - len(titel) - len(case)) + case)
    z.append("=" * w)
    z.append(f"Ticket:        {tk['nr']}            Kunde: {tk['kunde']}")
    z.append(f"Server:        {s['modell']}  SN {s['sn']}  Rack {s['rack']}")
    z.append(f"Fehlerbild:    {tk['titel']}")
    z.append(f"Datum:         {datetime.now():%d.%m.%Y %H:%M}")
    z.append("-" * w)
    if tk["tausch"]:
        for t in tk["tausch"]:
            z.append(f"Getauscht:     {t['label']:<12} alt SN {t['alt_sn']}  ->  neu SN {t['neu_sn']}")
    else:
        z.append("Getauscht:     kein Teil (Ersatzteil als 'Good – Not Used' zurück)")
    if tk.get("eskaliert"):
        z.append(f"Eskaliert:     {tk['eskaliert']['grund']}")
    z.append(f"Firmware:      BMC {s['fw']['BMC']}  BIOS {s['fw']['BIOS']}")
    if s["comp"]["gpu"]:
        z.append("Funktionstest: fieldiag " + (("PASS" if fd[-1]["pass"] else "FAIL") + f" (Level {fd[-1]['level']})" if fd else "nicht durchgeführt"))
    z.append("-" * w)
    for ok, txt, _, _ in P:
        z.append(f"[{'x' if ok else ' '}] {txt}")
    z.append("[ ] SN am Gerät = SN im Ticket, Wartungsetikett vorne/hinten     (selbst)")
    z.append("[ ] ESD-Armband, Altteil verpackt, PN/QN/SN notiert              (selbst)")
    if "Tencent" in tk["kunde"]:
        z.append("[ ] Rotes Klebeband am Altteil + Fotos                           (Tencent)")
    z.append("[ ] Kunde bestätigt: Server läuft, Checkliste unterschrieben     (selbst)")
    z.append("-" * w)
    z.append(f"Bewertung: {score}/100")
    z.append("=" * w)
    return "\n".join(z) + "\n"


def cmd_verlauf(a):
    st = core.lesen()
    if not st["verlauf"]:
        print("Noch keine abgeschlossenen Tickets.")
        return
    print(f"{B}{'Datum':<17} {'Ticket':<16} {'Server':<7} {'Punkte':>6}  Betreff{X}")
    for v in st["verlauf"]:
        print(f"{datetime.fromtimestamp(v['t']):%d.%m.%Y %H:%M}  {v['nr']:<16} {v['server']:<7} {v['score']:>6}  {v['titel']}")
    print(f"\nDurchschnitt: {sum(v['score'] for v in st['verlauf']) / len(st['verlauf']):.0f} Punkte aus {len(st['verlauf'])} Tickets")


def cmd_firmware(a):
    core.fw_depot_erzeugen()
    print(f"{B}Firmware-Depot{X}: {core.FW_DIR}   (Web: http://{eigene_ip()}:8000/firmware/)\n")
    for f in sorted(os.listdir(core.FW_DIR)):
        print(f"  {f}")
    print(f"\nFreigegeben laut Kunde: BMC {core.FW_TARGET['BMC']}, BIOS {core.FW_TARGET['BIOS']}  (neuere Dateien sind NICHT freigegeben)")
    print(f"Prüfen:  cd {core.FW_DIR} && sha256sum -c SHA256SUMS")
    print("Update:  BMC-Web → Maintenance → HPM Firmware Update, oder per Redfish:")
    print(f"  curl -u admin:admin -F UpdateFile=@{core.FW_DIR}/NF5688M6_BMC_{core.FW_TARGET['BMC']}.hpm http://bmc-gpu01:8001/redfish/v1/UpdateService/upload")


def cmd_reset(a):
    if "--ja" not in a:
        print("Setzt alle Server, das Ticket und den Verlauf zurück. Bestätigen mit: lab reset --ja")
        return
    with core.zustand() as st:
        neu = core.neuer_zustand()
        st.clear()
        st.update(neu)
    core.fw_depot_erzeugen()
    print("Lab zurückgesetzt.")


def cmd_update(a):
    import subprocess
    print("Lade neueste Version …")
    r = subprocess.run(["git", "-C", core.BASE, "pull", "--ff-only"])
    if r.returncode == 0:
        subprocess.run(["bash", os.path.join(core.BASE, "setup.sh")])


BEFEHLE = {"hilfe": cmd_hilfe, "help": cmd_hilfe, "-h": cmd_hilfe, "--help": cmd_hilfe,
           "neu": cmd_neu, "ticket": cmd_ticket, "szenarien": cmd_szenarien, "tipp": cmd_tipp,
           "pruefen": cmd_pruefen, "prüfen": cmd_pruefen, "abschluss": cmd_abschluss, "verlauf": cmd_verlauf,
           "eskalieren": cmd_eskalieren, "server": cmd_server, "login": cmd_login, "karte": cmd_karte,
           "tausch": cmd_tausch, "reseat": cmd_reseat, "stecken": cmd_reseat, "stick": cmd_stick,
           "firmware": cmd_firmware, "reset": cmd_reset, "update": cmd_update}
