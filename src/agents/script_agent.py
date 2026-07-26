"""
Script Agent

Role:
Generates the script for a selected topic based on channel tone and target length.
Pulls best-performing prompt versions for the channel to ensure continuous improvement.

Inputs:
- selected topic
- channel tone
- target length

Outputs:
- Generated script (Hook -> Core content -> Payoff -> CTA).
- 2-3 alternative hook lines.
"""

import json
import logging
from typing import Dict, Any

from src.db.models import PromptVersion

logger = logging.getLogger(__name__)

# Default prompt for script generation if none exists in the DB
DEFAULT_SCRIPT_PROMPT = """\
You are an expert YouTube scriptwriter. Your goal is to write a highly engaging mini-story based on the provided topic.

The script MUST follow this narrative mini-story arc structure:
1. Hook (first 2-3 seconds): Grab attention immediately with a setup.
2. Core Content/Tension: Deliver the main information and build tension/complication.
3. Payoff/Resolution: A surprising fact, resolution, or original insight.

Your constraints:
- Tone: {tone}
- Commentary Style: {commentary_style}
- Target length: ~{target_length_seconds} seconds (approx {word_count} words).
- Include brief commentary/original opinion, not just a dry summary.
- The output MUST be a JSON object containing:
  {{
    "hook": "The main hook line",
    "alternative_hooks": ["Alt hook 1", "Alt hook 2"],
    "core_content": "The main body of the script...",
    "payoff": "The twist or conclusion...",
    "full_script": "The complete script text combining the hook, core, and payoff"
  }}
"""

class ScriptAgent:
    def __init__(self, llm_client, db_session):
        self.llm_client = llm_client
        self.db_session = db_session

    def _get_performance_addendum(self, channel_id: int) -> str:
        """
        Fetches the latest EvaluationAgent-generated improvement instruction for this agent.
        Returns an empty string if none exists.
        This is appended to the base prompt so the JSON schema is never broken.
        """
        prompt_version = (
            self.db_session.query(PromptVersion)
            .filter(
                PromptVersion.channel_id == channel_id,
                PromptVersion.agent_name == "script_agent_longform",
            )
            .order_by(PromptVersion.created_at.desc())
            .first()
        )
        if prompt_version and prompt_version.prompt_text:
            logger.info(
                "[ScriptAgent] Found performance addendum for channel_id=%s (v%d).",
                channel_id,
                prompt_version.version_number,
            )
            return (
                "\n\nIMPORTANT — PERFORMANCE LEARNINGS FROM PAST VIDEOS:\n"
                + prompt_version.prompt_text
                + "\nApply these learnings while keeping the JSON output format exactly as specified above."
            )
            
        logger.info("[ScriptAgent] No performance addendum found for channel_id=%s.", channel_id)
        return ""

    def generate_script(self, topic: Dict[str, Any], channel_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate the script for the given topic.
        """
        logger.info("[ScriptAgent] Generating script for topic: '%s'", topic.get("topic_text"))
        channel_id = topic.get("channel_id")
        
        # Load constraints
        tone = channel_config.get("tone", "energetic and fast-paced")
        brand = channel_config.get("brand", {})
        commentary_style = brand.get("commentary_style", "insightful and thought-provoking")
        target_length = channel_config.get("long_form", {}).get("target_length_seconds", 560)
        stories_per_video = channel_config.get("long_form", {}).get("stories_per_video", 5)
        # Allocate time per story, leaving some buffer for cold open and bridges
        target_length = int((target_length * 0.85) / stories_per_video)
        
        # 150 words is roughly 1 minute of fast-paced speech
        word_count = int(150 * (target_length / 60.0))
        
        # Prepare prompts — always start from the canonical base prompt,
        # then append any EvaluationAgent-derived performance learnings.
        addendum = self._get_performance_addendum(channel_id)
        system_prompt_template = DEFAULT_SCRIPT_PROMPT + addendum
        system_prompt = system_prompt_template.format(
            tone=tone,
            commentary_style=commentary_style,
            target_length_seconds=target_length,
            word_count=word_count
        )
        
        user_prompt = f"Write a script for the topic: {topic.get('topic_text')}\n"
        if topic.get("description"):
            user_prompt += f"Background context:\n{topic['description']}\n"
            
        try:
            # Generate via LLM Client (using JSON mode)
            script_json = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7, # A bit of creativity
                max_tokens=1500
            )
            logger.info("[ScriptAgent] Script generated successfully.")
            return script_json
        except Exception as exc:
            logger.error("[ScriptAgent] Script generation failed: %s", exc)
            raise
