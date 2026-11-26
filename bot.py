import os
import json
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Модель задаётся переменной окружения GEMINI_MODEL — менять без правки кода.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash")

session = AiohttpSession(timeout=180)
bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ---------- ШАГ 1. Строгая оценка по Margo Gate (внутренняя, JSON) ----------
AUDIT_INSTRUCTION = """Ты строгий аудитор рекламных роликов бренда MIXIT (Казахстан, продажи на Kaspi).
Досмотри ролик ЦЕЛИКОМ, до последней секунды, со звуком. Оценивай только то, что реально видно и слышно,
каждое замечание — с таймкодом. Не додумывай и не ставь баллы «из вежливости».

=== СИСТЕМА MARGO GATE ===

ФОРМУЛА: gate_score = К1×3 + К2×2 + К3×1 (максимум 18)
РЕШЕНИЕ: 15–18 → ПУБЛИКУЕМ | 11–14 → ДОРАБОТКА | ≤10 → ПЕРЕСЪЁМКА
Если К1=0 — ПЕРЕСЪЁМКА при любых баллах.

--- К1 «Единая мысль и УТП» (0–3, вес 3) ---
3 — один товар, один угол, одна эмоция; УТП ясно за 10 секунд; нет прыжков между продуктами/проблемами
2 — мысль есть, но угол размыт или УТП проговорено вскользь
1 — несколько идей конкурируют, зритель теряется
0 — непонятно что рекламируется и зачем

--- К2 «Структура и логика» (0–3, вес 2) ---
3 — чёткая цепочка: хук (0–3 с) → проблема/желание → товар как решение (название на экране или голосом) →
    доказательство на себе (до/после при одном свете и ракурсе; ощущение «не липнет»; ингредиент;
    сравнение по одному критерию; почти пустая банка; реакция других) → призыв купить
    «Супер» и «просто держит баночку» — НЕ доказательство
2 — структура угадывается, но один блок слабый или переставлен
1 — структура сломана: нет доказательства или нет CTA, или хук не работает
0 — хаос, нет ни одного работающего блока

--- К3 «Сила хука и угол» (0–3, вес 1) ---
3 — в первые 3 секунды ОДНОВРЕМЕННО: картинка-захват (крупный план результата, до/после, макро текстура,
    резкий переход, эмоция лица) + фраза голосом или текстом; понятно без звука; один сильный угол:
    боль / желаемый результат / честный отзыв / сравнение / разрушение стереотипа / социальное доказательство
2 — один из двух элементов есть, угол нечёткий
1 — хук формальный, не цепляет
0 — хука нет или запрещённый зачин (Девочки / Қыздар / Всем привет / Сәлеметсіздер ме / Меня зовут…)

=== ФОРМАЛЬНЫЕ ПРОВЕРКИ ===
Каждая — да/нет с таймкодом и конкретным доказательством из ролика:
F1 один товар MIXIT — главный, не фон, не сторитайм без товара
F2 призыв купить в конце: голосом или текстом («переходите по ссылке», «артикул в описании» и т.п.)
F3 живой голос / своя озвучка (НЕ музыка без речи, НЕ липсинк под чужой звук)
F4 нет косметики других брендов в кадре и по названию
F5 нет медобещаний (лечит / убирает пигментацию / поры чище / омолаживает / от выпадения)
F6 нет распаковки как основы ролика
F7 нет запрещённых зачинов: «Девочки», «Қыздар», «Всем привет», «Сәлеметсіздер ме», «Меня зовут…»
F8 нет слов «дешёвый», «бюджетный», «недорогой», «арзан»
F9 бренд назван голосом (МИ́ксит, ударение на первый слог)
F10 хороший свет на лице, субтитры есть
F11 есть социальное доказательство (Kaspi-отзыв или аналог)
F12 дети не трогают и не наносят косметику

Если F1, F3, F4, F5, F6, F7, F8, F12 нарушены — решение ПЕРЕСЪЁМКА независимо от баллов.
Если нарушен только F2 — решение по баллам (не ПЕРЕСЪЁМКА автоматически).

Динамика: перечисли пустые секунды (молчание, движения без смысла, повтор).
Показ товара с рассказом — НЕ затянутость.
Если ролик не про косметику MIXIT — напиши в verdict_reason.

Произношение: проверь, говорит ли блогер «МИ́ксит» (ударение на И).
Если говорит «микси́т» или как-то иначе — укажи таймкод и что именно сказано.

Отвечай строго JSON по схеме."""

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "description": "какой товар MIXIT в ролике"},
        "idea": {"type": "string", "description": "главная мысль ролика одной фразой"},
        "language": {"type": "string", "description": "язык речи: ru / kk / смешанный / без речи"},
        "hook_text": {"type": "string", "description": "что сказано/написано в первые 3 с (дословно)"},
        "k1": {"type": "integer"}, "k1_why": {"type": "string"},
        "k2": {"type": "integer"}, "k2_why": {"type": "string"},
        "k3": {"type": "integer"}, "k3_why": {"type": "string"},
        "formal": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "evidence": {"type": "string"}
                },
                "required": ["check", "ok", "evidence"]
            }
        },
        "empty_seconds": {"type": "array", "items": {"type": "string"}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "fixes": {
            "type": "array",
            "description": "3–4 главные правки по убыванию важности",
            "items": {
                "type": "object",
                "properties": {
                    "timecode": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "how": {"type": "string"}
                },
                "required": ["what", "why", "how"]
            }
        },
        "verdict": {"type": "string", "enum": ["ПУБЛИКУЕМ", "ДОРАБОТКА", "ПЕРЕСЪЁМКА"]},
        "verdict_reason": {"type": "string"},
        "brand_mention": {"type": "string", "description": "назван / НЕ назван"},
        "pronunciation": {"type": "string", "description": "МИ́ксит ок / таймкод + что сказано вместо МИ́ксит"},
    },
    "required": ["product", "idea", "language", "hook_text",
                 "k1", "k1_why", "k2", "k2_why", "k3", "k3_why",
                 "formal", "fixes", "strengths", "verdict", "verdict_reason",
                 "brand_mention", "pronunciation"],
}

