"""Inspur-Lab: gemeinsamer Zustand, Server-Katalog und Fehler-Szenarien.

Alles ist simuliert. Modellnamen dienen nur der Orientierung, Seriennummern,
Firmware-Staende, Kunden und Tickets sind frei erfunden.
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
FAST = os.environ.get("LAB_FAST") == "1"  # fuer Tests: keine Wartezeiten

BMC_USER, BMC_PASS = "admin", "admin"

# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------

MODELS = {
    "NF5688M7": {
        "typ": "hgx", "beschreibung": "8-GPU HGX-Server (SXM)",
        "gpus": 8, "gpu_name": "NVIDIA H100 80GB HBM3", "gpu_pn": "699-2G520-0200",
        "nvswitch": 4, "dimm_dpc": 2, "psus": 6, "fans": 6, "nvme": 8, "hdd": 0,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "CPLD": "1.0.5", "HGX_FW": "96.00.89"},
    },
    "NF5468M7": {
        "typ": "pcie", "beschreibung": "4-GPU PCIe-Server",
        "gpus": 4, "gpu_name": "NVIDIA L40S", "gpu_pn": "900-2G133-0080",
        "nvswitch": 0, "dimm_dpc": 1, "psus": 4, "fans": 8, "nvme": 4, "hdd": 0,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "CPLD": "1.0.3"},
    },
    "NF5280M7": {
        "typ": "std", "beschreibung": "2-HE-Standardserver mit RAID",
        "gpus": 0, "gpu_name": "", "gpu_pn": "",
        "nvswitch": 0, "dimm_dpc": 1, "psus": 2, "fans": 6, "nvme": 0, "hdd": 8,
        "fw": {"BMC": "4.27.0", "BIOS": "03.01.00", "CPLD": "1.0.2", "RAID": "52.26.0-5014"},
    },
}

FW_TARGET = {"BMC": "4.32.1", "BIOS": "03.05.00"}

SERVERS = {
    "gpu01": {"modell": "NF5688M7", "port": 8001, "rack": "A12 U20-U27"},
    "gpu02": {"modell": "NF5468M7", "port": 8002, "rack": "A12 U14-U17"},
    "std01": {"modell": "NF5280M7", "port": 8003, "rack": "A12 U10-U11"},
}

HOTSWAP = {"psu", "fan", "nvme", "hdd"}

TEIL_NAMEN = {
    "gpu": "GPU", "dimm": "DIMM", "psu": "Netzteil (PSU)", "fan": "Lüftermodul",
    "nvme": "NVMe-SSD", "hdd": "HDD", "baseboard": "HGX-Baseboard",
}

# Szenarien: welche Servertypen, welches Bauteil, wie das Ticket klingt
SZENARIEN = {
    "gpu_offbus": {
        "typen": ["hgx", "pcie"], "teil": "gpu",
        "titel": "GPU nicht mehr erkannt",
        "meldung": ["Kunde meldet: nvidia-smi zeigt nur {n_minus} von {n} GPUs.",
                    "Kunde meldet: Jobs brechen ab, eine GPU ist 'fallen off the bus'."],
    },
    "gpu_remap_fail": {
        "typen": ["hgx", "pcie"], "teil": "gpu",
        "titel": "GPU-Speicherfehler (Row Remapping Failure)",
        "meldung": ["Kunde meldet: Wiederholte Speicherfehler auf einer GPU, Jobs werden abgebrochen.",
                    "Kunde meldet: Uncorrectable ECC auf GPU, Fehler kommt nach Reboot wieder."],
    },
    "gpu_remap_pending": {
        "typen": ["hgx"], "teil": "gpu",
        "titel": "GPU-ECC-Fehler (vorsorglich Tausch angefragt)",
        "meldung": ["Kunde meldet: ECC-Fehler auf einer GPU. Bitte prüfen und ggf. GPU tauschen."],
    },
    "nvswitch": {
        "typen": ["hgx"], "teil": "baseboard",
        "titel": "NVLink-Fehler",
        "meldung": ["Kunde meldet: NVLink-Bandbreite eingebrochen, Multi-GPU-Jobs sehr langsam.",
                    "Kunde meldet: NCCL-Tests schlagen fehl, NVLink-Fehler im Log."],
    },
    "dimm_ue": {
        "typen": ["hgx", "pcie", "std"], "teil": "dimm",
        "titel": "Speicherfehler (Uncorrectable ECC)",
        "meldung": ["Kunde meldet: Server ist abgestürzt, im BMC stehen Speicherfehler.",
                    "Kunde meldet: Uncorrectable ECC Error im SEL."],
    },
    "psu_fail": {
        "typen": ["hgx", "pcie", "std"], "teil": "psu",
        "titel": "Netzteilfehler",
        "meldung": ["Kunde meldet: Ein Netzteil zeigt Fehler, Redundanz verloren.",
                    "Kunde meldet: Alarm 'PSU Failure' im Monitoring."],
    },
    "fan_fail": {
        "typen": ["hgx", "pcie", "std"], "teil": "fan",
        "titel": "Lüfterausfall",
        "meldung": ["Kunde meldet: Lüfter-Alarm im BMC, Temperatur steigt.",
                    "Kunde meldet: Ein Lüftermodul dreht nicht mehr."],
    },
    "nvme_fail": {
        "typen": ["hgx", "pcie"], "teil": "nvme",
        "titel": "NVMe-SSD ausgefallen",
        "meldung": ["Kunde meldet: Eine NVMe-SSD ist aus dem System verschwunden.",
                    "Kunde meldet: Drive-Fault-LED an einer NVMe leuchtet."],
    },
    "raid_disk": {
        "typen": ["std"], "teil": "hdd",
        "titel": "RAID degraded – Festplatte ausgefallen",
        "meldung": ["Kunde meldet: RAID-Volume ist degraded, eine Platte ist ausgefallen.",
                    "Kunde meldet: Fault-LED an einer Festplatte, Monitoring meldet 'Degraded'."],
    },
    "fw_update": {
        "typen": ["hgx", "pcie", "std"], "teil": None,
        "titel": "Firmware-Update",
        "meldung": ["Wartungsauftrag: BMC auf {bmc} und BIOS auf {bios} aktualisieren."],
    },
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
    namen = []
    for cpu in (0, 1):
        for ch in range(8):
            for d in range(dpc):
                namen.append(f"CPU{cpu}_C{ch}D{d}")
    return namen


def neuer_server(name):
    info = SERVERS[name]
    m = MODELS[info["modell"]]
    s = {
        "name": name, "modell": info["modell"], "typ": m["typ"], "port": info["port"],
        "rack": info["rack"], "sn": sn("2207", 8), "power": "on", "power_since": now(),
        "bmc_boot_until": 0, "bmc_ip": f"bmc-{name}",
        "fw": dict(m["fw"]),
        "comp": {
            "gpu": [{"sn": sn("165", 10), "status": "ok"} for _ in range(m["gpus"])],
            "baseboard": ([{"sn": sn("1653", 9), "status": "ok", "nvswitch_bad": None}]
                          if m["nvswitch"] else []),
            "dimm": [{"name": n, "sn": sn("8A", 8), "status": "ok"} for n in dimm_namen(m["dimm_dpc"])],
            "psu": [{"sn": sn("PS", 10), "status": "ok"} for _ in range(m["psus"])],
            "fan": [{"sn": sn("FN", 8), "status": "ok"} for _ in range(m["fans"])],
            "nvme": [{"sn": sn("S6", 10), "status": "ok"} for _ in range(m["nvme"])],
            "hdd": [{"sn": sn("ZL", 8), "status": "ok", "rebuild_start": 0} for _ in range(m["hdd"])],
        },
        "sel": [],
        "sel_next": 1,
        "tasks": [],
    }
    sel_add(s, "Event Logging Disabled", "SEL_Status", "Log area reset/cleared", "Asserted")
    return s


def neuer_zustand():
    return {"version": 1, "servers": {n: neuer_server(n) for n in SERVERS}, "ticket": None,
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
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    st = json.load(f)
            else:
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
    # mit Schreibsperre, damit fertige Vorgaenge (Flash, Rebuild) gespeichert werden
    with zustand() as st:
        return st


def fortschreiben(st):
    """Zeitabhaengige Vorgaenge weiterfuehren (Flash-Tasks, RAID-Rebuild)."""
    t = now()
    for s in st["servers"].values():
        for task in s.get("tasks", []):
            if task["state"] == "Running":
                dauer = 1 if FAST else task["dauer"]
                pct = min(100, int((t - task["start"]) / dauer * 100))
                task["pct"] = pct
                if pct >= 100:
                    ende = task["start"] + dauer
                    task["state"] = "Completed"
                    s["fw"][task["komponente"]] = task["version"]
                    sel_add(s, "Version Change", "FW_Update", f"{task['komponente']} firmware updated to {task['version']}")
                    s["sel"][-1]["t"] = ende
                    if task["komponente"] == "BMC":
                        s["bmc_boot_until"] = ende + (1 if FAST else 45)
        for d in s["comp"]["hdd"]:
            if d["status"] == "rebuild" and t - d["rebuild_start"] > (1 if FAST else 120):
                d["status"] = "ok"
                sel_add(s, "Drive Slot", f"HDD{s['comp']['hdd'].index(d)}_Status", "Rebuild/Remap finished", "Deasserted")


def bmc_bereit(s):
    return now() >= s.get("bmc_boot_until", 0)


def rebuild_pct(d):
    if d["status"] != "rebuild":
        return 100
    return min(99, int((now() - d["rebuild_start"]) / (1 if FAST else 120) * 100))


# --------------------------------------------------------------------------
# Sensoren (aus dem Zustand abgeleitet)
# --------------------------------------------------------------------------


def sensoren(s):
    """Liste von (name, wert, einheit, status)."""
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
    for i, f in enumerate(s["comp"]["fan"]):
        for r in ("F", "R"):
            v = 0 if f["status"] != "ok" else rnd.randint(7800, 9600) if an else rnd.randint(2400, 2800)
            out.append((f"FAN{i}_{r}_Speed", v, "RPM", "lcr" if v == 0 else "ok"))
    for i, p in enumerate(s["comp"]["psu"]):
        ok = p["status"] == "ok"
        out.append((f"PSU{i}_Status", 0x01 if ok else 0x03, "discrete", "ok" if ok else "cr"))
        out.append((f"PSU{i}_POut", (rnd.randint(380, 520) if s["typ"] != "hgx" else rnd.randint(900, 1400)) if ok and an else 0,
                    "Watts", "ok"))
    for i, d in enumerate(s["comp"]["nvme"]):
        out.append((f"NVMe{i}_Status", 0x01 if d["status"] == "ok" else 0x02, "discrete",
                    "ok" if d["status"] == "ok" else "cr"))
    for i, d in enumerate(s["comp"]["hdd"]):
        st = {"ok": 0x01, "fail": 0x02, "rebuild": 0x81}[d["status"]]
        out.append((f"HDD{i}_Status", st, "discrete", "cr" if d["status"] == "fail" else "ok"))
    out.append(("P12V", 12.1 if an else 0.0, "Volts", "ok"))
    return out


# --------------------------------------------------------------------------
# Firmware-Images (Depot)
# --------------------------------------------------------------------------


def fw_images():
    """Dateien im Depot: (dateiname, modell, komponente, version, gueltig)."""
    imgs = []
    for modell in MODELS:
        imgs.append((f"{modell}_BMC_{FW_TARGET['BMC']}.bin", modell, "BMC", FW_TARGET["BMC"], True))
        imgs.append((f"{modell}_BIOS_{FW_TARGET['BIOS']}.bin", modell, "BIOS", FW_TARGET["BIOS"], True))
    # Fallen: beschaedigtes Image (Pruefsumme passt nicht)
    imgs.append((f"NF5688M7_BIOS_{FW_TARGET['BIOS']}_mirror-download.bin", "NF5688M7", "BIOS", FW_TARGET["BIOS"], False))
    return imgs


def fw_inhalt(modell, komp, version, gueltig=True, groesse=256 * 1024):
    kopf = f"INSPUR-LAB-IMG model={modell} comp={komp} ver={version}\n".encode()
    rnd = random.Random(f"{modell}{komp}{version}")
    rumpf = bytes(rnd.getrandbits(8) for _ in range(groesse))
    daten = kopf + rumpf
    if not gueltig:
        daten = daten[:-4096] + b"\x00" * 4096  # unvollstaendiger Download
    return daten


def fw_referenz_hash(modell, komp, version):
    return hashlib.sha256(fw_inhalt(modell, komp, version)).hexdigest()


def fw_depot_erzeugen():
    os.makedirs(FW_DIR, exist_ok=True)
    zeilen = []
    for name, modell, komp, ver, gueltig in fw_images():
        pfad = os.path.join(FW_DIR, name)
        if not os.path.exists(pfad):
            with open(pfad, "wb") as f:
                f.write(fw_inhalt(modell, komp, ver, gueltig))
        # SHA256SUMS enthaelt immer den Wert des ORIGINALS
        zeilen.append(f"{fw_referenz_hash(modell, komp, ver)}  {name}")
    with open(os.path.join(FW_DIR, "SHA256SUMS"), "w") as f:
        f.write("\n".join(zeilen) + "\n")
    with open(os.path.join(FW_DIR, "RELEASE-NOTES.txt"), "w") as f:
        f.write(RELEASE_NOTES)


RELEASE_NOTES = f"""Inspur-Lab Firmware-Depot (SIMULIERT, keine echte Firmware)
=============================================================

