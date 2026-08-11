import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from google import genai
from google.genai import types
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
Ты — эксперт по короткому продающему видеоконтенту (Reels, TikTok, Shorts).
Твоя задача — проанализировать видео и дать конструктивный аудит.

Формат ответа в Markdown:
- 🎯 **Оценка Хука (1-10):** что исправить в первые 2–3 секунды для захвата внимания.
- 🧹 **Чистота кадра:** нет ли сторонних брендов, лишних предметов и визуального шума.
- 💡 **Смысл и Польза:** понятно ли, в чем выгода и ценность продукта для клиента.
- 🎬 **Динамика и CTA:** оценка монтажа, звука, текста на экране и призыва к действию.
- ✅ **Чек-лист правок:** 3-5 конкретных шагов (что вырезать / переснять / доработать).
"""


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋 Я AI-аудитор продающих Reels и Shorts.\n\n"
        "Пришли мне видеоролик или видеосообщение (кружочек), "
        "и я дам разбор по хуку, пользе и чистоте кадра."
    )


async def analyze_video(message: Message):
    """Общая логика для video и video_note."""
    status_msg = await message.answer("📥 Видео получено! Скачиваю файл...")

    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"/tmp/temp_{file_id}.mp4"
    uploaded_gemini_file = None

    try:
        # Скачиваем файл
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=local_file_path)
        await status_msg.edit_text("🔍 Передаю видео в Gemini...")

        # Загружаем в Gemini Files API
        uploaded_gemini_file = await asyncio.to_thread(
            lambda: gemini_client.files.upload(
                file=local_file_path,
                config={"mime_type": "video/mp4"}
            )
        )

        # Ждём обработки файла на стороне Gemini
        await status_msg.edit_text("⏳ Обрабатываю видео (может занять до минуты)...")
        max_wait = 60  # секунд
        waited = 0
        while uploaded_gemini_file.state.name == "PROCESSING" and waited < max_wait:
            await asyncio.sleep(3)
            waited += 3
            uploaded_gemini_file = await asyncio.to_thread(
                lambda: gemini_client.files.get(name=uploaded_gemini_file.name)
            )

        if uploaded_gemini_file.state.name != "ACTIVE":
            await status_msg.edit_text(
                f"❌ Gemini не смог обработать видео (статус: {uploaded_gemini_file.state.name}).\n"
                "Попробуй другой файл или уменьши его размер."
            )
            return

        await status_msg.edit_text("🧠 Формирую разбор...")

        response = await asyncio.to_thread(
            lambda: gemini_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[
                    types.Part.from_uri(
                        file_uri=uploaded_gemini_file.uri,
                        mime_type="video/mp4"
                    ),
                    "Проанализируй этот продающий ролик с акцентом на хук, пользу и чистоту кадра без чужих брендов."
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2
                )
            )
        )

        await status_msg.delete()

        text_response = response.text
        # Разбиваем длинный ответ на части по 4000 символов
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
        # Удаляем файл из Gemini
        if uploaded_gemini_file:
            try:
                await asyncio.to_thread(
                    lambda: gemini_client.files.delete(name=uploaded_gemini_file.name)
                )
            except Exception:
                pass
        # Удаляем локальный файл
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
    await message.answer(
        "Пришли мне видео или кружочек — я его разберу 🎬"
    )


# Health check для хостинга (Render, Railway и т.д.)
async def handle_health_check(request):
    return web.Response(text="Bot is active")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")


async def main():
    await start_web_server()
    logging.info("Bot started polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
