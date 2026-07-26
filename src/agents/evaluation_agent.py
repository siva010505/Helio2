"""
Evaluation Agent

Role:
Reads mature performance metrics from the DB, sends them to the LLM to find
correlations and actionable insights, then writes improved prompt versions
back to the prompt_versions table so future runs benefit from the learnings.

Self-Correction Logic:
  - Compares topic_type / hook_style / script_length against AVD, CTR, Views.
  - Asks the LLM: "What patterns explain high vs low performance?"
  - Produces a concrete instruction delta (e.g., "Prefer fast-paced hooks.
    Avoid passive intros. Favour tech-news topics over educational ones.").
  - Saves that as a new PromptVersion row for every affected agent.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from src.db.models import Video, PerformanceMetric, PromptVersion

logger = logging.getLogger(__name__)

# Agents whose prompts can be dynamically improved
IMPROVABLE_AGENTS = ["script_agent_longform", "seo_agent_longform", "scoring_agent_longform"]

ANALYSIS_SYSTEM_PROMPT = """\
You are a senior YouTube analytics strategist and data scientist.
You will receive a dataset of YouTube Shorts performance records.
Each record contains:
  - topic_text: the subject of the video
  - hook_style: the opening hook type (question/stat/story/bold_claim)
  - upload_day: day of the week it was posted
  - views, ctr (click-through rate), average_view_duration, average_view_percentage

CRITICAL INSTRUCTION:
Raw view counts are heavily influenced by the YouTube algorithm, luck, and upload timing. 
You MUST prioritize Average View Percentage (Retention) and Average View Duration as the true indicators of script quality. A video with low views but 90%+ retention is a massive success. Look for consistent patterns across the cohort rather than isolated hits.

Your job:
1. Identify the top 2-3 patterns that explain HIGH performance (High Retention, High AVD).
2. Identify the top 2-3 patterns that explain LOW performance.
3. For each of the following agents, output a concrete, actionable instruction update
   (a SHORT paragraph) that will improve future video performance:
   - script_agent: how should the script / hook be written differently?
   - seo_agent: what title / description patterns drive higher CTR?
   - scoring_agent: which topic or content characteristics should be scored higher?

