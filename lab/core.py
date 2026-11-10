"""Inspur-Lab: gemeinsamer Zustand, Server-Katalog, Bootablauf und Fehler-Szenarien.

Alles ist simuliert. Modellnamen dienen nur der Orientierung; Seriennummern, Versionen,
Fehlercodes, Kunden und Tickets sind frei erfunden. Die Abläufe folgen den Inspur-Anleitungen
aus Ticket Kompakt (fieldiag, ECC-Kriterium, AI-EasyCfg, nvflash, DIMM, Firmware, Abnahme).
"""
import contextlib
import fcntl
import hashlib
import json
import os
import random
import string
import time
from datetime import datetime

BASE = os.environ.get("LAB_HOME", "/opt/inspur-lab")
VAR = os.environ.get("LAB_VAR", "/var/lib/inspur-lab")
STATE_FILE = os.path.join(VAR, "state.json")
LOCK_FILE = os.path.join(VAR, "state.lock")
FW_DIR = os.path.join(VAR, "firmware")
FAST = os.environ.get("LAB_FAST") == "1"  # Tests: keine Wartezeiten

BMC_USER, BMC_PASS = "admin", "admin"
POST_DAUER = 1 if FAST else 25

# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------

MODELS = {
    "NF5688M6": {
        "typ": "hgx", "beschreibung": "8-GPU HGX A100 (SXM4, NVSwitch)",
        "gpus": 8, "gpu_name": "NVIDIA A100-SXM4-80GB", "gpu_kurz": "GA100 [A100 SXM4 80GB]", "gpu_pn": "699-2G506-0212-300",
        "vbios": "92.00.45.00.06", "vbios_neu": "92.00.56.00.03", "nvswitch": 6, "nvlinks": 12, "pcie": "Gen4 x16",
        "fd_paket": "629-24287-0004-FLD-38440", "fd_sku": "HGX A100 8-GPU (P1001)", "polling": True,
        "dimm_dpc": 2, "psus": 6, "fans": 6, "nvme": 8, "hdd": 0,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "ME": "4.4.4.301", "CPLD": "1.0.5", "PSU": "1.5.3", "HGX_FW": "92.00.45"},
    },
    "NF5468M6": {
        "typ": "pcie", "beschreibung": "4-GPU PCIe (A30)",
        "gpus": 4, "gpu_name": "NVIDIA A30", "gpu_kurz": "GA100GL [A30 PCIe]", "gpu_pn": "699-2G200-0220-200",
        "vbios": "92.00.66.00.04", "vbios_neu": "92.00.3C.00.01", "nvswitch": 0, "nvlinks": 0, "pcie": "Gen4 x16",
        "fd_paket": "629-20530-0001-FLD-38221", "fd_sku": "A30 PCIe (P1001)", "polling": False,
        "dimm_dpc": 1, "psus": 4, "fans": 8, "nvme": 4, "hdd": 0,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "ME": "4.4.4.301", "CPLD": "1.0.3", "PSU": "1.5.1"},
    },
    "NF5280M7": {
        "typ": "std", "beschreibung": "2U-Standardserver mit RAID",
        "gpus": 0, "gpu_name": "", "gpu_kurz": "", "gpu_pn": "", "vbios": "", "vbios_neu": "",
        "nvswitch": 0, "nvlinks": 0, "pcie": "", "fd_paket": "", "fd_sku": "", "polling": False,
        "dimm_dpc": 1, "psus": 2, "fans": 6, "nvme": 0, "hdd": 8,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "ME": "6.1.4.47", "CPLD": "1.0.2", "PSU": "1.2.0", "RAID": "52.26.0-5014"},
    },
}

# freigegebene Ziel-Firmware (Kundenvorgabe) und neuere, NICHT freigegebene Stände im Depot
FW_TARGET = {"BMC": "4.32.1", "BIOS": "03.05.00"}
FW_NEUER = {"BMC": "4.35.0", "BIOS": "03.07.00"}

SERVERS = {
    "gpu01": {"modell": "NF5688M6", "port": 8001, "rack": "A12 U20-U25"},
    "gpu02": {"modell": "NF5468M6", "port": 8002, "rack": "A12 U14-U17"},
    "std01": {"modell": "NF5280M7", "port": 8003, "rack": "A12 U10-U11"},
}

HOTSWAP = {"psu", "fan", "nvme", "hdd"}
TEIL_NAMEN = {"gpu": "GPU", "dimm": "DIMM", "psu": "Netzteil (PSU)", "fan": "Lüftermodul",
              "nvme": "NVMe-SSD", "hdd": "HDD", "baseboard": "HGX-Baseboard"}
