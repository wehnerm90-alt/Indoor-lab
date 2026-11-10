"""awk (mawk 1.3.4) – Interpreter fuer die emulierte Shell."""
import math
import random
import re

from ..streams import StringWriter
from ..vfs import FsError
from .base import prog
from .text import RegexFehler, posix_regex


class AwkSyntax(Exception):
    pass


class AwkLaufzeit(Exception):
    pass


class _Next(Exception):
    pass


class _Exit(Exception):
    def __init__(self, code):
        self.code = code


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, v):
        self.v = v


# ---------------------------------------------------------------------------- Lexer
KEYWORDS = {"BEGIN", "END", "function", "func", "if", "else", "while", "for", "do", "break", "continue", "next",
            "nextfile", "exit", "return", "delete", "in", "getline", "print", "printf"}
BUILTIN_F = {"length", "substr", "index", "split", "sub", "gsub", "match", "sprintf", "tolower", "toupper", "int",
             "sqrt", "exp", "log", "sin", "cos", "atan2", "rand", "srand", "system", "close", "fflush"}


def lex(src):
    toks = []
    i, n, zeile = 0, len(src), 1

    def operand_vorher():
        if not toks:
            return False
        t, v = toks[-1][0], toks[-1][1]
        return t in ("num", "str", "name", "func", "builtin", "ere") or v in (")", "]", "$", "++", "--") and t == "op"

    while i < n:
        c = src[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "\\" and i + 1 < n and src[i + 1] == "\n":
            i += 2
            zeile += 1
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "\n":
            toks.append(("nl", "\n", zeile))
            zeile += 1
            i += 1
            continue
        if c.isdigit() or c == "." and i + 1 < n and src[i + 1].isdigit():
            m = re.match(r"0[xX][0-9a-fA-F]+|\d*\.?\d+(?:[eE][-+]?\d+)?\.?", src[i:])
            t = m.group()
            toks.append(("num", float(int(t, 16)) if t.lower().startswith("0x") else float(t.rstrip(".") or 0), zeile))
            i += m.end()
            continue
        if c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", src[i:])
            w = m.group()
            i += m.end()
            if w in KEYWORDS:
                toks.append(("kw", w, zeile))
            elif w in BUILTIN_F:
                toks.append(("builtin", w, zeile))
            elif i < n and src[i] == "(":
                toks.append(("func", w, zeile))
            else:
                toks.append(("name", w, zeile))
            continue
        if c == '"':
            j = i + 1
            out = []
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    e = src[j + 1]
                    m = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "/": "/", "a": "\a", "b": "\b",
                         "f": "\f", "v": "\v"}
                    if e in m:
                        out.append(m[e])
                        j += 2
                        continue
                    mo = re.match(r"[0-7]{1,3}", src[j + 1:])
                    if mo:
                        out.append(chr(int(mo.group(), 8)))
                        j += 1 + mo.end()
                        continue
                    out.append("\\" + e)
                    j += 2
                    continue
                if src[j] == "\n":
                    raise AwkSyntax(f"line {zeile}: runaway string constant \"{''.join(out)[:10]} ...")
                out.append(src[j])
                j += 1
            if j >= n:
                raise AwkSyntax(f"line {zeile}: runaway string constant \"{''.join(out)[:10]} ...")
            toks.append(("str", "".join(out), zeile))
            i = j + 1
            continue
        if c == "/" and not operand_vorher():
            j = i + 1
            out = []
            in_kl = False
            while j < n and (src[j] != "/" or in_kl):
                if src[j] == "\\" and j + 1 < n:
                    out.append(src[j:j + 2] if src[j + 1] != "/" else "/")
                    j += 2
                    continue
                if src[j] == "[":
                    in_kl = True
                elif src[j] == "]":
                    in_kl = False
                if src[j] == "\n":
                    raise AwkSyntax(f"line {zeile}: runaway regular expression /{''.join(out)[:10]} ...")
                out.append(src[j])
                j += 1
            if j >= n:
                raise AwkSyntax(f"line {zeile}: runaway regular expression /{''.join(out)[:10]} ...")
            toks.append(("ere", "".join(out), zeile))
            i = j + 1
            continue
        for op in ("+=", "-=", "*=", "/=", "%=", "^=", "**=", "**", "==", "<=", ">=", "!=", "++", "--", "&&", "||",
                   ">>", "!~", "{", "}", "(", ")", "[", "]", ";", ",", "+", "-", "*", "/", "%", "^", "!", ">", "<",
                   "|", "?", ":", "~", "$", "="):
            if src.startswith(op, i):
                toks.append(("op", "^" if op == "**" else ("^=" if op == "**=" else op), zeile))
                i += len(op)
                break
        else:
            raise AwkSyntax(f"line {zeile}: syntax error at or near {c}")
    toks.append(("eof", None, zeile))
    return toks


