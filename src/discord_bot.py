from io import BytesIO

from discord import app_commands
import discord
from remote_control import DISCORD_LIMIT, chunk
from utils import load_toml_as_dict
from window_controller import WindowController
try:
    from early_access.early_access import register_early_access_commands
    early_access = True
except ImportError:
    early_access = False
    def register_early_access_commands(a):
        pass


class DiscordBot:
    def __init__(self, remote):
        # Every decision a command makes lives in remote_control.py, so
        # Discord and Telegram cannot answer the same question differently.
        self.remote = remote
        self.runtime_manager = remote.runtime_manager
        self.data_service = remote.data_service
        self.started = False
        self.commands_synced = False

        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        self.register_events()
        self.register_commands()
        register_early_access_commands(self)

    @property
    def window_controller(self) -> WindowController:
        return self.remote.window_controller

    def set_window_controller(self, window_controller):
        self.remote.set_window_controller(window_controller)

    @staticmethod
    def _extract_discord_id(value):
        digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def get_authorized_user_id(self):
        config = load_toml_as_dict("cfg/webhook_config.toml", cache=False)
        return self._extract_discord_id(config.get("discord_id", ""))

    def get_configured_guild_id(self):
        guild_id = str(load_toml_as_dict("cfg/webhook_config.toml", cache=False).get("discord_guild_id", "")).strip()
        if not guild_id:
            return None

        try:
            return int(guild_id)
        except ValueError:
            print(f"Invalid discord_guild_id in cfg/webhook_config.toml: {guild_id}")
            return None

    def get_configured_guild(self):
        guild_id = self.get_configured_guild_id()
        if not guild_id:
            return None

        return discord.Object(id=guild_id)

    async def require_authorized_user(self, interaction: discord.Interaction) -> bool:
        authorized_user_id = self.get_authorized_user_id()
        if authorized_user_id is None:
            await interaction.response.send_message(
                "Discord remote control is disabled because discord_id is not configured.",
                ephemeral=True
            )
            return False

        if interaction.user.id != authorized_user_id:
            await interaction.response.send_message(
                "You are not authorized to control this Pyla instance.",
                ephemeral=True
            )
            return False

        configured_guild_id = self.get_configured_guild_id()
        if configured_guild_id and interaction.guild_id and interaction.guild_id != configured_guild_id:
            await interaction.response.send_message(
                "This Pyla instance is not configured for this Discord server.",
                ephemeral=True
            )
            return False

        return True

    async def sync_commands(self):
        guild = self.get_configured_guild()
        if guild:
            self.tree.copy_global_to(guild=guild)
            commands = await self.tree.sync(guild=guild)
            return len(commands), "guild"

        commands = await self.tree.sync()
        return len(commands), "global"

    def register_events(self):
        @self.client.event
        async def on_ready():
            print(f"Discord bot {self.client.user.name} is ready !")
            await self.sync_commands()

    def register_commands(self):
        def command(name, description, action):
            """One slash command that hands the work to remote_control."""

            @self.tree.command(name=name, description=description)
            async def run(interaction: discord.Interaction):
                if not await self.require_authorized_user(interaction):
                    return
                reply = action()
                pieces = chunk(reply.text, DISCORD_LIMIT)
                files = []
                if reply.photo is not None:
                    files = [discord.File(BytesIO(reply.photo), filename="screenshot.png")]
                await interaction.response.send_message(
                    pieces[0], files=files, ephemeral=True)
                for piece in pieces[1:]:
                    await interaction.followup.send(piece, ephemeral=True)

            return run

        command("screenshot", "Get a screenshot of the current game window",
                self.remote.screenshot)
        command("stop", "Makes the bot stop once it reaches the lobby", self.remote.stop)
        command("pause", "Makes the bot pause once it reaches the lobby", self.remote.pause)
        command("start", "Starts the bot if it's not already running", self.remote.start)
        command("status", "Returns the current status of the bot", self.remote.status)
        command("restart_brawl_stars", "Restarts Brawl Stars if the bot is running",
                self.remote.restart_game)
        command("view_queue", "View the current queue of the bot", self.remote.queue)

        @self.tree.command(
            name="help",
            description="Show the list of available commands",
        )
        async def help_command(interaction: discord.Interaction):
            if not await self.require_authorized_user(interaction):
                return

            commands = {
                "screenshot": "Get a screenshot of the current game window (only works when the bot is running)",
                "stop": "Makes the bot stop once it reaches the lobby",
                "pause": "Makes the bot pause once it reaches the lobby",
                "start": "Starts the bot if it's not already running",
                "status": "Returns the current status of the bot",
                "restart_brawl_stars": "Restarts Brawl Stars if the bot is running",
                "view_queue": "View the current queue of the bot",
                "add_to_queue": "Add a brawler to the queue (only works when the bot is not running)",
                "remove_from_queue": "Remove a brawler from the queue (only works when the bot is not running)",
                "clear_queue": "Clear the current queue (only works when the bot is not running)",
                "activate_playstyle": "Activate a playstyle (only works when the bot is not running)",
            }
            message = "**Available commands:**\n" + "\n".join(f"- `{command}`: {description}" for command, description in commands.items())
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    def run_bot(self):
        discord_bot_token = str(load_toml_as_dict("cfg/webhook_config.toml").get("discord_bot_token", "")).strip()
        if not discord_bot_token:
            print("Discord bot token is not configured. Skipping Discord bot startup.")
            return
        if self.started:
            return

        self.started = True
        try:
            self.client.run(discord_bot_token)
        finally:
            self.started = False
