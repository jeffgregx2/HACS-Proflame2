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
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    BACKEND_ESPHOME,
    BACKEND_YARDSTICK,
    CONF_ACTIVE_LISTENING,
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
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    CONF_RTL433_SAMPLES,
    DATA_LEARNING_DEBUG_LOGGING,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_FEATURE_OPTIONS,
    DEFAULT_FIREPLACE_SHORT_NAME,
    DOMAIN,
    available_backend_labels,
    available_backend_types,
    available_learning_backend_labels,
    available_learning_backend_types,
)
from .docs_urls import lilygo_controller_url, rtl433_manual_learning_url
from .learning import (
    DEFAULT_LEARN_TIMEOUT_SECONDS,
    DEFAULT_RECEIVE_TIMEOUT_SECONDS,
    ERROR_BACKEND_UNAVAILABLE,
    MIN_UNIQUE_CMD1_SAMPLES,
    MIN_UNIQUE_CMD2_SAMPLES,
    MIN_VALID_PACKETS,
    LearnResult,
    LearnSession,
    async_capture_next_learning_packet,
    async_close_learning_session,
    async_start_learning_session,
    derive_learn_result_from_session,
)
from .packet_debug import get_packet_debug_logger
from .profile import (
    DuplicateProfileIdError,
    InvalidNibbleError,
    InvalidProfileNameError,
    InvalidRemoteIdError,
    InvalidRtl433SamplesError,
    InvalidSavedProfileError,
    Rtl433ProfileDerivationError,
    Rtl433RemoteIdMismatchError,
    Rtl433Sample,
    default_feature_options,
    fireplace_features_from_options,
    normalize_entry_options,
    normalize_feature_options,
    normalize_manual_profile_input,
    normalize_manual_rtl433_profile_samples,
    normalize_saved_profile_input,
    parse_remote_id,
    parse_rtl433_samples,
    remote_id_as_hex,
    sanitize_fireplace_short_name,
)
from .protocol.packet import ProflameFrame, ProflamePacket
from .rf.registry import get_backend_definition, normalize_controller_id
from .rf.yardstick import YardStickBackendUnavailableError

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
    """Build the manual-entry schema for the active build."""

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
            vol.Required(CONF_FAN, default=DEFAULT_FEATURE_OPTIONS[CONF_FAN]): bool,
            vol.Required(CONF_LIGHT, default=DEFAULT_FEATURE_OPTIONS[CONF_LIGHT]): bool,
            vol.Required(CONF_FRONT, default=DEFAULT_FEATURE_OPTIONS[CONF_FRONT]): bool,
            vol.Required(CONF_AUX, default=DEFAULT_FEATURE_OPTIONS[CONF_AUX]): bool,
            vol.Required(CONF_CPI, default=DEFAULT_FEATURE_OPTIONS[CONF_CPI]): bool,
        }
    )


def _manual_rtl433_profile_schema() -> vol.Schema:
    """Build the rtl_433-assisted manual-learning setup schema."""

    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_FIREPLACE_SHORT_NAME, default=DEFAULT_FIREPLACE_SHORT_NAME): str,
            vol.Required(CONF_BACKEND_TYPE, default=BACKEND_YARDSTICK): _backend_selector(),
        }
    )


def _manual_rtl433_prompt_schema() -> vol.Schema:
    """Build the per-prompt rtl_433 sample paste schema."""

    return vol.Schema(
        {
            vol.Required(CONF_RTL433_SAMPLES): TextSelector(TextSelectorConfig(multiline=True)),
        }
    )


MANUAL_RTL433_POWER_OFF_SCHEMA = vol.Schema({})


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
        vol.Required(CONF_DEBUG_LOGGING, default=DEFAULT_DEBUG_LOGGING): bool,
        vol.Required(CONF_ACTIVE_LISTENING, default=False): bool,
        vol.Required(CONF_FIREPLACE_SHORT_NAME, default=DEFAULT_FIREPLACE_SHORT_NAME): str,
    }
)

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

