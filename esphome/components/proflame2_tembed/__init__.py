"""ESPHome external component skeleton for LilyGO T-Embed CC1101."""

from __future__ import annotations

import inspect

import esphome.automation as automation
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_CS_PIN, CONF_DATA_RATE, CONF_ID, CONF_SPI_ID

from esphome import pins
from esphome.components import i2c, sensor, spi, text_sensor

DEPENDENCIES = ["spi", "i2c", "api"]
AUTO_LOAD = ["sensor", "text_sensor"]

CONF_AIR_PAYLOAD_HEX = "air_payload_hex"
CONF_ASYNC_TX_DATA_PIN = "async_tx_data_pin"
CONF_PAYLOAD_BIT_LENGTH = "payload_bit_length"
CONF_BOARD_POWER_ENABLE_PIN = "board_power_enable_pin"
CONF_CC1101_CS_PIN = "cc1101_cs_pin"
CONF_CC1101_GDO0_PIN = "cc1101_gdo0_pin"
CONF_CC1101_GDO2_PIN = "cc1101_gdo2_pin"
CONF_DATA_RATE_BPS = "data_rate_bps"
CONF_DURATION_MS = "duration_ms"
CONF_DISPLAY_DEBUG_MODE = "display_debug_mode"
CONF_DISPLAY_DIM_TIMEOUT_MIN = "display_dim_timeout_min"
CONF_DISPLAY_WAKE_ON_ACTIVITY = "display_wake_on_activity"
CONF_INTER_FRAME_GAP_US = "inter_frame_gap_us"
CONF_POST_FRAME_IDLE_GAP_US = "post_frame_idle_gap_us"
CONF_MODE = "mode"
CONF_PRE_BURST_LOW_US = "pre_burst_low_us"
CONF_PRE_FRAME_LOW_US = "pre_frame_low_us"
CONF_PAYLOAD_BIT_LENGTH_OVERRIDE = "payload_bit_length_override"
CONF_PERIOD_US = "period_us"
CONF_DIAGNOSTIC_REPEAT_COUNT_OVERRIDE = "diagnostic_repeat_count_override"
CONF_REQUEST_ID = "request_id"
CONF_REPEAT_COUNT = "repeat_count"
CONF_RF_SWITCH_SW0_PIN = "rf_switch_sw0_pin"
CONF_RF_SWITCH_SW1_PIN = "rf_switch_sw1_pin"
CONF_RX_FREQUENCY_HZ = "rx_frequency_hz"
CONF_STATUS_TEXT = "status_text"
CONF_ENDPOINT_STATUS = "endpoint_status"
CONF_LAST_ERROR = "last_error"
CONF_LAST_TX_RESULT = "last_tx_result"
CONF_LAST_REQUEST_ID = "last_request_id"
CONF_LAST_TX_PATH = "last_tx_path"
CONF_LAST_PAYLOAD_HEX = "last_payload_hex"
CONF_LAST_MARCSTATE_BEFORE_TX = "last_marcstate_before_tx"
CONF_LAST_MARCSTATE_AFTER_TX = "last_marcstate_after_tx"
CONF_CC1101_PARTNUM = "cc1101_partnum"
CONF_CC1101_VERSION = "cc1101_version"
CONF_TX_SUCCESS_COUNT = "tx_success_count"
CONF_TX_FAILURE_COUNT = "tx_failure_count"
CONF_LAST_PAYLOAD_LENGTH = "last_payload_length"
CONF_LAST_REQUEST_REPEAT_COUNT = "last_request_repeat_count"
CONF_LAST_TX_ELAPSED_MS = "last_tx_elapsed_ms"
CONF_FIRMWARE_PROTOCOL_VERSION = "firmware_protocol_version"
CONF_CONFIG_REVISION = "config_revision"
CONF_BATTERY_PERCENT = "battery_percent"
CONF_RX_DROPPED_PACKET_COUNT = "rx_dropped_packet_count"
CONF_RX_NO_RF_CAPTURE_COUNT = "rx_no_rf_capture_count"
CONF_RX_INCOMPLETE_FIFO_COUNT = "rx_incomplete_fifo_count"
CONF_RX_DECODE_FAILED_COUNT = "rx_decode_failed_count"
CONF_RX_PROFILE_MISMATCH_COUNT = "rx_profile_mismatch_count"
CONF_RX_ACCEPTED_PACKET_COUNT = "rx_accepted_packet_count"
CONF_RX_TX_SUPPRESSED_COUNT = "rx_tx_suppressed_count"
CONF_RX_TRANSPORT_UNAVAILABLE_COUNT = "rx_transport_unavailable_count"
CONF_RX_LAST_REJECTION_SNAPSHOT = "rx_last_rejection_snapshot"
CONF_NATIVE_GROUP_TIMING_PROFILE = "native_group_timing_profile"
CONF_NATIVE_GROUP_REPEAT_BOUNDARY_MODE = "native_group_repeat_boundary_mode"
CONF_TX_FREQUENCY_HZ = "tx_frequency_hz"
CONF_TX_MODE = "tx_mode"
CONF_TX_REPEAT_COUNT = "tx_repeat_count"
CONF_TX_REPEAT_COUNT_SENSOR = "tx_repeat_count_sensor"
CONF_INTENDED_POWER = "intended_power"
CONF_INTENDED_FLAME = "intended_flame"
CONF_INTENDED_FAN = "intended_fan"
CONF_INTENDED_LIGHT = "intended_light"
CONF_INTENDED_PILOT = "intended_pilot"
CONF_INTENDED_THERMOSTAT = "intended_thermostat"
CONF_INTENDED_FRONT = "intended_front"
CONF_INTENDED_AUX = "intended_aux"
CONF_INTENDED_ACTION_LABEL = "intended_action_label"
CONF_FIREPLACE_NAME = "fireplace_name"

