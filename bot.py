import re, base64, asyncio, logging
from datetime import datetime

import aiohttp
import pymongo
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, UserNotParticipant
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from config import (BOT_TOKEN, APP_ID, API_HASH, CHANNEL_ID, PORT, DB_URI, DB_NAME, SHORTLINK_URL,
                    SHORTLINK_API, FORCE_SUB_CHANNEL, WORKERS, CUSTOM_CAPTION,
                    START_MSG, FORCE_MSG, ADMINS, SEP)

log = logging.getLogger("bot")
MAX_FILES = 200  # most files one link may deliver (stops a forged link from asking for millions)

# ---------------------------------------------------------------- database
db = pymongo.MongoClient(DB_URI)[DB_NAME]
users, settings = db["users"], db["config"]
run = asyncio.to_thread  # pymongo is blocking, so it runs off the event loop

# ---------------------------------------------------------------- client + state
app = Client("Bot", api_id=APP_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=WORKERS)
state = {"started": datetime.now(), "username": "", "db_channel": None, "invite": ""}
STATES = {}       # admin id -> /genlink or /batch conversation in progress
DELIVERING = set()  # users whose files are being sent right now
BROADCAST = {"running": False}


# ---------------------------------------------------------------- helpers
def encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().strip("=")


def decode(s: str) -> str:
    s = s.strip("=")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


async def _subscribed(_, client, msg):
    if not FORCE_SUB_CHANNEL or msg.from_user.id in ADMINS:
        return True
    try:
        m = await client.get_chat_member(FORCE_SUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        return False
    except Exception as e:
        log.warning("Force-sub check failed: %s", e)
        return False
    return m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)


subscribed = filters.create(_subscribed)
admin_pm = filters.private & filters.user(ADMINS)


def fmt(text, u):
    return text.format(first=u.first_name, last=u.last_name, id=u.id, mention=u.mention,
                       username="@" + u.username if u.username else None)


def parse_link(payload):
    """Message ids from a start link: "get-<id*channel>" or "get-<first*channel>-<last*channel>"."""
    parts = decode(payload).split("-")
    if parts[0] != "get" or len(parts) not in (2, 3):
        raise ValueError("unexpected link format")
    n = abs(state["db_channel"].id)
    nums = [int(p) for p in parts[1:]]
    if any(x <= 0 or x % n for x in nums):  # made for another channel
        raise ValueError("link was not generated for this DB channel")
    ids = [x // n for x in nums]
    if len(ids) == 2:
        a, b = ids
        ids = range(a, b + 1) if a <= b else range(a, b - 1, -1)
    if len(ids) > MAX_FILES:
        raise ValueError("too many files")
    return list(ids)


async def get_messages(ids):
    out = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            out += await app.get_messages(state["db_channel"].id, chunk)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            out += await app.get_messages(state["db_channel"].id, chunk)
    return out


def get_message_id(msg) -> int:
    """Message id in the DB channel from a forward or a t.me link (0 if not from it)."""
    ch = state["db_channel"]
    if msg.forward_from_chat:
        return (msg.forward_from_message_id or 0) if msg.forward_from_chat.id == ch.id else 0
    if msg.text and not msg.forward_sender_name:
        m = re.match(r"https?://(?:t|telegram)\.me/(?:c/)?([^/\s]+)/(\d+)", msg.text.strip())
        if m:
            chan, mid = m.group(1), int(m.group(2))
            if (chan.isdigit() and f"-100{chan}" == str(ch.id)) or (ch.username and chan.lower() == ch.username.lower()):
                return mid
    return 0


async def get_shortlink(link: str) -> str:
    """Shortened link, or the plain link if the shortener is not set or fails."""
    row = await run(settings.find_one, {"_id": "shortener_config"}) or {}
    site = row.get("shortener") or SHORTLINK_URL
    api = row.get("shortener_api") or SHORTLINK_API
    link = link.replace("http://", "https://")
    if not site or not api:
        return link
    shareus = site == "api.shareus.in"
    url = f"https://{site}/shortLink" if shareus else f"https://{site}/api"
    params = {"token": api, "format": "json", "link": link} if shareus else {"api": api, "url": link}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, params=params, raise_for_status=True) as r:
                data = await r.json(content_type=None)
        if data.get("status") == "success":
            return data["shortlink"] if shareus else data["shortenedUrl"]
        log.warning("shortener error: %s", data.get("message"))
    except Exception as e:
        log.warning("shortener error: %s", type(e).__name__)  # not the message: it can contain the API key
    return link


