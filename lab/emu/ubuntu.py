"""Grundgeruest eines Ubuntu-Systems im virtuellen Dateisystem."""
import json
import os
import time

from . import vfs
from .vfs import datei, geraet, link, ordner

DOT_BASHRC = open("/usr/share/base-files/dot.bashrc").read() if os.path.exists("/usr/share/base-files/dot.bashrc") else ""
DOT_PROFILE = """# ~/.profile: executed by Bourne-compatible login shells.

if [ "$BASH" ]; then
  if [ -f ~/.bashrc ]; then
    . ~/.bashrc
  fi
fi

mesg n 2> /dev/null || true
"""


def _laden(name):
    p = os.path.join(os.path.dirname(__file__), name)
    with open(p, encoding="utf-8") as f:
        return f.read()


PASSWD_BASIS = """root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
bin:x:2:2:bin:/bin:/usr/sbin/nologin
sys:x:3:3:sys:/dev:/usr/sbin/nologin
sync:x:4:65534:sync:/bin:/bin/sync
games:x:5:60:games:/usr/games:/usr/sbin/nologin
man:x:6:12:man:/var/cache/man:/usr/sbin/nologin
lp:x:7:7:lp:/var/spool/lpd:/usr/sbin/nologin
mail:x:8:8:mail:/var/mail:/usr/sbin/nologin
news:x:9:9:news:/var/spool/news:/usr/sbin/nologin
uucp:x:10:10:uucp:/var/spool/uucp:/usr/sbin/nologin
proxy:x:13:13:proxy:/bin:/usr/sbin/nologin
www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
backup:x:34:34:backup:/var/backups:/usr/sbin/nologin
list:x:38:38:Mailing List Manager:/var/list:/usr/sbin/nologin
irc:x:39:39:ircd:/run/ircd:/usr/sbin/nologin
_apt:x:42:65534::/nonexistent:/usr/sbin/nologin
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
systemd-network:x:998:998:systemd Network Management:/:/usr/sbin/nologin
systemd-timesync:x:996:996:systemd Time Synchronization:/:/usr/sbin/nologin
dhcpcd:x:100:65534:DHCP Client Daemon,,,:/usr/lib/dhcpcd:/bin/false
messagebus:x:101:102::/nonexistent:/usr/sbin/nologin
systemd-resolve:x:992:992:systemd Resolver:/:/usr/sbin/nologin
pollinate:x:102:1::/var/cache/pollinate:/bin/false
polkitd:x:991:991:User for polkitd:/:/usr/sbin/nologin
syslog:x:103:104::/nonexistent:/usr/sbin/nologin
uuidd:x:104:105::/run/uuidd:/usr/sbin/nologin
tcpdump:x:105:107::/nonexistent:/usr/sbin/nologin
tss:x:106:108:TPM software stack,,,:/var/lib/tpm:/bin/false
landscape:x:107:109::/var/lib/landscape:/usr/sbin/nologin
fwupd-refresh:x:989:989:Firmware update daemon:/var/lib/fwupd:/usr/sbin/nologin
usbmux:x:108:46:usbmux daemon,,,:/var/lib/usbmux:/usr/sbin/nologin
sshd:x:109:65534::/run/sshd:/usr/sbin/nologin
"""

GROUP_BASIS = """root:x:0:
daemon:x:1:
bin:x:2:
sys:x:3:
adm:x:4:syslog
tty:x:5:
disk:x:6:
lp:x:7:
mail:x:8:
news:x:9:
uucp:x:10:
man:x:12:
proxy:x:13:
kmem:x:15:
dialout:x:20:
cdrom:x:24:
sudo:x:27:
audio:x:29:
www-data:x:33:
backup:x:34:
operator:x:37:
plugdev:x:46:
staff:x:50:
users:x:100:
nogroup:x:65534:
utmp:x:43:
shadow:x:42:
systemd-journal:x:999:
systemd-network:x:998:
messagebus:x:102:
syslog:x:104:
lxd:x:101:
"""


