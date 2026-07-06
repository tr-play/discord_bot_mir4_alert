Plano: Comando /alarme
Context
O bot de alertas de bosses do MIR4 precisa de um comando manual que permita que administradores enviem uma notificação com texto livre para um usuário ou cargo específico, sem vínculo com nenhum boss. O comando será /alarme e enviará um embed estilizado no canal onde for executado, mencionando o alvo.

Abordagem
Adicionar o comando /alarme ao cog Commands já existente em mir4-boss-alert/cogs/commands.py.

Parâmetros do comando
Parâmetro	Tipo	Descrição
alvo	Union[discord.Member, discord.Role]	Usuário ou cargo a ser mencionado
mensagem	str	Texto livre da notificação (max 500 chars)
Comportamento
Restrito a administradores (@app_commands.checks.has_permissions(administrator=True))
Responde com defer(ephemeral=True) primeiro (confirmação privada ao admin)
Envia mensagem pública no canal com content=alvo.mention (para notificação real no Discord) + embed estilizado
Embed com cor laranja (0xFF8C00), título "🚨 Alarme", a mensagem no corpo, e footer com quem enviou
Arquivo a modificar
mir4-boss-alert/cogs/commands.py

1. Adicionar import
Na linha de imports, adicionar Union do módulo typing:

from typing import Union
2. Adicionar o comando dentro da classe Commands
Inserir antes do setup final, seguindo o padrão existente:

@app_commands.command(name="alarme", description="Envia uma mensagem de alarme para um usuário ou cargo")
@app_commands.checks.has_permissions(administrator=True)
async def alarme(
    self,
    interaction: discord.Interaction,
    alvo: Union[discord.Member, discord.Role],
    mensagem: str
):
    await interaction.response.defer(ephemeral=True)

    # Trunca mensagem se necessário
    if len(mensagem) > 500:
        mensagem = mensagem[:497] + "..."

    embed = discord.Embed(
        title="🚨 Alarme",
        description=mensagem,
        color=0xFF8C00
    )
    embed.set_footer(text=f"Enviado por {interaction.user.display_name}")

    await interaction.channel.send(content=alvo.mention, embed=embed)
    await interaction.followup.send(
        f"✅ Alarme enviado para {alvo.mention}.", ephemeral=True
    )
3. Tratamento de erro de permissão
Adicionar handler no cog_load ou via decorator padrão do bot (já existe em outros comandos — verificar se há um app_commands.error global; se não, adicionar):

@alarme.error
async def alarme_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Você precisa ser administrador para usar este comando.", ephemeral=True
        )
Verificação
Rodar o bot: python bot.py
No Discord, digitar /alarme — confirmar que aparece com os parâmetros alvo e mensagem
Testar com alvo = um usuário → a pessoa deve receber notificação e ver o embed
Testar com alvo = um cargo → todos os membros do cargo devem receber notificação
Testar sem permissão de admin → deve retornar mensagem de erro ephemeral
Testar com mensagem longa (>500 chars) → deve truncar com "..."