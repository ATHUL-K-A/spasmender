#include <Wire.h>

#define MPU9250_ADDR 0x68

int16_t gyroX, gyroY, gyroZ;

void setup() {
  Serial.begin(115200);

  Wire.begin(4, 5);  // SDA = D4, SCL = D5

  // Wake up MPU9250
  Wire.beginTransmission(MPU9250_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  Wire.endTransmission(true);

  Serial.println("MPU9250 Ready");
}

void loop() {

  // Read Gyroscope registers
  Wire.beginTransmission(MPU9250_ADDR);
  Wire.write(0x43);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU9250_ADDR, 6, true);

  gyroX = Wire.read() << 8 | Wire.read();
  gyroY = Wire.read() << 8 | Wire.read();
  gyroZ = Wire.read() << 8 | Wire.read();

  Serial.print("Gyro X: ");
  Serial.print(gyroX);
  Serial.print(" | Gyro Y: ");
  Serial.print(gyroY);
  Serial.print(" | Gyro Z: ");
  Serial.println(gyroZ);

  delay(300);
}