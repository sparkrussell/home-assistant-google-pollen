"""Date-aligned Google Pollen category sensors."""

from datetime import timedelta

from homeassistant.const import CONF_API_KEY, CONF_LANGUAGE, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_POLLEN,
    CONF_POLLEN_CATEGORIES,
    DEFAULT_LANGUAGE,
    DOMAIN,
    PLANT_TYPES,
    POLLEN_CATEGORIES,
)
from .coordinator import MAX_DATA_AGE, GooglePollenDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    """Set up selected sensors only after a successful initial refresh."""
    coordinator = GooglePollenDataUpdateCoordinator(
        hass,
        config_entry.data[CONF_API_KEY],
        config_entry.data[CONF_LATITUDE],
        config_entry.data[CONF_LONGITUDE],
        config_entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        config_entry=config_entry,
    )
    await coordinator.async_config_entry_first_refresh()
    codes = list(
        dict.fromkeys(
            config_entry.data.get(CONF_POLLEN_CATEGORIES, POLLEN_CATEGORIES)
            + config_entry.data.get(CONF_POLLEN, PLANT_TYPES)
        )
    )
    async_add_entities([GooglePollenSensor(coordinator, code) for code in codes])


class GooglePollenSensor(CoordinatorEntity, Entity):
    """A category state with explicit numeric/date forecast attributes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:flower-pollen"

    def __init__(self, coordinator, pollen_type):
        super().__init__(coordinator)
        self._pollen_type = pollen_type
        self._attr_unique_id = (
            f"google_pollen_{pollen_type.lower()}_{coordinator.latitude}_{coordinator.longitude}"
        )
        rows = coordinator.data.get(pollen_type, {})
        self._attr_name = next(iter(rows.values()), {}).get(
            "display_name", pollen_type.capitalize()
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{coordinator.latitude}_{coordinator.longitude}")},
            "name": "Google Pollen",
            "manufacturer": "Google",
            "model": "Pollen API",
            "entry_type": "service",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Re-evaluate local date and freshness without extra billed API requests.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_check_time,
                timedelta(minutes=1),
            )
        )

    @callback
    def _async_check_time(self, _now):
        self.async_write_ha_state()

    def _row(self, offset=0):
        target = (dt_util.now().date() + timedelta(days=offset)).isoformat()
        return (self.coordinator.data or {}).get(self._pollen_type, {}).get(target, {})

    @property
    def available(self):
        fetched_at = self.coordinator.fetched_at
        return bool(
            super().available
            and fetched_at is not None
            and timedelta(0) <= dt_util.utcnow() - fetched_at < MAX_DATA_AGE
            and self._row().get("index_value") is not None
        )

    @property
    def state(self):
        return self._row().get("category")

    @property
    def extra_state_attributes(self):
        today = dt_util.now().date()
        attrs = dict(self._row())
        attrs.setdefault("date", today.isoformat())
        attrs.setdefault("index_value", None)
        attrs.setdefault("category", None)
        attrs["last_updated"] = (
            self.coordinator.fetched_at.isoformat() if self.coordinator.fetched_at else None
        )
        attrs["forecast"] = [
            {
                "date": (today + timedelta(days=i)).isoformat(),
                "index_value": self._row(i).get("index_value"),
                "category": self._row(i).get("category"),
            }
            for i in range(4)
        ]
        for offset, label in enumerate(("tomorrow", "day 3", "day 4"), 1):
            attrs[label] = self._row(offset).get("index_value")
        return attrs
