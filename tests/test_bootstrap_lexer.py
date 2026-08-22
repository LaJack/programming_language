import subprocess
import tempfile
import unittest
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter, JackArray
from jack.module_loader import load_source_file
from jack.parser import Lexer


ROOT = Path(__file__).resolve().parents[1]
SELFHOST_ROOT = ROOT / 'selfhost'


def jack_harness(source: str, *, print_count: bool = False) -> str:
    declarations = [
        'import bootstrap.lexer;',
        f'u8[{len(source)}] source;',
    ]
    declarations.extend(
        f'source[{index}] = u8({value});'
        for index, value in enumerate(source.encode('ascii'))
    )
    declarations.extend([
        'Token[128] tokens;',
        'usize count = lex(source[..], tokens[..]);',
    ])
    if print_count:
        declarations.append('print(count);')
    return '\n'.join(declarations)


def expected_token(token):
    kind = {
        'EOF': 0,
        'IDENT': 1,
        'INT': 2,
        'FLOAT': 3,
        'STRING': 4,
    }.get(token.kind, 5)
    return (
        kind,
        token.span.start_offset,
        token.span.end_offset,
        token.span.start_line,
        token.span.start_column,
    )


class BootstrapLexerTests(unittest.TestCase):
    SOURCE = (
        'module sample;\n'
        'pub struct Point { u8 value; } // line comment\n'
        'i32 answer = 12..14; /* block\ncomment */\n'
        'str text = "hello\\nworld"; f32 ratio = 3.5;\n'
    )

    def test_jack_lexer_matches_bootstrap_python_token_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(jack_harness(self.SOURCE))
            interpreter = Interpreter()
            interpreter.eval_source_ast(
                load_source_file(entry, search_roots=[SELFHOST_ROOT])
            )

        count = int(interpreter.global_scope.get('count'))
        tokens = interpreter.global_scope.get('tokens')
        self.assertIsInstance(tokens, JackArray)
        expected = [expected_token(token) for token in Lexer(self.SOURCE).tokenize()]
        actual = [
            (
                int(token.kind),
                int(token.span.start),
                int(token.span.end),
                int(token.span.line),
                int(token.span.column),
            )
            for token in tokens.values[:count]
        ]
        self.assertEqual(expected, actual)

    def test_jack_lexer_compiles_and_runs_with_both_native_backends(self):
        source = 'u8 value = 12;\n'
        expected_count = len(Lexer(source).tokenize())
        for backend in ('c', 'llvm'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                entry = root / 'main.jack'
                output = root / 'lexer-test'
                entry.write_text(jack_harness(source, print_count=True))
                CompilerDriver(print_handler=None).compile_executable(
                    entry,
                    CompilationOptions(
                        backend=backend,
                        output=output,
                        module_roots=(SELFHOST_ROOT,),
                    ),
                )
                completed = subprocess.run(
                    [str(output)], capture_output=True, text=True, check=False
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual(f'count = {expected_count}\n', completed.stdout)


if __name__ == '__main__':
    unittest.main()
