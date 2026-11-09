"""``scripts/tracknet_eval.py``: the pure scoring parts. The dataset itself is
never in the repo, so the clip runner is exercised only by hand."""

from __future__ import annotations

from scripts.tracknet_eval import match_count, summarize


def test_match_count_is_one_to_one_and_nearest_first() -> None:
    assert match_count([1.0, 2.0], [1.01, 1.02, 2.5], 0.07) == 1
    assert match_count([1.0, 1.05], [1.03], 0.07) == 1
    assert match_count([1.0, 1.05], [1.0, 1.06], 0.07) == 2
    assert match_count([], [1.0], 0.07) == 0
    assert match_count([1.0], [], 0.07) == 0


def test_summary_skips_refused_and_unanalysed_clips_and_counts_gross_errors() -> None:
    rows = [
        {"game": 1, "clip": 1, "status": "complete", "truth_bounces": 4, "found_bounces": 4,
         "matched_bounces": 3, "truth_hits": 2, "found_hits": 1, "matched_hits": 1,
         "pos_err_m": [0.1, 0.3, 5.0]},
        {"game": 1, "clip": 2, "status": "refused", "truth_bounces": 9, "found_bounces": 0,
         "matched_bounces": 0, "truth_hits": 9, "found_hits": 0, "matched_hits": 0, "pos_err_m": []},
        {"game": 1, "clip": 3, "error": "VideoDecodeError: video_too_short"},
    ]
    text = summarize(rows)
    assert "clips 3, analysed 1" in text
    assert "bounces 3/4 recall 0.75 precision 0.75" in text
    assert "median error 0.20 m, over 3 m: 1/3" in text
