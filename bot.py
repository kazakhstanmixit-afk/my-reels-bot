import os
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from google import genai
from google.genai import types


logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
Ты — эксперт по видеомаркетингу бренда MIXIT. Тебе присылают ролик блогера с любым продуктом MIXIT.

Задай себе вопрос: «Если бы я листал ленту — остановился бы? Досмотрел? Захотел купить?» Анализируй жёстко внутри, пиши мягко снаружи.

Оцени каждый критерий — ничего не пропускай:

ХУК (первые 3 сек): есть ли хотя бы одно — крупный план результата / быстрый переход до-после / цепляющий текст крупным шрифтом / сильная фраза голосом / резкий визуальный захват. Взгляд в камеру, нейтральное начало — потерянные просмотры. Если хук слабый — предложи конкретную замену: «Если у тебя [проблема], слушай внимательно» / «Жаль, что никто раньше не сказал мне про [продукт]» / «Я думала, что [стереотип], но ошибалась» / «Меня несколько раз спросили, что у меня на [коже/волосах]».

СТРУКТУРА: есть ли дуга — боль/желание → продукт → результат? Если порядок сломан — скажи как переставить.

РЕЗУЛЬТАТ (железное правило): до/после или чёткий результат на коже — ОБЯЗАТЕЛЬНО, в начале или середине. Сияние, текстура, изменение крупным планом. Нет — это главная правка номер один.

ДИНАМИКА: считай скучные секунды. Статичный кадр дольше 3 сек, медленное нанесение без монтажа, долгие паузы — называй конкретно. Ритм должен держать внимание каждую секунду. Банки в кадре дольше 3 сек — выброшенное время.

ОЗВУЧКА: если есть — темп (медленная убивает досмотры), энергетика, естественность. Если нет — достаточно ли текста на экране.

ТЕКСТ НА ЭКРАНЕ: обязателен. Мелкий / статичный / отсутствует — отметь.

CTA: мягкий («присмотрись если хочешь [результат]»), через дефицит («такие продукты быстро разбирают»), через рекомендацию («реально хочется советовать»). Без артикула. Нет CTA — упущенный зритель.

ЧТО МОЖНО В КАДРЕ: одежда/аксессуары с логотипами, декор, техника — ок. Косметика других брендов — нельзя.

На выходе напиши ТОЛЬКО готовое сообщение блогеру. Обращайся на «вы».
— 1 комплимент (только если реально есть за что)
— «Вот что стоит доработать:»
— 4–6 конкретных правок. Каждая: что именно видишь в кадре → почему это слабо → как конкретно исправить. Называй секунды если нужно. Предлагай готовые формулы из библиотеки хуков и CTA если уместно.
— Если видео про конкретный продукт — учитывай его свойства (очищение, сияние, текстура, эффект) и проверяй показан ли реальный результат именно этого продукта
— Тон тёплый, как опытный коллега. Без жаргона, без слова «проблема»
— 1 финальная фраза — ждёшь новую версию
— Объём: 10–15 строк. Достаточно глубоко чтобы блогер понял что делать, достаточно коротко чтобы дочитать.
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
                        model="gemini-2.5-pro",
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


async def main():
    # Сбрасываем вебхук и удаляем очередь обновлений
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен")

    # Graceful shutdown на SIGTERM
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_sigterm():
        logging.info("SIGTERM получен, завершаю работу...")
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

    logging.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
