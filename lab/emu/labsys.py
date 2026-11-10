"""Lab-Anbindung: baut aus dem Serverzustand ein emuliertes Ubuntu (Kunden-OS) bzw. die AI PE.

Diagnosewerkzeuge (nvidia-smi, lspci, dmesg, ipmitool, nvme, storcli64, fieldiag) laufen als eigene
Lab-Skripte mit fester Argumentliste (nie ueber eine Shell). Alles andere ist reine Emulation.
"""
import json
import os
import posixpath
import re
import subprocess
import sys
import threading
import time

from .. import core
from . import ubuntu, vfs
from .progs.archiv import eintrag, gz_packen, tar_packen
from .progs.base import PROGS, prog, qa
from .progs.coreutils import strftime
from .system import System, binaer
from .vfs import FsError, datei, geraet, link, ordner

INBAND = os.path.join(core.BASE, "bin", "inband")
PE_BIN = os.path.join(core.BASE, "bin", "pe")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PAKETE = {m["fd_paket"]: m for m in core.MODELS.values() if m["fd_paket"]}
MODS_ALLE = ["nvidia_peermem", "nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia"]
MODS_NUTZER = {"nvidia": ["nvidia_uvm", "nvidia_peermem", "nvidia_modeset"], "nvidia_modeset": ["nvidia_drm"]}
MODS_GROESSE = {"nvidia_peermem": 16384, "nvidia_drm": 122880, "nvidia_modeset": 1556480, "nvidia_uvm": 4956160,
                "nvidia": 60678144}
MODS_SONST = [("ipmi_si", 90112, 0, ""), ("ipmi_devintf", 20480, 0, ""), ("ipmi_msghandler", 77824, 2, "ipmi_devintf,ipmi_si"),
              ("mlx5_ib", 495616, 0, ""), ("mlx5_core", 2195456, 1, "mlx5_ib"), ("nvme", 61440, 3, ""),
              ("nvme_core", 212992, 4, "nvme"), ("megaraid_sas", 196608, 2, ""), ("igb", 307200, 0, ""),
              ("ast", 106496, 0, ""), ("drm_kms_helper", 270336, 4, "ast,nvidia_drm"), ("drm", 761856, 6, "drm_kms_helper,ast,nvidia,nvidia_drm"),
              ("ixgbe", 466944, 0, ""), ("ahci", 49152, 0, ""), ("libahci", 53248, 1, "ahci"), ("xhci_pci", 24576, 0, "")]


