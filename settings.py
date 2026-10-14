"""Settings — ONE place (Stage 5). Flat `key: value` YAML-style file at
$PARLAY_DATA/settings.yaml (on the botserver: /data/settings.yaml in the
volume). Sensible defaults; the system runs well untouched. Edit the file or
use `app.py settings-set key value`; the dashboard displays all values.

unit_value (dollars per 1u) is UNSET by default and stays unset until Jake
sets it. Stakes are ALWAYS shown in units — never dollars — regardless.
"""
import os

import config

FILE_NAME = "settings.yaml"

DEFAULTS = {
    "target_book": "FanDuel",       # book suggestions are targeted at
    "sports_nhl": "on",             # on|off
    "sports_nfl": "on",             # on|off
    "unit_value": "",               # dollars per unit — UNSET until Jake sets it
    "quiet_hours_start": "23",      # ET, ntfy quiet window start (inclusive)
    "quiet_hours_end": "7",         # ET, quiet window end (exclusive)
}

_INT_KEYS = {"quiet_hours_start", "quiet_hours_end"}


def _path():
    return os.path.join(config.DATA_DIR, FILE_NAME)


def load():
    s = dict(DEFAULTS)
    try:
        with open(_path()) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in DEFAULTS:
                    s[k] = v
    except FileNotFoundError:
        pass
    for k in _INT_KEYS:
        try:
            s[k] = int(s[k])
        except (TypeError, ValueError):
            s[k] = int(DEFAULTS[k])
    return s


def save(updates):
    s = load()
    for k, v in updates.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting: {k}")
        s[k] = str(v)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(_path(), "w") as f:
        f.write("# Parlay Tracker settings — one place. Stakes are always in "
                "units; unit_value stays blank until Jake sets it.\n")
        for k in DEFAULTS:
            f.write(f"{k}: {s[k]}\n")
    return load()


def sports_enabled(s=None):
    s = s or load()
    out = []
    if str(s.get("sports_nhl", "on")).lower() == "on":
        out.append("NHL")
    if str(s.get("sports_nfl", "on")).lower() == "on":
        out.append("NFL")
    return out


def unit_value(s=None):
    """Dollars per unit, or None when unset (default). Never rendered on slips."""
    s = s or load()
    v = str(s.get("unit_value") or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None
