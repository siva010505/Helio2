from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Channel(Base):
    __tablename__ = 'channels'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    niche = Column(String, nullable=False)
    config_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Topic(Base):
    __tablename__ = 'topics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey('channels.id'))
    topic_text = Column(String, nullable=False)
    source = Column(String)  # "trend_search", "manual", "shorts_seed", "llm_brainstorm"
    source_context_json = Column(Text, nullable=True)  # original context for sourced topics
    score = Column(Float)
    score_breakdown_json = Column(Text, nullable=True)  # JSON: {dimensions, composite, reasoning}
    status = Column(String, default="candidate")  # "candidate", "selected", "rejected", "used"
    created_at = Column(DateTime, default=datetime.utcnow)

class Video(Base):
    __tablename__ = 'videos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey('channels.id'))
    topic_id = Column(Integer, ForeignKey('topics.id'))
    title = Column(String)
    description = Column(Text)
    tags_json = Column(Text)
    hook_style = Column(String)
    script_text = Column(Text)
    voice_used = Column(String)
    video_length_seconds = Column(Integer)
    file_path = Column(String)
    thumbnail_path = Column(String)
    youtube_video_id = Column(String, unique=True)
    upload_time = Column(DateTime)
    status = Column(String, default="drafted")  # "drafted", "assembled", "metadata_ready", "uploaded", "failed"
    created_at = Column(DateTime, default=datetime.utcnow)

class PerformanceMetric(Base):
    __tablename__ = 'performance_metrics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey('videos.id'))
    pulled_at = Column(DateTime, default=datetime.utcnow)
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    average_view_duration = Column(Float)
    average_view_percentage = Column(Float)
    ctr = Column(Float, nullable=True)

class PromptVersion(Base):
    __tablename__ = 'prompt_versions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey('channels.id'))
    agent_name = Column(String)  # e.g., "script_agent", "seo_agent"
    version_number = Column(Integer)
    prompt_text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    performance_summary_json = Column(Text, nullable=True)

class RunLog(Base):
    __tablename__ = 'run_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_type = Column(String)  # "daily_pipeline", "analytics_pull"
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    status = Column(String)
    error_text = Column(Text, nullable=True)
    summary_json = Column(Text)
