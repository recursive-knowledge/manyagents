"""Per-adapter in-agent skill installers (M11).

One module per adapter. Each ``install(*, session_id, oma_home, scope,
dry_run)`` builds an :class:`oms._installer.InstallPlan`, runs the consent
gate, applies atomically, and returns the manifest. All filesystem writes
flow through ``oms._installer`` so transparency + uninstall are uniform.
"""

from __future__ import annotations
