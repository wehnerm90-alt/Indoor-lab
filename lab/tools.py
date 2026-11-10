"""Nachbildungen der Werkzeuge: ipmitool, nvidia-smi, fieldiag, storcli64, nvme, dmesg, lspci.

Alle Ausgaben sind Simulationen fuer das Training. Formate sind an die echten
Werkzeuge angelehnt, aber nicht identisch.
"""
import hashlib
import os
import random
import sys
import time
from datetime import datetime

from . import core

GPU_BUS = ["18", "2A", "3A", "5D", "9A", "AB", "BA", "DB"]


def bus_id(i):
    return f"00000000:{GPU_BUS[i]}:00.0"


def gpu_uuid(g):
    h = hashlib.md5(g["sn"].encode()).hexdigest()
    return f"GPU-{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def fehler(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def inband_server(st):
    """Server, auf dessen 'Betriebssystem' wir gerade eingeloggt sind."""
    name = os.environ.get("LAB_SERVER")
    if not name or name not in st["servers"]:
        fehler("Dieser Befehl läuft auf dem Server selbst. Erst einloggen: lab login <server>  (z. B. lab login gpu01)")
    s = st["servers"][name]
    if s["power"] != "on":
        fehler("Connection to host lost: the server is powered off.\n(Server ist aus – einschalten per BMC oder ipmitool chassis power on)")
    return s


# --------------------------------------------------------------------------
# ipmitool
# --------------------------------------------------------------------------

