#!/usr/bin/env python3
"""
================================================================
 IoT Sensor Monitoring System — REST API + Live Dashboard
================================================================
 Serves a web API over SQLite data + a real-time SSE feed.

 Install:
   pip install flask flask-cors
 Run:
   python3 dashboard_api.py
 Open:
   http://localhost:5000
================================================================
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request
from flask_cors import CORS

DB_PATH = Path("data/sensors.db")

app = Flask(__name__)
CORS(app)

# ─── DB helper ───────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return dict(row) if row else None

# ─── API Routes ───────────────────────────────────────────────
@app.route("/api/latest")
def api_latest():
    """Latest reading per device."""
    db = get_db()
    rows = db.execute("""
        SELECT * FROM sensor_readings
        WHERE id IN (
            SELECT MAX(id) FROM sensor_readings GROUP BY device_id
        )
        ORDER BY received_at DESC
    """).fetchall()
    db.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/history")
def api_history():
    """Last N readings for a device."""
    device = request.args.get("device", "%")
    hours  = int(request.args.get("hours", 1))
    limit  = int(request.args.get("limit", 100))
    since  = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    db = get_db()
    rows = db.execute("""
        SELECT * FROM sensor_readings
        WHERE device_id LIKE ? AND received_at >= ?
        ORDER BY received_at DESC LIMIT ?
    """, (device, since, limit)).fetchall()
    db.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/alerts")
def api_alerts():
    """Recent alerts."""
    limit = int(request.args.get("limit", 20))
    db = get_db()
    rows = db.execute("""
        SELECT * FROM alerts ORDER BY received_at DESC LIMIT ?
    """, (limit,)).fetchall()
    db.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/devices")
def api_devices():
    db = get_db()
    rows = db.execute("SELECT * FROM device_status").fetchall()
    db.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/stats")
def api_stats():
    """Aggregated stats for the last hour."""
    db = get_db()
    row = db.execute("""
        SELECT
            COUNT(*)          AS total_readings,
            AVG(temperature)  AS avg_temp,
            MIN(temperature)  AS min_temp,
            MAX(temperature)  AS max_temp,
            AVG(humidity)     AS avg_humidity,
            AVG(pressure_hpa) AS avg_pressure,
            AVG(aqi_raw)      AS avg_aqi,
            AVG(light_pct)    AS avg_light
        FROM sensor_readings
        WHERE received_at >= datetime('now', '-1 hour')
    """).fetchone()
    alerts = db.execute("""
        SELECT COUNT(*) AS cnt FROM alerts
        WHERE received_at >= datetime('now', '-1 hour')
    """).fetchone()
    db.close()
    stats = dict(row)
    stats["alerts_last_hour"] = alerts["cnt"]
    # Round floats
    for k, v in stats.items():
        if isinstance(v, float):
            stats[k] = round(v, 2)
    return jsonify(stats)


# ─── SSE Live Feed ────────────────────────────────────────────
@app.route("/api/stream")
def stream():
    """Server-Sent Events — push latest reading every 5 s."""
    def generate():
        last_id = 0
        while True:
            db = get_db()
            rows = db.execute(
                "SELECT * FROM sensor_readings WHERE id > ? ORDER BY id ASC LIMIT 10",
                (last_id,)
            ).fetchall()
            db.close()
            for row in rows:
                last_id = row["id"]
                data = json.dumps(row_to_dict(row))
                yield f"data: {data}\n\n"
            time.sleep(5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ─── Dashboard HTML ───────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IoT Sensor Monitor</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;600;800&display=swap');
  :root {
    --bg: #0a0e1a; --panel: #111827; --border: #1e2d45;
    --accent: #00d4ff; --green: #00ff88; --red: #ff4466;
    --yellow: #ffcc00; --text: #c8d8e8; --dim: #4a5568;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font-family: 'Exo 2', sans-serif; min-height: 100vh; }
  header { border-bottom: 1px solid var(--border); padding: 1rem 2rem;
           display: flex; align-items: center; gap: 1rem; }
  header h1 { font-size: 1.4rem; font-weight: 800; letter-spacing: 2px;
              color: var(--accent); text-transform: uppercase; }
  .dot { width: 10px; height: 10px; border-radius: 50%;
         background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr));
          gap: 1rem; padding: 1.5rem 2rem; }
  .card { background: var(--panel); border: 1px solid var(--border);
          border-radius: 8px; padding: 1.2rem; position: relative; overflow: hidden; }
  .card::before { content: ''; position: absolute; top: 0; left: 0;
                  width: 3px; height: 100%; background: var(--accent); }
  .card.alert::before { background: var(--red); }
  .card label { font-size: 0.65rem; letter-spacing: 2px; text-transform: uppercase;
                color: var(--dim); }
  .card .value { font-family: 'Share Tech Mono', monospace;
                 font-size: 2.2rem; color: var(--accent); margin: 0.3rem 0; }
  .card .unit { font-size: 0.8rem; color: var(--dim); }
  .card .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
                 font-size: 0.7rem; font-weight: 600; letter-spacing: 1px; margin-top: 4px; }
  .good   { background: rgba(0,255,136,.15); color: var(--green); }
  .warn   { background: rgba(255,204,0,.15); color: var(--yellow); }
  .bad    { background: rgba(255,68,102,.15); color: var(--red); }
  .section { padding: 0 2rem 1rem; }
  .section h2 { font-size: 0.75rem; letter-spacing: 3px; text-transform: uppercase;
                color: var(--dim); margin-bottom: 1rem; }
  .log-box { background: var(--panel); border: 1px solid var(--border);
             border-radius: 8px; padding: 1rem; font-family: 'Share Tech Mono', monospace;
             font-size: 0.78rem; max-height: 220px; overflow-y: auto; }
  .log-entry { padding: 4px 0; border-bottom: 1px solid var(--border); }
  .log-entry .ts { color: var(--dim); margin-right: 8px; }
  .log-entry.alert-entry .ts { color: var(--red); }
  footer { text-align: center; padding: 1.5rem; color: var(--dim); font-size: 0.75rem; }
</style>
</head>
<body>
<header>
  <div class="dot" id="statusDot"></div>
  <h1>⚡ IoT Sensor Monitor</h1>
  <span style="margin-left:auto;font-size:.75rem;color:var(--dim)" id="lastUpdate">—</span>
</header>

<div class="grid" id="sensorGrid">
  <div class="card"><label>Temperature</label><div class="value" id="temp">—</div><span class="unit">°C</span></div>
  <div class="card"><label>Humidity</label><div class="value" id="hum">—</div><span class="unit">%</span></div>
  <div class="card"><label>Pressure</label><div class="value" id="pres">—</div><span class="unit">hPa</span></div>
  <div class="card"><label>Altitude</label><div class="value" id="alt">—</div><span class="unit">m</span></div>
  <div class="card"><label>Air Quality</label><div class="value" id="aqi">—</div><span class="unit"><span id="aqiBadge" class="badge">—</span></span></div>
  <div class="card"><label>Light Level</label><div class="value" id="light">—</div><span class="unit"><span id="lightBadge" class="badge">—</span></span></div>
  <div class="card"><label>Distance</label><div class="value" id="dist">—</div><span class="unit">cm</span></div>
</div>

<div class="section">
  <h2>📋 Live Event Log</h2>
  <div class="log-box" id="logBox"></div>
</div>

<footer>IoT Sensor Monitoring System · ESP32 + MQTT + Python · Live SSE Feed</footer>

<script>
const $ = id => document.getElementById(id);
function badge(val, good, mod){
  if(val==='GOOD'||val==='BRIGHT'||val==='NORMAL') return 'good';
  if(val==='MODERATE'||val==='DIM') return 'warn';
  return 'bad';
}
function log(msg, isAlert=false){
  const box = $('logBox');
  const d = document.createElement('div');
  d.className = 'log-entry' + (isAlert?' alert-entry':'');
  d.innerHTML = `<span class="ts">${new Date().toLocaleTimeString()}</span>${msg}`;
  box.prepend(d);
  if(box.children.length > 50) box.lastChild.remove();
}
function update(d){
  $('temp').textContent  = d.temperature ?? '—';
  $('hum').textContent   = d.humidity ?? '—';
  $('pres').textContent  = d.pressure_hpa ?? '—';
  $('alt').textContent   = d.altitude_m ?? '—';
  $('aqi').textContent   = d.aqi_raw ?? '—';
  $('light').textContent = d.light_pct ?? '—';
  $('dist').textContent  = d.distance_cm ?? '—';
  const ab = $('aqiBadge');
  ab.textContent = d.aqi_status||'—'; ab.className='badge '+badge(d.aqi_status);
  const lb = $('lightBadge');
  lb.textContent = d.light_status||'—'; lb.className='badge '+badge(d.light_status);
  $('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();
  log(`[${d.device_id}] T=${d.temperature}°C  H=${d.humidity}%  AQI=${d.aqi_status}`);
}
// SSE stream
const src = new EventSource('/api/stream');
src.onmessage = e => { try{ update(JSON.parse(e.data)); }catch(err){} };
src.onerror   = () => { $('statusDot').style.background='var(--red)'; };
// Initial load
fetch('/api/latest').then(r=>r.json()).then(arr=>{ if(arr[0]) update(arr[0]); });
fetch('/api/alerts?limit=10').then(r=>r.json()).then(alerts=>{
  alerts.forEach(a=>log(`🚨 ${a.alert_type}: ${a.message} (${a.value})`, true));
});
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"[WARN] Database not found at {DB_PATH} — start mqtt_logger.py first")
    print("Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
