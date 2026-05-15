"""Simple orchestration planner for capture-session actions."""

from __future__ import annotations

from .models import CaptureCommand, CaptureSessionConfig, FireplaceState


def build_operator_prompt(action: CaptureCommand) -> str:
    """Return one operator-facing prompt for the requested action."""

    prompts = {
        CaptureCommand.SETUP_STATE: (
            "Press one safe native remote setup button to establish the current rtl_433 state. "
            "Avoid boundary actions such as Flame Down at flame=1."
        ),
        CaptureCommand.POWER_TOGGLE: "Press Power once.",
        CaptureCommand.FLAME_UP: "Press Flame Up once.",
        CaptureCommand.FLAME_DOWN: "Press Flame Down once.",
        CaptureCommand.FAN_UP: "Press Fan Up once.",
        CaptureCommand.FAN_DOWN: "Press Fan Down once.",
    }
    return prompts[action]


class ActionPlanner:
    """Choose plausible non-no-op actions from the current fireplace state."""

    def __init__(self, config: CaptureSessionConfig) -> None:
        self._config = config
        self._next_command_index = 0
        self._plan_index = 0

    def choose_next_action(self, state: FireplaceState) -> CaptureCommand:
        """Return the next action using a rotating eligible-command order."""

        if self._config.command_plan:
            if self._plan_index >= len(self._config.command_plan):
                return self._config.command_plan[-1]
            return self._config.command_plan[self._plan_index]

        enabled = tuple(self._config.commands)
        enabled_set = set(enabled)

        if state.power in (None, 0):
            if CaptureCommand.POWER_TOGGLE in enabled_set:
                return CaptureCommand.POWER_TOGGLE
            return self._first_enabled_action(enabled)

        eligible = {command for command in enabled if self._is_action_eligible(state, command)}
        if not eligible:
            return self._first_enabled_action(enabled)

        total = len(enabled)
        for offset in range(total):
            index = (self._next_command_index + offset) % total
            candidate = enabled[index]
            if candidate not in eligible:
                continue
            self._next_command_index = (index + 1) % total
            return candidate

        return self._first_enabled_action(enabled)

    def _first_enabled_action(self, enabled: tuple[CaptureCommand, ...]) -> CaptureCommand:
        for candidate in enabled:
            return candidate
        if not enabled:
            raise ValueError("At least one capture command must be enabled.")
        return enabled[0]

    def record_valid_action(self, action: CaptureCommand) -> None:
        """Advance any explicit valid-sample plan after one successful sample."""

        if not self._config.command_plan:
            return
        if self._plan_index >= len(self._config.command_plan):
            return
        if action == self._config.command_plan[self._plan_index]:
            self._plan_index += 1

    def _is_action_eligible(self, state: FireplaceState, action: CaptureCommand) -> bool:
        if action == CaptureCommand.POWER_TOGGLE:
            return True
        if state.power != 1:
            return False
        if action == CaptureCommand.FLAME_UP:
            return state.flame is None or state.flame < self._config.flame_max
        if action == CaptureCommand.FLAME_DOWN:
            return state.flame is None or state.flame > self._config.flame_min
        if action == CaptureCommand.FAN_UP:
            return state.fan is None or state.fan < self._config.fan_max
        if action == CaptureCommand.FAN_DOWN:
            return state.fan is None or state.fan > self._config.fan_min
        return False

    def apply_action(self, state: FireplaceState, action: CaptureCommand) -> FireplaceState:
        """Return one conservatively updated state after a valid sample."""

        power = state.power
        flame = state.flame
        fan = state.fan

        if action == CaptureCommand.POWER_TOGGLE:
            if power == 1:
                return FireplaceState(power=0, flame=flame, fan=fan)
            return FireplaceState(power=1, flame=flame, fan=fan)
        if power != 1:
            return state
        if action == CaptureCommand.FLAME_UP and flame is not None:
            flame = min(self._config.flame_max, flame + 1)
        elif action == CaptureCommand.FLAME_DOWN and flame is not None:
            flame = max(self._config.flame_min, flame - 1)
        elif action == CaptureCommand.FAN_UP and fan is not None:
            fan = min(self._config.fan_max, fan + 1)
        elif action == CaptureCommand.FAN_DOWN and fan is not None:
            fan = max(self._config.fan_min, fan - 1)
        return FireplaceState(power=power, flame=flame, fan=fan)
