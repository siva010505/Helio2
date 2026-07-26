"""
Thumbnail Agent

Role:
Extracts multiple candidate frames from the assembled video, overlays text/graphics,
scores them with a Vision LLM for long-form grid click-through potential,
and returns the best thumbnail.
"""

import logging
import os
import random
import base64
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class ThumbnailAgent:
    def __init__(self, config: Dict[str, Any], llm_client):
        self.config = config
        self.llm_client = llm_client
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        brand_config = self.config.get("channels", [{}])[0].get("brand", {})
        self.font = brand_config.get("font", os.path.join(os.getcwd(), "assets", "fonts", "Roboto-Bold.ttf"))
        self.accent_color = brand_config.get("accent_color", "yellow")
        self.logo_path = brand_config.get("logo_path", "assets/logo/channel_logo.png")
        self.num_candidates = self.config.get("long_form", {}).get("thumbnail_candidates", 4)

    def _get_base64_image(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except:
            return ""

    def generate_thumbnail(self, video_path: str, title: str, video_id: int) -> str:
        """
        Extracts multiple frames, generates candidates, scores them, and returns the best one.
        """
        logger.info("[ThumbnailAgent] Generating %d candidate thumbnails for video %s", self.num_candidates, video_id)
        from moviepy import VideoFileClip
        from PIL import Image, ImageDraw, ImageFont
        import textwrap

        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            
            # Sample frames from different points avoiding first and last 10%
            timestamps = [
                random.uniform(duration * 0.1, duration * 0.9) 
                for _ in range(self.num_candidates)
            ]
            
            candidates = []
            
            try:
                if not os.path.exists(self.font):
                    font = ImageFont.load_default()
                else:
                    font = ImageFont.truetype(self.font, 160) # Large for grid
            except IOError:
                font = ImageFont.load_default()

            # Text treatment: max 3-5 words
            words = title.split()
            short_title = " ".join(words[:4])
            wrapped_text = textwrap.fill(short_title, width=15)

            for i, t in enumerate(timestamps):
                frame = clip.get_frame(t)
                img = Image.fromarray(frame)
                draw = ImageDraw.Draw(img)
                
                # Draw text on left side or bottom to leave negative space
                bbox = draw.textbbox((0, 0), wrapped_text, font=font)
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                W, H = img.size
                
                # Alternate positions
                if i % 2 == 0:
                    x, y = 100, H - h - 100
                else:
                    x, y = 100, 100
                
                # Draw stroke
                outline_range = 8
                for dx in range(-outline_range, outline_range+1):
                    for dy in range(-outline_range, outline_range+1):
                        draw.text((x+dx, y+dy), wrapped_text, font=font, fill="black")
                
                draw.text((x, y), wrapped_text, font=font, fill=self.accent_color)
                
                # Add logo
                if os.path.exists(self.logo_path):
                    try:
                        logo = Image.open(self.logo_path).convert("RGBA")
                        logo.thumbnail((250, 250))
                        img.paste(logo, (W - 300, 50), mask=logo)
                    except:
                        pass
                
                cand_path = self.cache_dir / f"thumbnail_cand_{video_id}_{i}.jpg"
                img.save(cand_path, "JPEG", quality=90)
                candidates.append(str(cand_path))
                
            clip.close()

            # Score candidates
            best_path = candidates[0]
            best_score = -1.0
            
            scoring_prompt = "How likely is this thumbnail to stop someone scrolling a video grid? Consider focal point, contrast, and dramatic effect."
            
            for cand in candidates:
                b64 = self._get_base64_image(cand)
                if not b64: continue
                try:
                    result = self.llm_client.score_image(scoring_prompt, b64)
                    score = float(result.get("score", 0.0))
                    logger.info("Candidate %s scored %.1f", cand, score)
                    if score > best_score:
                        best_score = score
                        best_path = cand
                except Exception as e:
                    logger.warning("Failed to score candidate %s: %s", cand, e)

            final_path = self.cache_dir / f"thumbnail_{video_id}.jpg"
            if os.path.exists(best_path):
                import shutil
                shutil.copy(best_path, final_path)
                
            # Cleanup candidates
            for cand in candidates:
                if os.path.exists(cand) and cand != best_path:
                    try: os.remove(cand)
                    except: pass

            logger.info("[ThumbnailAgent] Selected best thumbnail with score %.1f: %s", best_score, final_path)
            return str(final_path)

        except Exception as exc:
            logger.error("[ThumbnailAgent] Failed to generate thumbnail: %s", exc)
            return ""
