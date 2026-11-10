"""Emuliertes Terminal (tty) mit Line-Discipline: Echo, Zeilenpuffer, Ctrl+C/Ctrl+D, Roh-Modus.

Ein- und Ausgabe sind reine Zeichenketten, die ueber HTTP vom/zum Browser-Terminal gehen.
"""
import collections
import threading
import time

AUSGABE_MAX = 1_500_000


class Interrupted(Exception):
    """SIGINT (Ctrl+C) im Vordergrundprozess."""


class EOFInput(Exception):
    pass


class Tty:
    def __init__(self, cols=100, rows=36):
        self.cols, self.rows = cols, rows
        self.cond = threading.Condition()
        self.out = []
        self.out_len = 0         # Gesamtlaenge seit Beginn (Positionen fuer den Client)
        self.out_text = ""       # die letzten AUSGABE_MAX Zeichen
        self.basis = 0           # Position von out_text[0]
        self.canon = True
        self.echo = True
        self.isig = True
        self.linebuf = ""
        self.zeilen = collections.deque()  # fertige Zeilen (canon); None = EOF
        self.roh = collections.deque()     # Zeichen im Roh-Modus
        self.sigint = threading.Event()
        self.lebt = True
        self.letzte_eingabe = time.time()
        self.app_cursor = False

    # ---------------- Ausgabe ----------------
    def write(self, text):
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        with self.cond:
            self.out_text += text
            self.out_len += len(text)
            if len(self.out_text) > AUSGABE_MAX:
                weg = len(self.out_text) - AUSGABE_MAX
                self.out_text = self.out_text[weg:]
                self.basis += weg
            self.cond.notify_all()

    def write_raw(self, text):
        """Ohne \\n -> \\r\\n (fuer Vollbildprogramme, die selbst steuern)."""
        with self.cond:
            self.out_text += text
            self.out_len += len(text)
            self.cond.notify_all()

    def lesen_ab(self, pos, warten=5.0):
        ende = time.time() + warten
        with self.cond:
            while self.out_len <= pos and self.lebt and time.time() < ende:
                self.cond.wait(max(0.0, ende - time.time()))
            if pos < self.basis:
                pos = self.basis
            return self.out_text[pos - self.basis:], self.out_len

    # ---------------- Modus ----------------
    def modus(self, canon=None, echo=None, isig=None):
        with self.cond:
            if canon is not None:
                if not canon and self.canon and self.linebuf:
                    self.roh.extend(self.linebuf)
                    self.linebuf = ""
                if canon and not self.canon and self.roh:
                    # Tippvorsprung aus dem Roh-Modus in die Zeilenbearbeitung uebernehmen
                    rest = "".join(self.roh)
                    self.roh.clear()
                    self.cond.release()
                    try:
                        self.canon = True
                        self._feed(rest, echo=False)
                    finally:
                        self.cond.acquire()
                self.canon = canon
            if echo is not None:
                self.echo = echo
            if isig is not None:
                self.isig = isig

    # ---------------- Eingabe vom Browser ----------------
    def feed(self, data):
        self.letzte_eingabe = time.time()
        self._feed(data, echo=True)

    def _feed(self, data, echo=True):
        with self.cond:
            i = 0
            while i < len(data):
                c = data[i]
                # Escape-Sequenzen am Stueck behandeln
                seq = c
                if c == "\x1b" and i + 1 < len(data) and data[i + 1] in "[O":
                    j = i + 2
                    while j < len(data) and not (data[j].isalpha() or data[j] == "~"):
                        j += 1
                    seq = data[i:j + 1]
                i += len(seq)
                if self.isig and seq == "\x03":
                    self.sigint.set()
                    if self.echo and echo:
                        self._echo("^C")
                    if self.canon:
                        self.linebuf = ""
                    self.roh.clear() if not self.canon else None
                    self.cond.notify_all()
                    continue
                if self.isig and seq == "\x1c":  # Ctrl+\ -> SIGQUIT, wie SIGINT behandeln
                    self.sigint.set()
                    if self.echo and echo:
                        self._echo("^\\")
                    self.cond.notify_all()
                    continue
                if not self.canon:
                    self.roh.append(seq)
                    self.cond.notify_all()
                    continue
                e = self.echo and echo
                if seq in ("\x7f", "\x08"):
                    if self.linebuf:
                        self.linebuf = self.linebuf[:-1]
                        if e:
                            self._echo("\b \b")
                elif seq == "\x15":
                    if e:
                        self._echo("\b \b" * len(self.linebuf))
                    self.linebuf = ""
                elif seq == "\x17":
                    neu = self.linebuf.rstrip(" ")
                    neu = neu[:len(neu) - len(neu.split(" ")[-1])] if neu else ""
                    if e:
                        self._echo("\b \b" * (len(self.linebuf) - len(neu)))
                    self.linebuf = neu
                elif seq == "\x04":
                    if self.linebuf:
                        self.zeilen.append(self.linebuf)
                    else:
                        self.zeilen.append(None)
                    self.linebuf = ""
                elif seq in ("\r", "\n"):
                    self.zeilen.append(self.linebuf + "\n")
                    self.linebuf = ""
                    if e:
                        self._echo("\r\n")
                elif seq == "\t" or len(seq) == 1 and ord(seq) >= 32:
                    self.linebuf += seq
                    if e:
                        self._echo(seq)
                elif len(seq) == 1:  # andere Steuerzeichen werden als ^X angezeigt
                    self.linebuf += seq
                    if e:
                        self._echo("^" + chr(ord(seq) + 64))
                else:
                    self.linebuf += seq
                    if e:
                        self._echo("^[" + seq[1:])
                self.cond.notify_all()

    def _echo(self, t):
        self.out_text += t
        self.out_len += len(t)

    # ---------------- Lesen fuer Programme ----------------
    def read_line(self, pruef_sigint=True):
        """Kanonisch eine Zeile lesen. '' = EOF. Wirft Interrupted bei Ctrl+C."""
        with self.cond:
            while True:
                if pruef_sigint and self.sigint.is_set():
                    raise Interrupted()
                if not self.lebt:
                    return ""
                if self.zeilen:
                    z = self.zeilen.popleft()
                    return "" if z is None else z
                self.cond.wait(0.5)

    def read_key(self, timeout=None, pruef_sigint=False):
        ende = None if timeout is None else time.time() + timeout
        with self.cond:
            while True:
                if pruef_sigint and self.sigint.is_set():
                    raise Interrupted()
                if not self.lebt:
                    raise EOFInput()
                if self.roh:
                    return self.roh.popleft()
                if ende is not None:
                    rest = ende - time.time()
                    if rest <= 0:
                        return None
                    self.cond.wait(min(rest, 0.5))
                else:
                    self.cond.wait(0.5)

    def hat_eingabe(self):
        with self.cond:
            return bool(self.roh) or bool(self.zeilen)

    def beenden(self):
        with self.cond:
            self.lebt = False
            self.cond.notify_all()
