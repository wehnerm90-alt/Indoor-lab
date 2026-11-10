"""Remote Console (KVM) der nachgebildeten BMCs: Bildschirm-Zustand und Befehle.

Es wird KEINE echte Shell geoeffnet. Erlaubt sind nur die Lab-Werkzeuge und ein paar
harmlose Befehle; Pipes zu grep/head/tail/wc werden in Python nachgebildet.
"""
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime

from . import core

POST_DAUER = 1 if core.FAST else 25
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INBAND = os.path.join(core.BASE, "bin", "inband")
TOOLS = {"nvidia-smi", "fieldiag", "dmesg", "lspci", "nvme", "storcli64"}

HILFE = """Verfügbare Befehle (Lab-Konsole):
  nvidia-smi [-L|-q|-r -i N|nvlink -s]   fieldiag [--level1|--level2]
  dmesg   lspci   nvme list   storcli64 /c0 show   ipmitool sel elist (in-band)
  hostname  uname -a  uptime  date  whoami  ls  clear  reboot  poweroff  exit
  Pipes: | grep [-i] [-v] MUSTER   | head [-n N]   | tail [-n N]   | wc -l"""


def bildschirm(s):
    """-> dict mit mode (off|post|os) und ggf. POST-Zeilen."""
    if s["power"] != "on":
        return {"mode": "off"}
    seit = core.now() - s.get("power_since", 0)
    if seit < POST_DAUER:
        return {"mode": "post", "lines": post_zeilen(s, seit), "pct": int(seit / POST_DAUER * 100)}
    return {"mode": "os", "hostname": s["name"]}


def post_zeilen(s, seit):
    m = core.MODELS[s["modell"]]
    dimms = s["comp"]["dimm"]
    ok_dimms = [d for d in dimms if d["status"] == "ok"]
    z = [f"Inspur {s['modell']}   BIOS Version {s['fw']['BIOS']}   BMC Version {s['fw']['BMC']}",
         "Copyright (C) Inspur (Lab-Simulation)", "",
         "CPU0: Intel(R) Xeon(R) Platinum 8480+ (Lab)   CPU1: Intel(R) Xeon(R) Platinum 8480+ (Lab)"]
    if seit > POST_DAUER * 0.2:
        z.append(f"Memory: {len(ok_dimms) * 64} GB DDR5 detected ({len(ok_dimms)}/{len(dimms)} DIMMs)")
        for d in dimms:
            if d["status"] != "ok":
                z.append(f"  WARNING: DIMM {d['name']} disabled – uncorrectable ECC error logged")
    if seit > POST_DAUER * 0.4:
        for i, f in enumerate(s["comp"]["fan"]):
            if f["status"] != "ok":
                z.append(f"  WARNING: FAN{i} failure detected – check fan module")
        for i, p in enumerate(s["comp"]["psu"]):
            if p["status"] != "ok":
                z.append(f"  WARNING: PSU{i} failure – power redundancy lost")
        if m["gpus"]:
            sichtbar = sum(1 for g in s["comp"]["gpu"] if g["status"] != "offbus")
            z.append(f"PCIe devices: {sichtbar} x {m['gpu_name']} enumerated")
        if m["hdd"]:
            bad = [i for i, d in enumerate(s["comp"]["hdd"]) if d["status"] == "fail"]
            z.append("MegaRAID 9560-8i: VD 0 RAID6 " + ("DEGRADED" if bad else "Optimal"))
            for i in bad:
                z.append(f"  WARNING: PD 252:{i} FAILED")
    if seit > POST_DAUER * 0.6:
        z += ["", "Press <DEL> or <F2> to enter Setup, <F11> Boot Menu, <F12> Network Boot"]
    if seit > POST_DAUER * 0.8:
        z += ["", "Booting from NVMe0 …"]
    return z


