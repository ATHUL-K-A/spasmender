import os
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
import joblib

# ESP32_IP = "http://10.132.125.188"   # normal esp32 ip
ESP32_IP = "http://10.132.125.193"   # seeed studio ip

ESP32_DATA_URL    = f"{ESP32_IP}/data"
ESP32_CHANNEL_URL = f"{ESP32_IP}/set_channel"
ESP32_PING_URL    = f"{ESP32_IP}/ping"

BATCH_SIZE        = 20                         # must match BATCH_SIZE in .ino
POLL_INTERVAL     = BATCH_SIZE / 200.0         # 0.1 s is 10 req/s
 
FS                = 200
WINDOW            = 200
GRAPH_BUFFER_SIZE = 200


 
FATIGUE_COUNT_LIMIT = 600    # consecutive fatigued windows to confirm fatigue 3seconds
FATIGUE_RESET_STEPS = 2000   # polling steps before resetting counter (10 seconds) 2000ms
SVM_CONFIDENCE_THRESHOLD = 0.7
 
# One model file per sensor channel — update paths to match your folder
MODEL_PATHS = {
    0: r"D:\\Project\\Dataset EMG Fatigue\\candy_read\\model_train\svm_fatigue_model_BL.pkl",
    1: r"D:\\Project\\Dataset EMG Fatigue\\candy_read\\model_train\svm_fatigue_model_TL.pkl",
    2: r"D:\\Project\\Dataset EMG Fatigue\\candy_read\\model_train\svm_fatigue_model_L.pkl",
    3: r"D:\\Project\\Dataset EMG Fatigue\\candy_read\\model_train\svm_fatigue_model_L2.pkl",
}
 
# -------------------------------------------------
# FEATURE FUNCTIONS
# -------------------------------------------------
def compute_rms(signal):
    """Root Mean Square of envelope — amplitude indicator."""
    return np.sqrt(np.mean(signal ** 2))
 
def compute_mav(signal):
    """Mean Absolute Value of envelope — similar to RMS but less sensitive to peaks."""
    return np.mean(np.abs(signal))
 
def compute_wl(signal):
    """Waveform Length of raw signal — measures signal complexity."""
    return np.sum(np.abs(np.diff(signal)))
 
def compute_zcr(signal):
    """Zero Crossing Rate of raw signal — loosely related to frequency content."""
    return float(np.sum(np.diff(np.sign(signal)) != 0))
 
def compute_mdf(signal, fs):
    """Median Frequency via Welch PSD — frequency at which power splits equally."""
    f, Pxx = welch(signal, fs=fs, nperseg=len(signal))
    cumsum = np.cumsum(Pxx)
    return float(f[np.where(cumsum >= cumsum[-1] / 2)[0][0]])
 
def extract_feature_vector(raw_arr, env_arr):
    """Returns a (1, 5) numpy array ready for SVM prediction."""
    return np.array([[
        compute_rms(env_arr),
        compute_mav(env_arr),
        compute_wl(raw_arr),
        compute_zcr(raw_arr),
        compute_mdf(raw_arr, FS)
    ]])
 
# -------------------------------------------------
# PER-CHANNEL MODEL LOADING
# -------------------------------------------------
active_channel = {"value": 0}
svm_model      = None
 
def load_model_for_channel(ch):
    """
    Loads the SVM model for the given channel.
    Raises RuntimeError if the model file is missing or fails to load
    so Flask refuses to start/switch without a valid model.
    """
    global svm_model
 
    path = MODEL_PATHS.get(ch)
    if not path:
        raise RuntimeError(f"No model path defined for channel {ch}")
 
    if not os.path.exists(path):
        raise RuntimeError(
            f"Model file not found for channel {ch}:\n  {path}\n"
            f"Train and save the model before running Flask."
        )
 
    svm_model = joblib.load(path)
    print(f"Channel {ch} — SVM model loaded: {path}")
 
# Load model for default channel 0 on startup — crashes if missing 
load_model_for_channel(0)
 

# SHARED STATE

state_lock           = threading.Lock()
stop_event           = threading.Event()
poll_thread_instance = None
 
raw_buffer   = deque(maxlen=WINDOW)
env_buffer   = deque(maxlen=WINDOW)
graph_buffer = deque(maxlen=GRAPH_BUFFER_SIZE)
 
fatigue_counter_store = {"value": 0, "t1": 0}
 
state = {
    "live_rms":           0.0,
    "live_mdf":           0.0,
    "fatigue_counter":    0,
    "fatigue_detected":   False,
    "fatigue_confidence": 0.0,
    "status":             "STOPPED",
    "esp32_connected":    False,
    "active_channel":     0,
    "detection_mode":     "SVM"
}
 

# FLASK + SOCKETIO

app = Flask(__name__)
app.config["SECRET_KEY"] = "emg_secret"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
 

# FATIGUE DETECTION — SVM only

def detect_fatigue(raw_arr, env_arr):
    """
    Runs the loaded SVM model on the five extracted features.
    """
    features = extract_feature_vector(raw_arr, env_arr)
    proba    = svm_model.predict_proba(features)[0][1]  # P(class=fatigued)
    fatigued = proba >= SVM_CONFIDENCE_THRESHOLD
    return fatigued, round(float(proba), 4)
 

# POLLING THREAD
# Polls ESP32 at 10 req/s each response is a batch of 20 samples

