from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
from typing import Callable, Iterator

try:
    from .borrow_modes import borrow_mode_can_read
    from .builtin_types import is_builtin_type
    from .compile_time_pass import apply_compile_time_pass
    from .ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        ImportDeclaration,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        Statement,
        StructLiteralExpression,
        Try,
        TypeDeclaration,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from .hir_nodes import (
        HIRAssignment,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFormattedStringExpression,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRIfBranch,
        HIRImportDeclaration,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRModuleDeclaration,
        HIRPrint,
        HIRProgram,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRStructLiteralExpression,
        HIRStructLiteralField,
        HIRTry,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRViewFieldSymbol,
        HIRWhile,
    )
    from .semantic_pass import SemanticError, SemanticPass, SemanticScope, SymbolInfo
except ImportError:
    from borrow_modes import borrow_mode_can_read
    from builtin_types import is_builtin_type
    from compile_time_pass import apply_compile_time_pass
    from ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        ImportDeclaration,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        Statement,
        StructLiteralExpression,
        Try,
        TypeDeclaration,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from hir_nodes import (
        HIRAssignment,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFormattedStringExpression,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRIfBranch,
        HIRImportDeclaration,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRModuleDeclaration,
        HIRPrint,
        HIRProgram,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRStructLiteralExpression,
        HIRStructLiteralField,
        HIRTry,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRViewFieldSymbol,
        HIRWhile,
    )
    from semantic_pass import SemanticError, SemanticPass, SemanticScope, SymbolInfo


class HIRLoweringError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.span = span


def lower_to_hir(ast: list[Statement]) -> HIRProgram:
    return HIRLoweringPass().lower(ast)


def compile_to_hir(
    ast: list[Statement],
    print_handler: Callable[[str], None] | None = print,
    externs: dict[str, object] | None = None,
) -> HIRProgram:
    runtime_ast = apply_compile_time_pass(
        ast,
        print_handler=print_handler,
        externs=externs,
    )
    return lower_to_hir(runtime_ast)


