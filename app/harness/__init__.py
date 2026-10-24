"""The decision harness: follow a precedent, or ask a person.

Any caller (an HTTP pipeline, the CLI, an AI agent over MCP) sends one case and
gets one of two verdicts back. `follow` repeats a decision a person already made,
as a bounded action intent the caller executes itself. `ask` names what differs
or what could not be checked. Nothing in this package writes to a business
system. See docs/decide-once-plan.md.
"""
