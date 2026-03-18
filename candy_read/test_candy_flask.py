import serial
import time
import threading
import numpy as np
import pandas as pd
from scipy.signal import welch
from sklearn.linear_model import LinearRegression
from collections import deque
from flask import Flask, jsonify
from flask_cors import CORS
ser_global = None
# -------------------------------------------------
# PARAMETERS
# -------------------------------------------------
PORT = "COM4"
BAUD = 115200
FS = 200
WINDOW = 200
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
    raw = df["Raw_EMG"].iloc[start:start+WINDOW].values
    env = df["Envelope_EMG"].iloc[start:start+WINDOW].values

    rms_list.append(compute_rms(env))
    mdf_list.append(compute_mdf(raw, FS))

mean_rms = np.mean(rms_list)
mean_mdf = np.mean(mdf_list)

print("=== Dataset Reference Built ===")
print("Mean RMS:", mean_rms)
print("Mean MDF:", mean_mdf)

# -------------------------------------------------
# SHARED STATE
# -------------------------------------------------
state_lock = threading.Lock()
stop_event = threading.Event()
emg_thread_instance = None

state = {
    "live_rms": 0.0,
    "live_mdf": 0.0,
    "fatigue_counter": 0,
    "fatigue_detected": False,
    "status": "STOPPED"
}

# -------------------------------------------------
# EMG THREAD
# -------------------------------------------------
def emg_thread():
    global state

    try:
        global ser_global
        ser_global = serial.Serial(PORT, BAUD, timeout=1)
        time.sleep(2)
        ser = ser_global
        time.sleep(2)
        print("Serial connected.")
    except Exception as e:
        print("Serial error:", e)
        return

    raw_buffer = deque(maxlen=WINDOW)
    env_buffer = deque(maxlen=WINDOW)

    fatigue_counter = 0
    t1=0

    while not stop_event.is_set():        
        line = ser.readline().decode(errors="ignore").strip()
        if "," not in line:
            continue

        try:
            raw, env = map(float, line.split(","))
        except:
            continue

        raw_buffer.append(raw)
        env_buffer.append(env)

        if len(raw_buffer) < WINDOW:
            continue

        live_rms = compute_rms(np.array(env_buffer))
        live_mdf = compute_mdf(np.array(raw_buffer), FS)

        fatigued = (live_rms > mean_rms and live_mdf < mean_mdf)

        if fatigued:
            fatigue_counter += 1
            print(f"Fatigue Counter: {fatigue_counter} | RMS: {live_rms:.4f} | MDF: {live_mdf:.4f}")
            t1+=1
        if t1:
            t1+=1            
        if t1>=2000:
            print("Fatigue Counter restarted as 10 seconds has passedd from first detection")
            fatigue_counter=0
            t1=0

        fatigue_detected = fatigue_counter >= 600 

        with state_lock:
            state["live_rms"] = round(float(live_rms), 4)
            state["live_mdf"] = round(float(live_mdf), 4)
            state["fatigue_counter"] = fatigue_counter
            state["fatigue_detected"] = fatigue_detected
            state["status"] = "FATIGUED" if fatigued else "RUNNING"

        if fatigue_detected:
            print("⚠️ FATIGUE DETECTED")
            fatigue_counter = 0
            t1=0
            stop_event.set()

    ser.close()
    with state_lock:
        state["status"] = "STOPPED"

    print("EMG thread stopped.")

# -------------------------------------------------
# FLASK APP
# -------------------------------------------------
app = Flask(__name__)
CORS(app)

@app.route("/start", methods=["GET"])
def start():
    global emg_thread_instance

    if emg_thread_instance and emg_thread_instance.is_alive():
        return jsonify({"message": "Already running"})

    stop_event.clear()

    emg_thread_instance = threading.Thread(target=emg_thread, daemon=True)
    emg_thread_instance.start()

    with state_lock:
        state["status"] = "RUNNING"
        state["fatigue_detected"] = False
        state["fatigue_counter"] = 0

    return jsonify({"message": "Started"})

@app.route("/stop", methods=["GET"])
def stop():
    stop_event.set()
    return jsonify({"message": "Stopped"})

@app.route("/status", methods=["GET"])
def status():
    with state_lock:
        return jsonify(state)
@app.route("/set_channel/<int:ch>", methods=["GET"])
def set_channel(ch):
    global ser_global

    if ch < 0 or ch > 3:
        return jsonify({"error": "Invalid channel"}), 400

    if ser_global is None:
        return jsonify({"error": "Serial not initialized"}), 500

    try:
        ser_global.write(f"{ch}\n".encode())
        return jsonify({"message": f"Channel set to {ch}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# -------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)