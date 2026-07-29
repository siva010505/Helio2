"""
Longform Research Agent

Role:
Discovers candidate topics for the long-form channel by:
1. Querying the original Helio (Shorts) database for top-performing recent videos.
2. Supplementing with LLM brainstorming if needed.

Persists to Helio2 database.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Topic, Channel, Video, PerformanceMetric

logger = logging.getLogger(__name__)

DEDUP_LOOKBACK_DAYS = 30
MAX_CANDIDATES = 15
MIN_CANDIDATES = 3

BRAINSTORM_PROMPT = """\
You are an expert content strategist for a long-form YouTube channel in the following niche:
Niche: {niche}

The channel produces DEEP-DIVE single-topic videos — one video = one complete story.
Format: Cold Hook → Setup → Investigation → Twist → Implication (8–10 minutes each).

Your task: Brainstorm 15 highly specific, narrative-rich topic candidates.

WHAT WORKS (score these highly):
  - Real historical events with a clear arc (e.g., "The Dancing Plague of 1518")
  - Famous documented experiments with shocking outcomes (e.g., "The Stanford Prison Experiment")
  - Real cases involving a named person and an unusual psychological phenomenon
  - Documented mysteries that science has finally explained (or still can't)
  - Moments where mass psychology caused bizarre collective behavior

WHAT DOES NOT WORK (avoid these):
  - Generic concepts without a story ("Why do people procrastinate?")
  - Pure "X facts about Y" listicle topics
  - Topics already massively saturated on YouTube (Milgram is fine, but avoid rehashing obvious angles)
  - Topics requiring no narrative — just a definition and some statistics

For each candidate, the title should be SPECIFIC (include a real name, date, or place if possible).
A good title teases a mystery: "The Man Who Laughed Himself to Death", "The Village That Forgot to Sleep".

Output ONLY a valid JSON object matching this schema:
{{
    "candidates": [
        {{
            "title": "A specific, mystery-forward title under 55 characters",
            "description": "2-3 sentences: the real story arc — what happened, why it's strange, what the twist or revelation is."
        }}
    ]
}}
"""

def _deduplicate(
    candidates: list[dict],
    existing_topics: list[str],
    seen_titles: set,
) -> list[dict]:
    from difflib import SequenceMatcher

    def is_too_similar(a: str, b: str, threshold: float = 0.7) -> bool:
        a_lower = a.lower()
        b_lower = b.lower()
        if a_lower in b_lower or b_lower in a_lower:
            return True
        return SequenceMatcher(None, a_lower, b_lower).ratio() >= threshold

    accepted_titles: list[str] = []
    unique = []

    for c in candidates:
        title = c["title"].strip()
        if not title:
            continue
        if title in seen_titles:
            continue
        if any(is_too_similar(title, existing) for existing in existing_topics):
            continue
        if any(is_too_similar(title, accepted) for accepted in accepted_titles):
            continue
        seen_titles.add(title)
        accepted_titles.append(title)
        unique.append(c)

    return unique


class LongformResearchAgent:
    def __init__(self, db_session, llm_client, config):
        self.db = db_session
        self.llm = llm_client
        self.config = config
        
        # Setup read-only connection to Helio Shorts DB
        shorts_db_path = self.config.get("long_form", {}).get("helio_shorts_db_path", "../Helio/data/agent.db")
        # Support relative paths based on current working directory
        if not shorts_db_path.startswith("sqlite:///"):
            shorts_db_path = f"sqlite:///{shorts_db_path}"
        
        try:
            self.shorts_engine = create_engine(shorts_db_path, connect_args={"check_same_thread": False})
            self.ShortsSession = sessionmaker(bind=self.shorts_engine)
        except Exception as e:
            logger.error(f"[LongformResearchAgent] Failed to connect to Shorts DB: {e}")
            self.ShortsSession = None

    def _fetch_shorts_seeds(self) -> list[dict]:
        """Fetch top performing shorts from the last 30 days as long-form seeds."""
        if not self.ShortsSession:
            return []
            
        session = self.ShortsSession()
        try:
            from sqlalchemy import text
            query = text("""
                SELECT 
                    topics.topic_text, 
                    videos.description, 
                    performance_metrics.views, 
                    performance_metrics.likes, 
                    performance_metrics.average_view_percentage
                FROM videos
                JOIN performance_metrics ON videos.id = performance_metrics.video_id
                JOIN topics ON videos.topic_id = topics.id
                WHERE videos.upload_time >= datetime('now', '-30 days')
                  AND videos.status = 'uploaded'
                ORDER BY performance_metrics.views DESC
                LIMIT 10
            """)
            
            results = session.execute(query).fetchall()
            
            seeds = []
            for row in results:
                seeds.append({
                    "title": row.topic_text,
                    "description": row.description or "",
                    "source": "shorts_seed",
                    "original_context": {
                        "original_topic": row.topic_text,
                        "original_description": row.description,
                        "metrics": {
                            "views": row.views,
                            "likes": row.likes,
                            "avp": row.average_view_percentage
                        }
                    }
                })
            return seeds
        except Exception as e:
            logger.error(f"[LongformResearchAgent] Error querying Shorts DB: {e}")
            return []
        finally:
            session.close()

    def fetch_candidate_topics(
        self,
        channel_config: dict,
        channel_id: int,
    ) -> list[dict]:
        niche = channel_config.get("niche", "")
        logger.info("[LongformResearchAgent] Sourcing for niche: %s", niche)

        lookback = datetime.utcnow() - timedelta(days=DEDUP_LOOKBACK_DAYS)
        existing_topics: list[str] = [
            row.topic_text
            for row in self.db.query(Topic)
            .filter(
                Topic.channel_id == channel_id,
                Topic.created_at >= lookback,
                Topic.status.in_(["selected", "used"]),
            )
            .all()
        ]

        # 1. Fetch from Shorts DB
        shorts_candidates = self._fetch_shorts_seeds()
        logger.info(f"[LongformResearchAgent] Found {len(shorts_candidates)} seed candidates from Shorts DB.")

        # 2. Brainstorm via LLM
        try:
            system_prompt = BRAINSTORM_PROMPT.format(niche=niche)
            user_prompt = "Generate the JSON response with 15 candidates now."
            
            response = self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.8,
                max_tokens=1500
            )
            llm_candidates = response.get("candidates", [])
            for raw in llm_candidates:
                raw["source"] = "llm_brainstorm"
        except Exception as exc:
            logger.error("[LongformResearchAgent] LLM brainstorming failed: %s", exc)
            llm_candidates = []

        all_raw = shorts_candidates + llm_candidates

        # 3. Deduplicate
        seen_titles: set[str] = set()
        unique = _deduplicate(all_raw, existing_topics, seen_titles)
        unique = unique[:MAX_CANDIDATES]

        logger.info("[LongformResearchAgent] Unique candidates after dedup: %d", len(unique))

        # 4. Persist to DB
        persisted: list[dict] = []
        for item in unique:
            topic_row = Topic(
                channel_id=channel_id,
                topic_text=item["title"],
                source=item["source"],
                source_context_json=json.dumps(item.get("original_context", {})) if "original_context" in item else None,
                status="candidate",
            )
            self.db.add(topic_row)
            self.db.flush()

            persisted.append(
                {
                    "db_id": topic_row.id,
                    "channel_id": channel_id,
                    "topic_text": item["title"],
                    "description": item.get("description", ""),
                    "source": item["source"],
                }
            )

        self.db.commit()
        return persisted
