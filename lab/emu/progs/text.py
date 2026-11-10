"""Textwerkzeuge: grep, sed, sort, uniq, cut, tr, paste, comm, diff, cmp, xargs, find, column, strings, od."""
import difflib
import fnmatch
import posixpath
import re
import time

from .. import vfs
from ..vfs import FsError
from .base import ProgFehler, Usage, prog, qa, ql, quote_name
from .coreutils import hat, human, lesbar, standard, wert, zeilen_von, mode_str, user_name, group_name, zeit_ls, _bloecke

POSIX_KLASSEN = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
                 "space": r" \t\n\r\f\v", "blank": r" \t", "punct": r"!-/:-@\[-`{-~", "xdigit": "0-9A-Fa-f",
                 "print": r" -~", "graph": r"!-~", "cntrl": r"\x00-\x1f\x7f"}


class RegexFehler(Exception):
    pass


def posix_regex(muster, erweitert=False):
    """POSIX BRE/ERE -> Python-Regex."""
    out, i, n = [], 0, len(muster)
    tiefe = 0
    while i < n:
        c = muster[i]
        if c == "[":
            j = i + 1
            teile = ["["]
            if j < n and muster[j] == "^":
                teile.append("^")
                j += 1
            if j < n and muster[j] == "]":
                teile.append(r"\]")
                j += 1
            while j < n and muster[j] != "]":
                if muster.startswith("[:", j):
                    e = muster.find(":]", j)
                    if e < 0:
                        raise RegexFehler("Unmatched [, [^, [:, [., or [=")
                    k = muster[j + 2:e]
                    if k not in POSIX_KLASSEN:
                        raise RegexFehler("Invalid character class name")
                    teile.append(POSIX_KLASSEN[k])
                    j = e + 2
                    continue
                ch = muster[j]
                teile.append("\\\\" if ch == "\\" else ("\\[" if ch == "[" else ch))
                j += 1
            if j >= n:
                raise RegexFehler("Unmatched [, [^, [:, [., or [=")
            teile.append("]")
            out.append("".join(teile))
            i = j + 1
            continue
        if c == "\\" and i + 1 < n:
            d = muster[i + 1]
            i += 2
            if not erweitert and d in "(){}|+?":
                if d == "(":
                    tiefe += 1
                elif d == ")":
                    if tiefe == 0:
                        raise RegexFehler("Unmatched ) or \\)")
                    tiefe -= 1
                out.append(d)
                continue
            if d == "<":
                out.append(r"\b(?=\w)")
                continue
            if d == ">":
                out.append(r"\b(?<=\w)")
                continue
            if d in "wWsSbB" or d.isdigit():
                out.append("\\" + d)
                continue
            if d == "n":
                out.append(r"\n")
                continue
            if d == "t":
                out.append(r"\t")
                continue
            out.append(re.escape(d))
            continue
        if c == "\\":
            raise RegexFehler("Trailing backslash")
        if erweitert:
            if c == "(":
                tiefe += 1
            elif c == ")":
                if tiefe == 0:
                    raise RegexFehler("Unmatched ) or \\)")
                tiefe -= 1
            if c == "{" and not re.match(r"\{\d*(,\d*)?\}", muster[i:]):
                out.append(r"\{")
                i += 1
                continue
            out.append(c)
        else:
            if c in "(){}|+?":
                out.append("\\" + c)
            elif c == "*" and (not out or out[-1] in ("^", "(")):
                out.append(r"\*")
            else:
                out.append(c)
        i += 1
    if tiefe:
        raise RegexFehler("Unmatched ( or \\(")
    return "".join(out)


def kompiliere(muster, art, ignore=False, wort=False, zeile=False):
    if art == "F":
        rx = re.escape(muster)
    elif art == "P":
        rx = muster
    else:
        rx = posix_regex(muster, art == "E")
    if wort:
        rx = r"(?<![\w])(?:" + rx + r")(?![\w])"
    if zeile:
        rx = r"^(?:" + rx + r")$"
    return re.compile(rx, re.I if ignore else 0)


# ---------------------------------------------------------------------------- grep
GREP_LONGS = {"extended-regexp": 0, "fixed-strings": 0, "basic-regexp": 0, "perl-regexp": 0, "regexp": 1, "file": 1,
              "ignore-case": 0, "no-ignore-case": 0, "word-regexp": 0, "line-regexp": 0, "null-data": 0,
              "no-messages": 0, "invert-match": 0, "max-count": 1, "byte-offset": 0, "line-number": 0,
              "line-buffered": 0, "with-filename": 0, "no-filename": 0, "label": 1, "only-matching": 0, "quiet": 0,
              "silent": 0, "binary-files": 1, "text": 0, "directories": 1, "devices": 1, "recursive": 0,
              "dereference-recursive": 0, "include": 1, "exclude": 1, "exclude-from": 1, "exclude-dir": 1,
              "files-without-match": 0, "files-with-matches": 0, "count": 0, "initial-tab": 0, "null": 0,
              "before-context": 1, "after-context": 1, "context": 1, "group-separator": 1, "no-group-separator": 0,
              "color": 2, "colour": 2, "binary": 0}


@prog("grep", "egrep", "fgrep", "rgrep")
def grep(ctx):
    r = standard(ctx, "EFGPe:f:iywxzsvm:bnHhoqaIdD:rRLlcTUZA:B:C:0123456789", GREP_LONGS)
    if isinstance(r, int):
        return r
    opts, args = r
    art = {"egrep": "E", "fgrep": "F"}.get(ctx.prog, "G")
    if ctx.prog in ("egrep", "fgrep"):
        ctx.err(f"{ctx.prog}: warning: {ctx.prog} is obsolescent; using grep -{art}\n")
    muster, ignore, wort, ganz, inv, maxc = [], False, False, False, False, None
    nr = byte = only = quiet = still = cnt = l_ = L_ = False
    rek = ctx.prog == "rgrep"
    dateiname = None
    nach = vor = 0
    farbe = False
    inc, exc, exc_dir = [], [], []
    ziffern = ""
    for k, v in opts:
        if k in ("-E", "--extended-regexp"):
            art = "E"
        elif k in ("-F", "--fixed-strings"):
            art = "F"
        elif k in ("-G", "--basic-regexp"):
            art = "G"
        elif k in ("-P", "--perl-regexp"):
            art = "P"
        elif k in ("-e", "--regexp"):
            muster.append(v)
        elif k in ("-f", "--file"):
            t = lesbar(ctx, v, "grep")
            if t is None:
                return 2
            muster.extend(t.splitlines())
        elif k in ("-i", "-y", "--ignore-case"):
            ignore = True
        elif k == "--no-ignore-case":
            ignore = False
        elif k in ("-w", "--word-regexp"):
            wort = True
        elif k in ("-x", "--line-regexp"):
            ganz = True
        elif k in ("-v", "--invert-match"):
            inv = True
        elif k in ("-m", "--max-count"):
            maxc = int(v) if re.fullmatch(r"-?\d+", v) else None
            if maxc is None:
                ctx.err("grep: invalid max count\n")
                return 2
        elif k in ("-n", "--line-number"):
            nr = True
        elif k in ("-b", "--byte-offset"):
            byte = True
        elif k in ("-H", "--with-filename"):
            dateiname = True
        elif k in ("-h", "--no-filename"):
            dateiname = False
        elif k in ("-o", "--only-matching"):
            only = True
        elif k in ("-q", "--quiet", "--silent"):
            quiet = True
        elif k in ("-s", "--no-messages"):
            still = True
        elif k in ("-c", "--count"):
            cnt = True
        elif k in ("-l", "--files-with-matches"):
            l_ = True
        elif k in ("-L", "--files-without-match"):
            L_ = True
        elif k in ("-r", "--recursive", "-R", "--dereference-recursive"):
            rek = True
        elif k in ("-A", "--after-context"):
            nach = _ctxzahl(ctx, v)
            if nach is None:
                return 2
        elif k in ("-B", "--before-context"):
            vor = _ctxzahl(ctx, v)
            if vor is None:
                return 2
        elif k in ("-C", "--context"):
            nach = vor = _ctxzahl(ctx, v)
            if nach is None:
                return 2
        elif k in ("--color", "--colour"):
            farbe = v in (None, "always", "yes", "force") or v in ("auto", "tty", "if-tty") and ctx.isatty(1)
        elif k == "--include":
            inc.append(v)
        elif k == "--exclude":
            exc.append(v)
        elif k == "--exclude-dir":
            exc_dir.append(v)
        elif re.fullmatch(r"-\d", k):
            ziffern += k[1]
            nach = vor = int(ziffern)
    if not muster:
        if not args:
            return ctx.usage_fehler(None)
        muster = [args.pop(0)]
    teile = []
    for m in muster:
        teile.extend(m.split("\n"))
    try:
        if art == "F":
            rx = kompiliere("|".join(re.escape(m) for m in teile) if teile else "", "P", ignore, wort, ganz)
        else:
            if art == "E" or art == "P":
                verbunden = "|".join(f"(?:{posix_regex(m, True) if art == 'E' else m})" for m in teile)
            else:
                verbunden = "|".join(f"(?:{posix_regex(m, False)})" for m in teile)
            rx = kompiliere(verbunden, "P", ignore, wort, ganz)
    except (RegexFehler, re.error) as e:
        msg = str(e)
        if isinstance(e, re.error):
            msg = "Unmatched ( or \\(" if "missing )" in msg else "Unmatched [, [^, [:, [., or [=" if "unterminated character set" in msg else "Invalid regular expression"
        ctx.err(f"grep: {msg}\n")
        return 2
    leer_muster = any(m == "" for m in teile)
    if not args:
        args = ["."] if rek else ["-"]
    dateien = []
    rc_fehler = False
    for a in args:
        if a == "-":
            dateien.append(("-", None))
            continue
        try:
            n = ctx.node(a)
        except FsError as e:
            if not still:
                ctx.err(f"grep: {a}: {e.msg}\n")
            rc_fehler = True
            continue
        if n.typ == "d":
            if not rek:
                if not still:
                    ctx.err(f"grep: {a}: Is a directory\n")
                rc_fehler = True
                continue
            dateien.extend(_rek_dateien(ctx, a, n, inc, exc, exc_dir))
        else:
            if inc and not any(fnmatch.fnmatch(posixpath.basename(a), p) for p in inc) and False:
                continue
            dateien.append((a, n))
    if dateiname is None:
        dateiname = len(args) > 1 or len(dateien) > 1 or rek and not (len(args) == 1 and dateien and args[0] == dateien[0][0])
    gefunden = False
    F = {"ms": "\x1b[01;31m\x1b[K", "fn": "\x1b[35m\x1b[K", "ln": "\x1b[32m\x1b[K", "bn": "\x1b[32m\x1b[K",
         "se": "\x1b[36m\x1b[K", "e": "\x1b[m\x1b[K"}

    def f(code, text):
        return f"{F[code]}{text}{F['e']}" if farbe else text
    erste_gruppe = [True]
    for name, node in dateien:
        ctx.check()
        anzeige = "(standard input)" if name == "-" else name
        try:
            if name == "-" and ctx.stdin.isatty and not (cnt or l_ or L_ or vor or nach):
                # interaktiv zeilenweise
                text = None
            else:
                text = ctx.lesen(name)
        except FsError as e:
            if not still:
                ctx.err(f"grep: {name}: {e.msg}\n")
            rc_fehler = True
            continue
        if text is not None and "\0" in text[:4096] and not hat(opts, "-a", "--text"):
            binaer = True
        else:
            binaer = False
        treffer_n = 0
        if text is None:
            zeilen_iter = iter(lambda: ctx.stdin.readline(), "")
        else:
            zeilen_iter = iter(zeilen_von(text))
        puffer = []
        letzte_ausgabe = -10 ** 9
        nach_rest = 0
        offset = 0
        for i, z in enumerate(zeilen_iter):
            inhalt = z[:-1] if z.endswith("\n") else z
            treffer = bool(rx.search(inhalt)) or leer_muster
            if inv:
                treffer = not treffer
            if treffer and maxc is not None and treffer_n >= maxc:
                break
            if treffer:
                treffer_n += 1
                gefunden = True
                if quiet:
                    return 0
                if binaer or cnt or l_ or L_:
                    if l_ or L_:
                        break
                    continue
                if (vor or nach) and letzte_ausgabe >= 0 and i - len(puffer) > letzte_ausgabe + 1 or \
                        (vor or nach) and letzte_ausgabe < 0 and not erste_gruppe[0]:
                    ctx.out(f("se", "--") + "\n")
                erste_gruppe[0] = False
                for j, (pz, pi, po) in enumerate(puffer):
                    ctx.out(_praefix(f, dateiname, anzeige, nr, pi, byte, po, "-") + pz)
                puffer = []
                if only:
                    for m in rx.finditer(inhalt):
                        if m.group(0) == "":
                            continue
                        ctx.out(_praefix(f, dateiname, anzeige, nr, i, byte, offset + m.start(), ":") +
                                f("ms", m.group(0)) + "\n")
                else:
                    zeile = inhalt
                    if farbe and not inv:
                        zeile = rx.sub(lambda m: f("ms", m.group(0)) if m.group(0) else "", inhalt)
                    ctx.out(_praefix(f, dateiname, anzeige, nr, i, byte, offset, ":") + zeile + "\n")
                letzte_ausgabe = i
                nach_rest = nach
            else:
                if nach_rest > 0 and not (cnt or l_ or L_ or binaer):
                    ctx.out(_praefix(f, dateiname, anzeige, nr, i, byte, offset, "-") + inhalt + "\n")
                    letzte_ausgabe = i
                    nach_rest -= 1
                elif vor:
                    puffer.append((inhalt + "\n", i, offset))
                    puffer = puffer[-vor:]
            offset += len(z.encode("utf-8", "surrogateescape"))
        if cnt:
            ctx.out((f("fn", anzeige) + f("se", ":") if dateiname else "") + f"{treffer_n}\n")
        if l_ and treffer_n:
            ctx.out(f("fn", anzeige) + "\n")
        if L_ and not treffer_n:
            ctx.out(f("fn", anzeige) + "\n")
        if binaer and treffer_n and not (cnt or l_ or L_):
            ctx.out(f"grep: {anzeige}: binary file matches\n") if False else ctx.err(f"grep: {anzeige}: binary file matches\n")
    if rc_fehler and not (quiet and gefunden):
        return 2
    return 0 if gefunden else 1


