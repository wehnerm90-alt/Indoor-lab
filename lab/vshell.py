"""Nachgebildete Bash fuer die KVM-Konsole.

Verhaelt sich wie eine Bash (Fehlermeldungen, Exit-Codes, Pipes, &&, ||, ;, Umleitung, Variablen,
Wildcards, cd/ls/cat ...), fuehrt aber NIE beliebige Befehle aus: Dateisystem und Befehle sind in Python
nachgebaut. Nur die Lab-Diagnosewerkzeuge werden gezielt (ohne Shell, als Argumentliste) aufgerufen.
"""
import fnmatch
import os
import posixpath
import random
import re
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime

from . import core

INBAND = os.path.join(core.BASE, "bin", "inband")
TOOLS = {"nvidia-smi", "fieldiag", "dmesg", "lspci", "nvme", "storcli64", "ipmitool"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SITZUNGEN = {}

BEFEHLE = sorted({
    "cd", "pwd", "ls", "ll", "cat", "head", "tail", "less", "more", "grep", "egrep", "wc", "sort", "uniq", "echo",
    "touch", "mkdir", "rm", "rmdir", "cp", "mv", "clear", "history", "whoami", "id", "hostname", "uname", "uptime",
    "date", "free", "df", "lsblk", "ip", "lscpu", "dmidecode", "journalctl", "smartctl", "top", "ps", "which",
    "type", "man", "sudo", "env", "printenv", "export", "alias", "exit", "logout", "reboot", "poweroff", "shutdown",
    "halt", "init", "sleep", "true", "false", "systemctl", "lsmod", "ping", "vi", "vim", "nano", "sensors",
    "nvidia-bug-report.sh", "help", "tee", "find", "basename", "dirname", "stat", "file", "sync", "lspci",
} | TOOLS | {"fieldiag.sh"})


class Fehler(Exception):
    pass


# --------------------------------------------------------------------------
# Werkzeuge der Lab-Server (ohne Shell aufgerufen)
# --------------------------------------------------------------------------

def tool(name, prog, args):
    pfad = os.path.join(INBAND, prog) if prog != "ipmitool" else os.path.join(core.BASE, "bin", "ipmitool")
    env = dict(os.environ, LAB_SERVER=name, PYTHONIOENCODING="utf-8",
               PATH=INBAND + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    try:
        r = subprocess.run([sys.executable, pfad, *args], env=env, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return "", f"{prog}: timeout\n", 124
    logs = os.path.join(core.VAR, "fieldiag") + "/"
    return (ANSI.sub("", r.stdout).replace(logs, "/root/fieldiag/logs/"),
            ANSI.sub("", r.stderr).replace(logs, "/root/fieldiag/logs/"), r.returncode)


# --------------------------------------------------------------------------
# Virtuelles Dateisystem
# --------------------------------------------------------------------------

def basis_dateien(sh):
    s = core.lesen()["servers"][sh.name]
    m = core.MODELS[s["modell"]]
    n = sh.name
    ram_gb = sum(1 for d in s["comp"]["dimm"] if d["status"] == "ok") * 64
    f = {
        "/etc/hostname": n + "\n",
        "/etc/os-release": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION="24.04 LTS (Noble Numbat)"\nID=ubuntu\n',
        "/etc/hosts": f"127.0.0.1 localhost\n127.0.1.1 {n}\n",
        "/etc/fstab": "/dev/disk/by-uuid/4f2c1e0a / ext4 defaults 0 1\n/swap.img none swap sw 0 0\n",
        "/proc/version": "Linux version 6.8.0-45-generic (buildd@lcy02-amd64) (gcc 13.2.0) #45-Ubuntu SMP\n",
        "/proc/cpuinfo": "".join(f"processor\t: {i}\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Xeon(R) Platinum 8480+\n"
                                  f"physical id\t: {i // 56}\ncpu cores\t: 56\n\n" for i in range(4)) + "(... 224 processors, gekürzt)\n",
        "/proc/meminfo": f"MemTotal:       {ram_gb * 1024 * 1024} kB\nMemFree:        {ram_gb * 1024 * 1000} kB\nMemAvailable:   {ram_gb * 1024 * 1010} kB\n",
        "/root/.bashrc": "# ~/.bashrc\nalias ll='ls -alF'\n",
        "/root/.bash_history": "",
        "/var/log/syslog": lambda: syslog(sh),
        "/var/log/kern.log": lambda: tool(sh.name, "dmesg", [])[0],
        "/var/log/dmesg": lambda: tool(sh.name, "dmesg", [])[0],
        "/dev/ipmi0": "",
        "/dev/null": "",
    }
    for i, d in enumerate(s["comp"]["nvme"]):
        if d["status"] == "ok":
            f[f"/dev/nvme{i}"] = ""
            f[f"/dev/nvme{i}n1"] = ""
    if s["comp"]["hdd"]:
        f["/dev/sda"] = ""
        f["/dev/sda1"] = ""
    for i, g in enumerate(s["comp"]["gpu"]):
        if g["status"] != "offbus":
            f[f"/dev/nvidia{i}"] = ""
    if s["comp"]["gpu"]:
        f["/dev/nvidiactl"] = ""
        f["/root/fieldiag/fieldiag.sh"] = "#!/bin/bash\n# fieldiag launcher (Lab)\nexec fieldiag \"$@\"\n"
        f["/root/fieldiag/README.txt"] = ("fieldiag (Lab-Simulation)\n  ./fieldiag.sh --level1   kurzer Test\n"
                                          "  ./fieldiag.sh --level2   ausführlicher Test\n  --no_bmc   BMC-Sensoren auslassen\n"
                                          "Logs: /root/fieldiag/logs/\n")
        logdir = os.path.join(core.VAR, "fieldiag")
        if os.path.isdir(logdir):
            for name in sorted(os.listdir(logdir)):
                if name.startswith(f"fieldiag_{n}_"):
                    pfad = os.path.join(logdir, name)
                    f[f"/root/fieldiag/logs/{name}"] = (lambda p=pfad: open(p, encoding="utf-8").read())
    return f


def syslog(sh):
    out, _, _ = tool(sh.name, "dmesg", [])
    zeilen = []
    basis = datetime.now().strftime("%b %d")
    for z in out.splitlines():
        m = re.match(r"\[\s*([\d.]+)\]\s*(.*)", z)
        if m:
            zeilen.append(f"{basis} {sh.name} kernel: [{m.group(1)}] {m.group(2)}")
    zeilen.append(f"{basis} {sh.name} systemd[1]: Started nvidia-persistenced.service - NVIDIA Persistence Daemon.")
    return "\n".join(zeilen) + "\n"


STANDARD_DIRS = ["/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/proc", "/root", "/root/fieldiag",
                 "/root/fieldiag/logs", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/usr/bin", "/usr/lib",
                 "/usr/local", "/usr/sbin", "/var", "/var/log", "/var/tmp"]


class VShell:
    def __init__(self, name):
        self.name = name
        self.cwd = "/root"
        self.env = {"HOME": "/root", "USER": "root", "LOGNAME": "root", "HOSTNAME": name, "SHELL": "/bin/bash",
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "xterm",
                    "LANG": "C.UTF-8"}
        self.rc = 0
        self.hist = []
        self.neu = {}        # vom Nutzer angelegte Dateien
        self.neu_dirs = set()
        self.weg = set()     # geloeschte Pfade
        self.t = time.time()
        self.start = time.time()

    # ---------------- Dateisystem ----------------
    def dateien(self):
        f = basis_dateien(self)
        f.update(self.neu)
        return {k: v for k, v in f.items() if not self._geloescht(k)}

    def _geloescht(self, p):
        return any(p == w or p.startswith(w.rstrip("/") + "/") for w in self.weg)

    def verzeichnisse(self, dateien=None):
        dateien = dateien if dateien is not None else self.dateien()
        d = set(STANDARD_DIRS) | self.neu_dirs
        if not core.lesen()["servers"][self.name]["comp"]["gpu"]:
            d -= {"/root/fieldiag", "/root/fieldiag/logs"}
        for p in dateien:
            teile = p.split("/")
            for i in range(1, len(teile)):
                d.add("/".join(teile[:i]) or "/")
        return {x for x in d if not self._geloescht(x)}

    def pfad(self, p):
        if p in ("~", ""):
            p = "/root"
        elif p.startswith("~/"):
            p = "/root/" + p[2:]
        if not p.startswith("/"):
            p = posixpath.join(self.cwd, p)
        p = posixpath.normpath(p)
        return "/" if p.startswith("//") else p

    def lesen(self, p, prog):
        a = self.pfad(p)
        f = self.dateien()
        if a in self.verzeichnisse(f):
            raise Fehler(f"{prog}: {p}: Is a directory")
        if a not in f:
            raise Fehler(f"{prog}: {p}: No such file or directory")
        v = f[a]
        return v() if callable(v) else v

    def kinder(self, d, dateien=None):
        dateien = dateien if dateien is not None else self.dateien()
        dirs = self.verzeichnisse(dateien)
        praefix = d.rstrip("/") + "/"
        namen = {}
        for x in dirs:
            if x != d and x.startswith(praefix) and "/" not in x[len(praefix):]:
                namen[x[len(praefix):]] = "d"
        for x in dateien:
            if x.startswith(praefix) and "/" not in x[len(praefix):]:
                namen[x[len(praefix):]] = "f"
        if d == "/usr/bin":
            for b in BEFEHLE:
                if "." not in b or b == "nvidia-bug-report.sh":
                    namen.setdefault(b, "x")
        return namen

    def glob(self, wort):
        if not any(c in wort for c in "*?[") or wort.startswith("-"):
            return [wort]
        a = self.pfad(wort)
        d, muster = posixpath.split(a)
        treffer = sorted(n for n in self.kinder(d) if fnmatch.fnmatch(n, muster) and not n.startswith("."))
        if not treffer:
            return [wort]
        if wort.startswith("/"):
            return [posixpath.join(d, n) for n in treffer]
        rel_d = posixpath.dirname(wort)
        return [posixpath.join(rel_d, n) if rel_d else n for n in treffer]

    def prompt(self):
        c = self.cwd
        if c == "/root" or c.startswith("/root/"):
            c = "~" + c[5:]
        return f"root@{self.name}:{c}# "

    # ---------------- Ausfuehrung ----------------
    def ausfuehren(self, zeile):
        """-> (ausgabe, aktion)"""
        self.t = time.time()
        if zeile.strip():
            self.hist.append(zeile)
        zeile = self._expand(zeile.strip())
        if not zeile or zeile.startswith("#"):
            return "", None
        try:
            lx = shlex.shlex(zeile, posix=True, punctuation_chars="|&;<>")
            lx.whitespace_split = True
            lx.commenters = ""
            tokens = list(lx)
        except ValueError:
            self.rc = 2
            return "-bash: syntax error: unexpected end of file", None
        ausgabe, aktion = [], None
        # in Befehlsfolgen aufteilen: ; && ||
        folgen, aktuell, op = [], [], ";"
        for t in tokens:
            if t in (";", "&&", "||"):
                folgen.append((op, aktuell))
                aktuell, op = [], t
            else:
                aktuell.append(t)
        folgen.append((op, aktuell))
        for k, (op, teil) in enumerate(folgen):
            if not teil:
                if k == len(folgen) - 1 and op == ";" and k > 0:
                    continue
                self.rc = 2
                tok = op if k > 0 else (folgen[1][0] if len(folgen) > 1 else ";")
                return f"-bash: syntax error near unexpected token `{tok}'", None
            if op == "&&" and self.rc != 0 or op == "||" and self.rc == 0:
                continue
            out, akt = self._pipeline(teil)
            if out:
                ausgabe.append(out.rstrip("\n"))
            if akt:
                aktion = akt
                break
        return "\n".join(ausgabe), aktion

    def _expand(self, zeile):
        def ersetzen(m):
            name = m.group(1) or m.group(2)
            if name == "?":
                return str(self.rc)
            if name == "PWD":
                return self.cwd
            return self.env.get(name, "")
        # nicht in einfachen Anfuehrungszeichen
        teile = re.split(r"('[^']*')", zeile)
        return "".join(t if t.startswith("'") else re.sub(r"\$\{(\w+)\}|\$(\w+|\?)", ersetzen, t) for t in teile)

    def _pipeline(self, tokens):
        stufen, akt = [[]], None
        for t in tokens:
            if t == "|":
                stufen.append([])
            else:
                stufen[-1].append(t)
        if any(not s for s in stufen):
            self.rc = 2
            return "-bash: syntax error near unexpected token `|'", None
        stdin, fehler_out = "", []
        for i, st in enumerate(stufen):
            argv, ziel, anhaengen, err_mit, err_weg = [], None, False, False, False
            j = 0
            while j < len(st):
                t = st[j]
                if t in (">", ">>") and j + 1 < len(st):
                    ziel, anhaengen = st[j + 1], t == ">>"
                    j += 2
                    continue
                if t == "2" and j + 2 < len(st) and st[j + 1] == ">&" and st[j + 2] == "1":
                    err_mit = True
                    j += 3
                    continue
                if t == "2" and j + 2 < len(st) and st[j + 1] in (">", ">>"):
                    err_weg = True
                    j += 3
                    continue
                if t == "&>" and j + 1 < len(st):
                    err_mit, ziel, anhaengen = True, st[j + 1], False
                    j += 2
                    continue
                if t == "<" and j + 1 < len(st):
                    try:
                        stdin = self.lesen(st[j + 1], "-bash")
                    except Fehler as e:
                        self.rc = 1
                        return str(e), None
                    j += 2
                    continue
                argv.extend(self.glob(t))
                j += 1
            if not argv:
                continue
            self.tty = i == len(stufen) - 1 and not ziel
            out, err, rc, akt = self._befehl(argv, stdin)
            self.rc = rc
            if err_mit:
                out, err = (out + err), ""
            if err_weg:
                err = ""
            if err:
                fehler_out.append(err.rstrip("\n"))
            if ziel:
                if ziel == "/dev/null":
                    out = ""
                else:
                    p = self.pfad(ziel)
                    if p in self.verzeichnisse():
                        fehler_out.append(f"-bash: {ziel}: Is a directory")
                        self.rc = 1
                    elif posixpath.dirname(p) not in self.verzeichnisse():
                        fehler_out.append(f"-bash: {ziel}: No such file or directory")
                        self.rc = 1
                    else:
                        alt = ""
                        if anhaengen and p in self.dateien():
                            alt = self.lesen(ziel, "-bash")
                        self.neu[p] = alt + out
                        self.weg.discard(p)
                    out = ""
            stdin = out
            if akt:
                break
        teile = fehler_out + ([stdin.rstrip("\n")] if stdin else [])
        return "\n".join(t for t in teile if t), akt

    def _befehl(self, argv, stdin):
        """-> (stdout, stderr, rc, aktion)"""
        prog, args = argv[0], argv[1:]
        if prog == "sudo":
            if not args:
                return "", "usage: sudo -h | -K | -k | -V\n", 1, None
            return self._befehl(args, stdin)
        if "=" in prog and re.fullmatch(r"\w+=.*", prog) and not args:
            k, v = prog.split("=", 1)
            self.env[k] = v
            return "", "", 0, None
        if prog in ("./fieldiag.sh", "fieldiag.sh", "/root/fieldiag/fieldiag.sh"):
            if prog.startswith("./") and self.pfad(prog) not in self.dateien():
                return "", f"-bash: {prog}: No such file or directory\n", 127, None
            prog = "fieldiag"
        if prog == "ll":
            prog, args = "ls", ["-alF", *args]
        fn = getattr(self, "c_" + prog.replace("-", "_").replace(".", "_"), None)
        try:
            if prog in TOOLS:
                out, err, rc = tool(self.name, prog, args)
                return out, err, rc, None
            if fn:
                r = fn(args, stdin)
                if isinstance(r, tuple):
                    return (*r, None) if len(r) == 3 else r
                return r or "", "", 0, None
        except Fehler as e:
            return "", str(e) + "\n", 1, None
        if "/" in prog:
            return "", f"-bash: {prog}: No such file or directory\n", 127, None
        return "", f"{prog}: command not found\n", 127, None

    # ---------------- Befehle ----------------
    def c_help(self, a, i):
        return ("GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu) – Lab-Simulation\n"
                "Diagnose: nvidia-smi, fieldiag (cd ~/fieldiag; ./fieldiag.sh --level2), dmesg, lspci, nvme, storcli64,\n"
                "          ipmitool, journalctl, smartctl, systemctl, sensors, nvidia-bug-report.sh\n"
                "System:   ls cd pwd cat less head tail grep wc sort uniq find echo free df lsblk ip lscpu dmidecode top ps\n"
                "          uptime date uname hostname whoami id history env export which reboot poweroff\n")

    def c_true(self, a, i):
        return "", "", 0

    def c_false(self, a, i):
        return "", "", 1

    def c_cd(self, a, i):
        ziel = a[0] if a else "~"
        if ziel == "-":
            ziel = self.env.get("OLDPWD", self.cwd)
        p = self.pfad(ziel)
        if p in self.dateien():
            raise Fehler(f"-bash: cd: {ziel}: Not a directory")
        if p not in self.verzeichnisse():
            raise Fehler(f"-bash: cd: {ziel}: No such file or directory")
        self.env["OLDPWD"], self.cwd = self.cwd, p
        return ""

    def c_pwd(self, a, i):
        return self.cwd + "\n"

    def c_ls(self, a, i):
        opts = "".join(x[1:] for x in a if x.startswith("-") and x != "-" and not x.startswith("--"))
        for o in opts:
            if o not in "alhF1rtRSd":
                raise Fehler(f"ls: invalid option -- '{o}'\nTry 'ls --help' for more information.")
        ziele = [x for x in a if not x.startswith("-")] or ["."]
        dateien = self.dateien()
        dirs = self.verzeichnisse(dateien)
        out, err, rc = [], [], 0
        for z in ziele:
            p = self.pfad(z)
            if p in dateien:
                eintraege = {posixpath.basename(z) if "l" not in opts else z: "f"}
            elif p in dirs:
                eintraege = self.kinder(p, dateien)
                if "a" in opts:
                    eintraege = {".": "d", "..": "d", **eintraege}
                if p == "/root" and "a" in opts:
                    eintraege.update({".bashrc": "f", ".bash_history": "f", ".profile": "f"})
                elif p == "/root":
                    eintraege = {k: v for k, v in eintraege.items() if not k.startswith(".")}
            else:
                err.append(f"ls: cannot access '{z}': No such file or directory")
                rc = 2
                continue
            if len(ziele) > 1 and p in dirs:
                out.append(f"{z}:")
            namen = sorted((k for k in eintraege if "a" in opts or not k.startswith(".")), reverse="r" in opts)
            if "l" in opts:
                out.append(f"total {len(namen) * 4}")
                for n in namen:
                    typ = eintraege[n]
                    voll = posixpath.join(p, n) if p in dirs else p
                    groesse = 4096 if typ == "d" else (len(str(dateien.get(voll, ""))) if not callable(dateien.get(voll)) else 18734)
                    if voll.startswith("/dev/"):
                        groesse = 0
                    rechte = "drwxr-xr-x" if typ == "d" else ("-rwxr-xr-x" if typ == "x" or n.endswith(".sh") else "-rw-r--r--")
                    g = f"{groesse / 1024:.1f}K" if "h" in opts and groesse >= 1024 else str(groesse)
                    zeit = datetime.fromtimestamp(self.start).strftime("%b %d %H:%M")
                    out.append(f"{rechte} 1 root root {g:>6} {zeit} {n}{'/' if typ == 'd' and 'F' in opts else ''}")
            else:
                if namen:
                    tr = "  " if getattr(self, "tty", True) and "1" not in opts else "\n"
                    out.append(tr.join(n + ("/" if eintraege[n] == "d" and "F" in opts else "") for n in namen))
        return "\n".join(out) + ("\n" if out else ""), ("\n".join(err) + "\n" if err else ""), rc

    def c_cat(self, a, i):
        dateien = [x for x in a if not x.startswith("-")]
        if not dateien:
            return i, "", 0
        out, err, rc = [], [], 0
        for d in dateien:
            try:
                out.append(self.lesen(d, "cat"))
            except Fehler as e:
                err.append(str(e))
                rc = 1
        return "".join(out), "\n".join(err) + ("\n" if err else ""), rc

    c_less = c_more = c_cat

    def _n(self, a, std=10):
        n, rest = std, []
        k = 0
        while k < len(a):
            if a[k] == "-n" and k + 1 < len(a):
                n = int(re.sub(r"\D", "", a[k + 1]) or std)
                k += 2
                continue
            if re.fullmatch(r"-n?\d+", a[k]):
                n = int(re.sub(r"\D", "", a[k]))
            elif a[k] == "-f":
                pass
            else:
                rest.append(a[k])
            k += 1
        return n, rest

    def c_head(self, a, i):
        n, rest = self._n(a)
        text = "".join(self.lesen(d, "head") for d in rest) if rest else i
        return "\n".join(text.splitlines()[:n]) + "\n" if text else ""

    def c_tail(self, a, i):
        n, rest = self._n(a)
        text = "".join(self.lesen(d, "tail") for d in rest) if rest else i
        return "\n".join(text.splitlines()[-n:]) + "\n" if text else ""

    def c_grep(self, a, i):
        flags, rest = set(), []
        for x in a:
            if x.startswith("-") and len(x) > 1 and not rest:
                flags.update(x[1:])
            else:
                rest.append(x)
        if not rest:
            return "", "Usage: grep [OPTION]... PATTERNS [FILE]...\nTry 'grep --help' for more information.\n", 2
        muster, dateien = rest[0], rest[1:]
        try:
            rx = re.compile(muster if "E" in flags or "P" in flags else muster.replace(r"\|", "|"),
                            re.I if "i" in flags else 0)
        except re.error:
            rx = re.compile(re.escape(muster), re.I if "i" in flags else 0)
        quellen = []
        err = []
        if dateien:
            for d in dateien:
                try:
                    quellen.append((d, self.lesen(d, "grep")))
                except Fehler as e:
                    err.append(str(e).replace("grep: ", "grep: ", 1))
        else:
            quellen.append(("", i))
        treffer = []
        for name, text in quellen:
            for nr, z in enumerate(text.splitlines(), 1):
                hit = bool(rx.search(z)) if "w" not in flags else bool(re.search(rf"\b{rx.pattern}\b", z, rx.flags))
                if hit != ("v" in flags):
                    treffer.append(((name + ":") if len(quellen) > 1 else "") + (f"{nr}:" if "n" in flags else "") + z)
        if "c" in flags:
            return str(len(treffer)) + "\n", "\n".join(err), 0 if treffer else 1
        return ("\n".join(treffer) + "\n" if treffer else ""), ("\n".join(err) + "\n" if err else ""), (0 if treffer else (2 if err else 1))

    c_egrep = c_grep

    def c_wc(self, a, i):
        dateien = [x for x in a if not x.startswith("-")]
        text = "".join(self.lesen(d, "wc") for d in dateien) if dateien else i
        l, w, c = text.count("\n"), len(text.split()), len(text.encode())
        if "-l" in a:
            return f"{l}\n"
        if "-w" in a:
            return f"{w}\n"
        if "-c" in a:
            return f"{c}\n"
        return f"{l:>7} {w:>7} {c:>7}\n"

    def c_sort(self, a, i):
        z = i.splitlines()
        if "-n" in a:
            z.sort(key=lambda x: float(re.match(r"\s*(-?\d+\.?\d*)", x).group(1)) if re.match(r"\s*-?\d", x) else 0)
        else:
            z.sort()
        if "-r" in a or "-rn" in a or "-nr" in a:
            z.reverse()
        return "\n".join(z) + ("\n" if z else "")

    def c_uniq(self, a, i):
        out, prev, n = [], None, 0
        for z in i.splitlines() + [None]:
            if z == prev:
                n += 1
                continue
            if prev is not None:
                out.append(f"{n:>7} {prev}" if "-c" in a else prev)
            prev, n = z, 1
        return "\n".join(out) + ("\n" if out else "")

    def c_echo(self, a, i):
        nl = "-n" not in a
        text = " ".join(x for x in a if x not in ("-n", "-e"))
        if "-e" in a:
            text = text.replace("\\n", "\n").replace("\\t", "\t")
        return text + ("\n" if nl else "")

    def c_tee(self, a, i):
        for d in a:
            if not d.startswith("-"):
                self.neu[self.pfad(d)] = i
        return i

    def c_touch(self, a, i):
        for d in a:
            p = self.pfad(d)
            if posixpath.dirname(p) not in self.verzeichnisse():
                raise Fehler(f"touch: cannot touch '{d}': No such file or directory")
            if p not in self.dateien():
                self.neu[p] = ""
                self.weg.discard(p)
        return ""

    def c_mkdir(self, a, i):
        for d in [x for x in a if not x.startswith("-")]:
            p = self.pfad(d)
            if p in self.verzeichnisse() or p in self.dateien():
                if "-p" in a:
                    continue
                raise Fehler(f"mkdir: cannot create directory '{d}': File exists")
            if posixpath.dirname(p) not in self.verzeichnisse() and "-p" not in a:
                raise Fehler(f"mkdir: cannot create directory '{d}': No such file or directory")
            self.neu_dirs.add(p)
            self.weg.discard(p)
        return ""

    def c_rm(self, a, i):
        flags = "".join(x[1:] for x in a if x.startswith("-"))
        err = []
        for d in [x for x in a if not x.startswith("-")]:
            p = self.pfad(d)
            if p in ("/", "/proc", "/sys", "/dev") or p.startswith(("/proc/", "/sys/")):
                err.append(f"rm: cannot remove '{d}': Operation not permitted")
            elif p in self.verzeichnisse():
                if "r" not in flags and "R" not in flags:
                    err.append(f"rm: cannot remove '{d}': Is a directory")
                else:
                    self.weg.add(p)
            elif p in self.dateien():
                self.weg.add(p)
                self.neu.pop(p, None)
            elif "f" not in flags:
                err.append(f"rm: cannot remove '{d}': No such file or directory")
        return "", "\n".join(err) + ("\n" if err else ""), 1 if err else 0

    def c_rmdir(self, a, i):
        for d in a:
            p = self.pfad(d)
            if p not in self.verzeichnisse():
                raise Fehler(f"rmdir: failed to remove '{d}': No such file or directory")
            if self.kinder(p):
                raise Fehler(f"rmdir: failed to remove '{d}': Directory not empty")
            self.weg.add(p)
        return ""

    def c_cp(self, a, i, verschieben=False):
        x = [y for y in a if not y.startswith("-")]
        prog = "mv" if verschieben else "cp"
        if len(x) < 2:
            raise Fehler(f"{prog}: missing destination file operand after '{x[0] if x else ''}'")
        quelle, ziel = x[0], self.pfad(x[1])
        inhalt = self.lesen(quelle, prog)
        if ziel in self.verzeichnisse():
            ziel = posixpath.join(ziel, posixpath.basename(quelle))
        self.neu[ziel] = inhalt
        self.weg.discard(ziel)
        if verschieben:
            self.weg.add(self.pfad(quelle))
        return ""

    def c_mv(self, a, i):
        return self.c_cp(a, i, verschieben=True)

    def c_find(self, a, i):
        start = self.pfad(a[0] if a and not a[0].startswith("-") else ".")
        muster = a[a.index("-name") + 1] if "-name" in a and a.index("-name") + 1 < len(a) else "*"
        alles = sorted(self.verzeichnisse() | set(self.dateien()))
        out = [p for p in alles if (p == start or p.startswith(start.rstrip("/") + "/")) and fnmatch.fnmatch(posixpath.basename(p), muster)]
        return "\n".join(out) + "\n"

    def c_basename(self, a, i):
        return (posixpath.basename(a[0]) if a else "") + "\n"

    def c_dirname(self, a, i):
        return (posixpath.dirname(a[0]) or ".") + "\n" if a else "\n"

    def c_stat(self, a, i):
        if not a:
            raise Fehler("stat: missing operand")
        p = self.pfad(a[0])
        if p not in self.dateien() and p not in self.verzeichnisse():
            raise Fehler(f"stat: cannot statx '{a[0]}': No such file or directory")
        return f"  File: {a[0]}\n  Size: 4096\tBlocks: 8\tIO Block: 4096\nAccess: (0755/root)\n"

    def c_file(self, a, i):
        return "".join(f"{x}: {'directory' if self.pfad(x) in self.verzeichnisse() else 'ASCII text'}\n" for x in a)

    def c_clear(self, a, i):
        return "", "", 0, "clear"

    def c_history(self, a, i):
        return "".join(f"{n:>5}  {z}\n" for n, z in enumerate(self.hist, 1))

    def c_whoami(self, a, i):
        return "root\n"

    def c_id(self, a, i):
        return "uid=0(root) gid=0(root) groups=0(root)\n"

    def c_hostname(self, a, i):
        return self.name + "\n"

    def c_uname(self, a, i):
        if "-a" in a:
            return f"Linux {self.name} 6.8.0-45-generic #45-Ubuntu SMP PREEMPT_DYNAMIC x86_64 x86_64 x86_64 GNU/Linux\n"
        teile = {"-s": "Linux", "-n": self.name, "-r": "6.8.0-45-generic", "-m": "x86_64", "-o": "GNU/Linux"}
        out = [teile[x] for x in a if x in teile] or ["Linux"]
        return " ".join(out) + "\n"

    def c_uptime(self, a, i):
        s = core.lesen()["servers"][self.name]
        sek = int(core.now() - s.get("power_since", core.now()))
        tage = f"{sek // 86400} days, " if sek >= 86400 else ""
        return f" {datetime.now():%H:%M:%S} up {tage}{sek % 86400 // 3600:2d}:{sek % 3600 // 60:02d},  1 user,  load average: 0.08, 0.05, 0.01\n"

    def c_date(self, a, i):
        return datetime.now().strftime("%a %b %d %H:%M:%S CET %Y") + "\n"

    def c_free(self, a, i):
        s = core.lesen()["servers"][self.name]
        gb = sum(1 for d in s["comp"]["dimm"] if d["status"] == "ok") * 64
        if "-h" in a:
            return ("               total        used        free      shared  buff/cache   available\n"
                    f"Mem:           {gb - 3}Gi        11Gi      {gb - 30}Gi       4.1Mi        16Gi      {gb - 15}Gi\n"
                    "Swap:          8.0Gi          0B       8.0Gi\n")
        kb = (gb - 3) * 1024 * 1024
        return ("               total        used        free      shared  buff/cache   available\n"
                f"Mem:      {kb:>10}    11534336 {kb - 28311552:>11}        4200    16777216 {kb - 15728640:>11}\n"
                "Swap:        8388604           0     8388604\n")

    def c_df(self, a, i):
        h = "-h" in a
        return ("Filesystem      Size  Used Avail Use% Mounted on\n" if h else "Filesystem     1K-blocks     Used Available Use% Mounted on\n") + (
            "tmpfs            51G  4.1M   51G   1% /run\n/dev/nvme0n1p2  3.5T  412G  2.9T  13% /\n/dev/nvme0n1p1  1.1G  6.1M  1.1G   1% /boot/efi\n"
            if h else "tmpfs           52428800     4200  52424600   1% /run\n/dev/nvme0n1p2 3758096384 432013312 3133612800  13% /\n")

    def c_lsblk(self, a, i):
        s = core.lesen()["servers"][self.name]
        out = ["NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS"]
        if s["comp"]["hdd"]:
            out += ["sda           8:0    0  43.7T  0 disk", "└─sda1        8:1    0  43.7T  0 part /data"]
        for n, d in enumerate(s["comp"]["nvme"]):
            if d["status"] == "ok":
                out.append(f"nvme{n}n1     259:{n * 3}    0   3.5T  0 disk" + (" \n├─nvme0n1p1 259:1    0     1G  0 part /boot/efi\n└─nvme0n1p2 259:2    0   3.5T  0 part /" if n == 0 else ""))
        if not s["comp"]["nvme"] and not s["comp"]["hdd"]:
            out.append("sda           8:0    0 480G  0 disk /")
        return "\n".join(out) + "\n"

    def c_ip(self, a, i):
        sub = a[0] if a else ""
        if sub in ("a", "addr", "address"):
            nr = list(core.SERVERS).index(self.name)
            return ("1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000\n"
                    "    inet 127.0.0.1/8 scope host lo\n"
                    "2: eno1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000\n"
                    f"    link/ether 6c:fe:54:3a:1{nr}:0c brd ff:ff:ff:ff:ff:ff\n"
                    f"    inet 10.20.0.{20 + nr}/24 brd 10.20.0.255 scope global eno1\n")
        if sub in ("r", "route"):
            return "default via 10.20.0.1 dev eno1 proto static\n10.20.0.0/24 dev eno1 proto kernel scope link src 10.20.0.20\n"
        if sub in ("l", "link"):
            return "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN\n2: eno1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n"
        return "", 'Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\nwhere  OBJECT := { address | link | route | ... }\n', 255

    def c_lscpu(self, a, i):
        return ("Architecture:             x86_64\nCPU(s):                   224\nVendor ID:                GenuineIntel\n"
                "Model name:               Intel(R) Xeon(R) Platinum 8480+\nThread(s) per core:       2\n"
                "Core(s) per socket:       56\nSocket(s):                2\nNUMA node(s):             2\n")

    def c_dmidecode(self, a, i):
        s = core.lesen()["servers"][self.name]
        t = a[a.index("-t") + 1] if "-t" in a and a.index("-t") + 1 < len(a) else "system"
        if t in ("memory", "17"):
            out = ["# dmidecode 3.5 (Lab)"]
            for d in s["comp"]["dimm"]:
                ok = d["status"] == "ok"
                out.append(f"\nMemory Device\n\tSize: {'64 GB' if ok else 'No Module Installed'}\n\tLocator: {d['name']}\n"
                           f"\tType: DDR5\n\tSpeed: 4800 MT/s\n\tManufacturer: {'Samsung' if ok else 'NO DIMM'}\n\tSerial Number: {d['sn'] if ok else 'NO DIMM'}")
            return "\n".join(out) + "\n"
        if t in ("bios", "0"):
            return f"BIOS Information\n\tVendor: American Megatrends International, LLC.\n\tVersion: {s['fw']['BIOS']}\n"
        return f"System Information\n\tManufacturer: Inspur\n\tProduct Name: {s['modell']}\n\tSerial Number: {s['sn']}\n"

    def c_journalctl(self, a, i):
        text = syslog(self)
        zeilen = text.splitlines()
        if "-k" in a or "--dmesg" in a:
            zeilen = [z for z in zeilen if " kernel: " in z]
        n = None
        for k, x in enumerate(a):
            if x == "-n" and k + 1 < len(a):
                n = int(a[k + 1])
        if n:
            zeilen = zeilen[-n:]
        return "\n".join(zeilen) + "\n"

    def c_smartctl(self, a, i):
        s = core.lesen()["servers"][self.name]
        dev = next((x for x in a if x.startswith("/dev/")), None)
        if not dev:
            return "", "smartctl 7.4 (Lab)\nERROR: smartctl requires a device name as the final command-line argument.\n", 1
        if dev not in self.dateien():
            return "", f"smartctl 7.4 (Lab)\n{dev}: Unable to detect device type\n", 2
        return ("smartctl 7.4 2023-08-01 r5530 [x86_64-linux-6.8.0-45-generic] (Lab)\n=== START OF INFORMATION SECTION ===\n"
                f"Model Number:                       SAMSUNG MZQL23T8HCLS\nFirmware Version:                   GDC5602Q\n"
                "=== START OF SMART DATA SECTION ===\nSMART overall-health self-assessment test result: PASSED\n"
                "Percentage Used:                    3%\nMedia and Data Integrity Errors:    0\n")

    def c_top(self, a, i):
        return (f"top - {datetime.now():%H:%M:%S} up 3 days,  1 user,  load average: 0.08, 0.05, 0.01\n"
                "Tasks: 412 total,   1 running, 411 sleeping,   0 stopped,   0 zombie\n"
                "%Cpu(s):  0.3 us,  0.1 sy,  0.0 ni, 99.6 id,  0.0 wa\n\n"
                "    PID USER      PR  NI    VIRT    RES  %CPU  %MEM     TIME+ COMMAND\n"
                "   2214 root      20   0  305112  12304   0.7   0.0   1:02.11 nvidia-persistenced\n"
                "      1 root      20   0  168452  13212   0.0   0.0   0:09.73 systemd\n"
                "(Lab: Momentaufnahme, interaktives top wird nicht nachgebildet)\n")

    def c_ps(self, a, i):
        return ("USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
                "root           1  0.0  0.0 168452 13212 ?        Ss   Nov08   0:09 /sbin/init\n"
                "root        2214  0.7  0.0 305112 12304 ?        Ssl  Nov08   1:02 /usr/bin/nvidia-persistenced\n"
                "root        2301  0.0  0.0  12204  7400 ?        Ss   Nov08   0:00 sshd: /usr/sbin/sshd -D\n"
                f"root        9921  0.0  0.0   8796  5120 tty1     Ss   {datetime.now():%H:%M}   0:00 -bash\n")

    def c_which(self, a, i):
        out, rc = [], 0
        for x in a:
            if x in BEFEHLE or x in TOOLS:
                out.append(("/usr/sbin/" if x in ("dmidecode", "smartctl", "reboot") else "/usr/bin/") + x)
            else:
                rc = 1
        return "\n".join(out) + ("\n" if out else ""), "", rc

    def c_type(self, a, i):
        out = []
        for x in a:
            if x in ("cd", "pwd", "echo", "export", "history", "alias", "type", "exit"):
                out.append(f"{x} is a shell builtin")
            elif x in BEFEHLE:
                out.append(f"{x} is /usr/bin/{x}")
            else:
                return "", f"-bash: type: {x}: not found\n", 1
        return "\n".join(out) + "\n"

    def c_man(self, a, i):
        if not a:
            return "", "What manual page do you want?\nFor example, try 'man man'.\n", 1
        return "", f"No manual entry for {a[0]}\n(Lab: Handbuchseiten sind nicht nachgebildet)\n", 16

    def c_env(self, a, i):
        return "".join(f"{k}={v}\n" for k, v in sorted({**self.env, "PWD": self.cwd}.items()))

    c_printenv = c_env

    def c_export(self, a, i):
        for x in a:
            if "=" in x:
                k, v = x.split("=", 1)
                self.env[k] = v
        return "" if a else "".join(f'declare -x {k}="{v}"\n' for k, v in sorted(self.env.items()))

    def c_alias(self, a, i):
        return "alias grep='grep --color=auto'\nalias ll='ls -alF'\nalias ls='ls --color=auto'\n"

    def c_exit(self, a, i):
        return "logout\n", "", 0, "logout"

    c_logout = c_exit

    def _power(self, art, text):
        with core.zustand() as st:
            core.power(st, st["servers"][self.name], art)
        return f"\nBroadcast message from root@{self.name} on pts/1:\n\nThe system will {text} now!\n", "", 0, "reboot" if art == "cycle" else "off"

    def c_reboot(self, a, i):
        return self._power("cycle", "reboot")

    def c_poweroff(self, a, i):
        return self._power("soft", "power off")

    c_halt = c_poweroff

    def c_shutdown(self, a, i):
        if "-c" in a:
            return ""
        return self._power("cycle", "reboot") if "-r" in a else self._power("soft", "power off")

    def c_init(self, a, i):
        if a and a[0] == "6":
            return self.c_reboot(a, i)
        if a and a[0] == "0":
            return self.c_poweroff(a, i)
        return "", "init: missing runlevel\n", 1

    def c_sleep(self, a, i):
        try:
            time.sleep(min(5.0, float(a[0])))
        except (IndexError, ValueError):
            return "", "sleep: missing operand\n", 1
        return ""

    def c_sync(self, a, i):
        return ""

    def c_systemctl(self, a, i):
        s = core.lesen()["servers"][self.name]
        dienst = next((x for x in a if not x.startswith("-") and x not in ("status", "restart", "start", "stop", "is-active")), "")
        if "status" in a and dienst:
            name = dienst if dienst.endswith(".service") else dienst + ".service"
            fehlerhaft = name.startswith("nvidia-fabricmanager") and s["comp"]["baseboard"] and s["comp"]["baseboard"][0]["status"] != "ok"
            bekannt = {"nvidia-fabricmanager.service": bool(s["comp"]["baseboard"]), "nvidia-persistenced.service": bool(s["comp"]["gpu"]),
                       "sshd.service": True, "ssh.service": True, "systemd-journald.service": True}
            if not bekannt.get(name):
                return "", f"Unit {name} could not be found.\n", 4
            zustand = "failed (Result: exit-code)" if fehlerhaft else "active (running)"
            text = (f"● {name}\n     Loaded: loaded (/usr/lib/systemd/system/{name}; enabled; preset: enabled)\n"
                    f"     Active: {zustand} since {datetime.now():%a %Y-%m-%d} 08:12:03 CET\n")
            if fehlerhaft:
                text += f"{datetime.now():%b %d} 08:12:03 {self.name} nv-fabricmanager[3120]: NVSwitch fatal error detected, links down\n"
            return text, "", 3 if fehlerhaft else 0
        if "restart" in a or "start" in a or "stop" in a:
            return ""
        return "", "Lab: unterstützt 'systemctl status <dienst>'\n", 1

    def c_lsmod(self, a, i):
        s = core.lesen()["servers"][self.name]
        out = "Module                  Size  Used by\n"
        if s["comp"]["gpu"]:
            out += "nvidia_uvm           4956160  0\nnvidia_drm            122880  0\nnvidia              60678144  2 nvidia_uvm\n"
        return out + "megaraid_sas          196608  2\nnvme                   61440  3\n"

    def c_ping(self, a, i):
        ziel = next((x for x in a if not x.startswith("-")), None)
        if not ziel:
            return "", "ping: usage error: Destination address required\n", 1
        n = 4
        if "-c" in a and a.index("-c") + 1 < len(a):
            n = min(10, int(a[a.index("-c") + 1]))
        out = [f"PING {ziel} ({ziel}) 56(84) bytes of data."]
        for k in range(n):
            out.append(f"64 bytes from {ziel}: icmp_seq={k + 1} ttl=63 time={random.uniform(0.2, 0.9):.3f} ms")
        out += ["", f"--- {ziel} ping statistics ---", f"{n} packets transmitted, {n} received, 0% packet loss"]
        return "\n".join(out) + "\n"

    def c_vi(self, a, i):
        return "", "Lab: Editoren sind in der KVM-Konsole nicht nachgebildet (echo/tee/cat funktionieren).\n", 1

    c_vim = c_nano = c_vi

    def c_sensors(self, a, i):
        s = core.lesen()["servers"][self.name]
        return "coretemp-isa-0000\nAdapter: ISA adapter\n" + "".join(
            f"{n}:  +{w}.0°C\n" for n, w, e, _ in core.sensoren(s) if e == "degrees C" and w is not None and n.startswith("CPU"))

    def c_nvidia_bug_report_sh(self, a, i):
        p = self.pfad("nvidia-bug-report.log.gz")
        self.neu[p] = tool(self.name, "nvidia-smi", ["-q"])[0] + tool(self.name, "dmesg", [])[0]
        return ("nvidia-bug-report.sh will now collect information about your\nsystem and create the file "
                "'nvidia-bug-report.log.gz' in the current directory.\n\nRunning nvidia-bug-report.sh... complete.\n")

    # ---------------- Tab-Vervollstaendigung ----------------
    def vervollstaendigen(self, zeile):
        teile = zeile.split(" ")
        wort = teile[-1]
        if teile[0] == "systemctl" and len(teile) >= 3:
            dienste = ["nvidia-fabricmanager.service", "nvidia-persistenced.service", "ssh.service", "systemd-journald.service"]
            return [x for x in dienste if x.startswith(wort)]
        if len(teile) == 1 or teile[-2] in ("|", "&&", ";", "sudo"):
            if "/" not in wort:
                return sorted(b for b in BEFEHLE if b.startswith(wort))
        d_roh, praefix = posixpath.split(wort)
        d = self.pfad(d_roh or ".")
        if d not in self.verzeichnisse():
            return []
        out = []
        for n, typ in sorted(self.kinder(d).items()):
            if n.startswith(praefix) and (praefix.startswith(".") or not n.startswith(".")):
                out.append(posixpath.join(d_roh, n) + ("/" if typ == "d" else ""))
        return out


# --------------------------------------------------------------------------
# Sitzungen
# --------------------------------------------------------------------------

def neu(name):
    for k, v in list(SITZUNGEN.items()):
        if time.time() - v.t > 3600:
            SITZUNGEN.pop(k, None)
    sid = secrets.token_hex(8)
    SITZUNGEN[sid] = VShell(name)
    return sid


def holen(sid, name):
    sh = SITZUNGEN.get(sid)
    return sh if sh and sh.name == name else None
