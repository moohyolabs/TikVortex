"""Diagnose SSL "certificate has expired" errors for TikWM's file server.

Run from Tools > Maintenance > Check TikWM certificate.
Nothing is downloaded. It only looks at the certificates the server presents
(each address is inspected separately) and tells you which ones fail.
"""
import os
import socket
import ssl
import sys
import tempfile
from datetime import datetime, timezone

HOST = "v16.tokcdn.com"

print(f"Python {sys.version.split()[0]} ({sys.executable})")
print(f"OpenSSL: {ssl.OPENSSL_VERSION}")
try:
    import certifi
    print(f"certifi: {certifi.__version__}")
except Exception:
    print("certifi: not installed")
try:
    import truststore
    print(f"truststore: {truststore.__version__} (installed)")
except Exception as e:
    print(f"truststore: NOT available ({e.__class__.__name__}) - run: pip install truststore")
print(f"Your PC's clock (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}")
print()

extra = sys.argv[2:]
try:
    addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)})
    print(f"{HOST} resolves to (on this PC): {', '.join(addrs)}")
except Exception as e:
    addrs = []
    print(f"Could not look up {HOST}: {e}")
for ip in extra:
    if ip not in addrs:
        addrs.append(ip)
if extra:
    print(f"Also checking: {', '.join(extra)}")


def describe(ip):
    # 1) Inspect what the server presents (no trust decision, inspection only)
    insp = ssl.create_default_context()
    insp.check_hostname = False
    insp.verify_mode = ssl.CERT_NONE
    with socket.create_connection((ip, 443), timeout=10) as raw:
        with insp.wrap_socket(raw, server_hostname=HOST) as s:
            der = s.getpeercert(binary_form=True)
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(pem)
    try:
        info = ssl._ssl._test_decode_cert(path)
    finally:
        os.remove(path)
    issuer = dict(x[0] for x in info.get("issuer", ()))
    names = [v for k, v in info.get("subjectAltName", ()) if k == "DNS"]
    print(f"  certificate valid {info.get('notBefore')}  ->  {info.get('notAfter')}")
    print(f"  issued by: {issuer.get('organizationName', '?')} / {issuer.get('commonName', '?')}")
    print(f"  covers: {', '.join(names[:6])}{' ...' if len(names) > 6 else ''}")

    # 2) Normal verification, exactly like the app does
    for label, ctx in (("Python default check", ssl.create_default_context()), ("Windows/system check", None)):
        if ctx is None:
            try:
                import truststore
                ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except Exception:
                print(f"  {label}: skipped (truststore not installed)")
                continue
        try:
            with socket.create_connection((ip, 443), timeout=10) as raw:
                with ctx.wrap_socket(raw, server_hostname=HOST):
                    pass
            print(f"  {label}: OK")
        except Exception as e:
            print(f"  {label}: FAILED - {str(e)[:160]}")


for ip in addrs:
    print(f"\n{ip}:")
    try:
        describe(ip)
    except Exception as e:
        print(f"  could not connect: {e.__class__.__name__}: {str(e)[:160]}")
print("\nIf you need help, include everything above when you report the problem.")
