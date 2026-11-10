"""System-Werkzeuge: which, file, ps, free, uptime, systemctl, journalctl, ip, ping, apt ... (emuliert)."""
import posixpath
import re
import time

from ..vfs import FsError
from .base import SIGNALE, Usage, kill_impl, prog, quote_name
from .coreutils import hat, human, standard, wert, strftime


@prog("which", groesse=1074)
def which(ctx):
    args = ctx.argv[1:]
    alle = False
    while args and args[0].startswith("-"):
        if args[0] == "-a":
            alle = True
        elif args[0] == "-s":
            pass
        else:
            ctx.err("Illegal option --\n") if False else None
            ctx.err(f"Usage: /usr/bin/which [-as] args\n")
            return 2
        args = args[1:]
    rc = 0
    for a in args:
        gefunden = False
        if "/" in a:
            n = ctx.fs.finde(a, ctx.cwd)
            if n is not None and n.typ != "d" and n.mode & 0o111:
                ctx.out(a + "\n")
                gefunden = True
        else:
            for d in (ctx.env.get("PATH") or "").split(":"):
                p = posixpath.join(d or ".", a)
                n = ctx.fs.finde(p, ctx.cwd)
                if n is not None and n.typ != "d" and n.mode & 0o111:
                    ctx.out(p + "\n")
                    gefunden = True
                    if not alle:
                        break
        if not gefunden:
            rc = 1
    return rc


@prog("whereis")
def whereis(ctx):
    for a in [x for x in ctx.argv[1:] if not x.startswith("-")]:
        treffer = []
        for d in ("/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin", "/usr/lib"):
            if ctx.fs.finde(f"{d}/{a}") is not None:
                treffer.append(f"{d}/{a}")
        if treffer:
            treffer.append(f"/usr/share/man/man1/{a}.1.gz")
        ctx.out(f"{a}:" + "".join(" " + t for t in treffer) + "\n")
    return 0


