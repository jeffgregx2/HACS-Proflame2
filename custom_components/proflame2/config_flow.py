"""Config flow for the Proflame2 integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.core import callback

from .const import (
    BACKEND_TYPES,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DEFAULT_FEATURE_OPTIONS,
    DOMAIN,
)
from .learning import LearnResult, async_run_learning_with_backend
from .profile import (
    DuplicateProfileIdError,
    InvalidNibbleError,
    InvalidProfileNameError,
    InvalidRemoteIdError,
    InvalidSavedProfileError,
    default_feature_options,
    fireplace_features_from_options,
    normalize_entry_options,
    normalize_feature_options,
    normalize_manual_profile_input,
    normalize_saved_profile_input,
    remote_id_as_hex,
)

MANUAL_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_BACKEND_TYPE, default=BACKEND_TYPES[0]): vol.In(BACKEND_TYPES),
        vol.Required(CONF_REMOTE_ID): str,
        vol.Required(CONF_C1): vol.Any(int, str),
        vol.Required(CONF_D1): vol.Any(int, str),
        vol.Required(CONF_C2): vol.Any(int, str),
        vol.Required(CONF_D2): vol.Any(int, str),
        vol.Required(CONF_FAN, default=DEFAULT_FEATURE_OPTIONS[CONF_FAN]): bool,
        vol.Required(CONF_LIGHT, default=DEFAULT_FEATURE_OPTIONS[CONF_LIGHT]): bool,
        vol.Required(CONF_FRONT, default=DEFAULT_FEATURE_OPTIONS[CONF_FRONT]): bool,
        vol.Required(CONF_AUX, default=DEFAULT_FEATURE_OPTIONS[CONF_AUX]): bool,
        vol.Required(CONF_CPI, default=DEFAULT_FEATURE_OPTIONS[CONF_CPI]): bool,
    }
)

LEARN_SETUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_BACKEND_TYPE, default=BACKEND_TYPES[0]): vol.In(BACKEND_TYPES),
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

class Proflame2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proflame2."""

    VERSION = 1

    _learn_input: dict[str, Any] | None = None
    _learn_result: LearnResult | None = None
    _learn_task: asyncio.Task[LearnResult] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "Proflame2OptionsFlow":
        """Return the options flow handler."""

        return Proflame2OptionsFlow()

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
            menu_options=["learn", "manual"],
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
                return await self._async_create_profile_entry(
                    title=normalized.data[CONF_NAME],
                    data=normalized.data,
                    options=normalized.options,
                )

        suggested_values = self._manual_suggested_values()
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                MANUAL_PROFILE_SCHEMA, user_input or suggested_values
            ),
            errors=errors,
        )

    async def async_step_learn(self, user_input: dict[str, Any] | None = None):
        """Start guided remote learning."""

        if user_input is None:
            suggested_values = self._learn_input or {
                CONF_NAME: "",
                CONF_BACKEND_TYPE: BACKEND_TYPES[0],
            }
            return self.async_show_form(
                step_id="learn",
                data_schema=self.add_suggested_values_to_schema(
                    LEARN_SETUP_SCHEMA, suggested_values
                ),
            )

        self._learn_input = {
            CONF_NAME: str(user_input[CONF_NAME]).strip(),
            CONF_BACKEND_TYPE: str(user_input[CONF_BACKEND_TYPE]).strip().lower(),
        }
        self._learn_result = None
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        self._learn_task = self.hass.async_create_task(
            async_run_learning_with_backend(
                self.hass,
                self._learn_input[CONF_BACKEND_TYPE],
                timeout=float(domain_data.get(DATA_LEARNING_TIMEOUT, 10.0)),
                receive_timeout=float(domain_data.get(DATA_LEARNING_RECEIVE_TIMEOUT, 0.5)),
            )
        )
        return self.async_show_progress(
            step_id="learn_progress",
            progress_action="learn_remote",
            progress_task=self._learn_task,
        )

    async def async_step_learn_progress(self, user_input: dict[str, Any] | None = None):
        """Show guided learn progress while the backend-independent task runs."""

        if self._learn_task is None:
            return await self.async_step_learn()

        if not self._learn_task.done():
            return self.async_show_progress(
                step_id="learn_progress",
                progress_action="learn_remote",
                progress_task=self._learn_task,
            )

        if self._learn_result is None:
            self._learn_result = await self._learn_task

        return self.async_show_progress_done(
            next_step_id="learn_features" if self._learn_result.success else "learn_failed"
        )

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
            return await self._async_create_profile_entry(
                title=data[CONF_NAME],
                data=data,
                options=normalize_entry_options(user_input),
            )

        return self.async_show_form(
            step_id="learn_features",
            data_schema=self.add_suggested_values_to_schema(
                FEATURE_SELECTION_SCHEMA,
                default_feature_options(),
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
            menu_options=["retry_learn", "manual"],
            description_placeholders=description_placeholders,
        )

    async def async_step_retry_learn(self, user_input: dict[str, Any] | None = None):
        """Retry learn mode with the same backend and fireplace name."""

        if self._learn_input is None:
            return await self.async_step_learn()

        self._learn_result = None
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        self._learn_task = self.hass.async_create_task(
            async_run_learning_with_backend(
                self.hass,
                self._learn_input[CONF_BACKEND_TYPE],
                timeout=float(domain_data.get(DATA_LEARNING_TIMEOUT, 10.0)),
                receive_timeout=float(domain_data.get(DATA_LEARNING_RECEIVE_TIMEOUT, 0.5)),
            )
        )
        return self.async_show_progress(
            step_id="learn_progress",
            progress_action="learn_remote",
            progress_task=self._learn_task,
        )

    async def _async_create_profile_entry(
        self,
        *,
        title: str,
        data: dict[str, Any],
        options: dict[str, Any],
    ):
        """Create one permanent fireplace profile entry."""

        await self.async_set_unique_id(remote_id_as_hex(data[CONF_REMOTE_ID]))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=title,
            data=data,
            options=options,
        )

    def _manual_suggested_values(self) -> dict[str, Any]:
        """Build suggested values for manual entry, including learn fallback."""

        suggested = {
            CONF_NAME: "",
            CONF_BACKEND_TYPE: BACKEND_TYPES[0],
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

        if self._learn_result is not None and self._learn_result.remote_id is not None:
            suggested[CONF_REMOTE_ID] = remote_id_as_hex(self._learn_result.remote_id)

        return suggested


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
            updated = normalize_entry_options(self.config_entry.options)
            updated.update(normalize_feature_options(user_input))
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

        profile_choices = {
            profile_id: profile[CONF_NAME] for profile_id, profile in self._profiles.items()
        }
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
