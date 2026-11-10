"""Bash-Builtins mit den Original-Meldungen der Bash 5.2."""
import json
import os
import posixpath
import re
import time

from . import shell as S
from .vfs import FsError

_TEXTE = None


def texte():
    global _TEXTE
    if _TEXTE is None:
        with open(os.path.join(os.path.dirname(__file__), "texte.json"), encoding="utf-8") as f:
            _TEXTE = json.load(f)
    return _TEXTE


KEYWORDS = {"if", "then", "else", "elif", "fi", "case", "esac", "for", "select", "while", "until", "do", "done", "in",
            "function", "time", "{", "}", "!", "[[", "]]", "coproc"}


def _fehler(sh, io, name, msg):
    io.err(f"{sh.praefix()}{name}: {msg}\n")


def _ungueltige_option(sh, io, name, opt, usage):
    _fehler(sh, io, name, f"{opt}: invalid option")
    io.err(f"{name}: usage: {usage}\n")
    return 2


def b_colon(sh, argv, io):
    return 0


def b_true(sh, argv, io):
    return 0


def b_false(sh, argv, io):
    return 1


def b_cd(sh, argv, io):
    args = argv[1:]
    physisch = False
    while args and args[0].startswith("-") and args[0] not in ("-", "--"):
        for ch in args[0][1:]:
            if ch == "P":
                physisch = True
            elif ch == "L":
                physisch = False
            elif ch in "e@":
                pass
            else:
                return _ungueltige_option(sh, io, "cd", "-" + ch, "cd [-L|[-P [-e]] [-@]] [dir]")
        args = args[1:]
    if args and args[0] == "--":
        args = args[1:]
    if len(args) > 1:
        _fehler(sh, io, "cd", "too many arguments")
        return 1
    drucken = False
    if not args:
        ziel = sh.vars.get("HOME")
        if ziel is None:
            _fehler(sh, io, "cd", "HOME not set")
            return 1
    else:
        ziel = args[0]
        if ziel == "-":
            ziel = sh.vars.get("OLDPWD")
            if ziel is None:
                _fehler(sh, io, "cd", "OLDPWD not set")
                return 1
            drucken = True
    if ziel == "":
        return 0
    kandidaten = [ziel]
    cdpath = sh.vars.get("CDPATH")
    if cdpath and not ziel.startswith("/") and not ziel.startswith("."):
        kandidaten = [posixpath.join(d or ".", ziel) for d in cdpath.split(":")] + [ziel]
    letzter = None
    for k in kandidaten:
        try:
            node, real = sh.fs.aufloesen(k, sh.cwd)
        except FsError as e:
            letzter = e.msg
            continue
        if node.typ != "d":
            letzter = "Not a directory"
            continue
        neu = real if physisch else sh.fs.abs(k, sh.cwd)
        if not physisch:
            # logischer Pfad: '..' textuell aufloesen, aber Ziel muss existieren
            neu = sh.fs.abs(k, sh.cwd)
            if sh.fs.finde(neu) is None:
                neu = real
        sh.vars["OLDPWD"] = sh.cwd
        sh.cwd = neu
        sh.vars["PWD"] = neu
        if drucken or k != ziel:
            io.out(neu + "\n")
        return 0
    _fehler(sh, io, "cd", f"{ziel}: {letzter}")
    return 1


def b_pwd(sh, argv, io):
    for a in argv[1:]:
        if a.startswith("-") and a not in ("-L", "-P"):
            return _ungueltige_option(sh, io, "pwd", a[:2], "pwd [-LP]")
    if "-P" in argv[1:]:
        try:
            _, real = sh.fs.aufloesen(sh.cwd)
            io.out(real + "\n")
            return 0
        except FsError:
            pass
    io.out(sh.cwd + "\n")
    return 0


def b_echo(sh, argv, io):
    args = argv[1:]
    nl, esc = True, False
    while args and re.fullmatch(r"-[neE]+", args[0]):
        for ch in args[0][1:]:
            if ch == "n":
                nl = False
            elif ch == "e":
                esc = True
            elif ch == "E":
                esc = False
        args = args[1:]
    text = " ".join(args)
    if esc:
        if "\\c" in text:
            text = text[:text.index("\\c")]
            nl = False
        text = S.ansi_c(text.replace("\\'", "\\\\'").replace('\\"', '\\\\"'))
    io.out(text + ("\n" if nl else ""))
    return 0


