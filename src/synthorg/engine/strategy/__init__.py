"""Strategy module for trendslop mitigation.

Provides structural mitigation against LLM tendency to recommend trendy,
context-insensitive strategies.  Includes constitutional principles,
strategic lenses, and prompt injection for strategic agent roles.
Impact scoring and confidence calibration (``impact.py``,
``confidence.py``) had no production caller -- ``adapter.py`` never
wired either into the prompt-injection or decision-record path -- and
were removed.
"""
