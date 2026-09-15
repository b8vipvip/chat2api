"""chat2api server package."""

__version__ = "0.7.1"

# Load transport-integrity hardening before app.main installs the Responses v109
# middleware. The patch mutates only v109 bridge globals and is intentionally
# imported for its installation side effect.
from . import responses_protocol_integrity_v110_patch as _responses_protocol_integrity_v110_patch  # noqa: F401,E402
