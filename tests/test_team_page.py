from unittest import TestCase
from unittest.mock import patch

from nip_monitor import sources, team_page
from nip_monitor.models import Match, Result


def row(match_id="1", nip_second=False, scores=("-", "-"), timestamp="1790337600000"):
    teams = [("4411", "Ninjas in Pyjamas"), ("4914", "3DMAX")]
    if nip_second:
        teams.reverse()
    return (
        '<tr class="team-row"><td><span data-unix="' + timestamp + '">25/09/2026</span></td><td>'
        + ''.join(f'<a class="team-name team-{i+1}" href="/team/{tid}/team"><span>{name}</span></a>' for i, (tid, name) in enumerate(teams))
        + ''.join(f'<span class="score">{score}</span>' for score in scores)
        + f'</td><td><a href="/matches/{match_id}/match">Match</a></td></tr>'
    )


def page(upcoming="", results=""):
    return (
        '<div id="matchesBox"><h2>Upcoming matches for Ninjas in Pyjamas</h2>'
        '<table><tr><th><a href="/events/1/event">Future &amp; Event</a></th></tr>'
        + upcoming + '</table><h2>Recent results for Ninjas in Pyjamas</h2>'
        '<table><tr><th><a href="/events/2/event">Recent Event</a></th></tr>'
        + results + '</table></div>'
    )


class TeamPageTests(TestCase):
    def test_upcoming_includes_exact_utc_time_event_and_opponent(self):
        matches, results = team_page.parse_team_page(page(row()))
        self.assertEqual(matches, [Match("1", "3DMAX", "https://www.hltv.org/matches/1/match", "Future & Event", "2026-09-25T12:00:00Z")])
        self.assertEqual(results, [])

    def test_results_assign_scores_by_nip_team_id_not_position(self):
        matches, results = team_page.parse_team_page(page(results=row("2", nip_second=True, scores=("0", "2"))))
        self.assertEqual(matches, [])
        self.assertEqual(results[0].nip_score, 2)
        self.assertEqual(results[0].opponent_score, 0)
        self.assertEqual(results[0].event, "Recent Event")

    def test_in_progress_match_not_used_for_upcoming_reminder(self):
        matches, _ = team_page.parse_team_page(page(row(scores=("1", "0"))))
        self.assertEqual(matches, [])

    def test_invalid_time_marks_section_unavailable_not_empty(self):
        matches, results = team_page.parse_team_page(page(row(timestamp="invalid")))
        self.assertIsNone(matches)
        self.assertEqual(results, [])

    def test_incomplete_results_mark_section_unavailable(self):
        matches, results = team_page.parse_team_page(page(results=row(scores=("2", "-"))))
        self.assertEqual(matches, [])
        self.assertIsNone(results)

    def test_empty_recognized_sections_are_valid(self):
        self.assertEqual(team_page.parse_team_page(page()), ([], []))

    def test_heading_without_table_is_unavailable_not_empty(self):
        matches, results = team_page.parse_team_page('<div id="matchesBox"><h2>Upcoming matches for Ninjas in Pyjamas</h2></div>')
        self.assertIsNone(matches)
        self.assertIsNone(results)

    def test_verification_or_missing_table_not_accepted_as_empty(self):
        for html in ('<html>Just a moment...</html>', '<h2>Upcoming matches for Ninjas in Pyjamas</h2>'):
            with self.subTest(html=html), self.assertRaises(sources.SourceError):
                team_page.parse_team_page(html)

    def test_team_page_success_does_not_fetch_match_or_result_reader(self):
        with patch.object(team_page, "fetch_team_page", return_value=page(row(), row("2", scores=("2", "0")))), patch.object(
            sources, "fetch_via_reader", return_value="Transfers for Ninjas in Pyjamas"
        ) as reader:
            matches, results, transfers = sources.get_nip_data()
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(results), 1)
        self.assertIsNone(transfers)
        reader.assert_not_called()

    def test_team_failure_uses_original_paths_independently(self):
        expected_matches = [Match("1", "3DMAX", "url", "Event", "2026-09-25T12:00:00Z")]
        with patch.object(team_page, "fetch_team_page", side_effect=sources.SourceError("403")), patch.object(
            sources, "fetch_via_reader", side_effect=["matches", sources.SourceError("results blocked")]
        ), patch.object(sources, "parse_matches_html", return_value=expected_matches):
            matches, results, transfers = sources.get_nip_data()
        self.assertEqual(matches, expected_matches)
        self.assertIsNone(results)
        self.assertIsNone(transfers)

    def test_only_invalid_homepage_section_uses_fallback(self):
        expected = [Result("2", "3DMAX", 2, 0, "Recent Event")]
        with patch.object(team_page, "fetch_team_page", return_value=page(row(), row("2", scores=("-", "0")))), patch.object(
            sources, "fetch_via_reader", side_effect=["results"]
        ) as reader, patch.object(sources, "parse_results", return_value=expected):
            matches, results, _ = sources.get_nip_data()
        self.assertEqual(len(matches), 1)
        self.assertEqual(results, expected)
        self.assertNotIn(sources.HLTV_MATCHES_URL, [call.args[0] for call in reader.call_args_list])
        self.assertNotIn(sources.HLTV_TRANSFERS_URL, [call.args[0] for call in reader.call_args_list])
