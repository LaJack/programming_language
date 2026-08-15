import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path

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
        server = LanguageServer(None, None, None)
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


if __name__ == '__main__':
    unittest.main()
