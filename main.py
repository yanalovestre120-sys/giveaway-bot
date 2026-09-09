import discord
from discord.ext import commands
import json
import os
from datetime import datetime, timedelta, timezone
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==================== CONFIG ====================
TOKEN = os.environ["TOKEN"]
PREFIX = "+"
WELCOME_CHANNEL_ID = 1547032394136031293

SPECIAL_USERS = [
    "1223210346995777579",
    "1442226827346186282",
    "1391635894045380619",
    "937559709748166697",
    "1470082305564344352",
    "1263900802083459227",
]

# +ban +unban only (special users)
BAN_COMMAND_USERS = list(SPECIAL_USERS)

# +kick only (special users)
KICK_COMMAND_USERS = list(SPECIAL_USERS)

# +bl +unbl only (special users)
BL_COMMAND_USERS = list(SPECIAL_USERS)

# Role IDs for permission levels.
# After the bot finds them, it saves the ROLE IDs — so if you rename a role,
# permissions still work without updating this list.
ROLES = {
    # Perm level -> role IDs (primary) + optional names (display / fallback only)
    1: {
        "ids": [1547030963031380019],  # idea helper
        "names": ["idea helper"],
    },
    2: {
        "ids": [1547029458408710285],  # provider finder
        "names": ["provider finder"],
    },
    3: {
        "ids": [1547028673503428628],  # low tear gw manager
        "names": ["low tear gw manager"],
    },
    4: {
        "ids": [1547028347190644806],  # mid tear gw manager
        "names": ["mid tear gw manager"],
    },
    5: {
        "ids": [
            1546989569071775785,  # main helper
            1546995760111943772,  # gw manager
            1547028212859805836,  # high tear gw manager
        ],
        "names": ["main helper", "gw manager", "high tear gw manager"],
    },
    6: {
        "ids": [1547024942439206963],  # owner
        "names": ["owner"],
    },
}

BLACKLISTED_WORDS = [
    # slurs / hate
    "nigger", "nigga", "faggot", "fag", "tranny", "retard", "retarded",
    "nazi", "hitler", "kike", "chink", "spic", "coon", "beaner",
    # sexual / crude
    "femboy", "d*ck", "dick", "cock", "pussy", "whore", "slut", "hoe",
    "porn", "nudes", "onlyfans",
    # self-harm / threats
    "kys", "kill yourself", "kill urself", "hang yourself", "go die",
    "neck yourself", "end yourself",
]

# Scam / nitro bait — separate message + sanction reason "link"
SCAM_WORDS = [
    "free nitro", "discord.gift", "steamcommunity.com/gift",
    "free nitro giveaway", "nitro gift", "claim nitro",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.moderation = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ==================== DATA ====================
os.makedirs("data", exist_ok=True)
SANCTIONS_FILE = "data/sanctions.json"
BLACKLIST_FILE = "data/blacklist.json"
SNIPE_FILE = "data/snipe.json"
ROLE_PERMS_FILE = "data/role_perms.json"
TEMPROLES_FILE = "data/temproles.json"
COMMAND_PERMS_FILE = "data/command_perms.json"
GIFTS_FILE = "data/gifts.json"

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

sanctions_data = load_json(SANCTIONS_FILE, {})
blacklist = load_json(BLACKLIST_FILE, [])
snipe_data = load_json(SNIPE_FILE, {})
clearing_channels = set()
role_perms = load_json(ROLE_PERMS_FILE, {})
temproles_data = load_json(TEMPROLES_FILE, [])
_temprole_tasks = {}
command_overrides = load_json(COMMAND_PERMS_FILE, {})
gifts_data = load_json(GIFTS_FILE, {})  # message_id -> gift dict
_gift_tasks = {}

# Default required perm level per command (overridable via +changeperm)
DEFAULT_COMMAND_PERMS = {
    "warn": 1,
    "tempmute": 1,
    "unmute": 1,
    "mutelist": 1,
    "sanctions": 1,
    "perms": 1,
    "del": 2,  # del sanction
    "rolemembers": 2,
    "derank": 3,
    "clearwarns": 3,
    "addrole": 3,
    "delrole": 3,
    "clear": 4,
    "create": 4,
    "gcreate": 3,   # low tier gw manager+
    "gend": 3,
    "greroll": 3,
    "glist": 3,
    "gcancel": 4,
    "temprole": 6,
    "syncroles": 6,
    "modstats": 6,
    "banlist": 5,
    "baninfo": 5,
    "changeperm": 6,
    "ban": 99,  # special users only (handled separately)
    "unban": 99,
    "kick": 99,
    "bl": 99,
    "unbl": 99,
}

def save_sanctions(): save_json(SANCTIONS_FILE, sanctions_data)
def save_blacklist(): save_json(BLACKLIST_FILE, blacklist)
def save_snipe(): save_json(SNIPE_FILE, snipe_data)
def save_role_perms(): save_json(ROLE_PERMS_FILE, role_perms)
def save_temproles(): save_json(TEMPROLES_FILE, temproles_data)
def save_command_perms(): save_json(COMMAND_PERMS_FILE, command_overrides)
def save_gifts(): save_json(GIFTS_FILE, gifts_data)

def get_cmd_perm(name: str) -> int:
    key = name.lower().strip()
    if key in command_overrides:
        val = command_overrides[key]
        if val is None or str(val).lower() == "none":
            return 99  # effectively disabled / special only
        try:
            return int(val)
        except Exception:
            return DEFAULT_COMMAND_PERMS.get(key, 5)
    return DEFAULT_COMMAND_PERMS.get(key, 5)

# ==================== HELPERS ====================
def _match_role_exact(guild: discord.Guild, name: str):
    name = name.strip()
    if not name:
        return None
    role = discord.utils.find(lambda r, n=name: r.name == n, guild.roles)
    if role:
        return role
    role = discord.utils.find(lambda r, n=name: r.name.lower() == n.lower(), guild.roles)
    if role:
        return role
    if "•" in name:
        key = name.split("•")[-1].strip()
        role = discord.utils.find(lambda r, k=key: r.name.lower() == k.lower(), guild.roles)
        if role:
            return role
    return None

def resolve_role_ids(guild: discord.Guild, force: bool = False) -> dict:
    gid = str(guild.id)
    mapping = {}
    used_ids = set()
    for level in sorted(ROLES.keys(), reverse=True):
        entry = ROLES[level]
        found = []
        if isinstance(entry, dict):
            for rid in entry.get("ids", []):
                if rid not in used_ids:
                    found.append(rid)
                    used_ids.add(rid)
            for name in entry.get("names", []):
                role = _match_role_exact(guild, name)
                if role and role.id not in used_ids:
                    found.append(role.id)
                    used_ids.add(role.id)
        else:
            for name in entry:
                role = _match_role_exact(guild, name)
                if role and role.id not in used_ids:
                    found.append(role.id)
                    used_ids.add(role.id)
        mapping[level] = found

    role_perms[gid] = {str(k): v for k, v in mapping.items()}
    save_role_perms()
    return {k: set(v) for k, v in mapping.items()}

def get_perm_level(member: discord.Member) -> int:
    if str(member.id) in SPECIAL_USERS:
        return 99
    if not member.guild:
        return 0
    cache = resolve_role_ids(member.guild)
    member_ids = {r.id for r in member.roles}
    highest = 0
    for level, role_ids in cache.items():
        if member_ids & role_ids:
            highest = max(highest, level)
    return highest

def has_perm(member: discord.Member, level: int) -> bool:
    return get_perm_level(member) >= level

def can_moderate(moderator: discord.Member, target: discord.Member) -> bool:
    if moderator is None or target is None:
        return False
    if moderator.id == target.id:
        return False
    if str(moderator.id) in SPECIAL_USERS:
        return True
    if moderator.id == moderator.guild.owner_id:
        return True
    if target.id == target.guild.owner_id:
        return False
    if str(target.id) in SPECIAL_USERS:
        return False
    mod_level = get_perm_level(moderator)
    target_level = get_perm_level(target)
    # Regular members (no staff role) can always be moderated by staff
    if target_level == 0:
        return True
    # Staff targets: mod must have strictly higher perm level
    if mod_level <= target_level:
        return False
    # Also respect Discord role hierarchy between staff
    try:
        if moderator.top_role <= target.top_role:
            return False
    except Exception:
        return False
    return True

# ==================== EVENTS ====================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Streaming(
            name="SAB giveaways",
            url="https://www.twitch.tv/discord"
        )
    )
    for guild in bot.guilds:
        try:
            resolve_role_ids(guild)
            print(f"Perm roles loaded for: {guild.name}")
        except Exception as e:
            print(f"Role resolve failed for {guild.name}: {e}")
    try:
        await restore_temproles()
        print(f"Temp roles restored: {len(temproles_data)} pending")
    except Exception as e:
        print(f"Temp role restore failed: {e}")
    try:
        await restore_gifts()
        print(f"Gifts restored: {len([g for g in gifts_data.values() if g.get('active')])} active")
    except Exception as e:
        print(f"Gift restore failed: {e}")

