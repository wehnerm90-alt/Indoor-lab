"""Datenstroeme fuer emulierte Prozesse: Terminal, Pipe, Datei, String."""
import threading

from .tty import Interrupted


class BrokenPipe(Exception):
    pass


class Writer:
    isatty = False

    def write(self, text):
        raise NotImplementedError

    def close(self):
        pass


class NullWriter(Writer):
    def write(self, text):
        pass


class StringWriter(Writer):
    def __init__(self):
        self.teile = []

    def write(self, text):
        self.teile.append(text)

    def wert(self):
        return "".join(self.teile)


class TtyWriter(Writer):
    isatty = True

    def __init__(self, tty):
        self.tty = tty

    def write(self, text):
        self.tty.write(text)


class FileWriter(Writer):
    def __init__(self, fs, node):
        self.fs, self.node = fs, node

    def write(self, text):
        n = self.node
        if n.typ == "c":
            if n.schreib_fn:
                n.schreib_fn(text)
            return
        if n.schreib_fn:
            n.schreib_fn(text)
            return
        import time
        n.data = (n.data if isinstance(n.data, str) else "") + text
        n.groesse = None
        n.mtime = n.ctime = time.time()


class Pipe:
    MAX = 1 << 16

    def __init__(self):
        self.cond = threading.Condition()
        self.buf = []
        self.laenge = 0
        self.zu_schreiben = False   # Schreibseite geschlossen
        self.zu_lesen = False       # Leseseite geschlossen

    def write(self, text, check=None):
        if not text:
            return
        with self.cond:
            while self.laenge > self.MAX and not self.zu_lesen:
                if check:
                    check()
                self.cond.wait(0.2)
            if self.zu_lesen:
                raise BrokenPipe()
            self.buf.append(text)
            self.laenge += len(text)
            self.cond.notify_all()

    def schliessen_schreiben(self):
        with self.cond:
            self.zu_schreiben = True
            self.cond.notify_all()

    def schliessen_lesen(self):
        with self.cond:
            self.zu_lesen = True
            self.buf, self.laenge = [], 0
            self.cond.notify_all()

    def _warte(self, check):
        while not self.buf and not self.zu_schreiben:
            if check:
                check()
            self.cond.wait(0.2)

    def lesen_teil(self, check=None):
        with self.cond:
            self._warte(check)
            if not self.buf:
                return ""
            t = "".join(self.buf)
            self.buf, self.laenge = [], 0
            self.cond.notify_all()
            return t


class PipeWriter(Writer):
    def __init__(self, pipe, check=None):
        self.pipe, self.check = pipe, check

    def write(self, text):
        self.pipe.write(text, self.check)

    def close(self):
        self.pipe.schliessen_schreiben()


class Reader:
    """Basisklasse: liest Text. Unterklassen implementieren _teil() -> str ('' = EOF)."""
    isatty = False

    def __init__(self):
        self.rest = ""
        self.eof = False

    def _teil(self):
        return ""

    def _fuellen(self):
        if self.eof:
            return False
        t = self._teil()
        if t == "":
            self.eof = True
            return False
        self.rest += t
        return True

    def read(self):
        while self._fuellen():
            pass
        t, self.rest = self.rest, ""
        return t

    def readline(self):
        while "\n" not in self.rest and self._fuellen():
            pass
        i = self.rest.find("\n")
        if i < 0:
            t, self.rest = self.rest, ""
            return t
        t, self.rest = self.rest[:i + 1], self.rest[i + 1:]
        return t

    def lines(self):
        while True:
            z = self.readline()
            if z == "":
                return
            yield z

    def close(self):
        pass


class StringReader(Reader):
    def __init__(self, text):
        super().__init__()
        self.rest = text or ""
        self.eof = True


class NullReader(StringReader):
    def __init__(self):
        super().__init__("")


class PipeReader(Reader):
    def __init__(self, pipe, check=None):
        super().__init__()
        self.pipe, self.check = pipe, check

    def _teil(self):
        return self.pipe.lesen_teil(self.check)

    def close(self):
        self.pipe.schliessen_lesen()


class TtyReader(Reader):
    isatty = True

    def __init__(self, tty, pruef=True):
        super().__init__()
        self.tty, self.pruef = tty, pruef

    def _teil(self):
        if not self.pruef:   # Hintergrundjob: liest nicht vom Terminal
            return ""
        return self.tty.read_line()

    def readline(self):
        if self.rest:
            return super().readline()
        t = self._teil()
        if t == "":
            self.eof = True
        return t

    def read(self):
        teile = [self.rest]
        self.rest = ""
        while True:
            t = self._teil()
            if t == "":
                break
            teile.append(t)
        return "".join(teile)


__all__ = ["Interrupted", "BrokenPipe", "Writer", "NullWriter", "StringWriter", "TtyWriter", "FileWriter", "Pipe",
           "PipeWriter", "Reader", "StringReader", "NullReader", "PipeReader", "TtyReader"]
