# Inspur-Lab

Übungsumgebung für Inspur-Service-Tickets: drei simulierte Server mit BMC (Weboberfläche, Redfish, ipmitool),
Remote-KVM mit BIOS-Setup und AI PE, GPU-Werkzeugen (nvidia-smi, fieldiag, nvflash, AI-EasyCfg), RAID (storcli64) und Firmware-Updates. Es kommen zufällige Tickets rein,
du diagnostizierst, tauschst Teile, testest und meldest das Ticket mit einer Maintenance-Checkliste ab.

**Alles ist simuliert.** Es wird keine echte Firmware, kein echtes fieldiag und keine Hersteller-Software benutzt.
Modellnamen dienen nur der Orientierung; Seriennummern, Versionen, Kunden und Tickets sind frei erfunden.

## Installation (Proxmox)

In der Proxmox-Weboberfläche links auf den Server (`pve`) → **Shell**, dann:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/patricia181089-glitch/Indoor-lab/main/install.sh)"
```

Das Skript legt einen kleinen Debian-Container `inspur-lab` an (2 Kerne, 1 GB RAM, 4 GB Platte),
installiert das Lab und zeigt am Ende die Adressen. Nochmal ausführen = Lab aktualisieren.

In den Container: `pct enter <ID>` (ID steht am Ende der Installation) oder per SSH, falls ein Passwort gesetzt wurde.

## Die Server

| Name  | Modell   | Art                                   | BMC-Web / Redfish   | ipmitool -H |
|-------|----------|---------------------------------------|---------------------|-------------|
| gpu01 | NF5688M6 | HGX A100 8-GPU (SXM, 6 NVSwitch)      | `http://<IP>:8001`  | `bmc-gpu01` |
| gpu02 | NF5468M6 | 4x A30 PCIe                           | `http://<IP>:8002`  | `bmc-gpu02` |
| std01 | NF5280M7 | Standard mit RAID                     | `http://<IP>:8003`  | `bmc-std01` |

BMC-Login: `admin` / `admin`. Ticket-Portal und Firmware-Depot: `http://<IP>:8000`

## Ablauf eines Tickets (wie im Inspur-Ablauf)

Der Ablauf folgt den Anleitungen aus der TicketKompakt-App: **erst dokumentieren, dann eingreifen, am Ende alles
zurückstellen**. fieldiag gibt es *nicht* im Kunden-OS, sondern nur in der **AI PE** (USB-Stick).

1. **Ticket lesen:** `lab neu` (oder `lab neu gpu_remap_fail gpu01`), `lab ticket`. SN am Gerät = SN im Ticket?
   Sonst nichts tauschen, sondern `lab eskalieren <grund>`.
2. **Vorher sichern:** FW-Stände (BMC-Startseite / `ipmitool … mc info`), FRU (`ipmitool … fru print`),
   **Onekey-Log** (BMC → Fault Diagnosis → One-key Log). SEL erst löschen, wenn die Logs gesichert sind.
3. **Diagnose im Kunden-OS:** BMC → Remote Control → KVM, Login `root` + beliebiges Passwort
   (oder `lab login gpu01` – dieselbe Konsole im Terminal, trennen mit Strg+]). `nvidia-smi`, `lspci`, `dmesg`, `nvme list`, `storcli64`.
   ECC nach NVIDIA-Kriterium: **erst neu starten**, dann `nvidia-smi -q -d ROW_REMAPPER`.
   Nur wenn *Pending* oder *Remapping Failure* danach noch `Yes` ist → tauschen.
4. **AI PE booten:** `lab stick gpu01` (oder ein AI-ISO über Virtual Media), in der KVM *Reset*,
   beim POST **F11** (Button oben) → USB-Eintrag. Login `root` / `111111`.
5. **BIOS für fieldiag:** beim POST **DEL** → *Socket Configuration → VT-d = Disabled*,
   *Advanced → SR-IOV = Enabled*, **F10** speichern. Die Startwerte merken – sie müssen am Ende wieder rein!
6. **fieldiag vorbereiten** (in der AI PE – nach jedem Neustart neu, die PE läuft im RAM):
   ```bash
   mkdir /inspur && cp /media/629-24287-0004-FLD-38440.tgz /inspur/ && cd /inspur
   tar xfz 629-24287-0004-FLD-38440.tgz && cd 629-24287-0004-FLD-38440
   rmmod nvidia_peermem nvidia_drm nvidia_modeset nvidia_uvm   # Treiber entladen
   lsof /dev/nvidia* ; kill -9 <pid>                            # nvidia-persistenced beenden
   rmmod nvidia
   ipmitool raw 0x3c 0x4c 0x01 0x10 0x0                         # nur NF5688M6: BMC-Polling aus
   ./fieldiag.sh --no_bmc --level1                              # Fehler nachweisen
   ```
   (Alternativ AI-EasyCfg: `cd /iei/AI-EasyCfg; chmod +x ai-easyCfg; ./ai-easyCfg` → Menü 21/24/6.)
7. **Reparatur:** Server aus (`poweroff` oder BMC), **erst neu stecken** (`lab reseat gpu01 gpu 2`),
   wenn das nicht hilft **tauschen** (`lab tausch gpu01 gpu 2`). `lab karte gpu01` zeigt Slots und Bus-IDs.