def tool(name, prog_, args, timeout=240):
    pfad = os.path.join(INBAND, prog_) if prog_ != "ipmitool" else os.path.join(core.BASE, "bin", "ipmitool")
    env = dict(os.environ, LAB_SERVER=name, PYTHONIOENCODING="utf-8",
               PATH=INBAND + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    try:
        r = subprocess.run([sys.executable, pfad, *args], env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", f"{prog_}: timeout\n", 124
    logs = os.path.join(core.VAR, "fieldiag") + "/"
    return (ANSI.sub("", r.stdout).replace(logs, "/root/fieldiag/logs/"),
            ANSI.sub("", r.stderr).replace(logs, "/root/fieldiag/logs/"), r.returncode)


def zustand():
    return core.lesen()


def server(ctx):
    return core.lesen()["servers"][ctx.system.lab_name]


def mods(s):
    if s.get("mods") is None:
        return list(MODS_ALLE) if s.get("drv", True) and s["comp"]["gpu"] else []
    return s["mods"]


def gpus_sichtbar(s):
    from ..tools import sichtbar
    return [p for p in range(len(s["comp"]["gpu"])) if sichtbar(s, p) == "ok"]


# ---------------------------------------------------------------------------- Lab-Programme
def _tool_prog(ctx, prog_name):
    out, err, rc = tool(ctx.system.lab_name, prog_name, ctx.argv[1:])
    ctx.out(out)
    ctx.err(err)
    return rc


@prog("nvidia-smi")
def p_nvidia_smi(ctx):
    s = server(ctx)
    if not s["comp"]["gpu"]:
        ctx.system.cnf_ausgabe(ctx, "nvidia-smi")
        return 127
    a = ctx.argv[1:]
    schleife = None
    for i, x in enumerate(a):
        if x in ("-l", "--loop") and i + 1 < len(a) and a[i + 1].isdigit():
            schleife = int(a[i + 1])
        elif x.startswith("--loop="):
            schleife = int(x.split("=")[1] or 1)
    if schleife:
        rest = [x for j, x in enumerate(a) if not (x in ("-l", "--loop") or x.startswith("--loop=") or
                                                  (j > 0 and a[j - 1] in ("-l", "--loop")))]
        while True:
            out, err, rc = tool(ctx.system.lab_name, "nvidia-smi", rest)
            ctx.out(out)
            ctx.err(err)
            ctx.sleep(schleife)
    return _tool_prog(ctx, "nvidia-smi")


@prog("lspci")
def p_lspci(ctx):
    return _tool_prog(ctx, "lspci")


@prog("dmesg")
def p_dmesg(ctx):
    a = ctx.argv[1:]
    if a[:1] in (["-c"], ["-C"], ["--clear"], ["--read-clear"]):
        return 0
    rest = [x for x in a if x not in ("-T", "--ctime", "-H", "--human", "-w", "--follow", "-L", "--color", "--color=always")]
    out, err, rc = tool(ctx.system.lab_name, "dmesg", rest)
    if "-T" in a or "--ctime" in a:
        boot = ctx.system.boot

        def zeit(m):
            return "[" + time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(boot + float(m.group(1)))) + "]"
        out = re.sub(r"^\[\s*([\d.]+)\]", zeit, out, flags=re.M)
    ctx.out(out)
    ctx.err(err)
    if "-w" in a or "--follow" in a:
        while True:
            ctx.sleep(1)
    return rc


@prog("nvme", verz="/usr/sbin")
def p_nvme(ctx):
    return _tool_prog(ctx, "nvme")


@prog("storcli64", verz="/usr/local/sbin")
def p_storcli(ctx):
    return _tool_prog(ctx, "storcli64")


@prog("ipmitool")
def p_ipmitool(ctx):
    return _tool_prog(ctx, "ipmitool")


@prog("nvidia-bug-report.sh")
def p_bugreport(ctx):
    s = server(ctx)
    ctx.out("\nnvidia-bug-report.sh will now collect information about your\nsystem and create the file "
            "'nvidia-bug-report.log.gz' in the current\ndirectory.  It may take several seconds to run.  In some\n"
            "cases, it may hang trying to capture data generated dynamically\nby the Linux kernel and/or the NVIDIA "
            "kernel module.  While\nthe bug report log file will be incomplete if this happens, it\nmay still contain "
            "enough data to diagnose your problem.\n\nIf nvidia-bug-report.sh hangs, consider running with the "
            "--safe-mode\nand --extra-system-data command line arguments.\n\nPlease include the "
            "'nvidia-bug-report.log.gz' log file when reporting\nyour bug via the NVIDIA Linux forum (see "
            "forums.developer.nvidia.com)\nor by sending email to 'linux-bugs@nvidia.com'.\n\nBy delivering "
            "'nvidia-bug-report.log.gz' to NVIDIA, you acknowledge\nand agree that personal information may "
            "inadvertently be included in\nthe output.  Notwithstanding the foregoing, NVIDIA will use the\noutput "
            "only for the purpose of investigating your reported issue.\n\nRunning nvidia-bug-report.sh...")
    ctx.sleep(4 if not core.FAST else 0)
    teile = []
    for p, a in (("nvidia-smi", ["-q"]), ("lspci", ["-vvv"]), ("dmesg", [])):
        teile.append(f"____________________________________________\n\n{p} {' '.join(a)}\n\n" + tool(ctx.system.lab_name, p, a)[0])
    log = "".join(teile)
    try:
        ctx.fs.schreiben("nvidia-bug-report.log.gz", gz_packen(log), ctx.cwd)
    except FsError as e:
        ctx.out(f"\n\nERROR: Working directory is not writable; please cd to a directory\n       where you have write permission\n")
        return 1
    ctx.out(" complete.\n\n")
    return 0


@prog("lsmod", verz="/usr/sbin")
def p_lsmod(ctx):
    s = server(ctx)
    m = mods(s)
    out = ["Module                  Size  Used by\n"]
    for name in MODS_ALLE:
        if name in m:
            nutzer = [n for n in MODS_NUTZER.get(name, []) if n in m]
            anzahl = len(nutzer) + (1 if name == "nvidia" and s.get("persistenced") else 0) + (
                2 if name == "nvidia" and s["comp"]["baseboard"] and s.get("persistenced") else 0)
            out.append(f"{name:<22}{MODS_GROESSE[name]:>10}  {anzahl}{(' ' + ','.join(nutzer)) if nutzer else ''}\n")
    for name, g, n, by in MODS_SONST:
        if name == "megaraid_sas" and not s["comp"]["hdd"]:
            continue
        by2 = ",".join(x for x in by.split(",") if x and (x not in MODS_ALLE or x in m))
        out.append(f"{name:<22}{g:>10}  {n}{(' ' + by2) if by2 else ''}\n")
    ctx.out("".join(out))
    return 0


def _proc_modules(sysm):
    s = core.lesen()["servers"][sysm.lab_name]
    m = mods(s)
    out = []
    for name in MODS_ALLE:
        if name in m:
            nutzer = [n for n in MODS_NUTZER.get(name, []) if n in m]
            out.append(f"{name} {MODS_GROESSE[name]} {len(nutzer)} {','.join(nutzer) + ',' if nutzer else '-'} Live 0x0000000000000000 (POE)\n")
    for name, g, n, by in MODS_SONST:
        out.append(f"{name} {g} {n} {by + ',' if by else '-'} Live 0x0000000000000000\n")
    return "".join(out)


@prog("rmmod", verz="/usr/sbin")
def p_rmmod(ctx):
    ziele = [x for x in ctx.argv[1:] if not x.startswith("-")]
    if "-h" in ctx.argv or "--help" in ctx.argv:
        ctx.out("Usage:\n\trmmod [options] modulename ...\nOptions:\n\t-f, --force       forces a module unload "
                "and may crash your\n\t                  machine. This requires Forced Module Removal\n\t"
                "                  option in your kernel. DANGEROUS\n\t-s, --syslog      print to syslog, not stderr\n\t"
                "-v, --verbose     enables more messages\n\t-V, --version     show version\n\t-h, --help        show this help\n")
        return 0
    if "-V" in ctx.argv or "--version" in ctx.argv:
        ctx.out("kmod version 31\n+ZSTD +XZ -ZLIB +LIBCRYPTO -EXPERIMENTAL\n")
        return 0
    if not ziele:
        ctx.err("rmmod: ERROR: missing module name.\n")
        return 1
    rc = 0
    with core.zustand() as st:
        s = st["servers"][ctx.system.lab_name]
        m = mods(s)
        s["mods"] = m
        for name in ziele:
            name = name.replace("-", "_").removesuffix(".ko")
            if name not in m:
                bekannt = name in MODS_ALLE or any(name == x[0] for x in MODS_SONST)
                if not bekannt:
                    ctx.err(f"rmmod: ERROR: Module {name} is not currently loaded\n")
                else:
                    if any(name == x[0] for x in MODS_SONST):
                        x = next(x for x in MODS_SONST if x[0] == name)
                        if x[2]:
                            ctx.err(f"rmmod: ERROR: Module {name} is in use by: {x[3].replace(',', ' ')}\n" if x[3] else
                                    f"rmmod: ERROR: Module {name} is in use\n")
                            rc = 1
                            continue
                        continue
                    ctx.err(f"rmmod: ERROR: Module {name} is not currently loaded\n")
                rc = 1
                continue
            nutzer = [n for n in MODS_NUTZER.get(name, []) if n in m]
            if nutzer:
                ctx.err(f"rmmod: ERROR: Module {name} is in use by: {' '.join(nutzer)}\n")
                rc = 1
                continue
            if name == "nvidia" and s.get("persistenced"):
                ctx.err("rmmod: ERROR: Module nvidia is in use\n")
                rc = 1
                continue
            m.remove(name)
        vorher = s.get("drv", True)
        s["drv"] = "nvidia" in m
        if vorher and not s["drv"]:
            core.aktion(st, "NVIDIA-Treiber entladen")
    return rc


@prog("modprobe", verz="/usr/sbin")
def p_modprobe(ctx):
    a = ctx.argv[1:]
    ziele = [x for x in a if not x.startswith("-")]
    if not ziele:
        ctx.err("modprobe: ERROR: missing parameters. See -h.\n")
        return 1
    if "-r" in a or "--remove" in a:
        ctx.argv = ["rmmod"] + ziele
        return p_rmmod(ctx)
    kern = ctx.system.kernel
    with core.zustand() as st:
        s = st["servers"][ctx.system.lab_name]
        m = mods(s)
        s["mods"] = m
        for name in ziele:
            name = name.replace("-", "_")
            if name not in MODS_ALLE and not any(name == x[0] for x in MODS_SONST):
                ctx.err(f"modprobe: FATAL: Module {name} not found in directory /lib/modules/{kern}\n")
                return 1
            if name in MODS_ALLE and not s["comp"]["gpu"]:
                ctx.err(f"modprobe: FATAL: Module {name} not found in directory /lib/modules/{kern}\n")
                return 1
            benoetigt = ["nvidia"] + (["nvidia_modeset"] if name == "nvidia_drm" else []) + ([name] if name in MODS_ALLE else [])
            for b in benoetigt:
                if b not in m:
                    m.append(b)
        s["drv"] = "nvidia" in m
    return 0


@prog("insmod", verz="/usr/sbin")
def p_insmod(ctx):
    a = [x for x in ctx.argv[1:] if not x.startswith("-")]
    if not a:
        ctx.err("insmod: ERROR: missing filename.\n")
        return 1
    ctx.err(f"insmod: ERROR: could not load module {a[0]}: No such file or directory\n")
    return 1


@prog("modinfo", verz="/usr/sbin")
def p_modinfo(ctx):
    a = [x for x in ctx.argv[1:] if not x.startswith("-")]
    if not a:
        ctx.err("modinfo: ERROR: missing module or filename.\n")
        return 1
    name = a[0].replace("-", "_")
    if name == "nvidia":
        ctx.out(f"filename:       /lib/modules/{ctx.system.kernel}/updates/dkms/nvidia.ko.zst\nalias:          "
                "char-major-195-*\nversion:        550.90.07\nsupported:      external\nlicense:        NVIDIA\n"
                "firmware:       nvidia/550.90.07/gsp_tu10x.bin\nfirmware:       nvidia/550.90.07/gsp_ga10x.bin\n"
                "srcversion:     F2D4C5B4A3E1F9B60E1C3A4\ndepends:        \nretpoline:      Y\nname:           nvidia\n"
                f"vermagic:       {ctx.system.kernel} SMP preempt mod_unload modversions \n")
        return 0
    ctx.err(f"modinfo: ERROR: Module {name} not found.\n")
    return 1


@prog("lsof")
def p_lsof(ctx):
    a = ctx.argv[1:]
    s = server(ctx)
    ziele = [x for x in a if not x.startswith("-")]
    zeilen = []
    if s.get("persistenced") and "nvidia" in mods(s):
        zeilen.append(("nvidia-pe", 2214, "/dev/nvidiactl", "195,255", 801, "3u"))
        for k in gpus_sichtbar(s):
            zeilen.append(("nvidia-pe", 2214, f"/dev/nvidia{k}", f"195,{k}", 802 + k, f"{4 + k}u"))
    if s["comp"]["baseboard"] and s["comp"]["baseboard"][0]["status"] == "ok" and "nvidia" in mods(s) and s.get("fm", True):
        zeilen.append(("nv-fabric", 3120, "/dev/nvidia-nvswitchctl", "509,255", 840, "5u"))
    if ziele:
        zeilen = [z for z in zeilen if any(z[2] == t or z[2].startswith(t.rstrip("*")) for t in ziele)]
        fehl = [t for t in ziele if ctx.fs.finde(t, ctx.cwd) is None]
        for t in fehl:
            ctx.err(f"lsof: status error on {t}: No such file or directory\n")
        if fehl and not zeilen:
            ctx.err("lsof 4.95.0\n latest revision: https://github.com/lsof-org/lsof\n latest FAQ: "
                    "https://github.com/lsof-org/lsof/blob/master/00FAQ\n latest (non-formatted) man page: "
                    "https://github.com/lsof-org/lsof/blob/master/Lsof.8\n usage: [-?abhKlnNoOPRtUvVX] [+|-c c] "
                    "[+|-d s] [+D D] [+|-E] [+|-e s] [+|-f[gG]]\n [-F [f]] [-g [s]] [-i [i]] [+|-L [l]] [+m [m]] "
                    "[+|-M] [-o [o]] [-p s]\n [+|-r [t]] [-s [p:s]] [-S [t]] [-T [t]] [-u s] [+|-w] [-x [fl]] [--] "
                    "[names]\nUse the ``-h'' option to get more help information.\n")
            return 1
    elif not zeilen:
        zeilen = []
    if not ziele:
        zeilen = [("systemd", 1, "/", "253,0", 2, "cwd"), ("bash", ctx.system.shell_pid or 4000, "/dev/tty1", "4,1", 25, "0u")] + zeilen
    if not zeilen:
        return 1
    ctx.out("COMMAND    PID USER   FD   TYPE  DEVICE SIZE/OFF NODE NAME\n")
    for cmd, pid, name, dev, node, fd in zeilen:
        typ = "DIR" if name == "/" else "CHR"
        ctx.out(f"{cmd:<9} {pid:>4} root {fd:>4}   {typ:<4} {dev:>7}      0t0 {node:>4} {name}\n")
    return 0


# ---- fieldiag, nvflash, AI-EasyCfg (Programme auf dem Stick / im Paket) ----
@prog("fieldiag", verz="/nonexistent")
def p_fieldiag(ctx):
    pfad = ctx.argv[0]
    try:
        _, real = ctx.fs.aufloesen(pfad, ctx.cwd)
    except FsError:
        real = ctx.fs.abs(pfad, ctx.cwd)
    paketdir = posixpath.dirname(real)
    paket = None
    readme = ctx.fs.finde(paketdir + "/README.txt")
    if readme is not None:
        m = re.search(r"NVIDIA Field Diagnostic (\S+)", readme.inhalt())
        paket = m.group(1) if m else None
    if paket is None:
        paket = posixpath.basename(paketdir)
    a = ctx.argv[1:]
    if "-h" in a or "--help" in a:
        from ..tools import FD_HILFE
        ctx.out(FD_HILFE)
        return 0
    if ctx.system.os != "pe":
        ctx.err("ERROR: fieldiag must be run from the NVIDIA-provided diagnostic OS (AI PE).\n")
        return 1
    if ctx.fs.finde(paketdir + "/logs") is None:
        try:
            ctx.fs.anlegen(paketdir + "/logs", ordner())
        except FsError:
            pass
    env = dict(os.environ, LAB_SERVER=ctx.system.lab_name, PYTHONIOENCODING="utf-8", LAB_FD_PAKET=paket,
               LAB_FD_LOGDIR=paketdir + "/logs")
    vorher = set()
    ordner_real = os.path.join(core.VAR, "fieldiag", ctx.system.lab_name)
    if os.path.isdir(ordner_real):
        vorher = set(os.listdir(ordner_real))
    proc = subprocess.Popen([sys.executable, os.path.join(PE_BIN, "fieldiag"), *a], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for zeile in proc.stdout:
            ctx.check()
            ctx.out(zeile)
        rc = proc.wait()
    except BaseException:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    if os.path.isdir(ordner_real):
        for name in sorted(set(os.listdir(ordner_real)) - vorher):
            try:
                inhalt = open(os.path.join(ordner_real, name), encoding="utf-8").read()
                ctx.fs.schreiben(f"{paketdir}/logs/{name}", inhalt)
            except (OSError, FsError):
                pass
    return rc


@prog("nvflash", verz="/nonexistent")
def p_nvflash(ctx):
    a = ctx.argv[1:]
    with core.zustand(schreiben=False) as st:
        s = st["servers"][ctx.system.lab_name]
    m = core.MODELS[s["modell"]]
    kopf = ("NVIDIA Firmware Update Utility (Version 5.814.0)\nCopyright (C) 1993-2023, NVIDIA Corporation. "
            "All rights reserved.\n\n")
    if not a or "-h" in a or "--help" in a or "-?" in a:
        ctx.out(kopf + "nvflash [options] [filename]\n  --version, -v      Display version information about "
                "the firmware on the adapters.\n  --list, -a         List all NVIDIA display adapters found "
                "in the system.\n  --save, -b         Read the current firmware and save it to the specified file.\n"
                "  --index, -i        Force a specific device index.\n  --overridesub, -6  Override Subsystem ID "
                "mismatch.\n")
        return 0
    ctx.out(kopf)
    if "nvidia" in mods(s):
        ctx.out("ERROR: The NVIDIA kernel driver is loaded. nvflash cannot access the GPUs.\n"
                "       Unload it first: rmmod nvidia_peermem nvidia_drm nvidia_modeset nvidia_uvm nvidia\n")
        return 1
    gpus = gpus_sichtbar(s)
    idx = None
    for i, x in enumerate(a):
        if x in ("-i", "--index") and i + 1 < len(a):
            if not a[i + 1].isdigit():
                ctx.out("ERROR: invalid index\n")
                return 1
            idx = int(a[i + 1])
        elif x.startswith("--index="):
            idx = int(x.split("=")[1])
    if idx is not None and not 0 <= idx < len(gpus):
        ctx.out(f"ERROR: Adapter index {idx} not found\n")
        return 1
    dev_id = "20B7" if "A30" in m["gpu_name"] else "20B2"
    if any(x in a for x in ("--version", "-v", "--list", "-a")):
        for n, p in enumerate(gpus if idx is None else [gpus[idx]]):
            g = s["comp"]["gpu"][p]
            ctx.out(f"<{n if idx is None else idx}> {m['gpu_name']} (10DE,{dev_id},10DE,147F) "
                    f"S:00,B:{core.GPU_BUS[p].upper()},D:00,F:00\n")
            if "--version" in a or "-v" in a:
                ctx.out(f"    Version               : {g['vbios']}\n")
                if g.get("vbios_pending"):
                    ctx.out(f"    Pending update        : {g['vbios_pending']} (takes effect after reboot)\n")
        return 0
    if "--save" in a or "-b" in a:
        if idx is None:
            ctx.out("ERROR: --save requires -i <index> when more than one adapter is present\n")
            return 1
        k = a.index("--save") if "--save" in a else a.index("-b")
        datei_ = a[k + 1] if k + 1 < len(a) else ""
        if not datei_:
            ctx.out("ERROR: missing file name\n")
            return 1
        g = s["comp"]["gpu"][gpus[idx]]
        try:
            ctx.fs.schreiben(datei_, f"NVROM model={s['modell']} ver={g['vbios']}\n" + "\0" * 64, ctx.cwd)
        except FsError as e:
            ctx.out(f"ERROR: Unable to open file {datei_} for writing: {e.msg}\n")
            return 1
        ctx.out(f"Identifying EEPROM...\nEEPROM ID (EF,6015) : WBond W25Q32FW 1.65-1.95V 32768Kx1S, page\n"
                f"Reading adapter firmware image...\nIFR Data Size         : 2048 bytes\nIFR CRC32             : B9D9B0D9\n"
                f"Image Size            : 1048576 bytes\nVersion               : {g['vbios']}\n"
                f"Firmware image saved to {datei_}\n")
        return 0
    rom = next((x for x in a if not x.startswith("-") and not x.isdigit()), None)
    if rom is None:
        ctx.out("ERROR: no operation specified\n")
        return 1
    if idx is None and len(gpus) > 1:
        ctx.out(f"NVIDIA display adapters present in system:\n")
        for n, p in enumerate(gpus):
            ctx.out(f"<{n}> {m['gpu_name']} (10DE,{dev_id},10DE,147F) S:00,B:{core.GPU_BUS[p].upper()},D:00,F:00\n")
        ctx.out("\nERROR: multiple adapters found – specify the target with -i <index>\n")
        return 1
    idx = idx or 0
    try:
        inhalt = ctx.lesen(rom)
    except FsError as e:
        ctx.out(f"ERROR: Unable to open file {rom}: {e.msg}\n")
        return 1
    mm = re.search(r"model=(\S+) ver=(\S+)", inhalt)
    if not mm:
        ctx.out("ERROR: Invalid firmware image\n")
        return 1
    if mm.group(1) != s["modell"]:
        ctx.out("ERROR: Board ID mismatch – image is for a different board\n")
        return 1
    if s.get("gpu_wp", True) and m["polling"]:
        ctx.out("ERROR: EEPROM is write protected.\n"
                "       Disable the GPU write protection first (AI-EasyCfg menu 29 on NF5488M6/NF5688M6/NF5488A5).\n")
        return 1
    ver = mm.group(2)
    phys = gpus[idx]
    g = s["comp"]["gpu"][phys]
    ctx.out(f"Adapter: {m['gpu_name']} (10DE,{dev_id},10DE,147F) S:00,B:{core.GPU_BUS[phys].upper()},D:00,F:00\n\n"
            f"Current      - Version:{g['vbios']} ID:10DE:{dev_id}:10DE:147F\n"
            f"Replace with - Version:{ver} ID:10DE:{dev_id}:10DE:147F\n\nUpdate display adapter firmware?\n"
            "Press 'y' to confirm (any other key to abort): ")
    antwort = _ein_zeichen(ctx)
    ctx.out(antwort + "\n" if antwort and antwort.isprintable() else "\n")
    if antwort.lower() != "y":
        ctx.out("Update aborted by user.\n")
        return 1
    for schritt in ("Programming UPR image ...", "Programming firmware image ...", "Verifying update ..."):
        ctx.out(schritt + "\n")
        ctx.sleep(0 if core.FAST else 1.5)
    with core.zustand() as st2:
        g2 = st2["servers"][ctx.system.lab_name]["comp"]["gpu"][phys]
        g2["vbios_pending"] = ver if ver != g2["vbios"] else None
        core.aktion(st2, f"VBIOS {ver} auf GPU{phys} geschrieben (nvflash)")
    ctx.out(f"Update successful.\n\nNOTE: Firmware update is complete. A reboot is required for the new firmware "
            f"({ver}) to take effect.\n")
    return 0


def _ein_zeichen(ctx):
    if ctx.stdin.isatty and ctx.tty is not None:
        ctx.tty.modus(canon=False, echo=False)
        try:
            k = ctx.tty.read_key(pruef_sigint=False)
        finally:
            ctx.tty.modus(canon=True, echo=True)
        if k == "\x03":
            from .streams import Interrupted
            raise Interrupted()
        return k or ""
    z = ctx.stdin.readline()
    return z[:1]


EASY_MENU = ("=" * 62 + "\n                       AI-EasyCfg V2.2\n" + "=" * 62 + "\n"
             "  1. System information            2. Log collection\n"
             "  5. cuda samples                  6. fieldiag\n"
             "  7. dcgm                          8. gpu_burn\n"
             " 21. Unload NVIDIA driver         23. Reinstall NVIDIA driver\n"
             " 24. BMC polling off              25. BMC polling on\n"
             " 29. GPU write protection (NF5488A5/NF5688M6/NF5488M6)\n"
             "  0. Exit\n" + "-" * 62 + "\n")


@prog("ai-easycfg", verz="/nonexistent")
def p_easycfg(ctx):
    name = ctx.system.lab_name
    while True:
        ctx.out(EASY_MENU)
        ctx.out("Please input your choice: ")
        z = ctx.stdin.readline()
        if z == "":
            ctx.out("\n")
            return 0
        w = z.strip()
        with core.zustand(schreiben=False) as st:
            s = st["servers"][name]
        m = core.MODELS[s["modell"]]
        if w in ("0", "q", "exit", "quit"):
            return 0
        if w == "1":
            ctx.out(f"Model: {s['modell']}   SN: {s['sn']}   BIOS: {s['fw']['BIOS']}   BMC: {s['fw']['BMC']}\n"
                    f"GPUs: {len(gpus_sichtbar(s))}/{m['gpus']} x {m['gpu_name'] or '-'}   "
                    f"Driver: {'loaded' if 'nvidia' in mods(s) else 'not loaded'}\n")
        elif w == "2":
            ziel = f"/iei/AI-EasyCfg/logs/{ctx.system.hostname}-{s['sn']}"
            ctx.out("Collecting logs (nvidia-bug-report, lspci, dmesg, ipmitool sel) ...\n")
            ctx.sleep(0 if core.FAST else 3)
            for d, (p, args) in {"lspci.txt": ("lspci", ["-vvv"]), "dmesg.txt": ("dmesg", []),
                                 "sel.txt": ("ipmitool", ["sel", "elist"]), "nvidia-smi.txt": ("nvidia-smi", ["-q"])}.items():
                _mkdir_p(ctx, ziel)
                ctx.fs.schreiben(f"{ziel}/{d}", tool(name, p, args)[0])
            ctx.fs.schreiben(f"{ziel}/pcie_width_error.log", "")
            ctx.out(f"Done. Logs: {ziel}\n")
        elif w == "6":
            pakete = []
            ins = ctx.fs.finde("/inspur")
            if ins is not None and ins.typ == "d":
                for k, n in ins.eintraege().items():
                    if n.typ == "d" and ctx.fs.finde(f"/inspur/{k}/fieldiag.sh") is not None:
                        pakete.append(f"/inspur/{k}")
            if not pakete:
                ctx.out("fieldiag package not found in /inspur – copy and extract it first.\n")
            else:
                sub = ctx.sh.subshell()
                sub.run(f"cd {pakete[0]} && ./fieldiag.sh --no_bmc --level1", dict(ctx.fds))
        elif w in ("5", "7", "8"):
            ctx.out("This test is not available in the lab – use fieldiag (6).\n")
        elif w == "21":
            with core.zustand() as st2:
                s2 = st2["servers"][name]
                s2["persistenced"] = False
                s2["mods"] = []
                s2["drv"] = False
                core.aktion(st2, "NVIDIA-Treiber entladen (AI-EasyCfg 21)")
            ctx.out("Stopping nvidia-persistenced ... done\n"
                    "rmmod nvidia_peermem nvidia_drm nvidia_modeset nvidia_uvm nvidia ... done\n")
        elif w == "23":
            with core.zustand() as st2:
                s2 = st2["servers"][name]
                s2["mods"] = list(MODS_ALLE)
                s2["drv"] = True
            ctx.out("Installing NVIDIA driver 550.90.07 ... done (modules loaded)\n")
        elif w in ("24", "25"):
            if not m["polling"]:
                ctx.out(f"Not supported on {s['modell']} (only NF5488M6/NF5688M6/NF5688LM6).\n")
            else:
                an = w == "25"
                with core.zustand() as st2:
                    st2["servers"][name]["polling"] = an
                    core.aktion(st2, "BMC-Polling " + ("an" if an else "aus") + " (AI-EasyCfg)")
                ctx.out(f"BMC polling {'enabled (fan control: auto)' if an else 'disabled – fans at 100 %, remember to re-enable!'}\n")
        elif w == "29":
            if not m["polling"]:
                ctx.out(f"Not supported on {s['modell']}.\n")
            else:
                with core.zustand() as st2:
                    s2 = st2["servers"][name]
                    s2["gpu_wp"] = not s2.get("gpu_wp", True)
                    neu = s2["gpu_wp"]
                ctx.out(f"GPU write protection is now {'ENABLED' if neu else 'DISABLED'}.\n")
        elif w:
            ctx.out("Invalid choice.\n")


def _mkdir_p(ctx, pfad):
    akt = ""
    for t in [x for x in pfad.split("/") if x]:
        akt += "/" + t
        if ctx.fs.finde(akt) is None:
            ctx.fs.anlegen(akt, ordner())


@prog("dcgmi")
def p_dcgmi(ctx):
    ctx.err("Error: unable to establish a connection to the specified host: localhost\n"
            "Error: Unable to connect to host engine. Host engine connection invalid/disconnected.\n")
    return 255


# ---------------------------------------------------------------------------- Profil (Konsole)
FS_STORE = {}


def paket_dateien(paket):
    m = PAKETE[paket]
    teil = "hgx-a100-8-gpu" if m["typ"] == "hgx" else "a30-pcie"
    return {
        "fieldiag.sh": ("#!/bin/bash\n# NVIDIA Field Diagnostic launcher\ncd \"$(dirname \"$0\")\" || exit 1\n"
                        "exec ./fieldiag \"$@\"\n", 0o755, None),
        "fieldiag": (binaer("fieldiag"), 0o755, 48213376),
        "README.txt": (f"NVIDIA Field Diagnostic {paket}\nSupported SKU: {m['fd_sku']}\n\n"
                       "Usage:\n  ./fieldiag.sh --no_bmc --level1     quick test\n  ./fieldiag.sh --no_bmc --level2     full test\n"
                       "  ./fieldiag.sh --no_bmc --test gpumem\n  ./fieldiag.sh --no_bmc --test skucheck\n\n"
                       "Before running: VT-d/IOMMU off, SR-IOV on, MIG off, NVIDIA driver unloaded.\n"
                       "All GPUs must have the same VBIOS version (skucheck aborts otherwise).\n", 0o644, None),
        f"testargs_{teil}.json": ('{\n  "test_args": {"nvlink_lanes": 4, "pcie_width": 16}\n}\n', 0o644, None),
        "spec_" + paket.split("-FLD-")[1] + ".json": ('{ "sku": "' + m["fd_sku"] + '" }\n', 0o644, None),
        "lib/libnvdiag.so": ("\x7fELF\x02\x01\x01\x00shared library", 0o755, 91234560),
        "firmware/README": ("Firmware images used by fieldiag (do not modify)\n", 0o644, None),
    }


def paket_archiv(paket):
    t = 1715000000.0
    eintraege = [{"p": paket + "/", "t": "d", "m": 0o755, "mt": t}, {"p": paket + "/logs/", "t": "d", "m": 0o755, "mt": t},
                 {"p": paket + "/lib/", "t": "d", "m": 0o755, "mt": t},
                 {"p": paket + "/firmware/", "t": "d", "m": 0o755, "mt": t}]
    for name, (inhalt, mode, groesse) in paket_dateien(paket).items():
        e = {"p": f"{paket}/{name}", "t": "-", "m": mode, "mt": t, "d": inhalt}
        if groesse:
            e["g"] = groesse
        eintraege.append(e)
    return gz_packen(tar_packen(eintraege))


CPU = {"NF5688M6": ("Intel(R) Xeon(R) Platinum 8358 CPU @ 2.60GHz", 32, 2),
       "NF5468M6": ("Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz", 28, 2),
       "NF5280M7": ("Intel(R) Xeon(R) Gold 6430", 32, 2)}


class LabProfil:
    def __init__(self, name):
        st = core.lesen()
        s = st["servers"][name]
        self.name = name
        self.os = s.get("os", "prod")
        pe = self.os == "pe"
        self.pe = pe
        key = (name, "pe", s.get("boot_id")) if pe else (name, "prod", s["sn"])
        sysm = FS_STORE.get(key)
        cpu, kerne, sockel = CPU.get(s["modell"], CPU["NF5280M7"])
        if sysm is None:
            sysm = System(hostname="localhost" if pe else name,
                          kernel="5.15.0-91-generic" if pe else "6.8.0-45-generic",
                          kernel_version="#101-Ubuntu SMP Tue Nov 14 13:30:08 UTC 2023" if pe else
                          "#45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug 30 12:02:04 UTC 2024",
                          cpus=kerne * sockel * 2, ip=f"10.20.30.{11 + list(core.SERVERS).index(name)}")
            sysm.lab_name = name
            sysm.os = self.os
            ubuntu.grundsystem(sysm, pe=pe, hostname=sysm.hostname)
            self._lab_dateien(sysm, s, pe)
            self.name = name
            self.pe = pe
            self.cpu = (cpu, kerne, sockel)
            self._hooks(sysm, s, pe)
            sysm.alle_installieren()
            for p in ("/nonexistent",):
                pass
            FS_STORE[key] = sysm
            # alte Eintraege aufraeumen
            for k in [k for k in FS_STORE if k[0] == name and k != key and (k[1] == "pe" or k[2] != s["sn"])]:
                FS_STORE.pop(k, None)
        sysm.os = self.os
        sysm.boot = s.get("power_since", time.time()) + 25
        sysm.hostname = sysm.fs.finde("/etc/hostname").inhalt().strip() if sysm.fs.finde("/etc/hostname") else sysm.hostname
        sysm._prozesse = {}
        sysm.basis_prozesse()
        if not pe:
            tmp = sysm.fs.finde("/tmp")
            if tmp is not None and getattr(sysm, "_letzter_boot", None) != s.get("boot_id"):
                tmp.kinder.clear()
        sysm._letzter_boot = s.get("boot_id")
        self.system = sysm
        self.cpu = (cpu, kerne, sockel)
        self._hooks(sysm, s, pe)
        ubuntu.proc_dev(sysm, extra_dev=lambda: self._dev(sysm), extra_proc=None)
        self.ls_colors = ubuntu.lade_ls_colors()

    # ---------------- Dateien ----------------
    def _lab_dateien(self, sysm, s, pe):
        fs = sysm.fs
        t = 1725000000.0

        def setze(p, n):
            ubuntu._setze(fs, p, n)
            n.mtime = n.atime = n.ctime = t
            return n
        if pe:
            setze("/iei", ordner())
            setze("/iei/README.txt", datei("AI PE test tools\n  AI-EasyCfg/   chmod -R +x AI-EasyCfg/ ; cd AI-EasyCfg/ ; ./ai-easyCfg\n"
                                           "  nvflash/      ./nvflash.sh --version | -i <n> --save <file.rom> | -i <n> <file.rom>\n"
                                           "Copy the fieldiag package from the stick (/media) to /inspur and extract it there.\n"))
            setze("/iei/AI-EasyCfg", ordner())
            setze("/iei/AI-EasyCfg/ai-easyCfg", datei("#!/bin/bash\n# AI-EasyCfg V2.2\n#LABPROG:ai-easycfg\n", 0o644))
            setze("/iei/AI-EasyCfg/README.txt", datei("AI-EasyCfg V2.2\n 2 Log collection   6 fieldiag   21 unload driver   "
                                                      "23 reinstall driver\n 24/25 BMC polling off/on (NF5488M6/NF5688M6/NF5688LM6)"
                                                      "   29 GPU write protection\n", 0o644))
            setze("/iei/AI-EasyCfg/config", ordner())
            setze("/iei/AI-EasyCfg/config/models.cfg", datei("NF5488M6\nNF5688M6\nNF5688LM6\nNF5488A5\n", 0o644))
            setze("/iei/AI-EasyCfg/logs", ordner())
            for x in ("cuda_samples", "gpu_burn", "tools"):
                setze(f"/iei/AI-EasyCfg/{x}", ordner())
            setze("/iei/nvflash", ordner())
            setze("/iei/nvflash/nvflash.sh", datei("#!/bin/bash\ncd \"$(dirname \"$0\")\" || exit 1\nexec ./nvflash \"$@\"\n", 0o755))
            nf = setze("/iei/nvflash/nvflash", datei(binaer("nvflash"), 0o755))
            nf.groesse = 6218824
            setze("/inspur", ordner())
            setze("/media", ordner(0o777))
            for paket in PAKETE:
                f = setze(f"/media/{paket}.tgz", datei(paket_archiv(paket), 0o777))
                f.groesse = 1873225043 if PAKETE[paket]["typ"] == "hgx" else 1290772311
            setze("/media/README_Stick.txt", datei("NTFS data partition of the AI PE stick. fieldiag packages for HGX A100 "
                                                   "and A30 PCIe.\n", 0o777))
            setze("/media/System Volume Information", ordner(0o777))
        else:
            fs.finde("/etc/passwd").data += "svc_ops:x:1000:1000:Ops Service Account,,,:/home/svc_ops:/bin/bash\n"
            fs.finde("/etc/group").data += "svc_ops:x:1000:\n"
            sysm.users["svc_ops"] = (1000, 1000, "/home/svc_ops", "/bin/bash")
            sysm.gruppen["svc_ops"] = 1000
            h = setze("/home/svc_ops", ordner(0o750))
            h.uid = h.gid = 1000
            setze("/home/svc_ops/.ssh", ordner(0o700)).uid = 1000
            setze("/home/svc_ops/.ssh/authorized_keys", datei("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...customer-ops\n", 0o600)).uid = 1000
            setze("/opt/customer", ordner())
            setze("/opt/customer/README", datei("Customer production system. Do not install software or modify data.\n"))
            setze("/opt/MegaRAID", ordner()) if s["comp"]["hdd"] else None
            if s["comp"]["gpu"]:
                setze("/etc/modprobe.d/nvidia-graphics-drivers-kms.conf",
                      datei("# This file was generated by nvidia-driver-550\n# Set value to 0 to disable modesetting\n"
                            "options nvidia-drm modeset=1\n"))
                setze("/var/log/nvidia-installer.log", datei("nvidia-installer log file '/var/log/nvidia-installer.log'\n"))
            if s["comp"]["baseboard"]:
                setze("/usr/share/nvidia/nvswitch/fabricmanager.cfg", datei("LOG_LEVEL=4\nLOG_FILE_NAME=/var/log/fabricmanager.log\n"
                                                                             "FABRIC_MODE=0\n"))
        sysm.users.setdefault("root", (0, 0, "/root", "/bin/bash"))

    def _dev(self, sysm):
        s = core.lesen()["servers"][sysm.lab_name]
        d = {}
        for i, n in enumerate(s["comp"]["nvme"]):
            if n["status"] == "ok":
                d[f"nvme{i}"] = geraet(240, i, 0o600)
                d[f"nvme{i}n1"] = geraet(259, i * 4, 0o660, "b", gid=6)
                if i == 0 and not self.pe:
                    for p in (1, 2, 3):
                        d[f"nvme0n1p{p}"] = geraet(259, p, 0o660, "b", gid=6)
        if s["comp"]["hdd"]:
            d["sda"] = geraet(8, 0, 0o660, "b", gid=6)
        if self.pe:
            d["sdb"] = geraet(8, 16, 0o660, "b", gid=6)
            d["sdb1"] = geraet(8, 17, 0o660, "b", gid=6)
            d["sdb2"] = geraet(8, 18, 0o660, "b", gid=6)
        if s["comp"]["gpu"] and "nvidia" in mods(s) or (s["comp"]["gpu"] and not self.pe):
            for k in gpus_sichtbar(s):
                d[f"nvidia{k}"] = geraet(195, k, 0o666)
            d["nvidiactl"] = geraet(195, 255, 0o666)
            if "nvidia_uvm" in mods(s):
                d["nvidia-uvm"] = geraet(508, 0, 0o666)
                d["nvidia-uvm-tools"] = geraet(508, 1, 0o666)
            if s["comp"]["baseboard"]:
                d["nvidia-nvswitchctl"] = geraet(509, 255, 0o666)
                for k in range(core.MODELS[s["modell"]]["nvswitch"]):
                    d[f"nvidia-nvswitch{k}"] = geraet(509, k, 0o666)
        if not self.pe:
            d["mapper"] = ordner()
            d["mapper"].kinder["ubuntu--vg-ubuntu--lv"] = link("../dm-0")
            d["dm-0"] = geraet(252, 0, 0o660, "b", gid=6)
        for k in d.values():
            k.mtime = k.atime = k.ctime = sysm.boot
        return d

    # ---------------- Hooks ----------------
    def _hooks(self, sysm, s, pe):
        name = self.name
        h = sysm.hooks
        cpu, kerne, sockel = self.cpu

        def lab():
            return core.lesen()["servers"][name]

        def mem():
            ss = lab()
            gb = sum(1 for d in ss["comp"]["dimm"] if d["status"] == "ok") * 64
            ges = gb * 1024 * 1024 - 7 * 1024 * 1024
            return (ges, 7_812_448 + 2_103_000 * len(ss["comp"]["gpu"]), 41_224, 9_341_128, 0 if pe else 8 * 1024 * 1024)
        h["mem"] = mem

        def meminfo():
            g, u, sh, c, sw = mem()
            return (f"MemTotal:       {g:>8} kB\nMemFree:        {g - u - c:>8} kB\nMemAvailable:   {g - u:>8} kB\n"
                    f"Buffers:          {182340:>8} kB\nCached:         {c:>8} kB\nSwapCached:            0 kB\n"
                    f"SwapTotal:      {sw:>8} kB\nSwapFree:       {sw:>8} kB\nShmem:          {sh:>8} kB\n"
                    f"HugePages_Total:       0\nHugepagesize:       2048 kB\n")
        h["meminfo"] = meminfo

        def cpuinfo():
            out = []
            for i in range(min(sysm.cpus, 8)):
                out.append(f"processor\t: {i}\nvendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 106\n"
                           f"model name\t: {cpu}\nstepping\t: 6\nmicrocode\t: 0xd0003d1\ncpu MHz\t\t: 2600.000\n"
                           f"cache size\t: 49152 KB\nphysical id\t: {i // kerne}\nsiblings\t: {kerne * 2}\n"
                           f"core id\t\t: {i % kerne}\ncpu cores\t: {kerne}\nflags\t\t: fpu vme de pse tsc msr pae mce cx8 apic "
                           "sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx "
                           "pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc "
                           "cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx smx est tm2 ssse3 sdbg fma cx16 xtpr "
                           "pdcm pcid dca sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand "
                           "lahf_lm abm 3dnowprefetch cpuid_fault epb cat_l3 invpcid_single intel_ppin ssbd mba ibrs ibpb "
                           "stibp ibrs_enhanced tpr_shadow flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep "
                           "bmi2 erms invpcid cqm rdt_a avx512f avx512dq rdseed adx smap avx512ifma clflushopt clwb intel_pt "
                           "avx512cd sha_ni avx512bw avx512vl xsaveopt xsavec xgetbv1 xsaves\n\n")
            return "".join(out)
        h["cpuinfo"] = cpuinfo
        h["modules"] = lambda: _proc_modules(sysm)

        def lscpu():
            threads = sysm.cpus
            return (f"Architecture:             x86_64\n  CPU op-mode(s):         32-bit, 64-bit\n"
                    f"  Address sizes:          46 bits physical, 57 bits virtual\n  Byte Order:             Little Endian\n"
                    f"CPU(s):                   {threads}\n  On-line CPU(s) list:    0-{threads - 1}\nVendor ID:                GenuineIntel\n"
                    f"  Model name:             {cpu}\n    CPU family:           6\n    Model:                106\n"
                    f"    Thread(s) per core:   2\n    Core(s) per socket:   {kerne}\n    Socket(s):            {sockel}\n"
                    f"    Stepping:             6\n    CPU(s) scaling MHz:   100%\n    CPU max MHz:          3400.0000\n"
                    f"    CPU min MHz:          800.0000\n    BogoMIPS:             5200.00\nVirtualization features:  \n"
                    f"  Virtualization:         VT-x\nCaches (sum of all):      \n  L1d:                    {kerne * sockel * 48 // 1024} MiB ({kerne * sockel} instances)\n"
                    f"  L1i:                    {kerne * sockel * 32 // 1024} MiB ({kerne * sockel} instances)\n"
                    f"  L2:                     {kerne * sockel * 1.25:.0f} MiB ({kerne * sockel} instances)\n"
                    f"  L3:                     {48 * sockel} MiB ({sockel} instances)\nNUMA:                     \n"
                    f"  NUMA node(s):           {sockel}\n  NUMA node0 CPU(s):      0-{kerne - 1},{kerne * sockel}-{kerne * sockel + kerne - 1}\n"
                    f"  NUMA node1 CPU(s):      {kerne}-{kerne * 2 - 1},{kerne * sockel + kerne}-{threads - 1}\n")
        h["lscpu"] = lscpu

        def blockdev():
            ss = lab()
            out = []
            if pe:
                out += [("loop0", "1.8G", "loop", "/rofs", "squashfs", "", ""),
                        ("sdb", "57.3G", "disk", "", "", "", ""),
                        ("├─sdb1", "22G", "part", "/cdrom", "iso9660", "2023-11-20-08-14-02-00", "AI-EasyCfg"),
                        ("└─sdb2", "35.3G", "part", "/media", "ntfs", "4A1C2B7E1C2B6595", "DATA")]
            nv = [i for i, d in enumerate(ss["comp"]["nvme"]) if d["status"] == "ok"]
            for i in nv:
                out.append((f"nvme{i}n1", "3.5T", "disk", "", "", "", ""))
                if i == 0 and not pe:
                    out += [("├─nvme0n1p1", "1G", "part", "/boot/efi", "vfat", "A1B2-C3D4", ""),
                            ("├─nvme0n1p2", "2G", "part", "/boot", "ext4", "5c1e9a7b-2d4f-4e8a-9b3c-7f6e5d4c3b2a", ""),
                            ("└─nvme0n1p3", "3.5T", "part", "", "LVM2_member", "bQ7dKJ-2s3e-k9Xm-1Pa0-Lr5V-t8Nc-2Wz4Yh", ""),
                            ("  └─ubuntu--vg-ubuntu--lv", "100G", "lvm", "/", "ext4", "7d3e1b2a-9c8f-4e6d-a5b4-3c2d1e0f9a8b", "")]
            if ss["comp"]["hdd"]:
                ok = sum(1 for d in ss["comp"]["hdd"] if d["status"] in ("ok", "rebuild"))
                out.append(("sda", "21.8T", "disk", "", "", "", ""))
                if not nv and not pe:
                    out = [("sda", "21.8T", "disk", "", "", "", ""),
                           ("├─sda1", "1G", "part", "/boot/efi", "vfat", "A1B2-C3D4", ""),
                           ("├─sda2", "2G", "part", "/boot", "ext4", "5c1e9a7b-2d4f-4e8a-9b3c-7f6e5d4c3b2a", ""),
                           ("└─sda3", "21.8T", "part", "", "LVM2_member", "bQ7dKJ-2s3e", ""),
                           ("  └─ubuntu--vg-ubuntu--lv", "100G", "lvm", "/", "ext4", "7d3e1b2a-9c8f-4e6d-a5b4-3c2d1e0f9a8b", "")]
            return out
        h["blockdev"] = blockdev

        def df():
            g, u, sh, c, sw = mem()
            if pe:
                return [("udev", "devtmpfs", g // 2, 0, "/dev"), ("tmpfs", "tmpfs", g // 10, 2_412, "/run"),
                        ("/cow", "overlay", g // 2, 1_204_412, "/"), ("tmpfs", "tmpfs", g // 2, 0, "/dev/shm"),
                        ("/dev/sdb1", "iso9660", 23_068_672, 23_068_672, "/cdrom"),
                        ("/dev/loop0", "squashfs", 1_887_436, 1_887_436, "/rofs"),
                        ("/dev/sdb2", "fuseblk", 37_013_500, 3_512_028, "/media"),
                        ("tmpfs", "tmpfs", g // 2, 12, "/tmp")]
            return [("tmpfs", "tmpfs", g // 10, 3_788, "/run"),
                    ("/dev/mapper/ubuntu--vg-ubuntu--lv", "ext4", 102_626_232, 14_380_224, "/"),
                    ("tmpfs", "tmpfs", g // 2, 0, "/dev/shm"), ("tmpfs", "tmpfs", 5120, 0, "/run/lock"),
                    ("/dev/nvme0n1p2" if lab()["comp"]["nvme"] else "/dev/sda2", "ext4", 1_992_552, 191_204, "/boot"),
                    ("/dev/nvme0n1p1" if lab()["comp"]["nvme"] else "/dev/sda1", "vfat", 1_098_632, 6_216, "/boot/efi"),
                    ("tmpfs", "tmpfs", g // 10, 12, "/run/user/0")]
        h["df"] = df

        def mounts():
            out = []
            for f, typ, *_r, mnt in df():
                out.append(f"{f} {mnt} {typ} rw,relatime 0 0\n")
            out.insert(0, "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\nproc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n")
            return "".join(out)
        h["mounts"] = mounts
        h["mounts_mount"] = lambda: "".join(
            f"{z.split()[0]} on {z.split()[1]} type {z.split()[2]} ({z.split()[3]})\n" for z in mounts().splitlines())

        def netz():
            idx = list(core.SERVERS).index(name)
            return [("lo", "00:00:00:00:00:00", "127.0.0.1/8", "UP"),
                    ("eno1", f"b4:05:5d:{0x3a + idx:02x}:9c:{0x10 + idx:02x}", "" if pe else f"{sysm.ip}/24", "UP"),
                    ("eno2", f"b4:05:5d:{0x3a + idx:02x}:9c:{0x11 + idx:02x}", "", "DOWN"),
                    ("ibp26s0", f"00:00:10:27:fe:80:00:00", "", "UP" if s["comp"]["gpu"] else "DOWN")]
        h["netz"] = netz

        def dienste():
            ss = lab()
            d = {}
            if ss["comp"]["gpu"]:
                aktiv = ss.get("persistenced") and "nvidia" in mods(ss)
                d["nvidia-persistenced"] = ("NVIDIA Persistence Daemon", "active (running)" if aktiv else "inactive (dead)",
                                            "/usr/bin/nvidia-persistenced --verbose")
            if ss["comp"]["baseboard"]:
                ok = ss["comp"]["baseboard"][0]["status"] == "ok" and "nvidia" in mods(ss) and ss.get("fm", True)
                d["nvidia-fabricmanager"] = ("NVIDIA fabric manager service",
                                             "active (running)" if ok else "failed (Result: exit-code)",
                                             "/usr/bin/nv-fabricmanager -c /usr/share/nvidia/nvswitch/fabricmanager.cfg")
            if pe:
                for k in ("ssh",):
                    d.pop(k, None)
            return d
        h["dienste"] = dienste

        def dienst_aktion(ctx, cmd, dienst):
            with core.zustand() as st:
                ss = st["servers"][name]
                if dienst == "nvidia-persistenced":
                    if cmd in ("stop", "kill"):
                        ss["persistenced"] = False
                    elif cmd in ("start", "restart"):
                        if "nvidia" not in mods(ss):
                            ctx.err("Job for nvidia-persistenced.service failed because the control process exited with "
                                    "error code.\nSee \"systemctl status nvidia-persistenced.service\" and \"journalctl "
                                    "-xeu nvidia-persistenced.service\" for details.\n")
                            return 1
                        ss["persistenced"] = True
                if dienst == "nvidia-fabricmanager":
                    if cmd in ("stop", "kill"):
                        ss["fm"] = False
                    elif cmd in ("start", "restart"):
                        if ss["comp"]["baseboard"][0]["status"] != "ok" or "nvidia" not in mods(ss):
                            ss["fm"] = True
                            ctx.err("Job for nvidia-fabricmanager.service failed because the control process exited with "
                                    "error code.\nSee \"systemctl status nvidia-fabricmanager.service\" and \"journalctl "
                                    "-xeu nvidia-fabricmanager.service\" for details.\n")
                            return 1
                        ss["fm"] = True
            return 0
        h["dienst_aktion"] = dienst_aktion

        def dienst_log(dienst):
            ss = lab()
            if dienst == "nvidia-fabricmanager":
                if ss["comp"]["baseboard"] and ss["comp"]["baseboard"][0]["status"] != "ok":
                    return ["nv-fabricmanager[3120]: request to query NVSwitch device information from NVSwitch driver failed",
                            "nv-fabricmanager[3120]: detected number of NVSwitches don't match with any supported system "
                            "topology, aborting fabric manager",
                            "systemd[1]: nvidia-fabricmanager.service: Control process exited, code=exited, status=1/FAILURE",
                            "systemd[1]: nvidia-fabricmanager.service: Failed with result 'exit-code'.",
                            "systemd[1]: Failed to start nvidia-fabricmanager.service - NVIDIA fabric manager service."]
                if "nvidia" not in mods(ss):
                    return ["nv-fabricmanager[3120]: NVIDIA driver not loaded"]
                return ["systemd[1]: Starting nvidia-fabricmanager.service - NVIDIA fabric manager service...",
                        "nv-fabricmanager[3120]: Successfully configured all the available NVSwitches to route GPU NVLink traffic.",
                        "systemd[1]: Started nvidia-fabricmanager.service - NVIDIA fabric manager service."]
            if dienst == "nvidia-persistenced":
                return ["systemd[1]: Starting nvidia-persistenced.service - NVIDIA Persistence Daemon...",
                        "nvidia-persistenced[2214]: Started (2214)",
                        "systemd[1]: Started nvidia-persistenced.service - NVIDIA Persistence Daemon."]
            return []
        h["dienst_log"] = dienst_log

        def journal():
            out, _, _ = tool(name, "dmesg", [])
            boot = sysm.boot - 25
            z = []
            for zeile in out.splitlines():
                m = re.match(r"\[\s*([\d.]+)\]\s*(.*)", zeile)
                if m:
                    z.append(f"{time.strftime('%b %d %H:%M:%S', time.gmtime(boot + float(m.group(1))))} {sysm.hostname} "
                             f"kernel: {m.group(2)}")
            t2 = time.strftime('%b %d %H:%M:%S', time.gmtime(boot + 9))
            z += [f"{t2} {sysm.hostname} systemd[1]: Started nvidia-persistenced.service - NVIDIA Persistence Daemon."] if s["comp"]["gpu"] else []
            z += [f"{t2} {sysm.hostname} systemd[1]: Reached target multi-user.target - Multi-User System.",
                  f"{t2} {sysm.hostname} systemd-logind[1045]: New session 1 of user root.",
                  f"{t2} {sysm.hostname} login[1301]: ROOT LOGIN  on '/dev/tty1'"]
            return z
        h["journal"] = journal
        for p in ("/var/log/syslog", "/var/log/kern.log"):
            n = sysm.fs.finde(p)
            if n is not None:
                n.data = (lambda nur_kern=p.endswith("kern.log"): "".join(
                    z + "\n" for z in journal() if not nur_kern or " kernel: " in z))

        def power(ctx, art):
            text = {"reboot": "reboot", "poweroff": "power off", "halt": "halt"}.get(art, art)
            ctx.err if False else None
            tty = ctx.tty
            if tty is not None:
                tty.write("\n")
                for z in ("Stopping session-1.scope - Session 1 of User root...", "Stopped target multi-user.target - Multi-User System.",
                          "Stopped nvidia-persistenced.service - NVIDIA Persistence Daemon." if s["comp"]["gpu"] else
                          "Stopped cron.service - Regular background program processing daemon.",
                          "Stopped systemd-journald.service - Journal Service.", "Reached target shutdown.target - System Shutdown.",
                          "Reached target final.target - Late Shutdown Services."):
                    tty.write(f"[\x1b[0;32m  OK  \x1b[0m] {z}\n")
                    time.sleep(0.15)
                tty.write({"reboot": "[  {:.6f}] reboot: Restarting system\n", "poweroff": "[  {:.6f}] reboot: Power down\n",
                           "halt": "[  {:.6f}] reboot: System halted\n"}.get(art, "").format(time.time() - sysm.boot))
                time.sleep(0.8)
            with core.zustand() as st:
                core.power(st, st["servers"][name], "reset" if art == "reboot" else "soft", still=True)
            if tty is not None:
                tty.beenden()
            from .streams import Interrupted
            raise Interrupted()
        h["power"] = power

        def kill_hook(pid, sig, p):
            if pid == 2214 and sig in (9, 15, 2):
                with core.zustand() as st:
                    st["servers"][name]["persistenced"] = False
                return True
            if pid == 3120 and sig in (9, 15, 2):
                with core.zustand() as st:
                    st["servers"][name]["fm"] = False
                return True
            return False
        sysm.kill_hook = kill_hook

        def procs_sync():
            ss = lab()
            p = sysm._prozesse
            t = sysm.boot + 9
            if ss["comp"]["gpu"] and ss.get("persistenced") and "nvidia" in mods(ss):
                p.setdefault(2214, {"pid": 2214, "ppid": 1, "user": "root", "cmd": "/usr/bin/nvidia-persistenced --verbose",
                                    "comm": "nvidia-persiste", "start": t, "tty": "?", "stat": "Ss", "rss": 12304, "vsz": 305112})
            else:
                p.pop(2214, None)
            if ss["comp"]["baseboard"] and ss["comp"]["baseboard"][0]["status"] == "ok" and "nvidia" in mods(ss) and ss.get("fm", True):
                p.setdefault(3120, {"pid": 3120, "ppid": 1, "user": "root",
                                    "cmd": "/usr/bin/nv-fabricmanager -c /usr/share/nvidia/nvswitch/fabricmanager.cfg",
                                    "comm": "nv-fabricmanage", "start": t, "tty": "?", "stat": "Ssl", "rss": 2140, "vsz": 1201992})
            else:
                p.pop(3120, None)
        h["procs_sync"] = procs_sync

        def dmidecode(ctx):
            ss = lab()
            from ..tools import fru
            f = fru(ss)
            a = ctx.argv[1:]
            typ = None
            if "-t" in a and a.index("-t") + 1 < len(a):
                typ = a[a.index("-t") + 1]
            if "-s" in a and a.index("-s") + 1 < len(a):
                k = a[a.index("-s") + 1]
                werte = {"system-serial-number": ss["sn"], "system-product-name": ss["modell"],
                         "system-manufacturer": "Inspur", "bios-version": ss["fw"]["BIOS"], "baseboard-serial-number":
                         f.get("board_sn", ss["sn"]), "chassis-serial-number": ss["sn"]}
                if k not in werte:
                    ctx.err(f"Invalid string keyword: {k}\n")
                    return 2
                ctx.out(werte[k] + "\n")
                return 0
            teile = ["# dmidecode 3.5\nGetting SMBIOS data from sysfs.\nSMBIOS 3.3.0 present.\n"]
            if typ in (None, "bios", "0"):
                teile.append(f"\nHandle 0x0000, DMI type 0, 26 bytes\nBIOS Information\n\tVendor: American Megatrends International, LLC.\n"
                             f"\tVersion: {ss['fw']['BIOS']}\n\tRelease Date: 03/18/2024\n\tROM Size: 64 MB\n")
            if typ in (None, "system", "1"):
                teile.append(f"\nHandle 0x0001, DMI type 1, 27 bytes\nSystem Information\n\tManufacturer: Inspur\n"
                             f"\tProduct Name: {ss['modell']}\n\tVersion: 01\n\tSerial Number: {ss['sn']}\n"
                             f"\tUUID: 4c4c4544-0051-3510-8035-b3c04f{ss['sn'][-6:].lower().encode().hex()[:6]}\n"
                             f"\tWake-up Type: Power Switch\n\tSKU Number: Default string\n\tFamily: Inspur Server\n")
            if typ in (None, "baseboard", "2"):
                teile.append(f"\nHandle 0x0002, DMI type 2, 15 bytes\nBase Board Information\n\tManufacturer: Inspur\n"
                             f"\tProduct Name: {ss['modell']}\n\tSerial Number: {f.get('board_sn', '')}\n")
            if typ in ("memory", "17"):
                for d in ss["comp"]["dimm"]:
                    teile.append(f"\nMemory Device\n\tSize: {'64 GB' if d['status'] == 'ok' else 'No Module Installed'}\n"
                                 f"\tLocator: {d['name']}\n\tType: DDR4\n\tSpeed: 3200 MT/s\n\tManufacturer: Samsung\n"
                                 f"\tSerial Number: {d['sn']}\n\tPart Number: M393A8G40AB2-CWE\n")
            if typ and typ not in ("bios", "0", "system", "1", "baseboard", "2", "memory", "17"):
                ctx.err(f"Invalid type keyword: {typ}\n")
                return 2
            ctx.out("".join(teile))
            return 0
        h["dmidecode"] = dmidecode

        def hostnamectl():
            ss = lab()
            return (f" Static hostname: {sysm.hostname}\n       Icon name: computer-server\n         Chassis: server 🖳\n"
                    f"      Machine ID: 4f2c8e1a9b7d4c3e8f6a5b2d1c9e7f30\n         Boot ID: {ss['boot_id']:08x}a1b2c3d4e5f60718293a4b5c\n"
                    f"Operating System: {'Ubuntu 22.04.3 LTS' if pe else 'Ubuntu 24.04.1 LTS'}\n          Kernel: Linux {sysm.kernel}\n"
                    f"    Architecture: x86-64\n Hardware Vendor: Inspur\n  Hardware Model: {ss['modell']}\n"
                    f"Firmware Version: {ss['fw']['BIOS']}\n   Firmware Date: Mon 2024-03-18\n    Firmware Age: 2y 7month 3w 2d\n")
        h["hostnamectl"] = hostnamectl

        def usb():
            out = ["Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
                   "Bus 001 Device 002: ID 046b:ff01 American Megatrends, Inc. Virtual Hub",
                   "Bus 001 Device 003: ID 046b:ff10 American Megatrends, Inc. Virtual Keyboard and Mouse",
                   "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub"]
            if lab().get("stick"):
                out.insert(4, "Bus 002 Device 002: ID 0781:5581 SanDisk Corp. Ultra")
            return out
        h["usb"] = usb

        def pakete():
            liste = [("bash", "5.2.21-2ubuntu4", "GNU Bourne Again SHell"), ("coreutils", "9.4-3ubuntu6", "GNU core utilities"),
                     ("ipmitool", "1.8.19-7build2", "utility for IPMI control with kernel driver or LAN interface (daemon)"),
                     ("linux-image-6.8.0-45-generic", "6.8.0-45.45", "Signed kernel image generic"),
                     ("nvme-cli", "2.8-1build2", "NVMe management command line interface"),
                     ("openssh-server", "1:9.6p1-3ubuntu13.5", "secure shell (SSH) server, for secure access from remote machines"),
                     ("pciutils", "1:3.10.0-2build1", "PCI utilities"), ("tar", "1.35+dfsg-3build1", "GNU version of the tar archiving utility")]
            if s["comp"]["gpu"]:
                liste += [("nvidia-driver-550-server", "550.90.07-0ubuntu0.24.04.1", "NVIDIA Server Driver metapackage"),
                          ("nvidia-utils-550-server", "550.90.07-0ubuntu0.24.04.1", "NVIDIA Server Driver support binaries")]
            if s["comp"]["baseboard"]:
                liste += [("nvidia-fabricmanager-550", "550.90.07-1", "Fabric Manager for NVSwitch based systems.")]
            return sorted(liste)
        h["pakete"] = pakete
        h["schnell"] = core.FAST
        sysm.cnf = {"ifconfig": "net-tools", "netstat": "net-tools", "sensors": "lm-sensors",
                    "smartctl": "smartmontools", "iostat": "sysstat", "mpstat": "sysstat", "nmap": "nmap",
                    "tree": "tree", "unzip": "unzip", "zip": "zip", "traceroute": "traceroute",
                    "ethtool": "ethtool" if False else "ethtool", "mlxconfig": "mft", "iperf3": "iperf3",
                    "python": "python3", "pip": "python3-pip", "gcc": "gcc", "make": "make", "jq": "jq",
                    "hexdump": "bsdextrautils", "xxd": "xxd", "telnet": "inetutils-telnet", "screen": "screen",
                    "tmux": "tmux", "lshw": "lshw", "inxi": "inxi", "nvtop": "nvtop", "iftop": "iftop"}

        def cnf_ausgabe(ctx, n):
            ctx.err(f"Command '{n}' not found, but can be installed with:\n\napt install nvidia-utils-550-server\n\n")
        sysm.cnf_ausgabe = cnf_ausgabe
        h["ohne"] = {"fieldiag", "nvflash", "ai-easycfg"} | ({"storcli64"} if not s["comp"]["hdd"] else set()) | (
            {"nvidia-smi", "nvidia-bug-report.sh", "dcgmi"} if not s["comp"]["gpu"] else set()) | (
            {"dcgmi"} if pe else set())

    # ---------------- Konsole-Profil ----------------
    def boot_text(self):
        return "\x1b[H\x1b[2J"

    def issue(self):
        ver = "22.04.3" if self.pe else "24.04.1"
        return f"\n{('Ubuntu ' + ver + ' LTS')} {self.system.hostname} tty1\n\n"

    def pruefe(self, user, pw):
        if user != "root" and user not in self.system.users:
            return False
        if self.pe:
            return user == "root" and pw == "111111"
        return user == "root" and pw != ""

    def motd(self, letzter):
        sysm = self.system
        t = time.time()
        if self.pe:
            kopf = (f"Welcome to Ubuntu 22.04.3 LTS (GNU/Linux {sysm.kernel} x86_64)\n\n"
                    " * Documentation:  https://help.ubuntu.com\n * Management:     https://landscape.canonical.com\n"
                    " * Support:        https://ubuntu.com/advantage\n\n")
            if letzter:
                kopf += f"Last login: {strftime('%a %b %e %H:%M:%S UTC %Y', letzter)} on tty1\n"
            return kopf
        g, u, sh, c, sw = sysm.hooks["mem"]()
        procs = len(sysm.prozesse) + 1180
        kopf = (f"Welcome to Ubuntu 24.04.1 LTS (GNU/Linux {sysm.kernel} x86_64)\n\n"
                " * Documentation:  https://help.ubuntu.com\n * Management:     https://landscape.canonical.com\n"
                " * Support:        https://ubuntu.com/pro\n\n"
                f" System information as of {strftime('%a %b %e %H:%M:%S UTC %Y', t)}\n\n"
                f"  System load:  0.08                Processes:               {procs}\n"
                f"  Usage of /:   14.0% of 97.87GB    Users logged in:         0\n"
                f"  Memory usage: {max(1, int(u * 100 / g))}%                  IPv4 address for eno1:   {sysm.ip}\n"
                "  Swap usage:   0%\n\n"
                "Expanded Security Maintenance for Applications is not enabled.\n\n"
                "0 updates can be applied immediately.\n\n"
                "Enable ESM Apps to receive additional future security updates.\n"
                "See https://ubuntu.com/esm or run: sudo pro status\n\n\n")
        letzt = letzter or (sysm.boot - 86400 * 2 - 3700)
        kopf += f"Last login: {strftime('%a %b %e %H:%M:%S UTC %Y', letzt)} on tty1\n"
        return kopf

    def shell_setup(self, sh):
        from .testprofil import standard_env
        standard_env(sh, self.ls_colors)
        if self.pe:
            sh.vars["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
        hist = self.system.fs.finde("/root/.bash_history")
        if hist is not None:
            sh.hist.extend(z for z in hist.inhalt().splitlines() if z.strip())

    def nach_befehl(self, sh):
        hist = self.system.fs.finde("/root/.bash_history")
        if hist is not None:
            hist.data = "".join(z + "\n" for z in sh.hist[-2000:])
        return False


# ---------------------------------------------------------------------------- Konsolen-Verwaltung (fuer bmc.py)
KONSOLEN = {}
_LOCK = threading.Lock()


def konsole(name, boot_id, os_, cols=100, rows=36):
    from .session import Konsole
    key = (boot_id, os_)
    with _LOCK:
        alt = KONSOLEN.get(name)
        if alt and alt[0] == key and alt[1].lauf:
            k = alt[1]
            if cols and rows and (k.tty.cols, k.tty.rows) != (cols, rows):
                k.tty.cols, k.tty.rows = cols, rows
            return k
        if alt:
            alt[1].beenden()
        profil = LabProfil(name)
        k = Konsole(profil, cols, rows)
        KONSOLEN[name] = (key, k)
        return k


def beenden(name):
    with _LOCK:
        alt = KONSOLEN.pop(name, None)
        if alt:
            alt[1].beenden()
