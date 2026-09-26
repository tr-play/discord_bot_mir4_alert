# config.py
# O token é carregado do arquivo .env (nunca commitado no git)

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]

# The bot never touches Postgres directly -- everything boss-alert-related (schedule,
# account linking, completions, notification dedupe) goes through the site's own API.
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://mir4.opendataplay.com")
BOT_API_SECRET = os.environ.get("BOT_API_SECRET", "")