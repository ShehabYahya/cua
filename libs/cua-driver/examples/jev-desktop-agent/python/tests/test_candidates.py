from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from candidates import build_candidates
from contracts import Element, Observation, Rect, VisualRegion
from writer import PreparedText


class CandidateTest(unittest.TestCase):
    def observation(self, *, capture_id=None, visual=()):
        return Observation(
            snapshot_id="s1",
            pid=7,
            window_id=9,
            app="Firefox",
            window_title="Example",
            elements=(
                Element(1, "s1:1", "push button", "New Tab", actions=("click",)),
                Element(2, "s1:2", "entry", "Address and Search"),
                Element(3, "s1:3", "label", "Decorative"),
            ),
            visual_regions=visual,
            capture_id=capture_id,
            screenshot_width=1000,
            screenshot_height=800,
        )

    def test_atspi_push_button_is_clickable(self):
        candidates = build_candidates("click the New Tab button", self.observation())
        self.assertIn("click-1", {candidate.id for candidate in candidates})

    def test_new_tab_hotkey_is_also_available(self):
        ids = {c.id for c in build_candidates("open a new tab", self.observation())}
        self.assertIn("hotkey-new-tab", ids)

    def test_prepared_text_is_local_argument_only(self):
        candidates = build_candidates(
            "search for the person",
            self.observation(),
            prepared_texts=(PreparedText("text-1", "Alan Turing"),),
        )
        typed = next(c for c in candidates if c.id.startswith("type-2"))
        self.assertEqual(typed.arguments["text"], "Alan Turing")
        self.assertNotIn("Alan Turing", typed.description)
        focused = next(c for c in candidates if c.id == "type-focused-text-1")
        self.assertEqual(focused.arguments["text"], "Alan Turing")
        self.assertEqual(focused.source, "keyboard")

    def test_capture_bound_visual_candidate(self):
        visual = (
            VisualRegion("v1", "Icon only action", "button", Rect(100, 200, 40, 20), 0.9),
        )
        candidates = build_candidates(
            "activate icon only action",
            self.observation(capture_id="c1", visual=visual),
            allow_visual_clicks=True,
        )
        candidate = next(c for c in candidates if c.id == "visual-v1")
        self.assertEqual(candidate.capture_id, "c1")
        self.assertEqual(candidate.arguments["x"], 120.0)
        self.assertEqual(candidate.arguments["y"], 210.0)


    def test_browser_semantic_ref_uses_typed_browser_tool(self):
        observation = Observation(
            "s1",
            7,
            9,
            "Chrome",
            "Example",
            (
                Element(
                    100001,
                    None,
                    "button",
                    "Search",
                    actions=("click",),
                    source="browser",
                    browser_ref="p1:7",
                ),
            ),
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )
        candidate = next(
            c
            for c in build_candidates("click Search", observation)
            if c.id.startswith("browser-click-")
        )
        self.assertEqual(candidate.tool, "browser_click")
        self.assertEqual(candidate.arguments["ref"], "p1:7")
        self.assertEqual(candidate.arguments["input_route"], "dom_event")

    def test_browser_download_uses_approved_root_and_confirmation(self):
        observation = Observation(
            "s1",
            7,
            9,
            "Chrome",
            "Report",
            (
                Element(
                    100001,
                    None,
                    "link",
                    "Download PDF",
                    actions=("click",),
                    source="browser",
                    browser_ref="p1:4",
                ),
            ),
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )
        candidate = next(
            item
            for item in build_candidates(
                "download the PDF",
                observation,
                download_root="/home/test/Downloads",
            )
            if item.id.startswith("browser-download-")
        )
        self.assertEqual(candidate.tool, "browser_download")
        self.assertEqual(
            candidate.arguments["destination_root"],
            "/home/test/Downloads",
        )
        self.assertEqual(candidate.risk, "confirm")

    def test_browser_pointer_right_click_uses_typed_route(self):
        observation = Observation(
            "s1",
            7,
            9,
            "Chrome",
            "Example",
            (
                Element(
                    100001,
                    None,
                    "button",
                    "Card menu",
                    actions=("pointer",),
                    source="browser",
                    browser_ref="p1:9",
                ),
            ),
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )
        candidate = next(
            c
            for c in build_candidates(
                "right click the Card menu",
                observation,
            )
            if c.id.startswith("browser-right_click-")
        )
        self.assertEqual(candidate.tool, "browser_pointer")
        self.assertEqual(candidate.arguments["action"], "right_click")
        self.assertEqual(candidate.arguments["ref"], "p1:9")

    def test_browser_drag_builds_source_destination_pair(self):
        observation = Observation(
            "s1",
            7,
            9,
            "Chrome",
            "Board",
            (
                Element(
                    100001,
                    None,
                    "item",
                    "Report.pdf",
                    actions=("pointer",),
                    source="browser",
                    browser_ref="p1:1",
                ),
                Element(
                    100002,
                    None,
                    "region",
                    "Archive",
                    actions=("pointer",),
                    source="browser",
                    browser_ref="p1:2",
                ),
            ),
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )
        candidate = next(
            c
            for c in build_candidates(
                "drag Report.pdf to Archive",
                observation,
            )
            if c.id == "browser-drag-100001-100002"
        )
        self.assertEqual(candidate.arguments["action"], "drag")
        self.assertEqual(candidate.arguments["ref"], "p1:1")
        self.assertEqual(candidate.arguments["destination_ref"], "p1:2")

    def test_browser_upload_uses_recent_file_without_exposing_full_path(self):
        observation = Observation(
            "s1",
            7,
            9,
            "Chrome",
            "Compose",
            (
                Element(
                    100001,
                    None,
                    "button",
                    "Attach file",
                    actions=("upload",),
                    source="browser",
                    browser_ref="p1:12",
                ),
            ),
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )
        candidate = next(
            item
            for item in build_candidates(
                "attach the PDF",
                observation,
                recent_files=("/home/test/Downloads/report.pdf",),
            )
            if item.id.startswith("browser-upload-")
        )
        self.assertEqual(candidate.tool, "browser_set_input_files")
        self.assertEqual(
            candidate.arguments["files"],
            ("/home/test/Downloads/report.pdf",),
        )
        self.assertIn("report.pdf", candidate.description)
        self.assertNotIn("/home/test/Downloads", candidate.description)
        self.assertEqual(candidate.risk, "confirm")

    def test_password_field_is_filtered_when_policy_is_enabled(self):
        observation = Observation(
            "s1", 7, 9, "App", "Login",
            (Element(1, "s1:1", "entry", "Password"),),
        )
        candidates = build_candidates(
            "type the password",
            observation,
            prepared_texts=(PreparedText("text-1", "secret"),),
            enforce_policy=True,
        )
        self.assertFalse(
            any(
                c.id.startswith("type-")
                or c.id.startswith("bundle-fill-submit-")
                for c in candidates
            )
        )

    def test_password_field_policy_is_opt_in(self):
        observation = Observation(
            "s1", 7, 9, "App", "Login",
            (Element(1, "s1:1", "entry", "Password"),),
        )
        candidates = build_candidates(
            "type the password",
            observation,
            prepared_texts=(PreparedText("text-1", "secret"),),
            enforce_policy=False,
        )
        self.assertTrue(any(c.id.startswith("type-") for c in candidates))


if __name__ == "__main__":
    unittest.main()
