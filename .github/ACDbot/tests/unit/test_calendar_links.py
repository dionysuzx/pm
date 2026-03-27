import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'modules'))

from calendar_links import (
    build_google_calendar_link,
    build_ics_content,
    build_occurrence_ics_artifact_path,
    build_raw_github_url,
)


class TestCalendarLinks(unittest.TestCase):
    def test_build_google_calendar_link(self):
        link = build_google_calendar_link(
            summary="Test Protocol Call",
            start_time="2025-04-24T14:00:00Z",
            duration_minutes=60,
            description="Issue: https://github.com/ethereum/pm/issues/123",
        )

        self.assertIn("action=TEMPLATE", link)
        self.assertIn("text=Test+Protocol+Call", link)
        self.assertIn("dates=20250424T140000Z%2F20250424T150000Z", link)
        self.assertIn("Issue%3A+https%3A%2F%2Fgithub.com%2Fethereum%2Fpm%2Fissues%2F123", link)

    def test_build_occurrence_ics_artifact_path(self):
        self.assertEqual(
            build_occurrence_ics_artifact_path("acde", "2026-04-09T14:00:00Z", 25),
            ".github/ACDbot/artifacts/acde/2026-04-09_025/invite.ics",
        )
        self.assertEqual(
            build_occurrence_ics_artifact_path("one-off-1971", "2026-03-17T17:00:00Z", 1),
            ".github/ACDbot/artifacts/one-off-1971/2026-03-17_001/invite.ics",
        )

    def test_build_raw_github_url(self):
        self.assertEqual(
            build_raw_github_url("ethereum/pm", "master", ".github/ACDbot/artifacts/acde/2026-04-09_025/invite.ics"),
            "https://raw.githubusercontent.com/ethereum/pm/master/.github/ACDbot/artifacts/acde/2026-04-09_025/invite.ics",
        )

    def test_build_ics_content(self):
        content = build_ics_content(
            summary="All Core Devs - Execution (ACDE) #234, April 9, 2026",
            start_time="2026-04-09T14:00:00Z",
            duration_minutes=90,
            description="Meeting: https://zoom.us/j/123456789\n\nIssue: https://github.com/ethereum/pm/issues/2000",
            issue_url="https://github.com/ethereum/pm/issues/2000",
            uid="ethereum-pm-issue-2000@protocol-calls.ethereum.org",
        )

        self.assertIn("BEGIN:VCALENDAR\r\n", content)
        self.assertIn("VERSION:2.0\r\n", content)
        self.assertIn("METHOD:PUBLISH\r\n", content)
        self.assertIn("UID:ethereum-pm-issue-2000@protocol-calls.ethereum.org\r\n", content)
        self.assertIn("DTSTART:20260409T140000Z\r\n", content)
        self.assertIn("DTEND:20260409T153000Z\r\n", content)
        self.assertIn("SUMMARY:All Core Devs - Execution (ACDE) #234\\, April 9\\, 2026\r\n", content)
        self.assertIn(
            "DESCRIPTION:Meeting: https://zoom.us/j/123456789\\n\\nIssue: https://github.com/ethereum/pm/issues/2000\r\n",
            content,
        )
        self.assertIn("URL:https://github.com/ethereum/pm/issues/2000\r\n", content)
        self.assertTrue(content.endswith("END:VCALENDAR\r\n"))
