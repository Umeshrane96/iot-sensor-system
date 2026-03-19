#!/usr/bin/env python3
"""
================================================================
 IoT Sensor Monitoring System — MQTT Data Logger
 Linux Server Component
================================================================
 Subscribes to all sensor topics, validates payloads,
 persists to SQLite, writes structured logs, and fires
 console alerts on threshold breaches.

 Install deps:
   pip install paho-mqtt colorlog
 Run:
   python3 mqtt_logger.py
================================================================
"""

import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import colorlog  # pip install colorlog

# ─── Config ──────────────────────────────────────────────────
MQTT_BROKER   = "localhost"
MQTT_PORT     = 1883
MQTT_USER     = "iot_device"
MQTT_PASS     = "secure_pass_123"
CLIENT_ID     = "iot_logger_server"

SUBSCRIBE_TOPICS = [
    ("iot/sensors/data",   1),
    ("iot/sensors/status", 1),
    ("iot/sensors/alert",  2),
]

LOG_DIR   = Path("logs")
DB_PATH   = Path("data/sensors.db")
LOG_FILE  = LOG_DIR / f"sensor_{datetime.now().strftime('%Y%m%d')}.log"

# ─── Logging Setup ────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s [%(levelname)-8s]%(reset)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        "DEBUG":    "cyan",
        "INFO":     "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "bold_red",
    }
))
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))

log = logging.getLogger("IoTLogger")
log.setLevel(logging.DEBUG)
log.addHandler(handler)
log.addHandler(file_handler)

