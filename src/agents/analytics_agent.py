"""
Analytics Agent

Role:
Queries the YouTube Analytics API to pull real performance metrics
for uploaded videos that have reached maturity (>= 72 hours old).
Stores results in the performance_metrics table.

The 72-hour maturity rule is strictly enforced — any video uploaded
less than 72 hours ago is completely skipped.
"""

import os
import logging
import pickle
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from googleapiclient.discovery import build
from src.youtube.oauth import get_authenticated_service

from src.db.models import Video, PerformanceMetric

logger = logging.getLogger(__name__)

# Default maturity window if not in config
MATURITY_DAYS = 7
EARLY_PULL_HOURS = 60


class AnalyticsAgent:
    def __init__(self, config: Dict[str, Any], db_session):
        self.config = config
        self.db = db_session
        self.credentials_file = self.config.get("youtube", {}).get(
            "client_secret_file", "client_secret.json"
        )
        self.token_file = "token.pickle"

    # ------------------------------------------------------------------
    # Auth (reuses the same token.pickle as UploadAgent)
    # ------------------------------------------------------------------

    def _authenticate(self):
        return get_authenticated_service(self.credentials_file, self.token_file)

    # ------------------------------------------------------------------
    # Eligibility filter (72-hour rule)
    # ------------------------------------------------------------------

    def _mature_videos(self) -> List[Video]:
        """
        Returns all uploaded videos that are at least MATURITY_HOURS old
        and have not yet had metrics pulled in the past 24 hours.

        Rules:
        - Videos uploaded < 72h ago → always skipped (immature)
        - Videos with a metric pull in the last 24h → skipped (already fresh)
        - Everything else → eligible
        """
        maturity_days = self.config.get("long_form", {}).get("maturity_days", MATURITY_DAYS)
        early_pull_hours = self.config.get("long_form", {}).get("early_informational_pull_hours", EARLY_PULL_HOURS)
        
        maturity_cutoff = datetime.utcnow() - timedelta(days=maturity_days)
        early_cutoff = datetime.utcnow() - timedelta(hours=early_pull_hours)
        last_24h = datetime.utcnow() - timedelta(hours=24)

        eligible = (
            self.db.query(Video)
            .filter(
                Video.status == "uploaded",
                Video.youtube_video_id.isnot(None),
                Video.upload_time <= early_cutoff,   # At least early pull age
            )
            .all()
        )

        result = []
        for v in eligible:
            # Check if we pulled a metric for this video within the past 24 h
            recent_pull = (
                self.db.query(PerformanceMetric)
                .filter(
                    PerformanceMetric.video_id == v.id,
                    PerformanceMetric.pulled_at >= last_24h,
                )
                .first()
            )
            if recent_pull is None:
                result.append(v)   # no recent pull → include

        logger.info(
            "[AnalyticsAgent] %d videos eligible for metrics pull (>= %d h old).",
            len(result),
            early_pull_hours,
        )
        return result

    # ------------------------------------------------------------------
    # YouTube Analytics fetch
    # ------------------------------------------------------------------

    def _fetch_metrics(self, youtube_data, youtube_analytics, video_id: str) -> Dict[str, Any]:
        """
        Fetches live views/likes/comments from Data API (0 delay), and 
        retention metrics from Analytics API (which has a 48h delay).
        """
        # 1. Fetch live top-level stats from Data API
        data_response = youtube_data.videos().list(
            part="statistics",
            id=video_id
        ).execute()
        items = data_response.get("items", [])
        if not items:
            logger.warning("[AnalyticsAgent] Video %s not found in Data API.", video_id)
            return {}
        stats = items[0].get("statistics", {})
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        # 2. Fetch delayed retention stats from Analytics API
        today = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = "2020-01-01"
        
        avg_duration = None
        avg_pct = None
        try:
            analytics_response = (
                youtube_analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=today,
                    metrics="averageViewDuration,averageViewPercentage",
                    dimensions="video",
                    filters=f"video=={video_id}",
                )
                .execute()
            )
            rows = analytics_response.get("rows", [])
            if rows:
                avg_duration = float(rows[0][1])
                avg_pct = float(rows[0][2])
            else:
                logger.info("[AnalyticsAgent] No retention data available yet for video %s.", video_id)
        except Exception as e:
            logger.warning("[AnalyticsAgent] Analytics API error for %s: %s", video_id, e)
        return {
            "views": views,
            "likes": likes,
            "comments": comments,
            "average_view_duration": avg_duration,
            "average_view_percentage": avg_pct,
            "ctr": None,
        }

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    def pull_metrics(self) -> List[Dict]:
        """
        Main entrypoint.
        Authenticates, finds mature videos, pulls analytics, and persists
        them to the performance_metrics table.

        Returns a list of result dicts for logging / downstream use.
        """
        videos = self._mature_videos()
        if not videos:
            logger.info("[AnalyticsAgent] No mature videos to process. Exiting.")
            return []

        try:
            creds = self._authenticate()
        except FileNotFoundError as e:
            logger.error("[AnalyticsAgent] %s — skipping analytics pull.", e)
            return []

        youtube_analytics = build("youtubeAnalytics", "v2", credentials=creds)
        youtube_data = build("youtube", "v3", credentials=creds)

        results = []
        for video in videos:
            logger.info(
                "[AnalyticsAgent] Pulling metrics for video '%s' (yt_id=%s, age=%.1f h).",
                video.title,
                video.youtube_video_id,
                (datetime.utcnow() - video.upload_time).total_seconds() / 3600,
            )
            try:
                metrics = self._fetch_metrics(youtube_data, youtube_analytics, video.youtube_video_id)
                if not metrics:
                    continue

                record = PerformanceMetric(
                    video_id=video.id,
                    pulled_at=datetime.utcnow(),
                    views=metrics.get("views", 0),
                    likes=metrics.get("likes", 0),
                    comments=metrics.get("comments", 0),
                    average_view_duration=metrics.get("average_view_duration"),
                    average_view_percentage=metrics.get("average_view_percentage"),
                    ctr=metrics.get("ctr"),
                )
                self.db.add(record)
                self.db.commit()

                results.append({"video_id": video.id, "yt_id": video.youtube_video_id, **metrics})
                logger.info("[AnalyticsAgent] Saved metrics for video %s.", video.id)

            except Exception as exc:
                logger.error(
                    "[AnalyticsAgent] Failed to pull metrics for yt_id=%s: %s",
                    video.youtube_video_id,
                    exc,
                )

        return results