def _filter(zeilen, cmd):
    try:
        teile = shlex.split(cmd)
    except ValueError:
        return zeilen, "Syntaxfehler in Pipe"
    if not teile:
        return zeilen, None
    prog, args = teile[0], teile[1:]
    if prog == "grep":
        ign = inv = False
        rest = []
        for a in args:
            if a.startswith("-") and len(a) > 1 and not rest:
                ign |= "i" in a
                inv |= "v" in a
            else:
                rest.append(a)
        if not rest:
            return zeilen, "grep: Muster fehlt"
        try:
            rx = re.compile(rest[0], re.I if ign else 0)
        except re.error:
            rx = re.compile(re.escape(rest[0]), re.I if ign else 0)
        return [z for z in zeilen if bool(rx.search(z)) != inv], None
    if prog in ("head", "tail"):
        n = 10
        for i, a in enumerate(args):
            if a == "-n" and i + 1 < len(args) and args[i + 1].isdigit():
                n = int(args[i + 1])
            elif re.fullmatch(r"-\d+", a):
                n = int(a[1:])
        return (zeilen[:n] if prog == "head" else zeilen[-n:]), None
    if prog == "wc" and "-l" in args:
        return [str(len(zeilen))], None
    if prog in ("less", "more", "cat"):
        return zeilen, None
    return zeilen, f"{prog}: in der Lab-Konsole nicht verfügbar"


def ausfuehren(name, zeile):
    """Befehl auf dem 'Server' ausfuehren. -> (ausgabe, aktion) ; aktion: None|clear|logout"""
    zeile = zeile.strip()
    if not zeile:
        return "", None
    teile = [t.strip() for t in zeile.split("|")]
    try:
        argv = shlex.split(teile[0])
    except ValueError:
        return "-bash: Syntaxfehler", None
    prog, args = argv[0], argv[1:]
    prog = {"./fieldiag.sh": "fieldiag", "fieldiag.sh": "fieldiag"}.get(prog, prog)
    st = core.lesen()
    s = st["servers"][name]
    if prog in ("clear", "reset"):
        return "", "clear"
    if prog in ("exit", "logout"):
        return "", "logout"
    if prog in ("help", "hilfe"):
        out = HILFE
    elif prog == "hostname":
        out = name
    elif prog == "whoami":
        out = "root"
    elif prog == "date":
        out = datetime.now().strftime("%a %b %d %H:%M:%S %Z %Y").strip()
    elif prog == "uname":
        out = f"Linux {name} 6.8.0-45-generic #45-Ubuntu SMP x86_64 GNU/Linux" if "-a" in args else "Linux"
    elif prog == "uptime":
        sek = int(core.now() - s.get("power_since", core.now()))
        out = f" {datetime.now():%H:%M:%S} up {sek // 86400} days, {sek % 86400 // 3600:2d}:{sek % 3600 // 60:02d},  1 user,  load average: 0.08, 0.05, 0.01"
    elif prog == "ls":
        out = "fieldiag  fieldiag.sh  logs" if s["comp"]["gpu"] else "logs"
    elif prog in ("reboot", "poweroff", "shutdown", "halt"):
        with core.zustand() as st2:
            s2 = st2["servers"][name]
            if prog == "reboot" or (prog == "shutdown" and "-r" in args):
                core.power(st2, s2, "cycle")
                return "Broadcast message from root: The system will reboot now!", "reboot"
            core.power(st2, s2, "soft")
            return "Broadcast message from root: The system will power off now!", "off"
    elif prog in TOOLS or prog == "ipmitool":
        if prog == "ipmitool" and "-H" in args:
            return "ipmitool: remote (-H) bitte vom Lab-Container aus nutzen, hier nur in-band", None
        pfad = os.path.join(INBAND, prog) if prog in TOOLS else os.path.join(core.BASE, "bin", "ipmitool")
        env = dict(os.environ, LAB_SERVER=name, PATH=INBAND + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
                   PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run([sys.executable, pfad, *args], env=env, capture_output=True, text=True, timeout=180)
            out = (r.stdout + r.stderr).rstrip("\n")
        except subprocess.TimeoutExpired:
            out = "Zeitüberschreitung"
    else:
        return f"-bash: {prog}: command not found  (help zeigt die verfügbaren Befehle)", None
    zeilen = ANSI.sub("", out).split("\n")
    for f in teile[1:]:
        zeilen, err = _filter(zeilen, f)
        if err:
            return err, None
    return "\n".join(zeilen), None