# ---------- ШАГ 2. Сообщение блогеру (тёплое, по итогам оценки) ----------
MESSAGE_INSTRUCTION = """Ты пишешь блогеру от имени команды MIXIT по готовой строгой оценке ролика (JSON ниже).
Ничего не добавляй от себя сверх оценки и не смягчай суть правок — только тон.
Критику пиши прямо и конкретно: не «можно было лучше», а «хука нет — первые 3 секунды молчание».
Формат (на «вы», тепло, без жаргона, без слова «проблема»):
- 1 искренний комплимент за то, что реально хорошо (из strengths);
- «Вот что стоит доработать:»;
- 3–4 правки из fixes: таймкод → что в кадре → почему это мешает продажам → как сделать конкретно;
- если есть формальные нарушения (ok=false) — они идут первыми;
- финальная фраза: ждём новую версию (или «можно публиковать», если verdict = ПУБЛИКУЕМ).
10–12 строк максимум. Если язык ролика казахский — пиши на казахском, иначе на русском.
Пиши ТОЛЬКО сообщение блогеру."""


async def call_gemini(model: str, contents, config):
    def run():
        return gemini_client.models.generate_content(model=model, contents=contents, config=config)
    return await asyncio.to_thread(run)


async def generate_with_retry(contents, config, status_msg):
    """5 попыток на основной модели при 429/500/503, затем запасная модель."""
    last = None
    for model in (MODEL, FALLBACK_MODEL):
        for attempt in range(5 if model == MODEL else 2):
            try:
                return await call_gemini(model, contents, config), model
            except Exception as e:
                last = e
                s = str(e)
                logging.error(f"{model}, попытка {attempt + 1}: {s[:300]}")
                if any(code in s for code in ("429", "500", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL")):
                    wait = (attempt + 1) * 10
                    await status_msg.edit_text(f"Gemini перегружен, повторяю через {wait} сек…")
                    await asyncio.sleep(wait)
                    continue
                break
    raise Exception(f"Gemini не ответил: {str(last)[:300]}")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я AI-аудитор Reels и Shorts для MIXIT.\n\n"
        "Пришли мне видео или кружок — дам разбор."
    )


async def analyze_video(message: Message):
    status_msg = await message.answer("Видео получено! Скачиваю файл…")
    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"/tmp/temp_{file_id}.mp4"
    uploaded_gemini_file = None

    try:
        file_size = getattr(video_obj, "file_size", None)
        if file_size and file_size > 20 * 1024 * 1024:
            await status_msg.edit_text(
                "Видео слишком большое (больше 20 МБ).\n"
                "Сожмите или обрежьте до 60 секунд и пришлите снова."
            )
            return

        file_info = await bot.get_file(file_id, request_timeout=120)
        await bot.download_file(file_info.file_path, destination=local_file_path)
        await status_msg.edit_text("Передаю видео в Gemini…")

        uploaded_gemini_file = await asyncio.to_thread(lambda: gemini_client.files.upload(file=local_file_path))

        await status_msg.edit_text("Обрабатываю видео (может занять до 2 минут)…")
        waited = 0
        while uploaded_gemini_file.state.name == "PROCESSING" and waited < 180:
            await asyncio.sleep(3)
            waited += 3
            name = uploaded_gemini_file.name
            uploaded_gemini_file = await asyncio.to_thread(lambda: gemini_client.files.get(name=name))

        if uploaded_gemini_file.state.name != "ACTIVE":
            await status_msg.edit_text("Gemini не смог обработать видео.\nПопробуйте другой файл или уменьшите его размер.")
            return

        # ШАГ 1: строгая оценка по Margo Gate
        await status_msg.edit_text("Смотрю ролик целиком и оцениваю…")
        video_part = types.Part.from_uri(
            file_uri=uploaded_gemini_file.uri,
            mime_type=uploaded_gemini_file.mime_type or "video/mp4"
        )
        audit_cfg = types.GenerateContentConfig(
            system_instruction=AUDIT_INSTRUCTION,
            temperature=0,
            response_mime_type="application/json",
            response_schema=AUDIT_SCHEMA,
            max_output_tokens=8192,
        )
        resp, used_model = await generate_with_retry(
            [video_part, "Оцени этот ролик по правилам Margo Gate."],
            audit_cfg,
            status_msg
        )
        audit = json.loads(resp.text)
        score = audit["k1"] * 3 + audit["k2"] * 2 + audit["k3"]
        audit["score"] = score
        logging.info(f"AUDIT model={used_model} score={score} verdict={audit['verdict']} product={audit.get('product')}")

        # ШАГ 2: тёплое сообщение блогеру по оценке
        await status_msg.edit_text("Формирую разбор…")
        msg_cfg = types.GenerateContentConfig(
            system_instruction=MESSAGE_INSTRUCTION,
            temperature=0.3,
            max_output_tokens=2048
        )
        resp2, _ = await generate_with_retry(
            [json.dumps(audit, ensure_ascii=False)],
            msg_cfg,
            status_msg
        )

        await status_msg.delete()

        # Строка для менеджера
        k_line = f"К1={audit['k1']}/3 · К2={audit['k2']}/3 · К3={audit['k3']}/3"
        manager_line = (
            f"--- ОЦЕНКА ДЛЯ МЕНЕДЖЕРА ---\n"
            f"идея: {audit.get('idea', '—')}\n"
            f"---\n"
            f"{k_line}\n"
            f"gate_score: {score}/18\n"
            f"РЕШЕНИЕ: {audit['verdict']}\n"
            f"минусы: {audit.get('verdict_reason', '—')}\n"
            f"бренд голосом: {audit.get('brand_mention', '—')}\n"
            f"произношение: {audit.get('pronunciation', '—')}"
        )
        await message.answer(manager_line)

        # Сообщение блогеру
        text = resp2.text
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i + 4000])

    except Exception as e:
        logging.error(f"Ошибка: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"Что-то пошло не так:\n{str(e)[:500]}")
        except Exception:
            pass
    finally:
        if uploaded_gemini_file:
            try:
                name = uploaded_gemini_file.name
                await asyncio.to_thread(lambda: gemini_client.files.delete(name=name))
            except Exception:
                pass
        if os.path.exists(local_file_path):
            os.remove(local_file_path)


@dp.message(F.video)
async def handle_video(message: Message):
    await analyze_video(message)


@dp.message(F.video_note)
async def handle_video_note(message: Message):
    await analyze_video(message)


@dp.message()
async def handle_other(message: Message):
    await message.answer("Пришлите мне видео или кружок — разберу его.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info(f"Бот запущен, модель {MODEL}")
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    polling_task = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
    await stop_event.wait()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