def _ctxzahl(ctx, v):
    if not re.fullmatch(r"\d+", v or ""):
        ctx.err(f"grep: {v}: invalid context length argument\n")
        return None
    return int(v)


def _praefix(f, dateiname, name, nr, i, byte, off, sep):
    p = ""
    if dateiname:
        p += f("fn", name) + f("se", sep)
    if nr:
        p += f("ln", str(i + 1)) + f("se", sep)
    if byte:
        p += f("bn", str(off)) + f("se", sep)
    return p


def _rek_dateien(ctx, pfad, node, inc, exc, exc_dir):
    out = []
    for name, k in node.eintraege().items():
        p = (pfad.rstrip("/") + "/" + name) if pfad != "/" else "/" + name
        if k.typ == "d":
            if any(fnmatch.fnmatch(name, x) for x in exc_dir):
                continue
            if node.gen is not None and pfad in ("/proc", "/sys"):
                continue
            out.extend(_rek_dateien(ctx, p, k, inc, exc, exc_dir))
        elif k.typ == "-":
            if inc and not any(fnmatch.fnmatch(name, x) for x in inc):
                continue
            if any(fnmatch.fnmatch(name, x) for x in exc):
                continue
            out.append((p, k))
    return out


# ---------------------------------------------------------------------------- sed
class SedFehler(Exception):
    pass


def _sed_parse(skript, ext, nr_ausdruck=1):
    """-> Liste von Befehlen (dicts)."""
    befehle = []
    i, n = 0, len(skript)
    stapel = []

    def fehler(msg, pos=None):
        raise SedFehler(f"-e expression #{nr_ausdruck}, char {pos if pos is not None else i}: {msg}")

    def regex_lesen(start, trenner):
        j, teile = start, []
        while j < n:
            c = skript[j]
            if c == "\\" and j + 1 < n:
                if skript[j + 1] == trenner:
                    teile.append(trenner if trenner not in ".*[]^$\\+?(){}|" else "\\" + trenner)
                elif skript[j + 1] == "n":
                    teile.append("\\n")
                else:
                    teile.append(skript[j:j + 2])
                j += 2
                continue
            if c == "[":
                k = j + 1
                if k < n and skript[k] == "^":
                    k += 1
                if k < n and skript[k] == "]":
                    k += 1
                while k < n and skript[k] != "]":
                    k += 1
                teile.append(skript[j:k + 1])
                j = k + 1
                continue
            if c == trenner:
                return "".join(teile), j + 1
            teile.append(c)
            j += 1
        return None, j

    def adresse(j):
        if j < n and skript[j].isdigit():
            m = re.match(r"(\d+)(~(\d+))?", skript[j:])
            if m.group(2):
                return ("step", int(m.group(1)), int(m.group(3))), j + m.end()
            return ("zeile", int(m.group(1))), j + m.end()
        if j < n and skript[j] == "$":
            return ("letzte",), j + 1
        if j < n and (skript[j] == "/" or skript[j] == "\\"):
            tr = "/"
            if skript[j] == "\\":
                tr = skript[j + 1]
                j += 1
            rx, k = regex_lesen(j + 1, tr)
            if rx is None:
                fehler("unterminated address regex", k)
            flags = 0
            while k < n and skript[k] in "IM":
                flags |= re.I if skript[k] == "I" else re.M
                k += 1
            try:
                return ("re", re.compile(posix_regex(rx, ext) if rx else "", flags), rx == ""), k
            except (RegexFehler, re.error) as e:
                fehler(str(e), k)
        return None, j

    while i < n:
        while i < n and skript[i] in " \t\n;":
            i += 1
        if i >= n:
            break
        if skript[i] == "#":
            while i < n and skript[i] != "\n":
                i += 1
            continue
        if skript[i] == "}":
            if not stapel:
                fehler("unexpected `}'", i + 1)
            blk = stapel.pop()
            blk["ende"] = len(befehle)
            i += 1
            continue
        a1, i = adresse(i)
        a2 = None
        if a1 and i < n and skript[i] == ",":
            i += 1
            if i < n and skript[i] == "+":
                m = re.match(r"\+(\d+)", skript[i:])
                a2 = ("plus", int(m.group(1)))
                i += m.end()
            elif i < n and skript[i] == "~":
                m = re.match(r"~(\d+)", skript[i:])
                a2 = ("mult", int(m.group(1)))
                i += m.end()
            else:
                a2, i = adresse(i)
                if a2 is None:
                    fehler("unexpected `,'", i)
        while i < n and skript[i] in " \t":
            i += 1
        neg = False
        if i < n and skript[i] == "!":
            neg = True
            i += 1
            while i < n and skript[i] in " \t":
                i += 1
        if i >= n:
            fehler("missing command", i)
        c = skript[i]
        i += 1
        cmd = {"c": c, "a1": a1, "a2": a2, "neg": neg, "aktiv": False}
        if c == "{":
            stapel.append(cmd)
        elif c in "dDgGhHnNpPxz=lFqQ":
            if c in "qQ":
                m = re.match(r"\s*(\d+)", skript[i:])
                if m:
                    cmd["code"] = int(m.group(1))
                    i += m.end()
        elif c in "aic":
            if i < n and skript[i] == "\\":
                i += 1
                if i < n and skript[i] == "\n":
                    i += 1
            else:
                while i < n and skript[i] in " \t":
                    i += 1
            j = i
            text = []
            while j < n and skript[j] != "\n":
                if skript[j] == "\\" and j + 1 < n:
                    text.append(skript[j + 1] if skript[j + 1] != "n" else "\n")
                    j += 2
                    continue
                text.append(skript[j])
                j += 1
            cmd["text"] = "".join(text)
            i = j
            if not cmd["text"] and c != "c" and False:
                fehler("expected \\ after `a', `c' or `i'", i)
        elif c == "s":
            if i >= n:
                fehler("unterminated `s' command", i)
            tr = skript[i]
            rx, j = regex_lesen(i + 1, tr)
            if rx is None:
                fehler("unterminated `s' command", j)
            k = j
            ers = []
            while k < n and skript[k] != tr:
                if skript[k] == "\\" and k + 1 < n:
                    ers.append(skript[k:k + 2])
                    k += 2
                    continue
                if skript[k] == "\n":
                    fehler("unterminated `s' command", k)
                ers.append(skript[k])
                k += 1
            if k >= n:
                fehler("unterminated `s' command", k)
            k += 1
            flags = ""
            while k < n and skript[k] not in ";\n}":
                if skript[k] == "w":
                    cmd["wdatei"] = skript[k + 1:].strip().split("\n")[0]
                    k = n
                    break
                if skript[k] in " \t":
                    k += 1
                    continue
                if skript[k] not in "gpiImMe0123456789":
                    fehler("unknown option to `s'", k + 1)
                flags += skript[k]
                k += 1
            i = k
            try:
                cmd["rx"] = re.compile(posix_regex(rx, ext) if rx else "", re.I if ("i" in flags or "I" in flags) else 0)
            except (RegexFehler, re.error) as e:
                fehler(str(e), j)
            cmd["leer_rx"] = rx == ""
            cmd["ers"] = "".join(ers)
            cmd["flags"] = flags
        elif c == "y":
            tr = skript[i]
            teile = skript[i + 1:].split(tr)
            if len(teile) < 3:
                fehler("unterminated `y' command", i)
            a, b = teile[0], teile[1]
            if len(a) != len(b):
                fehler("strings for `y' command are different lengths", i)
            cmd["tab"] = str.maketrans(a, b)
            i = i + 1 + len(a) + 1 + len(b) + 1
        elif c in "rRwW":
            j = skript.find("\n", i)
            j = n if j < 0 else j
            cmd["datei"] = skript[i:j].strip()
            i = j
        elif c in "bt T":
            j = i
            while j < n and skript[j] not in ";\n}":
                j += 1
            cmd["label"] = skript[i:j].strip()
            i = j
        elif c == ":":
            j = i
            while j < n and skript[j] not in "\n;":
                j += 1
            cmd["label"] = skript[i:j].strip()
            i = j
        else:
            fehler(f"unknown command: `{c}'", i)
        befehle.append(cmd)
    if stapel:
        fehler("unmatched `{'", 0)
    return befehle


