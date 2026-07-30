"""
Assembly Agent

Role:
Takes the voice track, the stock videos/images, and the Whisper word-level timestamps,
and combines them using moviepy into a final 1080x1920 vertical video.
It dynamically overlays captions with custom styling and adds Ken Burns to images.
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class AssemblyAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.resolution = tuple(map(int, self.config.get("long_form", {}).get("resolution", "1920x1080").split('x')))
        
        brand_config = self.config.get("channels", [{}])[0].get("brand", {})
        self.font = brand_config.get("font", os.path.join(os.getcwd(), "assets", "fonts", "Roboto-Bold.ttf"))
        if not os.path.exists(self.font):
            logger.warning("Font %s not found. Captions may fail to render.", self.font)
            self.font = os.path.join(os.getcwd(), "assets", "fonts", "Roboto-Bold.ttf")
            
        self.accent_color = brand_config.get("accent_color", "yellow")
        
        # Priority 1: Separate intro_sting_path and bgm_path
        self.intro_sting_path = brand_config.get("intro_sting_path", "")
        self.bgm_path = brand_config.get("bgm_path", "")
        
        self.logo_path = brand_config.get("logo_path", "assets/logo/channel_logo.png")
        self.watermark_opacity = float(brand_config.get("watermark_opacity", 0.65))

    def _resize_and_crop(self, clip, target_resolution):
        from moviepy.video.fx.Crop import Crop
        from moviepy.video.fx.Resize import Resize
        
        target_w, target_h = target_resolution
        target_ratio = target_w / target_h
        
        clip_w, clip_h = clip.size
        clip_ratio = clip_w / clip_h
        
        if clip_ratio > target_ratio:
            resized_clip = clip.with_effects([Resize(height=target_h)])
            new_w = resized_clip.size[0]
            x_center = new_w / 2
            cropped = resized_clip.with_effects([Crop(x1=x_center - target_w/2, y1=0, x2=x_center + target_w/2, y2=target_h)])
        else:
            resized_clip = clip.with_effects([Resize(width=target_w)])
            new_h = resized_clip.size[1]
            y_center = new_h / 2
            cropped = resized_clip.with_effects([Crop(x1=0, y1=y_center - target_h/2, x2=target_w, y2=y_center + target_h/2)])
            
        return cropped

    def _apply_ken_burns(self, clip, duration, secondary_flashes=None):
        import random
        from moviepy.video.fx.Resize import Resize
        
        ken_burns_cfg = self.config.get("editing", {}).get("ken_burns", {})
        effects = ken_burns_cfg.get("effects", ["zoom_in"])
        zoom_range = ken_burns_cfg.get("zoom_range", [0.08, 0.15])
        
        effect = random.choice(effects)
        zoom_amount = random.uniform(zoom_range[0], zoom_range[1])
        
        punch_cfg = self.config.get("editing", {}).get("punch", {})
        flash_scale = punch_cfg.get("secondary_zoom_flash_scale", 1.18)
        flash_dur = punch_cfg.get("secondary_zoom_flash_duration_seconds", 0.25)
        
        def get_flash_multiplier(t):
            if not secondary_flashes:
                return 1.0
            mult = 1.0
            for flash_t in secondary_flashes:
                dt = abs(t - flash_t)
                if dt < flash_dur / 2:
                    progress = 1.0 - (dt / (flash_dur / 2))
                    mult = max(mult, 1.0 + (flash_scale - 1.0) * progress)
            return mult

        if effect == "zoom_in":
            def resize_func(t):
                return (1.0 + (zoom_amount * t / duration)) * get_flash_multiplier(t)
        elif effect == "zoom_out":
            def resize_func(t):
                return ((1.0 + zoom_amount) - (zoom_amount * t / duration)) * get_flash_multiplier(t)
        else:
            def resize_func(t):
                return (1.0 + zoom_amount) * get_flash_multiplier(t)
            
        base_clip = self._resize_and_crop(clip, self.resolution)
        zoomed_clip = base_clip.with_effects([Resize(resize_func)])
        
        target_w, target_h = self.resolution
        from moviepy.video.fx.Crop import Crop
        
        def crop_func(gf, t):
            zoomed_frame = gf(t)
            h, w, _ = zoomed_frame.shape
            
            if effect == "pan_left":
                max_x = max(0, w - target_w)
                x1 = int(max_x - (max_x * t / duration))
            elif effect == "pan_right":
                max_x = max(0, w - target_w)
                x1 = int(max_x * t / duration)
            else:
                x1 = int((w - target_w) / 2)
                
            y1 = int((h - target_h) / 2)
            
            x1 = max(0, min(x1, w - target_w))
            y1 = max(0, min(y1, h - target_h))
            
            return zoomed_frame[y1:y1+target_h, x1:x1+target_w]
            
        from moviepy import VideoClip
        ken_burns_clip = VideoClip(lambda t: crop_func(zoomed_clip.get_frame, t), duration=duration)
        return ken_burns_clip

    def _apply_zoom_flashes(self, clip, duration, secondary_flashes):
        if not secondary_flashes:
            return clip
            
        from moviepy.video.fx.Resize import Resize
        from moviepy import VideoClip

        punch_cfg = self.config.get("editing", {}).get("punch", {})
        flash_scale = punch_cfg.get("secondary_zoom_flash_scale", 1.18)
        flash_dur = punch_cfg.get("secondary_zoom_flash_duration_seconds", 0.25)
        
        def get_flash_multiplier(t):
            mult = 1.0
            for flash_t in secondary_flashes:
                dt = abs(t - flash_t)
                if dt < flash_dur / 2:
                    progress = 1.0 - (dt / (flash_dur / 2))
                    mult = max(mult, 1.0 + (flash_scale - 1.0) * progress)
            return mult

        target_w, target_h = self.resolution
        
        def resize_func(t):
            return get_flash_multiplier(t)
            
        zoomed_clip = clip.with_effects([Resize(resize_func)])
        
        def crop_func(gf, t):
            zoomed_frame = gf(t)
            h, w, _ = zoomed_frame.shape
            x1 = int((w - target_w) / 2)
            y1 = int((h - target_h) / 2)
            x1 = max(0, min(x1, w - target_w))
            y1 = max(0, min(y1, h - target_h))
            return zoomed_frame[y1:y1+target_h, x1:x1+target_w]
            
        return VideoClip(lambda t: crop_func(zoomed_clip.get_frame, t), duration=duration)

    def assemble_video(self, final_scenes: List[Dict], words_timing: List[Dict], voice_path: str, video_id: int) -> str:
        from moviepy import VideoFileClip, ImageClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, concatenate_videoclips
        from moviepy.video.fx.Loop import Loop
        
        logger.info("[AssemblyAgent] Starting video assembly for video %s", video_id)
        
        # We will render each scene individually to prevent 76+ open FFMPEG processes from causing OOM
        scene_files = []
        import subprocess
        import tempfile
        
        # Create a temp directory for scene chunks
        scenes_dir = self.cache_dir / f"scenes_{video_id}"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("[AssemblyAgent] Pre-rendering %d scenes to prevent memory leaks...", len(final_scenes))
        for idx, scene in enumerate(final_scenes):
            duration = scene["end_time"] - scene["start_time"]
            if duration <= 0:
                continue
                
            flashes = scene.get("zoom_flash_at", [])
            scene_output = str(scenes_dir / f"scene_{idx:03d}.mp4")
                
            try:
                path = scene["video_path"]
                if path.lower().endswith(('.jpg', '.jpeg', '.png')):
                    clip = ImageClip(path).with_duration(duration)
                    clip = self._apply_ken_burns(clip, duration, secondary_flashes=flashes)
                else:
                    clip = VideoFileClip(path, audio=False)
                    clip = self._resize_and_crop(clip, self.resolution)
                    
                    if clip.duration < duration:
                        clip = clip.with_effects([Loop(duration=duration)])
                    else:
                        clip = clip.subclipped(0, duration)
                        
                    clip = self._apply_zoom_flashes(clip, duration, flashes)
                    
                clip.write_videofile(
                    scene_output,
                    fps=24,
                    codec="libx264",
                    preset="ultrafast",
                    threads=1,
                    logger=None,
                    audio=False
                )
                clip.close()
                scene_files.append(scene_output)
            except Exception as exc:
                logger.error("Failed to process clip for scene %s: %s", scene.get("scene_number"), exc)
                from moviepy import ColorClip
                fallback = ColorClip(size=self.resolution, color=(0,0,0), duration=duration)
                fallback.write_videofile(
                    scene_output, fps=24, codec="libx264", preset="ultrafast", threads=1, logger=None, audio=False
                )
                fallback.close()
                scene_files.append(scene_output)
                
        logger.info("[AssemblyAgent] Using ffmpeg concat demuxer to instantly combine %d scenes...", len(scene_files))
        concat_txt = scenes_dir / "concat.txt"
        with open(concat_txt, "w") as f:
            for sf in scene_files:
                f.write(f"file '{Path(sf).resolve().as_posix()}'\n")
                
        combined_bg_path = str(self.cache_dir / f"combined_bg_{video_id}.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", str(concat_txt), 
            "-c", "copy", 
            combined_bg_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        logger.info("[AssemblyAgent] Loading the optimized combined background track.")
        main_video = VideoFileClip(combined_bg_path, audio=False)
        
        logger.info("[AssemblyAgent] Adding voice audio from %s", voice_path)
        voice_clip = AudioFileClip(voice_path)
        
        if main_video.duration > voice_clip.duration:
            main_video = main_video.subclipped(0, voice_clip.duration)
            
        audio_clips = [voice_clip]
        
        if self.intro_sting_path and os.path.exists(self.intro_sting_path):
            try:
                sting_clip = AudioFileClip(self.intro_sting_path)
                audio_clips.append(sting_clip)
            except Exception as exc:
                logger.warning("Failed to load intro sting: %s", exc)

        if self.bgm_path and os.path.exists(self.bgm_path):
            try:
                from moviepy.audio.fx.MultiplyVolume import MultiplyVolume
                from moviepy.audio.fx.AudioLoop import AudioLoop
                bgm_clip = AudioFileClip(self.bgm_path)
                bgm_clip = bgm_clip.with_effects([MultiplyVolume(0.1), AudioLoop(duration=main_video.duration)])
                audio_clips.append(bgm_clip)
            except Exception as exc:
                logger.warning("Failed to load BGM: %s", exc)
                
        # Swoosh transition logic removed as requested by the user
        final_audio = CompositeAudioClip(audio_clips)
        main_video = main_video.with_audio(final_audio)
        
        logger.info("[AssemblyAgent] Generating caption overlays via PIL Karaoke Accumulator (Zero-Overhead Mode)...")
        caption_timeline = []        
        caption_cache_dir = self.cache_dir / "captions"
        caption_cache_dir.mkdir(exist_ok=True)
        
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from moviepy import ImageClip
        
        # Load font
        try:
            font = ImageFont.truetype(self.font, 80)
        except IOError:
            logger.warning("Could not load trutype font %s. Falling back to default.", self.font)
            font = ImageFont.load_default()
            
        CHUNK_SIZE = 8
        MAX_LINE_WIDTH = 1400
        W, H = self.resolution
        
        # Robust width calculation
        def get_text_width(text, font):
            if hasattr(font, 'getlength'):
                return int(font.getlength(text))
            elif hasattr(font, 'getbbox'):
                return font.getbbox(text)[2]
            else:
                return font.getsize(text)[0]
                
        # Fix vertical jumping by using a reference string for consistent line height
        if hasattr(font, 'getbbox'):
            ref_h = font.getbbox("Ay")[3]
        elif hasattr(ImageDraw.Draw(Image.new("RGB", (1,1))), 'textbbox'):
            bbox = ImageDraw.Draw(Image.new("RGB", (1,1))).textbbox((0,0), "Ay", font=font)
            ref_h = bbox[3] - bbox[1]
        else:
            _, ref_h = font.getsize("Ay")
            
        space_w = get_text_width(" ", font)
        valid_words = [w for w in words_timing if w["word"].strip()]
        
        for i in range(0, len(valid_words), CHUNK_SIZE):
            chunk = valid_words[i:i+CHUNK_SIZE]
            
            # 1. Pre-calculate layout
            lines = []
            current_line = []
            current_w = 0
            
            for w in chunk:
                text = w["word"].strip()
                ww = get_text_width(text, font)
                
                if current_line and (current_w + space_w + ww > MAX_LINE_WIDTH):
                    lines.append(current_line)
                    current_line = []
                    current_w = 0
                    
                current_line.append({"text": text, "width": ww, "timing": w})
                if len(current_line) == 1:
                    current_w += ww
                else:
                    current_w += space_w + ww
            
            if current_line:
                lines.append(current_line)
                
            line_spacing = 20
            total_h = (len(lines) * ref_h) + (len(lines) - 1) * line_spacing
            
            # Target bottom 85% of screen
            target_y_center = int(H * 0.85)
            block_y = target_y_center - (total_h // 2)
            
            word_layouts = []
            current_y = block_y
            
            for line in lines:
                line_w = sum([w["width"] for w in line]) + (len(line) - 1) * space_w
                start_x = (W - line_w) // 2
                current_x = start_x
                
                for w in line:
                    word_layouts.append({
                        "text": w["text"],
                        "x": current_x,
                        "y": current_y,
                        "timing": w["timing"]
                    })
                    current_x += w["width"] + space_w
                    
                current_y += ref_h + line_spacing
                
            # 2. Accumulator Rendering
            for j in range(len(word_layouts)):
                target_word = word_layouts[j]
                
                img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                
                for k in range(len(word_layouts)):
                    w_info = word_layouts[k]
                    text = w_info["text"]
                    x, y = w_info["x"], w_info["y"]
                    
                    color = self.accent_color if k == j else "white"
                    
                    stroke = 4
                    for dx in range(-stroke, stroke+1, 2):
                        for dy in range(-stroke, stroke+1, 2):
                            draw.text((x+dx, y+dy), text, font=font, fill="black")
                            
                    draw.text((x, y), text, font=font, fill=color)
                    
                # Optimize memory for 10-minute videos by saving to disk instead of holding 1500 numpy arrays
                img_path = caption_cache_dir / f"caption_{i}_{j}.png"
                img.save(img_path, format="PNG", optimize=False)
                caption_timeline.append((target_word["timing"]["start"], target_word["timing"]["end"], str(img_path)))

        if caption_timeline:
            logger.info("[AssemblyAgent] Applying zero-overhead caption filter to %d caption frames.", len(caption_timeline))
            
            # Use a state dictionary to avoid nonlocal keyword limitations across multiple frames
            state = {"last_path": None, "last_img": None}
            
            def apply_caption(get_frame, t):
                frame = get_frame(t)
                
                active_path = None
                for start_t, end_t, path in caption_timeline:
                    if start_t <= t <= end_t:
                        active_path = path
                        break
                        
                if not active_path:
                    return frame
                    
                if active_path != state["last_path"]:
                    state["last_path"] = active_path
                    state["last_img"] = np.array(Image.open(active_path))
                    
                # Fast vectorized alpha compositing
                overlay = state["last_img"]
                alpha = overlay[:, :, 3:4] / 255.0
                frame = frame * (1 - alpha) + overlay[:, :, :3] * alpha
                return frame.astype(np.uint8)
                
            if hasattr(main_video, "transform"):
                main_video = main_video.transform(apply_caption)
            else:
                main_video = main_video.fl(apply_caption)
            
        final_clips = [main_video]
        if self.logo_path and os.path.exists(self.logo_path):
            try:
                from moviepy import ImageClip
                from moviepy.video.fx.Resize import Resize
                watermark = ImageClip(self.logo_path)
                
                # Resize the watermark so it's a small corner logo (e.g., 150px wide)
                if hasattr(watermark, "with_effects"):
                    watermark = watermark.with_effects([Resize(width=150)])
                elif hasattr(watermark, "resize"):
                    watermark = watermark.resize(width=150)
                
                if hasattr(watermark, "with_opacity"):
                    watermark = watermark.with_opacity(self.watermark_opacity)
                elif hasattr(watermark, "set_opacity"):
                    watermark = watermark.set_opacity(self.watermark_opacity)
                    
                if hasattr(watermark, "with_position"):
                    watermark = watermark.with_position((40, 40)).with_duration(main_video.duration)
                else:
                    watermark = watermark.set_position((40, 40)).set_duration(main_video.duration)
                
                final_clips.append(watermark)
                logger.info("[AssemblyAgent] Added watermark from %s", self.logo_path)
            except Exception as e:
                logger.warning("Failed to add watermark: %s", e)

        # Generate Section Title Cards (deep-dive format — no story number prefix)
        from PIL import Image, ImageDraw, ImageFont

        for scene in final_scenes:
            if "chapter_title" in scene:
                section_name = scene["chapter_title"].strip().upper()
                # Skip generic fallback titles and backend terms like 'Cold Hook'
                if not section_name or section_name in ("STORY", "FACT", "TRICK", "COLD HOOK", "FINAL THOUGHT"):
                    continue

                try:
                    font_size = 52
                    try:
                        c_font = ImageFont.truetype(self.font, font_size)
                        small_font = ImageFont.truetype(self.font, 28)
                    except Exception:
                        c_font = ImageFont.load_default()
                        small_font = c_font

                    # Calculate text dimensions
                    if hasattr(c_font, 'getbbox'):
                        bbox = c_font.getbbox(section_name)
                        text_w = bbox[2] - bbox[0]
                        text_h = bbox[3] - bbox[1]
                    else:
                        text_w, text_h = c_font.getsize(section_name)

                    padding_x, padding_y = 48, 22
                    bar_w = text_w + padding_x * 2
                    bar_h = text_h + padding_y * 2 + 8  # extra 8px for accent line

                    # Dark translucent bar (premium documentary look)
                    img = Image.new('RGBA', (bar_w, bar_h), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)

                    # Semi-transparent dark background
                    draw.rectangle([(0, 0), (bar_w, bar_h)], fill=(10, 10, 20, 210))

                    # Accent colour line on the left edge
                    accent_rgb = (255, 212, 0)  # default yellow
                    try:
                        hex_color = self.accent_color.lstrip('#')
                        accent_rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
                    except Exception:
                        pass
                    draw.rectangle([(0, 0), (5, bar_h)], fill=accent_rgb + (255,))

                    # White section title text
                    draw.text((padding_x, padding_y - 4), section_name, font=c_font, fill=(255, 255, 255, 255))

                    section_img_path = str(self.cache_dir / f"section_{scene['start_time']:.2f}.png")
                    img.save(section_img_path)

                    # Position: top-right corner
                    res_w, res_h = self.resolution
                    x_pos = res_w - bar_w - 60
                    y_pos = 60

                    chapter_clip = ImageClip(section_img_path).with_start(scene["start_time"]).with_duration(2.5)
                    if hasattr(chapter_clip, "with_position"):
                        chapter_clip = chapter_clip.with_position((x_pos, y_pos))
                    else:
                        chapter_clip = chapter_clip.set_position((x_pos, y_pos))

                    final_clips.append(chapter_clip)
                    logger.info("[AssemblyAgent] Added section card '%s' at t=%.1fs", section_name, scene["start_time"])
                except Exception as e:
                    logger.error("[AssemblyAgent] Failed to draw section title card: %s", e)

        main_video = CompositeVideoClip(final_clips)
            
        # Check for intro video and prepend if exists
        intro_vid_path = "assets/video/intro.mp4"
        if os.path.exists(intro_vid_path):
            try:
                from moviepy import VideoFileClip, concatenate_videoclips
                intro_clip = VideoFileClip(intro_vid_path)
                intro_clip = self._resize_and_crop(intro_clip, self.resolution)
                main_video = concatenate_videoclips([intro_clip, main_video])
                logger.info("[AssemblyAgent] Pre-pended custom intro video from %s", intro_vid_path)
            except Exception as e:
                logger.warning("Failed to prepend intro video: %s", e)

        output_path = self.cache_dir / f"final_video_{video_id}.mp4"
        logger.info("[AssemblyAgent] Exporting final video to %s", output_path)
        
        try:
            main_video.write_videofile(
                str(output_path),
                fps=24,
                codec="libx264",
                audio_codec="aac",
                preset="ultrafast",
                threads=1,
                logger="bar"
            )
            logger.info("[AssemblyAgent] Export successful!")
        except Exception as exc:
            logger.error("[AssemblyAgent] Export failed: %s", exc)
            raise
        finally:
            main_video.close()
            voice_clip.close()
            # No caption clips to close because we use zero-overhead fl() filtering
            
        return str(output_path)
