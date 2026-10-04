"""Network binding rules for the local-only desktop application."""

import ipaddress


def validate_local_bind_host(host: str) -> str:
    """Return a local-only bind host or raise a clear configuration error."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError(
            "HOST must be 'localhost' or a loopback IP address; "
            "this application only supports local binding"
        )

    normalized = host.strip()
    if normalized.casefold() == "localhost":
        return normalized

    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise ValueError(
            "HOST must be 'localhost' or a loopback IP address; "
            "this application only supports local binding"
        ) from exc

    if not address.is_loopback:
        raise ValueError(
            "HOST must be 'localhost' or a loopback IP address; "
            "this application only supports local binding"
        )

    return normalized
