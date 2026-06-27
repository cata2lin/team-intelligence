#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.28.0", "beautifulsoup4>=4.12.0"]
# ///
"""
Search YouTube for videos.
Uses web scraping (no API key required).

Usage (team runtime = uv):
    uv run search.py "kubernetes tutorial" --max-results 5
"""

import argparse
import json
import re
import sys

try:  # Windows CS machines run cp1252 — force UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from urllib.parse import quote_plus

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        json.dumps(
            {
                "error": "MISSING_DEPENDENCY",
                "message": "Required packages not installed. Run: pip install requests beautifulsoup4",
            }
        )
    )
    sys.exit(1)


def search_youtube(query: str, max_results: int = 5) -> dict:
    """Search YouTube and return video results."""

    # Use YouTube search URL
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        return {
            "error": "REQUEST_FAILED",
            "message": f"Failed to fetch search results: {str(e)}",
        }

    # Extract video data from the page
    # YouTube embeds video data in a script tag as JSON
    html = response.text

    # Find the ytInitialData JSON
    match = re.search(r"var ytInitialData = ({.*?});", html)
    if not match:
        # Try alternative pattern
        match = re.search(r"ytInitialData\s*=\s*({.*?});", html)

    if not match:
        return {
            "error": "PARSE_ERROR",
            "message": "Could not parse YouTube search results",
        }

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"error": "PARSE_ERROR", "message": "Could not parse YouTube JSON data"}

    # Navigate to video results
    results = []
    try:
        contents = data["contents"]["twoColumnSearchResultsRenderer"][
            "primaryContents"
        ]["sectionListRenderer"]["contents"]

        for section in contents:
            if "itemSectionRenderer" not in section:
                continue

            items = section["itemSectionRenderer"]["contents"]

            for item in items:
                if "videoRenderer" not in item:
                    continue

                video = item["videoRenderer"]
                video_id = video.get("videoId")

                if not video_id:
                    continue

                # Extract video info
                title = (
                    video.get("title", {}).get("runs", [{}])[0].get("text", "Unknown")
                )
                channel = (
                    video.get("ownerText", {})
                    .get("runs", [{}])[0]
                    .get("text", "Unknown")
                )

                # Duration
                duration_text = video.get("lengthText", {}).get("simpleText", "Unknown")

                # View count
                view_count_text = video.get("viewCountText", {}).get(
                    "simpleText", "0 views"
                )
                view_match = re.search(r"([\d,]+)", view_count_text.replace(",", ""))
                views = int(view_match.group(1)) if view_match else 0

                # Published time
                published = video.get("publishedTimeText", {}).get(
                    "simpleText", "Unknown"
                )

                results.append(
                    {
                        "video_id": video_id,
                        "url": f"https://youtube.com/watch?v={video_id}",
                        "title": title,
                        "channel": channel,
                        "duration": duration_text,
                        "views": views,
                        "views_text": view_count_text,
                        "published": published,
                    }
                )

                if len(results) >= max_results:
                    break

            if len(results) >= max_results:
                break

    except (KeyError, TypeError) as e:
        return {
            "error": "PARSE_ERROR",
            "message": f"Could not extract video data: {str(e)}",
        }

    return {
        "success": True,
        "query": query,
        "result_count": len(results),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Search YouTube for videos")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--max-results", type=int, default=5, help="Maximum number of results"
    )

    args = parser.parse_args()

    result = search_youtube(args.query, args.max_results)
    print(json.dumps(result, indent=2))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
