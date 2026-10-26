# Google Pollen — Steve-owned hardened copy

Home Assistant custom integration for Google Pollen API, derived from
[svenove/home-assistant-google-pollen v0.32.0](https://github.com/svenove/home-assistant-google-pollen/tree/47e9769ce80731c1b5f62c60d9ad21cdfd2e921d)
under the original MIT license. This independently maintained copy is not
endorsed by Google or the upstream author. See [provenance](NOTICE.md).

**Review-stage code: do not install before independent review and merge.**
The default branch starts as a seed only. No live installation or IQVIA cutover
has been performed. The parent reports Google API access verified privately;
HA installation and consumer acceptance remain pending.

## Hardened behavior

- Header-only API key, fixed HTTPS endpoint, no redirects or raw-response logs.
- Fixed-message exceptions with no raw request/response exception chain.
- Password-selector key entry with no credential defaults.
- Unknown stays unknown; true zero is preserved. Forecasts align by API date.
- Four-hour cloud polling; request failure, missing current index and ≥8h age
  make sensors unavailable. Local rollover/freshness checks need no API call.
- HACS 2.0.5 source packaging, no ZIP-release dependency or country restriction.
- Target: HA 2026.10.4 / Python 3.14; no overrides of HA-owned dependencies.

Read **[OPERATIONS.md](OPERATIONS.md)** before installation: setup gates,
security limits, exact states/attributes/date contract, Google-vs-IQVIA scale,
update/rollback steps and repeatable offline verification.

No upstream submissions or live credentials are used by development tests.
