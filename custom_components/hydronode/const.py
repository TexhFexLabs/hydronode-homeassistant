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
    SIGNAL_STRENGTH_DECIBELS,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfIrradiance,
    UnitOfLength,
    UnitOfMass,
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

DEFAULT_BASE_URL = "https://hydronode.texhfexlabs.de"
DEFAULT_POLL_INTERVAL = 60  # seconds, per HOMEASSISTANT_INTEGRATION.md
MIN_POLL_INTERVAL = 15
DEFAULT_INCLUDE_FOLLOWED = True
DEFAULT_FIRE_VALUE_EVENTS = False

# --- REST paths ----------------------------------------------------------------
BOOTSTRAP_PATH = "/api/ha/v1/bootstrap"
STATES_PATH = "/api/ha/v1/states"
BOOTSTRAP_REFRESH_INTERVAL = 5 * 60  # seconds — auto-discovery per design doc §6

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
    "DEW_POINT": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "HEAT_INDEX": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    "ABS_HUMIDITY": (None, "g/m³", SensorStateClass.MEASUREMENT),
    # Gas / air quality (webapp units; device_class only where HA accepts the unit)
    "CO": (
        SensorDeviceClass.CO,
        CONCENTRATION_PARTS_PER_MILLION,
        SensorStateClass.MEASUREMENT,
    ),
    "O2": (None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    "O3": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "SO2": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "NO": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "NH3": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "H2S": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "CH4": (None, CONCENTRATION_PARTS_PER_MILLION, SensorStateClass.MEASUREMENT),
    "LPG": (None, CONCENTRATION_PARTS_PER_MILLION, SensorStateClass.MEASUREMENT),
    "HCHO": (None, CONCENTRATION_PARTS_PER_BILLION, SensorStateClass.MEASUREMENT),
    "RADON": (None, "Bq/m³", SensorStateClass.MEASUREMENT),
    "IAQ": (None, None, SensorStateClass.MEASUREMENT),
    "AQI": (SensorDeviceClass.AQI, None, SensorStateClass.MEASUREMENT),
    # Particulate matter — mass concentration
    "PM1_0": (
        SensorDeviceClass.PM1,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    "PM2_5": (
        SensorDeviceClass.PM25,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    "PM4_0": (
        None,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    "PM10_0": (
        SensorDeviceClass.PM10,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
    # Particulate matter — number concentration
    "NC_0_5": (None, "#/cm³", SensorStateClass.MEASUREMENT),
    "NC_1_0": (None, "#/cm³", SensorStateClass.MEASUREMENT),
    "NC_2_5": (None, "#/cm³", SensorStateClass.MEASUREMENT),
    "NC_4_0": (None, "#/cm³", SensorStateClass.MEASUREMENT),
    "NC_10_0": (None, "#/cm³", SensorStateClass.MEASUREMENT),
    "TYP_SIZE": (None, "µm", SensorStateClass.MEASUREMENT),
    # Light & radiation
    "UV_A": (None, "mW/m²", SensorStateClass.MEASUREMENT),
    "UV_B": (None, "mW/m²", SensorStateClass.MEASUREMENT),
    "PAR": (None, "µmol/m²s", SensorStateClass.MEASUREMENT),
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
    "SOIL_NITROGEN": (None, "mg/kg", SensorStateClass.MEASUREMENT),
    "SOIL_PHOSPHORUS": (None, "mg/kg", SensorStateClass.MEASUREMENT),
    "SOIL_POTASSIUM": (None, "mg/kg", SensorStateClass.MEASUREMENT),
    "SOIL_SALINITY": (None, "mS/cm", SensorStateClass.MEASUREMENT),
    "SOIL_CO2": (
        SensorDeviceClass.CO2,
        CONCENTRATION_PARTS_PER_MILLION,
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
    "WATER_ORP": (
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.MILLIVOLT,
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_SALINITY": (None, "ppt", SensorStateClass.MEASUREMENT),
    "WATER_FLOW_RATE": (None, "L/min", SensorStateClass.MEASUREMENT),
    "WATER_PRESSURE": (
        SensorDeviceClass.PRESSURE,
        UnitOfPressure.BAR,
        SensorStateClass.MEASUREMENT,
    ),
    "WATER_NITRATE": (None, "mg/L", SensorStateClass.MEASUREMENT),
    "WATER_AMMONIA": (None, "mg/L", SensorStateClass.MEASUREMENT),
    "TANK_LEVEL": (None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    # Wetter (0x30-0x34)
    "WIND_SPEED": (
        SensorDeviceClass.WIND_SPEED,
        UnitOfSpeed.METERS_PER_SECOND,
        SensorStateClass.MEASUREMENT,
    ),
    "WIND_GUST": (
        SensorDeviceClass.WIND_SPEED,
        UnitOfSpeed.METERS_PER_SECOND,
        SensorStateClass.MEASUREMENT,
    ),
    "RAINFALL_RATE": (
        SensorDeviceClass.PRECIPITATION_INTENSITY,
        "mm/h",
        SensorStateClass.MEASUREMENT,
    ),
    "SNOW_DEPTH": (
        SensorDeviceClass.DISTANCE,
        UnitOfLength.CENTIMETERS,
        SensorStateClass.MEASUREMENT,
    ),
    "LEAF_WETNESS": (None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    "EVAPOTRANSPIRATION": (None, "mm", SensorStateClass.MEASUREMENT),
    "CLOUD_COVER": (None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    "VISIBILITY": (
        SensorDeviceClass.DISTANCE,
        UnitOfLength.METERS,
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
    "VOLTAGE": (
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.VOLT,
        SensorStateClass.MEASUREMENT,
    ),
    "ENERGY": (
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
    ),
    "POWER_FACTOR": (
        SensorDeviceClass.POWER_FACTOR,
        None,
        SensorStateClass.MEASUREMENT,
    ),
    "FREQUENCY": (
        SensorDeviceClass.FREQUENCY,
        UnitOfFrequency.HERTZ,
        SensorStateClass.MEASUREMENT,
    ),
    "APPARENT_POWER": (
        SensorDeviceClass.APPARENT_POWER,
        UnitOfApparentPower.VOLT_AMPERE,
        SensorStateClass.MEASUREMENT,
    ),
    "REACTIVE_POWER": (
        SensorDeviceClass.REACTIVE_POWER,
        "var",
        SensorStateClass.MEASUREMENT,
    ),
    "SOLAR_VOLTAGE": (
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.VOLT,
        SensorStateClass.MEASUREMENT,
    ),
    "SOLAR_CURRENT": (
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.AMPERE,
        SensorStateClass.MEASUREMENT,
    ),
    "SOLAR_POWER": (
        SensorDeviceClass.POWER,
        UnitOfPower.WATT,
        SensorStateClass.MEASUREMENT,
    ),
    "BATTERY_PERCENTAGE": (
        SensorDeviceClass.BATTERY,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    "BATTERY_CURRENT": (
        SensorDeviceClass.CURRENT,
        UnitOfElectricCurrent.AMPERE,
        SensorStateClass.MEASUREMENT,
    ),
    "BATTERY_TEMPERATURE": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
    ),
    # Physical / IoT
    "ALTITUDE": (
        SensorDeviceClass.DISTANCE,
        UnitOfLength.METERS,
        SensorStateClass.MEASUREMENT,
    ),
    "FLOW_RATE": (None, "L/min", SensorStateClass.MEASUREMENT),
    "WEIGHT": (
        SensorDeviceClass.WEIGHT,
        UnitOfMass.KILOGRAMS,
        SensorStateClass.MEASUREMENT,
    ),
    "FORCE": (None, "N", SensorStateClass.MEASUREMENT),
    "ACCELERATION_X": (None, "m/s²", SensorStateClass.MEASUREMENT),
    "ACCELERATION_Y": (None, "m/s²", SensorStateClass.MEASUREMENT),
    "ACCELERATION_Z": (None, "m/s²", SensorStateClass.MEASUREMENT),
    "GYRO_X": (None, "°/s", SensorStateClass.MEASUREMENT),
    "GYRO_Y": (None, "°/s", SensorStateClass.MEASUREMENT),
    "GYRO_Z": (None, "°/s", SensorStateClass.MEASUREMENT),
    "TILT": (None, DEGREE, SensorStateClass.MEASUREMENT),
    "MOTION": (None, None, SensorStateClass.MEASUREMENT),
    "DOOR_STATE": (None, None, SensorStateClass.MEASUREMENT),
    "LEAK_DETECTED": (None, None, SensorStateClass.MEASUREMENT),
    "PEOPLE_COUNT": (None, None, SensorStateClass.MEASUREMENT),
    "DOOR_OPEN_COUNT": (None, None, SensorStateClass.MEASUREMENT),
    "RSSI": (
        SensorDeviceClass.SIGNAL_STRENGTH,
        SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        SensorStateClass.MEASUREMENT,
    ),
    "SNR": (
        SensorDeviceClass.SIGNAL_STRENGTH,
        SIGNAL_STRENGTH_DECIBELS,
        SensorStateClass.MEASUREMENT,
    ),
    "GPS_SPEED": (
        SensorDeviceClass.SPEED,
        UnitOfSpeed.KILOMETERS_PER_HOUR,
        SensorStateClass.MEASUREMENT,
    ),
}

# Unknown sensor types fall back to a generic numeric sensor.
GENERIC_SENSOR_TYPE: tuple[
    SensorDeviceClass | None, str | None, SensorStateClass | None
] = (None, None, SensorStateClass.MEASUREMENT)

# --- Display precision -----------------------------------------------------------
# Backend delivers the LoRa fPort channel scale per (sensor, type, channel); when a
# channel has no fPort config the display default is 0.01 → 2 decimal places.
DEFAULT_SCALE = 0.01

# --- Friendly display names ------------------------------------------------------
# type -> human-readable entity name. Mirror of the webapp's SENSOR_TYPE_META
# (hydronode-web src/app/core/models/sensor-type.model.ts) so entities read
# "Particle Count >0.5µm" instead of "Nc_0_5". Unknown types fall back to
# Title-Case of the raw type name.
SENSOR_TYPE_NAMES: dict[str, str] = {
    # Core climate
    "TEMPERATURE": "Temperature",
    "TEMPERATURE2": "Temperature 2",
    "TEMPERATURE3": "Temperature 3",
    "HUMIDITY": "Humidity",
    "HUMIDITY2": "Humidity 2",
    "HUMIDITY3": "Humidity 3",
    "PRESSURE": "Air Pressure",
    "DEW_POINT": "Dew Point",
    "HEAT_INDEX": "Heat Index",
    "ABS_HUMIDITY": "Absolute Humidity",
    # Gas / air quality
    "CO2": "CO₂",
    "CO": "CO",
    "O2": "O₂",
    "VOC": "VOC",
    "O3": "Ozone (O₃)",
    "SO2": "SO₂",
    "NO2": "NO₂",
    "NO": "NO",
    "NH3": "Ammonia (NH₃)",
    "H2S": "Hydrogen Sulfide",
    "CH4": "Methane (CH₄)",
    "LPG": "LPG",
    "HCHO": "Formaldehyde",
    "RADON": "Radon",
    "IAQ": "IAQ Index",
    "AQI": "AQI",
    # Particulate matter — mass concentration
    "PM25": "PM2.5",
    "PM10": "PM10",
    "PM1_0": "PM1.0",
    "PM2_5": "PM2.5",
    "PM4_0": "PM4.0",
    "PM10_0": "PM10.0",
    # Particulate matter — number concentration
    "NC_0_5": "Particle Count >0.5µm",
    "NC_1_0": "Particle Count >1.0µm",
    "NC_2_5": "Particle Count >2.5µm",
    "NC_4_0": "Particle Count >4.0µm",
    "NC_10_0": "Particle Count >10µm",
    "TYP_SIZE": "Typical Particle Size",
    # Light & radiation
    "LIGHT": "Light",
    "UV_INDEX": "UV Index",
    "UV_A": "UV-A",
    "UV_B": "UV-B",
    "SOLAR_RADIATION": "Solar Radiation",
    "PAR": "PAR",
    # Weather
    "WIND_SPEED": "Wind Speed",
    "WIND_GUST": "Wind Gust",
    "WIND_DIRECTION": "Wind Direction",
    "RAINFALL": "Rainfall",
    "RAINFALL_RATE": "Rainfall Rate",
    "SNOW_DEPTH": "Snow Depth",
    "LEAF_WETNESS": "Leaf Wetness",
    "EVAPOTRANSPIRATION": "Evapotranspiration",
    "CLOUD_COVER": "Cloud Cover",
    "VISIBILITY": "Visibility",
    # Water
    "WATER_TEMPERATURE": "Water Temperature",
    "WATER_PH": "Water pH",
    "WATER_EC": "Water EC",
    "WATER_DISSOLVED_OXYGEN": "Dissolved Oxygen",
    "WATER_TURBIDITY": "Water Turbidity",
    "WATER_LEVEL": "Water Level",
    "WATER_ORP": "Water ORP",
    "WATER_SALINITY": "Water Salinity",
    "WATER_FLOW_RATE": "Water Flow Rate",
    "WATER_PRESSURE": "Water Pressure",
    "WATER_NITRATE": "Water Nitrate",
    "WATER_AMMONIA": "Water Ammonia",
    "TANK_LEVEL": "Tank Level",
    # Soil
    "SOIL_MOISTURE": "Soil Moisture",
    "SOIL_TEMPERATURE": "Soil Temperature",
    "SOIL_PH": "Soil pH",
    "SOIL_EC": "Soil EC",
    "SOIL_NITROGEN": "Soil Nitrogen",
    "SOIL_PHOSPHORUS": "Soil Phosphorus",
    "SOIL_POTASSIUM": "Soil Potassium",
    "SOIL_SALINITY": "Soil Salinity",
    "SOIL_CO2": "Soil CO₂",
    # Energy / electrical
    "VOLTAGE": "Voltage",
    "CURRENT": "Current",
    "POWER": "Power",
    "ENERGY": "Energy",
    "POWER_FACTOR": "Power Factor",
    "FREQUENCY": "Frequency",
    "APPARENT_POWER": "Apparent Power",
    "REACTIVE_POWER": "Reactive Power",
    "SOLAR_VOLTAGE": "Solar Voltage",
    "SOLAR_CURRENT": "Solar Current",
    "SOLAR_POWER": "Solar Power",
    "BATTERY_VOLTAGE": "Battery Voltage",
    "BATTERY_PERCENTAGE": "Battery",
    "BATTERY_CURRENT": "Battery Current",
    "BATTERY_TEMPERATURE": "Battery Temperature",
    # Physical / IoT
    "DISTANCE": "Distance",
    "ALTITUDE": "Altitude",
    "FLOW_RATE": "Flow Rate",
    "WEIGHT": "Weight",
    "FORCE": "Force",
    "SOUND_LEVEL": "Sound Level",
    "VIBRATION": "Vibration",
    "ACCELERATION_X": "Acceleration X",
    "ACCELERATION_Y": "Acceleration Y",
    "ACCELERATION_Z": "Acceleration Z",
    "GYRO_X": "Gyro X",
    "GYRO_Y": "Gyro Y",
    "GYRO_Z": "Gyro Z",
    "TILT": "Tilt",
    "MOTION": "Motion",
    "DOOR_STATE": "Door State",
    "LEAK_DETECTED": "Leak Detected",
    "PEOPLE_COUNT": "People Count",
    "DOOR_OPEN_COUNT": "Door Open Count",
    "RSSI": "RSSI",
    "SNR": "SNR",
    "GPS_SPEED": "GPS Speed",
}
