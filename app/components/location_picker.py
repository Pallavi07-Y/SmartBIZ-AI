from pathlib import Path

import streamlit.components.v1 as components


_component = components.declare_component(
    "smartbiz_location_picker",
    path=str(Path(__file__).with_name("location_picker")),
)


def location_picker(
    *,
    key,
    button_label="Use My Location",
):
    return _component(
        key=key,
        button_label=button_label,
        default=None,
    )
