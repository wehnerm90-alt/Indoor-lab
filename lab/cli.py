"""Der 'lab'-Befehl: Tickets erzeugen, Teile tauschen, Ergebnis pruefen, Abschluss."""
import os
import random
import socket
import sys
import textwrap
from datetime import datetime

from . import core

B, R, G, Y, C, X = "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
if not sys.stdout.isatty():
    B = R = G = Y = C = X = ""


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
  lab neu [szenario] [server]   neues Ticket (zufällig, oder gezielt)
  lab ticket                    aktuelles Ticket anzeigen
  lab szenarien                 alle Szenarien auflisten
  lab tipp                      Hinweis (kostet 5 Punkte)
  lab pruefen                   Zwischenstand: ist alles erledigt?
  lab abschluss                 Ticket abmelden + Maintenance-Checkliste
  lab verlauf                   bisherige Tickets und Punkte

{B}Server{X}
  lab server                    Server, BMC-Adressen, Stromzustand
  lab login <server>            auf dem Server einloggen (nvidia-smi, fieldiag, dmesg …)
  lab karte <server>            Service-Karte: Slots, Bus-IDs, DIMM-Belegung
  lab tausch <server> <teil> <nr>   Bauteil physisch tauschen
                                Teile: gpu, dimm, psu, fan, nvme, hdd, baseboard

{B}Firmware{X}
  lab firmware                  Firmware-Depot (Dateien, Prüfsummen)

{B}Sonstiges{X}
  lab reset                     alles zurücksetzen
  lab update                    Lab auf die neueste Version bringen

{B}Werkzeuge{X} (wie im Feld)
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel elist
  curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/Systems/1
  Auf dem Server (nach lab login): nvidia-smi, fieldiag, dmesg, lspci, nvme list, storcli64
"""


def main():
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
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
        print(f"  {name:<18} {d['titel']:<45} ({', '.join(srv)})")
    print("\nBeispiel: lab neu dimm_ue std01   oder einfach: lab neu")


def cmd_neu(a):
    force = "--force" in a
    a = [x for x in a if x != "--force"]
    name = a[0] if a else None
    srv = a[1] if len(a) > 1 else None
    if name in core.SERVERS:  # 'lab neu gpu01' = zufaelliges Szenario auf gpu01
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
    breite = 66
    print("┌" + "─" * breite + "┐")
    kopf = f" {tk['nr']}"
    case = f"CASE: {tk['case']} "
    print("│" + kopf + " " * (breite - len(kopf) - len(case)) + case + "│")
    print("├" + "─" * breite + "┤")
    felder = [("Kunde", tk["kunde"]), ("Standort", f"RZ Frankfurt (fiktiv), Rack {s['rack']}"),
              ("Server", f"{s['modell']}  SN {s['sn']}  ({tk['server']})"),
              ("Betreff", tk["titel"]), ("Erstellt", datetime.fromtimestamp(tk["erstellt"]).strftime("%d.%m.%Y %H:%M"))]
    for k, v in felder:
        print(f"│ {k:<9} {v:<{breite - 11}}│")
    print("├" + "─" * breite + "┤")
    for z in textwrap.wrap(tk["meldung"], breite - 2):
        print(f"│ {z:<{breite - 2}} │")
    print("├" + "─" * breite + "┤")
    teile = ", ".join(tk["teile"]) if tk["teile"] else "keine"
    for z in textwrap.wrap(f"Ersatzteile: {teile}", breite - 2):
        print(f"│ {z:<{breite - 2}} │")
    hinweis = ("Hinweis: Nur die im Ticket genannten Teile tauschen. Vor und nach der Reparatur testen, "
               "SEL sichern, nach erfolgreicher Reparatur SEL löschen.")
    if "Tencent" in tk["kunde"]:
        hinweis += " Tencent: rotes Klebeband am Altteil und Fotos nicht vergessen."
    for z in textwrap.wrap(hinweis, breite - 2):
        print(f"│ {z:<{breite - 2}} │")
    print("└" + "─" * breite + "┘")
    if tk["abgeschlossen"]:
        print(f"{G}(abgeschlossen){X}")


def cmd_ticket(a):
    st = core.lesen()
    if not st.get("ticket"):
        print("Kein Ticket. Starte eins mit: lab neu")
        return
    zeige_ticket(st["ticket"])


def cmd_server(a):
    st = core.lesen()
    ip = eigene_ip()
    tk = st.get("ticket")
    print(f"{B}{'Server':<7} {'Modell':<9} {'Strom':<5} {'BMC':<6} {'BMC-Web / Redfish':<28} ipmitool -H{X}")
    for n, s in st["servers"].items():
        strom = f"{G}an{X}  " if s["power"] == "on" else f"{R}aus{X} "
        bmc = "ok" if core.bmc_bereit(s) else f"{Y}boot{X}"
        mark = f"  {Y}<- Ticket{X}" if tk and not tk["abgeschlossen"] and tk["server"] == n else ""
        print(f"{n:<7} {s['modell']:<9} {strom} {bmc:<6} http://{ip}:{s['port']:<12} bmc-{n}{mark}")
    print(f"\nTicket-Portal & Firmware-Depot: http://{ip}:8000")
    print("BMC-Login: admin / admin")


def cmd_login(a):
    if not a:
        print("Welcher Server? z. B.: lab login gpu01")
        return
    name = a[0]
    st = core.lesen()
    if name not in st["servers"]:
        print(f"Unbekannter Server. Vorhanden: {', '.join(st['servers'])}")
        return
    s = st["servers"][name]
    if s["power"] != "on":
        print(f"ssh: connect to host {name} port 22: No route to host")
        print(f"{Y}(Server ist ausgeschaltet. Erst einschalten, z. B.: ipmitool -I lanplus -H bmc-{name} -U admin -P admin chassis power on){X}")
        return
    rc = os.path.join(core.VAR, f"login_{name}.rc")
    with open(rc, "w") as f:
        f.write(f"""[ -f ~/.bashrc ] && . ~/.bashrc
