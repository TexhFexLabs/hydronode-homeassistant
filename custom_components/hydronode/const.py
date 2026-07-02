"""Constants for the HydroNode integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_BILLION,
    CONCENTRATION_PARTS_PER_MILLION,
    DEGREE,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfIrradiance,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
)

DOMAIN = "hydronode"
MANUFACTURER = "TexhFex Labs"

# --- Config / options keys ---------------------------------------------------
CONF_BASE_URL = "base_url"
CONF_POLL_INTERVAL = "poll_interval"
CONF_INCLUDE_FOLLOWED = "include_followed"
CONF_FIRE_VALUE_EVENTS = "fire_value_events"

DEFAULT_BASE_URL = "https://hydronode.com"
DEFAULT_POLL_INTERVAL = 60  # seconds, per HOMEASSISTANT_INTEGRATION.md
MIN_POLL_INTERVAL = 15
DEFAULT_INCLUDE_FOLLOWED = True
DEFAULT_FIRE_VALUE_EVENTS = False

# --- REST paths ----------------------------------------------------------------
BOOTSTRAP_PATH = "/api/ha/v1/bootstrap"
STATES_PATH = "/api/ha/v1/states"
BOOTSTRAP_REFRESH_INTERVAL = 15 * 60  # seconds — auto-discovery per design doc §6

# --- WebSocket -----------------------------------------------------------------
WS_PATH = "/ws/ha/v1"
WS_AUTH_TIMEOUT = 10  # seconds
WS_MIN_BACKOFF = 1  # seconds
WS_MAX_BACKOFF = 60  # seconds
WS_CLOSE_AUTH_FAILED = 4401
WS_CLOSE_SESSION_LIMIT = 4429

MSG_TYPE_AUTH = "auth"
MSG_TYPE_AUTH_OK = "auth_ok"
EVENT_TYPE_VALUE_UPDATED = "value.updated"
EVENT_TYPE_ANOMALY_DETECTED = "anomaly.detected"
EVENT_TYPE_AI_ANALYZED = "anomaly.ai_analyzed"

# --- HA event-bus event names ---------------------------------------------------
EVENT_ANOMALY = "hydronode_anomaly"
EVENT_AI_ANALYSIS = "hydronode_ai_analysis"
EVENT_VALUE_UPDATED = "hydronode_value_updated"

# --- Availability ---------------------------------------------------------------
# A value older than this marks the entity unavailable. Generous on purpose:
# some channels (e.g. particulate matter) are only included in every Nth uplink,
# so a tight poll-based window would flap between value and "unavailable" and
# punch gaps into history graphs.
STALE_TIMEOUT_SECONDS = 2 * 60 * 60

# --- Sensor type mapping ---------------------------------------------------------
# type -> (device_class, native_unit_of_measurement, state_class)
# Reference: HydroNode CLAUDE.md "Sensor-Typen" / docs/api/INTEGRATION_README.md §5
SENSOR_TYPE_MAP: dict[
    str, tuple[SensorDeviceClass | None, str | None, SensorStateClass | None]
] = {
    # Luft/Klima (0x01-0x0F)
    "TEMPERATURE": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "HUMIDITY": (
        SensorDeviceClass.HUMIDITY,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    "BATTERY_VOLTAGE": (
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.VOLT,
        SensorStateClass.MEASUREMENT,
    ),
    "PRESSURE": (
        SensorDeviceClass.PRESSURE,
        UnitOfPressure.HPA,
        SensorStateClass.MEASUREMENT,
    ),
    "CO2": (
        SensorDeviceClass.CO2,
        CONCENTRATION_PARTS_PER_MILLION,
        SensorStateClass.MEASUREMENT,
    ),
    "PM25": (
        SensorDeviceClass.PM25,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    "PM10": (
        SensorDeviceClass.PM10,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    "LIGHT": (
        SensorDeviceClass.ILLUMINANCE,
        LIGHT_LUX,
        SensorStateClass.MEASUREMENT,
    ),
    "DISTANCE": (
        SensorDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        SensorStateClass.MEASUREMENT,
    ),
    "VOC": (
        SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        CONCENTRATION_PARTS_PER_BILLION,
        SensorStateClass.MEASUREMENT,
    ),
    "NO2": (
        SensorDeviceClass.NITROGEN_DIOXIDE,
        CONCENTRATION_PARTS_PER_BILLION,
        SensorStateClass.MEASUREMENT,
    ),
    "TEMPERATURE2": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "HUMIDITY2": (
        SensorDeviceClass.HUMIDITY,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    "TEMPERATURE3": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "HUMIDITY3": (
        SensorDeviceClass.HUMIDITY,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    # Boden (0x10-0x13)
    "SOIL_MOISTURE": (
        SensorDeviceClass.MOISTURE,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    "SOIL_TEMPERATURE": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "SOIL_PH": (
        SensorDeviceClass.PH,
        "pH",
        SensorStateClass.MEASUREMENT,
    ),
    "SOIL_EC": (
        None,
        "mS/cm",
        SensorStateClass.MEASUREMENT,
    ),
    # Wasser (0x20-0x25)
    "WATER_TEMPERATURE": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_PH": (
        SensorDeviceClass.PH,
        "pH",
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_EC": (
        None,
        "mS/cm",
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_DISSOLVED_OXYGEN": (
        None,
        "mg/L",
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_TURBIDITY": (
        None,
        "NTU",
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_LEVEL": (
        None,
        UnitOfLength.CENTIMETERS,
        SensorStateClass.MEASUREMENT,
    ),
    # Wetter (0x30-0x34)
    "WIND_SPEED": (
        SensorDeviceClass.WIND_SPEED,
        UnitOfSpeed.METERS_PER_SECOND,
        SensorStateClass.MEASUREMENT,
    ),
    "WIND_DIRECTION": (
        None,
        DEGREE,
        SensorStateClass.MEASUREMENT,
    ),
    "RAINFALL": (
        SensorDeviceClass.PRECIPITATION,
        UnitOfPrecipitationDepth.MILLIMETERS,
        SensorStateClass.TOTAL_INCREASING,
    ),
    "UV_INDEX": (
        None,
        "UV index",
        SensorStateClass.MEASUREMENT,
    ),
    "SOLAR_RADIATION": (
        SensorDeviceClass.IRRADIANCE,
        UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        SensorStateClass.MEASUREMENT,
    ),
    # Sonstige (0x40-0x43)
    "SOUND_LEVEL": (
        None,
        "dB",
        SensorStateClass.MEASUREMENT,
    ),
    "VIBRATION": (
        None,
        "g",
        SensorStateClass.MEASUREMENT,
    ),
    "CURRENT": (
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.AMPERE,
        SensorStateClass.MEASUREMENT,
    ),
    "POWER": (
        SensorDeviceClass.POWER,
        UnitOfPower.WATT,
        SensorStateClass.MEASUREMENT,
    ),
}

# Unknown sensor types fall back to a generic numeric sensor.
GENERIC_SENSOR_TYPE: tuple[
    SensorDeviceClass | None, str | None, SensorStateClass | None
] = (None, None, SensorStateClass.MEASUREMENT)
