"""
Test suite for the reporting module.

Covers console report formatting, the Markdown job summary, GitHub
workflow-command annotation escaping and truncation, GITHUB_OUTPUT /
GITHUB_STEP_SUMMARY file handling (including graceful degradation), and
the report() orchestrator.
"""

import io
import os
import tempfile
import unittest

from repo_size_guardian.models import Blob, Violation
from repo_size_guardian.reporting import (
    ReportConfig,
    ScanStats,
    emit_annotations,
    format_console_report,
    format_step_summary,
    remediation_hint,
    report,
    write_github_output,
)


def make_violation(path='big.bin', blob_sha='a' * 40, commit_sha='c' * 40,
                    status='A', size_bytes=1024, is_binary=True,
                    mime_type=None, rule_name='threshold.max_binary_size_kb',
                    message='File exceeds size threshold', severity='error',
                    category='size', threshold_kb=None):
    """Build a Blob + Violation pair for tests.

    A small helper so each test only spells out the fields it cares about.
    Exercises the post-Agent-C Violation shape (category / threshold_kb /
    size_kb) directly against models.Violation, rather than re-implementing
    it, so this suite breaks loudly if that contract drifts.
    """
    blob = Blob(
        path=path,
        blob_sha=blob_sha,
        commit_sha=commit_sha,
        status=status,
        size_bytes=size_bytes,
        is_binary=is_binary,
        mime_type=mime_type,
    )
    return Violation(
        blob=blob,
        rule_name=rule_name,
        message=message,
        severity=severity,
        category=category,
        threshold_kb=threshold_kb,
    )


class TestViolationShape(unittest.TestCase):
    """Sanity check that models.Violation has the fields this module needs."""

    def test_violation_has_expected_fields(self):
        v = make_violation()
        self.assertEqual(v.category, 'size')
        self.assertIsNone(v.threshold_kb)
        self.assertAlmostEqual(v.size_kb, 1.0)


class TestFormatConsoleReport(unittest.TestCase):
    """Tests for format_console_report."""

    def test_no_violations_prints_clean_summary(self):
        stats = ScanStats(commits_scanned=5, blobs_scanned=10, unique_blobs=8)
        text = format_console_report([], stats)
        self.assertNotEqual(text.strip(), '')
        self.assertIn('No violations found', text)
        self.assertIn('5', text)
        self.assertIn('10', text)
        self.assertIn('8', text)

    def test_header_and_stats_present(self):
        stats = ScanStats(commits_scanned=3, blobs_scanned=7, unique_blobs=6)
        text = format_console_report([], stats)
        self.assertIn('Repo Size Guardian', text)
        self.assertIn('Commits scanned: 3', text)
        self.assertIn('Blobs scanned: 7', text)
        self.assertIn('Unique blobs: 6', text)

    def test_violation_line_contains_all_fields(self):
        v = make_violation(
            path='assets/video.mp4', commit_sha='c1' * 20, size_bytes=int(4.2 * 1024 * 1024),
            severity='error', rule_name='threshold.max_binary_size_kb',
            message='Binary file exceeds max_binary_size_kb')
        stats = ScanStats(commits_scanned=1, blobs_scanned=1, unique_blobs=1)
        text = format_console_report([v], stats)

        self.assertIn('ERROR', text)
        self.assertIn('c1c1c1c', text)  # short sha (first 7 chars)
        self.assertIn('assets/video.mp4', text)
        self.assertIn('4.2 MB', text)
        self.assertIn('Binary file exceeds max_binary_size_kb', text)
        self.assertIn('[rule: threshold.max_binary_size_kb]', text)

    def test_warn_severity_rendered_uppercase(self):
        v = make_violation(severity='warn')
        text = format_console_report([v], ScanStats())
        self.assertIn('WARN', text)

    def test_summary_counts_by_severity_and_category(self):
        violations = [
            make_violation(severity='error', category='size'),
            make_violation(severity='error', category='disallowed'),
            make_violation(severity='warn', category='size'),
        ]
        text = format_console_report(violations, ScanStats())
        self.assertIn('2 error', text)
        self.assertIn('1 warn', text)
        self.assertIn('2 size', text)
        self.assertIn('1 disallowed', text)

    def test_missing_commit_sha_renders_placeholder_not_crash(self):
        v = make_violation(commit_sha='')
        text = format_console_report([v], ScanStats())
        self.assertIn('-------', text)

    def test_unknown_size_renders_placeholder(self):
        v = make_violation(size_bytes=None)
        text = format_console_report([v], ScanStats())
        self.assertIn('unknown size', text)

    def test_no_ansi_or_box_drawing_characters(self):
        v = make_violation()
        text = format_console_report([v], ScanStats(commits_scanned=1, blobs_scanned=1, unique_blobs=1))
        self.assertNotIn('\x1b', text)
        for ch in '┌┐└┘│─├┤┬┴┼║╔╗╚╝':
            self.assertNotIn(ch, text)

    def test_human_size_formatting_boundaries(self):
        cases = [
            (500, 'B'),
            (12595, 'KB'),   # ~12.3 KB
            (4404019, 'MB'),  # ~4.2 MB
        ]
        for size_bytes, unit in cases:
            v = make_violation(size_bytes=size_bytes)
            text = format_console_report([v], ScanStats())
            self.assertIn(unit, text)


