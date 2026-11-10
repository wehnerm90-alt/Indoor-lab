"""Konsole (tty1): getty/login und interaktive Bash-Sitzung."""
import threading
import time

from . import syntax as sx
from .readline import CTRL_C, Readline, unvollstaendig
from .shell import Exit, ExpandError, Shell
from .streams import Interrupted, TtyReader, TtyWriter
from .tty import Tty


class Konsole:
    """Eine Bildschirmkonsole eines emulierten Rechners.

    profil: Objekt mit
      .system            -> System
      .issue()           -> Text vor dem Login-Prompt
      .pruefe(user, pw)  -> bool
      .motd()            -> Begruessung nach dem Login
      .shell_setup(sh)   -> Umgebung/Aliase setzen
      .aktion(name)      -> reboot/poweroff an das Lab melden
    """

    def __init__(self, profil, cols=100, rows=36):
        self.profil = profil
        self.system = profil.system
        self.tty = Tty(cols, rows)
        self.letzter_login = None
        self.lauf = True
        self.thread = threading.Thread(target=self._getty, daemon=True)
        self.thread.start()

    def beenden(self):
        self.lauf = False
        self.tty.beenden()

    # ---------------------------------------------------------------- getty/login
    def _getty(self):
        tty = self.tty
        try:
            tty.write(self.profil.boot_text())
            while self.lauf and tty.lebt:
                tty.write(self.profil.issue())
                fehl = 0
                while self.lauf:
                    tty.modus(canon=True, echo=True, isig=True)
                    tty.sigint.clear()
                    tty.write(f"{self.system.hostname} login: ")
                    try:
                        user = tty.read_line(pruef_sigint=False)
                    except Interrupted:
                        continue
                    if not self.lauf or not tty.lebt:
                        return
                    if user == "":
                        tty.write("\n")
                        continue
                    user = user.strip()
                    if not user:
                        continue
                    tty.modus(echo=False)
                    tty.write("Password: ")
                    pw = tty.read_line(pruef_sigint=False)
                    tty.modus(echo=True)
                    tty.write("\n")
                    pw = pw.rstrip("\n")
                    if self.profil.pruefe(user, pw):
                        break
                    time.sleep(2.5)
                    tty.write("\nLogin incorrect\n")
                    fehl += 1
                    if fehl >= 3:
                        time.sleep(1)
                        tty.write("\x1b[H\x1b[2J")
                        tty.write(self.profil.issue())
                        fehl = 0
                if not self.lauf or not tty.lebt:
                    return
                self._sitzung(user)
                if not self.lauf:
                    return
                tty.write("\x1b[H\x1b[2J\x1b[3J")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            tty.write(f"\nlogin: internal error: {e}\n")

    # ---------------------------------------------------------------- Bash
    def _sitzung(self, user):
        tty = self.tty
        sysm = self.system
        sh = Shell(sysm, tty, interaktiv=True, login=True)
        sysm.shell_pid = sh.pid
        sysm.proc_start(sh.pid, "bash", "-bash", ppid=1301)
        sh.fds = {0: TtyReader(tty), 1: TtyWriter(tty), 2: TtyWriter(tty)}
        self.profil.shell_setup(sh)
        sh.vars["COLUMNS"] = str(tty.cols)
        sh.vars["LINES"] = str(tty.rows)
        tty.write(self.profil.motd(self.letzter_login))
        self.letzter_login = time.time()
        rl = Readline(tty, sh)
        abmelden = False
        self.shell = sh
        try:
            while self.lauf and tty.lebt and not abmelden:
                try:
                    sh.jobs_melden()
                except Exception:  # noqa: BLE001
                    pass
                sh.vars["COLUMNS"] = str(tty.cols)
                sh.vars["LINES"] = str(tty.rows)
                zeile = rl.eingabe(sh.prompt("PS1"))
                if zeile is CTRL_C:
                    sh.status = 130
                    continue
                if zeile is None:
                    if sh.vars.get("IGNOREEOF") is not None:
                        tty.write('Use "logout" to leave the shell.\n')
                        continue
                    tty.write("logout\n")
                    break
                text = zeile
                abbruch = False
                while unvollstaendig(text, sh.aliases):
                    weiter = rl.eingabe(sh.prompt("PS2"), verlauf=[])
                    if weiter is CTRL_C:
                        abbruch = True
                        break
                    if weiter is None:
                        tty.write(f"-bash: syntax error: unexpected end of file\n")
                        abbruch = True
                        break
                    text += "\n" + weiter
                if abbruch:
                    sh.status = 130 if weiter is CTRL_C else 2
                    continue
                if not text.strip():
                    continue
                try:
                    text2, geaendert = sh.verlauf_expansion(text)
                except ExpandError as e:
                    tty.write(f"-bash: {e.msg}\n")
                    self._verlauf(sh, text)
                    continue
                if geaendert:
                    tty.write(text2 + "\n")
                    text = text2
                self._verlauf(sh, text)
                tty.sigint.clear()
                tty.modus(canon=True, echo=True, isig=True)
                try:
                    sh.run(text)
                except Interrupted:
                    sh.status = 130
                    tty.write("\n")
                except Exit as e:
                    sh.status = e.code
                    abmelden = True
                except sx.SyntaxErr as e:
                    tty.write(f"-bash: {e.text()}\n")
                except RecursionError:
                    tty.write("-bash: maximum recursion depth exceeded\n")
                except Exception as e:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    tty.write(f"-bash: {e}\n")
                finally:
                    tty.sigint.clear()
                    tty.modus(canon=True, echo=True, isig=True)
                if self.profil.nach_befehl(sh):
                    return
        finally:
            sysm.proc_ende(sh.pid)
            self.shell = None

    @staticmethod
    def _verlauf(sh, text):
        ctl = sh.vars.get("HISTCONTROL", "")
        if text.startswith(" ") and ("ignorespace" in ctl or "ignoreboth" in ctl):
            return
        eintrag = text.replace("\n", "; ") if "\n" in text and not sx.Lexer(text).heredocs else text
        if sh.hist and sh.hist[-1] == eintrag and ("ignoredups" in ctl or "ignoreboth" in ctl):
            return
        sh.hist.append(eintrag)
        if len(sh.hist) > 1000:
            del sh.hist[:-1000]
