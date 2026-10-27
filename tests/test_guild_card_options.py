from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_card_presentation as fixture


class GuildCardOptionsTests(unittest.TestCase):
    setUp = fixture.CardCommandTests.setUp

    def test_guild_inventory_forwards_live_fusion_and_summary_filter(self):
        with patch("atcommander.account_registry.AccountRegistry") as registry:
            registry.return_value.list.return_value = []
            answer = self.service.execute("guild-inventory", {"member": "Current account", "fuse_level": 1, "display": "summary"})
        card = answer.rows[0]["data"][0]
        self.assertEqual(card["fusion"], "Dual")
        self.assertEqual((card["attack"], card["health"]), ("11", "27"))
        self.assertNotIn("unit_index", card)
        self.assertEqual(self.service.execute("user-inventory", {"fuse_level": 0}).rows, [])

    def test_guild_inventory_preserves_generated_artifact_for_result_panel(self):
        with patch("atcommander.account_registry.AccountRegistry") as registry, \
             patch("atcommander.utility_commands.APP_DIR", Path(self.temp.name)):
            registry.return_value.list.return_value = []
            answer = self.service.execute("guild-inventory", {"image_type": "svg", "display": "summary"})
        artifact = answer.data["artifacts"][0]
        self.assertEqual(artifact["alias"], "Current account")
        self.assertTrue(Path(artifact["path"]).is_file())
        self.assertEqual([name for name, _ in self.client.calls], ["init"])
