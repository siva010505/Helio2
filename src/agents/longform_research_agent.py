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

Below is a list of the channel's top-performing Short-form videos, sorted by their ability to retain viewers (Average View Percentage and Average View Duration).

HIGH-RETENTION SHORTS DATA:
{shorts_data}

Your task:
1. Analyze the PATTERNS of these high-retention shorts. What themes, mysteries, psychological hooks, or emotional triggers kept viewers watching?
2. Brainstorm 15 highly specific, narrative-rich long-form candidate topics that perfectly leverage these successful patterns.
3. CRITICAL RULE: Do NOT generate topics that are exactly the same as the Shorts data provided. You must generate COMPLETELY NEW stories, cases, or incidents that share the same *underlying pattern* but are entirely different topics.

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
    llm_client=None,
) -> list[dict]:
    from difflib import SequenceMatcher

    def is_too_similar(a: str, b: str, threshold: float = 0.7) -> bool:
        a_lower = a.lower()
        b_lower = b.lower()
        if a_lower in b_lower or b_lower in a_lower:
            return True
        return SequenceMatcher(None, a_lower, b_lower).ratio() >= threshold

    accepted_titles: list[str] = []
    unique_pass_1 = []

    # Pass 1: Fast string matching
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
        unique_pass_1.append(c)

    # Pass 2: Semantic AI Matching
    if not llm_client or not existing_topics or not unique_pass_1:
        return unique_pass_1

    try:
        logger.info("[LongformResearchAgent] Running Semantic AI deduplication on %d candidates...", len(unique_pass_1))
        system_prompt = (
            "You are a strict deduplication engine. You will be given a list of PAST video topics "
            "and a list of NEW candidate topics.\n\n"
            "Your job is to identify if any NEW candidate describes the EXACT SAME historical event, "
            "person, or specific subject matter as any of the PAST topics, regardless of what the title is.\n\n"
            "Output ONLY a JSON array of the exact titles from the NEW candidates list that are semantic duplicates. "
            "If none are duplicates, output an empty array [].\n"
            'Format: {"duplicates": ["Title 1", "Title 2"]}'
        )
        
        past_str = "\n".join(f"- {t}" for t in existing_topics)
        new_str = "\n".join(f"- {c['title']}: {c.get('description', '')}" for c in unique_pass_1)
        
        user_prompt = f"PAST TOPICS:\n{past_str}\n\nNEW CANDIDATES:\n{new_str}"
        
        response = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=500
        )
        
        semantic_duplicates = set(response.get("duplicates", []))
        if semantic_duplicates:
            logger.info("[LongformResearchAgent] Semantic AI flagged duplicates: %s", semantic_duplicates)
            
        unique_pass_2 = []
        for c in unique_pass_1:
            if c["title"] not in semantic_duplicates:
                unique_pass_2.append(c)
                
        return unique_pass_2
        
    except Exception as exc:
        logger.error("[LongformResearchAgent] Semantic deduplication failed, falling back to Pass 1 results: %s", exc)
        return unique_pass_1


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

    def _fetch_shorts_patterns(self) -> str:
        """Fetch top performing shorts from the last 30 days to use as pattern analysis data."""
        if not self.ShortsSession:
            return "No Shorts data available."
            
        session = self.ShortsSession()
        try:
            from sqlalchemy import text
            query = text("""
                SELECT 
                    topics.topic_text, 
                    videos.description, 
                    performance_metrics.views, 
                    performance_metrics.average_view_duration,
                    performance_metrics.average_view_percentage
                FROM videos
                JOIN performance_metrics ON videos.id = performance_metrics.video_id
                JOIN topics ON videos.topic_id = topics.id
                WHERE videos.upload_time >= datetime('now', '-30 days')
                  AND videos.status = 'uploaded'
                ORDER BY performance_metrics.average_view_percentage DESC, performance_metrics.average_view_duration DESC, performance_metrics.views DESC
                LIMIT 20
            """)
            
            results = session.execute(query).fetchall()
            
            lines = []
            for row in results:
                lines.append(
                    f"- Topic: {row.topic_text}\n"
                    f"  Description: {row.description}\n"
                    f"  Metrics: AVP={row.average_view_percentage}%, AVD={row.average_view_duration}s, Views={row.views}\n"
                )
            return "\n".join(lines) if lines else "No recent Shorts data available."
        except Exception as e:
            logger.error(f"[LongformResearchAgent] Error querying Shorts DB: {e}")
            return "No Shorts data available due to DB error."
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

        # 1. Fetch Shorts Data for Pattern Analysis
        shorts_data_str = self._fetch_shorts_patterns()

        # 2. Brainstorm via LLM
        try:
            system_prompt = BRAINSTORM_PROMPT.format(niche=niche, shorts_data=shorts_data_str)
            user_prompt = "Analyze the data and generate the JSON response with 15 completely new candidates now."
            
            response = self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.8,
                max_tokens=2000
            )
            llm_candidates = response.get("candidates", [])
            for raw in llm_candidates:
                raw["source"] = "llm_pattern_analysis"
        except Exception as exc:
            logger.error("[LongformResearchAgent] LLM pattern brainstorming failed: %s", exc)
            llm_candidates = []

        all_raw = llm_candidates

        # 3. Deduplicate
        seen_titles: set[str] = set()
        unique = _deduplicate(all_raw, existing_topics, seen_titles, llm_client=self.llm)
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
