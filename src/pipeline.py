"""
Pipeline

Role:
Wires all agents together end-to-end to produce and upload a single video
for a given selected topic.

Called once per selected topic by the OrchestratorAgent.

Current status (Phase 2): Stub — logs the topic and returns a placeholder dict.
Phases 3-8 will progressively implement each stage.

Stages (when complete):
    Script → Voice → Visual Planner → Visual Director (per scene) →
    Caption → Assembly → Thumbnail → SEO → Upload
"""

import json
import logging
from datetime import datetime

from src.db.models import Video

logger = logging.getLogger(__name__)


def run_pipeline(
    channel_config: dict,
    topics: list,
    db_session,
    llm_client=None,
    dry_run: bool = False,
    publish_time_str: str = None,
) -> dict:
    """
    Run the full video creation pipeline for a compilation of topics.
    """
    primary_topic_text = topics[0].get("topic_text", "unknown") if topics else "unknown"
    topic_db_id = topics[0].get("db_id") if topics else None

    logger.info(
        "[Pipeline] ── Starting compilation pipeline with primary topic: '%s' (dry_run=%s)",
        primary_topic_text, dry_run,
    )

    # ── Create a Video record immediately so we can track status ─────
    video = Video(
        channel_id=topics[0].get("channel_id") if topics else None,
        topic_id=topic_db_id,
        status="drafted",
        created_at=datetime.utcnow(),
    )
    db_session.add(video)
    db_session.commit()

    result = {
        "primary_topic": primary_topic_text,
        "video_db_id": video.id,
        "status": "stub",
        "youtube_video_id": None,
    }

    try:
        # ── Phase 3: Script Generation (Deep Dive — single narrative arc) ──
        from src.agents.deep_dive_script_agent import DeepDiveScriptAgent
        compilation_data = DeepDiveScriptAgent(llm_client, db_session).assemble_compilation(topics, channel_config)
        script_text = compilation_data.get("full_script")
        video.script_text = script_text
        video.hook_style = "deep_dive_cold_hook"
        db_session.commit()
        logger.info("[Pipeline] Phase 3 (Deep Dive Script) complete. ~%.0f words.", len(script_text.split()))

        # ── Phase 4: Voice Generation ──────────────────────────────
        from src.agents.voice_agent import VoiceAgent
        target_voice = channel_config.get('long_form', {}).get('voice', channel_config.get('voice', ''))
        voice_path = VoiceAgent(channel_config).generate_voice(script_text, target_voice, video.id)
        video.voice_used = target_voice
        db_session.commit()
        logger.info("[Pipeline] Phase 4 (Voice) complete. Audio saved at %s", voice_path)

        # ── Phase 5: Visual Planning ───────────────────────────────
        from src.agents.visual_planner_agent import VisualPlannerAgent
        visual_planner = VisualPlannerAgent(llm_client)
        shot_list = []
        
        def plan_and_append(text_segment):
            if not text_segment.strip(): return
            scenes = visual_planner.plan_visuals(text_segment, channel_config)
            shot_list.extend(scenes)

        logger.info("[Pipeline] Planning visuals for full deep-dive script...")

        # Deep-dive: the full script is ONE continuous narrative — plan it as one block.
        # Section markers are used to inject chapter title cards at the right scenes.
        section_markers = compilation_data.get("section_markers", [])
        story_starts = []   # Will be populated by section marker matching below

        plan_and_append(compilation_data["full_script"])

        closing_scene_idx = len(shot_list) - 1
        
        # Inject section chapter cards into the correct scenes by matching first_words
        for i, s in enumerate(shot_list):
            s["scene_number"] = i + 1
            seg_text = s.get("text_segment", "")
            for marker in section_markers:
                first_words = marker.get("first_words", "").strip()
                if first_words and seg_text.strip().startswith(first_words[:30]):
                    s["chapter_title"] = marker.get("section", "")
                    s["content_label"] = "Story"
            if i == closing_scene_idx:
                s["is_closing"] = True
            
        logger.info("[Pipeline] Phase 5 (Visual Planner) complete. %d scenes planned.", len(shot_list))

        # ── Phase 6: Captions ──────────────────────────────────────
        from src.agents.caption_agent import CaptionAgent
        words_timing = CaptionAgent(channel_config).generate_captions(voice_path)
        logger.info("[Pipeline] Phase 6 (Captions) complete. %d words timed.", len(words_timing))

        # ── Phase 5.5: Visual Direction ──────────────────────────────
        from src.agents.visual_director_agent import VisualDirectorAgent
        visual_director = VisualDirectorAgent(llm_client, channel_config)
        final_scenes = visual_director.select_visuals(shot_list, words_timing)
        
        video.status = "visuals_directed"
        db_session.commit()
        logger.info("[Pipeline] Phase 5.5 (Visual Director) complete. Final scenes aligned.")

        # ── Phase 7: Assembly ──────────────────────────────────────
        from src.agents.assembly_agent import AssemblyAgent
        video_path = AssemblyAgent(channel_config).assemble_video(final_scenes, words_timing, voice_path, video.id)
        video.file_path = video_path
        video.status = "assembled"
        db_session.commit()
        logger.info("[Pipeline] Phase 7 (Assembly) complete. Final video saved at %s", video_path)

        # ── Phase 8: SEO ───────────────────────────────────────────
        from src.agents.seo_agent import SEOAgent
        metadata = SEOAgent(llm_client, db_session).generate_metadata(script_text, primary_topic_text, channel_config)
        video.title = metadata.get("title", "")
        
        # Generate chapters for description
        chapters_text = "\n\nChapters:\n00:00 Cold Open\n"
        for s in final_scenes:
            if "chapter_title" in s:
                start_s = int(s["start_time"])
                mins, secs = divmod(start_s, 60)
                chapters_text += f"{mins:02d}:{secs:02d} {s['chapter_title'].title()}\n"
            elif s.get("is_closing"):
                start_s = int(s["start_time"])
                mins, secs = divmod(start_s, 60)
                chapters_text += f"{mins:02d}:{secs:02d} Final Thoughts\n"

        video.description = metadata.get("description", "") + chapters_text
        video.tags_json = json.dumps(metadata.get("tags", []))
        
        # ── Phase 7: Thumbnail (Runs after SEO since it needs title) ──
        from src.agents.thumbnail_agent import ThumbnailAgent
        thumbnail_path = ThumbnailAgent(channel_config, llm_client).generate_thumbnail(video_path, video.title, video.id)
        video.thumbnail_path = thumbnail_path

        video.status = "metadata_ready"
        db_session.commit()
        logger.info("[Pipeline] Phase 6 (SEO & Thumbnail) complete.")

        # ── Phase 8: Upload ────────────────────────────────────────
        if not dry_run:
            from src.agents.upload_agent import UploadAgent
            try:
                youtube_video_id = UploadAgent(channel_config, db_session=db_session, llm_client=llm_client).upload_video(
                    video_path=video.file_path,
                    title=video.title,
                    description=video.description,
                    tags=json.loads(video.tags_json) if video.tags_json else [],
                    thumbnail_path=video.thumbnail_path,
                    publish_time_str=publish_time_str,
                    dry_run=dry_run,
                    script_text=video.script_text
                )
                video.youtube_video_id = youtube_video_id
                video.status = "uploaded"
                video.upload_time = datetime.utcnow()
                db_session.commit()
                logger.info("[Pipeline] Phase 8 (Upload) complete. Video ID: %s", youtube_video_id)
                
                # ── Cleanup Cache ──────────────────────────────────────────
                import glob
                import os
                import shutil
                for f in glob.glob("data/cache/*"):
                    if not f.endswith(".gitkeep"):
                        try:
                            if os.path.isdir(f):
                                shutil.rmtree(f)
                            else:
                                os.remove(f)
                        except Exception as e:
                            logger.warning("Failed to delete cache file %s: %s", f, e)
                logger.info("[Pipeline] Cleaned up data/cache/ to save space after successful upload.")

            except Exception as e:
                logger.error("[Pipeline] Upload failed: %s", e)
                video.status = "failed"
                db_session.commit()
        else:
            logger.info("[Pipeline] Phase 8 (Upload) skipped due to dry_run=True.")

    except Exception as exc:
        logger.error("[Pipeline] Pipeline failed for '%s': %s", primary_topic_text, exc, exc_info=True)
        video.status = "failed"
        db_session.commit()
        result["status"] = "failed"
        result["error"] = str(exc)

    return result
