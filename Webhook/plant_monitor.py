import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import logging
from bleak import BleakClient, BleakScanner
import struct
from groq import Groq
from collections import deque

# Constants
TEMP_CHARACTERISTIC_UUID = "00002a6e-0000-1000-8000-00805f9b34fb"
HUMIDITY_CHARACTERISTIC_UUID = "00002a6f-0000-1000-8000-00805f9b34fb"
SOIL_MOISTURE_CHARACTERISTIC_UUID = "00002a70-0000-1000-8000-00805f9b34fb"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PlantMonitor:
    def __init__(self, user_data_folder, line_bot_api):
        self.user_data_folder = Path(user_data_folder)
        self.line_bot_api = line_bot_api
        self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        self.latest_readings = {}
        self.reading_history = {}
        self.connected = False

    async def find_arduino(self):
        """Scan for and find the Arduino device"""
        devices = await BleakScanner.discover()
        for d in devices:
            if d.name and "Plant" in d.name:
                return d.address
        return None

    def get_thresholds_from_llm(self, plant_name):
        """Get plant care thresholds from LLM based on scientific name"""
        prompt = (
            f"As a plant expert, provide the ideal growing conditions for {plant_name} in this exact format:\n"
            f"temperature_min\ttemperature_max\thumidity_min\thumidity_max\tlight_min\tlight_max\tmoisture_min\tmoisture_max\n"
            f"Only respond with tab-separated values in Celsius for temperature, percentage for others. No explanations."
        )

        response = self.client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        values = response.choices[0].message.content.strip().split('\t')
        return {
            'temperature': {'min': float(values[0]), 'max': float(values[1])},
            'humidity': {'min': float(values[2]), 'max': float(values[3])},
            'light': {'min': float(values[4]), 'max': float(values[5])},
            'moisture': {'min': float(values[6]), 'max': float(values[7])}
        }

    def update_plant_data(self, user_id, current_reading):
        """Update plant data with new sensor readings"""
        try:
            file_path = self.user_data_folder / f'plant_data_{user_id}.json'
            if not file_path.exists():
                return

            with open(file_path, 'r+') as f:
                data = json.load(f)

                # Initialize history if not present
                if 'reading_history' not in data:
                    data['reading_history'] = []

                # Add timestamp to current reading
                current_reading['timestamp'] = datetime.now().isoformat()

                # Add new reading
                data['reading_history'].append(current_reading)

                # Remove readings older than 1 week
                week_ago = datetime.now() - timedelta(days=7)
                data['reading_history'] = [
                    reading for reading in data['reading_history']
                    if datetime.fromisoformat(reading['timestamp']) > week_ago
                ]

                # Update latest reading
                data['latest_reading'] = current_reading

                # Reset file pointer and write updated data
                f.seek(0)
                json.dump(data, f, indent=4)
                f.truncate()

                # Check thresholds and send notification if needed
                self.check_thresholds(user_id, current_reading, data['thresholds'])

    def check_thresholds(self, user_id, reading, thresholds):
        """Check if readings are outside thresholds and notify user"""
        alerts = []

        for metric, value in reading.items():
            if metric in thresholds and isinstance(value, (int, float)):
                threshold = thresholds[metric]
                if value < threshold['min'] or value > threshold['max']:
                    alerts.append(f"{metric}: {value}")

        if alerts:
            self.send_alert(user_id, alerts, reading, thresholds)

    def send_alert(self, user_id, alerts, reading, thresholds):
        """Send alert message using LLM for natural language"""
        prompt = (
            f"You are Plantita, a caring plant expert. Create a friendly but urgent alert message about these concerning plant conditions:\n"
            f"Current readings: {reading}\n"
            f"Alerts: {alerts}\n"
            f"Ideal ranges: {thresholds}\n"
            f"Make it sound caring but emphasize the importance of addressing these issues."
        )

        response = self.client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        alert_message = response.choices[0].message.content

        # Send message using LINE Bot API
        from linebot.v3.messaging import TextMessage
        self.line_bot_api.push_message(
            user_id,
            messages=[TextMessage(text=alert_message)]
        )

    def get_latest_status(self, user_id):
        """Get natural language status update for plant"""
        try:
            file_path = self.user_data_folder / f'plant_data_{user_id}.json'
            if not file_path.exists():
                return "No plant registered yet!"

            with open(file_path, 'r') as f:
                data = json.load(f)

            if 'latest_reading' not in data:
                return "No readings available yet!"

            prompt = (
                f"You are Plantita, a caring plant expert. Create a friendly status update for {data['nickname']} ({data['scientific_name']}):\n"
                f"Current readings: {data['latest_reading']}\n"
                f"Ideal ranges: {data['thresholds']}\n"
                f"Include both the current status and any care suggestions if needed."
            )

            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-8b-8192"
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error getting status: {str(e)}")
            return "Sorry, I'm having trouble checking your plant right now!"

    async def notification_handler(self, sender, data):
        """Handle incoming notifications from the device"""
        try:
            value = struct.unpack('<f', data)[0]
            char_uuid = sender.uuid.lower()

            if char_uuid == TEMP_CHARACTERISTIC_UUID.lower():
                self.latest_readings['temperature'] = value
            elif char_uuid == HUMIDITY_CHARACTERISTIC_UUID.lower():
                self.latest_readings['humidity'] = value
            elif char_uuid == SOIL_MOISTURE_CHARACTERISTIC_UUID.lower():
                self.latest_readings['moisture'] = value

        except Exception as e:
            logger.error(f"Error processing notification: {e}")

    async def start_monitoring(self):
        """Start the monitoring process"""
        while True:
            try:
                if not self.connected:
                    arduino_address = await self.find_arduino()
                    if not arduino_address:
                        logger.error("Arduino not found")
                        await asyncio.sleep(60)
                        continue

                    async with BleakClient(arduino_address) as client:
                        self.connected = True
                        logger.info("Connected to Arduino")

                        # Enable notifications
                        characteristics = [
                            TEMP_CHARACTERISTIC_UUID,
                            HUMIDITY_CHARACTERISTIC_UUID,
                            SOIL_MOISTURE_CHARACTERISTIC_UUID
                        ]

                        for uuid in characteristics:
                            await client.start_notify(uuid, self.notification_handler)

                        while self.connected:
                            # Update readings every minute
                            await asyncio.sleep(60)

                            # Update data for all registered users
                            for user_file in self.user_data_folder.glob('plant_data_*.json'):
                                user_id = user_file.stem.split('_')[2]
                                self.update_plant_data(user_id, self.latest_readings.copy())

            except Exception as e:
                logger.error(f"Connection error: {e}")
                self.connected = False
                await asyncio.sleep(60)  # Wait before retrying