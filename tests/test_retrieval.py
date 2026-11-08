"""Unit tests for retrieval ranking (retrieval service)."""

import unittest

from helpers import load_service

retrieval = load_service("retrieval_main", "retrieval-service")


class TestRetrieve(unittest.TestCase):
    def test_vendor_question_returns_vendor_doc(self):
        snippets = retrieval.retrieve("When should vendor invoices be paid?")
        self.assertTrue(snippets)
        self.assertEqual(snippets[0]["doc"], "vendor-payment-policy.md")

    def test_close_question_returns_close_doc(self):
        snippets = retrieval.retrieve("What are the steps to close the month?")
        self.assertTrue(snippets)
        self.assertEqual(snippets[0]["doc"], "month-end-close-checklist.md")

    def test_reporting_question_returns_reporting_doc(self):
        snippets = retrieval.retrieve("What should a monthly report include?")
        self.assertTrue(snippets)
        self.assertEqual(snippets[0]["doc"], "reporting-guide.md")

    def test_unrelated_question_returns_nothing(self):
        self.assertEqual(retrieval.retrieve("What is the capital of France?"), [])

    def test_top_n_limit_is_respected(self):
        snippets = retrieval.retrieve("vendor invoices payments report", top_n=1)
        self.assertEqual(len(snippets), 1)

    def test_snippets_carry_doc_and_text(self):
        snippets = retrieval.retrieve("travel expenses receipts")
        self.assertTrue(snippets)
        for snippet in snippets:
            self.assertIn("doc", snippet)
            self.assertIn("text", snippet)
            self.assertTrue(snippet["text"])


if __name__ == "__main__":
    unittest.main()
