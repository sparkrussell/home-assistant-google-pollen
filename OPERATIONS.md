# Google Pollen operations

## Status and gates

This is preparation, not a completed IQVIA cutover. No live HA credentials were
retrieved, no live HA install/reload/restart was performed, and no automation was
modified by the coder. Independent review of the exact PR head must precede merge.
The default branch initially contains only a non-installable seed until that merge.

**Cloud access gate cleared by the parent session:** Steve saved the Google Cloud
API restriction change. The parent then privately verified HTTP 200 with forecast
data and GRASS/TREE/WEED in the response. This supersedes its earlier HTTP 403
`API_KEY_SERVICE_BLOCKED` result. The coder did not retrieve the key or repeat
that probe. Preserve the working Air Quality restriction when maintaining Pollen
access. Live integration installation, entity acceptance and consumer cutover
remain unperformed and separately gated by independent review.

## Security model

- Official Google documentation supports `X-Goog-Api-Key` for REST API-key
  authentication. This integration sends the key only in that HTTPS header to
  the fixed Pollen endpoint; never in query parameters. Redirects are disabled.
- A private, short-lived aiohttp session has a 30-second total timeout, no shared
  request-trace hooks, no environment proxy inheritance and no automatic
  raise-for-status. System TLS certificate validation remains enabled.
- Error bodies are not read. Network/JSON exceptions are discarded at the
  boundary, replaced with fixed messages outside exception handlers. Neither
  the raw exception nor its context/cause crosses into HA/aiohttp logging.
- Config-flow and coordinator never log responses, request reprs, headers,
  API keys or raw exceptions. Parser exceptions receive the same treatment.
- Initial API-key entry uses a password selector without a stored-key default.
  This masks UI entry; it does **not** encrypt HA's private config entry store.
  Protect HA admin access, backups, `/config`, and add-ons with config access.
- No diagnostics export, `.env`, secondary secret file or automated real-key
  test is provided. Do not enable HTTP header capture, debugger-local dumps or
  upload HA config-entry schemas/defaults. Ordinary tracebacks are tested;
  process memory, crash dumps, malicious instrumentation and debugger locals
  are outside this boundary and can still contain the credential.
- Use a dedicated Pollen-only key if practical, API restriction to Pollen API,
  and a server-side IP application restriction when a stable public egress IP
  is available. Browser-referrer restrictions are not appropriate to HA.
  Set quotas/billing alerts. Never paste keys into chat, URLs or shell arguments.

## Install after independent review and merge only

1. Record approved head/merge SHA, HA backup ID and current IQVIA consumer mapping.
   Keep IQVIA and its automations unchanged during additive validation. Do not
   install upstream and this copy simultaneously: both use `google_pollen`.
2. In Google Cloud, enable Pollen API and billing in the intended project; Steve
   privately supplies a suitably restricted key through HA's UI, not an agent
   transcript. The parent reports the API restriction gate above cleared.
3. In HACS 2.0.5, add custom repository
   `https://github.com/sparkrussell/home-assistant-google-pollen`, category
   **Integration**. Download only after the reviewed code is on default `main`.
   This copy uses source downloads, no release ZIP, no Norway-only restriction,
   and a fully qualified minimum HA version `2026.10.4`. Do not copy upstream
   PR #17's `info.json`; HACS 2.0.5 reads `hacs.json`.
4. Restart HA only in the separately authorized installation window. Add
   **Google Pollen** in Settings → Devices & Services. Enter key, coordinates
   and language. Select GRASS, TREE, WEED and only desired plant species.
   Initial setup validates the API; invalid/restricted auth returns a fixed
   error, transient failure stays in the form. The integration creates and first-
   refreshes its shared coordinator before forwarding sensor setup: transient or
   parser failure leaves the entry in `SETUP_RETRY`, auth failure in `SETUP_ERROR`,
   never a successfully loaded entry with zero sensors. HA owns retry scheduling.
5. Verify actual entity IDs from the HA entity registry, not this document's
   guesses. Inspect only non-secret state/attributes. Confirm local forecast
   dates, explicit missing data, four-hour polling and unavailable behavior.
   No synthetic no-pollen value may be used as a real Google response.
6. Only after real data acceptance, independently review the Morning Air and
   pollen-alert consumer diff. Swap those consumers separately with IQVIA kept
   available for rollback. Do not present installation as consumer cutover.

## Consumer data contract

Domain: `google_pollen`. One sensor per selected category/species. Unique ID
format retained: `google_pollen_<lowercase-code>_<latitude>_<longitude>`;
coordinates are rounded to four decimals by initial config flow. Entity IDs
are assigned by HA and may have location/name collision suffixes.

