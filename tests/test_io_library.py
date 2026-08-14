import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.c_emit_pass import emit_c
from jack.cli import main
from jack.module_loader import load_source_file
from jack.semantic_pass import SemanticError


def jack_string_literal(value: str) -> str:
    escaped = (
        value.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\n', '\\n')
        .replace('\r', '\\r')
        .replace('\t', '\\t')
        .replace('\0', '\\0')
    )
    return f'"{escaped}"'



def io_program(path: Path) -> str:
    return f"""
        import std.io;

        str path = {jack_string_literal(str(path))};
        File file(path);

        u8[4] buffer;
        usize bytes_read = file.read(&inout buffer[..]);
        i32 close_status = file.close();

        print(bytes_read);
        print(buffer[0]);
        print(buffer[1]);
        print(buffer[2]);
        print(buffer[3]);
        print(close_status);
    """


def comptime_io_program(path: Path) -> str:
    return f"""
        import std.io;

        comptime str path = {jack_string_literal(str(path))};
        comptime File file(path);

        comptime u8[4] buffer;
        comptime usize bytes_read = file.read(&inout buffer[..]);
        comptime i32 close_status = file.close();

        print(bytes_read);
        print(buffer[0]);
        print(buffer[1]);
        print(buffer[2]);
        print(buffer[3]);
        print(close_status);
    """


class IoLibraryTests(unittest.TestCase):
    def test_cli_interpreter_reads_file_through_std_io(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = root / 'data.txt'
            data.write_bytes(b'Jack IO')
            source = root / 'main.jack'
            source.write_text(io_program(data))

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual(
            'bytes_read = 4\n'
            'buffer[0] = 74\n'
            'buffer[1] = 97\n'
            'buffer[2] = 99\n'
            'buffer[3] = 107\n'
            'close_status = 0\n',
            output.getvalue(),
        )

    def test_cli_interpreter_runs_comptime_io_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = root / 'data.txt'
            data.write_bytes(b'Jack IO')
            source = root / 'main.jack'
            source.write_text(comptime_io_program(data))

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual(
            'bytes_read = 4\n'
            'buffer[0] = 74\n'
            'buffer[1] = 97\n'
            'buffer[2] = 99\n'
            'buffer[3] = 107\n'
            'close_status = 0\n',
            output.getvalue(),
        )

    def test_cli_interpreter_accepts_legacy_comptime_io_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = root / 'data.txt'
            data.write_bytes(b'Jack IO')
            source = root / 'main.jack'
            source.write_text(comptime_io_program(data))

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['--allow-comptime-io', '-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual(
            'bytes_read = 4\n'
            'buffer[0] = 74\n'
            'buffer[1] = 97\n'
            'buffer[2] = 99\n'
            'buffer[3] = 107\n'
            'close_status = 0\n',
            output.getvalue(),
        )

    def test_stdio_c_symbols_are_private_to_the_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'main.jack'
            source.write_text('''
                import std.io;

                &inout FILE raw_file;
            ''')

            with self.assertRaisesRegex(SemanticError, 'Type "FILE" is private'):
                emit_c(load_source_file(source))

    def test_stdio_file_handle_field_is_private_to_the_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'main.jack'
            source.write_text('''
                import std.io;

                str path = "";
                File file(path);
                print(file.handle);
            ''')

            with self.assertRaisesRegex(SemanticError, 'Field "handle" is private'):
                emit_c(load_source_file(source))

    def test_c_emitter_keeps_std_io_wrappers_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = root / 'data.txt'
            data.write_bytes(b'Jack IO')
            source = root / 'main.jack'
            source.write_text(io_program(data))

            ast = load_source_file(source)

        c_source = emit_c(ast)

        self.assertIn('#include "jack_std_io.h"', c_source)
        self.assertNotIn('FILE *jack_std_io_open_read(jack_str path);', c_source)
        self.assertNotIn('size_t fread(void *data, size_t size, size_t count, FILE *stream);', c_source)
        self.assertNotIn('int32_t fclose(FILE *stream);', c_source)
        self.assertIn('typedef struct std_io_File {', c_source)
        self.assertIn('    FILE *handle;', c_source)
        self.assertIn('void std_io_File_init(std_io_File *self, jack_str path);', c_source)
        self.assertIn('size_t std_io_File_read(std_io_File *self, jack_slice_u8 dst);', c_source)
        self.assertIn('int32_t std_io_File_close(std_io_File *self);', c_source)
        self.assertIn('self->handle = jack_std_io_open_read(path);', c_source)
        self.assertNotIn('char *path_buffer = (char *)malloc(path_len + 1);', c_source)
        self.assertNotIn('FILE *file = fopen(path_buffer, "rb");', c_source)
        self.assertIn('return fread(&(dst).data[0], 1, ((size_t)((dst).len)), self->handle);', c_source)
        self.assertIn('std_io_File_init(&file, path);', c_source)
        self.assertIn('bytes_read = std_io_File_read(&file, (jack_slice_u8){ buffer, 4 });', c_source)


if __name__ == '__main__':
    unittest.main()
