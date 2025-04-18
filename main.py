# pylint: disable=line-too-long
# pylint: disable=logging-fstring-interpolation

# main.py
import argparse
import asyncio
import configparser
import logging
import os
import sys
import datetime
from functools import wraps
import json
from pathlib import Path
import html
import requests

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ChatMemberHandler,
                          ContextTypes, Defaults, MessageHandler, filters)
from telegram.constants import ChatMemberStatus
import telegram.error # For error handling

import bot_logic # Import our processing functions

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING) # Reduce library noise
logging.getLogger("telethon").setLevel(logging.INFO) # Reduce library noise
logger = logging.getLogger(__name__)

# --- Load Configuration ---
def load_configuration():
    """Loads config from .env and config.ini"""
    load_dotenv() # Load .env file
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN not found in environment variables or .env file.")
        sys.exit(1)

    admin_id_str = os.getenv("ADMINS_IDS")
    if not admin_id_str:
        logger.critical("ADMINS_IDS not found in environment variables or .env file.")
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        config.read('config.ini')
        # Basic validation
        # if 'Admins' not in config or 'admin_ids' not in config['Admins']:
        #     raise ValueError("Missing [Admins] section or admin_ids in config.ini")
        if 'Processing' not in config or 'min_reactions_for_picture' not in config['Processing']:
            raise ValueError("Missing [Processing] section or min_reactions_for_picture in config.ini")
        # Add more checks as needed (paths, timezone etc)
        config['Bot'] = {'token': token} # Add token to config dict for convenience

        # Parse admin IDs into a set for efficient lookup
        # admin_id_str = config['Admins']['admin_ids']
        admin_ids = {int(admin_id.strip()) for admin_id in admin_id_str.split(',') if admin_id.strip()}
        config['Internal'] = {'admin_id_set': admin_ids} # Store parsed set
        config['HISTORY_ENDPOINT'] = f"{config['server_url']}/process_history"

    except Exception as e:
        logger.critical("Error loading or parsing config.ini: %s", e)
        sys.exit(1)

    return config._sections # Return as a dictionary

CONFIG = load_configuration()
ADMIN_IDS = CONFIG['Internal']['admin_id_set']

# --- Admin Check Decorator ---
def admin_only(func):
    """Decorator to restrict command access to admins defined in config."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or str(user.id) not in ADMIN_IDS:
            logger.warning("Unauthorized access denied for user %d (%s)",
                           user.id, user.username or 'NoUsername')
            if update.message:
                await update.message.reply_text("Sorry, you are not authorized to use this command.")
            return # Stop execution
        logger.info("Admin command execution allowed for user %d", user.id)
        return await func(update, context, *args, **kwargs)
    return wrapped

KNOWN_CHATS_FILE = Path("known_chats.json")
KNOWN_CHATS = {} # Dictionary to store {chat_id: {"title": "...", "type": "..."}}

def load_known_chats():
    global KNOWN_CHATS
    if KNOWN_CHATS_FILE.exists():
        try:
            with open(KNOWN_CHATS_FILE, 'r') as f:
                # Ensure keys are integers after loading from JSON
                KNOWN_CHATS = {int(k): v for k, v in json.load(f).items()}
                logger.info(f"Loaded {len(KNOWN_CHATS)} known chats from file.")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading known chats file: {e}")
            KNOWN_CHATS = {}
    else:
         KNOWN_CHATS = {}

def save_known_chats():
    try:
        with open(KNOWN_CHATS_FILE, 'w') as f:
            json.dump(KNOWN_CHATS, f, indent=2)
    except IOError as e:
        logger.error(f"Error saving known chats file: {e}")



# --- Bot Command Handlers ---
async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tracks chat additions/removals and updates from messages."""
    chat = update.effective_chat
    user = update.effective_user # User who caused the update (if applicable)

    if not chat:
        return # Should not happen for messages/chat member updates

    # Simplest: Update info on every message (can be slightly redundant)
    if chat.id not in KNOWN_CHATS or KNOWN_CHATS[chat.id]['title'] != chat.title:
        logger.info(f"Updating/adding chat {chat.id} ('{chat.title}', type: {chat.type}) to known list.")
        KNOWN_CHATS[chat.id] = {"title": chat.title or f"Chat {chat.id}", "type": chat.type}
        save_known_chats()