Respond ONLY with a valid JSON object in this exact schema:
{
  "summary": "<2-3 sentence overall insight>",
  "agent_updates": {
    "script_agent": "<instruction update paragraph>",
    "seo_agent": "<instruction update paragraph>",
    "scoring_agent": "<instruction update paragraph>"
  }
}
"""


class EvaluationAgent:
    def __init__(self, llm_client, db_session, config: Dict[str, Any]):
        self.llm = llm_client
        self.db = db_session
        self.config = config

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def _load_mature_metrics(self) -> List[Dict]:
        """
        Loads all performance metrics that are from videos which have
        already passed the 72-hour maturity window.
        Only includes records where we have at least views > 0.
        """
        maturity_days = self.config.get("long_form", {}).get("maturity_days", 7)
        cutoff = datetime.utcnow() - timedelta(days=maturity_days)
        
        records = (
            self.db.query(PerformanceMetric, Video)
            .join(Video, PerformanceMetric.video_id == Video.id)
            .filter(
                PerformanceMetric.views > 0,
                Video.youtube_video_id.isnot(None),
                Video.upload_time <= cutoff,
            )
            .order_by(PerformanceMetric.pulled_at.desc())
            .limit(30)   # cap context size for LLM
            .all()
        )

        data = []
        for metric, video in records:
            data.append({
                "video_id": video.id,
                "topic_text": video.title or "unknown",
                "hook_style": video.hook_style or "unknown",
                "upload_day": video.upload_time.strftime("%A") if video.upload_time else "unknown",
                "views": metric.views,
                "ctr": metric.ctr,
                "average_view_duration": metric.average_view_duration,
                "average_view_percentage": metric.average_view_percentage,
            })

        logger.info("[EvaluationAgent] Loaded %d mature metric records for analysis.", len(data))
        return data

    # ------------------------------------------------------------------
    # Prompt versioning helpers
    # ------------------------------------------------------------------

    def _latest_version_number(self, channel_id: int, agent_name: str) -> int:
        latest = (
            self.db.query(PromptVersion)
            .filter(
                PromptVersion.channel_id == channel_id,
                PromptVersion.agent_name == agent_name,
            )
            .order_by(PromptVersion.version_number.desc())
            .first()
        )
        return latest.version_number if latest else 0

    def _save_prompt_version(
        self,
        channel_id: int,
        agent_name: str,
        prompt_text: str,
        performance_summary: Dict,
    ):
        version = self._latest_version_number(channel_id, agent_name) + 1
        pv = PromptVersion(
            channel_id=channel_id,
            agent_name=agent_name,
            version_number=version,
            prompt_text=prompt_text,
            created_at=datetime.utcnow(),
            performance_summary_json=json.dumps(performance_summary),
        )
        self.db.add(pv)
        self.db.commit()
        logger.info(
            "[EvaluationAgent] Saved PromptVersion v%d for %s.", version, agent_name
        )

    # ------------------------------------------------------------------
    # Core analysis + self-correction
    # ------------------------------------------------------------------

    def run_evaluation(self) -> Dict[str, Any]:
        """
        Main entrypoint.
        1. Loads mature metrics.
        2. Sends to LLM for correlation analysis.
        3. Writes improved prompt versions to DB.

        Returns the raw LLM analysis dict.
        """
        metrics_data = self._load_mature_metrics()

        min_records = self.config.get("learning", {}).get("min_videos_before_adjusting", 5)
        record_count = len(metrics_data)

        if record_count < min_records:
            logger.info(
                "[EvaluationAgent] Insufficient data (%d records). "
                "Need at least %d mature metrics. Skipping.",
                record_count,
                min_records,
            )
            return {"status": "skipped", "reason": "insufficient_data"}
            
        current_epoch = record_count // min_records
        
        # Check if we already evaluated this epoch for any channel/agent.
        latest_pv = self.db.query(PromptVersion).order_by(PromptVersion.version_number.desc()).first()
        last_epoch = 0
        if latest_pv and latest_pv.performance_summary_json:
            try:
                summary_data = json.loads(latest_pv.performance_summary_json)
                last_epoch = summary_data.get("epoch", 0)
            except:
                pass
                
        if current_epoch <= last_epoch:
            logger.info(
                "[EvaluationAgent] Current epoch (%d) has already been evaluated (last_epoch=%d). "
                "Waiting for more mature videos before re-evaluating. Skipping.",
                current_epoch, last_epoch
            )
            return {"status": "skipped", "reason": "epoch_already_evaluated"}

        # Compose the analysis prompt
        user_prompt = (
            "Here is the performance dataset (JSON):\n\n"
            + json.dumps(metrics_data, indent=2)
            + "\n\nPerform your analysis and return the JSON response."
        )

        logger.info("[EvaluationAgent] Sending %d records to LLM for analysis.", len(metrics_data))

        try:
            analysis = self.llm.generate_json(
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,   # Low temp for deterministic analysis
                max_tokens=1500,
            )
        except Exception as exc:
            logger.error("[EvaluationAgent] LLM analysis failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

        logger.info("[EvaluationAgent] Analysis complete. Summary: %s", analysis.get("summary"))

        # Extract agent-specific updates and persist as new PromptVersions
        agent_updates = analysis.get("agent_updates", {})
        performance_summary = {
            "summary": analysis.get("summary"), 
            "record_count": record_count,
            "epoch": current_epoch
        }

        # Apply to every configured channel
        channels = self.config.get("channels", [])
        for channel in channels:
            # Fetch channel DB id
            channel_id = channel.get("db_id")
            if channel_id is None:
                raise ValueError(f"[EvaluationAgent] Missing 'db_id' for channel '{channel.get('name')}'.")

            for agent_name in IMPROVABLE_AGENTS:
                update_text = agent_updates.get(agent_name)
                if update_text:
                    self._save_prompt_version(
                        channel_id=channel_id,
                        agent_name=agent_name,
                        prompt_text=update_text,
                        performance_summary=performance_summary,
                    )

        return {"status": "success", "analysis": analysis}
