"""Self-signed HTTPS, adapted from Dan (한순중) phone_web_bridge.py @9da1f63.

Certificates now live under caller-supplied user data, never in a Git checkout.
cryptography is imported only when HTTPS is explicitly started.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import ssl
from pathlib import Path

from manux.link.security import private_address


def outside_repository(path):
    path = Path(path).resolve()
    if any((p / ".git").exists() or (p / "src/manux/core/contracts.py").exists()
           for p in (path, *path.parents)):
        raise ValueError("phone certificates must be outside any repository")
    # Includes this checkout even if its .git marker was not packaged.
    app = Path(__file__).resolve().parents[3]
    if path == app or app in path.parents:
        raise ValueError("phone certificates must be outside the application")
    return path


def ensure_certificate(data_dir, host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if not private_address(host):
        raise ValueError("private IP required")
    root = outside_repository(data_dir)
    folder = outside_repository(root / "link/certs")
    if not folder.is_relative_to(root):
        raise ValueError("certificate directory escapes user data")
    cert_path, key_path = folder / "phone-cert.pem", folder / "phone-key.pem"
    if any(p.is_symlink() for p in (cert_path, key_path)):
        raise ValueError("certificate file links are not allowed")
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(host) in sans.get_values_for_type(x509.IPAddress) and cert.not_valid_after_utc > now:
            return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                     x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MANUX local phone link")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    # Restrictive creation mode on POSIX; Windows inherits the user data directory ACL.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(data_dir, host):
    cert, key = ensure_certificate(data_dir, host)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context
