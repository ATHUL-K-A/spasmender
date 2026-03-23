import threading
import time
import requests
import numpy as np
import pandas as pd
from scipy.signal import welch
from collections import deque
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# -------------------------------------------------
# PARAMETERS
# -------------------------------------------------
ESP32_IP = "http://10.159.219.196"   # <-- Replace XXX with ESP32's IP from Serial Monitor
ESP32_DATA_URL = f"{ESP32_IP}/data"
ESP32_CHANNEL_URL = f"{ESP32_IP}/set_channel"
ESP32_PING_URL = f"{ESP32_IP}/ping"

POLL_INTERVAL = 1.0 / 200          # poll at 200 Hz to match SAMPLE_RATE
FS = 200
WINDOW = 200
GRAPH_BUFFER_SIZE = 200
CSV_FILE = "D:\Project\Dataset EMG Fatigue\candy_read\data\combined_BL50.csv"

# -------------------------------------------------
# FEATURE FUNCTIONS
# -------------------------------------------------
def compute_rms(signal):
    return np.sqrt(np.mean(signal ** 2))

def compute_mdf(signal, fs):
    f, Pxx = welch(signal, fs=fs, nperseg=len(signal))
    cumsum = np.cumsum(Pxx)
    return f[np.where(cumsum >= cumsum[-1] / 2)[0][0]]

# -------------------------------------------------
# LOAD DATASET BASELINE
# -------------------------------------------------
df = pd.read_csv(CSV_FILE)

rms_list = []
mdf_list = []
n_windows = (len(df) - WINDOW) // WINDOW + 1

for w in range(n_windows):
    start = w * WINDOW
    raw = df["Raw_EMG"].iloc[start:start + WINDOW].values
    env = df["Envelope_EMG"].iloc[start:start + WINDOW].values
    rms_list.append(compute_rms(env))
    mdf_list.append(compute_mdf(raw, FS))

mean_rms = np.mean(rms_list)
mean_mdf = np.mean(mdf_list)

print("=== Dataset Reference Built ===")
print(f"Mean RMS: {mean_rms}")
print(f"Mean MDF: {mean_mdf}")

# -------------------------------------------------
# SHARED STATE
# -------------------------------------------------
state_lock = threading.Lock()
stop_event = threading.Event()
poll_thread_instance = None

raw_buffer = deque(maxlen=WINDOW)
env_buffer = deque(maxlen=WINDOW)
graph_buffer = deque(maxlen=GRAPH_BUFFER_SIZE)
fatigue_counter_store = {"value": 0, "t1": 0}

state = {
    "live_rms": 0.0,
    "live_mdf": 0.0,
    "fatigue_counter": 0,
    "fatigue_detected": False,
    "status": "STOPPED",
    "esp32_connected": False
}

