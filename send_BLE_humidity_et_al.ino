#include <Arduino_HS300x.h>     // Library for HS300x (temperature and humidity)
#include <Arduino_LPS22HB.h>    // Library for LPS22HB (barometric pressure)
#include <ArduinoBLE.h>         // Library for BLE functionality

// Define pins for the soil moisture sensor
const int soilMoistureAnalogPin = A0;  // Analog output pin for the sensor
const int soilMoistureDigitalPin = 2; // Optional: Digital output pin for threshold

// BLE service and characteristic
BLEService sensorService("180A"); // Custom BLE service
BLEFloatCharacteristic temperatureCharacteristic("2A6E", BLERead | BLENotify); // Temperature
BLEFloatCharacteristic humidityCharacteristic("2A6F", BLERead | BLENotify);    // Humidity
BLEFloatCharacteristic pressureCharacteristic("2A6D", BLERead | BLENotify);    // Pressure
BLEFloatCharacteristic soilMoistureCharacteristic("2A70", BLERead | BLENotify); // Soil moisture

void setup() {
  Serial.begin(9600);
  delay(1000); // Allow time for Serial to initialize

  // Initialize BLE
  if (!BLE.begin()) {
    Serial.println("Failed to initialize BLE!");
    while (1);
  }

  // Set up BLE device information
  BLE.setLocalName("SensorDevice");
  BLE.setAdvertisedService(sensorService);

  // Add characteristics to the service
  sensorService.addCharacteristic(temperatureCharacteristic);
  sensorService.addCharacteristic(humidityCharacteristic);
  sensorService.addCharacteristic(pressureCharacteristic);
  sensorService.addCharacteristic(soilMoistureCharacteristic);

  // Add the service to BLE
  BLE.addService(sensorService);

  // Start advertising
  BLE.advertise();
  Serial.println("BLE device initialized and advertising...");

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
  // Handle BLE events
  BLEDevice central = BLE.central();
  if (central) {
    Serial.print("Connected to central: ");
    Serial.println(central.address());

    while (central.connected()) {
      // Read sensor data
      float temperature = HS300x.readTemperature();
      float humidity = HS300x.readHumidity();
      float pressure = BARO.readPressure();
      int soilMoistureRaw = analogRead(soilMoistureAnalogPin);
      float soilMoisturePercentage = abs(100 - map(soilMoistureRaw, 200, 1023, 0, 100));

      // Update BLE characteristics
      temperatureCharacteristic.writeValue(temperature);
      humidityCharacteristic.writeValue(humidity);
      pressureCharacteristic.writeValue(pressure);
      soilMoistureCharacteristic.writeValue(soilMoisturePercentage);

      // Output to Serial for debugging
      Serial.print("Temperature: ");
      Serial.print(temperature);
      Serial.println(" °C");

      Serial.print("Humidity: ");
      Serial.print(humidity);
      Serial.println(" %");

      Serial.print("Pressure: ");
      Serial.print(pressure);
      Serial.println(" hPa");

      Serial.print("Soil Moisture: ");
      Serial.print(soilMoisturePercentage);
      Serial.println(" %");

      Serial.println("---------------------------");

      delay(1000); // Wait before sending next data
    }

    Serial.println("Disconnected from central.");
  }
}
