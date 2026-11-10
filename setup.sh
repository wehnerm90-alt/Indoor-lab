#!/usr/bin/env bash
# Richtet das Lab im Container ein (wird von install.sh aufgerufen, geht auch nach 'git pull').
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

chmod +x bin/* bin/inband/*
ln -sf "$DIR/bin/lab" /usr/local/bin/lab
ln -sf "$DIR/bin/lab" /usr/bin/lab
ln -sf "$DIR/bin/lab-bmc" /usr/local/bin/lab-bmc
ln -sf "$DIR/bin/ipmitool" /usr/local/bin/ipmitool
[ -e /usr/bin/ipmitool ] || ln -sf "$DIR/bin/ipmitool" /usr/bin/ipmitool
mkdir -p /var/lib/inspur-lab

# BMC-Namen auf den Container selbst zeigen lassen
for s in gpu01 gpu02 std01; do
  grep -q " bmc-$s\$" /etc/hosts || echo "127.0.0.1 bmc-$s" >> /etc/hosts
done

cat > /etc/systemd/system/inspur-lab.service <<EOF
[Unit]
Description=Inspur-Lab (simulierte BMCs + Ticket-Portal)
After=network-online.target

[Service]
Environment=LAB_HOME=$DIR
ExecStart=/usr/bin/python3 $DIR/bin/lab-bmc
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now inspur-lab.service >/dev/null 2>&1
systemctl restart inspur-lab.service

cat > /etc/motd <<'EOF'

  ___                                  _          _
 |_ _|_ __  ___ _ __  _   _ _ __      | |    __ _| |__
  | || '_ \/ __| '_ \| | | | '__|_____| |   / _` | '_ \
  | || | | \__ \ |_) | |_| | | |_____| |__| (_| | |_) |
 |___|_| |_|___/ .__/ \__,_|_|       |_____\__,_|_.__/
               |_|       Übungsumgebung – alles simuliert

  lab neu        neues Ticket          lab server     Server & BMC-Adressen
  lab ticket     Ticket anzeigen       lab login gpu01  auf einen Server
  lab pruefen    Zwischenstand         lab abschluss  Ticket abmelden
  lab hilfe      alle Befehle          lab update     Lab aktualisieren

EOF
echo "Inspur-Lab eingerichtet."
