"""chat2api server package."""

__version__ = "0.7.1"

# Evict the exact stale websocket when an extension transport dies abnormally
# (for example RFC 6455 close code 1006) so reconnect/pairing does not keep
# dispatching through a dead registry entry.
from . import registry_transport_health_v149_patch as _registry_transport_health_v149_patch  # noqa: F401,E402

# Load transport-integrity hardening before app.main installs the Responses v109
# middleware. The patch mutates only v109 bridge globals and is intentionally
# imported for its installation side effect.
from . import responses_protocol_integrity_v110_patch as _responses_protocol_integrity_v110_patch  # noqa: F401,E402
