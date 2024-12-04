# to run this file, use the command: python app.py
# then run an instance of ngrok with the command: ngrok http 8000
# then update the webhook URL in the LINE Developer Console to the ngrok URL
# then try to send a message to the bot in LINE.

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
blob_api = MessagingApiBlob(api_client)  # New API client for handling binary content

# Create a directory for storing images
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'plant_images')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
logger.info(f"Images will be saved to: {UPLOAD_FOLDER}")

def save_image(message_content, user_id):
    """Save image from LINE to local storage"""
    # Create a timestamp for unique filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'plant_{user_id}_{timestamp}.jpg'
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    # Save the image
    with open(file_path, 'wb') as f:
        f.write(message_content)

    logger.info(f"Image saved: {file_path}")
    return file_path

@app.route("/")
def home():
    logger.info("Home endpoint accessed")
    return "Plantita Bot is Running Locally!"

@app.route("/webhook", methods=['POST'])
def webhook():
    logger.info("Webhook endpoint accessed")

    try:
        # Log all headers for debugging
        logger.debug("Request Headers:")
        for header, value in request.headers.items():
            logger.debug(f"{header}: {value}")

        # Get X-Line-Signature header value
        signature = request.headers.get('X-Line-Signature', '')
        logger.info(f"Signature: {signature}")

        # Get request body as text
        body = request.get_data(as_text=True)
        logger.info(f"Request body: {body}")

        # Quick response for verification
        if body == '{}' or body == '' or (isinstance(body, str) and json.loads(body).get('events', []) == []):
            logger.info("Verification request detected")
            return 'OK'

        # Handle actual webhook events
        handler.handle(body, signature)
        logger.info("Webhook handled successfully")
        return 'OK'

    except InvalidSignatureError as e:
        logger.error(f"Invalid signature error: {str(e)}")
        abort(400)
    except Exception as e:
        logger.error(f"Unexpected error in webhook: {str(e)}", exc_info=True)
        return 'OK'  # Return OK even on error to avoid timeouts

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    """Handle image messages from users"""
    try:
        logger.info(f"Handling image message from user: {event.source.user_id}")

        # Get message content using blob API
        message_content = blob_api.get_message_content(message_id=event.message.id)
        user_id = event.source.user_id

        # Save the image
        image_path = save_image(message_content, user_id)
        logger.info(f"Received image from {user_id}, saved to {image_path}")

        # Send acknowledgment message
        line_bot_api.reply_message_with_http_info(
            {
                'replyToken': event.reply_token,
                'messages': [
                    TextMessage(text="Thanks for sharing your plant photo! 🌿"),
                    TextMessage(text="I'm analyzing your plant now... Please wait a moment.")
                ]
            }
        )
        logger.info("Reply sent successfully")

    except Exception as e:
        logger.error(f"Error handling image: {str(e)}", exc_info=True)
        line_bot_api.reply_message_with_http_info(
            {
                'replyToken': event.reply_token,
                'messages': [
                    TextMessage(text="Sorry, I had trouble processing your image. Please try again later.")
                ]
            }
        )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """Handle text messages from users"""
    try:
        logger.info(f"Handling text message from user: {event.source.user_id}")
        text = event.message.text.lower()

        if "hello" in text or "hi" in text:
            reply = "Hello! 👋 I'm Plantita Bot. Send me a photo of your plant and I'll help you analyze it! 🌿"
        else:
            reply = "Send me a photo of your plant and I'll help you analyze it! 📸🌿"

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