# ==================== EVENTS (all the rest) ====================
@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    if message.channel.id in clearing_channels:
        return
    if message.content and message.content.startswith(f"{PREFIX}clear"):
        return
    attachments = []
    image_url = None
    for att in message.attachments:
        attachments.append({"url": att.url, "filename": att.filename, "content_type": att.content_type or ""})
        if image_url is None and (att.content_type and att.content_type.startswith("image/") or att.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))):
            image_url = att.url
    stickers = [s.name for s in getattr(message, "stickers", [])] if getattr(message, "stickers", None) else []
    content = message.content or ""
    if not content and not attachments and not stickers:
        content = "*no text*"
    elif not content and attachments:
        content = ""
    snipe_data[str(message.channel.id)] = {
        "content": content if content else "*attachment only*",
        "author": str(message.author),
        "author_id": message.author.id,
        "avatar": str(message.author.display_avatar.url),
        "time": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "image_url": image_url,
        "attachments": attachments,
        "stickers": stickers,
    }
    save_snipe()

@bot.event
async def on_bulk_message_delete(messages):
    return

async def filter_bad_content(message) -> bool:
    if not message.guild or message.author.bot:
        return False
    content = message.content or ""
    if not content:
        return False
    content_lower = content.lower()
    member = message.guild.get_member(message.author.id)
    if member and has_perm(member, 5):
        return False

    async def _warn_and_cleanup(text: str):
        try:
            warn_msg = await message.channel.send(text)
        except Exception:
            return
        try:
            await warn_msg.delete(delay=3)
        except Exception:
            pass

    for word in SCAM_WORDS:
        if word in content_lower:
            try:
                await message.delete()
            except Exception:
                pass
            add_sanction(message.author.id, "link", bot.user.id if bot.user else 0)
            await _warn_and_cleanup(f"{message.author.mention} this a some bad things you got going")
            emb = discord.Embed(title="Scam / Link Filter", color=0x000000, timestamp=datetime.now())
            emb.add_field(name="User", value=f"{message.author} (`{message.author.id}`)")
            emb.add_field(name="Matched", value=word)
            emb.add_field(name="Message", value=f"```{content[:800]}```", inline=False)
            await send_log(emb)
            return True

    for word in BLACKLISTED_WORDS:
        if word in content_lower:
            try:
                await message.delete()
            except Exception:
                pass
            add_sanction(message.author.id, "bad word", bot.user.id if bot.user else 0)
            await _warn_and_cleanup(f"{message.author.mention} you said a blacklisted word")
            emb = discord.Embed(title="Blacklisted Word", color=0x000000, timestamp=datetime.now())
            emb.add_field(name="User", value=f"{message.author} (`{message.author.id}`)")
            emb.add_field(name="Word", value=word)
            emb.add_field(name="Message", value=f"```{content[:800]}```", inline=False)
            await send_log(emb)
            return True

    return False

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if len(content) < 3:
            await message.channel.send(f"My prefix on this server is: `{PREFIX}`")
            return
    if await filter_bad_content(message):
        return
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    if after.author.bot or not after.guild:
        return
    if (before.content or "") == (after.content or ""):
        return
    await filter_bad_content(after)

@bot.event
async def on_member_join(member):
    if str(member.id) in blacklist:
        try:
            await member.ban(reason="Blacklisted")
        except Exception:
            pass
        return
    try:
        ch = bot.get_channel(WELCOME_CHANNEL_ID)
        if ch is None:
            ch = await bot.fetch_channel(WELCOME_CHANNEL_ID)
        count = member.guild.member_count or len(member.guild.members)
        emb = discord.Embed(
            title="New Member Joined!",
            description=(
                f"👏 Welcome {member.mention}!\n\n"
                f"Glad to have you here. Check out our channels and enjoy your stay! 🎉"
            ),
            color=0x000000,
            timestamp=datetime.now(timezone.utc),
        )
        emb.add_field(
            name="Account Created",
            value=discord.utils.format_dt(member.created_at, "R"),
            inline=True,
        )
        emb.add_field(
            name="Member Count",
            value=f"#{count}",
            inline=True,
        )
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.set_footer(text="Founder: Raynox • Bot maker: Teix")
        await ch.send(embed=emb)
    except Exception as e:
        print(f"Welcome message failed: {e}")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # ---------- TIMEOUT TRACKING ----------
    try:
        before_to = before.timed_out_until
        after_to = after.timed_out_until
    except Exception:
        return
    if after_to is not None and before_to != after_to:
        mod_id = bot.user.id if bot.user else 0
        reason = "timeout"
        try:
            async for entry in after.guild.audit_logs(limit=6, action=discord.AuditLogAction.member_update):
                if entry.target and entry.target.id == after.id:
                    if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 20:
                        if entry.user:
                            mod_id = entry.user.id
                        if entry.reason:
                            reason = entry.reason
                        break
        except Exception:
            pass
        if bot.user and mod_id == bot.user.id:
            return
        try:
            now = datetime.now(timezone.utc)
            until = after_to if after_to.tzinfo else after_to.replace(tzinfo=timezone.utc)
            secs = max(0, int((until - now).total_seconds()))
            if secs >= 86400:
                dur = f"{secs // 86400}d"
            elif secs >= 3600:
                dur = f"{secs // 3600}h"
            elif secs >= 60:
                dur = f"{secs // 60}m"
            else:
                dur = f"{secs}s"
        except Exception:
            dur = "?"
        text = f"timeout {dur}"
        if reason and reason != "timeout":
            text = f"timeout {dur} - {reason}"
        add_sanction(after.id, text, mod_id)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return
    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument, commands.TooManyArguments, commands.UserInputError)):
        name = ctx.command.name if ctx.command else "command"
        try:
            await ctx.send(f"invalid {name}")
        except Exception:
            pass
        return
    return

# ==================== COMMANDS ====================
@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! `{round(bot.latency*1000)}ms`")

@bot.command()
async def perms(ctx):
    cache = resolve_role_ids(ctx.guild)
    emb = discord.Embed(title="Permissions", color=0x000000)
    for level in sorted(ROLES.keys()):
        mentions = []
        seen = set()
        for rid in cache.get(level, set()):
            if rid in seen:
                continue
            role = ctx.guild.get_role(rid)
            if not role:
                continue
            seen.add(rid)
            mentions.append(role.mention)
        entry = ROLES.get(level, {})
        names = entry.get("names", []) if isinstance(entry, dict) else []
        for name in names:
            role = discord.utils.find(lambda r, n=name: r.name == n or r.name.lower() == n.lower(), ctx.guild.roles)
            if role and role.id not in seen:
                seen.add(role.id)
                mentions.append(role.mention)
        value = "\n".join(mentions) if mentions else "None found"
        if level == 6:
            value += "\n\n**Highest staff — access to advanced commands**"
        emb.add_field(name=f"Perm {level}", value=value, inline=False)
    emb.set_footer(text="Role IDs are saved — renaming a role will not break perms. Use +syncroles to rescan names.")
    await ctx.send(embed=emb)

