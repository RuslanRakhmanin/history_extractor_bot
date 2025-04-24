# Telegram Group Chat History Processor

A Python-based Telegram bot and CLI tool designed to process message history from specified Telegram group chats. It retrieves messages from the previous full day, identifies popular photos based on reactions, archives the data, and saves popular photos locally. This tool utilizes the `python-telegram-bot` library for the command interface and `Telethon` for accessing user-level chat history.

## Features

*   **History Retrieval:** Fetches message history for a specified group chat during the last full calendar day (00:00 to 23:59).
*   **Configurable Timezone:** Defines the "day" based on a timezone set in the configuration.
*   **Data Archiving:** Packages processed messages (including text, sender, timestamp, reaction counts) and photo references into a ZIP archive (`messages.json` + photos folder).
*   **Popular Photo Identification:** Identifies photo messages exceeding a configurable reaction threshold.
*   **Local Photo Saving:** Downloads and saves identified popular photos to a local directory.
*   **Dual Interface:**
    *   **Telegram Bot:** Responds to commands (e.g., `/process_history`) within Telegram. Bot commands are restricted to authorized admin users.
    *   **Command-Line Interface (CLI):** Allows triggering the processing directly from the server's terminal.
*   **Configurable:** Settings like admin IDs, reaction thresholds, output directories, and API keys are managed via configuration files (`.env` and `config.ini`).
*   **History Access:** Uses the Telethon library (authenticating as a user) to reliably access chat history, overcoming Bot API limitations.

## Requirements

