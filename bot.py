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
    "Ты эксперт по видеомаркетингу бренда MIXIT. "
    "Анализируй ролик блогера строго и честно внутри, но пиши блогеру тепло и конструктивно.\n\n"
    "ВНУТРИ СЕБЯ задай вопрос: остановился бы я в ленте? Досмотрел? Захотел купить? "
    "Оценивай по трём осям (0-3 каждая):\n"
    "- oneThought: одна чёткая идея или мешанина?\n"
    "- hookAngle: насколько сильны первые 3 секунды?\n"
    "- structure: есть ли логика хук -> проблема -> решение -> демонстрация -> CTA?\n\n"
    "КРИТЕРИИ ОЦЕНКИ:\n\n"
    "1. ХУК (первые 3 сек) - ВАЖНЕЙШЕЕ:\n"
    "   Должно быть ОДНОВРЕМЕННО два элемента:\n"
    "   а) Визуальный захват: крупный план результата, до/после, макро текстура кожи, "
    "резкий переход, эмоция лица\n"
    "   б) Вербальное сообщение: цепляющая фраза голосом ИЛИ текст на экране\n"
    "   Если один из элементов отсутствует - хук слабый. Предложи конкретную замену.\n"
    "   Сильные углы: через боль (проблема кожи), через желаемый результат (сияние, чистые поры), "
    "через честный отзыв, через сравнение до/после, через разрушение стереотипа, через social proof.\n\n"
    "2. ОДНА МЫСЛЬ:\n"
    "   Один продукт - один угол - одна эмоция. Прыжки между разными проблемами и продуктами "
    "разрушают структуру.\n\n"
    "3. РЕЗУЛЬТАТ (железное правило):\n"
    "   До/после или чёткий видимый эффект на коже/волосах - ОБЯЗАТЕЛЬНО. "
    "Сияние, блеск, текстура, чистота крупным планом. "
    "Если нет - это правка номер один. Просто держит баночку - не считается.\n\n"
    "4. ДИНАМИКА:\n"
    "   Считай скучные секунды. Контекст: блогер показывает баночку и рассказывает - это уместно, "
    "не затянуто. Затянутость - это молчание, пустые движения без слов, повтор без смысла. "
    "Оптимальная длина: 15-30 сек для Reels, до 60 сек для демонстрации продукта.\n\n"
    "5. ПОДАЧА:\n"
    "   Если есть озвучка - темп (медленная убивает досмотры), энергетика, естественность.\n"
    "   Если нет озвучки - достаточно ли текста на экране?\n"
    "   ОДНО из двух достаточно - не требуй оба одновременно.\n\n"
    "6. CTA (призыв к действию):\n"
    "   Обязателен в конце - ЛИБО голосом, ЛИБО текстом (одного достаточно).\n"
    "   Формат для Kaspi: 'Заказала на Kaspi, артикул оставила в описании' или аналог.\n"
    "   Нет CTA - обязательная правка.\n\n"
    "7. ПРОДАЮЩИЕ ЭЛЕМЕНТЫ:\n"
    "   Обозначена ли боль/проблема? Понятно ли УТП продукта? "
    "Есть ли социальное доказательство (отзыв, цифры, трансформация)?\n\n"
    "ЗАПРЕЩЕНО В КАДРЕ: косметика других брендов. Одежда с логотипами, декор, техника - ок.\n\n"
    "ВАЖНО: не требуй текстовые плашки для каждого этапа, если есть озвучка. "
    "Показывать продукт с рассказом - не затянутость. Артикул в видео писать не нужно.\n\n"
    "ФОРМАТ ОТВЕТА (только готовое сообщение блогеру, на 'вы'):\n"
    "- 1 комплимент за то, что реально хорошо в ролике\n"
    "- 'Вот что стоит доработать:'\n"
    "- 3-4 правки: что видишь в кадре + почему слабо + как исправить конкретно "
    "(с таймкодом если возможно)\n"
    "- Тон тёплый, без жаргона, без слова 'проблема'\n"
    "- 1 финальная фраза - ждёшь новую версию\n"
    "- 10-12 строк максимум\n\n"
    "ПИШИ ТОЛЬКО СООБЩЕНИЕ БЛОГЕРУ, БОЛЬШЕ НИЧЕГО."
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
                            "Проанализируй этот ролик."
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=0.2
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
