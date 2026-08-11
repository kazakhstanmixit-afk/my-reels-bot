import os
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from google import genai
from google.genai import types
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))  # Render даёт порт через PORT

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
Ты — эксперт по видеомаркетингу бренда MIXIT. Тебе присылают ролик блогера, снятый для продвижения бокса MIXIT (энзимная пудра, крем с витамином С, лимонница, свеча-лимон).

Сначала внутри себя глубоко оцени видео по критериям:
— Хук (первые 3 сек): есть ли крупный план кожи/результата, до/после, цепляющий текст на экране или сильная фраза? Долгое вступление, открывание двери, фраза «у меня нет проблем с кожей» — провальный хук.
— Демонстрация товара: виден ли результат на коже, текстура продукта, до/после?
— Темп и монтаж: нет ли затяжных скучных моментов, соответствует ли ритм энергии ролика?
— Подача голосом: темп речи, энергетика, естественность, чёткость. Медленная вялая речь — проблема.
— Текст на экране: обязателен. Статичный весь ролик или отсутствует — отметь.
— Продающие элементы: обозначена ли проблема, виден ли результат, есть ли эмоция желания купить?
— Что можно в кадре: одежда/аксессуары с логотипами, декор, техника — ок. Косметика других брендов — нельзя.

На выходе напиши ТОЛЬКО короткое сообщение, готовое отправить блогеру. Обращайся на «вы».
Правила сообщения:
— 1 короткий комплимент (что реально хорошо)
— 3–4 конкретных правки, каждая в 1–2 предложения — называй проблему и сразу давай решение
— Дружелюбно, как коллега, без жаргона и оценок по шкале
— Финальная фраза тёплая и мотивирующая
— Объём: 6–9 строк
Больше ничего не пиши — только само сообщение.
"""


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋 Я AI-аудитор продающих Reels и Shorts.\n\n"
        "Пришли мне видеоролик или видеосообщение (кружочек), "
        "и я дам разбор по хуку, пользе и чистоте кадра."
    )


async def analyze_video(message: Message):
    status_msg = await message.answer("📥 Видео получено! Скачиваю файл...")

    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"/tmp/temp_{file_id}.mp4"
    uploaded_gemini_file = None

    try:
        # Проверяем размер файла (Telegram Bot API лимит — 20 МБ)
        file_size = getattr(video_obj, "file_size", None)
        if file_size and file_size > 20 * 1024 * 1024:
            await status_msg.edit_text(
                "⚠️ Видео слишком большое (больше 20 МБ).\nСожмите его или обрежьте до 60 секунд и пришлите снова."
            )
            return

        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=local_file_path)
        await status_msg.edit_text("🔍 Передаю видео в Gemini...")

        def do_upload():
            return gemini_client.files.upload(file=local_file_path)

        uploaded_gemini_file = await asyncio.to_thread(do_upload)

        await status_msg.edit_text("⏳ Обрабатываю видео (может занять до минуты)...")
        max_wait = 60
        waited = 0
        while uploaded_gemini_file.state.name == "PROCESSING" and waited < max_wait:
            await asyncio.sleep(3)
            waited += 3
            file_name = uploaded_gemini_file.name
            def do_get(name=file_name):
                return gemini_client.files.get(name=name)
            uploaded_gemini_file = await asyncio.to_thread(do_get)

        if uploaded_gemini_file.state.name != "ACTIVE":
            await status_msg.edit_text(
                f"❌ Gemini не смог обработать видео (статус: {uploaded_gemini_file.state.name}).\n"
                "Попробуй другой файл или уменьши его размер."
            )
            return

        await status_msg.edit_text("🧠 Формирую разбор...")

        response = None
        for attempt in range(5):
            try:
                file_uri = uploaded_gemini_file.uri
                def do_generate(uri=file_uri):
                    return gemini_client.models.generate_content(
                        model="gemini-3.6-flash",
                        contents=[
                            types.Part.from_uri(
                                file_uri=uri,
                                mime_type="video/mp4"
                            ),
                            "Проанализируй этот продающий ролик с акцентом на хук, пользу и чистоту кадра без чужих брендов."
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=0.2
                        )
                    )
                response = await asyncio.to_thread(do_generate)
                break
            except Exception as e:
                logging.error(f"Попытка {attempt+1} не удалась: {e}")
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    if attempt < 4:
                        wait = (attempt + 1) * 10
                        await status_msg.edit_text(f"⏳ Gemini перегружен, повторяю через {wait} сек... (попытка {attempt + 1}/5)")
                        await asyncio.sleep(wait)
                    else:
                        raise Exception("Гемини перегружен, попробуй отправить видео ещё раз через пару минут.")
                else:
                    raise

        await status_msg.delete()

        text_response = response.text
        chunks = [text_response[i:i + 4000] for i in range(0, len(text_response), 4000)]
        for chunk in chunks:
            await message.answer(chunk, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка: {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                f"⚠️ Что-то пошло не так:\n`{str(e)[:500]}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    finally:
        if uploaded_gemini_file:
            try:
                await asyncio.to_thread(
                    lambda: gemini_client.files.delete(name=uploaded_gemini_file.name)
                )
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
    await message.answer("Пришли мне видео или кружочек — я его разберу 🎬")


async def handle_health_check(request):
    return web.Response(text="Bot is active")


async def main():
    # Сбрасываем вебхук и удаляем очередь обновлений — на случай если осталась старая сессия
    await bot.delete_webhook(drop_pending_updates=True)

    # Сначала поднимаем веб-сервер — Render ждёт порт в первые 5 минут
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Web server listening on port {PORT}")

    # Запускаем polling параллельно с веб-сервером
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )

    # Graceful shutdown на SIGTERM (Render посылает его при деплое/рестарте)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_sigterm():
        logging.info("SIGTERM получен, завершаю работу...")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    loop.add_signal_handler(signal.SIGINT, handle_sigterm)

    await stop_event.wait()

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    await runner.cleanup()
    logging.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
