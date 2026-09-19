from __future__ import annotations

import asyncio
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import (
    Decision,
    DesktopOverview,
    Element,
    Observation,
    Plan,
    PlanStep,
    Verification,
)
from loop import AgentLoop


class CompoundPlanner:
    async def plan(self, goal, **kwargs):
        return Plan(
            goal,
            (
                PlanStep(
                    "download report.pdf",
                    app="Chrome",
                    completion="report.pdf exists locally",
                ),
                PlanStep(
                    "rename report.pdf to project.pdf",
                    app="Files",
                    text="project.pdf",
                    completion="project.pdf exists locally",
                ),
                PlanStep(
                    "attach project.pdf",
                    app="Chrome",
                    completion="project.pdf is attached",
                ),
                PlanStep(
                    "send the email",
                    app="Chrome",
                    completion="message is sent",
                ),
            ),
        )

    async def repair_step(self, **kwargs):
        return kwargs["current"]


class CompoundDriver:
    capture_bound_click = False

    def __init__(self, root):
        self.root = Path(root)
        self.selected = False
        self.renaming = False
        self.typed = False
        self.downloaded = False
        self.renamed = False
        self.attached = False
        self.sent = False
        self.counter = 0

    async def desktop_overview(self, *, include_screenshot=True, include_apps=True):
        return DesktopOverview((), ())

    async def has_window(self, app):
        return True

    async def ensure_app(self, app):
        return None

    def with_foreground(self, candidate):
        return candidate

    async def observe(self, app=None, *, include_screenshot=True):
        self.counter += 1
        snapshot = f"s{self.counter}"
        if app == "Files":
            if self.renaming:
                elements = (
                    Element(
                        2,
                        f"{snapshot}:2",
                        "entry",
                        "Name",
                        value="project.pdf" if self.typed else "report.pdf",
                    ),
                )
            else:
                name = "project.pdf" if self.renamed else "report.pdf"
                elements = (
                    Element(
                        1,
                        f"{snapshot}:1",
                        "list item",
                        name,
                        selected=self.selected,
                        actions=("click",),
                    ),
                )
            return Observation(
                snapshot,
                8,
                10,
                "Files",
                "Downloads",
                elements,
            )

        if not self.downloaded:
            elements = (
                Element(
                    100001,
                    None,
                    "link",
                    "Download report.pdf",
                    actions=("click",),
                    source="browser",
                    browser_ref="p1:1",
                ),
            )
        elif not self.attached:
            elements = (
                Element(
                    100002,
                    None,
                    "button",
                    "Attach file",
                    actions=("upload",),
                    source="browser",
                    browser_ref="p2:1",
                ),
            )
        else:
            elements = (
                Element(
                    100003,
                    None,
                    "button",
                    "Send",
                    actions=("click",),
                    source="browser",
                    browser_ref="p3:1",
                ),
            )
        return Observation(
            snapshot,
            7,
            9,
            "Chrome",
            "Mail",
            elements,
            browser_target_id="bt-1",
            browser_tab_id="tab-1",
        )

    async def execute(self, candidate):
        if candidate.id.startswith("browser-download-"):
            (self.root / "report.pdf").write_bytes(b"pdf")
            self.downloaded = True
        elif candidate.id == "click-1":
            self.selected = True
        elif candidate.id == "press-rename":
            self.renaming = True
        elif candidate.id.startswith("type-2-"):
            self.typed = True
        elif candidate.id == "press-enter":
            old = self.root / "report.pdf"
            new = self.root / "project.pdf"
            old.rename(new)
            self.renaming = False
            self.renamed = True
        elif candidate.id.startswith("browser-upload-"):
            files = tuple(candidate.arguments["files"])
            self.assert_upload(files)
            self.attached = True
        elif candidate.id.startswith("browser-click-"):
            self.sent = True
        return {"effect": "confirmed"}

    def assert_upload(self, files):
        if files != (str((self.root / "project.pdf").resolve()),):
            raise AssertionError(f"unexpected upload files: {files}")


class CompoundChooser:
    def __init__(self):
        self.sequence = [
            "browser-download-100001",
            "click-1",
            "press-rename",
            "type-2-text-1",
            "press-enter",
            "browser-upload-100002-1",
            "browser-click-100003",
        ]

    async def choose(self, *, candidates, **kwargs):
        if not self.sequence:
            return Decision("done", 0.99, {"done": 0.99})
        selected = self.sequence.pop(0)
        ids = {candidate.id for candidate in candidates}
        if selected not in ids:
            raise AssertionError(
                f"expected {selected}, got candidates {sorted(ids)}"
            )
        return Decision(selected, 0.99, {selected: 0.99})


class CompoundVerifier:
    def __init__(self, driver):
        self.driver = driver

    async def verify(self, *, step, **kwargs):
        if step.goal.startswith("download"):
            done = self.driver.downloaded
        elif step.goal.startswith("rename"):
            done = self.driver.renamed
        elif step.goal.startswith("attach"):
            done = self.driver.attached
        elif step.goal.startswith("send"):
            done = self.driver.sent
        else:
            done = False
        return Verification(done, 0.99 if done else 0.1, "scripted oracle")


class CompoundWorkflowTest(unittest.TestCase):
    def test_download_rename_attach_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver = CompoundDriver(tmp)
            confirmations = []

            async def confirm(candidate):
                confirmations.append(candidate.id)
                return True

            agent = AgentLoop(
                driver,
                CompoundChooser(),
                planner=CompoundPlanner(),
                verifier=CompoundVerifier(driver),
                max_steps=20,
                download_root=tmp,
            )
            result = asyncio.run(
                agent.run(
                    "download Ahmed's report, rename it, attach it, and send the email",
                    act=True,
                    confirm=confirm,
                )
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.completed_subgoals, 4)
            self.assertTrue((Path(tmp) / "project.pdf").is_file())
            self.assertEqual(
                confirmations,
                [
                    "browser-download-100001",
                    "browser-upload-100002-1",
                    "browser-click-100003",
                ],
            )


if __name__ == "__main__":
    unittest.main()
