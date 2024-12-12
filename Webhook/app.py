from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
from linebot.v3.messaging import TextMessage
from linebot.v3.exceptions import InvalidSignatureError
import os
from dotenv import load_dotenv
import tempfile
from datetime import datetime
import logging
import json
import requests
import base64
from groq import Groq

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

def identify_plant(image_path):
    """Identify plant from image using Plant.id API"""
    try:
        url = "https://api.plant.id/v2/identify"
        api_key = os.getenv('PLANTID_API_KEY', "V3bNS9Yrc6yWsY2pgevJnjNoYtnhIw6Uvh9PxvuNwiyTyYf5Tr")

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
        api_key = os.getenv('PLANTID_API_KEY', "V3bNS9Yrc6yWsY2pgevJnjNoYtnhIw6Uvh9PxvuNwiyTyYf5Tr")

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

        if response.status_code == 200:
            results = response.json()
            health_info = results.get("health_assessment", {})

            if health_info.get("is_healthy"):
                return {
                    "is_healthy": True,
                    "probability": health_info["is_healthy_probability"],
                    "watering": results.get("watering", {}),
                    "best_watering": results.get("best_watering", ""),
                    "best_light_condition": results.get("best_light_condition", ""),
                    "best_soil_type": results.get("best_soil_type", "")
                }
            else:
                return {
                    "is_healthy": False,
                    "diseases": health_info.get("diseases", []),
                    "watering": results.get("watering", {}),
                    "best_watering": results.get("best_watering", ""),
                    "best_light_condition": results.get("best_light_condition", ""),
                    "best_soil_type": results.get("best_soil_type", "")
                }
        else:
            logger.error(f"Error response from Plant.id API: {response.text}")
            return {"status": "error", "message": "Could not assess plant health"}

    except Exception as e:
        logger.error(f"Error in health assessment: {str(e)}")
        return {"status": "error", "message": "Could not assess plant health"}

def send_groq_health_assessment(image_path, plant_name, health_data):
    """
    Sends plant health status message based on a picture and PlantID API to the Groq API.
    """
    try:
        # Initialize the Groq client
        client = Groq(
            api_key=os.getenv('GROQ_API_KEY', "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")
        )

        # Process health status for the prompt
        if health_data.get("is_healthy"):
            health_status = f"The plant appears healthy with {health_data['probability']*100:.1f}% confidence"
        else:
            diseases = []
            for disease in health_data.get("diseases", []):
                name = disease.get("name", "Unknown issue")
                prob = disease.get("probability", 0) * 100
                treatment = disease.get("treatment", "No specific treatment provided")
                diseases.append(f"- {name} ({prob:.1f}% confidence)\n  Treatment: {treatment}")
            health_status = "The plant shows signs of the following issues:\n" + "\n".join(diseases)

        # Add care recommendations if available
        care_info = []
        if health_data.get("watering"):
            watering = health_data["watering"]
            care_info.append(f"Watering needs: Level {watering.get('min', 1)} to {watering.get('max', 3)} (1=dry, 2=medium, 3=wet)")
        if health_data.get("best_watering"):
            care_info.append(f"Watering tips: {health_data['best_watering']}")
        if health_data.get("best_light_condition"):
            care_info.append(f"Light requirements: {health_data['best_light_condition']}")
        if health_data.get("best_soil_type"):
            care_info.append(f"Soil preferences: {health_data['best_soil_type']}")

        care_details = "\n".join(care_info) if care_info else "No specific care details available"

        # craft the prompt
        user_message = (
            f"You are Plantita, an expert plant care advisor with a warm, caring personality like a concerned aunt.\n\n"
            f"Based on the health assessment of the plant {plant_name}:\n"
            f"{health_status}\n\n"
            f"Additional care information:\n"
            f"{care_details}\n\n"
            f"Please analyze these conditions and provide:\n"
            "1. A caring, conversational assessment of the plant's current environment\n"
            "2. Specific recommendations for improvement if needed\n"
            "3. Any potential risks to the plant's health based on these conditions\n"
            "4. A simple action plan for the plant owner\n\n"
            "Keep your response friendly and encouraging, like a knowledgeable aunt giving advice about their beloved plants."
        )

        # Send to Groq
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": user_message}],
            model="llama3-8b-8192"
        )

        logger.info("Groq response generated successfully")
        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error in Groq health assessment: {str(e)}")
        return "I'm sorry, I'm having trouble analyzing your plant's health right now. Please try again later! 🌿"

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
    """Get current readings from Arduino sensors"""
    try:
        # Initialize serial connection to Arduino
        import serial
        arduino = serial.Serial('/dev/ttyUSB0', 9600)  # Adjust port as needed
        arduino.timeout = 5  # Set timeout for reading

        # Wait for connection to stabilize
        import time
        time.sleep(2)

        # Read data from Arduino
        readings = {
            'temperature': None,
            'humidity': None,
            'pressure': None
        }

        # Read each sensor value
        try:
            temperature = float(arduino.readline().decode().strip())
            humidity = float(arduino.readline().decode().strip())
            pressure = float(arduino.readline().decode().strip())

            readings = {
                'temperature': temperature,
                'humidity': humidity,
                'pressure': pressure
            }
        finally:
            arduino.close()

        return readings

    except Exception as e:
        logger.error(f"Error reading sensor data: {str(e)}")
        return None