*   Python 3.8+
*   A Telegram Account (the one that will be used by Telethon to access chat history)
*   A Telegram Bot Token (create a bot via [@BotFather](https://t.me/BotFather))
*   Telegram API ID and API Hash (obtainable from [my.telegram.org](https://my.telegram.org/apps) for your user account)

## Setup & Installation

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd history_extractor_bot
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # Activate the environment
    # On Linux/macOS:
    source venv/bin/activate
    # On Windows:
    .\venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Edit the `.env` file and fill in your actual credentials:
        *   `TELEGRAM_BOT_TOKEN`: Your token from @BotFather.
        *   `ADMINS_IDS`: Comma-separated list of numeric Telegram User IDs authorized to use bot commands. Find your ID using bots like [@userinfobot](https://t.me/userinfobot)
        *   `TELEGRAM_API_ID`: Your API ID from my.telegram.org.
        *   `TELEGRAM_API_HASH`: Your API Hash from my.telegram.org.
        *   `TELETHON_SESSION_NAME`: A name for the Telethon session file (e.g., `my_telegram_session`). This file stores login info after the first successful authorization.

5.  **Configure Settings:**
    *   Edit the `config.ini` file:
        *   `[Processing]` -> `min_reactions_for_picture`: Set the minimum reaction count for a photo to be considered popular.
        *   `[Processing]` -> `download_dir`, `archive_dir`: Set the relative paths for saving downloaded photos and ZIP archives.
        *   `[Processing]` -> `like_emojis`: Optionally specify emojis to count as "likes" (comma-separated, e.g., `👍, ❤️`). Leave empty to count all reactions.
        *   `[Processing]` -> `server_url`: Set the URL where the AI processing service is hosted. Use a server like [gemini-to-magento-service](https://github.com/MykolaKaradzha/gemini-to-magento-service)
        *   `[History]` -> `timezone`: Set the Olson timezone string (e.g., `UTC`, `Europe/London`, `America/New_York`) to define the "day".

6.  **Authorize Telethon (First Run - IMPORTANT!):**
    *   Telethon needs to log in as your user account the *first time* it runs or if the session expires. It will ask for your phone number, the code sent via Telegram, and your 2FA password (if enabled) **directly in the console where the script is running.**
    *   **It's recommended to run the CLI mode once interactively to complete this authorization before running the bot as a background service:**
        ```bash
        python main.py --cli --chat-id <any_valid_chat_id_or_groupname>
        ```
    *   Follow the prompts in your terminal. Once authorized, Telethon will create a `.session` file (named according to `TELETHON_SESSION_NAME` in your `.env`) and use it for subsequent logins.

## Usage

### Running the Telegram Bot Service

To run the bot so it listens for commands in Telegram:

```bash
python main.py
```

The bot will run in the foreground. For background execution, consider using tools like `systemd`, `supervisor`, or `screen`/`tmux`.

### Bot Commands (Admin Only)

Send these commands to the bot in any chat it's in, or directly to the bot (if processing a specific chat ID):

*   `/process_history`
    *   Processes the history of the chat where the command is issued.
    *   Sends status updates and the resulting ZIP archive back to this chat.
*   `/process_history <chat_id>`
    *   Processes the history for the specified chat ID (numeric) or group name.
    *   The user account associated with your Telethon API keys *must* be a member of the target chat.
    *   Sends status updates and the resulting ZIP archive back to the chat where the command was originally issued.
*   `/listchats`
    *   Lists the chats the bot is aware of being a member of, providing clickable links to run `/process_history` for each.

### Command-Line Interface (CLI)

Run the processing directly from your terminal:

```bash
python main.py --cli --chat-id <chat_id> [--date YYYY-MM-DD]
```

*   `--cli`: Required to run in CLI mode.
*   `--chat-id <chat_id>`: **Required.** Specifies the target chat ID (numeric) or group name (e.g., `-100123456789` or `group_name`).
*   `--date <YYYY-MM-DD>`: Optional. Process history for a specific date instead of yesterday.

**Example:**

```bash
# Process yesterday's history for chat ID -100123456789
python main.py --cli --chat-id -100123456789

# Process history for -100123456789 for 2023-10-26
python main.py --cli --chat-id group_name --date 2023-10-26
```

Output (paths to archives/photos, status messages) will be printed to the console.

## Project Structure

```
telegram-group-processor/
├── .env                  # Stores API keys and bot token (ignored by git)
├── .env.example          # Example environment file
├── config.ini            # Stores non-sensitive configuration
├── known_chats.json      # Stores known chat IDs
├── requirements.txt      # Python dependencies
├── main.py               # Main script: Handles bot setup, CLI parsing, command handlers
├── bot_logic.py          # Core logic: History fetching (Telethon), processing, zipping, downloading
├── downloads/            # Default directory for popular photos (created automatically)
├── archives/             # Default directory for ZIP archives (created automatically)
├── <session_name>.session # Telethon session file (created automatically, ignored by git)
└── README.md             # This file
└── .gitignore            # Specifies intentionally untracked files
```

## Important Notes

*   **User Account Permissions:** The Telegram user account configured via `API_ID`/`API_HASH` **must be a member** of any group chat you intend to process history for.
*   **Security:** Keep your `.env` file and the generated `.session` file secure. Do not commit them to version control.
*   **Rate Limits:** Both the Bot API and the user API (Telethon) are subject to Telegram's rate limits. Processing very active chats or running commands too frequently might result in temporary slowdowns (`FloodWaitError`). The script attempts basic handling but may need adjustments for heavy usage.

## How to get chat id for a group chat

1. Add the bot to the group chat. It will save chat id to the `known_chats.json` file.
2. Open web-version of Telegram. Open the group chat you need. Find the chat id in the URL. It will be something like `-2597620761` or `group_name`. `grpoup_name` could be used as it is. `-2597620761` could be used as it is only if the chat is a groupchat. If the chat is a superchat you need to add `100` after `-` in the chat id. For example `-1002597620761`.

## Troubleshooting

*   **Telethon Login Issues:** If login fails repeatedly, delete the `.session` file and try running interactively via the CLI (`python main.py --cli ...`) again to re-authorize. Ensure you're entering codes/passwords correctly in the console.
*   **Permission Errors (`Forbidden`, `ChatAdminRequiredError`, `UserNotParticipantError`):** This usually means the user account (for Telethon) or the bot (if a Bot API action fails) is not in the target chat or lacks necessary permissions (e.g., read history). Verify membership and group/channel settings.
*   **Configuration Errors:** Double-check that `.env` variables are correctly set and `config.ini` values are valid (e.g., numeric IDs, valid timezone). Check script logs for details.

## License

MIT License
