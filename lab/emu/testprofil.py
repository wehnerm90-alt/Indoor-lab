"""Einfaches Ubuntu-Profil (fuer Tests)."""
import time

from . import ubuntu
from .system import System


class Profil:
    def __init__(self, hostname="testhost", pe=False):
        self.system = System(hostname=hostname)
        ubuntu.grundsystem(self.system, pe=pe, hostname=hostname)
        ubuntu.proc_dev(self.system)
        self.system.alle_installieren()
        self.ls_colors = ubuntu.lade_ls_colors()

    def boot_text(self):
        return ""

    def issue(self):
        return f"\nUbuntu 24.04.1 LTS {self.system.hostname} tty1\n\n"

    def pruefe(self, user, pw):
        return user == "root" and pw != ""

    def motd(self, letzter):
        return "Welcome\n\n"

    def shell_setup(self, sh):
        standard_env(sh, self.ls_colors)

    def nach_befehl(self, sh):
        return False


def standard_env(sh, ls_colors=None):
    v = sh.vars
    v.update({"HOME": "/root", "USER": "root", "LOGNAME": "root", "SHELL": "/bin/bash", "TERM": "linux",
              "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
              "LANG": "C.UTF-8", "HISTCONTROL": "ignoreboth", "HISTSIZE": "1000", "HISTFILESIZE": "2000",
              "PS1": r"${debian_chroot:+($debian_chroot)}\u@\h:\w\$ ", "PS2": "> ", "PS4": "+ ",
              "IFS": " \t\n", "SHLVL": "1", "MAIL": "/var/mail/root", "MOTD_SHOWN": "pam",
              "HOSTNAME": sh.system.hostname, "BASH": "/usr/bin/bash", "BASH_VERSION": "5.2.21(1)-release",
              "UID": "0", "EUID": "0", "PPID": "1301", "OPTIND": "1", "HISTFILE": "/root/.bash_history",
              "XDG_RUNTIME_DIR": "/run/user/0", "XDG_SESSION_ID": "1", "XDG_SESSION_TYPE": "tty",
              "XDG_SESSION_CLASS": "user", "LESSOPEN": "| /usr/bin/lesspipe %s", "LESSCLOSE": "/usr/bin/lesspipe %s %s"})
    if ls_colors:
        v["LS_COLORS"] = ls_colors
    sh.export |= {"HOME", "USER", "LOGNAME", "SHELL", "TERM", "PATH", "LANG", "SHLVL", "MAIL", "MOTD_SHOWN",
                  "LS_COLORS", "XDG_RUNTIME_DIR", "XDG_SESSION_ID", "XDG_SESSION_TYPE", "XDG_SESSION_CLASS",
                  "LESSOPEN", "LESSCLOSE", "PWD", "OLDPWD"}
    sh.readonly |= {"UID", "EUID", "PPID"}
    sh.aliases.update({"ls": "ls --color=auto", "grep": "grep --color=auto", "fgrep": "fgrep --color=auto",
                       "egrep": "egrep --color=auto", "ll": "ls -alF", "la": "ls -A", "l": "ls -CF",
                       "alert": 'notify-send --urgency=low -i "$([ $? = 0 ] && echo terminal || echo error)" '
                                '"$(history|tail -n1|sed -e \'\\\'\'s/^\\s*[0-9]\\+\\s*//;s/[;&|]\\s*alert$//\'\\\'\')"'})
    sh.cwd = "/root"
    sh.vars["PWD"] = "/root"