class TestFormatStepSummary(unittest.TestCase):
    """Tests for format_step_summary."""

    def test_no_violations_still_produces_summary(self):
        stats = ScanStats(commits_scanned=2, blobs_scanned=4, unique_blobs=3)
        text = format_step_summary([], stats)
        self.assertNotEqual(text.strip(), '')
        self.assertIn('No violations found', text)
        self.assertIn('## Repo Size Guardian', text)

    def test_table_header_present(self):
        v = make_violation()
        text = format_step_summary([v], ScanStats())
        self.assertIn('| Severity | File | Size | Reason | Rule |', text)
        self.assertIn('| --- | --- | --- | --- | --- |', text)

    def test_table_row_contains_violation_data(self):
        v = make_violation(
            path='notes/plan.md', size_bytes=int(612 * 1024), severity='warn',
            rule_name='threshold.max_text_size_kb', message='Text file exceeds max_text_size_kb')
        text = format_step_summary([v], ScanStats())
        self.assertIn('| WARN | `notes/plan.md` | 612.0 KB | '
                       'Text file exceeds max_text_size_kb | `threshold.max_text_size_kb` |', text)

    def test_how_to_fix_section_present_and_deduped(self):
        violations = [
            make_violation(category='size', is_binary=True),
            make_violation(category='size', is_binary=True, path='other.bin'),
            make_violation(category='disallowed'),
        ]
        text = format_step_summary(violations, ScanStats())
        self.assertIn('### How to fix', text)
        # Two same-category/binary-ness violations should collapse into a
        # single distinct hint; only two bullet points total should exist.
        bullet_count = text.count('\n- ')
        self.assertEqual(bullet_count, 2)

    def test_status_line_reports_counts(self):
        violations = [
            make_violation(severity='error'),
            make_violation(severity='warn'),
        ]
        text = format_step_summary(violations, ScanStats())
        self.assertIn('2 violation(s) found', text)
        self.assertIn('1 error', text)
        self.assertIn('1 warn', text)

    def test_truncates_at_100_rows_with_overflow_line(self):
        violations = [make_violation(path='file{0}.bin'.format(i)) for i in range(105)]
        text = format_step_summary(violations, ScanStats())
        row_lines = [ln for ln in text.splitlines() if ln.startswith('| ERROR')]
        self.assertEqual(len(row_lines), 100)
        self.assertIn('5 more violation(s) not shown', text)
        self.assertIn('truncated at 100 rows', text)

    def test_exactly_100_rows_no_overflow_line(self):
        violations = [make_violation(path='file{0}.bin'.format(i)) for i in range(100)]
        text = format_step_summary(violations, ScanStats())
        self.assertNotIn('more violation(s) not shown', text)

    def test_pipe_and_backtick_in_path_are_escaped(self):
        v = make_violation(path='weird|path`with`chars.bin', message='has | pipe and ` backtick')
        text = format_step_summary([v], ScanStats())
        self.assertIn('weird\\|path\\`with\\`chars.bin', text)
        self.assertIn('has \\| pipe and \\` backtick', text)

    def test_newline_in_message_does_not_break_table_row(self):
        v = make_violation(message='line one\nline two\r\nline three')
        text = format_step_summary([v], ScanStats())
        table_rows = [ln for ln in text.splitlines() if ln.startswith('| ERROR')]
        self.assertEqual(len(table_rows), 1)
        self.assertIn('line one line two line three', table_rows[0])


