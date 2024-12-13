from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
from linebot.v3.messaging import TextMessage
from linebot.v3.exceptions import InvalidSignatureError
import os
from dotenv import load_dotenv
import tempfile
from datetime import datetime, timedelta
import logging
import json
import requests
import base64
from groq import Groq
import asyncio
import threading
from bleak import BleakClient, BleakScanner
import struct
from pathlib import Path
import time
from bleak.exc import BleakDBusError

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)

# LINE API v3 setup
configuration = Configuration(access_token=os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# Create API clients
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
blob_api = MessagingApiBlob(api_client)

# Create directories for storing images and user data
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'plant_images')
USER_DATA_FOLDER = os.path.join(os.getcwd(), 'user_data')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(USER_DATA_FOLDER, exist_ok=True)
logger.info(f"Images will be saved to: {UPLOAD_FOLDER}")

# Store user states
user_states = {}

# Bluetooth characteristics UUIDs
TEMP_CHARACTERISTIC_UUID = "00002a6e-0000-1000-8000-00805f9b34fb"
HUMIDITY_CHARACTERISTIC_UUID = "00002a6f-0000-1000-8000-00805f9b34fb"
SOIL_MOISTURE_CHARACTERISTIC_UUID = "00002a70-0000-1000-8000-00805f9b34fb"

# Global variables for Bluetooth monitoring
latest_readings = {}
bluetooth_connected = False

async def find_arduino():
    """Scan for and find the Arduino device"""
    devices = await BleakScanner.discover()
    for d in devices:
        if d.name and "Plant" in d.name:
            return d.address
    return None

async def notification_handler(sender, data):
    """Handle incoming notifications from the device"""
    try:
        # Get current timestamp for logging
        timestamp = time.strftime('%H:%M:%S')

        # Log raw data for debugging
        logger.debug(f"Raw data received at {timestamp}: {data.hex()}")

        # Unpack the data - using little endian format ('<') for 32-bit float
        try:
            value = struct.unpack('<f', data)[0]
        except struct.error:
            logger.error(f"Error unpacking data: {data.hex()}")
            return

        char_uuid = sender.uuid.lower()

        # Round the value to 1 decimal place for consistency
        value = round(value, 1)

        if char_uuid == TEMP_CHARACTERISTIC_UUID.lower():
            latest_readings['temperature'] = value
            logger.debug(f"Temperature: {value}°C")
        elif char_uuid == HUMIDITY_CHARACTERISTIC_UUID.lower():
            latest_readings['humidity'] = value
            logger.debug(f"Humidity: {value}%")
        elif char_uuid == SOIL_MOISTURE_CHARACTERISTIC_UUID.lower():
            latest_readings['moisture'] = value
            logger.debug(f"Soil Moisture: {value}%")

        logger.debug(f"Current readings: {latest_readings}")

    except Exception as e:
        logger.error(f"Error processing notification: {str(e)}")
        logger.error(f"Data that caused error: {data.hex() if data else 'No data'}")
        logger.error(f"Characteristic UUID: {sender.uuid}")

