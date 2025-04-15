# bot_logic.py
import asyncio
import datetime
import logging
import os
import json
import zipfile
from pathlib import Path

import pytz
from telegram import Bot, Message, PhotoSize, ReactionTypeEmoji
from telegram.constants import MessageEntityType
from telegram.error import Forbidden, TelegramError

logger = logging.getLogger(__name__)

# --- Configuration (passed in) ---
# config will be a dictionary loaded from config.ini

# --- Helper Functions ---

def get_last_full_day_range_utc(tz_name='UTC'):
    """Calculates the UTC start and end datetime for the previous full day."""
    try:
        target_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        logger.warning("Unknown timezone '%s', falling back to UTC.", tz_name)
        target_tz = pytz.utc

    now_tz = datetime.datetime.now(target_tz)
    yesterday_tz = now_tz.date() - datetime.timedelta(days=1)

    start_dt_tz = target_tz.localize(datetime.datetime.combine(yesterday_tz, datetime.time.min))
    end_dt_tz = target_tz.localize(datetime.datetime.combine(yesterday_tz, datetime.time.max))

    # Convert to UTC for Telegram API consistency (most APIs use UTC)
    start_dt_utc = start_dt_tz.astimezone(pytz.utc)
    # For iterating, we often want up to, but not including, the start of the *next* day
    # Let's define the end strictly as 23:59:59.999999 for clarity if needed,
    # but usually filtering < start_of_today works.
    end_dt_utc = end_dt_tz.astimezone(pytz.utc)

    logger.info("Target day: %s (%s)", yesterday_tz, tz_name)
    logger.info("UTC Range: %s to %s", start_dt_utc, end_dt_utc)

    return start_dt_utc, end_dt_utc, yesterday_tz # Return date for filenames


async def get_chat_history_for_day(bot: Bot, chat_id: int, start_dt_utc: datetime.datetime, end_dt_utc: datetime.datetime):
    """
    Attempts to fetch message history for a given period.

    NOTE: Telegram Bot API has limitations fetching *old* history.
    This function might only retrieve recent messages or fail if the bot lacks permissions
    or wasn't in the chat during that period. For comprehensive history access,
    a user bot (e.g., using Telethon) is often required.
    """
    messages = []
    last_message_id = None
    limit = 100 # Max limit per request

    logger.info("Attempting to fetch history for chat %d between %s and %s" % (
        chat_id, start_dt_utc, end_dt_utc) )

    # This part is tricky with standard Bot API. get_chat_history isn't a direct method.
    # We might need to iterate using get_chat(chat_id).last_message? Or listen to updates.
    # Let's *simulate* the outcome assuming we *could* get messages.
    # In a real scenario with PTB, you'd likely process messages as they arrive
    # and store them, or use Telethon for historical fetching.

    # --- Placeholder for Actual Fetching Logic ---
    # This is where you'd implement the actual message retrieval.
    # For demonstration, we'll return an empty list and log a warning.
    # Example using a hypothetical (or Telethon-like) approach:
    #
    # try:
    #     async for message in bot.iter_history(chat_id, start_date=end_dt_utc, end_date=start_dt_utc):
    #         # Note: iter_history might not exist in PTB Bot API wrapper like this.
    #         # This usually requires a user client library.
    #         if start_dt_utc <= message.date <= end_dt_utc:
    #             messages.append(message)
    #         elif message.date < start_dt_utc:
    #             break # Stop fetching older messages
    # except Exception as e:
    #     logger.error(f"Error fetching history for chat {chat_id}: {e}")
    #     # Handle specific errors like chat not found, bot kicked, etc.

    logger.warning("History fetching for chat %d is placeholder logic. "
                   "Standard Bot API has limitations. Consider using Telethon for full history access.", chat_id)
    # Return an empty list for now to allow processing logic to run
    return []
    # --- End Placeholder ---


def count_message_reactions(message: Message, like_emojis: list | None = None) -> int:
    """Counts reactions on a message, optionally filtering by specific emojis."""
    if not message.reactions:
        return 0

    total_count = 0
    if like_emojis:
        # Count only specified "like" emojis
        allowed_emojis_set = set(like_emojis)
        for reaction in message.reactions.reactions:
            if isinstance(reaction.type, ReactionTypeEmoji) and reaction.type.emoji in allowed_emojis_set:
                total_count += reaction.count
    else:
        # Count all reactions
        for reaction in message.reactions.reactions:
            total_count += reaction.count
    return total_count

# --- Main Processing Function ---

