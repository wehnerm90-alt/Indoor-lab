"""Nachgebildete BMCs (Redfish + Weboberflaeche) und das Ticket-Portal.

Port 8000: Ticket-Portal + Firmware-Depot
Port 8001-8003: je ein BMC (gpu01, gpu02, std01)
"""
import base64
import json
import os
import re
import secrets
import sys
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import bmcdata, core, kvm, vshell

HIER = os.path.dirname(os.path.abspath(__file__))
SESSIONS = {}      # token -> servername
PENDING = {}       # servername -> (komp, version, dateiname)
LOCK = threading.Lock()


_SH_LOCKS = {}


def vshell_lock(sh):
    return _SH_LOCKS.setdefault(id(sh), threading.Lock())


def html(name):
    with open(os.path.join(HIER, "web", name), encoding="utf-8") as f:
        return f.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "Lab-BMC/1.0"
    lab_name = None  # wird je Port gesetzt

    def log_message(self, fmt, *args):  # ruhig
        pass

    # ---------------- Antworten ----------------
    def senden(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=1, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def rf_fehler(self, code, msg):
        self.senden(code, {"error": {"code": "Base.1.8.GeneralError", "message": msg,
                                      "@Message.ExtendedInfo": [{"Message": msg}]}})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self):
        try:
            return json.loads(self.body() or b"{}")
        except json.JSONDecodeError:
            return None

    # ---------------- Routing ----------------
    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        pfad = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        try:
            if self.lab_name is None:
                return self.portal(method, pfad)
            return self.bmc(method, pfad)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self.rf_fehler(500, f"Internal error: {e}")

    # ---------------- Portal (8000) ----------------
    def portal(self, method, pfad):
        if pfad == "/":
            return self.senden(200, html("portal.html"), "text/html")
        if pfad == "/api/portal":
            st = core.lesen()
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            tk = st.get("ticket")
            servers = [{"name": n, "modell": s["modell"], "power": s["power"], "port": s["port"],
                        "url": f"http://{host}:{s['port']}/", "bmc_ok": core.bmc_bereit(s), "sn": s["sn"],
                        "rack": s["rack"]} for n, s in st["servers"].items()]
            return self.senden(200, {"ticket": tk, "servers": servers, "verlauf": st["verlauf"][-20:]})
        if pfad in ("/firmware", "/firmware/index.html"):
            core.fw_depot_erzeugen()
            dateien = sorted(os.listdir(core.FW_DIR))
            zeilen = "".join(f'<tr><td><a href="/firmware/{f}">{f}</a></td><td>{os.path.getsize(os.path.join(core.FW_DIR, f)):,} B</td></tr>'
                             for f in dateien)
            seite = (f"<!doctype html><meta charset=utf-8><title>Firmware-Depot</title>"
                     f"<style>body{{font-family:sans-serif;margin:2em}}td{{padding:4px 12px}}</style>"
                     f"<h2>Firmware-Depot (Lab, simuliert)</h2><p><a href='/'>← Portal</a></p>"
                     f"<table>{zeilen}</table><p>Vor dem Update Prüfsumme kontrollieren (SHA256SUMS).</p>")
            return self.senden(200, seite, "text/html")
        m = re.match(r"^/firmware/([\w.\-]+)$", pfad)
        if m:
            p = os.path.join(core.FW_DIR, m.group(1))
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    daten = f.read()
                typ = "text/plain" if p.endswith((".txt", "SUMS")) else "application/octet-stream"
                extra = {} if typ == "text/plain" else {"Content-Disposition": f'attachment; filename="{m.group(1)}"'}
                return self.senden(200, daten, typ, extra)
        return self.senden(404, "Not found", "text/plain")

    # ---------------- BMC ----------------
    def angemeldet(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                u, p = base64.b64decode(auth[6:]).decode().split(":", 1)
            except (ValueError, UnicodeDecodeError):
                return False
            return u == core.BMC_USER and p == core.BMC_PASS
        token = self.headers.get("X-Auth-Token")
        if not token:
            m = re.search(r"QSESSIONID=([\w-]+)", self.headers.get("Cookie", ""))
            token = m.group(1) if m else None
        return bool(token) and SESSIONS.get(token) == self.lab_name

    def bmc(self, method, pfad):
        name = self.lab_name
        st0 = core.lesen()
        s0 = st0["servers"][name]
        if not core.bmc_bereit(s0):
            if pfad == "/":
                return self.senden(200, html("bmc.html"), "text/html")
            return self.rf_fehler(503, "BMC is rebooting, please wait")
        if pfad == "/":
            return self.senden(200, html("bmc.html"), "text/html")
        if pfad == "/kvm":
            return self.senden(200, html("kvm.html"), "text/html")
        if pfad == "/redfish/v1" and method == "GET":
            return self.senden(200, self.service_root())
        if pfad == "/redfish/v1/SessionService/Sessions" and method == "POST" or pfad == "/api/session" and method == "POST":
            d = self.json_body() or {}
            if d.get("UserName", d.get("username")) == core.BMC_USER and d.get("Password", d.get("password")) == core.BMC_PASS:
                token = secrets.token_hex(16)
                SESSIONS[token] = name
                with core.zustand() as st:
                    bmcdata.audit(st["servers"][name], core.BMC_USER, f"Login from {self.client_address[0]}",
                                  "Redfish" if pfad.startswith("/redfish") else "Web")
                return self.senden(201, {"Id": token[:8], "UserName": core.BMC_USER},
                                   extra={"X-Auth-Token": token,
                                          "Set-Cookie": f"QSESSIONID={token}; Path=/; HttpOnly",
                                          "Location": f"/redfish/v1/SessionService/Sessions/{token[:8]}"})
            return self.rf_fehler(401, "Invalid username or password")
        if not self.angemeldet():
            return self.rf_fehler(401, "Unauthorized")
        if pfad == "/api/session" and method == "DELETE":
            m = re.search(r"QSESSIONID=([\w-]+)", self.headers.get("Cookie", ""))
            if m:
                SESSIONS.pop(m.group(1), None)
            return self.senden(200, {"ok": True}, extra={"Set-Cookie": "QSESSIONID=; Path=/; Max-Age=0"})
        if pfad.startswith("/api/"):
            return self.ui_api(method, pfad)
        if pfad.startswith("/redfish/v1"):
            return self.redfish(method, pfad)
        return self.rf_fehler(404, "Resource not found")

    # ---------------- Weboberflaeche-API ----------------
    def ui_api(self, method, pfad):
        name = self.lab_name
        if pfad == "/api/status":
            st = core.lesen()
            s = st["servers"][name]
            m = core.MODELS[s["modell"]]
            sens = [{"name": n, "wert": (None if w is None else (f"0x{w:02x}" if e == "discrete" else w)),
                     "einheit": "" if e == "discrete" else e, "status": stt} for n, w, e, stt in core.sensoren(s)]
            komp = {}
            for k, liste in s["comp"].items():
                komp[k] = [{"label": d.get("name") or f"{k.upper()}{i}", "sn": d["sn"], "status": d["status"],
                            "rebuild": core.rebuild_pct(d) if k == "hdd" else None} for i, d in enumerate(liste)]
            return self.senden(200, {
                "name": name, "modell": s["modell"], "beschreibung": m["beschreibung"], "sn": s["sn"],
                "power": s["power"], "fw": s["fw"], "sensoren": sens, "komponenten": komp,
                "sel": [{"id": e["id"], "zeit": core.ts(e["t"]), "typ": e["typ"], "sensor": e["sensor"],
                         "event": e["event"], "dir": e["dir"]} for e in reversed(s["sel"])],
                "tasks": s["tasks"][-3:], "pending": PENDING.get(name), "health": self.health(s),
                "idl": bmcdata.idl(s), "alarme": bmcdata.alarme(s),
                "audit": [{"zeit": core.ts(a["t"]), **a} for a in reversed(s.get("audit", [])[-150:])],
                "settings": bmcdata.einstellungen(s), "uid": s.get("uid", False),
                "build": "Jul 9 2024 11:32:18 CST" if s["fw"]["BMC"] == "4.27.0" else "Sep 30 2026 09:12:44 CST",
                "bmc_since": max(s.get("bmc_boot_until", 0), st.get("erstellt", 0))})
        if pfad == "/api/power" and method == "POST":
            d = self.json_body() or {}
            with core.zustand() as st:
                ok, msg = core.power(st, st["servers"][name], d.get("action", ""))
                bmcdata.audit(st["servers"][name], core.BMC_USER, f"Power control: {d.get('action')}")
            return self.senden(200 if ok else 400, {"ok": ok, "message": msg})
        if pfad in ("/api/sel/clear", "/api/idl/clear") and method == "POST":
            with core.zustand() as st:
                core.sel_clear(st, st["servers"][name])
                bmcdata.audit(st["servers"][name], core.BMC_USER, "Clear " + ("IDL" if "idl" in pfad else "SEL"))
            return self.senden(200, {"ok": True, "message": "Log cleared."})
        if pfad == "/api/settings" and method == "POST":
            d = self.json_body() or {}
            bereich, werte = d.get("bereich"), d.get("werte")
            if bereich not in bmcdata.STANDARD_EINSTELLUNGEN or werte is None:
                return self.senden(400, {"ok": False, "message": "Invalid settings"})
            with core.zustand() as st:
                s = st["servers"][name]
                e = bmcdata.einstellungen(s)
                if isinstance(e[bereich], dict):
                    e[bereich].update({k: v for k, v in werte.items() if k in e[bereich]})
                else:
                    e[bereich] = werte
                bmcdata.audit(s, core.BMC_USER, f"Settings changed: {bereich}")
            return self.senden(200, {"ok": True, "message": "Settings saved successfully."})
        if pfad == "/api/uid" and method == "POST":
            d = self.json_body() or {}
            with core.zustand() as st:
                st["servers"][name]["uid"] = bool(d.get("on"))
                bmcdata.audit(st["servers"][name], core.BMC_USER, f"UID LED {'on' if d.get('on') else 'off'}")
            return self.senden(200, {"ok": True, "message": "UID LED " + ("on" if d.get("on") else "off")})
        if pfad == "/api/factory" and method == "POST":
            with core.zustand() as st:
                s = st["servers"][name]
                s.pop("settings", None)
                bmcdata.audit(s, core.BMC_USER, "Restore factory defaults")
                core.bmc_reset(st, s)
            return self.senden(200, {"ok": True, "message": "Factory defaults restored. BMC is rebooting."})
        if pfad == "/api/onekey":
            st = core.lesen()
            s = st["servers"][name]
            daten = bmcdata.onekey(s, st)
            datei = f"onekeylog_{s['sn']}_{datetime.now():%Y%m%d%H%M%S}.tar.gz"
            return self.senden(200, daten, "application/gzip", {"Content-Disposition": f'attachment; filename="{datei}"'})
        if pfad == "/api/bmc/reset" and method == "POST":
            with core.zustand() as st:
                core.bmc_reset(st, st["servers"][name])
            return self.senden(200, {"ok": True})
        if pfad == "/api/fw/upload" and method == "POST":
            daten = self.body()
            dateiname = urllib.parse.unquote(self.headers.get("X-Filename", "upload.bin"))
            with core.zustand() as st:
                s = st["servers"][name]
                ok, msg, komp, ver = core.fw_pruefen(daten, s["modell"])
                if not ok:
                    core.fw_fehler_merken(st, s, msg)
                    PENDING.pop(name, None)
                    return self.senden(400, {"ok": False, "message": msg})
                PENDING[name] = {"komponente": komp, "version": ver, "datei": dateiname,
                                 "aktuell": s["fw"].get(komp)}
            return self.senden(200, {"ok": True, "pending": PENDING[name]})
        if pfad == "/api/fw/flash" and method == "POST":
            p = PENDING.get(name)
            if not p:
                return self.senden(400, {"ok": False, "message": "No image uploaded"})
            with core.zustand() as st:
                s = st["servers"][name]
                ok, msg, task = core.fw_starten(s, p["komponente"], p["version"])
                if not ok:
                    core.fw_fehler_merken(st, s, msg)
                    return self.senden(400, {"ok": False, "message": msg})
                core.aktion(st, f"Flash {p['komponente']} {p['version']} gestartet (Web)")
                bmcdata.audit(s, core.BMC_USER, f"Firmware update started: {p['komponente']} {p['version']}")
            PENDING.pop(name, None)
            return self.senden(200, {"ok": True, "task": task})
        if pfad == "/api/kvm/screen":
            st = core.lesen()
            return self.senden(200, kvm.bildschirm(st["servers"][name]))
        if pfad.startswith("/api/kvm/shell/") and method == "POST":
            st = core.lesen()
            if kvm.bildschirm(st["servers"][name])["mode"] != "os":
                return self.senden(409, {"message": "Host OS not running"})
            d = self.json_body() or {}
            if pfad == "/api/kvm/shell/start":
                sid = vshell.neu(name)
                return self.senden(200, {"sid": sid, "prompt": vshell.holen(sid, name).prompt()})
            sh = vshell.holen(str(d.get("sid", "")), name)
            if not sh:
                return self.senden(410, {"message": "Session expired"})
            zeile = str(d.get("line", ""))[:2000]
            if pfad == "/api/kvm/shell/exec":
                with vshell_lock(sh):
                    out, akt = sh.ausfuehren(zeile)
                return self.senden(200, {"output": out, "action": akt, "prompt": sh.prompt()})
            if pfad == "/api/kvm/shell/complete":
                return self.senden(200, {"matches": sh.vervollstaendigen(zeile)[:200]})
        if pfad == "/api/kvm/exec" and method == "POST":
            d = self.json_body() or {}
            st = core.lesen()
            if kvm.bildschirm(st["servers"][name])["mode"] != "os":
                return self.senden(409, {"output": "", "action": None, "message": "Host OS not running"})
            out, akt = kvm.ausfuehren(name, str(d.get("cmd", ""))[:500])
            return self.senden(200, {"output": out, "action": akt})
        if pfad == "/api/fw/cancel" and method == "POST":
            PENDING.pop(name, None)
            return self.senden(200, {"ok": True})
        return self.rf_fehler(404, "Resource not found")

    @staticmethod
    def health(s):
        schlecht = any(d["status"] not in ("ok", "rebuild") for liste in s["comp"].values() for d in liste)
        return "Critical" if schlecht else ("Warning" if any(d["status"] == "rebuild" for d in s["comp"]["hdd"]) else "OK")

    # ---------------- Redfish ----------------
    def service_root(self):
        return {"@odata.id": "/redfish/v1", "@odata.type": "#ServiceRoot.v1_15_0.ServiceRoot", "Id": "RootService",
                "Name": "Root Service (Lab)", "RedfishVersion": "1.15.0", "Vendor": "Inspur (Lab)",
                "Systems": {"@odata.id": "/redfish/v1/Systems"}, "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
                "Managers": {"@odata.id": "/redfish/v1/Managers"},
                "UpdateService": {"@odata.id": "/redfish/v1/UpdateService"},
                "TaskService": {"@odata.id": "/redfish/v1/TaskService"},
                "SessionService": {"@odata.id": "/redfish/v1/SessionService"}}

    @staticmethod
    def coll(oid, mitglieder, name):
        return {"@odata.id": oid, "Name": name, "Members@odata.count": len(mitglieder),
                "Members": [{"@odata.id": m} for m in mitglieder]}

    @staticmethod
    def status(st_):
        return {"State": "Enabled" if st_ != "absent" else "Absent",
                "Health": "OK" if st_ in ("ok", "rebuild") else "Critical"}

    def redfish(self, method, pfad):
        name = self.lab_name
        st = core.lesen()
        s = st["servers"][name]
        R = "/redfish/v1"
        if method == "GET":
            if pfad == R + "/Systems":
                return self.senden(200, self.coll(pfad, [R + "/Systems/1"], "Computer System Collection"))
            if pfad == R + "/Systems/1":
                ram = len(s["comp"]["dimm"]) * 64
                return self.senden(200, {
                    "@odata.id": pfad, "Id": "1", "Name": s["modell"], "Manufacturer": "Inspur (Lab)",
                    "Model": s["modell"], "SerialNumber": s["sn"], "PowerState": "On" if s["power"] == "on" else "Off",
                    "BiosVersion": s["fw"]["BIOS"], "Status": {"State": "Enabled", "Health": self.health(s)},
                    "ProcessorSummary": {"Count": 2, "Model": "Intel Xeon Platinum 8480+ (Lab)"},
                    "MemorySummary": {"TotalSystemMemoryGiB": ram},
                    "Memory": {"@odata.id": pfad + "/Memory"},
                    "LogServices": {"@odata.id": R + "/Managers/1/LogServices"},
                    "Actions": {"#ComputerSystem.Reset": {
                        "target": pfad + "/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": ["On", "ForceOff", "GracefulShutdown", "ForceRestart", "PowerCycle"]}}})
            if pfad == R + "/Systems/1/Memory":
                return self.senden(200, self.coll(pfad, [f"{pfad}/{d['name']}" for d in s["comp"]["dimm"]], "Memory Collection"))
            m = re.match(rf"^{R}/Systems/1/Memory/(\w+)$", pfad)
            if m:
                d = next((d for d in s["comp"]["dimm"] if d["name"] == m.group(1)), None)
                if d:
                    return self.senden(200, {"@odata.id": pfad, "Id": d["name"], "Name": d["name"], "CapacityMiB": 65536,
                                             "MemoryDeviceType": "DDR5", "SerialNumber": d["sn"],
                                             "Status": self.status(d["status"])})
            if pfad == R + "/Chassis":
                return self.senden(200, self.coll(pfad, [R + "/Chassis/1"], "Chassis Collection"))
            if pfad == R + "/Chassis/1":
                return self.senden(200, {"@odata.id": pfad, "Id": "1", "Name": "Chassis", "Model": s["modell"],
                                         "SerialNumber": s["sn"], "Status": {"Health": self.health(s)},
                                         "Thermal": {"@odata.id": pfad + "/Thermal"}, "Power": {"@odata.id": pfad + "/Power"},
                                         "PCIeDevices": {"@odata.id": pfad + "/PCIeDevices"}})
            if pfad == R + "/Chassis/1/Thermal":
                sens = core.sensoren(s)
                return self.senden(200, {"@odata.id": pfad, "Name": "Thermal",
                                         "Temperatures": [{"Name": n, "ReadingCelsius": w, "Status": {"Health": "OK"}}
                                                          for n, w, e, x in sens if e == "degrees C"],
                                         "Fans": [{"Name": n, "Reading": w, "ReadingUnits": "RPM",
                                                   "Status": {"Health": "OK" if x == "ok" else "Critical"}}
                                                  for n, w, e, x in sens if e == "RPM"]})
            if pfad == R + "/Chassis/1/Power":
                return self.senden(200, {"@odata.id": pfad, "Name": "Power", "PowerSupplies": [
                    {"MemberId": str(i), "Name": f"PSU{i}", "SerialNumber": p["sn"], "PowerCapacityWatts": 3000 if s["typ"] == "hgx" else 1600,
                     "Status": self.status(p["status"])} for i, p in enumerate(s["comp"]["psu"])]})
            if pfad == R + "/Chassis/1/PCIeDevices":
                return self.senden(200, self.coll(pfad, [f"{pfad}/GPU{i}" for i, g in enumerate(s["comp"]["gpu"])
                                                         if g["status"] != "offbus"], "PCIe Device Collection"))
            m = re.match(rf"^{R}/Chassis/1/PCIeDevices/GPU(\d+)$", pfad)
            if m and int(m.group(1)) < len(s["comp"]["gpu"]):
                g = s["comp"]["gpu"][int(m.group(1))]
                if g["status"] != "offbus":
                    return self.senden(200, {"@odata.id": pfad, "Id": f"GPU{m.group(1)}", "Manufacturer": "NVIDIA",
                                             "Model": core.MODELS[s["modell"]]["gpu_name"], "SerialNumber": g["sn"],
                                             "Status": self.status(g["status"])})
            if pfad == R + "/Managers":
                return self.senden(200, self.coll(pfad, [R + "/Managers/1"], "Manager Collection"))
            if pfad == R + "/Managers/1":
                return self.senden(200, {"@odata.id": pfad, "Id": "1", "Name": "BMC", "ManagerType": "BMC",
                                         "FirmwareVersion": s["fw"]["BMC"], "Status": {"Health": "OK"},
                                         "LogServices": {"@odata.id": pfad + "/LogServices"},
                                         "Actions": {"#Manager.Reset": {"target": pfad + "/Actions/Manager.Reset"}}})
            if pfad == R + "/Managers/1/LogServices":
                return self.senden(200, self.coll(pfad, [pfad + "/SEL"], "Log Service Collection"))
            if pfad == R + "/Managers/1/LogServices/SEL":
                return self.senden(200, {"@odata.id": pfad, "Id": "SEL", "Entries": {"@odata.id": pfad + "/Entries"},
                                         "Actions": {"#LogService.ClearLog": {"target": pfad + "/Actions/LogService.ClearLog"}}})
            if pfad == R + "/Managers/1/LogServices/SEL/Entries":
                return self.senden(200, {"@odata.id": pfad, "Name": "SEL Entries", "Members@odata.count": len(s["sel"]),
                                         "Members": [{"Id": str(e["id"]), "Created": core.ts(e["t"]), "EntryType": "SEL",
                                                      "SensorType": e["typ"], "Message": f"{e['sensor']}: {e['event']} ({e['dir']})",
                                                      "Severity": "Critical" if re.search(r"Uncorrectable|Failure|Fault|Critical|Lost|CATERR", e["event"]) else "OK"}
                                                     for e in s["sel"]]})
            if pfad == R + "/UpdateService":
                return self.senden(200, {"@odata.id": pfad, "Id": "UpdateService", "ServiceEnabled": True,
                                         "HttpPushUri": R + "/UpdateService/upload",
                                         "MultipartHttpPushUri": R + "/UpdateService/upload",
                                         "FirmwareInventory": {"@odata.id": pfad + "/FirmwareInventory"},
                                         "Actions": {"#UpdateService.SimpleUpdate": {
                                             "target": pfad + "/Actions/UpdateService.SimpleUpdate",
                                             "TransferProtocol@Redfish.AllowableValues": ["HTTP"]}}})
            if pfad == R + "/UpdateService/FirmwareInventory":
                return self.senden(200, self.coll(pfad, [f"{pfad}/{k}" for k in s["fw"]], "Firmware Inventory"))
            m = re.match(rf"^{R}/UpdateService/FirmwareInventory/(\w+)$", pfad)
            if m and m.group(1) in s["fw"]:
                return self.senden(200, {"@odata.id": pfad, "Id": m.group(1), "Name": m.group(1),
                                         "Version": s["fw"][m.group(1)], "Updateable": m.group(1) in ("BMC", "BIOS"),
                                         "Status": {"Health": "OK"}})
            if pfad == R + "/TaskService":
                return self.senden(200, {"@odata.id": pfad, "Tasks": {"@odata.id": pfad + "/Tasks"}})
            if pfad == R + "/TaskService/Tasks":
                return self.senden(200, self.coll(pfad, [f"{pfad}/{t['id']}" for t in s["tasks"]], "Task Collection"))
            m = re.match(rf"^{R}/TaskService/Tasks/(\d+)$", pfad)
            if m:
                t = next((t for t in s["tasks"] if t["id"] == int(m.group(1))), None)
                if t:
                    return self.senden(200, self.task_json(t))
            if pfad == R + "/SessionService":
                return self.senden(200, {"@odata.id": pfad, "Sessions": {"@odata.id": pfad + "/Sessions"}})
            return self.rf_fehler(404, "Resource not found")

        if method == "POST":
            if pfad == R + "/Systems/1/Actions/ComputerSystem.Reset":
                d = self.json_body() or {}
                mapping = {"On": "on", "ForceOff": "off", "GracefulShutdown": "soft", "ForceRestart": "reset",
                           "PowerCycle": "cycle"}
                if d.get("ResetType") not in mapping:
                    return self.rf_fehler(400, "Invalid ResetType")
                with core.zustand() as st:
                    ok, msg = core.power(st, st["servers"][name], mapping[d["ResetType"]])
                return self.senden(204, b"") if ok else self.rf_fehler(400, msg)
            if pfad == R + "/Managers/1/LogServices/SEL/Actions/LogService.ClearLog":
                with core.zustand() as st:
                    core.sel_clear(st, st["servers"][name])
                return self.senden(204, b"")
            if pfad == R + "/Managers/1/Actions/Manager.Reset":
                with core.zustand() as st:
                    core.bmc_reset(st, st["servers"][name])
                return self.senden(204, b"")
            if pfad in (R + "/UpdateService/upload", R + "/UpdateService"):
                daten = self.multipart_datei(self.body())
                return self.update(daten, "Redfish-Upload")
            if pfad == R + "/UpdateService/Actions/UpdateService.SimpleUpdate":
                d = self.json_body() or {}
                uri = d.get("ImageURI", "")
                datei = os.path.basename(urllib.parse.urlparse(uri).path)
                pfad_fw = os.path.join(core.FW_DIR, datei)
                if not datei or not os.path.isfile(pfad_fw):
                    return self.rf_fehler(400, f"Unable to download image from {uri}")
                with open(pfad_fw, "rb") as f:
                    return self.update(f.read(), "SimpleUpdate")
            return self.rf_fehler(404, "Resource not found")
        return self.rf_fehler(405, "Method not allowed")

    def multipart_datei(self, roh):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(\"?)([^\";]+)\1", ctype)
        if not m:
            return roh  # HttpPushUri: Image direkt im Body
        grenze = b"--" + m.group(2).encode()
        for teil in roh.split(grenze):
            if b"\r\n\r\n" not in teil:
                continue
            kopf, inhalt = teil.split(b"\r\n\r\n", 1)
            if b"filename=" in kopf or b'name="UpdateFile"' in kopf:
                return inhalt[:-2] if inhalt.endswith(b"\r\n") else inhalt
        return b""

    def update(self, daten, quelle):
        name = self.lab_name
        with core.zustand() as st:
            s = st["servers"][name]
            ok, msg, komp, ver = core.fw_pruefen(daten, s["modell"])
            if not ok:
                core.fw_fehler_merken(st, s, msg)
                return self.rf_fehler(400, msg)
            ok, msg, task = core.fw_starten(s, komp, ver)
            if not ok:
                core.fw_fehler_merken(st, s, msg)
                return self.rf_fehler(400, msg)
            core.aktion(st, f"Flash {komp} {ver} gestartet ({quelle})")
        return self.senden(202, self.task_json(task),
                           extra={"Location": f"/redfish/v1/TaskService/Tasks/{task['id']}"})

    @staticmethod
    def task_json(t):
        return {"@odata.id": f"/redfish/v1/TaskService/Tasks/{t['id']}", "Id": str(t["id"]),
                "Name": f"{t['komponente']} firmware update to {t['version']}", "TaskState": t["state"],
                "PercentComplete": t.get("pct", 0), "TaskStatus": "OK",
                "Messages": [{"Message": f"Flashing {t['komponente']} {t['version']}"}]}


def starten():
    core.fw_depot_erzeugen()
    core.lesen()
    server = []
    ports = [(None, 8000)] + [(n, i["port"]) for n, i in core.SERVERS.items()]
    for name, port in ports:
        h = type(f"H{port}", (Handler,), {"lab_name": name})
        httpd = ThreadingHTTPServer(("0.0.0.0", port), h)
        httpd.daemon_threads = True
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        server.append(httpd)
        print(f"Lab-Dienst auf Port {port} ({name or 'Portal'})", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    starten()
