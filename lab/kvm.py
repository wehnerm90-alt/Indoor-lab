"""Remote Console (KVM): was der Bildschirm des Servers gerade zeigt.

Phasen: off -> post -> (setup) -> (bootmenu) -> os (Kunden-OS oder AI PE) bzw. noboot.
"""
from . import core


def bildschirm(s):
    ph = s.get("phase", "os") if s["power"] == "on" else "off"
    if ph == "off":
        return {"mode": "off"}
    if ph == "post":
        seit = core.now() - s.get("power_since", 0)
        return {"mode": "post", "lines": post_zeilen(s, seit), "pct": min(100, int(seit / core.POST_DAUER * 100)),
                "setup": s.get("setup_req"), "bootmenu": s.get("bootmenu_req")}
    if ph == "setup":
        return {"mode": "setup", "bios": s["bios"], "info": {
            "modell": s["modell"], "bios": s["fw"]["BIOS"], "bmc": s["fw"]["BMC"], "me": s["fw"].get("ME", ""),
            "ram": sum(1 for d in s["comp"]["dimm"] if d["status"] == "ok") * 64,
            "bmc_ip": s.get("settings", {}).get("network", {}).get("ip", "")}}
    if ph == "bootmenu":
        return {"mode": "bootmenu", "ziele": core.boot_ziele(s)}
    if ph == "noboot":
        return {"mode": "noboot"}
    return {"mode": "os", "hostname": "localhost" if s.get("os") == "pe" else s["name"], "os": s.get("os", "prod"),
            "boot_id": s.get("boot_id")}


def post_zeilen(s, seit):
    m = core.MODELS[s["modell"]]
    dimms = s["comp"]["dimm"]
    ok = [d for d in dimms if d["status"] == "ok"]
    z = [f"Inspur {s['modell']}   BIOS Version {s['fw']['BIOS']}   BMC Version {s['fw']['BMC']}",
         "Copyright (C) 2024 American Megatrends International, LLC. (Lab-Simulation)", "",
         "CPU0: Intel(R) Xeon(R) Platinum 8358 CPU @ 2.60GHz   CPU1: Intel(R) Xeon(R) Platinum 8358 CPU @ 2.60GHz",
         f"Boot Mode: {s['bios']['bootmode']}   BMC IP: {s.get('settings', {}).get('network', {}).get('ip', '-')}", ""]
    d = core.POST_DAUER
    if seit > d * 0.15:
        z.append(f"Memory: {len(ok) * 64} GB DDR4 detected ({len(ok)}/{len(dimms)} DIMMs)")
        for x in dimms:
            if x["status"] != "ok":
                z.append(f"  WARNING: {x['name']} Memory Device Disabled (uncorrectable ECC error logged)")
    if seit > d * 0.35:
        for i, f in enumerate(s["comp"]["fan"]):
            if f["status"] != "ok":
                z.append(f"  WARNING: FAN{i} failure detected – check fan module")
        for i, p in enumerate(s["comp"]["psu"]):
            if p["status"] != "ok":
                z.append(f"  WARNING: PSU{i} failure – power redundancy lost")
        if m["gpus"]:
            n = sum(1 for g in s["comp"]["gpu"] if g["status"] != "offbus")
            z.append(f"PCIe: {n} x {m['gpu_name']} enumerated" + (f"  (expected {m['gpus']})" if n != m["gpus"] else ""))
        if m["hdd"]:
            bad = [i for i, x in enumerate(s["comp"]["hdd"]) if x["status"] == "fail"]
            z.append("MegaRAID 9560-8i: VD 0 RAID6 " + ("DEGRADED" if bad else "Optimal"))
    z += ["", "Press <DEL> or <ESC> to enter Setup, <F11> for Boot Menu, <F12> for Network Boot, <TAB> POST info"]
    if s.get("setup_req"):
        z += ["", "Entering Setup ..."]
    elif s.get("bootmenu_req"):
        z += ["", "Boot Menu selected ..."]
    return z


def taste(st, s, key):
    if s.get("phase") != "post":
        return False
    if key in ("DEL", "ESC", "F2"):
        s["setup_req"] = True
    elif key == "F11":
        s["bootmenu_req"] = True
    elif key == "F12":
        s["setup_req"] = False
    return True
