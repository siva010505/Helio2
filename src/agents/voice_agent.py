"""
Voice Agent

Role:
Generates the voice narration from the script using Piper (local TTS model).
It automatically downloads the ONNX model if not present.

Inputs:
- final script text
- configured voice (e.g., "en_US-lessac-medium")

Outputs:
- voice.wav (saved locally in data/cache/)
"""

import os
import subprocess
import logging
import requests
import json
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Fallback/Default Piper Voice Model URLs
DEFAULT_VOICE = "en_US-ryan-high"
PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/{language}/{speaker}/{quality}/{voice_model}"

class VoiceAgent:
    def __init__(self, config):
        self.config = config
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir = Path("data/models")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        
        if not shutil.which("piper") and not shutil.which("piper.exe"):
            raise RuntimeError("Piper TTS executable not found on PATH. Please install Piper and ensure it is accessible via the command line.")

    def _download_model(self, voice_name: str) -> tuple[Path, Path]:
        """
        Download the ONNX model and JSON config for Piper if not present.
        """
        # Parse voice_name to construct URL (assuming format like en_GB-alan-medium)
        parts = voice_name.split('-')
        if len(parts) >= 3:
            language = parts[0]
            speaker = parts[1]
            quality = parts[2]
        else:
            language = "en_US"
            speaker = "lessac"
            quality = "medium"
            voice_name = DEFAULT_VOICE

        model_filename = f"{voice_name}.onnx"
        config_filename = f"{model_filename}.json"
        
        model_path = self.models_dir / model_filename
        config_path = self.models_dir / config_filename

        if not model_path.exists() or not config_path.exists():
            logger.info(f"[VoiceAgent] Downloading Piper model {voice_name}...")
            
            # Construct URLs
            model_url = PIPER_VOICES_BASE.format(language=language, speaker=speaker, quality=quality, voice_model=model_filename)
            config_url = PIPER_VOICES_BASE.format(language=language, speaker=speaker, quality=quality, voice_model=config_filename)

            try:
                # Download config
                logger.info(f"Downloading {config_url}")
                r = requests.get(config_url, timeout=30)
                r.raise_for_status()
                with open(config_path, "wb") as f:
                    f.write(r.content)

                # Download model
                logger.info(f"Downloading {model_url}")
                r = requests.get(model_url, timeout=300)
                r.raise_for_status()
                with open(model_path, "wb") as f:
                    f.write(r.content)

                logger.info("[VoiceAgent] Model downloaded successfully.")
            except Exception as exc:
                logger.error("[VoiceAgent] Failed to download model: %s", exc)
                # Cleanup partial downloads
                if model_path.exists(): model_path.unlink()
                if config_path.exists(): config_path.unlink()
                raise

        return model_path, config_path

    def generate_voice(self, script_text: str, voice_config: str, video_id: int) -> str:
        """
        Generate voice.wav from script text.
        """
        logger.info("[VoiceAgent] Generating voice for video %s", video_id)
        
        # Determine voice (strip 'kokoro:' or 'piper:' prefixes if any)
        voice_name = voice_config.split(":")[-1] if ":" in voice_config else voice_config
        # We enforce piper for now since it is easily installed and runs locally
        if not voice_name.startswith("en_"):
            logger.info(f"[VoiceAgent] Defaulting '{voice_name}' to {DEFAULT_VOICE} for Piper TTS.")
            voice_name = DEFAULT_VOICE

        model_path, config_path = self._download_model(voice_name)

        output_wav = self.cache_dir / f"voice_{video_id}.wav"
        
        # Write script to temporary text file (append silence to prevent Piper from cutting off the last word)
        input_txt = self.cache_dir / f"script_{video_id}.txt"
        with open(input_txt, "w", encoding="utf-8") as f:
            f.write(script_text.strip() + "\n\n... ... ...\n")

        cmd = [
            "piper",
            "-m", str(model_path),
            "-c", str(config_path),
            "-i", str(input_txt),
            "-f", str(output_wav),
            "--length_scale", "1.15",
        ]
        
        logger.info("[VoiceAgent] Executing Piper TTS...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("[VoiceAgent] Voice generation complete: %s", output_wav)
        except subprocess.CalledProcessError as exc:
            logger.error("[VoiceAgent] Piper failed with exit code %s", exc.returncode)
            logger.error("[VoiceAgent] Piper STDERR: %s", exc.stderr)
            raise
        finally:
            if input_txt.exists():
                input_txt.unlink()

        return str(output_wav)
