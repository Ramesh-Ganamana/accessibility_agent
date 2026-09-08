import re
from urllib.parse import unquote

from accessibility_agent.models import Safety
from accessibility_agent.utils.url_utils import normalize_url, origin


def classify_link(
    url: str, name: str, start_url: str, same_origin: bool, download: bool = False
) -> Safety:
    try:
        normalize_url(url)
    except ValueError:
        return Safety.UNKNOWN
    if same_origin and origin(url) != origin(start_url):
        return Safety.EXTERNAL
    if download:
        return Safety.CAUTION
    if re.search(
        r"\b(delete|remove|logout|log\s*out|sign\s*out|unsubscribe|cancel|purchase|pay|"
        r"payment|checkout|send|submit|place[\s_-]*order|destroy|revoke)\b",
        unquote(url + " " + name).replace("_", " "),
        re.I,
    ):
        return Safety.DESTRUCTIVE
    return Safety.SAFE