# ---------------------------------------------------------------- user handlers
@app.on_message(filters.command("start") & filters.private & subscribed)
async def start(client, message):
    uid = message.from_user.id
    await run(users.replace_one, {"_id": uid}, {"_id": uid}, True)

    if len(message.command) > 1:
        try:
            ids = parse_link(message.command[1])
        except Exception as e:
            log.warning("Invalid start link: %s", e)
            return await message.reply("❌ Invalid link.")
        if uid in DELIVERING:
            return await message.reply("⏳ Your previous files are still being sent. Please wait a moment.")

        wait = await message.reply("⏳ Please wait...")
        try:
            msgs = await get_messages(ids)
        except Exception:
            log.exception("Could not fetch files")
            return await wait.edit("⚠️ Something went wrong. Please try again later.")
        await wait.delete()

        DELIVERING.add(uid)
        sent = 0
        try:
            for m in msgs:
                if not m or m.empty:
                    continue
                caption = m.caption.html if m.caption else ""
                if CUSTOM_CAPTION and m.document:
                    caption = CUSTOM_CAPTION.format(previouscaption=caption, filename=m.document.file_name)
                kw = dict(chat_id=uid, caption=caption, parse_mode=ParseMode.HTML,
                          reply_markup=None)
                try:
                    try:
                        await m.copy(**kw)
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        await m.copy(**kw)
                    sent += 1
                    await asyncio.sleep(0.5)
                except (UserIsBlocked, InputUserDeactivated):
                    return
                except Exception:
                    log.exception("File send error")
        finally:
            DELIVERING.discard(uid)
        if not sent:
            await message.reply("❌ Files not found. They may have been deleted.")
        return

    await message.reply_text(
        fmt(START_MSG, message.from_user), quote=True, disable_web_page_preview=True,
        reply_markup=Markup([[Btn("😊 About Me", callback_data="about"), Btn("🔒 Close", callback_data="close")]]))


@app.on_message(filters.command("start") & filters.private)
async def not_joined(client, message):
    buttons = [[Btn("📢 Join Channel", url=state["invite"])]]
    if len(message.command) > 1:
        buttons.append([Btn("🔄 Try Again", url=f"https://t.me/{state['username']}?start={message.command[1]}")])
    await message.reply(fmt(FORCE_MSG, message.from_user), reply_markup=Markup(buttons),
                        quote=True, disable_web_page_preview=True)


@app.on_callback_query()
async def callbacks(client, query):
    if query.data == "about":
        await query.message.edit_text("<b>○ Creator : @MrKrazyBot</b>", disable_web_page_preview=True,
                                      reply_markup=Markup([[Btn("🔒 Close", callback_data="close")]]))
    elif query.data == "close":
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except Exception:
            pass
    await query.answer()


# ---------------------------------------------------------------- admin handlers
@app.on_message(admin_pm & filters.command("users"))
async def users_count(client, message):
    await message.reply(f"👥 <b>{await run(users.count_documents, {})}</b> users are using this bot")


@app.on_message(admin_pm & filters.command("stats"))
async def stats(client, message):
    s = int((datetime.now() - state["started"]).total_seconds())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    total = await run(users.count_documents, {})
    await message.reply(f"📊 <b>Bot Stats</b>\n{SEP}\n\n⏱ Uptime: <code>{d}d {h}h {m}m {s}s</code>\n"
                        f"👥 Users: <code>{total}</code>")


@app.on_message(admin_pm & filters.command("broadcast"))
async def broadcast(client, message):
    src = message.reply_to_message
    if not src:
        msg = await message.reply("↩️ <code>Use this command as a reply to any telegram message.</code>")
        await asyncio.sleep(8)
        return await msg.delete()
    if BROADCAST["running"]:
        return await message.reply("⏳ A broadcast is already running.")

    BROADCAST["running"] = True
    try:
        ids = await run(lambda: [d["_id"] for d in users.find({}, {"_id": 1})])
        status = await message.reply(f"📢 <b>Broadcasting…</b>\n{SEP}\n\n👥 Total: <code>{len(ids)}</code>\n"
                                     "<i>This will take some time</i>")
        ok = blocked = deleted = failed = 0
        for uid in ids:
            try:
                try:
                    await src.copy(uid)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await src.copy(uid)
                ok += 1
            except UserIsBlocked:
                await run(users.delete_one, {"_id": uid})
                blocked += 1
            except InputUserDeactivated:
                await run(users.delete_one, {"_id": uid})
                deleted += 1
            except Exception:
                failed += 1
        await status.edit(f"✅ <b>Broadcast Completed</b>\n{SEP}\n\n👥 Total: <code>{len(ids)}</code>\n"
                          f"✅ Sent: <code>{ok}</code>\n🚫 Blocked: <code>{blocked}</code>\n"
                          f"👻 Deleted: <code>{deleted}</code>\n❌ Failed: <code>{failed}</code>")
    finally:
        BROADCAST["running"] = False


