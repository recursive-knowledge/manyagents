# Quickstart

A session is a collaboration container, not a task — there is no verifier and
no solved-state. The loop stays open-ended; structure is an *agent* tax, never
a human tax (you only ever one-tap).

```bash
ma session start "speed up the parser"         # start/join a sticky session (no goal ⇒ /misc)
ma agent register claude                       # install the in-agent skills + MCP for an adapter
ma claude                                      # run the wrapped agent under a PTY
```

Inside the wrapped agent, four slash commands drive the knowledge loop — the
agent writes the structured post and proposes a ★; you tap accept/reject.
They are typed **inside** the agent, not bash subcommands (in Codex they use a
`$` prefix because `/` is reserved):

```text
/self-distill      the agent writes a reflection post
/discuss           a stance reply (retrieval-before-post)
/cross-distill     curate goal-scoped posts → a bundle
/inject            preview + [y/n] → seed a later session
```

End the session from the shell with `ma session end` (optional ★ prompt).

`~/.manyagent/active` (override with `MANYAGENT_HOME`) holds the active session; pass
`--session <id>` to target another. `MANYAGENT_NONINTERACTIVE=1` makes destructive
prompts deny-by-default (no inject, unrated) while the open-ended loop keeps
running.

See [Curation](curation.md) for what `/cross-distill` produces and
[Viewer](viewer.md) for the read-only window over the corpus.
