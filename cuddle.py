from __future__ import annotations

import asyncio
import hashlib
import logging
import os

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ChosenInlineResult,
)
from pyrogram.enums import ChatAction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8832662771:AAHch3TkJhesVy4yiqbY4Dgb1HaWldY9VuM"
API_ID    = 25723056
API_HASH  = "cbda56fac135e92b755e1243aefe9697"
API_KEY   = "24292c_6TacMPfjHR_E4kloVn-JvTvtmIWSf4i0"
API_BASE  = "https://api.onegrab.fun"

HEADERS = {"X-API-Key": API_KEY}

_url_store: dict[str, str] = {}
_inline_chat: dict[str, int] = {}

MUSIC_PLATFORMS = (
    "youtube.com", "youtu.be", "music.youtube.com",
    "soundcloud.com", "music.apple.com", "deezer.com",
    "jiosaavn.com", "open.spotify.com", "gaana.com",
    "tidal.com", "bilibili.com", "mxplayer.in",
)

SNAP_PLATFORMS = (
    "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "facebook.com", "fb.watch", "pinterest.com", "reddit.com",
    "linkedin.com", "threads.net", "snapchat.com", "tumblr.com",
    "twitch.tv", "kick.com", "bluesky.app", "bsky.app",
)

app = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def _make_id(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _store_url(url: str) -> str:
    key = _make_id(url)[:16]
    _url_store[key] = url
    return key


def _get_url(key: str) -> str | None:
    return _url_store.get(key)


def classify_url(text: str) -> str | None:
    if not text.startswith(("http://", "https://")):
        return None
    if any(p in text for p in SNAP_PLATFORMS):
        return "snap"
    if any(p in text for p in MUSIC_PLATFORMS):
        return "music"
    return None


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict) -> dict:
    async with session.get(url, params=params, headers=HEADERS) as r:
        return await r.json()


async def webm_to_mp3(webm_path: str, mp3_path: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", webm_path,
        "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        mp3_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, msg: Message):
    await msg.reply(
        "🎵 **Media Downloader Bot**\n\n"
        "**Music URLs:**\n"
        "YouTube, YouTube Music, SoundCloud, Apple Music,\n"
        "Deezer, JioSaavn, Spotify, Gaana, Tidal\n\n"
        "**Social Media:**\n"
        "Instagram, Twitter/X, TikTok, Facebook, Reddit,\n"
        "LinkedIn, Threads, Snapchat, Twitch, Kick & more\n\n"
        "**Commands:**\n"
        "• Send any URL directly\n"
        "• `/search <query>` — search on YouTube\n"
        "• `/search <platform> <query>` — specific platform"
    )


@app.on_message(filters.command("search"))
async def search_cmd(client: Client, msg: Message):
    args = msg.text.split(None, 2)[1:]

    if not args:
        return await msg.reply("Usage: `/search <query>` or `/search <platform> <query>`")

    platforms = (
        "youtube", "ytmusic", "soundcloud", "apple_music",
        "deezer", "jiosaavn", "spotify", "gaana", "tidal",
    )

    if args[0].lower() in platforms:
        platform = args[0].lower()
        query    = args[1] if len(args) > 1 else ""
    else:
        platform = "youtube"
        query    = " ".join(args)

    if not query:
        return await msg.reply("❌ Query nahi diya!")

    wait = await msg.reply("🔍 Searching...")
    await client.send_chat_action(msg.chat.id, ChatAction.TYPING)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(
                session,
                f"{API_BASE}/api/search",
                {"query": query, "platform": platform, "limit": 5},
            )
        except Exception as e:
            return await wait.edit(f"❌ Error: {e}")

    results = data.get("results", [])
    if not results:
        return await wait.edit("❌ No results found.")

    buttons = []
    text    = f"🔍 **Results for:** `{query}`\n\n"

    for i, r in enumerate(results, 1):
        dur  = r.get("duration", 0)
        mins = dur // 60
        secs = dur % 60
        text += f"{i}. **{r['title']}**\n└ {r.get('channel', '')} • {mins}:{secs:02d}\n\n"
        buttons.append([
            InlineKeyboardButton(
                f"{i}. {r['title'][:40]}",
                callback_data=f"dl:{_store_url(r['url'])}",
            )
        ])

    await wait.edit(text, reply_markup=InlineKeyboardMarkup(buttons))


@app.on_message(filters.text & filters.regex(r"^https?://"))
async def handle_url(client: Client, msg: Message):
    url      = msg.text.strip()
    url_type = classify_url(url)

    if not url_type:
        return await msg.reply(
            "❌ Unsupported URL.\n\n"
            "Supported music: YouTube, SoundCloud, Spotify, Apple Music, Deezer, JioSaavn, Gaana, Tidal\n"
            "Supported social: Instagram, Twitter/X, TikTok, Facebook, Reddit, LinkedIn, Threads & more"
        )

    if url_type == "snap":
        await _snap_and_send(client, msg.chat.id, url, reply_to=msg.id)
    else:
        await _download_and_send(client, msg.chat.id, url, reply_to=msg.id)


@app.on_inline_query()
async def inline_search(client: Client, query: InlineQuery):
    text = query.query.strip()

    if not text or len(text) < 2:
        return await query.answer([])

    url_type = classify_url(text)

    if url_type:
        result_id = _make_id(text)
        key       = _store_url(text)
        label     = "📥 Download Video/Media" if url_type == "snap" else "🎵 Download Audio"
        cb_prefix = "snap" if url_type == "snap" else "dl"

        return await query.answer([
            InlineQueryResultArticle(
                id=result_id,
                title=label,
                description=text[:80],
                input_message_content=InputTextMessageContent(f"⏳ Processing `{text}`..."),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(label, callback_data=f"{cb_prefix}:{key}")
                ]]),
            )
        ])

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(
                session,
                f"{API_BASE}/api/search",
                {"query": text, "platform": "youtube", "limit": 8},
            )
        except Exception:
            return await query.answer([])

    results_raw = data.get("results", [])
    if not results_raw:
        return await query.answer([])

    results = []
    for r in results_raw:
        dur  = r.get("duration", 0)
        mins = dur // 60
        secs = dur % 60

        results.append(
            InlineQueryResultArticle(
                id=_make_id(r["url"]),
                title=r["title"],
                description=f"{r.get('channel', '')} • {mins}:{secs:02d}",
                thumb_url=r.get("thumbnail", ""),
                input_message_content=InputTextMessageContent(
                    f"🎵 **{r['title']}**\n"
                    f"👤 {r.get('channel', '')}\n"
                    f"⏱ {mins}:{secs:02d}"
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬇️ Download", callback_data=f"dl:{_store_url(r['url'])}")
                ]]),
            )
        )

    await query.answer(results, cache_time=10)


