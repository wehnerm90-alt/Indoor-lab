"""GNU-Readline-Nachbau fuer die emulierte Bash (Zeilenbearbeitung, Verlauf, Tab-Vervollstaendigung)."""
import posixpath
import re

from . import syntax as sx
from .builtins import BUILTINS, KEYWORDS
from .tty import EOFInput

CTRL_C = object()

WORT_TRENNER = " \t\n\"'><=;|&(:"


class Readline:
    def __init__(self, tty, sh):
        self.tty = tty
        self.sh = sh
        self.kill = ""
        self.tab_vorher = False

    # ---------------------------------------------------------------- Ausgabe-Hilfen
    def _pos(self, idx):
        cols = max(10, self.tty.cols)
        return divmod(idx, cols)

    def _gehe(self, von, nach):
        r1, c1 = self._pos(von)
        r2, c2 = self._pos(nach)
        s = ""
        if r2 < r1:
            s += f"\x1b[{r1 - r2}A"
        elif r2 > r1:
            s += f"\x1b[{r2 - r1}B"
        if c2 != c1 or r2 != r1:
            s += "\r" + (f"\x1b[{c2}C" if c2 else "")
        return s

    def _zeichnen(self, prompt, buf, pos, alt_cursor):
        """Zeile ab Promptbeginn neu zeichnen; Cursor auf pos. alt_cursor: aktuelle Cursorposition (Index)."""
        cols = max(10, self.tty.cols)
        text = prompt + buf
        s = self._gehe(alt_cursor, 0) + text + "\x1b[J"
        ende = len(text)
        if ende and ende % cols == 0:
            s += " \r\x1b[K"
        s += self._gehe(ende, len(prompt) + pos)
        self.tty.write_raw(s)
        return len(prompt) + pos

    # ---------------------------------------------------------------- Hauptschleife
    def eingabe(self, prompt, verlauf=None):
        """-> str, None (EOF) oder CTRL_C"""
        tty = self.tty
        tty.modus(canon=False, echo=False, isig=False)
        verlauf = verlauf if verlauf is not None else self.sh.hist
        hist = list(verlauf)
        hpos = len(hist)
        entwurf = ""
        buf, pos = "", 0
        tty.write_raw(prompt)
        cur = len(prompt)
        self.tab_vorher = False
        try:
            while True:
                k = tty.read_key()
                if k is None:
                    if not tty.lebt:
                        return None
                    continue
                tab = k == "\t"
                if k in ("\r", "\n"):
                    tty.write_raw(self._gehe(cur, len(prompt) + len(buf)) + "\r\n")
                    return buf
                if k == "\x03":
                    tty.write_raw(self._gehe(cur, len(prompt) + len(buf)) + "^C\r\n")
                    return CTRL_C
                if k == "\x04":
                    if not buf:
                        return None
                    if pos < len(buf):
                        buf = buf[:pos] + buf[pos + 1:]
                elif k in ("\x7f", "\x08"):
                    if pos > 0:
                        buf = buf[:pos - 1] + buf[pos:]
                        pos -= 1
                elif k == "\x1b[3~":
                    if pos < len(buf):
                        buf = buf[:pos] + buf[pos + 1:]
                elif k in ("\x1b[D", "\x1bOD", "\x02"):
                    pos = max(0, pos - 1)
                elif k in ("\x1b[C", "\x1bOC", "\x06"):
                    pos = min(len(buf), pos + 1)
                elif k in ("\x1b[H", "\x1bOH", "\x1b[1~", "\x01", "\x1b[7~"):
                    pos = 0
                elif k in ("\x1b[F", "\x1bOF", "\x1b[4~", "\x05", "\x1b[8~"):
                    pos = len(buf)
                elif k in ("\x1b[1;5D", "\x1bb"):
                    m = re.search(r"[A-Za-z0-9_]+[^A-Za-z0-9_]*$", buf[:pos])
                    pos = m.start() if m else 0
                elif k in ("\x1b[1;5C", "\x1bf"):
                    m = re.match(r"[^A-Za-z0-9_]*[A-Za-z0-9_]+", buf[pos:])
                    pos = pos + m.end() if m else len(buf)
                elif k in ("\x1b[A", "\x1bOA", "\x10"):
                    if hpos > 0:
                        if hpos == len(hist):
                            entwurf = buf
                        hpos -= 1
                        buf = hist[hpos]
                        pos = len(buf)
                    else:
                        tty.write_raw("\a")
                elif k in ("\x1b[B", "\x1bOB", "\x0e"):
                    if hpos < len(hist):
                        hpos += 1
                        buf = hist[hpos] if hpos < len(hist) else entwurf
                        pos = len(buf)
                    else:
                        tty.write_raw("\a")
                elif k == "\x0b":
                    self.kill = buf[pos:]
                    buf = buf[:pos]
                elif k == "\x15":
                    self.kill = buf[:pos]
                    buf, pos = buf[pos:], 0
                elif k == "\x17":
                    m = re.search(r"\S*\s*$", buf[:pos])
                    self.kill = buf[m.start():pos]
                    buf, pos = buf[:m.start()] + buf[pos:], m.start()
                elif k == "\x1bd":
                    m = re.match(r"\s*\S*", buf[pos:])
                    self.kill = buf[pos:pos + m.end()]
                    buf = buf[:pos] + buf[pos + m.end():]
                elif k == "\x19":
                    buf = buf[:pos] + self.kill + buf[pos:]
                    pos += len(self.kill)
                elif k == "\x14":
                    if 0 < pos:
                        p = pos if pos < len(buf) else pos - 1
                        if p > 0:
                            buf = buf[:p - 1] + buf[p] + buf[p - 1] + buf[p + 1:]
                            pos = min(len(buf), p + 1)
                elif k == "\x0c":
                    tty.write_raw("\x1b[H\x1b[2J")
                    cur = 0
                    cur = self._zeichnen(prompt, buf, pos, 0)
                    continue
                elif k == "\x12":
                    r = self._suche(prompt, buf, cur, hist)
                    if r is None:
                        return CTRL_C
                    buf, pos, cur, fertig = r
                    if fertig:
                        return buf
                    cur = self._zeichnen(prompt, buf, pos, cur)
                    continue
                elif tab:
                    r = self._vervollstaendigen(prompt, buf, pos, cur)
                    buf, pos, cur = r
                    self.tab_vorher = True
                    continue
                elif k.startswith("\x1b"):
                    continue
                elif len(k) == 1 and (ord(k) >= 32 or k == "\t"):
                    buf = buf[:pos] + k + buf[pos:]
                    pos += 1
                    if pos == len(buf):
                        # schneller Pfad: nur das Zeichen ausgeben
                        cols = max(10, tty.cols)
                        s = k
                        if (len(prompt) + pos) % cols == 0:
                            s += " \r\x1b[K"
                        tty.write_raw(s)
                        cur = len(prompt) + pos
                        self.tab_vorher = False
                        continue
                else:
                    continue
                self.tab_vorher = tab
                cur = self._zeichnen(prompt, buf, pos, cur)
        except EOFInput:
            return None
        finally:
            tty.modus(canon=True, echo=True, isig=True)

    # ---------------------------------------------------------------- Ctrl+R
    def _suche(self, prompt, buf, cur, hist):
        tty = self.tty
        such = ""
        treffer = ""
        idx = len(hist)

        def zeige(fehl=False):
            nonlocal cur
            text = f"({'failed ' if fehl else ''}reverse-i-search)`{such}': {treffer}"
            tty.write_raw(self._gehe(cur, 0) + "\r" + text + "\x1b[J")
            cur = len(text)
        zeige()
        while True:
            k = tty.read_key()
            if k is None:
                continue
            if k in ("\r", "\n"):
                tty.write_raw("\r\x1b[J" + prompt + treffer + "\r\n")
                return treffer, len(treffer), 0, True
            if k in ("\x07", "\x03"):
                tty.write_raw("\r\x1b[J")
                self._zeichnen(prompt, buf, len(buf), 0)
                return buf, len(buf), len(prompt) + len(buf), False
            if k == "\x12":
                start = idx - 1
            elif k in ("\x7f", "\x08"):
                such = such[:-1]
                start = len(hist) - 1
            elif len(k) == 1 and ord(k) >= 32:
                such += k
                start = idx if treffer and such in treffer else len(hist) - 1
            else:
                tty.write_raw("\r\x1b[J")
                return treffer, len(treffer), self._zeichnen(prompt, treffer, len(treffer), 0), False
            fehl = True
            for j in range(min(start, len(hist) - 1), -1, -1):
                if such and such in hist[j]:
                    idx, treffer, fehl = j, hist[j], False
                    break
            zeige(fehl and bool(such))

    def _neu(self, prompt, text):
        self.tty.write_raw(prompt + text)
        return True

    # ---------------------------------------------------------------- Tab
    def _vervollstaendigen(self, prompt, buf, pos, cur):
        links = buf[:pos]
        # aktuelles Wort bestimmen (unquotiert)
        i = len(links)
        while i > 0 and (links[i - 1] not in WORT_TRENNER or (i > 1 and links[i - 2] == "\\")):
            i -= 1
        wort_roh = links[i:]
        wort = re.sub(r"\\(.)", r"\1", wort_roh)
        vorher = links[:i]
        befehlspos = not vorher.strip() or re.search(r"(^|[;&|(]|&&|\|\||\bthen|\bdo|\belse|\bsudo|\btime)\s*$", vorher)
        erstes = vorher.split()[0] if vorher.split() else ""
        kandidaten = []
        if wort.startswith("$"):
            name = wort[1:].lstrip("{")
            kandidaten = sorted({"$" + ("{" if wort.startswith("${") else "") + v for v in self.sh.vars
                                 if v.startswith(name)})
            ist_dir = {}
        elif befehlspos and "/" not in wort:
            kandidaten = self._befehle(wort)
            ist_dir = {}
        else:
            nur_dirs = erstes in ("cd", "pushd", "rmdir")
            kandidaten, ist_dir = self._dateien(wort, nur_dirs, nur_exec=befehlspos)
        if not kandidaten:
            self.tty.write_raw("\a")
            return buf, pos, cur
        if len(kandidaten) == 1:
            k = kandidaten[0]
            einf = self._quote(k) + ("/" if ist_dir.get(k) else ("" if wort.startswith("$") and False else " "))
            if ist_dir.get(k):
                einf = self._quote(k) + "/"
            neu = vorher + einf
            buf = neu + buf[pos:]
            pos = len(neu)
            cur = self._zeichnen(prompt, buf, pos, cur)
            return buf, pos, cur
        praefix = kandidaten[0]
        for k in kandidaten[1:]:
            j = 0
            while j < len(praefix) and j < len(k) and praefix[j] == k[j]:
                j += 1
            praefix = praefix[:j]
        if len(praefix) > len(wort):
            neu = vorher + self._quote(praefix)
            buf = neu + buf[pos:]
            pos = len(neu)
            cur = self._zeichnen(prompt, buf, pos, cur)
            self.tab_vorher = False
            return buf, pos, cur
        if not self.tab_vorher:
            self.tty.write_raw("\a")
            return buf, pos, cur
        # Liste anzeigen
        anzeige = []
        for k in kandidaten:
            name = k.rstrip("/").split("/")[-1] if "/" in k.rstrip("/") else k
            anzeige.append(name + ("/" if ist_dir.get(k) else ""))
        tty = self.tty
        tty.write_raw(self._gehe(cur, len(prompt) + len(buf)) + "\r\n")
        if len(anzeige) > 100:
            tty.write_raw(f"Display all {len(anzeige)} possibilities? (y or n)")
            while True:
                a = tty.read_key()
                if a in ("y", "Y", " "):
                    tty.write_raw("\r\n")
                    break
                if a in ("n", "N", "\x7f", "\x03", "q"):
                    tty.write_raw("\r\n")
                    tty.write_raw(prompt + buf)
                    return buf, pos, self._zeichnen(prompt, buf, pos, len(prompt) + len(buf))
        tty.write_raw(_spalten(anzeige, tty.cols).replace("\n", "\r\n"))
        tty.write_raw(prompt + buf)
        cur = len(prompt) + len(buf)
        cur = self._zeichnen(prompt, buf, pos, cur)
        return buf, pos, cur

    @staticmethod
    def _quote(s):
        return re.sub(r"([ \t\\'\"$`!&;|<>()*?\[\]{}#~])", r"\\\1", s)

    def _befehle(self, wort):
        sh = self.sh
        namen = set(BUILTINS) | set(sh.aliases) | set(sh.funcs) | KEYWORDS
        for d in (sh.vars.get("PATH") or "").split(":"):
            n = sh.fs.finde(d or ".", sh.cwd)
            if n is not None and n.typ == "d":
                for k, v in n.eintraege().items():
                    if v.typ == "l":
                        v = sh.fs.finde(posixpath.join(d, k)) or v
                    if v.typ != "d" and v.mode & 0o111:
                        namen.add(k)
        return sorted(x for x in namen if x.startswith(wort))

    def _dateien(self, wort, nur_dirs=False, nur_exec=False):
        sh = self.sh
        anzeige_basis = ""
        pfad = wort
        if wort.startswith("~"):
            m = re.match(r"~([^/]*)", wort)
            home = sh.home() if not m.group(1) else (sh.system.home_von(m.group(1)) or None)
            if "/" not in wort:
                # Benutzernamen
                return sorted("~" + u for u in sh.system.users if ("~" + u).startswith(wort)), {}
            if home:
                pfad = home + wort[m.end():]
        d, teil = posixpath.split(pfad)
        if "/" in wort:
            anzeige_basis = wort[:wort.rfind("/") + 1]
        node = sh.fs.finde(d or ".", sh.cwd)
        if node is None or node.typ != "d":
            return [], {}
        erg, ist_dir = [], {}
        eintr = node.eintraege()
        namen = list(eintr.keys())
        for name in sorted(namen):
            if not name.startswith(teil):
                continue
            if name.startswith(".") and not teil.startswith("."):
                continue
            k = eintr[name]
            ziel = k
            if k.typ == "l":
                ziel = sh.fs.finde(posixpath.join(d or ".", name), sh.cwd) or k
            dir_ = ziel.typ == "d"
            if nur_dirs and not dir_:
                continue
            if nur_exec and not dir_ and not (ziel.mode & 0o111):
                continue
            voll = anzeige_basis + name
            erg.append(voll)
            ist_dir[voll] = dir_
        return erg, ist_dir


def _spalten(namen, cols):
    if not namen:
        return ""
    breite = max(len(n) for n in namen) + 2
    ncols = max(1, (cols + 2) // breite) if breite <= cols else 1
    nrows = -(-len(namen) // ncols)
    out = []
    for r in range(nrows):
        zeile = []
        for c in range(ncols):
            i = r + c * nrows
            if i < len(namen):
                zeile.append(namen[i].ljust(breite) if c < ncols - 1 and r + (c + 1) * nrows < len(namen) else namen[i])
        out.append("".join(zeile).rstrip() + "\n")
    return "".join(out)


def unvollstaendig(text, aliases=None):
    try:
        sx.parse(text, aliases)
        return False
    except sx.Incomplete:
        return True
    except sx.SyntaxErr:
        return False
