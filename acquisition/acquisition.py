import serial
import numpy as np
import time

ser = serial.Serial('COM4', 115200)  # change COM port
fs = 200
window_size = 200  # 1 seconds

buffer = []

def rms(signal):
    return np.sqrt(np.mean(np.square(signal)))

while True:
    line = ser.readline().decode().strip()
    if line.isdigit():
        buffer.append(int(line))

    if len(buffer) >= window_size:
        window = np.array(buffer[-window_size:])
        rms_val = rms(window)
        print("RMS:", rms_val)
    if    time.sleep(10)
baseline_rms = 200  # measure when muscle is fresh

if rms_val > baseline_rms * 1.3:
    print("⚠️ Muscle Fatigue Detected")
else:
    print("✅ Normal")
