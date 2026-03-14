# Auto Leech Plugin for WZML-X
# Ported from Thiru-ML by @ThiruXD
# Adapted for WZML-X (wzv3) by Claude

import os
import asyncio
import requests
import feedparser
from asyncio import sleep
from re import sub
from bs4 import BeautifulSoup
from cloudscraper import create_scraper
from pyrogram.handlers import MessageHandler
from pyrogram.filters import command

from bot import bot, bot_loop, LOGGER
from bot.core.config_manager import Config
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import send_message
from bot.helper.ext_utils.bot_utils import new_task
from bot.modules.mirror_leech import Mirror
from pymongo import MongoClient
from pymongo.server_api import ServerApi

# ─── Settings ───────────────────────────────────────────────────────────────
AA_DELAY = 5   # seconds between outer loop iterations
BB_DELAY = 7   # seconds between RSS feed checks
is_auto_leecher = True
IMGBB_API_KEY   = "7c555e92974a9049ac25c7e6f2afa652"  # replace with your own if needed

# ─── MongoDB Setup ───────────────────────────────────────────────────────────
_mongo_client  = MongoClient(Config.DATABASE_URL, server_api=ServerApi("1"))
_db            = _mongo_client.autoleech_wzmlx
rss_domains    = _db["rss_domains"]
rss_collection = _db["rss_col_data"]
thumbs_col     = _db["thumbnails"]

# ─── Domain helpers ──────────────────────────────────────────────────────────
def _get_link(name):
    doc = rss_domains.find_one({"name": name})
    return doc["url"] if doc else None

def _insert_or_update_link(name, data):
    result = rss_domains.update_one({"name": name}, {"$set": data}, upsert=True)
    return f"Link '{name}' {'updated' if result.matched_count else 'inserted'}."

def _get_all_links():
    return list(rss_domains.find({}))

# ─── Build RSS URLs from DB ───────────────────────────────────────────────────
def _build_rss_urls():
    tmv = _get_link("tmv")
    tbl = _get_link("tbl")
    urls = {}
    if tmv:
        urls.update({
            f"{tmv}/index.php?/forums/forum/17-hollywood-movies-in-multi-audios/all.xml":        "hollywood_1tmv",
            f"{tmv}/index.php?/forums/forum/11-web-hd-itunes-hd-bluray/all.xml":                "tamil_1tmv",
            f"{tmv}/index.php?/forums/forum/10-predvd-dvdscr-cam-tc/all.xml":                   "tamilmv_tamil_cam_rip",
            f"{tmv}/index.php?/forums/forum/25-hd-rips-dvd-rips-br-rips/all.xml":              "tamilmv_telegu_hdrip",
            f"{tmv}/index.php?/forums/forum/24-web-hd-itunes-hd-bluray/all.xml":               "tamilmv_telegu_webhd",
            f"{tmv}/index.php?/forums/forum/58-web-hd-itunes-hd-bluray/all.xml":               "tamilmv_hindi_webhd",
            f"{tmv}/index.php?/forums/forum/36-web-hd-itunes-hd-bluray/all.xml":               "tamilmv_maly_webhd",
            f"{tmv}/index.php?/forums/forum/49-web-hd-itunes-hd-bluray/all.xml":               "tamilmv_english_webhd",
        })
    if tbl:
        urls.update({
            f"{tbl}/index.php?/forums/forum/7-tamil-new-movies-hdrips-bdrips-dvdrips-hdtv/all.xml":                                          "tamil_tbl",
            f"{tbl}/index.php?/forums/forum/9-tamil-dubbed-movies-bdrips-hdrips-dvdscr-hdcam-in-multi-audios/all.xml": "hollywood_tbl",
        })
    return urls

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _post_to_dpaste(content):
    try:
        r = requests.post("https://dpaste.org/api/",
                          data={"content": content, "syntax": "json", "expiry_days": "360"})
        return r.text.strip() if r.status_code == 200 else f"dpaste error: {r.status_code}"
    except Exception as e:
        return f"dpaste error: {e}"