BIOS_STANDARD = {"vtd": "Enabled", "sriov": "Disabled", "bootmode": "UEFI", "acloss": "Last State"}
GPU_BUS = ["18", "2a", "3a", "5d", "9a", "ab", "ba", "db"]
NVSWITCH_BUS = ["c5", "c6", "c7", "c8", "c9", "ca"]

SZENARIEN = {
    "gpu_offbus": {"typen": ["hgx", "pcie"], "teil": "gpu", "titel": "GPU nicht mehr erkannt",
                   "meldung": ["Kunde meldet: nvidia-smi zeigt eine GPU nicht mehr, Jobs brechen ab.",
                               "IDL: PCIe Bus Uncorrectable Error an einem GPU-Slot. GPU im OS nicht mehr sichtbar."],
                   "vorschlag": "GPU neu stecken, ggf. tauschen, danach fieldiag."},
    "gpu_remap_fail": {"typen": ["hgx", "pcie"], "teil": "gpu", "titel": "GPU-ECC: Row Remapping Failure",
                       "meldung": ["Kunde meldet: Xid 64 (Row remapping failure) auf einer GPU, Jobs brechen ab.",
                                   "Kunde meldet: Uncorrectable ECC auf einer GPU, Fehler kommt nach Neustart wieder."],
                       "vorschlag": "ECC-Prüfung nach NVIDIA-Kriterium, ggf. GPU tauschen, VBIOS angleichen, fieldiag."},
    "gpu_remap_pending": {"typen": ["hgx", "pcie"], "teil": "gpu", "titel": "GPU-ECC-Fehler (Tausch angefragt)",
                          "meldung": ["Kunde meldet: Xid 94/63 auf einer GPU. Bitte prüfen und GPU tauschen."],
                          "vorschlag": "GPU tauschen."},
    "nvswitch": {"typen": ["hgx"], "teil": "baseboard", "titel": "NVLink/NVSwitch-Fehler",
                 "meldung": ["Kunde meldet: nvidia-fabricmanager startet nicht, NCCL-Tests schlagen fehl.",
                             "Kunde meldet: NVLink-Bandbreite eingebrochen, ein NVSwitch fehlt."],
                 "vorschlag": "HGX-Baseboard tauschen, danach fieldiag."},
    "dimm_ue": {"typen": ["hgx", "pcie", "std"], "teil": "dimm", "titel": "Speicherfehler (Uncorrectable ECC)",
                "meldung": ["IDL: Uncorrectable ECC auf einem DIMM, Server ist abgestürzt.",
                            "Kunde meldet: Speicherfehler im BMC, CATERR."],
                "vorschlag": "DIMM neu stecken, ggf. tauschen."},
    "psu_fail": {"typen": ["hgx", "pcie", "std"], "teil": "psu", "titel": "Netzteilfehler",
                 "meldung": ["IDL: Power Supply Failure, Redundanz verloren.",
                             "Kunde meldet: Alarm am Netzteil, Redundancy Lost."],
                 "vorschlag": "Netzteil prüfen, ggf. tauschen."},
    "fan_fail": {"typen": ["hgx", "pcie", "std"], "teil": "fan", "titel": "Lüfterausfall",
                 "meldung": ["IDL: Lüfter unter Lower Critical, Temperatur steigt."],
                 "vorschlag": "Lüftermodul tauschen."},
    "nvme_fail": {"typen": ["hgx", "pcie"], "teil": "nvme", "titel": "NVMe-SSD ausgefallen",
                  "meldung": ["Kunde meldet: Eine NVMe-SSD ist aus dem System verschwunden.",
                              "IDL: Drive Fault an einem NVMe-Slot."],
                  "vorschlag": "NVMe tauschen."},
    "raid_disk": {"typen": ["std"], "teil": "hdd", "titel": "RAID degraded – Festplatte ausgefallen",
                  "meldung": ["Kunde meldet: RAID-Volume degraded, eine Platte ausgefallen."],
                  "vorschlag": "Platte tauschen, Rebuild prüfen."},
    "sn_mismatch": {"typen": ["std"], "teil": "hdd", "titel": "Festplatte tauschen (Slot laut Kunde)",
                    "meldung": ["Kunde meldet: Platte defekt, bitte tauschen."],
                    "vorschlag": "Platte im genannten Slot tauschen."},
    "fw_update": {"typen": ["hgx", "pcie", "std"], "teil": None, "titel": "Firmware-Update (Kundenvorgabe)",
                  "meldung": ["Wartungsauftrag: BMC auf {bmc} und BIOS auf {bios} bringen (freigegebene Stände laut Kunde)."],
                  "vorschlag": "Stände vorher/nachher dokumentieren, HPM-Update."},
}

# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------


def now():
    return time.time()


