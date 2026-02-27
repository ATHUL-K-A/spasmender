from flask import Flask, jsonify
import threading
import serial
import time
import numpy as np
import pandas as pd
from scipy.signal import welch
from collections import deque
import os
print("Flask Process ID:", os.getpid())

# -------------------------------------------------
# PARAMETERS
# -------------------------------------------------
PORT = "COM4"
BAUD = 115200
FS = 200
WINDOW = 200

CSV_FILE = r"\candy_read\data\combined_BR50.csv"

app = Flask(__name__)

# -------------------------------------------------
# GLOBAL VARIABLES
# -------------------------------------------------
stop_event = threading.Event()
fatigue_detected = False


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
# LOAD DATASET & BUILD REFERENCE
# -------------------------------------------------
df = pd.read_csv(CSV_FILE)

rms_list = []
mdf_list = []

step = WINDOW
n_windows = (len(df) - WINDOW) // step + 1

for w in range(n_windows):
    start = w * step
    raw = df["Raw_EMG"].iloc[start:start+WINDOW].values
    env = df["Envelope_EMG"].iloc[start:start+WINDOW].values

    rms_list.append(compute_rms(env))
    mdf_list.append(compute_mdf(raw, FS))

rms_list = np.array(rms_list)
mdf_list = np.array(mdf_list)


mean_rms = rms_list.mean()
mean_mdf = mdf_list.mean()

print(f"Baseline loaded. mean={mean_rms:.2f} | mean_mdf={mean_mdf:.2f}")

raw_buffer = deque(maxlen=WINDOW)
env_buffer = deque(maxlen=WINDOW)
fatigue_counter = 0
t1 = 0
# -------------------------------------------------
# EMG THREAD FUNCTION
# -------------------------------------------------
def emg_loop():
    global running, fatigue_detected
    print("EMG loop running in PID:", os.getpid())

    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)

    while not stop_event.is_set():
        line = ser.readline().decode(errors="ignore").strip()

        if "," not in line:
            continue

        try:
            raw, env = map(float, line.split(","))
        except ValueError:
            continue

        raw_buffer.append(raw)
        env_buffer.append(env)

        if len(raw_buffer) < WINDOW:
            continue

        # Compute live features
        live_rms = compute_rms(np.array(env_buffer))
        live_mdf = compute_mdf(np.array(raw_buffer), FS)

        # Fatigue logic
        fatigued = (
            live_rms > mean_rms and
            live_mdf < mean_mdf
        )

        status = "⚠️ FATIGUED" if fatigued else "✅ NON-FATIGUED"

        print(
            f"Live RMS: {live_rms:7.2f} | "
            f"Live MDF: {live_mdf:6.1f} Hz | "
            f"{status}"
        )
        if fatigued:
            fatigue_counter += 1
            print(f"Fatigue window detected ({fatigue_counter}/600)")
            t1+=1

        # If fatigue persists for 3 seconds
        if fatigue_counter >= 600:
            print("\n⚠️ FATIGUE DETECTED (for 3 seconds)")
            fatigue_detected = True
            running = False
            break
        if t1>=2000:
            print("Fatigue Counter restarted as 10 seconds has passedd from first detection")
            fatigue_counter=0
            t1=0
    ser.close()


# -------------------------------------------------
# ROUTES
# -------------------------------------------------
@app.route("/start")
def start():
    print("START called in PID:", os.getpid())

    global fatigue_detected

    print("START endpoint hit")

    fatigue_detected = False
    stop_event.clear()

    thread = threading.Thread(target=emg_loop)
    thread.daemon = True
    thread.start()

    print("EMG thread started")

    return jsonify({"status": "started"})




@app.route("/stop")
def stop():
    stop_event.set()
    return jsonify({"status": "stopped"})


@app.route("/status")
def status():
    return jsonify({
        "running": not stop_event.is_set(),
        "fatigue": fatigue_detected
    })


# -------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False, threaded=True)