IPMI_HILFE = """ipmitool (Lab-Simulation)
Beispiele:
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel elist
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sdr elist
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin fru print
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin chassis power status|on|off|cycle
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin mc info
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin mc reset cold
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel clear
  ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin lan print 1
Auf dem Server selbst (nach 'lab login') geht auch ohne -H: ipmitool sel elist
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
        elif a in ("-h", "--help"):
            print(IPMI_HILFE)
            return
        else:
            rest.append(a)
            i += 1
    if not rest:
        print(IPMI_HILFE)
        return
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
                   "Error: Unable to establish IPMI v2 / RMCP+ session\n"
                   "(Tipp: Von hier aus mit -I lanplus -H bmc-<server> -U admin -P admin arbeiten)")
        s = st["servers"][name]
    else:
        s = None
        h = host.lower()
        for name, srv in st["servers"].items():
            if h in (name, f"bmc-{name}") or (opts.get("-p") and str(srv["port"]) == opts["-p"]):
                s = srv
        if s is None:
            fehler(f"Error: Unable to establish IPMI v2 / RMCP+ session\n(Unbekannter BMC '{host}'. Vorhanden: "
                   + ", ".join(f"bmc-{n}" for n in st["servers"]) + ")")
        if opts.get("-I") not in ("lanplus", "lan"):
            fehler("Error: Unable to establish IPMI v2 / RMCP+ session\n(Remote braucht -I lanplus)")
        pw = opts.get("-P") or (os.environ.get("IPMI_PASSWORD") if opts.get("-E") else None)
        if opts.get("-U") != core.BMC_USER or pw != core.BMC_PASS:
            fehler("Error: Unable to establish IPMI v2 / RMCP+ session")
    if not core.bmc_bereit(s):
        fehler("Error: Unable to establish IPMI v2 / RMCP+ session\n(BMC startet gerade neu, bitte warten)")
    return s


def _ipmi_befehl(st, s, a):
    cmd = a[0]
    sub = a[1] if len(a) > 1 else ""
    if cmd == "sel":
        if sub in ("list", "elist", ""):
            for e in s["sel"]:
                d, t = core.ts(e["t"]).split(" | ")
                print(f"{e['id']:>4x} | {d} | {t} | {e['typ']} {e['sensor']} | {e['event']} | {e['dir']}")
            if not s["sel"]:
                print("SEL has no entries")
        elif sub == "info":
            print("SEL Information\nVersion          : 1.5 (v1.5, v2 compliant)")
            print(f"Entries          : {len(s['sel'])}\nFree Space       : {65535 - 16 * len(s['sel'])} bytes")
            print(f"Percent Used     : {max(1, len(s['sel']) // 40)}%")
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
            if cmd == "sensor":
                print(f"{name:<17}| {w:<18}| {status}")
            else:
                print(f"{name:<17}| {w:<18}| {status}")
    elif cmd == "fru":
        m = core.MODELS[s["modell"]]
        print("FRU Device Description : Builtin FRU Device (ID 0)")
        print(" Chassis Type          : Rack Mount Chassis")
        print(" Board Mfg             : Inspur (Lab)")
        print(f" Board Product         : {s['modell']}")
        print(" Product Manufacturer  : Inspur (Lab)")
        print(f" Product Name          : {s['modell']} – {m['beschreibung']}")
        print(f" Product Serial        : {s['sn']}")
        for i, p in enumerate(s["comp"]["psu"]):
            print(f"\nFRU Device Description : PSU{i}_FRU (ID {i + 1})")
            print(f" Product Serial        : {p['sn']}")
    elif cmd in ("chassis", "power"):
        if cmd == "power":
            sub = a[1] if len(a) > 1 else "status"
        elif sub == "power":
            sub = a[2] if len(a) > 2 else "status"
        elif sub == "status":
            print(f"System Power         : {s['power']}\nPower Overload       : false\nMain Power Fault     : false")
            fan = any(f["status"] != "ok" for f in s["comp"]["fan"])
            print(f"Cooling/Fan Fault    : {'true' if fan else 'false'}\nFront-Panel Lockout  : inactive")
            return
        _power(st, s, sub)
    elif cmd == "mc":
        if sub == "info":
            maj, mi, pa = s["fw"]["BMC"].split(".")
            print("Device ID                 : 32\nDevice Revision           : 1")
            print(f"Firmware Revision         : {maj}.{mi}\nIPMI Version              : 2.0")
            print(f"Aux Firmware Rev Info     : 0x{int(pa):02x}\n(BMC-Version vollständig: {s['fw']['BMC']})")
        elif sub == "reset":
            core.bmc_reset(st, s)
            print(f"Sent {a[2] if len(a) > 2 else 'cold'} reset command to MC")
        else:
            fehler(f"Invalid mc command: {sub}")
    elif cmd == "lan":
        print("Set in Progress         : Set Complete\nIP Address Source       : Static Address")
        print(f"IP Address              : bmc-{s['name']} (Lab)\nSubnet Mask             : 255.255.255.0")
        mac = hashlib.md5(s["sn"].encode()).hexdigest()[:12]
        print("MAC Address             : " + ":".join(mac[i:i + 2] for i in range(0, 12, 2)))
    else:
        fehler(f"Invalid command: {cmd}\n{IPMI_HILFE}")


def _power(st, s, sub):
    ok, msg = core.power(st, s, sub)
    if not ok:
        fehler(msg)
    print(msg)


# --------------------------------------------------------------------------
# nvidia-smi
# --------------------------------------------------------------------------


def nvidia_smi(argv):
    with core.zustand() as st:
        s = inband_server(st)
        if not s["comp"]["gpu"]:
            fehler("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n"
                   "(Dieser Server hat keine GPUs.)")
        _nvsmi(st, s, argv)
        core.markiere_fix(st)


def _nvsmi(st, s, argv):
    gpus = s["comp"]["gpu"]
    m = core.MODELS[s["modell"]]
    idx = None
    if "-i" in argv:
        try:
            idx = int(argv[argv.index("-i") + 1])
        except (IndexError, ValueError):
            fehler("Invalid GPU index")
    lost = [i for i, g in enumerate(gpus) if g["status"] == "offbus"]

    if "-r" in argv or "--gpu-reset" in argv:
        if idx is None:
            fehler("GPU Reset requires -i <index>.")
        if idx in lost:
            fehler(f"Unable to determine the device handle for GPU {bus_id(idx)}: GPU is lost.")
        g = gpus[idx]
        print(f"GPU {bus_id(idx)} was successfully reset.")
        if g["status"] == "remap_pending":
            g["status"] = "ok"
        core.aktion(st, f"GPU{idx} Reset")
        return
    if lost and not ("-L" in argv or "nvlink" in argv):
        for i in lost:
            print(f"Unable to determine the device handle for GPU{i}: {bus_id(i)}: GPU is lost.  "
                  "Reboot the system to recover this GPU")
        if "-q" not in argv:
            return
    if "-L" in argv:
        for i, g in enumerate(gpus):
            if g["status"] != "offbus":
                print(f"GPU {i}: {m['gpu_name']} (UUID: {gpu_uuid(g)})")
        return
    if "nvlink" in argv:
        bb = s["comp"]["baseboard"][0] if s["comp"]["baseboard"] else None
        if not bb:
            fehler("NVLink is not supported on this system (PCIe-GPUs).")
        for i, g in enumerate(gpus):
            if g["status"] == "offbus":
                continue
            print(f"GPU {i}: {m['gpu_name']} (UUID: {gpu_uuid(g)})")
            for l in range(18):
                tot = bb["status"] == "nvlink" and l * 4 // 18 == bb["nvswitch_bad"]
                print(f"\t Link {l}: " + ("<inactive>" if tot else "26.562 GB/s"))
        return
    if "-q" in argv:
        print(f"\n==============NVSMI LOG==============\n\nTimestamp                                 : {datetime.now():%a %b %d %H:%M:%S %Y}")
        print(f"Driver Version                            : 550.90.07\nAttached GPUs                             : {len(gpus) - len(lost)}")
        for i, g in enumerate(gpus):
            if g["status"] == "offbus" or (idx is not None and i != idx):
                continue
            ue = {"remap_fail": 8, "remap_pending": 1}.get(g["status"], 0)
            print(f"GPU {bus_id(i)}\n    Product Name                          : {m['gpu_name']}")
            print(f"    Serial Number                         : {g['sn']}\n    GPU UUID                              : {gpu_uuid(g)}")
            print(f"    Minor Number                          : {i}\n    Board Part Number                     : {m['gpu_pn']}")
            print(f"    ECC Errors\n        Volatile\n            DRAM Uncorrectable            : {ue}")
            print(f"        Aggregate\n            DRAM Uncorrectable            : {ue}")
            print("    Remapped Rows")
            print(f"        Correctable Error                 : {1 if ue else 0}")
            print(f"        Uncorrectable Error               : {ue}")
            print(f"        Pending                           : {'Yes' if g['status'] == 'remap_pending' else 'No'}")
            print(f"        Remapping Failure Occurred        : {'Yes' if g['status'] == 'remap_fail' else 'No'}")
        return
    # Standardtabelle
    rnd = random.Random(int(time.time() / 5))
    print(f"{datetime.now():%a %b %d %H:%M:%S %Y}")
    print("+-----------------------------------------------------------------------------------------+")
    print("| NVIDIA-SMI 550.90.07    (Lab-Simulation)   Driver Version: 550.90.07    CUDA Version: 12.4 |")
    print("|-----------------------------------------+------------------------+----------------------+")
    print("| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |")
    print("| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |")
    print("|=========================================+========================+======================|")
    mem = "81559MiB" if s["typ"] == "hgx" else "46068MiB"
    cap = "700W" if s["typ"] == "hgx" else "350W"
    for i, g in enumerate(gpus):
        if g["status"] == "offbus":
            continue
        ue = {"remap_fail": 8, "remap_pending": 1}.get(g["status"], 0)
        name = m["gpu_name"].replace("NVIDIA ", "")[:20]
        print(f"| {i:>3}  {name:<20}           On | {bus_id(i)[4:]}   Off | {ue:>20} |")
        print(f"| N/A   {rnd.randint(31, 40)}C    P0  {rnd.randint(65, 120):>13}W / {cap:<5}|      1MiB / {mem:<9}|      0%      Default |")
        print("+-----------------------------------------+------------------------+----------------------+")


# --------------------------------------------------------------------------
# fieldiag
# --------------------------------------------------------------------------


def fieldiag(argv):
    if "-h" in argv or "--help" in argv:
        print("fieldiag (Lab-Simulation)\n  fieldiag --level1      kurzer Test (ca. 15 s)\n"
              "  fieldiag --level2      ausführlicher Test (ca. 40 s)\n  --no_bmc               BMC-Sensoren nicht prüfen")
        return
    level = 2 if "--level2" in argv else 1
    no_bmc = "--no_bmc" in argv
    with core.zustand() as st:
        s = inband_server(st)
        if not s["comp"]["gpu"]:
            fehler("fieldiag: no supported NVIDIA GPUs found on this system.")
        snap = {"name": s["name"], "modell": s["modell"], "typ": s["typ"],
                "gpus": [dict(g) for g in s["comp"]["gpu"]],
                "bb": dict(s["comp"]["baseboard"][0]) if s["comp"]["baseboard"] else None,
                "psu": [p["status"] for p in s["comp"]["psu"]],
                "fan": [f["status"] for f in s["comp"]["fan"]]}
    m = core.MODELS[snap["modell"]]
    zeilen = []

    def out(t=""):
        print(t, flush=True)
        zeilen.append(t)

    def pause(sek):
        if not core.FAST:
            time.sleep(sek * (2.5 if level == 2 else 1))

    out("=" * 72)
    out(f" fieldiag (LAB-SIMULATION) – Level {level}   System: {snap['modell']}  Host: {snap['name']}")
    out(f" Start: {datetime.now():%Y-%m-%d %H:%M:%S}   GPUs erwartet: {m['gpus']}")
    out("=" * 72)
    fails = []
    found = [i for i, g in enumerate(snap["gpus"]) if g["status"] != "offbus"]

    def test(name, ok, detail="", warn=False):
        pause(1.2)
        status = "PASS" if ok else ("WARN" if warn else "FAIL")
        out(f" [{status:^6}] {name:<34} {detail}")
        if not ok and not warn:
            fails.append(name)

    test("Inventory / GPU count", len(found) == m["gpus"],
         f"found {len(found)}/{m['gpus']}" + ("" if len(found) == m["gpus"] else
                                             f"  missing: {', '.join(bus_id(i) for i in range(m['gpus']) if i not in found)}"))
    if len(found) != m["gpus"]:
        out(" !! Inventory mismatch – remaining tests aborted.")
    else:
        test("PCIe link width/speed", True, "Gen5 x16 all GPUs" if snap["typ"] == "hgx" else "Gen4 x16 all GPUs")
        test("PCIe bandwidth (H2D/D2H)", True, "")
        bad = [(i, g) for i, g in enumerate(snap["gpus"]) if g["status"] in ("remap_fail", "remap_pending")]
        det = "; ".join(f"GPU{i} SN {g['sn']}: " + ("row remapping FAILURE – replace GPU" if g["status"] == "remap_fail"
                                                     else "row remap PENDING – GPU reset required")
                        for i, g in bad)
        test("GPU memory (ECC / row remapper)", not bad, det)
        test("Compute stress", not any(g["status"] == "remap_fail" for _, g in bad), "")
        if snap["bb"]:
            if snap["bb"]["status"] == "nvlink":
                test("NVLink / NVSwitch", False,
                     f"NVSwitch{snap['bb']['nvswitch_bad']}: 32 links down – baseboard SN {snap['bb']['sn']}")
            else:
                test("NVLink / NVSwitch", True, "144/144 links up")
        fan_bad = [i for i, f in enumerate(snap["fan"]) if f != "ok"]
        test("Thermal", not fan_bad, f"FAN{fan_bad[0]} not spinning, GPU temp rising" if fan_bad else "", warn=bool(fan_bad))
        if not no_bmc:
            psu_bad = [i for i, p in enumerate(snap["psu"]) if p != "ok"]
            test("BMC sensor check", not psu_bad, f"PSU{psu_bad[0]}_Status critical" if psu_bad else "")
    ergebnis = not fails
    out("-" * 72)
    out(f" RESULT: {'PASS' if ergebnis else 'FAIL'}" + ("" if ergebnis else f"   (failed: {', '.join(fails)})"))
    os.makedirs(os.path.join(core.VAR, "fieldiag"), exist_ok=True)
    log = os.path.join(core.VAR, "fieldiag", f"fieldiag_{snap['name']}_{datetime.now():%Y%m%d_%H%M%S}.log")
    with open(log, "w") as f:
        f.write("\n".join(zeilen) + "\n")
    out(f" Log: {log}")
    with core.zustand() as st:
        tk = st.get("ticket")
        if tk and tk["server"] == snap["name"] and not tk["abgeschlossen"]:
            tk["fieldiag"].append({"t": core.now(), "level": level, "pass": ergebnis})
            core.aktion(st, f"fieldiag Level {level}: {'PASS' if ergebnis else 'FAIL'}")
    sys.exit(0 if ergebnis else 1)


# --------------------------------------------------------------------------
# storcli64 / nvme / dmesg / lspci
# --------------------------------------------------------------------------


def storcli(argv):
    with core.zustand() as st:
        s = inband_server(st)
        hdd = s["comp"]["hdd"]
        if not hdd:
            fehler("CLI Version = 007.2807.0000.0000 (Lab)\nStatus = Failure\nDescription = Controller 0 not found")
        cmd = " ".join(argv).lower()
        print("CLI Version = 007.2807.0000.0000 (Lab-Simulation)\nOperating system = Linux")
        bad = [i for i, d in enumerate(hdd) if d["status"] != "ok"]
        vd_state = "Optl" if not bad else ("Dgrd" if any(hdd[i]["status"] == "fail" for i in bad) else "Rec")
        if "rebuild" in cmd:
            print("Controller = 0\nStatus = Success\n\n-----------------------------------------------------")
            print("Drive-ID    Progress% Status          Estimated Time Left\n-----------------------------------------------------")
            for i, d in enumerate(hdd):
                if "/s" in cmd and f"/s{i}" not in cmd:
                    continue
                st_ = "In progress" if d["status"] == "rebuild" else "Not in progress"
                pct = core.rebuild_pct(d) if d["status"] == "rebuild" else "-"
                print(f"/c0/e252/s{i} {pct!s:>9} {st_:<15} -")
        elif "/v" in cmd:
            print("Controller = 0\nStatus = Success\n\nVirtual Drives :\n==============")
            print("---------------------------------------------------------\nDG/VD TYPE  State Access Consist Cache  Size")
            print(f" 0/0  RAID6 {vd_state}  RW     Yes     RWBD   43.656 TB\n---------------------------------------------------------")
        elif "/eall/sall" in cmd or "/sall" in cmd:
            _storcli_pds(hdd)
        else:
            print("Controller = 0\nStatus = Success\nProduct Name = MegaRAID 9560-8i (Lab)")
            print(f"FW Package Build = {s['fw'].get('RAID', '-')}\n\nVD LIST :\n=======")
            print(f" 0/0  RAID6 {vd_state}  RW     Yes     RWBD   43.656 TB\n")
            _storcli_pds(hdd)
        core.markiere_fix(st)


def _storcli_pds(hdd):
    print("PD LIST :\n=======\n-------------------------------------------------------------------------")
    print("EID:Slt DID State DG     Size Intf Med Model                     SN")
    print("-------------------------------------------------------------------------")
    for i, d in enumerate(hdd):
        state = {"ok": "Onln", "fail": "Fail", "rebuild": "Rbld"}[d["status"]]
        print(f"252:{i:<3} {10 + i:>3} {state:<5} 0  7.276 TB SAS  HDD ST8000NM017B (Lab)      {d['sn']}")
    print("-------------------------------------------------------------------------")
    print("Onln=Online|Fail=Failed|Rbld=Rebuild|Dgrd=Degraded|Optl=Optimal")


def nvme(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        if not s["comp"]["nvme"]:
            fehler("No NVMe devices detected.")
        print("Node          SN              Model                          Usage                  Format")
        print("------------- --------------- ------------------------------ ---------------------- ---------")
        for i, d in enumerate(s["comp"]["nvme"]):
            if d["status"] == "ok":
                print(f"/dev/nvme{i}n1  {d['sn']:<15} SAMSUNG MZQL23T8HCLS (Lab)     3.84 TB / 3.84 TB      512 B")


def dmesg(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        t0 = 3.0
        zeilen = [(0.0, "Linux version 6.8.0-45-generic (Lab-Simulation)"),
                  (0.4, f"DMI: Inspur {s['modell']}/{s['modell']}, BIOS {s['fw']['BIOS']}"),
                  (1.2, "EDAC MC0: Giving out device to module i10nm_edac"),
                  (2.8, "nvidia: loading out-of-tree module taints kernel.")]
        for i, g in enumerate(s["comp"]["gpu"]):
            pci = bus_id(i)[4:].lower()
            if g["status"] == "offbus":
                zeilen.append((812.4, f"NVRM: Xid (PCI:{pci}): 79, pid=0, GPU has fallen off the bus."))
                zeilen.append((812.4, f"NVRM: GPU {pci}: GPU has fallen off the bus."))
            elif g["status"] == "remap_fail":
                zeilen.append((640.1, f"NVRM: Xid (PCI:{pci}): 48, pid=4711, An uncorrectable double bit error (DBE) has been detected"))
                zeilen.append((640.2, f"NVRM: Xid (PCI:{pci}): 64, pid=4711, Row remapping failure"))
            elif g["status"] == "remap_pending":
                zeilen.append((501.7, f"NVRM: Xid (PCI:{pci}): 94, pid=4711, Contained ECC error"))
                zeilen.append((501.8, f"NVRM: Xid (PCI:{pci}): 63, pid=4711, Row remapping event (pending, GPU reset required)"))
        bb = s["comp"]["baseboard"][0] if s["comp"]["baseboard"] else None
        if bb and bb["status"] == "nvlink":
            zeilen.append((302.5, f"nvidia-nvswitch{bb['nvswitch_bad']}: SXid (PCI:0000:{0xc5 + bb['nvswitch_bad']:02x}:00.0): 20034, Fatal, Link 12 LTSSM Fault Up"))
            zeilen.append((302.6, f"nvidia-nvswitch{bb['nvswitch_bad']}: SXid: 22013, Non-fatal, Minion Link DLREQ interrupt"))
        for d in s["comp"]["dimm"]:
            if d["status"] == "ue":
                zeilen.append((1203.9, f"EDAC MC0: 1 UE memory read error on {d['name']} (page:0x2f1a3 offset:0x0)"))
                zeilen.append((1203.9, "mce: [Hardware Error]: Machine check events logged"))
        for i, d in enumerate(s["comp"]["nvme"]):
            if d["status"] != "ok":
                zeilen.append((955.0, f"nvme nvme{i}: I/O 23 QID 0 timeout, reset controller"))
                zeilen.append((985.1, f"nvme nvme{i}: Device not ready; aborting reset, CSTS=0x1"))
                zeilen.append((985.2, f"nvme nvme{i}: Removing after probe failure status: -19"))
        for i, d in enumerate(s["comp"]["hdd"]):
            if d["status"] == "fail":
                zeilen.append((700.3, f"megaraid_sas 0000:4b:00.0: 252 (event) PD {10 + i:02x}(e0xfc/s{i}) is now FAILED"))
        for t, z in sorted(zeilen):
            print(f"[{t + t0:>12.6f}] {z}")


def lspci(argv):
    with core.zustand(schreiben=False) as st:
        s = inband_server(st)
        print("00:00.0 Host bridge: Intel Corporation Device 09a2 (Lab)")
        name = core.MODELS[s["modell"]]["gpu_name"]
        for i, g in enumerate(s["comp"]["gpu"]):
            if g["status"] != "offbus":
                print(f"{GPU_BUS[i].lower()}:00.0 3D controller: NVIDIA Corporation {name.replace('NVIDIA ', '')} (rev a1)")
        for i, d in enumerate(s["comp"]["nvme"]):
            if d["status"] == "ok":
                print(f"{0x60 + i:02x}:00.0 Non-Volatile memory controller: Samsung Electronics Co Ltd NVMe SSD Controller")
        if s["comp"]["hdd"]:
            print("4b:00.0 RAID bus controller: Broadcom / LSI MegaRAID 12GSAS/PCIe Secure SAS39xx")


TOOLS = {"ipmitool": ipmitool, "nvidia-smi": nvidia_smi, "fieldiag": fieldiag, "storcli64": storcli,
         "nvme": nvme, "dmesg": dmesg, "lspci": lspci}


def main():
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    name = os.path.basename(sys.argv[0])
    if name == "fieldiag.sh":
        name = "fieldiag"
    TOOLS[name](sys.argv[1:])
