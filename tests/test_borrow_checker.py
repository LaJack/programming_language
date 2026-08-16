import unittest

from jack.parser import parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


def validate_source(source: str):
    return validate_runtime_ast(parse(source))


class BorrowCheckerTests(unittest.TestCase):
    def test_allows_multiple_live_read_borrows(self):
        ast = validate_source('''
            i32 value = 1;
            &in i32 first = &in value;
            &in i32 second = &in value;
        ''')

        self.assertTrue(ast)

    def test_rejects_overlapping_live_writable_borrows(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &inout borrow'):
            validate_source('''
                i32 value = 1;
                &inout i32 first = &inout value;
                &inout i32 second = &inout value;
            ''')

    def test_rejects_write_while_read_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                i32 value = 1;
                &in i32 reader = &in value;
                value = 2;
            ''')

    def test_rejects_variable_read_while_out_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &out borrow'):
            validate_source('''
                i32 value = 1;
                &out i32 writer = &out value;
                i32 copy = value;
            ''')

    def test_rejects_print_while_inout_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &inout borrow'):
            validate_source('''
                i32 value = 1;
                &inout i32 writer = &inout value;
                print(value);
            ''')

    def test_rejects_return_while_out_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'while it is borrowed'):
            validate_source('''
                i32 read() {
                    i32 value = 1;
                    &out i32 writer = &out value;
                    return value;
                }
            ''')

    def test_rejects_non_borrow_argument_read_while_out_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &out borrow'):
            validate_source('''
                void use(i32 value) {
                }

                i32 value = 1;
                &out i32 writer = &out value;
                use(value);
            ''')

    def test_rejects_index_read_while_inout_slice_borrow_is_live(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &inout borrow'):
            validate_source('''
                u8[2] buffer;
                &inout u8[] window = &inout buffer[..];
                u8 first = buffer[0];
            ''')

    def test_allows_read_through_inout_borrow_owner(self):
        ast = validate_source('''
            u8[2] buffer;
            &inout u8[] window = &inout buffer[..];
            u8 first = window[0];
        ''')

        self.assertTrue(ast)

    def test_allows_write_after_nested_borrow_scope_ends(self):
        ast = validate_source('''
            i32 value = 1;
            if (true) {
                &in i32 reader = &in value;
            }
            value = 2;
        ''')

        self.assertTrue(ast)

    def test_allows_disjoint_field_borrows(self):
        ast = validate_source('''
            struct Pair {
                i32 left;
                i32 right;
            }

            Pair pair;
            &inout i32 left = &inout pair.left;
            &inout i32 right = &inout pair.right;
        ''')

        self.assertTrue(ast)

    def test_rejects_write_through_read_only_struct_borrow(self):
        with self.assertRaisesRegex(SemanticError, 'through &in borrow'):
            validate_source('''
                struct Packet {
                    i32 header;
                }

                void update(&in Packet packet) {
                    packet.header = 1;
                }
            ''')

    def test_rejects_field_borrow_overlapping_whole_value_borrow(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &inout borrow'):
            validate_source('''
                struct Pair {
                    i32 left;
                    i32 right;
                }

                Pair pair;
                &inout i32 left = &inout pair.left;
                &inout Pair whole = &inout pair;
            ''')

    def test_rejects_conflicting_temporary_call_borrows(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps temporary &in borrow'):
            validate_source('''
                void use(&in i32 read, &out i32 write) {
                    write = read;
                }

                i32 value = 1;
                use(value, value);
            ''')

    def test_allows_non_conflicting_temporary_call_borrows(self):
        ast = validate_source('''
            void use(&in i32 left, &in i32 right) {
            }

            i32 value = 1;
            use(value, value);
        ''')

        self.assertTrue(ast)

    def test_reborrow_blocks_writes_through_original_borrow_owner(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                u8[2] buffer;
                &inout u8[] window = &inout buffer[..];
                &in u8[] reader = &in window;
                window[0] = 1;
            ''')

    def test_allows_disjoint_view_borrows(self):
        ast = validate_source('''
            struct Packet {
                i32 header;
                i32 payload;
            }

            view HeaderView {
                inout i32 header;
            }

            view PayloadView {
                inout i32 payload;
            }

            Packet packet;
            &inout HeaderView header = &inout packet;
            &inout PayloadView payload = &inout packet;
        ''')

        self.assertTrue(ast)

    def test_rejects_overlapping_view_borrows(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                struct Packet {
                    i32 header;
                    i32 payload;
                }

                view HeaderRead {
                    in i32 header;
                }

                view HeaderWrite {
                    out i32 header;
                }

                Packet packet;
                &inout HeaderRead read = &inout packet;
                &inout HeaderWrite write = &inout packet;
            ''')

    def test_allows_returning_borrowed_parameter(self):
        ast = validate_source('''
            &in i32 identity(&in i32 value) {
                return value;
            }
        ''')

        self.assertTrue(ast)

    def test_allows_returning_borrow_derived_from_borrowed_parameter(self):
        ast = validate_source('''
            struct Packet {
                i32 header;
            }

            &in i32 header(&in Packet packet) {
                return &in packet.header;
            }
        ''')

        self.assertTrue(ast)

    def test_allows_returning_borrow_derived_from_self(self):
        ast = validate_source('''
            struct Packet {
                i32 header;

                &in i32 header_ref(&inout self) {
                    return &in self.header;
                }
            }
        ''')

        self.assertTrue(ast)

    def test_allows_returning_borrow_derived_from_global(self):
        ast = validate_source('''
            i32 global;

            &in i32 global_ref() {
                return &in global;
            }
        ''')

        self.assertTrue(ast)

    def test_rejects_returning_borrow_of_local_value(self):
        with self.assertRaisesRegex(SemanticError, 'Cannot return borrow of local value "value"'):
            validate_source('''
                &in i32 bad() {
                    i32 value = 1;
                    return &in value;
                }
            ''')

    def test_rejects_returning_local_borrow_variable_to_local_value(self):
        with self.assertRaisesRegex(SemanticError, 'Cannot return borrow of local value "value"'):
            validate_source('''
                &in i32 bad() {
                    i32 value = 1;
                    &in i32 ref = &in value;
                    return ref;
                }
            ''')

    def test_rejects_returning_borrow_of_by_value_parameter(self):
        with self.assertRaisesRegex(SemanticError, 'Cannot return borrow of local value "value"'):
            validate_source('''
                &in i32 bad(i32 value) {
                    return &in value;
                }
            ''')

    def test_rejects_returning_borrow_with_unknown_origin(self):
        with self.assertRaisesRegex(SemanticError, 'origin is unknown'):
            validate_source('''
                extern "c" type FILE;
                extern "c" &inout FILE open_handle();

                &inout FILE bad() {
                    return open_handle();
                }
            ''')

    def test_rejects_view_values(self):
        with self.assertRaisesRegex(SemanticError, 'can only be used behind an explicit borrow type'):
            validate_source('''
                view HeaderView {
                    in i32 header;
                }

                HeaderView header;
            ''')

    def test_rejects_non_inout_view_borrow_type(self):
        with self.assertRaisesRegex(SemanticError, 'must use &inout'):
            validate_source('''
                struct Packet {
                    i32 header;
                }

                view HeaderView {
                    in i32 header;
                }

                Packet packet;
                &in HeaderView header = &in packet;
            ''')

    def test_tracks_borrow_returned_from_function_call(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                &in i32 identity(&in i32 value) {
                    return value;
                }

                i32 value = 1;
                &in i32 ref = identity(value);
                value = 2;
            ''')

    def test_tracks_all_possible_borrow_return_origins_from_function_call(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                &in i32 pick(bool flag, &in i32 left, &in i32 right) {
                    if (flag) {
                        return left;
                    }
                    else {
                        return right;
                    }
                }

                i32 left = 1;
                i32 right = 2;
                &in i32 ref = pick(true, left, right);
                right = 3;
            ''')

    def test_tracks_borrow_returned_from_method_call(self):
        with self.assertRaisesRegex(SemanticError, 'overlaps live &in borrow'):
            validate_source('''
                struct Packet {
                    i32 header;

                    &in i32 header_ref(&inout self) {
                        return &in self.header;
                    }
                }

                Packet packet;
                &in i32 header = packet.header_ref();
                packet.header = 1;
            ''')

    def test_allows_returning_borrow_returned_from_function_call(self):
        ast = validate_source('''
            &in i32 outer(&in i32 value) {
                return identity(value);
            }

            &in i32 identity(&in i32 value) {
                return value;
            }
        ''')

        self.assertTrue(ast)

    def test_allows_reading_in_borrows_in_value_expressions(self):
        ast = validate_source('''
            &in u8 max(&in u8 a, &in u8 b) {
                if (a > b) {
                    return a;
                }
                else {
                    return b;
                }
            }

            u8 pick(&in u8 a, &in u8 b) {
                return max(a, b);
            }
        ''')

        self.assertTrue(ast)

    def test_rejects_reading_out_borrow_as_value(self):
        with self.assertRaisesRegex(SemanticError, 'write-only borrow'):
            validate_source('''
                void bad(&out u8 value) {
                    u8 copy = value;
                }
            ''')



if __name__ == '__main__':
    unittest.main()