def ts(t=None):
    return datetime.fromtimestamp(t or now()).strftime("%m/%d/%Y | %H:%M:%S")


def sn(prefix, n=10):
    return prefix + "".join(random.choice(string.digits + "ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(n))


def dimm_namen(dpc):
    return [f"CPU{c}_C{ch}D{d}" for c in (0, 1) for ch in range(8) for d in range(dpc)]


def neuer_server(name):
    info = SERVERS[name]
    m = MODELS[info["modell"]]
    s = {
        "name": name, "modell": info["modell"], "typ": m["typ"], "port": info["port"], "rack": info["rack"],
        "sn": sn("2207", 8), "power": "on", "power_since": now() - 3 * 86400, "boot_id": 1,
        "phase": "os", "os": "prod", "setup_req": False, "bootmenu_req": False,
        "bmc_boot_until": 0, "fw": dict(m["fw"]), "bios": dict(BIOS_STANDARD),
        "drv": True, "persistenced": True, "polling": True, "gpu_wp": True, "stick": False,
        "comp": {
            "gpu": [{"sn": sn("165", 10), "status": "ok", "vbios": m["vbios"], "vbios_pending": None,
                     "ecc_vol": 0, "ecc_agg": 0, "remap_corr": 0, "remap_unc": 0, "lost_boot": 0, "ursache": None}
                    for _ in range(m["gpus"])],
            "baseboard": [{"sn": sn("1653", 9), "status": "ok", "nvswitch_bad": None}] if m["nvswitch"] else [],
            "dimm": [{"name": n, "sn": sn("8A", 8), "status": "ok", "ursache": None} for n in dimm_namen(m["dimm_dpc"])],
            "psu": [{"sn": sn("PS", 10), "status": "ok", "ursache": None} for _ in range(m["psus"])],
            "fan": [{"sn": sn("FN", 8), "status": "ok", "ursache": None} for _ in range(m["fans"])],
            "nvme": [{"sn": sn("S6", 10), "status": "ok"} for _ in range(m["nvme"])],
            "hdd": [{"sn": sn("ZL", 8), "status": "ok", "rebuild_start": 0} for _ in range(m["hdd"])],
        },
        "sel": [], "sel_next": 1, "tasks": [],
    }
    sel_add(s, "Event Logging Disabled", "SEL_Status", "Log area reset/cleared", "Asserted")
    return s


def neuer_zustand():
    return {"version": 2, "servers": {n: neuer_server(n) for n in SERVERS}, "ticket": None,
            "verlauf": [], "erstellt": now()}


def sel_add(s, sensor_typ, sensor, event, richtung="Asserted"):
    s["sel"].append({"id": s["sel_next"], "t": now(), "typ": sensor_typ, "sensor": sensor,
                     "event": event, "dir": richtung})
    s["sel_next"] += 1


# --------------------------------------------------------------------------
# Zustand mit Dateisperre
# --------------------------------------------------------------------------


@contextlib.contextmanager
def zustand(schreiben=True):
    os.makedirs(VAR, exist_ok=True)
    with open(LOCK_FILE, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX if schreiben else fcntl.LOCK_SH)
        try:
            st = None
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    st = json.load(f)
                if st.get("version") != 2:
                    st = None  # alte Lab-Version: frisch anfangen
            if st is None:
                st = neuer_zustand()
            fortschreiben(st)
            yield st
            if schreiben:
                tmp = STATE_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(st, f, indent=1)
                os.replace(tmp, STATE_FILE)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def lesen():
    with zustand() as st:  # mit Schreibsperre, damit fertige Vorgaenge gespeichert werden
        return st


def fortschreiben(st):
    t = now()
    for s in st["servers"].values():
        for task in s.get("tasks", []):
            if task["state"] == "Running":
                dauer = 1 if FAST else task["dauer"]
                task["pct"] = min(100, int((t - task["start"]) / dauer * 100))
                if task["pct"] >= 100:
                    ende = task["start"] + dauer
                    task["state"] = "Completed"
                    s["fw"][task["komponente"]] = task["version"]
                    sel_add(s, "Version Change", "FW_Update", f"{task['komponente']} firmware updated to {task['version']}")
                    s["sel"][-1]["t"] = ende
                    if task["komponente"] == "BMC":
                        s["bmc_boot_until"] = ende + (1 if FAST else 45)
        for i, d in enumerate(s["comp"]["hdd"]):
            if d["status"] == "rebuild" and t - d["rebuild_start"] > (1 if FAST else 120):
                d["status"] = "ok"
                sel_add(s, "Drive Slot", f"HDD{i}_Status", "Rebuild/Remap finished", "Deasserted")
        if s["power"] == "on" and s["phase"] == "post" and t - s["power_since"] >= POST_DAUER:
            post_ende(st, s)


def bmc_bereit(s):
    return now() >= s.get("bmc_boot_until", 0)


def rebuild_pct(d):
    if d["status"] != "rebuild":
        return 100
    return min(99, int((now() - d["rebuild_start"]) / (1 if FAST else 120) * 100))


# --------------------------------------------------------------------------
# Boot-Ablauf: POST -> (Setup) -> (Bootmenue) -> OS / AI PE / kein Bootgeraet
# --------------------------------------------------------------------------


def boot_einstellung(s):
    return s.get("settings", {}).get("boot", {"device": "Hard Disk", "persist": "Once"})


def pe_quelle(s):
    """Ist ein bootfaehiges AI-PE-Medium da? (USB-Stick oder per Virtual Media gemountetes ISO)"""
    vm = s.get("settings", {}).get("vmedia", {})
    iso = vm.get("mounted") and "ai" in vm.get("image", "").lower() and vm.get("image", "").lower().endswith((".iso", ".iso.xz"))
    return s.get("stick") or bool(iso)


def boot_ziele(s):
    ziele = ["UEFI OS (Ubuntu) – NVMe0"]
    if s.get("stick"):
        ziele.append("UEFI: USB SanDisk 3.2Gen1, Partition 1 (AI PE)")
    vm = s.get("settings", {}).get("vmedia", {})
    if vm.get("mounted"):
        ziele.append("UEFI: AMI Virtual CDROM0 (" + os.path.basename(vm.get("image", "")) + ")")
    ziele += ["UEFI: PXE IPv4 Intel(R) Ethernet", "Enter Setup"]
    return ziele


def post_ende(st, s):
    if s.get("setup_req"):
        s["phase"] = "setup"
        return
    if s.get("bootmenu_req"):
        s["phase"] = "bootmenu"
        return
    b = boot_einstellung(s)
    dev = b.get("device", "Hard Disk")
    if b.get("persist", "Once") == "Once" and dev != "Hard Disk" and "settings" in s:
        s["settings"]["boot"]["device"] = "Hard Disk"
    if dev == "BIOS Setup":
        s["phase"] = "setup"
    elif dev in ("USB", "CD/DVD") and pe_quelle(s):
        booten(st, s, "pe")
    else:
        booten(st, s, "prod")


def booten(st, s, ziel):
    if ziel == "setup":
        s["phase"] = "setup"
        return
    if ziel == "prod" and s["bios"]["bootmode"] != "UEFI":
        s["phase"] = "noboot"  # OS ist im UEFI-Modus installiert
        return
    s["phase"], s["os"] = "os", ziel
    s["drv"], s["persistenced"] = True, True
    s.pop("mods", None)
    sel_add(s, "OS Boot", "OS_Status", "OS boot completed" if ziel == "prod" else "Boot from removable media")


def boot_wahl(st, s, idx):
    ziele = boot_ziele(s)
    if not 0 <= idx < len(ziele):
        return
    z = ziele[idx]
    s["bootmenu_req"] = False
    if "AI PE" in z or "Virtual CDROM" in z:
        if "Virtual CDROM" in z and not pe_quelle(s):
            booten(st, s, "prod")
        else:
            booten(st, s, "pe")
    elif z == "Enter Setup":
        s["phase"] = "setup"
    else:
        booten(st, s, "prod")


def setup_verlassen(st, s, speichern, werte=None):
    if speichern and werte:
        alt = dict(s["bios"])
        for k, v in werte.items():
            if k in s["bios"]:
                s["bios"][k] = v
        if alt != s["bios"]:
            aktion(st, "BIOS-Einstellungen geändert: " + ", ".join(f"{k}={v}" for k, v in s["bios"].items() if alt[k] != v))
            eingriff(st)
    s["setup_req"] = False
    if speichern:  # Save & Exit = Neustart
        power(st, s, "reset", still=True)
        return
    if s.get("bootmenu_req"):
        s["phase"] = "bootmenu"
    else:
        s["phase"] = "post"
        s["power_since"] = now() - POST_DAUER + 0.01
        post_ende(st, s)


# --------------------------------------------------------------------------
# Sensoren
# --------------------------------------------------------------------------


def sensoren(s):
    an = s["power"] == "on"
    rnd = random.Random(int(now() / 10) + s["port"])
    out = []
    fan_defekt = any(f["status"] != "ok" for f in s["comp"]["fan"])
    out.append(("Inlet_Temp", rnd.randint(22, 25), "degrees C", "ok"))
    for c in (0, 1):
        v = rnd.randint(48, 62) + (9 if fan_defekt else 0) if an else None
        out.append((f"CPU{c}_Temp", v, "degrees C", "ok"))
    for i, g in enumerate(s["comp"]["gpu"]):
        v = None if not an or g["status"] == "offbus" else rnd.randint(34, 41) + (7 if fan_defekt else 0)
        out.append((f"GPU{i}_Temp", v, "degrees C", "ok" if v is not None or not an else "ns"))
    fan_cfg = s.get("settings", {}).get("fan", {})
    polling_aus = MODELS[s["modell"]]["polling"] and not s.get("polling", True)
    for i, f in enumerate(s["comp"]["fan"]):
        for r in ("F", "R"):
            if f["status"] != "ok":
                v = 0
            elif not an:
                v = rnd.randint(2400, 2800)
            elif polling_aus:
                v = rnd.randint(15800, 16200)  # BMC regelt nicht mehr -> 100 %
            elif fan_cfg.get("mode") == "Manual":
                soll = int(16000 * int(fan_cfg.get("duty", 60)) / 100)
                v = rnd.randint(soll - 150, soll + 150)
            else:
                v = rnd.randint(7800, 9600)
            out.append((f"FAN{i}_{r}_Speed", v, "RPM", "lcr" if v == 0 else "ok"))
    for i, p in enumerate(s["comp"]["psu"]):
        ok = p["status"] == "ok"
        out.append((f"PSU{i}_Status", 0x01 if ok else 0x03, "discrete", "ok" if ok else "cr"))
        out.append((f"PSU{i}_PIn", (rnd.randint(380, 520) if s["typ"] != "hgx" else rnd.randint(900, 1400)) if ok and an else 0, "Watts", "ok"))
    for i, d in enumerate(s["comp"]["nvme"]):
        out.append((f"NVMe{i}_Status", 0x01 if d["status"] == "ok" else 0x02, "discrete", "ok" if d["status"] == "ok" else "cr"))
    for i, d in enumerate(s["comp"]["hdd"]):
        stt = {"ok": 0x01, "fail": 0x02, "rebuild": 0x81}[d["status"]]
        out.append((f"HDD{i}_Status", stt, "discrete", "cr" if d["status"] == "fail" else "ok"))
    out.append(("P12V", 12.1 if an else 0.0, "Volts", "ok"))
    return out


# --------------------------------------------------------------------------
# Firmware-Depot
# --------------------------------------------------------------------------


def fw_images():
    imgs = []
    for modell in MODELS:
        for k in ("BMC", "BIOS"):
            imgs.append((f"{modell}_{k}_{FW_TARGET[k]}.hpm", modell, k, FW_TARGET[k], True))
            imgs.append((f"{modell}_{k}_{FW_NEUER[k]}.hpm", modell, k, FW_NEUER[k], True))
    imgs.append((f"NF5688M6_BIOS_{FW_TARGET['BIOS']}_mirror.hpm", "NF5688M6", "BIOS", FW_TARGET["BIOS"], False))
    return imgs


def fw_inhalt(modell, komp, version, gueltig=True, groesse=256 * 1024):
    kopf = f"INSPUR-LAB-HPM model={modell} comp={komp} ver={version}\n".encode()
    rnd = random.Random(f"{modell}{komp}{version}")
    daten = kopf + bytes(rnd.getrandbits(8) for _ in range(groesse))
    if not gueltig:
        daten = daten[:-4096] + b"\x00" * 4096  # abgebrochener Download
    return daten


def fw_referenz_hash(modell, komp, version):
    return hashlib.sha256(fw_inhalt(modell, komp, version)).hexdigest()


RELEASE_NOTES = f"""Firmware-Depot (Lab-Simulation, keine echte Firmware)
=====================================================
Freigegeben (Kundenvorgabe):  BMC {FW_TARGET['BMC']}   BIOS {FW_TARGET['BIOS']}
Neuere Stände im Depot ({FW_NEUER['BMC']} / {FW_NEUER['BIOS']}) sind NICHT freigegeben.
Kundenspezifische Server: nur die freigegebene FW, nicht einfach die neueste!

- Vorher Stände notieren (BMC-Startseite, component.log im Onekey-Log).
- BMC zuerst. Host darf an bleiben, BMC startet danach neu (~45 s).
- BIOS: Host muss AUS sein. Voraussetzung BMC >= {FW_TARGET['BMC']}.
- Image passt nur zum eigenen Modell (Dateiname).
- Vor dem Flashen Prüfsumme kontrollieren: sha256sum -c SHA256SUMS
- Nachher Stände erneut prüfen und mit Vorgabe vergleichen.
"""


def fw_depot_erzeugen():
    os.makedirs(FW_DIR, exist_ok=True)
    zeilen = []
    for name, modell, komp, ver, gueltig in fw_images():
        pfad = os.path.join(FW_DIR, name)
        if not os.path.exists(pfad):
            with open(pfad, "wb") as f:
                f.write(fw_inhalt(modell, komp, ver, gueltig))
        zeilen.append(f"{fw_referenz_hash(modell, komp, ver)}  {name}")
    with open(os.path.join(FW_DIR, "SHA256SUMS"), "w") as f:
        f.write("\n".join(zeilen) + "\n")
    with open(os.path.join(FW_DIR, "RELEASE-NOTES.txt"), "w") as f:
        f.write(RELEASE_NOTES)


def fw_pruefen(daten, modell, komp_erwartet=None):
    try:
        kopf = daten.split(b"\n", 1)[0].decode()
    except UnicodeDecodeError:
        return False, "Invalid image format", None, None
    if not kopf.startswith("INSPUR-LAB-HPM"):
        return False, "Invalid image format", None, None
    teile = dict(p.split("=", 1) for p in kopf.split()[1:])
    if teile.get("model") != modell:
        return False, f"Image model mismatch (image: {teile.get('model')}, board: {modell})", None, None
    komp, ver = teile.get("comp"), teile.get("ver")
    if komp_erwartet and komp != komp_erwartet:
        return False, f"Image type mismatch (image is {komp})", None, None
    if hashlib.sha256(daten).hexdigest() != fw_referenz_hash(modell, komp, ver):
        return False, "Image verification failed (signature/checksum error)", None, None
    return True, "OK", komp, ver


def fw_starten(s, komp, version):
    if komp == "BIOS":
        if s["power"] == "on":
            return False, "BIOS update requires host power off", None
        if tuple(map(int, s["fw"]["BMC"].split("."))) < tuple(map(int, FW_TARGET["BMC"].split("."))):
            return False, f"BIOS {version} requires BMC >= {FW_TARGET['BMC']}", None
    if any(t["state"] == "Running" for t in s["tasks"]):
        return False, "Another update is in progress", None
    task = {"id": len(s["tasks"]) + 1, "komponente": komp, "version": version, "start": now(),
            "dauer": 40 if komp == "BMC" else 60, "state": "Running", "pct": 0}
    s["tasks"].append(task)
    return True, "Update started", task


# --------------------------------------------------------------------------
# Strom, SEL, BMC (gemeinsam fuer ipmitool, Redfish, Web, KVM)
# --------------------------------------------------------------------------


def _beim_einschalten(s):
    """Was beim (Neu-)Start passiert: Remap wird angewendet, VBIOS aktiv, defekte Teile melden sich."""
    s["boot_id"] = s.get("boot_id", 1) + 1
    for i, g in enumerate(s["comp"]["gpu"]):
        if g["status"] == "remap_pending":
            g["status"] = "ok"  # Remapping wird beim Neustart/GPU-Reset ins InfoROM uebernommen
        if g.get("vbios_pending"):
            g["vbios"], g["vbios_pending"] = g["vbios_pending"], None
        g["ecc_vol"] = 0
        if g["status"] == "offbus":
            sel_add(s, "Critical Interrupt", f"PCIe_GPU{i}", "Bus Uncorrectable Error")
    for d in s["comp"]["dimm"]:
        if d["status"] == "ue":
            sel_add(s, "Memory", d["name"], "Memory Device Disabled Detail(MFR:NOTFOUND,PN:NOTFOUND,SN:NOTFOUND)")


def power(st, s, art, still=False):
    if art == "status":
        return True, f"Chassis Power is {s['power']}"
    if art == "on":
        if s["power"] != "on":
            s.update(power="on", power_since=now(), phase="post", setup_req=False, bootmenu_req=False)
            _beim_einschalten(s)
            sel_add(s, "System ACPI Power State", "ACPI_PWR", "S0/G0 working")
            if not still:
                aktion(st, f"{s['name']} eingeschaltet")
            eingriff(st)
        markiere_fix(st)
        return True, "Chassis Power Control: Up/On"
    if art in ("off", "soft"):
        if s["power"] != "off":
            s.update(power="off", phase="off")
            sel_add(s, "System ACPI Power State", "ACPI_PWR", "S5/G2 soft-off")
            if not still:
                aktion(st, f"{s['name']} ausgeschaltet")
            eingriff(st)
        return True, "Chassis Power Control: Down/Off"
    if art in ("cycle", "reset"):
        if s["power"] != "on":
            return False, "Set Chassis Power Control to Cycle failed: Command not supported in present state"
        s.update(power_since=now(), phase="post", setup_req=False, bootmenu_req=False)
        _beim_einschalten(s)
        sel_add(s, "System Restart", "SysRestart", "System Restart Cause: reset via power reset command")
        if not still:
            aktion(st, f"{s['name']} Neustart ({art})")
        eingriff(st)
        markiere_fix(st)
        return True, f"Chassis Power Control: {'Cycle' if art == 'cycle' else 'Reset'}"
    return False, f"Invalid chassis power command: {art}"


def sel_clear(st, s):
    s["sel"], s["sel_next"] = [], 1
    sel_add(s, "Event Logging Disabled", "SEL_Status", "Log area reset/cleared")
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"] and tk["server"] == s["name"]:
        if not tk.get("onekey"):
            tk["sel_ohne_log"] = True
        aktion(st, "SEL gelöscht")


def bmc_reset(st, s):
    s["bmc_boot_until"] = now() + (1 if FAST else 45)
    aktion(st, f"BMC {s['name']} neu gestartet")


def fw_fehler_merken(st, s, meldung):
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"] and tk["server"] == s["name"]:
        tk.setdefault("fw_fehler", []).append(meldung)
        aktion(st, f"Firmware-Update abgelehnt: {meldung}")


# --------------------------------------------------------------------------
# Ticket-Hilfen
# --------------------------------------------------------------------------


def aktion(st, text):
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"]:
        tk["aktionen"].append({"t": now(), "text": text})


def eingriff(st):
    """Erster Eingriff am Server (Strom, Teil, BIOS, Firmware) – fuer 'vorher notiert?'"""
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"] and not tk.get("eingriff"):
        tk["eingriff"] = now()


def notiert(st, server, was):
    """FW-Stände / FRU / Onekey wurden angesehen bzw. gesichert."""
    tk = st.get("ticket")
    if not tk or tk["abgeschlossen"] or tk["server"] != server:
        return
    vor = not tk.get("eingriff")
    if was == "onekey":
        tk.setdefault("onekey", []).append({"t": now(), "nach_fix": bool(tk.get("fix_zeit"))})
    elif vor:
        tk[f"{was}_vorher"] = True


def ticket_nr():
    return "STR-" + datetime.now().strftime("%Y") + "-" + "".join(random.choice(string.digits) for _ in range(6))


def case_nr():
    return "INS" + datetime.now().strftime("%Y%m%d") + "".join(random.choice(string.digits) for _ in range(4))


def szenario_starten(st, name=None, server=None):
    if name and name not in SZENARIEN:
        raise ValueError(f"Unbekanntes Szenario: {name}")
    kandidaten = [(sz, srv) for sz, d in SZENARIEN.items() if not name or sz == name
                  for srv, info in SERVERS.items() if (not server or srv == server)
                  and MODELS[info["modell"]]["typ"] in d["typen"]]
    if not kandidaten:
        raise ValueError("Diese Kombination aus Szenario und Server gibt es nicht.")
    sz, srv = random.choice(kandidaten)
    st["servers"][srv] = neuer_server(srv)
    s = st["servers"][srv]
    d = SZENARIEN[sz]
    teil, idx, detail, ursache, extra = d["teil"], None, "", "defekt", {}
    t = now()
    sel_add(s, "System Firmware", "BIOS_Boot_Up", "State Asserted")
    sel_add(s, "OS Boot", "OS_Status", "OS boot completed")
    n_vor = len(s["sel"])
    m = MODELS[s["modell"]]
    s["fehler_boot"] = s["boot_id"]

    if sz in ("gpu_offbus", "gpu_remap_fail", "gpu_remap_pending"):
        idx = random.randrange(len(s["comp"]["gpu"]))
        g = s["comp"]["gpu"][idx]
        detail = f"GPU{idx}"
        if sz == "gpu_offbus":
            ursache = random.choice(["kontakt", "defekt"])
            g.update(status="offbus", ursache=ursache, lost_boot=s["boot_id"])
            sel_add(s, "Critical Interrupt", f"PCIe_GPU{idx}", "Bus Uncorrectable Error")
            sel_add(s, "Critical Interrupt", f"PCIe_GPU{idx}", "Bus Fatal Error")
        else:
            g.update(status="remap_fail" if sz == "gpu_remap_fail" else "remap_pending",
                     ecc_vol=2, ecc_agg=2, remap_corr=0, remap_unc=1 if sz == "gpu_remap_pending" else 8)
    elif sz == "nvswitch":
        idx = 0
        bad = random.randrange(m["nvswitch"])
        s["comp"]["baseboard"][0].update(status="nvlink", nvswitch_bad=bad)
        detail = f"NVSwitch{bad}"
    elif sz == "dimm_ue":
        idx = random.randrange(len(s["comp"]["dimm"]))
        ursache = random.choice(["kontakt", "defekt"])
        dd = s["comp"]["dimm"][idx]
        dd.update(status="ue", ursache=ursache)
        for _ in range(3):
            sel_add(s, "Memory", dd["name"], "Correctable ECC")
        sel_add(s, "Memory", dd["name"], "Uncorrectable ECC")
        sel_add(s, "Processor", "CPU_CATERR", "IERR Occured")
        detail = dd["name"]
    elif sz == "psu_fail":
        idx = random.randrange(len(s["comp"]["psu"]))
        ursache = random.choice(["kabel", "defekt"])
        s["comp"]["psu"][idx].update(status="fail", ursache=ursache)
        sel_add(s, "Power Supply", f"PSU{idx}_Status", "Power Supply AC lost" if ursache == "kabel" else "Power Supply Failure detected")
        sel_add(s, "Power Unit", "PSU_Redundancy", "Redundancy Lost")
        detail = f"PSU{idx}"
    elif sz == "fan_fail":
        idx = random.randrange(len(s["comp"]["fan"]))
        ursache = random.choice(["kontakt", "defekt"])
        s["comp"]["fan"][idx].update(status="fail", ursache=ursache)
        sel_add(s, "Fan", f"FAN{idx}_F_Speed", "Lower Critical going low")
        sel_add(s, "Fan", f"FAN{idx}_R_Speed", "Lower Critical going low")
        detail = f"FAN{idx}"
    elif sz == "nvme_fail":
        idx = random.randrange(len(s["comp"]["nvme"]))
        s["comp"]["nvme"][idx]["status"] = "fail"
        sel_add(s, "Drive Slot", f"NVMe{idx}_Status", "Drive Fault")
        detail = f"NVMe{idx}"
    elif sz in ("raid_disk", "sn_mismatch"):
        idx = random.randrange(len(s["comp"]["hdd"]))
        s["comp"]["hdd"][idx]["status"] = "fail"
        sel_add(s, "Drive Slot", f"HDD{idx}_Status", "Hard Disk Drive Detected Fault")
        detail = f"HDD{idx}"
        if sz == "sn_mismatch":
            falsch = random.choice([i for i in range(len(s["comp"]["hdd"])) if i != idx])
            extra = {"ticket_slot": falsch, "ticket_sn": s["comp"]["hdd"][falsch]["sn"]}
    zeit = t - random.randint(2, 4) * 86400
    for i, e in enumerate(s["sel"]):
        if i == n_vor:
            zeit = t - random.randint(1, 6) * 3600
        e["t"] = zeit
        zeit += random.randint(600, 6 * 3600) if i < n_vor else random.randint(20, 600)

    meldung = random.choice(d["meldung"]).format(bmc=FW_TARGET["BMC"], bios=FW_TARGET["BIOS"])
    if sz == "sn_mismatch":
        meldung += f" Slot {extra['ticket_slot']}, Platten-SN {extra['ticket_sn']}."
    elif detail and sz not in ("gpu_offbus", "nvswitch") and random.random() < 0.6:
        meldung += f" Betroffen laut Kunde: {detail}."
    teile = []
    if teil:
        teile.append(f"1x {TEIL_NAMEN[teil]}" + (f" ({m['gpu_name']}, PN {m['gpu_pn']})" if teil == "gpu" else ""))
    st["ticket"] = {
        "nr": ticket_nr(), "case": case_nr(), "szenario": sz, "titel": d["titel"], "server": srv,
        "teil": teil, "idx": idx, "detail": detail, "ursache": ursache, "meldung": meldung, "vorschlag": d["vorschlag"],
        "teile": teile, "kunde": random.choice(["Alibaba (fiktiv)", "Tencent (fiktiv)"]), "erstellt": t,
        "aktionen": [], "tausch": [], "reseat": [], "fix_zeit": None, "fieldiag": [], "tipps": 0,
        "abgeschlossen": False, "bios_start": dict(s["bios"]), "boot_bei_start": s["boot_id"], **extra,
    }
    return st["ticket"]


def fehler_behoben(st):
    tk = st["ticket"]
    if not tk:
        return False
    s = st["servers"][tk["server"]]
    sz = tk["szenario"]
    if sz == "fw_update":
        return all(s["fw"][k] == v for k, v in FW_TARGET.items())
    if sz == "sn_mismatch":
        return bool(tk.get("eskaliert"))
    if sz == "nvswitch":
        return s["comp"]["baseboard"][0]["status"] == "ok"
    if sz == "raid_disk":
        return s["comp"]["hdd"][tk["idx"]]["status"] == "ok"
    if sz == "gpu_remap_pending":
        g = s["comp"]["gpu"][tk["idx"]]
        return g["status"] == "ok"
    return s["comp"][tk["teil"]][tk["idx"]]["status"] == "ok"


def markiere_fix(st):
    tk = st.get("ticket")
    if tk and not tk["fix_zeit"] and fehler_behoben(st):
        tk["fix_zeit"] = now()
