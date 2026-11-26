import os
import unittest
from unittest.mock import patch

from finbotagents.default_config import build_default_config


class BrandCompatibilityTests(unittest.TestCase):
    def test_finbotagents_is_the_python_package(self):
        import finbotagents
        from finbotagents.graph import FinBotAgentsGraph
        self.assertTrue(finbotagents.__version__)
        self.assertEqual(FinBotAgentsGraph.__name__, "FinBotAgentsGraph")

    def test_new_graph_name_keeps_legacy_alias(self):
        from finbotagents.graph.trading_graph import FinBotAgentsGraph, TradingAgentsGraph
        self.assertIs(FinBotAgentsGraph, TradingAgentsGraph)

    def test_new_environment_value_takes_precedence(self):
        with patch.dict(os.environ, {"TRADINGAGENTS_LLM_PROVIDER": "old", "FINBOTAGENTS_LLM_PROVIDER": "new"}):
            self.assertEqual(build_default_config()["llm_provider"], "new")

    def test_legacy_environment_value_still_works(self):
        with patch.dict(os.environ, {"TRADINGAGENTS_LLM_PROVIDER": "old"}, clear=True):
            self.assertEqual(build_default_config()["llm_provider"], "old")

    def test_new_path_override_takes_precedence(self):
        with patch.dict(os.environ, {"TRADINGAGENTS_RESULTS_DIR": "/old", "FINBOTAGENTS_RESULTS_DIR": "/new"}):
            self.assertEqual(build_default_config()["results_dir"], "/new")
