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

            # Ask the LLM for a 2-part documentary style hook!
            hook_prompt = f"""You are a top-tier YouTube thumbnail designer (like DOAC).
For the video title: '{title}'
Create a 2-part clickbait text hook.
Part 1 should be intriguing (e.g. "they danced to" or "seeing true reality").
Part 2 MUST BE A SINGLE, SHOCKING WORD that will be highlighted in a red box (e.g. "DEATH" or "KILL US").
Keep it extremely short. No emojis.
Respond ONLY with a JSON object: {{"line1": "...", "highlight_word": "..."}}"""
            try:
                resp = self.llm_client.generate_json("You are an expert thumbnail designer.", hook_prompt)
                line1_text = resp.get("line1", "").strip().lower()
                highlighted_text = resp.get("highlight_word", "").strip().upper()
                if not line1_text or not highlighted_text:
                    raise ValueError("Missing keys")
            except Exception as e:
                logger.warning("Failed to generate hook JSON, falling back: %s", e)
                line1_text = "the secret of"
                highlighted_text = "THE VIDEO"

            for i, t in enumerate(timestamps):
                frame = clip.get_frame(t)
                img = Image.fromarray(frame).convert("RGBA")
                
                # Subtle color grading (documentary style)
                from PIL import ImageEnhance
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(1.15)
                enhancer = ImageEnhance.Color(img)
                img = enhancer.enhance(1.2)
                
                W, H = img.size
                
                # Add a soft vignette (darkened edges) to focus attention
                vignette = Image.new('RGBA', (W, H), (0, 0, 0, 0))
                v_draw = ImageDraw.Draw(vignette)
                for gy in range(300):
                    alpha = int(255 * ((300 - gy) / 300) * 0.7)
                    v_draw.rectangle([gy, gy, W - gy, H - gy], outline=(0, 0, 0, alpha))
                img = Image.alpha_composite(img, vignette)
                
                draw = ImageDraw.Draw(img)
                
                # Function to get exact text height
                def get_text_dimensions(text, font):
                    bbox = draw.textbbox((0, 0), text, font=font)
                    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[1]
                    
                w1, h1, offset1 = get_text_dimensions(line1_text, font)
                w2, h2, offset2 = get_text_dimensions(highlighted_text, font)
                
                margin = 20 # space between lines
                pad_x = 50
                pad_y = 20
                
                box_w = w2 + (pad_x * 2)
                box_h = h2 + (pad_y * 2)
                
                total_h = h1 + margin + box_h
                
                # Position at bottom center
                start_y = H - total_h - 100
                
                # Line 1
                x1 = (W - w1) // 2
                y1 = start_y
                
                # Drop shadow for line 1
                draw.text((x1+8, y1+8 - offset1), line1_text, font=font, fill=(0,0,0, 200))
                draw.text((x1, y1 - offset1), line1_text, font=font, fill="white")
                
                # Line 2 (Red Highlight Box)
                x2 = (W - box_w) // 2
                y2 = y1 + h1 + margin
                
                # Box Drop Shadow
                draw.rectangle([x2+10, y2+10, x2+box_w+10, y2+box_h+10], fill=(0,0,0,150))
                # Red Box
                draw.rectangle([x2, y2, x2+box_w, y2+box_h], fill="#D90000")
                
                # Text inside box
                text_x = x2 + pad_x
                text_y = y2 + pad_y - offset2
                draw.text((text_x, text_y), highlighted_text, font=font, fill="white")
                
                img = img.convert("RGB")
                
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
