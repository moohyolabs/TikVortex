"""Download engine for TikVortex (originally Ez-TikTok-Downloader by AlexRabbit).

Part of TikVortex - GNU GPL v3 or later, see LICENSE."""

__version__ = "2.0.0"


def _use_system_certificates() -> None:
    """Check HTTPS certificates with the operating system's trust store (the same
    check Chrome uses on Windows) instead of Python's bundled list. Some servers
    (e.g. TikWM's file server) send a certificate chain that browsers accept but
    Python's default check rejects as "certificate has expired". Verification
    stays fully on. Skipped silently if `truststore` isn't installed."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass


_use_system_certificates()
