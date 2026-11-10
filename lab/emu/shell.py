"""Emulierte GNU Bash 5.2: Expansion, Ausfuehrung, Builtins.

Fuehrt nie etwas auf dem echten System aus. Programme sind Python-Funktionen (progs/*), die nur das
virtuelle Dateisystem sehen.
"""
import os
import posixpath
import random
import re
import threading
import time

from . import syntax as sx
from .arith import ArithError, auswerten
from .muster import hat_glob, passt, zu_regex
from .streams import (BrokenPipe, FileWriter, Interrupted, NullReader, NullWriter, Pipe, PipeReader, PipeWriter,
                      StringReader, StringWriter)
from .vfs import FsError

BASH_VERSION = "5.2.21(1)-release"
IFS_STD = " \t\n"


class Exit(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Break(Exception):
    def __init__(self, n):
        super().__init__(n)
        self.n = n


class Continue(Break):
    pass


class Return(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ExpandError(Exception):
    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.msg, self.fatal = msg, fatal


class RedirError(Exception):
    pass


class IO:
    def __init__(self, fds):
        self.fds = fds

    def out(self, t):
        w = self.fds.get(1)
        if w is not None:
            w.write(t)

    def err(self, t):
        w = self.fds.get(2)
        if w is not None:
            w.write(t)

    @property
    def stdin(self):
        return self.fds.get(0) or NullReader()


class Feld:
    """Ein Feld waehrend der Expansion: Liste aus (text, quoted)."""
    __slots__ = ("teile", "erzwungen")

    def __init__(self):
        self.teile = []
        self.erzwungen = False

    def add(self, text, quoted):
        if text:
            self.teile.append((text, quoted))
        if quoted:
            self.erzwungen = True

    def text(self):
        return "".join(t for t, _ in self.teile)

    def quoted_idx(self):
        idx, pos = set(), 0
        for t, q in self.teile:
            if q:
                idx.update(range(pos, pos + len(t)))
            pos += len(t)
        return idx

    def leer(self):
        return not self.teile and not self.erzwungen


def ansi_c(text):
    """$'...'-Escapes."""
    out, i = [], 0
    m = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "v": "\v",
         "\\": "\\", "'": "'", '"': '"', "?": "?"}
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in m:
                out.append(m[n])
                i += 2
                continue
            if n in "01234567":
                mm = re.match(r"[0-7]{1,3}", text[i + 1:])
                out.append(chr(int(mm.group(), 8) & 0xFF))
                i += 1 + mm.end()
                continue
            if n == "x":
                mm = re.match(r"[0-9a-fA-F]{1,2}", text[i + 2:])
                if mm:
                    out.append(chr(int(mm.group(), 16)))
                    i += 2 + mm.end()
                    continue
            if n in "uU":
                mm = re.match(r"[0-9a-fA-F]{1,%d}" % (4 if n == "u" else 8), text[i + 2:])
                if mm:
                    out.append(chr(int(mm.group(), 16)))
                    i += 2 + mm.end()
                    continue
            if n == "c" and i + 2 < len(text):
                out.append(chr(ord(text[i + 2]) & 0x1F))
                i += 3
                continue
            out.append(c + n)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def brace_expand(wort):
    """Klammerexpansion {a,b} {1..5} (ausserhalb von Anfuehrungszeichen)."""
    # erste expandierbare Klammer finden
    i, n = 0, len(wort)
    tiefe_start = None
    while i < n:
        c = wort[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = wort.find("'", i + 1)
            i = (j if j >= 0 else n) + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and wort[j] != '"':
                j += 2 if wort[j] == "\\" else 1
            i = j + 1
            continue
        if c == "$" and i + 1 < n and wort[i + 1] in "{(":
            # ${...} / $(...) ueberspringen
            auf, zu = wort[i + 1], "}" if wort[i + 1] == "{" else ")"
            t, j = 1, i + 2
            while j < n and t:
                if wort[j] == auf:
                    t += 1
                elif wort[j] == zu:
                    t -= 1
                j += 1
            i = j
            continue
        if c == "{":
            tiefe_start = i
            # passendes } suchen, Kommas auf oberster Ebene
            t, j, kommas = 1, i + 1, []
            while j < n and t:
                cj = wort[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "'":
                    k = wort.find("'", j + 1)
                    j = (k if k >= 0 else n) + 1
                    continue
                if cj == "{":
                    t += 1
                elif cj == "}":
                    t -= 1
                    if t == 0:
                        break
                elif cj == "," and t == 1:
                    kommas.append(j)
                j += 1
            if j >= n or t:
                i += 1
                continue
            vor, inhalt, nach = wort[:tiefe_start], wort[tiefe_start + 1:j], wort[j + 1:]
            if kommas:
                teile, s = [], tiefe_start + 1
                for k in kommas:
                    teile.append(wort[s:k])
                    s = k + 1
                teile.append(wort[s:j])
                erg = []
                for t2 in teile:
                    erg.extend(brace_expand(vor + t2 + nach))
                return erg
            m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?", inhalt)
            m2 = re.fullmatch(r"([a-zA-Z])\.\.([a-zA-Z])(?:\.\.(-?\d+))?", inhalt)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                step = abs(int(m.group(3))) if m.group(3) else 1
                step = step or 1
                breite = max(len(m.group(1)), len(m.group(2))) if (
                    m.group(1).lstrip("-").startswith("0") and len(m.group(1).lstrip("-")) > 1 or
                    m.group(2).lstrip("-").startswith("0") and len(m.group(2).lstrip("-")) > 1) else 0
                rng = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
                erg = []
                for v in rng:
                    s = str(v).zfill(breite) if breite and v >= 0 else (
                        "-" + str(-v).zfill(breite - 1) if breite else str(v))
                    erg.extend(brace_expand(vor + s + nach))
                return erg
            if m2:
                a, b = ord(m2.group(1)), ord(m2.group(2))
                step = abs(int(m2.group(3))) if m2.group(3) else 1
                rng = range(a, b + 1, step or 1) if a <= b else range(a, b - 1, -(step or 1))
                erg = []
                for v in rng:
                    erg.extend(brace_expand(vor + chr(v) + nach))
                return erg
            # keine Expansion -> Rest dahinter weiter pruefen
            rest = brace_expand(nach)
            return [vor + "{" + inhalt + "}" + r for r in rest] if len(rest) > 1 or rest != [nach] else [wort]
        i += 1
    return [wort]


class Shell:
    def __init__(self, system, tty, interaktiv=True, login=True):
        self.system = system
        self.fs = system.fs
        self.tty = tty
        self.interaktiv = interaktiv
        self.login = login
        self.vars = {}
        self.arrays = {}
        self.export = set()
        self.readonly = set()
        self.funcs = {}
        self.aliases = {}
        self.cwd = "/root"
        self.status = 0
        self.args = []
        self.arg0 = "-bash" if login else "bash"
        self.opts = {"h", "m", "H", "B"} if interaktiv else {"h", "B"}
        self.pipefail = False
        self.hist = []
        self.jobs = []
        self.job_nr = 0
        self.fds = {}
        self.bg = False
        self.lokal = []
        self.loop_tiefe = 0
        self.func_tiefe = 0
        self.subst_status = None
        self.lineno = 1
        self.script = None
        self.umask = 0o022
        self.dirstack = []
        self.hash = {}
        self.traps = {}
        self.start = time.time()
        self.pid = system.neue_pid()
        self.subshell_tiefe = 0
        self.letztes_arg = ""
        self.letzter_bg = None
        self.aus_ende = False

    # ------------------------------------------------------------ Variablen
    def getv(self, name):
        if name in self.vars:
            return self.vars[name]
        if name in self.arrays:
            a = self.arrays[name]
            return a.get(0, "") if a else ""
        dyn = {
            "RANDOM": lambda: str(random.randint(0, 32767)),
            "SECONDS": lambda: str(int(time.time() - self.start)),
            "LINENO": lambda: str(self.lineno),
            "PWD": lambda: self.cwd,
            "BASHPID": lambda: str(self.pid),
            "EPOCHSECONDS": lambda: str(int(time.time())),
        }
        if name in dyn:
            return dyn[name]()
        return None

    def setv(self, name, wert, export=None):
        if name in self.readonly:
            raise ExpandError(f"{name}: readonly variable")
        if name == "PWD":
            self.vars["PWD"] = wert
            return
        self.arrays.pop(name, None) if False else None
        if name in self.arrays:
            self.arrays[name][0] = wert
        else:
            self.vars[name] = wert
        if export or (export is None and "a" in self.opts):
            self.export.add(name)

    def unsetv(self, name):
        if name in self.readonly:
            return False
        self.vars.pop(name, None)
        self.arrays.pop(name, None)
        self.export.discard(name)
        return True

    def env(self):
        e = {k: self.vars[k] for k in self.export if k in self.vars}
        e["PWD"] = self.cwd
        return e

    def home(self):
        return self.vars.get("HOME", "/root")

    # ------------------------------------------------------------ Hilfen
    def praefix(self):
        if self.script:
            return f"{self.script}: line {self.lineno}: "
        return f"{self.arg0}: " if self.arg0.startswith("-") else "bash: "

    def fehler(self, msg, fds=None):
        w = (fds or self.fds).get(2)
        if w is not None:
            w.write(self.praefix() + msg + "\n")

    def check(self):
        if not self.bg and self.tty is not None and self.tty.sigint.is_set():
            raise Interrupted()

    def subshell(self):
        s = Shell.__new__(Shell)
        s.__dict__.update(self.__dict__)
        s.vars = dict(self.vars)
        s.arrays = {k: dict(v) for k, v in self.arrays.items()}
        s.export = set(self.export)
        s.readonly = set(self.readonly)
        s.funcs = dict(self.funcs)
        s.aliases = dict(self.aliases)
        s.opts = set(self.opts)
        s.lokal = [dict(x) for x in self.lokal]
        s.dirstack = list(self.dirstack)
        s.fds = dict(self.fds)
        s.subshell_tiefe = self.subshell_tiefe + 1
        s.pid = self.system.neue_pid()
        s.jobs = []
        s.hist = self.hist
        return s

    # ------------------------------------------------------------ Einstieg
    def run(self, src, fds=None):
        """Befehlszeile/Skript ausfuehren. Gibt den Exit-Status zurueck."""
        if fds is not None:
            self.fds = fds
        try:
            ast = sx.parse(src, self.aliases)
        except sx.Incomplete:
            if self.script:
                self.fehler("syntax error: unexpected end of file")
            else:
                self.fehler("syntax error: unexpected end of file")
            self.status = 2
            return 2
        except sx.SyntaxErr as e:
            if self.script:
                self.fehler(e.text())
                self.fehler(f"`{src.strip().splitlines()[0] if src.strip() else ''}'")
            else:
                self.fehler(e.text())
            self.status = 2
            return 2
        return self.exec_list(ast)

    # ------------------------------------------------------------ Ausfuehrung
    def exec_list(self, lst):
        st = self.status
        for ao, bg in lst.items:
            if bg:
                st = self.exec_bg(ao)
            else:
                st = self.exec_andor(ao)
            self.status = st
        return st

    def exec_andor(self, ao):
        st = self.exec_pipeline(ao.first, teil_von_bedingung=bool(ao.rest))
        self.status = st
        for i, (op, pl) in enumerate(ao.rest):
            if op == "&&" and st != 0 or op == "||" and st == 0:
                continue
            st = self.exec_pipeline(pl, teil_von_bedingung=i < len(ao.rest) - 1)
            self.status = st
        return st

    def exec_bg(self, ao):
        self.job_nr = max([j["nr"] for j in self.jobs] + [0]) + 1
        sub = self.subshell()
        sub.bg = True
        sub.fds = dict(self.fds)
        sub.fds[0] = NullReader()
        pid = sub.pid
        job = {"nr": self.job_nr, "pid": pid, "cmd": getattr(ao, "src", ""), "done": False, "status": 0, "gemeldet": False}

        def lauf():
            try:
                job["status"] = sub.exec_andor(ao)
            except Exit as e:
                job["status"] = e.code
            except Exception:  # noqa: BLE001
                job["status"] = 1
            job["done"] = True
            self.system.proc_ende(pid)

        self.system.proc_start(pid, job["cmd"].split()[0] if job["cmd"].split() else "bash", job["cmd"])
        threading.Thread(target=lauf, daemon=True).start()
        self.jobs.append(job)
        self.letzter_bg = pid
        if self.interaktiv:
            self.fds[2].write(f"[{job['nr']}] {pid}\n")
        return 0

    def jobs_melden(self):
        if not self.jobs:
            return
        fertig = [j for j in self.jobs if j["done"] and not j["gemeldet"]]
        for k, j in enumerate(self.jobs):
            if j in fertig:
                zeichen = "+" if j is self.jobs[-1] else ("-" if len(self.jobs) > 1 and j is self.jobs[-2] else " ")
                txt = "Done" if j["status"] == 0 else f"Exit {j['status']}"
                self.fds[1].write(f"[{j['nr']}]{zeichen}  {txt:<24}{j['cmd']}\n")
                j["gemeldet"] = True
        self.jobs = [j for j in self.jobs if not j["gemeldet"]]

    def exec_pipeline(self, p, teil_von_bedingung=False):
        t0 = time.time()
        if len(p.cmds) == 1:
            fds = self.fds
            if p.stderr_mit[0]:
                fds = dict(fds)
            st = self.exec_cmd(p.cmds[0], fds, teil_von_bedingung or p.negate)
            stati = [st]
        else:
            n = len(p.cmds)
            pipes = [Pipe() for _ in range(n - 1)]
            stati = [0] * n
            threads = []
            fehler = []

            def stufe(i, sub, cmd, fds):
                try:
                    stati[i] = sub.exec_cmd(cmd, fds, True)
                except BrokenPipe:
                    stati[i] = 141
                except Interrupted:
                    stati[i] = 130
                except Exit as e:
                    stati[i] = e.code
                except (Break, Return):
                    stati[i] = 0
                except Exception as e:  # noqa: BLE001
                    fehler.append(e)
                    stati[i] = 1
                finally:
                    if i < n - 1:
                        pipes[i].schliessen_schreiben()
                    if i > 0:
                        pipes[i - 1].schliessen_lesen()

            for i, cmd in enumerate(p.cmds):
                sub = self.subshell()
                fds = dict(self.fds)
                if i > 0:
                    fds[0] = PipeReader(pipes[i - 1], sub.check)
                if i < n - 1:
                    fds[1] = PipeWriter(pipes[i], sub.check)
                    if p.stderr_mit[i]:
                        fds[2] = fds[1]
                sub.fds = fds
                th = threading.Thread(target=stufe, args=(i, sub, cmd, fds), daemon=True)
                threads.append(th)
                th.start()
            for th in threads:
                while th.is_alive():
                    th.join(0.2)
            if self.tty is not None and self.tty.sigint.is_set() and not self.bg:
                raise Interrupted()
            st = stati[-1]
            if self.pipefail:
                st = next((x for x in reversed(stati) if x), 0)
        self.arrays["PIPESTATUS"] = {i: str(x) for i, x in enumerate(stati)}
        if p.negate:
            st = 0 if st else 1
        if p.timed:
            d = time.time() - t0
            self.fds[2].write(f"\nreal\t{int(d // 60)}m{d % 60:.3f}s\nuser\t0m0.001s\nsys\t0m0.000s\n")
        if st != 0 and "e" in self.opts and not teil_von_bedingung and not p.negate:
            raise Exit(st)
        return st

    def exec_cmd(self, c, fds, in_bedingung=False):
        self.check()
        if isinstance(c, sx.Simple):
            return self.exec_simple(c, fds)
        if isinstance(c, sx.FuncDef):
            self.funcs[c.name] = c.body
            return 0
        try:
            fds2 = self.redirs(getattr(c, "redirs", []), fds)
        except RedirError as e:
            self.fehler(str(e), fds)
            return 1
        alt = self.fds
        self.fds = fds2
        try:
            if isinstance(c, sx.Group):
                if c.subshell:
                    sub = self.subshell()
                    sub.fds = fds2
                    try:
                        return sub.exec_list(c.body)
                    except Exit as e:
                        return e.code
                return self.exec_list(c.body)
            if isinstance(c, sx.If):
                for cond, body in c.zweige:
                    if self._bedingung(cond) == 0:
                        return self.exec_list(body)
                if c.sonst:
                    return self.exec_list(c.sonst)
                return 0
            if isinstance(c, sx.Loop):
                return self._loop(c)
            if isinstance(c, sx.For):
                return self._for(c)
            if isinstance(c, sx.ArithFor):
                return self._arith_for(c)
            if isinstance(c, sx.Case):
                return self._case(c)
            if isinstance(c, sx.DblBracket):
                try:
                    return self.dbl_bracket(c.toks)
                except ExpandError as e:
                    self.fehler(e.msg)
                    return 2
            if isinstance(c, sx.Arith):
                try:
                    v = self.arith(c.expr)
                except ExpandError as e:
                    self.fehler(e.msg)
                    return 1
                return 0 if v else 1
        finally:
            self.fds = alt
        return 0

    def _bedingung(self, lst):
        alt = set(self.opts)
        self.opts.discard("e")
        try:
            return self.exec_list(lst)
        finally:
            if "e" in alt:
                self.opts.add("e")

    def _loop(self, c):
        st = 0
        self.loop_tiefe += 1
        try:
            while True:
                self.check()
                r = self._bedingung(c.cond)
                if (r != 0) != c.until:
                    break
                try:
                    st = self.exec_list(c.body)
                except Continue as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                    continue
                except Break as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                    break
        finally:
            self.loop_tiefe -= 1
        return st

    def _for(self, c):
        if c.words is None:
            woerter = list(self.args)
        else:
            woerter = []
            for w in c.words:
                woerter.extend(self.expand(w))
        st = 0
        self.loop_tiefe += 1
        try:
            for w in woerter:
                self.check()
                self.setv(c.var, w)
                try:
                    st = self.exec_list(c.body)
                except Continue as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                    continue
                except Break as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                    break
        finally:
            self.loop_tiefe -= 1
        return st

    def _arith_for(self, c):
        st = 0
        self.loop_tiefe += 1
        try:
            if c.init:
                self.arith(c.init)
            while True:
                self.check()
                if c.cond and not self.arith(c.cond):
                    break
                try:
                    st = self.exec_list(c.body)
                except Continue as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                except Break as e:
                    if e.n > 1:
                        e.n -= 1
                        raise
                    break
                if c.step:
                    self.arith(c.step)
        except ExpandError as e:
            self.fehler(e.msg)
            return 1
        finally:
            self.loop_tiefe -= 1
        return st

    def _case(self, c):
        wort = self.expand_eins(c.word)
        durchfallen = False
        st = 0
        for muster, body, ende in c.items:
            treffer = durchfallen
            if not treffer:
                for m in muster:
                    feld = self.expand_feld(m)
                    if passt(feld.text(), wort, feld.quoted_idx()):
                        treffer = True
                        break
            if treffer:
                st = self.exec_list(body)
                if ende == ";&":
                    durchfallen = True
                    continue
                if ende == ";;&":
                    durchfallen = False
                    continue
                return st
        return st

    # ------------------------------------------------------------ einfacher Befehl
    def exec_simple(self, c, fds):
        self.lineno = c.lineno
        self.subst_status = None
        argv = []
        try:
            for w in c.words:
                argv.extend(self.expand(w))
            zuw = [self._zuweisung_teile(a) for a in c.assigns]
        except ExpandError as e:
            self.fehler(e.msg, fds)
            if e.fatal and not self.interaktiv:
                raise Exit(127)
            return 1
        if not argv:
            try:
                for name, wert, plus in zuw:
                    self._zuweisen(name, wert, plus)
            except ExpandError as e:
                self.fehler(e.msg, fds)
                return 1
            try:
                self.redirs(c.redirs, fds)
            except RedirError as e:
                self.fehler(str(e), fds)
                return 1
            return self.subst_status or 0
        if "x" in self.opts:
            ps4 = self.vars.get("PS4", "+ ")
            teile = [f"{n}{'+' if p else ''}={self._xq(w)}" for n, w, p in zuw] + [self._xq(a) for a in argv]
            fds[2].write(ps4 + " ".join(teile) + "\n")
        try:
            fds2 = self.redirs(c.redirs, fds)
        except RedirError as e:
            self.fehler(str(e), fds)
            return 1
        name = argv[0]
        self.letztes_arg = argv[-1]
        try:
            if name in self.funcs:
                return self._funktion(name, argv, fds2, zuw)
            from .builtins import BUILTINS, SPECIAL_BUILTINS
            if name in BUILTINS:
                alt = {}
                spezial = name in SPECIAL_BUILTINS
                for n, w, p in zuw:
                    if not spezial:
                        alt[n] = self.vars.get(n)
                    self._zuweisen(n, w, p)
                try:
                    return BUILTINS[name](self, argv, IO(fds2))
                finally:
                    for n, v in alt.items():
                        if v is None:
                            self.vars.pop(n, None)
                        else:
                            self.vars[n] = v
            env = self.env()
            for n, w, p in zuw:
                env[n] = (env.get(n, "") + w) if p else w
            return self.extern(argv, fds2, env)
        except ExpandError as e:
            self.fehler(e.msg, fds)
            return 1

    @staticmethod
    def _xq(w):
        if w == "" or re.search(r"[\s'\"\\$`*?\[\]|&;<>(){}#~!]", w):
            return "'" + w.replace("'", "'\\''") + "'"
        return w

    def _zuweisung_teile(self, a):
        m = sx.ASSIGN_RE.match(a)
        name, idx, plus = m.group(1), m.group(2), bool(m.group(3))
        roh = a[m.end():]
        if roh.startswith("(") and roh.endswith(")") and not idx:
            # Array-Zuweisung a=(x y z)
            innen = roh[1:-1]
            werte = []
            lx = sx.Lexer(innen)
            while True:
                t = lx.next()
                if t.typ == "EOF":
                    break
                if t.typ == "WORD":
                    werte.extend(self.expand(t.val))
            return (name, werte, plus)
        wert = self.expand_eins(roh, zuweisung=True)
        if idx:
            return (name + idx, wert, plus)
        return (name, wert, plus)

    def _zuweisen(self, name, wert, plus):
        if isinstance(wert, list):
            alt = self.arrays.get(name, {}) if plus else {}
            start = (max(alt) + 1) if alt else 0
            for i, w in enumerate(wert):
                alt[start + i] = w
            self.vars.pop(name, None)
            self.arrays[name] = alt
            return
        m = re.match(r"(\w+)\[(.*)\]$", name)
        if m:
            arr = self.arrays.setdefault(m.group(1), {})
            if m.group(1) in self.vars:
                arr[0] = self.vars.pop(m.group(1))
            i = self.arith(m.group(2)) if m.group(2) not in ("@", "*") else 0
            arr[i] = (arr.get(i, "") + wert) if plus else wert
            return
        if plus:
            wert = (self.getv(name) or "") + wert
        self.setv(name, wert)

    def _funktion(self, name, argv, fds, zuw):
        if self.func_tiefe > 1000:
            self.fehler(f"{name}: maximum function nesting level exceeded (1000)")
            return 1
        alt_args, alt_fds = self.args, self.fds
        self.args = argv[1:]
        self.fds = fds
        self.func_tiefe += 1
        self.lokal.append({})
        for n, w, p in zuw:
            self.lokal[-1].setdefault(n, self.vars.get(n))
            self._zuweisen(n, w, p)
        try:
            return self.exec_cmd(self.funcs[name], fds)
        except Return as r:
            return r.code
        finally:
            frame = self.lokal.pop()
            for n, v in frame.items():
                if v is None:
                    self.vars.pop(n, None)
                else:
                    self.vars[n] = v
            self.func_tiefe -= 1
            self.args, self.fds = alt_args, alt_fds

    # ------------------------------------------------------------ externe Programme
    def finde_programm(self, name):
        """-> (pfad, node) oder (None, fehlercode)."""
        if "/" in name:
            try:
                node, _ = self.fs.aufloesen(name, self.cwd)
            except FsError as e:
                return None, e.code
            return name, node
        if name in self.hash:
            n = self.fs.finde(self.hash[name])
            if n is not None:
                return self.hash[name], n
        for d in (self.vars.get("PATH") or "").split(":"):
            d = d or "."
            p = posixpath.join(d, name)
            n = self.fs.finde(p, self.cwd)
            if n is not None and n.typ != "d" and n.mode & 0o111:
                if self.interaktiv:
                    self.hash[name] = p
                return p, n
        return None, "NOTFOUND"

    def extern(self, argv, fds, env):
        name = argv[0]
        pfad, node = self.finde_programm(name)
        if pfad is None:
            if node == "NOTFOUND":
                return self.system.nicht_gefunden(self, name, fds)
            msg = {"ENOENT": "No such file or directory", "ENOTDIR": "Not a directory",
                   "ELOOP": "Too many levels of symbolic links"}.get(node, "No such file or directory")
            self.fehler(f"{name}: {msg}", fds)
            return 127
        if node.typ == "d":
            self.fehler(f"{name}: Is a directory", fds)
            return 126
        if not node.mode & 0o111:
            self.fehler(f"{name}: Permission denied", fds)
            return 126
        return self.system.starte(self, pfad, node, argv, fds, env)

    # ------------------------------------------------------------ Umleitungen
    def redirs(self, redirs, fds):
        if not redirs:
            return fds
        neu = dict(fds)
        for r in redirs:
            op = r.op
            fd = r.fd
            if op in ("<<", "<<-"):
                text = r.body or ""
                if not r.quoted:
                    text = self.expand_heredoc(text)
                neu[0 if fd is None else fd] = StringReader(text)
                continue
            if op == "<<<":
                text = self.expand_eins(r.ziel) + "\n"
                neu[0 if fd is None else fd] = StringReader(text)
                continue
            woerter = self.expand(r.ziel)
            if len(woerter) != 1:
                raise RedirError(f"{r.ziel}: ambiguous redirect")
            ziel = woerter[0]
            if op in (">&", "<&") and (ziel.isdigit() or ziel == "-"):
                quelle = (1 if op == ">&" else 0) if fd is None else fd
                if ziel == "-":
                    neu.pop(quelle, None)
                elif int(ziel) not in neu:
                    raise RedirError(f"{ziel}: Bad file descriptor")
                else:
                    neu[quelle] = neu[int(ziel)]
                continue
            if op in (">&",) and fd is None:
                op = "&>"
            if op in (">", ">|", ">>", "&>", "&>>"):
                w = self._oeffnen_schreiben(ziel, op in (">>", "&>>"))
                if op in ("&>", "&>>"):
                    neu[1] = neu[2] = w
                else:
                    neu[1 if fd is None else fd] = w
            elif op in ("<", "<>"):
                neu[0 if fd is None else fd] = self._oeffnen_lesen(ziel)
        return neu

    def _oeffnen_schreiben(self, ziel, anhaengen):
        if ziel == "/dev/null":
            return NullWriter()
        if ziel in ("/dev/stdout", "/dev/fd/1"):
            return self.fds.get(1) or NullWriter()
        if ziel in ("/dev/stderr", "/dev/fd/2"):
            return self.fds.get(2) or NullWriter()
        if ziel in ("/dev/tty", "/dev/console", "/dev/tty1") and self.tty is not None:
            from .streams import TtyWriter
            return TtyWriter(self.tty)
        try:
            node = self.fs.finde(ziel, self.cwd)
            if node is not None and node.typ == "d":
                raise RedirError(f"{ziel}: Is a directory")
            if node is not None and "C" in self.opts and not anhaengen and node.typ == "-":
                raise RedirError(f"{ziel}: cannot overwrite existing file")
            if node is None or not anhaengen:
                node = self.fs.schreiben(ziel, "", self.cwd, mode=0o666 & ~self.umask)
            return FileWriter(self.fs, node)
        except FsError as e:
            raise RedirError(f"{ziel}: {e.msg}")

    def _oeffnen_lesen(self, ziel):
        if ziel in ("/dev/stdin", "/dev/fd/0"):
            return self.fds.get(0) or NullReader()
        try:
            node, _ = self.fs.aufloesen(ziel, self.cwd)
        except FsError as e:
            raise RedirError(f"{ziel}: {e.msg}")
        if node.typ == "d":
            return StringReader("")
        return StringReader(node.inhalt())

    # ------------------------------------------------------------ Expansion
    def expand(self, wort):
        """Volle Expansion eines Worts -> Liste von Feldern."""
        erg = []
        for w in brace_expand(wort) if "{" in wort else [wort]:
            for f in self._split(self._scan(w)):
                text = f.text()
                if "f" not in self.opts and hat_glob(text, f.quoted_idx()):
                    treffer = self.glob(text, f.quoted_idx())
                    if treffer:
                        erg.extend(treffer)
                        continue
                    if "nullglob" in self.opts:
                        continue
                    if "failglob" in self.opts:
                        raise ExpandError(f"no match: {text}")
                erg.append(self._unescape_glob(text, f.quoted_idx()))
        return erg

    @staticmethod
    def _unescape_glob(text, q):
        return text

    def expand_eins(self, wort, zuweisung=False):
        """Ohne Wortaufteilung und Pfadexpansion (Zuweisungen, case, [[ ]])."""
        f = self._scan(wort, zuweisung=zuweisung, ein_feld=True)
        return "".join(t for t, _q, _s in f if not isinstance(t, list)) if f else ""

    def expand_feld(self, wort):
        teile = self._scan(wort, ein_feld=True)
        fe = Feld()
        for t, q, _s in teile:
            fe.add(t, q)
        return fe

    def expand_heredoc(self, text):
        out, i = [], 0
        while i < len(text):
            c = text[i]
            if c == "\\" and i + 1 < len(text) and text[i + 1] in "$`\\\n":
                if text[i + 1] != "\n":
                    out.append(text[i + 1])
                i += 2
                continue
            if c == "$" or c == "`":
                j = self._exp_ende(text, i)
                for t, _q, _s in self._scan(text[i:j], ein_feld=True, in_dq=True):
                    out.append(t)
                i = j
                continue
            out.append(c)
            i += 1
        return "".join(out)

    def _exp_ende(self, s, i):
        if s[i] == "`":
            j = i + 1
            while j < len(s) and s[j] != "`":
                j += 2 if s[j] == "\\" else 1
            return j + 1
        if i + 1 >= len(s):
            return i + 1
        n = s[i + 1]
        if n in "({":
            lx = sx.Lexer(s)
            try:
                return lx._paren_ende(i + 2) if n == "(" else lx._brace_ende(i + 2)
            except sx.Incomplete:
                return len(s)
        if n == "[":
            tiefe, j = 0, i + 1
            while j < len(s):
                if s[j] == "[":
                    tiefe += 1
                elif s[j] == "]":
                    tiefe -= 1
                    if tiefe == 0:
                        return j + 1
                j += 1
            return len(s)
        if n.isalpha() or n == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[i + 1:])
            return i + 1 + m.end()
        if n in "0123456789?$#@*!-":
            return i + 2
        return i + 1

    def _scan(self, w, zuweisung=False, ein_feld=False, in_dq=False):
        """-> Liste von (text, quoted, splitbar). '$@' als (list, True, False)."""
        teile = []
        i, n = 0, len(w)

        def add(t, q, s=False):
            teile.append((t, q, s))

        # Prozess-Substitution <(cmd)
        if not in_dq and not ein_feld and (w.startswith("<(") or w.startswith(">(")):
            try:
                ende = sx.Lexer(w)._paren_ende(2)
            except sx.Incomplete:
                ende = len(w)
            inhalt = self.cmd_subst(w[2:ende - 1], roh=True) if w[0] == "<" else ""
            nr = 63 - len(self.system.psub) % 40
            self.system.psub[nr] = inhalt
            add(f"/dev/fd/{nr}", True)
            i = ende
        # Tilde
        if not in_dq and w.startswith("~"):
            m = re.match(r"~([A-Za-z0-9_.+-]*)(?=/|$|:)" if zuweisung else r"~([A-Za-z0-9_.+-]*)(?=/|$)", w)
            if m:
                user = m.group(1)
                ziel = None
                if user == "":
                    ziel = self.home()
                elif user == "+":
                    ziel = self.cwd
                elif user == "-":
                    ziel = self.vars.get("OLDPWD")
                else:
                    ziel = self.system.home_von(user)
                if ziel is not None:
                    add(ziel, True)
                    i = m.end()
        while i < n:
            c = w[i]
            if in_dq:
                if c == "\\" and i + 1 < n and w[i + 1] in '$`"\\\n':
                    if w[i + 1] != "\n":
                        add(w[i + 1], True)
                    i += 2
                    continue
                if c == "$" or c == "`":
                    j = self._exp_ende(w, i)
                    self._expansion(w[i:j], True, teile)
                    i = j
                    continue
                add(c, True)
                i += 1
                continue
            if c == "\\":
                if i + 1 < n:
                    if w[i + 1] != "\n":
                        add(w[i + 1], True)
                    i += 2
                else:
                    add("\\", True)
                    i += 1
                continue
            if c == "'":
                j = w.find("'", i + 1)
                j = n if j < 0 else j
                add(w[i + 1:j], True)
                if j == i + 1:
                    add("", True)
                i = j + 1
                continue
            if c == '"':
                j = i + 1
                while j < n and w[j] != '"':
                    if w[j] == "\\":
                        j += 2
                        continue
                    if w[j] == "$" and j + 1 < n and w[j + 1] in "({":
                        j = self._exp_ende(w, j)
                        continue
                    if w[j] == "`":
                        j = self._exp_ende(w, j)
                        continue
                    j += 1
                vorher = len(teile)
                teile.extend(self._scan(w[i + 1:j], in_dq=True))
                if len(teile) == vorher:
                    add("", True)
                i = j + 1
                continue
            if c == "$" and i + 1 < n and w[i + 1] == "'":
                j = i + 2
                while j < n and w[j] != "'":
                    j += 2 if w[j] == "\\" else 1
                add(ansi_c(w[i + 2:j]), True)
                i = j + 1
                continue
            if c == "$" and i + 1 < n and w[i + 1] == '"':  # $"..." (Lokalisierung) wie "..."
                i += 1
                continue
            if c == "$" or c == "`":
                j = self._exp_ende(w, i)
                if j == i + 1 and c == "$":
                    add("$", False)
                    i += 1
                    continue
                self._expansion(w[i:j], False, teile)
                i = j
                continue
            if zuweisung and c == ":" and w[i + 1:i + 2] == "~":
                add(":", False)
                m = re.match(r"~(?=/|$|:)", w[i + 1:])
                if m:
                    add(self.home(), True)
                    i += 2
                    continue
                i += 1
                continue
            add(c, False)
            i += 1
        return teile

    def _expansion(self, text, quoted, teile):
        """text beginnt mit $ oder `. Haengt Teile an."""
        split = not quoted
        if text.startswith("`"):
            inner = text[1:-1] if text.endswith("`") else text[1:]
            inner = re.sub(r"\\([$`\\])", r"\1", inner)
            teile.append((self.cmd_subst(inner), quoted, split))
            return
        if text.startswith("$(("):
            if text.endswith("))"):
                expr = text[3:-2]
                expr = self.expand_heredoc(expr) if ("$" in expr or "`" in expr) else expr
                teile.append((str(self.arith(expr)), quoted, split))
                return
        if text.startswith("$("):
            teile.append((self.cmd_subst(text[2:-1]), quoted, split))
            return
        if text.startswith("$[") and text.endswith("]"):
            teile.append((str(self.arith(text[2:-1])), quoted, split))
            return
        if text.startswith("${"):
            self._param(text[2:-1], quoted, teile)
            return
        name = text[1:]
        if name in ("@", "*"):
            if quoted and name == "@":
                teile.append((list(self.args), True, False))
            elif quoted:
                ifs = self.vars.get("IFS", IFS_STD)
                teile.append(((ifs[:1] if ifs else "").join(self.args), True, False))
            else:
                teile.append((list(self.args), False, True))
            return
        teile.append((self.spezial(name), quoted, split))

    def spezial(self, name):
        if name == "?":
            return str(self.status)
        if name == "$":
            return str(self.system.shell_pid)
        if name == "#":
            return str(len(self.args))
        if name == "!":
            return str(self.letzter_bg) if self.letzter_bg else ""
        if name == "-":
            return "".join(sorted(self.opts & set("aefhmnuvxBCH"))) + ("i" if self.interaktiv else "")
        if name == "_":
            return self.letztes_arg
        if name.isdigit():
            k = int(name)
            if k == 0:
                return self.script or self.arg0
            return self.args[k - 1] if k <= len(self.args) else self._unbound(name, "")
        v = self.getv(name)
        if v is None:
            return self._unbound(name, "")
        return v

    def _unbound(self, name, default):
        if "u" in self.opts:
            raise ExpandError(f"{name}: unbound variable", fatal=True)
        return default

    def _param(self, inhalt, quoted, teile):
        split = not quoted
        # ${#name} / ${#arr[@]}
        if inhalt.startswith("#") and len(inhalt) > 1 and re.fullmatch(r"#([A-Za-z_]\w*|\d+|[@*?$#])(\[[@*]\])?",
                                                                          inhalt):
            n = inhalt[1:]
            m = re.fullmatch(r"(\w+)\[[@*]\]", n)
            if m:
                teile.append((str(len(self.arrays.get(m.group(1), {})) or (1 if m.group(1) in self.vars else 0)),
                              quoted, split))
                return
            if n in ("@", "*", "#"):
                teile.append((str(len(self.args)), quoted, split))
                return
            v = self._wert(n)
            teile.append((str(len(v or "")), quoted, split))
            return
        m_idx = re.fullmatch(r"!([A-Za-z_]\w*)\[[@*]\]", inhalt)
        if m_idx:
            arr = self.arrays.get(m_idx.group(1))
            keys = [str(k) for k in sorted(arr)] if arr else (["0"] if m_idx.group(1) in self.vars else [])
            teile.append((keys, quoted, split) if not quoted else (" ".join(keys), True, False))
            return
        if inhalt.startswith("!"):
            ref = self._wert(inhalt[1:]) or ""
            v = self._wert(ref) if ref else ""
            teile.append((v or "", quoted, split))
            return
        m = re.match(r"([A-Za-z_]\w*|\d+|[@*?$#!_-])(\[[^\]]*\])?", inhalt)
        if not m:
            raise ExpandError(f"${{{inhalt}}}: bad substitution")
        name, idx = m.group(1), m.group(2)
        rest = inhalt[m.end():]
        # Arrays
        if idx and idx[1:-1] in ("@", "*"):
            arr = self.arrays.get(name)
            werte = [arr[k] for k in sorted(arr)] if arr else ([self.vars[name]] if name in self.vars else [])
            if rest:
                werte = [self._op_wert(name, w, rest) for w in werte]
            if idx[1:-1] == "@" and quoted:
                teile.append((werte, True, False))
            elif quoted:
                teile.append((" ".join(werte), True, False))
            else:
                teile.append((werte, False, True))
            return
        if name in ("@", "*") and not idx:
            if rest.startswith(":"):
                m2 = re.match(r":\s*(-?[^:]*)(?::(.*))?$", rest)
                try:
                    off = self.arith(m2.group(1))
                    werte = ([self.script or self.arg0] + self.args)[off:] if off > 0 else self.args
                    if m2.group(2) is not None:
                        werte = werte[:self.arith(m2.group(2))]
                except Exception:  # noqa: BLE001
                    werte = self.args
            else:
                werte = [self._op_wert(name, a, rest) for a in self.args] if rest else list(self.args)
            if quoted and name == "@":
                teile.append((werte, True, False))
            else:
                teile.append((" ".join(werte) if quoted else werte, quoted, split))
            return
        if idx:
            arr = self.arrays.get(name, {})
            try:
                k = self.arith(idx[1:-1])
            except ExpandError:
                k = 0
            if k < 0 and arr:
                k = max(arr) + 1 + k
            wert = arr.get(k) if arr else (self.vars.get(name) if k == 0 else None)
        else:
            wert = self._wert(name)
        if not rest:
            if wert is None:
                wert = self._unbound(name, "")
            teile.append((wert, quoted, split))
            return
        teile.append((self._op(name, wert, rest), quoted, split))

    def _wert(self, name):
        if name.isdigit() or name in "?$#!-_":
            if name.isdigit() and int(name) > len(self.args):
                return None
            return self.spezial(name) if not (name.isdigit() and int(name) > len(self.args)) else None
        return self.getv(name)

    def _op_wert(self, name, wert, rest):
        return self._op(name, wert, rest)

    def _op(self, name, wert, rest):
        m = re.match(r"(:?)([-=+?])(.*)$", rest, re.S)
        if m:
            colon, op, arg = m.groups()
            leer = wert is None or (colon and wert == "")
            if op == "-":
                return self.expand_eins(arg) if leer else wert
            if op == "=":
                if leer:
                    v = self.expand_eins(arg)
                    if name.isdigit():
                        raise ExpandError(f"${name}: cannot assign in this way")
                    self.setv(name, v)
                    return v
                return wert
            if op == "+":
                return "" if leer else self.expand_eins(arg)
            if op == "?":
                if leer:
                    msg = self.expand_eins(arg) if arg else ("parameter null or not set" if colon else "parameter not set")
                    raise ExpandError(f"{name}: {msg}", fatal=True)
                return wert
        wert = wert or ""
        if rest.startswith("##") or rest.startswith("#"):
            gierig = rest.startswith("##")
            pat = self.expand_feld(rest[2 if gierig else 1:])
            rx = zu_regex(pat.text(), pat.quoted_idx(), ganz=False)
            best = None
            for k in range(len(wert) + 1):
                if re.fullmatch(rx, wert[:k], re.S):
                    best = k
                    if not gierig:
                        break
            return wert[best:] if best is not None else wert
        if rest.startswith("%%") or rest.startswith("%"):
            gierig = rest.startswith("%%")
            pat = self.expand_feld(rest[2 if gierig else 1:])
            rx = zu_regex(pat.text(), pat.quoted_idx(), ganz=False)
            best = None
            for k in (range(len(wert) + 1) if gierig else range(len(wert), -1, -1)):
                if re.fullmatch(rx, wert[k:], re.S):
                    best = k
                    break
            return wert[:best] if best is not None else wert
        if rest.startswith("/"):
            alle = rest.startswith("//")
            r = rest[2:] if alle else rest[1:]
            anker = ""
            if r[:1] in "#%" and not alle:
                anker, r = r[0], r[1:]
            teile = re.split(r"(?<!\\)/", r, maxsplit=1)
            pat = self.expand_feld(teile[0])
            ers = self.expand_eins(teile[1]) if len(teile) > 1 else ""
            rx = zu_regex(pat.text(), pat.quoted_idx(), ganz=False)
            if not pat.text():
                return wert
            if anker == "#":
                mm = re.match(rx, wert, re.S)
                return ers + wert[mm.end():] if mm else wert
            if anker == "%":
                for k in range(len(wert) + 1):
                    if re.fullmatch(rx, wert[k:], re.S):
                        return wert[:k] + ers
                return wert
            out, k, ersetzt = [], 0, False
            while k <= len(wert):
                if ersetzt and not alle:
                    out.append(wert[k:])
                    break
                best = None
                for e in range(len(wert), k, -1):
                    if re.fullmatch(rx, wert[k:e], re.S):
                        best = e
                        break
                if best is not None:
                    out.append(ers)
                    k = best
                    ersetzt = True
                    continue
                if k < len(wert):
                    out.append(wert[k])
                k += 1
            return "".join(out)
        if rest.startswith(":"):
            m2 = re.match(r":\s*(-?\s*[^:]*?)\s*(?::(.*))?$", rest, re.S)
            if m2 and not re.match(r":[-=+?]", rest):
                off = self.arith(m2.group(1)) if m2.group(1).strip() else 0
                if off < 0:
                    off = max(0, len(wert) + off) if len(wert) + off >= 0 else len(wert) + 1
                s = wert[off:]
                if m2.group(2) is not None:
                    ln = self.arith(m2.group(2))
                    if ln < 0:
                        if len(s) + ln < 0:
                            raise ExpandError(f"{m2.group(2).strip()}: substring expression < 0")
                        s = s[:len(s) + ln]
                    else:
                        s = s[:ln]
                return s
        if rest in ("^^", "^"):
            return wert.upper() if rest == "^^" else wert[:1].upper() + wert[1:]
        if rest in (",,", ","):
            return wert.lower() if rest == ",," else wert[:1].lower() + wert[1:]
        if rest == "@Q":
            return "'" + wert.replace("'", "'\\''") + "'"
        if rest == "@U":
            return wert.upper()
        if rest == "@L":
            return wert.lower()
        raise ExpandError(f"${{{name}{rest}}}: bad substitution")

    def _split(self, teile):
        """Wortaufteilung -> Liste von Feldern."""
        ifs = self.vars.get("IFS", IFS_STD)
        ws = "".join(c for c in ifs if c in " \t\n")
        andere = "".join(c for c in ifs if c not in " \t\n")
        felder = [Feld()]
        hatte_inhalt = False

        def neues():
            felder.append(Feld())

        for t, q, s in teile:
            if isinstance(t, list):  # "$@" / $@
                if not t:
                    continue
                for k, a in enumerate(t):
                    if k > 0:
                        neues()
                    if s and not q:
                        self._split_text(a, ws, andere, felder, neues)
                    else:
                        felder[-1].add(a, True)
                        if a == "":
                            felder[-1].erzwungen = True
                hatte_inhalt = True
                continue
            if s and not q and ifs != "":
                self._split_text(t, ws, andere, felder, neues)
            else:
                felder[-1].add(t, q)
            hatte_inhalt = True
        if not hatte_inhalt:
            return []
        return [f for f in felder if not f.leer()]

    @staticmethod
    def _split_text(t, ws, andere, felder, neues):
        i = 0
        while i < len(t):
            c = t[i]
            if c in ws:
                j = i
                while j < len(t) and t[j] in ws:
                    j += 1
                if j < len(t) and t[j] in andere:
                    j += 1
                    while j < len(t) and t[j] in ws:
                        j += 1
                if felder[-1].teile or felder[-1].erzwungen:
                    neues()
                i = j
                continue
            if c in andere:
                if not felder[-1].teile and not felder[-1].erzwungen:
                    felder[-1].erzwungen = True
                neues()
                i += 1
                while i < len(t) and t[i] in ws:
                    i += 1
                continue
            felder[-1].add(c, False)
            i += 1

    def glob(self, muster, quoted):
        """Pfadnamen-Expansion -> sortierte Liste oder []."""
        absolut = muster.startswith("/")
        komps = muster.split("/")
        # quoted-Indizes pro Komponente
        q_komps, pos = [], 0
        for k in komps:
            q_komps.append({i - pos for i in quoted if pos <= i < pos + len(k)})
            pos += len(k) + 1
        ergebnisse = ["/" if absolut else ""]
        start = 1 if absolut else 0
        for idx in range(start, len(komps)):
            k, q = komps[idx], q_komps[idx]
            letzter = idx == len(komps) - 1
            neu = []
            if k == "":
                neu = [e + ("" if e.endswith("/") or e == "" else "/") for e in ergebnisse] if letzter else ergebnisse
                ergebnisse = neu
                continue
            for basis in ergebnisse:
                verz = basis or "."
                if not hat_glob(k, q):
                    wort = "".join(ch for i2, ch in enumerate(k))
                    wort = self._entglob(k, q)
                    p = (basis if basis.endswith("/") or basis == "" else basis + "/") + wort
                    if self.fs.finde(p, self.cwd, folgen=False) is not None or self.fs.finde(p, self.cwd):
                        neu.append(p)
                    continue
                node = self.fs.finde(verz, self.cwd)
                if node is None or node.typ != "d":
                    continue
                rx = zu_regex(k, q)
                namen = list(node.eintraege().keys())
                for name in sorted(namen):
                    if name.startswith(".") and not (k.startswith(".") or "dotglob" in self.opts and name not in (".", "..")):
                        continue
                    if re.match(rx, name, re.S):
                        p = (basis if basis.endswith("/") or basis == "" else basis + "/") + name
                        if not letzter:
                            n2 = self.fs.finde(p, self.cwd)
                            if n2 is None or n2.typ != "d":
                                continue
                        neu.append(p)
            ergebnisse = neu
            if not ergebnisse:
                return []
        return sorted(ergebnisse)

    @staticmethod
    def _entglob(k, q):
        out, i = [], 0
        while i < len(k):
            if k[i] == "\\" and i not in q and i + 1 < len(k):
                out.append(k[i + 1])
                i += 2
                continue
            out.append(k[i])
            i += 1
        return "".join(out)

    def cmd_subst(self, src, roh=False):
        sub = self.subshell()
        w = StringWriter()
        sub.fds = dict(self.fds)
        sub.fds[1] = w
        try:
            st = sub.run(src)
        except Exit as e:
            st = e.code
        except (Break, Return):
            st = sub.status
        self.subst_status = st
        self.status = st
        return w.wert() if roh else w.wert().rstrip("\n")

    def arith(self, expr):
        if "$" in expr or "`" in expr:
            expr = self.expand_heredoc(expr)
        try:
            return auswerten(expr, lambda n: self._arith_get(n), lambda n, v: self._arith_set(n, v))
        except ArithError as e:
            raise ExpandError(f"{expr}: {e.msg} (error token is \"{e.token}\")")
        except RecursionError:
            raise ExpandError(f"{expr}: expression recursion level exceeded")

    def _arith_get(self, n):
        m = re.match(r"(\w+)\[(.*)\]$", n)
        if m:
            arr = self.arrays.get(m.group(1), {})
            return arr.get(self.arith(m.group(2)), "")
        v = self.getv(n)
        if v is None:
            if "u" in self.opts:
                raise ExpandError(f"{n}: unbound variable")
            return ""
        return v

    def _arith_set(self, n, v):
        m = re.match(r"(\w+)\[(.*)\]$", n)
        if m:
            self.arrays.setdefault(m.group(1), {})[self.arith(m.group(2))] = v
            return
        self.setv(n, v)

    # ------------------------------------------------------------ [[ ]]
    def dbl_bracket(self, toks):
        self._dt, self._di = toks, 0
        v = self._d_or()
        if self._di < len(self._dt):
            raise ExpandError(f"syntax error in conditional expression: unexpected token `{self._dt[self._di][1]}'")
        return 0 if v else 1

    def _d_peek(self):
        return self._dt[self._di] if self._di < len(self._dt) else (None, None)

    def _d_or(self):
        v = self._d_and()
        while self._d_peek() == ("OP", "||"):
            self._di += 1
            r = self._d_and()
            v = v or r
        return v

    def _d_and(self):
        v = self._d_not()
        while self._d_peek() == ("OP", "&&"):
            self._di += 1
            r = self._d_not()
            v = v and r
        return v

    def _d_not(self):
        if self._d_peek() == ("WORD", "!"):
            self._di += 1
            return not self._d_not()
        return self._d_prim()

    def _d_prim(self):
        t = self._d_peek()
        if t == ("OP", "("):
            self._di += 1
            v = self._d_or()
            if self._d_peek() != ("OP", ")"):
                raise ExpandError("syntax error in conditional expression")
            self._di += 1
            return v
        if t[0] is None:
            raise ExpandError("unexpected argument to conditional unary operator")
        typ, w = t
        nxt = self._dt[self._di + 1] if self._di + 1 < len(self._dt) else (None, None)
        if typ == "WORD" and re.fullmatch(r"-[a-zA-Z]", w) and nxt[0] == "WORD" and not (
                self._di + 2 < len(self._dt) and self._dt[self._di + 2][0] == "WORD"
                and self._dt[self._di + 2][1] in ("==", "=", "!=", "=~") ):
            self._di += 2
            return test_unaer(self, w, self.expand_eins(nxt[1]))
        links = self.expand_eins(w) if typ == "WORD" else w
        self._di += 1
        op = self._d_peek()
        if op[1] in ("==", "=", "!=", "=~", "<", ">", "-eq", "-ne", "-lt", "-le", "-gt", "-ge", "-nt", "-ot", "-ef"):
            self._di += 1
            r = self._d_peek()
            if r[0] is None:
                raise ExpandError(f"unexpected argument `{op[1]}' to conditional binary operator")
            self._di += 1
            if op[1] in ("==", "=", "!="):
                f = self.expand_feld(r[1])
                ok = passt(f.text(), links, f.quoted_idx())
                return ok if op[1] != "!=" else not ok
            if op[1] == "=~":
                # Regex: restliche Woerter bis Operator gehoeren dazu
                muster = self.expand_eins(r[1])
                try:
                    m = re.search(muster, links)
                except re.error:
                    return False
                if m:
                    self.arrays["BASH_REMATCH"] = {i: (g or "") for i, g in enumerate([m.group(0), *m.groups()])}
                else:
                    self.arrays["BASH_REMATCH"] = {}
                return bool(m)
            rechts = self.expand_eins(r[1])
            if op[1] in ("<", ">"):
                return links < rechts if op[1] == "<" else links > rechts
            return test_binaer(self, links, op[1], rechts, arith=True)
        return links != ""

    # ------------------------------------------------------------ Prompt
    def prompt(self, ps="PS1"):
        roh = self.vars.get(ps, "")
        out, i = [], 0
        user = self.vars.get("USER", "root")
        while i < len(roh):
            c = roh[i]
            if c == "\\" and i + 1 < len(roh):
                n = roh[i + 1]
                i += 2
                h = self.system.hostname
                w = self.cwd
                home = self.home()
                if w == home or w.startswith(home.rstrip("/") + "/"):
                    w = "~" + w[len(home.rstrip("/")):] if home != "/" else w
                rep = {"u": user, "h": h.split(".")[0], "H": h, "w": w, "W": "~" if self.cwd == home else (
                    posixpath.basename(self.cwd) or "/"), "$": "#" if user == "root" else "$", "n": "\n",
                       "s": "bash", "v": "5.2", "V": "5.2.21", "\\": "\\", "t": time.strftime("%H:%M:%S"),
                       "T": time.strftime("%I:%M:%S"), "@": time.strftime("%I:%M %p"), "A": time.strftime("%H:%M"),
                       "d": time.strftime("%a %b %d"), "l": "tty1", "j": str(len(self.jobs)), "#": str(len(self.hist) + 1),
                       "!": str(len(self.hist) + 1), "e": "\x1b", "a": "\a", "[": "", "]": ""}
                if n in rep:
                    out.append(rep[n])
                elif n in "01234567":
                    m = re.match(r"[0-7]{1,3}", roh[i - 1:])
                    out.append(chr(int(m.group(), 8)))
                    i += m.end() - 1
                else:
                    out.append("\\" + n)
                continue
            out.append(c)
            i += 1
        text = "".join(out)
        try:
            return self.expand_eins('"' + text.replace('"', '\\"') + '"') if "$" in text or "`" in text else text
        except Exception:  # noqa: BLE001
            return text

    # ------------------------------------------------------------ Verlaufsexpansion
    def verlauf_expansion(self, zeile):
        """!! !n !-n !str !$ -> (neue_zeile, geaendert) oder ExpandError."""
        if "!" not in zeile or "H" not in self.opts:
            return zeile, False
        out, i, geaendert = [], 0, False
        in_sq = False
        while i < len(zeile):
            c = zeile[i]
            if c == "'" and not in_sq:
                in_sq = True
            elif c == "'" and in_sq:
                in_sq = False
            if c == "\\" and i + 1 < len(zeile):
                out.append(zeile[i:i + 2])
                i += 2
                continue
            if c == "!" and not in_sq and i + 1 < len(zeile) and zeile[i + 1] not in " \t\n=(":
                rest = zeile[i + 1:]
                m = re.match(r"!|-?\d+|\$|\*|\^|[^\s:;&|()<>\"'$]+", rest)
                if m:
                    spec = m.group()
                    ev = None
                    if spec in ("!", "$", "*", "^"):
                        ev = self.hist[-1] if self.hist else None
                    elif re.fullmatch(r"-?\d+", spec):
                        k = int(spec)
                        idx = k - 1 if k > 0 else len(self.hist) + k
                        ev = self.hist[idx] if 0 <= idx < len(self.hist) else None
                    else:
                        ev = next((h for h in reversed(self.hist) if h.startswith(spec)), None)
                    if ev is None:
                        raise ExpandError(f"!{spec}: event not found")
                    if spec == "$":
                        ev = ev.split()[-1] if ev.split() else ""
                    elif spec == "*":
                        ev = " ".join(ev.split()[1:])
                    elif spec == "^":
                        ev = ev.split()[1] if len(ev.split()) > 1 else ""
                    out.append(ev)
                    i += 1 + len(spec)
                    geaendert = True
                    continue
            out.append(c)
            i += 1
        return "".join(out), geaendert


# ---------------------------------------------------------------------------- test / [
def test_unaer(sh, op, a):
    fs = sh.fs
    if op in ("-z", "-n"):
        return (a == "") if op == "-z" else (a != "")
    if op == "-v":
        return sh.getv(a) is not None
    if op == "-o":
        return a in sh.opts
    if op == "-t":
        return a.isdigit() and getattr(sh.fds.get(int(a)), "isatty", False)
    if op in ("-h", "-L"):
        n = fs.finde(a, sh.cwd, folgen=False)
        return n is not None and n.typ == "l"
    n = fs.finde(a, sh.cwd) if a else None
    if op == "-e" or op == "-a":
        return n is not None
    if n is None:
        return False
    return {"-f": n.typ == "-", "-d": n.typ == "d", "-c": n.typ == "c", "-b": n.typ == "b", "-p": n.typ == "p",
            "-S": n.typ == "s", "-s": n.size() > 0, "-r": True, "-w": not n.nur_lesen, "-x": bool(n.mode & 0o111) or n.typ == "d",
            "-u": bool(n.mode & 0o4000), "-g": bool(n.mode & 0o2000), "-k": bool(n.mode & 0o1000), "-O": n.uid == 0,
            "-G": n.gid == 0, "-N": n.mtime > n.atime}.get(op, False)


class TestFehler(Exception):
    pass


def _int(x, sh):
    t = x.strip()
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    raise TestFehler(f"{x}: integer expression expected")


def test_binaer(sh, a, op, b, arith=False):
    if op in ("=", "=="):
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op in ("-eq", "-ne", "-lt", "-le", "-gt", "-ge"):
        if arith:
            x, y = sh.arith(a or "0"), sh.arith(b or "0")
        else:
            x, y = _int(a, sh), _int(b, sh)
        return {"-eq": x == y, "-ne": x != y, "-lt": x < y, "-le": x <= y, "-gt": x > y, "-ge": x >= y}[op]
    if op in ("-nt", "-ot", "-ef"):
        na, nb = sh.fs.finde(a, sh.cwd), sh.fs.finde(b, sh.cwd)
        if op == "-ef":
            return na is not None and na is nb
        if op == "-nt":
            return na is not None and (nb is None or na.mtime > nb.mtime)
        return nb is not None and (na is None or na.mtime < nb.mtime)
    raise TestFehler(f"{op}: binary operator expected")


UNAER = {"-a", "-b", "-c", "-d", "-e", "-f", "-g", "-h", "-k", "-p", "-r", "-s", "-t", "-u", "-w", "-x", "-G", "-L",
         "-N", "-O", "-S", "-z", "-n", "-v", "-o"}
BINAER = {"=", "==", "!=", "<", ">", "-eq", "-ne", "-lt", "-le", "-gt", "-ge", "-nt", "-ot", "-ef"}


def test_eval(sh, args):
    """POSIX test-Algorithmus. -> bool, wirft TestFehler."""
    def ausdruck(a):
        n = len(a)
        if n == 0:
            return False
        if n == 1:
            return a[0] != ""
        if n == 2:
            if a[0] == "!":
                return not ausdruck(a[1:])
            if a[0] in UNAER:
                return test_unaer(sh, a[0], a[1])
            raise TestFehler(f"{a[0]}: unary operator expected")
        if n == 3:
            if a[1] in BINAER:
                return test_binaer(sh, a[0], a[1], a[2])
            if a[1] == "-a":
                return ausdruck(a[:1]) and ausdruck(a[2:])
            if a[1] == "-o":
                return ausdruck(a[:1]) or ausdruck(a[2:])
            if a[0] == "!":
                return not ausdruck(a[1:])
            if a[0] == "(" and a[2] == ")":
                return ausdruck(a[1:2])
            raise TestFehler(f"{a[1]}: binary operator expected")
        if n == 4:
            if a[0] == "!":
                return not ausdruck(a[1:])
            if a[0] == "(" and a[3] == ")":
                return ausdruck(a[1:3])
        # allgemein: -o hat geringere Prioritaet als -a
        for op in ("-o", "-a"):
            tiefe = 0
            for i in range(len(a) - 1, -1, -1):
                if a[i] == ")":
                    tiefe += 1
                elif a[i] == "(":
                    tiefe -= 1
                elif a[i] == op and tiefe == 0 and 0 < i < len(a) - 1:
                    l, r = ausdruck(a[:i]), ausdruck(a[i + 1:])
                    return (l or r) if op == "-o" else (l and r)
        if a[0] == "!":
            return not ausdruck(a[1:])
        if a[0] == "(" and a[-1] == ")":
            return ausdruck(a[1:-1])
        raise TestFehler("too many arguments")
    return ausdruck(args)