async def track_my_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the bot being added or removed from a chat."""
    my_member_update = update.my_chat_member
    if not my_member_update:
        return

    chat = my_member_update.chat
    new_status = my_member_update.new_chat_member.status

    logger.info(f"Bot membership status changed in chat {chat.id} ('{chat.title}') to {new_status}")

    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        if chat.id not in KNOWN_CHATS:
            logger.info(f"Bot added to chat {chat.id} ('{chat.title}', type: {chat.type}). Adding to list.")
            KNOWN_CHATS[chat.id] = {"title": chat.title or f"Chat {chat.id}", "type": chat.type}
            save_known_chats()
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
         if chat.id in KNOWN_CHATS:
            logger.info(f"Bot removed from chat {chat.id}. Removing from list.")
            del KNOWN_CHATS[chat.id]
            save_known_chats()

@admin_only # Assuming you have the admin_only decorator from the previous example
async def list_groupchats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists the chats the bot knows it's in, with clickable process links."""

    if not KNOWN_CHATS:
        await update.message.reply_text("I haven't recorded being in any chats yet.")
        return

    message_lines = ["<b>Chats I'm aware of:</b>"] # Start with HTML bold

    # Sort by title for better readability, handling cases where title might be missing
    sorted_chats = sorted(
        KNOWN_CHATS.items(),
        key=lambda item: item[1].get('title', f'Unknown Chat {item[0]}').lower()
    )

    for chat_id, info in sorted_chats:
        # Safely get title and escape any HTML special characters in it
        title = html.escape(info.get('title', f'Unknown Chat {chat_id}'))
        chat_type = info.get('type', '?')

        # Create the command string for this chat
        command_string = f"/process_history {chat_id}"

        # Format the line using HTML. <code> makes it easy to click/copy.
        line = (
            f"- {title} (ID: <code>{chat_id}</code>, Type: {chat_type})\n"
            f"  └ Run Process: <code>{command_string}</code>"
        )
        message_lines.append(line)

    full_message = "\n".join(message_lines)

    # Handle potential message length limits (Telegram limit is 4096 chars)
    if len(full_message) > 4096:
        # Find a good place to truncate (e.g., before the last entry's start)
        cutoff_point = full_message.rfind('\n-', 0, 4050) # Find last '-' entry start before limit
        if cutoff_point == -1: cutoff_point = 4050 # Fallback if no entry found
        full_message = full_message[:cutoff_point] + "\n\n<b>... (list truncated due to length)</b>"

    # Send the message using HTML parse mode
    await update.message.reply_text(full_message, parse_mode='HTML')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when the command /start is issued."""
    welcome_text = (
        "🤖 Welcome to the Group History Processor Bot!\n\n"
        "Available commands:\n"
        "- /start - Show this help message\n"
        "- /process_history <groupname> - Process yesterday's chat history (Admin only)\n"
        "- /process_history <groupname> <YYYY-MM-DD> - Process chat history for a date (Admin only)\n"
        "- /list_groupchats - List all known group chats (Admin only)\n"
        "\n"
        "This bot helps archive and analyze group chat history."
    )
    await update.message.reply_text(welcome_text)

@admin_only
async def process_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /process_history command.
    Processes history for the chat where the command is issued,
    OR for a specific chat ID provided as an argument.

    Usage:
    /process_history
    /process_history <target_chat_id_or_name>
    /process_history <target_chat_id_or_name> <date>
    """
    user_id = update.effective_user.id
    chat_where_command_was_sent = update.effective_chat.id
    args = context.args # This list contains strings of arguments after the command

    target_chat_id = None
    target_date = None
    feedback_chat_id = chat_where_command_was_sent # Where to send status messages

    if args:
        # Arguments were provided
        target_chat_id = args[0]
        logger.info(f"Admin {user_id} requested processing for specific chat ID: {target_chat_id}")
        await update.message.reply_text(
            f"Processing request for yesterday's history in chat ID: {target_chat_id}..."
            f"\n(I'll send results back here in chat {feedback_chat_id})."
        )

        if len(args) > 1:
            # Additional arguments were provided, assume it's a date
            target_date = args[1]
            try:
                target_date = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                await update.message.reply_text(
                    f"Error: Invalid date format '{target_date}'. Use YYYY-MM-DD."
                )

    else:
        # No arguments, use the current chat
        target_chat_id = chat_where_command_was_sent
        logger.info(f"Admin {user_id} initiated history processing for current chat {target_chat_id}")
        await update.message.reply_text(
             f"Processing request for yesterday's history in this chat (ID: {target_chat_id})..."
        )

    # --- Core Logic Execution ---
    if target_chat_id:
        processing_task = asyncio.create_task(
            bot_logic.process_chat_history(target_chat_id, CONFIG, target_date_override=target_date)
        )
        # Wait for the task to complete
        try:
            zip_filepath, popular_photos = await processing_task
            # --- Sending Results Back ---
            result_message = f"Telethon processing complete for chat: {target_chat_id}.\n"

            result_message = f"Processing complete for chat ID {target_chat_id}.\n"
            if zip_filepath:
                result_message += f"- Archive created: See below.\n"
            else:
                result_message += f"- Archive creation failed or no messages processed.\n"

            if popular_photos:
                result_message += f"- Found {len(popular_photos)} popular photos (saved locally on the server):\n"
                result_message += "\n".join([f"  - {os.path.basename(p)}" for p in popular_photos])
            else:
                result_message += "- No photos met the reaction criteria."

            # Send results to the chat where the command was originally issued
            await context.bot.send_message(chat_id=feedback_chat_id, text=result_message)

            if zip_filepath and os.path.exists(zip_filepath):
                try:
                    # Read the JSON from the zip file
                    import zipfile
                    with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                        with zip_ref.open('messages.json') as json_file:
                            json_data = json_file.read().decode('utf-8')
                    
                    # Send raw JSON to server
                    send_raw_history_to_server(CONFIG['HISTORY_ENDPOINT'], json_data)
                    
                    # Send the document to the chat where the command was issued
                    await context.bot.send_document(
                        chat_id=feedback_chat_id, document=open(zip_filepath, 'rb')
                    )

                except telegram.error.NetworkError as ne:
                    logger.error(f"Network error sending zip file {zip_filepath} to {feedback_chat_id}: {ne}")
                    await context.bot.send_message(chat_id=feedback_chat_id, text=f"Network error sending archive: {ne}. File saved locally.")

                except Exception as e:
                    logger.error(f"Failed to send zip file {zip_filepath} to chat {feedback_chat_id}: {e}")
                    await context.bot.send_message(
                        chat_id=feedback_chat_id, text=f"Could not send the archive file: {e}"
                    )

        except Exception as e:
            logger.exception(f"Error during /process_history command for target chat {target_chat_id} "
                             f"(requested from chat {feedback_chat_id}): {e}")
            # Get more detailed traceback for logging
            # tb_str = traceback.format_exc()
            # logger.error(f"Traceback:\n{tb_str}")
            await context.bot.send_message(
                chat_id=feedback_chat_id, text=f"An unexpected error occurred while processing chat {target_chat_id}: {e}"
            )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    logger.error("Update %s caused error %s", update, context.error, exc_info=context.error)


