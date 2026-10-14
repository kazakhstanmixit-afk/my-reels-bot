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
    "Ты эксперт по видеомаркетингу бренда MIXIT. Смотри ролик блогера и выдай СТРОГО два блока.\n\n"

    "=== БЛОК 1: ОЦЕНКА ДЛЯ МЕНЕДЖЕРА ===\n\n"

    "Оцени каждую ось от 0 до 3:\n\n"

    "hookAngle — сила хука (первые 3 секунды):\n"
    "  0 = нет ни визуального, ни вербального захвата\n"
    "  1 = есть что-то одно (только текст или только визуал)\n"
    "  2 = оба элемента есть, но слабые\n"
    "  3 = мощный двойной хук: крупный план результата/до-после/эмоция лица + цепляющая фраза одновременно\n\n"

    "oneThought — одна мысль:\n"
    "  0 = хаос, несколько тем и продуктов\n"
    "  1 = есть идея, но размытая\n"
    "  2 = понятно о чём, но уходит в сторону\n"
    "  3 = один продукт, один угол, одна эмоция от начала до конца\n\n"

    "structure — структура:\n"
    "  0 = нет логики\n"
    "  1 = есть хук или CTA, но не оба\n"
    "  2 = хук + демонстрация + CTA, но слабо\n"
    "  3 = полная дуга: хук -> до/после -> почему именно этот продукт -> CTA\n\n"

    "gate_score = сумма трёх осей (максимум 9).\n\n"

    "Проверь formal_pass — шесть отдельных критериев:\n\n"

    "double_hook_ok: в первые 3 секунды ОДНОВРЕМЕННО есть визуальный захват "
    "(крупный план результата, до/после, макро текстура кожи, яркая эмоция лица) "
    "И вербальный (фраза голосом или текст на экране) — ДА или НЕТ\n\n"

    "title_ok: есть СТАТИЧНЫЙ заголовок — текст который стоит неподвижно поверх видео в первые 5-8 секунд. "
    "НЕТ если: есть только субтитры (бегущие слова под речь); заголовок всплывает и сразу исчезает; "
    "текст появляется позже 8-й секунды; нет никакого текста в начале. "
    "Субтитры — это не заголовок. Заголовок — это отдельный неподвижный текст с темой ролика — ДА или НЕТ\n\n"
    "subtitles_ok: на протяжении всего видео есть субтитры (текст под речь синхронно с озвучкой). "
    "Субтитры обязательны. НЕТ если их нет или они есть только частично — ДА или НЕТ\n\n"

    "before_after_ok — два разных сценария, определи какой:\n"
    "СЦЕНАРИЙ А: блогер упоминает других блогеров ('видела у блогеров', 'как у [имя]', 'этот тренд') — "
    "тогда before_after_ok = ДА если в видео есть наложенные скрины/кадры этих блогеров. "
    "Нет скринов = НЕТ.\n"
    "СЦЕНАРИЙ Б: блогер не упоминает других — "
    "тогда before_after_ok = ДА только если есть чёткий визуальный результат на коже крупным планом: "
    "кожа ДО (без средства) и кожа ПОСЛЕ (с эффектом: сияние, текстура, скульптурирование, чистота пор). "
    "НЕТ если: только процесс нанесения; только слова об эффекте; результат не виден глазом. "
    "В сценарии Б это ЖЕЛЕЗНОЕ ПРАВИЛО — ДА или НЕТ\n\n"

    "cta_ok: в конце есть призыв к действию голосом ИЛИ текстом "
    "('артикул в описании' / 'Заказала на Kaspi' / 'ссылка в описании' или аналог) — ДА или НЕТ\n\n"

    "voiceover_ok: проверь ВСЁ перечисленное — НЕТ если хоть одно нарушено:\n"
    "  - озвучка совпадает с тем что показывается в кадре (говорит о нанесении — показывает нанесение)\n"
    "  - блогер раскрывает ПОЧЕМУ именно этот продукт: состав, эффект на коже, чем отличается — "
    "не просто 'классный' или 'понравился'\n"
    "  - нет глухой тишины больше 2 секунд подряд\n"
    "  - нет самолюбования: долгих кадров на лицо/себя без слов о продукте\n"
    "  - если блогер говорит 'видела у блогеров', 'как у [имя]', 'этот тренд' — "
    "это не нарушение само по себе, но слабое место: нет скринов/кадров других блогеров И нет своего крупного результата. "
    "Отметь как рекомендацию, не как жёсткое нарушение. Достаточно одного из двух\n"
    "  - если речь медленная (слова растянуты, паузы между фразами) — нужно ускорить монтажом\n"
    "ДА или НЕТ\n\n"

    "dynamics_ok: есть монтажная динамика (смена ракурсов, нарезка, переходы), "
    "нет затяжных однообразных кадров без движения и слов — ДА или НЕТ\n\n"

    "formal_pass: ДА только если ВСЕ семь выше = ДА, иначе НЕТ\n\n"

    "Решение:\n"
    "ПУБЛИКАЦИЯ — gate_score 8-9 И formal_pass = ДА\n"
    "ПРАВКИ — gate_score 5-7 ИЛИ 1-2 из formal_pass провалены\n"
    "ПЕРЕСЪЁМКА — gate_score ниже 5 ИЛИ три и более formal_pass провалены\n\n"

    "Выведи блок 1 СТРОГО в этом формате:\n"
    "--- ОЦЕНКА ДЛЯ МЕНЕДЖЕРА ---\n"
    "hookAngle: X/3\n"
    "oneThought: X/3\n"
    "structure: X/3\n"
    "gate_score: X/9\n"
    "double_hook_ok: ДА / НЕТ\n"
    "title_ok: ДА / НЕТ\n"
    "subtitles_ok: ДА / НЕТ\n"
    "before_after_ok: ДА / НЕТ\n"
    "cta_ok: ДА / НЕТ\n"
    "voiceover_ok: ДА / НЕТ\n"
    "dynamics_ok: ДА / НЕТ\n"
    "subtitles_ok: ДА / НЕТ\n"
    "formal_pass: ДА / НЕТ\n"
    "РЕШЕНИЕ: ПУБЛИКАЦИЯ / ПРАВКИ / ПЕРЕСЪЁМКА\n"
    "Причина: [1-2 предложения — конкретно что провалено и почему именно это решение]\n\n"

    "=== БЛОК 2: СООБЩЕНИЕ БЛОГЕРУ ===\n\n"

    "Напиши сообщение блогеру на 'вы', 10-12 строк.\n"
    "Структура:\n"
    "- 1 комплимент за то что реально хорошо в ролике\n"
    "- 'Вот что стоит доработать:'\n"
    "- Конкретные правки с таймкодом по каждому нарушенному критерию:\n"
    "  * Нет заголовка — попросить добавить статичный текст с темой ролика в первые 5-8 секунд "
    "(не субтитры, а отдельный неподвижный заголовок)\n"
    "  * Нет субтитров — субтитры обязательны на всём видео, синхронно с речью\n"
    "  * Нет до/после или только в конце — перенести в начало или сразу после хука\n"
    "  * CTA отсутствует — добавить 'артикул в описании' голосом или текстом\n"
    "  * Тишина / медленная речь — предложить ускорить или записать озвучку с раскрытием продукта: "
    "состав, эффект, почему именно он\n"
    "  * Озвучка не совпадает с кадром / самолюбование — указать конкретный момент и что сказать вместо\n"
    "  * Нет динамики — предложить добавить нарезку, смену ракурсов\n"
    "  * Если блогер упоминает других блогеров и нет скринов их видео — написать:\n"
    "    'Вы упоминаете других блогеров — обязательно добавьте наложением скрины или кадры их видео, "
    "чтобы зритель увидел о чём речь'\n"
    "  * Если блогер НЕ упоминает других и нет до/после — написать:\n"
    "    'Обязательно добавьте кадр кожи ДО (без средства) и крупный план результата ПОСЛЕ — "
    "это главное что убеждает зрителя'\n"
    "- Финал: короткая фраза что ждёшь новую версию\n\n"

    "Тон: тёплый, конкретный, без слова 'проблема', без жаргона.\n"
    "Если РЕШЕНИЕ = ПЕРЕСЪЁМКА — пиши прямо: что именно переснять и почему, без лишней мягкости.\n\n"

    "Выведи блок 2 СТРОГО в этом формате:\n"
    "--- СООБЩЕНИЕ БЛОГЕРУ ---\n"
    "[текст сообщения]\n\n"

    "ЗАПРЕЩЕНО:\n"
    "- Выводить что-то кроме двух блоков\n"
    "- Требовать одновременно озвучку И текст (одного достаточно)\n"
    "- Писать про артикул на самом видео\n"
    "- Считать затянутостью показ продукта с рассказом о его свойствах\n"
    "- Требовать текстовые плашки для каждого этапа если есть озвучка\n"
    "- Упоминать конверсию, продажи, метрики\n"
    "- Косметика других брендов в кадре — нарушение, упомяни в правках\n"
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
                            "Проанализируй этот ролик. Выведи СТРОГО два блока по инструкции."
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
