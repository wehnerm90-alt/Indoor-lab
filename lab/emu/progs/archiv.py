"""tar / gzip – Archive im virtuellen Dateisystem."""
import json
import posixpath
import re
import time

from .. import vfs
from ..vfs import FsError
from .base import Usage, prog, quote_name
from .coreutils import hat, mode_str, standard, wert

GZ = "\x1f\x8b\x08\x00LABGZ\n"
TAR = "LABTAR\n"


def gz_packen(text):
    return GZ + text


def gz_entpacken(text):
    if text.startswith(GZ):
        return text[len(GZ):]
    return None


def tar_packen(eintraege):
    return TAR + json.dumps(eintraege, ensure_ascii=False)


def tar_lesen(text):
    if text.startswith(GZ):
        text = text[len(GZ):]
    if not text.startswith(TAR):
        return None
    try:
        return json.loads(text[len(TAR):])
    except ValueError:
        return None


def eintrag(pfad, node):
    e = {"p": pfad, "t": node.typ, "m": node.mode & 0o7777, "mt": node.mtime}
    if node.typ == "-":
        e["d"] = node.inhalt()
        if node.groesse is not None:
            e["g"] = node.groesse
    elif node.typ == "l":
        e["z"] = node.ziel
    return e


def _groesse(e):
    if e["t"] == "-":
        return e.get("g", len(e.get("d", "").encode("utf-8", "surrogateescape")))
    return 0