# ---------------------------------------------------------------------------- Parser (-> Tupel-AST)
class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0
        self.funcs = {}

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def nimm(self):
        t = self.t[self.i]
        self.i += 1
        return t

    def ist(self, typ, val=None):
        t = self.peek()
        return t[0] == typ and (val is None or t[1] == val)

    def erwarte(self, typ, val=None):
        if not self.ist(typ, val):
            self.fehler()
        return self.nimm()

    def fehler(self):
        t = self.peek()
        if t[0] == "eof":
            raise AwkSyntax(f"line {t[2] + (0 if self.t and self.t[-2][0] == 'nl' else 1)}: missing }} near end of file" if self._offen() else
                            f"line {t[2]}: syntax error at or near end of file")
        v = t[1] if t[0] != "nl" else "end of line"
        if t[0] == "str":
            v = f'"{v}"' if False else v
        raise AwkSyntax(f"line {t[2]}: syntax error at or near {v if not isinstance(v, float) else _num_str(v)}")

    def _offen(self):
        tiefe = 0
        for tt in self.t[:self.i]:
            if tt == ("op", "{", tt[2]):
                tiefe += 1
            elif tt[0] == "op" and tt[1] == "}":
                tiefe -= 1
        return tiefe > 0

    def nls(self):
        while self.ist("nl") or self.ist("op", ";"):
            self.nimm()

    def opt_nl(self):
        while self.ist("nl"):
            self.nimm()

    def programm(self):
        items = []
        self.nls()
        while not self.ist("eof"):
            if self.ist("kw", "function") or self.ist("kw", "func"):
                self.nimm()
                name = self.nimm()[1]
                self.erwarte("op", "(")
                params = []
                while not self.ist("op", ")"):
                    params.append(self.erwarte("name")[1])
                    if self.ist("op", ","):
                        self.nimm()
                        self.opt_nl()
                self.erwarte("op", ")")
                self.opt_nl()
                self.funcs[name] = (params, self.block())
            elif self.ist("kw", "BEGIN") or self.ist("kw", "END"):
                art = self.nimm()[1]
                self.opt_nl()
                if not self.ist("op", "{"):
                    self.fehler()
                items.append((art, None, self.block()))
            else:
                p1 = None if self.ist("op", "{") else self.ausdruck()
                p2 = None
                if self.ist("op", ","):
                    self.nimm()
                    self.opt_nl()
                    p2 = self.ausdruck()
                if self.ist("op", "{"):
                    act = self.block()
                else:
                    act = None
                items.append(("main", (p1, p2) if p2 is not None else p1, act))
            self.nls()
        return items

    def block(self):
        self.erwarte("op", "{")
        st = self.stmts()
        self.erwarte("op", "}")
        return ("block", st)

    def stmts(self):
        out = []
        self.nls()
        while not self.ist("op", "}"):
            if self.ist("eof"):
                self.fehler()
            out.append(self.stmt())
            self.nls()
        return out

    def ende(self):
        if self.ist("op", ";") or self.ist("nl"):
            self.nimm()
        elif not self.ist("op", "}") and not self.ist("eof"):
            self.fehler()

    def einfach_oder_block(self):
        self.opt_nl()
        if self.ist("op", ";"):
            self.nimm()
            return ("block", [])
        return self.stmt()

    def stmt(self):
        t = self.peek()
        if t[0] == "op" and t[1] == "{":
            return self.block()
        if t[0] == "kw":
            w = t[1]
            if w == "if":
                self.nimm()
                self.erwarte("op", "(")
                c = self.ausdruck()
                self.erwarte("op", ")")
                dann = self.einfach_oder_block()
                k = self.i
                self.nls()
                if self.ist("kw", "else"):
                    self.nimm()
                    sonst = self.einfach_oder_block()
                else:
                    self.i = k
                    sonst = None
                return ("if", c, dann, sonst)
            if w == "while":
                self.nimm()
                self.erwarte("op", "(")
                c = self.ausdruck()
                self.erwarte("op", ")")
                if self.ist("op", ";"):
                    self.nimm()
                    return ("while", c, ("block", []))
                return ("while", c, self.einfach_oder_block())
            if w == "do":
                self.nimm()
                body = self.einfach_oder_block()
                self.nls()
                self.erwarte("kw", "while")
                self.erwarte("op", "(")
                c = self.ausdruck()
                self.erwarte("op", ")")
                self.ende()
                return ("do", body, c)
            if w == "for":
                self.nimm()
                self.erwarte("op", "(")
                if self.ist("name") and self.peek(1) == ("kw", "in", self.peek(1)[2]) and self.peek(3)[1] == ")":
                    var = self.nimm()[1]
                    self.nimm()
                    arr = self.nimm()[1]
                    self.erwarte("op", ")")
                    return ("forin", var, arr, self.einfach_oder_block())
                init = None if self.ist("op", ";") else self.einfach_ausdruck()
                self.erwarte("op", ";")
                self.opt_nl()
                cond = None if self.ist("op", ";") else self.ausdruck()
                self.erwarte("op", ";")
                self.opt_nl()
                step = None if self.ist("op", ")") else self.einfach_ausdruck()
                self.erwarte("op", ")")
                if self.ist("op", ";"):
                    self.nimm()
                    return ("for", init, cond, step, ("block", []))
                return ("for", init, cond, step, self.einfach_oder_block())
            if w in ("break", "continue", "next", "nextfile"):
                self.nimm()
                self.ende()
                return (w,)
            if w in ("exit", "return"):
                self.nimm()
                e = None
                if not (self.ist("op", ";") or self.ist("nl") or self.ist("op", "}") or self.ist("eof")):
                    e = self.ausdruck()
                self.ende()
                return (w, e)
            if w == "delete":
                self.nimm()
                name = self.erwarte("name")[1]
                idx = None
                if self.ist("op", "["):
                    self.nimm()
                    idx = self.ausdrucksliste()
                    self.erwarte("op", "]")
                self.ende()
                return ("delete", name, idx)
            if w in ("print", "printf"):
                self.nimm()
                args = []
                if self.ist("op", "(") and self._klammer_druck():
                    self.nimm()
                    args = self.ausdrucksliste()
                    self.erwarte("op", ")")
                elif not (self.ist("op", ";") or self.ist("nl") or self.ist("op", "}") or self.ist("op", ">")
                          or self.ist("op", "|") or self.ist("op", ">>") or self.ist("eof")):
                    args = [self.ausdruck(kein_gt=True)]
                    while self.ist("op", ","):
                        self.nimm()
                        self.opt_nl()
                        args.append(self.ausdruck(kein_gt=True))
                umlt = None
                if self.ist("op", ">") or self.ist("op", ">>") or self.ist("op", "|"):
                    op = self.nimm()[1]
                    umlt = (op, self.konkat(kein_gt=True))
                self.ende()
                return (w, args, umlt)
        e = self.einfach_ausdruck()
        self.ende()
        return ("expr", e)

    def _klammer_druck(self):
        # print (a,b) > "x"  vs  print (a)(b)
        tiefe, j = 0, self.i
        while j < len(self.t):
            t = self.t[j]
            if t[0] == "op" and t[1] in ("(", "["):
                tiefe += 1
            elif t[0] == "op" and t[1] in (")", "]"):
                tiefe -= 1
                if tiefe == 0:
                    nach = self.t[j + 1]
                    return nach[0] in ("nl", "eof") or nach[0] == "op" and nach[1] in (";", "}", ">", ">>", "|")
            elif t[0] in ("nl", "eof"):
                return False
            j += 1
        return False

    def einfach_ausdruck(self):
        return self.ausdruck()

    def ausdrucksliste(self):
        out = [self.ausdruck()]
        while self.ist("op", ","):
            self.nimm()
            self.opt_nl()
            out.append(self.ausdruck())
        return out

    def ausdruck(self, kein_gt=False):
        return self.zuweisung(kein_gt)

    def zuweisung(self, kein_gt):
        links = self.ternaer(kein_gt)
        if self.peek()[0] == "op" and self.peek()[1] in ("=", "+=", "-=", "*=", "/=", "%=", "^="):
            if links[0] not in ("var", "idx", "field"):
                self.fehler()
            op = self.nimm()[1]
            self.opt_nl()
            rechts = self.zuweisung(kein_gt)
            return ("assign", op, links, rechts)
        return links

    def ternaer(self, kein_gt):
        c = self.oder(kein_gt)
        if self.ist("op", "?"):
            self.nimm()
            self.opt_nl()
            a = self.ternaer(kein_gt)
            self.opt_nl()
            self.erwarte("op", ":")
            self.opt_nl()
            b = self.ternaer(kein_gt)
            return ("cond", c, a, b)
        return c

    def oder(self, kein_gt):
        a = self.und(kein_gt)
        while self.ist("op", "||"):
            self.nimm()
            self.opt_nl()
            a = ("or", a, self.und(kein_gt))
        return a

    def und(self, kein_gt):
        a = self.drin(kein_gt)
        while self.ist("op", "&&"):
            self.nimm()
            self.opt_nl()
            a = ("and", a, self.drin(kein_gt))
        return a

    def drin(self, kein_gt):
        a = self.match(kein_gt)
        while self.ist("kw", "in"):
            self.nimm()
            a = ("in", [a], self.erwarte("name")[1])
        return a

    def match(self, kein_gt):
        a = self.vergleich(kein_gt)
        while self.ist("op", "~") or self.ist("op", "!~"):
            op = self.nimm()[1]
            a = ("match", op, a, self.vergleich(kein_gt))
        return a

    def vergleich(self, kein_gt):
        a = self.konkat(kein_gt)
        t = self.peek()
        if t[0] == "op" and t[1] in ("<", "<=", "!=", "==", ">=") or (t[0] == "op" and t[1] == ">" and not kein_gt):
            op = self.nimm()[1]
            a = ("cmp", op, a, self.konkat(kein_gt))
        return a

    def _konkat_start(self):
        t = self.peek()
        if t[0] in ("num", "str", "ere", "name", "func", "builtin"):
            return True
        if t[0] == "op" and t[1] in ("$", "!", "(", "-", "+", "++", "--"):
            return True
        return False

    def konkat(self, kein_gt=False):
        a = self.additiv()
        while self._konkat_start() and not self.ist("kw", "in"):
            if self.peek()[0] == "op" and self.peek()[1] in ("-", "+"):
                break
            a = ("concat", a, self.additiv())
        return a

    def additiv(self):
        a = self.mult()
        while self.ist("op", "+") or self.ist("op", "-"):
            op = self.nimm()[1]
            a = ("bin", op, a, self.mult())
        return a

    def mult(self):
        a = self.unaer()
        while self.ist("op", "*") or self.ist("op", "/") or self.ist("op", "%"):
            op = self.nimm()[1]
            a = ("bin", op, a, self.unaer())
        return a

    def unaer(self):
        if self.ist("op", "!"):
            self.nimm()
            return ("not", self.unaer())
        if self.ist("op", "-"):
            self.nimm()
            return ("neg", self.unaer())
        if self.ist("op", "+"):
            self.nimm()
            return ("pos", self.unaer())
        return self.potenz()

    def potenz(self):
        a = self.postfix()
        if self.ist("op", "^"):
            self.nimm()
            return ("bin", "^", a, self.unaer())
        return a

    def postfix(self):
        if self.ist("op", "++") or self.ist("op", "--"):
            op = self.nimm()[1]
            ziel = self.postfix()
            return ("preinc", op, ziel)
        a = self.primaer()
        if a[0] in ("var", "idx", "field") and (self.ist("op", "++") or self.ist("op", "--")):
            return ("postinc", self.nimm()[1], a)
        return a

    def primaer(self):
        t = self.nimm()
        typ, v = t[0], t[1]
        if typ == "num":
            return ("num", v)
        if typ == "str":
            return ("str", v)
        if typ == "ere":
            return ("ere", v)
        if typ == "op" and v == "$":
            return ("field", self.postfix_ohne_inc())
        if typ == "op" and v == "(":
            e = self.ausdruck()
            if self.ist("op", ","):
                liste = [e]
                while self.ist("op", ","):
                    self.nimm()
                    liste.append(self.ausdruck())
                self.erwarte("op", ")")
                if self.ist("kw", "in"):
                    self.nimm()
                    return ("in", liste, self.erwarte("name")[1])
                self.fehler()
            self.erwarte("op", ")")
            return ("group", e)
        if typ == "op" and v == "-":
            return ("neg", self.unaer())
        if typ == "name":
            if self.ist("op", "["):
                self.nimm()
                idx = self.ausdrucksliste()
                self.erwarte("op", "]")
                return ("idx", v, idx)
            return ("var", v)
        if typ == "func":
            self.erwarte("op", "(")
            args = [] if self.ist("op", ")") else self.ausdrucksliste()
            self.erwarte("op", ")")
            return ("call", v, args)
        if typ == "builtin":
            if self.ist("op", "("):
                self.nimm()
                args = [] if self.ist("op", ")") else self.ausdrucksliste()
                self.erwarte("op", ")")
            else:
                args = []
            return ("builtin", v, args)
        if typ == "kw" and v == "getline":
            ziel = None
            if self.ist("name") or self.ist("op", "$"):
                ziel = self.primaer()
            datei = None
            if self.ist("op", "<"):
                self.nimm()
                datei = self.primaer()
            return ("getline", ziel, datei)
        self.i -= 1
        self.fehler()

    def postfix_ohne_inc(self):
        if self.ist("op", "++") or self.ist("op", "--"):
            op = self.nimm()[1]
            return ("preinc", op, self.primaer())
        if self.ist("op", "-"):
            self.nimm()
            return ("neg", self.primaer())
        return self.primaer()


