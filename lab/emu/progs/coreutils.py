"""GNU coreutils 9.4 (Ubuntu 24.04) – emuliert auf dem virtuellen Dateisystem."""
import base64 as b64
import hashlib
import math
import posixpath
import re
import time

from .. import vfs
from ..vfs import FsError
from .base import Ctx, ProgFehler, Usage, prog, qa, ql, quote_name

SECHS_MONATE = 15778476


# ---------------------------------------------------------------------------- Hilfen
def standard(ctx, short, longs, zahl_option=False, permutieren=True):
    """getopt + --help/--version. -> (opts, args) oder int (Rueckgabewert)."""
    longs = dict(longs)
    longs.setdefault("help", 0)
    longs.setdefault("version", 0)
    try:
        opts, args = ctx.getopt(short, longs, zahl_option=zahl_option, permutieren=permutieren)
    except Usage as u:
        return ctx.usage_fehler(u.msg)
    for o, _ in opts:
        if o == "--help":
            return ctx.hilfe()
        if o == "--version":
            return ctx.version()
    return opts, args


def hat(opts, *namen):
    return any(o in namen for o, _ in opts)


def wert(opts, *namen, std=None):
    w = std
    for o, v in opts:
        if o in namen:
            w = v
    return w


def human(n, si=False, ceil=True):
    basis = 1000 if si else 1024
    if n < basis:
        return str(int(n))
    einheiten = "kMGTPE" if si else "KMGTPE"
    v = float(n)
    for e in einheiten:
        v /= basis
        if v < 10:
            x = math.ceil(v * 10 - 1e-9) / 10 if ceil else round(v, 1)
            if x < 10:
                return f"{x:.1f}{e}"
            v = x
        if v < basis - 0.5 or e == einheiten[-1]:
            x = math.ceil(v - 1e-9) if ceil else round(v)
            if x >= basis and e != einheiten[-1]:
                continue
            return f"{int(x)}{e}"
    return str(n)


def mode_str(n):
    t = {"-": "-", "d": "d", "l": "l", "c": "c", "b": "b", "p": "p", "s": "s"}[n.typ]
    m = n.mode
    s = ""
    for wer, (r, w, x), sp, ch in ((0, (0o400, 0o200, 0o100), 0o4000, "s"), (1, (0o40, 0o20, 0o10), 0o2000, "s"),
                                   (2, (0o4, 0o2, 0o1), 0o1000, "t")):
        s += "r" if m & r else "-"
        s += "w" if m & w else "-"
        if m & sp:
            s += ch if m & x else ch.upper()
        else:
            s += "x" if m & x else "-"
    return t + s


def zeit_ls(t, jetzt=None, stil=None):
    jetzt = jetzt or time.time()
    g = time.gmtime(t)
    if stil == "full-iso":
        return time.strftime("%Y-%m-%d %H:%M:%S", g) + f".{int((t % 1) * 1e9):09d} +0000"
    if stil == "long-iso":
        return time.strftime("%Y-%m-%d %H:%M", g)
    if stil == "iso":
        return time.strftime("%m-%d %H:%M", g) if jetzt - SECHS_MONATE < t <= jetzt + 3600 else time.strftime("%Y-%m-%d ", g)
    if stil and stil.startswith("+"):
        return strftime(stil[1:], t)
    tag = f"{g.tm_mday:2d}"
    mon = time.strftime("%b", g)
    if jetzt - SECHS_MONATE < t <= jetzt + 3600:
        return f"{mon} {tag} {time.strftime('%H:%M', g)}"
    return f"{mon} {tag}  {g.tm_year}"


def strftime(fmt, t, utc=True):
    g = time.gmtime(t)
    ns = int((t % 1) * 1e9)

    def ersetze(m):
        f = m.group(1)
        k = m.group(2)
        if k == "N":
            return f"{ns:09d}"
        if k == "s":
            return str(int(t))
        if k == "Z":
            return "UTC"
        if k == "z":
            return "+0000"
        if k == "P":
            return time.strftime("%p", g).lower()
        if k == "e":
            r = f"{g.tm_mday:2d}"
            return r.strip() if f == "-" else r
        if k == "k":
            return f"{g.tm_hour:2d}"
        if k == "l":
            return f"{(g.tm_hour % 12) or 12:2d}"
        r = time.strftime("%" + k, g)
        if f == "-":
            r = r.lstrip("0") or "0"
        return r
    return re.sub(r"%([-_0^#]?)([a-zA-Z%+])", lambda m: "%" if m.group(2) == "%" else ersetze(m), fmt)


def user_name(ctx, uid):
    return ctx.system.user_name(uid)


def group_name(ctx, gid):
    return ctx.system.group_name(gid)


def lesbar(ctx, name, prog=None):
    """Datei lesen mit GNU-Meldung. -> text oder None (Fehler gemeldet)."""
    try:
        return ctx.lesen(name)
    except FsError as e:
        ctx.err(f"{prog or ctx.prog}: {quote_name(name)}: {e.msg}\n")
        return None


def zeilen_von(text):
    if not text:
        return []
    z = text.split("\n")
    if text.endswith("\n"):
        z = z[:-1]
        return [x + "\n" for x in z]
    return [x + "\n" for x in z[:-1]] + [z[-1]]


# ---------------------------------------------------------------------------- ls
LS_LONGS = {"all": 0, "almost-all": 0, "author": 0, "escape": 0, "block-size": 1, "ignore-backups": 0, "color": 2,
            "directory": 0, "dired": 0, "classify": 2, "file-type": 0, "format": 1, "full-time": 0,
            "group-directories-first": 0, "no-group": 0, "human-readable": 0, "si": 0, "dereference-command-line": 0,
            "dereference-command-line-symlink-to-dir": 0, "hide": 1, "hyperlink": 2, "indicator-style": 1, "inode": 0,
            "ignore": 1, "kibibytes": 0, "dereference": 0, "numeric-uid-gid": 0, "literal": 0, "hide-control-chars": 0,
            "show-control-chars": 0, "quote-name": 0, "quoting-style": 1, "reverse": 0, "recursive": 0, "size": 0,
            "sort": 1, "time": 1, "time-style": 1, "tabsize": 1, "width": 1, "context": 0, "zero": 0}

SICHER = re.compile(r"[A-Za-z0-9_./+:,@%^=-]+")


def ls_quote(name, stil):
    if stil == "literal":
        return name
    if stil == "escape":
        return re.sub(r"([ \\])", r"\\\1", name)
    if stil == "c":
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if SICHER.fullmatch(name) and not name.startswith("~") or name == "":
        return name
    if any(ord(c) < 32 for c in name):
        teile = []
        for c in name:
            if ord(c) < 32:
                teile.append("'$'\\" + {"\n": "n", "\t": "t", "\r": "r"}.get(c, f"{ord(c):03o}") + "''")
            else:
                teile.append(c)
        return "'" + "".join(teile) + "'"
    if "'" in name:
        if '"' not in name and "$" not in name and "`" not in name and "\\" not in name:
            return '"' + name + '"'
        return "'" + name.replace("'", "'\\''") + "'"
    return "'" + name + "'"


def ls_colors(ctx):
    roh = ctx.env.get("LS_COLORS")
    farben = {"rs": "0", "di": "01;34", "ln": "01;36", "pi": "40;33", "so": "01;35", "bd": "40;33;01", "cd": "40;33;01",
              "or": "", "ex": "01;32", "su": "37;41", "sg": "30;43", "tw": "30;42", "ow": "34;42", "st": "37;44"}
    ext = {}
    if roh is not None:
        farben = {"rs": "0"}
        for teil in roh.split(":"):
            if "=" not in teil:
                continue
            k, v = teil.split("=", 1)
            if k.startswith("*"):
                ext[k[1:]] = v
            else:
                farben[k] = v
    return farben, ext


def ls_farbe(ctx, n, name, farben, ext, ziel=None):
    if n is None:
        return farben.get("mi") or farben.get("or")
    t = n.typ
    if t == "d":
        if n.mode & 0o1000 and n.mode & 0o002:
            return farben.get("tw")
        if n.mode & 0o002:
            return farben.get("ow")
        if n.mode & 0o1000:
            return farben.get("st")
        return farben.get("di")
    if t == "l":
        if ziel is None and farben.get("or"):
            return farben.get("or")
        return farben.get("ln")
    if t == "p":
        return farben.get("pi")
    if t == "s":
        return farben.get("so")
    if t == "b":
        return farben.get("bd")
    if t == "c":
        return farben.get("cd")
    if n.mode & 0o4000:
        return farben.get("su")
    if n.mode & 0o2000:
        return farben.get("sg")
    if n.mode & 0o111:
        return farben.get("ex")
    low = name.lower()
    best = None
    for e, f in ext.items():
        if low.endswith(e.lower()) and (best is None or len(e) > len(best[0])):
            best = (e, f)
    if best:
        return best[1]
    return farben.get("fi") or None


class Eintrag:
    __slots__ = ("name", "pfad", "node", "ziel", "ziel_node", "anzeige", "farbe", "ind")

    def __init__(self, name, pfad, node):
        self.name, self.pfad, self.node = name, pfad, node
        self.ziel = self.ziel_node = None
        self.anzeige = name
        self.farbe = None
        self.ind = ""


@prog("ls", "dir", "vdir")
def ls(ctx: Ctx):
    r = standard(ctx, "aAbBcCdDfFgGhHiI:klLmnNopqQrRsStT:uUvw:xXZ1", LS_LONGS)
    if isinstance(r, int):
        return r
    opts, args = r
    tty = ctx.isatty(1)
    o = {"all": False, "almost": False, "lang": False, "format": "C" if tty else "1", "sort": "name", "rev": False,
         "rec": False, "dir": False, "ind": None, "inode": False, "num": False, "human": False, "si": False,
         "size": False, "zeit": "mtime", "stil": None, "farbe": False, "gruppe": True, "owner": True,
         "quote": "shell-escape" if tty else "literal", "deref": False, "gdf": False, "ignore": [], "hide": [],
         "breite": None, "tab": 8, "kib": False}
    if ctx.prog == "dir":
        o["format"], o["quote"] = "C", "escape"
    if ctx.prog == "vdir":
        o["format"], o["quote"] = "l", "escape"
    for k, v in opts:
        if k in ("-a", "--all"):
            o["all"], o["almost"] = True, False
        elif k in ("-A", "--almost-all"):
            o["almost"] = True
            o["all"] = False
        elif k in ("-l",):
            o["format"] = "l"
        elif k == "-g":
            o["format"], o["owner"] = "l", False
        elif k == "-o":
            o["format"], o["gruppe"] = "l", False
        elif k in ("-G", "--no-group"):
            o["gruppe"] = False
        elif k in ("-n", "--numeric-uid-gid"):
            o["format"], o["num"] = "l", True
        elif k == "-1":
            o["format"] = "1"
        elif k == "-C":
            o["format"] = "C"
        elif k == "-x":
            o["format"] = "x"
        elif k == "-m":
            o["format"] = "m"
        elif k == "--format":
            m = {"long": "l", "verbose": "l", "single-column": "1", "vertical": "C", "across": "x", "horizontal": "x",
                 "commas": "m"}
            if v not in m:
                return _ungueltig(ctx, "--format", v, list(m))
            o["format"] = m[v]
        elif k in ("-h", "--human-readable"):
            o["human"] = True
        elif k == "--si":
            o["human"] = o["si"] = True
        elif k in ("-r", "--reverse"):
            o["rev"] = True
        elif k in ("-R", "--recursive"):
            o["rec"] = True
        elif k in ("-d", "--directory"):
            o["dir"] = True
        elif k in ("-F", "--classify"):
            if v in (None, "always", "yes", "force") or v in ("auto", "tty", "if-tty") and tty:
                o["ind"] = "classify"
        elif k == "--file-type":
            o["ind"] = "file-type"
        elif k == "-p":
            o["ind"] = "slash"
        elif k == "--indicator-style":
            o["ind"] = None if v == "none" else v
        elif k in ("-i", "--inode"):
            o["inode"] = True
        elif k in ("-s", "--size"):
            o["size"] = True
        elif k == "-S":
            o["sort"] = "size"
        elif k == "-t":
            o["sort"] = "time"
        elif k == "-X":
            o["sort"] = "ext"
        elif k == "-v":
            o["sort"] = "version"
        elif k == "-U":
            o["sort"] = "none"
        elif k == "-f":
            o["sort"], o["all"], o["farbe"] = "none", True, False
        elif k == "--sort":
            m = {"none": "none", "size": "size", "time": "time", "extension": "ext", "version": "version",
                 "name": "name", "width": "width"}
            if v not in m:
                return _ungueltig(ctx, "--sort", v, list(m))
            o["sort"] = m[v]
        elif k == "-c":
            o["zeit"] = "ctime"
        elif k == "-u":
            o["zeit"] = "atime"
        elif k == "--time":
            o["zeit"] = {"atime": "atime", "access": "atime", "use": "atime", "ctime": "ctime", "status": "ctime",
                         "mtime": "mtime", "modification": "mtime", "birth": "mtime", "creation": "mtime"}.get(v, "mtime")
        elif k == "--full-time":
            o["format"], o["stil"] = "l", "full-iso"
        elif k == "--time-style":
            o["stil"] = v.removeprefix("posix-")
        elif k == "--color":
            o["farbe"] = v in (None, "always", "yes", "force") or (v in ("auto", "tty", "if-tty") and tty)
        elif k in ("-N", "--literal"):
            o["quote"] = "literal"
        elif k in ("-Q", "--quote-name"):
            o["quote"] = "c"
        elif k in ("-b", "--escape"):
            o["quote"] = "escape"
        elif k == "--quoting-style":
            o["quote"] = v
        elif k in ("-L", "--dereference"):
            o["deref"] = True
        elif k == "--group-directories-first":
            o["gdf"] = True
        elif k in ("-I", "--ignore"):
            o["ignore"].append(v)
        elif k == "--hide":
            o["hide"].append(v)
        elif k in ("-B", "--ignore-backups"):
            o["ignore"] += ["*~", ".*~"]
        elif k in ("-w", "--width"):
            o["breite"] = int(v) if v and v.isdigit() else None
        elif k in ("-T", "--tabsize"):
            o["tab"] = int(v) if v and v.isdigit() else 8
        elif k in ("-k", "--kibibytes"):
            o["kib"] = True
    if o["format"] == "l" and o["stil"] is None and False:
        pass
    breite = o["breite"] or ctx.cols
    farben, ext = ls_colors(ctx) if o["farbe"] else ({}, {})
    o["farben"], o["ext"] = farben, ext
    rc = 0
    if not args:
        args = ["."]
    dateien, ordner = [], []
    for a in args:
        try:
            n_l = ctx.fs.aufloesen(a, ctx.cwd, folgen=False)[0]
        except FsError as e:
            ctx.err(f"ls: cannot access {qa(a)}: {e.msg}\n")
            rc = 2
            continue
        n = n_l
        if n_l.typ == "l" and (o["deref"] or (not o["dir"] and o["format"] != "l" and o["ind"] != "classify")
                               or (o["format"] == "l" and a.endswith("/"))):
            try:
                n = ctx.fs.aufloesen(a, ctx.cwd)[0]
            except FsError:
                n = n_l
        e = Eintrag(a, a, n)
        if n.typ == "d" and not o["dir"]:
            ordner.append(e)
        else:
            dateien.append(e)
    _ls_sortieren(dateien, o)
    _ls_sortieren(ordner, o)
    erster = True
    st = {"farbe_gesetzt": False}
    if dateien:
        _ls_ausgabe(ctx, dateien, o, breite, st, ohne_total=True)
        erster = False
    mehrere = len(args) > 1 or o["rec"]
    stapel = list(ordner)
    while stapel:
        d = stapel.pop(0)
        ctx.check()
        if not erster:
            ctx.out("\n")
        if mehrere:
            ctx.out(ls_quote(d.pfad, o["quote"]) + ":\n")
        erster = False
        kinder = []
        try:
            node = d.node
            eintr = node.eintraege()
        except FsError:
            continue
        namen = list(eintr.keys())
        if o["all"]:
            namen = [".", ".."] + namen
        for nm in namen:
            if nm.startswith(".") and not (o["all"] or o["almost"]) or (o["almost"] and nm in (".", "..")):
                continue
            from ..muster import passt
            if any(passt(p, nm) for p in o["ignore"]) or (not (o["all"] or o["almost"]) and any(passt(p, nm) for p in o["hide"])):
                continue
            if nm == ".":
                kn = node
            elif nm == "..":
                kn = getattr(node, "parent", node) or node
            else:
                kn = eintr[nm]
            pfad = d.pfad.rstrip("/") + "/" + nm if d.pfad != "/" else "/" + nm
            ke = Eintrag(nm, pfad, kn)
            if o["deref"] and kn.typ == "l":
                ziel = ctx.fs.finde(pfad, ctx.cwd)
                if ziel is not None:
                    ke.node = ziel
            kinder.append(ke)
        _ls_sortieren(kinder, o)
        _ls_ausgabe(ctx, kinder, o, breite, st)
        if o["rec"]:
            unter = [k for k in kinder if k.node.typ == "d" and k.name not in (".", "..")]
            stapel = unter + stapel
    return rc


