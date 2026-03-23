// EMG Envelop - Muscle BioAmp Candy
// ESP32 as HTTP server — Flask polls /data from ESP32's IP

#include <WiFi.h>
#include <WebServer.h>

// WiFi credentials
const char* ssid = "Galaxy xxx";
const char* password = "scancheytuedukku";

#define SAMPLE_RATE 200
#define BAUD_RATE 115200
#define INPUT_PIN A0
#define BUFFER_SIZE 128
#define S0 4
#define S1 5
#define S2 6

int currentChannel = 0;
int circular_buffer[BUFFER_SIZE];
int data_index, sum;

// Latest computed values — Flask reads these
volatile int latest_signal = 0;
volatile int latest_envelop = 0;

WebServer server(80);  // ESP32 HTTP server on port 80

// ----------------------------------------------------------------
// HTTP HANDLERS
// ----------------------------------------------------------------

// Flask GETs this to fetch the latest EMG sample
void handleData() {
  String json = "{\"raw\":" + String(latest_signal) +
                ",\"env\":" + String(latest_envelop) + "}";
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

// Simple health check
void handlePing() {
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

// ----------------------------------------------------------------
void setup() {
  Serial.begin(BAUD_RATE);
  pinMode(S0, OUTPUT);
  pinMode(S1, OUTPUT);
  pinMode(S2, OUTPUT);

  // Connect to WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());  // <-- NOTE THIS IP, put it in Flask

  // Register endpoints
  server.on("/data", HTTP_GET, handleData);
  server.on("/set_channel", HTTP_GET, handleSetChannel);
  server.on("/ping", HTTP_GET, handlePing);
  server.begin();
  Serial.println("HTTP server started on port 80");

  setChannel(0);
}

// ----------------------------------------------------------------
void setChannel(int ch) {
  digitalWrite(S0, ch & 1);
  digitalWrite(S1, (ch >> 1) & 1);
  digitalWrite(S2, (ch >> 2) & 1);
  delayMicroseconds(200);
}

// ----------------------------------------------------------------
void loop() {
  server.handleClient();  // Handle any incoming HTTP requests

  // Handle serial channel switch (optional, keep for debugging)
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

  // Elapsed time
  static unsigned long past = 0;
  unsigned long present = micros();
  unsigned long interval = present - past;
  past = present;

  // Timer
  static long timer = 0;
  timer -= interval;

  if (timer < 0) {
    timer += 1000000 / SAMPLE_RATE;
    int sensor_value = analogRead(INPUT_PIN);
    int signal = EMGFilter(sensor_value);
    int envelop = getEnvelop(abs(signal));

    // Update latest values for Flask to read
    latest_signal = signal;
    latest_envelop = envelop;
  }
}

// ----------------------------------------------------------------
// Envelope detection
int getEnvelop(int abs_emg) {
  sum -= circular_buffer[data_index];
  sum += abs_emg;
  circular_buffer[data_index] = abs_emg;
  data_index = (data_index + 1) % BUFFER_SIZE;
  return (sum / BUFFER_SIZE) * 2;
}

// ----------------------------------------------------------------
// Band-Pass Butterworth IIR digital filter
// Sampling rate: 200 Hz, frequency: [74.5, 149.5] Hz
float EMGFilter(float input) {
  float output = input;
  {
    static float z1, z2;
    float x = output - 0.05159732 * z1 - 0.36347401 * z2;
    output = 0.01856301 * x + 0.03712602 * z1 + 0.01856301 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - -0.53945795 * z1 - 0.39764934 * z2;
    output = 1.00000000 * x + -2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - 0.47319594 * z1 - 0.70744137 * z2;
    output = 1.00000000 * x + 2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - -1.00211112 * z1 - 0.74520226 * z2;
    output = 1.00000000 * x + -2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  return output;
}
