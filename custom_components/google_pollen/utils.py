"""Secret-safe Google Pollen transport.

Never let aiohttp exceptions cross this boundary: request_info can contain
headers even when the URL itself is credential-free.
"""

import aiohttp

from .const import BASE_URL


class PollenError(Exception):
    """A fixed-message failure without request/response objects or raw cause."""


class PollenAuthError(PollenError):
    """A credential or API-restriction failure."""


async def fetch_pollen_data(api_key, latitude, longitude, language, days=1):
    """Request without URL credentials, redirects, response logging or raw errors."""
    failure = None
    data = None
    try:
        params = {
            "location.latitude": latitude,
            "location.longitude": longitude,
            "languageCode": language,
            "days": days,
            "plantsDescription": "false",
        }
        # A private session has no HA/user trace hooks that could record headers.
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            trust_env=False,
            raise_for_status=False,
        ) as session:
            async with session.get(
                BASE_URL,
                params=params,
                headers={"X-Goog-Api-Key": api_key},
                allow_redirects=False,
            ) as response:
                if response.status in (400, 401, 403):
                    failure = PollenAuthError(
                        "Google Pollen authentication or API restriction failed"
                    )
                elif response.status != 200:
                    failure = PollenError("Google Pollen HTTP request failed")
                else:
                    data = await response.json()
                    if (
                        not isinstance(data, dict)
                        or "error" in data
                        or not isinstance(data.get("dailyInfo"), list)
                        or not data["dailyInfo"]
                        or data.get("nextPageToken")
                    ):
                        failure = PollenError("Google Pollen returned invalid or incomplete data")
    except Exception:
        # Do not retain the original exception, even as suppressed __context__.
        failure = PollenError("Google Pollen request failed")
    # Raise OUTSIDE the handler: __context__ and __cause__ are both empty.
    if failure is not None:
        raise failure
    return data
