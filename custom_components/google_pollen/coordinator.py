"""Coordinator and date-preserving normalization for Google Pollen."""

from datetime import date, timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import PLANT_TYPES, POLLEN_CATEGORIES
from .utils import PollenAuthError, PollenError, fetch_pollen_data

from homeassistant.exceptions import ConfigEntryAuthFailed

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=4)
MAX_DATA_AGE = timedelta(hours=8)


def normalize_pollen_data(data, fetched_at):
    """Key forecasts by Google's actual calendar date, never response position.

    Google may omit indexInfo outside the season. That means unknown, not zero.
    Only documented index integers 0..5 are accepted; bool is not a numeric UPI.
    """
    result = {}
    seen_dates = set()
    for day in data["dailyInfo"]:
        parts = day["date"]
        forecast_date = date(parts["year"], parts["month"], parts["day"]).isoformat()
        if forecast_date in seen_dates:
            raise ValueError("Duplicate forecast date")
        seen_dates.add(forecast_date)
        for item in (day.get("pollenTypeInfo") or []) + (day.get("plantInfo") or []):
            code = item.get("code")
            if code not in POLLEN_CATEGORIES + PLANT_TYPES:
                continue
            index = item.get("indexInfo") or {}
            value = index.get("value")
            if type(value) is not int or not 0 <= value <= 5:
                value = None
            category = index.get("category")
            if not isinstance(category, str) or not category:
                category = None
            if category is None:
                value = None
            result.setdefault(code, {})[forecast_date] = {
                "date": forecast_date,
                "category": category if value is not None else None,
                "display_name": item.get("displayName")
                if isinstance(item.get("displayName"), str)
                else code.capitalize(),
                "in_season": item.get("inSeason") if type(item.get("inSeason")) is bool else None,
                "last_updated": fetched_at.isoformat(),
                "index_value": value,
            }
    return result


class GooglePollenDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetch on a four-hour cadence; errors make entities unavailable."""

    def __init__(self, hass, api_key, latitude, longitude, language, config_entry=None):
        self.api_key = api_key
        self.latitude = latitude
        self.longitude = longitude
        self.language = language
        self.fetched_at = None
        super().__init__(
            hass, _LOGGER, name="Pollen", update_interval=SCAN_INTERVAL, config_entry=config_entry
        )

    async def _async_update_data(self):
        failure = None
        result = None
        try:
            data = await fetch_pollen_data(
                api_key=self.api_key,
                latitude=self.latitude,
                longitude=self.longitude,
                language=self.language,
                days=4,
            )
            fetched_at = dt_util.utcnow()
            result = normalize_pollen_data(data, fetched_at)
        except PollenAuthError:
            failure = ConfigEntryAuthFailed(
                "Google Pollen authentication or API restriction failed"
            )
        except PollenError:
            failure = UpdateFailed("Google Pollen request failed")
        except Exception:
            # Parsing failures may embed response values: discard the raw cause.
            failure = UpdateFailed("Google Pollen data could not be processed")
        if failure is not None:
            raise failure
        self.fetched_at = fetched_at
        return result
