#include <Arduino_HS300x.h>
#include <Arduino_LPS22HB.h>
#include <ArduinoBLE.h>

// Define pins for the soil moisture sensor
const int soilMoistureAnalogPin = A0;

// BLE service and characteristic UUIDs
BLEService sensorService("180A");  // Environmental Sensing service
BLEFloatCharacteristic temperatureCharacteristic("2A6E", BLERead | BLENotify);
BLEFloatCharacteristic humidityCharacteristic("2A6F", BLERead | BLENotify);
BLEFloatCharacteristic pressureCharacteristic("2A6D", BLERead | BLENotify);
BLEFloatCharacteristic soilMoistureCharacteristic("2A70", BLERead | BLENotify);

// Variables to track last update time
unsigned long lastUpdateTime = 0;
const unsigned long updateInterval = 1000;  // Update every 1 second

void setup() {
  Serial.begin(9600);
  // Only wait for Serial if USB is connected
  #ifdef ARDUINO_USB_CDC_ON_BOOT
    const unsigned long startTime = millis();
    while (!Serial && millis() - startTime < 3000);  // Wait up to 3 seconds
  #endif
  
  Serial.println("Starting BLE Plant Monitor...");
  
  // Initialize BLE
  if (!BLE.begin()) {
    Serial.println("Failed to initialize BLE!");
    while (1);
  }

  // Initialize sensors
  if (!HS300x.begin()) {
    Serial.println("Failed to initialize HS300x sensor!");
    while (1);
  }

  if (!BARO.begin()) {
    Serial.println("Failed to initialize LPS22HB sensor!");
    while (1);
  }

  // Set up BLE device information
  BLE.setLocalName("Plant Monitor");
  BLE.setDeviceName("Plant Monitor");
  BLE.setAdvertisedService(sensorService);
  
  // Add characteristics to the service
  sensorService.addCharacteristic(temperatureCharacteristic);
  sensorService.addCharacteristic(humidityCharacteristic);
  sensorService.addCharacteristic(pressureCharacteristic);
  sensorService.addCharacteristic(soilMoistureCharacteristic);
  
  // Add the service
  BLE.addService(sensorService);

  // Set initial values
  temperatureCharacteristic.writeValue(0.0);
  humidityCharacteristic.writeValue(0.0);
  pressureCharacteristic.writeValue(0.0);
  soilMoistureCharacteristic.writeValue(0.0);

  // Start advertising
  BLE.advertise();
  Serial.println("BLE Plant Monitor is now advertising!");
}

void loop() {
  BLEDevice central = BLE.central();
  
  if (central) {
    Serial.print("Connected to central: ");
    Serial.println(central.address());

    while (central.connected()) {
      // Check if it's time to update
      if (millis() - lastUpdateTime >= updateInterval) {
        updateSensorValues();
        lastUpdateTime = millis();
      }
    }
    
    Serial.println("Disconnected from central");
  }
}

void updateSensorValues() {
  // Read sensors
  float temperature = HS300x.readTemperature();
  float humidity = HS300x.readHumidity();
  float pressure = BARO.readPressure();
  
  // Read soil moisture and convert to percentage
  int soilMoistureRaw = analogRead(soilMoistureAnalogPin);
  float soilMoisturePercentage = map(soilMoistureRaw, 200, 1023, 100, 0);

  // Update characteristics and check for success
  bool tempSuccess = temperatureCharacteristic.writeValue(temperature);
  bool humSuccess = humidityCharacteristic.writeValue(humidity);
  bool pressSuccess = pressureCharacteristic.writeValue(pressure);
  bool moistSuccess = soilMoistureCharacteristic.writeValue(soilMoisturePercentage);

  // Debug output
  Serial.println("\n-------- Sensor Updates --------");
  Serial.print("Temperature: "); Serial.print(temperature); 
  Serial.print(" °C (Update "); Serial.print(tempSuccess ? "success" : "failed"); Serial.println(")");
  
  Serial.print("Humidity: "); Serial.print(humidity);
  Serial.print(" % (Update "); Serial.print(humSuccess ? "success" : "failed"); Serial.println(")");
  
  Serial.print("Pressure: "); Serial.print(pressure);
  Serial.print(" kPa (Update "); Serial.print(pressSuccess ? "success" : "failed"); Serial.println(")");
  
  Serial.print("Soil Moisture: "); Serial.print(soilMoisturePercentage);
  Serial.print(" % (Update "); Serial.print(moistSuccess ? "success" : "failed"); Serial.println(")");
  
  Serial.println("------------------------------");
}