def get_plant_status_message(user_id, readings):
    """Generate a status message using Groq based on current readings"""
    try:
        # Load user's plant data
        plant_data_file = os.path.join(USER_DATA_FOLDER, f'plant_data_{user_id}.json')
        if not os.path.exists(plant_data_file):
            return "I don't have any registered plants for you yet! Would you like to register one? Just type 'register' to get started! 🌱"

        with open(plant_data_file, 'r') as f:
            plant_data = json.load(f)

        if not readings:
            return "I'm having trouble reading the sensor data right now. Please try again in a few minutes! 🌿"

        # Initialize Groq client
        client = Groq(
            api_key=os.getenv('GROQ_API_KEY', "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")
        )

        # Create prompt for Groq
        thresholds = plant_data['thresholds']
        plant_name = plant_data.get('nickname', plant_data['scientific_name'])

        prompt = (
            f"You are Plantita, an expert plant care advisor with a warm, caring personality like a concerned aunt.\n\n"
            f"Current readings for {plant_name}:\n"
            f"Temperature: {readings['temperature']}°C (ideal range: {thresholds['temperature']['min']}-{thresholds['temperature']['max']}°C)\n"
            f"Humidity: {readings['humidity']}% (ideal range: {thresholds['humidity']['min']}-{thresholds['humidity']['max']}%)\n"
            f"Pressure: {readings['pressure']} hPa\n\n"
            f"Please provide:\n"
            f"1. A friendly assessment of the current conditions\n"
            f"2. Any immediate actions needed\n"
            f"3. Tips for maintaining optimal conditions\n\n"
            f"Keep your response conversational and caring, like checking on a beloved plant. Limit the response to 3-4 sentences."
        )

        # Get response from Groq
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error generating plant status: {str(e)}")
        return "I'm having trouble checking on your plant right now. Please try again later! 🌿"


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
            reply_text = send_groq_health_assessment(image_path, plant_name, health_data)
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

def get_plant_description(plant_name, nickname, plant_details):
    """Get a natural description of the plant using Groq with care instructions"""
    try:
        client = Groq(
            api_key=os.getenv('GROQ_API_KEY', "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")
        )

        # Extract care information from plant_details
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
            f"Include both scientific facts and practical care advice. Make it detailed enough to serve as a care reference guide."
        )

        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192"
        )

        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Error getting plant description: {str(e)}")
        return f"Your beloved {nickname}, a beautiful {plant_name}"

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
            plant_name = user_states[user_id]['plant_name']
            image_path = user_states[user_id]['image_path']

            # Get plant health data from PlantID
            plant_details = get_health_assessment(image_path)

            # Get a natural description using Groq with plant details
            description = get_plant_description(plant_name, nickname, plant_details)

            plant_data = {
                'scientific_name': plant_name,
                'nickname': nickname,
                'thresholds': {
                    'temperature': {'min': 20, 'max': 30},
                    'humidity': {'min': 40, 'max': 60},
                    'light': {'min': 50, 'max': 80},
                    'moisture': {'min': 30, 'max': 70}
                },
                'description': description
            }
            save_user_plant_data(user_id, plant_data)
            reply = (
                f"Perfect! I've registered your {plant_name} with the nickname '{nickname}'. 🌱✨\n\n"
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
    logger.info("Starting Plantita Bot locally...")
    app.run(host='0.0.0.0', port=8000, debug=True, use_reloader=False)