export LAB_SERVER={name}
export PATH={os.path.join(core.BASE, "bin", "inband")}:$PATH
export PS1='\\[\\033[1;31m\\]root@{name}\\[\\033[0m\\]:\\w# '
alias fieldiag.sh=fieldiag
echo
echo "Eingeloggt auf {name} ({s['modell']}). Werkzeuge: nvidia-smi, fieldiag, dmesg, lspci, nvme list, storcli64, ipmitool"
echo "Zurück mit: exit"
echo
""")
    os.execvp("bash", ["bash", "--rcfile", rc, "-i"])


def cmd_karte(a):
    if not a or a[0] not in core.SERVERS:
        print("Welcher Server? z. B.: lab karte gpu01")
        return
    st = core.lesen()
    s = st["servers"][a[0]]
    m = core.MODELS[s["modell"]]
    print(f"{B}Service-Karte {s['modell']} ({a[0]}) – Lab-Version{X}\n")
    if m["gpus"]:
        print(f"{B}GPUs{X}  (Index = Slot-Nummer am Gehäuse)")
        for i in range(m["gpus"]):
            print(f"  GPU{i}  Slot {i}  PCIe-Bus {['18', '2A', '3A', '5D', '9A', 'AB', 'BA', 'DB'][i]}:00.0  "
                  f"{'SXM-Modul auf HGX-Board' if m['typ'] == 'hgx' else 'PCIe-Karte im Riser'}")
    if m["nvswitch"]:
        print(f"\n{B}HGX-Baseboard{X}: 4 NVSwitches (NVSwitch0-3) fest verlötet -> Tausch nur als komplettes Baseboard (lab tausch {a[0]} baseboard 0)")
    print(f"\n{B}DIMMs{X}  (Index für 'lab tausch')")
    namen = [d["name"] for d in s["comp"]["dimm"]]
    for i in range(0, len(namen), 4):
        print("  " + "   ".join(f"{j:>2}={namen[j]}" for j in range(i, min(i + 4, len(namen)))))
    print(f"\n{B}Netzteile{X}: PSU0–PSU{m['psus'] - 1} (Rückseite, von links)   {B}Lüfter{X}: FAN0–FAN{m['fans'] - 1} (von links, Frontansicht)")
    if m["nvme"]:
        print(f"{B}NVMe{X}: NVMe0–NVMe{m['nvme'] - 1} (Front-Einschübe)")
    if m["hdd"]:
        print(f"{B}HDD{X}: Slot 0–{m['hdd'] - 1} (Front), RAID-Controller Enclosure 252")
    print(f"\n{B}Hot-Swap{X}: Netzteile, Lüfter, NVMe, HDD.  {B}Nur ausgeschaltet{X}: GPU, DIMM, Baseboard.")


def cmd_tausch(a):
    if len(a) < 3:
        print("So geht's: lab tausch <server> <teil> <nr>   z. B. lab tausch gpu01 gpu 3")
        return
    srv, teil, nr = a[0], a[1].lower(), a[2]
    with core.zustand() as st:
        if srv not in st["servers"]:
            print(f"Unbekannter Server: {srv}")
            return
        s = st["servers"][srv]
        if teil not in s["comp"] or not s["comp"][teil]:
            print(f"Dieser Server hat kein Bauteil '{teil}'.")
            return
        liste = s["comp"][teil]
        if teil == "dimm" and not nr.isdigit():
            treffer = [i for i, d in enumerate(liste) if d["name"].lower() == nr.lower()]
            if not treffer:
                print(f"DIMM '{nr}' gibt es nicht. Siehe: lab karte {srv}")
                return
            idx = treffer[0]
        else:
            try:
                idx = int(nr)
                liste[idx]
            except (ValueError, IndexError):
                print(f"Nummer {nr} gibt es nicht ({teil} 0–{len(liste) - 1}).")
                return
        if teil not in core.HOTSWAP and s["power"] == "on":
            print(f"{R}Stopp!{X} {core.TEIL_NAMEN[teil]} ist nicht hot-swap-fähig. Server erst ausschalten "
                  f"(BMC-Web oder: ipmitool -I lanplus -H bmc-{srv} -U admin -P admin chassis power off).")
            return
        alt = liste[idx]
        alt_sn, alt_status = alt["sn"], alt["status"]
        neu_sn = core.sn(alt_sn[:2], len(alt_sn) - 2)
        alt["sn"] = neu_sn
        if teil == "hdd":
            alt["status"] = "rebuild"
            alt["rebuild_start"] = core.now()
        else:
            alt["status"] = "ok"
            if teil == "baseboard":
                alt["nvswitch_bad"] = None
        label = alt.get("name") or f"{teil.upper()}{idx}"
        tk = st.get("ticket")
        eintrag = {"t": core.now(), "server": srv, "teil": teil, "idx": idx, "label": label,
                   "alt_sn": alt_sn, "neu_sn": neu_sn, "war_defekt": alt_status != "ok"}
        if tk and not tk["abgeschlossen"]:
            tk["tausch"].append(eintrag)
            core.aktion(st, f"{label} getauscht ({alt_sn} -> {neu_sn})")
        core.sel_add(s, "Entity Presence", f"{label}_Presence", "Device Inserted/Device Present")
        core.markiere_fix(st)
    print(f"{G}{label} getauscht.{X} Altteil SN {alt_sn} -> Neuteil SN {neu_sn}")
    if teil == "hdd":
        print("Rebuild läuft automatisch (ca. 2 Minuten). Fortschritt: storcli64 /c0/eall/sall show rebuild")
    if teil not in core.HOTSWAP:
        print("Server danach wieder einschalten und testen.")
    if tk and alt_status == "ok":
        print(f"{Y}Hinweis: Das ausgebaute Teil zeigte keinen Fehler.{X}")


TIPPS = {
    "gpu_offbus": ["Wie viele GPUs zeigt nvidia-smi -L? Wie viele müssten es sein?",
                   "dmesg | grep -i xid  – Xid 79 heißt 'fallen off the bus'. Welche PCI-Adresse?",
                   "Mit 'lab karte <server>' die Bus-ID einem Slot zuordnen. Dann: Server aus, GPU tauschen, an, fieldiag."],
    "gpu_remap_fail": ["nvidia-smi -q zeigt pro GPU den Abschnitt 'Remapped Rows'.",
                       "'Remapping Failure Occurred: Yes' = GPU muss getauscht werden. fieldiag bestätigt das.",
                       "Server aus, betroffene GPU tauschen, einschalten, fieldiag --level2."],
    "gpu_remap_pending": ["nvidia-smi -q: Steht bei 'Remapped Rows' Pending: Yes oder Remapping Failure: Yes?",
                          "Pending heißt: Das Remapping wird beim nächsten GPU-Reset aktiv. Nicht jedes Ticket braucht ein Teil!",
                          "nvidia-smi -r -i <nr> (oder Power-Cycle), danach fieldiag. Das Ersatzteil bleibt unbenutzt."],
    "nvswitch": ["fieldiag --level2 und nvidia-smi nvlink -s zeigen, welche Links fehlen.",
                 "dmesg | grep -i sxid  – die Fehler kommen von einem NVSwitch.",
                 "NVSwitches sind aufs HGX-Baseboard gelötet: Server aus, 'lab tausch <server> baseboard 0', an, fieldiag."],
    "dimm_ue": ["Schau ins SEL: ipmitool ... sel elist",
                "Der Sensorname im SEL nennt den DIMM-Steckplatz (z. B. CPU0_C3D0).",
                "Server aus, 'lab tausch <server> dimm <name>', an, SEL prüfen und danach löschen."],
    "psu_fail": ["ipmitool ... sdr elist | grep PSU",
                 "Welches PSU*_Status steht nicht auf ok? Netzteile sind hot-swap.",
                 "'lab tausch <server> psu <nr>', danach Sensoren prüfen und SEL löschen."],
    "fan_fail": ["ipmitool ... sdr elist | grep FAN – welcher Lüfter steht auf 0 RPM?",
                 "Lüfter sind hot-swap, der Server darf an bleiben.",
                 "'lab tausch <server> fan <nr>', Drehzahlen prüfen, SEL löschen."],
    "nvme_fail": ["lab login <server>, dann 'nvme list' und 'dmesg | grep nvme'.",
                  "Welche nvmeX fehlt in nvme list? Die BMC-Sensoren NVMe*_Status zeigen es auch.",
                  "NVMe ist hot-swap: 'lab tausch <server> nvme <nr>', danach nvme list prüfen."],
    "raid_disk": ["lab login <server>, dann storcli64 /c0 show",
                  "Welche Platte steht auf 'Fail'? Die Slot-Nummer steht bei EID:Slt.",
                  "Hot-swap: 'lab tausch <server> hdd <slot>', dann Rebuild abwarten (storcli64 /c0/eall/sall show rebuild)."],
    "fw_update": ["Aktuellen Stand prüfen: BMC-Web 'Firmware' oder ipmitool ... mc info. Depot: 'lab firmware'.",
                  "Prüfsummen kontrollieren (sha256sum + SHA256SUMS). Ein Image im Depot ist kaputt!",
                  "Reihenfolge: zuerst BMC (Host an), BMC-Neustart abwarten, dann Host aus, BIOS flashen, Host an."],
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
    sz = tk["szenario"]
    core.markiere_fix(st)
    punkte = []  # (ok, text, abzug) ; abzug>0 nur wenn nicht ok
    behoben = core.fehler_behoben(st)
    if sz == "raid_disk" and any(d["status"] == "rebuild" for d in s["comp"]["hdd"]):
        punkte.append((False, "RAID-Rebuild läuft noch – erst abschließen, wenn das Volume 'Optl' ist", 0))
    else:
        punkte.append((behoben, "Fehler behoben", 40))
    punkte.append((s["power"] == "on", "Server läuft wieder (Power on)", 10))
    if sz.startswith("gpu") or sz == "nvswitch":
        vorher = any(not f["pass"] and (not tk["fix_zeit"] or f["t"] < tk["fix_zeit"]) for f in tk["fieldiag"])
        nachher = tk["fix_zeit"] and any(f["pass"] and f["t"] >= tk["fix_zeit"] for f in tk["fieldiag"])
        punkte.append((vorher, "Fehler vor der Reparatur mit fieldiag nachgewiesen", 5))
        punkte.append((bool(nachher), "fieldiag nach der Reparatur: PASS", 20))
    if sz != "fw_update":
        punkte.append((tk["sel_geloescht_nach_fix"], "SEL nach der Reparatur gelöscht", 5))
        if tk.get("sel_zu_frueh"):
            punkte.append((False, "SEL VOR der Reparatur gelöscht – Fehlerspuren vernichtet", 10))
    for f in tk.get("fw_fehler", []):
        if "verification" in f:
            punkte.append((False, "Beschädigtes Image hochgeladen – Prüfsumme vorher kontrollieren", 5))
            break
    for t in tk["tausch"]:
        richtig = (t["server"] == tk["server"] and t["teil"] == tk["teil"] and t["idx"] == tk["idx"])
        if sz == "gpu_remap_pending" and richtig:
            punkte.append((False, f"{t['label']} unnötig getauscht – ein GPU-Reset hätte gereicht", 20))
        elif not richtig:
            grund = "nicht im Ticket" if t["teil"] != tk["teil"] or t["server"] != tk["server"] else "falscher Steckplatz"
            punkte.append((False, f"{t['label']} auf {t['server']} getauscht ({grund} – nur die Teile aus dem Ticket tauschen)", 15))
    if tk["tipps"]:
        punkte.append((False, f"{tk['tipps']} Tipp(s) genutzt", 5 * tk["tipps"]))
    score = max(0, 100 - sum(ab for ok, _, ab in punkte if not ok))
    pflicht = {"Fehler behoben", "Server läuft wieder (Power on)", "fieldiag nach der Reparatur: PASS"}
    fertig = all(ok for ok, txt, ab in punkte if txt in pflicht or ab == 0)
    return punkte, score, fertig


def cmd_pruefen(a):
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        punkte, score, fertig = bewerten(st)
    print(f"{B}Prüfung {tk['nr']}{X}")
    for ok, txt, ab in punkte:
        print(f"  {G + '✔' + X if ok else R + '✘' + X} {txt}" + ("" if ok or not ab else f"  ({Y}-{ab}{X})"))
    print(f"\nPunkte bisher: {B}{score}/100{X}")
    print(f"{G}Bereit zum Abschluss: lab abschluss{X}" if fertig else f"{Y}Noch nicht fertig.{X}")


def cmd_abschluss(a):
    with core.zustand() as st:
        tk = st.get("ticket")
        if not tk or tk["abgeschlossen"]:
            print("Kein offenes Ticket.")
            return
        punkte, score, fertig = bewerten(st)
        if not fertig and "--trotzdem" not in a:
            print(f"{Y}Das Ticket ist noch nicht fertig.{X} Schau mit 'lab pruefen' nach. "
                  "(Abmelden ohne Lösung: lab abschluss --trotzdem)")
            return
        s = st["servers"][tk["server"]]
        tk["abgeschlossen"] = True
        tk["score"] = score
        st["verlauf"].append({"nr": tk["nr"], "case": tk["case"], "titel": tk["titel"], "server": tk["server"],
                              "score": score, "t": core.now(), "geloest": fertig})
        text = checkliste(tk, s, punkte, score)
    os.makedirs(os.path.join(core.VAR, "abschluesse"), exist_ok=True)
    pfad = os.path.join(core.VAR, "abschluesse", f"{tk['case']}.txt")
    with open(pfad, "w") as f:
        f.write(text)
    print(text)
    print(f"Gespeichert unter: {pfad}")


def checkliste(tk, s, punkte, score):
    getauscht = [t for t in tk["tausch"]]
    fd = [f for f in tk["fieldiag"]]
    letzte_fd = ("PASS" if fd[-1]["pass"] else "FAIL") + f" (Level {fd[-1]['level']})" if fd else "nicht durchgeführt"
    w = 70
    z = []
    z.append("=" * w)
    titel = "MAINTENANCE PROCESS CHECKLIST (Lab)"
    case = f"CASE: {tk['case']}"
    z.append(titel + " " * (w - len(titel) - len(case)) + case)
    z.append("=" * w)
    z.append(f"Ticket:        {tk['nr']}            Kunde: {tk['kunde']}")
    z.append(f"Server:        {s['modell']}  SN {s['sn']}  Rack {s['rack']}")
    z.append(f"Fehlerbild:    {tk['titel']}")
    z.append(f"Datum:         {datetime.now():%d.%m.%Y %H:%M}")
    z.append("-" * w)
    if getauscht:
        for t in getauscht:
            z.append(f"Getauscht:     {t['label']:<12} alt SN {t['alt_sn']}  ->  neu SN {t['neu_sn']}")
    else:
        z.append("Getauscht:     kein Teil (Ersatzteil unbenutzt zurück)")
    z.append(f"Firmware:      BMC {s['fw']['BMC']}  BIOS {s['fw']['BIOS']}")
    z.append(f"Funktionstest: fieldiag {letzte_fd}" if s["comp"]["gpu"] else "Funktionstest: Sensoren/SEL ok")
    z.append("-" * w)
    auto = [
        (True, "Ticket gelesen, Server anhand SN identifiziert"),
        (any("fieldiag" in x["text"] or "SEL" in x["text"] for x in tk["aktionen"]) or bool(fd), "Fehler vor der Reparatur verifiziert"),
        (bool(getauscht) or tk["szenario"] in ("gpu_remap_pending", "fw_update"), "Reparatur durchgeführt"),
        (s["power"] == "on", "Server wieder eingeschaltet"),
        (tk["sel_geloescht_nach_fix"] or tk["szenario"] == "fw_update", "SEL nach Reparatur gelöscht"),
    ]
    for ok, txt in auto:
        z.append(f"[{'x' if ok else ' '}] {txt}")
    z.append("[ ] ESD-Schutz benutzt                        (selbst abhaken)")
    z.append("[ ] Altteil verpackt, Retourenlabel           (selbst abhaken)")
    if "Tencent" in tk["kunde"]:
        z.append("[ ] Rotes Klebeband am Altteil + Fotos        (Tencent!)")
    z.append("[ ] Fotos: Typenschild, Altteil, Neuteil      (selbst abhaken)")
    z.append("-" * w)
    z.append(f"Bewertung: {score}/100")
    for ok, txt, ab in punkte:
        if not ok:
            z.append(f"  - {txt}" + (f" (-{ab})" if ab else ""))
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
    schnitt = sum(v["score"] for v in st["verlauf"]) / len(st["verlauf"])
    print(f"\nDurchschnitt: {schnitt:.0f} Punkte aus {len(st['verlauf'])} Tickets")


def cmd_firmware(a):
    core.fw_depot_erzeugen()
    print(f"{B}Firmware-Depot{X}: {core.FW_DIR}   (Web: http://{eigene_ip()}:8000/firmware/)\n")
    for f in sorted(os.listdir(core.FW_DIR)):
        print(f"  {f}")
    print(f"\nPrüfen:     cd {core.FW_DIR} && sha256sum -c SHA256SUMS")
    print("Update per Redfish, z. B.:")
    print(f"  curl -u admin:admin -F UpdateFile=@{core.FW_DIR}/NF5688M7_BMC_{core.FW_TARGET['BMC']}.bin "
          "http://bmc-gpu01:8001/redfish/v1/UpdateService/upload")
    print("Oder im Browser über die BMC-Weboberfläche (Firmware Update).")


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
           "server": cmd_server, "login": cmd_login, "karte": cmd_karte, "tausch": cmd_tausch,
           "firmware": cmd_firmware, "reset": cmd_reset, "update": cmd_update}
