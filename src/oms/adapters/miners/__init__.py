"""Per-adapter conversation miners (M13.1) — the ``Adapter.mine`` delegates.

One module per adapter (the ``oms.adapters.skills`` pattern). A miner reads
the harness's OWN local transcript of a wrapped run and normalizes it into
the ``harness`` rendition shape (Trace Renditions & Mining §4a):

    {"miner_version", "binding": "hook|scan", "completeness",
     "run_started", "segments": [{"harness_session_id", "transcript_path",
     "turns": [{"role", "ts", "text", "tool"?}]}]}

Miners parse defensively — the on-disk formats are undocumented and drift —
and scrub every text field before returning (transcripts carry full tool
inputs/outputs; the artifact may go public as a rendition).
"""
