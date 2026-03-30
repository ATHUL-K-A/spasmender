
// --- WiFi credentials ---
const char* SSID     = "Spasmender";
const char* PASSWORD = "scancheytuedukku";

// =============================================================
// EMG Signal Acquisition — Muscle BioAmp Candy
// Upside Down Labs | https://github.com/upsidedownlabs
// =============================================================
// Sampling runs on a dedicated FreeRTOS task pinned to Core 0.
// The HTTP server runs on Core 1 via loop().
// Both cores run simultaneously so handleClient() blocking
// never affects the 200 Hz sample rate.
// A 3-bit MUX (74HC4051) selects between 4 sensor channels.
// Flask polls /data every 100 ms to receive 20 batched samples.
// =============================================================

#include <WiFi.h>
#include <WebServer.h>
#include "esp_wifi.h"


// --- Sampling ---
#define SAMPLE_RATE  200
#define BATCH_SIZE   20
#define BAUD_RATE    115200
#define INPUT_PIN    34       // ADC1 pin — safe with WiFi active
#define BUFFER_SIZE  128

// --- MUX channel select pins ---
// Use GPIOs that are NOT ADC2 and NOT strapping pins
#define S0  26
#define S1  27
#define S2  14

// --- Globals ---
int circular_buffer[BUFFER_SIZE];
int data_index    = 0, sum = 0;
int currentChannel = 0;

// Batch buffer — written by sampling task, read by handleData()
struct Sample { int raw; int env; };
volatile Sample batch[BATCH_SIZE];
volatile int    batch_index = 0;

// Mutex to protect batch access between cores
SemaphoreHandle_t batchMutex;

// Flag to pause sampling briefly during channel switch
volatile bool pauseSampling = false;

WebServer server(80);

// =============================================================
// MUX channel selection
// Must NOT be called from the sampling task (Core 0) at the
// same time as a channel switch — pauseSampling guards this.
// =============================================================
void setChannel(int ch) {
    digitalWrite(S0,  ch        & 1);
    digitalWrite(S1, (ch >> 1)  & 1);
    digitalWrite(S2, (ch >> 2)  & 1);
    delayMicroseconds(200);   // allow MUX to settle
}

// =============================================================
// Envelope detection
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

// =============================================================
// Sampling task — runs on Core 0 at exactly 200 Hz
// Uses vTaskDelayUntil for precise 5 ms intervals
// =============================================================
void samplingTask(void* pvParameters) {
    const TickType_t period = pdMS_TO_TICKS(1000 / SAMPLE_RATE); // 5 ms
    TickType_t lastWakeTime = xTaskGetTickCount();

    for (;;) {
        vTaskDelayUntil(&lastWakeTime, period);

        // Skip sample during channel switch to avoid MUX glitch
        if (pauseSampling) continue;

        int raw     = analogRead(INPUT_PIN);
        int signal  = (int)EMGFilter((float)raw);
        int envelop = getEnvelop(abs(signal));

        if (xSemaphoreTake(batchMutex, 0) == pdTRUE) {
            int slot = batch_index % BATCH_SIZE;
            batch[slot].raw = signal;
            batch[slot].env = envelop;
            batch_index++;
            xSemaphoreGive(batchMutex);
        }
    }
}

// =============================================================
// HTTP handlers — run on Core 1 via loop()
// =============================================================

// Flask GETs this every ~100 ms to fetch 20 batched samples
void handleData() {
    server.sendHeader("Connection", "keep-alive");
    server.sendHeader("Keep-Alive", "timeout=5, max=1000");

    // Snapshot batch safely
    Sample snapshot[BATCH_SIZE];
    if (xSemaphoreTake(batchMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        for (int i = 0; i < BATCH_SIZE; i++) {
            snapshot[i].raw = batch[i].raw;
            snapshot[i].env = batch[i].env;
        }
        xSemaphoreGive(batchMutex);
    }

    String json = "[";
    for (int i = 0; i < BATCH_SIZE; i++) {
        json += "{\"raw\":" + String(snapshot[i].raw) +
                ",\"env\":" + String(snapshot[i].env) + "}";
        if (i < BATCH_SIZE - 1) json += ",";
    }
    json += "]";
    server.send(200, "application/json", json);
}

// Flask GETs this to switch the MUX channel
// e.g. GET /set_channel?ch=2
void handleSetChannel() {
    if (!server.hasArg("ch")) {
        server.send(400, "application/json", "{\"error\":\"Missing ch param\"}");
        return;
    }

    int ch = server.arg("ch").toInt();
    if (ch < 0 || ch > 3) {
        server.send(400, "application/json", "{\"error\":\"Invalid channel\"}");
        return;
    }

    // Pause sampling briefly so MUX glitch samples don't enter the batch
    pauseSampling = true;
    delayMicroseconds(500);   // wait for any in-progress sample to finish

    currentChannel = ch;
    setChannel(ch);

    // Reset envelope filter state and buffers on channel switch
    // so old channel's data doesn't bleed into the new channel
    if (xSemaphoreTake(batchMutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        sum = 0;
        data_index = 0;
        memset(circular_buffer, 0, sizeof(circular_buffer));
        for (int i = 0; i < BATCH_SIZE; i++) {
            batch[i].raw = 0;
            batch[i].env = 0;
        }
        batch_index = 0;
        xSemaphoreGive(batchMutex);
    }

    pauseSampling = false;

    Serial.print("Channel switched to: ");
    Serial.println(ch);
    server.send(200, "application/json",
                "{\"message\":\"Channel set to " + String(ch) + "\"}");
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

    // MUX select pins
    pinMode(S0, OUTPUT);
    pinMode(S1, OUTPUT);
    pinMode(S2, OUTPUT);
    setChannel(0);   // default to channel 0

    // ADC configuration for standard ESP32
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    // Connect to WiFi
    WiFi.begin(SSID, PASSWORD);
    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nConnected!");
    Serial.println("=========================");
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
    Serial.println("Update ESP32_IP in Flask!");
    Serial.println("=========================");

    // Disable WiFi power saving
    WiFi.setSleep(false);
    esp_wifi_set_ps(WIFI_PS_NONE);

    // Create mutex for batch buffer access between cores
    batchMutex = xSemaphoreCreateMutex();

    // Register HTTP endpoints and start server
    server.on("/data",        HTTP_GET, handleData);
    server.on("/set_channel", HTTP_GET, handleSetChannel);
    server.on("/ping",        HTTP_GET, handlePing);
    server.begin();
    Serial.println("HTTP server started");

    // Start sampling task on Core 0
    // HTTP server runs on Core 1 via loop()
    xTaskCreatePinnedToCore(
        samplingTask,   // task function
        "EMG_Sample",  // task name
        4096,          // stack size in bytes
        NULL,          // parameters
        1,             // priority
        NULL,          // task handle
        0              // core 0
    );
    Serial.println("Sampling task started on Core 0 at 200 Hz");
}

// =============================================================
// Main loop — Core 1, handles HTTP only
// =============================================================
void loop() {
    server.handleClient();
}