def get_thresholds_from_llm(plant_name):
    """Get plant care thresholds from LLM based on scientific name"""
    try:
        client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        prompt = (
            f"As a plant expert, provide the ideal growing conditions for {plant_name} in this exact format:\n"
            f"temperature_min\ttemperature_max\thumidity_min\thumidity_max\tlight_min\tlight_max\tmoisture_min\tmoisture_max\n"
            f"Only respond with tab-separated values in Celsius for temperature, percentage for others. No explanations."
        )

        response = client.chat.completions.create(
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
    except Exception as e:
        logger.error(f"Error getting thresholds from LLM: {str(e)}")
        return None

async def start_monitoring():
    """Start the monitoring process"""
    global bluetooth_connected
    while True:
        try:
            if not bluetooth_connected:
                logger.info("Scanning for Arduino Plant Monitor...")
                arduino_address = await find_arduino()
                if not arduino_address:
                    logger.error("Arduino not found")
                    await asyncio.sleep(10)  # Shorter retry interval for initial connection
                    continue

                logger.info(f"Attempting to connect to Arduino at {arduino_address}")
                async with BleakClient(arduino_address, timeout=20) as client:
                    try:
                        # Check if connection is successful
                        if not client.is_connected:
                            logger.error("Failed to connect")
                            continue

                        bluetooth_connected = True
                        logger.info("Successfully connected to Arduino")

                        # Get device info
                        services = await client.get_services()
                        logger.info("Services discovered:")
                        for service in services:
                            logger.info(f"Service: {service.uuid}")
                            for char in service.characteristics:
                                logger.info(f"  Characteristic: {char.uuid}")
                                logger.info(f"  Properties: {char.properties}")

                        # Enable notifications
                        characteristics = [
                            TEMP_CHARACTERISTIC_UUID,
                            HUMIDITY_CHARACTERISTIC_UUID,
                            SOIL_MOISTURE_CHARACTERISTIC_UUID
                        ]

                        for uuid in characteristics:
                            try:
                                await client.start_notify(uuid, notification_handler)
                                logger.info(f"Enabled notifications for {uuid}")
                            except Exception as e:
                                logger.error(f"Failed to enable notifications for {uuid}: {e}")

                        logger.info("All notifications enabled, entering monitoring loop")

                        # Monitoring loop
                        while bluetooth_connected and client.is_connected:
                            await asyncio.sleep(60)  # Wait for 1 minute
                            update_all_plant_data()
                            logger.debug(f"Current readings: {latest_readings}")

                    except BleakDBusError as e:
                        logger.error(f"Bluetooth communication error: {e}")
                        bluetooth_connected = False
                    except Exception as e:
                        logger.error(f"Unexpected error during monitoring: {e}")
                        bluetooth_connected = False

        except Exception as e:
            logger.error(f"Connection error: {e}")
            bluetooth_connected = False
            await asyncio.sleep(30)  # Wait before retrying connection

        finally:
            bluetooth_connected = False
            logger.info("Connection lost or monitoring stopped, will retry...")

def start_monitoring_thread():
    """Start the monitoring loop in a background thread"""
    asyncio.run(start_monitoring())

def update_all_plant_data():
    """Update data for all registered plants"""
    try:
        for user_file in Path(USER_DATA_FOLDER).glob('plant_data_*.json'):
            user_id = user_file.stem.split('_')[2]
            update_plant_data(user_id)
    except Exception as e:
        logger.error(f"Error updating plant data: {str(e)}")

def update_plant_data(user_id):
    """Update plant data with new sensor readings"""
    try:
        file_path = os.path.join(USER_DATA_FOLDER, f'plant_data_{user_id}.json')
        if not os.path.exists(file_path):
            return

        with open(file_path, 'r+') as f:
            data = json.load(f)

            # Check if it's time to update based on frequency
            last_check = datetime.fromisoformat(data.get('last_check_time', '2000-01-01T00:00:00'))
            frequency_minutes = data.get('monitoring_frequency', 60)  # default to hourly if not set
            if datetime.now() - last_check < timedelta(minutes=frequency_minutes):
                return  # Skip update if not enough time has passed

            # Initialize history if not present
            if 'reading_history' not in data:
                data['reading_history'] = []

            # Add timestamp to current reading
            current_reading = latest_readings.copy()
            current_reading['timestamp'] = datetime.now().isoformat()

            # Add new reading
            data['reading_history'].append(current_reading)

            # Remove readings older than 1 week
            week_ago = datetime.now() - timedelta(days=7)
            data['reading_history'] = [
                reading for reading in data['reading_history']
                if datetime.fromisoformat(reading['timestamp']) > week_ago
            ]

            # Update latest reading and check time
            data['latest_reading'] = current_reading
            data['last_check_time'] = datetime.now().isoformat()

            # Reset file pointer and write updated data
            f.seek(0)
            json.dump(data, f, indent=4)
            f.truncate()

            # Check thresholds and send notification if needed
            check_thresholds(user_id, current_reading, data['thresholds'], data.get('nickname', 'your plant'))

    except Exception as e:
        logger.error(f"Error updating plant data: {str(e)}")

def check_thresholds(user_id, reading, thresholds, plant_nickname=None):
    """Check if readings are outside thresholds and notify user"""
    try:
        alerts = []
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file_path = os.path.join(USER_DATA_FOLDER, f'plant_data_{user_id}.json')

        with open(file_path, 'r') as f:
            plant_data = json.load(f)

        # Get monitoring frequency and nickname
        monitoring_frequency = plant_data.get('monitoring_frequency', 60)  # default to hourly
        plant_nickname = plant_data.get('nickname', 'your plant')

        for metric, value in reading.items():
            if metric == 'timestamp':  # Skip timestamp field
                continue

            if metric in thresholds and isinstance(value, (int, float)):
                threshold = thresholds[metric]
                if value < threshold['min']:
                    alerts.append({
                        'metric': metric,
                        'value': value,
                        'threshold': threshold['min'],
                        'condition': 'low'
                    })
                elif value > threshold['max']:
                    alerts.append({
                        'metric': metric,
                        'value': value,
                        'threshold': threshold['max'],
                        'condition': 'high'
                    })

        if alerts:
            # Check if we should send an alert based on monitoring frequency
            last_alert_time = plant_data.get('last_alert_time')
            if last_alert_time:
                last_alert = datetime.fromisoformat(last_alert_time)
                min_time_between_alerts = timedelta(minutes=monitoring_frequency)
                if datetime.now() - last_alert < min_time_between_alerts:
                    logger.info(f"Skipping alert for {user_id}, not enough time elapsed since last alert")
                    return

            # Update last alert time
            plant_data['last_alert_time'] = timestamp
            with open(file_path, 'w') as f:
                json.dump(plant_data, f, indent=4)

            send_alert(user_id, alerts, reading, thresholds, plant_nickname)

    except Exception as e:
        logger.error(f"Error checking thresholds: {str(e)}")

def send_alert(user_id, alerts, reading, thresholds, plant_nickname):
    """Send alert message using LLM for natural language"""
    try:
        client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        # Format alerts for better prompt
        alert_details = []
        for alert in alerts:
            metric_name = alert['metric'].replace('_', ' ').title()
            condition = 'too low' if alert['condition'] == 'low' else 'too high'
            ideal = f"ideal: {alert['threshold']}"
            alert_details.append(f"{metric_name} is {condition} ({alert['value']}, {ideal})")

        alert_text = "\n".join(alert_details)

        prompt = (
            f"You are Plantita, a caring plant expert and concerned aunt figure. Your beloved {plant_nickname} "
            f"has some concerning readings that need attention:\n\n"
            f"{alert_text}\n\n"
            f"Create a caring but urgent notification message that:\n"
            f"1. Expresses concern about the specific issues\n"
            f"2. Explains the potential risks to the plant\n"
            f"3. Provides immediate actions they can take\n"
            f"4. Offers reassurance and support\n\n"
            f"Keep the tone warm and caring but emphasize the importance of addressing these issues soon. "
            f"Make it personal, like an aunt worried about her favorite plant."
        )

        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        alert_message = response.choices[0].message.content

        # Log the alert
        logger.info(f"Sending alert to {user_id} for {plant_nickname}")
        logger.debug(f"Alert message: {alert_message}")

        # Send the message
        line_bot_api.push_message_with_http_info(
            {
                'to': user_id,
                'messages': [TextMessage(text=alert_message)]
            }
        )

    except Exception as e:
        logger.error(f"Error sending alert: {str(e)}")

def identify_plant(image_path):
    """Identify plant from image using Plant.id API"""
    try:
        url = "https://api.plant.id/v2/identify"
        api_key = os.getenv('PLANTID_API_KEY')

        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Api-Key": api_key
        }

        data = {
            "images": [base64_image],
            "organs": [],
            "include-related-images": False,
            "include-plant-details": ["common_names", "url", "wiki_description"]
        }

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()

        results = response.json()
        if results.get("suggestions"):
            best_match = max(results["suggestions"], key=lambda x: x["probability"])
            return best_match["plant_name"]

        return "Unknown Plant"

    except Exception as e:
        logger.error(f"Error in plant identification: {str(e)}")
        return "Unknown Plant"

