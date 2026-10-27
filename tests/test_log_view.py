import tkinter as tk
import unittest
from atcommander.log_view import split_controls_log, logbox, open_log_window


class LogViewTests(unittest.TestCase):
    def setUp(self):
        try: self.root = tk.Tk()
        except tk.TclError: self.skipTest('Tk unavailable')
        self.root.geometry('900x400'); self.addCleanup(self.root.destroy)
    def test_oversized_controls_cannot_hide_the_log_and_divider_moves(self):
        panes, controls, bottom = split_controls_log(self.root)
        tk.Frame(controls, width=800, height=2000).pack()
        text = logbox(bottom); self.root.update()
        self.assertGreaterEqual(bottom.winfo_height(), 105)
        self.assertGreater(text.winfo_height(), 25)
        panes.sash_place(0, 0, 150); self.root.update()
        self.assertGreater(bottom.winfo_height(), 170)
    def test_larger_log_is_resizable_live_and_reuses_existing_window(self):
        source = logbox(self.root); source.configure(state='normal'); source.insert('end', 'Visible latest line\n'); source.configure(state='disabled')
        window = open_log_window(source); self.root.update()
        self.assertIn('Visible latest line', window._live_text.get('1.0', 'end'))
        self.assertIs(open_log_window(source), window)
        self.assertEqual(tuple(map(int, window.resizable())), (1, 1))
        window.tk.call(window.protocol('WM_DELETE_WINDOW'))
        self.assertIsNone(source._log_window)


if __name__ == '__main__': unittest.main()
