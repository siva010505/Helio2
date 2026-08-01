"""
SEO Agent

Role:
Generates highly optimized metadata (Title, Description, Tags) for the final video.
Uses the LLM to analyze the generated script and output YouTube-friendly SEO data.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

SEO_PROMPT = """\
You are an expert YouTube long-form documentary strategist.
Your goal is to generate highly clickable, dramatic, true-crime/mystery style metadata for the following deep-dive video.

Constraints:
1. Title: Create a high-CTR, dramatic title. Do NOT use emojis. It should sound like a premium documentary or a compelling true story.
   Examples of good titles:
   - The Man Who Killed While Sleepwalking (True Story)
   - He Committed Murder... While Asleep
   - The Sleepwalking Murder That Shocked Psychologists
   - Can You Kill Someone While Sleepwalking?
2. Description: A brief 2-3 sentence summary of the video, followed by 3-5 relevant hashtags.
3. Tags: A list of 5-8 highly relevant comma-separated tags for the YouTube algorithm.

Output your response strictly as a JSON object:
{
    "title": "Engaging Title Here",
    "description": "Engaging description with #hashtags",
    "tags": ["tag1", "tag2", "tag3"]
}
"""

class SEOAgent:
    def __init__(self, llm_client, db_session=None):
        self.llm_client = llm_client
        self.db_session = db_session

    def _get_performance_addendum(self, channel_id: int) -> str:
        """Fetch EvaluationAgent's latest seo_agent guidance, if any."""
        if not self.db_session:
            return ""
        from src.db.models import PromptVersion
        pv = (
            self.db_session.query(PromptVersion)
            .filter(
                PromptVersion.channel_id == channel_id,
                PromptVersion.agent_name == "seo_agent",
            )
            .order_by(PromptVersion.created_at.desc())
            .first()
        )
        if pv and pv.prompt_text:
            logger.info("[SEOAgent] Applying performance addendum from v%d.", pv.version_number)
            return (
                "\n\nIMPORTANT — PERFORMANCE LEARNINGS FROM PAST VIDEOS:\n"
                + pv.prompt_text
                + "\nApply these learnings. Keep the JSON output format exactly as specified."
            )
        return ""

    def generate_metadata(self, script_text: str, topic_text: str, channel_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate title, description, and tags using the LLM.
        """
        logger.info("[SEOAgent] Generating metadata for topic: '%s'", topic_text)

        channel_id = channel_config.get("db_id")
        if channel_id is None:
            raise ValueError("[SEOAgent] Missing 'db_id' in channel_config. Channel ID must be properly propagated.")
        addendum = self._get_performance_addendum(channel_id)
        system_prompt = SEO_PROMPT + addendum

        user_prompt = f"Topic: {topic_text}\n\nVideo Script:\n{script_text}\n\nNiche: {channel_config.get('niche')}"

        try:
            metadata = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=500
            )
            logger.info("[SEOAgent] Successfully generated metadata.")
            return metadata
        except Exception as exc:
            logger.error("[SEOAgent] Failed to generate metadata: %s", exc)
            raise

