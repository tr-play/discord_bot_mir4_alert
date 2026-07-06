import os
import sys

# Garante que o diretório de trabalho seja sempre o da pasta do bot
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.append(BASE_DIR)

# ------------------------------

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True        # ← Obrigatório para detectar reações

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot iniciado com sucesso!")
    print(f"   Nome: {bot.user}")
    print(f"   ID: {bot.user.id}")
    print("-" * 70)

    # Mostrar diretório atual (debug)
    print(f"📁 Diretório atual: {os.getcwd()}")

    # Carregar cogs com mensagens de erro detalhadas
    print("🔄 Carregando cogs...")

    try:
        await bot.load_extension("cogs.commands")
        print("✅ Cog 'commands' carregado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao carregar 'commands': {e}")

    try:
        await bot.load_extension("cogs.alerts")
        print("✅ Cog 'alerts' carregado com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao carregar 'alerts': {e}")

    print("-" * 70)

    # Mostrar comandos carregados
    print("📋 COMANDOS SLASH CARREGADOS:")
    print("-" * 70)
    if not bot.tree.get_commands():
        print("   Nenhum comando encontrado!")
    else:
        for cmd in bot.tree.walk_commands():
            print(f"   /{cmd.qualified_name}  →  {cmd.description}")
    
    print("-" * 70)

    # Sincronizar comandos
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} comandos sincronizados com o Discord!")
    except Exception as e:
        print(f"❌ Erro ao sincronizar comandos: {e}")


# 🔐 Token com proteção de erro
try:
    from config import TOKEN
    bot.run(TOKEN)
except Exception as e:
    print("❌ ERRO FATAL:", e)
    input("Pressione ENTER para sair...")