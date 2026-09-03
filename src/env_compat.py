"""Canonical Pandamonium environment names with Odysseus compatibility."""

from __future__ import annotations

import os
from collections.abc import MutableMapping


def apply_legacy_env_aliases(
    environ: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    """Expose canonical ``PANDAMONIUM_*`` values to legacy readers.

    The application still has established internal readers for
    ``ODYSSEUS_*``. A canonical value wins when both names are present; an
    old-only deployment keeps working unchanged.
    """

    target = os.environ if environ is None else environ
    for key, value in tuple(target.items()):
        if key.startswith("PANDAMONIUM_"):
            target[f"ODYSSEUS_{key.removeprefix('PANDAMONIUM_')}"] = value
    return target
