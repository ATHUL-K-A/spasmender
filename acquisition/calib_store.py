import serial
import time
import numpy as np
import csv
from collections import deque

# -----------------------------
# USER SETTINGS
# -----------------------------
PORT = "COM4"        # Change if required
BAUD = 115200
FS = 200             # Sampling frequency (Hz)
DURATION = 60        # seconds of data recording
WINDOW_SEC = 2       # RMS window length

REST_TIME = 10        # seconds
MVC_TIME = 10         # seconds

CSV_FILE = "emg_60sec_data.csv"

# -----------------------------
# SERIAL SETUP
# -----------------------------
ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(2)
print("Connected to ESP32")

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def compute_rms(data):
    data = np.array(data)
    return np.sqrt(np.mean(data ** 2))

def normalize(value, rest, mvc):
    if mvc - rest <= 0:
        return 0.0
    return (value - rest) / (mvc - rest)

# -----------------------------
# STEP 1: REST CALIBRATION
# -----------------------------
print("\n=== REST CALIBRATION ===")
print("Relax muscle completely...")

rest_samples = []
start = time.time()

while time.time() - start < REST_TIME:
    line = ser.readline().decode(errors="ignore").strip()
    if line.isdigit():
        rest_samples.append(int(line))

baseline = np.mean(rest_samples)
print(f"✔ Rest baseline = {baseline:.2f}")

# -----------------------------
# STEP 2: MVC CALIBRATION
# -----------------------------
print("\n=== MVC CALIBRATION ===")
print(" ------Flex muscle as HARD as possible!")

mvc_samples = []
start = time.time()

while time.time() - start < MVC_TIME:
    line = ser.readline().decode(errors="ignore").strip()
    if line.isdigit():
        mvc_samples.append(int(line))

mvc = max(mvc_samples)
print(f"✔ MVC value = {mvc}")

if mvc <= baseline:
    print("⚠️ Warning: MVC too low — calibration may be weak")

# -----------------------------
# STEP 3: 10-SECOND ACQUISITION
# -----------------------------
print(f"\n=== RECORDING sEMG FOR {DURATION} SECONDS ===")

signal_buffer = deque(maxlen=FS * WINDOW_SEC)
data_log = []

start = time.time()

while time.time() - start < DURATION:
    line = ser.readline().decode(errors="ignore").strip()
    if not line.isdigit():
        continue

    val = int(line)
    signal_buffer.append(val)

    rms_val = compute_rms(signal_buffer) if len(signal_buffer) == signal_buffer.maxlen else 0
    norm_rms = normalize(rms_val, baseline, mvc)

    # Clamp normalized RMS
    norm_rms = max(0.0, min(norm_rms, 1.0))

    timestamp = time.time() - start

    data_log.append([
        round(timestamp, 3),
        val,
        round(rms_val, 2),
        round(norm_rms, 2)
    ])

    print(
        f"t={timestamp:5.2f}s | "
        f"Raw={val:4d} | "
        f"RMS={rms_val:7.2f} | "
        f"Norm={norm_rms:.2f}"
    )

# -----------------------------
# STEP 4: SAVE TO CSV
# -----------------------------
with open(CSV_FILE, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["Time(s)", "Raw_EMG", "RMS", "Normalized_RMS"])
    writer.writerows(data_log)

ser.close()

print("\n✔ Recording complete")
print(f"✔ Data saved to {CSV_FILE}")