@app.on_chosen_inline_result()
async def chosen_result(client: Client, result: ChosenInlineResult):
    # Pyrogram ChosenInlineResult mein chat_id milta hai
    if result.chat_id:
        _inline_chat[result.inline_message_id] = result.chat_id


@app.on_callback_query()
async def callback_handler(client: Client, query: CallbackQuery):
    await query.answer()

    # Group/PM se aaya — seedha wahi bhejo
    if query.message:
        chat_id  = query.message.chat.id
        reply_to = query.message.id
    else:
        # Inline message callback — saved chat_id use karo
        chat_id  = _inline_chat.get(query.inline_message_id)
        reply_to = None
        if not chat_id:
            return await client.send_message(query.from_user.id, "❌ Session expired, send the URL again.")

    if query.data.startswith("dl:"):
        url = _get_url(query.data[3:])
        if not url:
            return await client.send_message(chat_id, "❌ Session expired.")
        await _download_and_send(client, chat_id, url, reply_to=reply_to)

    elif query.data.startswith("snap:"):
        url = _get_url(query.data[5:])
        if not url:
            return await client.send_message(chat_id, "❌ Session expired.")
        await _snap_and_send(client, chat_id, url, reply_to=reply_to)


async def _snap_and_send(client: Client, chat_id: int, url: str, reply_to: int = None):
    wait = await client.send_message(chat_id, "⏳ Processing...", reply_to_message_id=reply_to)
    await client.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(session, f"{API_BASE}/api/snap", {"url": url})
        except Exception as e:
            return await wait.edit(f"❌ Error: {e}")

    if not data or "error" in data:
        return await wait.edit(f"❌ No media found.\n`{data}`")

    videos  = data.get("videos") or []
    audios  = data.get("audios") or []
    images  = data.get("images") or []
    title   = data.get("title", "")
    caption = title[:900] if title else None

    if not videos and not audios and not images:
        return await wait.edit(f"❌ No media found.\n`{data}`")

    await wait.edit("📥 Downloading...")

    async with aiohttp.ClientSession() as session:
        for item in videos[:4]:
            video_url = item.get("url") or ""
            if not video_url:
                continue
            try:
                async with session.get(video_url) as resp:
                    if resp.status != 200:
                        continue
                    video_bytes = await resp.read()
                await client.send_video(
                    chat_id,
                    video=video_bytes,
                    caption=caption,
                    supports_streaming=True,
                )
            except Exception as e:
                logger.warning(f"Video send failed: {e}")

        for item in audios[:2]:
            audio_url = item.get("url") or ""
            if not audio_url:
                continue
            try:
                async with session.get(audio_url) as resp:
                    if resp.status != 200:
                        continue
                    audio_bytes = await resp.read()
                await client.send_audio(chat_id, audio=audio_bytes, caption=caption)
            except Exception as e:
                logger.warning(f"Audio send failed: {e}")

        if images and not videos:
            from pyrogram.types import InputMediaPhoto as PyroInputMediaPhoto
            media_group = []
            for img_url in images[:10]:
                if isinstance(img_url, str) and img_url:
                    media_group.append(PyroInputMediaPhoto(img_url))
            if media_group:
                media_group[0].caption = caption
                try:
                    await client.send_media_group(chat_id, media_group)
                except Exception as e:
                    logger.warning(f"Photo group send failed: {e}")

    await wait.delete()


