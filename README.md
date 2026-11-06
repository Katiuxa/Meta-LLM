# Meta LLM

Bot de Telegram y panel web para hablar con varios modelos a través de OpenRouter. Corre en la Raspberry Pi 3B, la misma del home lab. Con 1 GB de RAM no hay modelo local: la Pi solo lleva el bot, el historial corto y el panel. El cálculo está en OpenRouter.

El menú del chat cambia el modelo sin reiniciar. La lista mezcla dos tipos:

- Pocos rechazos, para charla, relatos y roleplay: Dolphin Venice 24B, Cydonia 24B, Skyfall 36B, Euryale 70B, Lunaris 8B, UnslopNemo 12B, MythoMax 13B y Hermes 3 / 4.
- Con filtros, para código y documentos: Qwen3 Coder y Command R+.

Las imágenes (generar o editar una foto) salen por Gemini Flash Image, también en OpenRouter. El panel, en el puerto 5000 de la red local, repite el chat y además puede bajar el HTML de una página pública y preguntarle al modelo activo por ese código.

La clave de OpenRouter, el token del bot y el id de Telegram no están en el repositorio. Van en `.env`.

## Arranque

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

`API_KEY` y `OPENROUTER_API_KEY` llevan la misma clave. `ALLOWED_TELEGRAM_ID` es el id numérico de tu usuario: el bot ignora al resto. El panel pide `WEB_PASSWORD`.

En local el panel queda en `http://127.0.0.1:5000`.

## En la Pi

`bot-ia.service` lo deja con systemd, tope de 150 MB, y lee el `.env` de `/home/admin/bots/bot_ia`. El script `deploy.sh` copia el código, instala el venv y arranca el servicio. Pide la IP, la contraseña de la Pi y las claves; no las guarda en el script.
