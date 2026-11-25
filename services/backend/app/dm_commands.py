"""Parse the owner's short, relative DM commands."""

import re

COMMAND = re.compile(
    r"\^\s*(caption|story|music|publish|order|fit|mode|autopost)\s*:[ \t]?(.*)",
    re.IGNORECASE | re.DOTALL,
)


def read_command(item):
    text = item.get("text")
    if item.get("item_type") != "text" or not isinstance(text, str):
        return None
    text = text.strip()
    if re.fullmatch(r"\^\s*rights", text, re.IGNORECASE):
        return "rights", "confirmed"
    if shorthand := re.fullmatch(r"\^\s*(draft|autopost)", text, re.IGNORECASE):
        return "publish", shorthand[1].lower()
    match = COMMAND.fullmatch(text)
    if not match:
        return None
    key, value = match[1].lower(), match[2].strip()
    if key == "caption" and len(value) <= 2200:
        return key, value
    if key == "story" and len(value) <= 180:
        return key, value
    if key == "music":
        if value.lower() in {"original", "playlist"}:
            return key, value.lower()
        if value.lower() in {"", "none"}:
            return key, ""
        if re.fullmatch(r"[0-9]{1,32}", value):
            return key, value
    if key in {"publish", "autopost"} and value.lower() in {"post", "story"}:
        return key, value.lower()
    if key == "mode" and value.lower() in {"draft", "autopost"}:
        return "publish", value.lower()
    if key == "fit" and value.lower() in {"original", "seamless", "crop", "dark"}:
        return key, {"original": "native", "seamless": "blend", "crop": "fill", "dark": "fit"}[
            value.lower()
        ]
    if key == "order" and value.lower() in {"randomize", "original"}:
        return key, value.lower()
    if (
        key == "order"
        and len(value) <= 100
        and re.fullmatch(r"[1-9][0-9]?(?:[\s,]+[1-9][0-9]?)+", value)
    ):
        return key, [int(number) for number in re.split(r"[\s,]+", value)]
    return "invalid", ""