async def process_chat_history(
    bot: Bot,
    chat_id: int,
    config: dict,
    target_date_override: datetime.date | None = None # Allow specific date for CLI/testing
    ) -> tuple[str | None, list[str]]:
    """
    Fetches history, processes messages, saves popular photos, and creates a zip archive.

    Returns:
        tuple: (path_to_zip_file or None, list_of_saved_popular_photo_paths)
    """
    tz_name = config['History']['timezone']
    min_reactions = int(config['Processing']['min_reactions_for_picture'])
    download_dir = Path(config['Processing']['download_dir'])
    archive_dir = Path(config['Processing']['archive_dir'])
    like_emojis_str = config['Processing']['like_emojis'].strip()
    like_emojis = [e.strip() for e in like_emojis_str.split(',') if e.strip()] if like_emojis_str else None

    # Ensure directories exist
    download_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Determine Date Range
    if target_date_override:
        target_day = target_date_override
        try:
            target_tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            target_tz = pytz.utc
        start_dt_tz = target_tz.localize(datetime.datetime.combine(target_day, datetime.time.min))
        end_dt_tz = target_tz.localize(datetime.datetime.combine(target_day, datetime.time.max))
        start_dt_utc = start_dt_tz.astimezone(pytz.utc)
        end_dt_utc = end_dt_tz.astimezone(pytz.utc)
        logger.info("Processing specified date: %s (%s)", target_day, tz_name)
    else:
        start_dt_utc, end_dt_utc, target_day = get_last_full_day_range_utc(tz_name)

    # 2. Fetch History (Placeholder - see function comment)
    messages = await get_chat_history_for_day(bot, chat_id, start_dt_utc, end_dt_utc)

    if not messages:
        logger.warning("No messages found or fetched for chat %d on %s.", chat_id, target_day)
        # Depending on requirements, you might still want an empty zip or just return
        # return None, [] # Option: return nothing if no messages

    # 3. Process Messages and Find Popular Photos
    processed_data = []
    popular_photo_paths = []
    photo_download_tasks = []
    photo_details = {} # Store details needed after download

    logger.info("Processing %d messages for chat %d on %s...", len(messages), chat_id, target_day)
    for msg in messages:
        if not isinstance(msg, Message): # Ensure it's a message object
            continue

        timestamp = msg.date.isoformat() if msg.date else "Unknown Time"
        sender = msg.from_user.username or msg.from_user.full_name if msg.from_user else "Unknown Sender"
        msg_text = msg.text or msg.caption or ""
        reaction_count = count_message_reactions(msg, like_emojis)

        message_info = {
            "message_id": msg.message_id,
            "sender": sender,
            "timestamp": timestamp,
            "text": msg_text,
            "reactions": reaction_count,
            "photos": []
        }

        if msg.photo:
            # Get the largest photo size
            photo: PhotoSize = msg.photo[-1]
            photo_file_id = photo.file_id
            photo_unique_id = photo.file_unique_id # Good for naming
            photo_filename = f"{target_day}_{chat_id}_{msg.message_id}_{photo_unique_id}.jpg"
            photo_rel_path = f"photos/{photo_filename}" # Path within the zip
            message_info["photos"].append({"file_id": photo_file_id, "zip_path": photo_rel_path})

            if reaction_count >= min_reactions:
                local_save_path = download_dir / photo_filename
                # Schedule download task
                photo_details[photo_file_id] = {"local_path": local_save_path, 
                                                "zip_path": photo_rel_path}
                photo_download_tasks.append(
                    download_file(bot, photo_file_id, local_save_path)
                )
                logger.info("Photo %s from msg %d has %d reactions (>= %d), scheduling download.", 
                            photo_file_id, msg.message_id, reaction_count, min_reactions)


        processed_data.append(message_info)

    # 4. Download Popular Photos Concurrently
    downloaded_files_info = {} # Map file_id to actual downloaded path
    if photo_download_tasks:
        logger.info("Starting download of %d popular photos...", len(photo_download_tasks))
        results = await asyncio.gather(*photo_download_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            file_id = photo_download_tasks[i].__self__.file_id # Access file_id from partial/coroutine if needed
            details = photo_details[file_id]
            if isinstance(result, Exception):
                logger.error("Failed to download photo %s: %s", file_id, result)
            elif result: # result should be the path if successful
                logger.info("Successfully downloaded photo %s to %s", file_id, result)
                popular_photo_paths.append(str(result))
                downloaded_files_info[file_id] = {"local_path": result, "zip_path": details["zip_path"]}
            else:
                logger.warning("Download task for %s completed but returned no path.", file_id)

    # 5. Create ZIP Archive
    zip_filename = f"chat_history_{chat_id}_{target_day}.zip"
    zip_filepath = archive_dir / zip_filename

    logger.info("Creating archive: %s", zip_filepath)
    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add messages summary (e.g., JSON or TXT)
            messages_json_str = json.dumps(processed_data, indent=2, ensure_ascii=False)
            zf.writestr("messages.json", messages_json_str)
            logger.debug("Added messages.json to zip.")

            # Add ALL photos mentioned in messages (if they were downloaded for popularity or need separate download)
            # For simplicity here, we only add the *popular* ones we already downloaded.
            # To add ALL photos, you'd need to download them even if not popular,
            # potentially making the process much longer.

            for file_id, info in downloaded_files_info.items():
                local_path = info["local_path"]
                zip_path = info["zip_path"]
                if local_path.exists():
                    zf.write(local_path, arcname=zip_path)
                    logger.debug("Added %s as %s to zip.", local_path, zip_path)
                else:
                    logger.warning("File %s for photo %s not found for zipping.", local_path, file_id)

        logger.info("Successfully created archive: %s", zip_filepath)
        return str(zip_filepath), popular_photo_paths

    except Exception as e:
        logger.exception("Failed to create zip file %s: %s", zip_filepath, e)
        return None, popular_photo_paths # Return paths even if zip fails

async def download_file(bot: Bot, file_id: str, path: Path):
    """Downloads a file using its file_id to the specified path."""
    try:
        file = await bot.get_file(file_id)
        await file.download_to_drive(custom_path=path)
        return path
    except Forbidden:
        logger.error("Forbidden: Bot might not have permission to download file %s.", file_id)
        # Raise or return None/False? Return None for gather compatibility
        return None
    except TelegramError as e:
        logger.error("TelegramError downloading file %s: %s", file_id, e)
        return None
    except Exception as e:
        logger.exception("Unexpected error downloading file %s: %s", file_id, e)
        return None