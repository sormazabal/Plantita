#include <Arduino_HS300x.h>     // Library for HS300x (temperature and humidity)
#include <Arduino_LPS22HB.h>    // Library for LPS22HB (barometric pressure)

void setup() {
  Serial.begin(9600);
  delay(1000);  // Allow time for Serial to initialize

  // Initialize the HS300x sensor for temperature and humidity
  if (!HS300x.begin()) {
    Serial.println("Failed to initialize HS300x sensor!");
    while (1);
  }

  // Initialize the LPS22HB sensor for barometric pressure
  if (!BARO.begin()) {
    Serial.println("Failed to initialize LPS22HB sensor!");
    while (1);
  }

  Serial.println("Sensors initialized successfully!");
}

void loop() {
  // Read temperature (in °C) and humidity (in %) from HS300x
  float temperature = HS300x.readTemperature();
  float humidity = HS300x.readHumidity();

  // Read barometric pressure (in hPa) from LPS22HB
  float pressure = BARO.readPressure();

  // Output the readings
  Serial.print("Temperature: ");
  Serial.print(temperature);
  Serial.println(" °C");

  Serial.print("Humidity: ");
  Serial.print(humidity);
  Serial.println(" %");

  Serial.print("Pressure: ");
  Serial.print(pressure);
  Serial.println(" hPa");

  Serial.println("---------------------------");

  delay(1000);  // Wait a second before repeating
}