def _num_str(v):
    if v == int(v) and abs(v) < 1e16:
        return str(int(v))
    return "%.6g" % v


# ---------------------------------------------------------------------------- Werte
class StrNum(str):
    """Feldwert: verhaelt sich als Zahl, wenn er wie eine aussieht."""


NUM_RE = re.compile(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


def zahl(v):
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        return float(v)
    m = re.match(r"\s*[-+]?(0[xX][0-9a-fA-F]+|\d*\.?\d*(?:[eE][-+]?\d+)?)", v)
    t = m.group().strip() if m else ""
    try:
        return float(t) if t not in ("", "+", "-", ".", "+.", "-.") else 0.0
    except ValueError:
        try:
            return float(re.match(r"\s*[-+]?\d*\.?\d*", v).group() or 0)
        except ValueError:
            return 0.0


class Interp:
    def __init__(self, ctx, items, funcs, args_dateien):
        self.ctx = ctx
        self.items = items
        self.funcs = funcs
        self.vars = {"FS": " ", "OFS": " ", "ORS": "\n", "RS": "\n", "NR": 0.0, "NF": 0.0, "FNR": 0.0,
                     "FILENAME": "", "SUBSEP": "\x1c", "CONVFMT": "%.6g", "OFMT": "%.6g", "RSTART": 0.0,
                     "RLENGTH": -1.0, "ENVIRON": dict(ctx.env)}
        self.arrays = {"ENVIRON": dict(ctx.env)}
        self.felder = [""]
        self.ausgaben = {}
        self.eingaben = {}
        self.range_aktiv = {}
        self.lokale = []
        self.rx_cache = {}
        self.dateien = args_dateien
        self.datei_idx = 0
        self.akt_reader = None
        self.exit_code = 0

    # --- Felder ---
    def setze_0(self, text):
        self.felder = [text]
        fs = self.str(self.vars["FS"])
        if fs == " ":
            teile = text.split()
        elif len(fs) == 1 and fs != " ":
            teile = text.split(fs) if text != "" else []
        else:
            teile = re.split(self.rx(fs), text) if text != "" else []
        self.felder += [StrNum(t) for t in teile]
        self.vars["NF"] = float(len(teile))

    def feld(self, i):
        i = int(zahl(i))
        if i < 0:
            raise AwkLaufzeit(f"trying to access out of range field {i}")
        if i == 0:
            return StrNum(self.felder[0])
        return self.felder[i] if i < len(self.felder) else ""

    def setze_feld(self, i, v):
        i = int(zahl(i))
        if i == 0:
            self.setze_0(self.str(v))
            return
        while len(self.felder) <= i:
            self.felder.append("")
        self.felder[i] = v
        self.vars["NF"] = float(max(int(zahl(self.vars["NF"])), i))
        self._neu_0()

    def _neu_0(self):
        nf = int(zahl(self.vars["NF"]))
        f = self.felder[1:nf + 1]
        f += [""] * (nf - len(f))
        self.felder = [self.felder[0]] + f
        self.felder[0] = self.str(self.vars["OFS"]).join(self.str(x) for x in f)

    # --- Konvertierung ---
    def str(self, v, fmt=None):
        if isinstance(v, float):
            if v == int(v) and abs(v) < 1e16:
                return str(int(v))
            if math.isinf(v):
                return "inf" if v > 0 else "-inf"
            if math.isnan(v):
                return "nan"
            try:
                return (fmt or self.vars["CONVFMT"]) % v
            except (TypeError, ValueError):
                return "%.6g" % v
        if isinstance(v, dict):
            raise AwkLaufzeit("can't use array in scalar context")
        return v

    def bool(self, v):
        if isinstance(v, float):
            return v != 0
        if isinstance(v, StrNum) and NUM_RE.match(v):
            return zahl(v) != 0
        return v != ""

    def rx(self, muster):
        r = self.rx_cache.get(muster)
        if r is None:
            try:
                r = re.compile(posix_regex(muster, True))
            except (RegexFehler, re.error) as e:
                raise AwkLaufzeit(f"regular expression compile failed (missing operand)\n{muster}")
            self.rx_cache[muster] = r
        return r

    # --- Variablen ---
    def get(self, name):
        for frame in reversed(self.lokale[-1:]):
            if name in frame:
                return frame[name]
        if name in self.arrays:
            return self.arrays[name]
        if name == "NF":
            return self.vars["NF"]
        return self.vars.get(name, "")

    def set(self, name, v):
        if self.lokale and name in self.lokale[-1]:
            self.lokale[-1][name] = v
            return
        if name == "NF":
            self.vars["NF"] = float(int(zahl(v)))
            self._neu_0()
            return
        self.vars[name] = v

    def array(self, name):
        if self.lokale and name in self.lokale[-1]:
            a = self.lokale[-1][name]
            if not isinstance(a, dict):
                a = {}
                self.lokale[-1][name] = a
            return a
        if name in self.vars and not isinstance(self.vars[name], dict) and self.vars[name] != "":
            raise AwkLaufzeit(f"type error in arg({name}) in function: scalar used as array")
        return self.arrays.setdefault(name, {})

    def idx_key(self, liste):
        return self.str(self.vars["SUBSEP"]).join(self.str(self.ev(e)) for e in liste)

    # --- Ausdruecke ---
    def ev(self, e):
        k = e[0]
        if k == "num":
            return e[1]
        if k == "str":
            return e[1]
        if k == "ere":
            return 1.0 if self.rx(e[1]).search(self.felder[0]) else 0.0
        if k == "group":
            return self.ev(e[1])
        if k == "var":
            return self.get(e[1])
        if k == "field":
            return self.feld(self.ev(e[1]))
        if k == "idx":
            a = self.array(e[1])
            key = self.idx_key(e[2])
            if key not in a:
                a[key] = ""
            return a[key]
        if k == "assign":
            op, ziel, rechts = e[1], e[2], e[3]
            v = self.ev(rechts)
            if op != "=":
                alt = zahl(self.ev(ziel))
                v = self._arith(op[0], alt, zahl(v))
            elif isinstance(v, StrNum):
                pass
            self._zuweisen(ziel, v)
            return v
        if k == "cond":
            return self.ev(e[2]) if self.bool(self.ev(e[1])) else self.ev(e[3])
        if k == "or":
            return 1.0 if self.bool(self.ev(e[1])) or self.bool(self.ev(e[2])) else 0.0
        if k == "and":
            return 1.0 if self.bool(self.ev(e[1])) and self.bool(self.ev(e[2])) else 0.0
        if k == "in":
            return 1.0 if self.idx_key(e[1]) in self.array(e[2]) else 0.0
        if k == "match":
            s = self.str(self.ev(e[2]))
            r = e[3]
            muster = r[1] if r[0] == "ere" else self.str(self.ev(r))
            treffer = bool(self.rx(muster).search(s))
            return 1.0 if treffer == (e[1] == "~") else 0.0
        if k == "cmp":
            a, b = self.ev(e[2]), self.ev(e[3])
            op = e[1]
            if self._numerisch(a) and self._numerisch(b):
                x, y = zahl(a), zahl(b)
            else:
                x, y = self.str(a), self.str(b)
            return 1.0 if {"<": x < y, "<=": x <= y, "==": x == y, "!=": x != y, ">=": x >= y, ">": x > y}[op] else 0.0
        if k == "concat":
            return self.str(self.ev(e[1])) + self.str(self.ev(e[2]))
        if k == "bin":
            return self._arith(e[1], zahl(self.ev(e[2])), zahl(self.ev(e[3])))
        if k == "not":
            return 0.0 if self.bool(self.ev(e[1])) else 1.0
        if k == "neg":
            return -zahl(self.ev(e[1]))
        if k == "pos":
            return zahl(self.ev(e[1]))
        if k == "preinc":
            v = zahl(self.ev(e[2])) + (1 if e[1] == "++" else -1)
            self._zuweisen(e[2], v)
            return v
        if k == "postinc":
            alt = zahl(self.ev(e[2]))
            self._zuweisen(e[2], alt + (1 if e[1] == "++" else -1))
            return alt
        if k == "builtin":
            return self._builtin(e[1], e[2])
        if k == "call":
            return self._call(e[1], e[2])
        if k == "getline":
            return self._getline(e[1], e[2])
        if k == "pipe_getline":
            return self._pipe_getline(e[1], e[2])
        raise AwkLaufzeit(f"unknown node {k}")

    @staticmethod
    def _numerisch(v):
        if isinstance(v, float):
            return True
        if isinstance(v, StrNum):
            return bool(NUM_RE.match(v))
        return False

    def _arith(self, op, a, b):
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                if a == 0:
                    return float("nan")
                return math.copysign(float("inf"), a)
            return a / b
        if op == "%":
            if b == 0:
                return float("nan")
            return math.fmod(a, b)
        if op == "^":
            try:
                return float(a ** b)
            except (OverflowError, ZeroDivisionError):
                return float("inf")
        raise AwkLaufzeit(op)

    def _zuweisen(self, ziel, v):
        if ziel[0] == "var":
            if isinstance(self.get(ziel[1]), dict):
                raise AwkLaufzeit(f"type error in assignment: array {ziel[1]} used as scalar")
            self.set(ziel[1], v)
        elif ziel[0] == "idx":
            self.array(ziel[1])[self.idx_key(ziel[2])] = v
        elif ziel[0] == "field":
            self.setze_feld(self.ev(ziel[1]), v)
        elif ziel[0] == "group":
            self._zuweisen(ziel[1], v)

    def _sprintf(self, fmt, args):
        out, i, a = [], 0, 0
        while i < len(fmt):
            c = fmt[i]
            if c != "%":
                out.append(c)
                i += 1
                continue
            m = re.match(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?([diouxXfFeEgGcs%])", fmt[i:])
            if not m:
                out.append(c)
                i += 1
                continue
            flags, breite, praez, typ = m.groups()
            i += m.end()
            if typ == "%":
                out.append("%")
                continue
            if breite == "*":
                breite = str(int(zahl(args[a]) if a < len(args) else 0))
                a += 1
            if praez == "*":
                praez = str(int(zahl(args[a]) if a < len(args) else 0))
                a += 1
            v = args[a] if a < len(args) else ""
            a += 1
            spec = "%" + (flags or "") + (breite or "") + ("." + praez if praez is not None else "")
            if typ in "diouxX":
                n = zahl(v)
                n = int(n) if not math.isinf(n) and not math.isnan(n) else 0
                if typ in "uoxX" and n < 0:
                    n += 1 << 64
                out.append((spec + ("d" if typ in "iu" else typ)) % n)
            elif typ in "fFeEgG":
                out.append((spec + typ) % zahl(v))
            elif typ == "c":
                if isinstance(v, float):
                    out.append((spec + "s") % chr(int(v)))
                else:
                    out.append((spec + "s") % self.str(v)[:1])
            else:
                out.append((spec + "s") % self.str(v))
        return "".join(out)

    def _builtin(self, name, args):
        ev = lambda i: self.ev(args[i]) if i < len(args) else ""  # noqa: E731
        if name == "length":
            if not args:
                return float(len(self.felder[0]))
            if args[0][0] == "var" and isinstance(self.get(args[0][1]), dict):
                return float(len(self.get(args[0][1])))
            return float(len(self.str(ev(0))))
        if name == "substr":
            s = self.str(ev(0))
            m = zahl(ev(1))
            start = round(m) if not math.isnan(m) else 0
            if len(args) > 2:
                n = zahl(ev(2))
                ende = start + round(n)
            else:
                ende = len(s) + 1
            start = max(start, 1)
            ende = min(ende, len(s) + 1)
            return s[start - 1:ende - 1] if ende > start else ""
        if name == "index":
            return float(self.str(ev(0)).find(self.str(ev(1))) + 1)
        if name == "split":
            s = self.str(ev(0))
            ziel = args[1][1]
            if len(args) > 2:
                fs = args[2][1] if args[2][0] == "ere" else self.str(ev(2))
            else:
                fs = self.str(self.vars["FS"])
            if fs == " ":
                teile = s.split()
            elif len(fs) == 1:
                teile = s.split(fs) if s else []
            else:
                teile = re.split(self.rx(fs), s) if s else []
            arr = self.array(ziel)
            arr.clear()
            for i, t in enumerate(teile, 1):
                arr[str(i)] = StrNum(t)
            return float(len(teile))
        if name in ("sub", "gsub"):
            r = args[0]
            muster = r[1] if r[0] == "ere" else self.str(self.ev(r))
            ers = self.str(ev(1))
            ziel = args[2] if len(args) > 2 else ("field", ("num", 0.0))
            s = self.str(self.ev(ziel))
            rx = self.rx(muster)
            n = 0

            def rep(m):
                nonlocal n
                n += 1
                out, i = [], 0
                while i < len(ers):
                    if ers[i] == "\\" and i + 1 < len(ers) and ers[i + 1] in "&\\":
                        out.append(ers[i + 1])
                        i += 2
                        continue
                    if ers[i] == "&":
                        out.append(m.group(0))
                    else:
                        out.append(ers[i])
                    i += 1
                return "".join(out)
            neu = rx.sub(rep, s, count=0 if name == "gsub" else 1)
            if n:
                self._zuweisen(ziel, neu)
            return float(n)
        if name == "match":
            s = self.str(ev(0))
            r = args[1]
            muster = r[1] if r[0] == "ere" else self.str(self.ev(r))
            m = self.rx(muster).search(s)
            if m:
                self.vars["RSTART"], self.vars["RLENGTH"] = float(m.start() + 1), float(m.end() - m.start())
            else:
                self.vars["RSTART"], self.vars["RLENGTH"] = 0.0, -1.0
            return self.vars["RSTART"]
        if name == "sprintf":
            if not args:
                return ""
            return self._sprintf(self.str(ev(0)), [self.ev(a) for a in args[1:]])
        if name == "tolower":
            return self.str(ev(0)).lower()
        if name == "toupper":
            return self.str(ev(0)).upper()
        if name == "int":
            v = zahl(ev(0))
            return float(math.trunc(v)) if not math.isinf(v) and not math.isnan(v) else v
        if name in ("sqrt", "exp", "log", "sin", "cos"):
            v = zahl(ev(0))
            try:
                return float(getattr(math, name)(v))
            except ValueError:
                return float("nan")
            except OverflowError:
                return float("inf")
        if name == "atan2":
            return math.atan2(zahl(ev(0)), zahl(ev(1)))
        if name == "rand":
            return random.random()
        if name == "srand":
            random.seed(zahl(ev(0)) if args else None)
            return 0.0
        if name == "system":
            self._flush()
            cmd = self.str(ev(0))
            sub = self.ctx.sh.subshell()
            rc = sub.run(cmd, dict(self.ctx.fds))
            return float(rc)
        if name == "close":
            k = self.str(ev(0))
            self.ausgaben.pop(k, None)
            self.eingaben.pop(k, None)
            return 0.0
        if name == "fflush":
            return 0.0
        raise AwkLaufzeit(name)

    def _call(self, name, args):
        if name not in self.funcs:
            raise AwkLaufzeit(f"function {name} never defined")
        params, body = self.funcs[name]
        frame = {}
        for i, p in enumerate(params):
            if i < len(args):
                a = args[i]
                if a[0] == "var" and isinstance(self.get(a[1]), dict) or a[0] == "var" and a[1] in self.arrays:
                    frame[p] = self.array(a[1])
                else:
                    frame[p] = self.ev(a)
            else:
                frame[p] = ""
        self.lokale.append(frame)
        try:
            self.exec(body)
        except _Return as r:
            return r.v
        finally:
            self.lokale.pop()
        return ""

    def _getline(self, ziel, datei):
        if datei is not None:
            name = self.str(self.ev(datei))
            if name not in self.eingaben:
                try:
                    t = self.ctx.lesen(name)
                except FsError:
                    return -1.0
                self.eingaben[name] = iter(t.splitlines())
            try:
                zeile = next(self.eingaben[name])
            except StopIteration:
                return 0.0
            if ziel is None:
                self.setze_0(zeile)
            else:
                self._zuweisen(ziel, StrNum(zeile))
            return 1.0
        zeile = self._naechste_zeile()
        if zeile is None:
            return 0.0
        self.vars["NR"] = zahl(self.vars["NR"]) + 1
        self.vars["FNR"] = zahl(self.vars["FNR"]) + 1
        if ziel is None:
            self.setze_0(zeile)
        else:
            self._zuweisen(ziel, StrNum(zeile))
        return 1.0

    def _pipe_getline(self, cmd_e, ziel):
        cmd = self.str(self.ev(cmd_e))
        if cmd not in self.eingaben:
            w = StringWriter()
            sub = self.ctx.sh.subshell()
            fds = dict(self.ctx.fds)
            fds[1] = w
            sub.run(cmd, fds)
            self.eingaben[cmd] = iter(w.wert().splitlines())
        try:
            zeile = next(self.eingaben[cmd])
        except StopIteration:
            return 0.0
        if ziel is None:
            self.setze_0(zeile)
            self.vars["NR"] = zahl(self.vars["NR"]) + 1
        else:
            self._zuweisen(ziel, StrNum(zeile))
        return 1.0

    # --- Anweisungen ---
    def exec(self, s):
        k = s[0]
        if k == "block":
            for x in s[1]:
                self.exec(x)
        elif k == "expr":
            self.ev(s[1])
        elif k in ("print", "printf"):
            args = s[1]
            if k == "print":
                if not args:
                    text = self.felder[0]
                else:
                    text = self.str(self.vars["OFS"]).join(
                        self.str(v, self.vars["OFMT"]) if isinstance(v, float) else self.str(v)
                        for v in (self.ev(a) for a in args))
                text += self.str(self.vars["ORS"])
            else:
                if not args:
                    raise AwkLaufzeit("printf: no format")
                text = self._sprintf(self.str(self.ev(args[0])), [self.ev(a) for a in args[1:]])
            self._ausgabe(text, s[2])
        elif k == "if":
            if self.bool(self.ev(s[1])):
                self.exec(s[2])
            elif s[3] is not None:
                self.exec(s[3])
        elif k == "while":
            while self.bool(self.ev(s[1])):
                self.ctx.check()
                try:
                    self.exec(s[2])
                except _Break:
                    break
                except _Continue:
                    continue
        elif k == "do":
            while True:
                self.ctx.check()
                try:
                    self.exec(s[1])
                except _Break:
                    break
                except _Continue:
                    pass
                if not self.bool(self.ev(s[2])):
                    break
        elif k == "for":
            if s[1] is not None:
                self.ev(s[1])
            while s[2] is None or self.bool(self.ev(s[2])):
                self.ctx.check()
                try:
                    self.exec(s[4])
                except _Break:
                    break
                except _Continue:
                    pass
                if s[3] is not None:
                    self.ev(s[3])
        elif k == "forin":
            arr = self.array(s[2])
            for key in list(arr.keys()):
                if key not in arr:
                    continue
                self.set(s[1], key)
                try:
                    self.exec(s[3])
                except _Break:
                    break
                except _Continue:
                    continue
        elif k == "break":
            raise _Break()
        elif k == "continue":
            raise _Continue()
        elif k == "next" or k == "nextfile":
            raise _Next()
        elif k == "exit":
            raise _Exit(int(zahl(self.ev(s[1]))) if s[1] is not None else self.exit_code)
        elif k == "return":
            raise _Return(self.ev(s[1]) if s[1] is not None else "")
        elif k == "delete":
            arr = self.array(s[1])
            if s[2] is None:
                arr.clear()
            else:
                arr.pop(self.idx_key(s[2]), None)

    def _ausgabe(self, text, umlt):
        if umlt is None:
            self.ctx.out(text)
            return
        op, ziel_e = umlt
        ziel = self.str(self.ev(ziel_e))
        if op == "|":
            self.ausgaben.setdefault(("|", ziel), []).append(text)
            return
        if ziel in ("/dev/stdout", "-"):
            self.ctx.out(text)
            return
        if ziel == "/dev/stderr":
            self.ctx.err(text)
            return
        key = (">", ziel)
        try:
            if key not in self.ausgaben:
                self.ausgaben[key] = True
                self.ctx.fs.schreiben(ziel, text, self.ctx.cwd, anhaengen=op == ">>")
            else:
                self.ctx.fs.schreiben(ziel, text, self.ctx.cwd, anhaengen=True)
        except FsError as e:
            raise AwkLaufzeit(f"cannot open \"{ziel}\" for output")

    def _flush(self):
        for key, teile in list(self.ausgaben.items()):
            if key[0] == "|":
                from ..streams import StringReader
                sub = self.ctx.sh.subshell()
                fds = dict(self.ctx.fds)
                fds[0] = StringReader("".join(teile))
                sub.run(key[1], fds)
                self.ausgaben.pop(key)

    # --- Eingabe ---
    def _naechste_zeile(self):
        while True:
            if self.akt_reader is None:
                if self.datei_idx >= len(self.dateien):
                    return None
                name = self.dateien[self.datei_idx]
                self.datei_idx += 1
                if "=" in name and re.match(r"[A-Za-z_]\w*=", name):
                    k, v = name.split("=", 1)
                    self.vars[k] = StrNum(v)
                    continue
                self.vars["FILENAME"] = name if name != "-" else ""
                self.vars["FNR"] = 0.0
                if name == "-":
                    self.akt_reader = self.ctx.stdin
                else:
                    try:
                        t = self.ctx.lesen(name)
                    except FsError as e:
                        raise AwkLaufzeit(f"cannot open \"{name}\" ({e.msg})", )
                    from ..streams import StringReader
                    self.akt_reader = StringReader(t)
            rs = self.str(self.vars["RS"])
            if rs == "\n":
                z = self.akt_reader.readline()
                if z == "":
                    self.akt_reader = None
                    continue
                return z[:-1] if z.endswith("\n") else z
            text = self.akt_reader.read()
            self.akt_reader = None
            if rs == "":
                recs = [r for r in re.split(r"\n\n+", text.strip("\n")) if r]
            else:
                recs = text.split(rs)
                if recs and recs[-1] in ("", "\n"):
                    recs.pop()
            self._puffer = getattr(self, "_puffer", []) + recs
            if self._puffer:
                return self._puffer.pop(0)

    def run(self):
        begin = [a for t, p, a in self.items if t == "BEGIN"]
        end = [a for t, p, a in self.items if t == "END"]
        main = [(p, a) for t, p, a in self.items if t == "main"]
        try:
            try:
                for b in begin:
                    self.exec(b)
            except _Next:
                pass
            if main or end:
                while True:
                    self.ctx.check()
                    if getattr(self, "_puffer", None):
                        zeile = self._puffer.pop(0)
                    else:
                        zeile = self._naechste_zeile()
                    if zeile is None:
                        break
                    self.vars["NR"] = zahl(self.vars["NR"]) + 1
                    self.vars["FNR"] = zahl(self.vars["FNR"]) + 1
                    self.setze_0(zeile)
                    try:
                        for idx, (p, a) in enumerate(main):
                            if self._passt(idx, p):
                                if a is None:
                                    self.ctx.out(self.felder[0] + self.str(self.vars["ORS"]))
                                else:
                                    self.exec(a)
                    except _Next:
                        continue
            for e in end:
                self.exec(e)
        except _Exit as ex:
            self.exit_code = ex.code
            if not getattr(self, "_im_end", False):
                self._im_end = True
                try:
                    for e in end:
                        self.exec(e)
                except _Exit as ex2:
                    self.exit_code = ex2.code
        self._flush()
        return self.exit_code

    def _passt(self, idx, p):
        if p is None:
            return True
        if isinstance(p[0], tuple):
            a, b = p
            if not self.range_aktiv.get(idx):
                if self.bool(self.ev(a)):
                    self.range_aktiv[idx] = True
                    if self.bool(self.ev(b)):
                        self.range_aktiv[idx] = False
                    return True
                return False
            if self.bool(self.ev(b)):
                self.range_aktiv[idx] = False
            return True
        return self.bool(self.ev(p))


def _pipe_getline_umbau(node):
    """'cmd | getline [var]' -> eigener Knoten (Parser kennt es als concat/cmp nicht)."""
    return node


USAGE = """Usage: mawk [Options] [Program] [file ...]

Program:
    The -f option value is the name of a file containing program text.
    If no -f option is given, a "--" ends option processing; the following
    parameters are the program text.

Options:
    -f program-file  Program  text is read from file instead of from the
                     command-line.  Multiple -f options are accepted.
    -F value         sets the field separator, FS, to value.
    -v var=value     assigns value to program variable var.
    --               unambiguous end of options.

    Implementation-specific options are prefixed with "-W".  They can be
    abbreviated:

    -W version       show version information and exit.
    -W compat        pre-POSIX 2001.
    -W dump          show assembler-like listing of program and exit.
    -W help          show this message and exit.
    -W interactive   set unbuffered output, line-buffered input.
    -W exec file     use file as program as well as last option.
    -W posix         stricter POSIX checking.
    -W random=number set initial random seed.
    -W sprintf=number adjust size of sprintf buffer.
    -W usage         show this message and exit.
"""


@prog("awk", "mawk", "nawk")
def awk(ctx):
    args = ctx.argv[1:]
    fs = None
    zuweisungen = []
    programm = None
    while args:
        a = args[0]
        if a == "--":
            args = args[1:]
            break
        if a.startswith("-F"):
            fs = a[2:] if len(a) > 2 else (args[1] if len(args) > 1 else None)
            args = args[1:] if len(a) > 2 else args[2:]
            if fs == "t":
                fs = "\t"
            continue
        if a.startswith("-v"):
            v = a[2:] if len(a) > 2 else (args[1] if len(args) > 1 else "")
            args = args[1:] if len(a) > 2 else args[2:]
            if "=" not in v:
                ctx.err(f"awk: improper assignment: -v {v}\n")
                return 2
            zuweisungen.append(v)
            continue
        if a.startswith("-f"):
            datei = a[2:] if len(a) > 2 else (args[1] if len(args) > 1 else "")
            args = args[1:] if len(a) > 2 else args[2:]
            try:
                programm = (programm or "") + ctx.lesen(datei)
            except FsError:
                ctx.err(f"awk: couldn't open file {datei}\n")
                return 2
            continue
        if a.startswith("-W"):
            w = a[2:] or (args[1] if len(args) > 1 else "")
            if w.startswith("v"):
                ctx.out("mawk 1.3.4 20240123\nCopyright 2008-2023,2024, Thomas E. Dickey\n"
                        "Copyright 1991-1996,2014, Michael D. Brennan\n\nrandom-funcs:       internal\n"
                        "regex-funcs:        internal\n\ncompiled limits:\nsprintf buffer      8192\n"
                        "maximum-integer     2147483647\n")
                return 0
            ctx.out(USAGE)
            return 0
        if a in ("--version", "-V"):
            ctx.out("mawk 1.3.4 20240123\nCopyright 2008-2023,2024, Thomas E. Dickey\n"
                    "Copyright 1991-1996,2014, Michael D. Brennan\n")
            return 0
        if a == "--help" or a == "-h":
            ctx.out(USAGE)
            return 0
        if a.startswith("-") and len(a) > 1 and programm is None:
            ctx.err(f"awk: not an option: {a}\n")
            return 2
        break
    if programm is None:
        if not args:
            ctx.err(USAGE)
            return 2
        programm, args = args[0], args[1:]
    try:
        toks = lex(programm)
        toks = _getline_pipes(toks)
        p = Parser(toks)
        items = p.programm()
    except AwkSyntax as e:
        ctx.err(f"awk: {e}\n")
        return 2
    except RecursionError:
        ctx.err("awk: program too complex\n")
        return 2
    it = Interp(ctx, items, p.funcs, args or ["-"])
    if fs is not None:
        it.vars["FS"] = fs
    for z in zuweisungen:
        k, v = z.split("=", 1)
        it.vars[k] = StrNum(S_ansi(v))
    try:
        return it.run()
    except AwkLaufzeit as e:
        ctx.err(f"awk: run time error: {e}\n\tFILENAME=\"{it.vars.get('FILENAME', '')}\" FNR={int(zahl(it.vars['FNR']))} "
                f"NR={int(zahl(it.vars['NR']))}\n" if "cannot open" not in str(e) else f"awk: {e}\n")
        return 2
    except RecursionError:
        ctx.err("awk: function call nesting too deep\n")
        return 2


def S_ansi(v):
    return v.replace("\\n", "\n").replace("\\t", "\t")


def _getline_pipes(toks):
    """Wandelt  <ausdruck> | getline [var]  in einen Pseudo-Builtin um."""
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "op" and t[1] == "|" and i + 1 < len(toks) and toks[i + 1][:2] == ("kw", "getline"):
            # vorherigen einfachen Operanden suchen (String, Variable, Klammerausdruck)
            if out and out[-1][0] in ("str", "name"):
                links = [out.pop()]
            elif out and out[-1][:2] == ("op", ")"):
                tiefe, j = 0, len(out) - 1
                while j >= 0:
                    if out[j][:2] == ("op", ")"):
                        tiefe += 1
                    elif out[j][:2] == ("op", "("):
                        tiefe -= 1
                        if tiefe == 0:
                            break
                    j -= 1
                links = out[j:]
                del out[j:]
            else:
                out.append(t)
                i += 1
                continue
            var = []
            k = i + 2
            if k < len(toks) and toks[k][0] == "name":
                var = [toks[k]]
                k += 1
            z = t[2]
            out += [("builtin", "__pipegetline", z), ("op", "(", z)] + links
            if var:
                out += [("op", ",", z), ("str", var[0][1], z)]
            out += [("op", ")", z)]
            i = k
            continue
        out.append(t)
        i += 1
    return out


_orig_builtin = Interp._builtin


def _builtin_mit_pipe(self, name, args):
    if name == "__pipegetline":
        ziel = ("var", self.ev(args[1])) if len(args) > 1 else None
        return self._pipe_getline(args[0], ziel)
    return _orig_builtin(self, name, args)


Interp._builtin = _builtin_mit_pipe
BUILTIN_F.add("__pipegetline")
