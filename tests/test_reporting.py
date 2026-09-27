"""
Test suite for the reporting module.

Covers console report formatting, the Markdown job summary, GitHub
workflow-command annotation escaping and truncation, GITHUB_OUTPUT /
GITHUB_STEP_SUMMARY file handling (including graceful degradation), and
the report() orchestrator -- all against the `ReportEntry`/`Violation`
("hit") model, including an entry with several hits.
"""

import io
import os
import tempfile
import unittest

from repo_size_guardian.models import Blob, ReportEntry, Violation
from repo_size_guardian.reporting import (
    ReportConfig,
    ScanStats,
    emit_annotations,
    emit_error_annotation,
    emit_warning,
    format_console_report,
    format_step_summary,
    remediation_hint,
    report,
    write_github_output,
)


def make_violation(rule_name='threshold', message='File exceeds size threshold',
                   severity='error', is_input_rule=False, has_size_condition=False,
                   is_binary=None):
    """Build a single hit for tests."""
    return Violation(
        rule_name=rule_name,
        message=message,
        severity=severity,
        is_input_rule=is_input_rule,
        has_size_condition=has_size_condition,
        is_binary=is_binary,
    )


def make_entry(path='big.bin', blob_sha='a' * 40, commit_sha='c' * 40,
               status='A', size_bytes=1024, violations=None, is_transient_version=None):
    """Build a Blob + ReportEntry pair for tests."""
    blob = Blob(path=path, blob_sha=blob_sha, commit_sha=commit_sha,
                status=status, size_bytes=size_bytes, is_transient_version=is_transient_version)
    return ReportEntry(blob=blob, violations=violations or [make_violation()])


class TestReportEntryShape(unittest.TestCase):
    """Sanity check that models.ReportEntry has the fields this module needs."""

    def test_entry_exposes_blob_fields(self):
        entry = make_entry()
        self.assertEqual(entry.path, 'big.bin')
        self.assertEqual(entry.commit_sha, 'c' * 40)
        self.assertEqual(entry.size_bytes, 1024)

    def test_severity_is_highest_among_violations(self):
        entry = make_entry(violations=[
            make_violation(severity='warn'), make_violation(severity='error')])
        self.assertEqual(entry.severity, 'error')

    def test_severity_is_warn_when_no_error_present(self):
        entry = make_entry(violations=[make_violation(severity='warn')])
        self.assertEqual(entry.severity, 'warn')


class TestFormatConsoleReport(unittest.TestCase):
    """Tests for format_console_report."""

    def test_no_entries_prints_clean_summary(self):
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

    def test_entry_line_contains_all_fields(self):
        entry = make_entry(
            path='assets/video.mp4', commit_sha='c1' * 20, size_bytes=int(4.2 * 1024 * 1024),
            violations=[make_violation(
                severity='error', rule_name='max_binary_size_kb',
                message='Binary file size 4300.0 KB exceeds 200 KB limit')])
        stats = ScanStats(commits_scanned=1, blobs_scanned=1, unique_blobs=1)
        text = format_console_report([entry], stats)

        self.assertIn('ERROR', text)
        self.assertIn('c1c1c1c', text)  # short sha (first 7 chars)
        self.assertIn('assets/video.mp4', text)
        self.assertIn('4.2 MB', text)
        self.assertIn('Binary file size 4300.0 KB exceeds 200 KB limit', text)
        self.assertIn('[rule: max_binary_size_kb]', text)

    def test_multiple_hits_joined_with_semicolon_and_rules_plural(self):
        entry = make_entry(violations=[
            make_violation(rule_name='r1', message='first reason'),
            make_violation(rule_name='r2', message='second reason'),
        ])
        text = format_console_report([entry], ScanStats())
        self.assertIn('first reason; second reason', text)
        self.assertIn('[rules: r1, r2]', text)

    def test_warn_severity_rendered_uppercase(self):
        entry = make_entry(violations=[make_violation(severity='warn')])
        text = format_console_report([entry], ScanStats())
        self.assertIn('WARN', text)

    def test_totals_line_matches_summary_wording(self):
        entries = [
            make_entry(path='a.bin', violations=[make_violation(severity='error')]),
            make_entry(path='b.bin', violations=[
                make_violation(severity='error'), make_violation(severity='warn')]),
        ]
        text = format_console_report(entries, ScanStats())
        self.assertIn('3 violation(s) in 2 file(s) (2 error, 1 warn)', text)

    def test_missing_commit_sha_renders_placeholder_not_crash(self):
        entry = make_entry(commit_sha='')
        text = format_console_report([entry], ScanStats())
        self.assertIn('-------', text)

    def test_unknown_size_renders_placeholder(self):
        entry = make_entry(size_bytes=None)
        text = format_console_report([entry], ScanStats())
        self.assertIn('unknown size', text)

    def test_no_ansi_or_box_drawing_characters(self):
        entry = make_entry()
        text = format_console_report([entry], ScanStats(
            commits_scanned=1, blobs_scanned=1, unique_blobs=1))
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
            entry = make_entry(size_bytes=size_bytes)
            text = format_console_report([entry], ScanStats())
            self.assertIn(unit, text)


