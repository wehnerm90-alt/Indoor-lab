"""Daten fuer die BMC-Oberflaeche: IDL-Log, aktuelle Alarme, Audit-Log, Einstellungen, One-key-Log."""
import hashlib
import io
import json
import tarfile
import time
from datetime import datetime

from . import core

STANDARD_EINSTELLUNGEN = {
    "network": {"hostname": "", "mode": "Static", "ip": "", "netmask": "255.255.255.0", "gateway": "10.10.0.1",
                "dns": "10.10.0.53", "vlan": False, "vlan_id": 0, "ncsi": "Dedicated"},
    "services": {"web": True, "web_port": 443, "kvm": True, "kvm_port": 7578, "ssh": True, "ssh_port": 22,
                 "ipmi": True, "ipmi_port": 623, "snmp": False, "snmp_port": 161, "redfish": True},
    "ntp": {"mode": "NTP", "server1": "pool.ntp.org", "server2": "", "timezone": "Europe/Berlin"},
    "mail": {"enable": False, "smtp": "", "port": 25, "sender": "", "recipients": "", "level": "Warning"},
    "snmp_trap": {"enable": False, "version": "v2c", "community": "public", "dest1": "", "dest2": ""},
    "snmp_setget": {"v2c": False, "ro_community": "public", "v3": False, "v3_user": ""},
    "log": {"sel_policy": "Circular", "audit": True, "syslog": False, "syslog_server": "", "syslog_port": 514},
    "boot": {"mode": "UEFI", "device": "Hard Disk", "persist": "Once"},
    "powercap": {"enable": False, "limit": 3000},
    "fan": {"mode": "Auto", "duty": 60},
    "vmedia": {"image": "", "mounted": False},
    "users": [{"name": "admin", "role": "Administrator", "enabled": True}],
}


def einstellungen(s):
    e = s.setdefault("settings", {})
    for k, v in STANDARD_EINSTELLUNGEN.items():
        if k not in e:
            e[k] = json.loads(json.dumps(v))
    if not e["network"]["hostname"]:
        e["network"]["hostname"] = "IEI" + s["sn"]
    if not e["network"]["ip"]:
        e["network"]["ip"] = f"10.10.0.{10 + list(core.SERVERS).index(s['name'])}"
    return e


def audit(s, user, aktion_, quelle="Web"):
    s.setdefault("audit", []).append({"t": core.now(), "user": user, "quelle": quelle, "aktion": aktion_})
    s["audit"] = s["audit"][-300:]


def _code(text):
    return hashlib.md5(text.encode()).hexdigest()[:8].upper()


