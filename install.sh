#!/usr/bin/env bash
# Inspur-Lab: legt auf einem Proxmox-Host einen LXC-Container an und installiert das Lab.
# Aufruf in der Proxmox-Shell (als root):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/patricia181089-glitch/Indoor-lab/main/install.sh)"
set -euo pipefail

REPO="${REPO:-https://github.com/patricia181089-glitch/Indoor-lab.git}"
HOSTNAME_CT="inspur-lab"
gruen() { printf '\033[32m%s\033[0m\n' "$*"; }
gelb()  { printf '\033[33m%s\033[0m\n' "$*"; }
rot()   { printf '\033[31m%s\033[0m\n' "$*"; }

command -v pct >/dev/null || { rot "Das muss in der Proxmox-Shell laufen (pct nicht gefunden)."; exit 1; }
[ "$(id -u)" = 0 ] || { rot "Bitte als root ausführen."; exit 1; }

if pct list | awk 'NR>1{print $3}' | grep -qx "$HOSTNAME_CT"; then
  CTID=$(pct list | awk -v h="$HOSTNAME_CT" 'NR>1 && $3==h {print $1; exit}')
  gelb "Es gibt schon einen Container '$HOSTNAME_CT' (ID $CTID). Ich aktualisiere nur das Lab darin."
  pct start "$CTID" 2>/dev/null || true
  if ! pct exec "$CTID" -- test -d /opt/inspur-lab 2>/dev/null; then
    rot "Container $CTID ist unvollständig (frühere Installation abgebrochen)."
    rot "Erst löschen:  pct stop $CTID; pct destroy $CTID   – dann dieses Skript nochmal starten."
    exit 1
  fi
  pct exec "$CTID" -- bash -c "cd /opt/inspur-lab && git pull -q && bash setup.sh"
  IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
  gruen "Fertig. Portal: http://$IP:8000"
  exit 0
fi

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
ROOTFS_STORE="${ROOTFS_STORE:-$(pvesm status --content rootdir | awk 'NR>1 && $3=="active"{print $1; exit}')}"
TMPL_STORE="${TMPL_STORE:-$(pvesm status --content vztmpl | awk 'NR>1 && $3=="active"{print $1; exit}')}"
[ -n "$ROOTFS_STORE" ] && [ -n "$TMPL_STORE" ] || { rot "Kein passender Speicher gefunden (rootdir/vztmpl)."; exit 1; }

gruen "1/5  Container-Vorlage suchen …"
pveam update >/dev/null
ARCH=$(dpkg --print-architecture)   # amd64 auf Intel/AMD
TMPL=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-13-standard_.*_${ARCH}\." | sort -V | tail -1)
[ -n "$TMPL" ] || TMPL=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-12-standard_.*_${ARCH}\." | sort -V | tail -1)
[ -n "$TMPL" ] || { rot "Keine Debian-Vorlage gefunden."; exit 1; }
if ! pveam list "$TMPL_STORE" | grep -q "$TMPL"; then
  gruen "     lade $TMPL …"
  pveam download "$TMPL_STORE" "$TMPL" >/dev/null
fi

PW=""
if [ -t 0 ] || [ -e /dev/tty ]; then
  echo
  echo "Root-Passwort für den Lab-Container festlegen (für Konsole und SSH)."
  echo "Leer lassen = kein Passwort, dann nur über 'pct enter $CTID' erreichbar."
  read -rsp "Passwort: " PW </dev/tty || PW=""
  echo
fi

gruen "2/5  Container $CTID ($HOSTNAME_CT) anlegen …"
pct create "$CTID" "$TMPL_STORE:vztmpl/$TMPL" \
  --hostname "$HOSTNAME_CT" --cores 2 --memory 1024 --swap 512 \
  --rootfs "$ROOTFS_STORE:4" --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --features nesting=1 --onboot 1 \
  --description "Inspur-Lab: Übungsumgebung für Service-Tickets (simuliert)" >/dev/null
pct start "$CTID"

gruen "3/5  Warte auf Netzwerk …"
for i in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then break; fi
  sleep 2
  [ "$i" = 60 ] && { rot "Container bekommt kein Netzwerk (DHCP?)."; exit 1; }
done

gruen "4/5  Pakete installieren und Lab herunterladen …"
pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 git curl jq ca-certificates openssh-server >/dev/null && git clone -q '$REPO' /opt/inspur-lab && bash /opt/inspur-lab/setup.sh"

if [ -n "$PW" ]; then
  printf 'root:%s\n' "$PW" | pct exec "$CTID" -- chpasswd
  pct exec "$CTID" -- bash -c "sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && systemctl restart ssh"
fi

gruen "5/5  Fertig!"
IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
cat <<EOF

  Inspur-Lab läuft im Container $CTID ($HOSTNAME_CT), IP $IP

  Ticket-Portal:   http://$IP:8000
  BMC gpu01:       http://$IP:8001   (admin / admin)
  BMC gpu02:       http://$IP:8002
  BMC std01:       http://$IP:8003

  In den Container:  pct enter $CTID$( [ -n "$PW" ] && printf '\n                     oder: ssh root@%s' "$IP")
  Dann:              lab neu       (erstes Ticket)
                     lab hilfe     (alle Befehle)

  Tipp: In der FritzBox für '$HOSTNAME_CT' immer dieselbe IP vergeben.
EOF
