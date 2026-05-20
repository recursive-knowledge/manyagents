"""``qwen`` — first-party **stub** (oma.adapters.md "builtin").

The ``qwen`` CLI is not assumed present (it was absent in the validated build
environment). This stub still satisfies the full ``Adapter`` contract so the
registry can list it and a contributor has the smallest possible reference;
its operations raise :class:`~oma.adapters.base.NotInstalled` until a real
``qwen`` integration replaces it. Declares ``source_fidelity="pty"`` (a PTY
tee is all a future qwen author is assumed to have).
"""

from __future__ import annotations

import subprocess

from oma.adapters.base import Adapter, NotInstalled, PromptPrefixInjector, RawTrace


class QwenAdapter(PromptPrefixInjector, Adapter):
    name = "qwen"
    binary = "qwen"
    version = "stub"
    source_fidelity = "pty"

    def invoke(self, args: list[str]) -> subprocess.Popen[str]:
        raise NotInstalled("qwen adapter is a stub; no qwen integration is shipped yet")

    def capture(self) -> RawTrace:
        raise NotInstalled("qwen adapter is a stub; capture() unavailable")
