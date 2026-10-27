"""Offline checks for the tracked GUI ad worker and orderly browser shutdown."""
from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander.app import ATCommanderApp
from atcommander import app as app_module, embedded_browser


def application():
    return SimpleNamespace(
        _closing=False,ad_thread=None,ad_stop=threading.Event(),events=Queue(),
        command_busy=False,research_busy=False,arena_runner=None,event_runner=None,
        adventure_runner=None,swole_runner=None)


class AdWorkerLifecycleTests(unittest.TestCase):
    def test_watch_worker_is_tracked_and_observes_stop_all(self):
        app=application();thread=Mock();thread.is_alive.return_value=True
        with patch.object(app_module.threading,'Thread',return_value=thread) as create, \
             patch.object(app_module,'attempt_legitimate_ad_ui',return_value={'ok':False}) as play:
            ATCommanderApp.watch_ads(app)
            self.assertIs(app.ad_thread,thread);thread.start.assert_called_once()
            work=create.call_args.kwargs['target']
            work()
            stop=play.call_args.kwargs['should_stop']
            self.assertFalse(stop())
            ATCommanderApp.stop_all(app)
            self.assertTrue(stop())

    def test_duplicate_or_overlapping_watch_does_not_launch_another_worker(self):
        cases=[application() for _ in range(4)]
        cases[0].ad_thread=Mock();cases[0].ad_thread.is_alive.return_value=True
        cases[1].command_busy=True;cases[2].research_busy=True
        cases[3].arena_runner=SimpleNamespace(running=True)
        for app in cases:
            with self.subTest(app=app),patch.object(app_module.threading,'Thread') as create, \
                 patch.object(app_module.messagebox,'showinfo'):
                ATCommanderApp.watch_ads(app)
            create.assert_not_called()

    def test_close_stops_and_waits_for_ad_worker_before_focus_browser_and_gui(self):
        app=application();events=[];pending=[];thread=Mock();thread.is_alive.return_value=True
        app.ad_thread=thread;app.research_thread=None;app.game_panel_thread=None
        app.root=SimpleNamespace(title=Mock(),after=lambda delay,callback:pending.append((delay,callback)),
                                 destroy=lambda:events.append('destroy'))
        app.learning_browser=Mock()
        app.stop_all=lambda:(ATCommanderApp.stop_all(app),events.append('stop'))
        def close():events.append('browser');return {'embedded':False,'reason':'browser_closed'}
        with patch.object(embedded_browser,'close_owned_browser',side_effect=close) as close_browser, \
             patch.object(embedded_browser,'configure',side_effect=lambda _:events.append('unregister')), \
             patch('atcommander.ad_browser_focus.release_all_game_input_focus',side_effect=lambda:events.append('focus')):
            ATCommanderApp.close_app(app)
            self.assertTrue(app.ad_stop.is_set());self.assertEqual(events,['stop'])
            close_browser.assert_not_called();self.assertEqual(pending[0][0],100)
            thread.is_alive.return_value=False
            pending.pop(0)[1]()
        self.assertEqual(events,['stop','focus','browser','unregister','destroy'])
        app.learning_browser.close.assert_not_called()


if __name__=='__main__':unittest.main()