def _sed_ers(ctx_ers, m):
    out, i = [], 0
    modus = None
    einmal = None
    e = ctx_ers

    def anwenden(t):
        nonlocal einmal
        if einmal == "u" and t:
            t, einmal = t[0].upper() + t[1:], None
        elif einmal == "l" and t:
            t, einmal = t[0].lower() + t[1:], None
        if modus == "U":
            t = t.upper()
        elif modus == "L":
            t = t.lower()
        return t
    while i < len(e):
        c = e[i]
        if c == "\\" and i + 1 < len(e):
            d = e[i + 1]
            i += 2
            if d.isdigit():
                out.append(anwenden(m.group(int(d)) or "" if int(d) <= (m.re.groups or 0) else ""))
            elif d == "n":
                out.append("\n")
            elif d == "t":
                out.append("\t")
            elif d == "&":
                out.append("&")
            elif d in "ULE":
                modus = None if d == "E" else d
            elif d in "ul":
                einmal = d
            else:
                out.append(d)
            continue
        if c == "&":
            out.append(anwenden(m.group(0)))
            i += 1
            continue
        out.append(anwenden(c))
        i += 1
    return "".join(out)


@prog("sed")
def sed(ctx):
    r = standard(ctx, "nrsEe:f:i::l:uz", {"quiet": 0, "silent": 0, "expression": 1, "file": 1, "in-place": 2,
                                          "line-length": 1, "regexp-extended": 0, "separate": 0, "unbuffered": 0,
                                          "null-data": 0, "posix": 0, "debug": 0, "sandbox": 0,
                                          "follow-symlinks": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    still = hat(opts, "-n", "--quiet", "--silent")
    ext = hat(opts, "-r", "-E", "--regexp-extended")
    inplace = any(o in ("-i", "--in-place") for o, _ in opts)
    skripte = [v for o, v in opts if o in ("-e", "--expression")]
    for o, v in opts:
        if o in ("-f", "--file"):
            t = lesbar(ctx, v, "sed")
            if t is None:
                return 1
            skripte.append(t)
    if not skripte:
        if not args:
            return ctx.usage_fehler(None)
        skripte = [args.pop(0)]
    try:
        befehle = []
        for k, s in enumerate(skripte, 1):
            befehle.extend(_sed_parse(s, ext, k))
    except SedFehler as e:
        ctx.err(f"sed: {e}\n")
        return 1
    labels = {c["label"]: idx for idx, c in enumerate(befehle) if c["c"] == ":"}
    for c in befehle:
        if c["c"] in "btT" and c.get("label") and c["label"] not in labels:
            ctx.err(f"sed: can't find label for jump to `{c['label']}'\n")
            return 1
    eingaben = args or ["-"]
    if inplace and not args:
        ctx.err("sed: no input files\n")
        return 1
    rc = 0
    gruppen = [[a] for a in eingaben] if inplace else [eingaben]
    for gruppe in gruppen:
        zeilen = []
        for a in gruppe:
            try:
                t = ctx.lesen(a)
            except FsError as e:
                ctx.err(f"sed: can't read {a}: {e.msg}\n")
                rc = 2
                continue
            zeilen.extend(zeilen_von(t))
        out = []
        try:
            q = _sed_lauf(ctx, befehle, labels, zeilen, still, out)
        except SedFehler as e:
            ctx.err(f"sed: {e}\n")
            return 4
        text = "".join(out)
        if inplace:
            try:
                ctx.fs.schreiben(gruppe[0], text, ctx.cwd)
            except FsError as e:
                ctx.err(f"sed: couldn't open file {gruppe[0]}: {e.msg}\n")
                rc = 4
        else:
            ctx.out(text)
        if q is not None:
            return q
    return rc


def _sed_lauf(ctx, befehle, labels, zeilen, still, out):
    halte = ""
    n_ges = len(zeilen)
    idx = 0
    zeilennr = 0
    for c in befehle:
        c["aktiv"] = False
        c["ende_nr"] = None
    while idx < n_ges:
        ctx.check()
        roh = zeilen[idx]
        idx += 1
        zeilennr += 1
        hatte_nl = roh.endswith("\n")
        ps = roh[:-1] if hatte_nl else roh
        anhang = []
        ersetzt = False
        loeschen = False
        pc = 0
        ende_code = None
        neustart = False
        while pc < len(befehle):
            c = befehle[pc]
            if not _sed_passt(c, ps, zeilennr, idx >= n_ges):
                if c["c"] == "{":
                    pc = c["ende"]
                    continue
                pc += 1
                continue
            k = c["c"]
            pc += 1
            if k == "{" or k == ":":
                continue
            if k == "s":
                rx = c["rx"]
                flags = c["flags"]
                nur = int(re.sub(r"\D", "", flags) or 0)
                zaehler = 0

                def ers(m):
                    nonlocal zaehler
                    zaehler += 1
                    if "g" in flags:
                        if nur and zaehler < nur:
                            return m.group(0)
                        return _sed_ers(c["ers"], m)
                    if nur:
                        return _sed_ers(c["ers"], m) if zaehler == nur else m.group(0)
                    return _sed_ers(c["ers"], m) if zaehler == 1 else m.group(0)
                neu = rx.sub(ers, ps)
                if neu != ps or (zaehler and ("g" in flags or zaehler >= max(nur, 1))):
                    if zaehler >= max(nur, 1):
                        ersetzt = True
                        ps = neu
                        if "p" in flags:
                            out.append(ps + "\n")
                        if c.get("wdatei"):
                            try:
                                ctx.fs.schreiben(c["wdatei"], ps + "\n", ctx.cwd, anhaengen=True)
                            except FsError:
                                pass
            elif k == "d":
                loeschen = True
                break
            elif k == "D":
                if "\n" in ps:
                    ps = ps.split("\n", 1)[1]
                    neustart = True
                    break
                loeschen = True
                break
            elif k == "p":
                out.append(ps + "\n")
            elif k == "P":
                out.append(ps.split("\n")[0] + "\n")
            elif k == "=":
                out.append(f"{zeilennr}\n")
            elif k == "a":
                anhang.append(c["text"] + "\n")
            elif k == "i":
                out.append(c["text"] + "\n")
            elif k == "c":
                if c["a2"] is None or not c["aktiv"]:
                    out.append(c["text"] + "\n")
                loeschen = True
                break
            elif k == "y":
                ps = ps.translate(c["tab"])
            elif k == "h":
                halte = ps
            elif k == "H":
                halte = halte + "\n" + ps
            elif k == "g":
                ps = halte
            elif k == "G":
                ps = ps + "\n" + halte
            elif k == "x":
                ps, halte = halte, ps
            elif k == "n":
                if not still:
                    out.append(ps + "\n")
                if idx >= n_ges:
                    loeschen = True
                    break
                roh = zeilen[idx]
                idx += 1
                zeilennr += 1
                hatte_nl = roh.endswith("\n")
                ps = roh[:-1] if hatte_nl else roh
            elif k == "N":
                if idx >= n_ges:
                    break
                roh = zeilen[idx]
                idx += 1
                zeilennr += 1
                hatte_nl = roh.endswith("\n")
                ps = ps + "\n" + (roh[:-1] if hatte_nl else roh)
            elif k == "z":
                ps = ""
            elif k == "l":
                out.append(ps.encode("unicode_escape").decode().replace("\\\\", "\\") + "$\n")
            elif k == "F":
                out.append("-\n")
            elif k == "r":
                try:
                    anhang.append(ctx.lesen(c["datei"]))
                except FsError:
                    pass
            elif k == "w":
                try:
                    ctx.fs.schreiben(c["datei"], ps + "\n", ctx.cwd, anhaengen=True)
                except FsError:
                    pass
            elif k in "qQ":
                ende_code = c.get("code", 0)
                if k == "Q":
                    loeschen = True
                break
            elif k == "b":
                pc = labels[c["label"]] if c.get("label") else len(befehle)
            elif k == "t":
                if ersetzt:
                    ersetzt = False
                    pc = labels[c["label"]] if c.get("label") else len(befehle)
            elif k == "T":
                if not ersetzt:
                    pc = labels[c["label"]] if c.get("label") else len(befehle)
                else:
                    ersetzt = False
        if not loeschen and not still:
            out.append(ps + ("\n" if hatte_nl or anhang else ""))
        out.extend(anhang)
        if ende_code is not None:
            return ende_code
        if neustart:
            zeilen.insert(idx, ps + "\n")
            n_ges += 1
            zeilennr -= 1
    return None


def _sed_adr(a, ps, nr, letzte):
    if a[0] == "zeile":
        return nr == a[1]
    if a[0] == "letzte":
        return letzte
    if a[0] == "step":
        return nr >= a[1] and (a[2] == 0 and nr == a[1] or a[2] and (nr - a[1]) % a[2] == 0)
    if a[0] == "re":
        return bool(a[1].search(ps))
    return False


def _sed_passt(c, ps, nr, letzte):
    a1, a2 = c["a1"], c["a2"]
    if a1 is None:
        ok = True
    elif a2 is None:
        ok = _sed_adr(a1, ps, nr, letzte)
    else:
        if not c["aktiv"]:
            if _sed_adr(a1, ps, nr, letzte):
                c["aktiv"] = True
                c["start"] = nr
                if a2[0] == "zeile" and a2[1] <= nr:
                    c["aktiv"] = False
                elif a2[0] == "plus" and a2[1] == 0:
                    c["aktiv"] = False
                ok = True
            else:
                ok = False
        else:
            ok = True
            if a2[0] == "zeile":
                if nr >= a2[1]:
                    c["aktiv"] = False
            elif a2[0] == "plus":
                if nr >= c["start"] + a2[1]:
                    c["aktiv"] = False
            elif a2[0] == "mult":
                if a2[1] and nr % a2[1] == 0:
                    c["aktiv"] = False
            elif _sed_adr(a2, ps, nr, letzte):
                c["aktiv"] = False
    return ok != c["neg"]


# ---------------------------------------------------------------------------- sort
def _sort_keyspec(spec):
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?([bdfgiMhnRrV]*)(?:,(\d+)(?:\.(\d+))?([bdfgiMhnRrV]*))?", spec)
    if not m:
        return None
    return {"f1": int(m.group(1)), "c1": int(m.group(2) or 1), "o1": m.group(3) or "",
            "f2": int(m.group(4)) if m.group(4) else None, "c2": int(m.group(5) or 0), "o2": m.group(6) or ""}


def _num_prefix(s):
    m = re.match(r"\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", s)
    return float(m.group(1)) if m else 0.0


def _human_wert(s):
    m = re.match(r"\s*([-+]?\d*\.?\d+)\s*([KMGTPEkmgtpe]?)", s)
    if not m:
        return 0.0
    return float(m.group(1)) * 1024 ** " KMGTPE".index(m.group(2).upper() or " ")


@prog("sort")
def sort(ctx):
    r = standard(ctx, "bcCdfghik:mMno:rRsS:t:T:uVz", {"ignore-leading-blanks": 0, "dictionary-order": 0,
                                                      "ignore-case": 0, "general-numeric-sort": 0,
                                                      "ignore-nonprinting": 0, "month-sort": 0,
                                                      "human-numeric-sort": 0, "numeric-sort": 0,
                                                      "random-sort": 0, "reverse": 0, "sort": 1,
                                                      "version-sort": 0, "check": 2, "key": 1, "merge": 0,
                                                      "output": 1, "stable": 0, "buffer-size": 1,
                                                      "field-separator": 1, "temporary-directory": 1,
                                                      "unique": 0, "zero-terminated": 0, "parallel": 1,
                                                      "random-source": 1, "debug": 0, "batch-size": 1,
                                                      "compress-program": 1, "files0-from": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    glob = ""
    keys = []
    sep = None
    uniq = rev = False
    ausgabe = None
    check = False
    for k, v in opts:
        if k in ("-n", "--numeric-sort"):
            glob += "n"
        elif k in ("-g", "--general-numeric-sort"):
            glob += "g"
        elif k in ("-h", "--human-numeric-sort"):
            glob += "h"
        elif k in ("-V", "--version-sort"):
            glob += "V"
        elif k in ("-M", "--month-sort"):
            glob += "M"
        elif k in ("-f", "--ignore-case"):
            glob += "f"
        elif k in ("-b", "--ignore-leading-blanks"):
            glob += "b"
        elif k in ("-d", "--dictionary-order"):
            glob += "d"
        elif k in ("-R", "--random-sort"):
            glob += "R"
        elif k in ("-r", "--reverse"):
            rev = True
        elif k in ("-u", "--unique"):
            uniq = True
        elif k in ("-k", "--key"):
            ks = _sort_keyspec(v)
            if ks is None:
                ctx.err(f"sort: invalid number at field start: invalid count at start of {ql(v)}\n")
                return 2
            keys.append(ks)
        elif k in ("-t", "--field-separator"):
            if len(v) != 1:
                ctx.err(f"sort: multi-character tab {quote_name(v)}\n")
                return 2
            sep = v
        elif k in ("-o", "--output"):
            ausgabe = v
        elif k in ("-c", "-C", "--check"):
            check = True
    zeilen = []
    for a in args or ["-"]:
        try:
            t = ctx.lesen(a)
        except FsError as e:
            ctx.err(f"sort: cannot read: {a}: {e.msg}\n")
            return 2
        zeilen.extend(z[:-1] if z.endswith("\n") else z for z in zeilen_von(t))
    monate = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                          "NOV", "DEC"], 1)}

    def felder(z):
        if sep is not None:
            return z.split(sep)
        return re.findall(r"\s*\S+", z) or [""]

    def teil(z, ks):
        f = felder(z)
        a = ks["f1"] - 1
        if a >= len(f):
            return ""
        s = f[a]
        if "b" in ks["o1"] + glob:
            s = s.lstrip()
        s = s[ks["c1"] - 1:]
        if ks["f2"] is None:
            rest = f[a + 1:]
            return s + ((sep or "").join(rest) if sep is not None else "".join(rest))
        e = ks["f2"] - 1
        if e == a:
            return s[:ks["c2"] - ks["c1"] + 1] if ks["c2"] else s
        mitte = f[a + 1:e]
        letzt = f[e] if e < len(f) else ""
        if ks["c2"]:
            letzt = letzt[:ks["c2"]]
        j = sep if sep is not None else ""
        return j.join([s] + mitte + ([letzt] if e < len(f) else []))

    def wertung(s, o):
        if "b" in o:
            s = s.lstrip()
        if "d" in o:
            s = re.sub(r"[^\w\s]", "", s)
        if "f" in o:
            s = s.upper()
        if "n" in o:
            return (0, _num_prefix(s), "")
        if "g" in o:
            try:
                return (0, float(s.strip() or "nan"), "")
            except ValueError:
                return (-1, 0.0, "")
        if "h" in o:
            return (0, _human_wert(s), "")
        if "M" in o:
            return (0, monate.get(s.strip()[:3].upper(), 0), "")
        if "V" in o:
            return (0, 0, [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)])
        return (0, 0, s.encode("utf-8", "surrogateescape"))

    import functools
    import random

    def vergleich(x, y):
        if keys:
            for ks in keys:
                o = ks["o1"] + ks["o2"] or glob
                a, b = wertung(teil(x, ks), o), wertung(teil(y, ks), o)
                if "R" in o:
                    a, b = hash(a), hash(b)
                if a != b:
                    res = -1 if a < b else 1
                    return -res if ("r" in ks["o1"] + ks["o2"]) != rev and "r" in (ks["o1"] + ks["o2"]) else (
                        -res if rev and "r" not in (ks["o1"] + ks["o2"]) else res)
            if uniq or hat(opts, "-s", "--stable"):
                return 0
        else:
            a, b = wertung(x, glob), wertung(y, glob)
            if a != b:
                res = -1 if a < b else 1
                return -res if rev else res
            if uniq or hat(opts, "-s", "--stable"):
                return 0
        if glob and "f" not in glob and not keys and ("n" in glob or "h" in glob or "g" in glob or "M" in glob or "V" in glob):
            pass
        bx, by = x.encode("utf-8", "surrogateescape"), y.encode("utf-8", "surrogateescape")
        if bx == by:
            return 0
        res = -1 if bx < by else 1
        return -res if rev else res
    if check:
        for i in range(1, len(zeilen)):
            if vergleich(zeilen[i - 1], zeilen[i]) > 0:
                if not hat(opts, "-C"):
                    ctx.err(f"sort: {args[0] if args else '-'}:{i + 1}: disorder: {zeilen[i]}\n")
                return 1
        return 0
    if "R" in glob and not keys:
        random.shuffle(zeilen)
        erg = zeilen
    else:
        erg = sorted(zeilen, key=functools.cmp_to_key(vergleich))
    if uniq:
        u = []
        for z in erg:
            if not u or vergleich(u[-1], z) != 0:
                u.append(z)
        erg = u
    text = "".join(z + "\n" for z in erg)
    if ausgabe:
        try:
            ctx.fs.schreiben(ausgabe, text, ctx.cwd)
        except FsError as e:
            ctx.err(f"sort: open failed: {ausgabe}: {e.msg}\n")
            return 2
        return 0
    ctx.out(text)
    return 0


