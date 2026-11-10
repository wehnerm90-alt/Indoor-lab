"""Nachbildungen der Werkzeuge: ipmitool, nvidia-smi, fieldiag, storcli64, nvme, dmesg, lspci.

Ausgaben sind an die echten Werkzeuge angelehnt, aber Simulationen fuer das Training.
Fehlercodes von fieldiag sind Lab-Codes (bis auf 000000000818 aus der Inspur-Anleitung).
"""
import hashlib
import os
import random
import re
import signal
import sys
import time
from datetime import datetime

from . import core

NVIDIA_FEHLT = ("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. "
                "Make sure that the latest NVIDIA driver is installed and running.")


def bus_id(i):
    return f"00000000:{core.GPU_BUS[i].upper()}:00.0"


def gpu_uuid(g):
    h = hashlib.md5(g["sn"].encode()).hexdigest()
    return f"GPU-{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def fehler(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def inband_server(st):
    name = os.environ.get("LAB_SERVER")
    if not name or name not in st["servers"]:
        fehler("Dieser Befehl läuft auf dem Server selbst (KVM-Konsole oder: lab login <server>).")
    s = st["servers"][name]
    if s["power"] != "on" or s.get("phase") != "os":
        fehler("Connection to host lost.")
    return s


def sichtbar(s, i):
    """GPU im Bus? offbus im selben Boot = noch da (rev ff), nach Neustart = weg."""
    g = s["comp"]["gpu"][i]
    if g["status"] != "offbus":
        return "ok"
    return "revff" if g.get("lost_boot") == s["boot_id"] else "weg"


def fehlerboot(s):
    return s.get("fehler_boot") == s["boot_id"]


# --------------------------------------------------------------------------
# ipmitool
# --------------------------------------------------------------------------

IPMI_HILFE = """ipmitool version 1.8.19 (Lab-Simulation)

usage: ipmitool [options...] <command>
  -I intf   Interface to use (lanplus)      -H hostname  Remote host name for LAN interface
  -U user   Remote session username         -P password  Remote session password
Commands:
  raw  sel  sdr  sensor  fru  chassis  power  mc  lan
Beispiele:
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel elist
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin fru print
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin raw 0x3c 0x4c 0x01 0x10        (BMC-Polling abfragen, M6)
"""


def ipmitool(argv):
    opts, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("-I", "-H", "-U", "-P", "-p", "-L", "-C", "-y"):
            if i + 1 >= len(argv):
                fehler(f"Option {a} requires an argument")
            opts[a] = argv[i + 1]
            i += 2
        elif a in ("-E", "-v", "-vv", "-c"):
            opts[a] = True
            i += 1
        elif a in ("-h", "--help", "help"):
            print(IPMI_HILFE)
            return
        else:
            rest.append(a)
            i += 1
    if not rest:
        print(IPMI_HILFE)
        sys.exit(1)
    with core.zustand() as st:
        s = _ipmi_ziel(st, opts)
        _ipmi_befehl(st, s, rest)
        core.markiere_fix(st)


def _ipmi_ziel(st, opts):
    host = opts.get("-H")
    if not host:
        name = os.environ.get("LAB_SERVER")
        if not name:
            fehler("Could not open device at /dev/ipmi0 or /dev/ipmi/0 or /dev/ipmidev/0: No such file or directory\n"
                   "(Tipp: von hier aus mit -I lanplus -H bmc-<server> -U admin -P admin)")
        s = st["servers"][name]
    else:
        h = host.lower()
        s = next((srv for n, srv in st["servers"].items() if h in (n, f"bmc-{n}")
                  or h == srv.get("settings", {}).get("network", {}).get("ip")), None)
        if s is None:
            fehler(f"Error: Unable to establish IPMI v2 / RMCP+ session")
        if opts.get("-I") not in ("lanplus", "lan"):
            fehler("Error: Unable to establish IPMI v2 / RMCP+ session")
        pw = opts.get("-P") or (os.environ.get("IPMI_PASSWORD") if opts.get("-E") else None)
        if opts.get("-U") != core.BMC_USER or pw != core.BMC_PASS:
            fehler("Error: Unable to establish IPMI v2 / RMCP+ session")
    if not core.bmc_bereit(s):
        fehler("Error: Unable to establish IPMI v2 / RMCP+ session")
    return s


FRU_STANDARD = {
    "c0": "", "c1": "", "c2": "", "c3": "",
    "b0": "Inspur", "b1": "", "b2": "", "b3": "",
    "p0": "Inspur", "p1": "", "p2": "", "p4": "", "p5": "", "p7": "",
}


def fru(s):
    f = s.setdefault("fru", {})
    for k, v in FRU_STANDARD.items():
        f.setdefault(k, v)
    if not f["p1"]:
        f.update(p1=s["modell"], p4=s["sn"], b1=s["modell"], b2=sn_board(s), c1=s["sn"],
                 c0=s["modell"] + ".00", p2=s["modell"] + "-00")
    return f


def sn_board(s):
    return "BRD" + hashlib.md5(s["sn"].encode()).hexdigest()[:9].upper()


