import io
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from jack.lsp_analysis import ProjectAnalyzer, uri_from_path
from jack.lsp_server import Document, LanguageServer


class SemanticLspProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, source: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path.resolve()

    def analyze(self, entry: Path, overlays=None, versions=None):
        return ProjectAnalyzer([self.root]).analyze(
            entry, overlays=overlays, versions=versions
        )

    def server_for(self, entry: Path, analysis, version=1):
        server = LanguageServer(None, io.BytesIO(), None)
        uri = uri_from_path(entry)
        server.documents[uri] = Document(uri, entry.read_text(), version)
        server.semantic_model = analysis.model
        return server, uri

    def test_unsaved_import_overlay_is_used_for_project_symbols(self):
        library = self.write(
            'math.jack',
            'module math;\npub i32 old_name(i32 value) { return value; }\n',
        )
        entry = self.write(
            'main.jack',
            'module app;\nimport math;\ni32 result = new_name(1);\n',
        )
        overlay = 'module math;\npub i32 new_name(i32 value) { return value; }\n'

        analysis = self.analyze(entry, overlays={library: overlay})

        self.assertEqual([], analysis.diagnostics)
        self.assertIn('new_name', {symbol.name for symbol in analysis.model.symbols.values()})
        self.assertNotIn('old_name', {symbol.name for symbol in analysis.model.symbols.values()})

    def test_checked_in_examples_have_no_editor_semantic_diagnostics(self):
        examples = Path(__file__).resolve().parents[1] / 'examples'
        entry = examples / 'borrows.jack'

        analysis = ProjectAnalyzer([examples]).analyze(entry)

        self.assertEqual([], analysis.diagnostics)

    def test_imported_semantic_error_keeps_imported_source_path(self):
        library = self.write(
            'bad.jack',
            'module bad;\npub Missing value;\n',
        )
        entry = self.write('main.jack', 'module app;\nimport bad;\n')

        analysis = self.analyze(entry)

        diagnostic = next(
            item for item in analysis.diagnostics if 'Unknown type "Missing"' in item.message
        )
        self.assertEqual(str(library), diagnostic.span.source_path)

    def test_cross_file_definition_references_and_rename(self):
        library = self.write(
            'math.jack',
            'module math;\npub i32 add(i32 left, i32 right) { return left + right; }\n',
        )
        entry = self.write(
            'main.jack',
            'module app;\nimport math.{add};\ni32 value = add(1, 2);\n',
        )
        analysis = self.analyze(entry, versions={entry: 4})
        server, uri = self.server_for(entry, analysis, version=4)
        position = {'line': 2, 'character': 13}

        definition = server._definition({
            'textDocument': {'uri': uri}, 'position': position,
        })
        references = server._references({
            'textDocument': {'uri': uri}, 'position': position,
            'context': {'includeDeclaration': True},
        })
        edit = server._rename({
            'textDocument': {'uri': uri}, 'position': position,
            'newName': 'sum',
        })

        self.assertEqual(uri_from_path(library), definition[0]['uri'])
        self.assertEqual(3, len(references))
        self.assertEqual(2, len(edit['documentChanges']))
        self.assertEqual(4, next(
            change['textDocument']['version'] for change in edit['documentChanges']
            if change['textDocument']['uri'] == uri
        ))

    def test_member_completion_uses_receiver_type(self):
        entry = self.write(
            'main.jack',
            'struct Point { i32 x; void reset(&inout self) { self.x = 0; } }\n'
            'Point point;\nprint(point);\n',
        )
        analysis = self.analyze(entry, versions={entry: 1})
        server, uri = self.server_for(entry, analysis)
        server.documents[uri].text = entry.read_text().replace('point);', 'point.);')

        completions = server._completion({
            'textDocument': {'uri': uri},
            'position': {'line': 2, 'character': 12},
        })

        self.assertEqual({'reset', 'x'}, {item['label'] for item in completions})

    def test_lexical_completion_uses_recovered_declarations(self):
        entry = self.write(
            'main.jack',
            'i32 before = 1;\ni32 broken = ;\nbef\n',
        )
        server = LanguageServer(None, None, None)
        uri = uri_from_path(entry)
        server.documents[uri] = Document(uri, entry.read_text(), 1)

        completions = server._completion({
            'textDocument': {'uri': uri},
            'position': {'line': 2, 'character': 3},
        })

        self.assertIn('before', {item['label'] for item in completions})

    def test_semantic_tokens_and_signature_help_use_project_model(self):
        entry = self.write(
            'main.jack',
            'i32 add(i32 left, i32 right) { return left + right; }\n'
            'i32 value = add(1, 2);\n',
        )
        analysis = self.analyze(entry, versions={entry: 1})
        server, uri = self.server_for(entry, analysis)

        tokens = server._semantic_tokens({'textDocument': {'uri': uri}})
        signature = server._signature_help({
            'textDocument': {'uri': uri},
            'position': {'line': 1, 'character': 19},
        })

        self.assertGreater(len(tokens['data']), 20)
        self.assertEqual('i32 add(i32 left, i32 right)', signature['signatures'][0]['label'])
        self.assertEqual(1, signature['activeParameter'])

    def test_editor_validator_reports_multiple_independent_errors(self):
        entry = self.write(
            'main.jack',
            'Missing value;\nprint(unknown_one);\nprint(unknown_two);\n',
        )

        analysis = self.analyze(entry)
        codes = {item.code for item in analysis.diagnostics}

        self.assertIn('unknown-type', codes)
        self.assertIn('unknown-name', codes)

    def test_typo_code_action_is_unique_and_versioned(self):
        entry = self.write('main.jack', 'i32 value = 1;\nprint(vaule);\n')
        analysis = self.analyze(entry, versions={entry: 3})
        server, uri = self.server_for(entry, analysis, version=3)
        diagnostic = {
            'range': {
                'start': {'line': 1, 'character': 6},
                'end': {'line': 1, 'character': 11},
            },
            'message': 'Unknown name "vaule".',
            'code': 'unknown-name',
        }

        actions = server._code_actions({
            'textDocument': {'uri': uri},
            'range': diagnostic['range'],
            'context': {'diagnostics': [diagnostic]},
        })

        self.assertEqual(['Change to "value"'], [item['title'] for item in actions])
        self.assertEqual(3, actions[0]['edit']['documentChanges'][0]['textDocument']['version'])

    def test_missing_selective_import_code_action_is_unique(self):
        self.write('math.jack', 'module math;\npub i32 add(i32 value) { return value; }\n')
        entry = self.write('main.jack', 'module app;\nprint(add(1));\n')
        analysis = self.analyze(entry, versions={entry: 1})
        server, uri = self.server_for(entry, analysis)
        diagnostic = {
            'range': {
                'start': {'line': 1, 'character': 6},
                'end': {'line': 1, 'character': 9},
            },
            'message': 'Unknown function "add".',
            'code': 'unknown-function',
        }

        actions = server._code_actions({
            'textDocument': {'uri': uri},
            'range': diagnostic['range'],
            'context': {'diagnostics': [diagnostic]},
        })

        action = next(item for item in actions if item['title'].startswith('Import'))
        self.assertEqual(
            'import math.{add};\n',
            action['edit']['documentChanges'][0]['edits'][0]['newText'],
        )

    def test_rename_rejects_stale_open_document(self):
        entry = self.write(
            'main.jack',
            'i32 identity(i32 value) { return value; }\nprint(identity(1));\n',
        )
        analysis = self.analyze(entry, versions={entry: 1})
        server, uri = self.server_for(entry, analysis, version=2)

        with self.assertRaisesRegex(ValueError, 'stale'):
            server._rename({
                'textDocument': {'uri': uri},
                'position': {'line': 1, 'character': 8},
                'newName': 'same',
            })

    def test_invalid_and_colliding_renames_are_rejected(self):
        entry = self.write(
            'main.jack',
            'i32 first(i32 value) { return value; }\n'
            'i32 second(i32 value) { return first(value); }\n',
        )
        analysis = self.analyze(entry, versions={entry: 1})
        server, uri = self.server_for(entry, analysis)

        with self.assertRaisesRegex(ValueError, 'valid'):
            server._rename({
                'textDocument': {'uri': uri},
                'position': {'line': 0, 'character': 4},
                'newName': 'while',
            })
        with self.assertRaisesRegex(ValueError, 'conflict'):
            server._rename({
                'textDocument': {'uri': uri},
                'position': {'line': 0, 'character': 4},
                'newName': 'second',
            })

    def test_live_comptime_extern_is_deferred_without_false_error(self):
        entry = self.write(
            'main.jack',
            'comptime extern i32 host_value();\n'
            'comptime i32 value = host_value();\n'
            'print(value);\n',
        )

        analysis = self.analyze(entry)

        self.assertTrue(analysis.deferred_comptime)
        self.assertEqual([], analysis.diagnostics)

    def test_duplicate_module_declarations_are_diagnosed(self):
        first = self.write('one.jack', 'module duplicate;\npub i32 one = 1;\n')
        second = self.write('two.jack', 'module duplicate;\npub i32 two = 2;\n')

        analysis = self.analyze(first)

        diagnostic = next(
            item for item in analysis.diagnostics
            if 'declared by both' in item.message
        )
        self.assertEqual(str(second), diagnostic.span.source_path)

    def test_stale_background_analysis_is_discarded(self):
        entry = self.write('main.jack', 'i32 value = 1;\n')
        first = self.analyze(entry)
        entry.write_text('i32 replacement = 2;\n')
        second = self.analyze(entry)
        server = LanguageServer(None, None, None)
        server.analysis_generation = 2
        stale = Future()
        stale.set_result(first)
        current = Future()
        current.set_result(second)

        server._analysis_finished(1, entry, stale)
        self.assertIsNone(server.semantic_model)
        server._analysis_finished(2, entry, current)

        self.assertIn(
            'replacement', {symbol.name for symbol in server.semantic_model.symbols.values()}
        )

    def test_analysis_cache_reuses_unchanged_snapshot(self):
        entry = self.write('main.jack', 'i32 value = 1;\n')
        analyzer = ProjectAnalyzer([self.root])

        first = analyzer.analyze(entry)
        second = analyzer.analyze(entry)

        self.assertIs(first, second)

    def test_imported_parse_diagnostics_stop_comptime_and_keep_path(self):
        library = self.write('broken.jack', 'module broken;\npub i32 bad = ;\n')
        entry = self.write('main.jack', 'module app;\nimport broken;\n')
        analyzer = ProjectAnalyzer([self.root])

        with patch('jack.lsp_analysis.compile_to_hir') as compile_to_hir:
            analysis = analyzer.analyze(entry)

        self.assertTrue(analysis.syntax_incomplete)
        self.assertFalse(compile_to_hir.called)
        self.assertEqual(str(library), analysis.diagnostics[0].span.source_path)

    def test_unresolved_module_diagnostic_points_to_owning_import(self):
        broken = self.write(
            'sample.jack',
            'module sample;\nimport protocol.frame;\ni32 value = 1;\n',
        )
        entry = self.write('main.jack', 'module app;\ni32 value = 1;\n')

        analysis = self.analyze(entry)

        diagnostic = next(
            item for item in analysis.diagnostics
            if 'Cannot resolve module "protocol.frame"' in item.message
        )
        self.assertEqual(str(broken), diagnostic.span.source_path)
        self.assertEqual(2, diagnostic.span.start_line)

    def test_server_keeps_last_semantic_model_for_syntax_errors(self):
        entry = self.write('main.jack', 'i32 original = 1;\n')
        valid = self.analyze(entry)
        entry.write_text('i32 broken = ;\n')
        invalid = self.analyze(entry)
        server = LanguageServer(None, io.BytesIO(), None)
        server.semantic_model = valid.model
        server.analysis_generation = 1
        completed = Future()
        completed.set_result(invalid)

        server._analysis_finished(1, entry, completed)

        self.assertIs(server.semantic_model, valid.model)


if __name__ == '__main__':
    unittest.main()