def _ungueltig(ctx, opt, v, gueltig):
    ctx.err(f"{ctx.prog}: invalid argument {quote_name(v)} for '{opt}'\nValid arguments are:\n" +
            "".join(f"  - '{g}'\n" for g in gueltig) + f"Usage: {ctx.prog} [OPTION]... [FILE]...\n"
            f"Try '{ctx.prog} --help' for more information.\n")
    return 2


def _versions_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _ls_sortieren(liste, o):
    zeit = o["zeit"]
    if o["sort"] == "none":
        if o["rev"]:
            liste.reverse()
        return
    key_name = lambda e: e.name.encode("utf-8", "surrogateescape")  # noqa: E731
    if o["sort"] == "name":
        liste.sort(key=key_name)
    elif o["sort"] == "size":
        liste.sort(key=key_name)
        liste.sort(key=lambda e: -e.node.size())
    elif o["sort"] == "time":
        liste.sort(key=key_name)
        liste.sort(key=lambda e: -getattr(e.node, zeit))
    elif o["sort"] == "ext":
        liste.sort(key=lambda e: (posixpath.splitext(e.name)[1] if not e.name.startswith(".") or e.name.count(".") > 1
                                  else "", e.name))
    elif o["sort"] == "version":
        liste.sort(key=lambda e: _versions_key(e.name))
    if o["rev"]:
        liste.reverse()
    if o["gdf"]:
        liste.sort(key=lambda e: 0 if e.node.typ == "d" else 1)


def _ls_indikator(n, stil):
    if not stil:
        return ""
    if n.typ == "d":
        return "/"
    if stil == "slash":
        return ""
    if n.typ == "l":
        return "@"
    if n.typ == "p":
        return "|"
    if n.typ == "s":
        return "="
    if stil == "classify" and n.typ == "-" and n.mode & 0o111:
        return "*"
    return ""


def _ls_name(ctx, e, o, st, lang=False):
    """Anzeige: Farbe + Name + Indikator."""
    q = ls_quote(e.name, o["quote"])
    farbe = None
    if o["farbe"]:
        ziel = None
        if e.node.typ == "l":
            ziel = ctx.fs.finde(e.pfad, ctx.cwd)
        farbe = ls_farbe(ctx, e.node, e.name, o["farben"], o["ext"], ziel)
    ind = ""
    if not (lang and e.node.typ == "l"):
        ind = _ls_indikator(e.node, o["ind"])
    if farbe and farbe not in ("0", "00"):
        pre = ""
        if not st["farbe_gesetzt"]:
            pre = "\x1b[0m"
            st["farbe_gesetzt"] = True
        text = f"{pre}\x1b[{farbe}m{q}\x1b[0m{ind}"
    else:
        text = q + ind
    return text, len(q) + len(ind)


