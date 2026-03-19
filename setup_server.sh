#!/bin/bash
# ================================================================
#  IoT Sensor Monitoring System — Server Setup Script
#  Run as: bash setup_server.sh
# ================================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
section() { echo -e "\n${GREEN}━━━  $1  ━━━${NC}"; }

section "IoT Sensor Monitor — Server Setup"

# ── 1. System packages ─────────────────────────────────────────
section "Installing system packages"
sudo apt-get update -q
sudo apt-get install -y mosquitto mosquitto-clients python3 python3-pip

# ── 2. Python packages ─────────────────────────────────────────
section "Installing Python packages"
pip3 install paho-mqtt flask flask-cors colorlog --quiet
info "Python packages installed"

# ── 3. Mosquitto config ────────────────────────────────────────
section "Configuring Mosquitto"
sudo cp mqtt_broker/mosquitto.conf /etc/mosquitto/conf.d/iot.conf

info "Creating MQTT users..."
sudo mosquitto_passwd -b -c /etc/mosquitto/passwd iot_device       "secure_pass_123"
sudo mosquitto_passwd -b    /etc/mosquitto/passwd iot_logger_server "secure_pass_123"

sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
info "Mosquitto started"

# ── 4. Create directories ──────────────────────────────────────
section "Creating data directories"
mkdir -p logs data
info "Directories: logs/  data/"

# ── 5. Systemd service for logger ─────────────────────────────
section "Installing systemd service"
WORK_DIR=$(pwd)

sudo tee /etc/systemd/system/iot-logger.service > /dev/null <<EOF
[Unit]
Description=IoT MQTT Data Logger
After=network.target mosquitto.service

[Service]
Type=simple
WorkingDirectory=${WORK_DIR}
ExecStart=/usr/bin/python3 ${WORK_DIR}/mqtt_logger.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/iot-dashboard.service > /dev/null <<EOF
[Unit]
Description=IoT Dashboard API
After=network.target iot-logger.service

[Service]
Type=simple
WorkingDirectory=${WORK_DIR}
ExecStart=/usr/bin/python3 ${WORK_DIR}/dashboard_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable iot-logger iot-dashboard
sudo systemctl start  iot-logger iot-dashboard
info "Services started"

# ── 6. Firewall ────────────────────────────────────────────────
section "Opening firewall ports"
if command -v ufw &>/dev/null; then
  sudo ufw allow 1883/tcp comment "MQTT"
  sudo ufw allow 5000/tcp comment "IoT Dashboard"
  sudo ufw allow 9001/tcp comment "MQTT WebSocket"
  info "UFW rules added"
else
  warn "ufw not found — open ports 1883, 5000, 9001 manually"
fi

# ── 7. Summary ─────────────────────────────────────────────────
section "Setup Complete"
echo ""
echo "  MQTT Broker  →  localhost:1883"
echo "  Dashboard    →  http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "  Service logs:"
echo "    journalctl -u iot-logger   -f"
echo "    journalctl -u iot-dashboard -f"
echo ""
echo "  Test publish (from another terminal):"
echo "    mosquitto_pub -h localhost -u iot_device -P secure_pass_123 \\"
echo "      -t iot/sensors/data -m '{\"device_id\":\"TEST\",\"environment\":{\"temperature\":25.5}}'"
