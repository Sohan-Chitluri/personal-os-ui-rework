"""Voice capture skill for Personal OS - whisper.cpp integration."""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Optional

from personal_os.core.config import get_config
from personal_os.core.vault import get_vault

logger = logging.getLogger(__name__)

MODELS_DIR = Path.home() / ".local" / "share" / "personal-os" / "models"
AUDIO_DIR = Path("/tmp") / "personal-os-audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

WHISPER_CPP_PATH = "/usr/bin/whisper-cli"  # NixOS path

MODELS = {
    "tiny": "ggml-tiny.bin",
    "base": "ggml-base.bin",
    "small": "ggml-small.bin",
    "medium": "ggml-medium.bin",
    "large-v3": "ggml-large-v3.bin",
}


async def check_whisper() -> bool:
    """Check if whisper.cpp is available."""
    try:
        proc = await asyncio.create_subprocess_exec(
            WHISPER_CPP_PATH, "--help",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


async def download_model(model_name: str) -> bool:
    """Download a whisper model."""
    if model_name not in MODELS:
        logger.error(f"Unknown model: {model_name}")
        return False
    
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_file = MODELS_DIR / MODELS[model_name]
    
    if model_file.exists():
        logger.info(f"Model {model_name} already exists")
        return True
    
    url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODELS[model_name]}"
    logger.info(f"Downloading {model_name} from {url}...")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "wget", "-O", str(model_file), url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            logger.info(f"Downloaded to {model_file}")
            return True
        else:
            logger.error(f"Download failed: {stderr.decode()}")
            return False
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False


async def transcribe(audio_file: Path, model: str = "base", language: str = "en") -> Optional[str]:
    """Transcribe audio file using whisper.cpp."""
    if not await check_whisper():
        logger.error("whisper.cpp not found")
        return None
    
    model_path = MODELS_DIR / MODELS.get(model, MODELS["base"])
    if not model_path.exists():
        logger.error(f"Model {model} not found. Run with --download first.")
        return None
    
    cmd = [
        WHISPER_CPP_PATH,
        "-m", str(model_path),
        "-l", language,
        "-otxt",
        "-f", str(audio_file),
    ]
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        
        if proc.returncode == 0:
            txt_file = audio_file.with_suffix(".txt")
            if txt_file.exists():
                return txt_file.read_text().strip()
        else:
            logger.error(f"Transcription failed: {stderr.decode()}")
    except asyncio.TimeoutError:
        logger.error("Transcription timed out")
    except Exception as e:
        logger.error(f"Transcription error: {e}")
    
    return None


async def record_audio(duration: int, sample_rate: int = 16000) -> Optional[Path]:
    """Record audio using arecord (ALSA)."""
    output_file = AUDIO_DIR / f"capture_{int(asyncio.get_event_loop().time())}.wav"
    
    cmd = [
        "arecord",
        "-f", "S16_LE",
        "-c", "1",
        "-r", str(sample_rate),
        "-d", str(duration),
        str(output_file),
    ]
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        
        if proc.returncode == 0 and output_file.exists():
            return output_file
        else:
            logger.error(f"Recording failed: {stderr.decode()}")
            return None
    except Exception as e:
        logger.error(f"Recording error: {e}")
        return None


async def capture_voice(
    duration: Optional[int] = 10,
    model: str = "base",
    continuous: bool = False,
    language: str = "en",
) -> Optional[str]:
    """Capture voice input and return transcribed text."""
    if not await check_whisper():
        logger.error("whisper.cpp not found. Install from https://github.com/ggerganov/whisper.cpp")
        return None
    
    if continuous:
        logger.info("Starting continuous capture (Ctrl+C to stop)...")
        try:
            while True:
                text = await _capture_once(duration, model, language)
                if text:
                    print(f"\n🎤 {text}")
                    # Auto-process
                    vault = get_vault()
                    vault.append_journal_entry(text, source="Voice")
        except KeyboardInterrupt:
            logger.info("Continuous capture stopped")
        return None
    
    return await _capture_once(duration, model, language)


async def _capture_once(duration: int, model: str, language: str) -> Optional[str]:
    """Single capture cycle."""
    print(f"🎙️  Recording for {duration} seconds... (speak now)")
    
    audio_file = await record_audio(duration)
    if not audio_file:
        return None
    
    print("🔄 Transcribing...")
    text = await transcribe(audio_file, model, language)
    
    if text:
        print(f"✅ Transcribed: {text}")
        
        # Auto-process
        from personal_os.core.processor import process_entry
        vault = get_vault()
        vault.append_journal_entry(text, source="Voice")
        result = await process_entry(text, "Voice")
        
        if result.changes_made:
            print("🔄 Auto-processed:")
            for change in result.changes_made:
                print(f"   {change}")
        else:
            print("📝 Logged. No actionable items extracted.")
        
        return text
    else:
        print("❌ Transcription failed or empty")
        return None


if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Voice capture for Personal OS")
    parser.add_argument("text", nargs="*", help="Direct text (bypass voice)")
    parser.add_argument("--duration", type=int, default=10, help="Recording duration")
    parser.add_argument("--model", default="base", help="Whisper model")
    parser.add_argument("--language", default="en", help="Language code")
    parser.add_argument("--continuous", action="store_true", help="Continuous capture")
    parser.add_argument("--download", help="Download model")
    parser.add_argument("--list-models", action="store_true", help="List available models")
    
    args = parser.parse_args()
    
    if args.list_models:
        print("Available models:")
        for k, v in MODELS.items():
            path = MODELS_DIR / v
            status = "✓" if path.exists() else "✗"
            print(f"  {status} {k}: {v}")
        sys.exit(0)
    
    if args.download:
        asyncio.run(download_model(args.download))
        sys.exit(0)
    
    if args.text:
        # Direct text input
        text = " ".join(args.text)
        print(f"Captured: {text}")
        asyncio.run(capture_voice(duration=0, model=args.model))
        sys.exit(0)
    
    asyncio.run(capture_voice(
        duration=args.duration,
        model=args.model,
        continuous=args.continuous,
        language=args.language,
    ))