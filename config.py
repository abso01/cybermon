import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "8784995267:AAGovA0zpC4-tyH4DZViwOxeQ8tVjMafKmI")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://captain-sacred-reduction-cities.trycloudflare.com")
PORT = int(os.getenv("PORT", 8000))
HOST = os.getenv("HOST", "0.0.0.0")