BMC {FW_TARGET['BMC']}  (alle Modelle)
  - Update immer ZUERST, vor dem BIOS.
  - Host darf eingeschaltet bleiben. Der BMC startet nach dem Update neu
    (ca. 45 Sekunden nicht erreichbar).
  - Image passt nur zum eigenen Servermodell (Dateiname beachten).

BIOS {FW_TARGET['BIOS']}  (alle Modelle)
  - Voraussetzung: BMC mindestens {FW_TARGET['BMC']}.
  - Host muss AUSGESCHALTET sein (chassis power off).
  - Nach dem Update Host wieder einschalten.

Vor jedem Update: Pruefsumme kontrollieren!
  sha256sum <datei>   und mit SHA256SUMS vergleichen.
"""


def fw_pruefen(daten, modell, komp_erwartet=None):
    """Prueft ein hochgeladenes Image. -> (ok, meldung, komp, version)"""
    try:
        kopf = daten.split(b"\n", 1)[0].decode()
    except UnicodeDecodeError:
        return False, "Invalid image format", None, None
    if not kopf.startswith("INSPUR-LAB-IMG"):
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
    """Flash-Vorgang anlegen. -> (ok, meldung, task)"""
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
# Szenario einspielen / Ticket
# --------------------------------------------------------------------------


def ticket_nr():
    return "STR-" + datetime.now().strftime("%Y") + "-" + "".join(random.choice(string.digits) for _ in range(6))


def case_nr():
    return "INS" + datetime.now().strftime("%Y%m%d") + "".join(random.choice(string.digits) for _ in range(4))


def szenario_starten(st, name=None, server=None):
    if name and name not in SZENARIEN:
        raise ValueError(f"Unbekanntes Szenario: {name}")
    kandidaten = []
    for sz, d in SZENARIEN.items():
        if name and sz != name:
            continue
        for srv, info in SERVERS.items():
            if server and srv != server:
                continue
            if MODELS[info["modell"]]["typ"] in d["typen"]:
                kandidaten.append((sz, srv))
    if not kandidaten:
        raise ValueError("Diese Kombination aus Szenario und Server gibt es nicht.")
    sz, srv = random.choice(kandidaten)
    # Server frisch aufsetzen, damit nichts vom letzten Ticket uebrig ist
    st["servers"][srv] = neuer_server(srv)
    s = st["servers"][srv]
    d = SZENARIEN[sz]
    teil = d["teil"]
    idx = None
    detail = ""
    t = now()
    # Vorgeschichte im SEL
    sel_add(s, "System Firmware", "System_FW_Progress", "System boot initiated")
    sel_add(s, "OS Boot", "OS_Status", "OS boot completed")
    n_vor = len(s["sel"])

    if sz in ("gpu_offbus", "gpu_remap_fail", "gpu_remap_pending"):
        idx = random.randrange(len(s["comp"]["gpu"]))
        s["comp"]["gpu"][idx]["status"] = {"gpu_offbus": "offbus", "gpu_remap_fail": "remap_fail",
                                           "gpu_remap_pending": "remap_pending"}[sz]
        if sz == "gpu_offbus":
            sel_add(s, "Critical Interrupt", f"PCIe_GPU{idx}", "Bus Uncorrectable Error")
        detail = f"GPU{idx}"
    elif sz == "nvswitch":
        idx = 0
        bad = random.randrange(4)
        s["comp"]["baseboard"][0]["status"] = "nvlink"
        s["comp"]["baseboard"][0]["nvswitch_bad"] = bad
        detail = f"NVSwitch{bad}"
    elif sz == "dimm_ue":
        idx = random.randrange(len(s["comp"]["dimm"]))
        s["comp"]["dimm"][idx]["status"] = "ue"
        nm = s["comp"]["dimm"][idx]["name"]
        for _ in range(3):
            sel_add(s, "Memory", f"DIMM_{nm}", "Correctable ECC")
        sel_add(s, "Memory", f"DIMM_{nm}", "Uncorrectable ECC")
        sel_add(s, "Critical Interrupt", "CPU_CATERR", "State Asserted")
        detail = nm
    elif sz == "psu_fail":
        idx = random.randrange(len(s["comp"]["psu"]))
        s["comp"]["psu"][idx]["status"] = "fail"
        sel_add(s, "Power Supply", f"PSU{idx}_Status", "Failure detected")
        sel_add(s, "Power Unit", "PSU_Redundancy", "Redundancy Lost")
        detail = f"PSU{idx}"
    elif sz == "fan_fail":
        idx = random.randrange(len(s["comp"]["fan"]))
        s["comp"]["fan"][idx]["status"] = "fail"
        sel_add(s, "Fan", f"FAN{idx}_F_Speed", "Lower Critical going low")
        sel_add(s, "Fan", f"FAN{idx}_R_Speed", "Lower Critical going low")
        detail = f"FAN{idx}"
    elif sz == "nvme_fail":
        idx = random.randrange(len(s["comp"]["nvme"]))
        s["comp"]["nvme"][idx]["status"] = "fail"
        sel_add(s, "Drive Slot", f"NVMe{idx}_Status", "Drive Fault")
        detail = f"NVMe{idx}"
    elif sz == "raid_disk":
        idx = random.randrange(len(s["comp"]["hdd"]))
        s["comp"]["hdd"][idx]["status"] = "fail"
        sel_add(s, "Drive Slot", f"HDD{idx}_Status", "Drive Fault")
        detail = f"HDD{idx}"
    # Zeitstempel: Vorgeschichte vor einigen Tagen, der Fehler vor wenigen Stunden
    zeit = t - random.randint(2, 4) * 86400
    for i, e in enumerate(s["sel"]):
        if i == n_vor:
            zeit = t - random.randint(1, 6) * 3600
        e["t"] = zeit
        zeit += random.randint(600, 6 * 3600) if i < n_vor else random.randint(20, 600)
    m = MODELS[s["modell"]]
    genau = sz not in ("gpu_offbus", "gpu_remap_pending", "nvswitch") and random.random() < 0.5
    meldung = random.choice(d["meldung"]).format(n=m["gpus"], n_minus=m["gpus"] - 1,
                                                 bmc=FW_TARGET["BMC"], bios=FW_TARGET["BIOS"])
    if genau and detail:
        meldung += f" Betroffen laut Kunde: {detail}."
    teile = []
    if teil:
        teile.append(f"1x {TEIL_NAMEN[teil]}" + (f" ({m['gpu_name']}, PN {m['gpu_pn']})" if teil == "gpu" else ""))
    kunde = random.choice(["Alibaba (fiktiv)", "Tencent (fiktiv)"])
    st["ticket"] = {
        "nr": ticket_nr(), "case": case_nr(), "szenario": sz, "titel": d["titel"], "server": srv,
        "teil": teil, "idx": idx, "detail": detail, "meldung": meldung, "teile": teile,
        "kunde": kunde, "erstellt": t, "aktionen": [], "tausch": [], "fix_zeit": None,
        "fieldiag": [], "sel_geloescht_nach_fix": False, "tipps": 0, "fehlversuche": 0,
        "abgeschlossen": False,
    }
    return st["ticket"]


def aktion(st, text):
    if st.get("ticket") and not st["ticket"]["abgeschlossen"]:
        st["ticket"]["aktionen"].append({"t": now(), "text": text})


def fehler_behoben(st):
    tk = st["ticket"]
    if not tk:
        return False
    s = st["servers"][tk["server"]]
    sz = tk["szenario"]
    if sz == "fw_update":
        return all(s["fw"][k] == v for k, v in FW_TARGET.items())
    if sz == "nvswitch":
        return s["comp"]["baseboard"][0]["status"] == "ok"
    if sz == "raid_disk":
        return s["comp"]["hdd"][tk["idx"]]["status"] == "ok"
    return s["comp"][tk["teil"]][tk["idx"]]["status"] == "ok"


def markiere_fix(st):
    tk = st.get("ticket")
    if tk and not tk["fix_zeit"] and fehler_behoben(st):
        tk["fix_zeit"] = now()


# --------------------------------------------------------------------------
# Gemeinsame Aktionen (ipmitool, Redfish und Weboberflaeche nutzen dieselben)
# --------------------------------------------------------------------------


def power(st, s, aktion_):
    """aktion_: on | off | soft | cycle | reset. -> (ok, meldung)"""
    if aktion_ == "status":
        return True, f"Chassis Power is {s['power']}"
    if aktion_ == "on":
        if s["power"] != "on":
            s["power"], s["power_since"] = "on", now()
            sel_add(s, "System ACPI Power State", "ACPI_State", "S0/G0: working")
            aktion(st, f"{s['name']} eingeschaltet")
        markiere_fix(st)
        return True, "Chassis Power Control: Up/On"
    if aktion_ in ("off", "soft"):
        if s["power"] != "off":
            s["power"] = "off"
            for g in s["comp"]["gpu"]:
                if g["status"] == "remap_pending":
                    g["status"] = "ok"  # Remapping wird beim naechsten GPU-Reset/Power-Cycle aktiv
            sel_add(s, "System ACPI Power State", "ACPI_State", "S5/G2: soft-off")
            aktion(st, f"{s['name']} ausgeschaltet")
        return True, "Chassis Power Control: Down/Off"
    if aktion_ in ("cycle", "reset"):
        if s["power"] != "on":
            return False, "Set Chassis Power Control to Cycle failed: Command not supported in present state"
        for g in s["comp"]["gpu"]:
            if g["status"] == "remap_pending":
                g["status"] = "ok"
        s["power_since"] = now()
        sel_add(s, "System ACPI Power State", "ACPI_State", "S0/G0: working")
        aktion(st, f"{s['name']} Power-{aktion_}")
        markiere_fix(st)
        return True, f"Chassis Power Control: {'Cycle' if aktion_ == 'cycle' else 'Reset'}"
    return False, f"Invalid chassis power command: {aktion_}"


def sel_clear(st, s):
    s["sel"], s["sel_next"] = [], 1
    sel_add(s, "Event Logging Disabled", "SEL_Status", "Log area reset/cleared")
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"] and tk["server"] == s["name"]:
        if fehler_behoben(st):
            tk["sel_geloescht_nach_fix"] = True
        else:
            tk["sel_zu_frueh"] = True
        aktion(st, "SEL gelöscht")


def bmc_reset(st, s):
    s["bmc_boot_until"] = now() + (1 if FAST else 45)
    aktion(st, f"BMC {s['name']} neu gestartet")


def fw_fehler_merken(st, s, meldung):
    tk = st.get("ticket")
    if tk and not tk["abgeschlossen"] and tk["server"] == s["name"]:
        tk.setdefault("fw_fehler", []).append(meldung)
        aktion(st, f"Firmware-Update abgelehnt: {meldung}")