@app.on_message(admin_pm & filters.command(["genlink", "batch"]))
async def link_command(client, message):
    batch = message.command[0].lower() == "batch"
    STATES[message.from_user.id] = {"batch": batch, "first": None, "at": datetime.now()}
    await message.reply("📨 Forward the <b>first</b> DB message or send its link." if batch
                        else "📨 Forward the <b>DB message</b> or send its link.")


async def link_input(message, st):
    """Next step of a /genlink or /batch conversation."""
    uid = message.from_user.id
    mid = get_message_id(message)
    if not mid:
        return await message.reply("❌ This post/link is not from my DB channel.", quote=True)
    if st["batch"] and st["first"] is None:
        st["first"] = mid
        return await message.reply("📩 Forward the <b>last</b> DB message or send its link.")

    STATES.pop(uid, None)
    n = abs(state["db_channel"].id)
    payload = f"get-{st['first'] * n}-{mid * n}" if st["batch"] else f"get-{mid * n}"
    link = f"https://telegram.me/{state['username']}?start={encode(payload)}"
    short = await get_shortlink(link)
    await message.reply_text(
        f"🔗 <b>Link Generated</b>\n{SEP}\n\n📎 <b>Link:</b> {link}\n\n⚡ <b>Short Link:</b> <code>{short}</code>",
        quote=True, disable_web_page_preview=True)


COMMANDS = ["start", "users", "broadcast", "batch", "genlink", "stats", "shortlink", "shortener"]


@app.on_message(admin_pm & ~filters.command(COMMANDS))
async def admin_text(client, message):
    """Continue /genlink or /batch, otherwise shorten the first link in the message."""
    st = STATES.get(message.from_user.id)
    if st and (datetime.now() - st["at"]).total_seconds() > 120:  # abandoned conversation
        STATES.pop(message.from_user.id, None)
        st = None
    if st:
        return await link_input(message, st)

    text = message.text or message.caption or ""
    m = re.search(r"https?://\S+", text)
    if not m:
        return
    link = m.group(0).rstrip(".,!?)]}>\"'")
    link = re.sub(r"^https?://(t\.me|telegram\.me)/", "https://telegram.me/", link)
    link = link.replace("telegram.me/+", "telegram.me/%2B")
    short = await get_shortlink(link)
    await message.reply_text(
        f"⚡ <b>Short Link Ready</b>\n{SEP}\n\n📎 <b>Original:</b> {text}\n\n🔗 <b>Short:</b> <code>{short}</code>",
        quote=True, disable_web_page_preview=True)


@app.on_message(admin_pm & filters.command("shortlink"))
async def set_shortlink(client, message):
    if len(message.command) != 3:
        return await message.reply("🔗 <b>Usage</b>\n<code>/shortlink site.com API</code>")
    site, api = re.sub(r"^https?://", "", message.command[1]).rstrip("/"), message.command[2]
    await run(settings.update_one, {"_id": "shortener_config"},
              {"$set": {"shortener": site, "shortener_api": api}}, True)
    await message.reply(f"✅ <b>Shortener Updated</b>\n{SEP}\n\n🌍 Site: <code>{site}</code>\n🔑 API: <code>{api}</code>")


@app.on_message(admin_pm & filters.command("shortener"))
async def show_shortener(client, message):
    row = await run(settings.find_one, {"_id": "shortener_config"}) or {}
    await message.reply(f"🔗 <b>Shortener</b>\n{SEP}\n\n🌍 Site: <code>{row.get('shortener') or SHORTLINK_URL}</code>\n"
                        f"🔑 API: <code>{row.get('shortener_api') or SHORTLINK_API}</code>\n\n"
                        f"💡 Change: <code>/shortlink site.com API</code>")


# ---------------------------------------------------------------- run
async def main():
    await app.start()
    app.set_parse_mode(ParseMode.HTML)
    state["username"] = (await app.get_me()).username

    if FORCE_SUB_CHANNEL:
        try:
            chat = await app.get_chat(FORCE_SUB_CHANNEL)
            if not chat.invite_link:
                await app.export_chat_invite_link(FORCE_SUB_CHANNEL)
                chat = await app.get_chat(FORCE_SUB_CHANNEL)
            state["invite"] = chat.invite_link
        except Exception as e:
            log.error("Can't get invite link for FORCE_SUB_CHANNEL=%s (is the bot admin there?): %s", FORCE_SUB_CHANNEL, e)
            return await app.stop()
    try:
        state["db_channel"] = await app.get_chat(CHANNEL_ID)
        await (await app.send_message(CHANNEL_ID, "Test Message")).delete()
    except Exception as e:
        log.error("Make sure the bot is admin in the DB channel and CHANNEL_ID=%s is right: %s", CHANNEL_ID, e)
        return await app.stop()

    routes = web.RouteTableDef()

    @routes.get("/", allow_head=True)
    async def health(_):
        return web.json_response("OK")

    web_app = web.Application()
    web_app.add_routes(routes)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    log.info("Bot started successfully.")
    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