def get_health_assessment(image_path):
    """Get plant health assessment using Plant.id API"""
    try:
        url = "https://api.plant.id/v2/health_assessment"
        api_key = os.getenv('PLANTID_API_KEY')

        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Api-Key": api_key
        }

        data = {
            "images": [base64_image],
            "organs": [],
            "include-related-images": False,
            "details": ["watering", "best_watering", "best_light_condition", "best_soil_type"]
        }

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()

        if response.status_code == 200:
            results = response.json()
            return {
                'health_info': results.get('health_assessment', {}),
                'is_healthy': results.get('is_healthy', True),
                'diseases': results.get('diseases', [])
            }
        else:
            logger.error(f"Error response from Plant.id API: {response.text}")
            return {"status": "error", "message": "Could not assess plant health"}

    except Exception as e:
        logger.error(f"Error in health assessment: {str(e)}")
        return {"status": "error", "message": "Could not assess plant health"}

def get_plant_description(plant_name, nickname, plant_details):
    """Get a natural description of the plant using Groq with care instructions"""
    try:
        client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        watering_info = plant_details.get('best_watering', 'Regular watering when soil feels dry')
        light_info = plant_details.get('best_light_condition', 'Moderate indirect light')
        soil_info = plant_details.get('best_soil_type', 'Well-draining potting mix')

        prompt = (
            f"You are Plantita, a knowledgeable and caring plant expert with both scientific expertise and a warm personality. "
            f"Create a comprehensive but friendly care guide for a {plant_name} named '{nickname}' that includes:\n\n"
            f"1. Scientific classification and common names\n"
            f"2. Natural habitat and growth characteristics\n"
            f"3. Detailed care instructions based on these specifications:\n"
            f"   - Watering: {watering_info}\n"
            f"   - Light: {light_info}\n"
            f"   - Soil: {soil_info}\n"
            f"4. Common issues to watch out for and their solutions\n"
            f"5. Special tips for optimal growth\n\n"
            f"Write this in a caring 'plant tita' tone - knowledgeable but warm, like an aunt teaching her favorite niece/nephew about plants. "
            f"Include both scientific facts and practical care advice."
        )

        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error getting plant description: {str(e)}")
        return f"Your beloved {nickname}, a beautiful {plant_name}"

