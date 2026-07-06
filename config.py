# config.py
# O token é carregado do arquivo .env (nunca commitado no git)

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]