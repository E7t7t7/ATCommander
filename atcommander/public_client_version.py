"""Resolve a verified combat profile from the unauthenticated game launcher.

Only public build/version fields are cached. This module never imports account
credentials, copies a game session, or sends an Animation Throwdown API request.
Unknown builds require verification of their native signing material before they
can authorize combat; a release number alone is not enough.
"""
from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any

import requests


PUBLIC_EMBED_URL = "https://www.kongregate.com/en/games/throwdown/animation-throwdown/embed"
CACHE_TTL_SECONDS = 3600
_MAX_CACHE_BYTES = 16384
_MAX_EMBED_BYTES = 1024 * 1024
_MAX_INDEX_BYTES = 256 * 1024
_LOCK = threading.RLock()

# Each entry was checked against its exact public native build. These builds use
# the same message/client hash material and Unity engine 6000.3.6f1. Do not infer
# an unverified future entry from a nearby version or cached account response.
_VERIFIED_PROFILES = {
    "Throwdown_WebGL_v1.181.0_r39893": {
        "client_version": "1181000", "client_version_full": "1.181.0",
        "platform": "Web", "unity": "Unity6000_3_6",
    },
    "Throwdown_WebGL_v1.182.0_r40510": {
        "client_version": "1182000", "client_version_full": "1.182.0",
        "platform": "Web", "unity": "Unity6000_3_6",
    },
    "Throwdown_WebGL_v1.183.0_r40786": {
        "client_version": "1183000", "client_version_full": "1.183.0",
        "platform": "Web", "unity": "Unity6000_3_6",
    },
}
_BUILD_PATTERN = r"Throwdown_WebGL_v([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})_r[0-9]{1,8}"
_INDEX_RE = re.compile(
    r"https://cb-cdn\.synapsegames\.com/webgl/(" + _BUILD_PATTERN +
    r")/index\.html(?=[\s\"'<>]|$)"
)
_PRODUCT_RE = re.compile(
    r"\bproductVersion[\"']?\s*[:=]\s*[\"']([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})[\"']"
)


class PublicClientVersionError(RuntimeError):
    """Safe public-only failure message suitable for the application's log."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


def _default_cache_path() -> Path:
    # Keep this file separate from account settings and captured protocol data.
    return Path.home() / ".atcommander" / "public_client_version.json"

def _profile_for_build(build):
    profile=_VERIFIED_PROFILES.get(build)
    if profile is None:
        from .client_updater import load_verified_profile
        profile=load_verified_profile(build)
    return dict(profile) if profile is not None else None


def _read_cache(path: Path, now: float) -> dict[str, str] | None:
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return None
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict) or set(cached) != {"schema", "build", "checked_at", "profile"}:
            return None
        build = cached.get("build")
        verified=_profile_for_build(build) if isinstance(build,str) else None
        if verified is None or cached.get("schema") != 1:
            return None
        checked_at = cached.get("checked_at")
        if isinstance(checked_at, bool) or not isinstance(checked_at, (int, float)):
            return None
        if not math.isfinite(checked_at) or not 0 <= now - checked_at < CACHE_TTL_SECONDS:
            return None
        profile = cached.get("profile")
        if profile != verified:
            return None
        return verified
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _save_cache(path: Path, build: str, profile: dict[str, str], now: float) -> None:
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist only allowlisted, verified public data, never fetched HTML.
        value = {"schema": 1, "build": build, "checked_at": now,
                 "profile": dict(profile)}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".public-client-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError):
        # A verified online profile remains usable when its public cache cannot
        # be written. The next resolution will validate the launcher again.
        pass
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def _read_public_text(session: requests.Session, url: str, limit: int, timeout: float) -> str:
    response: Any = None
    try:
        response = session.get(url, stream=True, timeout=timeout, allow_redirects=False)
        if response.status_code != 200:
            raise PublicClientVersionError(
                "The public game launcher is unavailable. Combat is paused until its version can be verified.",
                reason="public_launcher_unavailable",
            )
        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            if not isinstance(chunk, bytes):
                raise ValueError("Unexpected public response chunk")
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise PublicClientVersionError(
                    "The public game launcher exceeded its read limit. Combat is paused.",
                    reason="public_launcher_limit",
                )
        return chunks.decode("utf-8")
    except PublicClientVersionError:
        raise
    except (requests.RequestException, UnicodeError, ValueError, TypeError, OSError):
        # Network exceptions can contain arbitrary URLs or proxy credentials.
        raise PublicClientVersionError(
            "The public game client version could not be read. Combat is paused; retry when connectivity returns.",
            reason="public_launcher_read_failed",
        ) from None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def resolve_public_client_profile(
    *, force_refresh: bool = False, timeout: float = 12.0, cache_path: str | Path | None = None,
) -> dict[str, str]:
    """Return the exact current verified client fields, without browser startup.

    A valid public cache lasts less than one hour. Expired/invalid caches and
    forced refreshes require a successful public launcher check; stale profiles
    never silently authorize a combat request. No game request is retried here.
    """
    if isinstance(timeout, bool):
        raise ValueError("Public version timeout must be between 0 and 30 seconds")
    try:
        deadline = float(timeout)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Public version timeout must be between 0 and 30 seconds") from None
    if not math.isfinite(deadline) or not 0 < deadline <= 30:
        raise ValueError("Public version timeout must be between 0 and 30 seconds")
    path = _default_cache_path() if cache_path is None else Path(cache_path)
    with _LOCK:
        now = time.time()
        if not force_refresh:
            cached = _read_cache(path, now)
            if cached is not None:
                return cached
        # A new session has no account cookies/authentication. Disable netrc and
        # environment authentication/proxy material as well; no existing session
        # is passed into this module.
        with requests.Session() as session:
            session.trust_env = False
            embed = html.unescape(_read_public_text(session, PUBLIC_EMBED_URL, _MAX_EMBED_BYTES, deadline))
            embed = embed.replace("\\/", "/")
            matches = {(match.group(1), match.group(2)) for match in _INDEX_RE.finditer(embed)}
            if len(matches) != 1:
                raise PublicClientVersionError(
                    "The current public game build could not be identified unambiguously. Combat is paused.",
                    reason="public_build_ambiguous",
                )
            build, version = matches.pop()
            profile = _profile_for_build(build)
            if profile is None:
                raise PublicClientVersionError(
                    f"The current public game build {build} is not yet verified by Commander. Use Check / Update Game Client; combat is paused until verification finishes.",
                    reason="public_build_unverified",
                )
            # Construct this URL from the validated public build name. Arbitrary
            # iframe links, redirects, queries and account URLs are never used.
            index_url = f"https://cb-cdn.synapsegames.com/webgl/{build}/index.html"
            index = _read_public_text(session, index_url, _MAX_INDEX_BYTES, deadline)
            products = set(_PRODUCT_RE.findall(index))
            if products != {version} or profile["client_version_full"] != version:
                raise PublicClientVersionError(
                    "The public game build and product version disagree. Combat is paused until they can be verified.",
                    reason="public_build_mismatch",
                )
        result = dict(profile)
        _save_cache(path, build, result, time.time())
        return result