def _raw(st, s, args):
    try:
        b = [int(x, 16) for x in args]
    except ValueError:
        fehler("Invalid hex value")
    m = core.MODELS[s["modell"]]
    if b[:4] == [0x3c, 0x4c, 0x01, 0x10]:
        if not m["polling"]:
            fehler(f"Unable to send RAW command (channel=0x0 netfn=0x3c lun=0x0 cmd=0x4c rsp=0xc1): Invalid command")
        if len(b) == 4:
            print(f" {1 if s.get('polling', True) else 0:02x}")
            return
        an = b[4] == 1
        s["polling"] = an
        core.aktion(st, "BMC-Polling " + ("an" if an else "aus"))
        core.eingriff(st)
        return
    if b[:7] == [0x3e, 0x09, 0x00, 0x4c, 0xa5, 0x09, 0x00] and len(b) == 8:
        if b[7] == 1:
            s["onekey_ali"] = core.now()
            return
        if b[7] == 2:
            start = s.get("onekey_ali")
            if not start:
                print(" 00")
                return
            pct = 100 if core.FAST else min(100, int((core.now() - start) / 60 * 100))
            print(f" {0x64 if pct >= 100 else pct:02x}")
            return
    if b[:2] == [0x06, 0x52]:
        return  # Master Write-Read (z. B. Schreibschutz) – ok
    if b[:2] == [0x06, 0x01]:
        print(" 20 01 04 1b 02 bf 4e 00 00 00 00 00 00 00 00")
        return
    fehler(f"Unable to send RAW command (channel=0x0 netfn=0x{b[0]:x} lun=0x0 cmd=0x{(b[1] if len(b) > 1 else 0):x} rsp=0xc1): Invalid command")


def _ipmi_befehl(st, s, a):
    cmd = a[0]
    sub = a[1] if len(a) > 1 else ""
    if cmd == "raw":
        return _raw(st, s, a[1:])
    if cmd == "sel":
        if sub in ("list", "elist", ""):
            for e in s["sel"]:
                d, t = core.ts(e["t"]).split(" | ")
                print(f"{e['id']:>4x} | {d} | {t} | {e['typ']} {e['sensor']} | {e['event']} | {e['dir']}")
            if not s["sel"]:
                print("SEL has no entries")
        elif sub == "info":
            print("SEL Information\nVersion          : 1.5 (v1.5, v2 compliant)")
            print(f"Entries          : {len(s['sel'])}\nPercent Used     : {max(1, len(s['sel']) // 40)}%")
        elif sub == "clear":
            core.sel_clear(st, s)
            print("Clearing SEL.  Please allow a few seconds to erase.")
        else:
            fehler(f"Invalid SEL command: {sub}")
    elif cmd in ("sdr", "sensor"):
        for name, wert, einheit, status in core.sensoren(s):
            if wert is None:
                w, status = "no reading", "ns"
            elif einheit == "discrete":
                w = f"0x{wert:02x}"
            else:
                w = f"{wert} {einheit}"
            print(f"{name:<17}| {w:<18}| {status}")
    elif cmd == "fru":
        f = fru(s)
        if sub == "edit":
            # fru edit 0 field <p|b|c> <n> "Text"
            if len(a) < 7 or a[3] != "field" or a[4] not in "pbc" or not a[5].isdigit():
                fehler('usage: fru edit <id> field <c|b|p> <field id> <string>')
            f[a[4] + a[5]] = a[6]
            print(f"Read All FRU area\nFru Size         : 1024 bytes\nDone.")
            core.aktion(st, f"FRU geändert: {a[4]}{a[5]}={a[6]}")
            return
        if sub in ("read", "write"):
            print(f"Fru Size         : 1024 bytes\nDone.")
            return
        core.notiert(st, s["name"], "fru")
        print("FRU Device Description : Builtin FRU Device (ID 0)")
        print(f" Chassis Type          : Rack Mount Chassis\n Chassis Part Number   : {f['c0']}\n Chassis Serial        : {f['c1']}")
        if f["c2"]:
            print(f" Chassis Extra         : {f['c2']}")
        print(f" Board Mfg             : {f['b0']}\n Board Product         : {f['b1']}\n Board Serial          : {f['b2']}")
        print(f" Board Part Number     : {f['b3'] or s['modell'] + '-MB'}")
        print(f" Product Manufacturer  : {f['p0']}\n Product Name          : {f['p1']}\n Product Part Number   : {f['p2']}")
        print(f" Product Serial        : {f['p4']}")
        if f["p5"]:
            print(f" Product Asset Tag     : {f['p5']}")
        if f["p7"]:
            print(f" Product Extra         : {f['p7']}")
        for i, p in enumerate(s["comp"]["psu"]):
            print(f"\nFRU Device Description : PSU{i}_FRU (ID {i + 1})\n Product Serial        : {p['sn']}")
    elif cmd in ("chassis", "power"):
        if cmd == "power":
            sub = a[1] if len(a) > 1 else "status"
        elif sub == "power":
            sub = a[2] if len(a) > 2 else "status"
        elif sub == "status":
            print(f"System Power         : {s['power']}\nPower Overload       : false\nMain Power Fault     : false")
            print(f"Cooling/Fan Fault    : {'true' if any(f['status'] != 'ok' for f in s['comp']['fan']) else 'false'}")
            return
        elif sub == "identify":
            s["uid"] = not (len(a) > 2 and a[2] == "0")
            print("Chassis identify interval: " + ("indefinite" if s["uid"] else "off"))
            return
        ok, msg = core.power(st, s, sub)
        if not ok:
            fehler(msg)
        print(msg)
    elif cmd == "mc":
        if sub == "info":
            core.notiert(st, s["name"], "fw")
            maj, mi, pa = s["fw"]["BMC"].split(".")
            print(f"Device ID                 : 32\nDevice Revision           : 1\nFirmware Revision         : {maj}.{mi}")
            print(f"IPMI Version              : 2.0\nManufacturer ID           : 37945\nAux Firmware Rev Info     : \n    0x{int(pa):02x}")
        elif sub == "reset":
            core.bmc_reset(st, s)
            print(f"Sent {a[2] if len(a) > 2 else 'cold'} reset command to MC")
        else:
            fehler(f"Invalid mc command: {sub}")
    elif cmd == "lan":
        ip = s.get("settings", {}).get("network", {}).get("ip", "10.10.0.10")
        print(f"Set in Progress         : Set Complete\nIP Address Source       : Static Address\nIP Address              : {ip}")
        mac = hashlib.md5(s["sn"].encode()).hexdigest()[:12]
        print("MAC Address             : " + ":".join(mac[i:i + 2] for i in range(0, 12, 2)))
    else:
        fehler(f"Invalid command: {cmd}")