@prog("uniq")
def uniq(ctx):
    r = standard(ctx, "cdDf:is:uw:z", {"count": 0, "repeated": 0, "all-repeated": 2, "skip-fields": 1,
                                        "group": 2, "ignore-case": 0, "skip-chars": 1, "unique": 0,
                                        "check-chars": 1, "zero-terminated": 0}, zahl_option=False)
    if isinstance(r, int):
        return r
    opts, args = r
    count = hat(opts, "-c", "--count")
    nur_d = hat(opts, "-d", "--repeated")
    alle_d = hat(opts, "-D", "--all-repeated")
    nur_u = hat(opts, "-u", "--unique")
    ign = hat(opts, "-i", "--ignore-case")
    sf = int(wert(opts, "-f", "--skip-fields", std="0"))
    sc = int(wert(opts, "-s", "--skip-chars", std="0"))
    wc = wert(opts, "-w", "--check-chars")
    if len(args) > 2:
        return ctx.usage_fehler(f"extra operand {quote_name(args[2])}")
    t = lesbar(ctx, args[0] if args else "-", "uniq")
    if t is None:
        return 1

    def schluessel(z):
        z = z.rstrip("\n")
        if sf:
            z = re.sub(r"^(\s*\S+){%d}" % sf, "", z)
        z = z[sc:]
        if wc is not None:
            z = z[:int(wc)]
        return z.lower() if ign else z
    gruppen = []
    for z in zeilen_von(t):
        if not z.endswith("\n"):
            z += "\n"
        if gruppen and schluessel(gruppen[-1][0]) == schluessel(z):
            gruppen[-1].append(z)
        else:
            gruppen.append([z])
    out = []
    for g in gruppen:
        if nur_d and len(g) < 2 or nur_u and len(g) > 1 or alle_d and len(g) < 2:
            continue
        if alle_d:
            out.extend(g)
            continue
        out.append((f"{len(g):7d} " if count else "") + g[0])
    text = "".join(out)
    if len(args) == 2:
        ctx.fs.schreiben(args[1], text, ctx.cwd)
    else:
        ctx.out(text)
    return 0