@prog("tar")
def tar(ctx):
    roh = ctx.argv[1:]
    # alte Syntax: tar xzvf datei
    if roh and not roh[0].startswith("-") and re.fullmatch(r"[ctxrAuzjJvfpCOkwmPS]+", roh[0]):
        roh = ["-" + roh[0]] + roh[1:]
    try:
        opts, args = ctx.getopt("cxtrAuzjJvf:C:pOkwmPSaI:T:X:", {
            "create": 0, "extract": 0, "get": 0, "list": 0, "append": 0, "update": 0, "gzip": 0, "gunzip": 0,
            "bzip2": 0, "xz": 0, "verbose": 0, "file": 1, "directory": 1, "preserve-permissions": 0,
            "to-stdout": 0, "keep-old-files": 0, "strip-components": 1, "exclude": 1, "no-same-owner": 0,
            "overwrite": 0, "absolute-names": 0, "help": 0, "version": 0, "usage": 0, "auto-compress": 0,
            "wildcards": 0, "totals": 0, "numeric-owner": 0, "same-owner": 0, "touch": 0, "transform": 1,
            "files-from": 1, "exclude-from": 1, "one-file-system": 0}, argv=roh)
    except Usage as u:
        ctx.err(f"tar: {u.msg}\nTry 'tar --help' or 'tar --usage' for more information.\n")
        return 64 if "unrecognized" in u.msg else 2
    if hat(opts, "--help"):
        return ctx.hilfe()
    if hat(opts, "--version"):
        return ctx.version()
    modi = [o for o, _ in opts if o in ("-c", "--create", "-x", "--extract", "--get", "-t", "--list", "-r",
                                        "--append", "-u", "--update", "-A")]
    if not modi:
        ctx.err("tar: You must specify one of the '-Acdtrux', '--delete' or '--test-label' options\n"
                "Try 'tar --help' or 'tar --usage' for more information.\n")
        return 2
    if len(set(m.lstrip("-")[0] for m in modi)) > 1:
        ctx.err("tar: You may not specify more than one '-Acdtrux', '--delete' or  '--test-label' option\n"
                "Try 'tar --help' or 'tar --usage' for more information.\n")
        return 2
    modus = modi[0].lstrip("-")[0]
    if modus == "g":
        modus = "x"
    datei = wert(opts, "-f", "--file")
    verbose = sum(1 for o, _ in opts if o in ("-v", "--verbose"))
    zielverz = wert(opts, "-C", "--directory")
    gz = hat(opts, "-z", "--gzip", "--gunzip", "-a", "--auto-compress")
    strip = int(wert(opts, "--strip-components", std="0") or 0)
    stdout = hat(opts, "-O", "--to-stdout")
    excl = [v for o, v in opts if o == "--exclude"]
    basis = ctx.cwd
    if zielverz:
        try:
            n = ctx.node(zielverz)
            if n.typ != "d":
                raise FsError("ENOTDIR")
            basis = ctx.fs.abs(zielverz, ctx.cwd)
        except FsError as e:
            ctx.err(f"tar: {zielverz}: Cannot open: {e.msg}\ntar: Error is not recoverable: exiting now\n")
            return 2
    if modus == "c":
        if not args:
            ctx.err("tar: Cowardly refusing to create an empty archive\nTry 'tar --help' or 'tar --usage' for more "
                    "information.\n")
            return 2
        if datei is None and ctx.isatty(1):
            ctx.err("tar: Refusing to write archive contents to terminal (missing -f option?)\n"
                    "tar: Error is not recoverable: exiting now\n")
            return 2
        eintraege = []
        rc = 0
        fuehrend = False
        for a in args:
            try:
                n = ctx.fs.aufloesen(a, basis, folgen=False)[0]
            except FsError as e:
                ctx.err(f"tar: {a}: Cannot stat: {e.msg}\n")
                rc = 2
                continue
            name = a
            if name.startswith("/"):
                fuehrend = True
                name = name.lstrip("/")

            def sammle(pfad, node, anzeige):
                if any(re.fullmatch(x.replace("*", ".*"), posixpath.basename(pfad)) for x in excl):
                    return
                ctx.check()
                eintraege.append(eintrag(anzeige + ("/" if node.typ == "d" and not anzeige.endswith("/") else ""), node))
                if verbose:
                    ctx.out(_v_zeile(eintraege[-1]) if verbose > 1 else eintraege[-1]["p"] + "\n") if datei and datei != "-" else ctx.err(eintraege[-1]["p"] + "\n")
                if node.typ == "d":
                    for k, kn in node.eintraege().items():
                        sammle(pfad.rstrip("/") + "/" + k, kn, anzeige.rstrip("/") + "/" + k)
            if fuehrend and a == args[0] or fuehrend:
                pass
            sammle(a, n, name)
        if fuehrend:
            ctx.err("tar: Removing leading `/' from member names\n")
        text = tar_packen(eintraege)
        if gz or (datei and re.search(r"\.(tgz|tar\.gz)$", datei)) and hat(opts, "-a", "--auto-compress", "-z"):
            text = gz_packen(text)
        if datei in (None, "-"):
            ctx.out(text)
        else:
            try:
                ctx.fs.schreiben(datei, text, ctx.cwd)
            except FsError as e:
                ctx.err(f"tar: {datei}: Cannot open: {e.msg}\ntar: Error is not recoverable: exiting now\n")
                return 2
        if rc:
            ctx.err("tar: Exiting with failure status due to previous errors\n")
        return rc
    # lesen (x / t)
    if datei is None or datei == "-":
        if ctx.stdin.isatty:
            ctx.err("tar: Refusing to read archive contents from terminal (missing -f option?)\n"
                    "tar: Error is not recoverable: exiting now\n")
            return 2
        inhalt = ctx.stdin.read()
    else:
        try:
            n = ctx.node(datei)
            if n.typ == "d":
                ctx.err(f"tar: {datei}: Cannot read: Is a directory\ntar: At beginning of tape, quitting now\n"
                        "tar: Error is not recoverable: exiting now\n")
                return 2
            inhalt = n.inhalt()
        except FsError as e:
            ctx.err(f"tar: {datei}: Cannot open: {e.msg}\ntar: Error is not recoverable: exiting now\n")
            return 2
    eintraege = tar_lesen(inhalt)
    if eintraege is None:
        hook = ctx.system.hooks.get("tar_quelle")
        if hook:
            eintraege = hook(inhalt)
    if eintraege is None:
        if gz and not inhalt.startswith(GZ):
            ctx.err("\ngzip: stdin: not in gzip format\ntar: Child returned status 1\ntar: Error is not recoverable: "
                    "exiting now\n")
            return 2
        ctx.err("tar: This does not look like a tar archive\ntar: Skipping to next header\n"
                "tar: Exiting with failure status due to previous errors\n")
        return 2
    gesamt = sum(_groesse(e) for e in eintraege)
    if modus == "t":
        for e in eintraege:
            if args and not any(e["p"].rstrip("/") == a.rstrip("/") or e["p"].startswith(a.rstrip("/") + "/") for a in args):
                continue
            ctx.out(_v_zeile(e) if verbose else e["p"] + "\n")
        return 0
    # x
    rc = 0
    dauer = min(12.0, gesamt / 150e6) if not ctx.system.hooks.get("schnell") else 0
    gefunden = set()
    for i, e in enumerate(eintraege):
        ctx.check()
        p = e["p"]
        if args and not any(p.rstrip("/") == a.rstrip("/") or p.startswith(a.rstrip("/") + "/") for a in args):
            continue
        for a in args:
            if p.rstrip("/") == a.rstrip("/") or p.startswith(a.rstrip("/") + "/"):
                gefunden.add(a)
        teile = [t for t in p.split("/") if t]
        if strip:
            teile = teile[strip:]
            if not teile:
                continue
        ziel = posixpath.join(basis, "/".join(teile))
        if verbose:
            ctx.out(_v_zeile(e) if verbose > 1 else p + "\n")
        if stdout:
            if e["t"] == "-":
                ctx.out(e.get("d", ""))
            continue
        try:
            if e["t"] == "d":
                vorhanden = ctx.fs.finde(ziel)
                if vorhanden is None:
                    _mkdirs(ctx, ziel, e["m"], e.get("mt"))
                continue
            _mkdirs(ctx, posixpath.dirname(ziel), 0o755, None)
            alt = ctx.fs.finde(ziel, folgen=False)
            if alt is not None:
                if alt.typ == "d":
                    ctx.err(f"tar: {p}: Cannot open: Is a directory\n")
                    rc = 2
                    continue
                ctx.fs.entfernen(ziel)
            if e["t"] == "l":
                node = vfs.link(e["z"])
            else:
                node = vfs.datei(e.get("d", ""), e["m"])
                if "g" in e:
                    node.groesse = e["g"]
            node.mtime = node.atime = e.get("mt") or time.time()
            node.ctime = time.time()
            ctx.fs.anlegen(ziel, node)
        except FsError as ex:
            ctx.err(f"tar: {p}: Cannot open: {ex.msg}\n")
            rc = 2
        if dauer and len(eintraege):
            ctx.sleep(dauer / len(eintraege))
    if not stdout:
        for e in eintraege:
            if e["t"] == "d" and e.get("mt"):
                teile = [t for t in e["p"].split("/") if t][strip:]
                if teile:
                    n = ctx.fs.finde(posixpath.join(basis, "/".join(teile)))
                    if n is not None and n.typ == "d":
                        n.mtime = e["mt"]
    for a in args:
        if a not in gefunden:
            ctx.err(f"tar: {a}: Not found in archive\n")
            rc = 2
    if rc:
        ctx.err("tar: Exiting with failure status due to previous errors\n")
    hook = ctx.system.hooks.get("nach_entpacken")
    if hook:
        hook(ctx, basis, eintraege)
    return rc