class TestRemediationHint(unittest.TestCase):
    """Tests for remediation_hint."""

    def test_always_mentions_history_rewrite(self):
        for category, is_binary in [('size', True), ('size', False),
                                     ('disallowed', None), ('rule', None)]:
            v = make_violation(category=category, is_binary=is_binary)
            hint = remediation_hint(v)
            self.assertIn('later commit', hint.lower())
            self.assertIn('history', hint.lower())

    def test_size_binary_mentions_lfs(self):
        v = make_violation(category='size', is_binary=True)
        hint = remediation_hint(v)
        self.assertIn('LFS', hint)

    def test_size_text_mentions_threshold_config(self):
        v = make_violation(category='size', is_binary=False)
        hint = remediation_hint(v)
        self.assertIn('max_text_size_kb', hint)

    def test_size_text_when_is_binary_none(self):
        # Evaluator treats is_binary is None as text; hint text should follow.
        v = make_violation(category='size', is_binary=None)
        hint = remediation_hint(v)
        self.assertIn('max_text_size_kb', hint)

    def test_disallowed_mentions_ignore_and_allow_globs(self):
        v = make_violation(category='disallowed')
        hint = remediation_hint(v)
        self.assertIn('ignore.globs', hint)
        self.assertIn('overrides.allow_globs', hint)

    def test_rule_mentions_rule_id(self):
        v = make_violation(category='rule', rule_name='large-binaries')
        hint = remediation_hint(v)
        self.assertIn('large-binaries', hint)

    def test_hint_is_short(self):
        v = make_violation()
        hint = remediation_hint(v)
        self.assertLess(len(hint), 500)


class TestEmitAnnotations(unittest.TestCase):
    """Tests for emit_annotations, including escaping and truncation."""

    def test_error_and_warning_commands(self):
        violations = [
            make_violation(severity='error', path='a.bin', message='err msg'),
            make_violation(severity='warn', path='b.bin', message='warn msg'),
        ]
        stream = io.StringIO()
        emit_annotations(violations, ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('::error file=a.bin::err msg\n', output)
        self.assertIn('::warning file=b.bin::warn msg\n', output)

    def test_annotate_pr_false_emits_nothing(self):
        v = make_violation()
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(annotate_pr=False), stream=stream)
        self.assertEqual(stream.getvalue(), '')

    def test_message_percent_escaped_first(self):
        v = make_violation(message='100% done')
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        self.assertIn('100%25 done', stream.getvalue())

    def test_message_newline_and_cr_escaped(self):
        v = make_violation(message='line1\nline2\rline3')
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('line1%0Aline2%0Dline3', output)
        self.assertNotIn('\n', output.split('::error', 1)[1].split('\n')[0])

    def test_percent_escaped_before_other_escapes_no_double_escaping(self):
        # A literal '%0A' in the original message must not become '%250A'
        # nor be mistaken for an already-escaped newline; '%' must be
        # escaped to '%25' and nothing else touches it afterwards.
        v = make_violation(message='literal %0A text')
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('literal %250A text', output)

    def test_file_property_escapes_colon_and_comma(self):
        v = make_violation(path='weird:path,name.bin')
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('file=weird%3Apath%2Cname.bin::', output)

    def test_message_does_not_escape_colon_or_comma(self):
        # Colon/comma escaping is only required for property values, not
        # the free-form message.
        v = make_violation(message='reason: too big, sorry')
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('::reason: too big, sorry\n', output)

    def test_max_annotations_default_is_50(self):
        config = ReportConfig()
        self.assertEqual(config.max_annotations, 50)

    def test_exactly_max_annotations_emits_no_notice(self):
        violations = [make_violation(path='f{0}.bin'.format(i)) for i in range(50)]
        stream = io.StringIO()
        emit_annotations(violations, ReportConfig(max_annotations=50), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 50)
        self.assertNotIn('::notice::', output)

    def test_one_over_max_annotations_emits_notice_for_one_suppressed(self):
        violations = [make_violation(path='f{0}.bin'.format(i)) for i in range(51)]
        stream = io.StringIO()
        emit_annotations(violations, ReportConfig(max_annotations=50), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 50)
        self.assertIn('::notice::1 more violation(s) suppressed', output)
        self.assertIn('job summary', output)

    def test_max_annotations_zero_is_unlimited(self):
        violations = [make_violation(path='f{0}.bin'.format(i)) for i in range(200)]
        stream = io.StringIO()
        emit_annotations(violations, ReportConfig(max_annotations=0), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 200)
        self.assertNotIn('::notice::', output)

    def test_no_line_anchor_in_annotations(self):
        v = make_violation()
        stream = io.StringIO()
        emit_annotations([v], ReportConfig(), stream=stream)
        self.assertNotIn('line=', stream.getvalue())