def os_release(pe):
    if pe:
        return ('PRETTY_NAME="Ubuntu 22.04.3 LTS"\nNAME="Ubuntu"\nVERSION_ID="22.04"\n'
                'VERSION="22.04.3 LTS (Jammy Jellyfish)"\nVERSION_CODENAME=jammy\nID=ubuntu\nID_LIKE=debian\n'
                'HOME_URL="https://www.ubuntu.com/"\nSUPPORT_URL="https://help.ubuntu.com/"\n'
                'BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"\n'
                'PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"\n'
                'UBUNTU_CODENAME=jammy\n')
    return ('PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
            'VERSION="24.04.1 LTS (Noble Numbat)"\nVERSION_CODENAME=noble\nID=ubuntu\nID_LIKE=debian\n'
            'HOME_URL="https://www.ubuntu.com/"\nSUPPORT_URL="https://help.ubuntu.com/"\n'
            'BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"\n'
            'PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"\n'
            'UBUNTU_CODENAME=noble\nLOGO=ubuntu-logo\n')


def _setze(fs, pfad, node):
    """Datei/Ordner anlegen; fehlende Elternordner werden erstellt."""
    teile = [t for t in pfad.split("/") if t]
    akt = fs.root
    for t in teile[:-1]:
        k = akt.kinder.get(t)
        if k is None:
            k = ordner()
            k.parent = akt
            akt.kinder[t] = k
        akt = k
    node.parent = akt
    akt.kinder[teile[-1]] = node
    return node