proflame2_tembed_ns = cg.esphome_ns.namespace("proflame2_tembed")
TXMode = proflame2_tembed_ns.enum("TXMode", is_class=True)
TestPatternMode = proflame2_tembed_ns.enum("TestPatternMode", is_class=True)
AsyncTxDataPin = proflame2_tembed_ns.enum("AsyncTxDataPin", is_class=True)
NativeGroupTimingProfile = proflame2_tembed_ns.enum(
    "NativeGroupTimingProfile", is_class=True
)
NativeGroupRepeatBoundaryMode = proflame2_tembed_ns.enum(
    "NativeGroupRepeatBoundaryMode", is_class=True
)
Proflame2TEmbedComponent = proflame2_tembed_ns.class_(
    "Proflame2TEmbedComponent",
    cg.Component,
    spi.SPIDevice,
    i2c.I2CDevice,
)
Proflame2TEmbedTxAction = proflame2_tembed_ns.class_(
    "Proflame2TEmbedTxAction",
    automation.Action,
)
Proflame2TEmbedTestPatternAction = proflame2_tembed_ns.class_(
    "Proflame2TEmbedTestPatternAction",
    automation.Action,
)
Proflame2TEmbedTxStatefulAction = proflame2_tembed_ns.class_(
    "Proflame2TEmbedTxStatefulAction",
    automation.Action,
)
Proflame2TEmbedDisplayStateUpdateAction = proflame2_tembed_ns.class_(
    "Proflame2TEmbedDisplayStateUpdateAction",
    automation.Action,
)


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(Proflame2TEmbedComponent),
        cv.GenerateID(CONF_SPI_ID): cv.use_id(spi.SPIComponent),
        cv.Optional(CONF_TX_FREQUENCY_HZ, default=314_973_000): cv.positive_int,
        cv.Optional(CONF_RX_FREQUENCY_HZ, default=314_973_000): cv.positive_int,
        cv.Optional(CONF_DATA_RATE_BPS, default=2_400): cv.positive_int,
        cv.Optional(CONF_TX_REPEAT_COUNT, default=5): cv.int_range(min=1, max=20),
        cv.Optional(CONF_INTER_FRAME_GAP_US, default=0): cv.positive_int,
        cv.Optional(CONF_POST_FRAME_IDLE_GAP_US, default=0): cv.positive_int,
        cv.Optional(CONF_DISPLAY_DEBUG_MODE, default=False): cv.boolean,
        cv.Optional(CONF_DISPLAY_DIM_TIMEOUT_MIN, default=1): cv.int_range(min=0),
        cv.Optional(CONF_DISPLAY_WAKE_ON_ACTIVITY, default=True): cv.boolean,
        cv.Optional(CONF_TX_MODE, default="repeated_strobe"): cv.one_of(
            "continuous_burst",
            "repeated_strobe",
            "clean_timing_test",
            "proflame_pwm_symbols",
            "proflame_native_groups",
            lower=True,
        ),
        cv.Optional(
            CONF_NATIVE_GROUP_TIMING_PROFILE, default="yardstick_compat"
        ): cv.one_of("yardstick_compat", "native_remote", lower=True),
        cv.Optional(
            CONF_NATIVE_GROUP_REPEAT_BOUNDARY_MODE, default="continuous_tx"
        ): cv.one_of("continuous_tx", "reenter_tx", lower=True),
        cv.Optional(CONF_PRE_BURST_LOW_US, default=0): cv.positive_int,
        cv.Optional(CONF_PRE_FRAME_LOW_US, default=0): cv.positive_int,
        cv.Optional(CONF_DIAGNOSTIC_REPEAT_COUNT_OVERRIDE, default=0): cv.int_range(min=0, max=20),
        cv.Optional(CONF_PAYLOAD_BIT_LENGTH_OVERRIDE, default=200): cv.positive_int,
        cv.Optional(CONF_ASYNC_TX_DATA_PIN, default="gdo0"): cv.one_of(
            "gdo0", "gdo2", lower=True
        ),
        cv.Required(CONF_BOARD_POWER_ENABLE_PIN): pins.gpio_output_pin_schema,
        cv.Required(CONF_CC1101_CS_PIN): pins.gpio_output_pin_schema,
        cv.Required(CONF_CC1101_GDO0_PIN): pins.gpio_output_pin_schema,
        cv.Required(CONF_CC1101_GDO2_PIN): pins.gpio_output_pin_schema,
        cv.Required(CONF_RF_SWITCH_SW1_PIN): pins.gpio_output_pin_schema,
        cv.Required(CONF_RF_SWITCH_SW0_PIN): pins.gpio_output_pin_schema,
        cv.Optional(CONF_ENDPOINT_STATUS): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_ERROR): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_TX_RESULT): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_REQUEST_ID): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_TX_PATH): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_PAYLOAD_HEX): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_MARCSTATE_BEFORE_TX): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_LAST_MARCSTATE_AFTER_TX): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_CC1101_PARTNUM): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_CC1101_VERSION): text_sensor.text_sensor_schema(),
        cv.Optional(CONF_TX_SUCCESS_COUNT): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_TX_FAILURE_COUNT): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_LAST_PAYLOAD_LENGTH): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_LAST_REQUEST_REPEAT_COUNT): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_LAST_TX_ELAPSED_MS): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_TX_REPEAT_COUNT_SENSOR): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_FIRMWARE_PROTOCOL_VERSION): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_CONFIG_REVISION): sensor.sensor_schema(accuracy_decimals=0),
        cv.Optional(CONF_BATTERY_PERCENT): sensor.sensor_schema(
            unit_of_measurement="%",
            accuracy_decimals=0,
            device_class="battery",
            state_class="measurement",
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_DROPPED_PACKET_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_NO_RF_CAPTURE_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_INCOMPLETE_FIFO_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_DECODE_FAILED_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_PROFILE_MISMATCH_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_ACCEPTED_PACKET_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_TX_SUPPRESSED_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_TRANSPORT_UNAVAILABLE_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category="diagnostic",
        ),
        cv.Optional(CONF_RX_LAST_REJECTION_SNAPSHOT): text_sensor.text_sensor_schema(
            entity_category="diagnostic",
        ),
    }
).extend(cv.COMPONENT_SCHEMA).extend(i2c.i2c_device_schema(0x55))

