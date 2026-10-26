"""Pure public-output policies shared by the projection and offline brief.

These bounded checks do not authorize publication, verify sources or rewrite
stored model responses. They have no server, database or provider dependencies.
"""
import re
from urllib.parse import parse_qsl, urlsplit


def credential_like(value):
    """Recognize the existing key/private-key patterns without echoing them."""
    return isinstance(value, str) and re.search(
        r'(?<![A-Za-z])sk-[A-Za-z0-9_-]{24,}|-----BEGIN .*PRIVATE KEY-----', value
    ) is not None


def public_url(value):
    """Preserve the public projection's existing URL contract."""
    if not isinstance(value, str): return None
    if re.fullmatch(r'/api/arena/sources/[a-f0-9]{64}', value): return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or any(re.search(r'token|secret|password|api.?key|authorization', key, re.I)
                       for key, _ in parse_qsl(parsed.query))):
            return None
        parsed.port
        return value
    except ValueError:
        return None
