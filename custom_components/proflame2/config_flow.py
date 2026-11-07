"""Config flow for the Proflame2 integration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    BACKEND_ESPHOME,
    BACKEND_YARDSTICK,
    CONF_ACTIVE_LISTENING,
    CONF_ALLOW_FLAME_OFF_WHEN_ON,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_DEBUG_LOGGING,
    CONF_ESPHOME_ENTRY_ID,
    CONF_FAN,
    CONF_FIREPLACE_SHORT_NAME,
    CONF_FLAME,
    CONF_FRONT,
    CONF_INITIAL_FRAME,
    CONF_INITIAL_PACKET_SOURCE,
    CONF_LEARNING_ID,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REFRESH_PERIOD_MINUTES,
    CONF_REMOTE_ID,
    CONF_RF_BAND,
    DATA_LEARNING_DEBUG_LOGGING,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_FEATURE_OPTIONS,
    DEFAULT_FIREPLACE_SHORT_NAME,
    DEFAULT_REFRESH_PERIOD_MINUTES,
    DOMAIN,
    available_backend_labels,
    available_backend_types,
    available_learning_backend_labels,
    available_learning_backend_types,
)
from .controller_assignment import controller_assignments
from .docs_urls import learning_ids_url, lilygo_controller_url
from .learning import (
    DEFAULT_LEARN_TIMEOUT_SECONDS,
    DEFAULT_RECEIVE_TIMEOUT_SECONDS,
    ERROR_BACKEND_UNAVAILABLE,
    ERROR_CONTRADICTORY_PROFILE,
    MANUAL_MODE_INSTRUCTION,
    LearningModeTransition,
    LearnResult,
    LearnSession,
    async_capture_next_learning_packet,
    async_close_learning_session,
    async_enable_extended_frame_diagnostics,
    async_start_learning_session,
    derive_learn_result_from_session,
)
from .packet_debug import get_packet_debug_logger
from .profile import (
    DuplicateProfileIdError,
    InvalidNibbleError,
    InvalidProfileNameError,
    InvalidRefreshPeriodError,
    InvalidRemoteIdError,
    InvalidSavedProfileError,
    ManualProfileInput,
    default_feature_options,
    fireplace_features_from_options,
    normalize_entry_options,
    normalize_feature_options,
    normalize_manual_profile_input,
    normalize_saved_profile_input,
    parse_remote_id,
    remote_id_as_hex,
    sanitize_fireplace_short_name,
)
from .protocol.packet import ProflamePacket
from .rf.band import DEFAULT_RF_BAND, YARDSTICK_BANDS, yardstick_band
from .rf.registry import get_backend_definition, normalize_controller_id
from .rf.yardstick import YardStickBackendUnavailableError
from .rtl433_learning import InvalidLearningIdError, normalize_learning_id_profile_input

LILYGO_ESPHOME_LINK_HELP = (
    "Select the ESPHome device that runs the LilyGO T-Embed CC1101 Proflame2 firmware.\n\n"
    "If the device is not listed, create and flash the LilyGO ESPHome device first, then add it through "
    "Home Assistant's ESPHome integration and return to this setup flow.\n\n"
    f"Setup guide: {lilygo_controller_url()}"
)


def _backend_selector() -> SelectSelector:
    """Return the backend selector for manual setup."""

    return SelectSelector(
        SelectSelectorConfig(
            options=[
                {"value": backend_type, "label": available_backend_labels()[backend_type]}
                for backend_type in available_backend_types()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _learning_backend_selector() -> SelectSelector:
    """Return the backend selector for guided learning."""

    return SelectSelector(
        SelectSelectorConfig(
            options=[
                {
                    "value": backend_type,
                    "label": available_learning_backend_labels()[backend_type],
                }
                for backend_type in available_learning_backend_types()
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _manual_profile_schema() -> vol.Schema:
    """Build the default Learning ID import form."""

    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FIREPLACE_SHORT_NAME, default=DEFAULT_FIREPLACE_SHORT_NAME): str,
            vol.Required(CONF_BACKEND_TYPE, default=BACKEND_YARDSTICK): _backend_selector(),
            vol.Required(CONF_LEARNING_ID): str,
        }
    )


def _manual_legacy_profile_schema() -> vol.Schema:
    """Build the retained legacy remote ID and C/D entry form."""

    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FIREPLACE_SHORT_NAME, default=DEFAULT_FIREPLACE_SHORT_NAME): str,
            vol.Required(CONF_BACKEND_TYPE, default=BACKEND_YARDSTICK): _backend_selector(),
            vol.Required(CONF_REMOTE_ID): str,
            vol.Required(CONF_C1): str,
            vol.Required(CONF_D1): str,
            vol.Required(CONF_C2): str,
            vol.Required(CONF_D2): str,
        }
    )


def _learn_setup_schema() -> vol.Schema:
    """Build the learn-entry schema for the active build."""

    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FIREPLACE_SHORT_NAME, default=DEFAULT_FIREPLACE_SHORT_NAME): str,
            vol.Required(CONF_BACKEND_TYPE, default=BACKEND_YARDSTICK): _learning_backend_selector(),
        }
    )


FEATURE_SELECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FAN, default=DEFAULT_FEATURE_OPTIONS[CONF_FAN]): bool,
        vol.Required(CONF_LIGHT, default=DEFAULT_FEATURE_OPTIONS[CONF_LIGHT]): bool,
        vol.Required(CONF_FRONT, default=DEFAULT_FEATURE_OPTIONS[CONF_FRONT]): bool,
        vol.Required(CONF_AUX, default=DEFAULT_FEATURE_OPTIONS[CONF_AUX]): bool,
        vol.Required(CONF_CPI, default=DEFAULT_FEATURE_OPTIONS[CONF_CPI]): bool,
    }
)


def _optional_settings_schema(
    *, edit_short_name: bool = False, defaults: dict[str, Any] | None = None, supports_active_listening: bool = False
) -> vol.Schema:
    """Keep behavior settings separate from installed fireplace features."""
    defaults = defaults or {}
    fields = {
        vol.Required(CONF_ALLOW_FLAME_OFF_WHEN_ON, default=defaults.get(CONF_ALLOW_FLAME_OFF_WHEN_ON, False)): bool,
    }
    if supports_active_listening:
        fields[vol.Required(CONF_ACTIVE_LISTENING, default=defaults.get(CONF_ACTIVE_LISTENING, False))] = bool
    fields[vol.Required(CONF_DEBUG_LOGGING, default=defaults.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING))] = bool
    if edit_short_name:
        fields[
            vol.Required(
                CONF_FIREPLACE_SHORT_NAME, default=defaults.get(CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME)
            )
        ] = str
    fields[
        vol.Required(
            CONF_REFRESH_PERIOD_MINUTES,
            default=defaults.get(CONF_REFRESH_PERIOD_MINUTES, DEFAULT_REFRESH_PERIOD_MINUTES),
        )
    ] = vol.All(int, vol.Range(min=0))
    return vol.Schema(fields)


LEARN_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "power_on",
        "Press the Power button once. The fireplace does not need to start in any specific state.",
    ),
    ("power_off", "Press the Power button again."),
    ("restore_power_on", "Press the Power button once more."),
    ("flame_down", "Press the Flame Down button once."),
)

EXTRA_LEARN_PROMPT = (
    "cmd2_change",
    "Press Flame Down or Flame Up once more so the integration can collect an additional Cmd2-changing packet if needed.",
)

EXTENDED_DIAGNOSTIC_PROMPTS: tuple[tuple[str, str], ...] = (
    ("diagnostic_flame_change", "Press Flame Down or Flame Up once."),
    ("diagnostic_light_change", "Press a Light control once. If unavailable, press Flame Down or Flame Up once."),
    ("diagnostic_aux_change", "Toggle AUX once. If unavailable, press a Flame control once."),
    (
        "diagnostic_mode_change",
        "Change thermostat, pilot, or fan mode once. If unavailable, press a Flame control once.",
    ),
)


class Proflame2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proflame2."""

    VERSION = 1

    _learn_input: dict[str, Any] | None = None
    _learn_result: LearnResult | None = None
    _learn_task: asyncio.Task[ProflamePacket | LearnResult | LearningModeTransition] | None = None
    _learn_session: LearnSession | None = None
    _learn_prompt_index: int = 0
    _learn_action_number: int = 0
    _extended_diagnostic_active: bool = False
    _extended_diagnostic_log_path: str | None = None
    _extended_diagnostic_task: asyncio.Task[str | None] | None = None
    _pending_entry_data: dict[str, Any] | None = None
    _pending_entry_options: dict[str, Any] | None = None
    _manual_learning_id_import: bool = False
    _yardstick_setup_mode: str = "manual"

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> Proflame2OptionsFlow:
        """Return the options flow handler."""

        return Proflame2OptionsFlow()

    @callback
    def async_remove(self) -> None:
        """Cancel any in-flight learn task if the flow is closed."""

        self.async_cancel_progress_task()
        if self.hass is not None and self._learn_session is not None:
            self.hass.async_create_task(async_close_learning_session(self._learn_session))
            self._learn_session = None
        super().async_remove()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the first user step.

        When invoked without input from the UI, we show a menu so the user can
        choose between manual entry and guided learning. We keep backward
        compatibility for direct test-driven/manual invocation by treating
        user_input here as manual profile submission.
        """

        if user_input is not None:
            return await self.async_step_manual(user_input)

        return self.async_show_menu(
            step_id="user",
            menu_options=["learn", "manual", "manual_legacy"],
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Import either protocol from a Learning ID; default manual setup path."""

        # Retain programmatic callers that submit the original manual schema.
        if user_input is not None and CONF_LEARNING_ID not in user_input and CONF_REMOTE_ID in user_input:
            return await self.async_step_manual_legacy(user_input)
        self._manual_learning_id_import = True
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = normalize_learning_id_profile_input(user_input)
            except InvalidLearningIdError:
                errors[CONF_LEARNING_ID] = "invalid_learning_id"
            except InvalidRefreshPeriodError:
                errors[CONF_REFRESH_PERIOD_MINUTES] = "invalid_refresh_period"
            else:
                return await self._async_finish_manual_profile(normalized, user_input)
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                _manual_profile_schema(), user_input or self._manual_suggested_values()
            ),
            errors=errors,
            description_placeholders={
                "learning_ids_url": learning_ids_url(),
            },
        )

    async def async_step_manual_legacy(self, user_input: dict[str, Any] | None = None):
        """Retain manual entry of remote ID and legacy C/D constants."""

        self._manual_learning_id_import = False
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                normalized = normalize_manual_profile_input(user_input)
            except InvalidRefreshPeriodError:
                errors[CONF_REFRESH_PERIOD_MINUTES] = "invalid_refresh_period"
            except InvalidRemoteIdError:
                errors[CONF_REMOTE_ID] = "invalid_remote_id"
            except InvalidNibbleError as exc:
                error_key = _extract_invalid_nibble_key(user_input, exc)
                errors[error_key] = "invalid_nibble"
            else:
                return await self._async_finish_manual_profile(normalized, user_input)

        suggested_values = self._manual_suggested_values()
        return self.async_show_form(
            step_id="manual_legacy",
            data_schema=self.add_suggested_values_to_schema(
                _manual_legacy_profile_schema(), user_input or suggested_values
            ),
            errors=errors,
        )

    async def _async_finish_manual_profile(self, normalized: ManualProfileInput, user_input: dict[str, Any]):
        """Share controller linking and duplicate protection between import paths."""

        if self._backend_requires_esphome_entry(normalized.data[CONF_BACKEND_TYPE]):
            linked_entry_id = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            if linked_entry_id:
                normalized.data[CONF_ESPHOME_ENTRY_ID] = linked_entry_id
            if self._validate_manual_esphome_link(normalized.data) is not None:
                self._pending_entry_data = normalized.data
                self._pending_entry_options = normalized.options
                return await self.async_step_manual_esphome()
        self._pending_entry_data = normalized.data
        self._pending_entry_options = normalized.options
        return await self._async_finish_manual_setup()

    async def _async_finish_manual_setup(self):
        """Imported remote data needs local feature selection before entry creation."""

        data, options = self._pending_entry_data, self._pending_entry_options
        assert data is not None and options is not None
        if not self._manual_learning_id_import and self._entry_exists_for_controller_and_remote(
            controller_id=data[CONF_BACKEND_TYPE],
            remote_id=data[CONF_REMOTE_ID],
            linked_esphome_entry_id=data.get(CONF_ESPHOME_ENTRY_ID),
        ):
            return self.async_abort(reason="already_configured")
        if controller_assignments(self.hass, data, options):
            return self.async_abort(reason="controller_in_use")
        if data[CONF_BACKEND_TYPE] == BACKEND_YARDSTICK and CONF_RF_BAND not in data:
            self._yardstick_setup_mode = "manual"
            return await self.async_step_yardstick_band()
        return await self.async_step_manual_features()

    async def async_step_yardstick_band(self, user_input: dict[str, Any] | None = None):
        """Collect controller band before opening a YardStick for learning or control."""
        data = self._learn_input if self._yardstick_setup_mode == "learn" else self._pending_entry_data
        if data is None:
            return await self.async_step_user()
        if controller_assignments(self.hass, data, {}):
            return self.async_abort(reason="controller_in_use")
        errors = {}
        if user_input is not None:
            try:
                band = user_input[CONF_RF_BAND]
                yardstick_band(band)
            except (KeyError, ValueError):
                errors[CONF_RF_BAND] = "invalid_rf_band"
            else:
                data[CONF_RF_BAND] = band
                if self._yardstick_setup_mode == "learn":
                    return await self._async_begin_learning_from_current_input()
                return await self._async_finish_manual_setup()
        return self.async_show_form(
            step_id="yardstick_band",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RF_BAND, default=DEFAULT_RF_BAND): SelectSelector(
                        SelectSelectorConfig(
                            options=[{"value": band, "label": f"{band} MHz"} for band in YARDSTICK_BANDS],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual_features(self, user_input: dict[str, Any] | None = None):
        """Choose fireplace features locally, independently of the Learning ID."""

        data, options = self._pending_entry_data, self._pending_entry_options
        if data is None or options is None:
            return await self.async_step_manual()
        if user_input is not None:
            self._pending_entry_options = {**options, **normalize_feature_options(user_input)}
            return await self.async_step_optional_settings()
        return self.async_show_form(
            step_id="manual_features",
            last_step=False,
            data_schema=self.add_suggested_values_to_schema(FEATURE_SELECTION_SCHEMA, options),
        )

    async def async_step_optional_settings(self, user_input: dict[str, Any] | None = None):
        """Commit setup only after the final behavior-settings page succeeds."""
        data, options = self._pending_entry_data, self._pending_entry_options
        if data is None or options is None:
            return await self.async_step_user()
        supports_listening = get_backend_definition(data[CONF_BACKEND_TYPE]).supports_active_listening
        errors = {}
        if user_input is not None:
            self._pending_entry_options = {**options, **user_input}
            try:
                updated = normalize_entry_options(self._pending_entry_options)
                if not supports_listening:
                    updated[CONF_ACTIVE_LISTENING] = False
            except InvalidRefreshPeriodError:
                errors[CONF_REFRESH_PERIOD_MINUTES] = "invalid_refresh_period"
            else:
                return await self._async_create_profile_entry(title=data[CONF_NAME], data=data, options=updated)
        return self.async_show_form(
            step_id="optional_settings",
            last_step=True,
            data_schema=self.add_suggested_values_to_schema(
                _optional_settings_schema(
                    defaults=self._pending_entry_options, supports_active_listening=supports_listening
                ),
                self._pending_entry_options,
            ),
            errors=errors,
        )

    async def async_step_manual_esphome(self, user_input: dict[str, Any] | None = None):
        """Collect the ESPHome device link only for manual LilyGO setup."""

        if self._pending_entry_data is None or self._pending_entry_options is None:
            return await self.async_step_manual()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_entry_data[CONF_ESPHOME_ENTRY_ID] = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            esphome_entry_error = self._validate_manual_esphome_link(self._pending_entry_data)
            if esphome_entry_error is None:
                return await self._async_finish_manual_setup()
            errors[CONF_ESPHOME_ENTRY_ID] = esphome_entry_error

        return self.async_show_form(
            step_id="manual_esphome",
            data_schema=self._esphome_link_schema(),
            errors=errors,
            description_placeholders={"setup_text": LILYGO_ESPHOME_LINK_HELP},
        )

    async def async_step_learn(self, user_input: dict[str, Any] | None = None):
        """Start guided remote learning."""

        if user_input is None:
            suggested_values = self._learn_input or {
                CONF_NAME: "",
                CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
                CONF_FIREPLACE_SHORT_NAME: DEFAULT_FIREPLACE_SHORT_NAME,
            }
            return self.async_show_form(
                step_id="learn",
                data_schema=self.add_suggested_values_to_schema(_learn_setup_schema(), suggested_values),
            )

        self._learn_input = {
            CONF_NAME: str(user_input[CONF_NAME]).strip(),
            CONF_BACKEND_TYPE: normalize_controller_id(user_input[CONF_BACKEND_TYPE]),
            CONF_FIREPLACE_SHORT_NAME: sanitize_fireplace_short_name(
                user_input.get(CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME)
            ),
        }
        if self._backend_requires_esphome_entry(self._learn_input[CONF_BACKEND_TYPE]):
            self._learn_input[CONF_ESPHOME_ENTRY_ID] = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            esphome_entry_error = self._validate_manual_esphome_link(self._learn_input)
            if esphome_entry_error is not None:
                return await self.async_step_learn_esphome()
        return await self._async_begin_learning_from_current_input()

    async def async_step_learn_esphome(self, user_input: dict[str, Any] | None = None):
        """Collect the ESPHome device link only for LilyGO guided learning."""

        if self._learn_input is None:
            return await self.async_step_learn()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._learn_input[CONF_ESPHOME_ENTRY_ID] = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            esphome_entry_error = self._validate_manual_esphome_link(self._learn_input)
            if esphome_entry_error is None:
                return await self._async_begin_learning_from_current_input()
            errors[CONF_ESPHOME_ENTRY_ID] = esphome_entry_error

        return self.async_show_form(
            step_id="learn_esphome",
            data_schema=self._esphome_link_schema(),
            errors=errors,
            description_placeholders={"setup_text": LILYGO_ESPHOME_LINK_HELP},
        )

    async def _async_begin_learning_from_current_input(self):
        """Start guided learning after backend-specific setup has completed."""

        if self._learn_input is None:
            return await self.async_step_learn()
        if controller_assignments(self.hass, self._learn_input, {}):
            return self.async_abort(reason="controller_in_use")
        if self._learn_input[CONF_BACKEND_TYPE] == BACKEND_YARDSTICK and CONF_RF_BAND not in self._learn_input:
            self._yardstick_setup_mode = "learn"
            return await self.async_step_yardstick_band()
        self._learn_result = None
        await self._async_dispose_learning_session()
        learn_session = await self._async_start_learning_session_or_fail()
        if learn_session is None:
            return await self.async_step_learn_failed()
        self._learn_session = learn_session
        self._learn_prompt_index = 0
        self._learn_action_number = 0
        self._extended_diagnostic_active = False
        self._extended_diagnostic_log_path = None
        self._extended_diagnostic_task = None
        return await self.async_step_learn_prompt()

    async def async_step_learn_prompt(self, user_input: dict[str, Any] | None = None):
        """Begin listening immediately for the next guided prompt."""

        if self._learn_input is None or self._learn_session is None:
            return await self.async_step_learn()

        if self._learn_task is not None:
            if self._learn_task.done():
                return self.async_show_progress_done(next_step_id="learn_progress")
            return self.async_show_progress(
                step_id="learn_progress",
                progress_action="learn_remote",
                description_placeholders=self._learn_progress_placeholders(),
                progress_task=self._learn_task,
            )

        if self._extended_diagnostic_task is not None:
            self._extended_diagnostic_log_path = await self._extended_diagnostic_task
            self._extended_diagnostic_task = None

        if not self._extended_diagnostic_active:
            self._learn_action_number += 1
        self._learn_session.prompt_index = (
            self._learn_prompt_index if self._extended_diagnostic_active else self._learn_action_number - 1
        )
        self._learn_session.prompt_label = self._current_learn_prompt_label()
        self._learn_session.prompt_instruction = self._current_learn_instruction()
        self._learn_session.diagnostic_capture = self._extended_diagnostic_active
        if self._learn_session.packet_debug_logging_enabled:
            get_packet_debug_logger().info(
                "config_flow: prompt_index=%s instruction=%s",
                self._learn_prompt_index,
                self._current_learn_instruction(),
            )
        self._learn_task = self.hass.async_create_task(async_capture_next_learning_packet(self._learn_session))
        return self.async_show_progress(
            step_id="learn_progress",
            progress_action="learn_remote",
            description_placeholders=self._learn_progress_placeholders(),
            progress_task=self._learn_task,
        )

    async def async_step_learn_progress(self, user_input: dict[str, Any] | None = None):
        """Show guided learn progress while waiting for the next prompted packet."""

        if self._learn_task is None or self._learn_input is None:
            return await self.async_step_learn()

        if not self._learn_task.done():
            return self.async_show_progress(
                step_id="learn_progress",
                progress_action="learn_remote",
                description_placeholders=self._learn_progress_placeholders(),
                progress_task=self._learn_task,
            )

        capture_result = await self._learn_task
        self._learn_task = None

        if isinstance(capture_result, LearningModeTransition):
            return self.async_show_progress_done(next_step_id="learn_prompt")

        if isinstance(capture_result, LearnResult):
            self._learn_result = capture_result
            await self._async_dispose_learning_session()
            return self.async_show_progress_done(
                next_step_id="learn_features" if capture_result.success else "learn_failed"
            )

        corrective_action = (
            not self._extended_diagnostic_active
            and self._learn_prompt_index >= 3
            and (self._learn_session.prompt_label == "ensure_power_on" or not capture_result.state.power)
        )
        # Corrective packets remain derivation evidence, but don't complete a flame step.
        if not corrective_action:
            self._learn_prompt_index += 1
        if self._extended_diagnostic_active:
            if self._learn_prompt_index < len(EXTENDED_DIAGNOSTIC_PROMPTS):
                return self.async_show_progress_done(next_step_id="learn_prompt")
            self._learn_result = LearnResult(
                success=False,
                error=(
                    "This remote uses an extended frame whose integrity format is not yet supported. "
                    f"Diagnostic captures were saved to {self._extended_diagnostic_log_path}. Attach that file to a bug report."
                ),
            )
            await self._async_dispose_learning_session()
            return self.async_show_progress_done(next_step_id="learn_failed")
        maybe_result = derive_learn_result_from_session(self._learn_session)
        if maybe_result is not None:
            if (
                not maybe_result.success
                and maybe_result.error_code == ERROR_CONTRADICTORY_PROFILE
                and self._learn_session.extended_frame_records
            ):
                self._extended_diagnostic_active = True
                self._learn_prompt_index = 0
                self._extended_diagnostic_task = self.hass.async_create_task(
                    async_enable_extended_frame_diagnostics(self._learn_session)
                )
                return self.async_show_progress_done(next_step_id="learn_prompt")
            self._learn_result = maybe_result
            await self._async_dispose_learning_session()
            return self.async_show_progress_done(
                next_step_id="learn_features" if self._learn_result.success else "learn_failed"
            )

        return self.async_show_progress_done(next_step_id="learn_prompt")

    async def async_step_learn_features(self, user_input: dict[str, Any] | None = None):
        """Collect feature support after a successful learn pass."""

        if self._learn_result is None or not self._learn_result.success or self._learn_input is None:
            return await self.async_step_learn_failed()

        if user_input is not None:
            data = {
                CONF_NAME: self._learn_input[CONF_NAME],
                CONF_BACKEND_TYPE: self._learn_input[CONF_BACKEND_TYPE],
                **self._learn_result.data,
            }
            if self._learn_input[CONF_BACKEND_TYPE] == BACKEND_YARDSTICK:
                data[CONF_RF_BAND] = self._learn_input.get(CONF_RF_BAND, DEFAULT_RF_BAND)
            if self._backend_requires_esphome_entry(self._learn_input[CONF_BACKEND_TYPE]):
                data[CONF_ESPHOME_ENTRY_ID] = self._learn_input[CONF_ESPHOME_ENTRY_ID]
            if self._learn_result.final_packet is not None:
                data[CONF_INITIAL_FRAME] = asdict(self._learn_result.final_packet.frame)
                data[CONF_INITIAL_PACKET_SOURCE] = self._learn_result.final_packet.source or "observed_packet"
            self._pending_entry_data = data
            self._pending_entry_options = {
                **normalize_feature_options(user_input),
                CONF_FIREPLACE_SHORT_NAME: self._learn_input.get(
                    CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME
                ),
                CONF_DEBUG_LOGGING: self._learn_input.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING),
                CONF_ACTIVE_LISTENING: self._learn_input[CONF_BACKEND_TYPE] == BACKEND_ESPHOME,
            }
            return await self.async_step_optional_settings()

        suggested_values = default_feature_options()
        return self.async_show_form(
            step_id="learn_features",
            last_step=False,
            data_schema=self.add_suggested_values_to_schema(
                FEATURE_SELECTION_SCHEMA,
                user_input or suggested_values,
            ),
        )

    async def async_step_learn_failed(self, user_input: dict[str, Any] | None = None):
        """Show clear learn failure details with retry/manual fallback."""

        description_placeholders = {
            "error": (
                self._learn_result.error
                if self._learn_result and self._learn_result.error
                else "Unknown learning error."
            )
        }
        return self.async_show_menu(
            step_id="learn_failed",
            menu_options=["retry_learn", "manual", "manual_legacy"],
            description_placeholders=description_placeholders,
        )

    async def async_step_retry_learn(self, user_input: dict[str, Any] | None = None):
        """Retry learn mode with the same backend and fireplace name."""

        if self._learn_input is None:
            return await self.async_step_learn()
        if controller_assignments(self.hass, self._learn_input, {}):
            return self.async_abort(reason="controller_in_use")

        self._learn_result = None
        await self._async_dispose_learning_session()
        learn_session = await self._async_start_learning_session_or_fail()
        if learn_session is None:
            return await self.async_step_learn_failed()
        self._learn_session = learn_session
        self._learn_prompt_index = 0
        self._extended_diagnostic_active = False
        self._extended_diagnostic_log_path = None
        self._extended_diagnostic_task = None
        return await self.async_step_learn_prompt()

    async def _async_create_profile_entry(
        self,
        *,
        title: str,
        data: dict[str, Any],
        options: dict[str, Any],
    ):
        """Create one permanent fireplace profile entry."""

        if self._entry_exists_for_controller_and_remote(
            controller_id=data[CONF_BACKEND_TYPE],
            remote_id=data[CONF_REMOTE_ID],
            linked_esphome_entry_id=data.get(CONF_ESPHOME_ENTRY_ID),
        ):
            return self.async_abort(reason="already_configured")
        if controller_assignments(self.hass, data, options):
            return self.async_abort(reason="controller_in_use")
        return self.async_create_entry(
            title=title,
            data=data,
            options=options,
        )

    def _manual_suggested_values(self) -> dict[str, Any]:
        """Build suggested values for manual entry, including learn fallback."""

        suggested = {
            CONF_NAME: "",
            CONF_FIREPLACE_SHORT_NAME: DEFAULT_FIREPLACE_SHORT_NAME,
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: "",
            CONF_C1: "",
            CONF_D1: "",
            CONF_C2: "",
            CONF_D2: "",
            **default_feature_options(),
        }
        if self._learn_input is not None:
            suggested[CONF_NAME] = self._learn_input[CONF_NAME]
            suggested[CONF_BACKEND_TYPE] = self._learn_input[CONF_BACKEND_TYPE]
            suggested[CONF_FIREPLACE_SHORT_NAME] = self._learn_input.get(
                CONF_FIREPLACE_SHORT_NAME,
                DEFAULT_FIREPLACE_SHORT_NAME,
            )

        if self._learn_result is not None and self._learn_result.remote_id is not None:
            suggested[CONF_REMOTE_ID] = remote_id_as_hex(self._learn_result.remote_id)

        return suggested

    def _esphome_link_schema(self) -> vol.Schema:
        """Build an explicit ESPHome entry dropdown for LilyGO setup."""

        return vol.Schema(
            {
                vol.Required(CONF_ESPHOME_ENTRY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": entry.entry_id, "label": entry.title}
                            for entry in self.hass.config_entries.async_entries("esphome")
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _validate_manual_esphome_link(self, data: dict[str, Any]) -> str | None:
        """Validate ESPHome link requirements for manual setup."""

        if not self._backend_requires_esphome_entry(data[CONF_BACKEND_TYPE]):
            data.pop(CONF_ESPHOME_ENTRY_ID, None)
            return None

        linked_entry_id = data.get(CONF_ESPHOME_ENTRY_ID)
        if not isinstance(linked_entry_id, str) or not linked_entry_id:
            return "required"

        linked_entry = self.hass.config_entries.async_get_entry(linked_entry_id)
        if linked_entry is None or linked_entry.domain != "esphome":
            return "invalid_esphome_entry"

        return None

    def _entry_exists_for_controller_and_remote(
        self,
        *,
        controller_id: str,
        remote_id: int,
        linked_esphome_entry_id: str | None = None,
    ) -> bool:
        """Return whether this controller/remote pair already exists."""

        normalized_controller_id = normalize_controller_id(controller_id)
        normalized_remote_id = remote_id_as_hex(remote_id)

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            existing_controller_id = entry.data.get(CONF_BACKEND_TYPE)
            existing_remote_id = entry.data.get(CONF_REMOTE_ID)
            if existing_controller_id is None or existing_remote_id is None:
                continue
            try:
                normalized_existing_controller_id = normalize_controller_id(existing_controller_id)
            except ValueError:
                continue
            if normalized_existing_controller_id != normalized_controller_id:
                continue
            if self._backend_requires_esphome_entry(normalized_existing_controller_id):
                existing_linked_entry_id = entry.options.get(CONF_ESPHOME_ENTRY_ID) or entry.data.get(
                    CONF_ESPHOME_ENTRY_ID
                )
                if existing_linked_entry_id != linked_esphome_entry_id:
                    continue
            if remote_id_as_hex(parse_remote_id(existing_remote_id)) == normalized_remote_id:
                return True
        return False

    @staticmethod
    def _backend_requires_esphome_entry(controller_id: str) -> bool:
        """Return whether this controller must be linked to an ESPHome config entry."""

        return get_backend_definition(controller_id).requires_esphome_entry

    def _current_learn_instruction(self) -> str:
        """Return the current guided-learning prompt."""

        if self._learn_session is not None and self._learn_session.waiting_for_manual:
            instruction = MANUAL_MODE_INSTRUCTION
        elif self._extended_diagnostic_active:
            instruction = EXTENDED_DIAGNOSTIC_PROMPTS[self._learn_prompt_index][1]
        elif self._flame_learning_needs_power_on():
            instruction = "The fireplace is OFF. Press the Power button to turn it ON before adjusting the flame."
        elif self._learn_prompt_index >= 3 and self._learn_session is not None:
            packet = self._learn_session.last_observed_packet
            if packet is not None:
                direction = "Up" if packet.state.flame <= 1 else "Down"
                instruction = f"Press the Flame {direction} button once."
            else:
                instruction = LEARN_PROMPTS[3][1] if self._learn_prompt_index == 3 else EXTRA_LEARN_PROMPT[1]
        elif self._learn_prompt_index < len(LEARN_PROMPTS):
            instruction = LEARN_PROMPTS[self._learn_prompt_index][1]
        else:
            instruction = EXTRA_LEARN_PROMPT[1]
        return instruction

    def _flame_learning_needs_power_on(self) -> bool:
        """Require a received ON state before asking for a flame adjustment."""

        return (
            not self._extended_diagnostic_active
            and self._learn_prompt_index >= 3
            and self._learn_session is not None
            and self._learn_session.last_observed_packet is not None
            and not self._learn_session.last_observed_packet.state.power
        )

    def _learn_progress_placeholders(self) -> dict[str, str]:
        """Separate HA's progress header from the action without changing capture state."""

        instruction = self._current_learn_instruction()
        if self._learn_session is not None and self._learn_session.waiting_for_manual:
            header, _, instruction = instruction.partition("\n")
            header = header.strip()
        elif self._extended_diagnostic_active:
            header = f"Diagnostic step {self._learn_prompt_index + 1} of {len(EXTENDED_DIAGNOSTIC_PROMPTS)}"
        elif self._learn_action_number <= len(LEARN_PROMPTS):
            header = f"Step {max(1, self._learn_action_number)} of {len(LEARN_PROMPTS)}"
        else:
            header = f"Additional step {self._learn_action_number}"
        return {
            "backend_name": available_backend_labels()[self._learn_input[CONF_BACKEND_TYPE]],
            "header": header,
            "instruction": instruction,
        }

    def _current_learn_prompt_label(self) -> str:
        """Return the short label for the current guided-learning prompt."""

        if self._extended_diagnostic_active:
            return EXTENDED_DIAGNOSTIC_PROMPTS[self._learn_prompt_index][0]
        if self._flame_learning_needs_power_on():
            return "ensure_power_on"
        if self._learn_prompt_index < len(LEARN_PROMPTS):
            return LEARN_PROMPTS[self._learn_prompt_index][0]
        return EXTRA_LEARN_PROMPT[0]

    async def _async_dispose_learning_session(self) -> None:
        """Close the current guided-learning backend session, if any."""

        if self._extended_diagnostic_task is not None:
            self._extended_diagnostic_log_path = await self._extended_diagnostic_task
            self._extended_diagnostic_task = None
        if self._learn_session is not None:
            await async_close_learning_session(self._learn_session)
            self._learn_session = None

    async def _async_start_learning_session_or_fail(self) -> LearnSession | None:
        """Start guided learning or convert backend errors into flow-safe failures."""

        assert self._learn_input is not None
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        debug_logging = bool(self._learn_input.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)) or bool(
            domain_data.get(DATA_LEARNING_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
        )
        try:
            return await async_start_learning_session(
                self.hass,
                self._learn_input[CONF_BACKEND_TYPE],
                debug_logging=debug_logging,
                timeout=float(domain_data.get(DATA_LEARNING_TIMEOUT, DEFAULT_LEARN_TIMEOUT_SECONDS)),
                receive_timeout=float(
                    domain_data.get(
                        DATA_LEARNING_RECEIVE_TIMEOUT,
                        DEFAULT_RECEIVE_TIMEOUT_SECONDS,
                    )
                ),
                esphome_entry_id=self._learn_input.get(CONF_ESPHOME_ENTRY_ID),
                **(
                    {"rf_band": self._learn_input.get(CONF_RF_BAND, DEFAULT_RF_BAND)}
                    if self._learn_input[CONF_BACKEND_TYPE] == BACKEND_YARDSTICK
                    else {}
                ),
            )
        except (YardStickBackendUnavailableError, ValueError) as exc:
            self._learn_result = LearnResult(
                success=False,
                warnings=[],
                error_code=ERROR_BACKEND_UNAVAILABLE,
                error=str(exc),
            )
            return None


class Proflame2OptionsFlow(OptionsFlowWithReload):
    """Handle Proflame2 feature flags and saved profile options."""

    _selected_profile_id: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Choose whether to edit features or saved profiles."""

        return self.async_show_menu(
            step_id="init",
            menu_options=["features", "profiles"],
        )

    async def async_step_features(self, user_input: dict[str, Any] | None = None):
        """Manage editable feature flag options."""

        if user_input is not None:
            self._pending_options = {**self.config_entry.options, **normalize_feature_options(user_input)}
            return await self.async_step_optional_settings()
        return self.async_show_form(
            step_id="features",
            last_step=False,
            data_schema=self.add_suggested_values_to_schema(FEATURE_SELECTION_SCHEMA, self.config_entry.options),
        )

    async def async_step_optional_settings(self, user_input: dict[str, Any] | None = None):
        """Validate pending settings and save atomically, without changing state."""
        supports_listening = get_backend_definition(self.config_entry.data[CONF_BACKEND_TYPE]).supports_active_listening
        pending = getattr(self, "_pending_options", dict(self.config_entry.options))
        errors = {}
        if user_input is not None:
            pending = {**pending, **user_input}
            self._pending_options = pending
            if (
                self.config_entry.options.get(CONF_ALLOW_FLAME_OFF_WHEN_ON, False)
                and not pending.get(CONF_ALLOW_FLAME_OFF_WHEN_ON, False)
                and self._has_flame_zero_conflict(pending)
            ):
                errors[CONF_ALLOW_FLAME_OFF_WHEN_ON] = "flame_zero_conflict"
            else:
                try:
                    updated = {**pending, **normalize_entry_options(pending)}
                    if not supports_listening:
                        updated[CONF_ACTIVE_LISTENING] = False
                except InvalidRefreshPeriodError:
                    errors[CONF_REFRESH_PERIOD_MINUTES] = "invalid_refresh_period"
                else:
                    return self.async_create_entry(data=updated)
        suggested = {**normalize_entry_options(self.config_entry.options), **pending}
        return self.async_show_form(
            step_id="optional_settings",
            last_step=True,
            data_schema=self.add_suggested_values_to_schema(
                _optional_settings_schema(
                    edit_short_name=True, defaults=suggested, supports_active_listening=supports_listening
                ),
                suggested,
            ),
            errors=errors,
        )

    def _has_flame_zero_conflict(self, options: dict[str, Any]) -> bool:
        from .runtime import async_get_runtime_entries, runtime_current_state

        runtime = async_get_runtime_entries(self.hass).get(self.config_entry.entry_id)
        states = []
        if runtime is not None:
            states = [runtime_current_state(runtime), runtime.desired_state]
            states += [
                intent.state
                for intent in (runtime.control_intents.pending, runtime.control_intents.in_flight)
                if intent is not None
            ]
        if any(state is not None and state.power and state.flame == 0 for state in states):
            return True
        return any(
            profile.get(CONF_POWER) and profile.get(CONF_FLAME) == 0
            for profile in options.get(CONF_PROFILES, {}).values()
        )

    async def async_step_profiles(self, user_input: dict[str, Any] | None = None):
        """Manage saved profiles for this fireplace."""

        menu_options = ["add_profile"]
        if self._profiles:
            menu_options.append("select_profile")
        return self.async_show_menu(
            step_id="profiles",
            menu_options=menu_options,
        )

    async def async_step_select_profile(self, user_input: dict[str, Any] | None = None):
        """Choose an existing profile to edit or delete."""

        if not self._profiles:
            return await self.async_step_profiles()

        profile_choices = {profile_id: profile[CONF_NAME] for profile_id, profile in self._profiles.items()}
        action_choices = {
            "edit_profile": "Edit profile",
            "delete_profile": "Delete profile",
        }

        if user_input is not None:
            self._selected_profile_id = str(user_input[CONF_PROFILE_ID])
            action = str(user_input["action"])
            if self._selected_profile_id not in self._profiles:
                return self.async_show_form(
                    step_id="select_profile",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_PROFILE_ID): vol.In(profile_choices),
                            vol.Required("action", default="edit_profile"): vol.In(action_choices),
                        }
                    ),
                    errors={CONF_PROFILE_ID: "unknown_profile"},
                )
            return await getattr(self, f"async_step_{action}")()

        return self.async_show_form(
            step_id="select_profile",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE_ID): vol.In(profile_choices),
                    vol.Required("action", default="edit_profile"): vol.In(action_choices),
                }
            ),
        )

    async def async_step_add_profile(self, user_input: dict[str, Any] | None = None):
        """Create one saved fireplace profile."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                profile = normalize_saved_profile_input(
                    user_input,
                    features=self._features,
                    allow_flame_off_when_on=bool(self.config_entry.options.get(CONF_ALLOW_FLAME_OFF_WHEN_ON, False)),
                )
                if profile[CONF_PROFILE_ID] in self._profiles:
                    raise DuplicateProfileIdError(profile[CONF_PROFILE_ID])
            except InvalidProfileNameError:
                errors[CONF_NAME] = "invalid_profile_name"
            except InvalidSavedProfileError as exc:
                errors[_extract_invalid_profile_field(user_input, str(exc))] = "invalid_profile"
            except DuplicateProfileIdError:
                errors[CONF_NAME] = "duplicate_profile_id"
            else:
                updated = {**self.config_entry.options, **normalize_entry_options(self.config_entry.options)}
                updated[CONF_PROFILES][profile[CONF_PROFILE_ID]] = profile
                return self.async_create_entry(data=updated)

        return self.async_show_form(
            step_id="add_profile",
            data_schema=self.add_suggested_values_to_schema(
                _profile_schema(self._features),
                user_input or _default_profile_values(self._features),
            ),
            errors=errors,
        )

    async def async_step_edit_profile(self, user_input: dict[str, Any] | None = None):
        """Edit one existing saved fireplace profile."""

        profile_id = self._selected_profile_id
        if profile_id is None or profile_id not in self._profiles:
            return await self.async_step_select_profile()

        existing = self._profiles[profile_id]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                profile = normalize_saved_profile_input(
                    user_input,
                    features=self._features,
                    allow_flame_off_when_on=bool(self.config_entry.options.get(CONF_ALLOW_FLAME_OFF_WHEN_ON, False)),
                    profile_id=profile_id,
                )
            except InvalidProfileNameError:
                errors[CONF_NAME] = "invalid_profile_name"
            except InvalidSavedProfileError as exc:
                errors[_extract_invalid_profile_field(user_input, str(exc))] = "invalid_profile"
            else:
                updated = {**self.config_entry.options, **normalize_entry_options(self.config_entry.options)}
                updated[CONF_PROFILES][profile_id] = profile
                return self.async_create_entry(data=updated)

        suggested = dict(existing)
        return self.async_show_form(
            step_id="edit_profile",
            data_schema=self.add_suggested_values_to_schema(
                _profile_schema(self._features),
                user_input or suggested,
            ),
            errors=errors,
            description_placeholders={"profile_name": existing[CONF_NAME]},
        )

    async def async_step_delete_profile(self, user_input: dict[str, Any] | None = None):
        """Delete one existing saved fireplace profile."""

        profile_id = self._selected_profile_id
        if profile_id is None or profile_id not in self._profiles:
            return await self.async_step_select_profile()

        existing = self._profiles[profile_id]
        if user_input is not None and user_input.get("confirm"):
            updated = {**self.config_entry.options, **normalize_entry_options(self.config_entry.options)}
            updated[CONF_PROFILES].pop(profile_id, None)
            return self.async_create_entry(data=updated)

        return self.async_show_form(
            step_id="delete_profile",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders={"profile_name": existing[CONF_NAME]},
        )

    @property
    def _features(self):
        """Return the current fireplace feature flags for dynamic profile forms."""

        return fireplace_features_from_options(self.config_entry.options)

    @property
    def _profiles(self) -> dict[str, dict[str, Any]]:
        """Return normalized saved profiles for this config entry."""

        return normalize_entry_options(
            self.config_entry.options,
            features=self._features,
        )[CONF_PROFILES]


def _profile_schema(features) -> vol.Schema:
    """Build the add/edit profile schema for one fireplace."""

    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_POWER, default=True): bool,
        vol.Required(CONF_FLAME, default=1): vol.Any(int, float),
    }
    if features.fan:
        schema[vol.Optional(CONF_FAN, default=0)] = vol.Coerce(int)
    if features.light:
        schema[vol.Optional(CONF_LIGHT, default=0)] = vol.Coerce(int)
    if features.front:
        schema[vol.Optional(CONF_FRONT, default=False)] = bool
    if features.aux:
        schema[vol.Optional(CONF_AUX, default=False)] = bool
    if features.cpi:
        schema[vol.Optional(CONF_CPI, default=False)] = bool
    return vol.Schema(schema)


def _default_profile_values(features) -> dict[str, Any]:
    """Return default suggested values for a new saved profile."""

    suggested = {
        CONF_NAME: "",
        CONF_POWER: True,
        CONF_FLAME: 1,
    }
    if features.fan:
        suggested[CONF_FAN] = 0
    if features.light:
        suggested[CONF_LIGHT] = 0
    if features.front:
        suggested[CONF_FRONT] = False
    if features.aux:
        suggested[CONF_AUX] = False
    if features.cpi:
        suggested[CONF_CPI] = False
    return suggested


def _extract_invalid_nibble_key(user_input: dict[str, Any], exc: InvalidNibbleError) -> str:
    """Map an invalid nibble value back to the offending field."""

    for key in (CONF_C1, CONF_D1, CONF_C2, CONF_D2):
        try:
            if key in user_input:
                from .profile import parse_nibble

                parse_nibble(user_input[key])
        except InvalidNibbleError:
            return key
    return CONF_C1


def _extract_invalid_profile_field(user_input: dict[str, Any], message: str) -> str:
    """Map a saved-profile validation error back to the most relevant field."""

    lowered = message.lower()
    if "flame" in lowered:
        return CONF_FLAME
    if "fan" in lowered:
        return CONF_FAN
    if "light" in lowered:
        return CONF_LIGHT
    if "power" in lowered:
        return CONF_POWER
    return CONF_NAME
