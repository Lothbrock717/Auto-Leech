# Auto Leech Plugin for WZML-X (wzv3)
# Ported from Thiru-ML by @ThiruXD
# Adapted for WZML-X PluginBase system

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
from bot.core.plugin_manager import PluginBase, PluginInfo
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.ext_utils.bot_utils import new_task
from bot.modules.mirror_leech import Mirror
from pymongo import MongoClient
from pymongo.server_api import ServerApi

# ─── Settings ────────────────────────────────────────────────────────────────
AA_DELAY      = 5
BB_DELAY      = 7
is_running    = True
IMGBB_API_KEY = "7c555e92974a9049ac25c7e6f2afa652"

# ─── MongoDB ─────────────────────────────────────────────────────────────────
_client        = MongoClient(Config.DATABASE_URL, server_api=ServerApi("1"))
_db            = _client.autoleech_wzmlx
rss_domains    = _db["rss_domains"]
rss_collection = _db["rss_col_data"]
thumbs_col     = _db["thumbnails"]

# ─── Domain helpers ───────────────────────────────────────────────────────────
def _get_link(name):
    doc = rss_domains.find_one({"name": name})
    return doc["url"] if doc else None

def _insert_or_update_link(name, data):
    result = rss_domains.update_one({"name": name}, {"$set": data}, upsert=True)
    return f"Link '{name}' {'updated' if result.matched_count else 'inserted'}."

def _get_all_links():
    return list(rss_domains.find({}))

def _build_rss_urls():
    tmv = _get_link("tmv")
    tbl = _get_link("tbl")
    urls = {}
    if tmv:
        urls.update({
            f"{tmv}/index.php?/forums/forum/17-hollywood-movies-in-multi-audios/all.xml": "hollywood_1tmv",
            f"{tmv}/index.php?/forums/forum/11-web-hd-itunes-hd-bluray/all.xml":         "tamil_1tmv",
            f"{tmv}/index.php?/forums/forum/10-predvd-dvdscr-cam-tc/all.xml":            "tamilmv_tamil_cam",
            f"{tmv}/index.php?/forums/forum/25-hd-rips-dvd-rips-br-rips/all.xml":       "tamilmv_telegu_hdrip",
            f"{tmv}/index.php?/forums/forum/24-web-hd-itunes-hd-bluray/all.xml":        "tamilmv_telegu_webhd",
            f"{tmv}/index.php?/forums/forum/58-web-hd-itunes-hd-bluray/all.xml":        "tamilmv_hindi_webhd",
            f"{tmv}/index.php?/forums/forum/36-web-hd-itunes-hd-bluray/all.xml":        "tamilmv_malay_webhd",
            f"{tmv}/index.php?/forums/forum/49-web-hd-itunes-hd-bluray/all.xml":        "tamilmv_english_webhd",
        })
    if tbl:
        urls.update({
            f"{tbl}/index.php?/forums/forum/7-tamil-new-movies-hdrips-bdrips-dvdrips-hdtv/all.xml":                                       "tamil_tbl",
            f"{tbl}/index.php?/forums/forum/9-tamil-dubbed-movies-bdrips-hdrips-dvdscr-hdcam-in-multi-audios/all.xml": "hollywood_tbl",
        })
    return urls

# ─── Utilities ────────────────────────────────────────────────────────────────
def _post_to_dpaste(content):
    try:
        r = requests.post("https://dpaste.org/api/",
                          data={"content": content, "syntax": "json", "expiry_days": "360"})
        return r.text.strip() if r.status_code == 200 else f"dpaste error {r.status_code}"
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
    try:
        r = create_scraper().get(url, allow_redirects=True)
        if r.status_code == 200 and b"announce" in r.content[:500]:
            with open(file_name, "wb") as f:
                f.write(r.content)
            return file_name
    except Exception as e:
        LOGGER.error(f"[AutoLeech] Torrent download error: {e}")
    return None

def _get_group_id():
    grp = getattr(Config, "AUTO_LEECH_GRP_ID", None) or Config.LEECH_DUMP_CHAT
    if not grp:
        LOGGER.warning("[AutoLeech] AUTO_LEECH_GRP_ID not set!")
    return grp

def _get_dump_id():
    return getattr(Config, "AUTO_LEECH_DUMP_ID", None) or Config.LEECH_DUMP_CHAT