@bot.command()
async def syncroles(ctx):
    if not has_perm(ctx.author, get_cmd_perm("syncroles")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    cache = resolve_role_ids(ctx.guild, force=True)
    lines = []
    for level in sorted(cache.keys()):
        roles = []
        for rid in cache[level]:
            role = ctx.guild.get_role(rid)
            if role:
                roles.append(role.mention)
        lines.append(f"**Perm {level}:** {' '.join(roles) if roles else 'none'}")
    emb = discord.Embed(
        title="Roles synced",
        description="\n".join(lines) or "No roles matched.",
        color=0x000000
    )
    emb.set_footer(text="Saved role IDs. Renaming these roles will still keep the same perms.")
    await ctx.send(embed=emb)

@bot.command()
async def snipe(ctx):
    data = snipe_data.get(str(ctx.channel.id))
    if not data:
        return await empty_result(ctx, "Nothing to snipe.")
    desc = censor_blacklisted(data.get("content") or "")
    if data.get("stickers"):
        desc = (desc + "\n" if desc and desc != "*attachment only*" else "") + "Sticker: " + ", ".join(data["stickers"])
    if not desc:
        desc = "*attachment only*"
    emb = discord.Embed(title="Snipe", description=desc, color=0x000000)
    emb.add_field(name="Author", value=data["author"], inline=True)
    deleted_text = data.get("time", "unknown")
    if data.get("timestamp"):
        try:
            ts = datetime.fromisoformat(data["timestamp"])
            deleted_text = discord.utils.format_dt(ts, "R")
        except:
            pass
    emb.add_field(name="Deleted", value=deleted_text, inline=True)
    if data.get("image_url"):
        emb.set_image(url=data["image_url"])
    other = []
    for att in data.get("attachments") or []:
        is_img = (att.get("content_type") or "").startswith("image/") or att.get("filename", "").lower().endswith(((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")))
        if not is_img and att.get("url"):
            other.append(f"[{att.get('filename', 'file')}]({att['url']})")
    if other:
        emb.add_field(name="Files", value="\n".join(other[:5]), inline=False)
    emb.set_footer(text="Snipe")
    await ctx.send(embed=emb)

@bot.command(aliases=["warns"])
async def sanctions(ctx, target: str = None):
    try:
        user = None
        uid = None
        if target:
            raw = target.strip().replace("<@", "").replace("!", "").replace(">", "")
            if raw.isdigit():
                uid = raw
                try:
                    user = await bot.fetch_user(int(uid))
                except Exception:
                    user = None
            else:
                user = await get_target(ctx, target)
                if user:
                    uid = str(user.id)
        else:
            user = await get_target(ctx, None)
            if user is None:
                user = ctx.author
            uid = str(user.id)
        if not uid:
            return await ctx.send("invalid sanctions")
        lst = sanctions_data.get(str(uid), [])
        display = str(user) if user else f"User `{uid}`"
        if not lst:
            return await empty_result(ctx, f"**{display}** has no sanctions.")
        # Newest first, renumber 1, 2, 3... (Crow Bots style)
        ordered = list(reversed(lst))
        lines = []
        for i, s in enumerate(ordered, 1):
            date = s.get("date", "?")
            reason = s.get("reason", "No reason")
            lines.append(f"{i} - {date}: {reason}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        emb = discord.Embed(description=text, color=0x000000)
        if user is not None:
            avatar = getattr(getattr(user, "display_avatar", None), "url", None)
            emb.set_author(name=str(user), icon_url=avatar)
        else:
            emb.set_author(name=f"User {uid}")
        emb.set_footer(text="Sanctions")
        await ctx.send(embed=emb)
    except Exception as e:
        await ctx.send(f"Failed to load sanctions: `{e}`")

@bot.command(name="del")
async def del_sanction(ctx, action: str = None, arg1: str = None, arg2: str = None):
    if action != "sanction":
        return
    if not has_perm(ctx.author, get_cmd_perm("del")):
        return
    user = None
    number = None
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
    elif ctx.message.reference:
        user = await get_target(ctx, None)
    if arg1 and arg1.isdigit() and arg2 is None:
        number = arg1
    elif arg1 is not None and arg2 is not None and arg2.isdigit():
        if user is None:
            user = await get_target(ctx, arg1)
        number = arg2
    elif arg1 is not None and not arg1.isdigit() and arg2 is not None and arg2.isdigit():
        if user is None:
            user = await get_target(ctx, arg1)
        number = arg2
    if not user or not number or not str(number).isdigit():
        return await ctx.send("invalid del")
    uid = str(user.id)
    num = int(number)
    if uid not in sanctions_data or not any(s["id"] == num for s in sanctions_data[uid]):
        return await ctx.send("invalid del")
    deleted = next(s for s in sanctions_data[uid] if s["id"] == num)
    sanctions_data[uid] = [s for s in sanctions_data[uid] if s["id"] != num]
    for i, s in enumerate(sanctions_data[uid], 1):
        s["id"] = i
    save_sanctions()
    await ctx.send(f"Sanction deleted: {deleted['date']}: {deleted['reason']}")
    log = discord.Embed(title="Del Sanction", color=0x000000, timestamp=datetime.now())
    log.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
    log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
    log.add_field(name="Deleted", value=f"{deleted['date']}: {deleted['reason']}", inline=False)
    await send_log(log)

@bot.command()
async def warn(ctx, *, args: str = None):
    if not has_perm(ctx.author, get_cmd_perm("warn")):
        return
    user = None
    reason = "No reason provided"
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        if args:
            reason = args
            for m in ctx.message.mentions:
                reason = reason.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
            reason = reason.strip() or "No reason provided"
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        if args:
            reason = args.strip()
    elif args:
        parts = args.split(None, 1)
        user = await get_target(ctx, parts[0])
        if user and len(parts) > 1:
            reason = parts[1]
        elif not user:
            return await ctx.send("invalid warn")
    if not user:
        return await ctx.send("invalid warn")
    target_member = await get_member(ctx.guild, user)
    if target_member and not can_moderate(ctx.author, target_member):
        return await ctx.send("You can't warn someone with an equal or higher rank.")
    add_sanction(user.id, reason, ctx.author.id)
    emb = discord.Embed(title="warn", description=f"{user.mention} was warned\nreason: {reason}", color=0x000000)
    await ctx.send(embed=emb)
    log = discord.Embed(title="Warn", color=0x000000, timestamp=datetime.now())
    log.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
    log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
    log.add_field(name="Reason", value=reason, inline=False)
    await send_log(log)

@bot.command()
async def clearwarns(ctx, target: str = None):
    if not has_perm(ctx.author, get_cmd_perm("clearwarns")):
        return
    user = await get_target(ctx, target)
    if not user:
        return await ctx.send("invalid clearwarns")
    target_member = await get_member(ctx.guild, user)
    if target_member and not can_moderate(ctx.author, target_member):
        return await ctx.send("You can't clear warns for someone with an equal or higher rank.")
    sanctions_data[str(user.id)] = []
    save_sanctions()
    await ctx.send(f"Cleared all sanctions for **{user}**")
    log = discord.Embed(title="Clear Warns", color=0x000000, timestamp=datetime.now())
    log.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
    log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
    await send_log(log)

@bot.command()
async def tempmute(ctx, *, args: str = None):
    if not has_perm(ctx.author, get_cmd_perm("tempmute")):
        return
    if not args:
        return await ctx.send("invalid tempmute")
    user = None
    duration = None
    reason = "No reason"
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        rest = args
        for m in ctx.message.mentions:
            rest = rest.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        rest = rest.strip()
        parts = rest.split(None, 1)
        duration = parts[0] if parts else None
        if len(parts) > 1:
            reason = parts[1]
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        parts = args.strip().split(None, 1)
        duration = parts[0] if parts else None
        if len(parts) > 1:
            reason = parts[1]
    else:
        parts = args.split(None, 2)
        if not parts:
            return await ctx.send("invalid tempmute")
        user = await get_target(ctx, parts[0])
        duration = parts[1] if len(parts) > 1 else None
        reason = parts[2] if len(parts) > 2 else "No reason"
    if not user or not duration:
        return await ctx.send("invalid tempmute")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid tempmute")
    if member.id == ctx.author.id:
        return await ctx.send("invalid tempmute")
    if not can_moderate(ctx.author, member):
        return await ctx.send("You can't tempmute someone with an equal or higher rank.")
    delta = parse_duration(duration)
    if not delta:
        return await ctx.send("Invalid duration (examples: `30s` `10m` `1h` `7d`)")
    if delta.total_seconds() > 28 * 86400:
        return await ctx.send("Max timeout is 28 days.")
    try:
        await member.timeout(delta, reason=reason)
        add_sanction(user.id, f"timeout {duration} - {reason}", ctx.author.id)
        await ctx.send(f"Successfully timed out {member.mention} {duration} for the following reason: `{reason}`")
        log = discord.Embed(title="Tempmute", color=0x000000, timestamp=datetime.now())
        log.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        log.add_field(name="Duration", value=duration, inline=True)
        log.add_field(name="Reason", value=reason, inline=True)
        await send_log(log)
    except discord.Forbidden:
        await ctx.send("Missing permissions: move my role **above** the target's role and enable **Timeout Members** for me.")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def unmute(ctx, target: str = None):
    if not has_perm(ctx.author, get_cmd_perm("unmute")):
        return
    user = await get_target(ctx, target)
    if not user:
        return await ctx.send("invalid unmute")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid unmute")
    if not can_moderate(ctx.author, member):
        return await ctx.send("You can't unmute someone with an equal or higher rank.")
    try:
        await member.timeout(None)
        await ctx.send(f"Unmuted {member.mention} successfully")
        log = discord.Embed(title="Unmute", color=0x000000, timestamp=datetime.now())
        log.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        await send_log(log)
    except Exception as e:
        await ctx.send(f"Failed to unmute: {e}")

@bot.command()
async def mutelist(ctx):
    if not has_perm(ctx.author, get_cmd_perm("mutelist")):
        return
    muted = [m for m in ctx.guild.members if m.is_timed_out() and m.timed_out_until]
    if not muted:
        return await empty_result(ctx, "There are no muted members.")
    muted.sort(key=lambda m: m.timed_out_until, reverse=True)
    def format_remaining(until):
        now = datetime.now(timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        delta = until - now
        if delta.total_seconds() <= 0:
            return "0.0 days 0.0 hours and 0.0 minutes"
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{days}.0 days {hours}.0 hours and {minutes}.0 minutes"
    lines = []
    max_show = 40
    for m in muted[:max_show]:
        remaining = format_remaining(m.timed_out_until)
        lines.append(f"{m.mention} : {remaining}")
    description = "**Timeouts**\n" + "\n".join(lines)
    not_shown = len(muted) - max_show
    if not_shown > 0:
        description += f"\n{not_shown} not showed"
    emb = discord.Embed(
        title="Current mutes",
        description=description,
        color=0x000000
    )
    await ctx.send(embed=emb)

@bot.command()
async def ban(ctx, *, args: str = None):
    if str(ctx.author.id) not in BAN_COMMAND_USERS:
        return

    user = None
    reason = "No reason"

    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        if args:
            reason = args
            for m in ctx.message.mentions:
                reason = reason.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
            reason = reason.strip() or "No reason"
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        if args:
            reason = args.strip() or "No reason"
    elif args:
        parts = args.split(None, 1)
        user = await get_target(ctx, parts[0])
        if user and len(parts) > 1:
            reason = parts[1]
        elif not user:
            pass

    if not user:
        return await ctx.send("invalid ban")
    if user.id == ctx.author.id:
        return await ctx.send("invalid ban")

    try:
        await ctx.guild.ban(user, reason=reason)

        if reason and reason != "No reason":
            await ctx.send(f"Banned **{user}** | Reason: {reason}")
        else:
            await ctx.send(f"Banned **{user}**")

    except discord.Forbidden:
        await ctx.send("I don't have permission to ban that user (check my role position + Ban Members permission).")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def unban(ctx, user_id: str = None):
    if str(ctx.author.id) not in BAN_COMMAND_USERS:
        return
    if not user_id:
        return await ctx.send("invalid unban")

    # Clean the ID (supports raw ID or mention)
    raw = user_id.strip().replace("<@", "").replace("!", "").replace(">", "")
    if not raw.isdigit():
        return await ctx.send("invalid unban")

    try:
        user = await bot.fetch_user(int(raw))
    except (ValueError, discord.NotFound, discord.HTTPException):
        return await ctx.send("invalid unban")

    try:
        await ctx.guild.unban(user)
        await ctx.send(f"Unbanned **{user}**")

    except discord.NotFound:
        await ctx.send("This user is not banned.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to unban members.")
    except Exception as e:
        await ctx.send(f"Failed to unban: {e}")

@bot.command()
async def kick(ctx, *, args: str = None):
    if str(ctx.author.id) not in KICK_COMMAND_USERS:
        return
    user = None
    reason = "No reason"
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        if args:
            reason = args
            for m in ctx.message.mentions:
                reason = reason.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
            reason = reason.strip() or "No reason"
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        if args:
            reason = args.strip() or "No reason"
    elif args:
        parts = args.split(None, 1)
        user = await get_target(ctx, parts[0])
        if user and len(parts) > 1:
            reason = parts[1]
    if not user:
        return await ctx.send("invalid kick")
    if user.id == ctx.author.id:
        return await ctx.send("invalid kick")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid kick")
    if not can_moderate(ctx.author, member):
        return await ctx.send("You can't kick someone with an equal or higher rank.")
    try:
        await member.kick(reason=reason)
        if reason and reason != "No reason":
            await ctx.send(f"Kicked **{user}** | Reason: {reason}")
        else:
            await ctx.send(f"Kicked **{user}**")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def clear(ctx, *args):
    if not has_perm(ctx.author, get_cmd_perm("clear")):
        return
    amount = 10
    target = None
    if ctx.message.mentions:
        target = ctx.message.mentions[0]
        for a in reversed(args):
            if str(a).isdigit():
                amount = int(a)
                break
    elif ctx.message.reference:
        target = await get_target(ctx, None)
        if args and str(args[0]).isdigit():
            amount = int(args[0])
    elif args:
        if len(args) == 1 and str(args[0]).isdigit():
            num = int(args[0])
            if num > 10_000_000_000_000_000:
                try:
                    target = await bot.fetch_user(num)
                    amount = 100
                except Exception:
                    amount = max(1, min(num, 100))
            else:
                amount = max(1, min(num, 100))
        else:
            try:
                target = await get_target(ctx, args[0])
            except Exception:
                target = None
            if target is None and str(args[0]).isdigit():
                try:
                    target = await bot.fetch_user(int(args[0]))
                except Exception:
                    pass
            if len(args) > 1 and str(args[1]).isdigit():
                amount = int(args[1])
            elif target:
                amount = 100
    if target:
        amount = max(1, min(amount, 1000))
    else:
        amount = max(1, min(amount, 100))
    def check(m):
        if m.id == ctx.message.id:
            return True
        if target is None:
            return True
        return m.author.id == target.id
    clearing_channels.add(ctx.channel.id)
    try:
        if target is None:
            await ctx.channel.purge(limit=amount + 1, check=check)
        else:
            left = amount
            while left > 0:
                batch = min(100, left + 1)
                purged = await ctx.channel.purge(limit=batch, check=check)
                removed = sum(1 for m in purged if m.id != ctx.message.id)
                left -= max(removed, 1)
                if len(purged) < batch:
                    break
    except Exception:
        try:
            await ctx.message.delete()
        except Exception:
            pass
    finally:
        clearing_channels.discard(ctx.channel.id)

def find_role(guild, role_query: str):
    if not role_query:
        return None
    q = role_query.strip()
    if q.startswith("<@&") and q.endswith(">"):
        rid = q[3:-1]
        if rid.isdigit():
            return guild.get_role(int(rid))
    if q.isdigit():
        role = guild.get_role(int(q))
        if role:
            return role
    q_lower = q.lower()
    role = discord.utils.find(lambda r: r.name.lower() == q_lower, guild.roles)
    if role:
        return role
    starts = [r for r in guild.roles if r.name.lower().startswith(q_lower) and r.name != "@everyone"]
    if len(starts) == 1:
        return starts[0]
    if len(starts) > 1:
        starts.sort(key=lambda r: len(r.name))
        return starts[0]
    contains = [r for r in guild.roles if q_lower in r.name.lower() and r.name != "@everyone"]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        contains.sort(key=lambda r: (len(r.name), r.name.lower()))
        return contains[0]
    return None

@bot.command()
async def temprole(ctx, *, args: str = None):
    if not has_perm(ctx.author, get_cmd_perm("temprole")):
        return
    if not args:
        return await ctx.send("invalid temprole")
    rest = args
    user = None
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        for m in ctx.message.mentions:
            rest = rest.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
    if user is None and ctx.message.reference:
        ref = ctx.message.reference
        if ref.resolved and hasattr(ref.resolved, "author"):
            user = ref.resolved.author
        elif ref.message_id:
            try:
                ref_msg = await ctx.channel.fetch_message(ref.message_id)
                user = ref_msg.author
            except Exception:
                user = await get_target(ctx, None)
    tokens = rest.strip().split()
    if not tokens:
        return await ctx.send("invalid temprole")
    if user is None and tokens[0].isdigit() and len(tokens[0]) >= 15:
        user = await get_target(ctx, tokens[0])
        tokens = tokens[1:]
    if not tokens:
        return await ctx.send("invalid temprole")
    duration = None
    duration_idx = None
    for i, tok in enumerate(tokens):
        if parse_duration(tok):
            duration = tok
            duration_idx = i
            break
    if duration is None:
        return await ctx.send("invalid temprole")
    role_tokens = tokens[:duration_idx] + tokens[duration_idx + 1:]
    role_name = " ".join(role_tokens).strip()
    if not role_name:
        return await ctx.send("invalid temprole")
    if user is None:
        user = ctx.author
    delta = parse_duration(duration)
    if not delta or delta.total_seconds() < 1:
        return await ctx.send("invalid temprole")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid temprole")
    role = find_role(ctx.guild, role_name)
    if not role:
        return await ctx.send("invalid temprole")
    # Only Perm 6 / owner / special can assign roles >= their own top role
    if role >= ctx.author.top_role:
        if ctx.author.id != ctx.guild.owner_id and str(ctx.author.id) not in SPECIAL_USERS and not has_perm(ctx.author, 6):
            return await ctx.send("invalid temprole")
    if role >= ctx.guild.me.top_role:
        return await ctx.send("invalid temprole")
    try:
        if role not in member.roles:
            await member.add_roles(role, reason=f"Temp role {duration} by {ctx.author}")
        ends_at = datetime.now(timezone.utc) + delta
        schedule_temprole(ctx.guild.id, member.id, role.id, ends_at)
        await ctx.send(
            f"Gave **{role.name}** to {member.mention} for **{duration}** "
            f"(removes {discord.utils.format_dt(ends_at, 'R')})",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        log = discord.Embed(title="Temp Role", color=0x000000, timestamp=datetime.now())
        log.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        log.add_field(name="Moderator", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        log.add_field(name="Role", value=f"{role.name} (`{role.id}`)", inline=True)
        log.add_field(name="Duration", value=duration, inline=True)
        await send_log(log)
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def addrole(ctx, *, args: str = None):
    if not has_perm(ctx.author, get_cmd_perm("addrole")):
        return
    if not args:
        return await ctx.send("invalid addrole")
    user = None
    role_name = None
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        role_name = args
        for m in ctx.message.mentions:
            role_name = role_name.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        role_name = role_name.strip()
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        role_name = args.strip()
    else:
        parts = args.split(None, 1)
        if len(parts) >= 1 and parts[0].isdigit() and ctx.guild and ctx.guild.get_member(int(parts[0])):
            user = await get_target(ctx, parts[0])
            role_name = parts[1] if len(parts) > 1 else None
        elif len(parts) == 1 and parts[0].isdigit() and find_role(ctx.guild, parts[0]):
            user = ctx.author
            role_name = parts[0]
        else:
            user = ctx.author
            role_name = args.strip()
    if not user or not role_name:
        return await ctx.send("invalid addrole")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid addrole")
    role = find_role(ctx.guild, role_name)
    if not role:
        return await ctx.send("invalid addrole")
    # Only Perm 6 / owner / special can assign roles >= their own top role
    if role >= ctx.author.top_role:
        if ctx.author.id != ctx.guild.owner_id and str(ctx.author.id) not in SPECIAL_USERS and not has_perm(ctx.author, 6):
            return await ctx.send("invalid addrole")
    if role >= ctx.guild.me.top_role:
        return await ctx.send("invalid addrole")
    if role in member.roles:
        return await ctx.send(f"{member.mention} already has the {role.mention} role.")
    try:
        await member.add_roles(role)
        await ctx.send("1 role was added to 1 member")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def delrole(ctx, *, args: str = None):
    if not has_perm(ctx.author, get_cmd_perm("delrole")):
        return
    if not args:
        return await ctx.send("invalid delrole")
    user = None
    role_name = None
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        role_name = args
        for m in ctx.message.mentions:
            role_name = role_name.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        role_name = role_name.strip()
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        role_name = args.strip()
    else:
        parts = args.split(None, 1)
        if len(parts) >= 1 and parts[0].isdigit() and ctx.guild and ctx.guild.get_member(int(parts[0])):
            user = await get_target(ctx, parts[0])
            role_name = parts[1] if len(parts) > 1 else None
        elif len(parts) == 1 and parts[0].isdigit() and find_role(ctx.guild, parts[0]):
            user = ctx.author
            role_name = parts[0]
        else:
            user = ctx.author
            role_name = args.strip()
    if not user or not role_name:
        return await ctx.send("invalid delrole")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid delrole")
    role = find_role(ctx.guild, role_name)
    if not role:
        return await ctx.send("invalid delrole")
    # Only Perm 6 / owner / special can manage roles >= their own top role
    if role >= ctx.author.top_role:
        if ctx.author.id != ctx.guild.owner_id and str(ctx.author.id) not in SPECIAL_USERS and not has_perm(ctx.author, 6):
            return await ctx.send("invalid delrole")
    if role >= ctx.guild.me.top_role:
        return await ctx.send("invalid delrole")
    if role not in member.roles:
        return await ctx.send(f"{member.mention} does not have the {role.mention} role.")
    try:
        await member.remove_roles(role)
        await ctx.send("1 rôle was successfully removed from 1 member")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def derank(ctx, target: str = None):
    if not has_perm(ctx.author, get_cmd_perm("derank")):
        return
    user = await get_target(ctx, target)
    if not user:
        return await ctx.send("invalid derank")
    if user.id == ctx.author.id:
        return await ctx.send("invalid derank")
    member = await get_member(ctx.guild, user)
    if not member:
        return await ctx.send("invalid derank")
    # INSTANTLY remove ALL roles (no delay, no staff log embed, no cooldown)
    try:
        roles = [r for r in member.roles if r != ctx.guild.default_role and not r.managed]
        await member.remove_roles(*roles)
        await ctx.send(f"{member.mention} was deranked successfully")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def create(ctx, emoji: str = None, *, name: str = None):
    if not has_perm(ctx.author, get_cmd_perm("create")):
        return
    if emoji and not name:
        name = emoji
        emoji = None
    if not name:
        return await ctx.send("invalid create")
    role_name = f"{emoji} {name}".strip() if emoji else name.strip()
    existing = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
    if existing:
        return await ctx.send(f"A role named **{role_name}** already exists.")
    try:
        new_role = await ctx.guild.create_role(name=role_name, reason=f"Created by {ctx.author}")
        await ctx.send(f"Successfully created role **{new_role.name}**")
    except discord.Forbidden:
        await ctx.send("I don't have permission to create roles.")
    except Exception as e:
        await ctx.send(f"Failed: {e}")

@bot.command()
async def rolemembers(ctx, *, role_query: str = None):
    if not has_perm(ctx.author, get_cmd_perm("rolemembers")):
        return
    if not role_query:
        return await ctx.send("invalid rolemembers")
    role = discord.utils.find(
        lambda r: r.name.lower() == role_query.lower() or str(r.id) == role_query,
        ctx.guild.roles
    )
    if not role:
        return await ctx.send("invalid rolemembers")
    members = role.members
    if not members:
        return await ctx.send(f"No members have the role **{role.name}**.")
    lines = [f"{m.mention} (`{m.id}`)" for m in members[:30]]
    emb = discord.Embed(
        title=f"Members with {role.name} ({len(members)})",
        description="\n".join(lines),
        color=0x000000
    )
    if len(members) > 30:
        emb.set_footer(text=f"Showing 30/{len(members)}")
    await ctx.send(embed=emb)

@bot.command()
async def bl(ctx, *, args: str = None):
    if str(ctx.author.id) not in BL_COMMAND_USERS:
        return
    user = None
    reason = "No reason"
    if ctx.message.mentions:
        user = ctx.message.mentions[0]
        if args:
            reason = args
            for m in ctx.message.mentions:
                reason = reason.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
            reason = reason.strip() or "No reason"
    elif ctx.message.reference:
        user = await get_target(ctx, None)
        if args:
            reason = args.strip() or "No reason"
    elif args:
        parts = args.split(None, 1)
        user = await get_target(ctx, parts[0])
        if user and len(parts) > 1:
            reason = parts[1]
    if not user:
        return await ctx.send("invalid bl")
    if user.id == ctx.author.id:
        return await ctx.send("invalid bl")
    uid = str(user.id)
    if uid not in blacklist:
        blacklist.append(uid)
        save_blacklist()
    try:
        await ctx.guild.ban(user, reason=f"Blacklisted: {reason}")
    except Exception:
        pass
    if reason and reason != "No reason":
        desc = f"{user.mention} banned and blacklisted\nreason: {reason}"
    else:
        desc = f"{user.mention} banned and blacklisted"
    emb = discord.Embed(title="blacklist", description=desc, color=0x000000)
    await ctx.send(embed=emb)

@bot.command()
async def unbl(ctx, user_id: str = None):
    if str(ctx.author.id) not in BL_COMMAND_USERS:
        return
    if not user_id:
        return await ctx.send("invalid unbl")
    uid = user_id.strip()
    if uid in blacklist:
        blacklist.remove(uid)
        save_blacklist()
    try:
        user = await bot.fetch_user(int(uid))
        try:
            await ctx.guild.unban(user)
        except Exception:
            pass
        await ctx.send(f"Removed `{uid}` from blacklist and unbanned.")
    except Exception:
        await ctx.send(f"Removed `{uid}` from blacklist.")

@bot.command()
async def userinfo(ctx, target: str = None):
    user = await get_target(ctx, target) or ctx.author
    member = ctx.guild.get_member(user.id)
    emb = discord.Embed(color=0x000000)
    emb.set_author(name=str(user), icon_url=user.display_avatar.url)
    emb.set_thumbnail(url=user.display_avatar.url)
    emb.add_field(name="ID", value=user.id, inline=True)
    emb.add_field(name="Created", value=discord.utils.format_dt(user.created_at, "R"), inline=True)
    if member:
        emb.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R"), inline=True)
    await ctx.send(embed=emb)

@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    emb = discord.Embed(title=g.name, color=0x000000)
    if g.icon:
        emb.set_thumbnail(url=g.icon.url)
    emb.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
    emb.add_field(name="Members", value=g.member_count, inline=True)
    emb.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"), inline=True)
    await ctx.send(embed=emb)

@bot.command()
async def modstats(ctx):
    if not has_perm(ctx.author, get_cmd_perm("modstats")):
        return
    # Count sanctions issued by each moderator
    counts = {}
    for uid, entries in sanctions_data.items():
        for s in entries:
            mid = str(s.get("moderator", "0"))
            if mid and mid != "0":
                counts[mid] = counts.get(mid, 0) + 1
    if not counts:
        return await ctx.send("No moderation actions recorded yet.")
    sorted_mods = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:25]
    lines = []
    for i, (mid, cnt) in enumerate(sorted_mods, 1):
        lines.append(f"**{i}.** <@{mid}> — `{cnt}` actions")
    emb = discord.Embed(
        title="Moderator Statistics",
        description="\n".join(lines),
        color=0x000000,
        timestamp=datetime.now(timezone.utc),
    )
    emb.set_footer(text="Based on recorded sanctions / warns / timeouts")
    await ctx.send(embed=emb)

@bot.command()
async def banlist(ctx):
    if not has_perm(ctx.author, get_cmd_perm("banlist")):
        return
    try:
        bans = [entry async for entry in ctx.guild.bans(limit=50)]
    except discord.Forbidden:
        return await ctx.send("I need the **Ban Members** permission to view the ban list.")
    except Exception as e:
        return await ctx.send(f"Failed to fetch bans: {e}")
    if not bans:
        return await empty_result(ctx, "There are no banned users.")
    lines = []
    for entry in bans[:40]:
        user = entry.user
        reason = entry.reason or "No reason"
        if len(reason) > 60:
            reason = reason[:57] + "..."
        lines.append(f"**{user}** (`{user.id}`)\n↳ {reason}")
    emb = discord.Embed(
        title=f"Ban List ({len(bans)} shown)",
        description="\n\n".join(lines),
        color=0x000000,
    )
    if len(bans) >= 50:
        emb.set_footer(text="Showing up to 50 most recent bans")
    await ctx.send(embed=emb)

@bot.command()
async def baninfo(ctx, target: str = None):
    if not has_perm(ctx.author, get_cmd_perm("baninfo")):
        return
    user = await get_target(ctx, target)
    if not user:
        return await ctx.send("invalid baninfo")
    try:
        ban_entry = await ctx.guild.fetch_ban(user)
    except discord.NotFound:
        return await ctx.send(f"**{user}** is not banned.")
    except discord.Forbidden:
        return await ctx.send("I need the **Ban Members** permission to view ban info.")
    except Exception as e:
        return await ctx.send(f"Failed: {e}")
    emb = discord.Embed(title="Ban Info", color=0x000000, timestamp=datetime.now(timezone.utc))
    emb.set_author(name=str(user), icon_url=user.display_avatar.url)
    emb.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
    emb.add_field(name="Reason", value=ban_entry.reason or "No reason", inline=False)
    emb.set_thumbnail(url=user.display_avatar.url)
    await ctx.send(embed=emb)

@bot.command()
async def changeperm(ctx, command: str = None, level: str = None):
    if not has_perm(ctx.author, get_cmd_perm("changeperm")):
        return
    if not command or level is None:
        return await ctx.send("Usage: `+changeperm <command> <level|none>`\nExample: `+changeperm warn 2` or `+changeperm clear none`")
    cmd = command.lower().strip()
    # Normalize aliases
    if cmd in ("warns",):
        cmd = "sanctions"
    if cmd in ("del sanction", "delsanction"):
        cmd = "del"
    valid_cmds = set(DEFAULT_COMMAND_PERMS.keys()) | set(command_overrides.keys())
    # Allow setting for known commands
    if level.lower() in ("none", "off", "disable", "disabled"):
        command_overrides[cmd] = "none"
        save_command_perms()
        await ctx.send(f"Permission for `{cmd}` set to **none** (disabled for regular staff).")
        return
    try:
        lvl = int(level)
        if lvl < 0 or lvl > 6:
            return await ctx.send("Level must be between 0 and 6 (or `none`).")
    except ValueError:
        return await ctx.send("Level must be a number 0-6 or `none`.")
    command_overrides[cmd] = lvl
    save_command_perms()
    await ctx.send(f"Permission for `{cmd}` set to **Perm {lvl}**.")

# ==================== GIFT HUB ====================
GIFT_EMOJI = "🎁"

def _gift_key(message_id) -> str:
    return str(message_id)

async def _build_gift_embed(gift: dict, ended: bool = False) -> discord.Embed:
    ends_at = gift.get("ends_at")
    try:
        ends_dt = datetime.fromisoformat(ends_at) if ends_at else None
        if ends_dt and ends_dt.tzinfo is None:
            ends_dt = ends_dt.replace(tzinfo=timezone.utc)
    except Exception:
        ends_dt = None
    winners_n = int(gift.get("winners", 1))
    prize = gift.get("prize", "Gift")
    host_id = gift.get("host_id")
    entries = gift.get("entries", [])
    title = "🎁 Gift Ended" if ended else "🎁 Gift Giveaway"
    emb = discord.Embed(title=title, color=0x000000, timestamp=datetime.now(timezone.utc))
    emb.add_field(name="Prize", value=prize, inline=False)
    emb.add_field(name="Winners", value=str(winners_n), inline=True)
    emb.add_field(name="Entries", value=str(len(entries)), inline=True)
    if host_id:
        emb.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
    if ends_dt and not ended:
        emb.add_field(name="Ends", value=discord.utils.format_dt(ends_dt, "R"), inline=False)
        emb.description = f"React with {GIFT_EMOJI} to enter!"
    if ended:
        winner_ids = gift.get("winner_ids") or []
        if winner_ids:
            mentions = ", ".join(f"<@{w}>" for w in winner_ids)
            emb.add_field(name="Winner(s)", value=mentions, inline=False)
        else:
            emb.add_field(name="Winner(s)", value="No valid entries", inline=False)
    emb.set_footer(text="Founder: Raynox • Bot maker: Teix • Gift Hub")
    return emb

def _pick_winners(entries: list, count: int, exclude: list = None) -> list:
    import random
    pool = list(dict.fromkeys(entries))  # unique, preserve order then shuffle
    if exclude:
        exclude_set = set(str(x) for x in exclude)
        pool = [e for e in pool if str(e) not in exclude_set]
    if not pool:
        return []
    random.shuffle(pool)
    return pool[: max(1, min(count, len(pool)))]

async def _end_gift(message_id: str, cancelled: bool = False):
    gift = gifts_data.get(str(message_id))
    if not gift or not gift.get("active"):
        return
    gift["active"] = False
    key = _gift_key(message_id)
    task = _gift_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

    channel_id = gift.get("channel_id")
    guild_id = gift.get("guild_id")
    winners_n = int(gift.get("winners", 1))
    entries = list(gift.get("entries") or [])

    if cancelled:
        gift["winner_ids"] = []
        gift["cancelled"] = True
    else:
        gift["winner_ids"] = _pick_winners(entries, winners_n)
        gift["cancelled"] = False

    save_gifts()

    ch = bot.get_channel(channel_id) if channel_id else None
    if ch is None and channel_id:
        try:
            ch = await bot.fetch_channel(channel_id)
        except Exception:
            ch = None
    if not ch:
        return
    try:
        msg = await ch.fetch_message(int(message_id))
    except Exception:
        msg = None

    emb = await _build_gift_embed(gift, ended=True)
    if cancelled:
        emb.title = "🎁 Gift Cancelled"
        emb.color = 0x000000
    if msg:
        try:
            await msg.edit(embed=emb)
        except Exception:
            pass
        try:
            await msg.clear_reactions()
        except Exception:
            pass

    if not cancelled and gift.get("winner_ids"):
        mentions = ", ".join(f"<@{w}>" for w in gift["winner_ids"])
        try:
            await ch.send(
                f"🎉 Congratulations {mentions}! You won **{gift.get('prize', 'the gift')}**!\n"
                f"Host: <@{gift.get('host_id')}> — please arrange delivery."
            )
        except Exception:
            pass
    elif not cancelled:
        try:
            await ch.send("🎁 Gift ended with no valid entries.")
        except Exception:
            pass

def schedule_gift_end(message_id: str, ends_at: datetime):
    import asyncio
    key = _gift_key(message_id)
    old = _gift_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()
    now = datetime.now(timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    delay = max(0, (ends_at - now).total_seconds())

    async def _runner():
        try:
            await asyncio.sleep(delay)
            await _end_gift(message_id)
        except asyncio.CancelledError:
            return

    _gift_tasks[key] = asyncio.create_task(_runner())

async def restore_gifts():
    now = datetime.now(timezone.utc)
    for mid, gift in list(gifts_data.items()):
        if not gift.get("active"):
            continue
        try:
            ends = datetime.fromisoformat(gift["ends_at"])
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if ends <= now:
                await _end_gift(mid)
            else:
                schedule_gift_end(mid, ends)
        except Exception as e:
            print(f"gift restore error {mid}: {e}")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == (bot.user.id if bot.user else None):
        return
    if str(payload.emoji) != GIFT_EMOJI and getattr(payload.emoji, "name", None) != "🎁":
        return
    mid = str(payload.message_id)
    gift = gifts_data.get(mid)
    if not gift or not gift.get("active"):
        return
    if payload.guild_id and gift.get("guild_id") and int(gift["guild_id"]) != payload.guild_id:
        return
    uid = str(payload.user_id)
    entries = gift.setdefault("entries", [])
    if uid not in entries:
        entries.append(uid)
        save_gifts()

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != GIFT_EMOJI and getattr(payload.emoji, "name", None) != "🎁":
        return
    mid = str(payload.message_id)
    gift = gifts_data.get(mid)
    if not gift or not gift.get("active"):
        return
    uid = str(payload.user_id)
    entries = gift.get("entries") or []
    if uid in entries:
        gift["entries"] = [e for e in entries if e != uid]
        save_gifts()

@bot.command(aliases=["gstart", "giveaway"])
async def gcreate(ctx, duration: str = None, winners: str = "1", *, prize: str = None):
    """Create a gift: +gcreate <duration> [winners] <prize>
    Example: +gcreate 1h 1 Nitro Classic
    """
    if not has_perm(ctx.author, get_cmd_perm("gcreate")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    if not duration or not prize:
        return await ctx.send("Usage: `+gcreate <duration> [winners] <prize>`\nExample: `+gcreate 30m 1 Discord Nitro`")
    delta = parse_duration(duration)
    if not delta or delta.total_seconds() < 10:
        return await ctx.send("Invalid duration (examples: `30s` `10m` `1h` `7d`)")
    try:
        winners_n = int(winners)
        if winners_n < 1 or winners_n > 20:
            raise ValueError()
    except Exception:
        # winners omitted — treat winners token as part of prize
        prize = f"{winners} {prize}".strip()
        winners_n = 1
    ends_at = datetime.now(timezone.utc) + delta
    gift_stub = {
        "prize": prize,
        "winners": winners_n,
        "host_id": ctx.author.id,
        "entries": [],
        "ends_at": ends_at.isoformat(),
        "active": True,
        "channel_id": ctx.channel.id,
        "guild_id": ctx.guild.id if ctx.guild else None,
        "winner_ids": [],
    }
    emb = await _build_gift_embed(gift_stub, ended=False)
    msg = await ctx.send(embed=emb)
    try:
        await msg.add_reaction(GIFT_EMOJI)
    except Exception:
        pass
    gift_stub["message_id"] = msg.id
    gifts_data[str(msg.id)] = gift_stub
    save_gifts()
    schedule_gift_end(str(msg.id), ends_at)
    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.command()
async def gend(ctx, message_id: str = None):
    """End a gift early and pick winners. Reply to the gift or pass message ID."""
    if not has_perm(ctx.author, get_cmd_perm("gend")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    mid = None
    if ctx.message.reference and ctx.message.reference.message_id:
        mid = str(ctx.message.reference.message_id)
    elif message_id and message_id.isdigit():
        mid = message_id.strip()
    if not mid or mid not in gifts_data:
        return await ctx.send("Reply to a gift message or use `+gend <message_id>`")
    gift = gifts_data[mid]
    if not gift.get("active"):
        return await ctx.send("That gift is already ended.")
    await _end_gift(mid)
    await ctx.send("Gift ended and winners picked.")

@bot.command()
async def gcancel(ctx, message_id: str = None):
    """Cancel a gift without picking winners."""
    if not has_perm(ctx.author, get_cmd_perm("gcancel")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    mid = None
    if ctx.message.reference and ctx.message.reference.message_id:
        mid = str(ctx.message.reference.message_id)
    elif message_id and message_id.isdigit():
        mid = message_id.strip()
    if not mid or mid not in gifts_data:
        return await ctx.send("Reply to a gift message or use `+gcancel <message_id>`")
    gift = gifts_data[mid]
    if not gift.get("active"):
        return await ctx.send("That gift is already ended.")
    await _end_gift(mid, cancelled=True)
    await ctx.send("Gift cancelled.")

@bot.command()
async def greroll(ctx, message_id: str = None):
    """Reroll winner(s) for an ended gift. Reply to the gift or pass message ID."""
    if not has_perm(ctx.author, get_cmd_perm("greroll")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    mid = None
    if ctx.message.reference and ctx.message.reference.message_id:
        mid = str(ctx.message.reference.message_id)
    elif message_id and message_id.isdigit():
        mid = message_id.strip()
    if not mid or mid not in gifts_data:
        return await ctx.send("Reply to a gift message or use `+greroll <message_id>`")
    gift = gifts_data[mid]
    if gift.get("active"):
        return await ctx.send("Gift is still active — use `+gend` first.")
    entries = list(gift.get("entries") or [])
    winners_n = int(gift.get("winners", 1))
    old = list(gift.get("winner_ids") or [])
    new_winners = _pick_winners(entries, winners_n, exclude=old)
    if not new_winners:
        # if no one left excluding old, pick from all
        new_winners = _pick_winners(entries, winners_n)
    if not new_winners:
        return await ctx.send("No entries to reroll.")
    gift["winner_ids"] = new_winners
    save_gifts()
    emb = await _build_gift_embed(gift, ended=True)
    emb.title = "🎁 Gift Rerolled"
    ch = bot.get_channel(gift.get("channel_id"))
    if ch is None and gift.get("channel_id"):
        try:
            ch = await bot.fetch_channel(gift["channel_id"])
        except Exception:
            ch = None
    if ch:
        try:
            msg = await ch.fetch_message(int(mid))
            await msg.edit(embed=emb)
        except Exception:
            pass
        mentions = ", ".join(f"<@{w}>" for w in new_winners)
        await ch.send(
            f"🔄 Reroll! New winner(s): {mentions} — **{gift.get('prize', 'gift')}**"
        )
    await ctx.send("Rerolled.")

@bot.command()
async def glist(ctx):
    """List active gifts in this server."""
    if not has_perm(ctx.author, get_cmd_perm("glist")) and str(ctx.author.id) not in SPECIAL_USERS:
        return
    active = []
    gid = ctx.guild.id if ctx.guild else None
    for mid, g in gifts_data.items():
        if not g.get("active"):
            continue
        if gid and g.get("guild_id") and int(g["guild_id"]) != gid:
            continue
        active.append((mid, g))
    if not active:
        return await ctx.send("No active gifts.")
    lines = []
    for mid, g in active[:20]:
        prize = g.get("prize", "?")
        entries = len(g.get("entries") or [])
        ends = g.get("ends_at", "")
        try:
            ends_dt = datetime.fromisoformat(ends)
            if ends_dt.tzinfo is None:
                ends_dt = ends_dt.replace(tzinfo=timezone.utc)
            ends_txt = discord.utils.format_dt(ends_dt, "R")
        except Exception:
            ends_txt = ends
        lines.append(f"**{prize}** — `{mid}` — {entries} entries — ends {ends_txt}")
    emb = discord.Embed(title="Active Gifts", description="\n".join(lines), color=0x000000)
    emb.set_footer(text="Founder: Raynox • Bot maker: Teix • Gift Hub")
    await ctx.send(embed=emb)

@bot.command()
async def help(ctx):
    emb = discord.Embed(
        title="Command List",
        color=0x000000,
        description=(
            "Prefix: `+`\n"
            "You can **reply** to a message instead of mentioning the user."
        )
    )
    emb.add_field(
        name="🎁 Gift Hub (Perm 3+)",
        value=(
            "`+gcreate <duration> [winners] <prize>` — start a gift\n"
            "`+gend [message_id]` — end gift & pick winners (or reply)\n"
            "`+greroll [message_id]` — reroll winners (or reply)\n"
            "`+glist` — list active gifts\n"
            "`+gcancel [message_id]` — cancel gift (Perm 4+)\n"
            "Example: `+gcreate 1h 1 Discord Nitro`"
        ),
        inline=False
    )
    emb.add_field(
        name="Perm 1",
        value="`+help` `+warn <member> [reason]` `+mutelist` `+perms` `+sanctions <member>` `+tempmute <member> <duration> [reason]` `+unmute <member>`",
        inline=False
    )
    emb.add_field(
        name="Perm 2",
        value="`+del sanction <member> <number>` `+rolemembers <role>`",
        inline=False
    )
    emb.add_field(
        name="Perm 3",
        value="`+derank <member>` `+clearwarns <member>` `+addrole <member> <role>` `+delrole <member> <role>`",
        inline=False
    )
    emb.add_field(
        name="Perm 4",
        value="`+clear [number] [member]` `+create [emoji] [name]`",
        inline=False
    )
    emb.add_field(
        name="Perm 5",
        value="`+banlist` `+baninfo <id|mention>`",
        inline=False
    )
    emb.add_field(
        name="Perm 6",
        value="`+temprole <member> <duration> <role>` `+modstats` `+changeperm <command> <level|none>` `+syncroles`",
        inline=False
    )
    emb.add_field(
        name="Special Users only",
        value="`+ban` `+unban` `+kick` `+bl` `+unbl`",
        inline=False
    )
    emb.add_field(
        name="Everyone",
        value="`+userinfo` `+serverinfo` `+snipe` `+ping`",
        inline=False
    )
    emb.set_footer(text="Founder: Raynox • Bot maker: Teix")
    await ctx.send(embed=emb)

def censor_blacklisted(text: str) -> str:
    if not text:
        return text
    out = text
    words = sorted(BLACKLISTED_WORDS, key=len, reverse=True)
    for word in words:
        if not word:
            continue
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        def _blur(m, _w=word):
            w = m.group(0)
            if len(w) <= 2:
                return "*" * len(w)
            return w[0] + ("•" * (len(w) - 2)) + w[-1]
        out = pattern.sub(_blur, out)
    return out

def parse_duration(text: str):
    match = re.match(r"^(\d+)([smhd])$", text.lower())
    if not match:
        return None
    num, unit = int(match.group(1)), match.group(2)
    if unit == "s": return timedelta(seconds=num)
    if unit == "m": return timedelta(minutes=num)
    if unit == "h": return timedelta(hours=num)
    if unit == "d": return timedelta(days=num)
    return None

def _temprole_key(guild_id: int, user_id: int, role_id: int) -> str:
    return f"{guild_id}:{user_id}:{role_id}"

async def _remove_temprole(guild_id: int, user_id: int, role_id: int):
    key = _temprole_key(guild_id, user_id, role_id)
    _temprole_tasks.pop(key, None)
    global temproles_data
    temproles_data = [
        e for e in temproles_data
        if not (e.get("guild_id") == guild_id and e.get("user_id") == user_id and e.get("role_id") == role_id)
    ]
    save_temproles()
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            return
    role = guild.get_role(role_id)
    if not role:
        return
    if role not in member.roles:
        return
    try:
        await member.remove_roles(role, reason="Temporary role expired")
        log = discord.Embed(title="Temp Role Expired", color=0x000000, timestamp=datetime.now())
        log.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        log.add_field(name="Role", value=f"{role.name} (`{role.id}`)", inline=False)
        await send_log(log)
    except Exception:
        pass

def schedule_temprole(guild_id: int, user_id: int, role_id: int, ends_at: datetime):
    import asyncio
    key = _temprole_key(guild_id, user_id, role_id)
    old = _temprole_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()
    now = datetime.now(timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    delay = max(0, (ends_at - now).total_seconds())
    async def _runner():
        try:
            await asyncio.sleep(delay)
            await _remove_temprole(guild_id, user_id, role_id)
        except asyncio.CancelledError:
            return
    _temprole_tasks[key] = asyncio.create_task(_runner())
    global temproles_data
    temproles_data = [
        e for e in temproles_data
        if not (e.get("guild_id") == guild_id and e.get("user_id") == user_id and e.get("role_id") == role_id)
    ]
    temproles_data.append({
        "guild_id": guild_id,
        "user_id": user_id,
        "role_id": role_id,
        "ends_at": ends_at.isoformat(),
    })
    save_temproles()

async def restore_temproles():
    import asyncio
    now = datetime.now(timezone.utc)
    pending = list(temproles_data)
    for entry in pending:
        try:
            gid = int(entry["guild_id"])
            uid = int(entry["user_id"])
            rid = int(entry["role_id"])
            ends = datetime.fromisoformat(entry["ends_at"])
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if ends <= now:
                await _remove_temprole(gid, uid, rid)
            else:
                schedule_temprole(gid, uid, rid, ends)
        except Exception as e:
            print(f"temprole restore error: {e}")

async def send_log(embed: discord.Embed):
    return

def add_sanction(user_id: int, reason: str, mod_id: int):
    uid = str(user_id)
    if uid not in sanctions_data:
        sanctions_data[uid] = []
    entry = {
        "id": len(sanctions_data[uid]) + 1,
        "reason": reason,
        "date": datetime.now().strftime("%d/%m/%Y"),
        "moderator": str(mod_id)
    }
    sanctions_data[uid].append(entry)
    save_sanctions()
    return entry

async def get_target(ctx: commands.Context, arg: str = None):
    if ctx.message.mentions:
        return ctx.message.mentions[0]
    if ctx.message.reference:
        ref = ctx.message.reference
        if ref.resolved and hasattr(ref.resolved, "author"):
            return ref.resolved.author
        if ref.message_id:
            try:
                msg = await ctx.channel.fetch_message(ref.message_id)
                return msg.author
            except Exception:
                pass
    if arg:
        arg = arg.strip()
        if arg.isdigit():
            try:
                return await bot.fetch_user(int(arg))
            except Exception:
                pass
        if arg.startswith("<@") and arg.endswith(">"):
            raw = arg.replace("<@", "").replace("!", "").replace(">", "")
            if raw.isdigit():
                try:
                    return await bot.fetch_user(int(raw))
                except Exception:
                    pass
        if ctx.guild:
            name = arg.lower()
            for m in ctx.guild.members:
                if (
                    m.name.lower() == name
                    or (m.display_name and m.display_name.lower() == name)
                    or str(m).lower() == name
                ):
                    return m
            for m in ctx.guild.members:
                if m.name.lower().startswith(name) or (m.display_name and m.display_name.lower().startswith(name)):
                    return m
    return None

async def get_member(guild: discord.Guild, user):
    if user is None or guild is None:
        return None
    uid = getattr(user, "id", user)
    try:
        uid = int(uid)
    except Exception:
        return None
    member = guild.get_member(uid)
    if member:
        return member
    try:
        return await guild.fetch_member(uid)
    except Exception:
        return None

async def empty_result(ctx, text: str):
    try:
        msg = await ctx.send(text)
    except Exception:
        msg = None
    try:
        await ctx.message.delete()
    except Exception:
        pass
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass


# ==================== KEEP-ALIVE ====================
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is online")

    def log_message(self, format, *args):
        return

def start_keep_alive():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Keep-alive server running on port {port}")
    except Exception as e:
        print(f"Keep-alive server failed to start: {e}")

# ==================== RUN ====================
start_keep_alive()
bot.run(TOKEN)
