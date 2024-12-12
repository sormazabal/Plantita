import asyncio
import struct
from datetime import datetime, timedelta
from bleak import BleakClient
import logging
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BLEMonitor:
    def __init__(self, line_bot, groq_client):
        # BLE settings
        self.DEVICE_ADDRESS = "19:9F:19:C0:C2:42"  # Replace with your device address
        self.CHARACTERISTIC_UUIDS = {
            "temperature": "2A6E",
            "humidity": "2A6F",
            "pressure": "2A6D",
            "soil_moisture": "2A70"
        }

        # Monitoring settings
        self.CHECK_INTERVAL = 60  # Check readings every 60 seconds
        self.NOTIFICATION_INTERVAL = timedelta(hours=4)  # Send notifications every 4 hours
        self.last_notification_time = datetime.now()

        # Default thresholds (can be updated from user_data)
        self.thresholds = {
            "temperature": {"min": 20, "max": 30},
            "humidity": {"min": 40, "max": 80},
            "soil_moisture": {"min": 30, "max": 70}
        }

        # Communication clients
        self.line_bot = line_bot
        self.groq_client = groq_client

    async def read_characteristic(self, client: BleakClient, uuid: str) -> float:
        """Read a characteristic from the BLE device and convert to float."""
        try:
            data = await client.read_gatt_char(uuid)
            return struct.unpack('<f', data)[0]
        except Exception as e:
            logger.error(f"Error reading characteristic {uuid}: {e}")
            return None

    async def read_sensor_data(self, client: BleakClient) -> Dict[str, float]:
        """Read all sensor data from the BLE device."""
        sensor_data = {}
        for sensor, uuid in self.CHARACTERISTIC_UUIDS.items():
            value = await self.read_characteristic(client, uuid)
            if value is not None:
                sensor_data[sensor] = value
        return sensor_data

    def check_thresholds(self, sensor_data: Dict[str, float]) -> bool:
        """Check if any sensor readings are outside their thresholds."""
        for sensor, value in sensor_data.items():
            if sensor in self.thresholds:
                threshold = self.thresholds[sensor]
                if value < threshold["min"] or value > threshold["max"]:
                    return True
        return False

    async def generate_care_advice(self, sensor_data: Dict[str, float], plant_name: str) -> str:
        """Generate plant care advice using Groq LLM."""
        try:
            prompt = f"""You are Plantita, an expert plant care advisor with a warm personality.
Current readings for {plant_name}:
    temperature={sensor_data['temperature']}°C,
    humidity={sensor_data['humidity']}%,
    soil_moisture={sensor_data['soil_moisture']}%

Ideal conditions:
- Temperature: {self.thresholds['temperature']['min']}°C to {self.thresholds['temperature']['max']}°C
- Humidity: {self.thresholds['humidity']['min']}% to {self.thresholds['humidity']['max']}%
- Soil Moisture: {self.thresholds['soil_moisture']['min']}% to {self.thresholds['soil_moisture']['max']}%

Please provide caring advice about what actions need to be taken. Keep it brief and friendly."""

            response = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-8b-8192"
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error generating care advice: {e}")
            return "I noticed some unusual readings from your plant. Please check on it!"

    async def monitor_loop(self):
        """Main monitoring loop."""
        while True:
            try:
                async with BleakClient(self.DEVICE_ADDRESS) as client:
                    logger.info("Connected to BLE device")

                    while True:
                        # Read sensor data
                        sensor_data = await self.read_sensor_data(client)
                        if not sensor_data:
                            logger.error("Failed to read sensor data")
                            break

                        logger.info(f"Sensor readings: {sensor_data}")

                        # Check if we should send a notification
                        current_time = datetime.now()
                        time_since_last = current_time - self.last_notification_time

                        if (self.check_thresholds(sensor_data) or
                                time_since_last >= self.NOTIFICATION_INTERVAL):

                            # Generate care advice
                            advice = await self.generate_care_advice(
                                sensor_data,
                                "your plant"  # This could be customized per user
                            )

                            # Send to all users (you might want to modify this to send to specific users)
                            try:
                                self.line_bot.broadcast_message(advice)
                                self.last_notification_time = current_time
                                logger.info("Notification sent successfully")
                            except Exception as e:
                                logger.error(f"Error sending notification: {e}")

                        # Wait before next check
                        await asyncio.sleep(self.CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"BLE connection error: {e}")
                # Wait before trying to reconnect
                await asyncio.sleep(5)
                continue