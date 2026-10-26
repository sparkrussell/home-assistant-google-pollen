"""Secret sentinel, transport, actual HA flow/coordinator/sensor regressions."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import traceback
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp import web
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
import voluptuous as vol
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.google_pollen import config_flow, coordinator, sensor, utils
from custom_components.google_pollen.const import CONF_POLLEN, CONF_POLLEN_CATEGORIES

# Synthetic only; deliberately matches the UI grammar, never loaded from config.
SECRET = "AIza" + "SyntheticOnlyDoNotUseForGoogle".ljust(35, "x")
NOW = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)


def day(date="2026-10-26", value=2, category="Low", code="GRASS"):
    year, month, number = map(int, date.split("-"))
    return {
        "date": {"year": year, "month": month, "day": number},
        "pollenTypeInfo": [
            {
                "code": code,
                "displayName": code.title(),
                "inSeason": True,
                "indexInfo": {"value": value, "category": category},
            }
        ],
    }


def payload(*days):
    return {"dailyInfo": list(days or [day()])}


def assert_safe(error, caplog):
    assert SECRET not in str(error)
    assert SECRET not in repr(error)
    assert SECRET not in "".join(traceback.format_exception(error))
    assert SECRET not in caplog.text
    assert error.__context__ is None
    assert error.__cause__ is None


class Response:
    def __init__(self, status=200, data=None, error=None):
        self.status, self.data, self.error = status, data, error
        self.read = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        self.read = True
        if self.error:
            raise self.error
        return self.data


def transport(monkeypatch, response=None, error=None):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.get.return_value = response
    if error:
        session.get.side_effect = error
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(utils.aiohttp, "ClientSession", factory)
    return factory, session


async def test_transport_header_not_url(monkeypatch, caplog):
    response = Response(data=payload())
    factory, session = transport(monkeypatch, response)
    assert await utils.fetch_pollen_data(SECRET, 1, 2, "en", 4) == payload()
    assert response.read
    args, kwargs = session.get.call_args
    assert SECRET not in str(args) + str(kwargs["params"])
    assert kwargs["headers"] == {"X-Goog-Api-Key": SECRET}
    assert kwargs["allow_redirects"] is False
    assert factory.call_args.kwargs["timeout"].total == 30
    assert SECRET not in caplog.text


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404, 429, 500, 503])
async def test_http_failure_discards_body(monkeypatch, caplog, status):
    response = Response(status, {"error": {"message": SECRET}})
    transport(monkeypatch, response)
    expected = utils.PollenAuthError if status in (400, 401, 403) else utils.PollenError
    with pytest.raises(expected) as caught:
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")
    assert not response.read
    assert_safe(caught.value, caplog)


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: aiohttp.ClientConnectionError(SECRET),
        lambda: TimeoutError(SECRET),
        lambda: ValueError(SECRET),
        lambda: RuntimeError(SECRET),
        lambda: aiohttp.ClientResponseError(
            MagicMock(
                real_url="https://example.invalid/" + SECRET, headers={"X-Goog-Api-Key": SECRET}
            ),
            (),
            status=403,
            message=SECRET,
            headers={"X-Goog-Api-Key": SECRET},
        ),
    ],
)
async def test_raw_request_exceptions_never_escape(monkeypatch, caplog, error_factory):
    factory, session = transport(monkeypatch, error=error_factory())
    with pytest.raises(utils.PollenError) as caught:
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")
    assert session.get.called
    assert_safe(caught.value, caplog)


async def test_session_creation_error(monkeypatch, caplog):
    factory = MagicMock(side_effect=RuntimeError(SECRET))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", factory)
    with pytest.raises(utils.PollenError) as caught:
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")
    factory.assert_called_once()
    assert_safe(caught.value, caplog)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"dailyInfo": None},
        {"dailyInfo": []},
        {"dailyInfo": [], "error": {"message": SECRET}},
        {"dailyInfo": [day()], "nextPageToken": SECRET},
    ],
)
async def test_invalid_payload(monkeypatch, caplog, data):
    response = Response(data=data)
    transport(monkeypatch, response)
    with pytest.raises(utils.PollenError) as caught:
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")
    assert response.read
    assert_safe(caught.value, caplog)


async def test_json_error(monkeypatch, caplog):
    response = Response(error=ValueError(SECRET))
    transport(monkeypatch, response)
    with pytest.raises(utils.PollenError) as caught:
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")
    assert response.read
    assert_safe(caught.value, caplog)


async def test_cancellation_propagates(monkeypatch):
    transport(monkeypatch, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await utils.fetch_pollen_data(SECRET, 1, 2, "en")


async def test_real_aiohttp_header_and_redirect_boundary(monkeypatch, caplog):
    seen = []

    async def handler(request):
        seen.append((request.path, dict(request.query), request.headers.get("X-Goog-Api-Key")))
        if request.path == "/redirect":
            return web.Response(status=302, headers={"Location": "/leak"}, text=SECRET)
        return web.json_response(payload())

    app = web.Application()
    app.router.add_get("/{path}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        monkeypatch.setattr(utils, "BASE_URL", f"http://127.0.0.1:{port}/forecast")
        assert await utils.fetch_pollen_data(SECRET, 1, 2, "en") == payload()
        assert seen[0][2] == SECRET
        assert "key" not in seen[0][1]
        monkeypatch.setattr(utils, "BASE_URL", f"http://127.0.0.1:{port}/redirect")
        with pytest.raises(utils.PollenError) as caught:
            await utils.fetch_pollen_data(SECRET, 1, 2, "en")
        assert [r[0] for r in seen] == ["/forecast", "/redirect"]
        assert_safe(caught.value, caplog)
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(
    "index,expected",
    [
        ({}, None),
        (None, None),
        ({"value": None}, None),
        ({"value": 0, "category": "None"}, 0),
        ({"value": 5, "category": "Very high"}, 5),
        ({"value": False, "category": "None"}, None),
        ({"value": -1, "category": "None"}, None),
        ({"value": 6, "category": "None"}, None),
        ({"value": "2", "category": "Low"}, None),
        ({"value": float("nan"), "category": "Low"}, None),
        ({"value": 1}, None),
    ],
)
def test_missing_null_and_real_zero(index, expected):
    d = day()
    d["pollenTypeInfo"][0]["indexInfo"] = index
    result = coordinator.normalize_pollen_data(payload(d), NOW)
    assert result["GRASS"]["2026-10-26"]["index_value"] == expected
    assert result["GRASS"]["2026-10-26"]["last_updated"] == NOW.isoformat()


def make_sensor(hass, monkeypatch, data):
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        dt_util, "now", lambda: NOW.astimezone(dt_util.get_time_zone("America/Denver"))
    )
    c = coordinator.GooglePollenDataUpdateCoordinator(hass, SECRET, 1, 2, "en")
    c.data = coordinator.normalize_pollen_data(data, NOW)
    c.fetched_at = NOW
    return sensor.GooglePollenSensor(c, "GRASS")


async def test_sparse_unordered_dates_and_future_only_code(hass, monkeypatch):
    s = make_sensor(hass, monkeypatch, payload(day("2026-10-28", 5), day(value=0, category="None")))
    assert s.available and s.state == "None"
    attrs = s.extra_state_attributes
    assert attrs["index_value"] == 0
    assert attrs["tomorrow"] is None and attrs["day 3"] == 5 and attrs["day 4"] is None
    assert [r["date"] for r in attrs["forecast"]] == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
    ]
    future = make_sensor(hass, monkeypatch, payload(day("2026-10-27", 4)))
    assert not future.available and future.state is None
    assert future.extra_state_attributes["index_value"] is None
    assert future.extra_state_attributes["tomorrow"] == 4


async def test_midnight_and_freshness(hass, monkeypatch):
    s = make_sensor(hass, monkeypatch, payload(day(), day("2026-10-27", 4, "High")))
    assert s.available
    monkeypatch.setattr(dt_util, "now", lambda: NOW + timedelta(days=1))
    assert s.state == "High" and s.extra_state_attributes["date"] == "2026-10-27"
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW + timedelta(hours=8))
    assert not s.available
    assert s.extra_state_attributes["last_updated"] == NOW.isoformat()
    s.coordinator.last_update_success = False
    assert not s.available


@pytest.mark.parametrize(
    "data",
    [
        payload(day("2026-10-26"), day("2026-10-26")),
        {"dailyInfo": [{"date": {"year": SECRET}}]},
        {"dailyInfo": [None]},
        {"dailyInfo": [{"date": {"year": 2026, "month": 13, "day": 1}}]},
    ],
)
async def test_parser_errors_safe_through_coordinator(hass, monkeypatch, caplog, data):
    fetch = AsyncMock(return_value=data)
    monkeypatch.setattr(coordinator, "fetch_pollen_data", fetch)
    c = coordinator.GooglePollenDataUpdateCoordinator(hass, SECRET, 1, 2, "en")
    with pytest.raises(UpdateFailed) as caught:
        await c._async_update_data()
    fetch.assert_awaited_once()
    assert_safe(caught.value, caplog)


@pytest.mark.parametrize(
    "error,expected",
    [
        (utils.PollenError("fixed"), UpdateFailed),
        (utils.PollenAuthError("fixed"), ConfigEntryAuthFailed),
    ],
)
async def test_coordinator_framework_errors(hass, monkeypatch, caplog, error, expected):
    monkeypatch.setattr(coordinator, "fetch_pollen_data", AsyncMock(side_effect=error))
    entry = MagicMock(state=ConfigEntryState.SETUP_IN_PROGRESS)
    c = coordinator.GooglePollenDataUpdateCoordinator(hass, SECRET, 1, 2, "en", config_entry=entry)
    with pytest.raises(expected) as caught:
        await c._async_update_data()
    assert_safe(caught.value, caplog)
    with pytest.raises((ConfigEntryNotReady, ConfigEntryAuthFailed)) as setup_error:
        await c.async_config_entry_first_refresh()
    assert not c.last_update_success
    assert SECRET not in caplog.text
    assert SECRET not in "".join(traceback.format_exception(setup_error.value))


def flow_for(hass):
    flow = config_flow.GooglePollenConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}
    flow.handler = "google_pollen"
    flow.flow_id = "test-flow"
    return flow


def user_input():
    return {"api_key": SECRET, "latitude": 1.123456, "longitude": 2.123456, "language": "en"}


async def test_initial_password_form_no_default(hass):
    result = await flow_for(hass).async_step_user()
    schema = result["data_schema"].schema
    key, validator = next((k, v) for k, v in schema.items() if k.schema == "api_key")
    assert validator.config["type"] == "password"
    assert SECRET not in str(result)
    assert key.default is vol.UNDEFINED


async def test_initial_flow_and_selections(hass, monkeypatch):
    monkeypatch.setattr(
        config_flow, "fetch_pollen_data", AsyncMock(return_value=payload(day("2026-10-27")))
    )
    flow = flow_for(hass)
    result = await flow.async_step_user(user_input())
    assert result["step_id"] == "select_pollen"
    assert "GRASS" in flow._pollen_categories_list
    result = await flow.async_step_select_pollen(
        {CONF_POLLEN_CATEGORIES: ["GRASS"], CONF_POLLEN: []}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["latitude"] == 1.1235
    assert (
        result["data"]["api_key"] == SECRET
    )  # HA's private entry is the intended credential store.


@pytest.mark.parametrize(
    "response,error,field",
    [
        (None, utils.PollenAuthError("fixed"), "api_key"),
        (None, utils.PollenError("fixed"), "base"),
        ({"dailyInfo": [{"date": SECRET}]}, None, "base"),
        (payload({"date": {"year": 2026, "month": 10, "day": 26}}), None, "base"),
    ],
)
async def test_flow_errors_do_not_echo_secret(hass, monkeypatch, caplog, response, error, field):
    fetch = AsyncMock(return_value=response, side_effect=error)
    monkeypatch.setattr(config_flow, "fetch_pollen_data", fetch)
    result = await flow_for(hass).async_step_user(user_input())
    fetch.assert_awaited_once()
    assert result["type"] == "form" and field in result["errors"]
    assert SECRET not in str(result) + caplog.text


@pytest.mark.parametrize("key", [None, "", "not-a-key", SECRET + "\n"])
async def test_invalid_key_never_requests(hass, monkeypatch, key):
    fetch = AsyncMock()
    monkeypatch.setattr(config_flow, "fetch_pollen_data", fetch)
    data = user_input()
    data["api_key"] = key
    result = await flow_for(hass).async_step_user(data)
    assert result["errors"] == {"api_key": "invalid_api_key"}
    fetch.assert_not_awaited()


async def test_real_sensor_setup_first_refresh_failure(hass, monkeypatch, caplog):
    monkeypatch.setattr(
        coordinator, "fetch_pollen_data", AsyncMock(side_effect=utils.PollenError("fixed"))
    )
    entry = MagicMock(
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        data={**user_input(), CONF_POLLEN_CATEGORIES: ["GRASS"], CONF_POLLEN: []},
    )
    add = MagicMock()
    with pytest.raises(ConfigEntryNotReady) as caught:
        await sensor.async_setup_entry(hass, entry, add)
    add.assert_not_called()
    assert SECRET not in "".join(traceback.format_exception(caught.value)) + caplog.text


def test_hacs_metadata_and_dependencies():
    root = Path(__file__).resolve().parents[1]
    hacs = json.loads((root / "hacs.json").read_text())
    manifest = json.loads((root / "custom_components/google_pollen/manifest.json").read_text())
    assert hacs["homeassistant"] == "2026.10.4"
    assert not hacs["zip_release"] and not hacs["content_in_root"]
    assert "country" not in hacs and "filename" not in hacs
    assert manifest["requirements"] == []
    assert manifest["codeowners"] == ["@sparkrussell"]
    assert (
        "sparkrussell" in manifest["documentation"] and "sparkrussell" in manifest["issue_tracker"]
    )
    assert [
        p.name
        for p in (root / "custom_components").iterdir()
        if p.is_dir() and not p.name.startswith("__")
    ] == ["google_pollen"]
    assert "Sven-Ove Bjerkan" in (root / "LICENSE").read_text()


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_end_to_end_transport_flow_and_ha_tracebacks(hass, monkeypatch, caplog, status):
    import logging

    caplog.set_level(logging.DEBUG)
    response = Response(status=status, data={"error": {"message": SECRET}})
    _, session = transport(monkeypatch, response=response)
    result = await flow_for(hass).async_step_user(user_input())
    assert result["type"] == "form" and result["errors"]
    assert SECRET not in str(result)
    entry = MagicMock(state=ConfigEntryState.SETUP_IN_PROGRESS)
    c = coordinator.GooglePollenDataUpdateCoordinator(hass, SECRET, 1, 2, "en", config_entry=entry)
    with pytest.raises((ConfigEntryNotReady, ConfigEntryAuthFailed)) as caught:
        await c.async_config_entry_first_refresh()
    assert session.get.call_count == 2
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    # Exercise HA's runtime logger, including its DEBUG full traceback path.
    c.last_update_success = True
    await c.async_refresh()
    assert not c.last_update_success and c.last_exception is not None
    assert session.get.call_count == 3 and not response.read
    assert SECRET not in caplog.text
    assert_safe(c.last_exception, caplog)


async def test_real_ha_entity_state_and_time_callback(hass, monkeypatch):
    import logging
    from homeassistant.helpers.entity_component import EntityComponent

    s = make_sensor(
        hass, monkeypatch, payload(day(value=0, category="None"), day("2026-10-27", 4, "High"))
    )
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers import frame

    frame.async_setup(hass)
    entry = ConfigEntry(
        data={},
        domain="google_pollen",
        title="Test",
        version=1,
        minor_version=1,
        options={},
        source=SOURCE_USER,
        unique_id="test",
        discovery_keys={},
        subentries_data=[],
        state=ConfigEntryState.SETUP_IN_PROGRESS,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    component = EntityComponent(logging.getLogger(__name__), "sensor", hass)
    component._platforms["sensor"].config_entry = entry
    await component.async_add_entities([s])
    state = hass.states.get(s.entity_id)
    assert state is not None and state.state == "None"
    assert state.attributes["index_value"] == 0
    assert state.attributes["forecast"][1]["date"] == "2026-10-27"
    assert state.attributes["tomorrow"] == 4
    assert SECRET not in str(state)
    monkeypatch.setattr(dt_util, "now", lambda: NOW + timedelta(days=1))
    s._async_check_time(NOW)
    assert hass.states.get(s.entity_id).state == "High"
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW + timedelta(hours=8))
    s._async_check_time(NOW)
    assert hass.states.get(s.entity_id).state == "unavailable"
    await component._platforms["sensor"].async_reset()
    await s.coordinator.async_shutdown()


async def test_successful_initial_sensor_platform(hass, monkeypatch):
    monkeypatch.setattr(coordinator, "fetch_pollen_data", AsyncMock(return_value=payload()))
    entry = MagicMock(
        state=ConfigEntryState.SETUP_IN_PROGRESS,
        data={**user_input(), CONF_POLLEN_CATEGORIES: ["GRASS"], CONF_POLLEN: []},
    )
    add = MagicMock()
    await sensor.async_setup_entry(hass, entry, add)
    add.assert_called_once()
    entities = add.call_args.args[0]
    assert len(entities) == 1
    assert entities[0].coordinator.data["GRASS"]["2026-10-26"]["index_value"] == 2
    assert entities[0].unique_id == "google_pollen_grass_1.123456_2.123456"
    await entities[0].coordinator.async_shutdown()


async def test_reconfigure_no_credential_defaults_and_safe_error(hass, monkeypatch, caplog):
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    entry = MagicMock(data={**user_input(), CONF_POLLEN_CATEGORIES: ["GRASS"], CONF_POLLEN: []})
    monkeypatch.setattr(hass.config_entries, "async_get_entry", lambda _: entry)
    monkeypatch.setattr(config_flow, "fetch_pollen_data", AsyncMock(return_value=payload()))
    flow = flow_for(hass)
    flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": "test-entry"}
    result = await flow.async_step_reconfigure()
    assert result["type"] == "form" and result["step_id"] == "reconfigure"
    assert SECRET not in str(result)
    assert {key.schema for key in result["data_schema"].schema} == {
        CONF_POLLEN_CATEGORIES,
        CONF_POLLEN,
    }
    monkeypatch.setattr(
        config_flow, "fetch_pollen_data", AsyncMock(side_effect=utils.PollenError("fixed"))
    )
    result = await flow.async_step_reconfigure()
    assert result["type"] == "abort" and result["reason"] == "cannot_connect"
    assert SECRET not in str(result) + caplog.text


async def test_reconfigure_preserves_key_and_location(hass, monkeypatch):
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    entry = MagicMock(data={**user_input(), CONF_POLLEN_CATEGORIES: ["GRASS"], CONF_POLLEN: []})
    monkeypatch.setattr(hass.config_entries, "async_get_entry", lambda _: entry)
    update = MagicMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_update_entry", update)
    monkeypatch.setattr(
        config_flow, "fetch_pollen_data", AsyncMock(return_value=payload(day(code="TREE")))
    )
    flow = flow_for(hass)
    flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": "test-entry"}
    result = await flow.async_step_reconfigure({CONF_POLLEN_CATEGORIES: ["TREE"], CONF_POLLEN: []})
    assert result["type"] == "abort" and result["reason"] == "reconfigured"
    update.assert_called_once()
    saved = update.call_args.kwargs["data"]
    assert saved["api_key"] == SECRET
    assert saved["latitude"] == entry.data["latitude"]
    assert saved["longitude"] == entry.data["longitude"]
    assert saved[CONF_POLLEN_CATEGORIES] == ["TREE"]
