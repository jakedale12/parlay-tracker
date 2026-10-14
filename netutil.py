"""Outbound HTTP helper with a same-host-only redirect guard (SSRF hygiene).

Every external fetch in the tracker (ESPN, pick sources, Reddit RSS, NHL
API) goes through open_url(). Redirects are followed ONLY when the target
stays on the SAME host over https — a redirect to a different host (a
subdomain change counts as different), or a downgrade to plain http, is
refused and surfaces to the caller as an HTTPError carrying the 3xx code.
Scraped pages therefore cannot bounce the tracker into the LAN or any
other host via a cross-host redirect.
"""
import urllib.error
import urllib.parse
import urllib.request


class SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only https->https on the exact same host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        old = urllib.parse.urlparse(req.full_url)
        new = urllib.parse.urlparse(newurl)
        if old.scheme != "https" or new.scheme != "https" or \
                (new.hostname or "") != (old.hostname or ""):
            return None  # unhandled -> caller gets HTTPError(code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(SameHostRedirectHandler())


def open_url(req, timeout=30):
    """urlopen() replacement that pins redirects to the original host."""
    return _OPENER.open(req, timeout=timeout)