class HIRLoweringPass(SemanticPass):
    def lower(self, ast: list[Statement]) -> HIRProgram:
        self.validate(ast)
        declarations: list[HIRDeclaration] = []
        body: list[HIRStatement] = []
        top_level: list[HIRStatement] = []
        entry_module = self._entry_module(ast)
        module_dependencies = self._module_dependencies(ast, entry_module)
        for statement in ast:
            if self._is_top_level_declaration(statement):
                declaration = self._declaration(statement)
                declaration = self._record_statement(statement, declaration)
                declarations.append(declaration)
                top_level.append(declaration)
                continue
            with self._statement_context(statement):
                lowered = self._record_statement(
                    statement,
                    self._statement(statement, self.global_scope),
                )
                body.append(lowered)
                top_level.append(lowered)
        return HIRProgram(
            declarations=declarations,
            body=body,
            top_level=top_level,
            entry_module=entry_module,
            module_dependencies=module_dependencies,
        )

    def _is_top_level_declaration(self, statement: Statement) -> bool:
        return type(statement) in {
            ModuleDeclaration,
            ImportDeclaration,
            TypeDeclaration,
            ViewDeclaration,
            FunctionDeclaration,
            VariableDeclaration,
        }

    def _declaration(self, statement: Statement) -> HIRDeclaration:
        if type(statement) is ModuleDeclaration:
            return HIRModuleDeclaration(name=statement.name, span=statement.span)
        if type(statement) is ImportDeclaration:
            return HIRImportDeclaration(
                module_name=statement.module_name,
                alias=statement.alias,
                symbols=copy.deepcopy(statement.symbols),
                span=statement.span,
            )
        if type(statement) is TypeDeclaration:
            return self._type_declaration(statement)
        if type(statement) is ViewDeclaration:
            return self._view_declaration(statement)
        if type(statement) is FunctionDeclaration:
            return self._function_declaration(statement)
        if type(statement) is VariableDeclaration:
            with self._statement_context(statement):
                lowered = self._variable_declaration(statement, self.global_scope, top_level=True)
            return HIRGlobalVariable(
                symbol=lowered.symbol,
                initializer=lowered.initializer,
                constructor_call=lowered.constructor_call,
                span=statement.span,
            )
        raise HIRLoweringError(
            f'Top-level statement "{type(statement).__name__}" cannot be lowered as a HIR declaration.',
            getattr(statement, 'span', None),
        )

    def _type_declaration(self, declaration: TypeDeclaration) -> HIRTypeDeclaration:
        methods = [
            self._method_declaration(declaration, method)
            for method in declaration.methods
        ]
        return HIRTypeDeclaration(
            name=declaration.name,
            fields=[self._symbol(field) for field in declaration.fields],
            methods=methods,
            public=declaration.public,
            module_name=declaration.module_name,
            source_name=declaration.source_name,
            extern=declaration.extern,
            abi=declaration.abi,
            span=declaration.span,
        )

    def _view_declaration(self, declaration: ViewDeclaration) -> HIRViewDeclaration:
        return HIRViewDeclaration(
            name=declaration.name,
            fields=[
                HIRViewFieldSymbol(
                    name=field.name,
                    type_ref=self._copy_type(field.type),
                    mode=field.mode,
                    span=field.span,
                )
                for field in declaration.fields
            ],
            public=declaration.public,
            module_name=declaration.module_name,
            source_name=declaration.source_name,
            span=declaration.span,
        )

    def _function_declaration(
        self, declaration: FunctionDeclaration
    ) -> HIRFunctionDeclaration:
        scope = SemanticScope(self.global_scope)
        for parameter in declaration.parameters:
            scope.declare(
                parameter.name,
                SymbolInfo(
                    'variable',
                    parameter.type,
                    module_name=declaration.module_name,
                    can_return_borrow=self._is_borrow_type(parameter.type),
                ),
            )

        with self._function_context(declaration):
            body = [] if declaration.extern else self._block(declaration.body, scope)

        return HIRFunctionDeclaration(
            name=declaration.name,
            parameters=[self._symbol(parameter) for parameter in declaration.parameters],
            body=body,
            return_type=self._copy_type(declaration.return_type),
            self_parameter=None,
            raises=self._copy_types(declaration.raises),
            raises_inferred=declaration.raises_inferred,
            public=declaration.public,
            module_name=declaration.module_name,
            source_name=declaration.source_name,
            extern=declaration.extern,
            abi=declaration.abi,
            span=declaration.span,
        )

    def _method_declaration(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> HIRFunctionDeclaration:
        scope = SemanticScope(self.global_scope)
        self_parameter = self._method_self_parameter(type_decl, method)
        scope.declare(
            'self',
            SymbolInfo(
                'variable',
                self_parameter.type,
                module_name=type_decl.module_name,
                can_return_borrow=True,
            ),
        )
        for parameter in method.parameters:
            scope.declare(
                parameter.name,
                SymbolInfo(
                    'variable',
                    parameter.type,
                    module_name=type_decl.module_name,
                    can_return_borrow=self._is_borrow_type(parameter.type),
                ),
            )

        with self._method_context(type_decl, method):
            body = [] if method.extern else self._block(method.body, scope)

        declaration = HIRFunctionDeclaration(
            name=method.name,
            parameters=[self._symbol(parameter) for parameter in method.parameters],
            body=body,
            return_type=self._copy_type(method.return_type),
            self_parameter=self._symbol(self_parameter),
            raises=self._copy_types(method.raises),
            raises_inferred=method.raises_inferred,
            public=method.public,
            module_name=method.module_name or type_decl.module_name,
            source_name=method.source_name,
            extern=method.extern,
            abi=method.abi,
            span=method.span,
        )
        self._record_statement(method, declaration)
        return declaration

    def _block(
        self, statements: list[Statement], scope: SemanticScope
    ) -> list[HIRStatement]:
        lowered: list[HIRStatement] = []
        for statement in statements:
            lowered.append(
                self._record_statement(statement, self._statement(statement, scope))
            )
        return lowered

    def _record_statement(
        self, source: Statement, statement: HIRStatement
    ) -> HIRStatement:
        statement = replace(
            statement,
            module_name=statement.module_name or source.module_name,
            source_name=statement.source_name or source.source_name,
        )
        return statement

    def _entry_module(self, ast: list[Statement]) -> str | None:
        for statement in reversed(ast):
            if type(statement) not in {ModuleDeclaration, ImportDeclaration}:
                return statement.module_name
        return None

    def _module_dependencies(
        self, ast: list[Statement], entry_module: str | None
    ) -> dict[str | None, list[str]]:
        dependencies: dict[str | None, list[str]] = {}
        for statement in ast:
            module_name = statement.module_name or entry_module
            module_dependencies = dependencies.setdefault(module_name, [])
            for binding in statement.imports:
                if binding.module_name not in module_dependencies:
                    module_dependencies.append(binding.module_name)
        return dependencies

    def _statement(self, statement: Statement, scope: SemanticScope) -> HIRStatement:
        if type(statement) is VariableDeclaration:
            return self._variable_declaration(statement, scope, top_level=False)
        if type(statement) is Assignment:
            return self._assignment(statement, scope)
        if type(statement) is FunctionCall:
            return HIRExpressionStatement(
                expr=self._expression(statement, scope),
                span=statement.span,
            )
        if type(statement) is Return:
            return HIRReturn(
                expr=None if statement.expr is None else self._expression(statement.expr, scope),
                span=statement.span,
            )
        if type(statement) is Raise:
            expr = self._expression(statement.expr, scope)
            return HIRRaise(
                expr=expr,
                error_type=self._copy_type(expr.read_type or expr.type_ref),
                span=statement.span,
            )
        if type(statement) is Rethrow:
            return HIRRethrow(span=statement.span)
        if type(statement) is Print:
            if statement.expr is None:
                expr = self._name_expression(statement.name, scope, statement.span)
                return HIRPrint(expr=expr, label=statement.name, span=statement.span)
            return HIRPrint(
                expr=self._expression(statement.expr, scope),
                label=statement.name,
                span=statement.span,
            )
        if type(statement) is If:
            return self._if_statement(statement, scope)
        if type(statement) is While:
            return HIRWhile(
                condition=self._expression(statement.condition, scope),
                body=self._block(statement.body, SemanticScope(scope)),
                span=statement.span,
            )
        if type(statement) is For:
            return self._for_statement(statement, scope)
        if type(statement) is Try:
            return self._try_statement(statement, scope)
        raise HIRLoweringError(
            f'Statement "{type(statement).__name__}" cannot be lowered to HIR.',
            getattr(statement, 'span', None),
        )

    def _variable_declaration(
        self, declaration: VariableDeclaration, scope: SemanticScope, top_level: bool
    ) -> HIRVariableDeclaration:
        initializer = (
            None
            if declaration.expr is None
            else self._expression(declaration.expr, scope)
        )
        symbol = self._symbol(declaration)
        if not top_level:
            scope.declare(
                declaration.name,
                SymbolInfo(
                    'variable',
                    declaration.type,
                    module_name=declaration.module_name or self.current_module_name,
                    public=declaration.public,
                    source_name=declaration.source_name or declaration.name,
                    can_return_borrow=False,
                ),
            )
        constructor_call = None
        if declaration.constructor_args:
            constructor_call = self._call_expression(
                FunctionCall(
                    f'{declaration.name}.init',
                    declaration.constructor_args,
                    span=declaration.span,
                ),
                scope,
                record_source=False,
            )
        return HIRVariableDeclaration(
            symbol=symbol,
            initializer=initializer,
            constructor_call=constructor_call,
            span=declaration.span,
        )

    def _assignment(
        self, assignment: Assignment, scope: SemanticScope
    ) -> HIRAssignment:
        target = self._assignment_target(assignment.name, scope, assignment.span)
        return HIRAssignment(
            target=target,
            expr=self._expression(assignment.expr, scope),
            target_type=self._copy_type(target.type_ref),
            span=assignment.span,
        )

    def _assignment_target(
        self, target: str | Expression, scope: SemanticScope, span: SourceSpan | None
    ) -> HIRExpression:
        if type(target) is str:
            return self._name_expression(target, scope, span)
        return self._expression(target, scope)

    def _if_statement(self, statement: If, scope: SemanticScope) -> HIRIf:
        return HIRIf(
            branches=[
                HIRIfBranch(
                    condition=self._expression(branch.condition, scope),
                    body=self._block(branch.body, SemanticScope(scope)),
                    span=branch.span,
                )
                for branch in statement.branches
            ],
            else_body=(
                None
                if statement.else_body is None
                else self._block(statement.else_body, SemanticScope(scope))
            ),
            span=statement.span,
        )

    def _for_statement(self, statement: For, scope: SemanticScope) -> HIRFor:
        loop_scope = SemanticScope(scope)
        initializer = (
            None
            if statement.initializer is None
            else self._record_statement(
                statement.initializer,
                self._statement(statement.initializer, loop_scope),
            )
        )
        return HIRFor(
            initializer=initializer,
            condition=(
                None
                if statement.condition is None
                else self._expression(statement.condition, loop_scope)
            ),
            update=(
                None
                if statement.update is None
                else self._record_statement(
                    statement.update,
                    self._statement(statement.update, loop_scope),
                )
            ),
            body=self._block(statement.body, SemanticScope(loop_scope)),
            span=statement.span,
        )

    def _try_statement(self, statement: Try, scope: SemanticScope) -> HIRTry:
        catches: list[HIRCatchClause] = []
        for catch in statement.catches:
            catch_scope = SemanticScope(scope)
            if catch.name is not None:
                catch_scope.declare(
                    catch.name,
                    SymbolInfo(
                        'variable',
                        catch.error_type,
                        module_name=self.current_module_name,
                    ),
                )
            catches.append(
                HIRCatchClause(
                    error_type=self._copy_type(catch.error_type),
                    name=catch.name,
                    body=self._block(catch.body, catch_scope),
                    span=catch.span,
                )
            )
        return HIRTry(
            body=self._block(statement.body, SemanticScope(scope)),
            catches=catches,
            span=statement.span,
        )

    def _record_expression(
        self, source: Expression, expression: HIRExpression
    ) -> HIRExpression:
        return expression

    def _expression(self, expression: Expression, scope: SemanticScope) -> HIRExpression:
        if type(expression) is LiteralExpression:
            type_ref = TypeReference(expression.type)
            return self._record_expression(
                expression,
                HIRLiteralExpression(
                    value=expression.value,
                    literal_type=expression.type,
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        if type(expression) is VariableExpression:
            return self._name_expression(
                expression.name,
                scope,
                expression.span,
                source=expression,
            )
        if type(expression) is FunctionCall:
            return self._call_expression(expression, scope)
        if type(expression) is FormattedStringExpression:
            return self._record_expression(
                expression,
                HIRFormattedStringExpression(
                    parts=[
                        part if type(part) is str else self._expression(part, scope)
                        for part in expression.parts
                    ],
                    type_ref=TypeReference('str'),
                    read_type=TypeReference('str'),
                    span=expression.span,
                ),
            )
        if type(expression) is StructLiteralExpression:
            type_ref = self._copy_type(expression.type_ref)
            return self._record_expression(
                expression,
                HIRStructLiteralExpression(
                    fields=[
                        HIRStructLiteralField(
                            name=field.name,
                            expr=self._expression(field.expr, scope),
                            span=field.span,
                        )
                        for field in expression.fields
                    ],
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        if type(expression) is CompositeExpression:
            left = self._expression(expression.left, scope)
            right = self._expression(expression.right, scope)
            type_ref = self._composite_result_type(left, expression.operator)
            return self._record_expression(
                expression,
                HIRCompositeExpression(
                    left=left,
                    operator=expression.operator,
                    right=right,
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        if type(expression) is IndexExpression:
            target = self._expression(expression.target, scope)
            index = self._expression(expression.index, scope)
            type_ref = self._indexed_element_type(target.type_ref)
            return self._record_expression(
                expression,
                HIRIndexExpression(
                    target=target,
                    index=index,
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        if type(expression) is SliceExpression:
            target = self._expression(expression.target, scope)
            type_ref = TypeReference(self._element_type(target.type_ref).name, is_slice=True)
            return self._record_expression(
                expression,
                HIRSliceExpression(
                    target=target,
                    start=None if expression.start is None else self._expression(expression.start, scope),
                    end=None if expression.end is None else self._expression(expression.end, scope),
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        if type(expression) is BorrowExpression:
            inner = self._expression(expression.expr, scope)
            type_ref = self._borrow_type(inner.type_ref, expression.mode)
            return self._record_expression(
                expression,
                HIRBorrowExpression(
                    mode=expression.mode,
                    expr=inner,
                    type_ref=type_ref,
                    read_type=self._read_type(type_ref),
                    span=expression.span,
                ),
            )
        raise HIRLoweringError(
            f'Expression "{type(expression).__name__}" cannot be lowered to HIR.',
            getattr(expression, 'span', None),
        )

    def _name_expression(
        self,
        name: str,
        scope: SemanticScope,
        span: SourceSpan | None,
        source: Expression | None = None,
    ) -> HIRExpression:
        parts = name.split('.')
        if any(part == '' for part in parts):
            raise HIRLoweringError(f'Invalid name "{name}".', span)
        info = scope.get(parts[0])
        if info is None or info.kind != 'variable' or info.type_ref is None:
            raise HIRLoweringError(f'Unknown value "{parts[0]}" while lowering HIR.', span)

        current_type = info.type_ref
        expr: HIRExpression = HIRVariableExpression(
            name=parts[0],
            type_ref=self._copy_type(current_type),
            read_type=self._read_type(current_type),
            span=span,
        )

        for field_name in parts[1:]:
            view_field = self._view_field_for_type(current_type, field_name)
            if view_field is not None:
                current_type = view_field.type
                expr = HIRFieldAccessExpression(
                    target=expr,
                    field_name=field_name,
                    owner_type_name=self._type_name(self._element_type(expr.type_ref)),
                    from_view=True,
                    type_ref=self._copy_type(current_type),
                    read_type=self._read_type(current_type),
                    span=span,
                )
                continue

            type_decl = self._type_declaration_for(current_type)
            field = next((field for field in type_decl.fields if field.name == field_name), None)
            if field is None:
                raise HIRLoweringError(
                    f'Type "{type_decl.name}" has no field "{field_name}" while lowering HIR.',
                    span,
                )
            current_type = field.type
            expr = HIRFieldAccessExpression(
                target=expr,
                field_name=field_name,
                owner_type_name=type_decl.name,
                from_view=False,
                type_ref=self._copy_type(current_type),
                read_type=self._read_type(current_type),
                span=span,
            )

        if source is not None:
            return self._record_expression(source, expr)
        return expr

    def _call_expression(
        self, call: FunctionCall, scope: SemanticScope, *, record_source: bool = True
    ) -> HIRCallExpression:
        target, receiver, implicit_self = self._call_target(call, scope)
        arguments = [self._expression(argument, scope) for argument in call.parameters]
        expression = HIRCallExpression(
            target=target,
            arguments=arguments,
            receiver=receiver,
            implicit_self_argument=implicit_self,
            type_ref=self._copy_type(target.return_type),
            read_type=self._read_type(target.return_type),
            span=call.span,
        )
        if record_source:
            return self._record_expression(call, expression)
        return expression

    def _call_target(
        self, call: FunctionCall, scope: SemanticScope
    ) -> tuple[HIRCallTarget, HIRExpression | None, HIRBorrowExpression | None]:
        if call.function_name in {'sizeof', 'alignof'}:
            raise HIRLoweringError(
                f'{call.function_name} must be folded by the compile-time pass.',
                call.span,
            )
        if '.' in call.function_name:
            return self._method_call_target(call, scope)
        if call.function_name == 'len':
            return (
                HIRCallTarget(
                    kind='len',
                    name='len',
                    return_type=TypeReference('i32'),
                ),
                None,
                None,
            )
        if is_builtin_type(call.function_name):
            return (
                HIRCallTarget(
                    kind='builtin_conversion',
                    name=call.function_name,
                    return_type=TypeReference(call.function_name),
                ),
                None,
                None,
            )

        declaration = self.functions.get(call.function_name)
        if declaration is None:
            raise HIRLoweringError(
                f'Unknown function "{call.function_name}" while lowering HIR.',
                call.span,
            )
        return (
            HIRCallTarget(
                kind='function',
                name=call.function_name,
                return_type=self._copy_type(declaration.return_type),
                parameters=[self._symbol(parameter) for parameter in declaration.parameters],
                raises=self._copy_types(declaration.raises),
                extern=declaration.extern,
                abi=declaration.abi,
            ),
            None,
            None,
        )

    def _method_call_target(
        self, call: FunctionCall, scope: SemanticScope
    ) -> tuple[HIRCallTarget, HIRExpression, HIRBorrowExpression]:
        receiver_name, method_name = call.function_name.rsplit('.', 1)
        receiver = self._name_expression(receiver_name, scope, call.span)
        type_decl = self._type_declaration_for(receiver.type_ref)
        method = next((method for method in type_decl.methods if method.name == method_name), None)
        if method is None:
            raise HIRLoweringError(
                f'Type "{type_decl.name}" has no method "{method_name}" while lowering HIR.',
                call.span,
            )
        self_parameter = self._method_self_parameter(type_decl, method)
        self_type = self._copy_type(self_parameter.type)
        implicit_self = HIRBorrowExpression(
            mode=self_type.borrow or 'inout',
            expr=receiver,
            type_ref=self_type,
            read_type=self._read_type(self_type),
            span=call.span,
        )
        return (
            HIRCallTarget(
                kind='method',
                name=f'{type_decl.name}.{method_name}',
                return_type=self._copy_type(method.return_type),
                parameters=[self._symbol(parameter) for parameter in method.parameters],
                self_parameter=self._symbol(self_parameter),
                raises=self._copy_types(method.raises),
                extern=method.extern,
                abi=method.abi,
                owner_type_name=type_decl.name,
                receiver_name=receiver_name,
            ),
            receiver,
            implicit_self,
        )

    def _composite_result_type(
        self, left: HIRExpression, operator: str
    ) -> TypeReference:
        if operator == '+':
            return self._copy_type(left.read_type or left.type_ref)
        return TypeReference('bool')

    def _borrow_type(self, inner_type: TypeReference, mode: str) -> TypeReference:
        return TypeReference(
            inner_type.name,
            copy.deepcopy(inner_type.arguments),
            array_size=copy.deepcopy(inner_type.array_size),
            is_slice=inner_type.is_slice,
            borrow=mode,
        )

    def _read_type(self, type_ref: TypeReference) -> TypeReference | None:
        if (
            self._is_borrow_type(type_ref)
            and not self._is_array_type(type_ref)
            and not self._is_slice_type(type_ref)
            and not self._is_view_borrow_type(type_ref)
        ):
            if not borrow_mode_can_read(type_ref.borrow):
                return None
            return self._copy_type(self._element_type(type_ref))
        return self._copy_type(type_ref)

    def _symbol(self, declaration: VariableDeclaration) -> HIRVariableSymbol:
        return HIRVariableSymbol(
            name=declaration.name,
            type_ref=self._copy_type(declaration.type),
            comptime=declaration.comptime,
            public=declaration.public,
            module_name=declaration.module_name,
            source_name=declaration.source_name,
            extern=declaration.extern,
            abi=declaration.abi,
            span=declaration.span,
        )

    def _copy_type(self, type_ref: TypeReference) -> TypeReference:
        array_size = type_ref.array_size
        if array_size is not None:
            if type(array_size) is LiteralExpression:
                array_size = int(array_size.value)
            elif type(array_size) is not int:
                raise HIRLoweringError(
                    'Runtime HIR array types require a compile-time constant size.',
                    type_ref.span,
                )
        return TypeReference(
            type_ref.name,
            [
                self._copy_type(argument)
                if type(argument) is TypeReference
                else copy.deepcopy(argument)
                for argument in type_ref.arguments
            ],
            array_size=array_size,
            is_slice=type_ref.is_slice,
            borrow=type_ref.borrow,
            span=type_ref.span,
        )

    def _copy_types(self, type_refs: list[TypeReference]) -> list[TypeReference]:
        return [self._copy_type(type_ref) for type_ref in type_refs]

    @contextmanager
    def _statement_context(self, statement: Statement) -> Iterator[None]:
        with self._module_context(
            statement.module_name,
            list(statement.imports),
            list(statement.qualified_imports),
        ):
            yield

    @contextmanager
    def _function_context(self, declaration: FunctionDeclaration) -> Iterator[None]:
        with self._module_context(
            declaration.module_name,
            list(declaration.imports),
            list(declaration.qualified_imports),
        ):
            yield

    @contextmanager
    def _method_context(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> Iterator[None]:
        with self._module_context(
            type_decl.module_name,
            list(type_decl.imports),
            list(method.qualified_imports or type_decl.qualified_imports),
        ):
            yield

    @contextmanager
    def _module_context(
        self,
        module_name: str | None,
        imports: list,
        qualified_imports: list,
    ) -> Iterator[None]:
        previous_module_name = self.current_module_name
        previous_imports = self.current_imports
        previous_qualified_imports = self.current_qualified_imports
        self.current_module_name = module_name
        self.current_imports = imports
        self.current_qualified_imports = qualified_imports
        try:
            yield
        finally:
            self.current_module_name = previous_module_name
            self.current_imports = previous_imports
            self.current_qualified_imports = previous_qualified_imports
