"""Ein emulierter Linux-Rechner: Dateisystem, Prozesse, Benutzer, Programmstart."""
import itertools
import posixpath
import re
import threading
import time

from . import vfs
from .progs.base import PFADE, PROGS, Ctx, Usage
from .streams import Interrupted

MARKER = "#LABPROG:"


def binaer(name, groesse=None):
    """Inhalt einer emulierten ELF-Datei (enthaelt den Programmnamen als Marker)."""
    return "\x7fELF\x02\x01\x01\x00" + "\x00" * 8 + f"{MARKER}{name}\n"


class System:
    def __init__(self, hostname="localhost", kernel="6.8.0-45-generic",
                 kernel_version="#45-Ubuntu SMP PREEMPT_DYNAMIC Fri Aug 30 12:02:04 UTC 2024", cpus=8, ip="10.0.0.2"):
        self.fs = vfs.FS()
        self.hostname = hostname
        self.kernel = kernel
        self.kernel_version = kernel_version
        self.cpus = cpus
        self.ip = ip
        self.boot = time.time()
        self.zeit_offset = 0
        self._pid = itertools.count(4100)
        self._prozesse = {}      # pid -> dict
        self.cnf_ausgabe = None
        self.lab_name = None
        self.os = "prod"
        self.lock = threading.Lock()
        self.shell_pid = 0
        self.users = {"root": (0, 0, "/root", "/bin/bash")}
        self.gruppen = {"root": 0}
        self.hooks = {}          # name -> callable (Lab-Erweiterungen)
        self.cnf = {}            # command-not-found: name -> paket
        self.kill_hook = None
        self.job_events = {}
        self.psub = {}           # Prozess-Substitution: Nummer -> Inhalt
        self.basis_prozesse()

    # ---------------- Benutzer (aus /etc/passwd und /etc/group) ----------------
    def _passwd(self):
        n = self.fs.finde("/etc/passwd")
        erg = dict(self.users)
        if n is not None:
            for z in n.inhalt().splitlines():
                t = z.split(":")
                if len(t) >= 7 and t[2].isdigit():
                    erg[t[0]] = (int(t[2]), int(t[3]) if t[3].isdigit() else 0, t[5], t[6])
        return erg

    def _group(self):
        n = self.fs.finde("/etc/group")
        erg = dict(self.gruppen)
        if n is not None:
            for z in n.inhalt().splitlines():
                t = z.split(":")
                if len(t) >= 3 and t[2].isdigit():
                    erg[t[0]] = int(t[2])
        return erg

    def user_name(self, uid):
        for n, (u, *_rest) in self._passwd().items():
            if u == uid:
                return n
        return str(uid)

    def group_name(self, gid):
        for n, g in self._group().items():
            if g == gid:
                return n
        return str(gid)

    def uid_von(self, name):
        if name.isdigit():
            return int(name)
        p = self._passwd()
        return p[name][0] if name in p else None

    def gid_von(self, name):
        if name.isdigit():
            return int(name)
        return self._group().get(name)

    def home_von(self, name):
        p = self._passwd()
        return p[name][2] if name in p else None

    # ---------------- Prozesse ----------------
    def neue_pid(self):
        return next(self._pid)

    @property
    def prozesse(self):
        sync = self.hooks.get("procs_sync")
        if sync:
            try:
                sync()
            except Exception:  # noqa: BLE001
                pass
        return self._prozesse

    def basis_prozesse(self):
        t = self.boot
        basis = [
            (1, "root", "/sbin/init", "systemd"), (2, "root", "[kthreadd]", "kthreadd"),
            (3, "root", "[pool_workqueue_release]", "pool_workqueue_release"),
            (4, "root", "[kworker/R-rcu_g]", "kworker/R-rcu_g"), (15, "root", "[ksoftirqd/0]", "ksoftirqd/0"),
            (16, "root", "[rcu_preempt]", "rcu_preempt"), (17, "root", "[migration/0]", "migration/0"),
            (412, "root", "/usr/lib/systemd/systemd-journald", "systemd-journal"),
            (470, "root", "/usr/lib/systemd/systemd-udevd", "systemd-udevd"),
            (812, "systemd-network", "/usr/lib/systemd/systemd-networkd", "systemd-network"),
            (845, "systemd-resolve", "/usr/lib/systemd/systemd-resolved", "systemd-resolve"),
            (1020, "root", "/usr/sbin/cron -f -P", "cron"),
            (1031, "message+", "@dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation --syslog-only", "dbus-daemon"),
            (1045, "root", "/usr/lib/systemd/systemd-logind", "systemd-logind"),
            (1102, "syslog", "/usr/sbin/rsyslogd -n -iNONE", "rsyslogd"),
            (1188, "root", "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", "sshd"),
            (1203, "root", "/usr/sbin/irqbalance --foreground", "irqbalance"),
            (1301, "root", "/bin/login -p --", "login"),
        ]
        for pid, user, cmd, comm in basis:
            self._prozesse[pid] = {"pid": pid, "ppid": 0 if pid in (1, 2) else (2 if cmd.startswith("[") else 1),
                                  "user": user, "cmd": cmd, "comm": comm, "start": t + pid * 0.001, "tty": "?",
                                  "stat": "Ss" if not cmd.startswith("[") else "S", "rss": 0 if cmd.startswith("[") else 9000 + pid % 7000,
                                  "vsz": 0 if cmd.startswith("[") else 20000 + pid * 13 % 200000}
        self._prozesse[1301]["tty"] = "tty1"

    def proc_start(self, pid, comm, cmd, tty="tty1", user="root", ppid=None):
        with self.lock:
            self._prozesse[pid] = {"pid": pid, "ppid": ppid or self.shell_pid or 1301, "user": user, "cmd": cmd,
                                  "comm": comm[:15], "start": time.time(), "tty": tty, "stat": "S+", "rss": 4096,
                                  "vsz": 8000}

    def proc_ende(self, pid):
        with self.lock:
            self._prozesse.pop(pid, None)

    def signal(self, pid, num):
        p = self.prozesse.get(pid)
        if p is None:
            return False
        if self.kill_hook and self.kill_hook(pid, num, p):
            return True
        if pid in self.job_events:
            self.job_events[pid].set()
            return True
        if num == 0:
            return True
        if pid == 1:
            return True
        if p["cmd"].startswith("["):
            return True
        if pid == self.shell_pid and num in (9, 1):
            self.hooks.get("shell_kill", lambda: None)()
            return True
        if num in (15, 9, 2, 1) and pid not in (1301,) and pid != self.shell_pid:
            self.proc_ende(pid)
        return True

    # ---------------- Dateisysteme ----------------
    def dateisysteme(self):
        return self.hooks.get("df", lambda: [])()

    # ---------------- Programmstart ----------------
    def installieren(self, name, verz="/usr/bin", groesse=None):
        node = vfs.datei(binaer(name), 0o755)
        node.groesse = groesse or (35000 + (sum(map(ord, name)) * 977) % 200000)
        node.mtime = node.atime = node.ctime = 1712000000 + (sum(map(ord, name)) * 7919) % 9000000
        par = self.fs.finde(verz)
        if par is None:
            return
        node.parent = par
        par.kinder[name] = node

    def alle_installieren(self):
        for name, (verz, groesse) in PFADE.items():
            if name in self.hooks.get("ohne", set()):
                continue
            self.installieren(name, verz, groesse)

    def nicht_gefunden(self, sh, name, fds):
        w = fds.get(2)
        if w is None:
            return 127
        if not sh.interaktiv:
            w.write(f"{sh.praefix()}{name}: command not found\n")
            return 127
        if name in self.cnf:
            paket = self.cnf[name]
            w.write(f"Command '{name}' not found, but can be installed with:\n\napt install {paket}\n\n")
            return 127
        # aehnliche Befehle (wie command-not-found)
        w.write(f"{name}: command not found\n")
        return 127

    def starte(self, sh, pfad, node, argv, fds, env):
        inhalt = node.inhalt()
        m = re.search(re.escape(MARKER) + r"([\w.+\-\[\]]+)", inhalt[:400])
        name = m.group(1) if m else None
        if inhalt.startswith("#!"):
            erste = inhalt.split("\n", 1)[0][2:].strip()
            interp = erste.split()[0] if erste else ""
            if name and name in PROGS:
                return self._prog(sh, name, argv, fds, env)
            if posixpath.basename(interp) in ("bash", "sh", "dash") or interp == "/usr/bin/env" and "bash" in erste:
                if self.fs.finde(interp) is None and interp != "/usr/bin/env":
                    sh.fehler(f"{argv[0]}: {interp}: bad interpreter: No such file or directory", fds)
                    return 126
                return self.skript(sh, inhalt, argv, fds, env)
            if self.fs.finde(interp) is None:
                sh.fehler(f"{argv[0]}: {interp}: bad interpreter: No such file or directory", fds)
                return 126
            ip = posixpath.basename(interp)
            if ip in PROGS:
                return self._prog(sh, ip, [ip] + argv, fds, env)
            sh.fehler(f"{argv[0]}: cannot execute: required file not found", fds)
            return 126
        if name and name in PROGS:
            return self._prog(sh, name, argv, fds, env)
        if inhalt.startswith("\x7fELF"):
            hook = self.hooks.get("elf")
            if hook:
                r = hook(sh, pfad, node, argv, fds, env)
                if r is not None:
                    return r
            sh.fehler(f"{argv[0]}: cannot execute binary file: Exec format error", fds)
            return 126
        # Textdatei ohne #! -> als Shell-Skript
        return self.skript(sh, inhalt, argv, fds, env)

    def skript(self, sh, inhalt, argv, fds, env):
        from .shell import Exit, Shell
        kind = Shell(self, sh.tty, interaktiv=False, login=False)
        kind.bg = sh.bg
        kind.vars.update(env)
        kind.export = set(env)
        kind.cwd = sh.cwd
        kind.script = argv[0]
        kind.args = argv[1:]
        kind.arg0 = argv[0]
        kind.fds = fds
        kind.check = sh.check
        pid = kind.pid
        self.proc_start(pid, posixpath.basename(argv[0]), "/bin/bash " + " ".join(argv))
        try:
            return kind.run(inhalt, fds)
        except Exit as e:
            return e.code
        finally:
            self.proc_ende(pid)

    def _prog(self, sh, name, argv, fds, env):
        ctx = Ctx(sh, argv, fds, env, self)
        pid = self.neue_pid()
        self.proc_start(pid, posixpath.basename(argv[0]), " ".join(argv))
        try:
            rc = PROGS[name](ctx)
            return 0 if rc is None else int(rc)
        except Usage as u:
            return ctx.usage_fehler(u.msg)
        finally:
            self.proc_ende(pid)
