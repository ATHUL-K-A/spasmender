// =============================================================
// EMG Signal Acquisition — Muscle BioAmp Candy
// Upside Down Labs | https://github.com/upsidedownlabs
// =============================================================
// Acquires EMG signal via analog input, applies a Band-Pass
// Butterworth filter (74.5–149.5 Hz), computes the signal
// envelope, and serves batches of 20 samples over HTTP at 10
// requests/second (200 Hz / batch of 20). Flask polls /data.
// A 3-bit MUX (74HC4051) selects between 4 sensor channels.
// =============================================================

#include <WiFi.h>
#include <WebServer.h>
#include "esp_wifi.h"       // needed for esp wifi

// --- WiFi credentials ---
const char* SSID     = "Spasmender";
const char* PASSWORD = "scancheytuedukku";

// --- Static IP configuration ---
// Change these to match your network. Pick a free IP outside your
// router's DHCP range to avoid conflicts.
IPAddress LOCAL_IP(10, 159, 219, 196);
IPAddress GATEWAY(10, 159, 1, 1);
IPAddress SUBNET(255, 255, 255, 0);

// --- Sampling ---
#define SAMPLE_RATE  200        // Hz
#define BATCH_SIZE   20         // samples per HTTP response → 10 req/s
#define BAUD_RATE    115200
#define INPUT_PIN    A0
#define BUFFER_SIZE  128

// --- MUX channel select pins ---
#define S0  4
#define S1  5
#define S2  6

// --- Globals ---
int currentChannel = 0;
int circular_buffer[BUFFER_SIZE];
int data_index = 0, sum = 0;

// Batch buffer — filled by the sampling timer, read by handleData()
struct Sample { int raw; int env; };
volatile Sample batch[BATCH_SIZE];
volatile int    batch_index = 0;   // counts samples since last reset

WebServer server(80);

// =============================================================
// MUX channel selection
// =============================================================
void setChannel(int ch) {
    digitalWrite(S0,  ch        & 1);
    digitalWrite(S1, (ch >> 1)  & 1);
    digitalWrite(S2, (ch >> 2)  & 1);
    delayMicroseconds(200);
}

// =============================================================
// HTTP handlers
// =============================================================

// Flask GETs this every ~100 ms to fetch 20 batched samples
void handleData() {
    server.sendHeader("Connection", "keep-alive");
    server.sendHeader("Keep-Alive", "timeout=5, max=1000");

    String json = "[";
    for (int i = 0; i < BATCH_SIZE; i++) {
        json += "{\"raw\":" + String(batch[i].raw) +
                ",\"env\":" + String(batch[i].env) + "}";
        if (i < BATCH_SIZE - 1) json += ",";
    }
    json += "]";
    server.send(200, "application/json", json);
}

// Flask GETs this to switch the MUX channel
void handleSetChannel() {
    if (server.hasArg("ch")) {
        int ch = server.arg("ch").toInt();
        if (ch >= 0 && ch <= 3) {
            currentChannel = ch;
            setChannel(ch);
            server.send(200, "application/json",
                        "{\"message\":\"Channel set to " + String(ch) + "\"}");
            Serial.print("Channel switched to: ");
            Serial.println(ch);
        } else {
            server.send(400, "application/json", "{\"error\":\"Invalid channel\"}");
        }
    } else {
        server.send(400, "application/json", "{\"error\":\"Missing ch param\"}");
    }
}

// Health check — Flask pings this before starting
void handlePing() {
    server.send(200, "application/json", "{\"status\":\"ok\"}");
}

// =============================================================
// Setup
// =============================================================
void setup() {
    Serial.begin(BAUD_RATE);
    pinMode(S0, OUTPUT);
    pinMode(S1, OUTPUT);
    pinMode(S2, OUTPUT);

    // Apply static IP before connecting
    WiFi.config(LOCAL_IP, GATEWAY, SUBNET);
    WiFi.begin(SSID, PASSWORD);

    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nConnected! IP: " + WiFi.localIP().toString());

    // Disable WiFi power saving — prevents radio sleep causing signal gaps
    WiFi.setSleep(false);
    esp_wifi_set_ps(WIFI_PS_NONE);

    // Register endpoints and start HTTP server
    server.on("/data",        HTTP_GET, handleData);
    server.on("/set_channel", HTTP_GET, handleSetChannel);
    server.on("/ping",        HTTP_GET, handlePing);
    server.begin();
    Serial.println("HTTP server started — batch size: " + String(BATCH_SIZE));

    setChannel(0);
}

// =============================================================
// Main loop — samples EMG at SAMPLE_RATE Hz, batches results
// =============================================================
void loop() {
    server.handleClient();

    // Optional: serial channel switching for debugging
    if (Serial.available()) {
        String input = Serial.readStringUntil('\n');
        input.trim();
        int ch = input.toInt();
        if (ch >= 0 && ch <= 3 && ch != currentChannel) {
            currentChannel = ch;
            setChannel(currentChannel);
            Serial.print("Switched to channel: ");
            Serial.println(currentChannel);
        }
    }

    // Precise sampling timer
    static unsigned long past = 0;
    unsigned long present  = micros();
    unsigned long interval = present - past;
    past = present;

    static long timer = 0;
    timer -= interval;

    if (timer < 0) {
        timer += 1000000 / SAMPLE_RATE;

        int raw     = analogRead(INPUT_PIN);
        int signal  = (int)EMGFilter((float)raw);
        int envelop = getEnvelop(abs(signal));

        // Write into circular batch — overwrites oldest slot
        int slot = batch_index % BATCH_SIZE;
        batch[slot].raw = signal;
        batch[slot].env = envelop;
        batch_index++;
    }
}

// =============================================================
// Envelope detection — moving average of rectified signal
// =============================================================
int getEnvelop(int abs_emg) {
    sum -= circular_buffer[data_index];
    sum += abs_emg;
    circular_buffer[data_index] = abs_emg;
    data_index = (data_index + 1) % BUFFER_SIZE;
    return (sum / BUFFER_SIZE) * 2;
}

// =============================================================
// Band-Pass Butterworth IIR Filter
// Order 4, second-order sections | 74.5–149.5 Hz @ 200 Hz Fs
// Generated via filter_gen.py (CMU 16-223)
// =============================================================
float EMGFilter(float input) {
    float output = input;
    { static float z1, z2;
      float x = output - 0.05159732f*z1 - 0.36347401f*z2;
      output   = 0.01856301f*x + 0.03712602f*z1 + 0.01856301f*z2;
      z2=z1; z1=x; }
    { static float z1, z2;
      float x = output + 0.53945795f*z1 - 0.39764934f*z2;
      output   = x - 2.0f*z1 + z2;
      z2=z1; z1=x; }
    { static float z1, z2;
      float x = output - 0.47319594f*z1 - 0.70744137f*z2;
      output   = x + 2.0f*z1 + z2;
      z2=z1; z1=x; }
    { static float z1, z2;
      float x = output + 1.00211112f*z1 - 0.74520226f*z2;
      output   = x - 2.0f*z1 + z2;
      z2=z1; z1=x; }
    return output;
}
