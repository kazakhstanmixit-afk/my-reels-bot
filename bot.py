import os
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

session = AiohttpSession(timeout=120)
bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Ты эксперт по видеомаркетингу бренда MIXIT. Анализируй ролик блогера.\n\n"

    "ШАГ 1 - ВНУТРЕННЯЯ ОЦЕНКА (строго, честно):\n"
    "Оцени по трём осям от 0 до 3:\n"
    "- hookAngle (хук): 0 = нет ни визуального, ни вербального захвата; "
    "1 = есть что-то одно; 2 = оба элемента, но слабо; 3 = мощный двойной хук первые 3 сек\n"
    "- oneThought (одна мысль): 0 = хаос и разные темы; 1 = есть идея, но размытая; "
    "2 = понятно о чём, но уходит в сторону; 3 = один продукт, один угол, одна эмоция\n"
    "- structure (структура): 0 = нет логики; 1 = есть хук или CTA, но не оба; "
    "2 = хук + демонстрация + CTA, но слабо; 3 = полная дуга хук -> эффект -> доказательство -> CTA\n\n"
    "Сумма = gate_score (максимум 9).\n\n"
    "Проверь formal_pass (все три должны быть выполнены):\n"
    "- double_hook_ok: в первые 3 секунды есть ОДНОВРЕМЕННО визуальный захват "
    "(крупный план результата/до-после/макро текстура) И вербальный (фраза голосом или текст на экране)\n"
    "- before_after_ok: есть видимый результат на коже/волосах крупным планом (до/после или финальный эффект)\n"
    "- cta_ok: есть призыв к действию в конце - голосом ИЛИ текстом (одного достаточно), "
    "в формате Kaspi ('Заказала на Kaspi, артикул оставила в описании' или аналог)\n\n"
    "Решение:\n"
    "- ПУБЛИКАЦИЯ: gate_score 8-9 + formal_pass = все три ОК\n"
    "- ПРАВКИ: gate_score 5-7 + хотя бы два из трёх formal_pass\n"
    "- ПЕРЕСЪЁМКА: gate_score ниже 5 ИЛИ formal_pass провален по двум+ пунктам\n\n"

    "ШАГ 2 - ФОРМАТ ОТВЕТА:\n"
    "Выведи СТРОГО в таком формате, ничего лишнего:\n\n"
    "--- ОЦЕНКА ДЛЯ МЕНЕДЖЕРА ---\n"
    "hookAngle: X/3\n"
    "oneThought: X/3\n"
    "structure: X/3\n"
    "gate_score: X/9\n"
    "double_hook_ok: ДА / НЕТ\n"
    "before_after_ok: ДА / НЕТ\n"
    "cta_ok: ДА / НЕТ\n"
    "formal_pass: ДА / НЕТ\n"
    "РЕШЕНИЕ: ПУБЛИКАЦИЯ / ПРАВКИ / ПЕРЕСЪЁМКА\n"
    "Причина: [1-2 предложения почему именно это решение]\n\n"
    "--- СООБЩЕНИЕ БЛОГЕРУ ---\n"
    "[Тёплое сообщение на 'вы', 10-12 строк:\n"
    "- 1 комплимент за то, что реально хорошо\n"
    "- 'Вот что стоит доработать:'\n"
    "- 3-4 конкретные правки: что видно в кадре + почему слабо + как исправить "
    "(с таймкодом если возможно)\n"
    "- без слова 'проблема', без жаргона\n"
    "- финальная фраза: ждёшь новую версию]\n\n"

    "ПРАВИЛА:\n"
    "- Показывать продукт с рассказом - не затянутость\n"
    "- Текстовые плашки для каждого этапа НЕ обязательны если есть озвучка\n"
    "- Артикул на видео писать не нужно\n"
    "- Косметика других брендов в кадре - нарушение\n"
    "- Одежда с логотипами, декор, техника - ок\n"
    "- CTA: голос ИЛИ текст, не требуй оба\n"
    "- Если ролик набрал ПЕРЕСЪЁМКУ - сообщение блогеру должно быть прямым и чётким, "
    "без лишней мягкости: конкретно что переснять и почему\n"
)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я AI-аудитор Reels и Shorts для MIXIT.\n\n"
        "Пришли мне видео или кружок - дам разбор."
    )


async def analyze_video(message: Message):
    status_msg = await message.answer("Видео получено! Скачиваю файл...")

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
        await status_msg.edit_text("Передаю видео в Gemini...")

        def do_upload():
            return gemini_client.files.upload(file=local_file_path)

        uploaded_gemini_file = await asyncio.to_thread(do_upload)

        await status_msg.edit_text("Обрабатываю видео (может занять до минуты)...")
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
                "Gemini не смог обработать видео.\n"
                "Попробуйте другой файл или уменьшите его размер."
            )
            return

        await status_msg.edit_text("Формирую разбор...")

        response = None
        for attempt in range(5):
            try:
                file_uri = uploaded_gemini_file.uri

                def do_generate(uri=file_uri):
                    return gemini_client.models.generate_content(
                        model="gemini-3.5-flash",
                        contents=[
                            types.Part.from_uri(
                                file_uri=uri,
                                mime_type="video/mp4"
                            ),
                            "Проанализируй этот ролик строго по инструкции."
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=0.1
                        )
                    )

                response = await asyncio.to_thread(do_generate)
                break
            except Exception as e:
                logging.error(f"Попытка {attempt + 1}: {e}")
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    if attempt < 4:
                        wait = (attempt + 1) * 10
                        await status_msg.edit_text(
                            f"Gemini перегружен, повторяю через {wait} сек... "
                            f"(попытка {attempt + 1}/5)"
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise Exception("Gemini перегружен. Попробуйте через несколько минут.")
                else:
                    raise

        await status_msg.delete()

        text_response = response.text
        chunks = [text_response[i:i + 4000] for i in range(0, len(text_response), 4000)]
        for chunk in chunks:
            await message.answer(chunk)

    except Exception as e:
        logging.error(f"Ошибка: {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                f"Что-то пошло не так:\n{str(e)[:500]}"
            )
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
    await message.answer("Пришлите мне видео или кружок - разберу его.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_sigterm():
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    loop.add_signal_handler(signal.SIGINT, handle_sigterm)

    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )

    await stop_event.wait()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
