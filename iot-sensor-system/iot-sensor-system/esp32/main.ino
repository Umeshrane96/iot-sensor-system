/**
 * ============================================================
 *  IoT Sensor Monitoring System — ESP32 Firmware
 *  Author : Your Name
 *  Board  : ESP32 DevKit v1
 *  Sensors: DHT22 | MQ-135 | BMP280 | HC-SR04 | LDR
 * ============================================================
 *
 *  Wiring:
 *  DHT22   DATA  → GPIO 4
 *  MQ-135  AOUT  → GPIO 34 (ADC1_CH6)
 *  BMP280  SDA   → GPIO 21 | SCL → GPIO 22
 *  HC-SR04 TRIG  → GPIO 5  | ECHO → GPIO 18
 *  LDR     AOUT  → GPIO 35 (ADC1_CH7)  [voltage divider with 10kΩ]
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_BMP280.h>
#include <ArduinoJson.h>

// ─── WiFi & MQTT Config ───────────────────────────────────────
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_PASSWORD"
#define MQTT_BROKER     "192.168.1.100"   // Linux server IP
#define MQTT_PORT       1883
#define MQTT_USER       "iot_device"
#define MQTT_PASS       "secure_pass_123"
#define DEVICE_ID       "ESP32_NODE_01"

// ─── MQTT Topics ──────────────────────────────────────────────
#define TOPIC_SENSORS   "iot/sensors/data"
#define TOPIC_STATUS    "iot/sensors/status"
#define TOPIC_ALERT     "iot/sensors/alert"
#define TOPIC_CMD       "iot/sensors/cmd"   // subscribe — receive commands

// ─── Pin Definitions ─────────────────────────────────────────
#define DHT_PIN         4
#define DHT_TYPE        DHT22
#define MQ135_PIN       34
#define LDR_PIN         35
#define TRIG_PIN        5
#define ECHO_PIN        18
#define ONBOARD_LED     2

// ─── Thresholds for Alerts ────────────────────────────────────
#define TEMP_HIGH       35.0   // °C
#define HUMIDITY_HIGH   80.0   // %
#define AQI_HIGH        700    // raw ADC — tune after calibration
#define DIST_LOW        10.0   // cm  (object too close)

// ─── Timing ──────────────────────────────────────────────────
#define PUBLISH_INTERVAL   5000   // ms between normal publishes
#define RECONNECT_DELAY    3000   // ms between reconnect attempts
#define SENSOR_WARMUP      2000   // ms for MQ-135 warm-up

// ─── Objects ─────────────────────────────────────────────────
DHT            dht(DHT_PIN, DHT_TYPE);
Adafruit_BMP280 bmp;
WiFiClient     wifiClient;
PubSubClient   mqtt(wifiClient);

// ─── State ───────────────────────────────────────────────────
unsigned long lastPublish   = 0;
unsigned long lastReconnect = 0;
bool          bmpOK         = false;

// ─────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  pinMode(ONBOARD_LED, OUTPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  Serial.println("\n========================================");
  Serial.println("  IoT Sensor Monitor — ESP32 Firmware");
  Serial.println("========================================");

  // Init sensors
  dht.begin();
  bmpOK = bmp.begin(0x76);
  if (!bmpOK) {
    Serial.println("[WARN] BMP280 not found at 0x76 — trying 0x77");
    bmpOK = bmp.begin(0x77);
  }
  if (bmpOK) {
    bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                    Adafruit_BMP280::SAMPLING_X2,
                    Adafruit_BMP280::SAMPLING_X16,
                    Adafruit_BMP280::FILTER_X16,
                    Adafruit_BMP280::STANDBY_MS_500);
    Serial.println("[OK] BMP280 initialised");
  } else {
    Serial.println("[ERROR] BMP280 not found — pressure data disabled");
  }

  Serial.printf("[INFO] MQ-135 warming up (%d ms)...\n", SENSOR_WARMUP);
  delay(SENSOR_WARMUP);

  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(512);
  connectMQTT();

  Serial.println("[READY] Starting sensor loop\n");
}

// ─────────────────────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────────────────────
void loop() {
  if (!mqtt.connected()) {
    unsigned long now = millis();
    if (now - lastReconnect > RECONNECT_DELAY) {
      lastReconnect = now;
      connectMQTT();
    }
  }
  mqtt.loop();

  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL) {
    lastPublish = now;
    readAndPublish();
  }
}

// ─────────────────────────────────────────────────────────────
//  READ SENSORS & PUBLISH
// ─────────────────────────────────────────────────────────────
void readAndPublish() {
  // — DHT22 ——————————————————————————————————
  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("[WARN] DHT22 read failed — skipping cycle");
    return;
  }

  // — BMP280 ——————————————————————————————————
  float pressure = bmpOK ? bmp.readPressure() / 100.0F : 0.0;
  float altitude = bmpOK ? bmp.readAltitude(1013.25)   : 0.0;

  // — MQ-135 (Air Quality) ————————————————————
  int aqiRaw    = analogRead(MQ135_PIN);
  float aqiVolt = aqiRaw * (3.3 / 4095.0);
  String aqiLabel = classifyAQI(aqiRaw);

  // — LDR (Light) —————————————————————————————
  int ldrRaw     = analogRead(LDR_PIN);
  int lightPct   = map(ldrRaw, 0, 4095, 100, 0);  // invert: higher raw = darker
  String lightLabel = classifyLight(lightPct);

  // — HC-SR04 (Distance) ——————————————————————
  float distance = measureDistance();

  // — Build JSON payload ——————————————————————
  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["timestamp"]    = millis();

  JsonObject env = doc.createNestedObject("environment");
  env["temperature"]  = round(temperature * 10) / 10.0;
  env["humidity"]     = round(humidity * 10) / 10.0;
  env["pressure_hpa"] = round(pressure * 10) / 10.0;
  env["altitude_m"]   = round(altitude * 10) / 10.0;

  JsonObject air = doc.createNestedObject("air_quality");
  air["raw"]    = aqiRaw;
  air["volt"]   = round(aqiVolt * 100) / 100.0;
  air["status"] = aqiLabel;

  JsonObject light = doc.createNestedObject("light");
  light["percent"] = lightPct;
  light["status"]  = lightLabel;

  JsonObject dist = doc.createNestedObject("proximity");
  dist["distance_cm"] = round(distance * 10) / 10.0;

  // — Publish data —————————————————————————————
  char payload[512];
  serializeJson(doc, payload);
  bool ok = mqtt.publish(TOPIC_SENSORS, payload, true);  // retained

  Serial.printf("[PUB] %s → %s\n", TOPIC_SENSORS, ok ? "OK" : "FAIL");
  Serial.printf("  Temp=%.1f°C  Hum=%.1f%%  Pres=%.1fhPa  Alt=%.1fm\n",
                temperature, humidity, pressure, altitude);
  Serial.printf("  AQI=%d(%s)  Light=%d%%(%s)  Dist=%.1fcm\n\n",
                aqiRaw, aqiLabel.c_str(), lightPct, lightLabel.c_str(), distance);

  // LED heartbeat
  digitalWrite(ONBOARD_LED, HIGH); delay(50); digitalWrite(ONBOARD_LED, LOW);

  // — Check thresholds & fire alerts ————————————
  checkAndAlert(temperature, humidity, aqiRaw, distance);
}

// ─────────────────────────────────────────────────────────────
//  ALERT LOGIC
// ─────────────────────────────────────────────────────────────
void checkAndAlert(float temp, float hum, int aqi, float dist) {
  StaticJsonDocument<256> alert;
  alert["device_id"] = DEVICE_ID;
  bool triggered = false;

  if (temp > TEMP_HIGH) {
    alert["type"]    = "HIGH_TEMPERATURE";
    alert["value"]   = temp;
    alert["message"] = "Temperature exceeded safe threshold";
    triggered = true;
  } else if (hum > HUMIDITY_HIGH) {
    alert["type"]    = "HIGH_HUMIDITY";
    alert["value"]   = hum;
    alert["message"] = "Humidity exceeded safe threshold";
    triggered = true;
  } else if (aqi > AQI_HIGH) {
    alert["type"]    = "POOR_AIR_QUALITY";
    alert["value"]   = aqi;
    alert["message"] = "Air quality degraded — check ventilation";
    triggered = true;
  } else if (dist < DIST_LOW && dist > 0) {
    alert["type"]    = "PROXIMITY_ALERT";
    alert["value"]   = dist;
    alert["message"] = "Object detected too close";
    triggered = true;
  }

  if (triggered) {
    char buf[256];
    serializeJson(alert, buf);
    mqtt.publish(TOPIC_ALERT, buf);
    Serial.printf("[ALERT] %s\n", buf);
    // Blink LED 3× for visual alert
    for (int i = 0; i < 3; i++) {
      digitalWrite(ONBOARD_LED, HIGH); delay(150);
      digitalWrite(ONBOARD_LED, LOW);  delay(150);
    }
  }
}

// ─────────────────────────────────────────────────────────────
//  HC-SR04 — ULTRASONIC DISTANCE
// ─────────────────────────────────────────────────────────────
float measureDistance() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);  // 30 ms timeout
  if (duration == 0) return -1.0;                  // no echo / out of range
  return duration * 0.034 / 2.0;
}

// ─────────────────────────────────────────────────────────────
//  CLASSIFIERS
// ─────────────────────────────────────────────────────────────
String classifyAQI(int raw) {
  if (raw < 300)       return "GOOD";
  else if (raw < 500)  return "MODERATE";
  else if (raw < 700)  return "UNHEALTHY";
  else                 return "HAZARDOUS";
}

String classifyLight(int pct) {
  if (pct < 15)        return "DARK";
  else if (pct < 40)   return "DIM";
  else if (pct < 70)   return "NORMAL";
  else                 return "BRIGHT";
}

// ─────────────────────────────────────────────────────────────
//  MQTT INCOMING COMMAND HANDLER
// ─────────────────────────────────────────────────────────────
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.printf("[CMD] Topic: %s  Msg: %s\n", topic, msg.c_str());

  StaticJsonDocument<128> cmd;
  DeserializationError err = deserializeJson(cmd, msg);
  if (err) { Serial.println("[WARN] Invalid JSON command"); return; }

  const char* action = cmd["action"];
  if (strcmp(action, "reboot") == 0) {
    Serial.println("[CMD] Rebooting ESP32...");
    delay(500);
    ESP.restart();
  } else if (strcmp(action, "set_interval") == 0) {
    // Dynamic interval update — extend as needed
    Serial.printf("[CMD] Interval change requested: %d ms\n", (int)cmd["value"]);
  }
}

// ─────────────────────────────────────────────────────────────
//  WiFi
// ─────────────────────────────────────────────────────────────
void connectWiFi() {
  Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500); Serial.print("."); attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected — IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[ERROR] WiFi connection failed — check credentials");
  }
}

// ─────────────────────────────────────────────────────────────
//  MQTT
// ─────────────────────────────────────────────────────────────
void connectMQTT() {
  Serial.printf("[MQTT] Connecting to broker %s:%d...\n", MQTT_BROKER, MQTT_PORT);

  // LWT — Last Will & Testament (server knows if device goes offline)
  String lwtPayload = "{\"device_id\":\"" DEVICE_ID "\",\"status\":\"OFFLINE\"}";

  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS,
                   TOPIC_STATUS, 1, true, lwtPayload.c_str())) {
    Serial.println("[MQTT] Connected!");

    // Publish ONLINE status
    String onlineMsg = "{\"device_id\":\"" DEVICE_ID "\",\"status\":\"ONLINE\"}";
    mqtt.publish(TOPIC_STATUS, onlineMsg.c_str(), true);

    // Subscribe to command topic
    mqtt.subscribe(TOPIC_CMD);
    Serial.printf("[MQTT] Subscribed to %s\n", TOPIC_CMD);
  } else {
    Serial.printf("[MQTT] Failed — rc=%d — retrying in %d ms\n",
                  mqtt.state(), RECONNECT_DELAY);
  }
}
