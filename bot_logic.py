# pylint: disable=line-too-long
# pylint: disable=logging-fstring-interpolation

# bot_logic.py
import asyncio
import datetime
import logging
import os
import zipfile
import json

from pathlib import Path
import pytz
import re
from dotenv import load_dotenv

# Telethon Libraries
from telethon import TelegramClient
from telethon.tl.types import Message, Photo, ReactionCount # Import specific Telethon types
from telethon.errors import SessionPasswordNeededError, FloodWaitError, ChatAdminRequiredError, UserNotParticipantError
from telethon.utils import get_display_name

logger = logging.getLogger(__name__)

# --- Load Telethon Config ---
# Load .env variables for Telethon credentials needed within this module
load_dotenv()
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.getenv("TELETHON_SESSION_NAME", "my_telegram_session")

if not API_ID or not API_HASH:
    logger.critical("TELEGRAM_API_ID or TELEGRAM_API_HASH not found in environment variables.")
    # Decide how to handle this - exit or raise error? For now, log critical.
    # sys.exit(1) # Or raise ValueError(...)

try:
    API_ID = int(API_ID)
except (ValueError, TypeError):
    logger.critical("TELEGRAM_API_ID is not a valid integer.")
    # sys.exit(1) # Or raise ValueError(...)


# --- Helper Functions ---

def get_last_full_day_range_utc(tz_name='UTC'):
    """Calculates the UTC start and end datetime for the previous full day."""
    try:
        target_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{tz_name}', falling back to UTC.")
        target_tz = pytz.utc

    now_tz = datetime.datetime.now(target_tz)
    yesterday_tz = now_tz.date() - datetime.timedelta(days=1)

    start_dt_tz = target_tz.localize(datetime.datetime.combine(yesterday_tz, datetime.time.min))
    # End is exclusive for Telethon's offset_date, so use start of the next day
    end_dt_tz = start_dt_tz + datetime.timedelta(days=1)

    # Convert to UTC for Telethon API consistency and filtering
    start_dt_utc = start_dt_tz.astimezone(pytz.utc)
    end_dt_utc = end_dt_tz.astimezone(pytz.utc) # This is now the *start* of the target day

    logger.info(f"Target day: {yesterday_tz} ({tz_name})")
    logger.info(f"UTC Range: >= {start_dt_utc} and < {end_dt_utc}")

    # Return the date for filenames, and the precise start/end UTC datetimes
    return start_dt_utc, end_dt_utc, yesterday_tz


