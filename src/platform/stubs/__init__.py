"""A6 — trivial pass-through implementations of the four §6 stage CLIs.

They exist so the orchestrator and the integration harness can run the full
pipeline end-to-end before any real module is merged (--use-stubs). Each
stub honors the complete §6 contract: flags, exit codes, the final-stderr-
line metrics/error protocol, streaming IO, and schema-valid output — but
performs only trivial transformations. Stubs never call an LLM.
"""
