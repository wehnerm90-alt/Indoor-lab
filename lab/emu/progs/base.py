"""Gemeinsame Basis fuer emulierte Programme: Kontext, GNU-getopt, Registry."""
import posixpath
import re
import time

from ..builtins import texte
from ..streams import Interrupted, NullReader
from ..vfs import FsError

PROGS = {}          # name -> funktion(ctx) -> int
PFADE = {}          # name -> Verzeichnis (/usr/bin, /usr/sbin, ...)


def prog(*namen, verz="/usr/bin", groesse=None):
    def deko(fn):
        for n in namen:
            PROGS[n] = fn
            PFADE[n] = (verz, groesse)
        return fn
    return deko


SIGNALE = {"HUP": 1, "INT": 2, "QUIT": 3, "ILL": 4, "TRAP": 5, "ABRT": 6, "BUS": 7, "FPE": 8, "KILL": 9, "USR1": 10,
           "SEGV": 11, "USR2": 12, "PIPE": 13, "ALRM": 14, "TERM": 15, "STKFLT": 16, "CHLD": 17, "CONT": 18,
           "STOP": 19, "TSTP": 20, "TTIN": 21, "TTOU": 22, "URG": 23, "XCPU": 24, "XFSZ": 25, "VTALRM": 26,
           "PROF": 27, "WINCH": 28, "POLL": 29, "PWR": 30, "SYS": 31}


class Usage(Exception):
    """Falsche Aufrufsyntax -> Meldung + 'Try ... --help' + rc."""

    def __init__(self, msg, rc=None):
        super().__init__(msg)
        self.msg, self.rc = msg, rc


class ProgFehler(Exception):
    def __init__(self, msg, rc=1):
        super().__init__(msg)
        self.msg, self.rc = msg, rc


class Ctx:
    def __init__(self, sh, argv, fds, env, system):
        self.sh = sh
        self.argv = argv
        self.prog = posixpath.basename(argv[0])
        self.fds = fds
        self.env = env
        self.system = system
        self.fs = system.fs
        self.cwd = sh.cwd
        self.tty = sh.tty

    # ---- IO ----
    def out(self, text):
        w = self.fds.get(1)
        if w is not None and text:
            w.write(text)

    def err(self, text):
        w = self.fds.get(2)
        if w is not None and text:
            w.write(text)

    def fehler(self, msg):
        self.err(f"{self.prog}: {msg}\n")

    @property
    def stdin(self):
        return self.fds.get(0) or NullReader()

    def isatty(self, fd=1):
        return bool(getattr(self.fds.get(fd), "isatty", False))

    @property
    def cols(self):
        try:
            return int(self.env.get("COLUMNS") or (self.tty.cols if self.tty else 80))
        except ValueError:
            return 80

    @property
    def rows(self):
        return self.tty.rows if self.tty else 24

    def check(self):
        self.sh.check()

    def sleep(self, sek):
        ende = time.time() + sek
        while True:
            self.check()
            rest = ende - time.time()
            if rest <= 0:
                return
            time.sleep(min(rest, 0.1))

    # ---- Dateien ----
    def pfad(self, p):
        return self.fs.abs(p, self.cwd)

    def node(self, p, folgen=True):
        return self.fs.aufloesen(p, self.cwd, folgen)[0]

    def lesen(self, p):
        """Dateiinhalt; '-' = stdin. Wirft FsError."""
        if p == "-":
            return self.stdin.read()
        n = self.node(p)
        if n.typ == "d":
            raise FsError("EISDIR")
        if n.typ == "c" and n.rdev == (5, 0):
            return self.tty.read_line() if self.tty else ""
        return n.inhalt()

    def texte(self):
        return texte()["cmd"].get(self.prog, {})

    def hilfe(self, name=None):
        t = texte()["cmd"].get(name or self.prog, {})
        self.out(t.get("help", f"Usage: {name or self.prog} [OPTION]...\n"))
        return 0

    def version(self, name=None):
        t = texte()["cmd"].get(name or self.prog, {})
        self.out(t.get("version", f"{name or self.prog} (GNU coreutils) 9.4\n"))
        return 0

    def usage_fehler(self, msg, name=None, rc=None):
        t = texte()["cmd"].get(name or self.prog, {})
        self.err(f"{self.prog}: {msg}\n" if msg else "")
        self.err(t.get("usage_err", f"Try '{self.prog} --help' for more information.\n"))
        return rc if rc is not None else t.get("usage_rc", 1)

    def getopt(self, short, longs=None, argv=None, permutieren=True, zahl_option=False):
        """GNU getopt_long. short: 'abn:' ; longs: {'name': 0|1|2 (=optional)} oder {'name=': ...}.
        -> (liste[(opt, wert)], operanden). Wirft Usage."""
        return getopt(self.prog, argv if argv is not None else self.argv[1:], short, longs or {}, permutieren,
                      zahl_option)


