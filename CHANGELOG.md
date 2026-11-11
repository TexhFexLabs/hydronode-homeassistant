# Changelog

All notable changes to the HydroNode Home Assistant integration.

## [0.6.0] - unreleased

### Added

- Every request announces the HydroNode data platform (`X-HydroNode-Capabilities: data1`, WebSocket
  `caps`). Servers without it answer as before.
- MQTT, push, virtual and external sensors as devices (model *MQTT sensor*, *Push sensor*,
  *Virtual sensor*, *External data*), one entity per data point (`dp_{id}`).
- On/off data points as `binary_sensor`, choices as `enum` sensor, texts as text sensor.
- Writable data points as `switch`, `select` or `number` (`dp_{id}_write`) when the share allows
  changes and the token is a full-access token (`hat_`). A pending device command shows as
  `write_state: pending`.

### Unchanged

- Every existing entity keeps its unique ID and entity ID. Device numbers stay as they are.