FINAL_VALIDATE_SCHEMA = spi.final_validate_device_schema(
    "proflame2_tembed",
    require_miso=True,
    require_mosi=True,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    await spi.register_spi_device(
        var,
        {
            CONF_SPI_ID: config[CONF_SPI_ID],
            CONF_CS_PIN: config[CONF_CC1101_CS_PIN],
            CONF_DATA_RATE: 4_000_000,
        },
    )

    cg.add(var.set_tx_frequency_hz(config[CONF_TX_FREQUENCY_HZ]))
    cg.add(var.set_rx_frequency_hz(config[CONF_RX_FREQUENCY_HZ]))
    cg.add(var.set_data_rate_bps(config[CONF_DATA_RATE_BPS]))
    cg.add(var.set_tx_repeat_count(config[CONF_TX_REPEAT_COUNT]))
    cg.add(var.set_inter_frame_gap_us(config[CONF_INTER_FRAME_GAP_US]))
    cg.add(var.set_post_frame_idle_gap_us(config[CONF_POST_FRAME_IDLE_GAP_US]))
    cg.add(var.set_display_debug_mode(config[CONF_DISPLAY_DEBUG_MODE]))
    cg.add(var.set_display_dim_timeout_min(config[CONF_DISPLAY_DIM_TIMEOUT_MIN]))
    cg.add(var.set_display_wake_on_activity(config[CONF_DISPLAY_WAKE_ON_ACTIVITY]))
    cg.add(var.set_tx_mode_requested(config[CONF_TX_MODE]))
    cg.add(
        var.set_native_group_timing_profile_requested(
            config[CONF_NATIVE_GROUP_TIMING_PROFILE]
        )
    )
    cg.add(
        var.set_tx_mode(
            TXMode.CONTINUOUS_BURST
            if config[CONF_TX_MODE] == "continuous_burst"
            else TXMode.CLEAN_TIMING_TEST
            if config[CONF_TX_MODE] == "clean_timing_test"
            else TXMode.PROFLAME_PWM_SYMBOLS
            if config[CONF_TX_MODE] == "proflame_pwm_symbols"
            else TXMode.PROFLAME_NATIVE_GROUPS
            if config[CONF_TX_MODE] == "proflame_native_groups"
            else TXMode.REPEATED_STROBE
        )
    )
    cg.add(
        var.set_native_group_timing_profile(
            NativeGroupTimingProfile.NATIVE_REMOTE
            if config[CONF_NATIVE_GROUP_TIMING_PROFILE] == "native_remote"
            else NativeGroupTimingProfile.YARDSTICK_COMPAT
        )
    )
    cg.add(
        var.set_native_group_repeat_boundary_mode_requested(
            config[CONF_NATIVE_GROUP_REPEAT_BOUNDARY_MODE]
        )
    )
    cg.add(
        var.set_native_group_repeat_boundary_mode(
            NativeGroupRepeatBoundaryMode.REENTER_TX
            if config[CONF_NATIVE_GROUP_REPEAT_BOUNDARY_MODE] == "reenter_tx"
            else NativeGroupRepeatBoundaryMode.CONTINUOUS_TX
        )
    )
    cg.add(var.set_pre_burst_low_us(config[CONF_PRE_BURST_LOW_US]))
    cg.add(var.set_pre_frame_low_us(config[CONF_PRE_FRAME_LOW_US]))
    cg.add(
        var.set_diagnostic_repeat_count_override(
            config[CONF_DIAGNOSTIC_REPEAT_COUNT_OVERRIDE]
        )
    )
    cg.add(var.set_payload_bit_length_override(config[CONF_PAYLOAD_BIT_LENGTH_OVERRIDE]))
    cg.add(
        var.set_async_tx_data_pin(
            AsyncTxDataPin.GDO0
            if config[CONF_ASYNC_TX_DATA_PIN] == "gdo0"
            else AsyncTxDataPin.GDO2
        )
    )

    board_power_enable_pin = await cg.gpio_pin_expression(
        config[CONF_BOARD_POWER_ENABLE_PIN]
    )
    cc1101_gdo0_pin = await cg.gpio_pin_expression(config[CONF_CC1101_GDO0_PIN])
    cc1101_gdo2_pin = await cg.gpio_pin_expression(config[CONF_CC1101_GDO2_PIN])
    rf_switch_sw1_pin = await cg.gpio_pin_expression(config[CONF_RF_SWITCH_SW1_PIN])
    rf_switch_sw0_pin = await cg.gpio_pin_expression(config[CONF_RF_SWITCH_SW0_PIN])

    cg.add(var.set_board_power_enable_pin(board_power_enable_pin))
    cg.add(var.set_cc1101_gdo0_pin(cc1101_gdo0_pin))
    cg.add(var.set_cc1101_gdo2_pin(cc1101_gdo2_pin))
    cg.add(var.set_rf_switch_sw1_pin(rf_switch_sw1_pin))
    cg.add(var.set_rf_switch_sw0_pin(rf_switch_sw0_pin))

    text_sensor_setters = (
        (CONF_ENDPOINT_STATUS, "set_endpoint_status_sensor"),
        (CONF_LAST_ERROR, "set_last_error_sensor"),
        (CONF_LAST_TX_RESULT, "set_last_tx_result_sensor"),
        (CONF_LAST_REQUEST_ID, "set_last_request_id_sensor"),
        (CONF_LAST_TX_PATH, "set_last_tx_path_sensor"),
        (CONF_LAST_PAYLOAD_HEX, "set_last_payload_hex_sensor"),
        (CONF_LAST_MARCSTATE_BEFORE_TX, "set_last_marcstate_before_tx_sensor"),
        (CONF_LAST_MARCSTATE_AFTER_TX, "set_last_marcstate_after_tx_sensor"),
        (CONF_CC1101_PARTNUM, "set_cc1101_partnum_sensor"),
        (CONF_CC1101_VERSION, "set_cc1101_version_sensor"),
        (CONF_RX_LAST_REJECTION_SNAPSHOT, "set_rx_last_rejection_snapshot_sensor"),
    )
    for key, setter_name in text_sensor_setters:
        if key in config:
            sens = await text_sensor.new_text_sensor(config[key])
            cg.add(getattr(var, setter_name)(sens))

    sensor_setters = (
        (CONF_TX_SUCCESS_COUNT, "set_tx_success_count_sensor"),
        (CONF_TX_FAILURE_COUNT, "set_tx_failure_count_sensor"),
        (CONF_LAST_PAYLOAD_LENGTH, "set_last_payload_length_sensor"),
        (CONF_LAST_REQUEST_REPEAT_COUNT, "set_last_request_repeat_count_sensor"),
        (CONF_LAST_TX_ELAPSED_MS, "set_last_tx_elapsed_ms_sensor"),
        (CONF_TX_REPEAT_COUNT_SENSOR, "set_tx_repeat_count_sensor"),
        (CONF_FIRMWARE_PROTOCOL_VERSION, "set_firmware_protocol_version_sensor"),
        (CONF_CONFIG_REVISION, "set_config_revision_sensor"),
        (CONF_BATTERY_PERCENT, "set_battery_percent_sensor"),
        (CONF_RX_DROPPED_PACKET_COUNT, "set_rx_dropped_packet_count_sensor"),
        (CONF_RX_NO_RF_CAPTURE_COUNT, "set_rx_no_rf_capture_count_sensor"),
        (CONF_RX_INCOMPLETE_FIFO_COUNT, "set_rx_incomplete_fifo_count_sensor"),
        (CONF_RX_DECODE_FAILED_COUNT, "set_rx_decode_failed_count_sensor"),
        (CONF_RX_PROFILE_MISMATCH_COUNT, "set_rx_profile_mismatch_count_sensor"),
        (CONF_RX_ACCEPTED_PACKET_COUNT, "set_rx_accepted_packet_count_sensor"),
        (CONF_RX_TX_SUPPRESSED_COUNT, "set_rx_tx_suppressed_count_sensor"),
        (CONF_RX_TRANSPORT_UNAVAILABLE_COUNT, "set_rx_transport_unavailable_count_sensor"),
    )
    for key, setter_name in sensor_setters:
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(getattr(var, setter_name)(sens))


TX_ACTION_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_ID): cv.use_id(Proflame2TEmbedComponent),
        cv.Required(CONF_REQUEST_ID): cv.templatable(cv.string),
        cv.Required(CONF_AIR_PAYLOAD_HEX): cv.templatable(cv.string),
        cv.Required(CONF_PAYLOAD_BIT_LENGTH): cv.templatable(cv.positive_int),
        cv.Required(CONF_REPEAT_COUNT): cv.templatable(cv.int_range(min=1, max=20)),
        cv.Optional(CONF_STATUS_TEXT, default=""): cv.templatable(cv.string),
    }
)