def poll_esp32():
    print(f"Polling ESP32 at {ESP32_DATA_URL}")
    print(f"Channel {active_channel['value']} — SVM model active")
 
    session     = requests.Session()
    retry_delay = 0.5
 
    while not stop_event.is_set():
        loop_start = time.time()
 
        try:
            resp    = session.get(ESP32_DATA_URL, timeout=(2, 1.0))
            samples = resp.json()
 
            if not isinstance(samples, list):
                raise ValueError("Expected a JSON array from ESP32")
 
            retry_delay = 0.5
 
            with state_lock:
                state["esp32_connected"] = True
 
            # Append all samples in batch to buffers
            for s in samples:
                if stop_event.is_set():
                    break
                raw_val = float(s["raw"])
                env_val = float(s["env"])
                with state_lock:
                    raw_buffer.append(raw_val)
                    env_buffer.append(env_val)
                    graph_buffer.append({"raw": raw_val, "env": env_val})
                socketio.emit("emg_sample", {"raw": raw_val, "env": env_val})
 
            # Feature computation and SVM detection once per batch
            with state_lock:
                if len(raw_buffer) < WINDOW:
                    elapsed = time.time() - loop_start
                    time.sleep(max(0, POLL_INTERVAL - elapsed))
                    continue
 
                raw_arr = np.array(raw_buffer)
                env_arr = np.array(env_buffer)
 
                live_rms = compute_rms(env_arr)
                live_mdf = compute_mdf(raw_arr, FS)
 
                fatigued, confidence = detect_fatigue(raw_arr, env_arr)
 
                fc = fatigue_counter_store["value"]
                t1 = fatigue_counter_store["t1"]
 
                if fatigued:
                    fc += 20
                    print(
                        f"[SVM] Counter: {fc} | "
                        f"RMS: {live_rms:.4f} | "
                        f"MDF: {live_mdf:.2f} Hz | "
                        f"Conf: {confidence:.2%}"
                    )
                    t1 += 20
                if t1:
                    t1 += 20
                if t1 >= FATIGUE_RESET_STEPS:
                    print("Fatigue counter restarted — 10 seconds passed")
                    fc = 0
                    t1 = 0
 
                fatigue_detected = fc >= FATIGUE_COUNT_LIMIT
                fatigue_counter_store["value"] = fc
                fatigue_counter_store["t1"]    = t1
 
                state["live_rms"]           = round(live_rms, 4)
                state["live_mdf"]           = round(live_mdf, 4)
                state["fatigue_counter"]    = fc
                state["fatigue_detected"]   = fatigue_detected
                state["fatigue_confidence"] = confidence
                state["status"]             = "FATIGUED" if fatigued else "RUNNING"
 
            socketio.emit("status_update", dict(state))
 
            if fatigue_detected:
                print("FATIGUE CONFIRMED")
                socketio.emit("fatigue_alert", {"message": "Fatigue detected!"})
                with state_lock:
                    fatigue_counter_store["value"] = 0
                    fatigue_counter_store["t1"]    = 0
                    state["status"]                = "STOPPED"
                stop_event.set()
 
        except requests.exceptions.ConnectionError:
            with state_lock:
                state["esp32_connected"] = False
            print(f"ESP32 unreachable — retrying in {retry_delay:.1f}s")
            socketio.emit("status_update", dict(state))
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 5.0)
            session = requests.Session()
            continue
 
        except Exception as e:
            print(f"Poll error: {e}")
 
        elapsed = time.time() - loop_start
        time.sleep(max(0, POLL_INTERVAL - elapsed))
 
    with state_lock:
        state["status"]          = "STOPPED"
        state["esp32_connected"] = False
    socketio.emit("status_update", dict(state))
    print("Polling stopped.")
 

# ROUTES

@app.route("/start", methods=["GET"])
def start():
    global poll_thread_instance
 
    # Verify ESP32 is reachable
    try:
        r = requests.get(ESP32_PING_URL, timeout=5)
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
        fatigue_counter_store["t1"]    = 0
        state["status"]                = "RUNNING"
        state["fatigue_detected"]      = False
        state["fatigue_counter"]       = 0
        state["fatigue_confidence"]    = 0.0
        state["live_rms"]              = 0.0
        state["live_mdf"]              = 0.0
 
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
 
    # Tell ESP32 to switch MUX channel
    try:
        requests.get(f"{ESP32_CHANNEL_URL}?ch={ch}", timeout=2)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
 
    # Load the SVM model for the new channel — returns 503 if model missing
    try:
        load_model_for_channel(ch)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
 
    active_channel["value"] = ch
 
    # Reset buffers so old channel data does not bleed into new channel
    with state_lock:
        raw_buffer.clear()
        env_buffer.clear()
        graph_buffer.clear()
        fatigue_counter_store["value"] = 0
        fatigue_counter_store["t1"]    = 0
        state["fatigue_counter"]       = 0
        state["fatigue_confidence"]    = 0.0
        state["active_channel"]        = ch
 
    socketio.emit("status_update", dict(state))
    print(f"Channel switched to {ch} — SVM model loaded")
    return jsonify({"message": f"Channel set to {ch}", "detection_mode": "SVM"})
 
 
@app.route("/info", methods=["GET"])
def info():
    """Returns system info — useful for debugging from browser."""
    channel_models = {
        str(ch): "available" if os.path.exists(p) else "missing"
        for ch, p in MODEL_PATHS.items()
    }
    return jsonify({
        "detection_mode":  "SVM",
        "active_channel":  active_channel["value"],
        "channel_models":  channel_models,
        "svm_threshold":   SVM_CONFIDENCE_THRESHOLD,
        "batch_size":      BATCH_SIZE,
        "window":          WINDOW,
        "fs":              FS,
    })
 
 
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