# ─── Core scraper ────────────────────────────────────────────────────────────
async def _process_feed(rss_url, keyword):
    grp_id = _get_group_id()
    if not grp_id:
        return
    bot_info = await bot.get_me()
    thumb    = thumbs_col.find_one({"_id": bot_info.id})
    feed     = feedparser.parse(rss_url)
    if not feed.entries:
        return
    first_link = feed.entries[0].link
    existing   = rss_collection.find_one({"keyword": keyword})
    if not existing:
        rss_collection.insert_one({"keyword": keyword, "url": "Nhai-Illa"})
        existing = {"url": "Nhai-Illa"}
    if existing["url"] == first_link:
        return
    try:
        resp     = create_scraper().request("GET", first_link, allow_redirects=False)
        soup     = BeautifulSoup(resp.text, "html.parser")
        magnets  = soup.select('a[href^="magnet:?xt=urn:btih:"]')
        torrents = soup.select('a[data-fileext="torrent"]')
        title    = soup.title.string if soup.title else "Unknown"
        title_msg = await bot.send_message(grp_id,
            f"🎬 <b><u>{title}</u></b>\n\n— Auto Leech by WZML-X")
        try:
            await bot.pin_chat_message(grp_id, title_msg.id)
        except Exception:
            pass
        for t, m in zip(torrents, magnets):
            fname      = sub(r"www\S+|\- |\.torrent", "", t.string).strip()
            paste_text = f"🧲 {fname}\n\n<code>{m['href']}</code>\n\n🗒️ <a href=\"{t['href']}\">Torrent</a>"
            paste_link = _post_to_dpaste(paste_text)
            file_name  = f"{fname}.torrent"
            caption    = f"🧲 <b>{file_name}</b>\n\n🔗 {paste_link}"
            if not _download_torrent(t["href"], file_name):
                await bot.send_message(grp_id, f"❌ Failed: {caption}")
                continue
            await asyncio.sleep(3)
            filee = await bot.send_document(chat_id=grp_id, document=file_name, caption=caption)
            os.remove(file_name)
            dump_arg  = f" -dump {_get_dump_id()}" if _get_dump_id() else ""
            thumb_arg = f" -t {thumb['url']}" if thumb else ""
            try:
                leech_msg = await filee.reply_text(f"/qbleech{thumb_arg}{dump_arg}")
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
        LOGGER.error(f"[AutoLeech] Feed error: {e}")

# ─── RSS loop ─────────────────────────────────────────────────────────────────
async def _rss_loop():
    LOGGER.info("[AutoLeech] RSS Auto Leecher started!")
    while is_running:
        await sleep(AA_DELAY)
        try:
            urls = _build_rss_urls()
            if not urls:
                await sleep(60)
                continue
            for url, keyword in urls.items():
                await sleep(BB_DELAY)
                await _process_feed(url, keyword)
        except Exception as e:
            LOGGER.error(f"[AutoLeech] Loop error: {e}")

# ─── Command handlers ─────────────────────────────────────────────────────────
@new_task
async def cmd_auto_leech_help(client, message):
    await message.reply_text("""<b>⌬ Auto Leech Plugin — WZML-X</b>

<b>Setup:</b>
1️⃣ Add <code>AUTO_LEECH_GRP_ID</code> to Heroku config vars
2️⃣ Add <code>AUTO_LEECH_DUMP_ID</code> (optional)
3️⃣ Set domains using /setd

<b>Commands:</b>
• /setd <code>[keyword] | [domain]</code> — Set domain
  - <code>tmv</code> → 1TamilMV
  - <code>tbl</code> → 1TamilBlasters
• /getd — Show saved domains
• /scrape <code>[url]</code> — Manual scrape & leech
• /add_thumb — (reply to photo) Set thumbnail
• /show_thumb — View thumbnail
• /del_thumb — Delete thumbnail""")

@new_task
async def cmd_setdomain(client, message):
    if "|" not in message.text:
        return await message.reply_text(
            "❌ Usage: <code>/setd [keyword] | [domain]</code>\n\nKeywords: <code>tmv</code>, <code>tbl</code>")
    parts = message.text.split("|", 1)
    name  = parts[0].split()[-1].strip()
    link  = parts[1].strip()
    result = _insert_or_update_link(name, {"url": link, "title": name})
    await message.reply_text(f"✅ {result}")

@new_task
async def cmd_getdomains(client, message):
    links = _get_all_links()
    if not links:
        return await message.reply_text("❌ No domains saved. Use /setd to add one.")
    text = "🌐 <b>Saved Domains:</b>\n\n"
    for l in links:
        text += f"• <code>{l['name']}</code> → {l['url']}\n"
    await message.reply_text(text)

