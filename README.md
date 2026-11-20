# Meta LLM

Telegram bot and web panel for talking to several models through OpenRouter. It runs on the Raspberry Pi 3B, the same machine as the home lab. With 1 GB of RAM there is no local model: the Pi only runs the bot, a short history, and the panel. The compute stays on OpenRouter.

The chat menu switches the model without a restart. The list mixes two kinds:

- Few refusals, for chat, stories, and roleplay: Dolphin Venice 24B, Cydonia 24B, Skyfall 36B, Euryale 70B, Lunaris 8B, UnslopNemo 12B, MythoMax 13B, and Hermes 3 / 4.
- Moderated, for code and long documents: Qwen3 Coder and Command R+.

Images (generate or edit a photo) go through Gemini Flash Image, also on OpenRouter. The panel, on port 5000 of the local network, repeats the chat and can also download the HTML of a public page and ask the active model about that code.

The OpenRouter key, the bot token, and the Telegram id are not in the repository. They belong in `.env`.

## Start

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
venv\Scripts\python bot.py
```

Linux:

```bash
venv/bin/pip install -r requirements.txt
cp .env.example .env
venv/bin/python bot.py
```

`API_KEY` and `OPENROUTER_API_KEY` hold the same key. `ALLOWED_TELEGRAM_ID` is your numeric user id: the bot ignores everyone else. The panel asks for `WEB_PASSWORD`.

Locally the panel is at `http://127.0.0.1:5000`.

## On the Pi

`bot-ia.service` keeps it under systemd, with a 150 MB cap, and reads the `.env` from `/home/admin/bots/bot_ia`. `deploy.sh` copies the code, installs the venv, and starts the service. It asks for the IP, the Pi password, and the keys. It does not store them in the script.
