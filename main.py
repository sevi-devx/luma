import asyncio
import io
import logging
import os
import threading
import time
import traceback
from ctypes.util import find_library
from pathlib import Path

import discord
from discord.ext import commands, voice_recv
from dotenv import load_dotenv
from google import genai
from google.genai import types

from audio_utils import discord_to_gemini, gemini_to_discord, parse_rate, rms
from wake_word import check_wake_word

# Load configuration
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("GEMINI_API")
LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")

prompt_path = Path("prompt.txt")
PROMPT = (
    prompt_path.read_text(encoding="utf-8")
    if prompt_path.exists()
    else "You are a helpful AI voice assistant named Luma."
)

gemini = genai.Client(api_key=API_KEY)

ENERGY_THRESHOLD = 600.0
END_OF_SPEECH_SECONDS = 0.4
AWAKE_TIMEOUT_SECONDS = 15.0
MAX_UTTERANCE_BYTES = 48000 * 2 * 2 * 10
DISCORD_FRAME_BYTES = 3840


def log_task_result(task: asyncio.Task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        print("[Task crashed]:")
        traceback.print_exception(type(exc), exc, exc.__traceback__)


class GeminiPlayer(discord.AudioSource):
    def __init__(self):
        self._buf = bytearray()
        self._lock = threading.Lock()

    def feed(self, pcm: bytes):
        with self._lock:
            self._buf.extend(pcm)

    def clear(self):
        with self._lock:
            self._buf.clear()

    def has_audio(self) -> bool:
        with self._lock:
            return len(self._buf) > 0

    def read(self) -> bytes:
        with self._lock:
            if len(self._buf) >= DISCORD_FRAME_BYTES:
                frame = bytes(self._buf[:DISCORD_FRAME_BYTES])
                del self._buf[:DISCORD_FRAME_BYTES]
                return frame
        return b"\x00" * DISCORD_FRAME_BYTES

    def is_opus(self) -> bool:
        return False


class SpeakerState:
    def __init__(self):
        self.buffer = bytearray()
        self.preroll = bytearray()
        self.last_voice = 0.0


class GeminiVoiceSink(voice_recv.AudioSink):
    def __init__(self, vc, session, player, loop):
        super().__init__()
        self.vc = vc
        self.session = session
        self.player = player
        self.loop = loop

        self.is_awake = False
        self.awake_user_id = None
        self.sent_audio = False
        self.last_voice = 0.0
        self.last_activity = time.monotonic()

        self.speakers = {}
        self._lock = threading.Lock()
        self._seen_audio = False

    def wants_opus(self) -> bool:
        return False

    def cleanup(self):
        print("Audio sink cleaned up.")

    def write(self, user, data):
        pcm = data.pcm
        if not pcm or (user is not None and user.bot):
            return

        if not self._seen_audio:
            self._seen_audio = True
            print("[Sink]: Receiving voice packets OK")

        uid = user.id if user is not None else 0
        now = time.monotonic()
        loud = rms(pcm) > ENERGY_THRESHOLD

        if self.is_awake:
            if uid != self.awake_user_id:
                return
            if loud:
                self.last_voice = self.last_activity = now
                self.sent_audio = True
                if self.player.has_audio():
                    self.player.clear()
            if loud or self.sent_audio:
                self._submit(self._send_audio(discord_to_gemini(pcm)))
            return

        with self._lock:
            st = self.speakers.setdefault(uid, SpeakerState())
            
            st.preroll.extend(pcm)
            if len(st.preroll) > DISCORD_FRAME_BYTES * 15:
                del st.preroll[:DISCORD_FRAME_BYTES]

            if loud:
                st.last_voice = now
                if not st.buffer:
                    st.buffer.extend(st.preroll)
                st.buffer.extend(pcm)
            elif st.buffer:
                st.buffer.extend(pcm)

            if len(st.buffer) > MAX_UTTERANCE_BYTES:
                st.last_voice = 0.0

    def _submit(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(self._log_future)

    @staticmethod
    def _log_future(fut):
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc:
            print(f"[Gemini send error]: {exc!r}")

    async def _send_audio(self, pcm16: bytes):
        if pcm16:
            await self.session.send_realtime_input(
                audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000")
            )

    async def _forward_utterance(self, pcm16: bytes):
        for i in range(0, len(pcm16), 6400):
            await self._send_audio(pcm16[i : i + 6400])
        await self.session.send_realtime_input(audio_stream_end=True)

    def _check_wake(self, uid, pcm48: bytes):
        if self.is_awake:
            return
        pcm16 = discord_to_gemini(pcm48)
        if not check_wake_word(pcm16):
            return
        print("[State]: Wake word detected! Luma is awake.")
        now = time.monotonic()
        self.awake_user_id = uid
        self.last_voice = self.last_activity = now
        self.sent_audio = False
        self.is_awake = True
        self._submit(self._forward_utterance(pcm16))

    async def watchdog(self):
        while True:
            await asyncio.sleep(0.1)
            try:
                now = time.monotonic()
                if self.is_awake:
                    if self.sent_audio and now - self.last_voice > END_OF_SPEECH_SECONDS:
                        self.sent_audio = False
                        await self.session.send_realtime_input(audio_stream_end=True)
                    elif (
                        now - self.last_activity > AWAKE_TIMEOUT_SECONDS
                        and not self.player.has_audio()
                    ):
                        print("[State]: Going back to sleep due to inactivity...")
                        self.is_awake = False
                        self.awake_user_id = None
                        self.sent_audio = False
                else:
                    flushed = []
                    with self._lock:
                        for uid, st in self.speakers.items():
                            if st.buffer and now - st.last_voice > END_OF_SPEECH_SECONDS:
                                flushed.append((uid, bytes(st.buffer)))
                                st.buffer.clear()
                                st.preroll.clear()
                    for uid, pcm in flushed:
                        print(f"[Recording Stopped]: Checking {len(pcm)} bytes for wake word...")
                        self.loop.run_in_executor(None, self._check_wake, uid, pcm)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Watchdog error]: {e!r}")


async def run_gemini_voice_session(vc: voice_recv.VoiceRecvClient, ctx: commands.Context):
    loop = asyncio.get_running_loop()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(parts=[types.Part.from_text(text=PROMPT)]),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    player = GeminiPlayer()
    watchdog_task = None
    try:
        async with gemini.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            print(f"[Gemini]: Connected to {LIVE_MODEL}")
            vc.play(player)

            sink = GeminiVoiceSink(vc, session, player, loop)
            vc.listen(sink)
            watchdog_task = asyncio.create_task(sink.watchdog())

            while vc.is_connected():
                async for response in session.receive():
                    sc = response.server_content
                    if not sc:
                        continue

                    if sc.interrupted:
                        player.clear()

                    if sc.input_transcription and sc.input_transcription.text:
                        print(f"[You]: {sc.input_transcription.text}")
                    if sc.output_transcription and sc.output_transcription.text:
                        print(f"[Luma]: {sc.output_transcription.text}")

                    if sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                rate = parse_rate(part.inline_data.mime_type)
                                player.feed(gemini_to_discord(part.inline_data.data, rate))
                                sink.last_activity = time.monotonic()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        traceback.print_exc()
        await ctx.send(f"Gemini session error: `{e}`")
    finally:
        if watchdog_task:
            watchdog_task.cancel()
        player.clear()
        try:
            vc.stop_listening()
        except Exception:
            pass


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!luma ", intents=intents)
MY_ACCOUNT_ID = 673863774813421568
voice_tasks = {}


@bot.event
async def on_ready():
    if not discord.opus.is_loaded():
        lib = find_library("opus")
        if lib:
            discord.opus.load_opus(lib)
    print(f"Bot {bot.user.name} is online and ready! (Opus loaded: {discord.opus.is_loaded()})")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    is_mentioned = any(user.id == MY_ACCOUNT_ID for user in message.mentions)
    has_text = "sevi" in message.content.lower()

    if is_mentioned or has_text:
        await message.channel.send("yes po?")

    await bot.process_commands(message)


@bot.command(name="join")
async def join(ctx: commands.Context):
    if not ctx.author.voice:
        await ctx.send("You are not in a voice channel!")
        return
    if ctx.voice_client:
        await ctx.send("I'm already in a voice channel.")
        return

    channel = ctx.author.voice.channel
    vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
    await ctx.send(f"Connected to **{channel.name}**! Say **'Hey Luma'** to wake me up.")

    task = asyncio.create_task(run_gemini_voice_session(vc, ctx))
    task.add_done_callback(log_task_result)
    voice_tasks[ctx.guild.id] = task


@bot.command(name="leave")
async def leave(ctx: commands.Context):
    task = voice_tasks.pop(ctx.guild.id, None)
    if task:
        task.cancel()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected from voice channel.")
    else:
        await ctx.send("I am not in a voice channel.")


if __name__ == "__main__":
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.ERROR)
    logging.getLogger("discord.ext.voice_recv.opus").setLevel(logging.ERROR)
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is missing from environment variables.")
    if not API_KEY:
        raise ValueError("GEMINI_API is missing from environment variables.")
    bot.run(TOKEN)
