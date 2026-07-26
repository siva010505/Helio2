"""
Stock Search Module

Provides standalone functions to search Pexels, Pixabay, and Unsplash.
Extracted from VisualDirectorAgent to be shared with PunchAgent.
"""
import os
import requests
import logging
from typing import List

logger = logging.getLogger(__name__)

def search_pexels(query: str, limit: int = 3, api_key: str = None) -> List[str]:
    api_key = api_key or os.environ.get("PEXELS_API_KEY")
    if not api_key: return []
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": limit, "orientation": "landscape"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        videos = []
        for video in data.get("videos", []):
            files = video.get("video_files", [])
            if not files: continue
            files = sorted(files, key=lambda x: x.get("width", 0) * x.get("height", 0), reverse=True)
            videos.append(files[0]["link"])
        return videos
    except Exception as exc:
        logger.error("Pexels API error: %s", exc)
        return []

def search_pixabay(query: str, limit: int = 3, api_key: str = None) -> List[str]:
    api_key = api_key or os.environ.get("PIXABAY_API_KEY")
    if not api_key: return []
    url = "https://pixabay.com/api/videos/"
    params = {"key": api_key, "q": query, "per_page": limit + 3}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        videos = []
        for hit in data.get("hits", []):
            vids = hit.get("videos", {})
            if "large" in vids and vids["large"]["url"]:
                videos.append(vids["large"]["url"])
            elif "medium" in vids and vids["medium"]["url"]:
                videos.append(vids["medium"]["url"])
        return videos[:limit]
    except Exception as exc:
        logger.error("Pixabay API error: %s", exc)
        return []

def search_unsplash(query: str, limit: int = 3, api_key: str = None) -> List[str]:
    api_key = api_key or os.environ.get("UNSPLASH_API_KEY")
    if not api_key: return []
    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {api_key}"}
    params = {"query": query, "per_page": limit, "orientation": "landscape"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        images = []
        for res in data.get("results", []):
            urls = res.get("urls", {})
            if "regular" in urls:
                images.append(urls["regular"])
        return images
    except Exception as exc:
        logger.error("Unsplash API error: %s", exc)
        return []
