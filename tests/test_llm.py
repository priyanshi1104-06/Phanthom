"""The model layer: the real Anthropic SDK path against a local mock server, and the scripted provider."""
import os
import unittest
from unittest import mock

import anthropic

from runner import llm
from tests.mock_anthropic import MockAnthropic, error, message

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


class AnthropicProviderTest(unittest.TestCase):
    def setUp(self):
        self.reply = lambda body: (200, message(body, {"ok": True}))
        self.server = MockAnthropic(lambda body: self.reply(body))
        client = anthropic.Anthropic(base_url=self.server.url, api_key="test-key")
        llm.set_provider(llm.AnthropicProvider(client))

    def tearDown(self):
        self.server.close()
        llm.set_provider(None)

    def test_sends_a_structured_request_and_reads_the_json_after_thinking(self):
        self.assertEqual(llm.ask("specter", "system text", "the prompt", SCHEMA), {"ok": True})
        body = self.server.requests[0]["body"]
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertEqual(body["system"], "system text")
        self.assertEqual(body["messages"], [{"role": "user", "content": "the prompt"}])
        self.assertEqual(body["output_config"], {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
        self.assertNotIn("temperature", body)  # rejected by current models
        self.assertEqual(self.server.requests[0]["headers"]["x-api-key"], "test-key")

    def test_layers_use_their_own_models(self):
        llm.ask("oracle", "s", "p", SCHEMA)
        self.assertEqual(self.server.requests[0]["body"]["model"], "claude-fable-5-1")
        overrides = {"PHANTOM_ANALYST_MODEL": "claude-opus-5-5", "PHANTOM_ANALYST_EFFORT": "high"}
        with mock.patch.dict(os.environ, overrides):
            llm.ask("analyst", "s", "p", SCHEMA)
        self.assertEqual(self.server.requests[1]["body"]["model"], "claude-opus-5-5")
        self.assertEqual(self.server.requests[1]["body"]["output_config"]["effort"], "high")

    def test_refusal_and_cut_off_answers_are_no_answer(self):
        for stop in ("refusal", "max_tokens"):
            self.reply = lambda body, stop=stop: (200, message(body, {"ok": True}, stop_reason=stop))
            self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
        self.reply = lambda body: (200, message(body, text="not json"))
        self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))

    def test_server_errors_are_transient(self):
        self.reply = lambda body: error(529, "overloaded_error", "busy")
        self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
        self.reply = lambda body: (200, message(body, {"ok": True}))
        self.assertEqual(llm.ask("specter", "s", "p", SCHEMA), {"ok": True})  # still asking

    def test_bad_credentials_switch_models_off(self):
        self.reply = lambda body: error(401, "authentication_error", "invalid x-api-key")
        self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
        status = llm.status()
        self.assertFalse(status["available"])
        self.assertIn("ANTHROPIC_API_KEY", status["detail"])
        calls = len(self.server.requests)
        self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
        self.assertEqual(len(self.server.requests), calls)  # stopped asking

    def test_no_credentials_at_all(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            client = anthropic.Anthropic(base_url=self.server.url)
            llm.set_provider(llm.AnthropicProvider(client))
            self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
            self.assertIn("ANTHROPIC_API_KEY", llm.status()["detail"])
        self.assertEqual(self.server.requests, [])


class NoKeyTest(unittest.TestCase):
    def tearDown(self):
        llm.set_provider(None)

    def test_without_a_key_models_are_unavailable_from_the_start(self):
        with mock.patch.dict(os.environ, {"PHANTOM_MODEL_PROVIDER": "anthropic"}, clear=True):
            llm.set_provider(None)
            status = llm.status()
        self.assertFalse(status["available"])
        self.assertIn("no AI key", status["detail"])


class ScriptedAndSettingsTest(unittest.TestCase):
    def tearDown(self):
        llm.set_provider(None)

    def test_scripted_answers_match_prompt_and_layer(self):
        scripted = llm.ScriptedProvider([
            {"match": "def average", "layer": "specter", "answer": {"ok": False}},
            {"match": "def average", "answer": {"ok": True}},
        ])
        llm.set_provider(scripted)
        self.assertEqual(llm.ask("specter", "s", "x\ndef average(v): ...", SCHEMA), {"ok": False})
        self.assertEqual(llm.ask("analyst", "s", "def average(v): ...", SCHEMA), {"ok": True})
        self.assertIsNone(llm.ask("specter", "s", "def other(): ...", SCHEMA))
        self.assertEqual(len(scripted.calls), 3)

    def test_provider_none(self):
        with mock.patch.dict(os.environ, {"PHANTOM_MODEL_PROVIDER": "none"}):
            llm.set_provider(None)
            self.assertIsNone(llm.ask("specter", "s", "p", SCHEMA))
            self.assertFalse(llm.status()["available"])

    def test_load_env_never_overrides(self):
        path = os.path.join(os.path.dirname(__file__), "_test.env")
        with open(path, "w") as f:
            f.write("# comment\nPHANTOM_TEST_A=from-file\nPHANTOM_TEST_B = 'quoted'\nnot a pair\n")
        try:
            with mock.patch.dict(os.environ, {"PHANTOM_TEST_A": "already-set"}):
                llm.load_env(path)
                self.assertEqual(os.environ["PHANTOM_TEST_A"], "already-set")
                self.assertEqual(os.environ["PHANTOM_TEST_B"], "quoted")
        finally:
            os.remove(path)
            os.environ.pop("PHANTOM_TEST_B", None)


if __name__ == "__main__":
    unittest.main()
