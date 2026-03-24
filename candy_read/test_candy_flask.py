import threading
import serial
import time
import numpy as np
import pandas as pd
from scipy.signal import welch
from collections import deque
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# -------------------------------------------------
# PARAMETERS  — only change these
# -------------------------------------------------
PORT     = "COM4"       # change to your serial port (e.g. COM3, /dev/ttyUSB0)
BAUD     = 115200
FS       = 200
WINDOW   = 200
GRAPH_BUFFER_SIZE = 200
CSV_FILE = "D:\Project\Dataset EMG Fatigue\candy_read\data\combined_BL50.csv"

# -------------------------------------------------
# FEATURE FUNCTIONS
# -------------------------------------------------
def compute_rms(signal):
    return np.sqrt(np.mean(signal ** 2))

def compute_mdf(signal, fs):
    f, Pxx = welch(signal, fs=fs, nperseg=len(signal))
    cumsum  = np.cumsum(Pxx)
    return f[np.where(cumsum >= cumsum[-1] / 2)[0][0]]

# -------------------------------------------------
# LOAD DATASET BASELINE
# -------------------------------------------------
df = pd.read_csv(CSV_FILE)

rms_list, mdf_list = [], []
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
state_lock          = threading.Lock()
stop_event          = threading.Event()
poll_thread_instance = None
ser_global          = None

raw_buffer   = deque(maxlen=WINDOW)
env_buffer   = deque(maxlen=WINDOW)
graph_buffer = deque(maxlen=GRAPH_BUFFER_SIZE)

fatigue_counter_store = {"value": 0, "t1": 0}

state = {
    "live_rms":        0.0,
    "live_mdf":        0.0,
    "fatigue_counter": 0,
    "fatigue_detected": False,
    "status":          "STOPPED",
    "esp32_connected": False      # kept for dart compatibility; means "serial connected" here
}

pending_channel = {"value": 0}

# -------------------------------------------------
# FLASK + SOCKETIO
# -------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "emg_secret"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# -------------------------------------------------
# SERIAL READING THREAD
# reads "signal,envelop\n" lines printed by the Arduino
# -------------------------------------------------
def serial_thread():
    global ser_global
    t1_state = 0
    # Open serial port
    try:
        ser_global = serial.Serial(PORT, BAUD, timeout=1)
        time.sleep(2)           # wait for Arduino reset
        print(f"Serial connected on {PORT}")
        with state_lock:
            state["esp32_connected"] = True
    except Exception as e:
        print(f"Serial error: {e}")
        with state_lock:
            state["status"] = "STOPPED"
            state["esp32_connected"] = False
        socketio.emit("status_update", dict(state))
        return

    while not stop_event.is_set():
        try:
            line = ser_global.readline().decode(errors="ignore").strip()
        except Exception as e:
            print(f"Read error: {e}")
            break

        # Arduino prints:  signal,envelop
        if "," not in line:
            continue
        try:
            raw_val, env_val = map(float, line.split(","))
        except ValueError:
            continue

        with state_lock:
            raw_buffer.append(raw_val)
            env_buffer.append(env_val)
            graph_buffer.append({"raw": raw_val, "env": env_val})

        # Push sample to Flutter (emg_sample kept for dart compatibility,
        # though main.dart no longer plots it — harmless)
        socketio.emit("emg_sample", {"raw": raw_val, "env": env_val})

        with state_lock:
            if len(raw_buffer) < WINDOW:
                continue

            live_rms = compute_rms(np.array(env_buffer))
            live_mdf = compute_mdf(np.array(raw_buffer), FS)
            fatigued = (live_rms > mean_rms and live_mdf < mean_mdf)

            fc = fatigue_counter_store["value"]
            t1 = fatigue_counter_store["t1"]

            if fatigued:
                fc += 1
                print(f"Fatigue Counter: {fc} | RMS: {live_rms:.4f} | MDF: {live_mdf:.4f}")
                t1_state = 1
            if t1_state:
                t1 += 1
            if t1 >= 2000:
                print("Fatigue counter restarted — 10 seconds passed")
                fc = 0
                t1 = 0
                t1_state = 0
            fatigue_detected = fc >= 600
            fatigue_counter_store["value"] = fc
            fatigue_counter_store["t1"]    = t1

            state["live_rms"]        = round(live_rms, 4)
            state["live_mdf"]        = round(live_mdf, 4)
            state["fatigue_counter"] = fc
            state["fatigue_detected"] = fatigue_detected
            state["status"]          = "FATIGUED" if fatigued else "RUNNING"

        socketio.emit("status_update", dict(state))

        if fatigue_detected:
            print("⚠️ FATIGUE DETECTED")
            socketio.emit("fatigue_alert", {"message": "Fatigue detected!"})
            with state_lock:
                fatigue_counter_store["value"] = 0
                fatigue_counter_store["t1"]    = 0
                state["status"]                = "STOPPED"
            stop_event.set()

    # Cleanup
    try:
        ser_global.close()
    except Exception:
        pass

    with state_lock:
        state["status"]          = "STOPPED"
        state["esp32_connected"] = False

    socketio.emit("status_update", dict(state))
    print("Serial thread stopped.")

# -------------------------------------------------
# ROUTES  — identical surface to the WiFi version
# -------------------------------------------------

@app.route("/start", methods=["GET"])
def start():
    global poll_thread_instance

    if poll_thread_instance and poll_thread_instance.is_alive():
        return jsonify({"message": "Already running"})

    stop_event.clear()
    with state_lock:
        raw_buffer.clear()
        env_buffer.clear()
        graph_buffer.clear()
        fatigue_counter_store["value"] = 0
        fatigue_counter_store["t1"]    = 0
        state["status"]                = "RUNNING"
        state["fatigue_detected"]      = False
        state["fatigue_counter"]       = 0
        state["live_rms"]              = 0.0
        state["live_mdf"]              = 0.0

    poll_thread_instance = threading.Thread(target=serial_thread, daemon=True)
    poll_thread_instance.start()

    socketio.emit("status_update", dict(state))
    print("Monitoring started — reading from serial")
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


# Channel switch — sends the channel number over serial just like
# the original serial-based sketch expects ("0\n", "1\n", etc.)
@app.route("/set_channel/<int:ch>", methods=["GET"])
def set_channel(ch):
    global ser_global

    if ch < 0 or ch > 3:
        return jsonify({"error": "Invalid channel"}), 400

    if ser_global is None or not ser_global.is_open:
        return jsonify({"error": "Serial port not open"}), 500

    try:
        ser_global.write(f"{ch}\n".encode())
        pending_channel["value"] = ch
        return jsonify({"message": f"Channel set to {ch}"})
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