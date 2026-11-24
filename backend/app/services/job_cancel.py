"""The signal for a job the user stopped (2026-11-22). A job checks for it at
its progress reports and raises it; nothing in between may swallow it."""

from __future__ import annotations


class JobCancelled(Exception):
    """The user stopped this job. Not an error: the job's record and pictures
    are removed and its videos are deleted, as for any job that ends."""


__all__ = ["JobCancelled"]