| Field | Meaning |
| --- | --- |
| state | Google's localized category string (e.g. `Low`, `High`, `None`); **not** numeric UPI. No enum/measurement class is asserted. |
| availability | False after request failure, missing today's valid index/category, future-only data, or data age ≥8h. HA then displays `unavailable`. |
| `index_value` | Explicit integer UPI 0–5, or null. Missing/null/invalid `indexInfo` is never synthesized as zero. Explicit zero remains zero. |
| `category` | Localized category or null; not suitable for language-independent numeric thresholds. |
| `date` | Actual API calendar date for today's row, ISO `YYYY-MM-DD`; placeholder local date when current data is absent. |
| `last_updated` | UTC-aware successful fetch timestamp, not API observation/issuance time. Failure does not renew it. |
| `in_season` | Native boolean or null (not string). Absence/off-season alone does not establish zero. |
| `display_name` | Provider display name, falling back to the code. May be absent when today's row is missing. |
| `forecast` | Four ordered objects `{date, index_value, category}` for HA-local today through today+3; actual API dates are matched, never response positions. Missing day/species/index is null. |
| `tomorrow`, `day 3`, `day 4` | Legacy numeric forecast aliases (1, 2, 3 days ahead), null if unavailable. Use `forecast` dates for new consumers. |

Dates are matched to HA's configured local calendar, assumed to describe the
requested location. If HA's timezone differs from that location, explicitly
review date semantics before using the feed. A one-minute local timer re-evaluates
day rollover and 8h freshness without extra API traffic. Polls request four days
at a time every four hours; no missing-date compression. A response with a page
token is rejected rather than silently using an incomplete page. Duplicate or
malformed dates reject the update. Historical cache attributes can remain on
unavailable states: **availability and freshness are mandatory consumer gates**.

Google UPI is 0–5; IQVIA's scale and categories are different. Never copy an IQVIA
threshold onto Google. Proposed consumer logic: require available state, current
date, non-null integer index, and acceptable `last_updated` age; only then apply
a separately chosen Google UPI threshold. Treat unavailable as “pollen data
unavailable,” never “no pollen.” Forecast text must label its calendar date.

## Update and rollback

- Updates belong to this owned repo only. Compare against the pinned upstream
  baseline, run tests, open focused PR, get independent review, then merge.
  HACS follows owned `main`; do not silently switch back to upstream. No automated
  dependency overrides or release workflow is installed. Manifest requirements
  are empty because HA supplies aiohttp/Voluptuous; tests pin HA 2026.10.4.
- Record the installed immutable SHA before every update. Re-run offline tests
  for a new HA target before changing the stated support baseline. Confirm
  downloaded source after an authorized HACS update; restart only as required.
- Reconfigure supports pollen selections only, preserving the key and location.
  It does not prefill/expose a key. Key rotation has no dedicated reauth UI in
  this scope: perform a separately planned remove/re-add with recorded entity
  mapping; never edit `.storage` directly or expose entry defaults to an agent.
- Before consumer cutover rollback means disable/remove this additive entry
  and restore its reviewed prior source/HA backup if necessary; leave IQVIA
  untouched. After consumer cutover restore the saved IQVIA consumer payload
  first and verify its state, then disable Google. Do not reinstall unpatched
  upstream code with a live key: its URL/logging paths can leak credentials.
- If any key appears in output, treat it as compromised and rotate/revoke it;
  deleting a log or transcript is not remediation.

## Offline verification

```sh
uv venv --python 3.14 /home/spark/.hermes/cache/scratch/google-pollen-venv
uv pip install --python /home/spark/.hermes/cache/scratch/google-pollen-venv/bin/python -r requirements-test.txt
/home/spark/.hermes/cache/scratch/google-pollen-venv/bin/python -m pytest -q --basetemp=/home/spark/.hermes/cache/scratch/google-pollen-pytest
/home/spark/.hermes/cache/scratch/google-pollen-venv/bin/ruff check custom_components tests scripts
/home/spark/.hermes/cache/scratch/google-pollen-venv/bin/ruff format --check custom_components tests scripts
```

For the **official HACS 2.0.5 schema** and source layout check (not a live HACS
installation), obtain its public source checkout at
`c0dfd8b44297c3673c21973e2539375a53687a9c`, then run:

```sh
/home/spark/.hermes/cache/scratch/google-pollen-venv/bin/python scripts/validate_hacs.py --hacs-source /home/spark/.hermes/cache/scratch/google-pollen-hacs
```

Tests use real installed HA classes and aiohttp, mocked Google responses plus a
loopback HTTP server, a synthetic sentinel and an external-connect guard. Full
`ConfigEntries.async_setup` regressions exercise initial transient/parser failure,
HA's startup-triggered retry and recovery, auth failure, successful sensor creation
and unload cleanup. These do not mock platform forwarding; HA's dependency
installer is disabled. No Google key or live HA network access is required. This is bounded offline
compatibility/security verification, not proof of geographic coverage, live
billing/API access, a rendered HACS installation or a completed consumer cutover.

## References

- https://docs.cloud.google.com/docs/authentication/api-keys-use#using-with-rest
- https://developers.google.com/maps/documentation/pollen/reference/rest/v1/forecast/lookup
- https://developers.google.com/maps/api-security-best-practices
- https://www.hacs.xyz/docs/publish/integration/
- https://github.com/hacs/integration/blob/c0dfd8b44297c3673c21973e2539375a53687a9c/custom_components/hacs/utils/validate.py
- Source and licensing: [NOTICE.md](NOTICE.md), [LICENSE](LICENSE)
