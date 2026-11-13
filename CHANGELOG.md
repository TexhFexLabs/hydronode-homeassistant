# Changelog

All notable changes to the HydroNode Home Assistant integration.

## [0.6.2] - unreleased

### Added

- Interfaces: with `direction` from the server, the entity kind follows it. Data points HydroNode switches
  (`BOTH`, `OUT`) become switch, select or number; data points that only come from the device (`IN`) or are made
  in HydroNode (`LOCAL`) stay readings, even when marked writable. `OUT` without a reported state sets
  `assumed_state`.
- A device with one sensor shows the interface that feeds it as model, e.g. "MQTT interface Home broker". The
  device name stays, so automations and entity IDs are unchanged.

### Unchanged

- Servers without `direction` decide from `writable` as before. Discovered devices and interfaces themselves are
  not mirrored to Home Assistant.

## [0.6.1] - unreleased

### Added

- A switch, select or number follows its device at once: the WebSocket event `desired.updated` sets
  `write_state` (`pending`, `confirmed`, `failed`) the moment the device confirms or declines, also for
  changes made in the app or by an automation. A confirmed switch without its own state report shows
  the new value right away. Before, the answer came with the next poll (up to 60 s).

### Unchanged

- Servers without the event behave as before; the 60 s polling stays.

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

- Live updates over the WebSocket for every data point (`dataPointId` in `value.updated`): MQTT, push,
  virtual and external sensors, texts and references, not only device series.
- References of virtual sensors as entities of their own (`dp_{reference}`, attribute `reference_of`).
- Read-only tokens (`hrt_`): the setup accepts them after a "read only" notice; sensors and states
  only, no switches, selects or numbers.

### Unchanged

- Every existing entity keeps its unique ID and entity ID. Device numbers stay as they are.
