# Inspur-Lab

Übungsumgebung für Inspur-Service-Tickets: drei simulierte Server mit BMC (Weboberfläche, Redfish, ipmitool),
GPU-Werkzeugen (nvidia-smi, fieldiag), RAID (storcli64) und Firmware-Updates. Es kommen zufällige Tickets rein,
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

| Name  | Modell   | Art                       | BMC-Web / Redfish   | ipmitool -H |
|-------|----------|---------------------------|---------------------|-------------|
| gpu01 | NF5688M7 | 8-GPU HGX (SXM, NVSwitch) | `http://<IP>:8001`  | `bmc-gpu01` |
| gpu02 | NF5468M7 | 4-GPU PCIe                | `http://<IP>:8002`  | `bmc-gpu02` |
| std01 | NF5280M7 | Standard mit RAID         | `http://<IP>:8003`  | `bmc-std01` |

BMC-Login: `admin` / `admin`. Ticket-Portal und Firmware-Depot: `http://<IP>:8000`

## Ablauf eines Tickets

```bash
lab neu                       # zufälliges Ticket (oder: lab neu gpu_offbus gpu01)
lab ticket                    # Ticket nochmal lesen
ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel elist     # SEL lesen
ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sdr elist     # Sensoren
lab login gpu01               # auf den Server: nvidia-smi, fieldiag, dmesg, lspci, nvme list, storcli64
lab karte gpu01               # Service-Karte: welcher Slot ist welche Bus-ID / welcher DIMM
ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin chassis power off
lab tausch gpu01 gpu 3        # Teil physisch tauschen
ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin chassis power on
lab login gpu01 → fieldiag --level2
ipmitool -I lanplus -H bmc-gpu01 -U admin -P admin sel clear
lab pruefen                   # Zwischenstand
lab abschluss                 # Abmelden + Maintenance Process Checklist (mit CASE-Nummer)
```

## Szenarien

| Szenario            | Server       | Worum es geht |
|---------------------|--------------|---------------|
| `gpu_offbus`        | gpu01, gpu02 | GPU „fallen off the bus“ (Xid 79): fehlende Bus-ID dem Slot zuordnen |
| `gpu_remap_fail`    | gpu01, gpu02 | Row-Remapping-Failure: GPU tauschen |
| `gpu_remap_pending` | gpu01        | Remapping pending: **kein Tausch**, GPU-Reset reicht (Ersatzteil bleibt unbenutzt) |
| `nvswitch`          | gpu01        | NVLink-Links down, Baseboard tauschen |
| `dimm_ue`           | alle         | Uncorrectable ECC im SEL, DIMM-Slot finden |
| `psu_fail`          | alle         | Netzteil defekt (hot-swap) |
| `fan_fail`          | alle         | Lüfter steht (hot-swap) |
| `nvme_fail`         | gpu01, gpu02 | NVMe verschwunden (hot-swap) |
| `raid_disk`         | std01        | RAID degraded, Platte tauschen, Rebuild abwarten |
| `fw_update`         | alle         | BMC + BIOS aktualisieren: Reihenfolge, Prüfsummen, falsches Modell, kaputtes Image |

## Punkte

Jedes Ticket startet mit 100 Punkten. Abzug gibt es z. B. für falsch getauschte Teile, unnötige Tausche,
fehlendes fieldiag nach der Reparatur, nicht gelöschtes (oder zu früh gelöschtes) SEL, kaputte Images und Tipps.
`lab verlauf` zeigt deine Ergebnisse.

## Redfish-Beispiele

```bash
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/Systems/1 | jq
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/Managers/1/LogServices/SEL/Entries | jq
curl -u admin:admin http://bmc-gpu01:8001/redfish/v1/UpdateService/FirmwareInventory/BMC | jq
cd /var/lib/inspur-lab/firmware && sha256sum -c SHA256SUMS
curl -u admin:admin -F UpdateFile=@NF5688M7_BMC_4.32.1.bin http://bmc-gpu01:8001/redfish/v1/UpdateService/upload
curl -u admin:admin -X POST -H 'Content-Type: application/json' \
     -d '{"ResetType":"ForceOff"}' http://bmc-gpu01:8001/redfish/v1/Systems/1/Actions/ComputerSystem.Reset
```

## Sonstiges

- `lab update` holt die neueste Version, `lab reset --ja` setzt alles zurück.
- Entfernen: in der Proxmox-Shell `pct stop <ID> && pct destroy <ID>`.
