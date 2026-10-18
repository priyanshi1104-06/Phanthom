"""Specter's triage: risk levels from the words in each function, and what counts as an edit."""
import unittest

from runner import languages
from runner.triage import raise_level, rate_function, triage, words_of

PY = languages.get("python")


def rate(source, path=""):
    return rate_function(PY.functions(source)[0], path)


class WordsTest(unittest.TestCase):
    def test_identifiers_are_split(self):
        self.assertEqual(words_of("refundAmount user_id HTTPServer x2"),
                         {"refund", "amount", "user", "id", "httpserver", "x2"})


class LevelsTest(unittest.TestCase):
    def test_plain_code_is_low(self):
        self.assertEqual(rate("def add(a, b):\n    return a + b\n")["level"], "low")

    def test_payments_need_two_words(self):
        self.assertEqual(rate("def f(price):\n    return price\n")["level"], "low")
        result = rate("def refund(order, amount):\n    order.balance += amount\n")
        self.assertEqual(result["level"], "high")
        self.assertEqual(result["reasons"][0]["category"], "payments")

    def test_auth_and_concurrency_are_high_and_both_are_critical(self):
        self.assertEqual(rate("def check(password):\n    return password == 'x'\n")["level"], "high")
        self.assertEqual(rate("def run(lock):\n    with lock:\n        pass\n")["level"], "high")
        self.assertEqual(rate("def f(token, lock):\n    with lock:\n        return token\n")["level"], "critical")

    def test_database_is_medium_and_migrations_are_critical(self):
        self.assertEqual(rate("def f(db):\n    db.execute('select 1')\n")["level"], "medium")
        self.assertEqual(rate("def up(c):\n    c.execute('ALTER TABLE users ADD x int')\n")["level"], "critical")
        self.assertEqual(rate("def upgrade():\n    migrate()\n", "app/migrations/0002.py")["level"], "critical")

    def test_go_statements_are_concurrency(self):
        go = languages.get("go")
        if go is None:
            self.skipTest("no Go plugin")
        fn = go.functions("package x\n\nfunc f() {\n\tgo work()\n}\n")[0]
        self.assertEqual(rate_function(fn)["level"], "high")

    def test_the_model_raises_by_one_step_at_most_and_never_lowers(self):
        self.assertEqual(raise_level("low", "critical"), "medium")
        self.assertEqual(raise_level("high", "low"), "high")
        self.assertEqual(raise_level("high", "nonsense"), "high")
        self.assertEqual(raise_level("critical", "critical"), "critical")


class EditTest(unittest.TestCase):
    SOURCE = "def add(a, b):\n    return a + b\n\ndef pay(amount, balance):\n    return balance - amount\n"

    def test_first_look_never_escalates(self):
        result = triage(PY.functions(self.SOURCE))
        self.assertEqual((result["level"], result["changed"], result["escalate"]), ("high", [], None))

    def test_only_changed_functions_count(self):
        functions = PY.functions(self.SOURCE)
        previous = {f.name: f.text for f in functions}
        edited = PY.functions(self.SOURCE.replace("a + b", "a - b"))
        result = triage(edited, previous=previous)
        self.assertEqual((result["changed"], result["edit_level"], result["escalate"]), (["add"], "low", None))
        edited = PY.functions(self.SOURCE.replace("balance - amount", "balance - amount * 2"))
        result = triage(edited, previous=previous)
        self.assertEqual((result["changed"], result["edit_level"], result["escalate"]), (["pay"], "high", "oracle"))
        self.assertEqual(result["reasons"][0]["function"], "pay")


if __name__ == "__main__":
    unittest.main()
