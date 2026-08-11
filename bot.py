import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

# Ключи берутся из настроек Render автоматически
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
        "Пришли мне видео ролик или видеосообщение (кружочек), "
        "и я даю разбор по хуку, пользе и чистоте кадра."
    )

@dp.message(F.video | F.video_note)
async def handle_video(message: Message):
    status_msg = await message.answer("📥 Видео получено! Загружаю в ИИ...")
    
    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"temp_{file_id}.mp4"
    
    try:
        # Скачиваем из Telegram
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, local_file_path)
        
        await status_msg.edit_text("🔍 ИИ смотрит видео и готовится к анализу...")

        # Загружаем в Gemini
        uploaded_gemini_file = gemini_client.files.upload(file=local_file_path)

        while uploaded_gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            uploaded_gemini_file = gemini_client.files.get(name=uploaded_gemini_file.name)

        if uploaded_gemini_file.state.name == "FAILED":
            await status_msg.edit_text("❌ Ошибка при обработке видео.")
            return

        # Анализируем
        response = gemini_client.models.generate_content(
            model='gemini-1.5-flash',
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
        await message.answer(response.text, parse_mode="Markdown")

        gemini_client.files.delete(name=uploaded_gemini_file.name)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {str(e)}")

    finally:
        if os.path.exists(local_file_path):
            os.remove(local_file_path)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