class TestFormatStepSummary(unittest.TestCase):
    """Tests for format_step_summary."""

    def test_no_entries_still_produces_summary(self):
        stats = ScanStats(commits_scanned=2, blobs_scanned=4, unique_blobs=3)
        text = format_step_summary([], stats)
        self.assertNotEqual(text.strip(), '')
        self.assertIn('No violations found', text)
        self.assertIn('## Repo Size Guardian', text)

    def test_table_header_present(self):
        entry = make_entry()
        text = format_step_summary([entry], ScanStats())
        self.assertIn('| Severity | File | Size | Reason | Rules |', text)
        self.assertIn('| --- | --- | --- | --- | --- |', text)

    def test_table_row_contains_entry_data(self):
        entry = make_entry(
            path='notes/plan.md', size_bytes=int(612 * 1024),
            violations=[make_violation(
                severity='warn', rule_name='max_text_size_kb',
                message='Text file size 612.0 KB exceeds 500 KB limit')])
        text = format_step_summary([entry], ScanStats())
        self.assertIn('| WARN | `notes/plan.md` | 612.0 KB | '
                      'Text file size 612.0 KB exceeds 500 KB limit | `max_text_size_kb` |', text)

    def test_table_row_lists_every_rule_and_message(self):
        entry = make_entry(violations=[
            make_violation(rule_name='r1', message='first'),
            make_violation(rule_name='r2', message='second'),
        ])
        text = format_step_summary([entry], ScanStats())
        self.assertIn('first; second', text)
        self.assertIn('`r1, r2`', text)

    def test_how_to_fix_section_present_and_deduped(self):
        entries = [
            make_entry(violations=[make_violation(has_size_condition=True, is_binary=True)]),
            make_entry(path='other.bin',
                       violations=[make_violation(has_size_condition=True, is_binary=True)]),
            make_entry(path='third.bin',
                       violations=[make_violation(rule_name='disallow_extensions',
                                                  is_input_rule=True)]),
        ]
        text = format_step_summary(entries, ScanStats())
        self.assertIn('### How to fix', text)
        # Two same-shape size hits collapse into one hint; the third is
        # distinct (a different rule name), so two bullets total.
        bullet_count = text.count('\n- ')
        self.assertEqual(bullet_count, 2)

    def test_status_line_reports_counts(self):
        entries = [
            make_entry(path='a', violations=[make_violation(severity='error')]),
            make_entry(path='b', violations=[make_violation(severity='warn')]),
        ]
        text = format_step_summary(entries, ScanStats())
        self.assertIn('2 violation(s) in 2 file(s)', text)
        self.assertIn('1 error', text)
        self.assertIn('1 warn', text)

    def test_truncates_at_100_rows_with_overflow_line(self):
        entries = [make_entry(path='file{0}.bin'.format(i)) for i in range(105)]
        text = format_step_summary(entries, ScanStats())
        row_lines = [ln for ln in text.splitlines() if ln.startswith('| ERROR')]
        self.assertEqual(len(row_lines), 100)
        self.assertIn('5 more row(s) not shown', text)
        self.assertIn('truncated at 100 rows', text)

    def test_exactly_100_rows_no_overflow_line(self):
        entries = [make_entry(path='file{0}.bin'.format(i)) for i in range(100)]
        text = format_step_summary(entries, ScanStats())
        self.assertNotIn('more row(s) not shown', text)

    def test_pipe_and_backtick_in_path_are_escaped(self):
        entry = make_entry(path='weird|path`with`chars.bin',
                           violations=[make_violation(message='has | pipe and ` backtick')])
        text = format_step_summary([entry], ScanStats())
        self.assertIn('weird\\|path\\`with\\`chars.bin', text)
        self.assertIn('has \\| pipe and \\` backtick', text)

    def test_newline_in_message_does_not_break_table_row(self):
        entry = make_entry(violations=[make_violation(message='line one\nline two\r\nline three')])
        text = format_step_summary([entry], ScanStats())
        table_rows = [ln for ln in text.splitlines() if ln.startswith('| ERROR')]
        self.assertEqual(len(table_rows), 1)
        self.assertIn('line one line two line three', table_rows[0])


