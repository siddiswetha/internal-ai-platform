"""Unit tests for feature-flag and canary choice (question service)."""

import os
import unittest

from helpers import load_service

question = load_service("question_main", "question-service")


class TestChooseStyle(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ANSWER_STYLE", None)

    def tearDown(self):
        os.environ.pop("ANSWER_STYLE", None)

    def test_no_canary_returns_configured_style(self):
        flags = {"answer_style": "short",
                 "canary": {"enabled": False, "percent": 10, "new_style": "detailed"}}
        self.assertEqual(question.choose_style("abc123", flags), "short")

    def test_canary_at_100_percent_gives_new_style(self):
        flags = {"answer_style": "short",
                 "canary": {"enabled": True, "percent": 100, "new_style": "detailed"}}
        self.assertEqual(question.choose_style("abc123", flags), "detailed")

    def test_canary_at_0_percent_gives_old_style(self):
        flags = {"answer_style": "short",
                 "canary": {"enabled": True, "percent": 0, "new_style": "detailed"}}
        self.assertEqual(question.choose_style("abc123", flags), "short")

    def test_same_trace_id_always_gets_same_style(self):
        flags = {"answer_style": "short",
                 "canary": {"enabled": True, "percent": 50, "new_style": "detailed"}}
        first = question.choose_style("trace-xyz", flags)
        for _ in range(20):
            self.assertEqual(question.choose_style("trace-xyz", flags), first)

    def test_env_var_overrides_flag_file(self):
        os.environ["ANSWER_STYLE"] = "detailed"
        flags = {"answer_style": "short",
                 "canary": {"enabled": False, "percent": 10, "new_style": "detailed"}}
        self.assertEqual(question.choose_style("abc123", flags), "detailed")

    def test_missing_canary_block_is_safe(self):
        self.assertEqual(question.choose_style("abc123", {}), "short")


if __name__ == "__main__":
    unittest.main()
