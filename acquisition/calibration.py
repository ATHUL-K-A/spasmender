import serial
import time
import numpy as np
from collections import deque

# -------------------------------
# USER SETTINGS
# -------------------------------
PORT = "COM4"          # change if needed
BAUD = 115200
FS = 200               # sampling frequency
WINDOW_SEC = 1         # RMS window (seconds)
WINDOW_SIZE = FS * WINDOW_SEC

REST_TIME = 10          # seconds to record rest
MVC_TIME = 10           # seconds to record max contraction

# -------------------------------
# SERIAL SETUP
# -------------------------------
ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(2)
print("Connected to ESP32")

# -------------------------------
# BUFFERS
# -------------------------------
signal_buffer = deque(maxlen=WINDOW_SIZE)
rest_values = []
mvc_values = []

baseline = None
mvc = None

# -------------------------------
# FUNCTIONS
# -------------------------------
def compute_rms(data):
    data = np.array(data)
    return np.sqrt(np.mean(data ** 2))

def normalize(value, rest, mvc):
    if mvc - rest <= 0:
        return 0.0
    return (value - rest) / (mvc - rest)

# -------------------------------
# STEP 1: REST CALIBRATION
# -------------------------------
print("\n=== REST CALIBRATION ===")
print("Relax the muscle completely...")
print(f"Recording for {REST_TIME} seconds")

start = time.time()
while time.time() - start < REST_TIME:
    line = ser.readline().decode(errors="ignore").strip()
    if line.isdigit():
        rest_values.append(int(line))

baseline = np.mean(rest_values)
print(f"✔ Rest baseline = {baseline:.2f}")

# -------------------------------
# STEP 2: MVC CALIBRATION
# -------------------------------
print("\n=== MVC CALIBRATION ===")
print("Flex muscle as HARD as possible!")
print(f"Recording for {MVC_TIME} seconds")

start = time.time()
while time.time() - start < MVC_TIME:
    line = ser.readline().decode(errors="ignore").strip()
    if line.isdigit():
        mvc_values.append(int(line))

mvc = max(mvc_values)
print(f"✔ MVC value = {mvc}")

if mvc <= baseline:
    print("⚠️ Warning: MVC too low. Calibration may be unreliable.")

# -------------------------------
# STEP 3: REAL-TIME MONITORING
# -------------------------------
print("\n=== REAL-TIME EMG MONITORING ===")
print("Press Ctrl+C to stop\n")

try:
    while True:
        line = ser.readline().decode(errors="ignore").strip()
        if not line.isdigit():
            continue

        val = int(line)
        signal_buffer.append(val)

        if len(signal_buffer) < WINDOW_SIZE:
            continue

        rms_val = compute_rms(signal_buffer)
        norm_rms = normalize(rms_val, baseline, mvc)

        # Clamp
        norm_rms = max(0.0, min(norm_rms, 1.0))

        # Fatigue logic (simple & demo-safe)
        if norm_rms > 0.6:
            status = "⚠️ FATIGUE BUILDING"
        else:
            status = "✅ NORMAL"

        print(
            f"Raw: {val:4d} | "
            f"RMS: {rms_val:7.2f} | "
            f"Norm: {norm_rms:.2f} | {status}"
        )

except KeyboardInterrupt:
    print("\nStopped by user")
    ser.close()