def idl(s):
    """Diagnose-Log: SEL-Eintraege mit Typ, Schweregrad, Event-Code und Handlungsempfehlung,
    dazu Diagnosen, die der BMC aus dem HGX/GPU-Zustand ableitet."""
    out = []
    for e in s["sel"]:
        ev, sensor = e["event"], e["sensor"]
        typ, sev, tipp = "SYSTEM", "Info", "No action required."
        if "Uncorrectable ECC" in ev:
            typ, sev = "MEMORY", "Critical"
            tipp = (f"Uncorrectable ECC on {sensor}. Inspur flow: 1) server off (customer), reseat the DIMM – alarm gone? "
                    "2) replace the DIMM – alarm gone? 3) otherwise GS. Check the slot name twice.")
        elif "Memory Device Disabled" in ev:
            typ, sev = "MEMORY", "Warning"
            tipp = f"{sensor} was disabled by BIOS during POST. The module is not in use – reseat, if it stays disabled replace it."
        elif "Correctable ECC" in ev:
            typ, sev = "MEMORY", "Warning"
            tipp = "Correctable errors. Monitor; if they keep increasing or an uncorrectable error follows, follow the DIMM flow."
        elif "CATERR" in sensor or "IERR" in ev:
            typ, sev = "PROCESSOR", "Critical"
            tipp = "CATERR/IERR. Check the IDL entries just before (memory, PCIe) – often the CPU is not the root cause."
        elif sensor.startswith("PSU") and "AC lost" in ev:
            typ, sev = "POWER SUPPLY", "Critical"
            tipp = f"{sensor.split('_')[0]} input lost. Check power cord and PDU first – often not a PSU defect."
        elif sensor.startswith("PSU") and "Failure" in ev:
            typ, sev = "POWER SUPPLY", "Critical"
            tipp = f"{sensor.split('_')[0]} failure. Check seat/contact in the slot, then replace the PSU (hot-swap)."
        elif "Redundancy Lost" in ev:
            typ, sev = "POWER SUPPLY", "Warning"
            tipp = "Power redundancy lost. Find the affected PSU in this log."
        elif sensor.startswith("FAN"):
            typ, sev = "FAN", "Critical"
            tipp = f"{sensor.split('_')[0]} below lower critical threshold. Reseat or replace the fan module (hot-swap)."
        elif sensor.startswith("PCIe_GPU"):
            typ, sev = "PCIE", "Critical"
            n = sensor.replace("PCIe_GPU", "")
            tipp = (f"PCIe error on GPU slot {n} (BDF {core.GPU_BUS[int(n)]}:00.0). Inspur flow: 1) check power, reseat the card – "
                    "alarm gone? 2) replace the card – alarm gone? 3) mainboard/GPU board 4) GS. Afterwards fieldiag.")
        elif "Drive" in ev and "Fault" in ev:
            typ, sev = "STORAGE", "Critical"
            tipp = "Hard disk detected fault. Compare slot AND disk SN with the ticket – if they do not match, stop and escalate."
        elif "Rebuild" in ev:
            typ = "STORAGE"
        elif "SysRestart" in sensor or "boot" in ev.lower() or "BIOS_Boot" in sensor:
            typ = "SYS RESTART"
        elif "ACPI" in sensor:
            typ = "ACPI STATUS"
        elif "firmware updated" in ev:
            typ = "FIRMWARE"
        elif "Presence" in sensor:
            typ = "ENTITY PRESENCE"
        elif "Log area" in ev:
            typ = "EVENT LOG"
        richtung = "Deassert" if e["dir"] == "Deasserted" else "Assert"
        desc = f"{sensor} {ev}  - {richtung}"
        out.append({"id": e["id"], "sev": sev, "typ": typ, "desc": desc, "t": e["t"],
                    "code": _code(typ + ev)[:4] + "0" + _code(sensor)[:3], "host": "IEI" + s["sn"], "tipp": tipp})
    # aus dem HGX/GPU-Zustand abgeleitete Diagnosen (stehen nicht im SEL)
    basis = max([e["t"] for e in s["sel"]] or [core.now()]) - 600
    nid = 9000
    for i, g in enumerate(s["comp"]["gpu"]):
        if g["status"] == "remap_fail":
            nid += 1
            out.append({"id": nid, "sev": "Critical", "typ": "GPU", "t": basis,
                        "desc": f"GPU{i} (SN {g['sn']}) HBM uncorrectable error, row remapping failure - Assert",
                        "code": "4F0A0" + str(i), "host": "IEI" + s["sn"],
                        "tipp": f"Row remapping failed on GPU{i}. Replace the GPU and run fieldiag afterwards."})
        elif g["status"] == "remap_pending":
            nid += 1
            out.append({"id": nid, "sev": "Warning", "typ": "GPU", "t": basis,
                        "desc": f"GPU{i} (SN {g['sn']}) HBM ECC error, row remap pending - Assert",
                        "code": "4F0B0" + str(i), "host": "IEI" + s["sn"],
                        "tipp": f"Row remap pending on GPU{i}. A GPU reset or power cycle applies the remap. "
                                "Run fieldiag afterwards – replacement is usually NOT required."})
    for bb in s["comp"]["baseboard"]:
        if bb["status"] == "nvlink":
            nid += 1
            out.append({"id": nid, "sev": "Critical", "typ": "NVSWITCH", "t": basis,
                        "desc": f"NVSwitch{bb['nvswitch_bad']} fatal link error, links down - Assert",
                        "code": "4F2C000" + str(bb["nvswitch_bad"]), "host": "IEI" + s["sn"],
                        "tipp": "NVSwitch fatal error. NVSwitches are part of the HGX baseboard – replace the baseboard "
                                "and verify with fieldiag (NVLink test)."})
    out.sort(key=lambda x: x["t"], reverse=True)
    for x in out:
        x["zeit"] = datetime.fromtimestamp(x["t"]).strftime("%Y-%m-%dT%H:%M:%S+01:00")
    return out