# --------------------------------------------------------------------------
# nvidia-smi
# --------------------------------------------------------------------------


def nvidia_smi(argv):
    with core.zustand() as st:
        s = inband_server(st)
        if not s["comp"]["gpu"] or not s.get("drv", True):
            fehler(NVIDIA_FEHLT, 9)
        _nvsmi(st, s, argv)
        core.markiere_fix(st)


def _idx(argv):
    for k, a in enumerate(argv):
        if a in ("-i", "--id") and k + 1 < len(argv):
            return int(argv[k + 1])
        if a.startswith("--id="):
            return int(a.split("=", 1)[1])
    return None


def _nvsmi(st, s, argv):
    gpus = s["comp"]["gpu"]
    m = core.MODELS[s["modell"]]
    idx = _idx(argv)
    if idx is not None and not 0 <= idx < len(gpus):
        fehler(f"No devices were found matching the index {idx}", 6)
    vorhanden = [i for i in range(len(gpus)) if sichtbar(s, i) != "weg"]
    lost = [i for i in vorhanden if sichtbar(s, i) == "revff"]
    if "-h" in argv or "--help" in argv:
        print("NVIDIA System Management Interface -- v550.90.07 (Lab)\n\n  -L  -q [-d ECC|ROW_REMAPPER] [-i N]  -r -i N  -p 0|1 [-i N]\n"
              "  nvlink -s   topo -m   topo -p2p r   -mig 0|1   --query-gpu=... --format=csv")
        return
    if lost and not any(a in argv for a in ("-L",)):
        for i in lost:
            print(f"Unable to determine the device handle for GPU{bus_id(i)}: Unknown Error")
        if not any(a in argv for a in ("-q",)):
            sys.exit(255)
    # Indizes laufen ueber die sichtbaren GPUs (wie im echten System)
    sicht = [i for i in vorhanden if i not in lost]
    nr = {phys: n for n, phys in enumerate(sicht)}

    if "-r" in argv or "--gpu-reset" in argv:
        if idx is None:
            fehler("GPU reset requires a target GPU: -i <index>")
        phys = sicht[idx] if idx < len(sicht) else None
        g = gpus[phys]
        print(f"GPU {bus_id(phys)} was successfully reset.")
        if g["status"] == "remap_pending":
            g["status"] = "ok"
        g["ecc_vol"] = 0
        core.aktion(st, f"GPU{phys} Reset")
        return
    if "-p" in argv or "--reset-ecc-errors" in argv:
        k = argv.index("-p") if "-p" in argv else argv.index("--reset-ecc-errors")
        art = argv[k + 1] if k + 1 < len(argv) else ""
        if art not in ("0", "1"):
            fehler("Invalid ECC error type: use 0 (volatile) or 1 (aggregate)")
        ziele = [sicht[idx]] if idx is not None else sicht
        for phys in ziele:
            gpus[phys]["ecc_vol" if art == "0" else "ecc_agg"] = 0
            print(f"Reset {'volatile' if art == '0' else 'aggregate'} ECC errors to zero for GPU {bus_id(phys)}.")
        print("All done.")
        tk = st.get("ticket")
        if tk and not tk["abgeschlossen"]:
            tk.setdefault("ecc_reset", []).append({"art": art, "t": core.now(), "gpus": ziele})
        core.aktion(st, f"ECC-Zähler gelöscht (-p {art})")
        return
    if "-mig" in argv:
        print(f"Disabled MIG Mode for GPU {bus_id(sicht[idx or 0])}.\nAll done.")
        return
    if "-L" in argv:
        for phys in sicht:
            print(f"GPU {nr[phys]}: {m['gpu_name']} (UUID: {gpu_uuid(gpus[phys])})")
        return
    if "nvlink" in argv:
        bb = s["comp"]["baseboard"][0] if s["comp"]["baseboard"] else None
        if not bb:
            print("NVLink is not supported on this system.")
            return
        for phys in sicht:
            print(f"GPU {nr[phys]}: {m['gpu_name']} (UUID: {gpu_uuid(gpus[phys])})")
            for link in range(m["nvlinks"]):
                tot = bb["status"] == "nvlink" and link % m["nvswitch"] == bb["nvswitch_bad"]
                print(f"\t Link {link}: " + ("<inactive>" if tot else "25 GB/s"))
        return
    if "topo" in argv:
        n = len(sicht)
        hgx = bool(s["comp"]["baseboard"])
        bad = hgx and s["comp"]["baseboard"][0]["status"] != "ok"
        if "-p2p" in argv:
            print("\t" + "\t".join(f"GPU{k}" for k in range(n)))
            for a_ in range(n):
                print(f" GPU{a_}\t" + "\t".join("X" if a_ == b_ else ("OK" if hgx and not bad else ("NS" if not hgx else "CNS")) for b_ in range(n)))
            print("\nLegend:\n  X    = Self\n  OK   = Status Ok\n  CNS  = Chipset not supported\n  NS   = Not supported")
            return
        print("\t" + "\t".join(f"GPU{k}" for k in range(n)) + "\tCPU Affinity")
        for a_ in range(n):
            verb = ["X" if a_ == b_ else ((f"NV{m['nvlinks'] - (2 if bad else 0)}") if hgx else ("NODE" if (a_ < n // 2) == (b_ < n // 2) else "SYS")) for b_ in range(n)]
            print(f"GPU{a_}\t" + "\t".join(verb) + f"\t{'0-55' if a_ < n // 2 else '56-111'}")
        print("\nLegend:\n  X    = Self\n  SYS  = Connection traversing PCIe and the SMP interconnect\n  NODE = Connection within a NUMA node\n  NV#  = Connection traversing a bonded set of # NVLinks")
        return
    q = next((a for a in argv if a.startswith("--query-gpu=")), None)
    if q:
        felder = q.split("=", 1)[1].split(",")
        werte = {"index": lambda p, g: str(nr[p]), "name": lambda p, g: m["gpu_name"], "serial": lambda p, g: g["sn"],
                 "uuid": lambda p, g: gpu_uuid(g), "pci.bus_id": lambda p, g: bus_id(p), "vbios_version": lambda p, g: g["vbios"],
                 "ecc.errors.uncorrected.volatile.total": lambda p, g: str(g["ecc_vol"]),
                 "ecc.errors.uncorrected.aggregate.total": lambda p, g: str(g["ecc_agg"]),
                 "temperature.gpu": lambda p, g: str(random.randint(32, 40)), "driver_version": lambda p, g: "550.90.07"}
        fmt = next((a for a in argv if a.startswith("--format=")), "--format=csv")
        if "noheader" not in fmt:
            print(", ".join(felder))
        for phys in sicht:
            print(", ".join(werte.get(f, lambda p, g: "[N/A]")(phys, gpus[phys]) for f in felder))
        return
    if any(a.startswith("--query-retired-pages") for a in argv):
        print("gpu_bus_id, gpu_serial, retired_pages.cause, retired_pages.timestamp")
        return
    if "-q" in argv and "-d" in argv:
        sek = (argv[argv.index("-d") + 1] if argv.index("-d") + 1 < len(argv) else "").upper()
        print(f"\n==============NVSMI LOG==============\n\nTimestamp                                 : {datetime.now():%a %b %d %H:%M:%S %Y}")
        print(f"Driver Version                            : 550.90.07\n\nAttached GPUs                             : {len(sicht)}")
        for phys in sicht:
            if idx is not None and nr[phys] != idx:
                continue
            g = gpus[phys]
            print(f"GPU {bus_id(phys)}")
            if "ECC" in sek:
                print(f"    ECC Mode\n        Current                           : Enabled")
                print(f"    ECC Errors\n        Volatile\n            DRAM Uncorrectable            : {g['ecc_vol']}")
                print(f"        Aggregate\n            DRAM Uncorrectable            : {g['ecc_agg']}")
            if "ROW" in sek:
                print(f"    Remapped Rows\n        Correctable Error                 : {g['remap_corr']}\n        Uncorrectable Error               : {g['remap_unc']}")
                print(f"        Pending                           : {'Yes' if g['status'] == 'remap_pending' else 'No'}")
                print(f"        Remapping Failure Occurred        : {'Yes' if g['status'] == 'remap_fail' else 'No'}")
        return
    if "-q" in argv:
        print(f"\n==============NVSMI LOG==============\n\nTimestamp                                 : {datetime.now():%a %b %d %H:%M:%S %Y}")
        print(f"Driver Version                            : 550.90.07\nCUDA Version                              : 12.4\n")
        print(f"Attached GPUs                             : {len(sicht)}")
        for phys in sicht:
            if idx is not None and nr[phys] != idx:
                continue
            g = gpus[phys]
            print(f"GPU {bus_id(phys)}\n    Product Name                          : {m['gpu_name']}")
            print(f"    Serial Number                         : {g['sn']}\n    GPU UUID                              : {gpu_uuid(g)}")
            print(f"    Minor Number                          : {nr[phys]}\n    VBIOS Version                         : {g['vbios']}")
            print(f"    Board Part Number                     : {m['gpu_pn']}\n    MIG Mode\n        Current                           : Disabled")
            if s["comp"]["baseboard"]:
                ok = s["comp"]["baseboard"][0]["status"] == "ok"
                print(f"    Fabric\n        State                             : {'Completed' if ok else 'In Progress'}")
                print(f"        Status                            : {'Success' if ok else 'N/A'}")
            print(f"    PCI\n        Bus Id                            : {bus_id(phys)}")
            print(f"        GPU Link Info\n            PCIe Generation\n                Max                       : 4\n                Current                   : 4")
            print(f"            Link Width\n                Max                       : 16x\n                Current                   : 16x")
            print(f"    ECC Mode\n        Current                           : Enabled")
            print(f"    ECC Errors\n        Volatile\n            SRAM Uncorrectable            : 0\n            DRAM Uncorrectable            : {g['ecc_vol']}")
            print(f"        Aggregate\n            SRAM Uncorrectable            : 0\n            DRAM Uncorrectable            : {g['ecc_agg']}")
            print(f"    Retired Pages\n        Single Bit ECC                    : N/A\n        Double Bit ECC                    : N/A")
            print(f"    Remapped Rows\n        Correctable Error                 : {g['remap_corr']}")
            print(f"        Uncorrectable Error               : {g['remap_unc']}")
            print(f"        Pending                           : {'Yes' if g['status'] == 'remap_pending' else 'No'}")
            print(f"        Remapping Failure Occurred        : {'Yes' if g['status'] == 'remap_fail' else 'No'}")
            print("        Bank Remap Availability Histogram\n            Max                           : 639 bank(s)\n"
                  "            High                          : 0 bank(s)\n            Partial                       : 1 bank(s)\n"
                  "            Low                           : 0 bank(s)\n            None                          : 0 bank(s)")
            print(f"    Temperature\n        GPU Current Temp                  : {random.randint(32, 40)} C")
        return
    rnd = random.Random(int(time.time() / 5))
    print(f"{datetime.now():%a %b %d %H:%M:%S %Y}")
    print("+-----------------------------------------------------------------------------------------+")
    print("| NVIDIA-SMI 550.90.07              Driver Version: 550.90.07      CUDA Version: 12.4     |")
    print("|-----------------------------------------+------------------------+----------------------+")
    print("| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |")
    print("| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |")
    print("|=========================================+========================+======================|")
    mem = "81920MiB" if s["typ"] == "hgx" else "24576MiB"
    cap = "400W" if s["typ"] == "hgx" else "165W"
    for phys in sicht:
        g = gpus[phys]
        name = m["gpu_name"].replace("NVIDIA ", "")[:20]
        print(f"| {nr[phys]:>3}  {name:<20}           On | {bus_id(phys)[4:]}   Off | {g['ecc_vol']:>20} |")
        print(f"| N/A   {rnd.randint(31, 40)}C    P0  {rnd.randint(55, 90):>13}W / {cap:<5}|      1MiB / {mem:<9}|      0%      Default |")
        print("+-----------------------------------------+------------------------+----------------------+")
    print("\n+-----------------------------------------------------------------------------------------+")
    print("| Processes:                                                                              |")
    print("|  No running processes found                                                             |")
    print("+-----------------------------------------------------------------------------------------+")


# --------------------------------------------------------------------------
# fieldiag (laeuft nur aus der AI PE im entpackten Paket)
# --------------------------------------------------------------------------

FD_HILFE = """fieldiag.sh – NVIDIA Field Diagnostic (Lab-Simulation)
Usage: ./fieldiag.sh [--no_bmc] [--level1|--level2] [--test <name>]
  --level1        kurzer Test (Standard)
  --level2        ausführlicher Test (mehr Thermik/Last)
  --test <name>   nur einen Test: skucheck, inforom, pcie, gpumem, connectivity, thermal, gpustress, nvlink_bw
  --no_bmc        BMC-Tests auslassen (im Feld immer angeben)
"""


def fieldiag(argv):
    if "-h" in argv or "--help" in argv:
        print(FD_HILFE)
        return
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(130))
    paket = os.environ.get("LAB_FD_PAKET", "")
    logdir = os.environ.get("LAB_FD_LOGDIR", "logs")
    level = 2 if "--level2" in argv else 1
    test_nur = argv[argv.index("--test") + 1] if "--test" in argv and argv.index("--test") + 1 < len(argv) else None
    with core.zustand() as st:
        s = inband_server(st)
        m = core.MODELS[s["modell"]]
        snap = {k: s[k] for k in ("name", "modell", "typ", "drv", "polling", "bios", "sn", "boot_id", "fehler_boot")
                if k in s}
        geladen = s.get("mods")
        snap["mods"] = [x for x in ("nvidia_peermem", "nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia")
                        if geladen is None or x in geladen]
        snap["gpus"] = [dict(g, sichtbar=sichtbar(s, i)) for i, g in enumerate(s["comp"]["gpu"])]
        snap["bb"] = dict(s["comp"]["baseboard"][0]) if s["comp"]["baseboard"] else None
        snap["fan"] = [f["status"] for f in s["comp"]["fan"]]
    zeilen = []

    def out(t=""):
        print(t, flush=True)
        zeilen.append(t)

    def pause(sek):
        if not core.FAST:
            time.sleep(sek)

    out("NVIDIA Field Diagnostic (fieldiag) – Lab-Simulation")
    out(f"Package : {paket or '?'}")
    out(f"Started : {datetime.now():%Y-%m-%d %H:%M:%S}   Host SN: {snap['sn']}")
    out("-" * 72)
    abbruch = None
    if paket != m["fd_paket"]:
        abbruch = (f"ERROR: SKU not supported by this package. Detected: {m['fd_sku']} – "
                   f"package supports only {next((x['fd_sku'] for x in core.MODELS.values() if x['fd_paket'] == paket), 'unknown')}.")
    elif "--no_bmc" not in argv:
        abbruch = "ERROR: BMC access not configured (missing --bmc_ip/--bmc_user/--bmc_pass). Use --no_bmc for on-site tests."
    else:
        out("Checking prerequisites ...")
        pause(1)
        checks = [("NVIDIA kernel driver not loaded", not snap["drv"],
                   f"ERROR: NVIDIA kernel modules are loaded ({', '.join(snap['mods'])}). "
                   "Unload them first (rmmod or AI-EasyCfg menu 21)."),
                  ("IOMMU / Intel VT-d disabled", snap["bios"]["vtd"] == "Disabled",
                   "ERROR: IOMMU/VT-d is enabled. Disable 'Intel VT for Directed I/O' in BIOS "
                   "(Socket Configuration > IIO Configuration)."),
                  ("SR-IOV enabled", snap["bios"]["sriov"] == "Enabled",
                   "ERROR: SR-IOV support is disabled. Enable it in BIOS (Advanced > PCI Subsystem Settings).")]
        for name, ok, err in checks:
            pause(0.4)
            out(f"  {name:<44} {'OK' if ok else 'FAIL'}")
            if not ok and not abbruch:
                abbruch = err
    if abbruch:
        out("")
        out(abbruch)
        out("fieldiag aborted.")
        _fd_ende(snap, zeilen, None, level, test_nur, logdir)
        sys.exit(2)

    vorhanden = [(i, g) for i, g in enumerate(snap["gpus"]) if g["sichtbar"] == "ok"]
    nvsw = (m["nvswitch"] - (1 if snap["bb"] and snap["bb"]["status"] == "nvlink" else 0)) if snap["bb"] else 0
    out(f"Discovered {len(vorhanden)} GPU(s)" + (f", {nvsw} NVSwitch(es)" if m["nvswitch"] else "") + f"   SKU: {m['fd_sku']}")
    out("-" * 72)
    tests = ["skucheck", "inforom", "pcie", "gpumem"] + (["connectivity"] if m["nvswitch"] else []) + ["thermal"]
    if level == 2:
        tests += ["gpustress", "pcie_bw"] + (["nvlink_bw"] if m["nvswitch"] else [])
    if test_nur:
        if test_nur not in tests + ["gpustress", "pcie_bw", "nvlink_bw"]:
            out(f"ERROR: unknown test '{test_nur}'")
            sys.exit(2)
        tests = [test_nur]
    fails = []
    for n, t in enumerate(tests, 1):
        pause({"gpumem": 6, "gpustress": 10, "nvlink_bw": 6, "thermal": 4}.get(t, 2) * (2 if level == 2 else 1))
        res, detail, code = "PASS", "", None
        if t == "skucheck":
            if len(vorhanden) != m["gpus"]:
                fehlt = [bus_id(i) for i, g in enumerate(snap["gpus"]) if g["sichtbar"] != "ok"]
                res, code, detail = "FAIL", "000000000040", f"expected {m['gpus']} GPUs, found {len(vorhanden)} (missing {', '.join(fehlt)})"
            else:
                versionen = {}
                for i, g in vorhanden:
                    versionen.setdefault(g["vbios"], []).append(i)
                if len(versionen) > 1:
                    haupt = max(versionen, key=lambda v: len(versionen[v]))
                    abw = [f"GPU{i} {v}" for v, l in versionen.items() if v != haupt for i in l]
                    res, code, detail = "FAIL", "000000000081", f"VBIOS mismatch: {', '.join(abw)} (expected {haupt})"
        elif t == "inforom":
            if m["polling"] and snap.get("polling", True):
                res, code, detail = "FAIL", "000000000818", "SMBus/I2C access timeout (BMC polling active?)"
        elif t == "gpumem":
            bad = [(i, g) for i, g in vorhanden if g["status"] in ("remap_fail", "remap_pending")]
            if bad:
                i, g = bad[0]
                if g["status"] == "remap_fail":
                    res, code, detail = "FAIL", "000000000083", f"GPU{i} SN {g['sn']}: row remapping failure"
                else:
                    res, code, detail = "FAIL", "000000000084", f"GPU{i} SN {g['sn']}: row remap pending – GPU reset required"
        elif t in ("connectivity", "nvlink_bw"):
            if snap["bb"] and snap["bb"]["status"] == "nvlink":
                res, code, detail = "FAIL", "000000000120", f"NVSwitch{snap['bb']['nvswitch_bad']} not found / links down (baseboard SN {snap['bb']['sn']})"
        elif t == "thermal":
            bad = [i for i, f in enumerate(snap["fan"]) if f != "ok"]
            if bad:
                res, code, detail = "FAIL", "000000000210", f"GPU temperature above limit – FAN{bad[0]} not spinning"
        elif t == "gpustress":
            if any(g["status"] == "remap_fail" for _, g in vorhanden):
                res, code, detail = "FAIL", "000000000083", "DBE during stress"
        out(f"[{n:>2}/{len(tests)}] {t:<16} {'.' * 24} {res}" + (f"   {detail}" if detail else ""))
        if res == "FAIL":
            fails.append((t, code, detail))
            if t == "skucheck":
                out("Aborting: skucheck failed.")
                break
    out("=" * 72)
    if fails:
        out("    ########  ######  ##  ##")
        out("    ##       ##    ## ##  ##        FAIL")
        out("    ######   ######## ##  ##")
        out("    ##       ##    ## ##  ######")
        for t, code, detail in fails:
            out(f"  Test: {t:<14} Error Code: {code}   {detail}")
    else:
        out("    #####   ####   ####   ####")
        out("    ##  ## ##  ## ##     ##           PASS")
        out("    #####  ######  ####   ####")
        out("    ##     ##  ##     ##     ##")
        out("    ##     ##  ## #####  #####")
    _fd_ende(snap, zeilen, not fails, level, test_nur or "", logdir, tests)
    sys.exit(0 if not fails else 1)


def _fd_ende(snap, zeilen, ergebnis, level, test_nur, logdir, tests=()):
    ordner = os.path.join(core.VAR, "fieldiag", snap["name"])
    os.makedirs(ordner, exist_ok=True)
    datei = f"fieldiag_{snap['sn']}_{datetime.now():%Y%m%d_%H%M%S}.log"
    print(f"Log: {logdir.rstrip('/')}/{datei}", flush=True)
    with open(os.path.join(ordner, datei), "w") as f:
        f.write("\n".join(zeilen) + "\n")
    if ergebnis is None:
        return
    with core.zustand() as st:
        tk = st.get("ticket")
        if tk and tk["server"] == snap["name"] and not tk["abgeschlossen"]:
            tk["fieldiag"].append({"t": core.now(), "level": level, "pass": ergebnis, "tests": list(tests),
                                   "voll": not test_nur})
            core.aktion(st, f"fieldiag {'--test ' + test_nur if test_nur else 'Level ' + str(level)}: {'PASS' if ergebnis else 'FAIL'}")


# --------------------------------------------------------------------------
# storcli64 / nvme / dmesg / lspci
# --------------------------------------------------------------------------


def storcli(argv):
    with core.zustand() as st:
        s = inband_server(st)
        hdd = s["comp"]["hdd"]
        if not hdd:
            fehler("CLI Version = 007.2807.0000.0000\nStatus = Failure\nDescription = Controller 0 not found")
        cmd = " ".join(argv).lower()
        print("CLI Version = 007.2807.0000.0000 Dec 17, 2023\nOperating system = Linux 6.8.0-45-generic")
        bad = [i for i, d in enumerate(hdd) if d["status"] != "ok"]
        vd_state = "Optl" if not bad else ("Dgrd" if any(hdd[i]["status"] == "fail" for i in bad) else "Rec")
        if "rebuild" in cmd:
            print("Controller = 0\nStatus = Success\n\n-----------------------------------------------------")
            print("Drive-ID    Progress% Status          Estimated Time Left\n-----------------------------------------------------")
            for i, d in enumerate(hdd):
                if "/s" in cmd and f"/s{i} " not in cmd + " " and "/sall" not in cmd:
                    continue
                stt = "In progress" if d["status"] == "rebuild" else "Not in progress"
                pct = core.rebuild_pct(d) if d["status"] == "rebuild" else "-"
                print(f"/c0/e252/s{i} {pct!s:>9} {stt:<15} -")
        elif "/v" in cmd:
            print("Controller = 0\nStatus = Success\n\nVirtual Drives :\n==============")
            print("---------------------------------------------------------\nDG/VD TYPE  State Access Consist Cache  Size")
            print(f" 0/0  RAID6 {vd_state}  RW     Yes     RWBD   43.656 TB\n---------------------------------------------------------")
        elif "/eall/sall" in cmd or "/sall" in cmd:
            _storcli_pds(hdd)
        else:
            print("Controller = 0\nStatus = Success\nProduct Name = MegaRAID 9560-8i")
            print(f"FW Package Build = {s['fw'].get('RAID', '-')}\n\nVD LIST :\n=======")
            print(f" 0/0  RAID6 {vd_state}  RW     Yes     RWBD   43.656 TB\n")
            _storcli_pds(hdd)
        core.markiere_fix(st)


def _storcli_pds(hdd):
    print("PD LIST :\n=======\n-------------------------------------------------------------------------")
    print("EID:Slt DID State DG     Size Intf Med Model                SN")
    print("-------------------------------------------------------------------------")
    for i, d in enumerate(hdd):
        state = {"ok": "Onln", "fail": "Fail", "rebuild": "Rbld"}[d["status"]]
        print(f"252:{i:<3} {10 + i:>3} {state:<5} 0  7.276 TB SAS  HDD ST8000NM017B        {d['sn']}")
    print("-------------------------------------------------------------------------")
    print("Onln=Online|Fail=Failed|Rbld=Rebuild|Dgrd=Degraded|Optl=Optimal")


def nvme(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        if not argv or argv[0] in ("-h", "--help", "help"):
            print("usage: nvme <command> [<device>] [<args>]\n  list            List all NVMe devices and namespaces\n  smart-log       Retrieve SMART Log")
            return
        if argv[0] != "list":
            if argv[0] == "smart-log" and len(argv) > 1:
                print(f"Smart Log for NVME device:{os.path.basename(argv[1])} namespace-id:ffffffff\ncritical_warning : 0\n"
                      "temperature : 34°C\npercentage_used : 3%\nmedia_errors : 0")
                return
            fehler(f"ERROR: Invalid sub-command '{argv[0]}'")
        if not s["comp"]["nvme"]:
            return
        print("Node                  Generic               SN                   Model                                    Namespace  Usage                      Format           FW Rev")
        print("--------------------- --------------------- -------------------- ---------------------------------------- ---------- -------------------------- ---------------- --------")
        for i, d in enumerate(s["comp"]["nvme"]):
            if d["status"] == "ok":
                print(f"/dev/nvme{i}n1          /dev/ng{i}n1            {d['sn']:<20} SAMSUNG MZQL23T8HCLS-00A07             0x1          3.84  TB /   3.84  TB    512   B +  0 B   GDC5602Q")


def dmesg(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        z = [(0.0, "Linux version 6.8.0-45-generic (buildd@lcy02-amd64-075) #45-Ubuntu SMP PREEMPT_DYNAMIC"),
             (0.41, f"DMI: Inspur {s['modell']}/{s['modell']}, BIOS {s['fw']['BIOS']}"),
             (0.62, "DMAR: Intel(R) Virtualization Technology for Directed I/O" if s["bios"]["vtd"] == "Enabled" else "DMAR: IOMMU disabled"),
             (1.2, "EDAC MC0: Giving out device to module i10nm_edac controller Intel_10nm Socket#0 IMC#0")]
        if s.get("drv", True) and s["comp"]["gpu"]:
            z.append((2.8, "nvidia: loading out-of-tree module taints kernel."))
            z.append((2.9, "NVRM: loading NVIDIA UNIX x86_64 Kernel Module  550.90.07"))
        fb = fehlerboot(s)
        for i, g in enumerate(s["comp"]["gpu"]):
            pci = core.GPU_BUS[i]
            sb = sichtbar(s, i)
            if sb == "revff":
                z.append((812.4, f"NVRM: Xid (PCI:0000:{pci}:00): 79, pid='<unknown>', name=<unknown>, GPU has fallen off the bus."))
                z.append((812.4, f"NVRM: GPU 0000:{pci}:00.0: GPU has fallen off the bus."))
            elif sb == "weg":
                z.append((1.9, f"pcieport 0000:{pci[0]}c:01.0: pciehp: Slot(GPU{i}): Link Down"))
            elif fb and g["status"] == "remap_fail":
                z.append((640.1, f"NVRM: Xid (PCI:0000:{pci}:00): 48, pid=4711, name=python3, An uncorrectable double bit error (DBE) has been detected on GPU in the framebuffer at partition 6, subpartition 0."))
                z.append((640.2, f"NVRM: Xid (PCI:0000:{pci}:00): 64, pid=4711, name=python3, Row remapping failure"))
            elif fb and g["status"] == "remap_pending":
                z.append((501.7, f"NVRM: Xid (PCI:0000:{pci}:00): 94, pid=4711, name=python3, Contained: SM (0x1). RST: No, D-RST: No"))
                z.append((501.8, f"NVRM: Xid (PCI:0000:{pci}:00): 63, pid=4711, name=python3, Row remapping event: pending"))
        bb = s["comp"]["baseboard"][0] if s["comp"]["baseboard"] else None
        if bb and bb["status"] == "nvlink":
            sw = core.NVSWITCH_BUS[bb["nvswitch_bad"]]
            z.append((3.4, f"nvidia-nvswitch: Probing device 0000:{sw}:00.0 failed: -5"))
            z.append((3.5, f"nvidia-nvswitch{bb['nvswitch_bad']}: SXid (PCI:0000:{sw}:00.0): 20034, Fatal, Link LTSSM Fault Up"))
        for d in s["comp"]["dimm"]:
            if d["status"] == "ue":
                if fb:
                    z.append((1203.9, f"EDAC MC0: 1 UE memory read error on {d['name']} (page:0x2f1a3 offset:0x0)"))
                    z.append((1203.9, "mce: [Hardware Error]: Machine check events logged"))
                else:
                    z.append((0.9, f"EDAC: DIMM {d['name']} disabled by BIOS (memory device disabled)"))
        for i, d in enumerate(s["comp"]["nvme"]):
            if d["status"] != "ok":
                z.append((955.0, f"nvme nvme{i}: I/O 23 QID 0 timeout, reset controller"))
                z.append((985.2, f"nvme nvme{i}: Removing after probe failure status: -19"))
        for i, d in enumerate(s["comp"]["hdd"]):
            if d["status"] == "fail":
                z.append((700.3, f"megaraid_sas 0000:4b:00.0: 252 (event) PD {10 + i:02x}(e0xfc/s{i}) Path 500304801c5a0e0{i} reset (Type 03)"))
                z.append((700.4, f"megaraid_sas 0000:4b:00.0: PD {10 + i:02x}(e0xfc/s{i}) is now FAILED"))
        for t, txt in sorted(z):
            print(f"[{t + 3.0:>12.6f}] {txt}")


def _lspci_geraete(s):
    m = core.MODELS[s["modell"]]
    g = [("00:00.0", "Host bridge", "Intel Corporation Device 09a2 (rev 04)", None)]
    for i, gpu in enumerate(s["comp"]["gpu"]):
        sb = sichtbar(s, i)
        if sb != "weg":
            g.append((f"{core.GPU_BUS[i]}:00.0", "3D controller", f"NVIDIA Corporation {m['gpu_kurz']} (rev {'ff' if sb == 'revff' else 'a1'})", i))
    if m["nvswitch"]:
        bb = s["comp"]["baseboard"][0]
        for k in range(m["nvswitch"]):
            if not (bb["status"] == "nvlink" and bb["nvswitch_bad"] == k):
                g.append((f"{core.NVSWITCH_BUS[k]}:00.0", "Bridge", "NVIDIA Corporation GA100 [A100 NVSwitch] (rev a1)", None))
    for i, d in enumerate(s["comp"]["nvme"]):
        if d["status"] == "ok":
            g.append((f"{0x60 + i:02x}:00.0", "Non-Volatile memory controller", "Samsung Electronics Co Ltd NVMe SSD Controller PM9A3", None))
    if s["comp"]["hdd"]:
        g.append(("4b:00.0", "RAID bus controller", "Broadcom / LSI MegaRAID 12GSAS/PCIe Secure SAS39xx", None))
    g.append(("b1:00.0", "Ethernet controller", "Mellanox Technologies MT2894 Family [ConnectX-6 Lx]", None))
    return sorted(g)


def lspci(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        if "-h" in argv or "--help" in argv:
            print("Usage: lspci [<switches>]\n  -v/-vv/-vvv  verbose   -t  tree   -s [[dom]:][bus]:[slot][.func]   -d [vendor]:[device]")
            return
        geraete = _lspci_geraete(s)
        if "-d" in argv:
            v = argv[argv.index("-d") + 1] if argv.index("-d") + 1 < len(argv) else ""
            if v.lower().startswith("10de"):
                geraete = [x for x in geraete if "NVIDIA" in x[2]]
        sel = None
        for k, a in enumerate(argv):
            if a == "-s" and k + 1 < len(argv):
                sel = argv[k + 1].lower().removeprefix("0000:")
            elif a.startswith("-vs") and k + 1 < len(argv):
                sel = argv[k + 1].lower().removeprefix("0000:")
        if sel:
            geraete = [x for x in geraete if x[0].startswith(sel) or x[0].startswith(sel + ".")]
        verbose = any(re.fullmatch(r"-v+s?|-vv+.*|-vvv", a) for a in argv)
        if any(a.startswith("-t") for a in argv):
            print("-[0000:00]-+-00.0  Intel Corporation Device 09a2")
            for bdf, typ, name, _ in geraete[1:]:
                print(f"           +-[{bdf[:2]}]-00.0  {name.split(' (rev')[0]}")
            return
        for bdf, typ, name, gi in geraete:
            print(f"{bdf} {typ}: {name}")
            if verbose:
                revff = "(rev ff)" in name
                print(f"\tSubsystem: {'NVIDIA Corporation' if 'NVIDIA' in name else 'Inspur Electronic Information Industry Co., Ltd.'}")
                if revff:
                    print("\t!!! Unknown header type 7f")
                    continue
                print("\tCapabilities: [68] Express (v2) Endpoint, MSI 00")
                print("\t\tLnkCap:\tPort #0, Speed 16GT/s, Width x16, ASPM not supported")
                print("\t\tLnkSta:\tSpeed 16GT/s, Width x16")
                if gi is not None:
                    print("\tKernel driver in use: nvidia" if s.get("drv", True) else "\tKernel modules: nvidiafb, nouveau, nvidia")
                print()


TOOLS = {"ipmitool": ipmitool, "nvidia-smi": nvidia_smi, "fieldiag": fieldiag, "storcli64": storcli,
         "nvme": nvme, "dmesg": dmesg, "lspci": lspci}


def main():
    import signal as sg
    sg.signal(sg.SIGPIPE, sg.SIG_DFL)
    name = os.path.basename(sys.argv[0])
    TOOLS[name](sys.argv[1:])
