#  Luma — Real-Time Gemini Live Discord Voice Bot

Luma is a low-latency, real-time voice assistant for Discord powered by the **Google Gemini Live API** (`gemini-3.8-live`). Designed to feel like a natural phone call rather than a clunky text-to-speech bot, Luma features **wake-word activation ("Hey Luma")**, **barge-in interruption**, and **native multimodal speech-to-speech processing**.

---

## Features

- **Native Multimodal Processing:** Uses Gemini's Live WebSocket API for sub-second, direct audio-to-audio responses (no intermediate TTS/STT delay during conversation).
- **Wake-Word Activation ("Hey Luma"):** Stays in a low-power sleeping state until activated by a wake word, using fuzzy phonetic matching and Philippine English (`en-PH`/`fil-PH`) accent support.
- **Mid-Sentence Barge-In:** Stop Luma instantly by speaking while she is talking.
- **Low-Latency Audio Pipeline:** Converts Discord's $48\text{ kHz}$ stereo PCM audio to Gemini's $16\text{ kHz}$ mono format using high-fidelity linear interpolation.
- **Pre-Roll Audio Buffering:** Prevents clipping of the initial syllable when waking up the bot.
- **Bot Filtering:** Ignores audio coming from other Discord bots or internal echo.

---

## Project Structure

```text
├── main.py          # Discord bot commands, voice connections, and Gemini Live session
├── wake_word.py     # Wake-word speech recognition and fuzzy string matching
├── audio_utils.py   # Audio resampling (48kHz <-> 16kHz/24kHz) and RMS volume calculation
├── prompt.txt       # System prompt instructions defining Luma's persona and voice tone
├── .env             # API keys and secret tokens (ignored in git)
└── debug_audio/     # Local folder storing audio clips evaluated for wake words
```

---

## 🛠️ Prerequisites

1. **Python 3.10+**
2. **FFmpeg & libopus** (required by Discord for audio handling)
   - **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install ffmpeg libopus-dev`
   - **macOS:** `brew install ffmpeg opus`
   - **Windows:** Install FFmpeg via `winget install ffmpeg` and ensure it is in system PATH.
3. **Google Gemini API Key** (with access to the Live API)
4. **Discord Bot Token** with `Message Content Intent` and `Voice State Intent` enabled in the [Discord Developer Portal](https://discord.com/developers/applications).

---

## Installation

1. **Clone or download the repository:**
   ```bash
   cd /path/to/your/luma-bot
   ```

2. **Create and activate a Python virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Create a `.env` file in the project root:
   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   GEMINI_API=your_gemini_api_key_here
   GEMINI_LIVE_MODEL=gemini-3.8-live
   DEBUG_AUDIO=1
   ```

5. **Customize System Instructions:**
   Edit `prompt.txt` to define Luma's persona, language preferences, and behavior.

---

## Running the Bot

Start the bot from your activated virtual environment:

```bash
python main.py
```

---

## 🎮 Discord Commands

| Command | Description |
| :--- | :--- |
| `!luma join` | Connects Luma to your current voice channel and starts listening for "Hey Luma". |
| `!luma leave` | Disconnects Luma from the active voice channel and closes the Gemini WebSocket session. |
| Mention / Text | Mentioning the bot or typing `"sevi"` in a text channel triggers a brief text response. |

---

## ⚙️ Key Configuration Options (`main.py`)

- `ENERGY_THRESHOLD`: Sets the minimum microphone volume level (RMS) to classify incoming audio as speech vs. background noise.
- `END_OF_SPEECH_SECONDS`: Silence duration (in seconds) required before closing a wake-word audio buffer.
- `AWAKE_TIMEOUT_SECONDS`: Time of silence (in seconds) before Luma automatically reverts from Awake state back to Sleeping state.
