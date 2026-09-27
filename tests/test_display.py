"""Every published format is one of agents-core's standard `StatFormat`s (agents-hub
renders only those), for every configured indicator and every transform it uses."""

from __future__ import annotations

import typing

from agents_core.schema import StatFormat

from agents.macro.config import load_macro_config
from agents.macro.display import delta_display, display_for

STANDARD = set(typing.get_args(StatFormat))


def test_every_configured_format_is_standard():
    for ind in load_macro_config().indicators:
        for transform in [ind.primary, *ind.secondary, "change_pp", "level"]:
            assert display_for(ind, transform).format in STANDARD, (ind.id, transform)
        assert delta_display(ind).format in STANDARD, ind.id
