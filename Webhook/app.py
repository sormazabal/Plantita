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
        api_key = os.getenv('PLANTID_API_KEY')

        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Api-Key": api_key
        }

        data = {
            "images": [base64_image],
            "organs": ["leaf"],
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
            "organs": ["leaf"],
            "include-related-images": False
        }

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()

        results = response.json()
        health_info = results.get("health_assessment", {})

        if health_info.get("is_healthy"):
            return {
                "status": "healthy",
                "probability": health_info["is_healthy_probability"]
            }
        else:
            return {
                "status": "unhealthy",
                "diseases": health_info.get("diseases", [])
            }

    except Exception as e:
        logger.error(f"Error in health assessment: {str(e)}")
        return {"status": "error", "message": "Could not assess plant health"}

def save_image(message_content, user_id):
    """Save image from LINE to local storage"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'plant_{user_id}_{timestamp}.jpg'
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(file_path, 'wb') as f:
        f.write(message_content)

    logger.info(f"Image saved: {file_path}")
    return file_path

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
            # Process health assessment
            health_data = get_health_assessment(image_path)
            if health_data['status'] == 'healthy':
                reply_text = f"Good news! Your plant appears to be healthy with {health_data['probability']*100:.1f}% confidence! 🌱✨"
            else:
                diseases = health_data.get('diseases', [])
                if diseases:
                    disease_text = [f"- {d['name']} ({d['probability']*100:.1f}%)" for d in diseases]
                    reply_text = "I've detected some potential issues:\n" + "\n".join(disease_text)
                else:
                    reply_text = "I'm seeing some signs that your plant might need attention, but I can't pinpoint the exact issue. Consider consulting a local plant expert! 🌿"
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
            plant_name = user_states[user_id]['plant_name']

            plant_data = {
                'scientific_name': plant_name,
                'nickname': nickname,
                'thresholds': {
                    'temperature': {'min': 20, 'max': 30},
                    'humidity': {'min': 40, 'max': 60},
                    'light': {'min': 50, 'max': 80},
                    'moisture': {'min': 30, 'max': 70}
                },
                'description': f"Your beloved {nickname}"
            }
            save_user_plant_data(user_id, plant_data)
            reply = f"Perfect! I've registered your {plant_name} with the nickname '{nickname}'. You can now monitor its health and get care advice! 🌱✨"
            user_states[user_id] = {'state': 'idle'}

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