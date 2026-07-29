"""
Scoring & Selection Agent

Role:
Uses the LLM to score candidate topics on four dimensions:
  - novelty        (0-10): Is this genuinely new / not overexposed?
  - virality       (0-10): How shareable / emotionally triggering is this?
  - hook_potential (0-10): How easily can we open with a killer hook?
  - freshness      (0-10): Is it timely / has news broken recently?

Computes a weighted composite score, selects the top `videos_per_day` candidates,
updates their status to "selected" and stores the score breakdown in the DB.

Inputs:
- channel_config (dict): Channel config block from config.yaml.
- channel_id (int): DB primary key of the channel row.
- candidates (list[dict]): Output from ResearchAgent.fetch_candidate_topics().
- videos_per_day (int): How many topics to select.

Outputs:
- List[dict]: Selected topic dicts with scores and db_id.
- Side-effect: Updates Topic rows in DB (status → "selected" / "rejected",
  score = composite score, score_breakdown_json = JSON breakdown).
"""

import json
import logging
from typing import Any

from src.db.models import Topic

logger = logging.getLogger(__name__)

# Weights for the composite score (must sum to 1.0)
# narrative_strength is weighted highest — AVD (driven by story arc) is
# YouTube's #1 ranking signal for long-form content.
SCORE_WEIGHTS = {
    "novelty": 0.20,
    "virality": 0.25,
    "hook_potential": 0.25,
    "narrative_strength": 0.30,
}

SCORING_SYSTEM_PROMPT = """\
You are a long-form YouTube content strategist specializing in psychology & human behavior channels.
Your goal is to select the ONE topic that will hold a viewer's attention for 8–10 minutes straight.

The channel produces DEEP-DIVE single-topic videos (NOT compilations or listicles).
Each video follows: Cold Hook → Setup → Investigation → Twist → Implication → CTA.

You will be given a list of topic candidates and a channel niche.
For each topic, score it on four dimensions (each 0-10):

  - novelty (0-10):
    Is this topic genuinely underexplored on YouTube? Not yet a saturated "10 amazing psychology facts" style?
    A niche historical case scores higher than a topic with 500 existing videos.

  - virality (0-10):
    How strongly will this make a viewer feel something — shock, dread, wonder, or empathy?
    Will they share it with "you NEED to watch this"?

  - hook_potential (0-10):
    Can we open COLD with a shocking, mid-action scene? Does the topic have a dramatic
    inciting moment we can drop the viewer into in the first 10 seconds?

  - narrative_strength (0-10):  ← MOST IMPORTANT for long-form retention
    Does this topic have a single clear arc: a real person, real event, or real experiment
    with a beginning, rising tension, twist, and resolution?
    Score 9-10 ONLY if there is a specific documented story (e.g. a named person, a dated event,
    a recorded experiment) — NOT a generic concept like "confirmation bias".
    A topic like "The Dancing Plague of 1518" scores 10. "Why people procrastinate" scores 3.

CRITICAL: Prefer topics with ONE tight narrative over topics that are interesting but diffuse.
A single gripping true story will always outperform a collection of interesting facts.

Be critical. Most topics should score 4-7. Reserve 9-10 for truly exceptional cases.

Respond with a JSON array. Each element maps to a topic (in the same order as the input) and has:
  {
    "topic_text": "<exact topic text>",
    "novelty": <int 0-10>,
    "virality": <int 0-10>,
    "hook_potential": <int 0-10>,
    "narrative_strength": <int 0-10>,
    "reasoning": "<one-sentence justification focusing on narrative arc quality>"
  }
"""


def _build_scoring_prompt(niche: str, candidates: list[dict], history_summary: str) -> str:
    """Build the user prompt for scoring a batch of candidates."""
    lines = [
        f"Channel niche: {niche}",
        f"Channel performance history:\n{history_summary}\n",
        "Candidates to score (JSON array):",
        json.dumps(
            [
                {
                    "topic_text": c["topic_text"],
                    "description": c.get("description", ""),
                    "source": c.get("source", ""),
                }
                for c in candidates
            ],
            indent=2,
        ),
    ]
    return "\n".join(lines)


def _compute_composite(scores: dict) -> float:
    """Compute the weighted composite score from dimension scores."""
    return sum(scores.get(dim, 0) * weight for dim, weight in SCORE_WEIGHTS.items())


