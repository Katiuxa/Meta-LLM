"""Bot de Telegram y panel web: texto e imágenes por OpenRouter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import threading
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, session, stream_with_context
from flask_cors import CORS
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

def _load_env_file() -> None:
    path = Path(__file__).resolve().with_name(".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

MODELOS: list[dict[str, str]] = [
    {
        "nombre": "Dolphin Venice",
        "id": "cognitivecomputations/dolphin-mistral-24b-venice-edition",
        "descripcion": "Sin censura (Venice, 24B). Charla general directa, con menos rechazos que un modelo comercial.",
    },
    {
        "nombre": "Cydonia 24B",
        "id": "thedrummer/cydonia-24b-v4.1",
        "descripcion": "Sin censura. Escritura creativa: recuerda el hilo y se ciñe al prompt.",
    },
    {
        "nombre": "Skyfall 36B",
        "id": "thedrummer/skyfall-36b-v2",
        "descripcion": "Relatos y roleplay. Prosa con más matices y menos respuestas planas.",
    },
    {
        "nombre": "Euryale 70B",
        "id": "sao10k/l3.1-euryale-70b",
        "descripcion": "Roleplay largo y personajes. Modelo grande: más lento y más caro.",
    },
    {
        "nombre": "Lunaris 8B",
        "id": "sao10k/l3-lunaris-8b",
        "descripcion": "Pequeño y rápido. Charla y rol ligero cuando no hace falta un modelo grande.",
    },
    {
        "nombre": "UnslopNemo 12B",
        "id": "thedrummer/unslopnemo-12b",
        "descripcion": "Aventura y roleplay. Menos frases genéricas que un chat normal.",
    },
    {
        "nombre": "MythoMax 13B",
        "id": "gryphe/mythomax-l2-13b",
        "descripcion": "Clásico de descripciones ricas y roleplay. El más corto de contexto de la lista.",
    },
    {
        "nombre": "Magnum 72B",
        "id": "anthracite-org/magnum-v4-72b",
        "descripcion": "Prosa literaria. Textos largos cuidados, al estilo de un modelo de escritura.",
    },
    {
        "nombre": "Hermes 3 70B",
        "id": "nousresearch/hermes-3-llama-3.1-70b",
        "descripcion": "Razonar, agentes y tareas generales. Menos estricto que Llama oficial.",
    },
    {
        "nombre": "Hermes 3 405B",
        "id": "nousresearch/hermes-3-llama-3.1-405b",
        "descripcion": "El mismo Hermes 3, a 405B. Más capacidad y más coste.",
    },
    {
        "nombre": "Hermes 4 405B",
        "id": "nousresearch/hermes-4-405b",
        "descripcion": "Razonamiento híbrido de Nous sobre Llama 405B. El más pesado de la lista.",
    },
    {
        "nombre": "Qwen3 Coder",
        "id": "qwen/qwen3-coder-30b-a3b-instruct",
        "descripcion": "Código, revisión de lógica y scripts. Este sí lleva filtros.",
    },
    {
        "nombre": "Command R+",
        "id": "cohere/command-r-plus-08-2024",
        "descripcion": "Documentos largos, síntesis y arquitectura. Con filtros.",
    },
]
MODELS: tuple[str, ...] = tuple(item["id"] for item in MODELOS)
_from_env = os.environ.get("MODEL_ID", MODELS[0])
MODEL_ID = _from_env if _from_env in MODELS else MODELS[0]

API_KEY = os.environ.get("API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))
_STATIC = Path(__file__).resolve().parent / "static"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    (
        "Eres un Asistente Técnico de Diagnóstico y Control de Calidad de Software. "
        "Tu función es ayudar en la revisión de código, identificar cuellos de botella "
        "y proponer arquitecturas eficientes para la administración de servidores y bases de datos. "
        "Mantén un enfoque analítico y proporciona los scripts de corrección necesarios. "
        "No des exploits, payloads ni pasos para atacar sistemas."
    ),
)
ALLOWED_TELEGRAM_ID = int(os.environ.get("ALLOWED_TELEGRAM_ID", "0"))

MAX_HISTORY_MESSAGES = 20
TELEGRAM_CHUNK = 4000
HTTP_TIMEOUT = 180
MAX_NEW_TOKENS = 1024
MAX_IMAGE_BYTES = 8_000_000

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot-ia")

_state_lock = threading.Lock()
_model_id = MODEL_ID
_histories: dict[int, list[dict[str, str]]] = defaultdict(list)
_web_history: list[dict[str, str]] = []
_pending_photo: dict[int, dict[str, object]] = {}
_want_image: set[int] = set()
_llm: OpenAI | None = None

MENU_MODEL = "🧠 Cambiar Modelo"
MENU_CLEAR = "🧹 Borrar Historial"
MENU_STATUS = "⚙️ Estado de Servidor"
MENU_IMAGE = "🖼 Imagen"
CB_EDIT = "img:edit"
CB_CANCEL = "img:cancel"


def _openrouter() -> OpenAI:
    global _llm
    if _llm is None:
        _llm = OpenAI(
            api_key=API_KEY,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "http://localhost",
                "X-Title": "BotRaspberry",
            },
        )
    return _llm


def get_model() -> str:
    with _state_lock:
        return _model_id


def set_model(model_id: str) -> str:
    global _model_id
    with _state_lock:
        _model_id = model_id
        return _model_id


def _model_label(model_id: str) -> str:
    for item in MODELOS:
        if item["id"] == model_id:
            return item["nombre"]
    return model_id


def _catalog_public() -> list[dict[str, str]]:
    return [
        {"id": item["id"], "nombre": item["nombre"], "descripcion": item["descripcion"]}
        for item in MODELOS
    ]


def _model_menu_text() -> str:
    current = get_model()
    lines = ["Elige un modelo. El activo lleva ✅.", ""]
    for index, item in enumerate(MODELOS, start=1):
        mark = "✅ " if item["id"] == current else ""
        lines.append(f"{mark}{index}. {item['nombre']}")
        lines.append(item["descripcion"])
        lines.append("")
    lines.append("Las fotos usan Gemini, con la misma clave. Este menú solo cambia el texto.")
    return "\n".join(lines).strip()


def _model_choice() -> InlineKeyboardMarkup:
    current = get_model()
    rows = []
    for index, item in enumerate(MODELOS):
        prefix = "✅ " if item["id"] == current else ""
        rows.append([
            InlineKeyboardButton(f"{prefix}{item['nombre']}", callback_data=f"mdl:{index}")
        ])
    return InlineKeyboardMarkup(rows)


def _keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[MENU_MODEL, MENU_CLEAR], [MENU_STATUS, MENU_IMAGE]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _photo_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✏️ Modificar Imagen", callback_data=CB_EDIT),
            InlineKeyboardButton("🧹 Cancelar", callback_data=CB_CANCEL),
        ]]
    )


def _is_allowed(user_id: int | None) -> bool:
    return user_id is not None and user_id == ALLOWED_TELEGRAM_ID


def _trim(history: list[dict[str, str]]) -> None:
    extra = len(history) - MAX_HISTORY_MESSAGES
    if extra > 0:
        del history[:extra]


def _chunks(text: str, limit: int = TELEGRAM_CHUNK) -> list[str]:
    if len(text) <= limit:
        return [text or " "]
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n ")
    return [part for part in parts if part]


def _ask(history: list[dict[str, str]]) -> str:
    response = _openrouter().chat.completions.create(
        model=get_model(),
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
        max_tokens=MAX_NEW_TOKENS,
        temperature=0.4,
    )
    return (response.choices[0].message.content or "").strip()


def _probe_status() -> int:
    if not API_KEY:
        return 0
    try:
        response = requests.get(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=20,
        )
    except requests.RequestException:
        return 0
    return response.status_code


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(amount: float) -> str:
    decimals = 4 if abs(amount) < 0.01 else 2
    return f"{amount:.{decimals}f} USD".replace(".", ",")


def _openrouter_data(path: str) -> dict | None:
    if not API_KEY:
        return None
    try:
        response = requests.get(
            f"{OPENROUTER_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=20,
        )
    except requests.RequestException:
        log.warning("No pude consultar el gasto de OpenRouter")
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def spend_summary() -> dict[str, float | None]:
    """Gasto en USD. El total de la cuenta tiene prioridad; si no, el de esta clave."""
    credits = _openrouter_data("/credits")
    key = _openrouter_data("/key")
    total = left = None
    if credits is not None:
        used = _as_float(credits.get("total_usage"))
        bought = _as_float(credits.get("total_credits"))
        if used is not None:
            total = used
            if bought is not None:
                left = bought - used
    today = month = None
    if key is not None:
        if total is None:
            total = _as_float(key.get("usage"))
        today = _as_float(key.get("usage_daily"))
        month = _as_float(key.get("usage_monthly"))
    return {"total": total, "today": today, "month": month, "left": left}


def _spend_text() -> str:
    summary = spend_summary()
    total = summary["total"]
    if total is None:
        return "Gasto: ahora mismo OpenRouter no me lo ha dicho."
    lines = [f"Gastado: {_money(total)}"]
    if summary["left"] is not None:
        lines.append(f"Queda: {_money(summary['left'])}")
    if summary["today"] is not None:
        lines.append(f"Hoy: {_money(summary['today'])}")
    if summary["month"] is not None:
        lines.append(f"Este mes: {_money(summary['month'])}")
    return "\n".join(lines)


def _image_from_payload(payload: dict) -> bytes:
    choices = payload.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    images = message.get("images") or []
    if not images or not isinstance(images[0], dict):
        raise RuntimeError("OpenRouter no devolvió una imagen")
    image_url = images[0].get("image_url") or {}
    url = str(image_url.get("url") or "")
    if "," not in url:
        raise RuntimeError("OpenRouter no devolvió una imagen")
    return base64.b64decode(url.split(",", 1)[1])


def _openrouter_image(messages: list) -> bytes:
    response = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "BotRaspberry",
        },
        json={
            "model": IMAGE_MODEL,
            "messages": messages,
            "modalities": ["image", "text"],
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        log.error("Imagen HTTP %s: %s", response.status_code, response.text[:300])
        response.raise_for_status()
    return _image_from_payload(response.json())


def generate_image(prompt: str) -> bytes:
    return _openrouter_image([{"role": "user", "content": prompt}])


def edit_image(image: bytes, prompt: str, content_type: str = "image/jpeg") -> bytes:
    """Edita la foto con Gemini a través de OpenRouter."""
    mime = content_type if content_type.startswith("image/") else "image/jpeg"
    encoded = base64.b64encode(image).decode("ascii")
    return _openrouter_image([{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "Edita esta foto. Aplica solo este cambio y conserva a la persona, "
                    f"la pose y el fondo: {prompt}"
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ],
    }])


async def _reply(update: Update, text: str, *, with_keyboard: bool = False) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _chunks(text)
    for index, part in enumerate(parts):
        markup = _keyboard() if with_keyboard and index == len(parts) - 1 else None
        await message.reply_text(part, reply_markup=markup)


async def _keep_typing(chat, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await chat.send_action(ChatAction.TYPING)
        except Exception:
            log.debug("No se pudo enviar el estado escribiendo", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except (TimeoutError, asyncio.TimeoutError):
            continue


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id if user else None):
        return
    await _reply(
        update,
        "Listo. Escríbeme, pulsa Imagen para crear una foto o mándame una para editarla. "
        "/reset borra el historial.",
        with_keyboard=True,
    )


async def on_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        return
    _histories.pop(user.id, None)
    _pending_photo.pop(user.id, None)
    _want_image.discard(user.id)
    await _reply(update, "Historial borrado.", with_keyboard=True)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id) or not message.photo:
        return
    photo = message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    data = bytes(await tg_file.download_as_bytearray())
    if len(data) > MAX_IMAGE_BYTES:
        await _reply(update, "La imagen es demasiado grande.", with_keyboard=True)
        return
    _want_image.discard(user.id)
    _pending_photo[user.id] = {"image": data, "stage": "choose"}
    await message.reply_text("¿Qué hago con esta imagen?", reply_markup=_photo_choice())


async def on_photo_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not _is_allowed(user.id):
        return
    await query.answer()
    pending = _pending_photo.get(user.id)
    if query.data == CB_CANCEL or not pending:
        _pending_photo.pop(user.id, None)
        if query.message is not None:
            await query.message.reply_text("Cancelado.", reply_markup=_keyboard())
        return
    pending["stage"] = "describe"
    if query.message is not None:
        await query.message.reply_text(
            "Describe los cambios que quieres hacer "
            "(ej: 'cambia la camiseta por una chaqueta de cuero negra').",
            reply_markup=_keyboard(),
        )


async def on_model_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not _is_allowed(user.id):
        return
    await query.answer()
    try:
        index = int((query.data or "").split(":", 1)[1])
        item = MODELOS[index]
    except (ValueError, IndexError):
        return
    set_model(item["id"])
    if query.message is not None:
        await query.message.reply_text(
            f"Modelo activo: {item['nombre']}\n{item['descripcion']}",
            reply_markup=_keyboard(),
        )


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id):
        return
    choice = (message.text or "").strip()
    _want_image.discard(user.id)
    if choice == MENU_CLEAR:
        _histories.pop(user.id, None)
        _pending_photo.pop(user.id, None)
        await _reply(update, "Historial borrado.", with_keyboard=True)
        return
    if choice == MENU_MODEL:
        spend = await asyncio.to_thread(_spend_text)
        await message.reply_text(
            f"{_model_menu_text()}\n\n{spend}",
            reply_markup=_model_choice(),
        )
        return
    if choice == MENU_IMAGE:
        _pending_photo.pop(user.id, None)
        _want_image.add(user.id)
        await _reply(
            update,
            "Escribe qué imagen quieres crear. "
            "Si prefieres modificar una foto, mándamela. "
            "Cada imagen usa tu clave de OpenRouter.",
            with_keyboard=True,
        )
        return
    if choice == MENU_STATUS:
        code, spend = await asyncio.gather(
            asyncio.to_thread(_probe_status),
            asyncio.to_thread(_spend_text),
        )
        if code == 200:
            head = f"OpenRouter OK (HTTP 200).\n{_model_label(get_model())}"
        elif code == 0:
            head = "Sin conexión con OpenRouter."
        else:
            head = f"OpenRouter respondió HTTP {code}."
        await _reply(update, f"{head}\n{spend}", with_keyboard=True)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not _is_allowed(user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return

    pending = _pending_photo.get(user.id)
    if pending and pending.get("stage") == "describe":
        image = pending.get("image")
        _pending_photo.pop(user.id, None)
        if not isinstance(image, (bytes, bytearray)):
            await _reply(update, "No tengo la imagen. Vuelve a enviarla.", with_keyboard=True)
            return
        await message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        try:
            edited = await asyncio.to_thread(edit_image, bytes(image), text)
        except Exception:
            log.exception("Fallo al editar imagen")
            await _reply(update, "No pude editar la imagen.", with_keyboard=True)
            return
        await message.reply_photo(photo=edited, reply_markup=_keyboard())
        return

    if user.id in _want_image:
        await message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        try:
            picture = await asyncio.to_thread(generate_image, text)
        except Exception:
            log.exception("Fallo al crear imagen")
            await _reply(
                update,
                "No pude crear la imagen. Prueba otra descripción o pulsa otro botón para volver al chat.",
                with_keyboard=True,
            )
            return
        _want_image.discard(user.id)
        await message.reply_photo(photo=picture, reply_markup=_keyboard())
        return

    history = _histories[user.id]
    history.append({"role": "user", "content": text})
    _trim(history)
    snapshot = list(history)
    stop = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(message.chat, stop))
    try:
        reply = await asyncio.to_thread(_ask, snapshot)
    except Exception:
        log.exception("Fallo al llamar al modelo (user_id=%s)", user.id)
        if history and history[-1].get("role") == "user":
            history.pop()
        await _reply(update, "No pude obtener respuesta del modelo. Inténtalo de nuevo.")
        return
    finally:
        stop.set()
        await typing_task
    if not reply:
        reply = "(El modelo devolvió una respuesta vacía.)"
    history.append({"role": "assistant", "content": reply})
    _trim(history)
    await _reply(update, reply)


def _web_token() -> str:
    secret = str(web.secret_key).encode()
    return hmac.new(secret, WEB_PASSWORD.encode(), hashlib.sha256).hexdigest()


def _web_authed() -> bool:
    if session.get("ok") is True:
        return True
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    given = header[7:].strip()
    if not given:
        return False
    return hmac.compare_digest(given, _web_token())


web = Flask(__name__)
web.secret_key = os.environ.get("WEB_SECRET", "bot-ia-local")
CORS(
    web,
    resources={r"/api/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"],
)


@web.get("/")
def web_index():
    return send_from_directory(_STATIC, "index.html")


@web.get("/style.css")
def web_css():
    return send_from_directory(_STATIC, "style.css")


@web.get("/script.js")
def web_js():
    return send_from_directory(_STATIC, "script.js")


@web.get("/api/session")
def web_session():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    return jsonify({"ok": True, "model": get_model(), "models": _catalog_public()})


@web.get("/api/status")
def web_status():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    code = _probe_status()
    return jsonify({
        "ok": code == 200,
        "code": code,
        "model": get_model(),
        "gasto": _spend_text(),
    })


@web.post("/api/login")
def web_login():
    data = request.get_json(silent=True) or {}
    if str(data.get("password") or "") != WEB_PASSWORD:
        return jsonify({"ok": False}), 401
    session["ok"] = True
    return jsonify({
        "ok": True,
        "token": _web_token(),
        "model": get_model(),
        "models": _catalog_public(),
    })


@web.post("/api/model")
def web_model():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    chosen = str(data.get("model") or "")
    if chosen not in MODELS:
        return jsonify({"ok": False, "error": "modelo no permitido"}), 400
    set_model(chosen)
    return jsonify({"ok": True, "model": get_model()})


@web.post("/api/reset")
def web_reset():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    with _state_lock:
        _web_history.clear()
    return jsonify({"ok": True})


@web.post("/api/chat")
def web_chat():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "mensaje vacío"}), 400
    with _state_lock:
        _web_history.append({"role": "user", "content": text})
        _trim(_web_history)
        snapshot = list(_web_history)
    try:
        reply = _ask(snapshot)
    except Exception:
        log.exception("Fallo al llamar al modelo desde la web")
        with _state_lock:
            if _web_history and _web_history[-1].get("role") == "user":
                _web_history.pop()
        return jsonify({"ok": False, "error": "No pude obtener respuesta del modelo."}), 502
    if not reply:
        reply = "(El modelo devolvió una respuesta vacía.)"
    with _state_lock:
        _web_history.append({"role": "assistant", "content": reply})
        _trim(_web_history)
    return jsonify({"ok": True, "reply": reply, "model": get_model()})


@web.post("/api/edit")
def web_edit():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    upload = request.files.get("image")
    prompt = str(request.form.get("prompt") or "").strip()
    if upload is None or not prompt:
        return jsonify({"ok": False, "error": "faltan la imagen o la descripción"}), 400
    image = upload.read(MAX_IMAGE_BYTES + 1)
    if len(image) > MAX_IMAGE_BYTES:
        return jsonify({"ok": False, "error": "imagen demasiado grande"}), 400
    content_type = upload.mimetype or "image/jpeg"
    try:
        edited = edit_image(image, prompt, content_type)
    except Exception:
        log.exception("Fallo al editar imagen desde la web")
        return jsonify({"ok": False, "error": "No pude editar la imagen."}), 502
    encoded = base64.b64encode(edited).decode("ascii")
    mime = "image/png" if edited.startswith(b"\x89PNG") else "image/jpeg"
    return jsonify({"ok": True, "image": f"data:{mime};base64,{encoded}"})


_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
_audit_lock = threading.Lock()
_audit_bundle: dict[str, object] = {"url": "", "source": "", "turns": []}


def _redact(text: str) -> str:
    patterns = (
        r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b",
        r"\bsk-(?:or-)?[A-Za-z0-9_-]{20,}\b",
        r"\bhf_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, "[secreto omitido]", text)
    return text


def _looks_blocked(status: int, text: str) -> bool:
    low = text.lower()
    markers = (
        "just a moment",
        "cf-browser-verification",
        "cf-challenge",
        "attention required",
        "checking your browser",
        "enable javascript and cookies",
    )
    if status in (401, 403, 429, 503) and any(marker in low for marker in markers):
        return True
    return any(marker in low for marker in markers) and "<html" in low and len(text) < 8000


def _decode_body(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace").replace("\x00", "")
    try:
        return content.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        pass
    match = re.search(br"charset=[\"']?([A-Za-z0-9._-]+)", content[:4000], re.I)
    encoding = match.group(1).decode("ascii", "ignore") if match else "cp1252"
    try:
        text = content.decode(encoding, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    text = text.replace("\x00", "")
    if text.count("\ufffd") > max(30, len(text) // 40):
        raise RuntimeError("ilegible")
    return text


def _download(url: str) -> tuple[int, str]:
    response = requests.get(url, headers=_PAGE_HEADERS, timeout=25, allow_redirects=True)
    return response.status_code, _decode_body(response.content or b"")


def _reply_useless(text: str) -> bool:
    visible = text.replace("\ufffd", "").strip()
    letters = sum(1 for ch in visible if ch.isalpha())
    return letters < 12 or (text.count("\ufffd") > 0 and letters < 40)


def _script_urls(page_url: str, html: str) -> list[str]:
    host = urlparse(page_url).netloc
    found: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.I):
        absolute = urljoin(page_url, src)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != host or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= 4:
            break
    return found


def _fetch_page(url: str, progress) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("url")
    progress(12, "Conectando con la página…")
    status, html = _download(url)
    direct_ok = status == 200 and bool(html.strip()) and not _looks_blocked(status, html)
    parts: list[str] = []
    if direct_ok:
        progress(32, f"Página descargada ({len(html)} caracteres).")
        parts.append(f"<!-- pagina {url} -->\n{html[:28000]}")
        scripts = _script_urls(url, html)
        if scripts:
            progress(40, f"Hay {len(scripts)} script(s) del mismo sitio.")
        else:
            progress(48, "La página no enlaza scripts propios. Sigo con el HTML.")
        for index, script_url in enumerate(scripts, start=1):
            progress(42 + index * 6, f"Descargando script {index} de {len(scripts)}…")
            try:
                script_status, body = _download(script_url)
            except requests.RequestException:
                progress(42 + index * 6, f"Script {index} no respondió. Sigo con el resto.")
                continue
            if script_status != 200 or _looks_blocked(script_status, body):
                continue
            parts.append(f"\n<!-- script {script_url} -->\n{body[:12000]}")
        progress(70, "Código reunido. Ahora lo lee el modelo.")
    else:
        progress(24, "La descarga directa no vale. Probando otra vía…")
        mirrored = requests.get(
            "https://r.jina.ai/" + url,
            headers={"X-Return-Format": "markdown", "X-Retain-Images": "none"},
            timeout=40,
        )
        mirrored.encoding = "utf-8"
        mirror = mirrored.text.encode("utf-8", "ignore").decode("utf-8").strip()
        if mirrored.status_code != 200 or not mirror or _looks_blocked(mirrored.status_code, mirror):
            raise RuntimeError("bloqueada")
        progress(58, f"Copia de la página recibida ({len(mirror)} caracteres).")
        parts.append(mirror[:28000])
    source = (
        _redact("\n".join(parts))
        .encode("utf-8", "ignore")
        .decode("utf-8")
        .replace("\ufffd", "")
        .strip()
    )
    if len(source) < 40:
        raise RuntimeError("vacio")
    return source[:60000], len(source)


def _audit_event(kind: str, **payload) -> str:
    body = {"type": kind, **payload}
    return json.dumps(body, ensure_ascii=False) + "\n"


def _audit_stream(url: str, instruccion: str, refetch: bool):
    label = _model_label(get_model())
    events: queue.Queue = queue.Queue()

    def progress(pct: int, step: str) -> None:
        events.put(("progress", pct, step))

    outcome: dict[str, object] = {}

    def work() -> None:
        try:
            with _audit_lock:
                cached = _audit_bundle["url"] == url and bool(_audit_bundle["source"]) and not refetch
                cached_source = str(_audit_bundle["source"] or "")
            if cached:
                progress(70, "Usando el código ya descargado. No vuelve a bajar la página.")
                source = cached_source
                nbytes = len(source)
            else:
                if not url:
                    outcome["error"] = "Falta la URL."
                    outcome["code"] = 400
                    events.put(("end",))
                    return
                source, nbytes = _fetch_page(url, progress)
                with _audit_lock:
                    _audit_bundle["url"] = url
                    _audit_bundle["source"] = source
                    _audit_bundle["turns"] = []
            with _audit_lock:
                prior = list(_audit_bundle["turns"])  # type: ignore[arg-type]
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": (
                        "Eres un analizador estático. Solo puedes usar el código fuente que viene en el mensaje. "
                        "NO des guías, NO expliques herramientas externas y NO digas qué debería hacer el usuario. "
                        "Si un dato no está escrito en ese código, responde exactamente «No encontrado». "
                        "Puedes citar IDs de analítica, comentarios, enlaces y rutas que aparezcan literalmente, "
                        "y enlaces públicos de Telegram (t.me). "
                        "No copies secretos, contraseñas ni tokens de API: si aparece uno, escribe solo «secreto presente». "
                        "No des exploits, payloads ni pasos para atacar sistemas."
                    ),
                },
                {"role": "user", "content": "--- CODIGO FUENTE ---\n" + source},
                {"role": "assistant", "content": "Código recibido. Pregunta sobre ese texto."},
            ]
            for turn in prior[-6:]:
                if isinstance(turn, dict):
                    messages.append({
                        "role": str(turn.get("role") or "user"),
                        "content": str(turn.get("content") or ""),
                    })
            messages.append({"role": "user", "content": instruccion})
            progress(78, f"{label} está leyendo el código. Puede tardar. Sigue en marcha.")
            response = None
            failure = None
            for attempt in (1, 2):
                try:
                    if attempt == 2:
                        progress(84, f"{label} no ha respondido. Segundo intento…")
                    response = _openrouter().chat.completions.create(
                        model=get_model(),
                        messages=messages,
                        max_tokens=4000,
                        temperature=0.1,
                    )
                    failure = None
                    break
                except Exception as exc:
                    failure = exc
                    status = getattr(exc, "status_code", 0) or 0
                    log.warning("Auditoría rechazada por el modelo (HTTP %s, intento %s)", status, attempt)
                    if status not in (0, 400, 408, 409, 429, 500, 502, 503) or attempt == 2:
                        break
            if failure is not None or response is None:
                status = getattr(failure, "status_code", 0) or 0
                if status == 402:
                    outcome["error"] = "OpenRouter no tiene crédito para esta consulta."
                elif status == 429:
                    outcome["error"] = f"{label} está saturado. Espera un momento y pulsa otra vez."
                elif status in (400, 404):
                    outcome["error"] = (
                        f"{label} ha rechazado esta página. "
                        "Pulsa otra vez o cambia a Dolphin Venice o Qwen3 Coder."
                    )
                else:
                    outcome["error"] = f"No pude obtener respuesta de {label}. Pulsa otra vez."
                events.put(("end",))
                return
            reply = _redact((response.choices[0].message.content or "").strip()).replace("\ufffd", "")
            if _reply_useless(reply):
                progress(90, f"{label} ha devuelto basura. Repito la lectura con Dolphin Venice.")
                fallback = _openrouter().chat.completions.create(
                    model="cognitivecomputations/dolphin-mistral-24b-venice-edition",
                    messages=messages,
                    max_tokens=4000,
                    temperature=0.2,
                )
                reply = _redact((fallback.choices[0].message.content or "").strip()).replace("\ufffd", "")
                if _reply_useless(reply):
                    outcome["error"] = (
                        f"{label} no ha leído el código (respuesta ilegible). "
                        "Cambia a Qwen3 Coder y pulsa otra vez."
                    )
                    events.put(("end",))
                    return
                reply = "Leído con Dolphin Venice, porque " + label + " no devolvió texto útil.\n\n" + reply
            reply = reply or "No encontrado"
            with _audit_lock:
                turns = _audit_bundle["turns"]
                if isinstance(turns, list):
                    turns.append({"role": "user", "content": instruccion})
                    turns.append({"role": "assistant", "content": reply})
                    del turns[:-8]
            outcome["ok"] = True
            outcome["result"] = reply
            outcome["bytes"] = nbytes
            outcome["url"] = url
        except requests.RequestException:
            log.warning("Descarga de auditoría fallida")
            outcome["error"] = "Error 500: Objetivo inaccesible"
        except RuntimeError as exc:
            if str(exc) == "bloqueada":
                outcome["error"] = "La web ha bloqueado la descarga automática. No hay código real que analizar."
            elif str(exc) == "ilegible":
                outcome["error"] = "La descarga no es texto legible. No hay código que analizar."
            else:
                outcome["error"] = "Error 500: Objetivo inaccesible"
        events.put(("end",))

    threading.Thread(target=work, name="audit", daemon=True).start()
    last_pct = 5
    last_step = "Empieza la lectura…"
    seconds = 0
    yield _audit_event("progress", pct=last_pct, step=last_step)
    while True:
        try:
            item = events.get(timeout=2)
        except queue.Empty:
            seconds += 2
            last_pct = min(96, last_pct + 1)
            yield _audit_event(
                "progress",
                pct=last_pct,
                step=f"{last_step} Sigue trabajando ({seconds} s).",
            )
            continue
        if item[0] == "end":
            break
        _, last_pct, last_step = item
        seconds = 0
        yield _audit_event("progress", pct=int(last_pct), step=str(last_step))
    if outcome.get("ok"):
        yield _audit_event(
            "done",
            ok=True,
            result=outcome.get("result"),
            bytes=outcome.get("bytes"),
            url=outcome.get("url"),
        )
        return
    yield _audit_event("done", ok=False, error=str(outcome.get("error") or "Error 500: Objetivo inaccesible"))


@web.post("/api/audit_url")
def web_audit():
    if not _web_authed():
        return jsonify({"ok": False}), 401
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    instruccion = str(data.get("instruccion") or "").strip()
    refetch = bool(data.get("refetch", True))
    if not instruccion:
        return jsonify({"ok": False, "error": "Falta la instrucción."}), 400
    return Response(
        stream_with_context(_audit_stream(url, instruccion, refetch)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_web() -> None:
    web.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False)


def main() -> None:
    missing = [
        name
        for name, value in (
            ("API_KEY", API_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("WEB_PASSWORD", WEB_PASSWORD),
        )
        if not str(value).strip()
    ]
    if missing:
        raise SystemExit("Rellena estas variables en el .env: " + ", ".join(missing))
    if ALLOWED_TELEGRAM_ID <= 0:
        raise SystemExit("Define ALLOWED_TELEGRAM_ID con tu ID numérico de Telegram.")

    threading.Thread(target=_run_web, name="web", daemon=True).start()
    log.info("Panel web en http://0.0.0.0:%s", WEB_PORT)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(False)
        .build()
    )
    menu_filter = filters.Regex(
        "^(" + "|".join(re.escape(label) for label in (MENU_MODEL, MENU_CLEAR, MENU_STATUS, MENU_IMAGE)) + ")$"
    )
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("reset", on_reset))
    app.add_handler(CallbackQueryHandler(on_model_choice, pattern=r"^mdl:"))
    app.add_handler(CallbackQueryHandler(on_photo_choice, pattern=r"^img:"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(menu_filter, on_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot en marcha. modelo=%s allowed_id=%s", get_model(), ALLOWED_TELEGRAM_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
