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
        # ── Phase 3: Script Generation ────────────────────────────
        from src.agents.compilation_assembler_agent import CompilationAssemblerAgent
        compilation_data = CompilationAssemblerAgent(llm_client, db_session).assemble_compilation(topics, channel_config)
        script_text = compilation_data.get("full_script")
        video.script_text = script_text
        video.hook_style = "compilation_cold_open"
        db_session.commit()
        logger.info("[Pipeline] Phase 3 (Compilation Script) complete.")

        # ── Phase 4: Voice Generation ──────────────────────────────
        from src.agents.voice_agent import VoiceAgent
        voice_path = VoiceAgent(channel_config).generate_voice(script_text, channel_config.get('voice', ''), video.id)
        video.voice_used = channel_config.get('voice', '')
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

        logger.info("[Pipeline] Planning visuals for cold open...")
        plan_and_append(compilation_data["connective_tissue"]["cold_open"])
        
        # Track the start indices and titles for each story
        story_starts = []
        chapter_titles = compilation_data["connective_tissue"].get("chapter_titles", [])
        content_labels = compilation_data["connective_tissue"].get("content_labels", [])
        
        for i, story in enumerate(compilation_data["stories"]):
            logger.info("[Pipeline] Planning visuals for story %d...", i+1)
            # Record the index of the first scene of this story
            story_starts.append({
                "scene_idx": len(shot_list),
                "title": chapter_titles[i] if i < len(chapter_titles) else f"Story {i+1}",
                "label": content_labels[i] if i < len(content_labels) else "Story"
            })
            plan_and_append(story["script"])
            if i < len(compilation_data["stories"]) - 1:
                bridges = compilation_data["connective_tissue"].get("bridges", [])
                bridge = bridges[i] if i < len(bridges) else ""
                plan_and_append(bridge)
                
        logger.info("[Pipeline] Planning visuals for closing...")
        closing_scene_idx = len(shot_list)
        plan_and_append(compilation_data["connective_tissue"]["closing"])
        
        for i, s in enumerate(shot_list):
            s["scene_number"] = i + 1
            # Inject chapter markers directly into the correct scene
            for start_data in story_starts:
                if start_data["scene_idx"] == i:
                    s["chapter_title"] = start_data["title"]
                    s["content_label"] = start_data["label"]
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
                youtube_video_id = UploadAgent(channel_config, db_session=db_session).upload_video(
                    video_path=video.file_path,
                    title=video.title,
                    description=video.description,
                    tags=json.loads(video.tags_json) if video.tags_json else [],
                    thumbnail_path=video.thumbnail_path,
                    publish_time_str=publish_time_str,
                    dry_run=dry_run
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