TX_STATEFUL_ACTION_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_ID): cv.use_id(Proflame2TEmbedComponent),
        cv.Required(CONF_REQUEST_ID): cv.templatable(cv.string),
        cv.Required(CONF_AIR_PAYLOAD_HEX): cv.templatable(cv.string),
        cv.Required(CONF_PAYLOAD_BIT_LENGTH): cv.templatable(cv.positive_int),
        cv.Required(CONF_REPEAT_COUNT): cv.templatable(cv.int_range(min=1, max=20)),
        cv.Optional(CONF_STATUS_TEXT, default=""): cv.templatable(cv.string),
        cv.Optional(CONF_INTENDED_POWER, default=-1): cv.templatable(cv.int_range(min=-1, max=1)),
        cv.Optional(CONF_INTENDED_FLAME, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_FAN, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_LIGHT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_PILOT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_THERMOSTAT, default=-1): cv.templatable(cv.int_range(min=-1, max=1)),
        cv.Optional(CONF_INTENDED_FRONT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_AUX, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_ACTION_LABEL, default=""): cv.templatable(cv.string),
        cv.Optional(CONF_FIREPLACE_NAME, default=""): cv.templatable(cv.string),
    }
)


def _register_action(name, action_type, schema, **kwargs):
    """Register an action across ESPHome versions with/without synchronous=."""

    if "synchronous" not in inspect.signature(automation.register_action).parameters:
        kwargs.pop("synchronous", None)
    return automation.register_action(name, action_type, schema, **kwargs)