def send_raw_history_to_server(history_endpoint, json_string_data):
    """Sends the raw JSON string to the FastAPI server."""
    if not json_string_data:
        print("ℹ️ No JSON string data to send.")
        return

    print(f"🚀 Sending raw JSON string to {history_endpoint}...")

    # Set the Content-Type header explicitly to indicate it's JSON data
    # Even though the server treats it as raw text, this is accurate
    headers = {'Content-Type': 'application/json; charset=utf-8'}

    try:
        # Use the 'data' parameter to send raw bytes
        # Encode the Python string to UTF-8 bytes before sending
        response = requests.post(
            history_endpoint,
            data=json_string_data.encode('utf-8'), # Crucial: encode string to bytes
            headers=headers,
            timeout=90 # Increase timeout for potentially large data + LLM processing
        )

        # Check the response status code
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        print(f"✅ Success! Server responded with status code {response.status_code}.")
        # Process the response from the server
        try:
            result = response.json()
            print("📦 Server response:")
            # Pretty print the JSON response
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print("⚠️ Server response was not valid JSON.")
            print("Raw response text:", response.text)

    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending request to server: {e}")
        # More specific error details if available (e.g., connection error, timeout)
        if response is not None:
            print(f"Raw Response Text (if any): {response.text}")
    except Exception as e:
        print(f"❌ An unexpected error occurred during the request: {e}")

