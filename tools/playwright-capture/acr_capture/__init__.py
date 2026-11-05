"""acr_capture — Playwright (headless Chromium) capture tooling for the AČR / AZ channel map.

Opens mapped URLs (official webs, unit webs, Facebook / Instagram / X / YouTube /
LinkedIn) like a real desktop user, takes screenshots, extracts the latest post
where feasible and emits records compatible with ``report/data/captures.jsonl``
so ``build_site_v2.py`` keeps working unchanged.
"""

__version__ = "0.1.0"