@new_task
async def cmd_scrape(client, message):
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text("❌ Usage: <code>/scrape [url]</code>")
    grp_id = _get_group_id()
    if not grp_id:
        return await message.reply_text("❌ AUTO_LEECH_GRP_ID not set!")
    url      = parts[1].strip()
    bot_info = await bot.get_me()
    thumb    = thumbs_col.find_one({"_id": bot_info.id})
    await message.reply_text(f"🔍 Scraping: {url}")
    try:
        resp     = create_scraper().request("GET", url, allow_redirects=False)
        soup     = BeautifulSoup(resp.text, "html.parser")
        magnets  = soup.select('a[href^="magnet:?xt=urn:btih:"]')
        torrents = soup.select('a[data-fileext="torrent"]')
        title    = soup.title.string if soup.title else "Unknown"
        await message.reply_text(f"🎬 <b><u>{title}</u></b>")
        for t, m in zip(torrents, magnets):
            fname      = sub(r"www\S+|\- |\.torrent", "", t.string).strip()
            paste_text = f"🧲 {fname}\n\n<code>{m['href']}</code>\n\n🗒️ <a href=\"{t['href']}\">Torrent</a>"
            paste_link = _post_to_dpaste(paste_text)
            file_name  = f"{fname}.torrent"
            caption    = f"🧲 <b>{file_name}</b>\n\n🔗 {paste_link}"
            if not _download_torrent(t["href"], file_name):
                await message.reply_text(f"❌ Failed: {file_name}")
                continue
            await asyncio.sleep(3)
            filee = await bot.send_document(chat_id=grp_id, document=file_name, caption=caption)
            os.remove(file_name)
            dump_arg  = f" -dump {_get_dump_id()}" if _get_dump_id() else ""
            thumb_arg = f" -t {thumb['url']}" if thumb else ""
            try:
                leech_msg = await filee.reply_text(f"/qbleech{thumb_arg}{dump_arg}")
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
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text("⚠️ Reply to a photo to set as thumbnail.")
    bot_id    = (await bot.get_me()).id
    file_path = await message.reply_to_message.download()
    url       = _upload_to_imgbb(file_path)
    os.remove(file_path)
    if not url:
        return await message.reply_text("❌ Failed to upload thumbnail.")
    thumbs_col.update_one({"_id": bot_id}, {"$set": {"url": url}}, upsert=True)
    await message.reply_text(f"✅ Thumbnail saved!\n{url}")

@new_task
async def cmd_show_thumb(client, message):
    bot_id = (await bot.get_me()).id
    data   = thumbs_col.find_one({"_id": bot_id})
    if not data:
        return await message.reply_text("❌ No thumbnail found.")
    await message.reply_photo(photo=data["url"], caption=f"📸 Thumbnail\n{data['url']}")

@new_task
async def cmd_del_thumb(client, message):
    bot_id = (await bot.get_me()).id
    result = thumbs_col.delete_one({"_id": bot_id})
    if result.deleted_count == 0:
        return await message.reply_text("❌ No thumbnail to delete.")
    await message.reply_text("🗑️ Thumbnail deleted.")

# ─── Plugin Class ─────────────────────────────────────────────────────────────
class AutoLeechPlugin(PluginBase):
    PLUGIN_INFO = PluginInfo(
        name="autoleech",
        version="1.0.0",
        author="ThiruXD (ported for WZML-X)",
        description="Auto leech from TamilMV & TamilBlasters RSS feeds",
        commands=["auto_leech", "setd", "getd", "scrape", "add_thumb", "show_thumb", "del_thumb"],
    )

    async def on_load(self) -> bool:
        sudo = CustomFilters.sudo
        bot.add_handler(MessageHandler(cmd_auto_leech_help, filters=command("auto_leech") & sudo))
        bot.add_handler(MessageHandler(cmd_setdomain,       filters=command("setd")        & sudo))
        bot.add_handler(MessageHandler(cmd_getdomains,      filters=command("getd")        & sudo))
        bot.add_handler(MessageHandler(cmd_scrape,          filters=command("scrape")      & sudo))
        bot.add_handler(MessageHandler(cmd_add_thumb,       filters=command("add_thumb")   & sudo))
        bot.add_handler(MessageHandler(cmd_show_thumb,      filters=command("show_thumb")  & sudo))
        bot.add_handler(MessageHandler(cmd_del_thumb,       filters=command("del_thumb")   & sudo))
        bot_loop.create_task(_rss_loop())
        LOGGER.info("[AutoLeech] Plugin loaded successfully!")
        return True