def _liste_parse(spec):
    teile = []
    for t in spec.split(","):
        m = re.fullmatch(r"(\d*)(-?)(\d*)", t)
        if not m or t == "" or t == "-":
            return None
        a = int(m.group(1)) if m.group(1) else 1
        if m.group(2):
            b = int(m.group(3)) if m.group(3) else 10 ** 9
        else:
            b = a
        if a == 0:
            return "0"
        teile.append((a, b))
    return teile


@prog("cut")
def cut(ctx):
    r = standard(ctx, "b:c:d:f:nsz", {"bytes": 1, "characters": 1, "delimiter": 1, "fields": 1, "complement": 0,
                                       "only-delimited": 0, "output-delimiter": 1, "zero-terminated": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    b = wert(opts, "-b", "--bytes")
    c = wert(opts, "-c", "--characters")
    f = wert(opts, "-f", "--fields")
    d = wert(opts, "-d", "--delimiter")
    anz = sum(x is not None for x in (b, c, f))
    if anz == 0:
        return ctx.usage_fehler("you must specify a list of bytes, characters, or fields")
    if anz > 1:
        return ctx.usage_fehler("only one list may be specified")
    if d is not None and f is None:
        return ctx.usage_fehler("an input delimiter may be specified only when operating on fields")
    if d is not None and len(d) != 1:
        return ctx.usage_fehler("the delimiter must be a single character")
    spec = b or c or f
    liste = _liste_parse(spec)
    if liste == "0":
        return ctx.usage_fehler("fields and positions are numbered from 1")
    if liste is None:
        art = "field" if f else "byte/character"
        return ctx.usage_fehler(f"invalid {art} range" if "-" in spec else f"invalid {art} value {ql(spec)}")
    d = d if d is not None else "\t"
    od = wert(opts, "--output-delimiter", std=d if f else "")
    komp = hat(opts, "--complement")
    nur_d = hat(opts, "-s", "--only-delimited")

    def drin(i):
        x = any(a <= i <= e for a, e in liste)
        return not x if komp else x
    rc = 0
    for a in args or ["-"]:
        t = lesbar(ctx, a, "cut")
        if t is None:
            rc = 1
            continue
        out = []
        for z in zeilen_von(t):
            inhalt = z[:-1] if z.endswith("\n") else z
            if f:
                if d not in inhalt:
                    if not nur_d:
                        out.append(inhalt + "\n")
                    continue
                fs = inhalt.split(d)
                out.append(od.join(x for i, x in enumerate(fs, 1) if drin(i)) + "\n")
            else:
                if od:
                    # Bereiche zusammenhaengend mit Ausgabetrenner
                    teile, akt = [], ""
                    for i, ch in enumerate(inhalt, 1):
                        if drin(i):
                            akt += ch
                        elif akt:
                            teile.append(akt)
                            akt = ""
                    if akt:
                        teile.append(akt)
                    out.append(od.join(teile) + "\n")
                else:
                    out.append("".join(ch for i, ch in enumerate(inhalt, 1) if drin(i)) + "\n")
        ctx.out("".join(out))
    return rc


def _tr_set(s):
    klassen = {"alnum": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
               "alpha": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
               "digit": "0123456789", "lower": "abcdefghijklmnopqrstuvwxyz", "upper": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               "space": " \t\n\r\f\v", "blank": " \t", "punct": "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
               "xdigit": "0123456789ABCDEFabcdef", "cntrl": "".join(chr(i) for i in range(32)) + "\x7f",
               "print": "".join(chr(i) for i in range(32, 127)), "graph": "".join(chr(i) for i in range(33, 127))}
    out, i = [], 0
    esc = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
    while i < len(s):
        if s.startswith("[:", i):
            e = s.find(":]", i)
            if e > 0:
                k = s[i + 2:e]
                if k not in klassen:
                    raise ProgFehler(f"invalid character class {quote_name(k)}")
                out.extend(klassen[k])
                i = e + 2
                continue
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            m = re.match(r"[0-7]{1,3}", s[i + 1:])
            if m:
                c = chr(int(m.group(), 8))
                i += 1 + m.end()
            else:
                c = esc.get(s[i + 1], s[i + 1])
                i += 2
        else:
            i += 1
        if i < len(s) - 1 and s[i] == "-":
            e = s[i + 1]
            j = i + 2
            if e == "\\" and i + 2 < len(s):
                e = esc.get(s[i + 2], s[i + 2])
                j = i + 3
            if ord(e) < ord(c):
                raise ProgFehler(f"range-endpoints of '{c}-{e}' are in reverse collating sequence order")
            out.extend(chr(x) for x in range(ord(c), ord(e) + 1))
            i = j
            continue
        out.append(c)
    return out


@prog("tr")
def tr(ctx):
    r = standard(ctx, "cCdst", {"complement": 0, "delete": 0, "squeeze-repeats": 0, "truncate-set1": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    d = hat(opts, "-d", "--delete")
    s = hat(opts, "-s", "--squeeze-repeats")
    c = hat(opts, "-c", "-C", "--complement")
    if not args:
        return ctx.usage_fehler("missing operand")
    if d and not s and len(args) > 1:
        return ctx.usage_fehler(f"extra operand {quote_name(args[1])}\nOnly one string may be given when deleting "
                                "without squeezing repeats.".replace("\n", "\n"))
    if not d and not s and len(args) == 1:
        return ctx.usage_fehler(f"missing operand after {ql(args[0])}\nTwo strings must be given when "
                                "translating.")
    if len(args) > 2:
        return ctx.usage_fehler(f"extra operand {quote_name(args[2])}")
    try:
        s1 = _tr_set(args[0])
        s2 = _tr_set(args[1]) if len(args) > 1 else []
    except ProgFehler as e:
        ctx.err(f"tr: {e.msg}\n")
        return 1
    text = ctx.stdin.read()
    if c:
        menge = set(s1)
        alle = sorted({ch for ch in text if ch not in menge})
        s1 = alle
    if d:
        menge = set(s1)
        text = "".join(ch for ch in text if ch not in menge)
        if s and s2:
            text = _squeeze(text, set(s2))
    elif s and not s2:
        text = _squeeze(text, set(s1))
    else:
        if s2:
            s2 = s2 + [s2[-1]] * (len(s1) - len(s2)) if not hat(opts, "-t", "--truncate-set1") else s2
            tab = {}
            for a, b in zip(s1, s2):
                tab[a] = b
            text = "".join(tab.get(ch, ch) for ch in text)
        if s:
            text = _squeeze(text, set(s2))
    ctx.out(text)
    return 0


def _squeeze(text, menge):
    out = []
    for ch in text:
        if out and ch == out[-1] and ch in menge:
            continue
        out.append(ch)
    return "".join(out)


@prog("paste")
def paste(ctx):
    r = standard(ctx, "d:sz", {"delimiters": 1, "serial": 0, "zero-terminated": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    d = wert(opts, "-d", "--delimiters", std="\t").replace("\\t", "\t").replace("\\n", "\n") or "\0"
    inhalte = []
    stdin_text = None
    for a in args or ["-"]:
        if a == "-":
            if stdin_text is None:
                stdin_text = ctx.stdin.read()
            inhalte.append([z.rstrip("\n") for z in zeilen_von(stdin_text)])
            continue
        t = lesbar(ctx, a, "paste")
        if t is None:
            return 1
        inhalte.append([z.rstrip("\n") for z in zeilen_von(t)])
    out = []
    if hat(opts, "-s", "--serial"):
        for z in inhalte:
            teile = []
            for i, x in enumerate(z):
                teile.append(x)
                if i < len(z) - 1:
                    teile.append(d[i % len(d)])
            out.append("".join(teile) + "\n")
    else:
        n = max((len(z) for z in inhalte), default=0)
        for i in range(n):
            teile = []
            for k, z in enumerate(inhalte):
                teile.append(z[i] if i < len(z) else "")
                if k < len(inhalte) - 1:
                    teile.append(d[k % len(d)])
            out.append("".join(teile) + "\n")
    ctx.out("".join(out))
    return 0


@prog("comm")
def comm(ctx):
    r = standard(ctx, "123z", {"check-order": 0, "nocheck-order": 0, "output-delimiter": 1, "total": 0,
                                "zero-terminated": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if len(args) < 2:
        return ctx.usage_fehler("missing operand" if not args else f"missing operand after {quote_name(args[0])}")
    a = lesbar(ctx, args[0], "comm")
    b = lesbar(ctx, args[1], "comm")
    if a is None or b is None:
        return 1
    la, lb = [z.rstrip("\n") for z in zeilen_von(a)], [z.rstrip("\n") for z in zeilen_von(b)]
    weg = {k[1] for k, _ in opts if k in ("-1", "-2", "-3")}
    out = []
    i = j = 0
    while i < len(la) or j < len(lb):
        if j >= len(lb) or i < len(la) and la[i] < lb[j]:
            spalte, text = 1, la[i]
            i += 1
        elif i >= len(la) or lb[j] < la[i]:
            spalte, text = 2, lb[j]
            j += 1
        else:
            spalte, text = 3, la[i]
            i += 1
            j += 1
        if str(spalte) in weg:
            continue
        einr = sum(1 for s in (1, 2) if s < spalte and str(s) not in weg)
        out.append("\t" * einr + text + "\n")
    ctx.out("".join(out))
    return 0


@prog("diff")
def diff(ctx):
    r = standard(ctx, "abBcC:dEiqrsuU:wy", {"brief": 0, "unified": 2, "context": 2, "ignore-case": 0,
                                            "ignore-all-space": 0, "ignore-space-change": 0, "recursive": 0,
                                            "report-identical-files": 0, "side-by-side": 0, "text": 0,
                                            "new-file": 0, "color": 2, "ignore-blank-lines": 0, "normal": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    if len(args) < 2:
        ctx.err(f"diff: missing operand after {qa(args[0])}\ndiff: Try 'diff --help' for more information.\n"
                if args else "diff: missing operand\ndiff: Try 'diff --help' for more information.\n")
        return 2
    if len(args) > 2:
        ctx.err(f"diff: extra operand {quote_name(args[2])}\ndiff: Try 'diff --help' for more information.\n")
        return 2
    texte = []
    for a in args:
        try:
            texte.append(ctx.lesen(a))
        except FsError as e:
            ctx.err(f"diff: {a}: {e.msg}\n")
            return 2
    a, b = zeilen_von(texte[0]), zeilen_von(texte[1])
    ign = hat(opts, "-i", "--ignore-case")
    ws = hat(opts, "-w", "--ignore-all-space")
    norm = (lambda z: (re.sub(r"\s+", "", z) if ws else z).lower() if ign else (re.sub(r"\s+", "", z) if ws else z))
    na, nb = [norm(x) for x in a], [norm(x) for x in b]
    if na == nb:
        if hat(opts, "-s", "--report-identical-files"):
            ctx.out(f"Files {args[0]} and {args[1]} are identical\n")
        return 0
    if hat(opts, "-q", "--brief"):
        ctx.out(f"Files {args[0]} and {args[1]} differ\n")
        return 1
    uni = None
    for k, v in opts:
        if k in ("-u", "--unified"):
            uni = int(v) if v else 3
        elif k == "-U":
            uni = int(v)
    if uni is not None:
        def mt(p):
            try:
                n = ctx.node(p)
                return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(n.mtime)) + f".{int((n.mtime % 1) * 1e9):09d} +0000"
            except FsError:
                return ""
        zeilen = list(difflib.unified_diff(a, b, f"{args[0]}\t{mt(args[0])}", f"{args[1]}\t{mt(args[1])}", n=uni))
        out = []
        for z in zeilen:
            if not z.endswith("\n"):
                z += "\n\\ No newline at end of file\n"
            out.append(z.replace("--- ", "--- ", 1))
        ctx.out("".join(out).replace("\t\n", "\n"))
        return 1
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    out = []

    def rng(i, j):
        return f"{i + 1}" if j - i == 1 else f"{i + 1},{j}"
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            out.append(f"{rng(i1, i2)}c{rng(j1, j2)}\n")
            out.extend("< " + x if x.endswith("\n") else "< " + x + "\n\\ No newline at end of file\n" for x in a[i1:i2])
            out.append("---\n")
            out.extend("> " + x if x.endswith("\n") else "> " + x + "\n\\ No newline at end of file\n" for x in b[j1:j2])
        elif tag == "delete":
            out.append(f"{rng(i1, i2)}d{j1}\n")
            out.extend("< " + x if x.endswith("\n") else "< " + x + "\n\\ No newline at end of file\n" for x in a[i1:i2])
        elif tag == "insert":
            out.append(f"{i1}a{rng(j1, j2)}\n")
            out.extend("> " + x if x.endswith("\n") else "> " + x + "\n\\ No newline at end of file\n" for x in b[j1:j2])
    ctx.out("".join(out))
    return 1


@prog("cmp")
def cmp(ctx):
    r = standard(ctx, "bilsn:", {"print-bytes": 0, "ignore-initial": 1, "verbose": 0, "quiet": 0, "silent": 0,
                                  "bytes": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    if len(args) < 2:
        ctx.err("cmp: missing operand after " + (quote_name(args[0]) if args else "'cmp'") +
                "\ncmp: Try 'cmp --help' for more information.\n")
        return 2
    texte = []
    for a in args[:2]:
        try:
            texte.append(ctx.lesen(a).encode("utf-8", "surrogateescape"))
        except FsError as e:
            ctx.err(f"cmp: {a}: {e.msg}\n")
            return 2
    x, y = texte
    if x == y:
        return 0
    if hat(opts, "-s", "--quiet", "--silent"):
        return 1
    for i in range(min(len(x), len(y))):
        if x[i] != y[i]:
            line = x[:i].count(b"\n") + 1
            ctx.out(f"{args[0]} {args[1]} differ: byte {i + 1}, line {line}\n")
            return 1
    kurz = args[0] if len(x) < len(y) else args[1]
    ctx.err(f"cmp: EOF on {kurz} after byte {min(len(x), len(y))}, line {min(x, y, key=len).count(b'\n')}\n"
            if min(len(x), len(y)) else f"cmp: EOF on {kurz} which is empty\n")
    return 1


@prog("xargs")
def xargs(ctx):
    roh = ctx.argv[1:]
    try:
        opts, args = ctx.getopt("+0a:d:E:e::I:i::L:l::n:oP:prs:tx", {
            "null": 0, "arg-file": 1, "delimiter": 1, "eof": 2, "replace": 2, "max-lines": 2, "max-args": 1,
            "open-tty": 0, "max-procs": 1, "interactive": 0, "process-slot-var": 1, "no-run-if-empty": 0,
            "max-chars": 1, "show-limits": 0, "verbose": 0, "exit": 0, "help": 0, "version": 0}, argv=roh)
    except Usage as u:
        return ctx.usage_fehler(u.msg)
    if hat(opts, "--help"):
        return ctx.hilfe()
    if hat(opts, "--version"):
        return ctx.version()
    befehl = args or ["echo"]
    if hat(opts, "-a", "--arg-file"):
        t = lesbar(ctx, wert(opts, "-a", "--arg-file"), "xargs")
        if t is None:
            return 1
    else:
        t = ctx.stdin.read()
    if hat(opts, "-0", "--null"):
        teile = [x for x in t.split("\0") if x != ""]
    elif hat(opts, "-d", "--delimiter"):
        d = wert(opts, "-d", "--delimiter").replace("\\n", "\n").replace("\\t", "\t")
        teile = [x for x in t.split(d) if x != ""]
        if teile and teile[-1].endswith("\n"):
            teile[-1] = teile[-1][:-1]
    else:
        try:
            teile = _xargs_split(t)
        except ProgFehler as e:
            ctx.err(f"xargs: {e.msg}\n")
            return 1
    rep = None
    for k, v in opts:
        if k in ("-I",):
            rep = v
        elif k in ("-i", "--replace"):
            rep = v or "{}"
    n = wert(opts, "-n", "--max-args")
    L = wert(opts, "-L", "--max-lines")
    verbose = hat(opts, "-t", "--verbose")
    leer_nicht = hat(opts, "-r", "--no-run-if-empty")
    laeufe = []
    if rep is not None:
        zeilen = [z for z in t.split("\n") if z.strip()]
        for z in zeilen:
            laeufe.append([a.replace(rep, z.strip()) for a in befehl])
    elif n:
        n = int(n)
        for i in range(0, len(teile), n):
            laeufe.append(befehl + teile[i:i + n])
    elif L:
        zeilen = [z for z in t.split("\n") if z.strip()]
        L = int(L)
        for i in range(0, len(zeilen), L):
            laeufe.append(befehl + [w for z in zeilen[i:i + L] for w in z.split()])
    else:
        if teile or not leer_nicht:
            laeufe.append(befehl + teile)
    if not teile and leer_nicht:
        return 0
    rc = 0
    for argv in laeufe:
        ctx.check()
        if verbose:
            ctx.err(" ".join(argv) + "\n")
        r2 = ctx.sh.extern(argv, ctx.fds, ctx.env) if argv[0] not in __import__("lab.emu.builtins", fromlist=["BUILTINS"]).BUILTINS or argv[0] in ("echo", "printf", "kill", "pwd", "test", "[", "true", "false") else 127
        if r2 == 127:
            ctx.err(f"xargs: {argv[0]}: No such file or directory\n")
            return 127
        if r2 == 255:
            ctx.err(f"xargs: {argv[0]}: exited with status 255; aborting\n")
            return 124
        if r2:
            rc = 123
    return rc


def _xargs_split(t):
    teile, akt, i, q = [], "", 0, None
    hat_inhalt = False
    while i < len(t):
        c = t[i]
        if q:
            if c == q:
                q = None
            elif c == "\n":
                raise ProgFehler(f"unmatched {'single' if q == chr(39) else 'double'} quote; by default quotes are "
                                 "special to xargs unless you use the -0 option")
            else:
                akt += c
            i += 1
            continue
        if c in "'\"":
            q = c
            hat_inhalt = True
        elif c == "\\" and i + 1 < len(t):
            akt += t[i + 1]
            hat_inhalt = True
            i += 1
        elif c.isspace():
            if akt or hat_inhalt:
                teile.append(akt)
            akt, hat_inhalt = "", False
        else:
            akt += c
        i += 1
    if q:
        raise ProgFehler(f"unmatched {'single' if q == chr(39) else 'double'} quote; by default quotes are special "
                         "to xargs unless you use the -0 option")
    if akt or hat_inhalt:
        teile.append(akt)
    return teile


# ---------------------------------------------------------------------------- find
class _FindParser:
    def __init__(self, toks, ctx):
        self.t, self.i, self.ctx = toks, 0, ctx
        self.hat_aktion = False

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def nimm(self):
        x = self.peek()
        self.i += 1
        return x

    def parse(self):
        if not self.t:
            return lambda e: True
        f = self.oder()
        if self.peek() is not None:
            if self.peek() == ")":
                raise ProgFehler("invalid expression; you have too many ')'")
            raise ProgFehler(f"unknown predicate `{self.peek()}'")
        return f

    def oder(self):
        a = self.und()
        while self.peek() in ("-o", "-or"):
            self.nimm()
            b = self.und()
            a = (lambda x, y: lambda e: x(e) or y(e))(a, b)
        return a

    def und(self):
        a = self.nicht()
        while self.peek() is not None and self.peek() not in ("-o", "-or", ")"):
            if self.peek() in ("-a", "-and"):
                self.nimm()
            b = self.nicht()
            a = (lambda x, y: lambda e: x(e) and y(e))(a, b)
        return a

    def nicht(self):
        if self.peek() in ("!", "-not"):
            self.nimm()
            f = self.nicht()
            return lambda e: not f(e)
        return self.prim()

    def arg(self, name):
        v = self.nimm()
        if v is None:
            raise ProgFehler(f"missing argument to `{name}'")
        return v

    def prim(self):
        t = self.nimm()
        ctx = self.ctx
        if t == "(":
            f = self.oder()
            if self.nimm() != ")":
                raise ProgFehler("invalid expression; I was expecting to find a ')' somewhere but did not see one.")
            return f
        if t in ("-name", "-iname"):
            m = self.arg(t)
            ig = t == "-iname"
            return lambda e: fnmatch.fnmatchcase(e["name"].lower() if ig else e["name"], m.lower() if ig else m)
        if t in ("-path", "-ipath", "-wholename"):
            m = self.arg(t)
            return lambda e: fnmatch.fnmatchcase(e["pfad"], m)
        if t in ("-regex", "-iregex"):
            m = self.arg(t)
            rx = re.compile(posix_regex(m, False), re.I if t == "-iregex" else 0)
            return lambda e: bool(rx.fullmatch(e["pfad"]))
        if t == "-type":
            ty = self.arg(t)
            erlaubt = set(ty.split(","))
            for x in erlaubt:
                if x not in "fdlcbps":
                    raise ProgFehler(f"Unknown argument to -type: {x}")
            m = {"f": "-", "d": "d", "l": "l", "c": "c", "b": "b", "p": "p", "s": "s"}
            return lambda e: e["node"].typ in {m[x] for x in erlaubt}
        if t == "-size":
            s = self.arg(t)
            mm = re.fullmatch(r"([-+]?)(\d+)([cwbkMG]?)", s)
            if not mm:
                raise ProgFehler(f"invalid -size type `{s}'")
            einheit = {"": 512, "b": 512, "c": 1, "w": 2, "k": 1024, "M": 1 << 20, "G": 1 << 30}[mm.group(3)]
            n = int(mm.group(2))

            def size(e):
                if e["node"].typ == "d":
                    v = 4096
                else:
                    v = e["node"].size()
                einh = -(-v // einheit)
                if mm.group(1) == "+":
                    return einh > n
                if mm.group(1) == "-":
                    return einh < n
                return einh == n
            return size
        if t in ("-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin"):
            s = self.arg(t)
            mm = re.fullmatch(r"([-+]?)(\d+)", s)
            if not mm:
                raise ProgFehler(f"invalid argument `{s}' to `{t}'")
            fak = 60 if t.endswith("min") else 86400
            attr = {"m": "mtime", "a": "atime", "c": "ctime"}[t[1]]
            n = int(mm.group(2))

            def zeit(e):
                alter = int((time.time() - getattr(e["node"], attr)) // fak)
                if mm.group(1) == "+":
                    return alter > n
                if mm.group(1) == "-":
                    return alter < n
                return alter == n
            return zeit
        if t == "-newer":
            ref = self.arg(t)
            try:
                rn = ctx.node(ref)
            except FsError as e:
                raise ProgFehler(f"{ql(ref)}: {e.msg}")
            return lambda e: e["node"].mtime > rn.mtime
        if t == "-empty":
            return lambda e: (e["node"].typ == "d" and not e["node"].eintraege()) or (e["node"].typ == "-" and e["node"].size() == 0)
        if t == "-perm":
            s = self.arg(t)
            pre = s[0] if s[0] in "-/" else ""
            try:
                from .coreutils import chmod_wert
                m = chmod_wert(s.lstrip("-/"), 0)
            except ValueError:
                raise ProgFehler(f"invalid mode `{s}'")
            if pre == "-":
                return lambda e: e["node"].mode & m == m
            if pre == "/":
                return lambda e: bool(e["node"].mode & m) or m == 0
            return lambda e: e["node"].mode & 0o7777 == m
        if t in ("-user", "-uid"):
            u = self.arg(t)
            return lambda e: u in ("root", "0")
        if t in ("-group", "-gid"):
            g = self.arg(t)
            return lambda e: g in ("root", "0")
        if t == "-links":
            n = int(self.arg(t).lstrip("+-"))
            return lambda e: e["node"].nlink() == n
        if t in ("-maxdepth", "-mindepth"):
            v = self.arg(t)
            if not v.isdigit():
                raise ProgFehler(f"Expected a positive decimal integer argument to {t}, but got `{v}'")
            setattr(self, t[1:], int(v))
            return lambda e: True
        if t in ("-print", "-print0"):
            self.hat_aktion = True
            end = "\0" if t == "-print0" else "\n"
            return lambda e: (ctx.out(e["pfad"] + end), True)[1]
        if t == "-printf":
            fmt = self.arg(t)
            self.hat_aktion = True

            def pf(e):
                n = e["node"]
                rep = {"p": e["pfad"], "f": e["name"], "s": str(n.size()), "m": f"{n.mode & 0o7777:o}",
                       "M": mode_str(n), "u": "root", "g": "root", "h": posixpath.dirname(e["pfad"]) or ".",
                       "d": str(e["tiefe"]), "y": {"-": "f"}.get(n.typ, n.typ), "%": "%",
                       "t": time.strftime("%a %b %e %H:%M:%S.0000000000 %Y", time.gmtime(n.mtime)),
                       "T@": str(n.mtime)}
                txt = re.sub(r"%([pfsmMughdyt%])", lambda mm: rep[mm.group(1)], fmt)
                ctx.out(txt.replace("\\n", "\n").replace("\\t", "\t").replace("\\0", "\0"))
                return True
            return pf
        if t == "-ls":
            self.hat_aktion = True

            def lsfn(e):
                n = e["node"]
                ctx.out(f"{n.ino:>9} {_bloecke(n):>6} {mode_str(n)} {n.nlink():>3} root     root     "
                        f"{n.size():>8} {zeit_ls(n.mtime)} {e['pfad']}" + (f" -> {n.ziel}" if n.typ == "l" else "") + "\n")
                return True
            return lsfn
        if t == "-delete":
            self.hat_aktion = True
            self.depth = True

            def loe(e):
                try:
                    if e["node"].typ == "d" and e["node"].eintraege():
                        ctx.err(f"find: cannot delete {quote_name(e['pfad'])}: Directory not empty\n")
                        return False
                    ctx.fs.entfernen(e["pfad"], ctx.cwd)
                except FsError as ex:
                    ctx.err(f"find: cannot delete {quote_name(e['pfad'])}: {ex.msg}\n")
                    return False
                return True
            return loe
        if t in ("-exec", "-execdir", "-ok", "-okdir"):
            cmd = []
            while True:
                x = self.nimm()
                if x is None:
                    raise ProgFehler(f"missing argument to `{t}'")
                if x == ";":
                    plus = False
                    break
                if x == "+" and cmd and cmd[-1] == "{}":
                    plus = True
                    break
                cmd.append(x)
            if not cmd:
                raise ProgFehler(f"missing argument to `{t}'")
            self.hat_aktion = True
            if plus:
                sammel = []
                self.nachher = getattr(self, "nachher", [])

                def flush():
                    if sammel:
                        ctx.sh.extern(cmd[:-1] + list(sammel), ctx.fds, ctx.env)
                self.nachher.append(flush)

                def sam(e):
                    sammel.append(e["pfad"])
                    return True
                return sam

            def ex(e):
                argv = [a.replace("{}", e["pfad"] if t in ("-exec", "-ok") else "./" + e["name"]) for a in cmd]
                if t in ("-ok", "-okdir"):
                    ctx.err(f"< {' '.join(argv)} ... {e['pfad']} > ? ")
                    if not ctx.stdin.readline().strip().lower().startswith("y"):
                        return False
                from ..builtins import BUILTINS
                if argv[0] in BUILTINS and argv[0] not in ("echo", "printf", "test", "[", "true", "false", "pwd", "kill"):
                    ctx.err(f"find: '{argv[0]}': No such file or directory\n")
                    return False
                return ctx.sh.extern(argv, ctx.fds, ctx.env) == 0
            return ex
        if t in ("-prune",):
            return lambda e: (e.__setitem__("prune", True), True)[1]
        if t in ("-quit",):
            def q(e):
                raise StopIteration
            return q
        if t in ("-xdev", "-mount", "-depth", "-follow", "-daystart", "-noleaf", "-ignore_readdir_race", "-true"):
            if t == "-depth":
                self.depth = True
            if t == "-follow":
                self.follow = True
            return lambda e: True
        if t == "-false":
            return lambda e: False
        if t == "-readable" or t == "-writable" or t == "-executable":
            return lambda e: t != "-executable" or bool(e["node"].mode & 0o111)
        if t is None:
            raise ProgFehler("expected an expression")
        if t.startswith("-"):
            raise ProgFehler(f"unknown predicate `{t}'")
        raise ProgFehler(f"paths must precede expression: `{t}'")


@prog("find")
def find(ctx):
    args = ctx.argv[1:]
    if args[:1] == ["--help"]:
        return ctx.hilfe()
    if args[:1] == ["--version"]:
        return ctx.version()
    follow = False
    while args and args[0] in ("-H", "-L", "-P"):
        follow = args[0] == "-L"
        args = args[1:]
    pfade = []
    while args and not (args[0].startswith("-") and len(args[0]) > 1 or args[0] in ("!", "(")):
        pfade.append(args.pop(0))
    if not pfade:
        pfade = ["."]
    p = _FindParser(args, ctx)
    p.depth = False
    p.follow = follow
    try:
        f = p.parse()
    except ProgFehler as e:
        ctx.err(f"find: {e.msg}\n")
        return 1
    except re.error:
        ctx.err("find: invalid regular expression\n")
        return 1
    maxd = getattr(p, "maxdepth", None)
    mind = getattr(p, "mindepth", 0)
    drucken = not p.hat_aktion
    rc = 0

    def lauf(pfad, node, tiefe):
        nonlocal rc
        ctx.check()
        e = {"pfad": pfad, "name": posixpath.basename(pfad.rstrip("/")) or pfad, "node": node, "tiefe": tiefe}
        if tiefe == 0:
            e["name"] = posixpath.basename(pfad.rstrip("/")) or ("/" if pfad.startswith("/") else pfad)
        if not p.depth and tiefe >= mind and (maxd is None or tiefe <= maxd):
            if f(e) and drucken:
                ctx.out(pfad + "\n")
        if node.typ == "d" and (maxd is None or tiefe < maxd) and not e.get("prune"):
            if node.gen is not None and pfad.rstrip("/") in ("/proc", "/sys"):
                kinder = node.eintraege()
            else:
                kinder = node.eintraege()
            for name, k in list(kinder.items()):
                if follow and k.typ == "l":
                    try:
                        k = ctx.fs.aufloesen(pfad.rstrip("/") + "/" + name, ctx.cwd)[0]
                    except FsError:
                        pass
                lauf((pfad.rstrip("/") if pfad != "/" else "") + "/" + name, k, tiefe + 1)
        if p.depth and tiefe >= mind and (maxd is None or tiefe <= maxd):
            if f(e) and drucken:
                ctx.out(pfad + "\n")

    try:
        for start in pfade:
            try:
                n = ctx.fs.aufloesen(start, ctx.cwd, folgen=follow)[0]
            except FsError as e:
                ctx.err(f"find: {ql(start)}: {e.msg}\n")
                rc = 1
                continue
            lauf(start, n, 0)
    except StopIteration:
        pass
    for fl in getattr(p, "nachher", []):
        fl()
    return rc


@prog("column")
def column(ctx):
    r = standard(ctx, "c:ts:o:xnJN:R:", {"columns": 1, "table": 0, "separator": 1, "output-separator": 1,
                                          "fillrows": 0, "table-columns": 1, "table-right": 1})
    if isinstance(r, int):
        return r
    opts, args = r
    t = "".join(lesbar(ctx, a) or "" for a in args) if args else ctx.stdin.read()
    zeilen = [z for z in t.split("\n") if z.strip()]
    if hat(opts, "-t", "--table"):
        sep = wert(opts, "-s", "--separator")
        osep = wert(opts, "-o", "--output-separator", std="  ")
        rows = [(z.split(sep) if sep else z.split()) for z in zeilen]
        n = max((len(x) for x in rows), default=0)
        breiten = [max((len(x[i]) for x in rows if i < len(x)), default=0) for i in range(n)]
        out = []
        for x in rows:
            teile = [f"{v:<{breiten[i]}}" if i < len(x) - 1 else v for i, v in enumerate(x)]
            out.append(osep.join(teile).rstrip() + "\n")
        ctx.out("".join(out))
        return 0
    ctx.out("".join(z + "\n" for z in zeilen))
    return 0


@prog("strings")
def strings(ctx):
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    for a in args or ["-"]:
        t = lesbar(ctx, a, "strings")
        if t is None:
            return 1
        for m in re.finditer(r"[\x20-\x7e\t]{4,}", t):
            ctx.out(m.group(0) + "\n")
    return 0


@prog("od")
def od(ctx):
    r = standard(ctx, "A:bcdj:N:ostvw::xS:", {"address-radix": 1, "format": 1, "output-duplicates": 0,
                                              "read-bytes": 1, "skip-bytes": 1, "width": 2, "traditional": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    t = lesbar(ctx, args[0] if args else "-", "od")
    if t is None:
        return 1
    b = t.encode("utf-8", "surrogateescape")
    c = hat(opts, "-c")
    x = hat(opts, "-x")
    out = []
    for off in range(0, len(b), 16):
        chunk = b[off:off + 16]
        if c:
            teile = []
            for by in chunk:
                ch = {0: "\\0", 7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r"}.get(by)
                teile.append(f"{ch:>4}" if ch else (f"{chr(by):>4}" if 32 <= by < 127 else f"{by:>4o}"))
            out.append(f"{off:07o}" + "".join(teile) + "\n")
        elif x:
            woerter = [int.from_bytes(chunk[i:i + 2].ljust(2, b"\0"), "little") for i in range(0, len(chunk), 2)]
            out.append(f"{off:07o}" + "".join(f" {w:04x}" for w in woerter) + "\n")
        else:
            woerter = [int.from_bytes(chunk[i:i + 2].ljust(2, b"\0"), "little") for i in range(0, len(chunk), 2)]
            out.append(f"{off:07o}" + "".join(f" {w:06o}" for w in woerter) + "\n")
    out.append(f"{len(b):07o}\n")
    ctx.out("".join(out))
    return 0


@prog("bc")
def bc(ctx):
    """Einfacher bc: Ausdruecke mit scale, Variablen, + - * / % ^ ( )."""
    from decimal import Decimal, getcontext, ROUND_DOWN
    getcontext().prec = 60
    args = [a for a in ctx.argv[1:] if not a.startswith("-")]
    mathlib = "-l" in ctx.argv
    text = "".join(lesbar(ctx, a, "bc") or "" for a in args) if args else ctx.stdin.read()
    vars_ = {"scale": Decimal(20 if mathlib else 0)}

    def fmt(v):
        sc = int(vars_["scale"])
        if v == v.to_integral_value() and sc == 0:
            return str(int(v))
        t = format(v, "f")
        if "." in t:
            ganz, nach = t.split(".")
            if ganz in ("0", "-0"):
                ganz = "-" if t.startswith("-") else ""
            return ganz + ("." + nach if nach else "")
        return t

    def ev(expr):
        toks = re.findall(r"\d*\.\d+|\d+|[A-Za-z_]\w*|\^|[-+*/%()]", expr)
        pos = [0]

        def peek():
            return toks[pos[0]] if pos[0] < len(toks) else None

        def nimm():
            pos[0] += 1
            return toks[pos[0] - 1]

        def prim():
            t = nimm()
            if t == "(":
                v = add()
                nimm()
                return v
            if t == "-":
                return -prim()
            if re.fullmatch(r"\d*\.?\d+", t):
                return Decimal(t)
            return vars_.get(t, Decimal(0))

        def potenz():
            v = prim()
            if peek() == "^":
                nimm()
                return v ** int(potenz())
            return v

        def mul():
            v = potenz()
            while peek() in ("*", "/", "%"):
                op = nimm()
                r = potenz()
                if op == "*":
                    v = v * r
                elif r == 0:
                    raise ZeroDivisionError
                elif op == "/":
                    sc = int(vars_["scale"])
                    v = (v / r).quantize(Decimal(1).scaleb(-sc), rounding=ROUND_DOWN) if sc else Decimal(int(v / r))
                else:
                    v = v - r * Decimal(int(v / r))
            return v

        def add():
            v = mul()
            while peek() in ("+", "-"):
                op = nimm()
                r = mul()
                v = v + r if op == "+" else v - r
            return v
        return add()
    for zeile in re.split(r"[;\n]", text):
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if zeile == "quit":
            break
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*(.+)", zeile)
        try:
            if m:
                vars_[m.group(1)] = ev(m.group(2))
                continue
            ctx.out(fmt(ev(zeile)) + "\n")
        except ZeroDivisionError:
            ctx.err("Runtime error (func=(main), adr=6): Divide by zero\n")
        except Exception:  # noqa: BLE001
            ctx.err("(standard_in) 1: syntax error\n")
    return 0
