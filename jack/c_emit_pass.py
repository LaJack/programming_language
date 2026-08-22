import copy
from dataclasses import fields, is_dataclass
from typing import Callable, Iterable

try:
    from .borrow_modes import borrow_mode_can_read, borrow_mode_can_write, borrow_mode_compatible
    from .builtin_types import (
        BUILTIN_TYPE_SPECS,
        is_bool_type,
        is_builtin_type,
        is_raw_byte_type,
    )
    from .ast_nodes import (
        FunctionDeclaration,
        ModuleDeclaration,
        ImportDeclaration,
        Statement,
        TypeDeclaration,
        TypeReference,
        VariableDeclaration,
        ViewDeclaration,
    )
    from .compile_time_pass import apply_compile_time_pass
    from .cleanup_lowering_pass import lower_hir_static_cleanups
    from .hir_lowering_pass import lower_to_hir
    from .hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDereferenceExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRFormattedStringExpression,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRMoveExpression,
        HIRPointerCastExpression,
        HIRPointerOffsetExpression,
        HIRPrint,
        HIRProgram,
        HIRRawAddressExpression,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRTry,
        HIRUnsafeBlock,
        HIRStructLiteralExpression,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRWhile,
    )
except ImportError:
    from borrow_modes import borrow_mode_can_read, borrow_mode_can_write, borrow_mode_compatible
    from builtin_types import (
        BUILTIN_TYPE_SPECS,
        is_bool_type,
        is_builtin_type,
        is_raw_byte_type,
    )
    from ast_nodes import (
        FunctionDeclaration,
        ModuleDeclaration,
        ImportDeclaration,
        Statement,
        TypeDeclaration,
        TypeReference,
        VariableDeclaration,
        ViewDeclaration,
    )
    from compile_time_pass import apply_compile_time_pass
    from cleanup_lowering_pass import lower_hir_static_cleanups
    from hir_lowering_pass import lower_to_hir
    from hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDereferenceExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRFormattedStringExpression,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRMoveExpression,
        HIRPointerCastExpression,
        HIRPointerOffsetExpression,
        HIRPrint,
        HIRProgram,
        HIRRawAddressExpression,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRTry,
        HIRUnsafeBlock,
        HIRStructLiteralExpression,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRWhile,
    )


class CEmitError(Exception):
    pass


class CEmitFeatureNotImplemented(CEmitError):
    pass


def emit_hir_c(
    program: HIRProgram, *, debug: bool = False, optimization: int = 0
) -> str:
    return CEmitPass(debug=debug, optimization=optimization).emit_hir(program)


def emit_hir_c_files(
    program: HIRProgram,
    entry_module: str | None = None,
    *,
    debug: bool = False,
    optimization: int = 0,
) -> dict[str, str]:
    return CEmitPass(debug=debug, optimization=optimization).emit_hir_files(
        program, entry_module=entry_module
    )


def emit_c(
    ast: list[Statement],
    print_handler: Callable[[str], None] | None = None,
    externs: dict[str, object] | None = None,
    *,
    debug: bool = False,
    optimization: int = 0,
) -> str:
    return emit_runtime_c(
        apply_compile_time_pass(ast, print_handler=print_handler, externs=externs),
        debug=debug,
        optimization=optimization,
    )


def emit_runtime_c(
    ast: list[Statement], *, debug: bool = False, optimization: int = 0
) -> str:
    runtime_hir = lower_to_hir(ast)
    lowered_hir = lower_hir_static_cleanups(runtime_hir)
    return emit_hir_c(lowered_hir, debug=debug, optimization=optimization)


def emit_c_files(
    ast: list[Statement],
    print_handler: Callable[[str], None] | None = None,
    externs: dict[str, object] | None = None,
    *,
    debug: bool = False,
    optimization: int = 0,
) -> dict[str, str]:
    entry_module = _infer_entry_module(ast)
    runtime_ast = apply_compile_time_pass(ast, print_handler=print_handler, externs=externs)
    return emit_runtime_c_files(
        runtime_ast,
        entry_module=entry_module,
        debug=debug,
        optimization=optimization,
    )


def emit_runtime_c_files(
    ast: list[Statement], entry_module: str | None = None, *,
    debug: bool = False, optimization: int = 0,
) -> dict[str, str]:
    runtime_hir = lower_to_hir(ast)
    lowered_hir = lower_hir_static_cleanups(runtime_hir)
    return emit_hir_c_files(
        lowered_hir,
        entry_module=entry_module,
        debug=debug,
        optimization=optimization,
    )


def _infer_entry_module(ast: list[Statement]) -> str | None:
    for node in reversed(ast):
        if type(node) not in {ModuleDeclaration, ImportDeclaration}:
            module_name = getattr(node, 'module_name', None)
            if module_name is not None:
                return module_name
    return None


