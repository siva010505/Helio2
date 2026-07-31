"""
Visual Director Agent

Role:
Retrieves stock footage candidates based on scene queries, extracts a keyframe,
scores them using a Vision LLM, and aligns the final selected videos with precise audio timestamps.
Tracks used footage to avoid repeats. Generates fallback kinetic cards if API fails.

Inputs:
- scenes (from VisualPlannerAgent)
- words (from CaptionAgent)

Outputs:
- Aligned scenes: list of {scene_number, start_time, end_time, video_path, text_segment}
"""

import os
import requests
import base64
import logging
import json
import string
import random
from typing import Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

def clean_text(text: str) -> str:
    return text.translate(str.maketrans('', '', string.punctuation)).lower().strip()

class VisualDirectorAgent:
    def __init__(self, llm_client, config: Dict[str, Any]):
        self.llm_client = llm_client
        self.config = config
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = Path("data/visual_history.json")
        self._load_history()
        
        self.pexels_api_key = os.environ.get("PEXELS_API_KEY")
        self.pixabay_api_key = os.environ.get("PIXABAY_API_KEY")
        self.unsplash_api_key = os.environ.get("UNSPLASH_API_KEY")

    def _load_history(self):
        if self.history_file.exists():
            try:
                with open(self.history_file, "r") as f:
                    self.history = set(json.load(f))
            except:
                self.history = set()
        else:
            self.history = set()
            
    def _save_history(self):
        with open(self.history_file, "w") as f:
            json.dump(list(self.history), f)

    def _align_timings(self, scenes: List[Dict], words: List[Dict]) -> List[Dict]:
        aligned = []
        word_idx = 0
        total_words = len(words)
        
        for i, scene in enumerate(scenes):
            scene_text = clean_text(scene.get("text_segment", ""))
            scene_start = words[word_idx]["start"] if word_idx < total_words else 0.0
            
            accumulated = []
            while word_idx < total_words:
                w_clean = clean_text(words[word_idx]["word"])
                if w_clean:
                    accumulated.append(w_clean)
                word_idx += 1
                
                if len(" ".join(accumulated)) >= len(scene_text) * 0.9:
                    break
                    
            scene_end = words[word_idx - 1]["end"] if word_idx > 0 else 0.0
            
            if i == len(scenes) - 1:
                if word_idx < total_words:
                    scene_end = words[-1]["end"]
                # Add a 4-second buffer to the final scene to cover audio reverb/trailing silence.
                # assembly_agent will trim any excess down to the exact voice_clip.duration.
                scene_end += 4.0
                
            aligned.append({
                **scene,
                "start_time": scene_start,
                "end_time": scene_end
            })
            
        return aligned


    def _extract_frame_base64(self, file_path: str) -> str:
        from PIL import Image
        import io
        
        try:
            if file_path.lower().endswith(('.mp4', '.mov', '.avi')):
                from moviepy import VideoFileClip
                clip = VideoFileClip(file_path)
                t = clip.duration / 2.0
                frame = clip.get_frame(t)
                clip.close()
                img = Image.fromarray(frame)
            else:
                img = Image.open(file_path)
                
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((512, 512))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to extract frame from %s: %s", file_path, exc)
            return ""

    def _score_video(self, file_path: str, description: str) -> float:
        if not self.config.get("visual_sources", {}).get("vision_scoring", {}).get("enabled", True):
            return 10.0

        b64_image = self._extract_frame_base64(file_path)
        if not b64_image:
            return 0.0

        try:
            result = self.llm_client.score_image(description, b64_image)
            score = float(result.get("score", 0.0))
            logger.info("Scored candidate %s -> %.1f  reason: %s", file_path, score, result.get("reason", ""))
            return score
        except Exception as exc:
            logger.error("Vision scoring failed: %s", exc)
            return 0.0

    def _create_fallback_card(self, text: str, duration: float, output_path: str) -> str:
        from moviepy import ColorClip, TextClip, CompositeVideoClip
        
        brand = self.config.get("channels", [{}])[0].get("brand", {})
        font_path = brand.get("font", os.path.join(os.getcwd(), "assets", "fonts", "Roboto-Bold.ttf"))
        
        bg = ColorClip(size=(1920, 1080), color=(30, 30, 30), duration=duration)
        txt = TextClip(
            font=font_path,
            text=text,
            font_size=80,
            color="white",
            size=(1600, None),
            method="caption",
            text_align="center"
        )
        txt = txt.with_position("center").with_duration(duration)
        comp = CompositeVideoClip([bg, txt])
        comp.write_videofile(output_path, fps=24, logger=None, audio=False)
        
        bg.close()
        txt.close()
        comp.close()
        return output_path

    def select_visuals(self, scenes: List[Dict], words: List[Dict]) -> List[Dict]:
        logger.info("[VisualDirector] Aligning timings for %d scenes.", len(scenes))
        aligned_scenes = self._align_timings(scenes, words)
        
        from src.services.stock_search import search_pexels, search_pixabay, search_unsplash
        
        final_scenes = []
        for scene in aligned_scenes:
            query = scene.get('search_query', '')
            logger.info("[VisualDirector] Processing Scene %d: '%s'", scene.get('scene_number', 0), query)
            
            urls = []
            urls.extend(search_pexels(query, api_key=self.pexels_api_key))
            urls.extend(search_pixabay(query, api_key=self.pixabay_api_key))
            urls.extend(search_unsplash(query, api_key=self.unsplash_api_key))
            
            fresh_urls = [u for u in urls if u not in self.history]
            used_urls = [u for u in urls if u in self.history]
            max_cands = self.config.get("visual_sources", {}).get("vision_scoring", {}).get("max_candidates_scored_per_scene", 2)
            urls_to_check = (fresh_urls + used_urls)[:max_cands]
            
            best_file_path = None
            best_score = -1.0
            best_url = None
            
            candidates_to_clean = []
            
            for idx, url in enumerate(urls_to_check):
                ext = ".jpg" if "unsplash" in url else ".mp4"
                cand_path = self.cache_dir / f"cand_s{scene.get('scene_number', 0)}_{idx}{ext}"
                try:
                    r = requests.get(url, stream=True, timeout=20)
                    r.raise_for_status()
                    with open(cand_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            
                    score = self._score_video(str(cand_path), scene.get("description", ""))
                    if score > best_score:
                        best_score = score
                        best_file_path = str(cand_path)
                        best_url = url
                        
                    candidates_to_clean.append(str(cand_path))
                except Exception as exc:
                    logger.error("Failed handling candidate %d: %s", idx, exc)
                    
            if best_file_path:
                self.history.add(best_url)
                self._save_history()
            else:
                logger.warning("No valid visual found for Scene %d. Generating fallback card.", scene.get('scene_number', 0))
                duration = scene.get("end_time", 0.0) - scene.get("start_time", 0.0)
                if duration <= 0:
                    duration = 3.0
                fallback_path = str(self.cache_dir / f"fallback_s{scene.get('scene_number', 0)}.mp4")
                best_file_path = self._create_fallback_card(query, duration, fallback_path)
                
            for path in candidates_to_clean:
                if path != best_file_path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass
                
            final_scenes.append({
                **scene,
                "video_path": best_file_path
            })
            
        return final_scenes