def getopt(prog, args, short, longs, permutieren=True, zahl_option=False):
    kurz = {}
    i = 0
    while i < len(short):
        c = short[i]
        if c in "+-":
            if c == "+":
                permutieren = False
            i += 1
            continue
        art = 0
        if short[i + 1:i + 3] == "::":
            art, i = 2, i + 3
        elif short[i + 1:i + 2] == ":":
            art, i = 1, i + 2
        else:
            i += 1
        kurz[c] = art
    opts, rest = [], []
    k = 0
    while k < len(args):
        a = args[k]
        if a == "--":
            rest.extend(args[k + 1:])
            break
        if a.startswith("--") and len(a) > 2:
            name, gleich, wert = a[2:].partition("=")
            if name in longs:
                kandidaten = [name]
            else:
                kandidaten = [n for n in longs if n.startswith(name)]
            if not kandidaten:
                raise Usage(f"unrecognized option '{a if not gleich else '--' + name + '=' + wert}'" if not gleich
                            else f"unrecognized option '--{name}={wert}'")
            if len(kandidaten) > 1:
                # gleiche Bedeutung (Alias) gilt nicht als mehrdeutig
                raise Usage(f"option '--{name}' is ambiguous; possibilities:" +
                            "".join(f" '--{c}'" for c in kandidaten))
            voll = kandidaten[0]
            art = longs[voll]
            if art == 0 and gleich:
                raise Usage(f"option '--{voll}' doesn't allow an argument")
            if art == 1 and not gleich:
                if k + 1 >= len(args):
                    raise Usage(f"option '--{voll}' requires an argument")
                k += 1
                wert = args[k]
            opts.append(("--" + voll, wert if (gleich or art == 1) else None))
            k += 1
            continue
        if a.startswith("-") and len(a) > 1:
            if zahl_option and re.fullmatch(r"-\d+", a):
                opts.append(("-NUM", a[1:]))
                k += 1
                continue
            j = 1
            while j < len(a):
                c = a[j]
                if c not in kurz:
                    raise Usage(f"invalid option -- '{c}'")
                art = kurz[c]
                if art == 0:
                    opts.append(("-" + c, None))
                    j += 1
                    continue
                if j + 1 < len(a):
                    opts.append(("-" + c, a[j + 1:]))
                elif art == 1:
                    if k + 1 >= len(args):
                        raise Usage(f"option requires an argument -- '{c}'")
                    k += 1
                    opts.append(("-" + c, args[k]))
                else:
                    opts.append(("-" + c, None))
                break
            k += 1
            continue
        if not permutieren:
            rest.extend(args[k:])
            break
        rest.append(a)
        k += 1
    return opts, rest


def quote_name(n):
    """GNU-Quoting fuer Dateinamen in Meldungen ('datei mit leerzeichen')."""
    if n and re.fullmatch(r"[A-Za-z0-9_./+:,@%=^-]+", n):
        return n
    if "'" not in n:
        return f"'{n}'"
    return '"' + n.replace('"', '\\"') + '"' if '"' not in n else "'" + n.replace("'", "'\\''") + "'"


def qa(n):
    """GNU quoteaf: immer in einfachen Anfuehrungszeichen (shell-escape-always)."""
    if "'" not in n:
        return f"'{n}'"
    if '"' not in n and "$" not in n and "`" not in n and "\\" not in n:
        return '"' + n + '"'
    return "'" + n.replace("'", "'\\''") + "'"


def ql(n):
    """GNU quote(): Locale-Anfuehrungszeichen (UTF-8)."""
    return "\u2018" + n + "\u2019"


def fsmsg(prog, aktion, name, e):
    """z. B. cat: datei: No such file or directory"""
    if aktion:
        return f"{prog}: {aktion} {quote_name(name)}: {e.msg}"
    return f"{prog}: {quote_name(name)}: {e.msg}"


def kill_impl(sh, argv, io, builtin=False):
    args = argv[1:]
    sig = "TERM"
    if not args:
        if builtin:
            io.err("kill: usage: kill [-s sigspec | -n signum | -sigspec] pid | jobspec ... or kill -l [sigspec]\n")
            return 2
        io.err("\nUsage:\n kill [options] <pid> [...]\n")
        return 1
    if args[0] in ("-l", "-L"):
        if len(args) > 1:
            x = args[1]
            if x.isdigit():
                n = int(x) & 127
                name = next((k for k, v in SIGNALE.items() if v == n), None)
                io.out((name or x) + "\n")
            else:
                io.out(str(SIGNALE.get(x.upper().removeprefix("SIG"), "")) + "\n")
            return 0
        zeilen, buf = [], []
        for name, num in sorted(SIGNALE.items(), key=lambda x: x[1]):
            buf.append(f"{num:2d}) SIG{name:<8}")
            if len(buf) == 5:
                zeilen.append("\t".join(buf))
                buf = []
        if buf:
            zeilen.append("\t".join(buf))
        io.out("\n".join(z.rstrip() for z in zeilen) + "\n")
        return 0
    if args[0] == "-s" and len(args) > 1:
        sig, args = args[1], args[2:]
    elif args[0] == "-n" and len(args) > 1:
        sig, args = args[1], args[2:]
    elif args[0].startswith("-") and len(args[0]) > 1:
        sig, args = args[0][1:], args[1:]
    s = sig.upper().removeprefix("SIG")
    if s.isdigit():
        num = int(s)
    elif s in SIGNALE:
        num = SIGNALE[s]
    else:
        io.err(f"{sh.praefix()}kill: {sig}: invalid signal specification\n")
        return 1
    rc = 0
    for a in args:
        if a.startswith("%"):
            nr = a[1:]
            job = next((j for j in sh.jobs if str(j["nr"]) == nr or nr in ("", "+", "%") and j is sh.jobs[-1]), None)
            if not job:
                io.err(f"{sh.praefix()}kill: {a}: no such job\n")
                rc = 1
                continue
            pid = job["pid"]
        elif re.fullmatch(r"-?\d+", a):
            pid = int(a)
        else:
            io.err(f"{sh.praefix()}kill: {a}: arguments must be process or job IDs\n")
            rc = 1
            continue
        ok = sh.system.signal(pid, num)
        if not ok:
            io.err(f"{sh.praefix()}kill: ({pid}) - No such process\n")
            rc = 1
    return rc


__all__ = ["PROGS", "PFADE", "prog", "Ctx", "Usage", "ProgFehler", "getopt", "quote_name", "qa", "ql", "fsmsg", "FsError",
           "Interrupted", "SIGNALE", "kill_impl"]