def _upload_to_imgbb(image_path):
    try:
        with open(image_path, "rb") as f:
            r = requests.post("https://api.imgbb.com/1/upload",
                              params={"key": IMGBB_API_KEY}, files={"image": f})
        return r.json()["data"]["url"] if r.status_code == 200 else None
    except Exception:
        return None

def _download_torrent(url, file_name):
    scraper = create_scraper()
    try:
        r = scraper.get(url, allow_redirects=True)
        if r.status_code == 200 and b"announce" in r.content[:500]:
            with open(file_name, "wb") as f:
                f.write(r.content)
            return file_name
    except Exception as e:
        LOGGER.error(f"[AutoLeech] Torrent download error: {e}")
    return None

def _get_group_id():
    """Get AUTO_LEECH_GRP_ID from Config or fall back to LEECH_DUMP_CHAT."""
    grp = getattr(Config, "AUTO_LEECH_GRP_ID", None) or Config.LEECH_DUMP_CHAT
    if not grp:
        LOGGER.warning("[AutoLeech] AUTO_LEECH_GRP_ID not set! Add it to your config vars.")
    return grp

def _get_dump_id():
    return getattr(Config, "AUTO_LEECH_DUMP_ID", None) or Config.LEECH_DUMP_CHAT

# ─── Core scraper (shared by TamilMV & TamilBlasters) ────────────────────────
async def _process_feed(rss_url, keyword, site_name):
    grp_id = _get_group_id()
    if not grp_id:
        return

    bot_info = await bot.get_me()
    bot_id   = bot_info.id
    thumb    = thumbs_col.find_one({"_id": bot_id})

    feed = feedparser.parse(rss_url)
    if not feed.entries:
        LOGGER.warning(f"[AutoLeech] No entries in feed: {rss_url}")
        return

    first_entry = feed.entries[0]
    first_link  = first_entry.link

    existing = rss_collection.find_one({"keyword": keyword})
    if existing is None:
        rss_collection.insert_one({"keyword": keyword, "url": "Nhai-Illa"})
        existing = {"url": "Nhai-Illa"}

    if existing["url"] == first_link:
        return  # nothing new

    try:
        cget      = create_scraper().request
        resp      = cget("GET", first_link, allow_redirects=False)
        soup      = BeautifulSoup(resp.text, "html.parser")
        magnets   = soup.select('a[href^="magnet:?xt=urn:btih:"]')
        torrents  = soup.select('a[data-fileext="torrent"]')
        title     = soup.title.string if soup.title else "Unknown Title"

        title_msg = await bot.send_message(grp_id,
            f"🎬 <b><u>{title}</u></b>\n\n📡 Source: {site_name}\n\n— Auto Leech by WZML-X")
        try:
            await bot.pin_chat_message(grp_id, title_msg.id)
        except Exception:
            pass

        for t, m in zip(torrents, magnets):
            filename   = sub(r"www\S+|\- |\.torrent", "", t.string).strip()
            paste_text = f"🧲 {filename}\n\n<code>{m['href']}</code>\n\n🗒️ <a href=\"{t['href']}\">Torrent File</a>"
            paste_link = _post_to_dpaste(paste_text)
            file_name  = f"{filename}.torrent"
            caption    = f"🧲 <b>{file_name}</b>\n\n🔗 Magnet links:\n{paste_link}"

            if not _download_torrent(t["href"], file_name):
                await bot.send_message(grp_id, f"❌ Failed to download torrent:\n{caption}")
                continue

            await asyncio.sleep(3)
            filee = await bot.send_document(chat_id=grp_id, document=file_name, caption=caption)
            os.remove(file_name)

            dump_arg = f" -dump {_get_dump_id()}" if _get_dump_id() else ""
            thumb_arg = f" -t {thumb['url']}" if thumb else ""
            cmd = f"/qbleech{thumb_arg}{dump_arg}"

            try:
                leech_msg = await filee.reply_text(cmd)
                bot_loop.create_task(
                    Mirror(bot, leech_msg, is_qbit=True, is_leech=True).new_event()
                )
                await asyncio.sleep(BB_DELAY)
                await leech_msg.delete()
            except Exception as e:
                await filee.reply_text(f"❌ Leech error: {e}")

        rss_collection.update_one({"keyword": keyword}, {"$set": {"url": first_link}})
        await bot.send_sticker(grp_id,
            "CAACAgUAAxkBAAIjxGY75nsXUSCCFO6LB-KiGRPC5kiuAAJzBgACJggpVXKB2uxzC9oxHgQ")

    except Exception as e:
        LOGGER.error(f"[AutoLeech] Error processing {site_name} feed: {e}")