async def get_chat_history_for_day_telethon(
    client: TelegramClient,
    chat_entity, # Can be chat ID, username, etc.
    start_dt_utc: datetime.datetime,
    end_dt_utc: datetime.datetime):
    """
    Fetches message history for a given period using Telethon.
    Iterates from oldest to newest within the approximate range.
    """
    messages = []
    logger.info(f"Attempting to fetch Telethon history for chat '{chat_entity}' between {start_dt_utc} and {end_dt_utc}")

    try:
        # Ensure client is connected (although usually done outside)
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            logger.error("Telethon client is not authorized. Please run script interactively first.")
            # Handle authorization flow if needed (e.g., phone code, password)
            # This basic implementation assumes an existing, authorized session.
            return [] # Cannot proceed without authorization

        # Use iter_messages:
        # - Set offset_date to the *end* of the period (exclusive). Messages *older* than this will start.
        # - Use reverse=True to get messages oldest-first, making it easier to stop.
        # - Set limit=None to fetch all messages in the range (Telethon handles batching).
        async for message in client.iter_messages(
            entity=chat_entity,
            limit=None,
            offset_date=start_dt_utc, # the meaning of `offset_date` parameters is reversed, so fetch messages *after* the start of the prev day (UTC)
            reverse=True             # Start from older messages towards newer ones
        ):
            # client.iter_messages(entity=chat_entity, limit=None, offset_date=end_dt_utc, reverse=True)
            # Message dates are timezone-aware (usually UTC)
            msg_date_utc = message.date

            # Check if the message date is within our *precise* desired range
            if msg_date_utc >= end_dt_utc:
                # This message is too new (shouldn't happen often with offset_date but good check)
                continue
            if msg_date_utc < start_dt_utc:
                # This message is older than our start date. Since we reverse, we can stop.
                # However, offset_date should handle this. Let's double check logic.
                # If reverse=True, messages older than offset_date appear first.
                # The iteration should ideally *start* near start_dt_utc.
                # Let's filter strictly >= start_dt_utc
                pass # Skip messages before the start time

            # If we are here, the message is within the target day [start_dt_utc, end_dt_utc)
            if start_dt_utc <= msg_date_utc < end_dt_utc:
                messages.append(message)
            elif msg_date_utc >= end_dt_utc:
                # If somehow we get a message newer than our range end (after starting), stop.
                break


        logger.info(f"Fetched {len(messages)} messages using Telethon for chat '{chat_entity}' on target day.")
        return messages

    except (ChatAdminRequiredError, UserNotParticipantError):
        logger.error(f"Cannot access chat '{chat_entity}'. Bot/User may lack permissions or not be a participant.")
        return []
    except ValueError as e:
        logger.error(f"Invalid chat entity '{chat_entity}': {e}. Is the ID/username correct?")
        return []
    except FloodWaitError as e:
        logger.warning(f"Telegram Flood Wait: Sleeping for {e.seconds} seconds.")
        await asyncio.sleep(e.seconds + 1)
        # Potentially retry logic could be added here, but for now, just return empty
        return []
    except Exception as e:
        logger.exception(f"Unexpected error fetching Telethon history for chat '{chat_entity}': {e}")
        return []


def count_telethon_message_reactions(message: Message, like_emojis: list | None = None) -> int:
    """Counts reactions on a Telethon message, optionally filtering."""
    if not message.reactions or not message.reactions.results:
        return 0

    total_count = 0
    if like_emojis:
        allowed_emojis_set = set(like_emojis)
        for reaction_count in message.reactions.results:
            # Accessing emoji requires checking the reaction type
            # For ReactionCount, reaction is the emoji string itself
            if isinstance(reaction_count, ReactionCount) and reaction_count.reaction in allowed_emojis_set:
                total_count += reaction_count.count
            # TODO: Handle ReactionCustomEmoji if needed
    else:
        # Count all reactions
        for reaction_count in message.reactions.results:
            total_count += reaction_count.count
    return total_count


async def download_telethon_file(client: TelegramClient, message_media, path: Path):
    """Downloads media from a Telethon message to the specified path."""
    try:
        await client.download_media(message_media, file=path)
        return path
    except Exception as e:
        # Catch specific Telethon errors if needed
        logger.exception(f"Unexpected error downloading media via Telethon: {e}")
        return None

# --- Main Processing Function ---