class TestWriteGithubOutput(unittest.TestCase):
    """Tests for write_github_output, including graceful degradation."""

    def test_none_path_is_noop(self):
        # Must not raise, and must not touch the filesystem.
        write_github_output([make_violation()], ReportConfig(github_output_path=None))

    def test_writes_violations_found_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            violations = [make_violation(severity='error'), make_violation(severity='warn')]
            write_github_output(violations, ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('violations_found=2\n', content)
            summary_lines = [ln for ln in content.splitlines() if ln.startswith('summary=')]
            self.assertEqual(len(summary_lines), 1)
            self.assertIn('2 violation(s) found', summary_lines[0])

    def test_zero_violations_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            write_github_output([], ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('violations_found=0\n', content)
            self.assertIn('summary=No violations found\n', content)

    def test_appends_rather_than_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            with open(path, 'w') as f:
                f.write('some_other_step_output=hello\n')
            write_github_output([make_violation()], ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('some_other_step_output=hello\n', content)
            self.assertIn('violations_found=1\n', content)

    def test_unwritable_path_warns_on_stderr_and_does_not_raise(self):
        bad_path = os.path.join(tempfile.gettempdir(),
                                 'repo-size-guardian-does-not-exist-dir', 'gh_output')
        stderr_capture = io.StringIO()
        import sys
        original_stderr = sys.stderr
        sys.stderr = stderr_capture
        try:
            write_github_output([make_violation()], ReportConfig(github_output_path=bad_path))
        finally:
            sys.stderr = original_stderr
        self.assertIn('warning', stderr_capture.getvalue().lower())


class TestReportConfigDefaults(unittest.TestCase):
    """Tests for ReportConfig's environment-variable-based defaults."""

    def test_defaults_read_from_environment_at_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            output_path = os.path.join(tmp, 'output.txt')
            old_summary = os.environ.get('GITHUB_STEP_SUMMARY')
            old_output = os.environ.get('GITHUB_OUTPUT')
            os.environ['GITHUB_STEP_SUMMARY'] = summary_path
            os.environ['GITHUB_OUTPUT'] = output_path
            try:
                config = ReportConfig()
                self.assertEqual(config.step_summary_path, summary_path)
                self.assertEqual(config.github_output_path, output_path)
            finally:
                if old_summary is None:
                    os.environ.pop('GITHUB_STEP_SUMMARY', None)
                else:
                    os.environ['GITHUB_STEP_SUMMARY'] = old_summary
                if old_output is None:
                    os.environ.pop('GITHUB_OUTPUT', None)
                else:
                    os.environ['GITHUB_OUTPUT'] = old_output

    def test_defaults_none_when_env_absent(self):
        old_summary = os.environ.pop('GITHUB_STEP_SUMMARY', None)
        old_output = os.environ.pop('GITHUB_OUTPUT', None)
        try:
            config = ReportConfig()
            self.assertIsNone(config.step_summary_path)
            self.assertIsNone(config.github_output_path)
        finally:
            if old_summary is not None:
                os.environ['GITHUB_STEP_SUMMARY'] = old_summary
            if old_output is not None:
                os.environ['GITHUB_OUTPUT'] = old_output

    def test_explicit_path_overrides_environment(self):
        os.environ['GITHUB_STEP_SUMMARY'] = '/should/not/be/used'
        try:
            config = ReportConfig(step_summary_path='/explicit/path.md')
            self.assertEqual(config.step_summary_path, '/explicit/path.md')
        finally:
            os.environ.pop('GITHUB_STEP_SUMMARY', None)


class TestReportOrchestrator(unittest.TestCase):
    """End-to-end tests for report()."""

    def test_zero_violations_end_to_end(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            output_path = os.path.join(tmp, 'output.txt')
            config = ReportConfig(step_summary_path=summary_path, github_output_path=output_path)
            stats = ScanStats(commits_scanned=3, blobs_scanned=9, unique_blobs=9)

            report([], stats, config, stream=stream)

            console_text = stream.getvalue()
            self.assertIn('No violations found', console_text)
            self.assertNotEqual(console_text.strip(), '')

            with open(summary_path) as f:
                summary_text = f.read()
            self.assertIn('No violations found', summary_text)

            with open(output_path) as f:
                output_text = f.read()
            self.assertIn('violations_found=0', output_text)
            self.assertIn('summary=No violations found', output_text)

    def test_with_violations_end_to_end_writes_all_four_surfaces(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            output_path = os.path.join(tmp, 'output.txt')
            config = ReportConfig(step_summary_path=summary_path, github_output_path=output_path,
                                   max_annotations=50)
            stats = ScanStats(commits_scanned=1, blobs_scanned=2, unique_blobs=2)
            violations = [
                make_violation(path='a.bin', severity='error', category='size', is_binary=True),
                make_violation(path='b.md', severity='warn', category='size', is_binary=False),
            ]

            report(violations, stats, config, stream=stream)

            console_text = stream.getvalue()
            self.assertIn('Violations (2):', console_text)
            self.assertIn('::error file=a.bin::', console_text)
            self.assertIn('::warning file=b.md::', console_text)

            with open(summary_path) as f:
                summary_text = f.read()
            self.assertIn('| Severity | File | Size | Reason | Rule |', summary_text)
            self.assertIn('### How to fix', summary_text)

            with open(output_path) as f:
                output_text = f.read()
            self.assertIn('violations_found=2', output_text)

    def test_local_run_with_no_github_env_does_not_crash(self):
        # Simulates the owner running the tool by hand outside Actions:
        # no GITHUB_STEP_SUMMARY / GITHUB_OUTPUT, so those surfaces no-op.
        old_summary = os.environ.pop('GITHUB_STEP_SUMMARY', None)
        old_output = os.environ.pop('GITHUB_OUTPUT', None)
        try:
            stream = io.StringIO()
            config = ReportConfig()
            self.assertIsNone(config.step_summary_path)
            self.assertIsNone(config.github_output_path)
            stats = ScanStats(commits_scanned=1, blobs_scanned=1, unique_blobs=1)
            report([make_violation()], stats, config, stream=stream)
            self.assertIn('Repo Size Guardian scan report', stream.getvalue())
        finally:
            if old_summary is not None:
                os.environ['GITHUB_STEP_SUMMARY'] = old_summary
            if old_output is not None:
                os.environ['GITHUB_OUTPUT'] = old_output

    def test_step_summary_is_appended_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            with open(summary_path, 'w') as f:
                f.write('# Some other step already wrote this\n')
            config = ReportConfig(step_summary_path=summary_path, github_output_path=None)
            report([make_violation()], ScanStats(), config, stream=io.StringIO())
            with open(summary_path) as f:
                content = f.read()
            self.assertTrue(content.startswith('# Some other step already wrote this\n'))
            self.assertIn('## Repo Size Guardian', content)


if __name__ == '__main__':
    unittest.main()
