import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from google import genai
from google.genai import types
from aiohttp import web

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Получение ключей из переменных окружения
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

@dp.message(F.command_start)
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋 Я AI-аудитор продающих Reels и Shorts.\n\n"
        "Пришли мне видеоролик или видеосообщение (кружочек), "
        "и я дам разбор по хуку, пользе и чистоте кадра."
    )

@dp.message(F.video | F.video_note)
async def handle_video(message: Message):
    status_msg = await message.answer("📥 Видео получено! Загружаю в ИИ...")
    
    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"temp_{file_id}.mp4"
    
    try:
        # Скачиваем файл
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, local_file_path)
        await status_msg.edit_text("🔍 ИИ смотрит видео и готовится к анализу...")

        # Загружаем в Gemini API
        uploaded_gemini_file = gemini_client.files.upload(file=local_file_path)

        # Ждем завершения обработки на стороне Google
        while uploaded_gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            uploaded_gemini_file = gemini_client.files.get(name=uploaded_gemini_file.name)

        if uploaded_gemini_file.state.name == "FAILED":
            await status_msg.edit_text("❌ Ошибка при обработке видео.")
            return

        # Запрос к генеративной модели
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                uploaded_gemini_file,
                "Проанализируй этот продающий ролик с акцентом на хук, пользу и чистоту кадра без чужих брендов."
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2
            )
        )

        await status_msg.delete()

        # Отправка ответа пользователю (с обрезкой, если текст превышает лимит Telegram)
        text_response = response.text
        if len(text_response) > 4000:
            for chunk in [text_response[i:i+4000] for i in range(0, len(text_response), 4000)]:
                await message.answer(chunk, parse_mode="Markdown")
        else:
            await message.answer(text_response, parse_mode="Markdown")

        # Удаление файла с серверов Gemini
        gemini_client.files.delete(name=uploaded_gemini_file.name)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {str(e)}")

    finally:
        # Удаление временного локального файла
        if os.path.exists(local_file_path):
            os.remove(local_file_path)

# Веб-сервер для поддержки бесплатного тарифа Render (Web Service)
async def handle_health_check(request):
    return web.Response(text="Bot is active")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Запуск бота в режиме опроса Telegram
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