class TestTransientVersionNote(unittest.TestCase):
    """
    A `transient_version` entry's reason gets the "removed or replaced
    later" note, shared by the console report, the job summary's Reason
    column, and the annotation message alike (see `_entry_reason`).
    """

    def test_console_report_includes_note(self):
        entry = make_entry(path='scratch.bin', is_transient_version=True)
        text = format_console_report([entry], ScanStats())
        self.assertIn(
            'This version of scratch.bin was removed or replaced later in '
            'this pull request, but it stays in the history.', text)

    def test_console_report_omits_note_when_not_transient_version(self):
        entry = make_entry(is_transient_version=False)
        text = format_console_report([entry], ScanStats())
        self.assertNotIn('removed or replaced later', text)

    def test_console_report_omits_note_when_undetermined(self):
        entry = make_entry(is_transient_version=None)
        text = format_console_report([entry], ScanStats())
        self.assertNotIn('removed or replaced later', text)

    def test_job_summary_reason_column_includes_note(self):
        entry = make_entry(path='scratch.bin', is_transient_version=True)
        text = format_step_summary([entry], ScanStats())
        self.assertIn(
            'This version of scratch.bin was removed or replaced later in '
            'this pull request, but it stays in the history.', text)

    def test_job_summary_omits_note_when_not_transient_version(self):
        entry = make_entry(is_transient_version=False)
        text = format_step_summary([entry], ScanStats())
        self.assertNotIn('removed or replaced later', text)

    def test_annotation_message_includes_note(self):
        entry = make_entry(path='scratch.bin', is_transient_version=True)
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        self.assertIn(
            'This version of scratch.bin was removed or replaced later in '
            'this pull request, but it stays in the history.', stream.getvalue())

    def test_annotation_message_omits_note_when_not_transient_version(self):
        entry = make_entry(is_transient_version=False)
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        self.assertNotIn('removed or replaced later', stream.getvalue())

    def test_note_appended_after_the_joined_hit_messages(self):
        entry = make_entry(
            is_transient_version=True,
            violations=[make_violation(message='Matched rule \'r\'')])
        text = format_console_report([entry], ScanStats())
        line = next(ln for ln in text.splitlines() if 'big.bin' in ln)
        self.assertIn("Matched rule 'r' This version of big.bin was removed", line)


class TestRemediationHint(unittest.TestCase):
    """Tests for remediation_hint."""

    def test_always_mentions_history_rewrite(self):
        cases = [
            make_violation(has_size_condition=True, is_binary=True),
            make_violation(has_size_condition=True, is_binary=False),
            make_violation(has_size_condition=False, is_input_rule=True),
            make_violation(has_size_condition=False, is_input_rule=False),
        ]
        for violation in cases:
            hint = remediation_hint(violation)
            self.assertIn('later commit', hint.lower())
            self.assertIn('history', hint.lower())

    def test_size_binary_mentions_lfs(self):
        violation = make_violation(has_size_condition=True, is_binary=True)
        hint = remediation_hint(violation)
        self.assertIn('LFS', hint)

    def test_size_text_mentions_raising_the_limit(self):
        violation = make_violation(has_size_condition=True, is_binary=False)
        hint = remediation_hint(violation)
        self.assertIn('raise the size limit', hint)

    def test_size_undetermined_type_uses_text_wording(self):
        # Evaluator treats is_binary=None as text-like; hint text follows.
        violation = make_violation(has_size_condition=True, is_binary=None)
        hint = remediation_hint(violation)
        self.assertIn('raise the size limit', hint)

    def test_non_size_input_rule_names_the_input(self):
        violation = make_violation(
            has_size_condition=False, is_input_rule=True, rule_name='disallow_extensions')
        hint = remediation_hint(violation)
        self.assertIn('`disallow_extensions` input', hint)

    def test_non_size_policy_rule_names_the_rule(self):
        violation = make_violation(
            has_size_condition=False, is_input_rule=False, rule_name='large-binaries')
        hint = remediation_hint(violation)
        self.assertIn('policy rule `large-binaries`', hint)

    def test_hint_is_short(self):
        violation = make_violation()
        hint = remediation_hint(violation)
        self.assertLess(len(hint), 500)