def _ls_ausgabe(ctx, liste, o, breite, st, ohne_total=False):
    if o["format"] == "l":
        return _ls_lang(ctx, liste, o, st, ohne_total)
    quoted = o["quote"] == "shell-escape" and any(ls_quote(e.name, o["quote"]) != e.name and
                                                   ls_quote(e.name, o["quote"])[:1] in "'\"" for e in liste)
    teile = []
    for e in liste:
        t, ln = _ls_name(ctx, e, o, st)
        if o["inode"]:
            t, ln = f"{e.node.ino} " + t, ln + len(str(e.node.ino)) + 1
        if o["size"]:
            b = _bloecke(e.node)
            s = human(b * 1024, o["si"]) if o["human"] else str(b)
            t, ln = s + " " + t, ln + len(s) + 1
        if quoted and o["format"] in ("C", "x") and not ls_quote(e.name, o["quote"])[:1] in "'\"":
            t, ln = " " + t, ln + 1
        teile.append((t, ln))
    if o["size"] and not ohne_total and liste:
        ges = sum(_bloecke(e.node) for e in liste)
        ctx.out(f"total {human(ges * 1024, o['si']) if o['human'] else ges}\n")
    if not teile:
        return
    if o["format"] == "1":
        ctx.out("".join(t + "\n" for t, _ in teile))
        return
    if o["format"] == "m":
        zeile, pos = [], 0
        out = []
        for i, (t, ln) in enumerate(teile):
            sep = ", " if i < len(teile) - 1 else ""
            if pos and pos + ln + len(sep) >= breite:
                out.append("".join(zeile).rstrip() + "\n")
                zeile, pos = [], 0
            zeile.append(t + sep)
            pos += ln + len(sep)
        out.append("".join(zeile) + "\n")
        ctx.out("".join(out))
        return
    tab = 0 if o["farbe"] else o["tab"]
    n = len(teile)
    laengen = [ln for _, ln in teile]
    max_cols = max(1, min(n, breite // 3))
    beste = 1
    beste_breiten = [max(laengen)]
    for ncols in range(1, max_cols + 1):
        if o["format"] == "C":
            nrows = math.ceil(n / ncols)
            if math.ceil(n / nrows) != ncols and ncols > 1:
                continue
            spalten = [0] * ncols
            for i, ln in enumerate(laengen):
                c = i // nrows
                spalten[c] = max(spalten[c], ln + (2 if c != ncols - 1 else 0))
        else:
            spalten = [0] * ncols
            for i, ln in enumerate(laengen):
                c = i % ncols
                spalten[c] = max(spalten[c], ln + (2 if c != ncols - 1 else 0))
        if sum(spalten) < breite:
            beste, beste_breiten = ncols, spalten
    ncols = beste
    nrows = math.ceil(n / ncols)
    out = []
    for r in range(nrows):
        pos = 0
        zeile = []
        idxs = [r + c * nrows for c in range(ncols)] if o["format"] == "C" else [r * ncols + c for c in range(ncols)]
        idxs = [i for i in idxs if i < n]
        for k, i in enumerate(idxs):
            t, ln = teile[i]
            zeile.append(t)
            if k < len(idxs) - 1:
                ziel = pos + beste_breiten[k if o["format"] == "C" else k]
                von = pos + ln
                zeile.append(_einruecken(von, ziel, tab))
                pos = ziel
        out.append("".join(zeile) + "\n")
    ctx.out("".join(out))


def _einruecken(von, bis, tab):
    s = []
    while von < bis:
        if tab and bis // tab > (von + 1) // tab:
            s.append("\t")
            von += tab - von % tab
        else:
            s.append(" ")
            von += 1
    return "".join(s)


def _bloecke(n):
    if n.typ == "d":
        return 4
    if n.typ in "lcbps":
        return 0
    s = n.size()
    return math.ceil(s / 4096) * 4 if s else 0


def _ls_lang(ctx, liste, o, st, ohne_total):
    if not ohne_total:
        ges = sum(_bloecke(e.node) for e in liste)
        ctx.out(f"total {human(ges * 1024, o['si']) if o['human'] else ges}\n")
    if not liste:
        return
    jetzt = time.time()
    zeilen = []
    for e in liste:
        n = e.node
        user = str(n.uid) if o["num"] else user_name(ctx, n.uid)
        grp = str(n.gid) if o["num"] else group_name(ctx, n.gid)
        if n.typ in "cb":
            groesse = (str(n.rdev[0]) + ",", str(n.rdev[1]))
        else:
            groesse = human(n.size(), o["si"]) if o["human"] else str(n.size())
        zeilen.append((e, mode_str(n), str(n.nlink()), user, grp, groesse,
                       zeit_ls(getattr(n, o["zeit"]), jetzt, o["stil"])))
    w_link = max(len(z[2]) for z in zeilen)
    w_user = max(len(z[3]) for z in zeilen)
    w_grp = max(len(z[4]) for z in zeilen)
    geraete = [z[5] for z in zeilen if isinstance(z[5], tuple)]
    w_maj = max((len(g[0]) for g in geraete), default=0)
    w_min = max((len(g[1]) for g in geraete), default=0)
    w_size = max([len(z[5]) for z in zeilen if not isinstance(z[5], tuple)] + ([w_maj + 1 + w_min] if geraete else []))
    w_ino = max(len(str(z[0].node.ino)) for z in zeilen)
    quoted = o["quote"] == "shell-escape" and any(ls_quote(e.name, o["quote"])[:1] in "'\"" and
                                                   ls_quote(e.name, o["quote"]) != e.name for e in liste)
    out = []
    for e, mode, nl, user, grp, groesse, zeit in zeilen:
        teile = []
        if o["inode"]:
            teile.append(f"{e.node.ino:>{w_ino}} ")
        if o["size"]:
            b = _bloecke(e.node)
            teile.append(f"{human(b * 1024) if o['human'] else b} ")
        teile.append(f"{mode} {nl:>{w_link}} ")
        if o["owner"]:
            teile.append(f"{user:<{w_user}} ")
        if o["gruppe"]:
            teile.append(f"{grp:<{w_grp}} ")
        if isinstance(groesse, tuple):
            g = f"{groesse[0]:>{w_maj + 1}} {groesse[1]:>{w_min}}"
            teile.append(f"{g:>{w_size}} ")
        else:
            teile.append(f"{groesse:>{w_size}} ")
        teile.append(zeit + " ")
        name, _ = _ls_name(ctx, e, o, st, lang=True)
        if quoted and ls_quote(e.name, o["quote"])[:1] not in "'\"":
            name = " " + name
        teile.append(name)
        if e.node.typ == "l":
            ziel = e.node.ziel
            zq = ls_quote(ziel, o["quote"])
            if o["farbe"]:
                zn = ctx.fs.finde(e.pfad, ctx.cwd)
                f = ls_farbe(ctx, zn, ziel, o["farben"], o["ext"], zn) if zn is not None else o["farben"].get("or")
                if zn is not None and zn.typ == "l":
                    f = o["farben"].get("ln")
                zq = f"\x1b[{f}m{zq}\x1b[0m" if f and f not in ("0", "00") else zq
            ind = ""
            zn = ctx.fs.finde(e.pfad, ctx.cwd)
            if zn is not None and o["ind"]:
                ind = _ls_indikator(zn, o["ind"])
            teile.append(f" -> {zq}{ind}")
        out.append("".join(teile) + "\n")
    ctx.out("".join(out))


# ---------------------------------------------------------------------------- cat & Co.
@prog("cat")
def cat(ctx):
    r = standard(ctx, "AbeEnstTuv", {"show-all": 0, "number-nonblank": 0, "show-ends": 0, "number": 0,
                                       "squeeze-blank": 0, "show-tabs": 0, "show-nonprinting": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    ends = hat(opts, "-A", "-e", "-E", "--show-all", "--show-ends")
    tabs = hat(opts, "-A", "-t", "-T", "--show-all", "--show-tabs")
    nonp = hat(opts, "-A", "-e", "-t", "-v", "--show-all", "--show-nonprinting")
    nb = hat(opts, "-b", "--number-nonblank")
    num = hat(opts, "-n", "--number") or nb
    squeeze = hat(opts, "-s", "--squeeze-blank")
    rc = 0
    nr = 0
    leer_vorher = False
    einfach = not (ends or tabs or nonp or num or squeeze)
    for a in args or ["-"]:
        ctx.check()
        if a == "-" and ctx.stdin.isatty and einfach:
            # interaktiv: zeilenweise durchreichen
            while True:
                z = ctx.stdin.readline()
                if z == "":
                    break
                ctx.out(z)
            continue
        try:
            text = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"cat: {quote_name(a)}: {e.msg}\n")
            rc = 1
            continue
        if einfach:
            ctx.out(text)
            continue
        out = []
        for z in zeilen_von(text):
            inhalt = z[:-1] if z.endswith("\n") else z
            nl = z.endswith("\n")
            leer = inhalt == ""
            if squeeze and leer and leer_vorher:
                continue
            leer_vorher = leer
            if nonp:
                inhalt = "".join(_nonprint(c, tabs) for c in inhalt)
            elif tabs:
                inhalt = inhalt.replace("\t", "^I")
            pre = ""
            if num and not (nb and leer):
                nr += 1
                pre = f"{nr:6d}\t"
            out.append(pre + inhalt + ("$" if ends and nl else "") + ("\n" if nl else ""))
        ctx.out("".join(out))
    return rc


def _nonprint(c, tabs):
    o = ord(c)
    if c == "\t":
        return "^I" if tabs else c
    if o < 32:
        return "^" + chr(o + 64)
    if o == 127:
        return "^?"
    if 128 <= o < 160:
        return "M-^" + chr(o - 128 + 64)
    return c


@prog("tac")
def tac(ctx):
    r = standard(ctx, "brs:", {"before": 0, "regex": 0, "separator": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    rc = 0
    for a in args or ["-"]:
        try:
            t = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"tac: failed to open {qa(a)} for reading: {e.msg}\n")
            rc = 1
            continue
        z = zeilen_von(t)
        if z and not z[-1].endswith("\n"):
            z[-1] = z[-1]
            ctx.out(z[-1] + "".join(reversed(z[:-1])))
        else:
            ctx.out("".join(reversed(z)))
    return rc


def _zahl_arg(ctx, v, was):
    m = re.fullmatch(r"([-+]?)(\d+)([bkKMGTPEZY]?B?|[kKMGT]iB)?", v or "")
    if not m:
        raise Usage(None)
    n = int(m.group(2))
    mult = {"": 1, None: 1, "b": 512, "k": 1024, "K": 1024, "KB": 1000, "kB": 1000, "M": 1024 ** 2, "MB": 1000 ** 2,
            "G": 1024 ** 3, "GB": 1000 ** 3, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}.get(m.group(3), 1)
    return m.group(1), n * mult


@prog("head")
def head(ctx):
    r = standard(ctx, "c:n:qvz", {"bytes": 1, "lines": 1, "quiet": 0, "silent": 0, "verbose": 0, "zero-terminated": 0},
                 zahl_option=True)
    if isinstance(r, int):
        return r
    opts, args = r
    modus, n, neg = "n", 10, False
    kopf = None
    for k, v in opts:
        if k in ("-n", "--lines", "-NUM"):
            try:
                vz, n = _zahl_arg(ctx, v, "lines")
            except Usage:
                ctx.err(f"head: invalid number of lines: {ql(v)}\n")
                return 1
            modus, neg = "n", vz == "-"
        elif k in ("-c", "--bytes"):
            try:
                vz, n = _zahl_arg(ctx, v, "bytes")
            except Usage:
                ctx.err(f"head: invalid number of bytes: {ql(v)}\n")
                return 1
            modus, neg = "c", vz == "-"
        elif k in ("-q", "--quiet", "--silent"):
            kopf = False
        elif k in ("-v", "--verbose"):
            kopf = True
    args = args or ["-"]
    if kopf is None:
        kopf = len(args) > 1
    rc = 0
    erster = True
    for a in args:
        ctx.check()
        if a == "-" and modus == "n" and not neg:
            if kopf:
                ctx.out(("" if erster else "\n") + "==> standard input <==\n")
            erster = False
            for _ in range(n):
                z = ctx.stdin.readline()
                if z == "":
                    break
                ctx.out(z)
            continue
        try:
            text = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"head: cannot open {qa(a)} for reading: {e.msg}\n")
            rc = 1
            continue
        if kopf:
            ctx.out(("" if erster else "\n") + f"==> {'standard input' if a == '-' else a} <==\n")
        erster = False
        if modus == "c":
            b = text.encode("utf-8", "surrogateescape")
            b = b[:-n] if neg and n else (b"" if neg else b[:n]) if not neg else b
            if neg and n == 0:
                b = text.encode("utf-8", "surrogateescape")
            ctx.out(b.decode("utf-8", "replace"))
        else:
            z = zeilen_von(text)
            ctx.out("".join(z[:-n] if neg and n else (z if neg else z[:n])))
    return rc


@prog("tail")
def tail(ctx):
    roh = ctx.argv[1:]
    # obsolete Syntax: tail +5 / tail -5f
    if roh and re.fullmatch(r"\+\d+[lcf]?", roh[0]):
        roh = ["-n", roh[0]] + roh[1:]
    r = standard(ctx, "c:fFn:qs:vz", {"bytes": 1, "follow": 2, "lines": 1, "pid": 1, "quiet": 0, "silent": 0,
                                       "retry": 0, "sleep-interval": 1, "verbose": 0, "zero-terminated": 0,
                                       "max-unchanged-stats": 1}, zahl_option=True)
    if isinstance(r, int):
        return r
    opts, args = r
    modus, n, ab = "n", 10, False
    folgen = False
    kopf = None
    for k, v in opts:
        if k in ("-n", "--lines", "-NUM"):
            m = re.fullmatch(r"([-+]?)(\d+)", v or "")
            if not m:
                ctx.err(f"tail: invalid number of lines: {ql(v)}\n")
                return 1
            modus, n, ab = "n", int(m.group(2)), m.group(1) == "+"
        elif k in ("-c", "--bytes"):
            m = re.fullmatch(r"([-+]?)(\d+)", v or "")
            if not m:
                ctx.err(f"tail: invalid number of bytes: {ql(v)}\n")
                return 1
            modus, n, ab = "c", int(m.group(2)), m.group(1) == "+"
        elif k in ("-f", "--follow", "-F"):
            folgen = True
        elif k in ("-q", "--quiet", "--silent"):
            kopf = False
        elif k in ("-v", "--verbose"):
            kopf = True
    args = args or ["-"]
    if kopf is None:
        kopf = len(args) > 1
    rc = 0
    erster = True
    offen = []
    for a in args:
        ctx.check()
        try:
            text = ctx.lesen(a)
            node = None if a == "-" else ctx.node(a)
        except FsError as e:
            ctx.err(f"tail: cannot open {qa(a)} for reading: {e.msg}\n")
            rc = 1
            continue
        if kopf:
            ctx.out(("" if erster else "\n") + f"==> {'standard input' if a == '-' else a} <==\n")
        erster = False
        if modus == "c":
            ctx.out(text[n - 1:] if ab else (text[-n:] if n else ""))
        else:
            z = zeilen_von(text)
            ctx.out("".join(z[max(n - 1, 0):] if ab else (z[-n:] if n else [])))
        if node is not None:
            offen.append((a, node, len(text)))
    if folgen:
        if not offen:
            ctx.err("tail: no files remaining\n")
            return 1
        letzte = offen[-1][0]
        while True:
            ctx.sleep(1)
            for i, (a, node, pos) in enumerate(offen):
                t = node.inhalt()
                if len(t) > pos:
                    if len(offen) > 1 and a != letzte:
                        ctx.out(f"\n==> {a} <==\n")
                        letzte = a
                    ctx.out(t[pos:])
                    offen[i] = (a, node, len(t))
                elif len(t) < pos:
                    ctx.err(f"tail: {a}: file truncated\n")
                    offen[i] = (a, node, 0)
    return rc


@prog("wc")
def wc(ctx):
    r = standard(ctx, "clLmw", {"bytes": 0, "chars": 0, "lines": 0, "max-line-length": 0, "words": 0,
                                  "files0-from": 1, "total": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    w = []
    if hat(opts, "-l", "--lines"):
        w.append("l")
    if hat(opts, "-w", "--words"):
        w.append("w")
    if hat(opts, "-m", "--chars"):
        w.append("m")
    if hat(opts, "-c", "--bytes"):
        w.append("c")
    if hat(opts, "-L", "--max-line-length"):
        w.append("L")
    if not w:
        w = ["l", "w", "c"]
    reihen = ["l", "w", "m", "c", "L"]
    w = [x for x in reihen if x in w]
    ergebnisse = []
    rc = 0
    nur_regulaer = True
    groessen = 0
    for a in args or ["-"]:
        ctx.check()
        try:
            text = ctx.lesen(a)
            if a == "-":
                nur_regulaer = False
            else:
                n = ctx.node(a)
                if n.typ != "-":
                    nur_regulaer = False
                groessen += n.size()
        except FsError as e:
            ctx.err(f"wc: {quote_name(a)}: {e.msg}\n")
            rc = 1
            continue
        b = text.encode("utf-8", "surrogateescape")
        werte = {"l": text.count("\n"), "w": len(text.split()), "m": len(text), "c": len(b),
                 "L": max((len(z.expandtabs(8)) for z in text.split("\n")), default=0)}
        ergebnisse.append((werte, "" if a == "-" and not args else a))
    if not ergebnisse:
        return rc
    total = {k: sum(e[0][k] for e in ergebnisse) for k in "lwmc"}
    total["L"] = max(e[0]["L"] for e in ergebnisse)
    if len(w) == 1 and len(ergebnisse) == 1:
        breite = 1
    elif nur_regulaer:
        breite = max(len(str(groessen)), 1)
    else:
        breite = 7
    out = []
    for werte, name in ergebnisse:
        out.append(" ".join(f"{werte[k]:>{breite}}" for k in w) + (f" {name}" if name else "") + "\n")
    if len(ergebnisse) > 1:
        out.append(" ".join(f"{total[k]:>{breite}}" for k in w) + " total\n")
    ctx.out("".join(out))
    return rc


# ---------------------------------------------------------------------------- Dateien anlegen/loeschen
@prog("touch")
def touch(ctx):
    r = standard(ctx, "acd:fhmr:t:", {"no-create": 0, "date": 1, "reference": 1, "no-dereference": 0, "time": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing file operand")
    nicht_anlegen = hat(opts, "-c", "--no-create")
    t = time.time()
    rc = 0
    for a in args:
        n = ctx.fs.finde(a, ctx.cwd)
        if n is None:
            if nicht_anlegen:
                continue
            try:
                ctx.fs.schreiben(a, "", ctx.cwd, mode=0o644)
            except FsError as e:
                ctx.err(f"touch: cannot touch {qa(a)}: {e.msg}\n")
                rc = 1
            continue
        n.mtime = n.atime = n.ctime = t
    return rc


@prog("mkdir")
def mkdir(ctx):
    r = standard(ctx, "m:pvZ", {"mode": 1, "parents": 0, "verbose": 0, "context": 2})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    eltern = hat(opts, "-p", "--parents")
    verbose = hat(opts, "-v", "--verbose")
    mode = 0o755
    mv = wert(opts, "-m", "--mode")
    if mv:
        try:
            mode = chmod_wert(mv, 0o777 & ~0o022, True)
        except ValueError:
            ctx.err(f"mkdir: invalid mode {quote_name(mv)}\n")
            return 1
    rc = 0
    for a in args:
        if eltern:
            p = ctx.pfad(a)
            teile = [t for t in p.split("/") if t]
            akt = ""
            for t in teile:
                akt += "/" + t
                n = ctx.fs.finde(akt)
                if n is None:
                    try:
                        ctx.fs.anlegen(akt, vfs.ordner(mode))
                        if verbose:
                            ctx.out(f"mkdir: created directory {qa(akt if a.startswith('/') else _rel(a, akt, ctx))}\n")
                    except FsError as e:
                        ctx.err(f"mkdir: cannot create directory {ql(a)}: {e.msg}\n")
                        rc = 1
                        break
                elif n.typ != "d":
                    ctx.err(f"mkdir: cannot create directory {ql(a)}: Not a directory\n")
                    rc = 1
                    break
            continue
        try:
            ctx.fs.anlegen(a, vfs.ordner(mode), ctx.cwd)
            if verbose:
                ctx.out(f"mkdir: created directory {qa(a)}\n")
        except FsError as e:
            ctx.err(f"mkdir: cannot create directory {ql(a)}: {e.msg}\n")
            rc = 1
    return rc


def _rel(original, absolut, ctx):
    if original.startswith("/"):
        return absolut
    basis = ctx.cwd.rstrip("/") + "/"
    return absolut[len(basis):] if absolut.startswith(basis) else absolut


@prog("rmdir")
def rmdir(ctx):
    r = standard(ctx, "pv", {"ignore-fail-on-non-empty": 0, "parents": 0, "verbose": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    rc = 0
    ignor = hat(opts, "--ignore-fail-on-non-empty")
    for a in args:
        ziele = [a]
        if hat(opts, "-p", "--parents"):
            p = a.rstrip("/")
            while "/" in p:
                p = p.rsplit("/", 1)[0]
                if p:
                    ziele.append(p)
        for z in ziele:
            try:
                n = ctx.node(z, folgen=False)
                if n.typ != "d":
                    raise FsError("ENOTDIR")
                if n.eintraege():
                    raise FsError("ENOTEMPTY")
                ctx.fs.entfernen(z, ctx.cwd)
                if hat(opts, "-v", "--verbose"):
                    ctx.out(f"rmdir: removing directory, {qa(z)}\n")
            except FsError as e:
                if e.code == "ENOTEMPTY" and ignor:
                    break
                ctx.err(f"rmdir: failed to remove {qa(z)}: {e.msg}\n")
                rc = 1
                break
    return rc


@prog("rm")
def rm(ctx):
    r = standard(ctx, "dfiIrRv", {"force": 0, "interactive": 2, "one-file-system": 0, "no-preserve-root": 0,
                                    "preserve-root": 2, "recursive": 0, "dir": 0, "verbose": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    force = rek = verbose = dir_ok = inter = False
    for k, v in opts:
        if k in ("-f", "--force"):
            force, inter = True, False
        elif k in ("-r", "-R", "--recursive"):
            rek = True
        elif k in ("-v", "--verbose"):
            verbose = True
        elif k in ("-d", "--dir"):
            dir_ok = True
        elif k in ("-i",) or k == "--interactive" and v in (None, "always"):
            inter, force = True, False
    if not args:
        if force:
            return 0
        return ctx.usage_fehler("missing operand")
    rc = 0
    for a in args:
        ctx.check()
        basis = a.rstrip("/").split("/")[-1]
        if basis in (".", ".."):
            ctx.err(f"rm: refusing to remove '.' or '..' directory: skipping {qa(a)}\n")
            rc = 1
            continue
        if ctx.fs.abs(a, ctx.cwd) == "/":
            ctx.err("rm: it is dangerous to operate recursively on '/'\n"
                    "rm: use --no-preserve-root to override this failsafe\n")
            rc = 1
            continue
        try:
            n = ctx.node(a, folgen=False)
        except FsError as e:
            if not force:
                ctx.err(f"rm: cannot remove {qa(a)}: {e.msg}\n")
                rc = 1
            continue
        if n.typ == "d":
            if not rek:
                if dir_ok and not n.eintraege():
                    pass
                else:
                    ctx.err(f"rm: cannot remove {qa(a)}: Is a directory\n")
                    rc = 1
                    continue
            if inter and not _frage(ctx, f"rm: remove directory {qa(a)}? "):
                continue
            if rek:
                rc |= _rm_rek(ctx, a, n, verbose)
                continue
        elif inter:
            art = "regular empty file" if n.typ == "-" and n.size() == 0 else (
                "regular file" if n.typ == "-" else "symbolic link" if n.typ == "l" else "file")
            if not _frage(ctx, f"rm: remove {art} {qa(a)}? "):
                continue
        try:
            ctx.fs.entfernen(a, ctx.cwd)
            if verbose:
                ctx.out(f"removed {qa(a)}\n")
        except FsError as e:
            ctx.err(f"rm: cannot remove {qa(a)}: {e.msg}\n")
            rc = 1
    return rc


def _rm_rek(ctx, pfad, n, verbose):
    rc = 0
    for name, kind in list(n.kinder.items()):
        ctx.check()
        p = pfad.rstrip("/") + "/" + name
        if kind.typ == "d":
            rc |= _rm_rek(ctx, p, kind, verbose)
        else:
            try:
                ctx.fs.entfernen(p, ctx.cwd)
                if verbose:
                    ctx.out(f"removed {qa(p)}\n")
            except FsError as e:
                ctx.err(f"rm: cannot remove {qa(p)}: {e.msg}\n")
                rc = 1
    if n.gen is not None:
        ctx.err(f"rm: cannot remove {qa(pfad)}: Operation not permitted\n")
        return 1
    try:
        ctx.fs.entfernen(pfad, ctx.cwd)
        if verbose:
            ctx.out(f"removed directory {qa(pfad)}\n")
    except FsError as e:
        ctx.err(f"rm: cannot remove {qa(pfad)}: {e.msg}\n")
        rc = 1
    return rc


def _frage(ctx, text):
    ctx.err(text)
    antwort = ctx.stdin.readline()
    return antwort.strip().lower().startswith("y")


def _kopie(node, t=None, attribute=False):
    n = vfs.Node(node.typ, node.mode if attribute or node.typ != "-" else node.mode, t=t)
    if node.typ == "-":
        n.data = node.inhalt() if callable(node.data) else node.data
        n.groesse = node.groesse
    elif node.typ == "l":
        n.ziel = node.ziel
    elif node.typ in "cb":
        n.rdev = node.rdev
    if attribute:
        n.mtime, n.atime, n.uid, n.gid = node.mtime, node.atime, node.uid, node.gid
    return n


@prog("cp")
def cp(ctx):
    r = standard(ctx, "abdfHilLnprRsStTuvx", {"archive": 0, "attributes-only": 0, "backup": 2, "copy-contents": 0,
                                               "force": 0, "interactive": 0, "link": 0, "dereference": 0,
                                               "no-clobber": 0, "no-dereference": 0, "preserve": 2, "no-preserve": 1,
                                               "parents": 0, "recursive": 0, "remove-destination": 0, "reflink": 2,
                                               "sparse": 1, "strip-trailing-slashes": 0, "symbolic-link": 0,
                                               "suffix": 1, "target-directory": 1, "no-target-directory": 0,
                                               "update": 2, "verbose": 0, "one-file-system": 0, "debug": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    rek = hat(opts, "-r", "-R", "--recursive", "-a", "--archive")
    attr = hat(opts, "-p", "-a", "--archive", "--preserve")
    verbose = hat(opts, "-v", "--verbose")
    noclob = hat(opts, "-n", "--no-clobber")
    inter = hat(opts, "-i", "--interactive")
    ziel_d = wert(opts, "-t", "--target-directory")
    if ziel_d:
        args = args + [ziel_d]
    if not args:
        return ctx.usage_fehler("missing file operand")
    if len(args) == 1:
        return ctx.usage_fehler(f"missing destination file operand after {qa(args[0])}")
    quellen, ziel = args[:-1], args[-1]
    zn = ctx.fs.finde(ziel, ctx.cwd)
    ist_dir = zn is not None and zn.typ == "d"
    if len(quellen) > 1 and not ist_dir:
        ctx.err(f"cp: target {qa(ziel)} is not a directory\n")
        return 1
    rc = 0
    for q in quellen:
        ctx.check()
        try:
            qn = ctx.node(q)
        except FsError as e:
            ctx.err(f"cp: cannot stat {qa(q)}: {e.msg}\n")
            rc = 1
            continue
        dest = (ziel.rstrip("/") + "/" + posixpath.basename(q.rstrip("/"))) if ist_dir else ziel
        if qn.typ == "d" and not rek:
            ctx.err(f"cp: -r not specified; omitting directory {qa(q)}\n")
            rc = 1
            continue
        rc |= _cp_eins(ctx, q, qn, dest, rek, attr, verbose, noclob, inter)
    return rc


def _cp_eins(ctx, q, qn, dest, rek, attr, verbose, noclob, inter):
    try:
        if ctx.fs.abs(q, ctx.cwd) == ctx.fs.abs(dest, ctx.cwd):
            ctx.err(f"cp: {qa(q)} and {qa(dest)} are the same file\n")
            return 1
        dn = ctx.fs.finde(dest, ctx.cwd)
        if qn.typ == "d":
            q_abs, d_abs = ctx.fs.abs(q, ctx.cwd), ctx.fs.abs(dest, ctx.cwd)
            if d_abs.startswith(q_abs.rstrip("/") + "/"):
                ctx.err(f"cp: cannot copy a directory, {qa(q)}, into itself, {qa(dest)}\n")
                return 1
            if dn is None:
                neu = vfs.ordner(qn.mode)
                if attr:
                    neu.mtime = qn.mtime
                ctx.fs.anlegen(dest, neu, ctx.cwd)
                if verbose:
                    ctx.out(f"{qa(q)} -> {qa(dest)}\n")
            elif dn.typ != "d":
                ctx.err(f"cp: cannot overwrite non-directory {qa(dest)} with directory {qa(q)}\n")
                return 1
            rc = 0
            for name, kind in list(qn.eintraege().items()):
                rc |= _cp_eins(ctx, q.rstrip("/") + "/" + name, kind, dest.rstrip("/") + "/" + name, rek, attr,
                               verbose, noclob, inter)
            return rc
        if dn is not None:
            if dn.typ == "d":
                ctx.err(f"cp: cannot overwrite directory {qa(dest)} with non-directory\n")
                return 1
            if noclob:
                return 0
            if inter and not _frage(ctx, f"cp: overwrite {qa(dest)}? "):
                return 0
            dn.data = qn.inhalt()
            dn.groesse = qn.groesse
            dn.mtime = dn.ctime = time.time()
            if attr:
                dn.mode, dn.mtime = qn.mode, qn.mtime
        else:
            neu = _kopie(qn, attribute=attr)
            if not attr and qn.typ == "-":
                neu.mode = qn.mode & 0o777
            ctx.fs.anlegen(dest, neu, ctx.cwd)
        if verbose:
            ctx.out(f"{qa(q)} -> {qa(dest)}\n")
        return 0
    except FsError as e:
        ctx.err(f"cp: cannot create regular file {qa(dest)}: {e.msg}\n")
        return 1


@prog("mv")
def mv(ctx):
    r = standard(ctx, "bfinS:t:Tuv", {"backup": 2, "force": 0, "interactive": 0, "no-clobber": 0,
                                       "strip-trailing-slashes": 0, "suffix": 1, "target-directory": 1,
                                       "no-target-directory": 0, "update": 2, "verbose": 0, "exchange": 0,
                                       "no-copy": 0, "debug": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    verbose = hat(opts, "-v", "--verbose")
    noclob = hat(opts, "-n", "--no-clobber")
    inter = hat(opts, "-i", "--interactive")
    ziel_d = wert(opts, "-t", "--target-directory")
    if ziel_d:
        args = args + [ziel_d]
    if not args:
        return ctx.usage_fehler("missing file operand")
    if len(args) == 1:
        return ctx.usage_fehler(f"missing destination file operand after {qa(args[0])}")
    quellen, ziel = args[:-1], args[-1]
    zn = ctx.fs.finde(ziel, ctx.cwd)
    ist_dir = zn is not None and zn.typ == "d"
    if len(quellen) > 1 and not ist_dir:
        ctx.err(f"mv: target {qa(ziel)} is not a directory\n")
        return 1
    rc = 0
    for q in quellen:
        try:
            qn = ctx.node(q, folgen=False)
        except FsError as e:
            ctx.err(f"mv: cannot stat {qa(q)}: {e.msg}\n")
            rc = 1
            continue
        dest = (ziel.rstrip("/") + "/" + posixpath.basename(q.rstrip("/"))) if ist_dir else ziel
        q_abs, d_abs = ctx.fs.abs(q, ctx.cwd), ctx.fs.abs(dest, ctx.cwd)
        if q_abs == d_abs:
            ctx.err(f"mv: {qa(q)} and {qa(dest)} are the same file\n")
            rc = 1
            continue
        if qn.typ == "d" and d_abs.startswith(q_abs.rstrip("/") + "/"):
            ctx.err(f"mv: cannot move {qa(q)} to a subdirectory of itself, {qa(dest)}\n")
            rc = 1
            continue
        dn = ctx.fs.finde(dest, ctx.cwd, folgen=False)
        if dn is not None:
            if noclob:
                continue
            if dn.typ == "d" and qn.typ != "d":
                ctx.err(f"mv: cannot overwrite directory {qa(dest)} with non-directory\n")
                rc = 1
                continue
            if dn.typ == "d" and dn.eintraege():
                ctx.err(f"mv: cannot move {qa(q)} to {qa(dest)}: Directory not empty\n")
                rc = 1
                continue
            if inter and not _frage(ctx, f"mv: overwrite {qa(dest)}? "):
                continue
        try:
            par, name, _ = ctx.fs.eltern(q, ctx.cwd)
            if par is not None and name not in par.kinder:
                raise FsError("EBUSY")
            ctx.fs.eltern(dest, ctx.cwd)
            n = ctx.fs.entfernen(q, ctx.cwd)
            ctx.fs.anlegen(dest, n, ctx.cwd, ersetzen=True)
            n.ctime = time.time()
            if verbose:
                ctx.out(f"renamed {qa(q)} -> {qa(dest)}\n")
        except FsError as e:
            ctx.err(f"mv: cannot move {qa(q)} to {qa(dest)}: {e.msg}\n")
            rc = 1
    return rc


@prog("ln")
def ln(ctx):
    r = standard(ctx, "bdfFiLnPrsS:t:Tv", {"backup": 2, "directory": 0, "force": 0, "interactive": 0, "logical": 0,
                                            "no-dereference": 0, "physical": 0, "relative": 0, "symbolic": 0,
                                            "suffix": 1, "target-directory": 1, "no-target-directory": 0,
                                            "verbose": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    sym = hat(opts, "-s", "--symbolic")
    force = hat(opts, "-f", "--force")
    verbose = hat(opts, "-v", "--verbose")
    if not args:
        return ctx.usage_fehler("missing file operand")
    if len(args) == 1:
        args = args + [posixpath.basename(args[0].rstrip("/"))]
    quellen, ziel = args[:-1], args[-1]
    zn = ctx.fs.finde(ziel, ctx.cwd)
    ist_dir = zn is not None and zn.typ == "d" and not hat(opts, "-n", "--no-dereference", "-T")
    rc = 0
    for q in quellen:
        dest = (ziel.rstrip("/") + "/" + posixpath.basename(q.rstrip("/"))) if ist_dir else ziel
        try:
            if ctx.fs.finde(dest, ctx.cwd, folgen=False) is not None:
                if not force:
                    ctx.err(f"ln: failed to create {'symbolic ' if sym else 'hard '}link {qa(dest)}: File exists\n")
                    rc = 1
                    continue
                ctx.fs.entfernen(dest, ctx.cwd)
            if sym:
                ctx.fs.anlegen(dest, vfs.link(q), ctx.cwd)
            else:
                qn = ctx.node(q)
                if qn.typ == "d":
                    ctx.err(f"ln: {qa(q)}: hard link not allowed for directory\n")
                    rc = 1
                    continue
                par, name, _ = ctx.fs.eltern(dest, ctx.cwd)
                par.kinder[name] = qn
            if verbose:
                ctx.out(f"{qa(dest)} -> {qa(q)}\n")
        except FsError as e:
            if not sym:
                ctx.err(f"ln: failed to access {qa(q)}: {e.msg}\n")
            else:
                ctx.err(f"ln: failed to create symbolic link {qa(dest)}: {e.msg}\n")
            rc = 1
    return rc


def chmod_wert(spec, alt, ist_dir=False, umask=0o022):
    if re.fullmatch(r"[0-7]{1,4}", spec):
        v = int(spec, 8)
        if len(spec) <= 3 and ist_dir:
            v |= alt & 0o6000
        return v
    mode = alt
    for teil in spec.split(","):
        m = re.fullmatch(r"([ugoa]*)([-+=][rwxXstugo]*)+", teil)
        if not m:
            raise ValueError(spec)
        wer = m.group(1) or "a"
        maske_umask = not m.group(1)
        rest = teil[len(m.group(1)):]
        for op, rechte in re.findall(r"([-+=])([rwxXstugo]*)", rest):
            bits = 0
            for r_ in rechte:
                if r_ in "ugo":
                    src = {"u": (mode >> 6) & 7, "g": (mode >> 3) & 7, "o": mode & 7}[r_]
                    for w in wer.replace("a", "ugo"):
                        bits |= src << {"u": 6, "g": 3, "o": 0}[w]
                    continue
                for w in wer.replace("a", "ugo"):
                    sh = {"u": 6, "g": 3, "o": 0}[w]
                    if r_ == "r":
                        bits |= 4 << sh
                    elif r_ == "w":
                        bits |= 2 << sh
                    elif r_ == "x" or r_ == "X" and (ist_dir or mode & 0o111):
                        bits |= 1 << sh
                    elif r_ == "s" and w in "ug":
                        bits |= 0o4000 if w == "u" else 0o2000
                    elif r_ == "t" and w == "o":
                        bits |= 0o1000
            if maske_umask and op != "=":
                bits &= ~umask | 0o7000
            if op == "+":
                mode |= bits
            elif op == "-":
                mode &= ~bits
            else:
                clear = 0
                for w in wer.replace("a", "ugo"):
                    clear |= 7 << {"u": 6, "g": 3, "o": 0}[w]
                    clear |= {"u": 0o4000, "g": 0o2000, "o": 0o1000}[w]
                mode = (mode & ~clear) | bits
    return mode


@prog("chmod")
def chmod(ctx):
    # chmod erlaubt -x/-w als Modus
    roh = ctx.argv[1:]
    modus_idx = None
    neue = []
    for i, a in enumerate(roh):
        if modus_idx is None and re.fullmatch(r"-[rwxXst]+", a):
            modus_idx = len(neue)
            neue.append("\0" + a)
        else:
            neue.append(a)
    try:
        opts, args = ctx.getopt("cfvR", {"changes": 0, "silent": 0, "quiet": 0, "verbose": 0, "recursive": 0,
                                          "reference": 1, "preserve-root": 0, "no-preserve-root": 0, "help": 0,
                                          "version": 0}, argv=neue)
    except Usage as u:
        return ctx.usage_fehler(u.msg)
    if hat(opts, "--help"):
        return ctx.hilfe()
    if hat(opts, "--version"):
        return ctx.version()
    args = [a[1:] if a.startswith("\0") else a for a in args]
    if not args:
        return ctx.usage_fehler("missing operand")
    if len(args) == 1:
        return ctx.usage_fehler(f"missing operand after {ql(args[0])}")
    spec, dateien = args[0], args[1:]
    rek = hat(opts, "-R", "--recursive")
    verbose = hat(opts, "-v", "--verbose")
    changes = hat(opts, "-c", "--changes")
    still = hat(opts, "-f", "--silent", "--quiet")
    rc = 0

    def eins(p, n):
        nonlocal rc
        if n.typ == "l":
            return
        try:
            neu = chmod_wert(spec, n.mode & 0o7777, n.typ == "d")
        except ValueError:
            raise
        alt = n.mode & 0o7777
        n.mode = (n.mode & ~0o7777) | neu
        n.ctime = time.time()
        if verbose or changes and alt != neu:
            if alt != neu:
                ctx.out(f"mode of {qa(p)} changed from {alt:04o} ({mode_str(_fake(n.typ, alt))[1:]}) "
                        f"to {neu:04o} ({mode_str(_fake(n.typ, neu))[1:]})\n")
            else:
                ctx.out(f"mode of {qa(p)} retained as {neu:04o} ({mode_str(_fake(n.typ, neu))[1:]})\n")
        if rek and n.typ == "d":
            for name, k in list(n.eintraege().items()):
                eins(p.rstrip("/") + "/" + name, k)

    for d in dateien:
        try:
            n = ctx.node(d)
        except FsError as e:
            if not still:
                ctx.err(f"chmod: cannot access {qa(d)}: {e.msg}\n")
            rc = 1
            continue
        try:
            eins(d, n)
        except ValueError:
            ctx.err(f"chmod: invalid mode: {ql(spec)}\nTry 'chmod --help' for more information.\n")
            return 1
    return rc


def _fake(typ, mode):
    n = vfs.Node(typ if typ != "l" else "-", mode)
    return n


@prog("chown", "chgrp")
def chown(ctx):
    r = standard(ctx, "cfhvRHLP", {"changes": 0, "silent": 0, "quiet": 0, "verbose": 0, "recursive": 0,
                                    "dereference": 0, "no-dereference": 0, "from": 1, "reference": 1,
                                    "preserve-root": 0, "no-preserve-root": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    if len(args) == 1:
        return ctx.usage_fehler(f"missing operand after {ql(args[0])}")
    spec, dateien = args[0], args[1:]
    if ctx.prog == "chgrp":
        user, grp = None, spec
    else:
        user, _, grp = spec.replace(".", ":").partition(":") if ":" in spec or "." in spec else (spec, "", None)
        grp = grp or None
    uid = gid = None
    if user:
        uid = ctx.system.uid_von(user)
        if uid is None:
            ctx.err(f"{ctx.prog}: invalid user: {quote_name(spec)}\n")
            return 1
    if grp:
        gid = ctx.system.gid_von(grp)
        if gid is None:
            ctx.err(f"{ctx.prog}: invalid group: {quote_name(spec)}\n")
            return 1
    rc = 0
    rek = hat(opts, "-R", "--recursive")

    def eins(n):
        if uid is not None:
            n.uid = uid
        if gid is not None:
            n.gid = gid
        if rek and n.typ == "d":
            for k in n.eintraege().values():
                eins(k)
    for d in dateien:
        try:
            eins(ctx.node(d))
        except FsError as e:
            ctx.err(f"{ctx.prog}: cannot access {qa(d)}: {e.msg}\n")
            rc = 1
    return rc


# ---------------------------------------------------------------------------- Pfade
@prog("basename")
def basename(ctx):
    r = standard(ctx, "as:z", {"multiple": 0, "suffix": 1, "zero": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    suffix = wert(opts, "-s", "--suffix")
    mehrere = hat(opts, "-a", "--multiple") or suffix is not None
    if not mehrere:
        if len(args) > 2:
            return ctx.usage_fehler(f"extra operand {ql(args[2])}")
        suffix = args[1] if len(args) == 2 else None
        args = args[:1]
    for a in args:
        b = a.rstrip("/") or ("/" if a.startswith("/") else "")
        b = b.rsplit("/", 1)[-1] if b != "/" else "/"
        if suffix and b.endswith(suffix) and b != suffix:
            b = b[:-len(suffix)]
        ctx.out(b + "\n")
    return 0


@prog("dirname")
def dirname(ctx):
    r = standard(ctx, "z", {"zero": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    for a in args:
        p = a.rstrip("/")
        if not p:
            ctx.out("/\n" if a.startswith("/") else ".\n")
            continue
        if "/" not in p:
            ctx.out(".\n")
            continue
        d = p.rsplit("/", 1)[0].rstrip("/")
        ctx.out((d or "/") + "\n")
    return 0


@prog("readlink")
def readlink(ctx):
    r = standard(ctx, "efmnqsvz", {"canonicalize": 0, "canonicalize-existing": 0, "canonicalize-missing": 0,
                                    "no-newline": 0, "quiet": 0, "silent": 0, "verbose": 0, "zero": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    kanon = hat(opts, "-f", "-e", "-m", "--canonicalize", "--canonicalize-existing", "--canonicalize-missing")
    nl = "" if hat(opts, "-n", "--no-newline") else "\n"
    rc = 0
    for a in args:
        try:
            if kanon:
                _, p = ctx.fs.aufloesen(a, ctx.cwd)
                ctx.out(p + nl)
            else:
                n = ctx.node(a, folgen=False)
                if n.typ != "l":
                    rc = 1
                    continue
                ctx.out(n.ziel + nl)
        except FsError as e:
            if hat(opts, "-m", "--canonicalize-missing"):
                ctx.out(ctx.pfad(a) + nl)
                continue
            if hat(opts, "-v", "--verbose"):
                ctx.err(f"readlink: {a}: {e.msg}\n")
            rc = 1
    return rc


@prog("realpath")
def realpath(ctx):
    r = standard(ctx, "eLmPqsz", {"canonicalize-existing": 0, "canonicalize-missing": 0, "logical": 0,
                                   "physical": 0, "quiet": 0, "relative-to": 1, "relative-base": 1, "strip": 0,
                                   "no-symlinks": 0, "zero": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    rc = 0
    for a in args:
        try:
            if hat(opts, "-s", "--strip", "--no-symlinks"):
                ctx.out(ctx.pfad(a) + "\n")
                continue
            _, p = ctx.fs.aufloesen(a, ctx.cwd)
            ctx.out(p + "\n")
        except FsError as e:
            par = ctx.fs.finde(posixpath.dirname(ctx.pfad(a)) or "/")
            if e.code == "ENOENT" and par is not None and not hat(opts, "-e"):
                ctx.out(ctx.pfad(a) + "\n")
                continue
            ctx.err(f"realpath: {quote_name(a)}: {e.msg}\n")
            rc = 1
    return rc


@prog("stat")
def stat(ctx):
    r = standard(ctx, "c:fLt", {"dereference": 0, "file-system": 0, "format": 1, "printf": 1, "terse": 0,
                                 "cached": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    fmt = wert(opts, "-c", "--format")
    pfmt = wert(opts, "--printf")
    deref = hat(opts, "-L", "--dereference")
    rc = 0
    for a in args:
        try:
            n = ctx.node(a, folgen=deref)
        except FsError as e:
            ctx.err(f"stat: cannot statx {qa(a)}: {e.msg}\n")
            rc = 1
            continue
        if hat(opts, "-f", "--file-system"):
            ctx.out(f"  File: \"{a}\"\n    ID: 8f2b3a1c00000000 Namelen: 255     Type: ext2/ext3\n"
                    "Block size: 4096       Fundamental block size: 4096\n"
                    "Blocks: Total: 230006534  Free: 201345221  Available: 189621339\n"
                    "Inodes: Total: 58556416   Free: 58123456\n")
            continue
        if fmt is not None or pfmt is not None:
            ctx.out(_stat_format(ctx, pfmt if pfmt is not None else fmt, a, n) + ("" if pfmt is not None else "\n"))
            continue
        if hat(opts, "-t", "--terse"):
            ctx.out(f"{a} {n.size()} {_bloecke(n) * 2} {n.mode | _typbits(n):x} {n.uid} {n.gid} fd00 {n.ino} "
                    f"{n.nlink()} 0 0 {int(n.atime)} {int(n.mtime)} {int(n.ctime)} {int(n.ctime)} 4096\n")
            continue
        art = {"-": "regular file" if n.size() else "regular empty file", "d": "directory", "l": "symbolic link",
               "c": "character special file", "b": "block special file", "p": "fifo", "s": "socket"}[n.typ]
        name = f"{a} -> {n.ziel}" if n.typ == "l" else a
        geraet = "Device: 0,5\t" if a.startswith("/dev") else ("Device: 0,22\t" if a.startswith("/proc") else "Device: 253,0\t")
        geraetetyp = f" Device type: {n.rdev[0]},{n.rdev[1]}" if n.typ in "cb" else ""
        if n.typ in "cb":
            zeile2 = f"  Size: {n.size():<10}\tBlocks: {_bloecke(n) * 2:<10} IO Block: 4096   {art}\n"
        else:
            zeile2 = f"  Size: {n.size():<10}\tBlocks: {_bloecke(n) * 2:<10} IO Block: 4096   {art}\n"
        ctx.out(f"  File: {name}\n" + zeile2 +
                f"{geraet}Inode: {n.ino:<11} Links: {n.nlink()}{geraetetyp}\n"
                f"Access: ({n.mode & 0o7777:04o}/{mode_str(n)})  Uid: ({n.uid:5d}/{user_name(ctx, n.uid):>8})   "
                f"Gid: ({n.gid:5d}/{group_name(ctx, n.gid):>8})\n"
                f"Access: {_iso_ns(n.atime)}\nModify: {_iso_ns(n.mtime)}\nChange: {_iso_ns(n.ctime)}\n"
                f" Birth: {_iso_ns(n.ctime)}\n")
    return rc


def _typbits(n):
    return {"-": 0o100000, "d": 0o40000, "l": 0o120000, "c": 0o20000, "b": 0o60000, "p": 0o10000, "s": 0o140000}[n.typ]


def _iso_ns(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1e9):09d} +0000"


def _stat_format(ctx, fmt, a, n):
    def ers(m):
        c = m.group(1)
        return {
            "n": a, "N": f"'{a}'" + (f" -> '{n.ziel}'" if n.typ == "l" else ""), "s": str(n.size()),
            "a": f"{n.mode & 0o7777:o}", "A": mode_str(n), "u": str(n.uid), "U": user_name(ctx, n.uid),
            "g": str(n.gid), "G": group_name(ctx, n.gid), "i": str(n.ino), "h": str(n.nlink()),
            "F": {"-": "regular file" if n.size() else "regular empty file", "d": "directory", "l": "symbolic link",
                  "c": "character special file", "b": "block special file", "p": "fifo", "s": "socket"}[n.typ],
            "y": _iso_ns(n.mtime), "Y": str(int(n.mtime)), "x": _iso_ns(n.atime), "X": str(int(n.atime)),
            "z": _iso_ns(n.ctime), "Z": str(int(n.ctime)), "w": _iso_ns(n.ctime), "W": str(int(n.ctime)),
            "b": str(_bloecke(n) * 2), "B": "512", "o": "4096", "f": f"{n.mode | _typbits(n):x}", "%": "%",
            "d": "64768", "D": "fd00", "m": "/", "t": "0", "T": "0",
        }.get(c, "?")
    text = re.sub(r"%(.)", ers, fmt)
    return text.replace("\\n", "\n").replace("\\t", "\t") if True else text


# ---------------------------------------------------------------------------- Diverses
@prog("tee")
def tee(ctx):
    r = standard(ctx, "aip", {"append": 0, "ignore-interrupts": 0, "output-error": 2})
    if isinstance(r, int):
        return r
    opts, args = r
    anh = hat(opts, "-a", "--append")
    ziele = []
    rc = 0
    for a in args:
        try:
            n = ctx.fs.finde(a, ctx.cwd)
            if n is None or not anh:
                n = ctx.fs.schreiben(a, "", ctx.cwd)
            ziele.append(n)
        except FsError as e:
            ctx.err(f"tee: {quote_name(a)}: {e.msg}\n")
            rc = 1
    while True:
        z = ctx.stdin.readline()
        if z == "":
            break
        ctx.out(z)
        for n in ziele:
            if n.typ == "c":
                continue
            n.data = (n.data if isinstance(n.data, str) else "") + z
            n.groesse = None
            n.mtime = time.time()
    return rc


@prog("sleep")
def sleep(ctx):
    r = standard(ctx, "", {})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        return ctx.usage_fehler("missing operand")
    ges = 0.0
    for a in args:
        m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)([smhd]?)", a)
        if not m:
            ctx.err(f"sleep: invalid time interval {ql(a)}\nTry 'sleep --help' for more information.\n")
            return 1
        ges += float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    ctx.sleep(ges)
    return 0


@prog("date")
def date(ctx):
    r = standard(ctx, "d:f:I::r:R::s:u", {"date": 1, "file": 1, "iso-8601": 2, "reference": 1, "rfc-email": 0,
                                           "rfc-3339": 1, "set": 1, "debug": 0, "universal": 0, "utc": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    t = time.time() + ctx.system.zeit_offset
    d = wert(opts, "-d", "--date")
    if d is not None:
        t2 = _datum_parse(d, t)
        if t2 is None:
            ctx.err(f"date: invalid date {ql(d)}\n")
            return 1
        t = t2
    ref = wert(opts, "-r", "--reference")
    if ref:
        try:
            t = ctx.node(ref).mtime
        except FsError as e:
            ctx.err(f"date: {quote_name(ref)}: {e.msg}\n")
            return 1
    if hat(opts, "-s", "--set"):
        ctx.err("date: cannot set date: Operation not permitted\n") if False else None
    if hat(opts, "-R", "--rfc-email"):
        ctx.out(strftime("%a, %d %b %Y %H:%M:%S +0000", t) + "\n")
        return 0
    iso = None
    for k, v in opts:
        if k in ("-I", "--iso-8601"):
            iso = v or "date"
    if iso:
        f = {"date": "%Y-%m-%d", "hours": "%Y-%m-%dT%H+00:00", "minutes": "%Y-%m-%dT%H:%M+00:00",
             "seconds": "%Y-%m-%dT%H:%M:%S+00:00", "ns": "%Y-%m-%dT%H:%M:%S,%N+00:00"}.get(iso, "%Y-%m-%d")
        ctx.out(strftime(f, t) + "\n")
        return 0
    fmt = None
    for a in args:
        if a.startswith("+"):
            if fmt is not None:
                return ctx.usage_fehler(f"extra operand {ql(a)}")
            fmt = a[1:]
        else:
            ctx.err("date: cannot set date: Operation not permitted\n")
            ctx.out(strftime("%a %b %e %H:%M:%S %Z %Y", t) + "\n")
            return 1
    ctx.out(strftime(fmt if fmt is not None else "%a %b %e %H:%M:%S %Z %Y", t) + "\n")
    return 0


def _datum_parse(s, jetzt):
    s = s.strip().lower()
    if s in ("", "now", "today"):
        return jetzt
    if s == "yesterday":
        return jetzt - 86400
    if s == "tomorrow":
        return jetzt + 86400
    m = re.fullmatch(r"@(\d+)", s)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"([-+]?\d+)\s*(second|sec|minute|min|hour|day|week|month|year)s?(\s+ago)?", s)
    if m:
        n = int(m.group(1)) * {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "day": 86400,
                               "week": 604800, "month": 2629746, "year": 31556952}[m.group(2)]
        return jetzt - n if m.group(3) else jetzt + n
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dt%H:%M:%S"):
        try:
            import calendar
            return calendar.timegm(time.strptime(s, f))
        except ValueError:
            pass
    return None


@prog("seq")
def seq(ctx):
    roh = ctx.argv[1:]
    # negative Zahlen sind keine Optionen
    neue = []
    for a in roh:
        neue.append("\0" + a if re.fullmatch(r"-\d+(\.\d+)?", a) else a)
    try:
        opts, args = ctx.getopt("f:s:w", {"format": 1, "separator": 1, "equal-width": 0, "help": 0, "version": 0},
                                argv=neue)
    except Usage as u:
        return ctx.usage_fehler(u.msg)
    if hat(opts, "--help"):
        return ctx.hilfe()
    if hat(opts, "--version"):
        return ctx.version()
    args = [a[1:] if a.startswith("\0") else a for a in args]
    if not args:
        return ctx.usage_fehler("missing operand")
    if len(args) > 3:
        return ctx.usage_fehler(f"extra operand {ql(args[3])}")
    try:
        zahlen = [float(a) if "." in a or "e" in a.lower() else int(a) for a in args]
    except ValueError:
        bad = next(a for a in args if not re.fullmatch(r"[-+]?\d+(\.\d*)?", a))
        return ctx.usage_fehler(f"invalid floating point argument: {ql(bad)}")
    if len(zahlen) == 1:
        a, s, b = 1, 1, zahlen[0]
    elif len(zahlen) == 2:
        a, s, b = zahlen[0], 1, zahlen[1]
    else:
        a, s, b = zahlen
    if s == 0:
        ctx.err(f"seq: invalid Zero increment value: {ql(args[1])}\nTry 'seq --help' for more information.\n")
        return 1
    sep = wert(opts, "-s", "--separator", std="\n")
    fmt = wert(opts, "-f", "--format")
    nachkomma = max((len(x.split(".")[1]) for x in args if "." in x), default=0)
    werte = []
    v = a
    while (s > 0 and v <= b + 1e-12) or (s < 0 and v >= b - 1e-12):
        werte.append(v)
        v = a + s * len(werte)
        if len(werte) % 1000 == 0:
            ctx.check()
        if len(werte) > 2_000_000:
            break
    if fmt:
        texte = [fmt % w for w in werte]
    elif nachkomma:
        texte = [f"{w:.{nachkomma}f}" for w in werte]
    else:
        texte = [str(int(w)) if float(w).is_integer() else str(w) for w in werte]
    if hat(opts, "-w", "--equal-width") and texte:
        b_ = max(len(t) for t in texte)
        texte = [t.zfill(b_) if not t.startswith("-") else "-" + t[1:].zfill(b_ - 1) for t in texte]
    if texte:
        # in Bloecken ausgeben, damit 'seq 1 1000000 | head' nicht ewig dauert
        for i in range(0, len(texte), 2000):
            ctx.check()
            ctx.out(sep.join(texte[i:i + 2000]) + (sep if i + 2000 < len(texte) else "\n"))
    return 0


@prog("yes")
def yes(ctx):
    r = standard(ctx, "", {})
    if isinstance(r, int):
        return r
    _, args = r
    text = (" ".join(args) if args else "y") + "\n"
    block = text * max(1, 4096 // len(text))
    while True:
        ctx.check()
        ctx.out(block)
        if ctx.isatty(1):
            time.sleep(0.01)


@prog("env")
def env(ctx):
    roh = ctx.argv[1:]
    try:
        opts, args = ctx.getopt("+0iu:C:S:v", {"null": 0, "ignore-environment": 0, "unset": 1, "chdir": 1,
                                               "split-string": 1, "debug": 0, "help": 0, "version": 0,
                                               "default-signal": 2, "ignore-signal": 2, "block-signal": 2,
                                               "list-signal-handling": 0}, argv=roh)
    except Usage as u:
        return ctx.usage_fehler(u.msg)
    if hat(opts, "--help"):
        return ctx.hilfe()
    if hat(opts, "--version"):
        return ctx.version()
    e = {} if hat(opts, "-i", "--ignore-environment") else dict(ctx.env)
    for k, v in opts:
        if k in ("-u", "--unset"):
            e.pop(v, None)
    while args and "=" in args[0] and not args[0].startswith("="):
        k, v = args.pop(0).split("=", 1)
        e[k] = v
    if not args:
        ctx.out("".join(f"{k}={v}\n" for k, v in e.items()))
        return 0
    return ctx.sh.extern(args, ctx.fds, e) if args[0] not in ("cd",) else 127


@prog("printenv")
def printenv(ctx):
    r = standard(ctx, "0", {"null": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if not args:
        ctx.out("".join(f"{k}={v}\n" for k, v in ctx.env.items()))
        return 0
    rc = 0
    for a in args:
        if a in ctx.env:
            ctx.out(ctx.env[a] + "\n")
        else:
            rc = 1
    return rc


@prog("echo")
def echo_ext(ctx):
    from ..builtins import b_echo
    from ..shell import IO
    if ctx.argv[1:] == ["--help"]:
        return ctx.hilfe()
    if ctx.argv[1:] == ["--version"]:
        return ctx.version()
    return b_echo(ctx.sh, ctx.argv, IO(ctx.fds))


@prog("printf")
def printf_ext(ctx):
    from ..builtins import printf_format
    from ..shell import IO
    if ctx.argv[1:] == ["--help"]:
        return ctx.hilfe()
    if ctx.argv[1:] == ["--version"]:
        return ctx.version()
    if len(ctx.argv) < 2:
        return ctx.usage_fehler("missing operand")
    text, rc = printf_format(ctx.sh, IO(ctx.fds), ctx.argv[1], ctx.argv[2:])
    ctx.out(text)
    return rc


@prog("true")
def true_ext(ctx):
    if ctx.argv[1:] == ["--help"]:
        return ctx.hilfe()
    if ctx.argv[1:] == ["--version"]:
        return ctx.version()
    return 0


@prog("false")
def false_ext(ctx):
    if ctx.argv[1:] == ["--help"]:
        ctx.hilfe()
    return 1


@prog("test", "[")
def test_ext(ctx):
    from ..builtins import b_test
    from ..shell import IO
    return b_test(ctx.sh, ctx.argv, IO(ctx.fds))


@prog("pwd")
def pwd_ext(ctx):
    r = standard(ctx, "LP", {"logical": 0, "physical": 0})
    if isinstance(r, int):
        return r
    try:
        _, p = ctx.fs.aufloesen(ctx.cwd)
    except FsError:
        p = ctx.cwd
    ctx.out((ctx.cwd if hat(r[0], "-L", "--logical") else p) + "\n")
    return 0


@prog("whoami")
def whoami(ctx):
    r = standard(ctx, "", {})
    if isinstance(r, int):
        return r
    if r[1]:
        return ctx.usage_fehler(f"extra operand {ql(r[1][0])}")
    ctx.out("root\n")
    return 0


@prog("id")
def id_(ctx):
    r = standard(ctx, "agGnruzZ", {"context": 0, "group": 0, "groups": 0, "name": 0, "real": 0, "user": 0, "zero": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if args and args[0] != "root":
        if ctx.system.uid_von(args[0]) is None:
            ctx.err(f"id: {quote_name(args[0])}: no such user\n")
            return 1
    name = hat(opts, "-n", "--name")
    if hat(opts, "-u", "--user"):
        ctx.out(("root" if name else "0") + "\n")
    elif hat(opts, "-g", "--group"):
        ctx.out(("root" if name else "0") + "\n")
    elif hat(opts, "-G", "--groups"):
        ctx.out(("root" if name else "0") + "\n")
    else:
        ctx.out("uid=0(root) gid=0(root) groups=0(root)\n")
    return 0


@prog("groups")
def groups(ctx):
    r = standard(ctx, "", {})
    if isinstance(r, int):
        return r
    if r[1]:
        ctx.out(f"{r[1][0]} : root\n")
    else:
        ctx.out("root\n")
    return 0


@prog("uname")
def uname(ctx):
    r = standard(ctx, "asnrvmpio", {"all": 0, "kernel-name": 0, "nodename": 0, "kernel-release": 0,
                                     "kernel-version": 0, "machine": 0, "processor": 0, "hardware-platform": 0,
                                     "operating-system": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if args:
        return ctx.usage_fehler(f"extra operand {ql(args[0])}")
    s = ctx.system
    felder = {"s": "Linux", "n": s.hostname, "r": s.kernel, "v": s.kernel_version, "m": "x86_64", "p": "x86_64",
              "i": "x86_64", "o": "GNU/Linux"}
    if hat(opts, "-a", "--all"):
        ctx.out(" ".join(felder[k] for k in "snrvmpio") + "\n")
        return 0
    lang = {"--kernel-name": "s", "--nodename": "n", "--kernel-release": "r", "--kernel-version": "v",
            "--machine": "m", "--processor": "p", "--hardware-platform": "i", "--operating-system": "o"}
    gew = set()
    for k, _ in opts:
        gew.add(lang.get(k, k[1:]))
    if not gew:
        gew = {"s"}
    ctx.out(" ".join(felder[k] for k in "snrvmpio" if k in gew) + "\n")
    return 0


@prog("hostname")
def hostname(ctx):
    args = ctx.argv[1:]
    s = ctx.system
    if not args:
        ctx.out(s.hostname + "\n")
        return 0
    a = args[0]
    if a in ("-s", "--short"):
        ctx.out(s.hostname.split(".")[0] + "\n")
    elif a in ("-f", "--fqdn", "--long"):
        ctx.out(s.hostname + "\n")
    elif a in ("-i", "--ip-address"):
        ctx.out("127.0.1.1\n")
    elif a in ("-I", "--all-ip-addresses"):
        ctx.out(s.ip + " \n")
    elif a in ("-d", "--domain"):
        ctx.out("\n")
    elif a in ("-h", "--help"):
        return ctx.hilfe()
    elif a in ("-V", "--version"):
        ctx.out("hostname 3.23+nmu2ubuntu2\n")
    elif a.startswith("-"):
        ctx.err(f"hostname: invalid option -- '{a[1:2]}'\n")
        ctx.hilfe()
        return 255
    else:
        ctx.err("hostname: you must be root to change the host name\n") if False else None
        s.hostname = a
        ctx.sh.vars["HOSTNAME"] = a
    return 0


@prog("nproc")
def nproc(ctx):
    r = standard(ctx, "", {"all": 0, "ignore": 1})
    if isinstance(r, int):
        return r
    ctx.out(f"{ctx.system.cpus}\n")
    return 0


@prog("tty")
def tty(ctx):
    r = standard(ctx, "s", {"silent": 0, "quiet": 0})
    if isinstance(r, int):
        return r
    if ctx.stdin.isatty:
        if not hat(r[0], "-s", "--silent", "--quiet"):
            ctx.out("/dev/tty1\n")
        return 0
    if not hat(r[0], "-s", "--silent", "--quiet"):
        ctx.out("not a tty\n")
    return 1


@prog("sync")
def sync(ctx):
    r = standard(ctx, "df", {"data": 0, "file-system": 0})
    return r if isinstance(r, int) else 0


@prog("mktemp")
def mktemp(ctx):
    r = standard(ctx, "dpqtu", {"directory": 0, "dry-run": 0, "quiet": 0, "suffix": 1, "tmpdir": 2})
    if isinstance(r, int):
        return r
    opts, args = r
    import random
    import string
    vorlage = args[0] if args else "tmp.XXXXXXXXXX"
    m = re.search(r"X{3,}", vorlage)
    if not m:
        ctx.err(f"mktemp: too few X's in template {quote_name(vorlage)}\n")
        return 1
    name = vorlage[:m.start()] + "".join(random.choice(string.ascii_letters + string.digits)
                                         for _ in range(m.end() - m.start())) + vorlage[m.end():]
    if not args or "/" not in name:
        name = "/tmp/" + name
    try:
        if hat(opts, "-d", "--directory"):
            ctx.fs.anlegen(name, vfs.ordner(0o700), ctx.cwd)
        else:
            ctx.fs.anlegen(name, vfs.datei("", 0o600), ctx.cwd)
    except FsError as e:
        ctx.err(f"mktemp: failed to create file via template {quote_name(vorlage)}: {e.msg}\n")
        return 1
    ctx.out(name + "\n")
    return 0


@prog("truncate")
def truncate(ctx):
    r = standard(ctx, "cor:s:", {"no-create": 0, "io-blocks": 0, "reference": 1, "size": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    s = wert(opts, "-s", "--size")
    if s is None:
        return ctx.usage_fehler("you must specify either '--size' or '--reference'")
    if not args:
        return ctx.usage_fehler("missing file operand")
    m = re.fullmatch(r"(\d+)([KMG]?)", s)
    groesse = int(m.group(1)) * {"": 1, "K": 1024, "M": 1 << 20, "G": 1 << 30}[m.group(2)] if m else 0
    for a in args:
        try:
            n = ctx.fs.finde(a, ctx.cwd) or ctx.fs.schreiben(a, "", ctx.cwd)
            d = n.inhalt()
            n.data = d[:groesse] if groesse <= len(d) else d + "\0" * (groesse - len(d))
        except FsError as e:
            ctx.err(f"truncate: cannot open {quote_name(a)} for writing: {e.msg}\n")
            return 1
    return 0


def _summen(ctx, algo):
    r = standard(ctx, "bctwz", {"binary": 0, "check": 0, "tag": 0, "text": 0, "zero": 0, "ignore-missing": 0,
                                 "quiet": 0, "status": 0, "strict": 0, "warn": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    rc = 0
    if hat(opts, "-c", "--check"):
        fehler = 0
        for a in args or ["-"]:
            t = lesbar(ctx, a)
            if t is None:
                return 1
            for z in t.splitlines():
                m = re.match(r"([0-9a-fA-F]+) [ *](.+)$", z)
                if not m:
                    continue
                try:
                    inhalt = ctx.lesen(m.group(2))
                except FsError as e:
                    ctx.err(f"{ctx.prog}: {m.group(2)}: {e.msg}\n")
                    ctx.out(f"{m.group(2)}: FAILED open or read\n")
                    fehler += 1
                    continue
                ok = hashlib.new(algo, inhalt.encode("utf-8", "surrogateescape")).hexdigest() == m.group(1).lower()
                if not ok:
                    fehler += 1
                if not (ok and hat(opts, "--quiet")):
                    ctx.out(f"{m.group(2)}: {'OK' if ok else 'FAILED'}\n")
        if fehler:
            ctx.err(f"{ctx.prog}: WARNING: {fehler} computed checksum{'s' if fehler > 1 else ''} did NOT match\n")
            return 1
        return 0
    for a in args or ["-"]:
        try:
            t = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"{ctx.prog}: {quote_name(a)}: {e.msg}\n")
            rc = 1
            continue
        h = hashlib.new(algo, t.encode("utf-8", "surrogateescape")).hexdigest()
        ctx.out(f"{h}  {a}\n")
    return rc


@prog("md5sum")
def md5sum(ctx):
    return _summen(ctx, "md5")


@prog("sha1sum")
def sha1sum(ctx):
    return _summen(ctx, "sha1")


@prog("sha256sum")
def sha256sum(ctx):
    return _summen(ctx, "sha256")


@prog("sha512sum")
def sha512sum(ctx):
    return _summen(ctx, "sha512")


@prog("base64")
def base64(ctx):
    r = standard(ctx, "diw:", {"decode": 0, "ignore-garbage": 0, "wrap": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    t = lesbar(ctx, args[0] if args else "-")
    if t is None:
        return 1
    if hat(opts, "-d", "--decode"):
        try:
            ctx.out(b64.b64decode(t.encode(), validate=False).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            ctx.err("base64: invalid input\n")
            return 1
        return 0
    enc = b64.b64encode(t.encode("utf-8", "surrogateescape")).decode()
    w = int(wert(opts, "-w", "--wrap", std="76"))
    if w:
        enc = "\n".join(enc[i:i + w] for i in range(0, len(enc), w))
    ctx.out(enc + "\n")
    return 0


@prog("timeout")
def timeout(ctx):
    roh = ctx.argv[1:]
    try:
        opts, args = ctx.getopt("+k:s:v", {"kill-after": 1, "signal": 1, "verbose": 0, "foreground": 0,
                                           "preserve-status": 0, "help": 0, "version": 0}, argv=roh)
    except Usage as u:
        return ctx.usage_fehler(u.msg, rc=125)
    if hat(opts, "--help"):
        return ctx.hilfe()
    if len(args) < 2:
        return ctx.usage_fehler("missing operand", rc=125)
    m = re.fullmatch(r"(\d+(?:\.\d*)?)([smhd]?)", args[0])
    if not m:
        ctx.err(f"timeout: invalid time interval {quote_name(args[0])}\n")
        return 125
    dauer = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    import threading
    erg = {"rc": None}
    sub = ctx.sh.subshell()

    def lauf():
        try:
            erg["rc"] = sub.extern(args[1:], ctx.fds, ctx.env)
        except Exception:  # noqa: BLE001
            erg["rc"] = 1
    abbruch = threading.Event()
    sub.check = lambda: (_ for _ in ()).throw(__import__("lab.emu.streams", fromlist=["Interrupted"]).Interrupted()) \
        if abbruch.is_set() or (ctx.tty and ctx.tty.sigint.is_set()) else None
    th = threading.Thread(target=lauf, daemon=True)
    th.start()
    ende = time.time() + dauer
    while th.is_alive() and time.time() < ende:
        ctx.check()
        th.join(0.1)
    if th.is_alive():
        abbruch.set()
        th.join(2)
        return 124
    return erg["rc"] or 0


@prog("nohup")
def nohup(ctx):
    args = ctx.argv[1:]
    if not args:
        return ctx.usage_fehler("missing operand", rc=125)
    if ctx.isatty(1):
        ctx.err("nohup: ignoring input and appending output to 'nohup.out'\n")
        n = ctx.fs.finde("nohup.out", ctx.cwd) or ctx.fs.schreiben("nohup.out", "", ctx.cwd)
        from ..streams import FileWriter
        fds = dict(ctx.fds)
        fds[1] = fds[2] = FileWriter(ctx.fs, n)
        return ctx.sh.extern(args, fds, ctx.env)
    return ctx.sh.extern(args, ctx.fds, ctx.env)


@prog("nice", "ionice", "taskset", "stdbuf")
def nice(ctx):
    args = ctx.argv[1:]
    while args and args[0].startswith("-"):
        if args[0] in ("-n", "-c", "-i", "-p") and len(args) > 1:
            args = args[2:]
        else:
            args = args[1:]
    if not args:
        ctx.out("0\n")
        return 0
    return ctx.sh.extern(args, ctx.fds, ctx.env)


@prog("expr")
def expr(ctx):
    args = ctx.argv[1:]
    if args == ["--help"]:
        return ctx.hilfe()
    if not args:
        ctx.err("expr: missing operand\nTry 'expr --help' for more information.\n")
        return 2
    try:
        v = _Expr(args).run()
    except ProgFehler as e:
        ctx.err(f"expr: {e.msg}\n")
        return 2
    except ZeroDivisionError:
        ctx.err("expr: division by zero\n")
        return 2
    ctx.out(f"{v}\n")
    return 1 if v in ("", "0", 0) else 0


class _Expr:
    def __init__(self, a):
        self.a, self.i = a, 0

    def peek(self):
        return self.a[self.i] if self.i < len(self.a) else None

    def nimm(self):
        t = self.peek()
        self.i += 1
        return t

    def run(self):
        v = self.oder()
        if self.peek() is not None:
            raise ProgFehler(f"syntax error: unexpected argument {quote_name(self.peek())}")
        return v

    def oder(self):
        v = self.und()
        while self.peek() == "|":
            self.nimm()
            r = self.und()
            v = v if v not in ("", "0", 0) else (r if r not in ("", "0", 0) else 0)
        return v

    def und(self):
        v = self.vergl()
        while self.peek() == "&":
            self.nimm()
            r = self.vergl()
            v = v if v not in ("", "0", 0) and r not in ("", "0", 0) else 0
        return v

    @staticmethod
    def _num(x):
        if isinstance(x, int):
            return x
        if re.fullmatch(r"[-+]?\d+", str(x)):
            return int(x)
        raise ProgFehler("non-integer argument")

    def vergl(self):
        v = self.add()
        while self.peek() in ("<", "<=", "=", "==", "!=", ">=", ">"):
            op = self.nimm()
            r = self.add()
            try:
                a, b = self._num(v), self._num(r)
            except ProgFehler:
                a, b = str(v), str(r)
            v = int({"<": a < b, "<=": a <= b, "=": a == b, "==": a == b, "!=": a != b, ">=": a >= b, ">": a > b}[op])
        return v

    def add(self):
        v = self.mul()
        while self.peek() in ("+", "-"):
            op = self.nimm()
            r = self.mul()
            v = self._num(v) + self._num(r) if op == "+" else self._num(v) - self._num(r)
        return v

    def mul(self):
        v = self.prim()
        while self.peek() in ("*", "/", "%"):
            op = self.nimm()
            r = self.prim()
            a, b = self._num(v), self._num(r)
            if op == "*":
                v = a * b
            elif b == 0:
                raise ZeroDivisionError
            elif op == "/":
                v = int(a / b)
            else:
                v = a - b * int(a / b)
        return v

    def prim(self):
        t = self.nimm()
        if t is None:
            raise ProgFehler("syntax error: missing argument after " + quote_name(self.a[-1]) if self.a else "syntax error")
        if t == "(":
            v = self.oder()
            if self.nimm() != ")":
                raise ProgFehler("syntax error: expecting ')'")
            return v
        if t == "length":
            return len(self.nimm() or "")
        if self.peek() == ":":
            self.nimm()
            muster = self.nimm() or ""
            m = re.match(muster.replace(r"\(", "(").replace(r"\)", ")"), t)
            if m is None:
                return 0 if "(" not in muster.replace(r"\(", "(") else ""
            return m.group(1) if m.groups() else len(m.group(0))
        return t


@prog("rev")
def rev(ctx):
    args = ctx.argv[1:]
    rc = 0
    for a in args or ["-"]:
        try:
            t = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"rev: cannot open {a}: {e.msg}\n")
            rc = 1
            continue
        ctx.out("".join(z.rstrip("\n")[::-1] + ("\n" if z.endswith("\n") else "") for z in zeilen_von(t)))
    return rc


@prog("fold")
def fold(ctx):
    r = standard(ctx, "bsw:", {"bytes": 0, "spaces": 0, "width": 1}, zahl_option=True)
    if isinstance(r, int):
        return r
    opts, args = r
    w = int(wert(opts, "-w", "--width", "-NUM", std="80"))
    for a in args or ["-"]:
        t = lesbar(ctx, a)
        if t is None:
            return 1
        out = []
        for z in zeilen_von(t):
            inhalt, nl = (z[:-1], "\n") if z.endswith("\n") else (z, "")
            while len(inhalt) > w:
                cut = w
                if hat(opts, "-s", "--spaces"):
                    sp = inhalt.rfind(" ", 0, w)
                    if sp > 0:
                        cut = sp + 1
                out.append(inhalt[:cut] + "\n")
                inhalt = inhalt[cut:]
            out.append(inhalt + nl)
        ctx.out("".join(out))
    return 0


@prog("nl")
def nl(ctx):
    r = standard(ctx, "b:d:f:h:i:l:n:ps:v:w:", {"body-numbering": 1, "number-width": 1, "number-separator": 1,
                                                 "starting-line-number": 1, "number-format": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    stil = wert(opts, "-b", "--body-numbering", std="t")
    breite = int(wert(opts, "-w", "--number-width", std="6"))
    sep = wert(opts, "-s", "--number-separator", std="\t")
    n = int(wert(opts, "-v", "--starting-line-number", std="1"))
    for a in args or ["-"]:
        t = lesbar(ctx, a)
        if t is None:
            return 1
        out = []
        for z in zeilen_von(t):
            inhalt = z.rstrip("\n")
            if stil == "a" or stil == "t" and inhalt.strip():
                out.append(f"{n:>{breite}}{sep}{z}")
                n += 1
            else:
                out.append(" " * (breite + len(sep)) + z)
        ctx.out("".join(out))
    return 0


@prog("shuf")
def shuf(ctx):
    import random
    r = standard(ctx, "ei:n:o:rz", {"echo": 0, "input-range": 1, "head-count": 1, "output": 1, "repeat": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if hat(opts, "-e", "--echo"):
        z = [a + "\n" for a in args]
    elif wert(opts, "-i", "--input-range"):
        a, b = wert(opts, "-i", "--input-range").split("-")
        z = [f"{i}\n" for i in range(int(a), int(b) + 1)]
    else:
        t = lesbar(ctx, args[0] if args else "-")
        if t is None:
            return 1
        z = [x if x.endswith("\n") else x + "\n" for x in zeilen_von(t)]
    random.shuffle(z)
    n = wert(opts, "-n", "--head-count")
    if n:
        z = z[:int(n)]
    ctx.out("".join(z))
    return 0


@prog("du")
def du(ctx):
    r = standard(ctx, "0abcd:hHklLmPsSxX:", {"all": 0, "apparent-size": 0, "bytes": 0, "total": 0,
                                              "max-depth": 1, "human-readable": 0, "si": 0, "summarize": 0,
                                              "separate-dirs": 0, "exclude": 1, "time": 2, "block-size": 1,
                                              "dereference": 0, "inodes": 0, "threshold": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    h = hat(opts, "-h", "--human-readable")
    summ = hat(opts, "-s", "--summarize")
    alle = hat(opts, "-a", "--all")
    md = wert(opts, "-d", "--max-depth")
    md = int(md) if md is not None else None
    if summ:
        md = 0
    gesamt = 0
    rc = 0

    def fmt(kb):
        return human(kb * 1024) if h else str(kb)

    def lauf(p, n, tiefe):
        ctx.check()
        if n.typ != "d":
            kb = _bloecke(n)
            if alle and (md is None or tiefe <= md) or tiefe == 0:
                ctx.out(f"{fmt(kb)}\t{p}\n")
            return kb
        summe = 4
        for name, k in sorted(n.eintraege().items()):
            if n.gen is not None:
                continue
            summe += lauf(p.rstrip("/") + "/" + name, k, tiefe + 1)
        if md is None or tiefe <= md:
            ctx.out(f"{fmt(summe)}\t{p}\n")
        return summe

    for a in args or ["."]:
        try:
            n = ctx.node(a)
        except FsError as e:
            ctx.err(f"du: cannot access {qa(a)}: {e.msg}\n")
            rc = 1
            continue
        gesamt += lauf(a, n, 0)
    if hat(opts, "-c", "--total"):
        ctx.out(f"{fmt(gesamt)}\ttotal\n")
    return rc


@prog("df")
def df(ctx):
    r = standard(ctx, "aB:hHiklPTt:vx:", {"all": 0, "block-size": 1, "human-readable": 0, "si": 0, "inodes": 0,
                                          "local": 0, "output": 2, "portability": 0, "print-type": 0, "type": 1,
                                          "exclude-type": 1, "total": 0, "sync": 0, "no-sync": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    h = hat(opts, "-h", "--human-readable")
    T = hat(opts, "-T", "--print-type")
    zeilen = ctx.system.dateisysteme()
    kopf = ["Filesystem", "Type", "1K-blocks" if not h else "Size", "Used", "Available" if not h else "Avail",
            "Use%", "Mounted on"]
    if not T:
        kopf.pop(1)
    tab = [kopf]
    for fs_, typ, gesamt, belegt, mnt in zeilen:
        frei = gesamt - belegt
        proz = f"{math.ceil(belegt * 100 / gesamt) if gesamt else 0}%" if gesamt else "-"
        werte = [human(gesamt * 1024) if h else str(gesamt), human(belegt * 1024) if h else str(belegt),
                 human(frei * 1024) if h else str(frei)]
        z = [fs_, typ, *werte, proz, mnt]
        if not T:
            z.pop(1)
        tab.append(z)
    breiten = [max(len(z[i]) for z in tab) for i in range(len(kopf))]
    off = 1 if T else 0
    if h:
        breiten[1 + off] = max(breiten[1 + off], 4)
        breiten[2 + off] = max(breiten[2 + off], 5)
        breiten[3 + off] = max(breiten[3 + off], 5)
    out = []
    for z in tab:
        teile = []
        for i, f in enumerate(z):
            if i == 0 or (T and i == 1):
                teile.append(f"{f:<{breiten[i]}}")
            elif i == len(z) - 1:
                teile.append(f)
            else:
                teile.append(f"{f:>{breiten[i]}}")
        out.append(" ".join(teile).rstrip() + "\n")
    ctx.out("".join(out))
    return 0


@prog("bash", "sh", "dash", "rbash")
def bash_prog(ctx):
    """Neue (Unter-)Shell: bash -c '...', bash datei, oder interaktiv."""
    from ..shell import Exit, Shell
    args = ctx.argv[1:]
    if args[:1] == ["--version"]:
        ctx.out("GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)\nCopyright (C) 2022 Free Software "
                "Foundation, Inc.\nLicense GPLv3+: GNU GPL version 3 or later <http://gnu.org/licenses/gpl.html>\n\n"
                "This is free software; you are free to change and redistribute it.\nThere is NO WARRANTY, to the "
                "extent permitted by law.\n")
        return 0
    if args[:1] == ["--help"]:
        ctx.out("GNU bash, version 5.2.21(1)-release-(x86_64-pc-linux-gnu)\nUsage:\tbash [GNU long option] [option] ...\n"
                "\tbash [GNU long option] [option] script-file ...\n")
        return 0
    optionen = set()
    while args and args[0].startswith(("-", "+")) and args[0] not in ("-", "--", "-c") and len(args[0]) > 1:
        a = args.pop(0)
        if a in ("-l", "--login", "-i", "--norc", "--noprofile", "-s"):
            continue
        for ch in a[1:]:
            if ch in "exuvnfCh":
                optionen.add(ch)
    if args and args[0] == "--":
        args = args[1:]
    kind = Shell(ctx.system, ctx.tty, interaktiv=False, login=False)
    kind.bg = ctx.sh.bg
    kind.check = ctx.sh.check
    kind.vars.update(ctx.env)
    kind.vars.setdefault("PS1", ctx.sh.vars.get("PS1", ""))
    kind.vars["SHLVL"] = str(int(ctx.env.get("SHLVL", "1") or 1) + 1)
    kind.export = set(ctx.env)
    kind.cwd = ctx.sh.cwd
    kind.opts |= optionen
    kind.fds = ctx.fds
    try:
        if args and args[0] == "-c":
            if len(args) < 2:
                ctx.err(f"{ctx.prog}: -c: option requires an argument\n")
                return 2
            kind.script = ctx.prog
            kind.arg0 = args[2] if len(args) > 2 else ctx.prog
            kind.args = args[3:]
            return kind.run(args[1])
        if args:
            datei = args[0]
            try:
                n = ctx.node(datei)
                if n.typ == "d":
                    ctx.err(f"{ctx.prog}: {datei}: Is a directory\n")
                    return 126
                inhalt = n.inhalt()
            except FsError as e:
                ctx.err(f"{ctx.prog}: {datei}: {e.msg}\n")
                return 127
            if inhalt.startswith("\x7fELF"):
                ctx.err(f"{datei}: {datei}: cannot execute binary file\n")
                return 126
            kind.script = datei
            kind.arg0 = datei
            kind.args = args[1:]
            return kind.run(inhalt)
        if ctx.stdin.isatty and ctx.tty is not None:
            return _interaktiv(ctx, kind)
        kind.script = ctx.prog
        return kind.run(ctx.stdin.read())
    except Exit as e:
        return e.code


def _interaktiv(ctx, kind):
    from ..readline import CTRL_C, Readline, unvollstaendig
    from ..shell import Exit
    from ..streams import Interrupted
    kind.interaktiv = True
    kind.opts |= {"h", "m", "H", "B"}
    kind.arg0 = "bash"
    kind.aliases = dict(ctx.sh.aliases)
    kind.hist = list(ctx.sh.hist)
    tty = ctx.tty
    rl = Readline(tty, kind)
    while True:
        kind.jobs_melden()
        z = rl.eingabe(kind.prompt("PS1"))
        if z is CTRL_C:
            kind.status = 130
            continue
        if z is None:
            tty.write("exit\n")
            return kind.status
        while unvollstaendig(z, kind.aliases):
            w = rl.eingabe(kind.prompt("PS2"), verlauf=[])
            if w is None or w is CTRL_C:
                z = None
                break
            z += "\n" + w
        if not z or not z.strip():
            continue
        kind.hist.append(z)
        tty.sigint.clear()
        try:
            kind.run(z)
        except Interrupted:
            kind.status = 130
            tty.write("\n")
        except Exit as e:
            return e.code
        finally:
            tty.sigint.clear()
