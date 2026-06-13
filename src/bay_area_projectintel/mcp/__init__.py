"""MCP surface for ProjectIntel.

Exposes the project's compliance-aware crawler primitives as Model Context
Protocol tools so an external agent (a cloud routine, an agent SDK, or a
future WeChat bridge) can drive crawling without re-implementing — or
bypassing — robots.txt checks, per-domain rate limiting, and the local cache.
"""
