"""
Upload Agent

Role:
Handles authenticating with the YouTube Data API v3 using OAuth2.
Uploads the final assembled video and sets the SEO metadata and thumbnail.
Enforces quota limits and handles API retries.
"""

import os
import pickle
import logging
import time
from typing import Dict, Any
from datetime import datetime, timedelta, date
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from src.youtube.oauth import get_authenticated_service
from src.db.models import Video
from sqlalchemy import cast, Date

logger = logging.getLogger(__name__)

class UploadAgent:
    def __init__(self, config: Dict[str, Any], db_session=None, llm_client=None):
        self.config = config
        self.db = db_session
        self.llm_client = llm_client
        self.credentials_file = self.config.get("youtube", {}).get("client_secret_file", "client_secret.json")
        self.token_file = "token.pickle"
        
        # Load quota settings
        # Note: if config is passed as channel_config, we need to access the root config
        # We assume the caller passed the full config or the quota is accessible
        # If not, we will default to 8000/1600
        # Wait, pipeline.py passes channel_config. Let's safely get quota
        
    def _get_quota_config(self):
        # We try to get from the main config if passed, else default
        quota = self.config.get("quota", {})
        if not quota:
            # Maybe it's a channel config, let's assume global config structure is unavailable 
            # and fallback to default
            budget = 8000
            cost = 1600
        else:
            budget = quota.get("youtube_daily_unit_budget", 8000)
            cost = quota.get("upload_cost_units", 1600)
        return budget, cost

    def _authenticate(self):
        return get_authenticated_service(self.credentials_file, self.token_file)

    def _check_quota(self):
        if not self.db:
            logger.warning("[UploadAgent] db_session not provided, skipping quota check.")
            return True
            
        budget, cost = self._get_quota_config()
        
        # Count successful uploads today
        uploads_today = self.db.query(Video).filter(
            Video.status == 'uploaded',
            cast(Video.upload_time, Date) == date.today()
        ).count()
        
        current_spent = uploads_today * cost
        if current_spent + cost > budget:
            logger.error("[UploadAgent] Quota exceeded! Spent %d + %d (cost) > %d (budget).", current_spent, cost, budget)
            raise RuntimeError(f"YouTube API quota exceeded. Budget: {budget}, Spent today: {current_spent}")
            
        logger.info("[UploadAgent] Quota check passed. Spent %d/%d (adding %d).", current_spent, budget, cost)
        return True

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list,
        thumbnail_path: str = None,
        publish_time_str: str = None,
        dry_run: bool = False,
        script_text: str = "",
    ) -> str:
        """
        Uploads the video to YouTube.
        If dry_run is True, forces privacyStatus to 'private' and ignores scheduling.
        Returns the new YouTube Video ID.
        """
        logger.info("[UploadAgent] Starting authentication for YouTube API...")
        
        self._check_quota()
        
        try:
            creds = self._authenticate()
        except FileNotFoundError as e:
            logger.error("[UploadAgent] %s", e)
            raise
            
        youtube = build('youtube', 'v3', credentials=creds)

        status_dict = {
            'privacyStatus': 'private', # User requested it be private for now
            'selfDeclaredMadeForKids': False, 
        }

        # User requested immediate upload with no scheduling:
        # if publish_time_str and not dry_run:
        #     try:
        #         now = datetime.utcnow()
        #         h, m = map(int, publish_time_str.split(':'))
        #         target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        #         if target <= now:
        #             target += timedelta(days=1)
        #         status_dict['publishAt'] = target.isoformat() + "Z"
        #         logger.info("[UploadAgent] Scheduling video to publish at %s", status_dict['publishAt'])
        #     except Exception as e:
        #         logger.warning("[UploadAgent] Failed to parse publish_time_str '%s', uploading immediately as private: %s", publish_time_str, e)

        body = {
            'snippet': {
                'title': title[:100],
                'description': description,
                'tags': tags,
                'categoryId': '22'  # People & Blogs
            },
            'status': status_dict
        }

        logger.info("[UploadAgent] Uploading video file '%s'...", video_path)
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype='video/mp4')
        
        request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )

        # Retry with backoff
        max_retries = 3
        retry_delay = 5
        response = None
        
        for attempt in range(1, max_retries + 1):
            try:
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        logger.info("[UploadAgent] Uploaded %d%%", int(status.progress() * 100))
                break # Success!
            except Exception as e:
                if attempt < max_retries:
                    logger.warning("[UploadAgent] Upload failed on attempt %d: %s. Retrying in %d seconds...", attempt, e, retry_delay)
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    # Recreate the request for safety
                    request = youtube.videos().insert(
                        part=','.join(body.keys()),
                        body=body,
                        media_body=media
                    )
                else:
                    logger.error("[UploadAgent] Upload failed after %d attempts: %s", max_retries, e)
                    raise

        video_id = response.get('id')
        logger.info("[UploadAgent] Video uploaded successfully! Video ID: %s", video_id)
        
        # Upload Thumbnail if provided
        if thumbnail_path and os.path.exists(thumbnail_path):
            logger.info("[UploadAgent] Uploading custom thumbnail from '%s'...", thumbnail_path)
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype='image/jpeg')
                ).execute()
                logger.info("[UploadAgent] Successfully uploaded custom thumbnail!")
            except Exception as e:
                logger.warning("[UploadAgent] Failed to upload custom thumbnail: %s", e)
        
        # Generate actual_content_summary
        actual_content_summary = ""
        if getattr(self, "llm_client", None) and script_text:
            try:
                logger.info("[UploadAgent] Generating actual_content_summary for cross-promotion...")
                system_prompt = (
                    "You are a cross-promotion assistant. Read the following script and write a 2-3 sentence "
                    "summary explaining the ACTUAL story, experiment, or incident covered in it. "
                    "CRITICAL RULE: Do NOT just repeat the title or the hook. The Shorts pipeline AI will use this "
                    "summary as background context to write an engaging promotional Short, so it must explain "
                    "what the video actually talks about. This ensures the Shorts AI doesn't hallucinate an unrelated story.\n\n"
                    'Output exactly this JSON format: {"summary": "your summary here"}'
                )
                
                response = self.llm_client.generate_json(
                    system_prompt=system_prompt,
                    user_prompt=script_text,
                    temperature=0.3,
                    max_tokens=300
                )
                actual_content_summary = response.get("summary", "")
            except Exception as e:
                logger.error("[UploadAgent] Failed to generate actual_content_summary: %s", e)

        # Write shared pointer file
        try:
            shared_file = Path("latest_long_form.json")
            
            import json
            with open(shared_file, "w") as f:
                json.dump({
                    "title": title,
                    "link": f"https://youtu.be/{video_id}" if video_id else "",
                    "actual_content_summary": actual_content_summary
                }, f, indent=2)
            logger.info("[UploadAgent] Wrote shared pointer to %s", shared_file)
        except Exception as e:
            logger.error("[UploadAgent] Failed to write shared pointer file: %s", e)

        return video_id