@_register_action(
    "proflame2_tembed.tx",
    Proflame2TEmbedTxAction,
    TX_ACTION_SCHEMA,
    synchronous=True,
)
async def tx_to_code(config, action_id, template_arg, args):
    parent = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, parent)

    request_id = await cg.templatable(config[CONF_REQUEST_ID], args, cg.std_string)
    air_payload_hex = await cg.templatable(
        config[CONF_AIR_PAYLOAD_HEX],
        args,
        cg.std_string,
    )
    payload_bit_length = await cg.templatable(
        config[CONF_PAYLOAD_BIT_LENGTH],
        args,
        cg.uint32,
    )
    repeat_count = await cg.templatable(config[CONF_REPEAT_COUNT], args, cg.uint8)
    status_text = await cg.templatable(config[CONF_STATUS_TEXT], args, cg.std_string)

    cg.add(var.set_request_id(request_id))
    cg.add(var.set_air_payload_hex(air_payload_hex))
    cg.add(var.set_payload_bit_length(payload_bit_length))
    cg.add(var.set_repeat_count(repeat_count))
    cg.add(var.set_status_text(status_text))
    return var



@_register_action(
    "proflame2_tembed.tx_stateful",
    Proflame2TEmbedTxStatefulAction,
    TX_STATEFUL_ACTION_SCHEMA,
    synchronous=True,
)
async def tx_stateful_to_code(config, action_id, template_arg, args):
    parent = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, parent)

    request_id = await cg.templatable(config[CONF_REQUEST_ID], args, cg.std_string)
    air_payload_hex = await cg.templatable(config[CONF_AIR_PAYLOAD_HEX], args, cg.std_string)
    payload_bit_length = await cg.templatable(config[CONF_PAYLOAD_BIT_LENGTH], args, cg.uint32)
    repeat_count = await cg.templatable(config[CONF_REPEAT_COUNT], args, cg.uint8)
    status_text = await cg.templatable(config[CONF_STATUS_TEXT], args, cg.std_string)
    intended_power = await cg.templatable(config[CONF_INTENDED_POWER], args, cg.int32)
    intended_flame = await cg.templatable(config[CONF_INTENDED_FLAME], args, cg.int32)
    intended_fan = await cg.templatable(config[CONF_INTENDED_FAN], args, cg.int32)
    intended_light = await cg.templatable(config[CONF_INTENDED_LIGHT], args, cg.int32)
    intended_pilot = await cg.templatable(config[CONF_INTENDED_PILOT], args, cg.int32)
    intended_thermostat = await cg.templatable(config[CONF_INTENDED_THERMOSTAT], args, cg.int32)
    intended_front = await cg.templatable(config[CONF_INTENDED_FRONT], args, cg.int32)
    intended_aux = await cg.templatable(config[CONF_INTENDED_AUX], args, cg.int32)
    intended_action_label = await cg.templatable(config[CONF_INTENDED_ACTION_LABEL], args, cg.std_string)
    fireplace_name = await cg.templatable(config[CONF_FIREPLACE_NAME], args, cg.std_string)

    cg.add(var.set_request_id(request_id))
    cg.add(var.set_air_payload_hex(air_payload_hex))
    cg.add(var.set_payload_bit_length(payload_bit_length))
    cg.add(var.set_repeat_count(repeat_count))
    cg.add(var.set_status_text(status_text))
    cg.add(var.set_intended_power(intended_power))
    cg.add(var.set_intended_flame(intended_flame))
    cg.add(var.set_intended_fan(intended_fan))
    cg.add(var.set_intended_light(intended_light))
    cg.add(var.set_intended_pilot(intended_pilot))
    cg.add(var.set_intended_thermostat(intended_thermostat))
    cg.add(var.set_intended_front(intended_front))
    cg.add(var.set_intended_aux(intended_aux))
    cg.add(var.set_intended_action_label(intended_action_label))
    cg.add(var.set_fireplace_name(fireplace_name))
    return var