def printf_format(sh, io, fmt, args, name="printf"):
    """Bash-printf. -> (text, rc)."""
    out = []
    rc = 0
    args = list(args)
    erstes = True

    def zahl(a, typ):
        nonlocal rc
        if a is None or a == "":
            return 0
        t = a.strip()
        if t[:1] in "'\"" and len(t) >= 2:
            return ord(t[1])
        try:
            if typ == "f":
                return float(t)
            if re.fullmatch(r"[-+]?0[xX][0-9a-fA-F]+", t):
                return int(t, 16)
            if re.fullmatch(r"[-+]?0[0-7]+", t):
                return int(t, 8)
            return int(t)
        except ValueError:
            m = re.match(r"[-+]?\d+(\.\d*)?", t)
            _fehler(sh, io, name, f"{a}: invalid number")
            rc = 1
            if m:
                return float(m.group()) if typ == "f" else int(float(m.group()))
            return 0

    while erstes or args:
        erstes = False
        verbraucht = False
        i = 0
        while i < len(fmt):
            c = fmt[i]
            if c == "\\":
                m = re.match(r"\\(?:[0-7]{1,3}|x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|.)", fmt[i:])
                seq = m.group() if m else "\\"
                if seq == "\\c":
                    return "".join(out), rc
                if re.fullmatch(r"\\[0-7]{1,3}", seq):
                    out.append(chr(int(seq[1:], 8)))
                else:
                    out.append(S.ansi_c(seq))
                i += len(seq)
                continue
            if c == "%":
                m = re.match(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?([diouxXfFeEgGcsbq%])", fmt[i:])
                if not m:
                    spec = fmt[i:i + 2]
                    _fehler(sh, io, name, f"`{spec[1:] or '%'}': invalid format character" if len(spec) > 1 else
                            "`%': missing format character")
                    return "".join(out), 1
                flags, breite, praez, typ = m.groups()
                i += m.end()
                if typ == "%":
                    out.append("%")
                    continue
                if breite == "*":
                    breite = str(zahl(args.pop(0) if args else None, "d"))
                    verbraucht = True
                if praez == "*":
                    praez = str(zahl(args.pop(0) if args else None, "d"))
                    verbraucht = True
                arg = args.pop(0) if args else None
                if arg is not None:
                    verbraucht = True
                spec = "%" + (flags or "") + (breite or "") + ("." + praez if praez is not None else "")
                if typ in "diouxX":
                    v = zahl(arg, "d")
                    v = int(v)
                    if typ == "u" and v < 0:
                        v += 1 << 64
                    out.append((spec + {"i": "d", "u": "d"}.get(typ, typ)) % v)
                elif typ in "fFeEgG":
                    out.append((spec + typ) % zahl(arg, "f"))
                elif typ == "c":
                    out.append((spec + "s") % ((arg or "")[:1]))
                elif typ == "s":
                    out.append((spec + "s") % (arg or ""))
                elif typ == "b":
                    t = arg or ""
                    if "\\c" in t:
                        out.append((spec + "s") % S.ansi_c(t[:t.index("\\c")]))
                        return "".join(out), rc
                    t = re.sub(r"\\0([0-7]{1,3})", lambda mm: "\\" + mm.group(1), t)
                    out.append((spec + "s") % S.ansi_c(t))
                elif typ == "q":
                    t = arg or ""
                    out.append((spec + "s") % (S.Shell._xq(t) if t else "''"))
                continue
            out.append(c)
            i += 1
        if not verbraucht:
            break
    return "".join(out), rc


def b_printf(sh, argv, io):
    args = argv[1:]
    var = None
    if args and args[0] == "-v":
        if len(args) < 2:
            _fehler(sh, io, "printf", "-v: option requires an argument")
            io.err("printf: usage: printf [-v var] format [arguments]\n")
            return 2
        var, args = args[1], args[2:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        io.err("printf: usage: printf [-v var] format [arguments]\n")
        return 2
    if args[0].startswith("-") and args[0] != "-":
        return _ungueltige_option(sh, io, "printf", args[0][:2], "printf [-v var] format [arguments]")
    text, rc = printf_format(sh, io, args[0], args[1:])
    if var:
        sh.setv(var, text)
    else:
        io.out(text)
    return rc


def _declare_zeile(sh, name, export=None):
    flags = ""
    if name in sh.arrays:
        flags += "a"
    if name in sh.readonly:
        flags += "r"
    if export or name in sh.export:
        flags += "x"
    flags = flags or "-"
    if name in sh.arrays:
        werte = " ".join(f'[{k}]="{v}"' for k, v in sorted(sh.arrays[name].items()))
        return f"declare -{flags} {name}=({werte})"
    if name not in sh.vars:
        return f"declare -{flags} {name}"
    v = sh.vars[name].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'declare -{flags} {name}="{v}"'


def b_export(sh, argv, io):
    args = argv[1:]
    weg = False
    druck = False
    while args and args[0].startswith("-") and args[0] != "-":
        if args[0] == "--":
            args = args[1:]
            break
        for ch in args[0][1:]:
            if ch == "n":
                weg = True
            elif ch == "p":
                druck = True
            elif ch == "f":
                pass
            else:
                return _ungueltige_option(sh, io, "export", "-" + ch, "export [-fn] [name[=value] ...] or export -p")
        args = args[1:]
    if not args:
        for n in sorted(sh.export):
            if n in sh.vars or n in sh.arrays:
                io.out(_declare_zeile(sh, n, True) + "\n")
            else:
                io.out(f"declare -x {n}\n")
        return 0
    rc = 0
    for a in args:
        m = re.match(r"([A-Za-z_]\w*)(\+?)=(.*)$", a, re.S)
        name = m.group(1) if m else a
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            _fehler(sh, io, "export", f"`{a}': not a valid identifier")
            rc = 1
            continue
        if weg:
            sh.export.discard(name)
            continue
        if m:
            wert = (sh.vars.get(name, "") + m.group(3)) if m.group(2) else m.group(3)
            sh.setv(name, wert)
        sh.export.add(name)
    return rc


def b_unset(sh, argv, io):
    args = argv[1:]
    funcs = False
    while args and args[0].startswith("-"):
        if args[0] == "-f":
            funcs = True
        elif args[0] in ("-v", "-n"):
            funcs = False
        elif args[0] == "--":
            args = args[1:]
            break
        else:
            return _ungueltige_option(sh, io, "unset", args[0][:2], "unset [-f] [-v] [-n] [name ...]")
        args = args[1:]
    rc = 0
    for a in args:
        if funcs:
            sh.funcs.pop(a, None)
            continue
        m = re.fullmatch(r"(\w+)\[(.+)\]", a)
        if m and m.group(1) in sh.arrays:
            try:
                sh.arrays[m.group(1)].pop(sh.arith(m.group(2)), None)
            except S.ExpandError:
                pass
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", a):
            _fehler(sh, io, "unset", f"`{a}': not a valid identifier")
            rc = 1
            continue
        if a in sh.readonly:
            _fehler(sh, io, "unset", f"{a}: cannot unset: readonly variable")
            rc = 1
            continue
        if a in sh.vars or a in sh.arrays:
            sh.unsetv(a)
        else:
            sh.funcs.pop(a, None)
    return rc


SET_O = ["allexport", "braceexpand", "emacs", "errexit", "errtrace", "functrace", "hashall", "histexpand", "history",
         "ignoreeof", "interactive-comments", "keyword", "monitor", "noclobber", "noexec", "noglob", "nolog", "notify",
         "nounset", "onecmd", "physical", "pipefail", "posix", "privileged", "verbose", "vi", "xtrace"]
O_BUCHST = {"allexport": "a", "errexit": "e", "noglob": "f", "hashall": "h", "monitor": "m", "noclobber": "C",
            "nounset": "u", "xtrace": "x", "verbose": "v", "histexpand": "H", "braceexpand": "B", "noexec": "n"}


def _o_an(sh, name):
    if name == "pipefail":
        return sh.pipefail
    if name in ("emacs", "history", "interactive-comments"):
        return sh.interaktiv or name == "interactive-comments"
    b = O_BUCHST.get(name)
    return b in sh.opts if b else False


def b_set(sh, argv, io):
    args = argv[1:]
    if not args:
        namen = sorted(set(sh.vars) | set(sh.arrays))
        for n in namen:
            if n in sh.arrays:
                io.out(f"{n}=(" + " ".join(f'[{k}]="{v}"' for k, v in sorted(sh.arrays[n].items())) + ")\n")
            else:
                io.out(f"{n}={S.Shell._xq(sh.vars[n])}\n")
        for f in sorted(sh.funcs):
            io.out(f"{f} () \n{{ \n    ...\n}}\n")
        return 0
    while args:
        a = args[0]
        if a == "--":
            sh.args = args[1:]
            return 0
        if a in ("-o", "+o"):
            if len(args) == 1:
                for n in SET_O:
                    if a == "-o":
                        io.out(f"{n:<15}\t{'on' if _o_an(sh, n) else 'off'}\n")
                    else:
                        io.out(f"set {'-' if _o_an(sh, n) else '+'}o {n}\n")
                return 0
            name = args[1]
            if name not in SET_O:
                _fehler(sh, io, "set", f"{name}: invalid option name")
                return 1
            if name == "pipefail":
                sh.pipefail = a == "-o"
            elif name in O_BUCHST:
                (sh.opts.add if a == "-o" else sh.opts.discard)(O_BUCHST[name])
            args = args[2:]
            continue
        if a[:1] in "-+" and len(a) > 1:
            for ch in a[1:]:
                if ch not in "abefhkmnptuvxBCEHPT":
                    _fehler(sh, io, "set", f"{a[0]}{ch}: invalid option")
                    io.err("set: usage: set [-abefhkmnptuvxBCEHPT] [-o option-name] [--] [-] [arg ...]\n")
                    return 2
                (sh.opts.add if a[0] == "-" else sh.opts.discard)(ch)
            args = args[1:]
            continue
        sh.args = args
        return 0
    return 0


def b_shift(sh, argv, io):
    n = 1
    if len(argv) > 1:
        if not re.fullmatch(r"\d+", argv[1]):
            _fehler(sh, io, "shift", f"{argv[1]}: numeric argument required")
            return 1
        n = int(argv[1])
    if n > len(sh.args):
        return 1
    sh.args = sh.args[n:]
    return 0


def b_read(sh, argv, io):
    args = argv[1:]
    roh, prompt, still, anzahl, timeout, arr, delim = False, None, False, None, None, None, "\n"
    while args and args[0].startswith("-") and len(args[0]) > 1:
        a = args.pop(0)
        if a == "--":
            break
        k = 1
        while k < len(a):
            ch = a[k]
            if ch in "pntadu":
                wert = a[k + 1:] or (args.pop(0) if args else None)
                if wert is None:
                    _fehler(sh, io, "read", f"-{ch}: option requires an argument")
                    io.err("read: usage: read [-ers] [-a array] [-d delim] [-i text] [-n nchars] [-N nchars] "
                           "[-p prompt] [-t timeout] [-u fd] [name ...]\n")
                    return 2
                if ch == "p":
                    prompt = wert
                elif ch == "n":
                    anzahl = int(wert) if wert.isdigit() else None
                elif ch == "t":
                    timeout = wert
                elif ch == "a":
                    arr = wert
                elif ch == "d":
                    delim = wert[:1] or "\0"
                break
            if ch == "r":
                roh = True
            elif ch == "s":
                still = True
            elif ch in "eN":
                pass
            else:
                _fehler(sh, io, "read", f"-{ch}: invalid option")
                io.err("read: usage: read [-ers] [-a array] [-d delim] [-i text] [-n nchars] [-N nchars] "
                       "[-p prompt] [-t timeout] [-u fd] [name ...]\n")
                return 2
            k += 1
    namen = args or (["REPLY"] if not arr else [])
    stdin = io.stdin
    tty = getattr(stdin, "isatty", False)
    if prompt and tty:
        io.err(prompt)
    if tty and still:
        sh.tty.modus(echo=False)
    try:
        if tty and anzahl:
            sh.tty.modus(canon=False)
            zeichen = []
            try:
                while len(zeichen) < anzahl:
                    k = sh.tty.read_key(pruef_sigint=True)
                    if k is None:
                        break
                    if k == "\r":
                        break
                    zeichen.append(k)
                    if not still:
                        sh.tty.write(k)
            finally:
                sh.tty.modus(canon=True)
            zeile = "".join(zeichen)
            eof = False
        else:
            zeile = stdin.readline()
            eof = not zeile.endswith("\n")
            if anzahl:
                zeile = zeile[:anzahl]
    finally:
        if tty and still:
            sh.tty.modus(echo=True)
            if still:
                io.err("\n") if False else None
    if zeile == "" and eof:
        for n in namen:
            sh.setv(n, "")
        return 1
    zeile = zeile.rstrip("\n")
    if not roh:
        zeile = re.sub(r"\\(.)", r"\1", zeile)
    ifs = sh.vars.get("IFS", " \t\n")
    if arr:
        teile = zeile.split() if ifs == " \t\n" else re.split("[" + re.escape(ifs) + "]", zeile)
        sh.arrays[arr] = {i: t for i, t in enumerate(teile)}
        return 1 if eof else 0
    ws = "".join(c for c in ifs if c in " \t\n")
    zeile = zeile.strip(ws) if ws else zeile
    for i, n in enumerate(namen):
        if i == len(namen) - 1:
            sh.setv(n, zeile.strip(ws) if ws else zeile)
            break
        m = re.match(r"[^" + re.escape(ifs) + r"]*", zeile) if ifs else None
        teil = m.group() if m else zeile
        sh.setv(n, teil)
        zeile = zeile[len(teil):]
        zeile = zeile.lstrip(ws)
        if zeile[:1] and zeile[:1] in ifs and zeile[:1] not in ws:
            zeile = zeile[1:].lstrip(ws)
    return 1 if eof else 0


def b_exit(sh, argv, io):
    code = sh.status
    if len(argv) > 1:
        if not re.fullmatch(r"[-+]?\d+", argv[1]):
            if sh.interaktiv and sh.login and sh.subshell_tiefe == 0:
                io.err("logout\n")
            _fehler(sh, io, "exit", f"{argv[1]}: numeric argument required")
            raise S.Exit(2)
        if len(argv) > 2:
            _fehler(sh, io, "exit", "too many arguments")
            return 1
        code = int(argv[1]) & 0xFF
    if sh.interaktiv and sh.subshell_tiefe == 0:
        if any(not j["done"] for j in sh.jobs) and not getattr(sh, "_exit_gewarnt", False):
            io.err("There are running jobs.\n") if False else None
        io.err("logout\n" if sh.login else "exit\n")
    raise S.Exit(code)


def b_logout(sh, argv, io):
    if not sh.login:
        _fehler(sh, io, "logout", "not login shell: use `exit'")
        return 1
    return b_exit(sh, argv, io)


def b_return(sh, argv, io):
    if sh.func_tiefe == 0 and not sh.script:
        _fehler(sh, io, "return", "can only `return' from a function or sourced script")
        return 1
    code = sh.status
    if len(argv) > 1:
        if not re.fullmatch(r"[-+]?\d+", argv[1]):
            _fehler(sh, io, "return", f"{argv[1]}: numeric argument required")
            code = 2
        else:
            code = int(argv[1]) & 0xFF
    raise S.Return(code)


def _schleifen_n(sh, argv, io, name):
    n = 1
    if len(argv) > 1:
        if not re.fullmatch(r"\d+", argv[1]):
            _fehler(sh, io, name, f"{argv[1]}: numeric argument required")
            return None
        n = int(argv[1])
        if n < 1:
            _fehler(sh, io, name, f"{argv[1]}: loop count out of range")
            return None
    return n


def b_break(sh, argv, io):
    if sh.loop_tiefe == 0:
        _fehler(sh, io, "break", "only meaningful in a `for', `while', or `until' loop")
        return 0
    n = _schleifen_n(sh, argv, io, "break")
    if n is None:
        return 1
    raise S.Break(min(n, sh.loop_tiefe))


def b_continue(sh, argv, io):
    if sh.loop_tiefe == 0:
        _fehler(sh, io, "continue", "only meaningful in a `for', `while', or `until' loop")
        return 0
    n = _schleifen_n(sh, argv, io, "continue")
    if n is None:
        return 1
    raise S.Continue(min(n, sh.loop_tiefe))


def b_alias(sh, argv, io):
    args = argv[1:]
    if args and args[0] == "-p":
        args = args[1:]
        if not args:
            for k in sorted(sh.aliases):
                io.out(f"alias {k}={_sq(sh.aliases[k])}\n")
            return 0
    if not args:
        for k in sorted(sh.aliases):
            io.out(f"alias {k}={_sq(sh.aliases[k])}\n")
        return 0
    rc = 0
    for a in args:
        if a.startswith("-") and a != "-":
            return _ungueltige_option(sh, io, "alias", a[:2], "alias [-p] [name[=value] ... ]")
        if "=" in a:
            k, v = a.split("=", 1)
            if not k or re.search(r"[\s/$`=\"'\\|&;()<>]", k):
                _fehler(sh, io, "alias", f"`{k}': invalid alias name")
                rc = 1
                continue
            sh.aliases[k] = v
        elif a in sh.aliases:
            io.out(f"alias {a}={_sq(sh.aliases[a])}\n")
        else:
            _fehler(sh, io, "alias", f"{a}: not found")
            rc = 1
    return rc


def _sq(v):
    return "'" + v.replace("'", "'\\''") + "'"


def b_unalias(sh, argv, io):
    args = argv[1:]
    if not args:
        io.err("unalias: usage: unalias [-a] name [name ...]\n")
        return 2
    if args[0] == "-a":
        sh.aliases.clear()
        return 0
    rc = 0
    for a in args:
        if a in sh.aliases:
            del sh.aliases[a]
        else:
            _fehler(sh, io, "unalias", f"{a}: not found")
            rc = 1
    return rc


def _art(sh, name, alle=False):
    """-> Liste von (typ, text)."""
    erg = []
    if name in sh.aliases:
        erg.append(("alias", f"{name} is aliased to `{sh.aliases[name]}'"))
    if name in KEYWORDS:
        erg.append(("keyword", f"{name} is a shell keyword"))
    if name in sh.funcs:
        erg.append(("function", f"{name} is a function\n{name} () \n{{ \n    ...\n}}"))
    if name in BUILTINS:
        erg.append(("builtin", f"{name} is a shell builtin"))
    if alle or not erg:
        if "/" in name:
            n = sh.fs.finde(name, sh.cwd)
            if n is not None and n.typ != "d" and n.mode & 0o111:
                erg.append(("file", f"{name} is {name}"))
        else:
            if name in sh.hash and not alle:
                erg.append(("file", f"{name} is hashed ({sh.hash[name]})"))
            else:
                for d in (sh.vars.get("PATH") or "").split(":"):
                    p = posixpath.join(d or ".", name)
                    n = sh.fs.finde(p, sh.cwd)
                    if n is not None and n.typ != "d" and n.mode & 0o111:
                        erg.append(("file", f"{name} is {p}"))
                        if not alle:
                            break
    return erg


def _pfad_von(sh, name, alle=False):
    out = []
    for d in (sh.vars.get("PATH") or "").split(":"):
        p = posixpath.join(d or ".", name)
        n = sh.fs.finde(p, sh.cwd)
        if n is not None and n.typ != "d" and n.mode & 0o111:
            out.append(p)
            if not alle:
                break
    return out


def b_type(sh, argv, io):
    args = argv[1:]
    nur_typ = alle = pfad = False
    while args and args[0].startswith("-") and len(args[0]) > 1:
        a = args.pop(0)
        if a == "--":
            break
        for ch in a[1:]:
            if ch == "t":
                nur_typ = True
            elif ch == "a":
                alle = True
            elif ch in "pP":
                pfad = True
            elif ch == "f":
                pass
            else:
                return _ungueltige_option(sh, io, "type", "-" + ch, "type [-afptP] name [name ...]")
    rc = 0
    for name in args:
        if pfad:
            p = _pfad_von(sh, name, alle)
            if not p and not (name in BUILTINS or name in sh.aliases or name in KEYWORDS or name in sh.funcs) or (
                    not p and argv[1:2] == ["-P"]):
                rc = 1
            for x in p:
                io.out(x + "\n")
            continue
        erg = _art(sh, name, alle)
        if not erg:
            if not nur_typ:
                _fehler(sh, io, "type", f"{name}: not found")
            rc = 1
            continue
        for typ, text in (erg if alle else erg[:1]):
            io.out((typ if nur_typ else text) + "\n")
    return rc


def b_command(sh, argv, io):
    args = argv[1:]
    v = V = False
    while args and args[0].startswith("-") and len(args[0]) > 1:
        a = args.pop(0)
        if a == "--":
            break
        for ch in a[1:]:
            if ch == "v":
                v = True
            elif ch == "V":
                V = True
            elif ch == "p":
                pass
            else:
                return _ungueltige_option(sh, io, "command", "-" + ch, "command [-pVv] command [arg ...]")
    if not args:
        return 0
    if v or V:
        rc = 0
        for name in args:
            erg = _art(sh, name)
            if not erg:
                if V:
                    _fehler(sh, io, "command", f"{name}: not found")
                rc = 1
                continue
            typ, text = erg[0]
            if V:
                io.out(text + "\n")
            elif typ == "alias":
                io.out(f"alias {name}={_sq(sh.aliases[name])}\n")
            elif typ == "file":
                io.out((name if "/" in name else _pfad_von(sh, name)[0] if _pfad_von(sh, name) else sh.hash.get(name, name)) + "\n")
            else:
                io.out(name + "\n")
        return rc
    name = args[0]
    if name in BUILTINS:
        return BUILTINS[name](sh, args, io)
    return sh.extern(args, io.fds, sh.env())


def b_builtin(sh, argv, io):
    if len(argv) < 2:
        return 0
    if argv[1] not in BUILTINS:
        _fehler(sh, io, "builtin", f"{argv[1]}: not a shell builtin")
        return 1
    return BUILTINS[argv[1]](sh, argv[1:], io)


def b_source(sh, argv, io):
    name = argv[0]
    if len(argv) < 2:
        _fehler(sh, io, name, "filename argument required")
        io.err(f"{name}: usage: {name} filename [arguments]\n")
        return 2
    datei = argv[1]
    try:
        if "/" not in datei:
            pfade = [posixpath.join(d or ".", datei) for d in (sh.vars.get("PATH") or "").split(":")] + [datei]
            node = None
            for p in pfade:
                node = sh.fs.finde(p, sh.cwd)
                if node is not None and node.typ == "-":
                    break
            if node is None:
                raise FsError("ENOENT")
        else:
            node, _ = sh.fs.aufloesen(datei, sh.cwd)
        if node.typ == "d":
            _fehler(sh, io, datei, "is a directory")
            return 1
        inhalt = node.inhalt()
    except FsError as e:
        _fehler(sh, io, datei, e.msg)
        return 1
    alt_args = sh.args
    if len(argv) > 2:
        sh.args = argv[2:]
    try:
        return sh.run(inhalt, io.fds)
    except S.Return as r:
        return r.code
    finally:
        if len(argv) > 2:
            sh.args = alt_args


def b_eval(sh, argv, io):
    src = " ".join(argv[1:])
    if not src.strip():
        return 0
    return sh.run(src, io.fds)


def b_exec(sh, argv, io):
    args = argv[1:]
    while args and args[0].startswith("-") and args[0] != "--":
        args = args[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        sh.fds.update(io.fds)
        return 0
    if args[0] in BUILTINS:
        rc = BUILTINS[args[0]](sh, args, io)
    else:
        pfad, node = sh.finde_programm(args[0])
        if pfad is None:
            _fehler(sh, io, "exec", f"{args[0]}: not found")
            if not sh.interaktiv:
                raise S.Exit(127)
            return 127
        rc = sh.extern(args, io.fds, sh.env())
    raise S.Exit(rc)


def b_history(sh, argv, io):
    args = argv[1:]
    if args and args[0] == "-c":
        sh.hist.clear()
        return 0
    if args and args[0].startswith("-") and args[0] not in ("-c",):
        if args[0] in ("-w", "-a", "-r", "-n"):
            return 0
        if args[0] == "-d" and len(args) > 1 and args[1].isdigit():
            i = int(args[1]) - 1
            if 0 <= i < len(sh.hist):
                sh.hist.pop(i)
                return 0
            _fehler(sh, io, "history", f"{args[1]}: history position out of range")
            return 1
        return _ungueltige_option(sh, io, "history", args[0][:2],
                                  "history [-c] [-d offset] [n] or history -anrw [filename] or history -ps arg [arg...]")
    liste = list(enumerate(sh.hist, 1))
    if args:
        if not args[0].isdigit():
            _fehler(sh, io, "history", f"{args[0]}: numeric argument required")
            return 1
        liste = liste[-int(args[0]):] if int(args[0]) else []
    for n, h in liste:
        io.out(f"{n:5d}  {h}\n")
    return 0


def b_help(sh, argv, io):
    t = texte()
    if len(argv) == 1:
        io.out(t["help_all"])
        return 0
    rc = 1
    for a in argv[1:]:
        if a.startswith("-") and a in ("-d", "-m", "-s"):
            continue
        treffer = [k for k in t["builtin"] if passt_help(a, k)]
        for k in treffer:
            io.out(t["builtin"][k])
            rc = 0
        if not treffer:
            _fehler(sh, io, "help", f"no help topics match `{a}'.  Try `help help' or `man -k {a}' or `info {a}'.")
    return rc


def passt_help(muster, name):
    from .muster import passt
    return passt(muster + "*" if not any(c in muster for c in "*?[") else muster, name)


def b_jobs(sh, argv, io):
    for i, j in enumerate(sh.jobs):
        zeichen = "+" if i == len(sh.jobs) - 1 else ("-" if i == len(sh.jobs) - 2 else " ")
        status = ("Done" if j["status"] == 0 else f"Exit {j['status']}") if j["done"] else "Running"
        pid = f"{j['pid']} " if "-l" in argv else ""
        io.out(f"[{j['nr']}]{zeichen}  {pid}{status:<24}{j['cmd']}{' &' if not j['done'] else ''}\n")
    for j in sh.jobs:
        if j["done"]:
            j["gemeldet"] = True
    sh.jobs = [j for j in sh.jobs if not j["gemeldet"]]
    return 0


def b_fg(sh, argv, io):
    laufend = [j for j in sh.jobs if not j["done"]]
    if not laufend:
        _fehler(sh, io, argv[0], "current: no such job")
        return 1
    j = laufend[-1]
    io.out(j["cmd"] + "\n")
    while not j["done"]:
        try:
            sh.check()
        except S.Interrupted:
            raise
        time.sleep(0.1)
    j["gemeldet"] = True
    sh.jobs = [x for x in sh.jobs if x is not j]
    return j["status"]


def b_bg(sh, argv, io):
    laufend = [j for j in sh.jobs if not j["done"]]
    if not laufend:
        _fehler(sh, io, "bg", "current: no such job")
        return 1
    _fehler(sh, io, "bg", f"job {laufend[-1]['nr']} already in background")
    return 0


def b_wait(sh, argv, io):
    while any(not j["done"] for j in sh.jobs):
        sh.check()
        time.sleep(0.1)
    st = sh.jobs[-1]["status"] if sh.jobs else 0
    return st


def b_kill(sh, argv, io):
    from .progs import base
    return base.kill_impl(sh, argv, io, builtin=True)


def b_let(sh, argv, io):
    if len(argv) < 2:
        _fehler(sh, io, "let", "expression expected")
        return 1
    v = 0
    for a in argv[1:]:
        try:
            v = sh.arith(a)
        except S.ExpandError as e:
            _fehler(sh, io, "let", e.msg)
            return 1
    return 0 if v else 1


def b_declare(sh, argv, io):
    name = argv[0]
    args = argv[1:]
    flags, weg = set(), set()
    while args and args[0][:1] in "-+" and len(args[0]) > 1:
        a = args.pop(0)
        if a == "--":
            break
        for ch in a[1:]:
            if ch not in "aAfFgilnprtuxI":
                return _ungueltige_option(sh, io, name, a[0] + ch,
                                          f"{name} [-aAfFgiIlnrtux] [name[=value] ...] or {name} -p [-aAfFilnrtux] [name ...]")
            (flags if a[0] == "-" else weg).add(ch)
    if name == "local" and sh.func_tiefe == 0:
        _fehler(sh, io, "local", "can only be used in a function")
        return 1
    if not args:
        if "f" in flags or "F" in flags:
            for f in sorted(sh.funcs):
                io.out(f"declare -f {f}\n" if "F" in flags else f"{f} () \n{{ \n    ...\n}}\n")
            return 0
        namen = sorted(set(sh.vars) | set(sh.arrays))
        for n in namen:
            if "x" in flags and n not in sh.export or "r" in flags and n not in sh.readonly:
                continue
            io.out(_declare_zeile(sh, n) + "\n")
        return 0
    rc = 0
    for a in args:
        m = re.match(r"([A-Za-z_]\w*)(\[[^\]]*\])?(\+?)=(.*)$", a, re.S)
        n = m.group(1) if m else a
        if not re.fullmatch(r"[A-Za-z_]\w*", n):
            _fehler(sh, io, name, f"`{a}': not a valid identifier")
            rc = 1
            continue
        if "p" in flags and not m:
            if n in sh.vars or n in sh.arrays:
                io.out(_declare_zeile(sh, n) + "\n")
            else:
                _fehler(sh, io, name, f"{n}: not found")
                rc = 1
            continue
        if name == "local" or (name in ("declare", "typeset") and sh.func_tiefe and "g" not in flags):
            if sh.lokal:
                sh.lokal[-1].setdefault(n, sh.vars.get(n))
        if n in sh.readonly and m:
            _fehler(sh, io, name, f"{n}: readonly variable")
            rc = 1
            continue
        if m:
            wert = m.group(4)
            if "a" in flags and wert.startswith("(") and wert.endswith(")"):
                sh._zuweisen(n, wert[1:-1].split(), False)
            else:
                if "i" in flags:
                    try:
                        wert = str(sh.arith(wert))
                    except S.ExpandError:
                        wert = "0"
                if "u" in flags:
                    wert = wert.upper()
                if "l" in flags:
                    wert = wert.lower()
                sh._zuweisen(n, wert, bool(m.group(3)))
        elif "a" in flags:
            sh.arrays.setdefault(n, {})
        elif n not in sh.vars and name == "local":
            sh.vars[n] = ""
        if "x" in flags:
            sh.export.add(n)
        if "x" in weg:
            sh.export.discard(n)
        if "r" in flags:
            sh.readonly.add(n)
    return rc


def b_readonly(sh, argv, io):
    args = argv[1:]
    if not args or args == ["-p"]:
        for n in sorted(sh.readonly):
            io.out(_declare_zeile(sh, n) + "\n")
        return 0
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            sh.setv(k, v)
            sh.readonly.add(k)
        else:
            sh.readonly.add(a)
    return 0


def b_umask(sh, argv, io):
    args = argv[1:]
    if not args:
        io.out(f"{sh.umask:04o}\n")
        return 0
    if args[0] == "-S":
        m = ~sh.umask & 0o777
        teile = []
        for wer, shift in (("u", 6), ("g", 3), ("o", 0)):
            r = (m >> shift) & 7
            teile.append(wer + "=" + ("r" if r & 4 else "") + ("w" if r & 2 else "") + ("x" if r & 1 else ""))
        io.out(",".join(teile) + "\n")
        return 0
    if re.fullmatch(r"[0-7]{1,4}", args[0]):
        sh.umask = int(args[0], 8)
        return 0
    _fehler(sh, io, "umask", f"{args[0]}: octal number out of range" if args[0].isdigit() else
            f"`{args[0][:1]}': invalid symbolic mode operator")
    return 1


def b_ulimit(sh, argv, io):
    if "-a" in argv:
        io.out("""real-time non-blocking time  (microseconds, -R) unlimited
core file size              (blocks, -c) 0
data seg size               (kbytes, -d) unlimited
scheduling priority                 (-e) 0
file size                   (blocks, -f) unlimited
pending signals                     (-i) 2061593
max locked memory           (kbytes, -l) 65982656
max memory size             (kbytes, -m) unlimited
open files                          (-n) 1024
pipe size                (512 bytes, -p) 8
POSIX message queues         (bytes, -q) 819200
real-time priority                  (-r) 0
stack size                  (kbytes, -s) 8192
cpu time                   (seconds, -t) unlimited
max user processes                  (-u) 2061593
virtual memory              (kbytes, -v) unlimited
file locks                          (-x) unlimited
""")
        return 0
    if "-n" in argv and len(argv) == 2:
        io.out("1024\n")
        return 0
    if len(argv) == 1:
        io.out("unlimited\n")
    return 0


def b_times(sh, argv, io):
    d = time.time() - sh.start
    io.out(f"0m{min(d, 59) * 0.002:.3f}s 0m{min(d, 59) * 0.001:.3f}s\n0m0.000s 0m0.000s\n")
    return 0


def b_trap(sh, argv, io):
    args = argv[1:]
    if not args or args == ["-p"]:
        for sig, cmd in sorted(sh.traps.items()):
            io.out(f"trap -- {_sq(cmd)} {sig}\n")
        return 0
    if args[0] == "-l":
        from .progs.base import SIGNALE
        zeilen = []
        for i, (num, name) in enumerate(sorted((v, k) for k, v in SIGNALE.items())):
            zeilen.append(f"{num:2d}) SIG{name}")
        io.out("\t".join(zeilen) + "\n")
        return 0
    cmd, sigs = args[0], args[1:]
    for s in sigs:
        s = s.upper().removeprefix("SIG")
        if cmd == "-":
            sh.traps.pop(s, None)
        else:
            sh.traps[s] = cmd
    return 0


SHOPT = {"autocd": False, "cdable_vars": False, "cdspell": False, "checkhash": False, "checkjobs": False,
         "checkwinsize": True, "cmdhist": True, "dotglob": False, "expand_aliases": True, "extglob": True,
         "extquote": True, "failglob": False, "globstar": False, "histappend": True, "nocaseglob": False,
         "nocasematch": False, "nullglob": False, "progcomp": True, "promptvars": True, "sourcepath": True}


def b_shopt(sh, argv, io):
    args = argv[1:]
    setzen = None
    q = False
    while args and args[0].startswith("-"):
        a = args.pop(0)
        if a == "-s":
            setzen = True
        elif a == "-u":
            setzen = False
        elif a == "-q":
            q = True
        elif a in ("-p", "-o"):
            pass
        else:
            return _ungueltige_option(sh, io, "shopt", a[:2], "shopt [-pqsu] [-o] [optname ...]")
    werte = dict(SHOPT)
    for k in werte:
        if k in sh.opts:
            werte[k] = True
    if not args:
        for k, v in sorted(werte.items()):
            if setzen is None or setzen == v:
                io.out(f"{k:<15}\t{'on' if v else 'off'}\n")
        return 0
    rc = 0
    for a in args:
        if a not in werte:
            _fehler(sh, io, "shopt", f"{a}: invalid shell option name")
            rc = 1
            continue
        if setzen is None:
            if not q:
                io.out(f"{a:<15}\t{'on' if werte[a] else 'off'}\n")
            rc = rc or (0 if werte[a] else 1)
        elif setzen:
            sh.opts.add(a)
        else:
            sh.opts.discard(a)
    return rc


def b_test(sh, argv, io):
    name = argv[0]
    args = argv[1:]
    if name == "[":
        if not args or args[-1] != "]":
            _fehler(sh, io, "[", "missing `]'")
            return 2
        args = args[:-1]
    try:
        return 0 if S.test_eval(sh, args) else 1
    except S.TestFehler as e:
        _fehler(sh, io, name, str(e))
        return 2


def b_hash(sh, argv, io):
    args = argv[1:]
    if args == ["-r"]:
        sh.hash.clear()
        return 0
    if not args:
        if not sh.hash:
            io.out("hash: hash table empty\n")
            return 0
        io.out("hits\tcommand\n")
        for k, p in sorted(sh.hash.items()):
            io.out(f"   1\t{p}\n")
        return 0
    rc = 0
    for a in args:
        p = _pfad_von(sh, a)
        if p:
            sh.hash[a] = p[0]
        else:
            _fehler(sh, io, "hash", f"{a}: not found")
            rc = 1
    return rc


def b_pushd(sh, argv, io):
    args = argv[1:]
    if not args:
        if not sh.dirstack:
            _fehler(sh, io, "pushd", "no other directory")
            return 1
        ziel = sh.dirstack.pop(0)
    else:
        ziel = args[0]
    alt = sh.cwd
    rc = b_cd(sh, ["cd", ziel], _StillIO(io))
    if rc:
        _fehler(sh, io, "pushd", f"{ziel}: No such file or directory")
        return 1
    sh.dirstack.insert(0, alt)
    return b_dirs(sh, ["dirs"], io)


def b_popd(sh, argv, io):
    if not sh.dirstack:
        _fehler(sh, io, "popd", "directory stack empty")
        return 1
    ziel = sh.dirstack.pop(0)
    b_cd(sh, ["cd", ziel], _StillIO(io))
    return b_dirs(sh, ["dirs"], io)


def b_dirs(sh, argv, io):
    if "-c" in argv:
        sh.dirstack.clear()
        return 0
    home = sh.home()

    def kurz(p):
        return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p
    liste = [sh.cwd] + sh.dirstack
    if "-v" in argv:
        for i, p in enumerate(liste):
            io.out(f"{i:2d}  {kurz(p)}\n")
    else:
        io.out(" ".join(kurz(p) for p in liste) + "\n")
    return 0


class _StillIO(S.IO):
    def __init__(self, io):
        super().__init__(io.fds)

    def out(self, t):
        pass


def b_getopts(sh, argv, io):
    if len(argv) < 3:
        io.err("getopts: usage: getopts optstring name [arg ...]\n")
        return 2
    spec, var = argv[1], argv[2]
    args = argv[3:] if len(argv) > 3 else sh.args
    optind = int(sh.vars.get("OPTIND", "1") or 1)
    pos = getattr(sh, "_getopts_pos", 1)
    if optind > len(args):
        sh.setv(var, "?")
        return 1
    a = args[optind - 1]
    if a == "--":
        sh.setv("OPTIND", str(optind + 1))
        sh.setv(var, "?")
        return 1
    if not a.startswith("-") or a == "-":
        sh.setv(var, "?")
        return 1
    ch = a[pos]
    still = spec.startswith(":")
    idx = spec.find(ch)
    naechst = pos + 1
    if idx < 0 or ch == ":":
        sh.setv(var, "?")
        if still:
            sh.setv("OPTARG", ch)
        else:
            io.err(f"{sh.praefix()}illegal option -- {ch}\n")
            sh.vars.pop("OPTARG", None)
    elif idx + 1 < len(spec) and spec[idx + 1] == ":":
        if naechst < len(a):
            sh.setv("OPTARG", a[naechst:])
            naechst = len(a)
        elif optind < len(args):
            sh.setv("OPTARG", args[optind])
            optind += 1
            naechst = len(a)
        else:
            if still:
                sh.setv(var, ":")
                sh.setv("OPTARG", ch)
            else:
                sh.setv(var, "?")
                io.err(f"{sh.praefix()}option requires an argument -- {ch}\n")
            sh.setv("OPTIND", str(optind + 1))
            sh._getopts_pos = 1
            return 0
        sh.setv(var, ch)
    else:
        sh.setv(var, ch)
    if naechst >= len(a):
        sh.setv("OPTIND", str(optind + 1))
        sh._getopts_pos = 1
    else:
        sh._getopts_pos = naechst
    return 0


def b_mapfile(sh, argv, io):
    args = argv[1:]
    trim = False
    while args and args[0].startswith("-"):
        if args[0] == "-t":
            trim = True
        args = args[1:]
    name = args[0] if args else "MAPFILE"
    zeilen = list(io.stdin.lines())
    sh.arrays[name] = {i: (z.rstrip("\n") if trim else z) for i, z in enumerate(zeilen)}
    return 0


def b_compgen(sh, argv, io):
    args = argv[1:]
    praefix = args[-1] if args and not args[-1].startswith("-") and len(args) > 1 else ""
    namen = set()
    if "-c" in args:
        namen |= set(BUILTINS) | set(sh.aliases) | set(sh.funcs) | KEYWORDS
        for d in (sh.vars.get("PATH") or "").split(":"):
            n = sh.fs.finde(d)
            if n is not None and n.typ == "d":
                namen |= {k for k, v in n.eintraege().items() if v.mode & 0o111}
    if "-b" in args:
        namen |= set(BUILTINS)
    if "-a" in args:
        namen |= set(sh.aliases)
    if "-v" in args:
        namen |= set(sh.vars)
    if "-k" in args:
        namen |= KEYWORDS
    for n in sorted(namen):
        if n.startswith(praefix):
            io.out(n + "\n")
    return 0 if namen else 1


def b_noop(sh, argv, io):
    return 0


def b_enable(sh, argv, io):
    for b in sorted(BUILTINS):
        io.out(f"enable {b}\n")
    return 0


def b_caller(sh, argv, io):
    return 1


def b_disown(sh, argv, io):
    if not sh.jobs:
        _fehler(sh, io, "disown", "current: no such job")
        return 1
    sh.jobs.pop()
    return 0


def b_suspend(sh, argv, io):
    _fehler(sh, io, "suspend", "cannot suspend a login shell")
    return 1


def b_fc(sh, argv, io):
    if "-l" in argv:
        for n, h in list(enumerate(sh.hist, 1))[-16:]:
            io.out(f"{n}\t {h}\n")
        return 0
    _fehler(sh, io, "fc", "history specification out of range")
    return 1


def b_bind(sh, argv, io):
    return 0


BUILTINS = {
    ":": b_colon, "true": b_true, "false": b_false, "cd": b_cd, "pwd": b_pwd, "echo": b_echo, "printf": b_printf,
    "export": b_export, "unset": b_unset, "set": b_set, "shift": b_shift, "read": b_read, "exit": b_exit,
    "logout": b_logout, "return": b_return, "break": b_break, "continue": b_continue, "alias": b_alias,
    "unalias": b_unalias, "type": b_type, "command": b_command, "builtin": b_builtin, "source": b_source,
    ".": b_source, "eval": b_eval, "exec": b_exec, "history": b_history, "help": b_help, "jobs": b_jobs, "fg": b_fg,
    "bg": b_bg, "wait": b_wait, "kill": b_kill, "let": b_let, "declare": b_declare, "typeset": b_declare,
    "local": b_declare, "readonly": b_readonly, "umask": b_umask, "ulimit": b_ulimit, "times": b_times,
    "trap": b_trap, "shopt": b_shopt, "test": b_test, "[": b_test, "hash": b_hash, "pushd": b_pushd,
    "popd": b_popd, "dirs": b_dirs, "getopts": b_getopts, "mapfile": b_mapfile, "readarray": b_mapfile,
    "compgen": b_compgen, "complete": b_noop, "compopt": b_noop, "enable": b_enable, "caller": b_caller,
    "disown": b_disown, "suspend": b_suspend, "fc": b_fc, "bind": b_bind,
}
SPECIAL_BUILTINS = {":", ".", "source", "break", "continue", "eval", "exec", "exit", "export", "readonly", "return",
                    "set", "shift", "trap", "unset"}
