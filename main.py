import os
import time
import random
import asyncio
import sqlite3
import datetime

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# ---------------------------------------------------------------- DB
db = sqlite3.connect("bot.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    guild_id INTEGER, user_id INTEGER,
    xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
    coins INTEGER DEFAULT 0, last_daily REAL DEFAULT 0,
    PRIMARY KEY(guild_id, user_id));
CREATE TABLE IF NOT EXISTS settings(
    guild_id INTEGER PRIMARY KEY,
    welcome_channel INTEGER, autorole INTEGER, log_channel INTEGER);
CREATE TABLE IF NOT EXISTS warns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER, user_id INTEGER, mod_id INTEGER,
    reason TEXT, ts REAL);
""")
db.commit()


def get_user(gid, uid):
    db.execute("INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)", (gid, uid))
    return db.execute(
        "SELECT xp, level, coins, last_daily FROM users WHERE guild_id=? AND user_id=?",
        (gid, uid)).fetchone()


def get_setting(gid, key):
    db.execute("INSERT OR IGNORE INTO settings(guild_id) VALUES(?)", (gid,))
    row = db.execute(f"SELECT {key} FROM settings WHERE guild_id=?", (gid,)).fetchone()
    return row[0]


def set_setting(gid, key, value):
    db.execute("INSERT OR IGNORE INTO settings(guild_id) VALUES(?)", (gid,))
    db.execute(f"UPDATE settings SET {key}=? WHERE guild_id=?", (value, gid))
    db.commit()


# ---------------------------------------------------------------- Tickets UI
class CloseTicket(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("Closing in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()


class OpenTicket(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open a ticket", style=discord.ButtonStyle.primary,
                       emoji="🎫", custom_id="ticket:open")
    async def open(self, interaction: discord.Interaction, _):
        g = interaction.guild
        name = f"ticket-{interaction.user.name}".lower()[:90]
        if discord.utils.get(g.text_channels, name=name):
            return await interaction.response.send_message(
                "You already have an open ticket.", ephemeral=True)
        overwrites = {
            g.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        ch = await g.create_text_channel(name, overwrites=overwrites)
        await ch.send(f"{interaction.user.mention} support will be with you soon.",
                      view=CloseTicket())
        await interaction.response.send_message(f"Ticket created: {ch.mention}", ephemeral=True)


# ---------------------------------------------------------------- Bot
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.xp_cooldown = {}

    async def setup_hook(self):
        self.add_view(OpenTicket())
        self.add_view(CloseTicket())
        await self.tree.sync()


bot = Bot()


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game("/help"))
    print(f"Logged in as {bot.user}")


async def send_log(guild, embed):
    cid = get_setting(guild.id, "log_channel")
    ch = guild.get_channel(cid) if cid else None
    if ch:
        await ch.send(embed=embed)


# ---------------------------------------------------------------- Events
@bot.event
async def on_member_join(member):
    gid = member.guild.id
    rid = get_setting(gid, "autorole")
    if rid and (role := member.guild.get_role(rid)):
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass
    cid = get_setting(gid, "welcome_channel")
    if cid and (ch := member.guild.get_channel(cid)):
        e = discord.Embed(title="Welcome! 🎉",
                          description=f"{member.mention} joined **{member.guild.name}**.\n"
                                      f"You are member #{member.guild.member_count}.",
                          color=discord.Color.green())
        e.set_thumbnail(url=member.display_avatar.url)
        await ch.send(embed=e)


@bot.event
async def on_member_remove(member):
    cid = get_setting(member.guild.id, "welcome_channel")
    if cid and (ch := member.guild.get_channel(cid)):
        await ch.send(f"👋 **{member}** left the server.")


@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    e = discord.Embed(title="Message deleted", color=discord.Color.red(),
                      description=message.content or "(no text)")
    e.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
    e.add_field(name="Channel", value=message.channel.mention)
    await send_log(message.guild, e)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    key = (message.guild.id, message.author.id)
    now = time.time()
    if now - bot.xp_cooldown.get(key, 0) >= 60:
        bot.xp_cooldown[key] = now
        xp, level, coins, _ = get_user(*key)
        xp += random.randint(10, 20)
        new_level = int((xp / 100) ** 0.5)
        db.execute("UPDATE users SET xp=?, level=?, coins=coins+? WHERE guild_id=? AND user_id=?",
                   (xp, new_level, random.randint(1, 5), *key))
        db.commit()
        if new_level > level:
            await message.channel.send(
                f"🆙 {message.author.mention} reached **level {new_level}**!")
    await bot.process_commands(message)


# ---------------------------------------------------------------- Moderation
mod = app_commands.Group(name="mod", description="Moderation",
                         default_permissions=discord.Permissions(moderate_members=True),
                         guild_only=True)


@mod.command(description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(i: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await i.response.send_message(f"👢 {member} kicked. Reason: {reason}")


@mod.command(description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(i: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    await i.response.send_message(f"🔨 {member} banned. Reason: {reason}")


@mod.command(description="Unban by user ID")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(i: discord.Interaction, user_id: str):
    user = await bot.fetch_user(int(user_id))
    await i.guild.unban(user)
    await i.response.send_message(f"✅ {user} unbanned.")


@mod.command(description="Timeout a member (minutes)")
async def timeout(i: discord.Interaction, member: discord.Member,
                  minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason"):
    await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    await i.response.send_message(f"🔇 {member} timed out for {minutes} min.")


@mod.command(description="Remove a timeout")
async def untimeout(i: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await i.response.send_message(f"🔊 {member} timeout removed.")


@mod.command(description="Delete messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(i: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await i.response.defer(ephemeral=True)
    deleted = await i.channel.purge(limit=amount)
    await i.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)


@mod.command(description="Warn a member")
async def warn(i: discord.Interaction, member: discord.Member, reason: str):
    db.execute("INSERT INTO warns(guild_id,user_id,mod_id,reason,ts) VALUES(?,?,?,?,?)",
               (i.guild.id, member.id, i.user.id, reason, time.time()))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM warns WHERE guild_id=? AND user_id=?",
                       (i.guild.id, member.id)).fetchone()[0]
    await i.response.send_message(f"⚠️ {member.mention} warned ({count} total). Reason: {reason}")


@mod.command(description="List a member's warnings")
async def warns(i: discord.Interaction, member: discord.Member):
    rows = db.execute("SELECT id, reason, ts FROM warns WHERE guild_id=? AND user_id=?",
                      (i.guild.id, member.id)).fetchall()
    if not rows:
        return await i.response.send_message("No warnings.", ephemeral=True)
    text = "\n".join(f"`#{r[0]}` <t:{int(r[2])}:d> — {r[1]}" for r in rows)
    await i.response.send_message(embed=discord.Embed(
        title=f"Warnings for {member}", description=text, color=discord.Color.orange()))


@mod.command(description="Lock or unlock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(i: discord.Interaction, locked: bool = True):
    ow = i.channel.overwrites_for(i.guild.default_role)
    ow.send_messages = False if locked else None
    await i.channel.set_permissions(i.guild.default_role, overwrite=ow)
    await i.response.send_message("🔒 Locked." if locked else "🔓 Unlocked.")


@mod.command(description="Set slowmode in seconds (0 = off)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(i: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
    await i.channel.edit(slowmode_delay=seconds)
    await i.response.send_message(f"🐢 Slowmode: {seconds}s")


bot.tree.add_command(mod)

# ---------------------------------------------------------------- Setup
setup = app_commands.Group(name="setup", description="Server setup",
                           default_permissions=discord.Permissions(administrator=True),
                           guild_only=True)


@setup.command(description="Set the welcome channel")
async def welcome(i: discord.Interaction, channel: discord.TextChannel):
    set_setting(i.guild.id, "welcome_channel", channel.id)
    await i.response.send_message(f"Welcome channel: {channel.mention}")


@setup.command(description="Set the auto-role for new members")
async def autorole(i: discord.Interaction, role: discord.Role):
    set_setting(i.guild.id, "autorole", role.id)
    await i.response.send_message(f"Auto-role: {role.mention}")


@setup.command(description="Set the logs channel")
async def logs(i: discord.Interaction, channel: discord.TextChannel):
    set_setting(i.guild.id, "log_channel", channel.id)
    await i.response.send_message(f"Logs channel: {channel.mention}")


@setup.command(description="Post the ticket panel here")
async def tickets(i: discord.Interaction):
    e = discord.Embed(title="Support 🎫", description="Press the button to open a ticket.",
                      color=discord.Color.blurple())
    await i.channel.send(embed=e, view=OpenTicket())
    await i.response.send_message("Panel posted.", ephemeral=True)


bot.tree.add_command(setup)


# ---------------------------------------------------------------- Levels & economy
@bot.tree.command(description="Show your level and XP")
async def rank(i: discord.Interaction, member: discord.Member = None):
    member = member or i.user
    xp, level, coins, _ = get_user(i.guild.id, member.id)
    e = discord.Embed(title=str(member), color=discord.Color.gold())
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Level", value=level)
    e.add_field(name="XP", value=xp)
    e.add_field(name="Coins", value=f"🪙 {coins}")
    await i.response.send_message(embed=e)


@bot.tree.command(description="Top 10 by XP")
async def leaderboard(i: discord.Interaction):
    rows = db.execute("SELECT user_id, xp, level FROM users WHERE guild_id=? "
                      "ORDER BY xp DESC LIMIT 10", (i.guild.id,)).fetchall()
    lines = [f"**{n}.** <@{u}> — level {l} ({x} XP)" for n, (u, x, l) in enumerate(rows, 1)]
    await i.response.send_message(embed=discord.Embed(
        title="🏆 Leaderboard", description="\n".join(lines) or "Empty.",
        color=discord.Color.gold()))


@bot.tree.command(description="Claim your daily coins")
async def daily(i: discord.Interaction):
    _, _, _, last = get_user(i.guild.id, i.user.id)
    if time.time() - last < 86400:
        nxt = int(last + 86400)
        return await i.response.send_message(f"Come back <t:{nxt}:R>.", ephemeral=True)
    reward = random.randint(100, 250)
    db.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE guild_id=? AND user_id=?",
               (reward, time.time(), i.guild.id, i.user.id))
    db.commit()
    await i.response.send_message(f"🪙 You got **{reward}** coins!")


@bot.tree.command(description="Bet coins on a coin flip")
async def gamble(i: discord.Interaction, amount: app_commands.Range[int, 1, 1000000]):
    _, _, coins, _ = get_user(i.guild.id, i.user.id)
    if amount > coins:
        return await i.response.send_message("Not enough coins.", ephemeral=True)
    win = random.random() < 0.5
    delta = amount if win else -amount
    db.execute("UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?",
               (delta, i.guild.id, i.user.id))
    db.commit()
    await i.response.send_message(f"{'🎉 You won' if win else '💸 You lost'} **{amount}** coins!")


@bot.tree.command(description="Send coins to someone")
async def pay(i: discord.Interaction, member: discord.Member,
              amount: app_commands.Range[int, 1, 1000000]):
    _, _, coins, _ = get_user(i.guild.id, i.user.id)
    get_user(i.guild.id, member.id)
    if member.bot or member == i.user or amount > coins:
        return await i.response.send_message("Invalid payment.", ephemeral=True)
    db.execute("UPDATE users SET coins=coins-? WHERE guild_id=? AND user_id=?",
               (amount, i.guild.id, i.user.id))
    db.execute("UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?",
               (amount, i.guild.id, member.id))
    db.commit()
    await i.response.send_message(f"Sent 🪙 {amount} to {member.mention}.")


# ---------------------------------------------------------------- Fun & utility
@bot.tree.command(description="Ask the magic 8-ball")
async def eightball(i: discord.Interaction, question: str):
    a = random.choice(["Yes", "No", "Maybe", "Definitely", "Never", "Ask again later"])
    await i.response.send_message(f"🎱 **{question}**\n{a}")


@bot.tree.command(description="Flip a coin")
async def coinflip(i: discord.Interaction):
    await i.response.send_message(random.choice(["Heads 🪙", "Tails 🪙"]))


@bot.tree.command(description="Roll a die")
async def roll(i: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
    await i.response.send_message(f"🎲 {random.randint(1, sides)}")


@bot.tree.command(description="Create a poll")
async def poll(i: discord.Interaction, question: str, option1: str, option2: str,
               option3: str = None, option4: str = None):
    opts = [o for o in (option1, option2, option3, option4) if o]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    e = discord.Embed(title=f"📊 {question}", color=discord.Color.blurple(),
                      description="\n".join(f"{emojis[n]} {o}" for n, o in enumerate(opts)))
    await i.response.send_message(embed=e)
    msg = await i.original_response()
    for n in range(len(opts)):
        await msg.add_reaction(emojis[n])


@bot.tree.command(description="Start a giveaway")
@app_commands.default_permissions(manage_guild=True)
async def giveaway(i: discord.Interaction, minutes: app_commands.Range[int, 1, 10080], prize: str):
    end = int(time.time() + minutes * 60)
    e = discord.Embed(title=f"🎉 {prize}", color=discord.Color.magenta(),
                      description=f"React with 🎉 to enter!\nEnds <t:{end}:R>")
    await i.response.send_message(embed=e)
    msg = await i.original_response()
    await msg.add_reaction("🎉")
    await asyncio.sleep(minutes * 60)
    msg = await i.channel.fetch_message(msg.id)
    react = discord.utils.get(msg.reactions, emoji="🎉")
    users = [u async for u in react.users() if not u.bot] if react else []
    if users:
        await i.channel.send(f"🏆 {random.choice(users).mention} won **{prize}**!")
    else:
        await i.channel.send("No one entered the giveaway.")


@bot.tree.command(description="Remind me later")
async def remind(i: discord.Interaction, minutes: app_commands.Range[int, 1, 10080], text: str):
    await i.response.send_message(f"⏰ I'll remind you in {minutes} min.", ephemeral=True)
    await asyncio.sleep(minutes * 60)
    await i.channel.send(f"{i.user.mention} ⏰ {text}")


@bot.tree.command(description="Bot latency")
async def ping(i: discord.Interaction):
    await i.response.send_message(f"🏓 {round(bot.latency * 1000)} ms")


@bot.tree.command(description="Show a user's avatar")
async def avatar(i: discord.Interaction, member: discord.Member = None):
    member = member or i.user
    e = discord.Embed(title=str(member)).set_image(url=member.display_avatar.url)
    await i.response.send_message(embed=e)


@bot.tree.command(description="Info about a member")
async def userinfo(i: discord.Interaction, member: discord.Member = None):
    m = member or i.user
    e = discord.Embed(title=str(m), color=m.color)
    e.set_thumbnail(url=m.display_avatar.url)
    e.add_field(name="ID", value=m.id)
    e.add_field(name="Joined", value=discord.utils.format_dt(m.joined_at, "R"))
    e.add_field(name="Created", value=discord.utils.format_dt(m.created_at, "R"))
    e.add_field(name="Top role", value=m.top_role.mention)
    await i.response.send_message(embed=e)


@bot.tree.command(description="Info about the server")
async def serverinfo(i: discord.Interaction):
    g = i.guild
    e = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Members", value=g.member_count)
    e.add_field(name="Channels", value=len(g.channels))
    e.add_field(name="Roles", value=len(g.roles))
    e.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"))
    await i.response.send_message(embed=e)


@bot.tree.command(description="List all commands")
async def help(i: discord.Interaction):
    e = discord.Embed(title="Commands", color=discord.Color.blurple())
    e.add_field(name="🛡️ Moderation", value="/mod kick, ban, unban, timeout, untimeout, "
                "clear, warn, warns, lock, slowmode", inline=False)
    e.add_field(name="⚙️ Setup", value="/setup welcome, autorole, logs, tickets", inline=False)
    e.add_field(name="📈 Levels & 🪙 Economy", value="/rank, leaderboard, daily, gamble, pay",
                inline=False)
    e.add_field(name="🎮 Fun & tools", value="/eightball, coinflip, roll, poll, giveaway, "
                "remind, ping, avatar, userinfo, serverinfo", inline=False)
    await i.response.send_message(embed=e)


# ---------------------------------------------------------------- Errors
@bot.tree.error
async def on_error(i: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission for that."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing permissions to do that."
        
