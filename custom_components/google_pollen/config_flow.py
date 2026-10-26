"""Config flow for Google Pollen integration."""

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LANGUAGE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

from .const import (
    CONF_POLLEN,
    CONF_POLLEN_CATEGORIES,
    DEFAULT_LANGUAGE,
    DOMAIN,
    POLLEN_CATEGORIES as CONF_CATEGORY_CODES,
)
from .utils import PollenAuthError, PollenError, fetch_pollen_data
from .coordinator import normalize_pollen_data
from homeassistant.util import dt as dt_util


class GooglePollenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Google Pollen."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._init_info = {}
        self._pollen_list = []
        self._pollen_categories_list = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY)
            if not isinstance(api_key, str) or not re.fullmatch(r"AIza[0-9A-Za-z_-]{35}", api_key):
                errors[CONF_API_KEY] = "invalid_api_key"
            else:
                try:
                    user_input[CONF_LATITUDE] = round(user_input[CONF_LATITUDE], 4)
                    user_input[CONF_LONGITUDE] = round(user_input[CONF_LONGITUDE], 4)
                    await self._fetch_pollen_data(user_input)
                    return await self.async_step_select_pollen()
                except PollenAuthError:
                    errors[CONF_API_KEY] = "invalid_api_key"
                except PollenError:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                    vol.Required(
                        CONF_LATITUDE, default=round(self.hass.config.latitude, 4)
                    ): cv.latitude,
                    vol.Required(
                        CONF_LONGITUDE, default=round(self.hass.config.longitude, 4)
                    ): cv.longitude,
                    vol.Required(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): cv.language,
                }
            ),
            errors=errors,
        )

    async def async_step_select_pollen(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the step to select pollen categories."""

        if user_input is not None:
            self._init_info[CONF_POLLEN_CATEGORIES] = user_input[CONF_POLLEN_CATEGORIES]
            self._init_info[CONF_POLLEN] = user_input[CONF_POLLEN]

            await self.async_set_unique_id(
                f"{self._init_info[CONF_LATITUDE]}-{self._init_info[CONF_LONGITUDE]}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Pollen ({self._init_info[CONF_LATITUDE]}, {self._init_info[CONF_LONGITUDE]})",
                data=self._init_info,
            )

        return self.async_show_form(
            step_id="select_pollen",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLLEN_CATEGORIES,
                        default=list(self._pollen_categories_list.keys()),
                    ): cv.multi_select(self._pollen_categories_list),
                    vol.Required(
                        CONF_POLLEN, default=list(self._pollen_list.keys())
                    ): cv.multi_select(self._pollen_list),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the reconfigure step."""
        errors = {}

        # Initialize self._init_info with the current entry data if not already set
        if not self._init_info:
            self._init_info = dict(
                self.hass.config_entries.async_get_entry(self.context["entry_id"]).data
            )

        # Fetch pollen data to populate self._pollen_categories and other attributes
        try:
            await self._fetch_pollen_data(self._init_info, user_input)
        except PollenError:
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            # Update _init_info with the new selections
            self._init_info[CONF_POLLEN_CATEGORIES] = user_input[CONF_POLLEN_CATEGORIES]
            self._init_info[CONF_POLLEN] = user_input[CONF_POLLEN]

            # Coordinator entities are recreated by the config-entry reload.
            self.hass.config_entries.async_update_entry(
                self.hass.config_entries.async_get_entry(self.context["entry_id"]),
                data=self._init_info,
            )

            # The existing update listener schedules the reload exactly once.
            return self.async_abort(reason="reconfigured")

        # Map stored codes back to display names for default values
        selected_categories = [
            code
            for code in self._init_info.get(CONF_POLLEN_CATEGORIES, [])
            if code in self._pollen_categories_list
        ]

        selected_pollen = [
            code for code in self._init_info.get(CONF_POLLEN, []) if code in self._pollen_list
        ]

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    # vol.Optional(CONF_LANGUAGE, default=self._init_info.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)): cv.language,
                    vol.Required(
                        CONF_POLLEN_CATEGORIES, default=selected_categories
                    ): cv.multi_select(self._pollen_categories_list),
                    vol.Required(CONF_POLLEN, default=selected_pollen): cv.multi_select(
                        self._pollen_list
                    ),
                }
            ),
            errors=errors,
        )

    async def _fetch_pollen_data(
        self, init_info: dict[str, Any], user_input: dict[str, Any] | None = None
    ) -> None:
        """Fetch pollen data from the API."""
        if CONF_API_KEY not in init_info:
            raise KeyError(f"Missing {CONF_API_KEY} in configuration")

        api_key = init_info[CONF_API_KEY]
        latitude = init_info[CONF_LATITUDE]
        longitude = init_info[CONF_LONGITUDE]
        language = init_info[CONF_LANGUAGE]

        self._init_info = init_info.copy()

        pollen_data = await fetch_pollen_data(
            api_key=api_key,
            latitude=latitude,
            longitude=longitude,
            language=language,
            days=4,
        )

        failure = None
        try:
            normalized = normalize_pollen_data(pollen_data, dt_util.utcnow())
            self._pollen_categories_list = {
                code: next(iter(rows.values()))["display_name"]
                for code, rows in normalized.items()
                if code in CONF_CATEGORY_CODES
            }
            self._pollen_list = {
                code: next(iter(rows.values()))["display_name"]
                for code, rows in normalized.items()
                if code not in CONF_CATEGORY_CODES
            }
            if not normalized:
                failure = PollenError("Google Pollen returned no selectable data")
        except Exception:
            failure = PollenError("Google Pollen data could not be processed")
        if failure is not None:
            raise failure