def alarme(s):
    a = []
    for k, liste in s["comp"].items():
        for i, d in enumerate(liste):
            if d["status"] in ("ok",):
                continue
            label = d.get("name") or f"{k.upper()}{i}"
            text = {"offbus": "Device not present / link lost", "remap_fail": "HBM row remapping failure",
                    "remap_pending": "HBM row remap pending", "nvlink": f"NVSwitch{d.get('nvswitch_bad')} links down",
                    "ue": "Uncorrectable ECC error", "fail": "Failure detected",
                    "rebuild": "Rebuild in progress"}.get(d["status"], d["status"])
            sev = "Warning" if d["status"] in ("remap_pending", "rebuild") else "Critical"
            a.append({"sev": sev, "komponente": label, "text": text})
    return a


def analy_process(s):
    """M6/M7: Register-Auswertung mit Tauschempfehlung (wie AnalyProcess.log)."""
    z = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] AnalyProcess start (Lab-Simulation)"]
    for d in s["comp"]["dimm"]:
        if d["status"] == "ue":
            z += ["The first fault source is Memory.", f"Replace Memory device location: {d['name']}"]
    if any(e["sensor"] == "CPU_CATERR" for e in s["sel"]) and not any(d["status"] == "ue" for d in s["comp"]["dimm"]):
        z.append("The first fault source CPU is CPU0 (no memory fault found).")
    for i, g in enumerate(s["comp"]["gpu"]):
        if g["status"] == "offbus":
            z.append(f"PCIe root port {core.GPU_BUS[i]}: Surprise Down / Uncorrectable – device GPU{i} (BDF {core.GPU_BUS[i]}:00.0)")
    if len(z) == 1:
        z.append("No fault source found.")
    return "\n".join(z)


def onekey(s, st):
    """One-key Collection Log als tar.gz (Aufbau wie beim echten Onekey-Log, Inhalt simuliert)."""
    teile = {
        "log/selelist.csv": "ID,Date,Time,Sensor Type,Sensor,Event,Direction\n" + "\n".join(
            f"{e['id']},{core.ts(e['t']).replace(' | ', ',')},{e['typ']},{e['sensor']},{e['event']},{e['dir']}" for e in s["sel"]),
        "log/idl.log": "\n".join(f"{x['id']:>4} {x['zeit']} {x['sev']:<8} {x['typ']:<14} {x['code']} {x['desc']}" for x in idl(s)),
        "log/audit.log": "\n".join(f"{core.ts(a['t'])} {a['user']} {a['quelle']} {a['aktion']}" for a in s.get("audit", [])),
        "log/psuFaultHistory.log": "\n".join(f"PSU{i} SN {p['sn']} status {p['status']}" for i, p in enumerate(s["comp"]["psu"])),
        "component.log": "\n".join(f"{k} Version: {v}" for k, v in s["fw"].items()) + "\n\n" + "\n".join(
            f"{k.upper()}{i}: {d.get('name', '')} SN {d['sn']} status {d['status']}" for k, l in s["comp"].items() for i, d in enumerate(l)),
        "AnalyProcess.log": analy_process(s),
        "rundatainfo.log": "\n".join(f"{n:<18}| {w} {e} | {stt}" for n, w, e, stt in core.sensoren(s)),
        "sollog/solHostCaptured.log": "(Lab) Serial-over-LAN capture",
        "README.txt": "One-key Collection Log (Lab-Simulation). Wichtig: log/idl.log (Warning/Critical), log/audit.log\n"
                      "(wer hat aus/neu gestartet), component.log (Versionen), AnalyProcess.log (Tauschempfehlung),\n"
                      "rundatainfo.log (Sensoren).",
    }
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:gz") as tar:
        for name, inhalt in teile.items():
            daten = inhalt.encode()
            info = tarfile.TarInfo(f"onekeylog/{name}")
            info.size, info.mtime = len(daten), int(time.time())
            tar.addfile(info, io.BytesIO(daten))
    return puffer.getvalue()