async def process_chat_history(
    chat_id_or_username: int | str,
    config: dict,
    target_date_override: datetime.date | None = None
    ) -> tuple[str | None, list[str]]:
    """
    Fetches history using Telethon, processes messages, saves popular photos, and creates a zip archive.

    Returns:
        tuple: (path_to_zip_file or None, list_of_saved_popular_photo_paths)
    """
    if not API_ID or not API_HASH:
        logger.error("Telethon API_ID or API_HASH not configured. Cannot process history.")
        return None, []

    tz_name = config['History']['timezone']
    min_reactions = int(config['Processing']['min_reactions_for_picture'])
    download_dir = Path(config['Processing']['download_dir'])
    archive_dir = Path(config['Processing']['archive_dir'])
    like_emojis_str = config['Processing']['like_emojis'].strip()
    like_emojis = [e.strip() for e in like_emojis_str.split(',') if e.strip()] if like_emojis_str else None

    # Ensure output directories exist
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
        end_dt_tz = start_dt_tz + datetime.timedelta(days=1) # Exclusive end
        start_dt_utc = start_dt_tz.astimezone(pytz.utc)
        end_dt_utc = end_dt_tz.astimezone(pytz.utc)
        logger.info(f"Processing specified date: {target_day} ({tz_name})")
    else:
        start_dt_utc, end_dt_utc, target_day = get_last_full_day_range_utc(tz_name)

    # 2. Fetch History using Telethon
    messages = []
    telethon_client = TelegramClient(SESSION_NAME, API_ID, API_HASH, system_version="4.16.30-vxCUSTOM") # Use session name

    try:
        logger.info(f"Connecting Telethon client (Session: {SESSION_NAME})...")
        # Context manager handles connect/disconnect
        async with telethon_client as client:
            messages = await get_chat_history_for_day_telethon(
                client, chat_id_or_username, start_dt_utc, end_dt_utc
            )
    except SessionPasswordNeededError:
        logger.error("Telethon login failed: 2FA Password needed. Run script interactively first.")
        # Cannot proceed without interactive password entry
        return None, []
    except Exception as e:
        logger.exception(f"Failed to initialize or run Telethon client: {e}")
        return None, []
    # Client is automatically disconnected here by 'async with'

    if not messages:
        logger.warning(f"No messages found or fetched via Telethon for chat '{chat_id_or_username}' on {target_day}.")
        # Decide if an empty zip should be created or just return
        return None, []

    # 3. Process Messages and Find Popular Photos
    processed_data = []
    popular_photo_paths = []
    photo_download_tasks = []
    photo_details = {} # Store details needed after download

    logger.info(f"Processing {len(messages)} Telethon messages for chat '{chat_id_or_username}' on {target_day}...")
    # Need the client again for downloads, let's reconnect briefly if needed, or structure differently
    # Reconnecting for downloads might be inefficient. Better to do downloads within the 'async with' block?
    # Let's restructure slightly to download within the fetch block or pass client

    # --- Option: Re-Connect for downloads (simpler structure for now) ---
    # This is less efficient but separates concerns slightly
    download_client = TelegramClient(SESSION_NAME, API_ID, API_HASH, system_version="4.16.30-vxCUSTOM")
    try:
        async with download_client as dl_client:
            if not await dl_client.is_user_authorized():
                raise ValueError("Client not authorized for downloads") # Should be authorized already

            for msg in messages:
                if not isinstance(msg, Message):
                    continue

                timestamp = msg.date.isoformat()
                sender_obj = await msg.get_sender() # Need to fetch sender info
                sender_name = get_display_name(sender_obj) if sender_obj else "Unknown Sender"
                msg_text = msg.text or "" # Telethon uses msg.text for caption too
                reaction_count = count_telethon_message_reactions(msg, like_emojis)

                message_info = {
                    "message_id": msg.id,
                    "sender": sender_name,
                    "sender_id": sender_obj.id if sender_obj else None,
                    "timestamp": timestamp,
                    "text": msg_text,
                    "reactions": reaction_count,
                    "photos": []
                }

                if msg.photo and isinstance(msg.photo, Photo):
                    # Telethon message.photo is the Photo object directly (largest size usually)
                    photo_id = msg.photo.id
                    # Create a unique-enough filename
                    # Access hash might change, use photo_id and message_id
                    photo_filename = f"{target_day}_{msg.chat_id}_{msg.id}_{photo_id}.jpg"
                    photo_rel_path = f"photos/{photo_filename}"
                    message_info["photos"].append({"photo_id": photo_id, "zip_path": photo_rel_path})

                    if reaction_count >= min_reactions:
                        local_save_path = download_dir / photo_filename
                        # Schedule download task using the download_client
                        photo_details[msg.id] = {"local_path": local_save_path, "zip_path": photo_rel_path, "media": msg.photo}
                        # Use partial or lambda to pass arguments to the download coroutine
                        task = download_telethon_file(dl_client, msg.photo, local_save_path)
                        photo_download_tasks.append(task)
                        logger.info(f"Photo msg {msg.id} has {reaction_count} reactions (>= {min_reactions}), scheduling download.")

                processed_data.append(message_info)

             # 4. Download Popular Photos Concurrently
            downloaded_files_info = {}
            if photo_download_tasks:
                logger.info(f"Starting download of {len(photo_download_tasks)} popular photos via Telethon...")
                results = await asyncio.gather(*photo_download_tasks, return_exceptions=True)

                # Match results back to original info (this mapping is tricky with gather)
                # Iterate through original details and check results based on path?
                for msg_id, details in photo_details.items():
                    local_path = details["local_path"]
                    # Find the result corresponding to this path (or index if tasks kept order)
                    # This assumes results maintain order, which gather *usually* does
                    # A more robust way might involve returning (path, success_flag) from download
                    idx = -1
                    for i, task in enumerate(photo_download_tasks):
                        # Inspecting the task details to match is hard. Assume order for now.
                        # Or match based on the `local_path` passed to the task if possible.
                        # Let's find the corresponding result by checking the target path
                        # This relies on download_telethon_file returning the path on success
                        potential_path = None
                        task_coro = task # asyncio.gather returns results in order
                        # Find the corresponding result by iterating through results
                        # This is still clumsy. A better approach is needed if order isn't guaranteed
                        # or if we need to correlate failures better.

                    # Simplistic approach: Assume results are ordered and check for Exceptions
                    try:
                        # Find index based on details (requires tasks to be identifiable or ordered)
                        # For now, let's just iterate results and log based on the path returned
                        pass # This correlation needs improvement

                    except Exception as gather_e:
                        logger.error(f"Error processing download result: {gather_e}")


                # Simpler logging based on successful results:
                successful_downloads = [str(res) for res in results if isinstance(res, Path) and res.exists()]
                failed_downloads = [str(details['local_path']) for i, details in enumerate(photo_details.values()) 
                                    if isinstance(results[i], Exception)]

                popular_photo_paths.extend(successful_downloads)
                for path_str in successful_downloads:
                    # Find corresponding details to store zip_path
                    for msg_id_d, details_d in photo_details.items():
                        if str(details_d["local_path"]) == path_str:
                            downloaded_files_info[msg_id_d] = {"local_path": Path(path_str), "zip_path": details_d["zip_path"]}
                            break

                if failed_downloads:
                    logger.warning(f"Failed to download {len(failed_downloads)} popular photos: {failed_downloads}")


    except ValueError as ve: # Catch the "Client not authorized" error
        logger.error(f"Telethon authorization error during downloads: {ve}")
        # Cannot proceed with downloads
    except Exception as e:
        logger.exception(f"Error during Telethon download phase: {e}")
    # Download client automatically disconnected

    # 5. Create ZIP Archive
    zip_filename = f"chat_history_{chat_id_or_username}_{target_day}.zip"
    # Sanitize chat_id_or_username if it's a string like '@channelname'
    safe_chat_ref = re.sub(r'[^\w\-]+', '_', str(chat_id_or_username))
    zip_filename = f"chat_history_{safe_chat_ref}_{target_day}.zip"
    zip_filepath = archive_dir / zip_filename

    logger.info(f"Creating archive: {zip_filepath}")
    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
            messages_json_str = json.dumps(processed_data, indent=2, ensure_ascii=False)
            zf.writestr("messages.json", messages_json_str)
            logger.debug("Added messages.json to zip.")

            # Add downloaded popular photos
            for msg_id_f, info in downloaded_files_info.items():
                local_path = info["local_path"]
                zip_path = info["zip_path"]
                if local_path.exists():
                    zf.write(local_path, arcname=zip_path)
                    logger.debug(f"Added {local_path} as {zip_path} to zip.")
                else:
                    logger.warning(f"File {local_path} for popular photo msg {msg_id_f} not found for zipping.")

        logger.info(f"Successfully created archive: {zip_filepath}")
        return str(zip_filepath), popular_photo_paths

    except Exception as e:
        logger.exception(f"Failed to create zip file {zip_filepath}: {e}")
        # Return paths even if zip fails, but None for zip path
        return None, popular_photo_paths
    