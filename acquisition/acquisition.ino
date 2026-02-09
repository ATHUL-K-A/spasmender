const int emgPin = A0;  // ADC pin
const int fs = 200;    // Sampling frequency (Hz)

unsigned long lastSample = 0;
unsigned long interval = 1000 / fs;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);           // 0–4095
  analogSetAttenuation(ADC_11db);     // Allows up to ~3.6V

}

void loop() {
  if (millis() - lastSample >= interval) {
    lastSample = millis();
    int emgValue = analogRead(emgPin);
    Serial.println(emgValue);
  }
}