def save_image(message_content, user_id):
    """Save image from LINE to local storage"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'plant_{user_id}_{timestamp}.jpg'
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(file_path, 'wb') as f:
        f.write(message_content)

    logger.info(f"Image saved: {file_path}")
    return file_path

def get_current_readings():
    """Get current readings from plant monitor"""
    try:
        if not latest_readings:
            return None
        return latest_readings.copy()
    except Exception as e:
        logger.error(f"Error reading sensor data: {str(e)}")
        return None

def get_plant_status_message(user_id, readings):
    """Get natural language status update for plant"""
    try:
        file_path = os.path.join(USER_DATA_FOLDER, f'plant_data_{user_id}.json')
        if not os.path.exists(file_path):
            return "I don't have any registered plants for you yet! Would you like to register one? Just type 'register' to get started! 🌱"

        with open(file_path, 'r') as f:
            data = json.load(f)

        if 'latest_reading' not in data:
            return "No readings available yet!"

        client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        prompt = (
            f"You are Plantita, a caring plant expert. Create a friendly status update for {data['nickname']} ({data['scientific_name']}):\n"
            f"Current readings: {data['latest_reading']}\n"
            f"Ideal ranges: {data['thresholds']}\n"
            f"Include both the current status and any care suggestions if needed."
        )

        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return "Sorry, I'm having trouble checking your plant right now!"

def save_user_plant_data(user_id, plant_data):
    """Save user's plant data to JSON file"""
    filename = f'plant_data_{user_id}.json'
    file_path = os.path.join(USER_DATA_FOLDER, filename)

    with open(file_path, 'w') as f:
        json.dump(plant_data, f, indent=4)

    logger.info(f"Plant data saved: {file_path}")

@app.route("/")
def home():
    logger.info("Home endpoint accessed")
    return "Plantita Bot is Running!"