# -------------------------------------------------
# FLASK + SOCKETIO
# -------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "emg_secret"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# -------------------------------------------------
# POLLING THREAD — Flask polls ESP32 at 200 Hz
# -------------------------------------------------
def poll_esp32():
    print(f"Polling ESP32 at {ESP32_DATA_URL}")
    session = requests.Session()  # reuse connection for speed

    while not stop_event.is_set():
        loop_start = time.time()

        try:
            resp = session.get(ESP32_DATA_URL, timeout=0.1)
            body = resp.json()
            raw = float(body["raw"])
            env = float(body["env"])

            with state_lock:
                state["esp32_connected"] = True
                raw_buffer.append(raw)
                env_buffer.append(env)
                graph_buffer.append({"raw": raw, "env": env})

            # Push sample to Flutter app
            socketio.emit("emg_sample", {"raw": raw, "env": env})

            with state_lock:
                if len(raw_buffer) < WINDOW:
                    elapsed = time.time() - loop_start
                    time.sleep(max(0, POLL_INTERVAL - elapsed))
                    continue

                live_rms = compute_rms(np.array(env_buffer))
                live_mdf = compute_mdf(np.array(raw_buffer), FS)
                fatigued = (live_rms > mean_rms and live_mdf < mean_mdf)

                fc = fatigue_counter_store["value"]
                t1 = fatigue_counter_store["t1"]

                if fatigued:
                    fc += 1
                    print(f"Fatigue Counter: {fc} | RMS: {live_rms:.4f} | MDF: {live_mdf:.4f}")
                    t1 += 1
                if t1:
                    t1 += 1
                if t1 >= 2000:
                    print("Fatigue counter restarted — 10 seconds passed")
                    fc = 0
                    t1 = 0

                fatigue_detected = fc >= 600
                fatigue_counter_store["value"] = fc
                fatigue_counter_store["t1"] = t1

                state["live_rms"] = round(live_rms, 4)
                state["live_mdf"] = round(live_mdf, 4)
                state["fatigue_counter"] = fc
                state["fatigue_detected"] = fatigue_detected
                state["status"] = "FATIGUED" if fatigued else "RUNNING"

            socketio.emit("status_update", dict(state))

            if fatigue_detected:
                print("⚠️ FATIGUE DETECTED")
                socketio.emit("fatigue_alert", {"message": "Fatigue detected!"})
                with state_lock:
                    fatigue_counter_store["value"] = 0
                    fatigue_counter_store["t1"] = 0
                    state["status"] = "STOPPED"
                stop_event.set()

        except requests.exceptions.ConnectionError:
            with state_lock:
                state["esp32_connected"] = False
            print("ESP32 not reachable — retrying...")
            time.sleep(1)
            continue
        except Exception as e:
            print(f"Poll error: {e}")

        # Maintain 200 Hz pace
        elapsed = time.time() - loop_start
        time.sleep(max(0, POLL_INTERVAL - elapsed))

    with state_lock:
        state["status"] = "STOPPED"
        state["esp32_connected"] = False
    socketio.emit("status_update", dict(state))
    print("Polling stopped.")

# -------------------------------------------------
# ROUTES
# -------------------------------------------------
@app.route("/start", methods=["GET"])
def start():
    global poll_thread_instance

    # Check ESP32 is reachable before starting
    try:
        r = requests.get(ESP32_PING_URL, timeout=2)
        if r.status_code != 200:
            return jsonify({"error": "ESP32 not responding"}), 503
    except Exception:
        return jsonify({"error": "Cannot reach ESP32 — check IP and WiFi"}), 503

    if poll_thread_instance and poll_thread_instance.is_alive():
        return jsonify({"message": "Already running"})

    stop_event.clear()
    with state_lock:
        raw_buffer.clear()
        env_buffer.clear()
        graph_buffer.clear()
        fatigue_counter_store["value"] = 0
        fatigue_counter_store["t1"] = 0
        state["status"] = "RUNNING"
        state["fatigue_detected"] = False
        state["fatigue_counter"] = 0
        state["live_rms"] = 0.0
        state["live_mdf"] = 0.0

    poll_thread_instance = threading.Thread(target=poll_esp32, daemon=True)
    poll_thread_instance.start()

    socketio.emit("status_update", dict(state))
    print("Monitoring started — polling ESP32")
    return jsonify({"message": "Started"})

@app.route("/stop", methods=["GET"])
def stop():
    stop_event.set()
    with state_lock:
        state["status"] = "STOPPED"
    socketio.emit("status_update", dict(state))
    return jsonify({"message": "Stopped"})

@app.route("/status", methods=["GET"])
def status():
    with state_lock:
        return jsonify(state)

@app.route("/graph_data", methods=["GET"])
def graph_data():
    with state_lock:
        return jsonify(list(graph_buffer))

@app.route("/set_channel/<int:ch>", methods=["GET"])
def set_channel(ch):
    if ch < 0 or ch > 3:
        return jsonify({"error": "Invalid channel"}), 400
    try:
        r = requests.get(f"{ESP32_CHANNEL_URL}?ch={ch}", timeout=2)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------
# SOCKETIO EVENTS
# -------------------------------------------------
@socketio.on("connect")
def on_connect():
    print("Mobile client connected")
    with state_lock:
        emit("status_update", dict(state))
        emit("graph_history", list(graph_buffer))

@socketio.on("disconnect")
def on_disconnect():
    print("Mobile client disconnected")

# -------------------------------------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False)