DISPLAY_STATE_UPDATE_ACTION_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_ID): cv.use_id(Proflame2TEmbedComponent),
        cv.Optional(CONF_INTENDED_POWER, default=-1): cv.templatable(cv.int_range(min=-1, max=1)),
        cv.Optional(CONF_INTENDED_FLAME, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_FAN, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_LIGHT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_PILOT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_THERMOSTAT, default=-1): cv.templatable(cv.int_range(min=-1, max=1)),
        cv.Optional(CONF_INTENDED_FRONT, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_AUX, default=-1): cv.templatable(cv.int_),
        cv.Optional(CONF_INTENDED_ACTION_LABEL, default=""): cv.templatable(cv.string),
        cv.Optional(CONF_FIREPLACE_NAME, default=""): cv.templatable(cv.string),
    }
)


@_register_action(
    "proflame2_tembed.display_state_update",
    Proflame2TEmbedDisplayStateUpdateAction,
    DISPLAY_STATE_UPDATE_ACTION_SCHEMA,
    synchronous=True,
)
async def display_state_update_to_code(config, action_id, template_arg, args):
    parent = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, parent)

    intended_power = await cg.templatable(config[CONF_INTENDED_POWER], args, cg.int32)
    intended_flame = await cg.templatable(config[CONF_INTENDED_FLAME], args, cg.int32)
    intended_fan = await cg.templatable(config[CONF_INTENDED_FAN], args, cg.int32)
    intended_light = await cg.templatable(config[CONF_INTENDED_LIGHT], args, cg.int32)
    intended_pilot = await cg.templatable(config[CONF_INTENDED_PILOT], args, cg.int32)
    intended_thermostat = await cg.templatable(config[CONF_INTENDED_THERMOSTAT], args, cg.int32)
    intended_front = await cg.templatable(config[CONF_INTENDED_FRONT], args, cg.int32)
    intended_aux = await cg.templatable(config[CONF_INTENDED_AUX], args, cg.int32)
    intended_action_label = await cg.templatable(config[CONF_INTENDED_ACTION_LABEL], args, cg.std_string)
    fireplace_name = await cg.templatable(config[CONF_FIREPLACE_NAME], args, cg.std_string)

    cg.add(var.set_intended_power(intended_power))
    cg.add(var.set_intended_flame(intended_flame))
    cg.add(var.set_intended_fan(intended_fan))
    cg.add(var.set_intended_light(intended_light))
    cg.add(var.set_intended_pilot(intended_pilot))
    cg.add(var.set_intended_thermostat(intended_thermostat))
    cg.add(var.set_intended_front(intended_front))
    cg.add(var.set_intended_aux(intended_aux))
    cg.add(var.set_intended_action_label(intended_action_label))
    cg.add(var.set_fireplace_name(fireplace_name))
    return var


