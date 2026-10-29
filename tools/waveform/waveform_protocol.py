"""Load the existing pure RF decoder without bootstrapping Home Assistant.

Use a private package namespace for offline tools; never replace or stub the
integration's real packages in sys.modules. Protocol source remains shared.
"""

import sys
from importlib import import_module
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2] / "custom_components/proflame2"
NAMESPACE = "_proflame2_waveform_protocol"

for suffix, directory in (("", ROOT), (".rf", ROOT / "rf"), (".protocol", ROOT / "protocol")):
    name = NAMESPACE + suffix
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(directory)]
        package.__package__ = name
        package.__spec__ = ModuleSpec(name, loader=None, is_package=True)
        package.__spec__.submodule_search_locations = package.__path__
        sys.modules[name] = package

_pulse = import_module(NAMESPACE + ".rf.pulse")
find_proflame_pcm_candidates = _pulse.find_proflame_pcm_candidates


def decode_structural_legacy_words(bits: str, offset: int) -> tuple[int, ...] | None:
    """Debug-only seven-word structure/parity check without operational gap policy.

    Reuse the existing word decoder, not a second implementation. This does not
    establish legacy integrity, extended-frame format, or suitability for TX.
    """
    return _pulse._decode_words(bits, offset, 7)
