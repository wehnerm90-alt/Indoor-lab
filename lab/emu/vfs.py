"""Virtuelles Linux-Dateisystem (nur im Speicher).

Inodes mit Typ, Rechten, Besitzer, Zeiten, Symlinks und Geraetedateien. Es gibt keinerlei Zugriff auf das echte
Dateisystem des Containers: alles, was die Konsole sieht, liegt hier im Speicher.
"""
import itertools
import posixpath
import time

_INO = itertools.count(100000)

ERRNO = {
    "ENOENT": "No such file or directory", "ENOTDIR": "Not a directory", "EISDIR": "Is a directory",
    "EEXIST": "File exists", "ENOTEMPTY": "Directory not empty", "EACCES": "Permission denied",
    "EINVAL": "Invalid argument", "ELOOP": "Too many levels of symbolic links", "EPERM": "Operation not permitted",
    "EBUSY": "Device or resource busy", "EROFS": "Read-only file system", "ENOSPC": "No space left on device",
    "ENAMETOOLONG": "File name too long", "EXDEV": "Invalid cross-device link", "ENXIO": "No such device or address",
}


class FsError(Exception):
    def __init__(self, code):
        super().__init__(ERRNO[code])
        self.code = code
        self.msg = ERRNO[code]


class Node:
    __slots__ = ("typ", "mode", "uid", "gid", "mtime", "atime", "ctime", "data", "kinder", "ziel", "rdev", "ino",
                 "gen", "groesse", "nur_lesen", "schreib_fn", "parent")

    def __init__(self, typ, mode, data=None, uid=0, gid=0, t=None):
        t = t if t is not None else time.time()
        self.typ = typ          # '-', 'd', 'l', 'c', 'b', 'p', 's'
        self.mode = mode        # Rechtebits inkl. suid/sgid/sticky
        self.uid, self.gid = uid, gid
        self.mtime = self.atime = self.ctime = t
        self.data = data        # str oder callable -> str
        self.kinder = {} if typ == "d" else None
        self.ziel = None        # Symlink-Ziel
        self.rdev = None        # (major, minor)
        self.ino = next(_INO)
        self.gen = None         # callable -> {name: Node} fuer dynamische Verzeichnisse (/proc, /dev)
        self.groesse = None     # feste Groesse (z. B. Binaerdateien)
        self.nur_lesen = False
        self.schreib_fn = None  # z. B. /proc/sys/...

    # ---- Inhalt ----
    def inhalt(self):
        if self.typ == "c":
            return ""
        d = self.data
        if callable(d):
            try:
                return d() or ""
            except Exception:  # noqa: BLE001
                return ""
        return d or ""

    def size(self):
        if self.typ == "d":
            return 4096
        if self.typ == "l":
            return len(self.ziel.encode())
        if self.typ in "cbps":
            return 0
        if self.groesse is not None:
            return self.groesse
        if callable(self.data):
            return 0 if self.nur_lesen else len(self.inhalt().encode("utf-8", "replace"))
        return len((self.data or "").encode("utf-8", "replace"))

    def eintraege(self):
        if self.typ != "d":
            return {}
        if self.gen:
            try:
                dyn = self.gen()
            except Exception:  # noqa: BLE001
                dyn = {}
            for n in dyn.values():
                n.parent = self
            return {**dyn, **self.kinder}
        return self.kinder

    def nlink(self):
        if self.typ == "d":
            return 2 + sum(1 for n in self.eintraege().values() if n.typ == "d")
        return 1


def datei(data="", mode=0o644, **kw):
    return Node("-", mode, data, **kw)


def ordner(mode=0o755, **kw):
    return Node("d", mode, **kw)


def link(ziel, **kw):
    n = Node("l", 0o777, **kw)
    n.ziel = ziel
    return n


def geraet(major, minor, mode=0o666, typ="c", gid=0):
    n = Node(typ, mode, gid=gid)
    n.rdev = (major, minor)
    return n


