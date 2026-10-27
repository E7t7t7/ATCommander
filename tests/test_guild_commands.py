import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_utility_commands as fixture
from atcommander.local_commands import CommandInputError

class GuildCommandTests(unittest.TestCase):
    setUp=fixture.UtilityTests.setUp
    def roster(self):
        self.client.data['faction']={'members':[{'name':'Rogu','user_id':7,'role':1}]}
    def test_configuration_preserves_omitted_fields_and_scopes_guild(self):
        self.service.execute('guild-configure',{'name':'First','defense_plan':'Keep'})
        self.service.execute('guild-configure',{'report_format':'csv'})
        saved=self.store.read()['guild_preferences_by_id']['9']
        self.assertEqual((saved['name'],saved['defense_plan'],saved['report_format']),('First','Keep','csv'))
        self.client.data['user_data']['faction_id']=10
        self.service.execute('guild-configure',{'name':'Second'})
        self.assertEqual(self.store.read()['guild_preferences_by_id']['9']['name'],'First')
    def test_bad_assignment_never_writes_preferences_or_changes_game(self):
        self.roster()
        with self.assertRaises(CommandInputError):
            self.service.execute('guild-configure',{'assignments':json.dumps([{'member':'Unknown','island':1}])})
        self.assertNotIn('guild_preferences_by_id',self.store.read())
        self.assertEqual([name for name,_ in self.client.calls],['init'])
    def test_report_exports_only_public_fields_and_no_game_mutations(self):
        self.roster();self.client.data['faction']['members'][0]['password']='test-password-private'
        with patch('atcommander.config.APP_DIR',Path(self.temp.name)):
            result=self.service.execute('guild-report',{'format':'json'})
        output=Path(result.data['artifacts'][0]['path']).read_text()
        self.assertNotIn('password',output);self.assertNotIn('user_id',output)
        self.assertIn('Rogu',output)
        self.assertEqual([name for name,_ in self.client.calls],['init'])
    def test_import_previews_before_explicit_local_save(self):
        self.roster();path=Path(self.temp.name)/'plan.csv'
        path.write_text('guild_id,member,island\n9,Rogu,2\n')
        result=self.service.execute('guild-defense-plan',{'mode':'import','path':str(path)})
        self.assertFalse(result.data['applied']);self.assertNotIn('guild_preferences_by_id',self.store.read())
        result=self.service.execute('guild-defense-plan',{'mode':'import','path':str(path),'apply':True})
        self.assertTrue(result.data['applied'])
        self.assertEqual(self.store.read()['guild_preferences_by_id']['9']['assignments'],[{'member':'Rogu','island':2}])
        self.assertEqual([name for name,_ in self.client.calls],['init','init'])

if __name__=='__main__':unittest.main()