# ─── Main RSS loop ────────────────────────────────────────────────────────────
@new_task
async def rss_auto_leecher():
    LOGGER.info("[AutoLeech] RSS Auto Leecher started!")
    while is_auto_leecher:
        await sleep(AA_DELAY)
        try:
            rss_urls = _build_rss_urls()
            if not rss_urls:
                LOGGER.warning("[AutoLeech] No domains set. Use /setd to add domains.")
                await sleep(60)
                continue
            for url, keyword in rss_urls.items():
                await sleep(BB_DELAY)
                site = "1TamilMV" if "tmv" in str(_get_link("tmv") or "") and _get_link("tmv") in url else "1TamilBlasters"
                await _process_feed(url, keyword, site)
        except Exception as e:
            LOGGER.error(f"[AutoLeech] Loop error: {e}")

# ─── Commands ─────────────────────────────────────────────────────────────────

@new_task
async def cmd_setdomain(client, message):
    """Usage: /setd tmv | https://domain.com"""
    if "|" not in message.text:
        return await message.reply_text(
            "❌ Wrong format!\n\n<b>Usage:</b> <code>/setd [keyword] | [domain]</code>\n\n"
            "<b>Keywords:</b>\n• <code>tmv</code> — 1TamilMV\n• <code>tbl</code> — 1TamilBlasters"
        )
    parts = message.text.split("|", 1)
    name  = parts[0].split()[-1].strip()
    link  = parts[1].strip()
    result = _insert_or_update_link(name, {"url": link, "title": name})
    await message.reply_text(f"✅ {result}")

@new_task
async def cmd_getdomains(client, message):
    """Show all saved domains."""
    links = _get_all_links()
    if not links:
        return await message.reply_text("❌ No domains saved yet. Use /setd to add one.")
    text = "🌐 <b>Saved Domains:</b>\n\n"
    for l in links:
        text += f"• <code>{l['name']}</code> → {l['url']}\n"
    await message.reply_text(text)

@new_task
async def cmd_scrape(client, message):
    """Usage: /scrape https://1tamilmv.xxx/... or 1tamilblasters url"""
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text("❌ Usage: <code>/scrape [url]</code>")

    grp_id = _get_group_id()
    if not grp_id:
        return await message.reply_text("❌ AUTO_LEECH_GRP_ID not set in config!")

    url    = parts[1].strip()
    bot_info = await bot.get_me()
    bot_id   = bot_info.id
    thumb    = thumbs_col.find_one({"_id": bot_id})

    await message.reply_text(f"🔍 Scraping: {url}")
    try:
        cget     = create_scraper().request
        resp     = cget("GET", url, allow_redirects=False)
        soup     = BeautifulSoup(resp.text, "html.parser")
        magnets  = soup.select('a[href^="magnet:?xt=urn:btih:"]')
        torrents = soup.select('a[data-fileext="torrent"]')
        title    = soup.title.string if soup.title else "Unknown Title"

        await message.reply_text(f"🎬 <b><u>{title}</u></b>")

        for t, m in zip(torrents, magnets):
            filename   = sub(r"www\S+|\- |\.torrent", "", t.string).strip()
            paste_text = f"🧲 {filename}\n\n<code>{m['href']}</code>\n\n🗒️ <a href=\"{t['href']}\">Torrent File</a>"
            paste_link = _post_to_dpaste(paste_text)
            file_name  = f"{filename}.torrent"
            caption    = f"🧲 <b>{file_name}</b>\n\n🔗 Magnet links:\n{paste_link}"

            if not _download_torrent(t["href"], file_name):
                await message.reply_text(f"❌ Failed to download: {file_name}")
                continue

            await asyncio.sleep(3)
            filee = await bot.send_document(chat_id=grp_id, document=file_name, caption=caption)
            os.remove(file_name)

            dump_arg  = f" -dump {_get_dump_id()}" if _get_dump_id() else ""
            thumb_arg = f" -t {thumb['url']}" if thumb else ""
            cmd = f"/qbleech{thumb_arg}{dump_arg}"

            try:
                leech_msg = await filee.reply_text(cmd)
                bot_loop.create_task(
                    Mirror(bot, leech_msg, is_qbit=True, is_leech=True).new_event()
                )
                await asyncio.sleep(BB_DELAY)
                await leech_msg.delete()
            except Exception as e:
                await filee.reply_text(f"❌ Leech error: {e}")

    except Exception as e:
        await message.reply_text(f"❌ Scrape failed: {e}")