TEST_PATTERN_ACTION_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_ID): cv.use_id(Proflame2TEmbedComponent),
        cv.Required(CONF_REQUEST_ID): cv.templatable(cv.string),
        cv.Optional(CONF_MODE, default="alternating_ook"): cv.templatable(
            cv.one_of("alternating_ook", "carrier_on", "carrier_off", lower=True)
        ),
        cv.Optional(CONF_DURATION_MS, default=1500): cv.templatable(
            cv.int_range(min=100, max=5000)
        ),
        cv.Optional(CONF_PERIOD_US, default=833): cv.templatable(
            cv.int_range(min=100, max=1000000)
        ),
        cv.Optional(CONF_STATUS_TEXT, default=""): cv.templatable(cv.string),
    }
)


@_register_action(
    "proflame2_tembed.cc1101_test_pattern",
    Proflame2TEmbedTestPatternAction,
    TEST_PATTERN_ACTION_SCHEMA,
    synchronous=True,
)
async def cc1101_test_pattern_to_code(config, action_id, template_arg, args):
    parent = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, parent)

    request_id = await cg.templatable(config[CONF_REQUEST_ID], args, cg.std_string)
    mode_value = config[CONF_MODE]
    if isinstance(mode_value, str):
        mode_value = {
            "alternating_ook": TestPatternMode.ALTERNATING_OOK,
            "carrier_on": TestPatternMode.CARRIER_ON,
            "carrier_off": TestPatternMode.CARRIER_OFF,
        }[mode_value]
    mode = await cg.templatable(mode_value, args, TestPatternMode)
    duration_ms = await cg.templatable(config[CONF_DURATION_MS], args, cg.uint32)
    period_us = await cg.templatable(config[CONF_PERIOD_US], args, cg.uint32)
    status_text = await cg.templatable(config[CONF_STATUS_TEXT], args, cg.std_string)

    cg.add(var.set_request_id(request_id))
    cg.add(var.set_mode(mode))
    cg.add(var.set_duration_ms(duration_ms))
    cg.add(var.set_period_us(period_us))
    cg.add(var.set_status_text(status_text))
    return var
