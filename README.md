# 🌐 IoT Sensor Monitoring System

A production-grade end-to-end IoT pipeline — **ESP32 firmware → MQTT → Linux server → SQLite → REST API + Live Dashboard**.

```
┌─────────────────┐      MQTT (QoS 1/2)      ┌─────────────────────────────────────┐
│   ESP32 Node    │ ─────────────────────────▶│          Linux Server               │
│                 │                           │                                     │
│  DHT22          │   Topics:                 │  Mosquitto Broker  :1883            │
│  MQ-135         │   iot/sensors/data        │  mqtt_logger.py  ──▶ SQLite DB      │
│  BMP280         │   iot/sensors/status      │  dashboard_api.py  ──▶ :5000        │
│  HC-SR04        │   iot/sensors/alert       │                                     │
│  LDR            │◀─ iot/sensors/cmd ───────│  REST API + SSE Live Feed           │
└─────────────────┘                           └─────────────────────────────────────┘
```

---

## 📦 Sensors & Why They Were Chosen

| Sensor  | Measures             | Protocol | Skill Demonstrated          |
|---------|----------------------|----------|-----------------------------|
| DHT22   | Temperature, Humidity| 1-Wire   | Digital sensor reading      |
| MQ-135  | Air Quality / CO₂    | Analog   | ADC, voltage mapping, thresholds |
| BMP280  | Pressure, Altitude   | I2C      | I2C bus, multi-register read|
| HC-SR04 | Distance             | GPIO PWM | Pulse timing, `pulseIn()`   |
| LDR     | Ambient Light        | Analog   | Voltage divider, ADC mapping|

---

## 🗂️ Project Structure

```
iot-sensor-system/
├── esp32/
│   └── main.ino              # ESP32 firmware (Arduino)
├── server/
│   ├── mqtt_logger.py        # MQTT subscriber + SQLite logger
│   ├── dashboard_api.py      # Flask REST API + SSE + Web UI
│   └── mqtt_broker/
│       └── mosquitto.conf    # Broker config with auth + persistence
├── setup_server.sh           # One-shot Linux server setup
└── README.md
```

---

## 🚀 Quick Start

### 1 — Linux Server

```bash
cd server
bash ../setup_server.sh          # installs Mosquitto, Python deps, systemd services
```

Or manually:
```bash
pip3 install paho-mqtt flask flask-cors colorlog
python3 mqtt_logger.py &         # terminal 1 — MQTT listener
python3 dashboard_api.py &       # terminal 2 — web dashboard
```

### 2 — ESP32 Firmware

**Arduino libraries required** (install via Library Manager):
- `PubSubClient` by Nick O'Leary
- `DHT sensor library` by Adafruit
- `Adafruit BMP280 Library`
- `ArduinoJson` by Benoit Blanchon

**Steps:**
1. Open `esp32/main.ino` in Arduino IDE
2. Set `WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_BROKER` (your server IP)
3. Flash to ESP32 DevKit v1
4. Open Serial Monitor at 115200 baud

---

## 📡 MQTT Topics

| Topic                  | Direction      | QoS | Description                     |
|------------------------|----------------|-----|---------------------------------|
| `iot/sensors/data`     | ESP32 → Server | 1   | Full sensor reading (JSON)      |
| `iot/sensors/status`   | ESP32 → Server | 1   | ONLINE / OFFLINE (LWT)          |
| `iot/sensors/alert`    | ESP32 → Server | 2   | Threshold breach notification   |
| `iot/sensors/cmd`      | Server → ESP32 | 1   | Remote commands (reboot, etc.)  |

### Example Payload — `iot/sensors/data`
```json
{
  "device_id": "ESP32_NODE_01",
  "timestamp": 123456789,
  "environment": {
    "temperature": 27.4,
    "humidity": 62.1,
    "pressure_hpa": 1013.2,
    "altitude_m": 12.5
  },
  "air_quality": {
    "raw": 320,
    "volt": 0.26,
    "status": "MODERATE"
  },
  "light": {
    "percent": 65,
    "status": "NORMAL"
  },
  "proximity": {
    "distance_cm": 45.2
  }
}
```

---

## 🌐 REST API Reference

| Endpoint            | Method | Description                       |
|---------------------|--------|-----------------------------------|
| `/`                 | GET    | Live web dashboard                |
| `/api/latest`       | GET    | Latest reading per device         |
| `/api/history`      | GET    | Historical data (`?hours=1&limit=100`) |
| `/api/alerts`       | GET    | Recent alerts (`?limit=20`)       |
| `/api/devices`      | GET    | Device status list                |
| `/api/stats`        | GET    | 1-hour aggregated statistics      |
| `/api/stream`       | GET    | Server-Sent Events live feed      |

---

## 🔌 Wiring Diagram

```
ESP32 DevKit v1
────────────────────────────────────────────────────
DHT22        DATA  ──────────────────── GPIO 4
             VCC   ──────────────────── 3.3V
             GND   ──────────────────── GND

MQ-135       AOUT  ──────────────────── GPIO 34 (ADC)
             VCC   ──────────────────── 5V
             GND   ──────────────────── GND

BMP280       SDA   ──────────────────── GPIO 21
             SCL   ──────────────────── GPIO 22
             VCC   ──────────────────── 3.3V
             GND   ──────────────────── GND

HC-SR04      TRIG  ──────────────────── GPIO 5
             ECHO  ── 1kΩ/2kΩ divider ── GPIO 18  (3.3V safe!)
             VCC   ──────────────────── 5V
             GND   ──────────────────── GND

LDR (10kΩ)   OUT   ──────────────────── GPIO 35 (ADC)
             (voltage divider with 10kΩ pull-down to GND)
```

> ⚠️ **HC-SR04 Echo Pin** outputs 5V — use a **voltage divider** (1kΩ + 2kΩ) to bring it to ~3.3V before connecting to ESP32.

---

## ⚡ Key Features

- **Last Will & Testament (LWT)** — server instantly knows when a device goes offline
- **MQTT QoS 2 alerts** — guaranteed delivery for critical threshold events
- **Retained messages** — new subscribers immediately get the latest status
- **Remote commands** — send `{"action":"reboot"}` to restart the device over MQTT
- **SSE live stream** — browser dashboard updates in real time without polling
- **SQLite WAL mode** — concurrent reads while logger is writing
- **Systemd services** — auto-restart on crash, starts on boot

---

## 🛡️ Security Notes

- Change `MQTT_PASS` in both firmware and server before deployment
- For production, enable TLS on Mosquitto (port 8883) and use certificates
- Restrict UFW to known ESP32 IP ranges if possible

---


