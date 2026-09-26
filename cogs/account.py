"""Personal account linking (Discord <-> xdoApp site account) and the boss-completion
checklist. Kept as its own cog rather than piled into commands.py (~1000 lines already).

All state lives on the site's Postgres, reached only through api_client.py -- this cog
never touches a database directly. The bot only ever knows a user's Discord snowflake
id; resolving that to a site account is the API's job.

No per-guild language here: a DM has no guild to resolve `/setup_language` against, so
these responses are always Portuguese, the same simplification xdoApp's own push-alert
cron already documents for itself.
"""
import time

import discord
from discord import app_commands
from discord.ext import commands

import api_client

SITE_SETTINGS_URL = "https://mir4.opendataplay.com/settings"
BOSS_TYPES = ("MB", "PICO", "PRACA", "WB", "EVENTO")

# Simple in-process rate limit as a second layer on top of the site's own per-discord_id
# rate limit on /api/discord/bot/claim-code -- avoids even sending a request for an
# obviously-abusive burst of attempts.
_vincular_attempts: dict[str, list[float]] = {}
_VINCULAR_WINDOW_SECONDS = 600
_VINCULAR_MAX_ATTEMPTS = 5


def _rate_limited(discord_id: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _vincular_attempts.get(discord_id, []) if now - t < _VINCULAR_WINDOW_SECONDS]
    attempts.append(now)
    _vincular_attempts[discord_id] = attempts
    return len(attempts) > _VINCULAR_MAX_ATTEMPTS


class SilenceTypeButton(discord.ui.Button):
    def __init__(self, boss_type: str):
        super().__init__(
            label=f"🔕 Silenciar {boss_type}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"boss_alert:silence_type:{boss_type}",
        )
        self.boss_type = boss_type

    async def callback(self, interaction: discord.Interaction):
        # Ack within Discord's ~3s window before the (network) API call, not after --
        # otherwise a slow response from the site times out the interaction even though
        # the action itself went through.
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        await api_client.set_notify_type(discord_id, self.boss_type, False)
        await interaction.followup.send(
            f"🔕 Você não vai mais receber avisos de **{self.boss_type}**. "
            f"Reative em `/minhasemana` ou em {SITE_SETTINGS_URL}.",
            ephemeral=True,
        )


class ConfirmUnlinkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger, custom_id="boss_alert:unlink_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await api_client.unlink(str(interaction.user.id))
        await interaction.edit_original_response(content="✅ Conta desvinculada.", view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="boss_alert:unlink_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelado.", view=None)


class UnlinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🔗 Desvincular conta",
            style=discord.ButtonStyle.danger,
            custom_id="boss_alert:unlink_prompt",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Tem certeza que quer desvincular sua conta? Você vai parar de receber alertas por DM.",
            view=ConfirmUnlinkView(),
            ephemeral=True,
        )


class BossAlertView(discord.ui.View):
    """Attached to every personal DM alert. `types` is the subset of boss types present
    in that specific batch -- routing back still works after a bot restart because every
    possible custom_id is covered by the template instance registered once in setup()."""

    def __init__(self, types: list[str] | None = None):
        super().__init__(timeout=None)
        for boss_type in types if types is not None else BOSS_TYPES:
            self.add_item(SilenceTypeButton(boss_type))
        self.add_item(UnlinkButton())


