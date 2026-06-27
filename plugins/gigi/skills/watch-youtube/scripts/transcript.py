#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["youtube-transcript-api>=0.6.2"]
# ///
"""
Extract transcript from YouTube video.
Outputs JSON with video metadata and transcript.

Usage (team runtime = uv):
    uv run transcript.py "https://youtube.com/watch?v=VIDEO_ID"
    uv run transcript.py "VIDEO_ID"
"""

import json
import re
import sys
from urllib.parse import parse_qs, urlparse

try:  # Windows CS machines run cp1252 — force UTF-8 so transcripts don't crash.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
except ImportError:
    print(
        json.dumps(
            {
                "error": "MISSING_DEPENDENCY",
                "message": "youtube-transcript-api not installed. Run: pip install youtube-transcript-api",
            }
        )
    )
    sys.exit(1)


def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    # Already a video ID
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id

    # Parse URL
    parsed = urlparse(url_or_id)

    # youtu.be/VIDEO_ID
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/")

    # youtube.com/watch?v=VIDEO_ID
    if parsed.netloc in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            if "v" in query:
                return query["v"][0]
        # youtube.com/shorts/VIDEO_ID
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
        # youtube.com/embed/VIDEO_ID
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]

    raise ValueError(f"Could not extract video ID from: {url_or_id}")


def get_transcript(video_id: str) -> dict:
    """Fetch transcript and metadata for a YouTube video."""
    try:
        # Create API instance (new API style)
        api = YouTubeTranscriptApi()

        # Try to get English transcript first, then any available
        transcript_list = api.list(video_id)

        # Prefer manual English, then auto-generated English, then any
        transcript = None
        language = None

        try:
            transcript = transcript_list.find_manually_created_transcript(
                ["en", "en-US", "en-GB"]
            )
            language = "en (manual)"
        except Exception:
            try:
                transcript = transcript_list.find_generated_transcript(
                    ["en", "en-US", "en-GB"]
                )
                language = "en (auto)"
            except Exception:
                # Get first available transcript
                for t in transcript_list:
                    transcript = t
                    language = t.language_code
                    break

        if transcript is None:
            return {
                "error": "NO_TRANSCRIPT",
                "message": "No transcript available for this video",
            }

        # Fetch the transcript
        fetched = transcript.fetch()
        segments = fetched.snippets

        # Combine into full text
        full_text = " ".join(seg.text for seg in segments)

        # Calculate duration from last segment
        if segments:
            last_seg = segments[-1]
            duration = last_seg.start + (last_seg.duration or 0)
        else:
            duration = 0

        return {
            "success": True,
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
            "language": language,
            "duration_seconds": int(duration),
            "segment_count": len(segments),
            "transcript": full_text,
            "segments": [
                {
                    "start": round(seg.start, 2),
                    "duration": round(seg.duration or 0, 2),
                    "text": seg.text,
                }
                for seg in segments
            ],
        }

    except VideoUnavailable:
        return {
            "error": "VIDEO_UNAVAILABLE",
            "message": "Video is unavailable (private, deleted, or region-locked)",
        }
    except TranscriptsDisabled:
        return {
            "error": "TRANSCRIPTS_DISABLED",
            "message": "Transcripts are disabled for this video",
        }
    except NoTranscriptFound:
        return {
            "error": "NO_TRANSCRIPT",
            "message": "No transcript found for this video",
        }
    except Exception as e:
        return {"error": "UNKNOWN_ERROR", "message": str(e)}


def main():
    if len(sys.argv) < 2:
        print(
            json.dumps(
                {
                    "error": "MISSING_ARGUMENT",
                    "message": "Usage: python transcript.py <youtube-url-or-id>",
                }
            )
        )
        sys.exit(1)

    url_or_id = sys.argv[1]

    try:
        video_id = extract_video_id(url_or_id)
    except ValueError as e:
        print(json.dumps({"error": "INVALID_URL", "message": str(e)}))
        sys.exit(1)

    result = get_transcript(video_id)
    print(json.dumps(result, indent=2))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