@new_task
async def cmd_add_thumb(client, message):
    """Reply to a photo: /add_thumb"""
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text("⚠️ Reply to a photo to set as thumbnail.")
    bot_id    = (await bot.get_me()).id
    file_path = await message.reply_to_message.download()
    url       = _upload_to_imgbb(file_path)
    os.remove(file_path)
    if not url:
        return await message.reply_text("❌ Failed to upload thumbnail to imgbb.")
    thumbs_col.update_one({"_id": bot_id}, {"$set": {"url": url}}, upsert=True)
    await message.reply_text(f"✅ Thumbnail saved!\n{url}")

@new_task
async def cmd_show_thumb(client, message):
    bot_id = (await bot.get_me()).id
    data   = thumbs_col.find_one({"_id": bot_id})
    if not data:
        return await message.reply_text("❌ No thumbnail found.")
    await message.reply_photo(photo=data["url"], caption=f"📸 Current thumbnail\n{data['url']}")

@new_task
async def cmd_del_thumb(client, message):
    bot_id = (await bot.get_me()).id
    result = thumbs_col.delete_one({"_id": bot_id})
    if result.deleted_count == 0:
        return await message.reply_text("❌ No thumbnail to delete.")
    await message.reply_text("🗑️ Thumbnail deleted.")

@new_task
async def cmd_auto_leech_help(client, message):
    help_text = """<b>⌬ Auto Leech Plugin — WZML-X</b>

<b>Setup Steps:</b>
1️⃣ Add <code>AUTO_LEECH_GRP_ID</code> to your Heroku config vars (the group/channel where files will be leeched)
2️⃣ Add <code>AUTO_LEECH_DUMP_ID</code> (optional — dump channel ID)
3️⃣ Set domains using /setd

<b>Domain Commands (Sudo only):</b>
• /setd <code>[keyword] | [domain]</code> — Set domain
  - Keywords: <code>tmv</code> (1TamilMV), <code>tbl</code> (1TamilBlasters)
• /getd — Show saved domains

<b>Thumbnail Commands:</b>
• /add_thumb — (reply to photo) Set thumbnail
• /show_thumb — View current thumbnail
• /del_thumb — Delete thumbnail

<b>Manual Scrape:</b>
• /scrape <code>[url]</code> — Manually scrape & leech from TamilMV or TamilBlasters URL

<b>Help:</b>
• /auto_leech — Show this help message

<i>Bot auto-checks RSS feeds every ~7 seconds for new releases and leeches them automatically!</i>"""
    await message.reply_text(help_text)

# ─── Register handlers & start loop ──────────────────────────────────────────
bot.add_handler(MessageHandler(cmd_auto_leech_help, filters=command("auto_leech") & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_setdomain,       filters=command("setd")        & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_getdomains,      filters=command("getd")        & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_scrape,          filters=command("scrape")      & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_add_thumb,       filters=command("add_thumb")   & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_show_thumb,      filters=command("show_thumb")  & CustomFilters.sudo))
bot.add_handler(MessageHandler(cmd_del_thumb,       filters=command("del_thumb")   & CustomFilters.sudo))

# Start the auto leecher loop
rss_auto_leecher()