def _mkdirs(ctx, pfad, mode, mt):
    teile = [t for t in pfad.split("/") if t]
    akt = ""
    for t in teile:
        akt += "/" + t
        n = ctx.fs.finde(akt)
        if n is None:
            d = vfs.ordner(mode)
            if mt:
                d.mtime = mt
            ctx.fs.anlegen(akt, d)
        elif n.typ != "d":
            raise FsError("ENOTDIR")


def _v_zeile(e):
    n = vfs.Node(e["t"], e["m"])
    g = _groesse(e)
    t = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e.get("mt") or time.time()))
    name = e["p"] + (f" -> {e['z']}" if e["t"] == "l" else "")
    return f"{mode_str(n)} root/root {g:>9} {t} {name}\n"


@prog("gzip", "gunzip", "zcat")
def gzip(ctx):
    r = standard(ctx, "cdfhklLnNqrStvV123456789", {"stdout": 0, "to-stdout": 0, "decompress": 0, "uncompress": 0,
                                                    "force": 0, "keep": 0, "list": 0, "license": 0, "no-name": 0,
                                                    "name": 0, "quiet": 0, "recursive": 0, "suffix": 1, "test": 0,
                                                    "verbose": 0, "fast": 0, "best": 0, "rsyncable": 0})
    if isinstance(r, int):
        return r
    opts, args = r
    entpacken = ctx.prog in ("gunzip", "zcat") or hat(opts, "-d", "--decompress", "--uncompress")
    stdout = ctx.prog == "zcat" or hat(opts, "-c", "--stdout", "--to-stdout")
    behalten = hat(opts, "-k", "--keep")
    if not args:
        if ctx.stdin.isatty and not entpacken:
            ctx.err("gzip: compressed data not written to a terminal. Use -f to force compression.\n"
                    "For help, type: gzip -h\n")
            return 1
        t = ctx.stdin.read()
        if entpacken:
            d = gz_entpacken(t)
            if d is None:
                ctx.err("gzip: stdin: not in gzip format\n")
                return 1
            ctx.out(d)
        else:
            ctx.out(gz_packen(t))
        return 0
    rc = 0
    for a in args:
        try:
            n = ctx.node(a)
        except FsError as e:
            ctx.err(f"gzip: {a}: {e.msg}\n")
            rc = 1
            continue
        if n.typ == "d":
            ctx.err(f"gzip: {a} is a directory -- ignored\n")
            rc = 2
            continue
        t = n.inhalt()
        if entpacken:
            if not re.search(r"\.(gz|tgz|z)$", a) and not stdout:
                ctx.err(f"gzip: {a}: unknown suffix -- ignored\n")
                rc = 2
                continue
            d = gz_entpacken(t)
            if d is None:
                ctx.err(f"gzip: {a}: not in gzip format\n")
                rc = 1
                continue
            if stdout:
                ctx.out(d)
                continue
            ziel = a[:-3] if a.endswith(".gz") else (a[:-4] + ".tar" if a.endswith(".tgz") else a[:-2])
        else:
            if a.endswith(".gz"):
                ctx.err(f"gzip: {a} already has .gz suffix -- unchanged\n")
                rc = 2
                continue
            d = gz_packen(t)
            if stdout:
                ctx.out(d)
                continue
            ziel = a + ".gz"
        if ctx.fs.finde(ziel, ctx.cwd) is not None and not hat(opts, "-f", "--force"):
            ctx.err(f"gzip: {ziel} already exists; do you wish to overwrite (y or n)? ")
            antw = ctx.stdin.readline()
            if not antw.lower().startswith("y"):
                ctx.err("\tnot overwritten\n")
                continue
        try:
            neu = ctx.fs.schreiben(ziel, d, ctx.cwd, mode=n.mode)
            neu.mode = n.mode
            neu.mtime = n.mtime
            if not behalten:
                ctx.fs.entfernen(a, ctx.cwd)
        except FsError as e:
            ctx.err(f"gzip: {ziel}: {e.msg}\n")
            rc = 1
    return rc
