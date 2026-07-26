"""
Visual Planner Agent

Role:
Breaks down the generated script into a list of scenes/shots.
Estimates the duration of each scene, and provides a search query and description for the Visual Director.

Inputs:
- final script text

Outputs:
- JSON list of scenes (scene_number, text_segment, search_query, description)
"""

import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

VISUAL_PLANNER_PROMPT = """\
You are an expert video director for long-form YouTube videos. Your job is to break down the provided script into distinct visual scenes.
Based on the natural speaking pace, aim for each scene to cover roughly {scene_target_seconds} seconds of speech. Maintain a measured, engaging pace suitable for long-form storytelling.

For each scene, provide:
- "scene_number": The sequential number (1, 2, 3...)
- "text_segment": The exact sentence(s) from the script covered in this scene. Every word of the script must be included exactly once across all scenes.
- "search_query": A short 2-4 word query to search stock video sites (e.g. Pexels/Pixabay) that perfectly matches the text. (e.g., "hacker typing", "robot factory", "abstract data"). Do NOT use abstract words like "concept" or "AI", use literal visual descriptions.
- "description": A brief description of what the visual should convey, to be used to score the footage relevance later.

The output MUST be a JSON list of objects:
[
  {
    "scene_number": 1,
    "text_segment": "Imagine coding without actually coding!",
    "search_query": "typing fast computer",
    "description": "Person typing rapidly on a keyboard, illuminated by screen light."
  },
  ...
]
"""

class VisualPlannerAgent:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def _validate_reconstruction(self, script_text: str, scenes: List[Dict[str, Any]]) -> bool:
        import string
        def clean(text: str) -> str:
            return text.translate(str.maketrans('', '', string.punctuation)).lower().split()
        
        orig_words = clean(script_text)
        reconstructed = []
        for s in scenes:
            reconstructed.extend(clean(s.get("text_segment", "")))
            
        if not orig_words: return True
        ratio = len(reconstructed) / len(orig_words)
        return 0.9 <= ratio <= 1.1

    def plan_visuals(self, script_text: str, channel_config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Generate a shot list for the given script with a retry loop on failure.
        """
        logger.info("[VisualPlannerAgent] Planning visuals for script...")
        user_prompt = f"Break down this script into scenes:\n\n{script_text}"
        
        target_seconds = 15.0
        if channel_config:
            target_seconds = channel_config.get("long_form", {}).get("scene_target_seconds", 15.0)
            
        system_prompt = VISUAL_PLANNER_PROMPT.replace("{scene_target_seconds}", str(target_seconds))

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                scenes_json = self.llm_client.generate_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.7,
                    max_tokens=4000
                )
                
                # If the LLM returned a dict with a key instead of a list, extract it.
                if isinstance(scenes_json, dict):
                    for k, v in scenes_json.items():
                        if isinstance(v, list):
                            scenes_json = v
                            break
                
                if not isinstance(scenes_json, list):
                    raise ValueError(f"LLM did not return a list of scenes. Got: {type(scenes_json)}")
                    
                if not self._validate_reconstruction(script_text, scenes_json):
                    if attempt < max_attempts:
                        logger.warning(f"[VisualPlannerAgent] Validation failed on attempt {attempt}. Retrying...")
                        continue
                    else:
                        logger.warning("[VisualPlannerAgent] Validation failed on final attempt. Proceeding anyway.")
                    
                if len(scenes_json) < 10 and len(script_text.split()) > 100:
                    logger.warning("[VisualPlannerAgent] LLM returned only %d scenes for a long script. It may have ignored the short-scene instruction.", len(scenes_json))
                    
                logger.info("[VisualPlannerAgent] Successfully planned %d scenes.", len(scenes_json))
                return scenes_json
            except Exception as exc:
                if attempt < max_attempts:
                    logger.warning(f"[VisualPlannerAgent] Attempt {attempt} failed: {exc}. Retrying...")
                else:
                    logger.error("[VisualPlannerAgent] Visual planning failed on final attempt: %s", exc)
                    raise