class TestEmitAnnotations(unittest.TestCase):
    """Tests for emit_annotations, including escaping and truncation."""

    def test_error_and_warning_commands(self):
        entries = [
            make_entry(path='a.bin', violations=[
                       make_violation(severity='error', message='err msg')]),
            make_entry(path='b.bin', violations=[
                       make_violation(severity='warn', message='warn msg')]),
        ]
        stream = io.StringIO()
        emit_annotations(entries, ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('::error file=a.bin::err msg\n', output)
        self.assertIn('::warning file=b.bin::warn msg\n', output)

    def test_entry_with_several_hits_lists_every_message(self):
        entry = make_entry(violations=[
            make_violation(message='first'), make_violation(message='second')])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        self.assertIn('first; second', stream.getvalue())

    def test_annotate_pr_false_emits_nothing(self):
        entry = make_entry()
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(annotate_pr=False), stream=stream)
        self.assertEqual(stream.getvalue(), '')

    def test_message_percent_escaped_first(self):
        entry = make_entry(violations=[make_violation(message='100% done')])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        self.assertIn('100%25 done', stream.getvalue())

    def test_message_newline_and_cr_escaped(self):
        entry = make_entry(violations=[make_violation(message='line1\nline2\rline3')])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('line1%0Aline2%0Dline3', output)
        self.assertNotIn('\n', output.split('::error', 1)[1].split('\n')[0])

    def test_percent_escaped_before_other_escapes_no_double_escaping(self):
        entry = make_entry(violations=[make_violation(message='literal %0A text')])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('literal %250A text', output)

    def test_file_property_escapes_colon_and_comma(self):
        entry = make_entry(path='weird:path,name.bin')
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('file=weird%3Apath%2Cname.bin::', output)

    def test_message_does_not_escape_colon_or_comma(self):
        entry = make_entry(violations=[make_violation(message='reason: too big, sorry')])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        output = stream.getvalue()
        self.assertIn('::reason: too big, sorry\n', output)

    def test_max_annotations_default_is_10(self):
        config = ReportConfig()
        self.assertEqual(config.max_annotations, 10)

    def test_exactly_max_annotations_emits_no_notice(self):
        entries = [make_entry(path='f{0}.bin'.format(i)) for i in range(50)]
        stream = io.StringIO()
        emit_annotations(entries, ReportConfig(max_annotations=50), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 50)
        self.assertNotIn('::notice::', output)

    def test_one_over_max_annotations_emits_notice_for_one_suppressed(self):
        entries = [make_entry(path='f{0}.bin'.format(i)) for i in range(51)]
        stream = io.StringIO()
        emit_annotations(entries, ReportConfig(max_annotations=50), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 50)
        self.assertIn('::notice::1 more file(s) suppressed', output)
        self.assertIn('job summary', output)

    def test_max_annotations_counts_entries_not_hits(self):
        # An entry with several hits is still a single annotation.
        entry = make_entry(violations=[make_violation(), make_violation(), make_violation()])
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(max_annotations=1), stream=stream)
        self.assertEqual(stream.getvalue().count('::error'), 1)

    def test_max_annotations_zero_is_unlimited(self):
        entries = [make_entry(path='f{0}.bin'.format(i)) for i in range(200)]
        stream = io.StringIO()
        emit_annotations(entries, ReportConfig(max_annotations=0), stream=stream)
        output = stream.getvalue()
        self.assertEqual(output.count('::error'), 200)
        self.assertNotIn('::notice::', output)

    def test_no_line_anchor_in_annotations(self):
        entry = make_entry()
        stream = io.StringIO()
        emit_annotations([entry], ReportConfig(), stream=stream)
        self.assertNotIn('line=', stream.getvalue())


class TestEmitErrorAnnotation(unittest.TestCase):
    """
    Tests for emit_error_annotation: the ad-hoc ::error:: annotation used
    outside the per-entry path (config errors, internal crashes).
    """

    def test_writes_a_plain_error_annotation(self):
        stream = io.StringIO()
        emit_error_annotation('something went wrong', stream=stream)
        self.assertEqual(stream.getvalue(), '::error::something went wrong\n')

    def test_no_file_property(self):
        stream = io.StringIO()
        emit_error_annotation('oops', stream=stream)
        self.assertNotIn('file=', stream.getvalue())

    def test_reuses_the_same_message_escaping_as_emit_annotations(self):
        stream = io.StringIO()
        emit_error_annotation('100% done\nline2\rline3', stream=stream)
        output = stream.getvalue()
        self.assertEqual(output, '::error::100%25 done%0Aline2%0Dline3\n')


class TestEmitWarning(unittest.TestCase):
    """Tests for emit_warning: the ad-hoc ::warning:: annotation."""

    def test_writes_a_plain_warning_annotation(self):
        stream = io.StringIO()
        emit_warning('nothing is being enforced', stream=stream)
        self.assertEqual(stream.getvalue(), '::warning::nothing is being enforced\n')

    def test_reuses_the_same_message_escaping_as_emit_error_annotation(self):
        stream = io.StringIO()
        emit_warning('100% done\nline2', stream=stream)
        self.assertEqual(stream.getvalue(), '::warning::100%25 done%0Aline2\n')


