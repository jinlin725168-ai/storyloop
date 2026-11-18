"""Compatibility entry point: the Claude Code adapter ships as `storyloop.claude_agent`.

Existing configurations that run this file keep working; new ones use
`["{python}", "-P", "-m", "storyloop.claude_agent", ...]` (what `storyloop init --agent claude` writes).
"""
import sys

from storyloop.claude_agent import main

if __name__ == "__main__":
    sys.exit(main())