class FS:
    def __init__(self):
        self.root = ordner()
        self.root.parent = self.root

    # ---------- Pfadauflösung ----------
    def _teile(self, pfad, cwd):
        if not pfad.startswith("/"):
            pfad = cwd.rstrip("/") + "/" + pfad
        return [t for t in pfad.split("/") if t]

    def aufloesen(self, pfad, cwd="/", folgen=True, _tiefe=0):
        """-> (node, absoluter_kanonischer_pfad). Fehler: FsError."""
        if pfad == "":
            raise FsError("ENOENT")
        if _tiefe > 40:
            raise FsError("ELOOP")
        teile = self._teile(pfad, cwd)
        node, weg = self.root, []
        for i, t in enumerate(teile):
            letzter = i == len(teile) - 1
            if node.typ != "d":
                raise FsError("ENOTDIR")
            if t == ".":
                continue
            if t == "..":
                if weg:
                    weg.pop()
                node = self._knoten(weg)
                continue
            kind = node.eintraege().get(t)
            if kind is None:
                raise FsError("ENOENT")
            if kind.typ == "l" and (folgen or not letzter):
                basis = "/" + "/".join(weg)
                ziel_node, ziel_pfad = self.aufloesen(kind.ziel, basis, True, _tiefe + 1)
                node, weg = ziel_node, [x for x in ziel_pfad.split("/") if x]
                continue
            node = kind
            weg.append(t)
        if pfad.endswith("/") and node.typ != "d":
            raise FsError("ENOTDIR")
        return node, "/" + "/".join(weg)

    def _knoten(self, weg):
        node = self.root
        for t in weg:
            node = node.eintraege()[t]
            while node.typ == "l":
                node, _ = self.aufloesen(node.ziel, "/")
        return node

    def finde(self, pfad, cwd="/", folgen=True):
        try:
            return self.aufloesen(pfad, cwd, folgen)[0]
        except FsError:
            return None

    def eltern(self, pfad, cwd="/"):
        """-> (eltern_node, name, eltern_pfad) fuer anzulegende/zu loeschende Eintraege."""
        if not pfad.startswith("/"):
            pfad = cwd.rstrip("/") + "/" + pfad
        p = pfad.rstrip("/") or "/"
        d, name = posixpath.split(p)
        if name in ("", ".", ".."):
            if p == "/":
                return None, "/", "/"
            node, ap = self.aufloesen(p, "/")
            return None, name, ap
        node, dp = self.aufloesen(d or "/", "/")
        if node.typ != "d":
            raise FsError("ENOTDIR")
        return node, name, dp

    def abs(self, pfad, cwd="/"):
        """Logisch normalisierter absoluter Pfad (ohne Symlinks aufzuloesen)."""
        if not pfad.startswith("/"):
            pfad = cwd.rstrip("/") + "/" + pfad
        p = posixpath.normpath(pfad)
        return "/" + p.lstrip("/") if p.startswith("//") else p

    # ---------- Aenderungen ----------
    def anlegen(self, pfad, node, cwd="/", ersetzen=False):
        par, name, _ = self.eltern(pfad, cwd)
        if par is None:
            raise FsError("EEXIST")
        if par.nur_lesen:
            raise FsError("EACCES")
        if name in par.eintraege() and not ersetzen:
            raise FsError("EEXIST")
        if len(name) > 255:
            raise FsError("ENAMETOOLONG")
        node.parent = par
        par.kinder[name] = node
        par.mtime = par.ctime = time.time()
        return node

    def schreiben(self, pfad, text, cwd="/", anhaengen=False, mode=0o644):
        """Datei schreiben (anlegen falls noetig). Gibt den Knoten zurueck."""
        node = self.finde(pfad, cwd)
        if node is None:
            try:
                ziel_node, _ = self.aufloesen(pfad, cwd, folgen=False)
            except FsError:
                ziel_node = None
            if ziel_node is not None and ziel_node.typ == "l":  # haengender Symlink -> Ziel anlegen
                par, name, dp = self.eltern(pfad, cwd)
                return self.schreiben(posixpath.join(dp, ziel_node.ziel), text, "/", anhaengen, mode)
            node = self.anlegen(pfad, datei("", mode), cwd)
        if node.typ == "d":
            raise FsError("EISDIR")
        if node.typ == "c":
            if node.schreib_fn:
                node.schreib_fn(text)
            return node
        if node.nur_lesen or callable(node.data) and not node.schreib_fn:
            if node.schreib_fn:
                node.schreib_fn(text)
                return node
            raise FsError("EACCES")
        node.data = (node.data or "") + text if anhaengen else text
        node.groesse = None
        node.mtime = node.ctime = time.time()
        return node

    def entfernen(self, pfad, cwd="/"):
        par, name, _ = self.eltern(pfad, cwd)
        if par is None:
            raise FsError("EBUSY")
        if name not in par.kinder:
            if name in par.eintraege():
                raise FsError("EPERM")
            raise FsError("ENOENT")
        if par.nur_lesen:
            raise FsError("EACCES")
        n = par.kinder.pop(name)
        par.mtime = par.ctime = time.time()
        return n

    def liste(self, node):
        return sorted(node.eintraege().items())
