from __future__ import annotations

import asyncio
import hashlib
import logging
import os

import aiohttp
from pyrogram import Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    filters,
)
from telegram.constants import ChatAction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8832662771:AAHch3TkJhesVy4yiqbY4Dgb1HaWldY9VuM"
BOT_USERNAME = "NixieAllDownloaderbot"
API_ID    = 25723056
API_HASH  = "cbda56fac135e92b755e1243aefe9697"
API_KEY   = "24292c_6TacMPfjHR_E4kloVn-JvTvtmIWSf4i0"
API_BASE  = "https://api.onegrab.fun"

HEADERS = {"X-API-Key": API_KEY}

pyro: Client = None

_url_store: dict[str, str] = {}

# inline_result_id -> (url, url_type)
_inline_pending: dict[str, tuple[str, str]] = {}

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


async def _edit_or_reply(context, chat_id, wait_msg_id, text, **kwargs):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=wait_msg_id,
            text=text,
            **kwargs,
        )
    except Exception:
        await context.bot.send_message(chat_id, text, **kwargs)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    args = context.args

    # Deep link handler: /start dl_<key> or /start snap_<key>
    if args:
        param = args[0]
        if param.startswith("dl_") or param.startswith("snap_"):
            url_type = "music" if param.startswith("dl_") else "snap"
            key      = param[3:]
            url      = _get_url(key)
            if not url:
                return await msg.reply_text("❌ Link expired. Please search again.")
            if url_type == "snap":
                await _snap_and_send(msg, context, url)
            else:
                await _download_and_send(msg, context, url)
            return

    await msg.reply_text(
        "🎵 *Media Downloader Bot*\n\n"
        "*Music URLs:*\n"
        "YouTube, YouTube Music, SoundCloud, Apple Music,\n"
        "Deezer, JioSaavn, Spotify, Gaana, Tidal\n\n"
        "*Social Media:*\n"
        "Instagram, Twitter/X, TikTok, Facebook, Reddit,\n"
        "LinkedIn, Threads, Snapchat, Twitch, Kick & more\n\n"
        "*Commands:*\n"
        "• Send any URL directly\n"
        "• `/search <query>` — search on YouTube\n"
        "• `/search <platform> <query>` — specific platform",
        parse_mode="Markdown",
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    args = context.args

    if not args:
        return await msg.reply_text(
            "Usage: `/search <query>` or `/search <platform> <query>`",
            parse_mode="Markdown",
        )

    platforms = (
        "youtube", "ytmusic", "soundcloud", "apple_music",
        "deezer", "jiosaavn", "spotify", "gaana", "tidal",
    )

    if args[0].lower() in platforms:
        platform = args[0].lower()
        query    = " ".join(args[1:])
    else:
        platform = "youtube"
        query    = " ".join(args)

    if not query:
        return await msg.reply_text("❌ Query nahi diya!")

    wait = await msg.reply_text("🔍 Searching...")
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(
                session,
                f"{API_BASE}/api/search",
                {"query": query, "platform": platform, "limit": 5},
            )
        except Exception as e:
            return await wait.edit_text(f"❌ Error: {e}")

    results = data.get("results", [])
    if not results:
        return await wait.edit_text("❌ No results found.")

    buttons = []
    text    = f"🔍 *Results for:* `{query}`\n\n"

    for i, r in enumerate(results, 1):
        dur  = r.get("duration", 0)
        mins = dur // 60
        secs = dur % 60
        text += f"{i}. *{r['title']}*\n└ {r.get('channel', '')} • {mins}:{secs:02d}\n\n"
        buttons.append([
            InlineKeyboardButton(
                f"{i}. {r['title'][:40]}",
                callback_data=f"dl:{_store_url(r['url'])}",
            )
        ])

    await wait.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg      = update.effective_message
    url      = msg.text.strip()
    url_type = classify_url(url)

    if not url_type:
        return await msg.reply_text(
            "❌ Unsupported URL.\n\n"
            "Supported music: YouTube, SoundCloud, Spotify, Apple Music, Deezer, JioSaavn, Gaana, Tidal\n"
            "Supported social: Instagram, Twitter/X, TikTok, Facebook, Reddit, LinkedIn, Threads & more"
        )

    if url_type == "snap":
        await _snap_and_send(msg, context, url)
    else:
        await _download_and_send(msg, context, url)


async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram sends chosen_inline_result with inline_message_id AND from/chat context.
    We use this to know which chat the inline result was sent to, then edit that message.
    """
    result   = update.chosen_inline_result
    result_id = result.result_id
    chat_id  = result.from_user.id  # fallback — see note below

    pending = _inline_pending.get(result_id)
    if not pending:
        return

    url, url_type = pending

    # We can only get the inline_message_id here, not the group chat_id
    # So we edit the inline message to show status, and send media to user PM
    # For group delivery, user must click the callback button on the inline message
    inline_message_id = result.inline_message_id

    if inline_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                inline_message_id=inline_message_id,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬇️ Download",
                        callback_data=f"{'snap' if url_type == 'snap' else 'dl'}:{_store_url(url)}",
                    )
                ]]),
            )
        except Exception:
            pass


async def _snap_and_send(source, context, url: str):
    from telegram import Message
    if isinstance(source, Message):
        chat_id = source.chat_id
        wait    = await source.reply_text("⏳ Processing...")
    else:
        chat_id = source.from_user.id
        wait    = await context.bot.send_message(chat_id, "⏳ Processing...")

    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(session, f"{API_BASE}/api/snap", {"url": url})
        except Exception as e:
            return await wait.edit_text(f"❌ Error: {e}")

    if not data or "error" in data:
        return await wait.edit_text(f"❌ No media found.\n`{data}`", parse_mode="Markdown")

    videos  = data.get("videos") or []
    audios  = data.get("audios") or []
    images  = data.get("images") or []
    title   = data.get("title", "")
    caption = title[:900] if title else None

    if not videos and not audios and not images:
        return await wait.edit_text(f"❌ No media found.\n`{data}`", parse_mode="Markdown")

    await wait.edit_text("📥 Downloading...")

    async with aiohttp.ClientSession() as session:
        for item in videos[:4]:
            video_url = item.get("url") or ""
            thumb_url = item.get("thumbnail") or ""
            if not video_url:
                continue
            try:
                async with session.get(video_url) as resp:
                    if resp.status != 200:
                        continue
                    video_bytes = await resp.read()
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_bytes,
                    caption=caption,
                    thumbnail=thumb_url or None,
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
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_bytes,
                    caption=caption,
                    title=title[:64] if title else None,
                )
            except Exception as e:
                logger.warning(f"Audio send failed: {e}")

        if images and not videos:
            media_group = []
            for img_url in images[:10]:
                if isinstance(img_url, str) and img_url:
                    media_group.append(InputMediaPhoto(media=img_url))
            if media_group:
                try:
                    media_group[0] = InputMediaPhoto(
                        media=media_group[0].media,
                        caption=caption,
                    )
                    await context.bot.send_media_group(chat_id=chat_id, media=media_group)
                except Exception as e:
                    logger.warning(f"Photo group send failed: {e}")

    await wait.delete()


async def _download_and_send(source, context, url: str):
    from telegram import Message
    if isinstance(source, Message):
        chat_id = source.chat_id
        wait    = await source.reply_text("⏳ Processing...")
    else:
        chat_id = source.from_user.id
        wait    = await context.bot.send_message(chat_id, "⏳ Processing...")

    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    async with aiohttp.ClientSession() as session:
        try:
            track, meta = await asyncio.gather(
                fetch_json(session, f"{API_BASE}/api/track", {"url": url}),
                fetch_json(session, f"{API_BASE}/api/get_url", {"url": url}),
            )
        except Exception as e:
            return await wait.edit_text(f"❌ Error: {e}")

    if "cdnurl" not in track:
        return await wait.edit_text(
            f"❌ Failed to get download URL.\n`{track}`",
            parse_mode="Markdown",
        )

    cdn_url  = track["cdnurl"]
    platform = track.get("platform", "")
    info     = meta.get("results", [{}])[0]

    title    = info.get("title")     or track.get("title")     or "Audio"
    channel  = info.get("channel")   or track.get("channel")   or ""
    duration = info.get("duration")  or track.get("duration")  or 0
    thumb    = info.get("thumbnail") or track.get("thumbnail") or ""
    video_id = track.get("id")       or _make_id(url)[:16]

    caption = f"🎵 *{title}*"
    if channel:
        caption += f"\n👤 {channel}"
    caption += f"\n🌐 {platform.capitalize()}"

    await wait.edit_text(f"📥 Downloading *{title}*...", parse_mode="Markdown")

    webm_path  = f"/tmp/{video_id}.webm"
    mp3_path   = f"/tmp/{video_id}.mp3"
    thumb_path = f"/tmp/{video_id}.jpg"

    try:
        msg_id = int(cdn_url.split("/")[-1])
        await pyro.download_media(
            await pyro.get_messages("FALLENAPI", msg_id),
            file_name=webm_path,
        )

        if not os.path.exists(webm_path):
            return await wait.edit_text("❌ Download failed.")

        await wait.edit_text(f"🔄 Converting *{title}*...", parse_mode="Markdown")

        if not await webm_to_mp3(webm_path, mp3_path):
            return await wait.edit_text("❌ Conversion failed.")

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

        with open(mp3_path, "rb") as audio_f:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_f,
                filename=f"{title}.mp3",
                caption=caption,
                parse_mode="Markdown",
                duration=duration or None,
                performer=channel or None,
                title=title,
                thumbnail=open(thumb_path, "rb") if thumb_ok else None,
            )

        await wait.delete()

    except Exception as e:
        await wait.edit_text(f"❌ Send failed: {e}")

    finally:
        for path in (webm_path, mp3_path, thumb_path):
            try:
                os.remove(path)
            except Exception:
                pass


async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    text  = query.query.strip()

    if not text or len(text) < 2:
        return await query.answer([], cache_time=0)

    url_type = classify_url(text)

    if url_type:
        result_id = _make_id(text)
        key       = _store_url(text)
        cb_prefix = "snap" if url_type == "snap" else "dl"
        label     = "📥 Download Video/Media" if url_type == "snap" else "🎵 Download Audio"

        _inline_pending[result_id] = (text, url_type)

        result = InlineQueryResultArticle(
            id=result_id,
            title=label,
            description=text[:80],
            input_message_content=InputTextMessageContent(
                message_text=f"⏳ Processing `{text}`...",
                parse_mode="Markdown",
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(label, callback_data=f"{cb_prefix}:{key}")
            ]]),
        )
        return await query.answer([result], cache_time=0)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(
                session,
                f"{API_BASE}/api/search",
                {"query": text, "platform": "youtube", "limit": 8},
            )
        except Exception:
            return await query.answer([], cache_time=0)

    results_raw = data.get("results", [])
    if not results_raw:
        return await query.answer([], cache_time=0)

    results = []
    for r in results_raw:
        dur      = r.get("duration", 0)
        mins     = dur // 60
        secs     = dur % 60
        desc     = f"{r.get('channel', '')} • {mins}:{secs:02d}"
        result_id = _make_id(r["url"])
        key      = _store_url(r["url"])

        _inline_pending[result_id] = (r["url"], "music")

        msg_text = (
            f"🎵 *{r['title']}*\n"
            f"👤 {r.get('channel', '')}\n"
            f"⏱ {mins}:{secs:02d}"
        )

        results.append(
            InlineQueryResultArticle(
                id=result_id,
                title=r["title"],
                description=desc,
                thumbnail_url=r.get("thumbnail", ""),
                input_message_content=InputTextMessageContent(
                    message_text=msg_text,
                    parse_mode="Markdown",
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬇️ Download", callback_data=f"dl:{key}")
                ]]),
            )
        )

    await query.answer(results, cache_time=10)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # If query.message exists — regular chat/group, send there
    # If None — came from inline message, send to user PM
    if query.message is not None:
        chat_id = query.message.chat_id
        source  = query.message
    else:
        chat_id = query.from_user.id
        source  = None

    if query.data.startswith("dl:"):
        key = query.data[3:]
        url = _get_url(key)
        if not url:
            return await context.bot.send_message(chat_id, "❌ Session expired, send the URL again.")
        if source:
            await _download_and_send(source, context, url)
        else:
            wait = await context.bot.send_message(chat_id, "⏳ Processing...")
            await _download_and_send_to(wait, chat_id, context, url)

    elif query.data.startswith("snap:"):
        key = query.data[5:]
        url = _get_url(key)
        if not url:
            return await context.bot.send_message(chat_id, "❌ Session expired, send the URL again.")
        if source:
            await _snap_and_send(source, context, url)
        else:
            wait = await context.bot.send_message(chat_id, "⏳ Processing...")
            await _snap_and_send_to(wait, chat_id, context, url)


async def _snap_and_send_to(wait, chat_id: int, context, url: str):
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)

    async with aiohttp.ClientSession() as session:
        try:
            data = await fetch_json(session, f"{API_BASE}/api/snap", {"url": url})
        except Exception as e:
            return await wait.edit_text(f"❌ Error: {e}")

    if not data or "error" in data:
        return await wait.edit_text(f"❌ No media found.\n`{data}`", parse_mode="Markdown")

    videos  = data.get("videos") or []
    audios  = data.get("audios") or []
    images  = data.get("images") or []
    title   = data.get("title", "")
    caption = title[:900] if title else None

    if not videos and not audios and not images:
        return await wait.edit_text(f"❌ No media found.\n`{data}`", parse_mode="Markdown")

    await wait.edit_text("📥 Downloading...")

    async with aiohttp.ClientSession() as session:
        for item in videos[:4]:
            video_url = item.get("url") or ""
            thumb_url = item.get("thumbnail") or ""
            if not video_url:
                continue
            try:
                async with session.get(video_url) as resp:
                    if resp.status != 200:
                        continue
                    video_bytes = await resp.read()
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_bytes,
                    caption=caption,
                    thumbnail=thumb_url or None,
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
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_bytes,
                    caption=caption,
                    title=title[:64] if title else None,
                )
            except Exception as e:
                logger.warning(f"Audio send failed: {e}")

        if images and not videos:
            media_group = []
            for img_url in images[:10]:
                if isinstance(img_url, str) and img_url:
                    media_group.append(InputMediaPhoto(media=img_url))
            if media_group:
                try:
                    media_group[0] = InputMediaPhoto(media=media_group[0].media, caption=caption)
                    await context.bot.send_media_group(chat_id=chat_id, media=media_group)
                except Exception as e:
                    logger.warning(f"Photo group send failed: {e}")

    await wait.delete()


async def _download_and_send_to(wait, chat_id: int, context, url: str):
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    async with aiohttp.ClientSession() as session:
        try:
            track, meta = await asyncio.gather(
                fetch_json(session, f"{API_BASE}/api/track", {"url": url}),
                fetch_json(session, f"{API_BASE}/api/get_url", {"url": url}),
            )
        except Exception as e:
            return await wait.edit_text(f"❌ Error: {e}")

    if "cdnurl" not in track:
        return await wait.edit_text(f"❌ Failed to get download URL.\n`{track}`", parse_mode="Markdown")

    cdn_url  = track["cdnurl"]
    platform = track.get("platform", "")
    info     = meta.get("results", [{}])[0]

    title    = info.get("title")     or track.get("title")     or "Audio"
    channel  = info.get("channel")   or track.get("channel")   or ""
    duration = info.get("duration")  or track.get("duration")  or 0
    thumb    = info.get("thumbnail") or track.get("thumbnail") or ""
    video_id = track.get("id")       or _make_id(url)[:16]

    caption = f"🎵 *{title}*"
    if channel:
        caption += f"\n👤 {channel}"
    caption += f"\n🌐 {platform.capitalize()}"

    await wait.edit_text(f"📥 Downloading *{title}*...", parse_mode="Markdown")

    webm_path  = f"/tmp/{video_id}.webm"
    mp3_path   = f"/tmp/{video_id}.mp3"
    thumb_path = f"/tmp/{video_id}.jpg"

    try:
        msg_id = int(cdn_url.split("/")[-1])
        await pyro.download_media(
            await pyro.get_messages("FALLENAPI", msg_id),
            file_name=webm_path,
        )

        if not os.path.exists(webm_path):
            return await wait.edit_text("❌ Download failed.")

        await wait.edit_text(f"🔄 Converting *{title}*...", parse_mode="Markdown")

        if not await webm_to_mp3(webm_path, mp3_path):
            return await wait.edit_text("❌ Conversion failed.")

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

        with open(mp3_path, "rb") as audio_f:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_f,
                filename=f"{title}.mp3",
                caption=caption,
                parse_mode="Markdown",
                duration=duration or None,
                performer=channel or None,
                title=title,
                thumbnail=open(thumb_path, "rb") if thumb_ok else None,
            )

        await wait.delete()

    except Exception as e:
        await wait.edit_text(f"❌ Send failed: {e}")

    finally:
        for path in (webm_path, mp3_path, thumb_path):
            try:
                os.remove(path)
            except Exception:
                pass


async def run():
    global pyro
    pyro = Client(
        "bot_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )
    await pyro.start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^https?://"), handle_url))
    app.add_handler(InlineQueryHandler(inline_search))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    app.add_handler(CallbackQueryHandler(callback_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Bot started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
