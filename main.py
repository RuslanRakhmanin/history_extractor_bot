# pylint: disable=line-too-long

# main.py
import argparse
import asyncio
import configparser
import logging
import os
import sys
import datetime
from functools import wraps

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (Application, ApplicationBuilder, CommandHandler,
                          ContextTypes, Defaults, MessageHandler, filters)

import bot_logic # Import our processing functions

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING) # Reduce library noise
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

# --- Bot Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when the command /start is issued."""
    welcome_text = (
        "🤖 Welcome to the Group History Processor Bot!\n\n"
        "Available commands:\n"
        "- /start - Show this help message\n"
        "- /process_history - Process yesterday's chat history (Admin only)\n\n"
        "This bot helps archive and analyze group chat history."
    )
    await update.message.reply_text(welcome_text)

@admin_only
async def process_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /process_history command."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await update.message.reply_text("Processing request for yesterday's history...")
    logger.info("Admin %s initiated history processing for chat %s", user_id, chat_id)

    try:
        # Use the bot instance from context
        zip_filepath, popular_photos = await bot_logic.process_chat_history(
            context.bot, chat_id, CONFIG
        )

        result_message = "Processing complete.\n"
        if zip_filepath:
            result_message += "- Archive created: Sent below.\n"
        else:
            result_message += "- Archive creation failed or no messages processed.\n"

        if popular_photos:
            result_message += f"- Found {len(popular_photos)} popular photos (saved locally on the server):\n"
             # Only list filenames for brevity in chat
            result_message += "\n".join([f"  - {os.path.basename(p)}" for p in popular_photos])
        else:
            result_message += "- No photos met the reaction criteria."

        await update.message.reply_text(result_message)

        if zip_filepath and os.path.exists(zip_filepath):
            try:
                await update.message.reply_document(document=open(zip_filepath, 'rb'))
            except Exception as e:
                logger.error("Failed to send zip file %s: %s", zip_filepath, e)
                await update.message.reply_text(f"Could not send the archive file: {e}")

    except Exception as e:
        logger.exception("Error during /process_history command for chat %s: %s", chat_id, e)
        await update.message.reply_text(f"An unexpected error occurred: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    logger.error("Update %s caused error %s", update, context.error, exc_info=context.error)


# --- CLI Handling ---
async def run_cli_processing(args):
    """Initializes a temporary bot instance and runs processing from CLI."""
    logger.info("Running in CLI mode.")
    if not args.chat_id:
        print("Error: --chat-id is required for CLI mode.", file=sys.stderr)
        sys.exit(1)

    try:
        chat_id = int(args.chat_id)
    except ValueError:
        print(f"Error: Invalid chat ID '{args.chat_id}'. Must be an integer.", file=sys.stderr)
        sys.exit(1)

    target_date = None
    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    # Need a bot instance to make API calls
    defaults = Defaults()
    # Use ApplicationBuilder just to get a configured bot instance easily
    cli_app = ApplicationBuilder().token(CONFIG['Bot']['token']).defaults(defaults).build()
    bot = cli_app.bot # Get the bot instance

    print(f"Processing history for chat ID: {chat_id} on date: {target_date or 'yesterday'}")
    try:
        zip_filepath, popular_photos = await bot_logic.process_chat_history(
            bot, chat_id, CONFIG, target_date_override=target_date
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
        logger.exception("Error during CLI processing for chat %s: %s", chat_id, e)
        print(f"\nAn error occurred: {e}", file=sys.stderr)
    finally:
        # Gracefully shutdown the underlying httpx client if Application was used
        if hasattr(cli_app, '_updater') and cli_app._updater: # Check internal structure might change
            await cli_app.shutdown()
        elif hasattr(cli_app, 'shutdown'): # More direct if available
            await cli_app.shutdown()



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
        # Add other handlers if needed (e.g., /start, /help)
        application.add_error_handler(error_handler)

        # Start the Bot
        application.run_polling()


if __name__ == "__main__":
    main()