@app.route("/webhook", methods=['POST'])
def webhook():
    logger.info("Webhook endpoint accessed")

    try:
        logger.debug("Request Headers:")
        for header, value in request.headers.items():
            logger.debug(f"{header}: {value}")

        signature = request.headers.get('X-Line-Signature', '')
        logger.info(f"Signature: {signature}")

        body = request.get_data(as_text=True)
        logger.info(f"Request body: {body}")

        if body == '{}' or body == '' or (isinstance(body, str) and json.loads(body).get('events', []) == []):
            logger.info("Verification request detected")
            return 'OK'

        handler.handle(body, signature)
        logger.info("Webhook handled successfully")
        return 'OK'

    except InvalidSignatureError as e:
        logger.error(f"Invalid signature error: {str(e)}")
        abort(400)
    except Exception as e:
        logger.error(f"Unexpected error in webhook: {str(e)}", exc_info=True)
        return 'OK'

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    """Handle image messages from users"""
    try:
        logger.info(f"Handling image message from user: {event.source.user_id}")
        user_id = event.source.user_id
        message_content = blob_api.get_message_content(message_id=event.message.id)
        image_path = save_image(message_content, user_id)

        # Check user state
        user_state = user_states.get(user_id, {}).get('state')
        logger.info(f"User state for {user_id}: {user_state}")

        if user_state == 'awaiting_registration_image':
            # Process registration image
            plant_name = identify_plant(image_path)
            user_states[user_id] = {
                'state': 'awaiting_nickname',
                'plant_name': plant_name,
                'image_path': image_path
            }
            reply_text = f"I've identified your plant as {plant_name}! What nickname would you like to give it?"

        elif user_state == 'awaiting_identification_image':
            # Process identification request
            plant_name = identify_plant(image_path)
            reply_text = f"This appears to be {plant_name}! Would you like to register this plant? Just type 'register' if you do! 🌿"
            user_states[user_id] = {'state': 'idle'}

        elif user_state == 'awaiting_assessment_image':
            # Process health assessment using Groq
            health_data = get_health_assessment(image_path)
            plant_name = user_states.get(user_id, {}).get('plant_name', 'your plant')
            client = Groq(api_key=os.getenv('GROQ_API_KEY'))

            # Create detailed health assessment prompt
            prompt = (
                f"You are Plantita, a caring plant expert analyzing the health of {plant_name}. "
                f"Based on the assessment:\n"
                f"Health Status: {'Healthy' if health_data.get('is_healthy') else 'Showing signs of stress'}\n"
                f"Diseases: {health_data.get('diseases', [])}\n"
                f"Care Info: {health_data.get('health_info', {})}\n\n"
                f"Please provide:\n"
                f"1. A caring assessment of the plant's health\n"
                f"2. Specific recommendations for improvement\n"
                f"3. Preventive care tips\n"
                f"Keep your tone warm and encouraging, like a knowledgeable aunt giving plant advice."
            )

            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-8b-8192"
            )

            reply_text = response.choices[0].message.content
            user_states[user_id] = {'state': 'idle'}

        else:
            reply_text = "Thanks for sharing your plant photo! 🌿 Would you like me to:\n\n" \
                         "1. Register this plant (type 'register')\n" \
                         "2. Identify it (say 'Hi Plantita, please help me identify this plant!')\n" \
                         "3. Check its health (say 'Hello Plantita, can you help me assess this plant?')"

        line_bot_api.reply_message_with_http_info(
            {
                'replyToken': event.reply_token,
                'messages': [TextMessage(text=reply_text)]
            }
        )
        logger.info("Reply sent successfully")

    except Exception as e:
        logger.error(f"Error handling image: {str(e)}", exc_info=True)
        line_bot_api.reply_message_with_http_info(
            {
                'replyToken': event.reply_token,
                'messages': [TextMessage(text="Sorry, I had trouble processing your image. Please try again later.")]
            }
        )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """Handle text messages from users"""
    try:
        logger.info(f"Handling text message from user: {event.source.user_id}")
        user_id = event.source.user_id
        text = event.message.text.lower()

        if text == 'register':
            user_states[user_id] = {'state': 'awaiting_registration_image'}
            reply = "Please send me a clear photo of your plant so I can register it! 📸🌿"

        elif text.startswith("hi plantita, please help me identify"):
            user_states[user_id] = {'state': 'awaiting_identification_image'}
            reply = "Of course! Please send me a clear photo of the plant you'd like to identify 🔍🌱"

        elif text.startswith("hello plantita, can you help me assess"):
            user_states[user_id] = {'state': 'awaiting_assessment_image'}
            reply = "I'll be happy to check your plant's health! Please send me a clear photo 🏥🌿"

        elif user_states.get(user_id, {}).get('state') == 'awaiting_nickname':
            nickname = text.strip()
            user_states[user_id].update({
                'nickname': nickname,
                'state': 'awaiting_frequency'
            })
            reply = (
                f"Great nickname! 🌱 Now, how often would you like me to check on {nickname}? Please choose:\n\n"
                "1️⃣ Every minute (type '1')\n"
                "2️⃣ Every hour (type '2')\n"
                "3️⃣ Every 8 hours (type '3')"
            )

        elif user_states.get(user_id, {}).get('state') == 'awaiting_frequency':
            frequency_map = {
                '1': {'minutes': 1, 'description': 'minute'},
                '2': {'minutes': 60, 'description': 'hour'},
                '3': {'minutes': 480, 'description': '8 hours'}
            }

            if text not in frequency_map:
                reply = (
                    "Please select a valid monitoring frequency:\n\n"
                    "1️⃣ Every minute (type '1')\n"
                    "2️⃣ Every hour (type '2')\n"
                    "3️⃣ Every 8 hours (type '3')"
                )
            else:
                frequency = frequency_map[text]
                plant_name = user_states[user_id]['plant_name']
                nickname = user_states[user_id]['nickname']
                image_path = user_states[user_id]['image_path']

                # Get plant health data from PlantID
                health_data = get_health_assessment(image_path)

                # Get thresholds from LLM if not provided by PlantID
                thresholds = get_thresholds_from_llm(plant_name)
                plant_details = health_data.get('health_info', {})
                description = get_plant_description(plant_name, nickname, plant_details)

                plant_data = {
                    'scientific_name': plant_name,
                    'nickname': nickname,
                    'thresholds': thresholds,
                    'description': description,
                    'reading_history': [],
                    'monitoring_frequency': frequency['minutes'],
                    'last_check_time': datetime.now().isoformat(),
                    'last_alert_time': None
                }
                save_user_plant_data(user_id, plant_data)
                reply = (
                    f"Perfect! I've registered your {plant_name} with the nickname '{nickname}'. 🌱✨\n\n"
                    f"I'll check on {nickname} every {frequency['description']} and let you know if anything needs attention!\n\n"
                    f"{description}\n\n"
                    "You can now monitor its health and get care advice!"
                )
                user_states[user_id] = {'state': 'idle'}

        elif text.startswith("hi plantita, can you check on my plant"):
            readings = get_current_readings()
            reply = get_plant_status_message(user_id, readings)

        else:
            reply = "Hello! 👋 I'm Plantita Bot. I can help you:\n\n" \
                    "1. Register your plant (type 'register')\n" \
                    "2. Identify plants (say 'Hi Plantita, please help me identify this plant!')\n" \
                    "3. Assess plant health (say 'Hello Plantita, can you help me assess this plant?')"

        line_bot_api.reply_message_with_http_info(
            {
                'replyToken': event.reply_token,
                'messages': [TextMessage(text=reply)]
            }
        )
        logger.info("Reply sent successfully")

    except Exception as e:
        logger.error(f"Error handling text message: {str(e)}", exc_info=True)

if __name__ == "__main__":
    logger.info("Starting Plantita Bot...")

    # Start Bluetooth monitoring in a background thread
    monitoring_thread = threading.Thread(target=start_monitoring_thread, daemon=True)
    monitoring_thread.start()

    app.run(host='0.0.0.0', port=8000, debug=True, use_reloader=False)