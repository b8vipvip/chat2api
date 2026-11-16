"""Single production composition owner for the C-track control-plane modules.

C-01 policy storage is installed before v17 so the existing administrator
session middleware can enforce it. Remaining C modules are installed only
after their existing production dependencies are present.
"""
from __future__ import annotations

from typing import Any


def install_control_plane_authority_v1(app: Any) -> Any:
    """Install the C-01 policy store/routes before v17 installs admin auth."""
    from .control_plane_v1_patch import install_control_plane_v1

    return install_control_plane_v1(app)


def install_control_plane_features_v1(app: Any) -> Any:
    """Install dependent C-02..C-07 services exactly once.

    This wires the available slices into the production ASGI app. It does not
    claim complete tenant resource isolation, full model-endpoint coverage,
    executable plugins, or runtime quota reservation.
    """
    if getattr(app.state, "control_plane_features_v1_installed", False):
        return app
    if not getattr(app.state, "control_plane_installed", False):
        raise RuntimeError("C-01 control-plane authority must be installed before C-track features")

    required = ("admin_sessions", "api_keys", "user_accounts", "registry")
    missing = [name for name in required if getattr(app.state, name, None) is None]
    if missing:
        raise RuntimeError(
            "C-track production integration requires existing app state: "
            + ", ".join(missing)
        )

    # Invitations consume the existing server-side registration policy.
    from .registration_invitations_v1_patch import install_registration_invitations
    install_registration_invitations(app)

    # Workspace membership must precede entitlement evaluation because tenant
    # and tenant-role policies resolve from the real key owner/membership record.
    from .tenant_membership_v1_patch import install_tenant_memberships
    install_tenant_memberships(app)

    # This wraps the existing final model resolver/catalog; it never selects a
    # Worker and never substitutes requested model for served-model evidence.
    from .model_entitlement_v1_patch import install_model_entitlements
    install_model_entitlements(app)

    # Product plugins remain metadata-only. No executor or secret loading is
    # installed by this integration boundary.
    from .plugin_registry_v1_patch import install_plugin_registry
    install_plugin_registry(app)

    # The reservation journal is exposed for inspection/reconciliation only.
    # Its request-admission and Broker-terminal hooks remain deliberately off.
    from .quota_reservation_admin_v1_patch import install_quota_reservation_admin_v1
    install_quota_reservation_admin_v1(app)
    if getattr(app.state, "quota_reservation_runtime_enabled", False):
        raise RuntimeError("C-06 runtime reservation hooks must remain disabled until lifecycle integration is verified")

    # Serve the C-07 feature asset through the existing single admin page owner.
    from .admin_control_plane_ui_v1_patch import install_admin_control_plane_ui_v1
    install_admin_control_plane_ui_v1(app)

    app.state.control_plane_features_v1_installed = True
    app.state.control_plane_integration_version = 1
    return app