def grundsystem(sysm, pe=False, hostname="localhost", bootzeit=None):
    fs = sysm.fs
    t_inst = 1725000000.0  # Installationszeitpunkt (Aug 2024)
    alt = lambda n: (setattr(n, "mtime", t_inst), setattr(n, "atime", t_inst), setattr(n, "ctime", t_inst), n)[3]  # noqa: E731
    for d in ["usr", "usr/bin", "usr/sbin", "usr/lib", "usr/lib64", "usr/lib/x86_64-linux-gnu", "usr/local",
              "usr/local/bin", "usr/local/sbin", "usr/local/lib", "usr/share", "usr/share/doc", "usr/share/man",
              "usr/share/man/man1", "usr/include", "usr/src", "usr/games", "boot", "boot/grub", "dev", "etc", "home",
              "media", "mnt", "opt", "proc", "root", "run", "srv", "sys", "tmp", "var", "var/log", "var/lib",
              "var/cache", "var/tmp", "var/spool", "var/mail", "var/backups", "var/opt", "var/local", "var/log/apt",
              "var/log/journal", "var/lib/apt", "var/lib/dpkg", "etc/default", "etc/ssh", "etc/netplan",
              "etc/systemd", "etc/systemd/system", "etc/apt", "etc/cron.d", "etc/cron.daily", "etc/modprobe.d",
              "etc/udev", "etc/update-motd.d", "etc/profile.d", "etc/skel", "lost+found", "snap", "cdrom"]:
        _setze(fs, "/" + d, alt(ordner()))
    fs.finde("/root").mode = 0o700
    fs.finde("/tmp").mode = 0o1777
    fs.finde("/var/tmp").mode = 0o1777
    fs.finde("/lost+found").mode = 0o700
    for name, ziel in (("bin", "usr/bin"), ("sbin", "usr/sbin"), ("lib", "usr/lib"), ("lib64", "usr/lib64")):
        _setze(fs, "/" + name, alt(link(ziel)))
    if not pe:
        for name in ("bin.usr-is-merged", "lib.usr-is-merged", "sbin.usr-is-merged"):
            _setze(fs, "/" + name, alt(ordner()))
    _setze(fs, "/usr/lib64/ld-linux-x86-64.so.2", alt(link("../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")))
    for n in ("ld-linux-x86-64.so.2", "libc.so.6", "libm.so.6", "libpthread.so.0", "libtinfo.so.6", "libselinux.so.1",
              "libpcre2-8.so.0", "libcap.so.2", "libsystemd.so.0", "libudev.so.1", "libkmod.so.2"):
        f = datei("\x7fELF\x02\x01\x01\x00shared library", 0o755)
        f.groesse = 200000 + sum(map(ord, n)) * 311 % 2000000
        _setze(fs, "/usr/lib/x86_64-linux-gnu/" + n, alt(f))
    os_rel = os_release(pe)
    ver = "22.04.3" if pe else "24.04.1"
    code = "jammy" if pe else "noble"
    dateien = {
        "/etc/hostname": hostname + "\n",
        "/etc/hosts": f"127.0.0.1 localhost\n127.0.1.1 {hostname}\n\n# The following lines are desirable for IPv6 capable hosts\n"
                      "::1     ip6-localhost ip6-loopback\nfe00::0 ip6-localnet\nff00::0 ip6-mcastprefix\n"
                      "ff02::1 ip6-allnodes\nff02::2 ip6-allrouters\n",
        "/usr/lib/os-release": os_rel,
        "/etc/lsb-release": f"DISTRIB_ID=Ubuntu\nDISTRIB_RELEASE={ver[:5]}\nDISTRIB_CODENAME={code}\n"
                            f'DISTRIB_DESCRIPTION="Ubuntu {ver} LTS"\n',
        "/etc/issue": f"Ubuntu {ver} LTS \\n \\l\n\n",
        "/etc/issue.net": f"Ubuntu {ver} LTS\n",
        "/etc/debian_version": "bookworm/sid\n" if pe else "trixie/sid\n",
        "/etc/passwd": PASSWD_BASIS,
        "/etc/group": GROUP_BASIS,
        "/etc/shadow": "root:$y$j9T$Kp0uVq3F8Lr4N2s1aB.cd/$Xv7sQe1jLm9Wn5Rt3Uy8Zp2Ko4Ib6Hc0Df1Ga2Ek3:19970:0:99999:7:::\n"
                       "daemon:*:19970:0:99999:7:::\nbin:*:19970:0:99999:7:::\nsys:*:19970:0:99999:7:::\n",
        "/etc/shells": "# /etc/shells: valid login shells\n/bin/sh\n/usr/bin/sh\n/bin/bash\n/usr/bin/bash\n/bin/rbash\n"
                       "/usr/bin/rbash\n/usr/bin/dash\n",
        "/etc/fstab": ("# /etc/fstab: static file system information.\n#\n# Use 'blkid' to print the universally unique "
                       "identifier for a\n# device; this may be used with UUID= as a more robust way to name devices\n"
                       "# that works even if disks are added and removed. See fstab(5).\n#\n"
                       "# <file system> <mount point>   <type>  <options>       <dump>  <pass>\n"
                       "# / was on /dev/ubuntu-vg/ubuntu-lv during curtin installation\n"
                       "/dev/disk/by-id/dm-uuid-LVM-bQ7dKJ2s3ek9Xm1Pa0Lr5Vt8Nc2Wz4Yh6Gf1Ds3Ab7Ce9Tq2Ru5Vo8Wn1Xm4Yl /"
                       " ext4 defaults 0 1\n# /boot was on /dev/nvme0n1p2 during curtin installation\n"
                       "/dev/disk/by-uuid/5c1e9a7b-2d4f-4e8a-9b3c-7f6e5d4c3b2a /boot ext4 defaults 0 1\n"
                       "# /boot/efi was on /dev/nvme0n1p1 during curtin installation\n"
                       "/dev/disk/by-uuid/A1B2-C3D4 /boot/efi vfat defaults 0 1\n/swap.img\tnone\tswap\tsw\t0\t0\n")
        if not pe else "overlay / overlay rw 0 0\ntmpfs /tmp tmpfs nosuid,nodev 0 0\n",
        "/etc/timezone": "Etc/UTC\n",
        "/etc/profile": ('# /etc/profile: system-wide .profile file for the Bourne shell (sh(1))\n'
                         '# and Bourne compatible shells (bash(1), ksh(1), ash(1), ...).\n\n'
                         'if [ "${PS1-}" ]; then\n  if [ "${BASH-}" ] && [ "$BASH" != "/bin/sh" ]; then\n'
                         '    # The file bash.bashrc already sets the default PS1.\n    # PS1=\'\\h:\\w\\$ \'\n'
                         '    if [ -f /etc/bash.bashrc ]; then\n      . /etc/bash.bashrc\n    fi\n  else\n'
                         '    if [ "$(id -u)" -eq 0 ]; then\n      PS1=\'# \'\n    else\n      PS1=\'$ \'\n    fi\n  fi\nfi\n\n'
                         'if [ -d /etc/profile.d ]; then\n  for i in /etc/profile.d/*.sh; do\n    if [ -r $i ]; then\n'
                         '      . $i\n    fi\n  done\n  unset i\nfi\n'),
        "/etc/bash.bashrc": "# System-wide .bashrc file for interactive bash(1) shells.\n\n"
                            "# If not running interactively, don't do anything\n[ -z \"$PS1\" ] && return\n",
        "/etc/environment": 'PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"\n',
        "/etc/resolv.conf": ("# This is /run/systemd/resolve/stub-resolv.conf managed by man:systemd-resolved(8).\n"
                             "# Do not edit.\n#\nnameserver 127.0.0.53\noptions edns0 trust-ad\nsearch .\n"),
        "/etc/ssh/sshd_config": "Include /etc/ssh/sshd_config.d/*.conf\nKbdInteractiveAuthentication no\nUsePAM yes\n"
                                "X11Forwarding yes\nPrintMotd no\nAcceptEnv LANG LC_*\nSubsystem\tsftp\t/usr/lib/openssh/sftp-server\n",
        "/etc/default/grub": ('GRUB_DEFAULT=0\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\n'
                              'GRUB_DISTRIBUTOR=`( . /etc/os-release; echo ${NAME:-Ubuntu} ) 2>/dev/null || echo Ubuntu`\n'
                              'GRUB_CMDLINE_LINUX_DEFAULT=""\nGRUB_CMDLINE_LINUX=""\n'),
        "/etc/modules": "# /etc/modules: kernel modules to load at boot time.\n#\n# This file contains the names of kernel "
                        "modules that should be loaded\n# at boot time, one per line. Lines beginning with \"#\" are ignored.\n"
                        "# Parameters can be specified after the module name.\n\n",
        "/etc/crontab": "# /etc/crontab: system-wide crontab\nSHELL=/bin/sh\nPATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n\n"
                        "17 *\t* * *\troot\tcd / && run-parts --report /etc/cron.hourly\n",
        "/etc/apt/sources.list": "# Ubuntu sources have moved to /etc/apt/sources.list.d/ubuntu.sources\n",
        "/etc/machine-id": "4f2c8e1a9b7d4c3e8f6a5b2d1c9e7f30\n",
        "/etc/skel/.bashrc": DOT_BASHRC,
        "/etc/skel/.profile": DOT_PROFILE,
        "/etc/skel/.bash_logout": "# ~/.bash_logout: executed by bash(1) when login shell exits.\n",
        "/root/.bashrc": DOT_BASHRC,
        "/root/.profile": DOT_PROFILE,
        "/proc/.keep": "",
        "/boot/config-" + sysm.kernel: "CONFIG_X86_64=y\n",
        "/var/log/dpkg.log": "2024-08-27 10:21:33 startup archives unpack\n",
        "/var/log/apt/history.log": "\nStart-Date: 2024-08-27  10:21:30\nCommandline: apt-get -y install nvidia-driver-550-server\nEnd-Date: 2024-08-27  10:24:12\n",
        "/var/log/bootstrap.log": "",
        "/usr/share/doc/README": "",
    }
    for p, inhalt in dateien.items():
        if p == "/proc/.keep":
            continue
        n = datei(inhalt, 0o644)
        _setze(fs, p, alt(n))
    fs.finde("/etc/shadow").mode = 0o640
    fs.finde("/etc/shadow").gid = 42
    fs.finde("/root/.bashrc").mode = 0o644
    _setze(fs, "/etc/os-release", alt(link("../usr/lib/os-release")))
    _setze(fs, "/etc/localtime", alt(link("/usr/share/zoneinfo/Etc/UTC")))
    _setze(fs, "/usr/share/zoneinfo/Etc/UTC", alt(datei("TZif2\0\0\0", 0o644)))
    _setze(fs, "/etc/mtab", alt(link("../proc/self/mounts")))
    k = sysm.kernel
    for n, g in ((f"/boot/vmlinuz-{k}", 14900000), (f"/boot/initrd.img-{k}", 68200000), (f"/boot/System.map-{k}", 8600000)):
        f = datei("\x7fELF kernel", 0o600 if "vmlinuz" in n or "System" in n else 0o644)
        f.groesse = g
        _setze(fs, n, alt(f))
    _setze(fs, "/boot/vmlinuz", alt(link(f"vmlinuz-{k}")))
    _setze(fs, "/boot/initrd.img", alt(link(f"initrd.img-{k}")))
    _setze(fs, "/boot/grub/grub.cfg", alt(datei("# DO NOT EDIT THIS FILE\n#\n# It is automatically generated by grub-mkconfig\n", 0o444)))
    _setze(fs, "/swap.img", alt(datei("", 0o600))) if not pe else None
    if not pe:
        fs.finde("/swap.img").groesse = 8589934592
    _setze(fs, "/root/.cache", alt(ordner(0o700)))
    _setze(fs, "/root/.ssh", alt(ordner(0o700)))
    _setze(fs, "/root/.ssh/authorized_keys", alt(datei("", 0o600)))
    _setze(fs, "/root/.bash_history", datei("", 0o600))
    # Bash und Freunde
    for n in ("bash", "sh", "rbash", "dash"):
        pass
    _setze(fs, "/usr/bin/bash", alt(_bin("bash", 1446024)))
    _setze(fs, "/usr/bin/sh", alt(link("dash")))
    _setze(fs, "/usr/bin/dash", alt(_bin("bash", 129784)))
    _setze(fs, "/usr/bin/rbash", alt(link("bash")))
    _setze(fs, "/usr/bin/which", alt(datei("#!/bin/sh\n#LABPROG:which\n", 0o755)))
    # /var/log mit typischen Dateien
    for n in ("syslog", "kern.log", "auth.log", "cloud-init.log", "cloud-init-output.log"):
        f = datei("", 0o640)
        f.uid, f.gid = (104, 4) if n in ("syslog", "kern.log", "auth.log") else (0, 4)
        _setze(fs, "/var/log/" + n, f)
    for n, mode, gid in (("btmp", 0o660, 43), ("wtmp", 0o664, 43), ("lastlog", 0o664, 43), ("faillog", 0o644, 0),
                         ("alternatives.log", 0o644, 0), ("dmesg", 0o640, 4)):
        f = datei("", mode)
        f.gid = gid
        _setze(fs, "/var/log/" + n, alt(f))
    for d, mode in (("installer", 0o755), ("landscape", 0o755), ("unattended-upgrades", 0o755), ("private", 0o700),
                    ("dist-upgrade", 0o755), ("sysstat", 0o755)):
        _setze(fs, "/var/log/" + d, alt(ordner(mode)))
    _setze(fs, "/var/log/README", alt(link("../../usr/share/doc/systemd/README.logs")))
    return fs


