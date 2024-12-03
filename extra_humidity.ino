#include <Arduino_HS300x.h>     // Library for HS300x (temperature and humidity)
#include <Arduino_LPS22HB.h>    // Library for LPS22HB (barometric pressure)

// Define pins for the soil moisture sensor
const int soilMoistureAnalogPin = A0;  // Analog output pin for the sensor
const int soilMoistureDigitalPin = 2; // Optional: Digital output pin for threshold

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

  // Set up the soil moisture sensor digital pin (if used)
  pinMode(soilMoistureDigitalPin, INPUT);

  Serial.println("Sensors initialized successfully!");
}

void loop() {
  // Read temperature (in °C) and humidity (in %) from HS300x
  float temperature = HS300x.readTemperature();
  float humidity = HS300x.readHumidity();

  // Read barometric pressure (in hPa) from LPS22HB
  float pressure = BARO.readPressure();

  // Read soil moisture level from the analog pin
  int soilMoistureRaw = analogRead(soilMoistureAnalogPin);
  
  // Map the raw value (0-1023) to a percentage (0-100)
  float soilMoisturePercentage = map(soilMoistureRaw, 0, 1023, 0, 100);

  // Optional: Read soil moisture threshold from the digital pin
  int soilMoistureThreshold = digitalRead(soilMoistureDigitalPin);

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

  Serial.print("Soil Moisture (Analog): ");
  Serial.print(soilMoisturePercentage);
  Serial.println(" %");

  // Check if the digital pin indicates a dry condition
  if (soilMoistureThreshold == LOW) {
    Serial.println("Soil Moisture (Digital): DRY");
  } else {
    Serial.println("Soil Moisture (Digital): WET");
  }

  Serial.println("---------------------------");

  delay(1000);  // Wait a second before repeating
}