async def _download_and_send(client: Client, chat_id: int, url: str, reply_to: int = None):
    wait = await client.send_message(chat_id, "⏳ Processing...", reply_to_message_id=reply_to)
    await client.send_chat_action(chat_id, ChatAction.UPLOAD_AUDIO)

    async with aiohttp.ClientSession() as session:
        try:
            track, meta = await asyncio.gather(
                fetch_json(session, f"{API_BASE}/api/track", {"url": url}),
                fetch_json(session, f"{API_BASE}/api/get_url", {"url": url}),
            )
        except Exception as e:
            return await wait.edit(f"❌ Error: {e}")

    if "cdnurl" not in track:
        return await wait.edit(f"❌ Failed to get download URL.\n`{track}`")

    cdn_url  = track["cdnurl"]
    platform = track.get("platform", "")
    info     = meta.get("results", [{}])[0]

    title    = info.get("title")     or track.get("title")     or "Audio"
    channel  = info.get("channel")   or track.get("channel")   or ""
    duration = info.get("duration")  or track.get("duration")  or 0
    thumb    = info.get("thumbnail") or track.get("thumbnail") or ""
    video_id = track.get("id")       or _make_id(url)[:16]

    caption = f"🎵 **{title}**"
    if channel:
        caption += f"\n👤 {channel}"
    caption += f"\n🌐 {platform.capitalize()}"

    await wait.edit(f"📥 Downloading **{title}**...")

    webm_path  = f"/tmp/{video_id}.webm"
    mp3_path   = f"/tmp/{video_id}.mp3"
    thumb_path = f"/tmp/{video_id}.jpg"

    try:
        msg_id = int(cdn_url.split("/")[-1])
        await client.download_media(
            await client.get_messages("FALLENAPI", msg_id),
            file_name=webm_path,
        )

        if not os.path.exists(webm_path):
            return await wait.edit("❌ Download failed.")

        await wait.edit(f"🔄 Converting **{title}**...")

        if not await webm_to_mp3(webm_path, mp3_path):
            return await wait.edit("❌ Conversion failed.")

        thumb_ok = False
        if thumb:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(thumb) as r:
                        if r.status == 200:
                            with open(thumb_path, "wb") as f:
                                f.write(await r.read())
                            thumb_ok = True
                except Exception:
                    pass

        await wait.edit(f"📤 Uploading **{title}**...")

        await client.send_audio(
            chat_id,
            audio=mp3_path,
            caption=caption,
            duration=duration or None,
            performer=channel or None,
            title=title,
            thumb=thumb_path if thumb_ok else None,
        )

        await wait.delete()

    except Exception as e:
        await wait.edit(f"❌ Send failed: {e}")

    finally:
        for path in (webm_path, mp3_path, thumb_path):
            try:
                os.remove(path)
            except Exception:
                pass


if __name__ == "__main__":
    app.run()