def _parse_scores(raw: Any, candidates: list[dict]) -> list[dict]:
    """
    Validate and normalise the LLM's scoring JSON.

    Falls back gracefully if the LLM returns fewer items than expected,
    giving missing topics a composite score of 0.
    """
    scored: list[dict] = []
    if not isinstance(raw, list):
        logger.warning("ScoringAgent: expected JSON array, got %s", type(raw))
        raw = []

    for idx, c in enumerate(candidates):
        item = raw[idx] if idx < len(raw) else {}
        dims = {
            "novelty": int(item.get("novelty", 0)),
            "virality": int(item.get("virality", 0)),
            "hook_potential": int(item.get("hook_potential", 0)),
            "narrative_strength": int(item.get("narrative_strength", 0)),
        }
        # Clamp all dimensions to [0, 10]
        dims = {k: max(0, min(10, v)) for k, v in dims.items()}
        composite = round(_compute_composite(dims), 3)

        scored.append(
            {
                **c,
                "dimensions": dims,
                "composite_score": composite,
                "reasoning": item.get("reasoning", ""),
            }
        )

    return scored


def _get_history_summary(db_session, channel_id: int, limit: int = 5) -> str:
    """
    Build a short natural-language summary of recent video performance
    to give the LLM context for scoring novelty and virality.
    """
    from src.db.models import Video, PerformanceMetric

    recent_videos = (
        db_session.query(Video)
        .filter(Video.channel_id == channel_id, Video.status == "uploaded")
        .order_by(Video.upload_time.desc())
        .limit(limit)
        .all()
    )

    if not recent_videos:
        return "No historical videos yet. This is a new channel."

    lines = [f"Last {len(recent_videos)} uploaded videos (most recent first):"]
    for v in recent_videos:
        metric = (
            db_session.query(PerformanceMetric)
            .filter(PerformanceMetric.video_id == v.id)
            .order_by(PerformanceMetric.pulled_at.desc())
            .first()
        )
        if metric:
            lines.append(
                f'- "{v.title}" | {metric.views} views | '
                f"{metric.average_view_percentage or 0:.0f}% avg retention"
            )
        else:
            lines.append(f'- "{v.title}" | no metrics yet')

    return "\n".join(lines)


class ScoringAgent:
    """
    Scores and selects the best candidate topics for the day.

    Usage:
        agent = ScoringAgent(llm_client, db_session)
        selected = agent.score_and_select(channel_config, channel_id, candidates, videos_per_day=2)
    """

    def __init__(self, llm_client, db_session):
        self.llm = llm_client
        self.db = db_session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_and_select(
        self,
        channel_config: dict,
        channel_id: int,
        candidates: list[dict],
        videos_per_day: int = 2,
    ) -> list[dict]:
        """
        Score all candidates with the LLM and select the top `videos_per_day`.

        Args:
            channel_config: Channel config block from config.yaml.
            channel_id: DB PK of the channel.
            candidates: List of candidate dicts from ResearchAgent.
            videos_per_day: How many to select.

        Returns:
            List of selected topic dicts (sorted descending by composite_score).
        """
        if not candidates:
            logger.warning("[ScoringAgent] No candidates provided — nothing to score.")
            return []

        niche = channel_config.get("niche", "")
        logger.info(
            "[ScoringAgent] Scoring %d candidates for niche: %s",
            len(candidates), niche,
        )

        # ── 1. Build history context ─────────────────────────────────
        history_summary = _get_history_summary(self.db, channel_id)

        # ── 2. LLM scoring call ──────────────────────────────────────
        user_prompt = _build_scoring_prompt(niche, candidates, history_summary)
        try:
            raw_scores = self.llm.generate_json(
                system_prompt=SCORING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,   # Lower temp → more consistent scores
                max_tokens=3000,
            )
        except Exception as exc:
            logger.error("[ScoringAgent] LLM scoring call failed: %s", exc)
            raise

        # ── 3. Parse & validate ──────────────────────────────────────
        scored = _parse_scores(raw_scores, candidates)
        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        # ── 4. Select top N ──────────────────────────────────────────
        selected = scored[:videos_per_day]
        rejected = scored[videos_per_day:]

        logger.info(
            "[ScoringAgent] Selected %d topic(s):",
            len(selected),
        )
        for s in selected:
            logger.info(
                "  ✓ [%.2f] %s — %s",
                s["composite_score"], s["topic_text"], s.get("reasoning", ""),
            )

        # ── 5. Persist scores back to DB ─────────────────────────────
        self._persist_scores(selected, status="selected")
        self._persist_scores(rejected, status="rejected")
        self.db.commit()

        return selected

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist_scores(self, topics: list[dict], status: str) -> None:
        """Update Topic rows in the DB with scores and new status."""
        for t in topics:
            db_row = self.db.query(Topic).filter(Topic.id == t["db_id"]).first()
            if not db_row:
                logger.warning(
                    "[ScoringAgent] Topic id=%s not found in DB — skipping.", t["db_id"]
                )
                continue

            db_row.score = t["composite_score"]
            db_row.status = status
            db_row.score_breakdown_json = json.dumps(
                {
                    "dimensions": t.get("dimensions", {}),
                    "composite": t["composite_score"],
                    "reasoning": t.get("reasoning", ""),
                }
            )
