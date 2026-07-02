<p align="center">
  <img src="brands/hydronode/logo@2x.png" width="128" alt="HydroNode logo">
</p>

# HydroNode for Home Assistant

Home Assistant integration for [HydroNode](https://hydronode.com) — auto-discovered sensor
entities for every sensor you own, that's shared with you, or that you follow, live values
pushed over WebSocket, and anomaly / AI-analysis events on the Home Assistant event bus.

- **Auto-discovery** — new sensors and follows show up without reconfiguration (bootstrap
  refetch every 15 minutes and on every WebSocket reconnect).
- **Native graphs** — entities carry the correct `device_class` / `unit_of_measurement` /
  `state_class`, so Home Assistant's long-term statistics and history graphs work out of the box.
- **Hybrid transport** — REST polling every 60 s as a reliable baseline, WebSocket push for
  immediate updates. If the WebSocket drops, polling keeps entities current; no data loss, only
  a bit of latency.
- **Events, not just states** — anomaly detections and AI analyses fire on the HA event bus
  (`hydronode_anomaly`, `hydronode_ai_analysis`) with the full set of the sensor's current values
  attached, so automations can react directly.

---

## Installation

### HACS (custom repository)

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/TexhFexLabs/hydronode-homeassistant` as an **Integration**.
3. Search for **HydroNode** in HACS and install it.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/hydronode/` into your Home Assistant `config/custom_components/`
   directory.
2. Restart Home Assistant.

---

## Creating a Personal Access Token

The integration authenticates with a HydroNode **Personal Access Token** (`hat_…`), not your
regular account password. Tokens are created from an active HydroNode web/app session (never
from another PAT, so a leaked token can't mint further tokens):

```bash
curl -X POST https://hydronode.com/api/user/api-tokens \
  -H "Authorization: Bearer <your JWT session token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Home Assistant"}'
```

The response contains the token exactly once — copy it before closing the response, it cannot
be retrieved again (only revoked and replaced):

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "name": "Home Assistant",
  "token": "hat_abcdefghij1234567890abcdefghij1234",
  "createdAt": "2026-07-01T10:15:00Z"
}
```

If you ever need to revoke it (e.g. after removing the integration), use:

```bash
curl -X DELETE https://hydronode.com/api/user/api-tokens/{id} \
  -H "Authorization: Bearer <your JWT session token or a PAT>"
```

---

## Configuration

1. In Home Assistant, go to **Settings → Devices & Services → Add Integration** and search for
   **HydroNode**.
2. Enter your **Base URL** (default `https://hydronode.com`) and the **Personal Access Token**
   you created above.
3. The integration validates the token against `GET /api/ha/v1/bootstrap` and, on success,
   creates one device per station (including a pseudo-device for sensors without a station) and
   one sensor entity per `(sensor, type, channel)` combination.

### Options

Available under **Settings → Devices & Services → HydroNode → Configure**:

| Option | Default | Description |
|---|---|---|
| Poll interval | 60 s | How often `/api/ha/v1/states` is polled as the REST fallback. |
| Include followed public stations | on | Whether followed public stations show up as devices/entities. |
| Fire an event on every value update | off | Also fire `hydronode_value_updated` on the HA event bus for every WebSocket `value.updated` push (entity state is always updated regardless of this option). |

### Reauthentication

If your token is revoked or expires, Home Assistant raises a repair issue and prompts you to
re-enter a token (config entry **Reconfigure/Reauthenticate** flow) — no need to remove and
re-add the integration.

---

## Entities

- **Sensors** (`sensor.*`) — one per `(sensor, type, channel)`. Known HydroNode sensor types
  (temperature, humidity, CO2, PM2.5/PM10, pH, EC, rainfall, wind, and more — see
  [`const.py`](custom_components/hydronode/const.py) for the full list of 30+ types) get the
  matching `device_class`/`unit_of_measurement`/`state_class`. Unknown types still work as plain
  numeric sensors.
- **Events** (`event.*`) — one per station device, with `event_types: ["anomaly", "ai_analysis"]`.
  Each event's `event_data` mirrors the WebSocket payload (see below), so you can read it
  directly in automation triggers via `trigger.event.data`.

An entity becomes `unavailable` when its sensor is inactive on HydroNode, or when its last known
value is older than roughly 2× the poll interval.

Entity names are just the measurement title (the LoRaWAN channel name if configured, otherwise
the prettified type, e.g. `Battery_Voltage` or `Water Temperature`). The station device supplies
the prefix, so the full friendly name reads `<Station> <Title>`. Measurement types that have not
sent a value for 30 days are no longer discovered.

---

## Branding (logo in Home Assistant)

Home Assistant loads integration logos from [home-assistant/brands](https://github.com/home-assistant/brands),
not from the integration itself. The ready-made assets live in [`brands/hydronode/`](brands/hydronode/)
(`icon.png` 256×256, `icon@2x.png` 512×512). To get the logo displayed, open a PR against
`home-assistant/brands` adding these files under `custom_integrations/hydronode/`. Until that PR
is merged, Home Assistant shows a generic placeholder icon.

---

## Events & automation examples

### `hydronode_anomaly`

Fired on the HA event bus whenever HydroNode's anomaly detector flags an unexpected reading.
`event_data` includes `anomalyId`, `sensorId`, `stationId`, `sensorName`, `anomalyType`
(`SPIKE`/`DROP`/`GRADUAL_DRIFT`/`THRESHOLD_BREACH`), `deviation`, `currentValue`,
`expectedValue`, `timestamp`, and a `values` map with **every current reading of that sensor**
(e.g. `{"WATER_TEMPERATURE": 28.5, "WATER_PH": 6.8, "WATER_EC": 1200.0}`), so automations can
combine multiple readings without extra lookups.

```yaml
automation:
  - alias: "HydroNode: notify on anomaly"
    trigger:
      - platform: event
        event_type: hydronode_anomaly
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.anomalyType == 'SPIKE' }}"
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "HydroNode anomaly: {{ trigger.event.data.sensorName }}"
          message: >
            {{ trigger.event.data.anomalyType }} detected:
            {{ trigger.event.data.currentValue }}
            (expected ~{{ trigger.event.data.expectedValue }},
            {{ trigger.event.data.deviation }}σ off).

  - alias: "HydroNode: high temp + low humidity alert"
    trigger:
      - platform: event
        event_type: hydronode_anomaly
    condition:
      - condition: template
        value_template: >
          {{ trigger.event.data.values.get('TEMPERATURE', 0) > 30 and
             trigger.event.data.values.get('HUMIDITY', 100) < 50 }}
    action:
      - service: notify.mobile_app_my_phone
        data:
          message: "Greenhouse running hot and dry — check ventilation."
```

### `hydronode_ai_analysis`

Fired a few minutes after `hydronode_anomaly`, once HydroNode's AI review has finished. In
addition to the same base fields as `hydronode_anomaly`, `event_data` carries `provider` and an
`analysis` object: `explanation`, `confidence`, `air_quality_score`, `is_ventilation_event`,
`should_alert`, `alert_severity` (`NONE`/`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`), `environment_score`,
`affected_sensors`, and `recommendation`.

```yaml
automation:
  - alias: "HydroNode: AI-confirmed high-severity alert"
    trigger:
      - platform: event
        event_type: hydronode_ai_analysis
    condition:
      - condition: template
        value_template: >
          {{ trigger.event.data.analysis.should_alert and
             trigger.event.data.analysis.alert_severity in ['HIGH', 'CRITICAL'] }}
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "HydroNode: {{ trigger.event.data.analysis.alert_severity }} alert"
          message: >
            {{ trigger.event.data.sensorName }}: {{ trigger.event.data.analysis.explanation }}
            Recommendation: {{ trigger.event.data.analysis.recommendation }}
```

You can also trigger directly off entity state changes as with any other Home Assistant sensor:

```yaml
automation:
  - alias: "HydroNode: water temperature too high"
    trigger:
      - platform: numeric_state
        entity_id: sensor.wanne_1_water_temperature
        above: 28
    action:
      - service: notify.mobile_app_my_phone
        data:
          message: "Wanne 1 water temperature is above 28°C."
```

---

## Diagnostics

Config entry diagnostics (**Settings → Devices & Services → HydroNode → ⋮ → Download
diagnostics**) include the current bootstrap payload and states count, with the Personal Access
Token redacted.

---

## Backend API reference

This integration talks to the HydroNode HA API contract documented in
[`docs/api/HOMEASSISTANT_INTEGRATION.md`](https://github.com/TexhFexLabs/HydroNode/blob/main/docs/api/HOMEASSISTANT_INTEGRATION.md)
of the main HydroNode repository: token management, `GET /api/ha/v1/bootstrap`,
`GET /api/ha/v1/states`, public station follow/unfollow, and the `/ws/ha/v1` WebSocket protocol.

---

## License

Proprietary — © TexhFex Labs. See [`LICENSE`](LICENSE).
