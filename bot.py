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
Ty - strogiy ekspert po videomarketingu brenda MIXIT. Tebe prisylayut rolik blogera s produktom MIXIT.

Snachala vnutri sebya sprosi: "Esli by ya listal lentu - ostanovilsya by? Dosmotrel? Zaxotel kupit?" Analiziruy zhestko vnutri, pishi myagko snaruzhi.

OCENI PO KRITERIYAM:

XUK (pervye 3 sek): est li vizualnyy zaxvat + tekst na ekrane? Vzglyad v kameru, neytral'noe nachalo, dolgoe vstuplenie - poteryannye prosmotryi. Esli slabiy - predlozhi konkretnuyu zamenu: nachat s krupnogo plana rezultata na kozhe/volosax + tsepyayushchiy tekst.

REZULTAT (zheleznoe pravilo): do/posle ili chetkiy effekt na kozhe/volosax - OBYAZATELNO. Siyaniye, blesk, tekstura, chistota krupnym planom. Net - eto glavnaya pravka nomer odin.

DINAMIKA: video dolzhno byt dinamichnym, bez zatyazhek. Statichnyy kadr dolshe 3 sek, medlennoe naneseniye, dolgiye pauzy - nazyvay konkretno. Banki v kadre bolshe 3 sek - vybroshennoe vremya. Poryadok blokov ne obyazatelen - glavnoe chtoby bylo dinamichno i ne zatyanutyy.

OZVU4KA: esli est - temp, energetika, estestvennost. Medlennaya/vyalaya rech - skazhi pryamo. Esli net - dostatochno li teksta na ekrane.

TEKST NA EKRANE: obyazatelen. Melkiy/statichnyy/otsutstvuet - otmet.

CTA: obyazatelno v konce - golosom ili tekstom: "Zakazala na Kaspi, artikul ostavila v opisanii" ili "Nashla na Kaspi, artikul nizhe". Esli CTA drugoy ili net - obyazatelnaya pravka.

CHTO MOZHNO V KADRE: odezhda/aksessuary s logotipami, dekor, texnika - ok. Kosmetika drugix brendov - nelzya.

Na vyxode napishi TOLKO gotovoe soobshcheniye blogeru. Obrashchaysya na "vy".
- 1 kompliment (tolko esli realno est za chto)
- "Vot chto stoit dorabotat:"
- 4-5 pravok: chto vidish v kadre + pochemu slabo + kak ispravit konkretno
- Ton teplyy, bez zhargona, bez slova "problema"
- 1 finalnaya fraza - zhdesh novuyu versiyu
- 10-12 strok. Emko.
"""


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Privet! Ya AI-auditor prodayushchikh Reels i Shorts MIXIT.\n\n"
        "Prishli mne video ili kruglyy video i ya dam razbor."
    )


async def analyze_video(message: Message):
    status_msg = await message.answer("Video polucheno! Skachivayu fayl...")

    video_obj = message.video or message.video_note
    file_id = video_obj.file_id
    local_file_path = f"/tmp/temp_{file_id}.mp4"
    uploaded_gemini_file = None

    try:
        file_size = getattr(video_obj, "file_size", None)
        if file_size and file_size > 20 * 1024 * 1024:
            await status_msg.edit_text(
                "Video slishkom bolshoe (bolshe 20 MB).\n"
                "Szhmite ego ili obrezhe do 60 sekund i prishlite snova."
            )
            return

        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, destination=local_file_path)
        await status_msg.edit_text("Peredayu video v Gemini...")

        def do_upload():
            return gemini_client.files.upload(file=local_file_path)

        uploaded_gemini_file = await asyncio.to_thread(do_upload)

        await status_msg.edit_text("Obrabatyvayu video (mozhet zanyat do minuty)...")
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
                f"Gemini ne smog obrabotat video (status: {uploaded_gemini_file.state.name}).\n"
                "Poprobuy drugoy fayl ili umenshy ego razmer."
            )
            return

        await status_msg.edit_text("Formiruyu razbor...")

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
                            "Proanaliziruy etot rolik."
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            temperature=0.2
                        )
                    )
                response = await asyncio.to_thread(do_generate)
                break
            except Exception as e:
                logging.error(f"Popytka {attempt+1}: {e}")
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    if attempt < 4:
                        wait = (attempt + 1) * 10
                        await status_msg.edit_text(f"Gemini peregruzhen, povtoryayu cherez {wait} sek... (popytka {attempt+1}/5)")
                        await asyncio.sleep(wait)
                    else:
                        raise Exception("Gemini peregruzhen, poprobuy cherez neskolko minut.")
                else:
                    raise

        await status_msg.delete()

        text_response = response.text
        chunks = [text_response[i:i+4000] for i in range(0, len(text_response), 4000)]
        for chunk in chunks:
            await message.answer(chunk, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Oshibka: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"Chto-to poshlo ne tak:\n`{str(e)[:500]}`", parse_mode="Markdown")
        except Exception:
            pass

    finally:
        if uploaded_gemini_file:
            try:
                await asyncio.to_thread(lambda: gemini_client.files.delete(name=uploaded_gemini_file.name))
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
    await message.answer("Prishli mne video ili kruglyy - ya ego razberu.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot zapushchen")

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
