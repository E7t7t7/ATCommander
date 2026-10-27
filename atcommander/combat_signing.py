"""Pure request signing used by the current public WebGL client.

Derived from Throwdown_WebGL_v1.181.0_r39893 (Unity 6000.3.6f1): production
request coroutine raw WASM function 32119, hash helper 42057, and public
IL2CPP string literals at usage addresses 7224104 and 7224108.

The game hashes the user ID, current Unix second, and API message name for
msgsig. It hashes the user ID, current Unix second, platform, and client
version for clisig. Neither signature depends on combat/deck payload fields.
The client hash helper uses UTF-8 bytes and lowercase MD5 hexadecimal.
No account state, signatures, or signing inputs are logged by this module.
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Mapping

DEFAULT_PLATFORM = 'Web'
DEFAULT_CLIENT_VERSION = '1181000'
DEFAULT_CLIENT_VERSION_FULL = '1.181.0'
DEFAULT_UNITY = 'Unity6000_3_6'

# Public build constants. Keep their contents out of diagnostic output.
_MESSAGE_FORMAT = '{0}{1}{2}hMjxyK2z6TQCuA6jUq1B'
_CLIENT_FORMAT = '{0}{1}{2}{3}ndausekT239a'


def _unix_second(value: float | int) -> str:
    if isinstance(value, bool):
        raise ValueError('Client time must be a positive Unix timestamp')
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Client time must be a positive Unix timestamp') from None
    if not math.isfinite(timestamp) or timestamp < 1:
        raise ValueError('Client time must be a positive Unix timestamp')
    return str(int(timestamp))


def _required_text(value: Any, label: str) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError(f'{label} is required for combat signing')
    text = str(value)
    if not text.strip():
        raise ValueError(f'{label} is required for combat signing')
    return text


def generate_signatures(
    user_id: Any,
    message: str,
    client_time: float | int,
    client_version: Any = DEFAULT_CLIENT_VERSION,
    platform: Any = DEFAULT_PLATFORM,
) -> dict[str, str]:
    """Compute the two signatures from explicit inputs, without I/O or logging."""
    identity = _required_text(user_id, 'User ID')
    message_name = _required_text(message, 'API message')
    timestamp = _unix_second(client_time)
    version = _required_text(client_version, 'Client version')
    client_platform = _required_text(platform, 'Platform')
    message_material = _MESSAGE_FORMAT.format(identity, timestamp, message_name)
    client_material = _CLIENT_FORMAT.format(identity, timestamp, client_platform, version)
    return {
        'msgsig': hashlib.md5(message_material.encode('utf-8')).hexdigest(),
        'clisig': hashlib.md5(client_material.encode('utf-8')).hexdigest(),
    }


def sign_combat_fields(
    fields: Mapping[str, Any],
    message: str,
    *,
    user_id: Any,
    now: float | int | None = None,
    client_version: Any = None,
    platform: Any = None,
    client_version_full: Any = None,
    unity: Any = None,
) -> dict[str, Any]:
    """Copy request fields and replace stale signatures with fresh client fields.

    Use current public client version/platform unless explicitly supplied.
    Stale learned fields are replaced. User ID is explicit because it normally
    belongs to the API query rather than the submitted form fields.
    The caller remains responsible for authentication and choosing the endpoint.
    """
    result = dict(fields)
    timestamp = _unix_second(time.time() if now is None else now)
    version = client_version if client_version is not None else DEFAULT_CLIENT_VERSION
    client_platform = platform if platform is not None else DEFAULT_PLATFORM
    signatures = generate_signatures(user_id, message, int(timestamp), version, client_platform)
    result['client_time'] = timestamp
    result['client_version'] = str(version)
    result['platform'] = str(client_platform)
    result['client_version_full'] = str(client_version_full if client_version_full is not None else DEFAULT_CLIENT_VERSION_FULL)
    result['unity'] = str(unity if unity is not None else DEFAULT_UNITY)
    result.update(signatures)
    return result