# --- CLI Handling ---
async def run_cli_processing(args):
    """Initializes a temporary bot instance and runs processing from CLI."""
    logger.info("Running in CLI mode.")
    if not args.chat_id:
        print("Error: --chat-id is required for CLI mode.", file=sys.stderr)
        sys.exit(1)

    # try:
    #     target_chat_entity = int(args.chat_id)
    # except ValueError:
    #     print(f"Error: Invalid chat ID '{args.chat_id}'. Must be an integer.", file=sys.stderr)
    #     sys.exit(1)
    target_chat_entity = args.chat_id

    target_date = None
    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    print(f"Processing history for chat ID: {target_chat_entity} on date: {target_date or 'yesterday'}")
    try:
        # Directly call the bot_logic function which now uses Telethon
        zip_filepath, popular_photos = await bot_logic.process_chat_history(
            target_chat_entity, CONFIG, target_date_override=target_date
        )


        print("\nProcessing Results:")
        if zip_filepath:
            print(f"- Archive created at: {zip_filepath}")
        else:
            print("- Archive creation failed or no messages processed.")

        if popular_photos:
            print(f"- Found {len(popular_photos)} popular photos saved locally:")
            for photo_path in popular_photos:
                print(f"  - {photo_path}")
        else:
            print("- No photos met the reaction criteria.")

    except Exception as e:
        logger.exception("Error during CLI processing for chat %s: %s", target_chat_entity, e)
        print(f"\nAn error occurred: {e}", file=sys.stderr)


def main():
    """ --- Main Execution --- """
    parser = argparse.ArgumentParser(description="Telegram Group History Processor Bot & CLI")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line mode instead of as a bot."
    )
    parser.add_argument(
        "--chat-id",
        type=str, # Read as string first for better error handling
        help="Target chat ID (required for CLI mode)."
    )
    parser.add_argument(
        "--date",
        type=str,
        help="Target date (YYYY-MM-DD) for processing (CLI mode only, defaults to yesterday)."
    )
    args = parser.parse_args()
    load_known_chats() # Load known chats at startup

    if args.cli:
        # Run the CLI part using asyncio
        asyncio.run(run_cli_processing(args))
    else:
        # Run the bot
        logger.info("Starting Telegram bot...")
        defaults = Defaults()
        application = (
            ApplicationBuilder()
            .token(CONFIG['Bot']['token'])
            .defaults(defaults)
            .build()
        )

        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("process_history", process_history_command))
        application.add_handler(CommandHandler("list_groupchats", list_groupchats_command))
        application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.UpdateType.MESSAGE & (~filters.COMMAND), track_chats))
        application.add_handler(ChatMemberHandler(track_my_membership, ChatMemberHandler.MY_CHAT_MEMBER))
        # Add other handlers if needed (e.g., /start, /help)
        application.add_error_handler(error_handler)

        # Start the Bot
        application.run_polling()


if __name__ == "__main__":
    main()