class TestWriteGithubOutput(unittest.TestCase):
    """Tests for write_github_output, including graceful degradation."""

    def test_none_path_is_noop(self):
        write_github_output([make_entry()], ReportConfig(github_output_path=None))

    def test_writes_counts_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            entries = [
                make_entry(path='a.bin', violations=[make_violation(severity='error')]),
                make_entry(path='b.bin', violations=[make_violation(severity='warn')]),
            ]
            write_github_output(entries, ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('violating_file_count=2\n', content)
            self.assertIn('violation_count=2\n', content)
            summary_lines = [ln for ln in content.splitlines() if ln.startswith('summary=')]
            self.assertEqual(len(summary_lines), 1)
            self.assertIn('2 violation(s) in 2 file(s)', summary_lines[0])

    def test_counts_distinct_files_not_entries(self):
        # Two entries for the same path (two file versions) count as one
        # violating file, even though they add up to two hits.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            entries = [
                make_entry(path='a.txt', blob_sha='sha1', commit_sha='c1' * 20),
                make_entry(path='a.txt', blob_sha='sha2', commit_sha='c2' * 20),
            ]
            write_github_output(entries, ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('violating_file_count=1\n', content)
            self.assertIn('violation_count=2\n', content)

    def test_zero_entries_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            write_github_output([], ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('violating_file_count=0\n', content)
            self.assertIn('violation_count=0\n', content)
            self.assertIn('summary=No violations found\n', content)

    def test_appends_rather_than_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'gh_output')
            with open(path, 'w') as f:
                f.write('some_other_step_output=hello\n')
            write_github_output([make_entry()], ReportConfig(github_output_path=path))
            with open(path) as f:
                content = f.read()
            self.assertIn('some_other_step_output=hello\n', content)
            self.assertIn('violation_count=1\n', content)

    def test_unwritable_path_warns_on_stderr_and_does_not_raise(self):
        bad_path = os.path.join(tempfile.gettempdir(),
                                'repo-size-guardian-does-not-exist-dir', 'gh_output')
        stderr_capture = io.StringIO()
        import sys
        original_stderr = sys.stderr
        sys.stderr = stderr_capture
        try:
            write_github_output([make_entry()], ReportConfig(github_output_path=bad_path))
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

    def test_zero_entries_end_to_end(self):
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
            self.assertIn('violating_file_count=0', output_text)
            self.assertIn('violation_count=0', output_text)
            self.assertIn('summary=No violations found', output_text)

    def test_with_entries_end_to_end_writes_all_four_surfaces(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            output_path = os.path.join(tmp, 'output.txt')
            config = ReportConfig(step_summary_path=summary_path, github_output_path=output_path,
                                  max_annotations=50)
            stats = ScanStats(commits_scanned=1, blobs_scanned=2, unique_blobs=2)
            entries = [
                make_entry(path='a.bin', violations=[
                    make_violation(severity='error', has_size_condition=True, is_binary=True)]),
                make_entry(path='b.md', violations=[
                    make_violation(severity='warn', has_size_condition=True, is_binary=False)]),
            ]

            report(entries, stats, config, stream=stream)

            console_text = stream.getvalue()
            self.assertIn('::error file=a.bin::', console_text)
            self.assertIn('::warning file=b.md::', console_text)

            with open(summary_path) as f:
                summary_text = f.read()
            self.assertIn('| Severity | File | Size | Reason | Rules |', summary_text)
            self.assertIn('### How to fix', summary_text)

            with open(output_path) as f:
                output_text = f.read()
            self.assertIn('violation_count=2', output_text)

    def test_local_run_with_no_github_env_does_not_crash(self):
        old_summary = os.environ.pop('GITHUB_STEP_SUMMARY', None)
        old_output = os.environ.pop('GITHUB_OUTPUT', None)
        try:
            stream = io.StringIO()
            config = ReportConfig()
            self.assertIsNone(config.step_summary_path)
            self.assertIsNone(config.github_output_path)
            stats = ScanStats(commits_scanned=1, blobs_scanned=1, unique_blobs=1)
            report([make_entry()], stats, config, stream=stream)
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
            report([make_entry()], ScanStats(), config, stream=io.StringIO())
            with open(summary_path) as f:
                content = f.read()
            self.assertTrue(content.startswith('# Some other step already wrote this\n'))
            self.assertIn('## Repo Size Guardian', content)


if __name__ == '__main__':
    unittest.main()
