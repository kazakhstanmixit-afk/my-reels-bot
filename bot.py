import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
import google.generativeai as genai
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Настройка ключа Gemini
genai.configure(api_key=GEMINI_API_KEY)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

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

# Используем модель gemini-1.5-flash с системными инструкциями
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=SYSTEM_INSTRUCTION
)

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
    uploaded_file = None
    
    try:
        # 1. Скачиваем видео из Telegram
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, local_file_path)
        await status_msg.edit_text("🔍 ИИ смотрит видео и готовится к анализу...")

        # 2. Загружаем файл в Google Gemini (асинхронно в отдельном потоке)
        uploaded_file = await asyncio.to_thread(genai.upload_file, local_file_path)

        # 3. Ждем, пока Google обработает видео
        while uploaded_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            uploaded_file = await asyncio.to_thread(genai.get_file, uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            await status_msg.edit_text("❌ Ошибка при обработке видео со стороны Google.")
            return

        # 4. Генерируем разбор
        response = await asyncio.to_thread(
            model.generate_content,
            [uploaded_file, "Проанализируй этот продающий ролик с акцентом на хук, пользу и чистоту кадра без чужих брендов."]
        )

        await status_msg.delete()

        # 5. Отправляем ответ
        text_response = response.text
        if len(text_response) > 4000:
            for chunk in [text_response[i:i+4000] for i in range(0, len(text_response), 4000)]:
                await message.answer(chunk, parse_mode="Markdown")
        else:
            await message.answer(text_response, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {str(e)}")

    finally:
        # Удаляем временные файлы
        if uploaded_file:
            try:
                await asyncio.to_thread(genai.delete_file, uploaded_file.name)
            except Exception:
                pass
        if os.path.exists(local_file_path):
            os.remove(local_file_path)

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

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