class Account(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="vincular", description="Vincula sua conta do Discord à sua conta do site")
    @app_commands.describe(codigo="Código gerado em mir4.opendataplay.com/settings")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
    async def vincular(self, interaction: discord.Interaction, codigo: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        if _rate_limited(discord_id):
            await interaction.followup.send("Muitas tentativas. Espere um pouco e tente de novo.", ephemeral=True)
            return

        code = codigo.strip().upper().replace(" ", "")
        result = await api_client.claim_code(discord_id, interaction.user.name, code)
        error = result["body"].get("error")

        if result["status"] == 200:
            await interaction.followup.send(
                "✅ Conta vinculada! Suas preferências e checklist agora são compartilhados com o site.",
                ephemeral=True,
            )
        elif error == "already_linked_to_other_account":
            await interaction.followup.send(
                f"❌ Este Discord já está vinculado a outra conta do site. "
                f"Desvincule por lá primeiro em {SITE_SETTINGS_URL}, depois tente de novo.",
                ephemeral=True,
            )
        elif error == "rate_limited":
            await interaction.followup.send("Muitas tentativas. Espere um pouco e tente de novo.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"❌ Código inválido ou expirado. Gere um novo em {SITE_SETTINGS_URL}.",
                ephemeral=True,
            )

    @app_commands.command(name="desvincular", description="Desvincula sua conta do Discord da conta do site")
    async def desvincular(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await api_client.unlink(str(interaction.user.id))
        await interaction.followup.send("✅ Conta desvinculada. Você não vai mais receber alertas por DM.", ephemeral=True)

    @app_commands.command(name="feito", description="Marca um mini boss (MB) como feito essa semana")
    @app_commands.describe(boss_id="ID do boss (ex: wb_007)")
    async def feito(self, interaction: discord.Interaction, boss_id: str):
        await interaction.response.defer(ephemeral=True)
        result = await api_client.set_completion(str(interaction.user.id), boss_id.lower(), True)
        error = result["body"].get("error")
        if result["status"] == 200:
            await interaction.followup.send(f"✅ `{boss_id}` marcado como feito essa semana.", ephemeral=True)
        elif error == "not_linked":
            await interaction.followup.send(f"Vincule sua conta primeiro com `/vincular` ({SITE_SETTINGS_URL}).", ephemeral=True)
        elif error == "boss_not_completable":
            await interaction.followup.send("Só bosses do tipo MB (Mini Boss) têm checklist.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Não foi possível marcar esse boss.", ephemeral=True)

    @app_commands.command(name="pendente", description="Desmarca um mini boss (MB) como feito essa semana")
    @app_commands.describe(boss_id="ID do boss (ex: wb_007)")
    async def pendente(self, interaction: discord.Interaction, boss_id: str):
        await interaction.response.defer(ephemeral=True)
        result = await api_client.set_completion(str(interaction.user.id), boss_id.lower(), False)
        if result["status"] == 200:
            await interaction.followup.send(f"↩️ `{boss_id}` voltou a ficar pendente.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Não foi possível atualizar esse boss.", ephemeral=True)

    @app_commands.command(name="minhasemana", description="Mostra seu vínculo, checklist e preferências pessoais")
    async def minhasemana(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        subscribers = await api_client.get_subscribers()
        me = next((s for s in subscribers if s["discord_id"] == discord_id), None)

        if me is None:
            await interaction.followup.send(
                f"🔗 Conta não vinculada. Gere um código em {SITE_SETTINGS_URL} e rode `/vincular <codigo>`.",
                ephemeral=True,
            )
            return

        types_on = [t for t in BOSS_TYPES if me["notify_types_enabled"].get(t, False)]
        embed = discord.Embed(title="📋 Minha semana", color=0x7F77DD)
        embed.add_field(name="Vinculado como", value=f"@{me['discord_username'] or '?'}", inline=False)
        embed.add_field(name="Antecedência de aviso", value=f"{me['notify_lead_minutes']} min", inline=True)
        embed.add_field(name="Tipos ativos", value=", ".join(types_on) or "nenhum", inline=True)
        completed = me.get("completed_boss_ids", [])
        embed.add_field(
            name=f"MBs feitos essa semana ({len(completed)})",
            value=", ".join(f"`{b}`" for b in completed) if completed else "nenhum ainda",
            inline=False,
        )
        embed.set_footer(text=f"Edite tudo isso em {SITE_SETTINGS_URL}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(BossAlertView())  # persistent registration -- covers every possible custom_id
    await bot.add_cog(Account(bot))
