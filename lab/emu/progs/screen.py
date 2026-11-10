"""Vollbildprogramme: less, more, man, nano, vi/vim, top, watch (ueber Escape-Sequenzen)."""
import re
import time

from ..streams import Interrupted, StringWriter
from ..vfs import FsError
from .base import prog, quote_name
from .coreutils import human, strftime

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _sichtbar(t):
    return len(ANSI.sub("", t))


def _schneiden(t, n):
    """Zeile mit ANSI-Codes auf n sichtbare Zeichen kuerzen."""
    out, sicht, i = [], 0, 0
    while i < len(t) and sicht < n:
        m = ANSI.match(t, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(t[i])
        sicht += 1
        i += 1
    return "".join(out)


def _taste(ctx, timeout=None):
    k = ctx.tty.read_key(timeout=timeout)
    return k


class Roh:
    def __init__(self, ctx, alt=True):
        self.ctx, self.alt = ctx, alt

    def __enter__(self):
        self.ctx.tty.modus(canon=False, echo=False, isig=False)
        if self.alt:
            self.ctx.tty.write_raw("\x1b[?1049h\x1b[H\x1b[2J")
        return self

    def __exit__(self, *a):
        if self.alt:
            self.ctx.tty.write_raw("\x1b[?25h\x1b[?1049l")
        self.ctx.tty.modus(canon=True, echo=True, isig=True)
        return False


# ---------------------------------------------------------------------------- Pager
def pager(ctx, text, name="", lines_text=False, man=None, prompt_stil="less"):
    if not ctx.isatty(1) or ctx.tty is None:
        ctx.out(text)
        return 0
    zeilen = text.split("\n")
    if zeilen and zeilen[-1] == "":
        zeilen.pop()
    # lange Zeilen umbrechen (wie less ohne -S)
    cols = ctx.cols
    umg = []
    for z in zeilen:
        z = z.replace("\t", "        ")
        if _sichtbar(z) <= cols:
            umg.append(z)
            continue
        roh = ANSI.sub("", z)
        for i in range(0, len(roh), cols):
            umg.append(roh[i:i + cols])
    zeilen = umg
    rows = ctx.rows - 1
    top = 0
    suche = None
    meldung = None
    tty = ctx.tty
    with Roh(ctx):
        while True:
            top = max(0, min(top, max(0, len(zeilen) - rows)))
            s = ["\x1b[H"]
            for i in range(rows):
                j = top + i
                z = zeilen[j] if j < len(zeilen) else "~"
                if suche and j < len(zeilen):
                    try:
                        z = re.sub(f"({suche})", "\x1b[7m\\1\x1b[27m", z)
                    except re.error:
                        pass
                s.append(z + "\x1b[K\r\n")
            ende = top + rows >= len(zeilen)
            if meldung:
                status = meldung
                meldung = None
            elif man:
                status = f" Manual page {man} line {top + 1}" + (" (END)" if ende else "") + " (press h for help or q to quit)"
                status = f"\x1b[7m{status}\x1b[27m"
            elif ende:
                status = "\x1b[7m(END)\x1b[27m"
            elif top == 0 and name:
                status = f"\x1b[7m{name}\x1b[27m"
            else:
                status = ":"
            s.append(status + "\x1b[K")
            tty.write_raw("".join(s))
            k = _taste(ctx)
            if k is None:
                continue
            if k in ("q", "Q", ":q", "\x1b"):
                break
            if k in (" ", "f", "\x06", "\x1b[6~", "z"):
                top += rows
            elif k in ("b", "\x02", "\x1b[5~", "w"):
                top -= rows
            elif k in ("\r", "\n", "j", "e", "\x1b[B", "\x0e", "\x05"):
                top += 1
            elif k in ("k", "y", "\x1b[A", "\x10", "\x19"):
                top -= 1
            elif k in ("d", "\x04"):
                top += rows // 2
            elif k in ("u", "\x15"):
                top -= rows // 2
            elif k in ("g", "<", "\x1b[H", "\x1b[1~"):
                top = 0
            elif k in ("G", ">", "\x1b[F", "\x1b[4~"):
                top = len(zeilen)
            elif k in ("/", "?"):
                eingabe = _zeile_lesen(ctx, k, rows)
                if eingabe:
                    suche = eingabe
                    top = _finde(zeilen, suche, top + 1, top)
                    if top is None:
                        top = 0
                        meldung = "\x1b[7mPattern not found  (press RETURN)\x1b[27m"
            elif k == "n" and suche:
                t2 = _finde(zeilen, suche, top + 1, None)
                if t2 is None:
                    meldung = "\x1b[7mPattern not found  (press RETURN)\x1b[27m"
                else:
                    top = t2
            elif k == "N" and suche:
                for j in range(top - 1, -1, -1):
                    if re.search(suche, zeilen[j]):
                        top = j
                        break
            elif k == "h":
                meldung = "\x1b[7mSUMMARY OF LESS COMMANDS: q quit, SPACE next page, b back, /pattern search, " \
                          "n next match\x1b[27m"
            elif k == "\x03":
                continue
    return 0


def _finde(zeilen, muster, start, fallback):
    try:
        rx = re.compile(muster)
    except re.error:
        return fallback
    for j in range(start, len(zeilen)):
        if rx.search(zeilen[j]):
            return j
    return fallback


def _zeile_lesen(ctx, praefix, zeile_nr):
    tty = ctx.tty
    buf = ""
    tty.write_raw(f"\x1b[{zeile_nr + 1};1H\x1b[K{praefix}")
    while True:
        k = tty.read_key()
        if k in ("\r", "\n"):
            return buf
        if k in ("\x03", "\x1b"):
            return None
        if k in ("\x7f", "\x08"):
            if not buf:
                return None
            buf = buf[:-1]
            tty.write_raw("\b \b")
        elif k and len(k) == 1 and ord(k) >= 32:
            buf += k
            tty.write_raw(k)


def _eingabe_text(ctx, args):
    teile = []
    for a in args or ["-"]:
        if a == "-":
            if ctx.stdin.isatty:
                continue
            teile.append(ctx.stdin.read())
            continue
        try:
            n = ctx.node(a)
            if n.typ == "d":
                return None, f"{a} is a directory"
            teile.append(n.inhalt())
        except FsError as e:
            return None, f"{a}: {e.msg}"
    return "".join(teile), None


@prog("less")
def less(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-") or a == "-"]
    if "--help" in ctx.argv:
        return ctx.hilfe()
    if "--version" in ctx.argv or "-V" in ctx.argv:
        return ctx.version()
    if not args and ctx.stdin.isatty:
        ctx.err('Missing filename ("less --help" for help)\n')
        return 1
    text, fehler = _eingabe_text(ctx, args)
    if fehler:
        ctx.err(fehler + "\n")
        return 1
    if "\0" in text[:2000]:
        name = args[0] if args else "-"
        ctx.err(f'"{name}" may be a binary file.  See it anyway? ')
        a = ctx.stdin.readline()
        if not a.lower().startswith("y"):
            return 0
    return pager(ctx, text, args[0] if args else "")


@prog("more")
def more(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    text, fehler = _eingabe_text(ctx, args)
    if fehler:
        ctx.err(f"more: {fehler}\n" if "directory" not in fehler else f"\n*** {fehler} ***\n\n")
        return 1
    if not ctx.isatty(1):
        ctx.out(text)
        return 0
    zeilen = text.split("\n")
    if zeilen and zeilen[-1] == "":
        zeilen.pop()
    rows = ctx.rows - 1
    if len(zeilen) <= rows:
        ctx.out(text)
        return 0
    tty = ctx.tty
    pos = rows
    ctx.out("\n".join(zeilen[:rows]) + "\n")
    with Roh(ctx, alt=False):
        while pos < len(zeilen):
            proz = int(pos * 100 / len(zeilen))
            tty.write_raw(f"\x1b[7m--More--({proz}%)\x1b[27m")
            k = _taste(ctx)
            tty.write_raw("\r\x1b[K")
            if k in ("q", "Q", "\x03"):
                break
            if k in ("\r", "\n", "\x1b[B"):
                tty.write_raw(zeilen[pos] + "\r\n")
                pos += 1
            elif k in (" ", "f", "z"):
                block = zeilen[pos:pos + rows]
                tty.write_raw("\r\n".join(block) + "\r\n")
                pos += rows
    return 0


@prog("man")
def man(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    if not args:
        ctx.err("What manual page do you want?\nFor example, try 'man man'.\n")
        return 1
    seite = args[-1]
    hilfe = ctx.texte() if False else None
    from ..builtins import texte
    t = texte()
    h = t["cmd"].get(seite, {}).get("help")
    if seite in t["builtin"] and not h:
        h = t["builtin"][seite]
    if not h:
        ctx.err(f"No manual entry for {seite}\n")
        return 16
    text = _man_seite(seite, h)
    return pager(ctx, text, man=f"{seite}(1)")


def _man_seite(name, hilfe):
    kopf = f"{name.upper()}(1)"
    titel = "User Commands"
    breite = 78
    erste = kopf + " " * ((breite - len(kopf) * 2 - len(titel)) // 2) + titel
    erste = erste + " " * (breite - len(erste) - len(kopf)) + kopf
    zeilen = hilfe.rstrip("\n").split("\n")
    usage = [z for z in zeilen if z.lower().startswith("usage")]
    beschr = next((z for z in zeilen[1:4] if z.strip() and not z.lower().startswith("usage")), "")
    out = [erste, "", "\x1b[1mNAME\x1b[0m", f"       {name} - {beschr.strip().rstrip('.').lower() if beschr else name}", "",
           "\x1b[1mSYNOPSIS\x1b[0m"]
    for u in usage or [f"Usage: {name}"]:
        out.append("       " + re.sub(r"^Usage:\s*", "", u, flags=re.I))
    out += ["", "\x1b[1mDESCRIPTION\x1b[0m"]
    for z in zeilen:
        if z.lower().startswith("usage"):
            continue
        out.append("       " + z if z.strip() else "")
    out += ["", "\x1b[1mAUTHOR\x1b[0m", "       Written by the GNU project.", "",
            "\x1b[1mSEE ALSO\x1b[0m", "       Full documentation <https://www.gnu.org/software/coreutils/" + name + ">", "",
            "GNU coreutils 9.4" + " " * 26 + "April 2024" + " " * 25 + kopf]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- nano
@prog("nano", "pico", "rnano")
def nano(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-") and not a.startswith("+")]
    if "--version" in ctx.argv or "-V" in ctx.argv:
        ctx.out(" GNU nano, version 7.2\n (C) 2023 the Free Software Foundation and various contributors\n"
                " Compiled options: --disable-libmagic --enable-utf8\n")
        return 0
    if not ctx.isatty(1):
        ctx.err("Too many errors from stdin\n")
        return 1
    name = args[0] if args else ""
    text = ""
    neu = True
    if name:
        try:
            n = ctx.node(name)
            if n.typ == "d":
                meldung = f"\"{name}\" is a directory"
                name = ""
            else:
                text = n.inhalt()
                neu = False
        except FsError:
            pass
    zeilen = text.split("\n")
    if text.endswith("\n"):
        zeilen.pop()
    if not zeilen:
        zeilen = [""]
    tty = ctx.tty
    cols, rows = ctx.cols, ctx.rows
    r = c = 0
    top = 0
    geaendert = False
    meldung = ("New File" if neu else f"Read {len(zeilen) if text else 0} line{'s' if len(zeilen) != 1 else ''}") if name else ""
    clip = []
    hilfe = ["^G Help      ^O Write Out ^W Where Is  ^K Cut       ^T Execute   ^C Location  M-U Undo     M-A Set Mark",
             "^X Exit      ^R Read File ^\\ Replace   ^U Paste     ^J Justify   ^/ Go To Line M-E Redo     M-6 Copy"]

    def zeichne():
        nonlocal top
        hoehe = rows - 4
        if r < top:
            top = r
        if r >= top + hoehe:
            top = r - hoehe + 1
        titel = f"  GNU nano 7.2"
        mitte = name or "New Buffer"
        rechts = "Modified  " if geaendert else ""
        kopf = titel + " " * max(1, (cols - len(mitte)) // 2 - len(titel)) + mitte
        kopf = kopf + " " * max(1, cols - len(kopf) - len(rechts)) + rechts
        s = ["\x1b[H\x1b[7m" + kopf[:cols].ljust(cols) + "\x1b[0m\r\n"]
        for i in range(hoehe):
            j = top + i
            z = zeilen[j].replace("\t", "        ")[:cols] if j < len(zeilen) else ""
            s.append(z + "\x1b[K\r\n")
        m = f"[ {meldung} ]" if meldung else ""
        s.append((" " * max(0, (cols - len(m)) // 2) + (f"\x1b[7m{m}\x1b[0m" if m else "")) + "\x1b[K\r\n")
        for hz in hilfe:
            s.append(re.sub(r"(\^.|M-.)", "\x1b[7m\\1\x1b[0m", hz[:cols]) + "\x1b[K" + ("\r\n" if hz is hilfe[0] else ""))
        s.append(f"\x1b[{r - top + 2};{min(c, cols - 1) + 1}H")
        tty.write_raw("".join(s))

    def frage(text_):
        tty.write_raw(f"\x1b[{rows - 2};1H\x1b[7m{text_}\x1b[K\x1b[0m")
        buf = ""
        while True:
            k = tty.read_key()
            if k in ("\r", "\n"):
                return buf
            if k in ("\x03",):
                return None
            if k in ("\x7f", "\x08"):
                buf = buf[:-1]
            elif k and len(k) == 1 and ord(k) >= 32:
                buf += k
            tty.write_raw(f"\x1b[{rows - 2};1H\x1b[7m{text_}{buf}\x1b[K\x1b[0m")

    def speichern(ziel):
        nonlocal geaendert, name, meldung
        try:
            inhalt = "\n".join(zeilen) + "\n"
            ctx.fs.schreiben(ziel, inhalt, ctx.cwd)
            name = ziel
            geaendert = False
            meldung = f"Wrote {len(zeilen)} line{'s' if len(zeilen) != 1 else ''}"
            return True
        except FsError as e:
            meldung = f"Error writing {ziel}: {e.msg}"
            return False

    with Roh(ctx):
        while True:
            zeichne()
            k = _taste(ctx)
            if k is None:
                continue
            meldung_alt = meldung
            meldung = ""
            if k == "\x18":  # ^X
                if geaendert:
                    tty.write_raw(f"\x1b[{rows - 2};1H\x1b[7mSave modified buffer?\x1b[0m\x1b[K\r\n"
                                  " \x1b[7mY\x1b[0m Yes\x1b[K\r\n\x1b[7mN\x1b[0m No           \x1b[7m^C\x1b[0m Cancel\x1b[K")
                    while True:
                        a = tty.read_key()
                        if a in ("y", "Y"):
                            z = frage(f"File Name to Write: {name}" if False else "File Name to Write: ") if not name else name
                            if z is None:
                                break
                            if speichern(z):
                                return 0
                            break
                        if a in ("n", "N"):
                            return 0
                        if a == "\x03":
                            meldung = "Cancelled"
                            break
                    continue
                return 0
            if k == "\x0f":  # ^O
                z = frage("File Name to Write: ")
                if z is None:
                    meldung = "Cancelled"
                    continue
                speichern(z or name) if (z or name) else None
                continue
            if k == "\x0b":  # ^K
                clip = [zeilen.pop(r)]
                if not zeilen:
                    zeilen = [""]
                r = min(r, len(zeilen) - 1)
                c = 0
                geaendert = True
                continue
            if k == "\x15":  # ^U
                for z in reversed(clip):
                    zeilen.insert(r, z)
                r += len(clip)
                geaendert = True
                continue
            if k == "\x17":  # ^W
                z = frage("Search: ")
                if z:
                    for j in list(range(r + 1, len(zeilen))) + list(range(0, r + 1)):
                        if z in zeilen[j]:
                            r, c = j, zeilen[j].index(z)
                            break
                    else:
                        meldung = f"\"{z}\" not found"
                continue
            if k == "\x03":
                meldung = f"line {r + 1}/{len(zeilen)} ({int((r + 1) * 100 / len(zeilen))}%), col {c + 1}/{len(zeilen[r]) + 1}"
                continue
            if k == "\x07":
                meldung = "Help is not available in the lab"
                continue
            if k in ("\x1b[A",):
                r = max(0, r - 1)
                c = min(c, len(zeilen[r]))
            elif k in ("\x1b[B",):
                r = min(len(zeilen) - 1, r + 1)
                c = min(c, len(zeilen[r]))
            elif k in ("\x1b[D",):
                if c > 0:
                    c -= 1
                elif r > 0:
                    r -= 1
                    c = len(zeilen[r])
            elif k in ("\x1b[C",):
                if c < len(zeilen[r]):
                    c += 1
                elif r < len(zeilen) - 1:
                    r += 1
                    c = 0
            elif k in ("\x1b[H", "\x01"):
                c = 0
            elif k in ("\x1b[F", "\x05"):
                c = len(zeilen[r])
            elif k in ("\x1b[5~",):
                r = max(0, r - (rows - 4))
                c = min(c, len(zeilen[r]))
            elif k in ("\x1b[6~",):
                r = min(len(zeilen) - 1, r + (rows - 4))
                c = min(c, len(zeilen[r]))
            elif k in ("\r", "\n"):
                rest = zeilen[r][c:]
                zeilen[r] = zeilen[r][:c]
                zeilen.insert(r + 1, rest)
                r += 1
                c = 0
                geaendert = True
            elif k in ("\x7f", "\x08"):
                if c > 0:
                    zeilen[r] = zeilen[r][:c - 1] + zeilen[r][c:]
                    c -= 1
                elif r > 0:
                    c = len(zeilen[r - 1])
                    zeilen[r - 1] += zeilen.pop(r)
                    r -= 1
                geaendert = True
            elif k == "\x1b[3~":
                if c < len(zeilen[r]):
                    zeilen[r] = zeilen[r][:c] + zeilen[r][c + 1:]
                elif r < len(zeilen) - 1:
                    zeilen[r] += zeilen.pop(r + 1)
                geaendert = True
            elif len(k) == 1 and (ord(k) >= 32 or k == "\t"):
                zeilen[r] = zeilen[r][:c] + k + zeilen[r][c:]
                c += 1
                geaendert = True
            else:
                meldung = meldung_alt


# ---------------------------------------------------------------------------- vi
@prog("vi", "vim", "vim.tiny", "view")
def vi(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-") and not a.startswith("+")]
    if "--version" in ctx.argv:
        ctx.out("VIM - Vi IMproved 9.1 (2024 Jan 02, compiled Apr 01 2024 20:12:25)\nIncluded patches: 1-16, 647\n"
                "Modified by team+vim@tracker.debian.org\nCompiled by team+vim@tracker.debian.org\n"
                "Huge version without GUI.\n")
        return 0
    if not ctx.isatty(1):
        ctx.err("Vim: Warning: Output is not to a terminal\n")
    name = args[0] if args else ""
    text, neu = "", True
    meldung = ""
    if name:
        try:
            n = ctx.node(name)
            if n.typ == "d":
                meldung = f'"{name}" is a directory'
            else:
                text, neu = n.inhalt(), False
        except FsError:
            pass
    zeilen = text.split("\n")
    if text.endswith("\n"):
        zeilen.pop()
    if not zeilen:
        zeilen = [""]
    if name and not meldung:
        meldung = f'"{name}" [New]' if neu else f'"{name}" {len(zeilen)}L, {len(text.encode())}B'
    tty = ctx.tty
    cols, rows = ctx.cols, ctx.rows
    r = c = top = 0
    modus = "n"
    geaendert = False
    puffer = []
    rueck = []
    nur_lesen = ctx.prog == "view"
    letzte = ""
    suche = None

    def zeichne():
        nonlocal top
        hoehe = rows - 1
        if r < top:
            top = r
        if r >= top + hoehe:
            top = r - hoehe + 1
        s = ["\x1b[H"]
        for i in range(hoehe):
            j = top + i
            s.append((zeilen[j].replace("\t", "        ")[:cols] if j < len(zeilen) else "\x1b[1;34m~\x1b[0m") + "\x1b[K\r\n")
        unten = "\x1b[1m-- INSERT --\x1b[0m" if modus == "i" else meldung
        pos = f"{r + 1},{c + 1}" if zeilen[r] else f"{r + 1},0-1"
        prozent = "All" if len(zeilen) <= hoehe else ("Top" if top == 0 else ("Bot" if top + hoehe >= len(zeilen) else f"{int(top * 100 / len(zeilen))}%"))
        s.append(unten + "\x1b[K" + f"\x1b[{rows};{cols - 18}H{pos:<14}{prozent}")
        cc = min(c, max(0, len(zeilen[r]) - (0 if modus == "i" else 1)))
        s.append(f"\x1b[{r - top + 1};{cc + 1}H")
        tty.write_raw("".join(s))

    def befehl():
        tty.write_raw(f"\x1b[{rows};1H\x1b[K:")
        buf = ""
        while True:
            k = tty.read_key()
            if k in ("\r", "\n"):
                return buf
            if k in ("\x1b", "\x03"):
                return None
            if k in ("\x7f", "\x08"):
                if not buf:
                    return None
                buf = buf[:-1]
            elif k and len(k) == 1 and ord(k) >= 32:
                buf += k
            tty.write_raw(f"\x1b[{rows};1H\x1b[K:{buf}")

    def schreiben(ziel):
        nonlocal geaendert, name, meldung
        if not ziel:
            meldung = "\x1b[37;41mE32: No file name\x1b[0m"
            return False
        try:
            inhalt = "\n".join(zeilen) + "\n"
            existierte = ctx.fs.finde(ziel, ctx.cwd) is not None
            ctx.fs.schreiben(ziel, inhalt, ctx.cwd)
            meldung = f'"{ziel}" {"" if existierte else "[New] "}{len(zeilen)}L, {len(inhalt.encode())}B written'
            name = ziel
            geaendert = False
            return True
        except FsError as e:
            meldung = f'\x1b[37;41m"{ziel}" E212: Can\'t open file for writing\x1b[0m'
            return False

    def sichern():
        rueck.append(([*zeilen], r, c))
        if len(rueck) > 100:
            rueck.pop(0)

    with Roh(ctx):
        while True:
            zeichne()
            k = _taste(ctx)
            if k is None:
                continue
            if modus == "i":
                if k in ("\x1b", "\x03"):
                    modus = "n"
                    c = max(0, c - 1)
                    meldung = ""
                elif k in ("\r", "\n"):
                    rest = zeilen[r][c:]
                    zeilen[r] = zeilen[r][:c]
                    zeilen.insert(r + 1, rest)
                    r, c = r + 1, 0
                    geaendert = True
                elif k in ("\x7f", "\x08"):
                    if c > 0:
                        zeilen[r] = zeilen[r][:c - 1] + zeilen[r][c:]
                        c -= 1
                    elif r > 0:
                        c = len(zeilen[r - 1])
                        zeilen[r - 1] += zeilen.pop(r)
                        r -= 1
                    geaendert = True
                elif k == "\x1b[A":
                    r = max(0, r - 1)
                    c = min(c, len(zeilen[r]))
                elif k == "\x1b[B":
                    r = min(len(zeilen) - 1, r + 1)
                    c = min(c, len(zeilen[r]))
                elif k == "\x1b[D":
                    c = max(0, c - 1)
                elif k == "\x1b[C":
                    c = min(len(zeilen[r]), c + 1)
                elif k == "\x1b[3~":
                    zeilen[r] = zeilen[r][:c] + zeilen[r][c + 1:]
                elif len(k) == 1 and (ord(k) >= 32 or k == "\t"):
                    zeilen[r] = zeilen[r][:c] + k + zeilen[r][c:]
                    c += 1
                    geaendert = True
                continue
            # Normalmodus
            meldung = "" if k not in (":",) else meldung
            folge = letzte + k
            letzte = ""
            if k in ("h", "\x1b[D", "\x7f"):
                c = max(0, c - 1)
            elif k in ("l", "\x1b[C", " "):
                c = min(max(0, len(zeilen[r]) - 1), c + 1)
            elif k in ("j", "\x1b[B", "\r", "+"):
                r = min(len(zeilen) - 1, r + 1)
            elif k in ("k", "\x1b[A", "-"):
                r = max(0, r - 1)
            elif k in ("0", "\x1b[H", "^"):
                c = 0
            elif k in ("$", "\x1b[F"):
                c = max(0, len(zeilen[r]) - 1)
            elif k == "G":
                r = len(zeilen) - 1
            elif folge == "gg":
                r = 0
            elif k == "g":
                letzte = "g"
            elif k in ("i", "\x1b[2~"):
                modus = "i"
                sichern()
            elif k == "I":
                modus, c = "i", 0
                sichern()
            elif k == "a":
                modus, c = "i", min(len(zeilen[r]), c + 1)
                sichern()
            elif k == "A":
                modus, c = "i", len(zeilen[r])
                sichern()
            elif k == "o":
                sichern()
                zeilen.insert(r + 1, "")
                r, c, modus, geaendert = r + 1, 0, "i", True
            elif k == "O":
                sichern()
                zeilen.insert(r, "")
                c, modus, geaendert = 0, "i", True
            elif k == "x":
                if zeilen[r]:
                    sichern()
                    zeilen[r] = zeilen[r][:c] + zeilen[r][c + 1:]
                    c = min(c, max(0, len(zeilen[r]) - 1))
                    geaendert = True
            elif folge == "dd":
                sichern()
                puffer = [zeilen.pop(r)]
                if not zeilen:
                    zeilen = [""]
                r = min(r, len(zeilen) - 1)
                c = 0
                geaendert = True
            elif k == "d":
                letzte = "d"
            elif folge == "yy":
                puffer = [zeilen[r]]
                meldung = ""
            elif k == "y":
                letzte = "y"
            elif k == "p" and puffer:
                sichern()
                for i, z in enumerate(puffer):
                    zeilen.insert(r + 1 + i, z)
                r += 1
                geaendert = True
            elif k == "P" and puffer:
                sichern()
                for i, z in enumerate(puffer):
                    zeilen.insert(r + i, z)
                geaendert = True
            elif k == "u":
                if rueck:
                    zeilen, r, c = rueck.pop()
                    meldung = "1 change; before #1  0 seconds ago"
                else:
                    meldung = "Already at oldest change"
            elif folge == "ZZ":
                if geaendert:
                    if not schreiben(name):
                        continue
                return 0
            elif k == "Z":
                letzte = "Z"
            elif k == "/":
                tty.write_raw(f"\x1b[{rows};1H\x1b[K/")
                buf = ""
                while True:
                    k2 = tty.read_key()
                    if k2 in ("\r", "\n"):
                        break
                    if k2 in ("\x1b", "\x03"):
                        buf = None
                        break
                    if k2 in ("\x7f", "\x08"):
                        buf = buf[:-1]
                    elif k2 and len(k2) == 1:
                        buf += k2
                    tty.write_raw(f"\x1b[{rows};1H\x1b[K/{buf}")
                if buf:
                    suche = buf
                    k = "n"
            if k == "n" and suche:
                for j in list(range(r + 1, len(zeilen))) + list(range(0, r + 1)):
                    if suche in zeilen[j]:
                        if j <= r:
                            meldung = "search hit BOTTOM, continuing at TOP"
                        r, c = j, zeilen[j].index(suche)
                        break
                else:
                    meldung = f"\x1b[37;41mE486: Pattern not found: {suche}\x1b[0m"
            elif k == ":":
                cmd = befehl()
                if cmd is None:
                    meldung = ""
                    continue
                cmd = cmd.strip()
                if cmd in ("q", "quit"):
                    if geaendert:
                        meldung = "\x1b[37;41mE37: No write since last change (add ! to override)\x1b[0m"
                        continue
                    return 0
                if cmd in ("q!", "quit!", "qa!", "cq"):
                    return 0
                if cmd.startswith("w") and cmd.rstrip("!") in ("w", "wq", "x", "wq!") or cmd.startswith("w "):
                    ziel = cmd.split(None, 1)[1] if " " in cmd else name
                    if nur_lesen and not cmd.endswith("!"):
                        meldung = "\x1b[37;41mE45: 'readonly' option is set (add ! to override)\x1b[0m"
                        continue
                    if schreiben(ziel) and cmd.rstrip("!") in ("wq", "x"):
                        return 0
                    continue
                if cmd == "x":
                    if geaendert and not schreiben(name):
                        continue
                    return 0
                if cmd.isdigit():
                    r = min(len(zeilen) - 1, max(0, int(cmd) - 1))
                    continue
                if cmd in ("set nu", "set number", "set nonu", "syntax on", "noh"):
                    continue
                meldung = f"\x1b[37;41mE492: Not an editor command: {cmd}\x1b[0m"
            if r >= len(zeilen):
                r = len(zeilen) - 1
            c = min(c, max(0, len(zeilen[r]) - 1)) if modus == "n" else c


# ---------------------------------------------------------------------------- top / watch
def _top_text(ctx, cols, rows):
    sysm = ctx.system
    up = time.time() - sysm.boot
    tage, r = divmod(int(up), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    dauer = (f"{tage} day{'s' if tage != 1 else ''}, " if tage else "") + (f"{h:2d}:{m:02d}" if h else f"{m} min")
    procs = sorted(sysm.prozesse.values(), key=lambda p: p["pid"])
    gesamt, used, shared, cache, swap = sysm.hooks.get("mem", lambda: (16 * 1024 ** 2, 1024 ** 2, 0, 1024 ** 2, 0))()
    mib = lambda kb: kb / 1024  # noqa: E731
    kopf = [
        f"top - {strftime('%H:%M:%S', time.time())} up {dauer},  1 user,  load average: 0.08, 0.05, 0.01",
        f"Tasks: {len(procs) + 600:>3} total,   1 running, {len(procs) + 599:>3} sleeping,   0 stopped,   0 zombie",
        "%Cpu(s):  0.1 us,  0.1 sy,  0.0 ni, 99.8 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st",
        f"MiB Mem : {mib(gesamt):>9.1f} total, {mib(gesamt - used - cache):>9.1f} free, {mib(used):>9.1f} used, "
        f"{mib(cache):>9.1f} buff/cache",
        f"MiB Swap: {mib(swap):>9.1f} total, {mib(swap):>9.1f} free, {0:>9.1f} used. {mib(gesamt - used):>9.1f} avail Mem",
        "",
        "\x1b[7m    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND" + " " * max(0, cols - 79) + "\x1b[0m",
    ]
    zeilen = []
    for p in sorted(procs, key=lambda p: (p["comm"] != "top", p["pid"])):
        pr = "rt" if "migration" in p["comm"] else "20"
        ni = "0"
        zeilen.append(f"{p['pid']:>7} {p['user'][:8]:<8}  {pr:>2}  {ni:>2} {p['vsz']:>7} {p['rss']:>6} {p['rss'] // 2:>6} "
                      f"{'R' if p['comm'] == 'top' else 'S'}   0.0   0.0   0:00.{p['pid'] % 97:02d} {p['comm']}")
    return kopf + zeilen[:max(0, rows - len(kopf) - 1)]


@prog("top")
def top(ctx):
    a = ctx.argv[1:]
    batch = any(x.startswith("-b") or x == "b" for x in a)
    n = None
    for i, x in enumerate(a):
        if x.startswith("-n"):
            v = x[2:] or (a[i + 1] if i + 1 < len(a) else "1")
            n = int(v) if v.isdigit() else 1
        if x.startswith("-bn"):
            n = int(x[3:] or 1)
    if batch or not ctx.isatty(1):
        for i in range(n or 1):
            ctx.out("\n".join(_top_text(ctx, 200, 10000)).replace("\x1b[7m", "").replace("\x1b[0m", "") + "\n\n")
            if i + 1 < (n or 1):
                ctx.sleep(3)
        return 0
    tty = ctx.tty
    runden = 0
    with Roh(ctx):
        tty.write_raw("\x1b[?25l")
        while True:
            zeilen = _top_text(ctx, ctx.cols, ctx.rows)
            tty.write_raw("\x1b[H" + "".join(_schneiden(z, ctx.cols) + "\x1b[K\r\n" for z in zeilen[:-1]) +
                          _schneiden(zeilen[-1], ctx.cols) + "\x1b[K\x1b[J")
            runden += 1
            if n and runden >= n:
                break
            k = ctx.tty.read_key(timeout=3)
            if k in ("q", "\x03"):
                break
    return 0


@prog("htop")
def htop(ctx):
    return top(ctx)


@prog("watch")
def watch(ctx):
    a = ctx.argv[1:]
    intervall = 2.0
    titel = True
    while a and a[0].startswith("-"):
        if a[0] in ("-n", "--interval") and len(a) > 1:
            try:
                intervall = max(0.1, float(a[1]))
            except ValueError:
                ctx.err(f"watch: failed to parse argument: '{a[1]}'\n")
                return 1
            a = a[2:]
            continue
        if a[0].startswith("-n") and len(a[0]) > 2:
            intervall = float(a[0][2:])
            a = a[1:]
            continue
        if a[0] in ("-t", "--no-title"):
            titel = False
        elif a[0] in ("-h", "--help"):
            return ctx.hilfe()
        elif a[0] in ("-v", "--version"):
            ctx.out("watch from procps-ng 4.0.4\n")
            return 0
        a = a[1:]
    if not a:
        ctx.err("\nUsage:\n watch [options] command\n\nOptions:\n  -b, --beep          beep if command has a "
                "non-zero exit\n  -n, --interval <secs>  seconds to wait between updates\n")
        return 1
    cmd = " ".join(a)
    tty = ctx.tty
    with Roh(ctx):
        while True:
            w = StringWriter()
            sub = ctx.sh.subshell()
            fds = dict(ctx.fds)
            fds[1] = fds[2] = w
            sub.fds = fds
            try:
                sub.run(cmd, fds)
            except Interrupted:
                break
            except Exception:  # noqa: BLE001
                pass
            text = w.wert().replace("\t", "        ")
            kopf = ""
            if titel:
                links = f"Every {intervall:.1f}s: {cmd}"
                rechts = f"{ctx.system.hostname}: {strftime('%a %b %e %H:%M:%S %Y', time.time())}"
                kopf = links + " " * max(1, ctx.cols - len(links) - len(rechts)) + rechts + "\r\n\r\n"
            zeilen = text.split("\n")[:ctx.rows - (2 if titel else 0)]
            tty.write_raw("\x1b[H" + kopf + "".join(_schneiden(z, ctx.cols) + "\x1b[K\r\n" for z in zeilen[:-1]) +
                          (_schneiden(zeilen[-1], ctx.cols) if zeilen else "") + "\x1b[J")
            ende = time.time() + intervall
            weg = False
            while time.time() < ende:
                k = tty.read_key(timeout=0.2)
                if k == "\x03" or k == "q":
                    weg = True
                    break
            if weg:
                break
    return 0