# ─── Database ─────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrency
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id     TEXT    NOT NULL,
            received_at   TEXT    NOT NULL,
            temperature   REAL,
            humidity      REAL,
            pressure_hpa  REAL,
            altitude_m    REAL,
            aqi_raw       INTEGER,
            aqi_status    TEXT,
            light_pct     INTEGER,
            light_status  TEXT,
            distance_cm   REAL,
            raw_payload   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            received_at TEXT    NOT NULL,
            alert_type  TEXT,
            value       REAL,
            message     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_status (
            device_id   TEXT PRIMARY KEY,
            status      TEXT,
            last_seen   TEXT
        )
    """)
    conn.commit()
    log.info("Database initialised → %s", DB_PATH)
    return conn

# ─── Payload Handlers ─────────────────────────────────────────
def handle_sensor_data(db: sqlite3.Connection, payload: dict) -> None:
    ts = datetime.utcnow().isoformat()
    dev = payload.get("device_id", "unknown")

    env   = payload.get("environment", {})
    air   = payload.get("air_quality", {})
    light = payload.get("light", {})
    prox  = payload.get("proximity", {})

    row = {
        "device_id":    dev,
        "received_at":  ts,
        "temperature":  env.get("temperature"),
        "humidity":     env.get("humidity"),
        "pressure_hpa": env.get("pressure_hpa"),
        "altitude_m":   env.get("altitude_m"),
        "aqi_raw":      air.get("raw"),
        "aqi_status":   air.get("status"),
        "light_pct":    light.get("percent"),
        "light_status": light.get("status"),
        "distance_cm":  prox.get("distance_cm"),
        "raw_payload":  json.dumps(payload),
    }

    db.execute("""
        INSERT INTO sensor_readings
        (device_id, received_at, temperature, humidity, pressure_hpa,
         altitude_m, aqi_raw, aqi_status, light_pct, light_status,
         distance_cm, raw_payload)
        VALUES
        (:device_id, :received_at, :temperature, :humidity, :pressure_hpa,
         :altitude_m, :aqi_raw, :aqi_status, :light_pct, :light_status,
         :distance_cm, :raw_payload)
    """, row)
    db.commit()

    log.info(
        "📡 [%s] Temp=%.1f°C  Hum=%.1f%%  Pres=%.1fhPa  "
        "AQI=%s(%s)  Light=%d%%(%s)  Dist=%.1fcm",
        dev,
        row["temperature"] or 0, row["humidity"] or 0,
        row["pressure_hpa"] or 0,
        row["aqi_raw"] or 0, row["aqi_status"] or "-",
        row["light_pct"] or 0, row["light_status"] or "-",
        row["distance_cm"] or 0,
    )


def handle_alert(db: sqlite3.Connection, payload: dict) -> None:
    ts  = datetime.utcnow().isoformat()
    dev = payload.get("device_id", "unknown")

    db.execute("""
        INSERT INTO alerts (device_id, received_at, alert_type, value, message)
        VALUES (?, ?, ?, ?, ?)
    """, (dev, ts,
          payload.get("type"),
          payload.get("value"),
          payload.get("message")))
    db.commit()

    log.warning(
        "🚨 ALERT [%s] type=%s  value=%s  msg=%s",
        dev, payload.get("type"), payload.get("value"), payload.get("message")
    )


def handle_status(db: sqlite3.Connection, payload: dict) -> None:
    ts     = datetime.utcnow().isoformat()
    dev    = payload.get("device_id", "unknown")
    status = payload.get("status", "UNKNOWN")

    db.execute("""
        INSERT INTO device_status (device_id, status, last_seen)
        VALUES (?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET status=excluded.status,
                                             last_seen=excluded.last_seen
    """, (dev, status, ts))
    db.commit()

    level = logging.INFO if status == "ONLINE" else logging.WARNING
    icon  = "🟢" if status == "ONLINE" else "🔴"
    log.log(level, "%s Device [%s] is now %s", icon, dev, status)

# ─── MQTT Callbacks ───────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    db = userdata["db"]
    codes = {
        0: "Connected ✓",
        1: "Bad protocol version",
        2: "Client ID rejected",
        3: "Broker unavailable",
        4: "Bad credentials",
        5: "Not authorised",
    }
    if rc == 0:
        log.info("MQTT broker connected → %s:%d", MQTT_BROKER, MQTT_PORT)
        for topic, qos in SUBSCRIBE_TOPICS:
            client.subscribe(topic, qos)
            log.info("  Subscribed → %s (QoS %d)", topic, qos)
    else:
        log.error("MQTT connect failed: %s (rc=%d)", codes.get(rc, "Unknown"), rc)


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning("Unexpected MQTT disconnect (rc=%d) — auto-reconnecting...", rc)


def on_message(client, userdata, msg):
    db = userdata["db"]
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError as e:
        log.error("JSON parse error on topic [%s]: %s", msg.topic, e)
        return

    topic = msg.topic
    if topic == "iot/sensors/data":
        handle_sensor_data(db, payload)
    elif topic == "iot/sensors/alert":
        handle_alert(db, payload)
    elif topic == "iot/sensors/status":
        handle_status(db, payload)
    else:
        log.debug("Unhandled topic: %s", topic)

# ─── Stats Printer ────────────────────────────────────────────
def print_stats(db: sqlite3.Connection) -> None:
    """Print a periodic stats summary to console."""
    row = db.execute("""
        SELECT COUNT(*) as total,
               AVG(temperature) as avg_temp,
               AVG(humidity) as avg_hum,
               AVG(aqi_raw) as avg_aqi
        FROM sensor_readings
        WHERE received_at >= datetime('now', '-1 hour')
    """).fetchone()

    alert_count = db.execute(
        "SELECT COUNT(*) FROM alerts WHERE received_at >= datetime('now', '-1 hour')"
    ).fetchone()[0]

    log.info(
        "── 1-hr Stats ── readings=%d  avg_temp=%.1f°C  avg_hum=%.1f%%  "
        "avg_aqi=%.0f  alerts=%d",
        row["total"] or 0,
        row["avg_temp"] or 0,
        row["avg_hum"] or 0,
        row["avg_aqi"] or 0,
        alert_count,
    )

# ─── Entry Point ─────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  IoT Sensor Monitoring System — Logger v1.0")
    log.info("=" * 60)

    db = init_db()

    client = mqtt.Client(client_id=CLIENT_ID, userdata={"db": db})
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    # Graceful shutdown
    def _shutdown(sig, frame):
        log.info("Shutting down...")
        client.disconnect()
        db.close()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except ConnectionRefusedError:
        log.critical("Cannot connect to MQTT broker at %s:%d — is Mosquitto running?",
                     MQTT_BROKER, MQTT_PORT)
        sys.exit(1)

    client.loop_start()

    stats_interval = 60  # seconds
    last_stats = time.time()

    log.info("Listening for sensor data... (Ctrl+C to stop)\n")
    while True:
        time.sleep(1)
        if time.time() - last_stats >= stats_interval:
            print_stats(db)
            last_stats = time.time()


if __name__ == "__main__":
    main()
