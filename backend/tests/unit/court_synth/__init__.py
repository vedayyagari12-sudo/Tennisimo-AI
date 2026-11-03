"""Synthetic court truth renderer (SESSION_MODE_PHASE2.md 11.1). Test-only.

Never imported by ``app/`` (enforced by ``test_app_never_imports_tests``). No
video is committed and no file is written: frames are generated in memory.

Passing synthetic tests does not validate real-world accuracy. The scene
shares the design's assumptions, including a clean model of how a ball looks.
"""