class CEmitPass:
    C_KEYWORDS = {
        'auto',
        'bool',
        'break',
        'case',
        'char',
        'const',
        'continue',
        'default',
        'do',
        'double',
        'else',
        'enum',
        'extern',
        'false',
        'float',
        'for',
        'goto',
        'if',
        'inline',
        'int',
        'long',
        'register',
        'restrict',
        'return',
        'short',
        'signed',
        'sizeof',
        'static',
        'struct',
        'switch',
        'true',
        'typedef',
        'union',
        'unsigned',
        'void',
        'volatile',
        'while',
        '_Alignas',
        '_Alignof',
        '_Atomic',
        '_Bool',
        '_Complex',
        '_Generic',
        '_Imaginary',
        '_Noreturn',
        '_Static_assert',
        '_Thread_local',
    }

    RUNTIME_SLICE_ELEMENT_KEYS = frozenset({
        *BUILTIN_TYPE_SPECS.keys(),
        'str',
        'c_char',
        'c_void',
    })
    C_HEADER_DECLARED_FUNCTIONS = frozenset({
        'fclose',
        'fopen',
        'fread',
        'fwrite',
        'jack_std_io_open_read',
    })

    def __init__(self, *, debug: bool = False, optimization: int = 0) -> None:
        self.debug = debug
        self.optimization = optimization
        self.names: dict[str, str] = {}
        runtime_slice_names = {
            f'jack_slice_{key}'
            for key in self.RUNTIME_SLICE_ELEMENT_KEYS
        } | {
            f'jack_in_slice_{key}'
            for key in self.RUNTIME_SLICE_ELEMENT_KEYS
        }
        self.used_names: set[str] = {
            'jack_current_error',
            'jack_end_try',
            'jack_error',
            'jack_error_frame',
            'jack_error_frame_stack',
            'jack_str',
            'jack_str_equal',
            'jack_rethrow',
            'jack_throw',
            'jack_try',
            'JACK_ERROR_OK',
            'main',
            'memcmp',
            'memcpy',
            'printf',
            *runtime_slice_names,
        }
        self.slice_types: dict[tuple[str, bool], str] = {}
        self.type_declarations: dict[str, TypeDeclaration] = {}
        self.view_declarations: dict[str, ViewDeclaration] = {}
        self.function_declarations: dict[str, FunctionDeclaration] = {}
        self.global_variable_types: dict[str, TypeReference] = {}
        self.error_tag_values: dict[str, int] = {}
        self.error_payload_size = 1
        self.current_function_raises = False
        self.current_return_type: TypeReference | None = None
        self.current_caught_error_name: str | None = None
        self.active_try_frames: list[str] = []
        self.error_frame_counter = 0
        self.temporary_counters: dict[str, int] = {}
        self.hir_program: HIRProgram | None = None

    def _hir_read_type(self, expression: HIRExpression) -> TypeReference:
        if expression.read_type is None:
            raise CEmitError('Cannot read through a write-only borrow.')
        return expression.read_type

    def emit_hir(self, program: HIRProgram) -> str:
        self.hir_program = program
        types = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRTypeDeclaration)
        ]
        views = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRViewDeclaration)
        ]
        functions = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        ]
        globals_ = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRGlobalVariable)
        ]
        methods = [
            (type_decl, method)
            for type_decl in types
            for method in type_decl.methods
        ]

        self._reserve_hir_readable_names(program)
        self.type_declarations = {declaration.name: declaration for declaration in types}
        self.view_declarations = {declaration.name: declaration for declaration in views}
        self.function_declarations = {
            declaration.name: declaration for declaration in functions
        }
        self.global_variable_types = {
            declaration.symbol.name: declaration.symbol.type_ref
            for declaration in globals_
        }
        self._assign_hir_error_tag_values(program)
        self.error_payload_size = self._max_error_payload_size()
        self._collect_hir_slice_types(program)

        lines = self._emit_runtime_includes(functions)
        lines.append('')
        lines.extend(self._emit_error_runtime_globals())
        lines.extend(self._emit_error_declarations())
        lines.extend(self._emit_slice_type_declarations())

        if types:
            lines.append('')
            lines.extend(self._emit_type_declaration(declaration) for declaration in types)
        if views:
            lines.append('')
            lines.extend(self._emit_view_declaration(declaration) for declaration in views)
        if globals_:
            lines.append('')
            lines.extend(
                self._emit_hir_global_variable_declaration(declaration)
                for declaration in globals_
            )
        if functions or methods:
            lines.append('')
            lines.extend(
                self._emit_function_prototype(function)
                for function in functions
                if not self._is_c_header_declared_function(function)
            )
            lines.extend(
                self._emit_method_prototype(type_decl, method)
                for type_decl, method in methods
            )
            lines.append('')
            first_definition = True
            for function in functions:
                if function.extern:
                    continue
                if not first_definition:
                    lines.append('')
                lines.extend(self._emit_function_definition(function))
                first_definition = False
            for type_decl, method in methods:
                if not first_definition:
                    lines.append('')
                lines.extend(self._emit_method_definition(type_decl, method))
                first_definition = False

        lines.append('')
        lines.extend(self._emit_hir_main(program.top_level, globals_))
        return '\n'.join(self._compact_blank_lines(lines)) + '\n'

    def _emit_hir_global_variable_declaration(
        self, declaration: HIRGlobalVariable
    ) -> str:
        symbol = declaration.symbol
        declaration_source = self._emit_declaration(
            symbol.type_ref,
            self._mangle(symbol.name),
            self.global_variable_types,
        )
        if symbol.extern:
            if symbol.abi != 'c':
                raise CEmitError(
                    f'Extern variable "{symbol.name}" must use the "c" ABI.'
                )
            line = f'extern {declaration_source};'
        else:
            line = f'{declaration_source};'
        return '\n'.join(self._with_source_directive([line], declaration))

    def _emit_hir_main(
        self,
        top_level: list[HIRStatement],
        globals_: list[HIRGlobalVariable],
    ) -> list[str]:
        lines = ['int main(void) {']
        env = dict(self.global_variable_types)
        body: list[str] = []
        for statement in top_level:
            if isinstance(statement, HIRGlobalVariable):
                body.extend(self._emit_hir_top_level_variable(statement, env))
            elif not isinstance(statement, HIRDeclaration):
                body.extend(self._emit_hir_statement(statement, env))
        body.extend(
            self._emit_deinit_calls(
                [
                    declaration.symbol.name
                    for declaration in globals_
                    if not declaration.symbol.extern
                    and self._has_method(declaration.symbol.type_ref, 'deinit')
                ],
                env,
            )
        )
        body.append('return 0;')
        lines.extend(self._indent(line) for line in body)
        lines.append('}')
        return lines

    def emit_hir_files(
        self,
        program: HIRProgram,
        entry_module: str | None = None,
    ) -> dict[str, str]:
        self.hir_program = program
        entry_module = program.entry_module if entry_module is None else entry_module
        module_order: list[str | None] = []
        for statement in program.top_level:
            module_name = statement.module_name or entry_module
            if module_name not in module_order:
                module_order.append(module_name)
        if entry_module not in module_order:
            module_order.append(entry_module)
        imported_modules = [
            module
            for module in module_order
            if module is not None and module != entry_module
        ]
        module_files = self._module_file_bases(imported_modules)
        module_headers = {
            module: f'{file_base}.h'
            for module, file_base in module_files.items()
        }

        types = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRTypeDeclaration)
        ]
        views = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRViewDeclaration)
        ]
        functions = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        ]
        globals_ = [
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRGlobalVariable)
        ]
        self._reserve_hir_readable_names(program)
        self.type_declarations = {declaration.name: declaration for declaration in types}
        self.view_declarations = {declaration.name: declaration for declaration in views}
        self.function_declarations = {
            declaration.name: declaration for declaration in functions
        }
        self.global_variable_types = {
            declaration.symbol.name: declaration.symbol.type_ref
            for declaration in globals_
        }
        self._assign_hir_error_tag_values(program)
        self.error_payload_size = self._max_error_payload_size()
        self._collect_hir_slice_types(program)

        statements_by_module = {
            module: [
                statement
                for statement in program.top_level
                if (statement.module_name or entry_module) == module
            ]
            for module in module_order
        }
        files: dict[str, str] = {}
        for module in imported_modules:
            statements = statements_by_module[module]
            files[module_headers[module]] = self._emit_hir_module_header(
                module,
                statements,
                module_headers,
                program.module_dependencies.get(module, []),
            )
            files[f'{module_files[module]}.c'] = self._emit_hir_module_source(
                module,
                statements,
                module_headers[module],
            )

        files['main.c'] = self._emit_hir_entry_source(
            statements_by_module.get(entry_module, []),
            imported_modules,
            module_headers,
            statements_by_module,
        )
        return files

    def _hir_module_sections(self, statements: list[HIRStatement]):
        types = [
            statement for statement in statements
            if isinstance(statement, HIRTypeDeclaration)
        ]
        views = [
            statement for statement in statements
            if isinstance(statement, HIRViewDeclaration)
        ]
        functions = [
            statement for statement in statements
            if isinstance(statement, HIRFunctionDeclaration)
        ]
        globals_ = [
            statement for statement in statements
            if isinstance(statement, HIRGlobalVariable)
        ]
        methods = [
            (type_decl, method)
            for type_decl in types
            for method in type_decl.methods
        ]
        runtime = [
            statement for statement in statements
            if isinstance(statement, HIRGlobalVariable)
            or not isinstance(statement, HIRDeclaration)
        ]
        return types, views, functions, globals_, methods, runtime

    def _emit_hir_module_header(
        self,
        module: str,
        statements: list[HIRStatement],
        module_headers: dict[str, str],
        dependencies: list[str],
    ) -> str:
        types, views, functions, globals_, methods, _ = self._hir_module_sections(statements)
        header_types = [declaration for declaration in types if declaration.public]
        header_views = [declaration for declaration in views if declaration.public]
        header_globals = [
            declaration for declaration in globals_ if declaration.symbol.public
        ]
        header_functions = [
            declaration
            for declaration in functions
            if declaration.public and not self._is_c_header_declared_function(declaration)
        ]
        header_methods = [
            (type_decl, method)
            for type_decl, method in methods
            if type_decl.public or method.public
        ]
        guard = self._header_guard(module_headers[module])
        lines = [f'#ifndef {guard}', f'#define {guard}', '']
        lines.extend(self._emit_runtime_includes(functions))
        for dependency in dependencies:
            header = module_headers.get(dependency)
            if header is not None:
                lines.append(f'#include "{header}"')
        lines.append('')
        lines.extend(self._emit_error_declarations())
        lines.extend(
            self._emit_non_empty(
                self._emit_type_declaration(declaration)
                for declaration in header_types
            )
        )
        lines.extend(
            self._emit_non_empty(
                self._emit_view_declaration(declaration)
                for declaration in header_views
            )
        )
        public_api_nodes = [
            *header_globals,
            *header_functions,
            *(method for _, method in header_methods),
        ]
        lines.extend(self._emit_slice_type_declarations_for(public_api_nodes))
        lines.extend(
            self._emit_hir_global_variable_extern_declaration(declaration)
            for declaration in header_globals
        )
        lines.extend(
            self._emit_function_prototype(declaration)
            for declaration in header_functions
        )
        lines.extend(
            self._emit_method_prototype(type_decl, method)
            for type_decl, method in header_methods
        )
        if self._hir_module_needs_init(statements):
            lines.append(f'void {self._module_init_function_name(module)}(void);')
        if self._hir_module_needs_deinit(statements):
            lines.append(f'void {self._module_deinit_function_name(module)}(void);')
        lines.extend(['', '#endif'])
        return '\n'.join(self._compact_blank_lines(lines)) + '\n'

    def _emit_hir_module_source(
        self,
        module: str,
        statements: list[HIRStatement],
        header_name: str,
    ) -> str:
        types, views, functions, globals_, methods, runtime = self._hir_module_sections(statements)
        source_types = [declaration for declaration in types if not declaration.public]
        source_views = [declaration for declaration in views if not declaration.public]
        source_functions = [
            declaration
            for declaration in functions
            if not declaration.public and not self._is_c_header_declared_function(declaration)
        ]
        private_methods = [
            (type_decl, method)
            for type_decl, method in methods
            if not (type_decl.public or method.public)
        ]
        lines = [f'#include "{header_name}"', '']
        lines.extend(self._emit_error_declarations())
        lines.extend(
            self._emit_non_empty(
                self._emit_type_declaration(declaration)
                for declaration in source_types
            )
        )
        lines.extend(
            self._emit_non_empty(
                self._emit_view_declaration(declaration)
                for declaration in source_views
            )
        )
        if source_functions or private_methods:
            if lines[-1] != '':
                lines.append('')
            lines.extend(
                self._emit_function_prototype(
                    declaration,
                    static=not declaration.public and not declaration.extern,
                )
                for declaration in source_functions
            )
            lines.extend(
                self._emit_method_prototype(
                    type_decl,
                    method,
                    static=not (type_decl.public or method.public),
                )
                for type_decl, method in private_methods
            )
        lines.extend(
            self._emit_hir_global_variable_extern_declaration(declaration)
            for declaration in globals_
            if declaration.symbol.extern and not declaration.symbol.public
        )
        lines.extend(
            self._emit_non_empty(
                self._emit_hir_global_variable_definition(
                    declaration,
                    static=not declaration.symbol.public
                    and not declaration.symbol.extern,
                )
                for declaration in globals_
            )
        )

        first_definition = not any(
            line for line in lines if line and not line.startswith('#include')
        )
        for function in functions:
            if function.extern:
                continue
            if not first_definition:
                lines.append('')
            lines.extend(
                self._emit_function_definition(
                    function,
                    static=not function.public and not function.extern,
                )
            )
            first_definition = False
        for type_decl, method in methods:
            if not first_definition:
                lines.append('')
            lines.extend(
                self._emit_method_definition(
                    type_decl,
                    method,
                    static=not (type_decl.public or method.public),
                )
            )
            first_definition = False
        if self._hir_module_needs_init(statements):
            if not first_definition:
                lines.append('')
            lines.extend(self._emit_hir_module_init(module, runtime))
            first_definition = False
        if self._hir_module_needs_deinit(statements):
            if not first_definition:
                lines.append('')
            lines.extend(self._emit_hir_module_deinit(module, globals_))
        return '\n'.join(self._compact_blank_lines(lines)) + '\n'

    def _emit_hir_entry_source(
        self,
        statements: list[HIRStatement],
        imported_modules: list[str],
        module_headers: dict[str, str],
        statements_by_module: dict[str | None, list[HIRStatement]],
    ) -> str:
        types, views, functions, globals_, methods, runtime = self._hir_module_sections(statements)
        lines = self._emit_runtime_includes(functions)
        lines.extend(
            f'#include "{module_headers[module]}"'
            for module in imported_modules
        )
        lines.append('')
        lines.extend(self._emit_error_runtime_globals())
        lines.extend(self._emit_error_declarations())
        lines.extend(self._emit_slice_type_declarations())
        if types:
            lines.append('')
            lines.extend(self._emit_type_declaration(declaration) for declaration in types)
        if views:
            lines.append('')
            lines.extend(self._emit_view_declaration(declaration) for declaration in views)
        if globals_:
            lines.append('')
            lines.extend(
                self._emit_hir_global_variable_declaration(declaration)
                for declaration in globals_
            )
        if functions or methods:
            lines.append('')
            lines.extend(
                self._emit_function_prototype(
                    function,
                    static=not function.public and not function.extern,
                )
                for function in functions
                if not self._is_c_header_declared_function(function)
            )
            lines.extend(
                self._emit_method_prototype(
                    type_decl,
                    method,
                    static=not (type_decl.public or method.public),
                )
                for type_decl, method in methods
            )
            lines.append('')
            first_definition = True
            for function in functions:
                if function.extern:
                    continue
                if not first_definition:
                    lines.append('')
                lines.extend(
                    self._emit_function_definition(
                        function,
                        static=not function.public and not function.extern,
                    )
                )
                first_definition = False
            for type_decl, method in methods:
                if not first_definition:
                    lines.append('')
                lines.extend(
                    self._emit_method_definition(
                        type_decl,
                        method,
                        static=not (type_decl.public or method.public),
                    )
                )
                first_definition = False
        lines.append('')
        lines.extend(
            self._emit_hir_split_main(
                runtime,
                globals_,
                imported_modules,
                statements_by_module,
            )
        )
        return '\n'.join(self._compact_blank_lines(lines)) + '\n'

    def _emit_hir_split_main(
        self,
        runtime: list[HIRStatement],
        globals_: list[HIRGlobalVariable],
        imported_modules: list[str],
        statements_by_module: dict[str | None, list[HIRStatement]],
    ) -> list[str]:
        lines = ['int main(void) {']
        env = dict(self.global_variable_types)
        body: list[str] = []
        for module in imported_modules:
            if self._hir_module_needs_init(statements_by_module[module]):
                body.append(f'{self._module_init_function_name(module)}();')
        for statement in runtime:
            if isinstance(statement, HIRGlobalVariable):
                body.extend(self._emit_hir_top_level_variable(statement, env))
            else:
                body.extend(self._emit_hir_statement(statement, env))
        body.extend(
            self._emit_deinit_calls(
                self._hir_deinit_global_names(globals_),
                env,
            )
        )
        for module in reversed(imported_modules):
            if self._hir_module_needs_deinit(statements_by_module[module]):
                body.append(f'{self._module_deinit_function_name(module)}();')
        body.append('return 0;')
        lines.extend(self._indent(line) for line in body)
        lines.append('}')
        return lines

    def _emit_hir_module_init(
        self, module: str, runtime: list[HIRStatement]
    ) -> list[str]:
        lines = [f'void {self._module_init_function_name(module)}(void) {{']
        env = dict(self.global_variable_types)
        body: list[str] = []
        for statement in runtime:
            if isinstance(statement, HIRGlobalVariable):
                body.extend(self._emit_hir_top_level_variable(statement, env))
            else:
                body.extend(self._emit_hir_statement(statement, env))
        lines.extend(self._indent(line) for line in body)
        lines.append('}')
        return lines

    def _emit_hir_module_deinit(
        self, module: str, globals_: list[HIRGlobalVariable]
    ) -> list[str]:
        lines = [f'void {self._module_deinit_function_name(module)}(void) {{']
        body = self._emit_deinit_calls(
            self._hir_deinit_global_names(globals_),
            dict(self.global_variable_types),
        )
        lines.extend(self._indent(line) for line in body)
        lines.append('}')
        return lines

    def _emit_hir_global_variable_extern_declaration(
        self, declaration: HIRGlobalVariable
    ) -> str:
        symbol = declaration.symbol
        declaration_source = self._emit_declaration(
            symbol.type_ref,
            self._mangle(symbol.name),
            self.global_variable_types,
        )
        return '\n'.join(self._with_source_directive(
            [f'extern {declaration_source};'], declaration
        ))

    def _emit_hir_global_variable_definition(
        self,
        declaration: HIRGlobalVariable,
        static: bool = False,
    ) -> str:
        symbol = declaration.symbol
        if symbol.extern:
            return ''
        prefix = 'static ' if static else ''
        line = prefix + self._emit_declaration(
            symbol.type_ref,
            self._mangle(symbol.name),
            self.global_variable_types,
        ) + ';'
        return '\n'.join(self._with_source_directive([line], declaration))

    def _hir_module_needs_init(self, statements: list[HIRStatement]) -> bool:
        for statement in statements:
            if isinstance(statement, HIRGlobalVariable):
                if (
                    not statement.symbol.extern
                    and (
                        statement.initializer is not None
                        or statement.constructor_call is not None
                    )
                ):
                    return True
            elif not isinstance(statement, HIRDeclaration):
                return True
        return False

    def _hir_module_needs_deinit(self, statements: list[HIRStatement]) -> bool:
        return bool(
            self._hir_deinit_global_names(
                [
                    statement
                    for statement in statements
                    if isinstance(statement, HIRGlobalVariable)
                ]
            )
        )

    def _hir_deinit_global_names(
        self, globals_: list[HIRGlobalVariable]
    ) -> list[str]:
        return [
            declaration.symbol.name
            for declaration in globals_
            if not declaration.symbol.extern
            and self._has_method(declaration.symbol.type_ref, 'deinit')
        ]





    def _module_file_bases(self, modules: list[str]) -> dict[str, str]:
        used = {'main', 'jack_runtime', 'jack_std_io'}
        bases: dict[str, str] = {}
        for module in modules:
            base = self._sanitize_identifier(module).lower()
            if not base:
                base = 'module'
            candidate = base
            counter = 2
            while candidate in used:
                candidate = f'{base}_{counter}'
                counter += 1
            used.add(candidate)
            bases[module] = candidate
        return bases





    def _emit_runtime_includes(self, functions: list[FunctionDeclaration]) -> list[str]:
        lines = [
            '#ifndef JACK_ERROR_PAYLOAD_SIZE',
            f'#define JACK_ERROR_PAYLOAD_SIZE {self.error_payload_size}',
            '#endif',
            '#include "jack_runtime.h"',
        ]
        if self._needs_std_io_runtime_header(functions):
            lines.append('#include "jack_std_io.h"')
        return lines


















    def _module_init_function_name(self, module: str) -> str:
        return self._mangle(f'{module}$init')

    def _module_deinit_function_name(self, module: str) -> str:
        return self._mangle(f'{module}$deinit')

    def _header_guard(self, header_name: str) -> str:
        return f'JACK_GENERATED_{self._sanitize_identifier(header_name).upper()}_'

    def _emit_non_empty(self, lines: Iterable[str]) -> list[str]:
        return [line for line in lines if line]

    def _compact_blank_lines(self, lines: list[str]) -> list[str]:
        compacted: list[str] = []
        previous_blank = False
        for line in lines:
            blank = line == ''
            if blank and previous_blank:
                continue
            compacted.append(line)
            previous_blank = blank
        return compacted


    def _assign_hir_error_tag_values(self, program: HIRProgram) -> None:
        names: list[str] = []
        for node in self._walk_hir(program):
            error_types: Iterable[TypeReference] = []
            if isinstance(node, HIRFunctionDeclaration):
                error_types = node.raises
            elif isinstance(node, HIRCatchClause):
                error_types = [node.error_type]
            elif isinstance(node, HIRRaise):
                error_types = [node.error_type]
            elif isinstance(node, HIRCallExpression):
                error_types = node.target.raises
            for error_type in error_types:
                type_name = self._type_name(error_type)
                if type_name not in names:
                    names.append(type_name)

        self.error_tag_values = {
            type_name: index
            for index, type_name in enumerate(names, start=1)
        }



    def _max_error_payload_size(self) -> int:
        sizes = [
            self._layout_of_type(TypeReference(type_name), set())[0]
            for type_name in self.error_tag_values
        ]
        return max([1, *sizes])

    def _layout_of_type(
        self, type_ref: TypeReference, seen: set[str]
    ) -> tuple[int, int]:
        if self._is_borrow_type(type_ref) and self._is_slice_type(type_ref):
            return self._aggregate_layout([self._pointer_layout(), self._builtin_layout('i32')])
        if self._is_borrow_type(type_ref):
            return self._pointer_layout()
        if self._is_slice_type(type_ref):
            return self._aggregate_layout([self._pointer_layout(), self._builtin_layout('i32')])
        if self._is_array_type(type_ref):
            count = self._layout_array_count(type_ref.array_size)
            element_size, element_align = self._layout_of_type(self._element_type(type_ref), seen)
            return element_size * count, element_align

        type_name = self._type_name(type_ref)
        if is_builtin_type(type_name):
            return self._builtin_layout(type_name)
        if type_name == 'str':
            return self._aggregate_layout([self._pointer_layout(), self._builtin_layout('i32')])
        if type_name == 'c_char':
            return 1, 1
        if type_name in {'void', 'c_void', 'type'}:
            raise CEmitError(f'Cannot compute error payload layout for type "{type_name}".')

        declaration = self.type_declarations.get(type_name)
        if declaration is None or declaration.extern or getattr(declaration, 'parameters', []):
            raise CEmitError(f'Cannot compute error payload layout for type "{type_name}".')
        if type_name in seen:
            return 1, 1
        seen.add(type_name)
        if not declaration.fields:
            return 1, 1
        return self._aggregate_layout(
            self._layout_of_type(field.type, set(seen))
            for field in declaration.fields
        )

    def _layout_array_count(self, extent: object | None) -> int:
        if type(extent) is not int:
            raise CEmitError('Error payload array sizes must be constant in HIR.')
        value = extent
        if value < 0:
            raise CEmitError('Error payload array sizes must be non-negative.')
        return value

    def _builtin_layout(self, type_name: str) -> tuple[int, int]:
        spec = BUILTIN_TYPE_SPECS[type_name]
        size = max(1, (spec.bits + 7) // 8)
        return size, size

    def _pointer_layout(self) -> tuple[int, int]:
        return self._builtin_layout('usize')

    def _aggregate_layout(self, fields: Iterable[tuple[int, int]]) -> tuple[int, int]:
        offset = 0
        aggregate_align = 1
        has_fields = False
        for size, align in fields:
            has_fields = True
            offset = self._round_up(offset, align)
            offset += size
            aggregate_align = max(aggregate_align, align)
        if not has_fields:
            return 1, 1
        return self._round_up(offset, aggregate_align), aggregate_align

    def _round_up(self, value: int, alignment: int) -> int:
        if alignment <= 0:
            raise CEmitError(f'Invalid alignment {alignment}.')
        return ((value + alignment - 1) // alignment) * alignment

    def _emit_error_declarations(self) -> list[str]:
        lines: list[str] = []
        if self.error_tag_values:
            lines.extend([
                '#ifndef JACK_ERROR_OK',
                '#define JACK_ERROR_OK 0',
                '#endif',
            ])
        for type_name, value in sorted(self.error_tag_values.items(), key=lambda item: item[1]):
            tag_name = self._error_tag_name(type_name)
            lines.extend([
                f'#ifndef {tag_name}',
                f'#define {tag_name} {value}',
                '#endif',
            ])
        if lines:
            lines.append('')
        return lines

    def _error_tag_name(self, error_name: str) -> str:
        return self._mangle(f'{error_name}$error_tag')

    def _emit_error_runtime_globals(self) -> list[str]:
        return [
            'jack_error_frame *jack_error_frame_stack = NULL;',
            'jack_error jack_current_error = {JACK_ERROR_OK, {0}};',
            '',
        ]

    def _next_error_frame_name(self) -> str:
        self.error_frame_counter += 1
        return self._mangle(f'$error_frame${self.error_frame_counter}')

    def _next_temporary_name(self, purpose: str) -> str:
        next_value = self.temporary_counters.get(purpose, 0) + 1
        self.temporary_counters[purpose] = next_value
        return self._mangle(f'${purpose}${next_value}')

    def _function_raises(self, declaration: FunctionDeclaration) -> bool:
        return bool(declaration.raises)

    def _emit_type_declaration(self, declaration: TypeDeclaration) -> str:
        self._ensure_runtime_statement(declaration)
        if declaration.extern:
            if declaration.abi != 'c':
                raise CEmitError(f'Extern type "{declaration.name}" must use the "c" ABI.')
            return ''
        if declaration.parameters:
            raise CEmitError(f'Generic type "{declaration.name}" reached C emission.')

        lines = [f'typedef struct {self._mangle(declaration.name)} {{']
        if not declaration.fields:
            lines.append('    uint8_t jack_empty;')
        for field in declaration.fields:
            self._ensure_runtime_statement(field)
            lines.append(f'    {self._emit_declaration(field.type, self._mangle(field.name), dict())};')
        lines.append(f'}} {self._mangle(declaration.name)};')
        return '\n'.join(self._with_source_directive(lines, declaration))

    def _emit_view_declaration(self, declaration: ViewDeclaration) -> str:
        self._ensure_runtime_statement(declaration)
        lines = [f'typedef struct {self._mangle(declaration.name)} {{']
        if not declaration.fields:
            lines.append('    uint8_t jack_empty;')
        for field in declaration.fields:
            lines.append(f'    {self._emit_view_field_declaration(field)};')
        lines.append(f'}} {self._mangle(declaration.name)};')
        return '\n'.join(self._with_source_directive(lines, declaration))

    def _emit_view_field_declaration(self, field) -> str:
        field_type = TypeReference(
            field.type.name,
            list(field.type.arguments),
            array_size=field.type.array_size,
            is_slice=field.type.is_slice,
            borrow=field.mode,
        )
        return self._emit_declaration(field_type, self._mangle(field.name), dict())


    def _emit_function_prototype(
        self, declaration: FunctionDeclaration, static: bool = False
    ) -> str:
        self._ensure_runtime_statement(declaration)
        prefix = 'static ' if static else ''
        lines = [f'{prefix}{self._emit_function_signature(declaration)};']
        return '\n'.join(self._with_source_directive(lines, declaration))

    def _emit_method_prototype(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration, static: bool = False
    ) -> str:
        self._ensure_runtime_statement(method)
        prefix = 'static ' if static else ''
        lines = [f'{prefix}{self._emit_method_signature(type_decl, method)};']
        return '\n'.join(self._with_source_directive(lines, method))

    def _emit_function_definition(
        self, declaration: HIRFunctionDeclaration, static: bool = False
    ) -> list[str]:
        self._ensure_runtime_statement(declaration)
        if declaration.extern:
            raise CEmitError(f'Extern function "{declaration.name}" cannot be emitted as a definition.')
        return_type = declaration.return_type
        env = {parameter.name: parameter.type for parameter in declaration.parameters}
        prefix = 'static ' if static else ''
        previous_raises = self.current_function_raises
        previous_return_type = self.current_return_type
        self.current_function_raises = self._function_raises(declaration)
        self.current_return_type = return_type
        try:
            lines = [f'{prefix}{self._emit_function_signature(declaration)} {{']
            lines.extend(
                self._indent(line)
                for line in self._emit_hir_block(declaration.body, env)
            )
            has_direct_return = any(
                isinstance(statement, HIRReturn)
                for statement in declaration.body
            )
            if (
                not self._is_void_type(return_type)
                and not has_direct_return
            ):
                lines.append(self._indent(f'return {self._return_zero_value(return_type)};'))
            lines.append('}')
            return self._with_source_directive(lines, declaration)
        finally:
            self.current_function_raises = previous_raises
            self.current_return_type = previous_return_type

    def _needs_std_io_runtime_header(self, functions: list[FunctionDeclaration]) -> bool:
        return any(
            function.extern
            and function.abi == 'c'
            and function.name == 'jack_std_io_open_read'
            for function in functions
        )

    def _is_c_header_declared_function(self, declaration: FunctionDeclaration) -> bool:
        return (
            declaration.extern
            and declaration.abi == 'c'
            and declaration.name in self.C_HEADER_DECLARED_FUNCTIONS
        )

    def _emit_method_definition(
        self, type_decl: HIRTypeDeclaration, method: HIRFunctionDeclaration,
        static: bool = False,
    ) -> list[str]:
        self._ensure_runtime_statement(method)
        return_type = method.return_type
        env = {'self': self._method_self_type(type_decl, method)}
        env.update({parameter.name: parameter.type for parameter in method.parameters})
        prefix = 'static ' if static else ''
        previous_raises = self.current_function_raises
        previous_return_type = self.current_return_type
        self.current_function_raises = self._function_raises(method)
        self.current_return_type = return_type
        try:
            lines = [f'{prefix}{self._emit_method_signature(type_decl, method)} {{']
            lines.extend(
                self._indent(line)
                for line in self._emit_hir_block(method.body, env)
            )
            has_direct_return = any(
                isinstance(statement, HIRReturn)
                for statement in method.body
            )
            if (
                not self._is_void_type(return_type)
                and not has_direct_return
            ):
                lines.append(self._indent(f'return {self._return_zero_value(return_type)};'))
            lines.append('}')
            return self._with_source_directive(lines, method)
        finally:
            self.current_function_raises = previous_raises
            self.current_return_type = previous_return_type

    def _emit_function_signature(self, declaration: FunctionDeclaration) -> str:
        parameters = ', '.join(
            self._emit_parameter_declaration(parameter)
            for parameter in declaration.parameters
        )
        if not parameters:
            parameters = 'void'
        declarator = f'{self._mangle(declaration.name)}({parameters})'
        return self._emit_declaration(declaration.return_type, declarator, dict())

    def _emit_method_signature(self, type_decl: TypeDeclaration, method: FunctionDeclaration) -> str:
        parameters = [self._emit_parameter_declaration(self._method_self_parameter(type_decl, method))]
        parameters.extend(
            self._emit_parameter_declaration(parameter)
            for parameter in method.parameters
        )
        declarator = f'{self._method_function_name(type_decl.name, method.name)}({", ".join(parameters)})'
        return self._emit_declaration(method.return_type, declarator, dict())

    def _method_self_parameter(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> VariableDeclaration:
        if method.self_parameter is None:
            raise CEmitError(f'Method "{type_decl.name}.{method.name}" must declare an explicit self parameter.')
        return method.self_parameter

    def _method_self_type(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> TypeReference:
        parameter_type = self._method_self_parameter(type_decl, method).type
        return TypeReference(
            parameter_type.name,
            list(parameter_type.arguments),
            array_size=parameter_type.array_size,
            is_slice=parameter_type.is_slice,
            borrow=parameter_type.borrow,
        )





    def _emit_hir_top_level_variable(
        self, statement: HIRGlobalVariable, env: dict[str, TypeReference]
    ) -> list[str]:
        symbol = statement.symbol
        env[symbol.name] = symbol.type_ref
        if symbol.extern:
            return []
        if statement.constructor_call is not None:
            if self._hir_call_raises(statement.constructor_call):
                raise CEmitFeatureNotImplemented(
                    'C emission for raising constructors is not implemented yet.'
                )
            return self._with_source_directive(
                [f'{self._emit_hir_call(statement.constructor_call, env)};'],
                statement,
            )
        if statement.initializer is None:
            return []
        if symbol.type_ref.array_size is not None:
            target = self._mangle(symbol.name)
            source = self._emit_hir_expression(statement.initializer, env)
            return self._with_source_directive(
                [f'__builtin_memmove({target}, {source}, sizeof({target}));'],
                statement,
            )
        return self._with_source_directive([
            f'{self._mangle(symbol.name)} = '
            f'{self._emit_hir_expression_as_type(statement.initializer, symbol.type_ref, env)};'
        ], statement)

    def _emit_hir_statement(
        self, statement: HIRStatement, env: dict[str, TypeReference]
    ) -> list[str]:
        return self._with_source_directive(
            self._emit_hir_statement_body(statement, env), statement
        )

    def _emit_hir_statement_body(
        self, statement: HIRStatement, env: dict[str, TypeReference]
    ) -> list[str]:
        if isinstance(statement, HIRVariableDeclaration):
            return self._emit_hir_variable_declaration(statement, env)
        if isinstance(statement, HIRAssignment):
            return self._emit_hir_assignment(statement, env)
        if isinstance(statement, HIRExpressionStatement):
            return self._emit_hir_expression_statement(statement, env)
        if isinstance(statement, HIRReturn):
            return self._emit_hir_return_statement(statement, env)
        if isinstance(statement, HIRRaise):
            return self._emit_hir_raise_statement(statement, env)
        if isinstance(statement, HIRRethrow):
            return self._emit_rethrow_statement()
        if isinstance(statement, HIRPrint):
            return [self._emit_hir_print(statement, env)]
        if isinstance(statement, HIRIf):
            return self._emit_hir_if(statement, env)
        if isinstance(statement, HIRWhile):
            return self._emit_hir_while(statement, env)
        if isinstance(statement, HIRFor):
            return self._emit_hir_for(statement, env)
        if isinstance(statement, HIRTry):
            return self._emit_hir_try(statement, env)
        if isinstance(statement, HIRBlock):
            return self._emit_hir_scoped_block(statement, env)
        if isinstance(statement, HIRUnsafeBlock):
            return self._emit_hir_scoped_block(
                HIRBlock(body=statement.body, span=statement.span), env
            )
        raise CEmitError(f'Unknown HIR statement type "{type(statement).__name__}".')

    def _with_source_directive(self, lines: list[str], node: object) -> list[str]:
        if not self.debug or not lines:
            return lines
        span = getattr(node, 'span', None)
        source_path = getattr(span, 'source_path', None)
        if span is None or source_path is None:
            return lines
        escaped = source_path.replace('\\', '\\\\').replace('"', '\\"')
        return [f'#line {span.start_line} "{escaped}"', *lines]

    def _emit_hir_scoped_block(
        self, statement: HIRBlock, env: dict[str, TypeReference]
    ) -> list[str]:
        lines = ['{']
        lines.extend(
            self._indent(line)
            for line in self._emit_hir_block(statement.body, dict(env))
        )
        lines.append('}')
        return lines

    def _emit_hir_block(
        self, statements: list[HIRStatement], env: dict[str, TypeReference]
    ) -> list[str]:
        lines: list[str] = []
        for statement in statements:
            lines.extend(self._emit_hir_statement(statement, env))
            if self._hir_statement_terminates(statement):
                return lines
        return lines

    def _hir_statement_terminates(self, statement: HIRStatement) -> bool:
        if isinstance(statement, HIRBlock):
            return bool(statement.body) and self._hir_statement_terminates(
                statement.body[-1]
            )
        return isinstance(statement, (HIRReturn, HIRRaise, HIRRethrow))

    def _emit_hir_variable_declaration(
        self, statement: HIRVariableDeclaration, env: dict[str, TypeReference]
    ) -> list[str]:
        symbol = statement.symbol
        if symbol.type_ref.array_size is not None and statement.initializer is not None:
            env[symbol.name] = symbol.type_ref
            target = self._mangle(symbol.name)
            source = self._emit_hir_expression(statement.initializer, env)
            lines = [
                f'{self._emit_declaration(symbol.type_ref, target, env)} = {{0}};',
                f'__builtin_memmove({target}, {source}, sizeof({target}));',
            ]
            if statement.constructor_call is not None:
                lines.append(f'{self._emit_hir_call(statement.constructor_call, env)};')
            return lines
        initializer = (
            self._emit_hir_expression_as_type(statement.initializer, symbol.type_ref, env)
            if statement.initializer is not None
            else self._zero_value(symbol.type_ref)
        )
        env[symbol.name] = symbol.type_ref
        lines = [
            f'{self._emit_declaration(symbol.type_ref, self._mangle(symbol.name), env)} = {initializer};'
        ]
        if statement.constructor_call is not None:
            if self._hir_call_raises(statement.constructor_call):
                raise CEmitFeatureNotImplemented(
                    'C emission for raising constructors is not implemented yet.'
                )
            lines.append(f'{self._emit_hir_call(statement.constructor_call, env)};')
        return lines

    def _emit_hir_assignment(
        self, statement: HIRAssignment, env: dict[str, TypeReference]
    ) -> list[str]:
        if statement.target_type.array_size is not None:
            target = self._emit_hir_expression(statement.target, env)
            source = self._emit_hir_expression(statement.expr, env)
            return [f'__builtin_memmove({target}, {source}, sizeof({target}));']
        return [
            f'{self._emit_hir_expression(statement.target, env)} = '
            f'{self._emit_hir_expression_as_type(statement.expr, statement.target_type, env)};'
        ]

    def _emit_hir_expression_statement(
        self, statement: HIRExpressionStatement, env: dict[str, TypeReference]
    ) -> list[str]:
        if isinstance(statement.expr, HIRCallExpression) and self._hir_call_raises(statement.expr):
            return self._emit_hir_raising_call_statement(statement.expr, env)
        return [f'{self._emit_hir_expression(statement.expr, env)};']

    def _emit_hir_return_statement(
        self, statement: HIRReturn, env: dict[str, TypeReference]
    ) -> list[str]:
        if self.current_return_type is None:
            raise CEmitError('Return statement reached C emission outside a function.')
        prefix = self._emit_active_try_frame_exits()
        if statement.expr is None:
            return [*prefix, 'return;']
        if self._is_void_type(self.current_return_type):
            raise CEmitError('Cannot return a value from a void function.')
        return [
            *prefix,
            f'return {self._emit_hir_expression_as_type(statement.expr, self.current_return_type, env)};',
        ]

    def _emit_hir_raise_statement(
        self, statement: HIRRaise, env: dict[str, TypeReference]
    ) -> list[str]:
        if not self.current_function_raises:
            raise CEmitError('raise reached a non-raising C function.')
        error_type = statement.error_type
        error_name = self._type_name(error_type)
        tag_name = self._error_tag_name(error_name)
        temporary_name = self._next_temporary_name('error_payload')
        return [
            f'{self._emit_declaration(error_type, temporary_name, env)} = '
            f'{self._emit_hir_expression_as_type(statement.expr, error_type, env)};',
            f'jack_throw({tag_name}, &{temporary_name}, sizeof({temporary_name}));',
        ]

    def _emit_hir_if(
        self, statement: HIRIf, env: dict[str, TypeReference]
    ) -> list[str]:
        lines: list[str] = []
        first = True
        for branch in statement.branches:
            prefix = 'if' if first else 'else if'
            lines.append(
                f'{prefix} ({self._emit_hir_condition(branch.condition, env, f"{prefix} condition")}) {{'
            )
            lines.extend(
                self._indent(line)
                for line in self._emit_hir_block(branch.body, dict(env))
            )
            lines.append('}')
            first = False

        if statement.else_body is not None:
            lines.append('else {')
            lines.extend(
                self._indent(line)
                for line in self._emit_hir_block(statement.else_body, dict(env))
            )
            lines.append('}')
        return lines

    def _emit_hir_while(
        self, statement: HIRWhile, env: dict[str, TypeReference]
    ) -> list[str]:
        lines = [f'while ({self._emit_hir_condition(statement.condition, env, "while condition")}) {{']
        lines.extend(
            self._indent(line)
            for line in self._emit_hir_block(statement.body, dict(env))
        )
        lines.append('}')
        return lines

    def _emit_hir_for(
        self, statement: HIRFor, env: dict[str, TypeReference]
    ) -> list[str]:
        loop_env = dict(env)
        initializer = self._emit_hir_for_initializer(statement.initializer, loop_env)
        condition = '' if statement.condition is None else self._emit_hir_condition(statement.condition, loop_env, 'for condition')
        update = self._emit_hir_for_update(statement.update, loop_env)
        lines = [f'for ({initializer}; {condition}; {update}) {{']
        lines.extend(
            self._indent(line)
            for line in self._emit_hir_block(statement.body, dict(loop_env))
        )
        lines.append('}')
        return lines

    def _emit_hir_try(
        self, statement: HIRTry, env: dict[str, TypeReference]
    ) -> list[str]:
        frame_name = self._next_error_frame_name()
        caught_name = self._next_temporary_name('caught_error')
        previous_raises = self.current_function_raises
        self.current_function_raises = True
        self.active_try_frames.append(frame_name)
        try:
            body = self._emit_hir_block(statement.body, dict(env))
        finally:
            self.active_try_frames.pop()
            self.current_function_raises = previous_raises

        lines = [
            f'jack_error_frame {frame_name};',
            f'jack_error {caught_name};',
            f'if (jack_try(&{frame_name}) == 0) {{',
        ]
        lines.extend(self._indent(line) for line in body)
        lines.append(self._indent(f'jack_end_try(&{frame_name});'))
        lines.append('} else {')
        lines.append(self._indent(f'{caught_name} = jack_current_error;'))
        lines.append(self._indent(f'jack_end_try(&{frame_name});'))

        previous_caught_error_name = self.current_caught_error_name
        self.current_caught_error_name = caught_name
        try:
            for index, catch in enumerate(statement.catches):
                prefix = 'if' if index == 0 else 'else if'
                catch_env = dict(env)
                catch_body: list[str] = []
                if catch.name is not None:
                    catch_env[catch.name] = catch.error_type
                    catch_body.extend(self._emit_hir_catch_binding(caught_name, catch, catch_env))
                catch_body.extend(self._emit_hir_block(catch.body, catch_env))
                lines.append(self._indent(f'{prefix} ({self._emit_hir_catch_condition(caught_name, catch)}) {{'))
                lines.extend(
                    self._indent(self._indent(line))
                    for line in catch_body
                )
                lines.append(self._indent('}'))
        finally:
            self.current_caught_error_name = previous_caught_error_name

        lines.append(self._indent('else {'))
        lines.append(self._indent(self._indent(f'jack_rethrow({caught_name});')))
        lines.append(self._indent('}'))
        lines.append('}')
        return lines

    def _emit_hir_for_initializer(
        self, statement: HIRStatement | None, env: dict[str, TypeReference]
    ) -> str:
        if statement is None:
            return ''
        if isinstance(statement, HIRVariableDeclaration):
            if statement.constructor_call is not None:
                raise CEmitError('Constructed variables are not supported in for initializers yet.')
            symbol = statement.symbol
            initializer = (
                self._emit_hir_expression_as_type(statement.initializer, symbol.type_ref, env)
                if statement.initializer is not None
                else self._zero_value(symbol.type_ref)
            )
            env[symbol.name] = symbol.type_ref
            return f'{self._emit_declaration(symbol.type_ref, self._mangle(symbol.name), env)} = {initializer}'
        return self._emit_hir_for_update(statement, env)

    def _emit_hir_for_update(
        self, statement: HIRStatement | None, env: dict[str, TypeReference]
    ) -> str:
        if statement is None:
            return ''
        if isinstance(statement, HIRAssignment):
            return (
                f'{self._emit_hir_expression(statement.target, env)} = '
                f'{self._emit_hir_expression_as_type(statement.expr, statement.target_type, env)}'
            )
        if isinstance(statement, HIRExpressionStatement):
            if isinstance(statement.expr, HIRCallExpression) and self._hir_call_raises(statement.expr):
                raise CEmitFeatureNotImplemented(
                    'C emission for raising function calls in for clauses is not implemented yet.'
                )
            return self._emit_hir_expression(statement.expr, env)
        raise CEmitError(f'Unsupported HIR for clause statement "{type(statement).__name__}".')

    def _emit_hir_raising_call_statement(
        self, call: HIRCallExpression, env: dict[str, TypeReference]
    ) -> list[str]:
        if not self.current_function_raises:
            raise CEmitError('Raising call reached a non-raising C function.')
        return [f'{self._emit_hir_call(call, env, allow_raising=True)};']

    def _hir_call_raises(self, call: HIRCallExpression) -> bool:
        return bool(call.target.raises)

    def _emit_hir_condition(
        self, expression: HIRExpression, env: dict[str, TypeReference], context: str
    ) -> str:
        condition_type = self._hir_read_type(expression)
        if self._type_name(condition_type) != 'bool':
            raise CEmitError(
                f'Expected bool for {context}, got "{self._type_name(condition_type)}".'
            )
        return self._emit_hir_value_expression(expression, env)

    def _emit_hir_catch_condition(self, caught_name: str, catch: HIRCatchClause) -> str:
        return f'{caught_name}.tag == {self._error_tag_name(self._type_name(catch.error_type))}'

    def _emit_hir_catch_binding(
        self, caught_name: str, catch: HIRCatchClause, env: dict[str, TypeReference]
    ) -> list[str]:
        if catch.name is None:
            return []
        target = self._mangle(catch.name)
        return [
            f'{self._emit_declaration(catch.error_type, target, env)};',
            f'memcpy(&{target}, {caught_name}.payload, sizeof({target}));',
        ]


    def _emit_active_try_frame_exits(self) -> list[str]:
        return [
            f'jack_end_try(&{frame_name});'
            for frame_name in reversed(self.active_try_frames)
        ]


    def _emit_rethrow_statement(self) -> list[str]:
        if self.current_caught_error_name is None:
            raise CEmitError('rethrow reached C emission outside a catch block.')
        return [f'jack_rethrow({self.current_caught_error_name});']











    def _emit_deinit_calls(
        self, names: list[str], env: dict[str, TypeReference]
    ) -> list[str]:
        lines: list[str] = []
        for name in reversed(names):
            lines.extend(self._emit_deinit_value(self._mangle(name), env[name]))
        return lines

    def _emit_deinit_value(
        self, expression: str, type_ref: TypeReference
    ) -> list[str]:
        if type_ref.borrow is not None or type_ref.is_slice:
            return []
        if type_ref.array_size is not None:
            element_type = copy.deepcopy(type_ref)
            element_type.array_size = None
            size = int(self._emit_array_size(type_ref.array_size))
            lines: list[str] = []
            for index in reversed(range(size)):
                lines.extend(
                    self._emit_deinit_value(f'{expression}[{index}]', element_type)
                )
            return lines
        type_decl = self.type_declarations.get(self._type_name(type_ref))
        if type_decl is None or type_decl.extern:
            return []
        lines: list[str] = []
        method = next(
            (candidate for candidate in type_decl.methods if candidate.name == 'deinit'),
            None,
        )
        if method is not None:
            if self._function_raises(method):
                raise CEmitFeatureNotImplemented(
                    'C emission for raising destructors is not implemented yet.'
                )
            lines.append(
                f'{self._method_function_name(type_decl.name, method.name)}'
                f'(&{expression});'
            )
        for field in reversed(type_decl.fields):
            lines.extend(
                self._emit_deinit_value(
                    self._field_access(expression, field.name), field.type_ref
                )
            )
        return lines



    def _emit_hir_print(self, statement: HIRPrint, env: dict[str, TypeReference]) -> str:
        if isinstance(statement.expr, HIRFormattedStringExpression):
            return self._emit_hir_formatted_string_print(statement.expr, env)
        value = self._emit_hir_value_expression(statement.expr, env)
        value_type = self._hir_read_type(statement.expr)
        return self._emit_print_value(statement.label or '', value, value_type)

    def _emit_print_value(self, label: str, value: str, value_type: TypeReference) -> str:
        if label == '':
            if self._is_str_type(value_type):
                return (
                    'printf("%.*s\\n", '
                    f'(int)({self._field_access(value, "len")}), '
                    f'{self._field_access(value, "data")});'
                )
            if is_builtin_type(self._type_name(value_type)):
                return self._emit_builtin_print('', value, value_type)
            raise CEmitError(f'Cannot print values of type "{self._type_name(value_type)}".')

        escaped_label = self._escape_c_string(label)
        if self._is_str_type(value_type):
            return (
                f'printf("{escaped_label} = %.*s\\n", '
                f'(int)({self._field_access(value, "len")}), '
                f'{self._field_access(value, "data")});'
            )
        if is_builtin_type(self._type_name(value_type)):
            return self._emit_builtin_print(escaped_label, value, value_type)
        raise CEmitError(f'Cannot print values of type "{self._type_name(value_type)}".')





    def _read_expression_source(self, value: str, value_type: TypeReference) -> str:
        if self._is_readable_scalar_borrow_type(value_type):
            return f'(*{value})'
        return value



    def _is_readable_scalar_borrow_type(self, type_ref: TypeReference) -> bool:
        if (
            not self._is_borrow_type(type_ref)
            or self._is_array_type(type_ref)
            or self._is_slice_type(type_ref)
            or self._is_view_borrow_type(type_ref)
        ):
            return False
        if not borrow_mode_can_read(type_ref.borrow):
            raise CEmitError('Cannot read through a write-only borrow.')
        return True

    def _emit_hir_expression_as_type(
        self, expression: HIRExpression, expected_type: TypeReference, env: dict[str, TypeReference]
    ) -> str:
        if self._is_view_borrow_type(expected_type) and isinstance(expression, HIRBorrowExpression):
            return self._emit_hir_view_borrow_expression(expression, expected_type, env)
        if self._is_borrow_type(expected_type):
            return self._emit_hir_expression(expression, env)
        return self._emit_hir_value_expression(expression, env)

    def _emit_hir_value_expression(
        self, expression: HIRExpression, env: dict[str, TypeReference]
    ) -> str:
        value = self._emit_hir_expression(expression, env)
        return self._read_expression_source(value, expression.type_ref)

    def _emit_hir_expression(
        self, expression: HIRExpression, env: dict[str, TypeReference]
    ) -> str:
        if isinstance(expression, HIRLiteralExpression):
            return self._emit_literal_value(expression.value, expression.literal_type)
        if isinstance(expression, HIRVariableExpression):
            return 'self' if expression.name == 'self' else self._mangle(expression.name)
        if isinstance(expression, HIRFieldAccessExpression):
            return self._emit_hir_field_access(expression, env)
        if isinstance(expression, HIRCompositeExpression):
            return self._emit_hir_composite_expression(expression, env)
        if isinstance(expression, HIRFormattedStringExpression):
            raise CEmitError('Runtime formatted string values are only supported directly in print statements.')
        if isinstance(expression, HIRCallExpression):
            return self._emit_hir_call(expression, env)
        if isinstance(expression, HIRStructLiteralExpression):
            return self._emit_hir_struct_literal(expression, env)
        if isinstance(expression, HIRIndexExpression):
            return self._emit_hir_index_expression(expression, env)
        if isinstance(expression, HIRSliceExpression):
            return self._emit_hir_slice_literal(expression, env, mutable=False)
        if isinstance(expression, HIRBorrowExpression):
            return self._emit_hir_borrow_expression(expression, env)
        if isinstance(expression, HIRMoveExpression):
            return self._emit_hir_expression(expression.expr, env)
        if isinstance(expression, HIRDereferenceExpression):
            return f'(*{self._emit_hir_expression(expression.expr, env)})'
        if isinstance(expression, HIRRawAddressExpression):
            return f'(&{self._emit_hir_expression(expression.expr, env)})'
        if isinstance(expression, HIRPointerOffsetExpression):
            return (
                f'({self._emit_hir_expression(expression.pointer, env)} + '
                f'{self._emit_hir_value_expression(expression.offset, env)})'
            )
        if isinstance(expression, HIRPointerCastExpression):
            return (
                f'(({self._emit_type(self._element_type(expression.type_ref))} *)'
                f'{self._emit_hir_expression(expression.pointer, env)})'
            )
        raise CEmitError(f'Unknown HIR expression type "{type(expression).__name__}".')

    def _emit_hir_field_access(
        self, expression: HIRFieldAccessExpression, env: dict[str, TypeReference]
    ) -> str:
        target = self._emit_hir_expression(expression.target, env)
        field_name = self._mangle(expression.field_name)
        if expression.from_view:
            field_source = self._emit_field_access(target, field_name)
            return field_source if self._is_slice_type(expression.type_ref) else f'(*{field_source})'
        if self._is_scalar_borrow_field_target(expression.target.type_ref):
            return self._emit_pointer_field_access(target, field_name)
        return self._emit_field_access(target, field_name)

    def _is_scalar_borrow_field_target(self, type_ref: TypeReference) -> bool:
        return (
            self._is_borrow_type(type_ref)
            and not self._is_array_type(type_ref)
            and not self._is_slice_type(type_ref)
            and not self._is_view_borrow_type(type_ref)
        )

    def _emit_pointer_field_access(self, expression: str, field_name: str) -> str:
        if self._can_keep_original_identifier(expression):
            return f'{expression}->{field_name}'
        return f'({expression})->{field_name}'

    def _emit_hir_struct_literal(
        self, expression: HIRStructLiteralExpression, env: dict[str, TypeReference]
    ) -> str:
        type_decl = self._type_declaration_for(expression.type_ref)
        field_types = {field.name: field.type for field in type_decl.fields}
        fields = ', '.join(
            f'.{self._mangle(field.name)} = {self._emit_hir_expression_as_type(field.expr, field_types[field.name], env)}'
            for field in expression.fields
        )
        if not fields:
            fields = '0'
        return f'({self._emit_type(expression.type_ref)}){{{fields}}}'

    def _emit_hir_composite_expression(
        self, expression: HIRCompositeExpression, env: dict[str, TypeReference]
    ) -> str:
        left_type = self._hir_read_type(expression.left)
        right_type = self._hir_read_type(expression.right)
        left = self._emit_hir_value_expression(expression.left, env)
        right = self._emit_hir_value_expression(expression.right, env)

        if self._is_str_type(left_type) or self._is_str_type(right_type):
            if not self._is_str_type(left_type) or not self._is_str_type(right_type):
                raise CEmitError('Cannot combine str values with non-str values.')
            if expression.operator == '==':
                return self._emit_str_equal(left, right)
            if expression.operator == '!=':
                return f'(!{self._emit_str_equal(left, right)})'
            raise CEmitError(f'Operator "{expression.operator}" is not implemented for strings.')

        left_name = self._type_name(left_type)
        right_name = self._type_name(right_type)
        if is_builtin_type(left_name) or is_builtin_type(right_name):
            if left_name != right_name:
                raise CEmitError(f'Cannot combine values of type "{left_name}" and "{right_name}".')
            if is_bool_type(left_name) and expression.operator not in {'==', '!='}:
                raise CEmitError(f'Operator "{expression.operator}" is not implemented for bool values.')
            if is_raw_byte_type(left_name) and expression.operator not in {'==', '!='}:
                raise CEmitError(f'Operator "{expression.operator}" is not implemented for raw byte types.')

        return f'({left} {expression.operator} {right})'

    def _emit_hir_call(
        self,
        call: HIRCallExpression,
        env: dict[str, TypeReference],
        allow_raising: bool = True,
    ) -> str:
        target = call.target
        if target.kind == 'len':
            return self._emit_hir_len_call(call, env)
        if target.kind == 'builtin_conversion':
            return self._emit_hir_builtin_conversion(call, env)
        if target.kind == 'function':
            if target.raises and not allow_raising:
                raise CEmitFeatureNotImplemented(
                    f'C emission for raising function call "{target.name}" as an expression is not implemented yet.'
                )
            arguments = self._emit_hir_call_arguments(call.arguments, target.parameters, env)
            return f'{self._mangle(target.name)}({arguments})'
        if target.kind == 'method':
            receiver_name = target.receiver_name
            owner_type_name = target.owner_type_name
            self_parameter = target.self_parameter
            if owner_type_name is None or self_parameter is None or receiver_name is None:
                raise CEmitError(f'Incomplete HIR method target for "{target.name}".')
            method_name = target.name.rsplit('.', 1)[-1]
            if target.raises and not allow_raising:
                raise CEmitFeatureNotImplemented(
                    f'C emission for raising method call "{owner_type_name}.{method_name}" as an expression is not implemented yet.'
                )
            arguments = [
                self._emit_hir_receiver_argument(call, self_parameter.type_ref, env)
            ]
            emitted_arguments = self._emit_hir_call_arguments(call.arguments, target.parameters, env)
            if emitted_arguments:
                arguments.append(emitted_arguments)
            return f'{self._method_function_name(owner_type_name, method_name)}({", ".join(arguments)})'
        raise CEmitError(f'Unknown HIR call target kind "{target.kind}".')

    def _emit_hir_call_arguments(
        self,
        arguments: list[HIRExpression],
        parameters: list[HIRVariableSymbol],
        env: dict[str, TypeReference],
    ) -> str:
        if len(arguments) != len(parameters):
            raise CEmitError(
                f'Function call expects {len(parameters)} argument(s), got {len(arguments)}.'
            )
        return ', '.join(
            self._emit_hir_call_argument(argument, parameter.type_ref, env)
            for parameter, argument in zip(parameters, arguments)
        )

    def _emit_hir_call_argument(
        self, argument: HIRExpression, expected_type: TypeReference, env: dict[str, TypeReference]
    ) -> str:
        if self._is_borrow_type(expected_type):
            if isinstance(argument, HIRBorrowExpression):
                if not borrow_mode_compatible(expected_type.borrow, argument.mode):
                    raise CEmitError(
                        f'Cannot pass an &{argument.mode} borrow to an &{expected_type.borrow} parameter.'
                    )
                return self._emit_hir_expression_as_type(argument, expected_type, env)

            actual_type = argument.type_ref
            if not self._is_borrow_type(actual_type):
                raise CEmitError(
                    f'Parameter of type "{self._type_name(expected_type)}" requires a borrow-compatible HIR argument.'
                )
            if not borrow_mode_compatible(expected_type.borrow, actual_type.borrow):
                raise CEmitError(
                    f'Cannot pass an &{actual_type.borrow} borrow to an &{expected_type.borrow} parameter.'
                )
            return self._emit_hir_expression_as_type(argument, expected_type, env)
        if isinstance(argument, HIRBorrowExpression):
            raise CEmitError('Borrow argument passed to a non-borrow parameter.')
        if isinstance(argument, HIRMoveExpression):
            argument = argument.expr
        return self._emit_hir_expression_as_type(argument, expected_type, env)

    def _emit_hir_receiver_argument(
        self, call: HIRCallExpression, expected_type: TypeReference, env: dict[str, TypeReference]
    ) -> str:
        if call.implicit_self_argument is not None:
            return self._emit_hir_expression_as_type(call.implicit_self_argument, expected_type, env)
        receiver = call.receiver
        if receiver is None:
            raise CEmitError(f'Method call "{call.target.name}" has no receiver.')
        mode = expected_type.borrow or 'inout'
        borrowed = HIRBorrowExpression(
            mode=mode,
            expr=receiver,
            type_ref=TypeReference(
                receiver.type_ref.name,
                copy.deepcopy(receiver.type_ref.arguments),
                array_size=copy.deepcopy(receiver.type_ref.array_size),
                is_slice=receiver.type_ref.is_slice,
                borrow=mode,
            ),
            read_type=None,
            span=call.span,
        )
        return self._emit_hir_expression_as_type(borrowed, expected_type, env)

    def _emit_hir_builtin_conversion(
        self, call: HIRCallExpression, env: dict[str, TypeReference]
    ) -> str:
        if len(call.arguments) != 1:
            raise CEmitError(
                f'Type conversion "{call.target.name}" expects 1 argument, got {len(call.arguments)}.'
            )
        argument_expression = call.arguments[0]
        argument = self._emit_hir_value_expression(argument_expression, env)
        source_type = self._type_name(self._hir_read_type(argument_expression))
        target_type = call.target.name
        source_spec = BUILTIN_TYPE_SPECS[source_type]
        target_spec = BUILTIN_TYPE_SPECS[target_type]
        if (
            target_spec.family == 'raw'
            and source_spec.family in {'signed', 'unsigned', 'endian_signed'}
            and source_spec.bits == target_spec.bits
        ):
            if source_type == 'be_i32':
                return f'JACK_B32_FROM_BE32({argument})'
            if source_type == 'le_i32':
                return f'JACK_B32_FROM_LE32({argument})'
            return f'JACK_B{target_spec.bits}_FROM_NATIVE{source_spec.bits}({argument})'
        return f'(({self._emit_type(TypeReference(target_type))})({argument}))'

    def _emit_hir_index_expression(
        self, expression: HIRIndexExpression, env: dict[str, TypeReference]
    ) -> str:
        target = self._emit_hir_expression(expression.target, env)
        index = self._emit_hir_value_expression(expression.index, env)
        target_type = expression.target.type_ref
        if self._is_slice_type(target_type):
            return f'{self._field_access(target, "data")}[{index}]'
        if self._is_array_type(target_type):
            return f'{target}[{index}]'
        raise CEmitError(f'Cannot index value of type "{self._type_name(target_type)}".')

    def _emit_hir_borrow_expression(
        self, expression: HIRBorrowExpression, env: dict[str, TypeReference]
    ) -> str:
        mutable = borrow_mode_can_write(expression.mode)
        if isinstance(expression.expr, HIRSliceExpression):
            return self._emit_hir_slice_literal(expression.expr, env, mutable=mutable)

        target_type = expression.expr.type_ref
        target = self._emit_hir_expression(expression.expr, env)
        if self._is_slice_type(target_type):
            if target_type.borrow == expression.mode:
                return target
            slice_type = self._slice_type_name(self._element_type(target_type), mutable=mutable)
            return (
                f'({slice_type}){{ '
                f'{self._field_access(target, "data")}, '
                f'{self._field_access(target, "len")} }}'
            )
        if target_type.borrow is not None:
            return target
        if self._is_array_type(target_type):
            return target
        return f'&{target}'

    def _emit_hir_view_borrow_expression(
        self, expression: HIRBorrowExpression, expected_type: TypeReference, env: dict[str, TypeReference]
    ) -> str:
        view = self.view_declarations.get(expected_type.name)
        if view is None:
            raise CEmitError(f'Unknown view "{expected_type.name}" in C emission.')
        if expression.mode != 'inout':
            raise CEmitError('View borrows must be created with &inout.')
        fields = ', '.join(
            f'.{self._mangle(field.name)} = {self._emit_hir_view_field_borrow_initializer(expression.expr, field, env)}'
            for field in view.fields
        )
        if not fields:
            fields = '0'
        return f'({self._emit_type(self._element_type(expected_type))}){{{fields}}}'

    def _emit_hir_view_field_borrow_initializer(
        self, expression: HIRExpression, field, env: dict[str, TypeReference]
    ) -> str:
        field_source = self._emit_hir_field_access_by_name(expression, field.name, field.type, env)
        if self._is_slice_type(field.type):
            return field_source
        if self._is_array_type(field.type):
            return field_source
        return f'&{field_source}'

    def _emit_hir_field_access_by_name(
        self, expression: HIRExpression, field_name: str, field_type: TypeReference, env: dict[str, TypeReference]
    ) -> str:
        target = self._emit_hir_expression(expression, env)
        mangled = self._mangle(field_name)
        if self._is_scalar_borrow_field_target(expression.type_ref):
            return self._emit_pointer_field_access(target, mangled)
        return self._emit_field_access(target, mangled)

    def _emit_hir_slice_literal(
        self, expression: HIRSliceExpression, env: dict[str, TypeReference], mutable: bool
    ) -> str:
        target_type = expression.target.type_ref
        if self._is_borrow_type(target_type) and self._is_array_type(target_type):
            element_type = self._element_type(target_type)
        elif self._is_array_type(target_type):
            element_type = self._element_type(target_type)
        elif self._is_slice_type(target_type):
            element_type = self._element_type(target_type)
        else:
            raise CEmitError(f'Cannot slice value of type "{self._type_name(target_type)}".')

        target = self._emit_hir_expression(expression.target, env)
        start = '0' if expression.start is None else self._emit_hir_value_expression(expression.start, env)
        length = self._emit_hir_slice_length(expression, env)

        if self._is_slice_type(target_type):
            data = self._field_access(target, 'data')
            data = data if start == '0' else f'&{data}[{start}]'
        else:
            data = target if start == '0' else f'&{target}[{start}]'

        slice_type = self._slice_type_name(element_type, mutable=mutable)
        return f'({slice_type}){{ {data}, {length} }}'

    def _emit_hir_slice_length(self, expression: HIRSliceExpression, env: dict[str, TypeReference]) -> str:
        target_type = expression.target.type_ref
        if self._is_array_type(target_type):
            total_length = self._emit_array_size(target_type.array_size)
        elif self._is_slice_type(target_type):
            total_length = self._field_access(self._emit_hir_expression(expression.target, env), 'len')
        else:
            raise CEmitError(f'Cannot slice value of type "{self._type_name(target_type)}".')

        start = '0' if expression.start is None else self._emit_hir_value_expression(expression.start, env)
        if expression.end is None:
            return total_length if start == '0' else f'({total_length} - {start})'
        end = self._emit_hir_value_expression(expression.end, env)
        return f'({end} - {start})'

    def _emit_hir_len_call(self, call: HIRCallExpression, env: dict[str, TypeReference]) -> str:
        if len(call.arguments) != 1:
            raise CEmitError(f'len expects 1 argument, got {len(call.arguments)}.')
        argument = call.arguments[0]
        if isinstance(argument, HIRSliceExpression):
            return f'((int32_t)({self._emit_hir_slice_length(argument, env)}))'
        if isinstance(argument, HIRBorrowExpression):
            if isinstance(argument.expr, HIRSliceExpression):
                return f'((int32_t)({self._emit_hir_slice_length(argument.expr, env)}))'
            inner_type = argument.expr.type_ref
            if self._is_array_type(inner_type):
                return f'((int32_t)({self._emit_array_size(inner_type.array_size)}))'
            if self._is_slice_type(inner_type):
                return self._field_access(self._emit_hir_expression(argument.expr, env), 'len')

        argument_type = argument.type_ref
        if self._is_array_type(argument_type):
            return f'((int32_t)({self._emit_array_size(argument_type.array_size)}))'
        if self._is_slice_type(argument_type):
            return self._field_access(self._emit_hir_expression(argument, env), 'len')
        raise CEmitError(f'len expects an array or slice, got "{self._type_name(argument_type)}".')




    def _emit_builtin_print(self, label: str, value: str, value_type: TypeReference) -> str:
        format_parts: list[str] = []
        current: list[str] = []
        if label:
            current.append(f'{label} = ')
        self._append_value_format(format_parts, current, value_type)
        current.append('\\n')
        self._flush_c_format_part(format_parts, current)
        return f'printf({" ".join(format_parts)}, {", ".join(self._value_format_arguments(value, value_type))});'


    def _emit_hir_formatted_string_print(
        self, expression: HIRFormattedStringExpression, env: dict[str, TypeReference]
    ) -> str:
        format_parts: list[str] = []
        current: list[str] = []
        arguments: list[str] = []

        for part in expression.parts:
            if type(part) is str:
                current.append(self._escape_c_format_text(part))
                continue

            value_type = self._hir_read_type(part)
            value = self._emit_hir_value_expression(part, env)
            self._append_value_format(format_parts, current, value_type)
            arguments.extend(self._value_format_arguments(value, value_type))

        current.append('\\n')
        self._flush_c_format_part(format_parts, current)
        format_source = ' '.join(format_parts)
        if arguments:
            return f'printf({format_source}, {", ".join(arguments)});'
        return f'printf({format_source});'

    def _append_value_format(
        self, format_parts: list[str], current: list[str], value_type: TypeReference
    ) -> None:
        type_name = self._type_name(value_type)
        if self._is_str_type(value_type):
            current.append('%.*s')
            return
        if not is_builtin_type(type_name):
            raise CEmitError(f'Cannot format values of type "{type_name}".')

        spec = BUILTIN_TYPE_SPECS[type_name]
        if spec.family == 'bool':
            current.append('%s')
            return
        if spec.family == 'float':
            current.append('%.17g' if type_name == 'f64' else '%.9g')
            return
        if spec.family == 'raw':
            current.append(f'0x%0{spec.printf_width}')
        elif spec.printf_macro == 'zu':
            current.append('%zu')
            return
        else:
            current.append('%')
        self._flush_c_format_part(format_parts, current)
        format_parts.append(spec.printf_macro)

    def _value_format_arguments(self, value: str, value_type: TypeReference) -> list[str]:
        type_name = self._type_name(value_type)
        if self._is_str_type(value_type):
            return [
                f'(int)({self._field_access(value, "len")})',
                self._field_access(value, 'data'),
            ]
        if not is_builtin_type(type_name):
            raise CEmitError(f'Cannot format values of type "{type_name}".')

        spec = BUILTIN_TYPE_SPECS[type_name]
        if spec.family == 'bool':
            return [f'({value}) ? "true" : "false"']
        if spec.family == 'float':
            return [f'(double)({value})']
        if spec.bits < 32:
            if spec.family in {'signed', 'endian_signed'}:
                return [f'(int)({value})']
            return [f'(unsigned int)({value})']
        return [f'({spec.c_type})({value})']

    def _flush_c_format_part(self, format_parts: list[str], current: list[str]) -> None:
        if current:
            format_parts.append(f'"{"".join(current)}"')
            current.clear()

    def _emit_str_equal(self, left: str, right: str) -> str:
        return f'jack_str_equal({left}, {right})'


    def _emit_literal_value(self, value: object, literal_type: str) -> str:
        if literal_type == 'null':
            return 'NULL'
        if is_builtin_type(literal_type):
            if literal_type == 'bool':
                return 'true' if value else 'false'
            if literal_type == 'f32':
                return f'((float){float(value):.9g})'
            if literal_type == 'f64':
                return f'{float(value):.17g}'
            return str(int(value))
        if literal_type == 'str':
            string_value = str(value)
            return f'(jack_str){{"{self._escape_c_string(string_value)}", {len(string_value.encode("utf-8"))}}}'
        raise CEmitFeatureNotImplemented(f'C literal emission for type "{literal_type}" is not implemented yet.')











    def _emit_parameter_declaration(self, parameter: VariableDeclaration) -> str:
        if self._is_array_type(parameter.type) and not self._is_borrow_type(parameter.type):
            raise CEmitError(
                f'Array parameter "{parameter.name}" must be declared as an explicit borrow or slice for C emission.'
            )
        return self._emit_declaration(parameter.type, self._mangle(parameter.name), dict())

    def _emit_declaration(
        self, type_ref: TypeReference, name: str, env: dict[str, TypeReference]
    ) -> str:
        if type_ref.pointer_mode is not None:
            const_prefix = 'const ' if type_ref.pointer_mode == 'in' else ''
            return f'{const_prefix}{self._emit_type(self._element_type(type_ref))} *{name}'
        if self._is_view_borrow_type(type_ref):
            return f'{self._emit_type(self._element_type(type_ref))} {name}'
        if self._is_borrow_type(type_ref):
            element_type = self._element_type(type_ref)
            if self._is_slice_type(type_ref):
                mutable = borrow_mode_can_write(type_ref.borrow)
                return f'{self._slice_type_name(element_type, mutable=mutable)} {name}'
            if self._is_array_type(type_ref):
                const_prefix = '' if borrow_mode_can_write(type_ref.borrow) else 'const '
                return f'{const_prefix}{self._emit_type(element_type)} *{name}'
            const_prefix = '' if borrow_mode_can_write(type_ref.borrow) else 'const '
            return f'{const_prefix}{self._emit_type(element_type)} *{name}'

        if self._is_slice_type(type_ref):
            return f'{self._slice_type_name(self._element_type(type_ref), mutable=True)} {name}'
        if self._is_array_type(type_ref):
            size = self._emit_array_size(type_ref.array_size)
            return f'{self._emit_type(self._element_type(type_ref))} {name}[{size}]'
        return f'{self._emit_type(type_ref)} {name}'












    def _emit_array_size(self, extent: object | None) -> str:
        if extent is None:
            raise CEmitError('Array type is missing a size.')
        if type(extent) is not int:
            raise CEmitError(
                'Runtime HIR array types require a compile-time constant size.'
            )
        return str(extent)




    def _collect_hir_slice_types(self, program: HIRProgram) -> None:
        for node in self._walk_hir(program):
            if isinstance(node, TypeReference):
                self._collect_type_slice_types(node)


    def _collect_type_slice_types(self, type_ref: TypeReference) -> None:
        if self._is_slice_type(type_ref):
            self._slice_type_name(self._element_type(type_ref), mutable=borrow_mode_can_write(type_ref.borrow))
        if self._is_borrow_type(type_ref) and self._is_slice_type(type_ref):
            self._slice_type_name(self._element_type(type_ref), mutable=borrow_mode_can_write(type_ref.borrow))


    def _emit_slice_type_declarations(self) -> list[str]:
        return self._emit_selected_slice_type_declarations(set(self.slice_types))


    def _emit_slice_type_declarations_for(self, nodes: Iterable[object]) -> list[str]:
        selected: set[tuple[str, bool]] = set()
        for node in nodes:
            for child in self._walk_hir(node):
                if isinstance(child, TypeReference) and self._is_slice_type(child):
                    element = self._element_type(child)
                    selected.add(
                        (self._type_key(element), borrow_mode_can_write(child.borrow))
                    )
        return self._emit_selected_slice_type_declarations(selected)


    def _emit_selected_slice_type_declarations(
        self, selected: set[tuple[str, bool]]
    ) -> list[str]:
        lines: list[str] = []
        for (element_key, mutable), name in sorted(self.slice_types.items(), key=lambda item: item[1]):
            if (element_key, mutable) not in selected:
                continue
            if self._runtime_declares_slice_type(element_key):
                continue
            element_type = self._type_from_key(element_key)
            const_prefix = '' if mutable else 'const '
            guard = f'{name.upper()}_DEFINED'
            lines.extend([
                f'#ifndef {guard}',
                f'#define {guard}',
                f'typedef struct {name} {{',
                f'    {const_prefix}{self._emit_type(element_type)} *data;',
                '    int32_t len;',
                f'}} {name};',
                '#endif',
                '',
            ])
        return lines

    def _slice_type_name(self, element_type: TypeReference, mutable: bool) -> str:
        key = self._type_key(element_type)
        type_key = (key, mutable)
        existing = self.slice_types.get(type_key)
        if existing is not None:
            return existing
        prefix = 'jack_slice' if mutable else 'jack_in_slice'
        if self._runtime_declares_slice_type(key):
            name = f'{prefix}_{key}'
            self.slice_types[type_key] = name
            return name
        name = self._mangle(f'{prefix}${key}')
        self.slice_types[type_key] = name
        return name

    def _runtime_declares_slice_type(self, element_key: str) -> bool:
        return element_key in self.RUNTIME_SLICE_ELEMENT_KEYS

    def _type_from_key(self, key: str) -> TypeReference:
        return TypeReference(key)

    def _element_type(self, type_ref: TypeReference) -> TypeReference:
        return TypeReference(type_ref.name, list(type_ref.arguments))

    def _is_array_type(self, type_ref: TypeReference) -> bool:
        return type_ref.array_size is not None

    def _is_slice_type(self, type_ref: TypeReference) -> bool:
        return type_ref.is_slice

    def _is_borrow_type(self, type_ref: TypeReference) -> bool:
        return type_ref.borrow is not None

    def _is_view_borrow_type(self, type_ref: TypeReference) -> bool:
        return self._is_borrow_type(type_ref) and type_ref.name in self.view_declarations

    def _emit_type(self, type_ref: TypeReference | str) -> str:
        if isinstance(type_ref, str):
            name = type_ref
            arguments = []
            has_modifier = False
        else:
            name = type_ref.name
            arguments = type_ref.arguments
            has_modifier = (
                type_ref.array_size is not None
                or type_ref.is_slice
                or type_ref.borrow is not None
                or type_ref.pointer_mode is not None
            )

        if has_modifier:
            raise CEmitError(f'Modified type "{self._type_name(type_ref)}" needs a C declarator.')
        if arguments:
            raise CEmitError(f'Unresolved generic type "{name}" reached C emission.')
        if is_builtin_type(name):
            return BUILTIN_TYPE_SPECS[name].c_type
        if name == 'str':
            return 'jack_str'
        if name == 'c_char':
            return 'char'
        if name == 'void' or name == 'c_void':
            return 'void'
        if name == 'type':
            raise CEmitError('Comptime type value reached C emission.')
        return self._mangle(name)

    def _zero_value(self, type_ref: TypeReference) -> str:
        if self._is_array_type(type_ref):
            return '{0}'
        if self._is_slice_type(type_ref) or self._is_borrow_type(type_ref):
            raise CEmitError(f'Cannot create a zero value for type "{self._type_name(type_ref)}".')
        if is_builtin_type(self._type_name(type_ref)):
            type_name = self._type_name(type_ref)
            if is_bool_type(type_name):
                return 'false'
            return '0.0' if BUILTIN_TYPE_SPECS[type_name].family == 'float' else '0'
        if self._is_str_type(type_ref):
            return '(jack_str){"", 0}'
        if self._is_void_type(type_ref):
            raise CEmitError('Cannot create a zero value for type "void".')
        return '{0}'

    def _return_zero_value(self, type_ref: TypeReference) -> str:
        if is_builtin_type(self._type_name(type_ref)):
            type_name = self._type_name(type_ref)
            if is_bool_type(type_name):
                return 'false'
            return '0.0' if BUILTIN_TYPE_SPECS[type_name].family == 'float' else '0'
        if self._is_str_type(type_ref):
            return '(jack_str){"", 0}'
        if self._is_void_type(type_ref):
            raise CEmitError('Cannot return a value for type "void".')
        return f'({self._emit_type(type_ref)}){{0}}'

    def _type_name(self, type_ref: TypeReference) -> str:
        if type_ref.arguments:
            raise CEmitError(f'Unresolved generic type "{type_ref.name}" reached C emission.')
        name = type_ref.name
        if type_ref.array_size is not None:
            name = f'{name}[{self._type_key(type_ref.array_size)}]'
        elif type_ref.is_slice:
            name = f'{name}[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        elif type_ref.pointer_mode is not None:
            prefix = '?' if type_ref.nullable else ''
            name = f'{prefix}*{type_ref.pointer_mode} {name}'
        return name

    def _type_key(self, expression: object) -> str:
        if type(expression) is TypeReference:
            return self._type_name(expression)
        if type(expression) is int:
            return str(expression)
        return type(expression).__name__

    def _is_void_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'void'

    def _is_str_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'str'



    def _method_declaration_for(
        self, type_decl: TypeDeclaration, method_name: str
    ) -> FunctionDeclaration:
        method = next((method for method in type_decl.methods if method.name == method_name), None)
        if method is None:
            raise CEmitError(f'Type "{type_decl.name}" has no method "{method_name}".')
        return method


    def _has_method(self, type_ref: TypeReference, method_name: str) -> bool:
        type_decl = self.type_declarations.get(self._type_name(self._element_type(type_ref)))
        return type_decl is not None and any(method.name == method_name for method in type_decl.methods)


    def _type_declaration_for(self, type_ref: TypeReference) -> TypeDeclaration:
        type_name = self._type_name(self._element_type(type_ref))
        type_decl = self.type_declarations.get(type_name)
        if type_decl is None:
            raise CEmitError(f'Type "{type_name}" has no methods or fields.')
        return type_decl


    def _method_function_name(self, type_name: str, method_name: str) -> str:
        return self._mangle(f'{type_name}${method_name}')


    def _reserve_hir_readable_names(self, program: HIRProgram) -> None:
        for node in self._walk_hir(program):
            if isinstance(
                node,
                (HIRTypeDeclaration, HIRViewDeclaration, HIRFunctionDeclaration),
            ):
                self._reserve_readable_name(node.name)
            elif isinstance(node, HIRVariableSymbol):
                self._reserve_readable_name(node.name)
            elif isinstance(node, HIRCatchClause) and node.name is not None:
                self._reserve_readable_name(node.name)

    def _walk_hir(self, root):
        seen: set[int] = set()

        def walk(value):
            if value is None or isinstance(value, (str, bytes, int, float, bool)):
                return
            if isinstance(value, dict):
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item)
                return
            if not is_dataclass(value):
                return

            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            yield value
            for field in fields(value):
                yield from walk(getattr(value, field.name))

        yield from walk(root)


    def _reserve_readable_name(self, name: str) -> None:
        if name in self.names or not self._can_keep_original_identifier(name):
            return
        if name in self.used_names:
            return
        self.names[name] = name
        self.used_names.add(name)

    def _mangle(self, name: str) -> str:
        if name in self.names:
            return self.names[name]

        base = self._sanitize_identifier(name)
        if self._is_c_keyword(base) or self._is_reserved_c_identifier(base):
            base = f'{base}_jack'

        candidate = base
        counter = 2
        while (
            candidate in self.used_names
            or self._is_c_keyword(candidate)
            or self._is_reserved_c_identifier(candidate)
        ):
            candidate = f'{base}_{counter}'
            counter += 1

        self.names[name] = candidate
        self.used_names.add(candidate)
        return candidate

    def _sanitize_identifier(self, name: str) -> str:
        chars: list[str] = []
        previous_was_separator = False
        for char in name:
            if char.isascii() and (char.isalnum() or char == '_'):
                chars.append(char)
                previous_was_separator = False
            elif not previous_was_separator:
                chars.append('_')
                previous_was_separator = True

        candidate = ''.join(chars).strip('_')
        if not candidate:
            candidate = 'jack_name'
        if candidate[0].isdigit():
            candidate = f'jack_{candidate}'
        return candidate

    def _can_keep_original_identifier(self, name: str) -> bool:
        if not name or self._is_c_keyword(name) or self._is_reserved_c_identifier(name):
            return False
        if not (name[0].isascii() and (name[0].isalpha() or name[0] == '_')):
            return False
        return all(char.isascii() and (char.isalnum() or char == '_') for char in name)

    def _is_c_keyword(self, name: str) -> bool:
        return name in self.C_KEYWORDS

    def _is_reserved_c_identifier(self, name: str) -> bool:
        return name.startswith('__') or (len(name) > 1 and name[0] == '_' and name[1].isupper())

    def _emit_field_access(self, expression: str, field_name: str) -> str:
        if self._can_keep_original_identifier(expression):
            return f'{expression}.{field_name}'
        return self._field_access(expression, field_name)

    def _field_access(self, expression: str, field_name: str) -> str:
        return f'({expression}).{field_name}'

    def _escape_c_format_text(self, value: str) -> str:
        return self._escape_c_string(value).replace('%', '%%')

    def _escape_c_string(self, value: str) -> str:
        escaped: list[str] = []
        for byte in value.encode('utf-8'):
            if byte == ord('\\'):
                escaped.append('\\\\')
            elif byte == ord('"'):
                escaped.append('\\"')
            elif byte == ord('\n'):
                escaped.append('\\n')
            elif byte == ord('\r'):
                escaped.append('\\r')
            elif byte == ord('\t'):
                escaped.append('\\t')
            elif 32 <= byte <= 126:
                escaped.append(chr(byte))
            else:
                escaped.append(f'\\{byte:03o}')
        return ''.join(escaped)

    def _indent(self, line: str) -> str:
        return f'    {line}' if line else ''

    def _ensure_runtime_statement(self, statement: Statement) -> None:
        if getattr(statement, 'comptime', False):
            raise CEmitError(
                f'Unexpected comptime statement "{type(statement).__name__}" after the compile-time pass.'
            )