8. **VBIOS angleichen** (nach GPU-Tausch, Treiber entladen): `cd /iei/nvflash`, `./nvflash.sh --version`,
   `./nvflash.sh -i 0 --save gut.rom`, `./nvflash.sh -i <neu> gut.rom` → `y`. Auf der NF5688M6 vorher
   den Schreibschutz in AI-EasyCfg Menü 29 aufheben. Neue VBIOS gilt erst nach einem Neustart.
9. **Test:** fieldiag erneut (Level 2) → **PASS**. Bei `gpu_remap_pending` reichen `--test gpumem` und
   `--test skucheck` plus `nvidia-smi -p 0` / `-p 1` (Zähler löschen).
10. **Zurückstellen:** Polling wieder an (`… 0x10 0x1`), BIOS auf die Startwerte, Stick ziehen
    (`lab stick gpu01 raus`), Boot ins Kunden-OS, **Onekey-Log nachher** sichern.
11. `lab pruefen` (Zwischenstand) → `lab abschluss` (Maintenance Process Checklist mit CASE-Nummer).

`lab tipp` gibt Hinweise (kostet Punkte), `lab checkliste` zeigt die Abnahme-Punkte.

## Die Konsole

Die Konsole (KVM und `lab login`) ist ein emuliertes Ubuntu: Bash 5.2 mit Pipes, Umleitungen, Variablen,
Schleifen, Funktionen, Tab-Vervollständigung, Verlauf (↑, Strg+R), Strg+C – und die üblichen Programme
(ls, cat, grep, sed, awk, find, tar, less, nano, vi, top, systemctl, journalctl, ip, ps, …) mit den
originalen Ausgaben, Fehlermeldungen und Exit-Codes. Es läuft dabei **nichts** auf dem echten Container:
Dateisystem, Prozesse und Programme existieren nur im Speicher. Was du im Kunden-OS anlegst, bleibt bis zum
nächsten Szenario; die AI PE vergisst alles beim Neustart (wie der echte Live-Stick).

## Szenarien

| Szenario            | Server       | Worum es geht |
|---------------------|--------------|---------------|
| `gpu_offbus`        | gpu01, gpu02 | GPU vom Bus gefallen (`rev ff`): Kontakt oder defekt – erst neu stecken |
| `gpu_remap_fail`    | gpu01, gpu02 | Row-Remapping-Failure: Neustart, Kriterium prüfen, tauschen, VBIOS angleichen, fieldiag |
| `gpu_remap_pending` | gpu01, gpu02 | Kunde will Tausch – nach Neustart ist *Pending* aber `No`: **kein Tausch**, Zähler löschen |
| `nvswitch`          | gpu01        | NVSwitch fehlt / Fabric Manager startet nicht: Baseboard tauschen |
| `dimm_ue`           | alle         | Uncorrectable ECC, IERR: DIMM-Position aus IDL/Onekey, erst neu stecken |
| `psu_fail`          | alle         | Netzteil: AC lost (Kabel) oder Failure (defekt) |
| `fan_fail`          | alle         | Lüfter steht (hot-swap) |
| `nvme_fail`         | gpu01, gpu02 | NVMe verschwunden (hot-swap) |
| `raid_disk`         | std01        | RAID degraded, Platte tauschen, Rebuild abwarten |
| `sn_mismatch`       | std01        | Slot/SN im Ticket passen nicht zur defekten Platte → **eskalieren**, nichts tauschen |
| `fw_update`         | alle         | BMC + BIOS per HPM auf die **freigegebenen** Stände (neuere im Depot sind eine Falle) |

## Punkte

Jedes Ticket startet mit 100 Punkten. Pflicht: Fehler behoben, Kunden-OS bootet, bei GPU-Tickets fieldiag-PASS
nach der Reparatur. Abzug u. a. für: FW/FRU/Onekey nicht vorher gesichert, SEL ohne Log gelöscht, getauscht
ohne neu zu stecken, Tausch ohne Neustart/Kriterium, ungleiche VBIOS, falsche Teile, BIOS/Polling nicht
zurückgestellt, Stick vergessen, Bootreihenfolge dauerhaft verstellt, nicht freigegebene FW, kaputte Images, Tipps.
`lab verlauf` zeigt deine Ergebnisse.

## Redfish-Beispiele

```bash
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/Systems/1 | jq
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/Managers/1/LogServices/SEL/Entries | jq
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/UpdateService/FirmwareInventory/BMC | jq
cd /var/lib/inspur-lab/firmware && sha256sum -c SHA256SUMS
curl -u admin:admin -F UpdateFile=@NF5688M6_BMC_4.32.1.hpm http://bmc-gpu01:8001/redfish/v1/UpdateService/upload
curl -u admin:admin -X POST -H 'Content-Type: application/json' \
     -d '{"ResetType":"ForceOff"}' http://bmc-gpu01:8001/redfish/v1/Systems/1/Actions/ComputerSystem.Reset
```

## Sonstiges

- `lab update` holt die neueste Version, `lab reset --ja` setzt alles zurück.
- Entfernen: in der Proxmox-Shell `pct stop <ID> && pct destroy <ID>`.
