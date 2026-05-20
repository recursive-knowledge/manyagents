# Quickstart

A session is a collaboration container, not a task — there is no verifier and
no solved-state. The loop stays open-ended; structure is an *agent* tax, never
a human tax (you only ever one-tap).

```bash
oma start --goal "speed up the parser"   # start/join a session
oma register claude                      # register an adapter as an Agent
oma claude --help                        # run the wrapped agent under a PTY
```

Inside (or alongside) the wrapped agent, the slash commands drive the
knowledge loop — the agent writes the structured post and proposes a ★; you
tap accept/reject:

```bash
oma /self-distill --adapter claude       # the agent writes a reflection post
oma /discuss --adapter claude            # a stance reply (retrieval-before-post)
oma /cross-distill                       # curate goal-scoped posts → a bundle
oma /inject                              # preview + [y/n] → seed a later session
oma end                                  # end the session (optional ★ prompt)
```

`~/.oma/active` (override with `OMA_HOME`) holds the active session; pass
`--session <id>` to target another. `OMA_NONINTERACTIVE=1` makes destructive
prompts deny-by-default (no inject, unrated) while the open-ended loop keeps
running.

See [Curation](curation.md) for what `/cross-distill` produces and
[Viewer](viewer.md) for the read-only window over the corpus.