@prog("file")
def file(ctx):
    r = standard(ctx, "bcCdEhikLlNnprsSvzZ0", {"brief": 0, "mime": 0, "mime-type": 0, "dereference": 0,
                                               "no-dereference": 0, "separator": 1, "special-files": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        ctx.err("Usage: file [-bcCdEhikLlNnprsSvzZ0] [--apple] [--extension] [--mime-encoding]\n"
                "            [--mime-type] [-e <testname>] [-F <separator>]  [-f <namefile>]\n"
                "            [-m <magicfiles>] [-P <parameter=value>] [--exclude-quiet]\n"
                "            <file> ...\n       file -C [-m <magicfiles>]\n       file [--help]\n")
        return 1
    kurz = hat(opts, "-b", "--brief")
    breite = max(len(a) for a in args) + 1
    for a in args:
        n = ctx.fs.finde(a, ctx.cwd, folgen=hat(opts, "-L", "--dereference"))
        if n is None:
            beschr = f"cannot open `{a}' (No such file or directory)"
        else:
            beschr = _file_typ(ctx, a, n)
        ctx.out((beschr if kurz else f"{a + ':':<{breite}} {beschr}") + "\n")
    return 0


def _file_typ(ctx, a, n):
    if n.typ == "d":
        return "sticky, directory" if n.mode & 0o1000 else "directory"
    if n.typ == "l":
        ziel = ctx.fs.finde(a, ctx.cwd)
        return f"symbolic link to {n.ziel}" if ziel is not None else f"broken symbolic link to {n.ziel}"
    if n.typ == "c":
        return f"character special ({n.rdev[0]}/{n.rdev[1]})"
    if n.typ == "b":
        return f"block special ({n.rdev[0]}/{n.rdev[1]})"
    t = n.inhalt()
    if not t:
        return "empty"
    if t.startswith("\x7fELF"):
        if "shared library" in t:
            return ("ELF 64-bit LSB shared object, x86-64, version 1 (GNU/Linux), dynamically linked, "
                    "BuildID[sha1]=2e5abcec2e6c1b0bd5f8ee6bb4e3ca1e74d6e3c1, stripped")
        return ("ELF 64-bit LSB pie executable, x86-64, version 1 (SYSV), dynamically linked, interpreter "
                "/lib64/ld-linux-x86-64.so.2, BuildID[sha1]=36b86f957a1be53733633d184c3a3354f3fc7b12, "
                "for GNU/Linux 3.2.0, stripped")
    if t.startswith("\x1f\x8b"):
        return "gzip compressed data, from Unix, original size modulo 2^32 10240"
    if t.startswith("TZif"):
        return "timezone data (fat), version 2, no gmt time, no leap seconds"
    if t.startswith("#!"):
        interp = t.split("\n", 1)[0][2:].strip()
        base = posixpath.basename(interp.split()[0]) if interp else ""
        art = {"bash": "Bourne-Again shell script", "sh": "POSIX shell script", "python3": "Python script"}.get(
            base, f"a {interp} script")
        return f"{art}, ASCII text executable"
    if t.startswith("NVROM"):
        return "data"
    if all(ord(c) < 128 for c in t[:4096]):
        lang = " with very long lines" if any(len(z) > 300 for z in t.split("\n")[:200]) else ""
        crlf = ", with CRLF line terminators" if "\r\n" in t else ""
        noeol = ", with no line terminators" if "\n" not in t else ""
        return f"ASCII text{lang}{crlf}{noeol}"
    return "Unicode text, UTF-8 text"


@prog("clear")
def clear(ctx):
    if ctx.argv[1:2] == ["-x"]:
        ctx.out("\x1b[H\x1b[2J")
    else:
        ctx.out("\x1b[H\x1b[2J\x1b[3J")
    return 0


@prog("reset")
def reset(ctx):
    ctx.out("\x1bc\x1b[H\x1b[2J")
    return 0


@prog("tput")
def tput(ctx):
    a = ctx.argv[1:]
    if not a:
        ctx.err("usage: tput [-V] [-S] [-T term] capname\n")
        return 2
    m = {"cols": str(ctx.cols), "lines": str(ctx.rows), "clear": "\x1b[H\x1b[2J", "bold": "\x1b[1m",
         "sgr0": "\x1b(B\x1b[m", "smul": "\x1b[4m", "rev": "\x1b[7m", "colors": "8", "longname": "linux console"}
    if a[0] == "setaf" and len(a) > 1:
        ctx.out(f"\x1b[3{a[1]}m")
        return 0
    if a[0] == "setab" and len(a) > 1:
        ctx.out(f"\x1b[4{a[1]}m")
        return 0
    if a[0] in m:
        ctx.out(m[a[0]] + ("\n" if a[0] in ("cols", "lines", "colors", "longname") else ""))
        return 0
    ctx.err(f"tput: unknown terminfo capability '{a[0]}'\n")
    return 4


@prog("sudo")
def sudo(ctx):
    args = ctx.argv[1:]
    while args and args[0].startswith("-"):
        if args[0] in ("-i", "-s"):
            ctx.out("") if False else None
        if args[0] in ("-u", "-g") and len(args) > 1:
            args = args[2:]
            continue
        if args[0] == "-V":
            ctx.out("Sudo version 1.9.15p5\nSudoers policy plugin version 1.9.15p5\n")
            return 0
        if args[0] in ("-h", "--help"):
            ctx.out("sudo - execute a command as another user\n\nusage: sudo -h | -K | -k | -V\n")
            return 0
        args = args[1:]
    if not args:
        ctx.err("usage: sudo -h | -K | -k | -V\nusage: sudo -v [-ABkNnS] [-g group] [-h host] [-p prompt] [-u user]\n"
                "usage: sudo -l [-ABkNnS] [-g group] [-h host] [-p prompt] [-U user]\n            [-u user] [command [arg ...]]\n"
                "usage: sudo [-ABbEHkNnPS] [-r role] [-t type] [-C num] [-D directory]\n"
                "            [-g group] [-h host] [-p prompt] [-R directory] [-T timeout]\n"
                "            [-u user] [VAR=value] [-i | -s] [command [arg ...]]\n"
                "usage: sudo -e [-ABkNnS] [-r role] [-t type] [-C num] [-D directory]\n"
                "            [-g group] [-h host] [-p prompt] [-R directory] [-T timeout]\n"
                "            [-u user] file ...\n")
        return 1
    from ..builtins import BUILTINS
    if args[0] in BUILTINS and args[0] not in ("echo", "printf", "kill", "test", "[", "true", "false", "pwd"):
        ctx.err(f"sudo: {args[0]}: command not found\n")
        return 1
    pfad, node = ctx.sh.finde_programm(args[0])
    if pfad is None:
        ctx.err(f"sudo: {args[0]}: command not found\n")
        return 1
    return ctx.sh.extern(args, ctx.fds, ctx.env)


@prog("su")
def su(ctx):
    args = [a for a in ctx.argv[1:] if a not in ("-", "-l", "--login")]
    if "-c" in args:
        i = args.index("-c")
        if i + 1 < len(args):
            return ctx.sh.subshell().run(args[i + 1], ctx.fds)
    user = args[0] if args else "root"
    if user != "root" and ctx.system.uid_von(user) is None:
        ctx.err(f"su: user {user} does not exist or the user entry does not contain all the required fields\n")
        return 1
    return 0


# ---------------------------------------------------------------------------- Prozesse
def _etime(sek):
    sek = int(sek)
    d, r = divmod(sek, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}-{h:02d}:{m:02d}:{s:02d}"
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@prog("ps")
def ps(ctx):
    args = ctx.argv[1:]
    procs = sorted(ctx.system.prozesse.values(), key=lambda p: p["pid"])
    jetzt = time.time()
    flags = "".join(args)
    if args and args[0] in ("--help", "-h") or args[:1] == ["--help"]:
        ctx.out("\nUsage:\n ps [options]\n\n Try 'ps --help <simple|list|output|threads|misc|all>'\n"
                "  or 'ps --help <s|l|o|t|m|a>'\n for additional help text.\n\nFor more details see ps(1).\n")
        return 0
    if args[:1] in (["-V"], ["--version"]):
        ctx.out("ps from procps-ng 4.0.4\n")
        return 0
    ausw = None
    if "-p" in args:
        i = args.index("-p")
        ausw = {int(x) for x in re.split(r"[ ,]", args[i + 1]) if x.isdigit()} if i + 1 < len(args) else set()
    if "-C" in args:
        i = args.index("-C")
        name = args[i + 1] if i + 1 < len(args) else ""
        procs = [p for p in procs if p["comm"] == name]
    if ausw is not None:
        procs = [p for p in procs if p["pid"] in ausw]
    zeit_ges = lambda p: "00:00:00"  # noqa: E731
    if args and (args[0] in ("aux", "-aux", "axu", "uax", "-aux") or re.fullmatch(r"-?[aux]+", args[0] or "") and "u" in args[0]):
        out = ["USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"]
        for p in procs:
            start = time.strftime("%H:%M", time.gmtime(p["start"])) if jetzt - p["start"] < 86400 else time.strftime("%b%d", time.gmtime(p["start"]))
            user = p["user"][:8] if len(p["user"]) <= 8 else p["user"][:7] + "+"
            out.append(f"{user:<8} {p['pid']:>7}  0.0  0.0 {p['vsz']:>6} {p['rss']:>5} {p['tty']:<8} {p['stat']:<4} "
                       f"{start:>5}   0:00 {p['cmd']}\n")
        ctx.out("".join(out))
        return 0
    if args and args[0] in ("-ef", "-e", "-A", "ax", "-ax", "aux", "-eF", "-ely", "-elf", "axf", "-efH", "-eH", "-ejH"):
        if "f" in args[0] and args[0] != "axf" or args[0] in ("-ef",):
            out = ["UID          PID    PPID  C STIME TTY          TIME CMD\n"]
            for p in procs:
                stime = time.strftime("%H:%M", time.gmtime(p["start"])) if jetzt - p["start"] < 86400 else time.strftime("%b%d", time.gmtime(p["start"]))
                out.append(f"{p['user'][:8]:<8} {p['pid']:>7} {p['ppid']:>7}  0 {stime} {p['tty']:<8} {zeit_ges(p)} {p['cmd']}\n")
            ctx.out("".join(out))
            return 0
        if args[0] in ("ax", "-ax", "axf"):
            out = ["    PID TTY      STAT   TIME COMMAND\n"]
            for p in procs:
                out.append(f"{p['pid']:>7} {p['tty']:<8} {p['stat']:<6} 0:00 {p['cmd']}\n")
            ctx.out("".join(out))
            return 0
        out = ["    PID TTY          TIME CMD\n"]
        for p in procs:
            out.append(f"{p['pid']:>7} {p['tty']:<8} {zeit_ges(p)} {p['comm']}\n")
        ctx.out("".join(out))
        return 0
    if args and args[0].startswith("-") and "o" in args[0] or "-o" in args or "o" in args[:1]:
        i = args.index("-o") if "-o" in args else 0
        felder = args[i + 1].split(",") if i + 1 < len(args) else ["pid"]
        k = {"pid": ("PID", "pid", 7), "ppid": ("PPID", "ppid", 7), "user": ("USER", "user", -8),
             "comm": ("COMMAND", "comm", -15), "cmd": ("CMD", "cmd", -30), "args": ("COMMAND", "cmd", -30),
             "stat": ("STAT", "stat", -4), "tty": ("TT", "tty", -8), "etime": ("ELAPSED", "etime", 11),
             "%cpu": ("%CPU", "cpu", 4), "%mem": ("%MEM", "mem", 4), "rss": ("RSS", "rss", 5)}
        if "-e" not in args and "-A" not in args and ausw is None and "-C" not in args:
            procs = [p for p in procs if p["tty"] == "tty1"]
        zeilen = []
        kopf = []
        for f in felder:
            name = f.split("=")[0]
            if name not in k:
                ctx.err(f"error: unknown user-defined format specifier \"{name}\"\n")
                return 1
            kopf.append(k[name])
        if not all("=" in f and f.endswith("=") for f in felder):
            zeilen.append(" ".join(f"{h:>{w}}" if w > 0 else f"{h:<{-w}}" for h, _, w in kopf).rstrip() + "\n")
        for p in procs:
            werte = []
            for h, key, w in kopf:
                v = _etime(jetzt - p["start"]) if key == "etime" else ("0.0" if key in ("cpu", "mem") else p[key])
                werte.append(f"{v:>{w}}" if w > 0 else f"{v:<{-w}}")
            zeilen.append(" ".join(werte).rstrip() + "\n")
        ctx.out("".join(zeilen))
        return 0
    out = ["    PID TTY          TIME CMD\n"]
    for p in procs:
        if p["tty"] == "tty1" and p["pid"] != 1301:
            out.append(f"{p['pid']:>7} {p['tty']:<8} 00:00:00 {p['comm']}\n")
    ctx.out("".join(out))
    return 0


@prog("pgrep", "pidof", "pkill", "killall")
def pgrep(ctx):
    args = [a for a in ctx.argv[1:]]
    if not args:
        if ctx.prog in ("pgrep", "pkill"):
            ctx.err(f"{ctx.prog}: no matching criteria specified\nTry `{ctx.prog} --help' for more information.\n")
            return 2
        return 1
    sig = 15
    voll = "-f" in args
    liste = "-l" in args or "-a" in args
    muster = [a for a in args if not a.startswith("-")]
    for a in args:
        m = re.fullmatch(r"-(\d+|[A-Z]+)", a)
        if m and a not in ("-f", "-l", "-a"):
            s = m.group(1)
            sig = int(s) if s.isdigit() else SIGNALE.get(s.removeprefix("SIG"), 15)
    treffer = []
    for p in sorted(ctx.system.prozesse.values(), key=lambda x: x["pid"]):
        for mu in muster:
            ziel = p["cmd"] if voll else p["comm"]
            if ctx.prog in ("pidof", "killall"):
                if p["comm"] == mu or posixpath.basename(p["cmd"].split()[0]) == mu:
                    treffer.append(p)
            elif re.search(mu, ziel):
                treffer.append(p)
    if ctx.prog == "pidof":
        if treffer:
            ctx.out(" ".join(str(p["pid"]) for p in reversed(treffer)) + "\n")
            return 0
        return 1
    if ctx.prog in ("pkill", "killall"):
        if not treffer:
            if ctx.prog == "killall":
                ctx.err(f"{muster[0]}: no process found\n")
            return 1
        for p in treffer:
            ctx.system.signal(p["pid"], sig)
        return 0
    for p in treffer:
        ctx.out(f"{p['pid']} {p['cmd'] if '-a' in args else p['comm']}\n" if liste else f"{p['pid']}\n")
    return 0 if treffer else 1


@prog("kill")
def kill_ext(ctx):
    from ..shell import IO
    if ctx.argv[1:2] == ["-V"] or ctx.argv[1:2] == ["--version"]:
        ctx.out("kill from procps-ng 4.0.4\n")
        return 0
    return kill_impl(ctx.sh, ctx.argv, IO(ctx.fds), builtin=False)


@prog("free")
def free(ctx):
    r = standard(ctx, "bkmghltswcv", {"bytes": 0, "kilo": 0, "mega": 0, "giga": 0, "tera": 0, "peta": 0,
                                       "kibi": 0, "mebi": 0, "gibi": 0, "tebi": 0, "pebi": 0, "human": 0,
                                       "si": 0, "lohi": 0, "total": 0, "seconds": 1, "count": 1, "wide": 0,
                                       "committed": 0, "line": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    mem = ctx.system.hooks.get("mem", lambda: (16 * 1024 ** 2, 1024 ** 2, 512 * 1024, 2 * 1024 ** 2, 0))()
    gesamt, used, shared, cache, swap = mem
    frei = gesamt - used - cache
    avail = gesamt - used
    sw_t = swap
    h = hat(opts, "-h", "--human")
    teiler = 1
    if hat(opts, "-m", "--mebi"):
        teiler = 1024
    elif hat(opts, "-g", "--gibi"):
        teiler = 1024 ** 2
    elif hat(opts, "-b", "--bytes"):
        teiler = 1 / 1024

    def f(kb):
        if h:
            v = human(kb * 1024, ceil=False)
            v = re.sub(r"([KMGT])$", r"\1i", v) if v[-1].isalpha() else v + "B"
            return v
        return str(int(kb / teiler))
    ctx.out("               total        used        free      shared  buff/cache   available\n")
    ctx.out(f"Mem:    {f(gesamt):>12}{f(used):>12}{f(frei):>12}{f(shared):>12}{f(cache):>12}{f(avail):>12}\n")
    ctx.out(f"Swap:   {f(sw_t):>12}{f(0):>12}{f(sw_t):>12}\n")
    return 0


def _uptime_text(ctx):
    up = time.time() - ctx.system.boot
    jetzt = strftime("%H:%M:%S", time.time())
    tage, r = divmod(int(up), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if tage:
        dauer = f"{tage} day{'s' if tage != 1 else ''}, " + (f"{h:2d}:{m:02d}" if h else f"{m} min")
    else:
        dauer = f"{h:2d}:{m:02d}" if h else f"{m} min"
    return f" {jetzt} up {dauer},  1 user,  load average: 0.08, 0.05, 0.01"


@prog("uptime")
def uptime(ctx):
    r = standard(ctx, "psV", {"pretty": 0, "since": 0})
    if isinstance(r, int):
        return r
    opts, _ = r
    if hat(opts, "-p", "--pretty"):
        up = int(time.time() - ctx.system.boot)
        h, r2 = divmod(up, 3600)
        m = r2 // 60
        teile = []
        if h:
            teile.append(f"{h} hour{'s' if h != 1 else ''}")
        teile.append(f"{m} minute{'s' if m != 1 else ''}")
        ctx.out("up " + ", ".join(teile) + "\n")
        return 0
    if hat(opts, "-s", "--since"):
        ctx.out(strftime("%Y-%m-%d %H:%M:%S", ctx.system.boot) + "\n")
        return 0
    ctx.out(_uptime_text(ctx) + "\n")
    return 0


@prog("w")
def w(ctx):
    ctx.out(_uptime_text(ctx) + "\n")
    ctx.out("USER     TTY      FROM             LOGIN@   IDLE   JCPU   PCPU WHAT\n")
    ctx.out(f"root     tty1     -                {strftime('%H:%M', ctx.system.boot + 60)}    0.00s  0.02s  0.00s w\n")
    return 0


@prog("who")
def who(ctx):
    if "-b" in ctx.argv:
        ctx.out(f"         system boot  {strftime('%Y-%m-%d %H:%M', ctx.system.boot)}\n")
        return 0
    if "-r" in ctx.argv:
        ctx.out(f"         run-level 5  {strftime('%Y-%m-%d %H:%M', ctx.system.boot)}\n")
        return 0
    ctx.out(f"root     tty1         {strftime('%Y-%m-%d %H:%M', ctx.system.boot + 60)}\n")
    return 0


@prog("last")
def last(ctx):
    b = ctx.system.boot
    ctx.out(f"root     tty1                          {strftime('%a %b %e %H:%M', b + 60)}   still logged in\n"
            f"reboot   system boot  {ctx.system.kernel:<16} {strftime('%a %b %e %H:%M', b)}   still running\n\n"
            f"wtmp begins {strftime('%a %b %e %H:%M:%S %Y', b - 86400 * 70)}\n")
    return 0


@prog("lsblk")
def lsblk(ctx):
    r = standard(ctx, "abdDzfiJlmnOo:pPrsSTx:ME:e:", {"all": 0, "bytes": 0, "nodeps": 0, "discard": 0,
                                                       "fs": 0, "ascii": 0, "json": 0, "list": 0, "perms": 0,
                                                       "noheadings": 0, "output": 1, "output-all": 0,
                                                       "paths": 0, "pairs": 0, "raw": 0, "scsi": 0, "tree": 2,
                                                       "sort": 1, "merge": 0, "exclude": 1, "include": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    geraete = ctx.system.hooks.get("blockdev", lambda: [])()
    if hat(opts, "-f", "--fs"):
        ctx.out("NAME                      FSTYPE      FSVER    LABEL UUID                                   FSAVAIL FSUSE% MOUNTPOINTS\n")
        for name, groesse, typ, mnt, fstype, uuid, label in geraete:
            ctx.out(f"{name:<25} {fstype:<11} {'1.0' if fstype else '':<8} {label:<5} {uuid:<38} "
                    f"{'' if not mnt else '':>7} {'':>6} {mnt}\n".rstrip() + "\n")
        return 0
    ctx.out("NAME                      MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS\n")
    for i, (name, groesse, typ, mnt, *_rest) in enumerate(geraete):
        majmin = _majmin(name)
        ctx.out(f"{name:<25} {majmin:>7}  0 {groesse:>6}  0 {typ:<4} {mnt}".rstrip() + "\n")
    return 0


def _majmin(name):
    n = name.strip("├─└│ ")
    if n.startswith("nvme"):
        m = re.match(r"nvme(\d+)n1(?:p(\d+))?", n)
        return f"259:{int(m.group(1)) * 4 + int(m.group(2) or 0)}" if m else "259:0"
    if n.startswith("sd"):
        idx = ord(n[2]) - 97
        part = int(n[3:] or 0)
        return f"8:{idx * 16 + part}"
    if n.startswith("loop"):
        return f"7:{n[4:] or 0}"
    if "ubuntu" in n:
        return "252:0"
    return "0:0"


@prog("blkid", verz="/usr/sbin")
def blkid(ctx):
    for name, groesse, typ, mnt, fstype, uuid, label in ctx.system.hooks.get("blockdev", lambda: [])():
        if fstype:
            dev = "/dev/" + name.strip("├─└│ ") if "ubuntu" not in name else "/dev/mapper/ubuntu--vg-ubuntu--lv"
            ctx.out(f'{dev}: UUID="{uuid}" BLOCK_SIZE="4096" TYPE="{fstype}"' +
                    (f' LABEL="{label}"' if label else "") + "\n")
    return 0


@prog("mount")
def mount(ctx):
    args = ctx.argv[1:]
    if not args or args == ["-l"]:
        ctx.out(ctx.system.hooks.get("mounts_mount", lambda: "")())
        return 0
    if args[0] in ("-h", "--help"):
        return ctx.hilfe()
    if args[0] in ("-V", "--version"):
        ctx.out("mount from util-linux 2.39.3 (libmount 2.39.3: selinux, smack, btrfs, verity, namespaces, idmapping, "
                "statx, assert, debug)\n")
        return 0
    hook = ctx.system.hooks.get("mount")
    if hook:
        return hook(ctx, args)
    ziel = [a for a in args if not a.startswith("-")]
    if len(ziel) == 1:
        ctx.err(f"mount: {ziel[0]}: can't find in /etc/fstab.\n")
        return 1
    dev, mnt = ziel[0], ziel[1]
    if ctx.fs.finde(dev, ctx.cwd) is None:
        ctx.err(f"mount: {mnt}: special device {dev} does not exist.\n       dmesg(1) may have more information after "
                "failed mount system call.\n")
        return 32
    if ctx.fs.finde(mnt, ctx.cwd) is None:
        ctx.err(f"mount: {mnt}: mount point does not exist.\n       dmesg(1) may have more information after "
                "failed mount system call.\n")
        return 32
    return 0


@prog("umount")
def umount(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.err("Usage:\n umount [-hV]\n umount -a [options]\n umount [options] <source> | <directory>\n\n"
                "Unmount filesystems.\n")
        return 1
    hook = ctx.system.hooks.get("umount")
    if hook:
        return hook(ctx, args)
    ctx.err(f"umount: {args[0]}: not mounted.\n")
    return 32


@prog("findmnt")
def findmnt(ctx):
    ctx.out(ctx.system.hooks.get("findmnt", lambda: "TARGET SOURCE FSTYPE OPTIONS\n/      /dev/sda1 ext4 rw\n")())
    return 0


@prog("lscpu")
def lscpu(ctx):
    ctx.out(ctx.system.hooks.get("lscpu", lambda: "Architecture:            x86_64\n")())
    return 0


@prog("dmidecode", verz="/usr/sbin")
def dmidecode(ctx):
    hook = ctx.system.hooks.get("dmidecode")
    return hook(ctx) if hook else 0


# ---------------------------------------------------------------------------- systemd
DIENSTE_BASIS = {
    "ssh": ("OpenBSD Secure Shell server", "active (running)", "sshd -D"),
    "cron": ("Regular background program processing daemon", "active (running)", "/usr/sbin/cron -f -P"),
    "systemd-journald": ("Journal Service", "active (running)", "/usr/lib/systemd/systemd-journald"),
    "systemd-networkd": ("Network Configuration", "active (running)", "/usr/lib/systemd/systemd-networkd"),
    "systemd-resolved": ("Network Name Resolution", "active (running)", "/usr/lib/systemd/systemd-resolved"),
    "rsyslog": ("System Logging Service", "active (running)", "/usr/sbin/rsyslogd -n -iNONE"),
    "systemd-logind": ("User Login Management", "active (running)", "/usr/lib/systemd/systemd-logind"),
    "irqbalance": ("irqbalance daemon", "active (running)", "/usr/sbin/irqbalance --foreground"),
}


@prog("systemctl")
def systemctl(ctx):
    args = [a for a in ctx.argv[1:] if a not in ("--no-pager", "-l", "--full", "--quiet", "-q")]
    still = "-q" in ctx.argv or "--quiet" in ctx.argv
    dienste = dict(DIENSTE_BASIS)
    dienste.update(ctx.system.hooks.get("dienste", lambda: {})())
    if not args or args[0] in ("list-units",):
        ctx.out("  UNIT                                LOAD   ACTIVE SUB     DESCRIPTION\n")
        for n, (beschr, zustand, _c) in sorted(dienste.items()):
            akt, sub = (zustand.split(" (")[0], zustand.split("(")[1].rstrip(")")) if "(" in zustand else (zustand, "dead")
            ctx.out(f"  {n + '.service':<35} loaded {akt:<6} {sub:<7} {beschr}\n")
        ctx.out(f"\nLegend: LOAD   → Reflects whether the unit definition was properly loaded.\n"
                f"        ACTIVE → The high-level unit activation state, i.e. generalization of SUB.\n"
                f"        SUB    → The low-level unit activation state, values depend on unit type.\n\n"
                f"{len(dienste)} loaded units listed.\n")
        return 0
    if args[0] in ("--help", "-h"):
        return ctx.hilfe()
    if args[0] in ("--version",):
        ctx.out("systemd 255 (255.4-1ubuntu8.4)\n")
        return 0
    cmd = args[0]
    namen = [a.removesuffix(".service") for a in args[1:]]
    if cmd in ("reboot", "poweroff", "halt"):
        return ctx.system.hooks.get("power", lambda c, a: 0)(ctx, cmd)
    if cmd in ("daemon-reload",):
        return 0
    if cmd == "list-unit-files":
        ctx.out("UNIT FILE                              STATE           PRESET\n")
        for n in sorted(dienste):
            ctx.out(f"{n + '.service':<38} enabled         enabled\n")
        ctx.out(f"\n{len(dienste)} unit files listed.\n")
        return 0
    if not namen:
        ctx.err("Too few arguments.\n")
        return 1
    rc = 0
    for n in namen:
        if n not in dienste:
            if cmd == "status":
                ctx.err(f"Unit {n}.service could not be found.\n")
                rc = 4
            elif cmd in ("is-active",):
                ctx.out("inactive\n")
                rc = 3
            elif cmd in ("is-enabled",):
                ctx.err(f"Failed to get unit file state for {n}.service: No such file or directory\n")
                rc = 1
            else:
                ctx.err(f"Failed to {cmd} {n}.service: Unit {n}.service not found.\n")
                rc = 5
            continue
        beschr, zustand, cmdline = dienste[n]
        hook = ctx.system.hooks.get("dienst_aktion")
        if cmd in ("start", "stop", "restart", "enable", "disable", "reload", "kill", "mask", "unmask"):
            if hook:
                r2 = hook(ctx, cmd, n)
                if r2 is not None:
                    rc = rc or r2
            if cmd in ("enable", "disable") and not still:
                ctx.err(f"Synchronizing state of {n}.service with SysV service script with /usr/lib/systemd/systemd-sysv-install.\n") if False else None
            continue
        if cmd == "is-active":
            akt = zustand.split(" ")[0]
            if not still:
                ctx.out(akt + "\n")
            rc = 0 if akt == "active" else 3
            continue
        if cmd == "is-enabled":
            ctx.out("enabled\n")
            continue
        if cmd == "status":
            seit = ctx.system.boot + 8
            aktiv = zustand.startswith("active")
            punkt = "\x1b[0;1;32m●\x1b[0m" if aktiv and ctx.isatty(1) else ("○" if not aktiv and "failed" not in zustand else "×" if "failed" in zustand else "●")
            ctx.out(f"{punkt} {n}.service - {beschr}\n"
                    f"     Loaded: loaded (/usr/lib/systemd/system/{n}.service; enabled; preset: enabled)\n")
            if aktiv:
                pid = next((p["pid"] for p in ctx.system.prozesse.values() if cmdline.split()[0] in p["cmd"]), 1100)
                ctx.out(f"     Active: {zustand} since {strftime('%a %Y-%m-%d %H:%M:%S UTC', seit)}; "
                        f"{_seit(time.time() - seit)} ago\n"
                        f"   Main PID: {pid} ({posixpath.basename(cmdline.split()[0])[:15]})\n"
                        f"      Tasks: 1 (limit: 618847)\n     Memory: 2.1M (peak: 2.6M)\n        CPU: 41ms\n"
                        f"     CGroup: /system.slice/{n}.service\n             └─{pid} {cmdline}\n")
                log = ctx.system.hooks.get("dienst_log", lambda n: [])(n)
                if log:
                    ctx.out("\n" + "".join(f"{strftime('%b %d %H:%M:%S', seit - 1)} {ctx.system.hostname} {z}\n" for z in log))
                rc = 0
            else:
                ctx.out(f"     Active: {zustand}\n")
                log = ctx.system.hooks.get("dienst_log", lambda n: [])(n)
                if log:
                    ctx.out("\n" + "".join(f"{strftime('%b %d %H:%M:%S', seit)} {ctx.system.hostname} {z}\n" for z in log))
                rc = 3
    return rc


def _seit(s):
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}min {s}s" if False else f"{m}min"
    h, m = divmod(m, 60)
    return f"{h}h {m}min"


@prog("service", verz="/usr/sbin")
def service(ctx):
    a = ctx.argv[1:]
    if len(a) < 2:
        ctx.err("Usage: service < option > | --status-all | [ service_name [ command | --full-restart ] ]\n")
        return 1
    ctx.argv = ["systemctl", a[1], a[0]]
    return systemctl(ctx)


@prog("journalctl")
def journalctl(ctx):
    zeilen = ctx.system.hooks.get("journal", lambda: [])()
    args = ctx.argv[1:]
    if "-k" in args or "--dmesg" in args:
        zeilen = [z for z in zeilen if " kernel: " in z]
    if "-u" in args:
        i = args.index("-u")
        u = args[i + 1].removesuffix(".service") if i + 1 < len(args) else ""
        zeilen = [z for z in zeilen if u.split("-")[0] in z]
        if not zeilen:
            ctx.out("-- No entries --\n")
            return 0
    if "-n" in args:
        i = args.index("-n")
        try:
            zeilen = zeilen[-int(args[i + 1]):]
        except (ValueError, IndexError):
            pass
    if "-p" in args or "--priority" in args:
        zeilen = [z for z in zeilen if re.search(r"error|fail|Xid|critical", z, re.I)]
    if "-f" in args:
        ctx.out("".join(z + "\n" for z in zeilen[-10:]))
        while True:
            ctx.sleep(1)
    if "-r" in args:
        zeilen = list(reversed(zeilen))
    if not ctx.isatty(1) or "--no-pager" in args:
        ctx.out("".join(z + "\n" for z in zeilen))
        return 0
    from .screen import pager
    return pager(ctx, "".join(z + "\n" for z in zeilen), "journalctl", lines_text=True)


# ---------------------------------------------------------------------------- Netzwerk
@prog("ip", verz="/usr/sbin")
def ip(ctx):
    args = [a for a in ctx.argv[1:] if a not in ("-c", "-color", "--color")]
    kurz = False
    if args and args[0] in ("-br", "-brief"):
        kurz, args = True, args[1:]
    if args and args[0] == "-4":
        args = args[1:]
    if not args:
        ctx.err("Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n       ip [ -force ] -batch filename\n"
                "where  OBJECT := { address | addrlabel | amt | fou | help | ila | ioam | l2tp | link |\n"
                "                   macsec | maddress | monitor | mptcp | mroute | mrule |\n"
                "                   neighbor | neighbour | netconf | netns | nexthop | ntable |\n"
                "                   ntbl | route | rule | sr | stats | tap | tcpmetrics |\n"
                "                   token | tunnel | tuntap | vrf | xfrm }\n")
        return 255
    obj = args[0]
    ifs = ctx.system.hooks.get("netz", lambda: [("lo", "00:00:00:00:00:00", "127.0.0.1/8", "UP")])()
    if "address".startswith(obj) or obj == "a":
        out = []
        for i, (name, mac, adr, state) in enumerate(ifs, 1):
            if kurz:
                out.append(f"{name:<16} {state if name != 'lo' else 'UNKNOWN':<14} {adr}\n")
                continue
            lo = name == "lo"
            out.append(f"{i}: {name}: <{'LOOPBACK,UP,LOWER_UP' if lo else 'BROADCAST,MULTICAST,UP,LOWER_UP' if state == 'UP' else 'NO-CARRIER,BROADCAST,MULTICAST,UP'}> "
                       f"mtu {65536 if lo else 1500} qdisc {'noqueue' if lo else 'mq'} state {'UNKNOWN' if lo else state} "
                       f"group default qlen 1000\n    link/{'loopback' if lo else 'ether'} {mac} brd "
                       f"{'00:00:00:00:00:00' if lo else 'ff:ff:ff:ff:ff:ff'}\n")
            if adr:
                out.append(f"    inet {adr} {'scope host lo' if lo else 'brd ' + adr.split('/')[0].rsplit('.', 1)[0] + '.255 scope global ' + name}\n"
                           f"       valid_lft forever preferred_lft forever\n")
        ctx.out("".join(out))
        return 0
    if "link".startswith(obj) or obj == "l":
        out = []
        for i, (name, mac, adr, state) in enumerate(ifs, 1):
            lo = name == "lo"
            out.append(f"{i}: {name}: <{'LOOPBACK,UP,LOWER_UP' if lo else 'BROADCAST,MULTICAST,UP,LOWER_UP'}> mtu "
                       f"{65536 if lo else 1500} qdisc {'noqueue' if lo else 'mq'} state {'UNKNOWN' if lo else state} "
                       f"mode DEFAULT group default qlen 1000\n    link/{'loopback' if lo else 'ether'} {mac} brd "
                       f"{'00:00:00:00:00:00' if lo else 'ff:ff:ff:ff:ff:ff'}\n")
        ctx.out("".join(out))
        return 0
    if "route".startswith(obj) or obj == "r":
        for name, mac, adr, state in ifs:
            if name != "lo" and adr:
                net = adr.split("/")[0].rsplit(".", 1)[0]
                ctx.out(f"default via {net}.1 dev {name} proto static\n{net}.0/24 dev {name} proto kernel scope link src {adr.split('/')[0]}\n")
                break
        return 0
    if obj == "help":
        ctx.err("Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n")
        return 255
    ctx.err(f'Object "{obj}" is unknown, try "ip help".\n')
    return 255


@prog("ss")
def ss(ctx):
    ctx.out("Netid State  Recv-Q Send-Q Local Address:Port  Peer Address:PortProcess\n"
            "tcp   LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*\n"
            "tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*\n")
    return 0


@prog("ping")
def ping(ctx):
    args = [a for a in ctx.argv[1:]]
    anzahl = None
    ziele = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-c" and i + 1 < len(args):
            anzahl = int(args[i + 1]) if args[i + 1].isdigit() else None
            if anzahl is None:
                ctx.err(f"ping: invalid argument: '{args[i + 1]}'\n")
                return 1
            i += 2
            continue
        if a in ("-i", "-W", "-w", "-s", "-t", "-I") and i + 1 < len(args):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        ziele.append(a)
        i += 1
    if not ziele:
        ctx.err("ping: usage error: Destination address required\n")
        return 1
    z = ziele[0]
    lokal = z in ("localhost", "127.0.0.1", ctx.system.hostname, ctx.system.ip, "::1")
    if not re.fullmatch(r"[\d.]+", z) and not lokal:
        ctx.err(f"ping: {z}: Temporary failure in name resolution\n")
        return 2
    ip_ = "127.0.0.1" if z in ("localhost", ctx.system.hostname) else z
    ctx.out(f"PING {z} ({ip_}) 56(84) bytes of data.\n")
    n = 0
    t0 = time.time()
    try:
        while anzahl is None or n < anzahl:
            n += 1
            if lokal:
                ctx.out(f"64 bytes from {ip_}: icmp_seq={n} ttl=64 time=0.0{30 + n % 40} ms\n")
            if anzahl is None or n < anzahl:
                ctx.sleep(1)
    except Exception:
        ctx.out("^C\n") if False else None
        dauer = int((time.time() - t0) * 1000)
        ctx.out(f"\n--- {z} ping statistics ---\n{n} packets transmitted, {n if lokal else 0} received, "
                f"{'0' if lokal else '100'}% packet loss, time {dauer}ms\n" + (
                    "rtt min/avg/max/mdev = 0.031/0.045/0.069/0.012 ms\n" if lokal else "\n"))
        raise
    dauer = int((time.time() - t0) * 1000)
    ctx.out(f"\n--- {z} ping statistics ---\n{n} packets transmitted, {n if lokal else 0} received, "
            f"{'0' if lokal else '100'}% packet loss, time {dauer}ms\n" + (
                "rtt min/avg/max/mdev = 0.031/0.045/0.069/0.012 ms\n" if lokal else "\n"))
    return 0 if lokal else 1


@prog("curl")
def curl(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.err("curl: try 'curl --help' or 'curl --manual' for more information\n")
        return 2
    host = re.sub(r"^\w+://", "", args[0]).split("/")[0].split(":")[0]
    ctx.err(f"curl: (6) Could not resolve host: {host}\n")
    return 6


@prog("wget")
def wget(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.err("wget: missing URL\nUsage: wget [OPTION]... [URL]...\n\nTry `wget --help' for more options.\n")
        return 1
    url = args[0] if "://" in args[0] else "http://" + args[0]
    host = re.sub(r"^\w+://", "", url).split("/")[0]
    ctx.err(f"--{strftime('%Y-%m-%d %H:%M:%S', time.time())}--  {url}\nResolving {host} ({host})... failed: "
            f"Temporary failure in name resolution.\nwget: unable to resolve host address ‘{host}’\n")
    return 4


@prog("ssh")
def ssh(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.err("usage: ssh [-46AaCfGgKkMNnqsTtVvXxYy] [-B bind_interface] [-b bind_address]\n"
                "           [-c cipher_spec] [-D [bind_address:]port] [-E log_file]\n"
                "           [-e escape_char] [-F configfile] [-I pkcs11] [-i identity_file]\n"
                "           [-J destination] [-L address] [-l login_name] [-m mac_spec]\n"
                "           [-O ctl_cmd] [-o option] [-p port] [-Q query_option] [-R address]\n"
                "           [-S ctl_path] [-W host:port] [-w local_tun[:remote_tun]]\n"
                "           destination [command [argument ...]]\n")
        return 255
    host = args[0].split("@")[-1]
    ctx.sleep(3)
    ctx.err(f"ssh: connect to host {host} port 22: No route to host\n" if re.fullmatch(r"[\d.]+", host) else
            f"ssh: Could not resolve hostname {host}: Temporary failure in name resolution\n")
    return 255


@prog("scp")
def scp(ctx):
    ctx.err("usage: scp [-346ABCOpqRrsTv] [-c cipher] [-D sftp_server_path] [-F ssh_config]\n"
            "           [-i identity_file] [-J destination] [-l limit] [-o ssh_option]\n"
            "           [-P port] [-S program] [-X sftp_option] source ... target\n")
    return 1


@prog("hostnamectl")
def hostnamectl(ctx):
    h = ctx.system.hooks.get("hostnamectl")
    if h:
        ctx.out(h())
    return 0


@prog("timedatectl")
def timedatectl(ctx):
    t = time.time()
    ctx.out(f"               Local time: {strftime('%a %Y-%m-%d %H:%M:%S UTC', t)}\n"
            f"           Universal time: {strftime('%a %Y-%m-%d %H:%M:%S UTC', t)}\n"
            f"                 RTC time: {strftime('%a %Y-%m-%d %H:%M:%S', t)}\n"
            "                Time zone: Etc/UTC (UTC, +0000)\nSystem clock synchronized: yes\n"
            "              NTP service: active\n          RTC in local TZ: no\n")
    return 0


# ---------------------------------------------------------------------------- Pakete
@prog("apt", "apt-get")
def apt(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.out("apt 2.7.14 (amd64)\nUsage: apt [options] command\n\napt is a commandline package manager and "
                "provides commands for\nsearching and managing as well as querying information about packages.\n")
        return 0
    cmd = args[0]
    if cmd == "update":
        for i, src in enumerate(["http://archive.ubuntu.com/ubuntu noble InRelease",
                                 "http://security.ubuntu.com/ubuntu noble-security InRelease",
                                 "http://archive.ubuntu.com/ubuntu noble-updates InRelease"], 1):
            ctx.sleep(1.2)
            host = src.split("/")[2]
            ctx.out(f"Ign:{i} {src}\n")
        ctx.sleep(1)
        for i, src in enumerate(["http://archive.ubuntu.com/ubuntu noble InRelease",
                                 "http://security.ubuntu.com/ubuntu noble-security InRelease"], 1):
            ctx.out(f"Err:{i} {src}\n  Temporary failure resolving '{src.split('/')[2]}'\n")
        ctx.out("Reading package lists... Done\n")
        ctx.err("W: Failed to fetch http://archive.ubuntu.com/ubuntu/dists/noble/InRelease  Temporary failure "
                "resolving 'archive.ubuntu.com'\nW: Some index files failed to download. They have been ignored, "
                "or old ones used instead.\n")
        return 0
    if cmd in ("install", "upgrade", "full-upgrade", "dist-upgrade"):
        ctx.out("Reading package lists... Done\nBuilding dependency tree... Done\nReading state information... Done\n")
        pakete = args[1:]
        if cmd == "install" and pakete:
            ctx.err(f"E: Unable to locate package {pakete[0]}\n")
            return 100
        ctx.out("Calculating upgrade... Done\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n")
        return 0
    if cmd in ("remove", "purge"):
        ctx.err("E: Customer production system – changes not allowed in the lab.\n") if False else None
        ctx.out("Reading package lists... Done\nBuilding dependency tree... Done\n")
        ctx.err(f"E: Unable to locate package {args[1] if len(args) > 1 else ''}\n")
        return 100
    if cmd == "list":
        ctx.out("Listing... Done\n")
        return 0
    ctx.err(f"E: Invalid operation {cmd}\n")
    return 100


@prog("dpkg")
def dpkg(ctx):
    args = ctx.argv[1:]
    pakete = ctx.system.hooks.get("pakete", lambda: [])()
    if args[:1] in (["-l"], ["--list"]):
        muster = args[1] if len(args) > 1 else None
        ctx.out("Desired=Unknown/Install/Remove/Purge/Hold\n| Status=Not/Inst/Conf-files/Unpacked/halF-conf/"
                "Half-inst/trig-aWait/Trig-pend\n|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)\n"
                "||/ Name                                  Version                          Architecture Description\n"
                "+++-=====================================-================================-============-"
                "================================================\n")
        import fnmatch
        n = 0
        for name, ver, beschr in pakete:
            if muster and not fnmatch.fnmatch(name, muster):
                continue
            n += 1
            ctx.out(f"ii  {name:<37} {ver:<32} amd64        {beschr}\n")
        if muster and not n:
            ctx.err(f"dpkg-query: no packages found matching {muster}\n")
            return 1
        return 0
    if args[:1] == ["--version"]:
        ctx.out("Debian 'dpkg' package management program version 1.22.6 (amd64).\n")
        return 0
    ctx.err("dpkg: error: need an action option\n\nType dpkg --help for help about installing and deinstalling "
            "packages [*];\nUse 'apt' or 'aptitude' for user-friendly package management;\n")
    return 2


@prog("lsb_release")
def lsb_release(ctx):
    rel = ctx.fs.finde("/etc/lsb-release")
    d = dict(re.findall(r'(\w+)="?([^"\n]*)"?', rel.inhalt() if rel else ""))
    a = ctx.argv[1:]
    alle = not a or "-a" in a
    if alle or "-i" in a:
        ctx.out(f"Distributor ID:\t{d.get('DISTRIB_ID', 'Ubuntu')}\n")
    if alle or "-d" in a:
        ctx.out(f"Description:\t{d.get('DISTRIB_DESCRIPTION', '')}\n")
    if alle or "-r" in a:
        ctx.out(f"Release:\t{d.get('DISTRIB_RELEASE', '')}\n")
    if alle or "-c" in a:
        ctx.out(f"Codename:\t{d.get('DISTRIB_CODENAME', '')}\n")
    if alle and not a:
        ctx.err("No LSB modules are available.\n")
    return 0


@prog("crontab")
def crontab(ctx):
    if "-l" in ctx.argv:
        ctx.err("no crontab for root\n")
        return 1
    return 0


@prog("passwd")
def passwd(ctx):
    ctx.out("New password: ")
    ctx.tty.modus(echo=False)
    try:
        a = ctx.stdin.readline()
        ctx.out("\nRetype new password: ")
        b = ctx.stdin.readline()
    finally:
        ctx.tty.modus(echo=True)
    ctx.out("\n")
    if a != b:
        ctx.err("Sorry, passwords do not match.\npasswd: Authentication token manipulation error\n"
                "passwd: password unchanged\n")
        return 10
    ctx.out("passwd: password updated successfully\n")
    return 0


@prog("logger")
def logger(ctx):
    return 0


@prog("mesg")
def mesg(ctx):
    if len(ctx.argv) == 1:
        ctx.out("is n\n")
        return 1
    return 0


@prog("lsusb")
def lsusb(ctx):
    for z in ctx.system.hooks.get("usb", lambda: [])():
        ctx.out(z + "\n")
    return 0


# ---------------------------------------------------------------------------- Ein-/Ausschalten
@prog("reboot", "poweroff", "halt", "shutdown", verz="/usr/sbin")
def reboot(ctx):
    args = ctx.argv[1:]
    if ctx.prog == "shutdown":
        if "-c" in args:
            ctx.out("") if False else None
            return 0
        art = "reboot" if "-r" in args else "poweroff"
        wann = [a for a in args if not a.startswith("-")]
        if wann and wann[0] not in ("now", "+0"):
            ctx.out(f"Shutdown scheduled for {strftime('%a %Y-%m-%d %H:%M:%S UTC', time.time() + 60)}, use 'shutdown -c' to cancel.\n")
            return 0
    else:
        art = ctx.prog
    if "--help" in args:
        return ctx.hilfe(ctx.prog)
    return ctx.system.hooks.get("power", lambda c, a: 0)(ctx, art)


@prog("init", "telinit", verz="/usr/sbin")
def init(ctx):
    a = ctx.argv[1:]
    if a[:1] == ["6"]:
        return ctx.system.hooks.get("power", lambda c, x: 0)(ctx, "reboot")
    if a[:1] == ["0"]:
        return ctx.system.hooks.get("power", lambda c, x: 0)(ctx, "poweroff")
    ctx.err("init: required argument missing.\n") if not a else None
    return 1 if not a else 0
