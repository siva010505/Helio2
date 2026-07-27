"""
Compilation Assembler Agent

Role:
Assembles a complete long-form compilation script by generating individual scripts for 
selected stories and wrapping them with a cold open, bridge lines, and closing recap.
"""

import json
import logging
from typing import Dict, Any, List

from src.agents.script_agent import ScriptAgent

logger = logging.getLogger(__name__)

COMPILATION_PROMPT = """\
You are an expert YouTube compilation writer. You are provided with a list of generated mini-stories.
Your task is to write the connective tissue that weaves them into a single cohesive long-form video.

You must write:
1. Cold Open (15-25 seconds): Preview all the stories to hook the viewer. Save the strongest story for last ("...and story #X is the one investigators still can't explain"). Do not include channel intro here.
2. Bridge Lines: For each transition between consecutive stories, write 1-2 sentences that close the previous story's loop and open the next one.
3. Closing Recap + CTA (20-30 seconds): A brief recap, ask to subscribe, and a pointer back to related Shorts.
4. Chapter Titles: Generate a highly engaging 2-3 word title for each story (e.g. "THE DANCING PLAGUE" or "THE MIRROR TRICK").

Topics and their generated scripts:
{stories_json}

The output MUST be a JSON object containing:
{{
  "cold_open": "Cold open script...",
  "bridges": ["Bridge from 1 to 2", "Bridge from 2 to 3", "..."],
  "closing": "Closing script...",
  "chapter_titles": ["Title 1", "Title 2"]
}}
"""

class CompilationAssemblerAgent:
    def __init__(self, llm_client, db_session):
        self.llm_client = llm_client
        self.db_session = db_session
        self.script_agent = ScriptAgent(llm_client, db_session)

    def assemble_compilation(self, topics: List[Dict[str, Any]], channel_config: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[CompilationAssemblerAgent] Assembling compilation for {len(topics)} topics.")
        
        stories = []
        for i, topic in enumerate(topics):
            # Generate script for this story
            story_script = self.script_agent.generate_script(topic, channel_config)
            stories.append({
                "topic": topic.get("topic_text"),
                "script": story_script.get("full_script", "")
            })
            
        system_prompt = COMPILATION_PROMPT.format(
            stories_json=json.dumps(stories, indent=2)
        )
        
        try:
            # Generate connective tissue
            connective_tissue = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt="Generate the connective tissue (cold open, bridges, closing) now.",
                temperature=0.7,
                max_tokens=1500
            )
        except Exception as exc:
            logger.error("[CompilationAssemblerAgent] Failed to generate connective tissue: %s", exc)
            raise

        # Assemble final script
        final_script = connective_tissue.get("cold_open", "") + "\n\n"
        chapter_titles = connective_tissue.get("chapter_titles", [])
        
        for i, story in enumerate(stories):
            title = chapter_titles[i] if i < len(chapter_titles) else f"STORY {i+1}"
            final_script += f"--- STORY {i+1}: {title} ---\n"
            final_script += story["script"] + "\n\n"
            
            if i < len(stories) - 1:
                bridges = connective_tissue.get("bridges", [])
                bridge = bridges[i] if i < len(bridges) else ""
                final_script += f"--- BRIDGE ---\n{bridge}\n\n"

        final_script += "--- CLOSING ---\n"
        final_script += connective_tissue.get("closing", "")
        
        # Validate spoken duration
        target_total_length = channel_config.get("long_form", {}).get("target_total_length_seconds", 560)
        word_count = len(final_script.split())
        estimated_duration = word_count / 2.5 # roughly 2.5 words per sec for average pace
        
        margin = target_total_length * 0.15
        if abs(estimated_duration - target_total_length) > margin:
            logger.warning(
                f"[CompilationAssemblerAgent] Warning: Estimated duration {estimated_duration:.1f}s "
                f"is outside ±15%% of target {target_total_length}s."
            )
            
        return {
            "stories": stories,
            "connective_tissue": connective_tissue,
            "full_script": final_script,
            "estimated_duration": estimated_duration
        }
