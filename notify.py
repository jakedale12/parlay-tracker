"""ntfy push — same pattern as far-monitor/glitch-monitor (self-hosted ntfy)."""
import urllib.request

import config


def ntfy(title, message, priority="default", tags=None):
    safe_title = title.encode("ascii", "replace").decode("ascii")  # headers are latin-1
    headers = {"Title": safe_title[:120], "Priority": priority}
    if tags:
        if isinstance(tags, str):
            tags = [tags]
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(
        f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}",
        data=message.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status
