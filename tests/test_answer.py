"""Unit tests for answer drafting and the language-model interface."""

import unittest

from helpers import load_service

answer = load_service("answer_main", "answer-service")


def _snippets():
    return [
        {"doc": "vendor-payment-policy.md",
         "text": "Vendor invoices are paid within 30 days of receipt. "
                 "Approval is needed for payments over $1,000."},
        {"doc": "month-end-close-checklist.md",
         "text": "Reconcile the ledger against the bank statement first."},
    ]


class TestDraftAnswer(unittest.TestCase):
    def test_short_answer_uses_first_sentence_and_sources(self):
        text = answer.draft_answer("When are invoices paid?", _snippets())
        self.assertIn("Vendor invoices are paid within 30 days of receipt.", text)
        self.assertIn("vendor-payment-policy.md", text)

    def test_detailed_answer_has_more_detail_than_short(self):
        short = answer.draft_answer("When are invoices paid?", _snippets(), style="short")
        detailed = answer.draft_answer(
            "When are invoices paid?", _snippets(), style="detailed")
        self.assertGreater(len(detailed), len(short))
        self.assertIn("Approval is needed", detailed)

    def test_no_snippets_says_so_plainly(self):
        text = answer.draft_answer("What is the capital of France?", [])
        self.assertIn("do not have information", text)

    def test_answer_never_adds_facts_not_in_snippets(self):
        text = answer.draft_answer("When are invoices paid?", _snippets())
        self.assertNotIn("France", text)
        self.assertNotIn("90 days", text)

    def test_unknown_style_falls_back_to_short(self):
        # draft_answer itself does not validate; the handler does.
        # The short template is the default path, so an unknown style
        # passed here still produces a short, sourced answer.
        text = answer.draft_answer("When are invoices paid?", _snippets(), style="weird")
        self.assertIn("vendor-payment-policy.md", text)


class TestLLMInterface(unittest.TestCase):
    def test_mock_client_drafts_from_snippets(self):
        text = answer.LLM.draft("When are invoices paid?", _snippets(), style="short")
        self.assertIn("30 days", text)

    def test_real_client_stub_refuses_without_configuration(self):
        with self.assertRaises(RuntimeError):
            answer.RealLLMClient().draft("When are invoices paid?", _snippets())

    def test_prompt_templates_exist_for_both_styles(self):
        self.assertIn("short", answer.PROMPT_TEMPLATES)
        self.assertIn("detailed", answer.PROMPT_TEMPLATES)
        self.assertIn("only the snippets", answer.PROMPT_TEMPLATES["short"])


if __name__ == "__main__":
    unittest.main()
