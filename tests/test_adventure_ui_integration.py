import unittest
from atcommander.app import ATCommanderApp

class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value

class AdventureSelectionTests(unittest.TestCase):
    def app(self,label):
        app=object.__new__(ATCommanderApp)
        app.adventure_kind=Value(label)
        app.adventure_mission_combo={}
        app.adventure_mission_choice=Value("Stage 1")
        app.adventure_mission_id=Value("107")
        app.adventure_campaign_rows=[
            {"id":"9037","name":"Throwdown Celebration","missions":[{"id":"9249","display":"Stage 1"}]},
            {"id":"9040","name":"Throwdown Celebration","missions":[{"id":"9258","display":"Stage 1"},{"id":"9259","display":"Stage 2"}]}]
        return app

    def test_selected_anniversary_chapter_controls_actual_mission(self):
        app=self.app("Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9040]")
        app._refresh_adventure_campaign_missions()
        self.assertEqual("9258",app.adventure_mission_id.get())
        self.assertEqual(["Stage 1","Stage 2"],app.adventure_mission_combo["values"])
        app.adventure_mission_choice.set("Stage 2")
        app._adventure_mission_changed()
        self.assertEqual("9259",app.adventure_mission_id.get())

    def test_main_mission_field_is_preserved_by_event_selector_refresh(self):
        app=self.app("Main Adventure")
        app._refresh_adventure_campaign_missions()
        self.assertEqual("107",app.adventure_mission_id.get())
        self.assertEqual(["Main uses Island / Stage controls"],app.adventure_mission_combo["values"])

    def test_active_label_change_preserves_chapter_key(self):
        old="Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9040]"
        labels=["Main Adventure","Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9037]",
                "Seasonal / Event: Throwdown Celebration [Campaign 9040]"]
        self.assertEqual(labels[2],ATCommanderApp._adventure_label_after_refresh(old,labels))

    def test_inactive_to_active_label_change_preserves_chapter_key(self):
        old="Seasonal / Event: Throwdown Celebration [Campaign 9040]"
        labels=["Main Adventure","Seasonal / Event: Throwdown Celebration [Campaign 9037]",
                "Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9040]"]
        self.assertEqual(labels[2],ATCommanderApp._adventure_label_after_refresh(old,labels))

    def test_missing_key_cannot_fall_back_to_ambiguous_same_name_chapter(self):
        old="Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9040]"
        labels=["Main Adventure","Seasonal / Event: Throwdown Celebration [Campaign 9037]",
                "Seasonal / Event: Throwdown Celebration [Campaign 9038]"]
        self.assertEqual("Main Adventure",ATCommanderApp._adventure_label_after_refresh(old,labels))

    def test_live_marker_key_transitions_to_real_catalog_campaign(self):
        old="Live Event: Anniversary • stage mapping needed [Key live:anniversary]"
        labels=["Main Adventure","Seasonal / Event: Throwdown Celebration • ACTIVE [Campaign 9040]"]
        previous_row={"id":"live:anniversary","name":"Anniversary","source":"player_prefs_live_marker","marker":"9040"}
        rows=[{"id":"9040","name":"Throwdown Celebration","source":"static","missions":[{"id":"9258"}]}]
        self.assertEqual(labels[1],ATCommanderApp._adventure_label_after_refresh(old,labels,previous_row,rows))

if __name__=="__main__":unittest.main()