def _bin(name, groesse):
    from .system import binaer
    f = datei(binaer(name), 0o755)
    f.groesse = groesse
    return f


def lade_ls_colors():
    try:
        t = json.loads(_laden("texte.json"))["ls_colors"]
        import re
        m = re.search(r"LS_COLORS='([^']*)'", t)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def proc_dev(sysm, extra_dev=None, extra_proc=None):
    """/dev und /proc als dynamische Verzeichnisse."""
    fs = sysm.fs
    dev = fs.finde("/dev")

    def dev_gen():
        d = {
            "null": geraet(1, 3), "zero": geraet(1, 5), "full": geraet(1, 7), "random": geraet(1, 8),
            "urandom": geraet(1, 9), "tty": geraet(5, 0), "console": geraet(5, 1, 0o600), "ptmx": geraet(5, 2),
            "tty0": geraet(4, 0, 0o620), "tty1": geraet(4, 1, 0o620), "tty2": geraet(4, 2, 0o620),
            "kmsg": geraet(1, 11, 0o644), "mem": geraet(1, 1, 0o640), "ipmi0": geraet(239, 0, 0o600),
            "fuse": geraet(10, 229), "loop0": geraet(7, 0, 0o660, "b"), "loop1": geraet(7, 1, 0o660, "b"),
            "mapper": ordner(), "disk": ordner(), "pts": ordner(), "shm": ordner(0o1777), "hugepages": ordner(),
            "input": ordner(), "net": ordner(), "cpu": ordner(), "block": ordner(), "char": ordner(),
            "stdin": link("/proc/self/fd/0"), "stdout": link("/proc/self/fd/1"), "stderr": link("/proc/self/fd/2"),
            "fd": ordner(), "core": link("/proc/kcore"), "rtc": link("rtc0"), "rtc0": geraet(252, 0, 0o600),
        }
        d["null"].schreib_fn = lambda t: None
        for nr, inhalt in list(sysm.psub.items()):
            d["fd"].kinder[str(nr)] = datei(inhalt, 0o500)
        d["zero"].data = lambda: ""
        for k in d.values():
            k.mtime = k.atime = k.ctime = sysm.boot
        if extra_dev:
            d.update(extra_dev())
        return d
    dev.gen = dev_gen
    proc = fs.finde("/proc")

    def proc_gen():
        def f(fn):
            n = datei(fn, 0o444)
            n.nur_lesen = True
            n.mtime = n.atime = n.ctime = time.time()
            return n
        up = time.time() - sysm.boot
        d = {
            "version": f(lambda: f"Linux version {sysm.kernel} (buildd@lcy02-amd64-116) (x86_64-linux-gnu-gcc-13 "
                                 f"(Ubuntu 13.2.0-23ubuntu4) 13.2.0, GNU ld (GNU Binutils for Ubuntu) 2.42) "
                                 f"{sysm.kernel_version}\n"),
            "uptime": f(lambda: f"{time.time() - sysm.boot:.2f} {(time.time() - sysm.boot) * sysm.cpus * 0.98:.2f}\n"),
            "loadavg": f(lambda: f"0.08 0.05 0.01 1/{len(sysm.prozesse) + 900} {max(sysm.prozesse) if sysm.prozesse else 1}\n"),
            "cmdline": f(lambda: f"BOOT_IMAGE=/vmlinuz-{sysm.kernel} root=/dev/mapper/ubuntu--vg-ubuntu--lv ro\n"),
            "filesystems": f(lambda: "nodev\tsysfs\nnodev\ttmpfs\nnodev\tproc\n\text4\n\tvfat\nnodev\toverlay\n"),
            "mounts": f(lambda: sysm.hooks.get("mounts", lambda: "")()),
            "partitions": f(lambda: sysm.hooks.get("partitions", lambda: "major minor  #blocks  name\n")()),
            "cpuinfo": f(lambda: sysm.hooks.get("cpuinfo", lambda: "")()),
            "meminfo": f(lambda: sysm.hooks.get("meminfo", lambda: "")()),
            "modules": f(lambda: sysm.hooks.get("modules", lambda: "")()),
            "interrupts": f(lambda: "           CPU0       CPU1\n  0:         45          0  IR-IO-APIC    2-edge      timer\n"),
            "sys": ordner(), "net": ordner(), "driver": ordner(), "irq": ordner(), "bus": ordner(),
            "self": link(str(sysm.shell_pid or 1)),
            "kmsg": f(lambda: ""), "stat": f(lambda: f"cpu  {int(up * 10)} 0 {int(up * 3)} {int(up * 800)} 12 0 5 0 0 0\n"),
        }
        if extra_proc:
            d.update(extra_proc())
        for pid, p in list(sysm.prozesse.items()):
            pd = ordner(0o555)
            pd.kinder = {"cmdline": f(lambda p=p: p["cmd"].replace(" ", "\0") + "\0"),
                         "comm": f(lambda p=p: p["comm"] + "\n"),
                         "status": f(lambda p=p: f"Name:\t{p['comm']}\nState:\tS (sleeping)\nPid:\t{p['pid']}\nPPid:\t{p['ppid']}\n"),
                         "fd": ordner(0o500), "cwd": link("/"), "exe": link(p["cmd"].split()[0] if p["cmd"].startswith("/") else "/")}
            pd.mtime = pd.atime = pd.ctime = p["start"]
            d[str(pid)] = pd
        return d
    proc.gen = proc_gen
    proc.mode = 0o555
    fs.finde("/sys").mode = 0o555
    return fs
