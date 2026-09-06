import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.llm_provider_settings import provider_model_update


class ProviderSettingsTests(unittest.TestCase):
    def apply(self, services, updates):
        services.update(provider_model_update(services, updates, "local/default-GGUF"))

    def test_round_trip_keeps_both_model_choices_and_enhancer(self):
        services = {"llm_provider": "local", "llm_model_id": "local/custom-GGUF",
                    "enhance_llm_model_id": "local/enhancer-GGUF"}
        self.apply(services, {"llm_provider": "minimax_subscription"})
        self.assertEqual(services["llm_model_id"], "MiniMax-M3")
        self.apply(services, {"llm_model_id": "MiniMax-M-custom"})
        self.apply(services, {"llm_provider": "local"})
        self.assertEqual(services["llm_model_id"], "local/custom-GGUF")
        self.assertEqual(services["enhance_llm_model_id"], "local/enhancer-GGUF")
        self.apply(services, {"llm_provider": "minimax_subscription"})
        self.assertEqual(services["llm_model_id"], "MiniMax-M-custom")

    def test_broken_existing_local_config_recovers(self):
        services = {"llm_provider": "local", "llm_model_id": "MiniMax-M3"}
        self.apply(services, {"llm_provider": "local"})
        self.assertEqual(services["llm_model_id"], "local/default-GGUF")

    def test_explicit_new_model_wins(self):
        services = {"llm_provider": "minimax", "llm_model_id": "MiniMax-M3"}
        self.apply(services, {"llm_provider": "local", "llm_model_id": "my/model-GGUF"})
        self.assertEqual(services["llm_model_id"], "my/model-GGUF")


if __name__ == "__main__":
    unittest.main()