RTL433_MANUAL_PROMPTS: tuple[tuple[str, str], ...] = (
    ("power_on", "Press **Power** once, then paste the rtl_433 decoded line for that press."),
    ("temp_down", "Press **Temp Down** once, then paste the rtl_433 decoded line for that press."),
    ("temp_up", "Press **Temp Up** once, then paste the rtl_433 decoded line for that press."),
    ("temp_down_again", "Press **Temp Down** once more, then paste the rtl_433 decoded line for that press."),
    ("temp_up_again", "Press **Temp Up** once more, then paste the rtl_433 decoded line for that press."),
)
RTL433_MANUAL_COMMAND = "rtl_433 -f 315M -R 207 -M level -F json"
RTL433_MANUAL_LEARNING_GUIDE = (
    f"[rtl_433-assisted manual learning guide]({rtl433_manual_learning_url()})"
)


class Proflame2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proflame2."""

    VERSION = 1

    _learn_input: dict[str, Any] | None = None
    _learn_result: LearnResult | None = None
    _learn_task: asyncio.Task[ProflamePacket | LearnResult] | None = None
    _learn_session: LearnSession | None = None
    _learn_prompt_index: int = 0
    _manual_pending_data: dict[str, Any] | None = None
    _manual_pending_options: dict[str, Any] | None = None
    _manual_rtl433_input: dict[str, Any] | None = None
    _manual_rtl433_samples: list[Rtl433Sample]
    _manual_rtl433_prompt_index: int = 0

    def __init__(self) -> None:
        """Initialize per-flow manual learning state."""

        self._manual_rtl433_samples = []

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
            menu_options=["learn", "manual_rtl433", "manual"],
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Handle manual fireplace profile entry."""

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                normalized = normalize_manual_profile_input(user_input)
            except InvalidRemoteIdError:
                errors[CONF_REMOTE_ID] = "invalid_remote_id"
            except InvalidNibbleError as exc:
                error_key = _extract_invalid_nibble_key(user_input, exc)
                errors[error_key] = "invalid_nibble"
            else:
                if self._backend_requires_esphome_entry(normalized.data[CONF_BACKEND_TYPE]):
                    linked_entry_id = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
                    if linked_entry_id:
                        normalized.data[CONF_ESPHOME_ENTRY_ID] = linked_entry_id
                    esphome_entry_error = self._validate_manual_esphome_link(normalized.data)
                    if esphome_entry_error is not None:
                        self._manual_pending_data = normalized.data
                        self._manual_pending_options = normalized.options
                        return await self.async_step_manual_esphome()
                return await self._async_create_profile_entry(
                    title=normalized.data[CONF_NAME],
                    data=normalized.data,
                    options=normalized.options,
                )

        suggested_values = self._manual_suggested_values()
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(_manual_profile_schema(), user_input or suggested_values),
            errors=errors,
        )

    async def async_step_manual_rtl433(self, user_input: dict[str, Any] | None = None):
        """Collect setup details for rtl_433-assisted manual learning."""

        if user_input is None:
            suggested_values = self._manual_rtl433_suggested_values()
            return self.async_show_form(
                step_id="manual_rtl433",
                data_schema=self.add_suggested_values_to_schema(
                    _manual_rtl433_profile_schema(),
                    suggested_values,
                ),
            )

        self._manual_rtl433_input = {
            CONF_NAME: str(user_input[CONF_NAME]).strip(),
            CONF_BACKEND_TYPE: normalize_controller_id(user_input[CONF_BACKEND_TYPE]),
            CONF_FIREPLACE_SHORT_NAME: sanitize_fireplace_short_name(
                user_input.get(CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME)
            ),
        }
        self._manual_rtl433_samples = []
        self._manual_rtl433_prompt_index = 0
        if self._backend_requires_esphome_entry(self._manual_rtl433_input[CONF_BACKEND_TYPE]):
            return await self.async_step_manual_rtl433_esphome()
        return await self.async_step_manual_rtl433_prompt()

    async def async_step_manual_rtl433_esphome(self, user_input: dict[str, Any] | None = None):
        """Collect the ESPHome device link for rtl_433-assisted LilyGO setup."""

        if self._manual_rtl433_input is None:
            return await self.async_step_manual_rtl433()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._manual_rtl433_input[CONF_ESPHOME_ENTRY_ID] = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            esphome_entry_error = self._validate_manual_esphome_link(self._manual_rtl433_input)
            if esphome_entry_error is None:
                return await self.async_step_manual_rtl433_prompt()
            errors[CONF_ESPHOME_ENTRY_ID] = esphome_entry_error

        return self.async_show_form(
            step_id="manual_rtl433_esphome",
            data_schema=self._esphome_link_schema(),
            errors=errors,
            description_placeholders={"setup_text": LILYGO_ESPHOME_LINK_HELP},
        )

    async def async_step_manual_rtl433_prompt(self, user_input: dict[str, Any] | None = None):
        """Collect one prompted rtl_433 decoded sample batch."""

        if self._manual_rtl433_input is None:
            return await self.async_step_manual_rtl433()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parsed_samples = parse_rtl433_samples(user_input[CONF_RTL433_SAMPLES])
                updated_samples = [*self._manual_rtl433_samples, *parsed_samples]
                self._validate_manual_rtl433_samples(updated_samples)
            except InvalidRtl433SamplesError:
                errors[CONF_RTL433_SAMPLES] = "invalid_rtl433_samples"
            except Rtl433RemoteIdMismatchError:
                errors[CONF_RTL433_SAMPLES] = "rtl433_remote_id_mismatch"
            except Rtl433ProfileDerivationError:
                errors[CONF_RTL433_SAMPLES] = "rtl433_profile_derivation_failed"
            else:
                self._manual_rtl433_samples = updated_samples
                maybe_result = self._try_build_manual_rtl433_learn_result()
                if maybe_result is not None:
                    self._learn_result = maybe_result
                    return await self.async_step_manual_rtl433_power_off()
                self._manual_rtl433_prompt_index += 1

        prompt_label, instruction = self._current_manual_rtl433_prompt()
        return self.async_show_form(
            step_id="manual_rtl433_prompt",
            data_schema=self.add_suggested_values_to_schema(
                _manual_rtl433_prompt_schema(),
                {CONF_RTL433_SAMPLES: ""},
            ),
            errors=errors,
            description_placeholders={
                "instruction": instruction,
                "prompt_label": prompt_label,
                "rtl433_command": RTL433_MANUAL_COMMAND,
                "rtl433_manual_learning_guide": RTL433_MANUAL_LEARNING_GUIDE,
                "sample_count": str(len(self._manual_rtl433_samples)),
            },
        )

    async def async_step_manual_rtl433_power_off(self, user_input: dict[str, Any] | None = None):
        """Confirm manual rtl_433 learning is complete."""

        if self._learn_result is None or not self._learn_result.success:
            return await self.async_step_manual_rtl433_prompt()
        if user_input is not None:
            return await self.async_step_learn_features()
        return self.async_show_form(
            step_id="manual_rtl433_power_off",
            data_schema=MANUAL_RTL433_POWER_OFF_SCHEMA,
        )

    async def async_step_manual_esphome(self, user_input: dict[str, Any] | None = None):
        """Collect the ESPHome device link only for manual LilyGO setup."""

        if self._manual_pending_data is None or self._manual_pending_options is None:
            return await self.async_step_manual()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._manual_pending_data[CONF_ESPHOME_ENTRY_ID] = str(user_input.get(CONF_ESPHOME_ENTRY_ID, "")).strip()
            esphome_entry_error = self._validate_manual_esphome_link(self._manual_pending_data)
            if esphome_entry_error is None:
                return await self._async_create_profile_entry(
                    title=self._manual_pending_data[CONF_NAME],
                    data=self._manual_pending_data,
                    options=self._manual_pending_options,
                )
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
        self._learn_result = None
        await self._async_dispose_learning_session()
        learn_session = await self._async_start_learning_session_or_fail()
        if learn_session is None:
            return await self.async_step_learn_failed()
        self._learn_session = learn_session
        self._learn_prompt_index = 0
        return await self.async_step_learn_prompt()

    async def async_step_learn_prompt(self, user_input: dict[str, Any] | None = None):
        """Begin listening immediately for the next guided prompt."""

        if self._learn_input is None or self._learn_session is None:
            return await self.async_step_learn()

        self._learn_session.prompt_index = self._learn_prompt_index
        self._learn_session.prompt_label = self._current_learn_prompt_label()
        self._learn_session.prompt_instruction = self._current_learn_instruction()
        if self._learn_session.debug_logging_enabled:
            get_packet_debug_logger().info(
                "config_flow: prompt_index=%s instruction=%s",
                self._learn_prompt_index,
                self._current_learn_instruction(),
            )
        self._learn_task = self.hass.async_create_task(async_capture_next_learning_packet(self._learn_session))
        return self.async_show_progress(
            step_id="learn_progress",
            progress_action="learn_remote",
            description_placeholders={
                "backend_name": available_backend_labels()[self._learn_input[CONF_BACKEND_TYPE]],
                "instruction": self._current_learn_instruction(),
            },
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
                description_placeholders={
                    "backend_name": available_backend_labels()[self._learn_input[CONF_BACKEND_TYPE]],
                    "instruction": self._current_learn_instruction(),
                },
                progress_task=self._learn_task,
            )

        capture_result = await self._learn_task
        self._learn_task = None

        if isinstance(capture_result, LearnResult):
            self._learn_result = capture_result
            await self._async_dispose_learning_session()
            return self.async_show_progress_done(
                next_step_id="learn_features" if capture_result.success else "learn_failed"
            )

        self._learn_prompt_index += 1
        maybe_result = derive_learn_result_from_session(self._learn_session)
        if maybe_result is not None:
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
            if self._backend_requires_esphome_entry(self._learn_input[CONF_BACKEND_TYPE]):
                data[CONF_ESPHOME_ENTRY_ID] = self._learn_input[CONF_ESPHOME_ENTRY_ID]
            if self._learn_result.final_packet is not None:
                data[CONF_INITIAL_FRAME] = asdict(self._learn_result.final_packet.frame)
                data[CONF_INITIAL_PACKET_SOURCE] = self._learn_result.final_packet.source or "observed_packet"
            return await self._async_create_profile_entry(
                title=data[CONF_NAME],
                data=data,
                options=normalize_entry_options(user_input),
            )

        suggested_values = default_feature_options()
        suggested_values[CONF_FIREPLACE_SHORT_NAME] = self._learn_input.get(
            CONF_FIREPLACE_SHORT_NAME,
            DEFAULT_FIREPLACE_SHORT_NAME,
        )
        suggested_values[CONF_DEBUG_LOGGING] = self._learn_input.get(
            CONF_DEBUG_LOGGING,
            DEFAULT_DEBUG_LOGGING,
        )
        suggested_values[CONF_ACTIVE_LISTENING] = self._learn_input[CONF_BACKEND_TYPE] == BACKEND_ESPHOME
        return self.async_show_form(
            step_id="learn_features",
            data_schema=self.add_suggested_values_to_schema(
                FEATURE_SELECTION_SCHEMA,
                suggested_values,
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
            menu_options=["retry_learn", "manual_rtl433", "manual"],
            description_placeholders=description_placeholders,
        )

    async def async_step_retry_learn(self, user_input: dict[str, Any] | None = None):
        """Retry learn mode with the same backend and fireplace name."""

        if self._learn_input is None:
            return await self.async_step_learn()

        self._learn_result = None
        await self._async_dispose_learning_session()
        learn_session = await self._async_start_learning_session_or_fail()
        if learn_session is None:
            return await self.async_step_learn_failed()
        self._learn_session = learn_session
        self._learn_prompt_index = 0
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

    def _manual_rtl433_suggested_values(self) -> dict[str, Any]:
        """Build suggested values for rtl_433-assisted manual learning."""

        suggested = self._manual_suggested_values()
        suggested[CONF_RTL433_SAMPLES] = ""
        for key in (CONF_REMOTE_ID, CONF_C1, CONF_D1, CONF_C2, CONF_D2):
            suggested.pop(key, None)
        return suggested

    def _current_manual_rtl433_prompt(self) -> tuple[str, str]:
        """Return the current manual rtl_433 prompt, cycling as needed."""

        return RTL433_MANUAL_PROMPTS[self._manual_rtl433_prompt_index % len(RTL433_MANUAL_PROMPTS)]

    def _validate_manual_rtl433_samples(self, samples: list[Rtl433Sample]) -> None:
        """Validate accumulated rtl_433 evidence without requiring success yet."""

        if self._manual_rtl433_input is None:
            raise InvalidRtl433SamplesError("Manual rtl_433 setup has not started.")
        normalize_manual_rtl433_profile_samples(
            self._manual_rtl433_input,
            tuple(samples),
        )

    def _try_build_manual_rtl433_learn_result(self) -> LearnResult | None:
        """Return a learned profile once pasted rtl_433 rows prove enough evidence."""

        if self._manual_rtl433_input is None:
            return None
        samples = tuple(self._manual_rtl433_samples)
        if len(samples) < MIN_VALID_PACKETS:
            return None

        cmd1_samples = {(sample.cmd1, sample.err1) for sample in samples}
        cmd2_samples = {(sample.cmd2, sample.err2) for sample in samples}
        if len(cmd1_samples) < MIN_UNIQUE_CMD1_SAMPLES or len(cmd2_samples) < MIN_UNIQUE_CMD2_SAMPLES:
            return None

        normalized = normalize_manual_rtl433_profile_samples(self._manual_rtl433_input, samples)
        self._learn_input = {
            CONF_NAME: normalized.profile.data[CONF_NAME],
            CONF_BACKEND_TYPE: normalized.profile.data[CONF_BACKEND_TYPE],
            CONF_FIREPLACE_SHORT_NAME: normalized.profile.options.get(
                CONF_FIREPLACE_SHORT_NAME,
                DEFAULT_FIREPLACE_SHORT_NAME,
            ),
            CONF_DEBUG_LOGGING: normalized.profile.options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING),
        }
        if CONF_ESPHOME_ENTRY_ID in normalized.profile.data:
            self._learn_input[CONF_ESPHOME_ENTRY_ID] = normalized.profile.data[CONF_ESPHOME_ENTRY_ID]

        final_sample = samples[-1]
        final_packet = ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=final_sample.remote_id,
                cmd1=final_sample.cmd1,
                err1=final_sample.err1,
                cmd2=final_sample.cmd2,
                err2=final_sample.err2,
            ),
            source="rtl433_manual",
        )
        return LearnResult(
            success=True,
            remote_id=normalized.profile.data[CONF_REMOTE_ID],
            c1=normalized.profile.data[CONF_C1],
            d1=normalized.profile.data[CONF_D1],
            c2=normalized.profile.data[CONF_C2],
            d2=normalized.profile.data[CONF_D2],
            packets_seen=len(samples),
            valid_packets=len(samples),
            final_packet=final_packet,
        )

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

        if self._learn_prompt_index < len(LEARN_PROMPTS):
            instruction = LEARN_PROMPTS[self._learn_prompt_index][1]
        else:
            instruction = EXTRA_LEARN_PROMPT[1]
        return instruction

    def _current_learn_prompt_label(self) -> str:
        """Return the short label for the current guided-learning prompt."""

        if self._learn_prompt_index < len(LEARN_PROMPTS):
            return LEARN_PROMPTS[self._learn_prompt_index][0]
        return EXTRA_LEARN_PROMPT[0]

    async def _async_dispose_learning_session(self) -> None:
        """Close the current guided-learning backend session, if any."""

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
            )
        except YardStickBackendUnavailableError as exc:
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
            updated = normalize_entry_options({**self.config_entry.options, **user_input})
            updated.update(normalize_feature_options(user_input))
            updated[CONF_DEBUG_LOGGING] = bool(
                user_input.get(
                    CONF_DEBUG_LOGGING,
                    updated.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING),
                )
            )
            updated[CONF_ACTIVE_LISTENING] = bool(
                user_input.get(CONF_ACTIVE_LISTENING, updated.get(CONF_ACTIVE_LISTENING, False))
            )
            updated[CONF_FIREPLACE_SHORT_NAME] = sanitize_fireplace_short_name(
                user_input.get(
                    CONF_FIREPLACE_SHORT_NAME,
                    updated.get(CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME),
                )
            )
            return self.async_create_entry(data=updated)

        suggested_values = default_feature_options()
        suggested_values.update(normalize_entry_options(self.config_entry.options))
        return self.async_show_form(
            step_id="features",
            data_schema=self.add_suggested_values_to_schema(FEATURE_SELECTION_SCHEMA, suggested_values),
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
                updated = normalize_entry_options(self.config_entry.options)
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
                    profile_id=profile_id,
                )
            except InvalidProfileNameError:
                errors[CONF_NAME] = "invalid_profile_name"
            except InvalidSavedProfileError as exc:
                errors[_extract_invalid_profile_field(user_input, str(exc))] = "invalid_profile"
            else:
                updated = normalize_entry_options(self.config_entry.options)
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
            updated = normalize_entry_options(self.config_entry.options)
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
        vol.Required(CONF_FLAME, default=1): vol.Coerce(int),
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
