from __future__ import annotations

import copy
from dataclasses import fields, is_dataclass, replace
from typing import Iterable

RaisedError = str

try:
    from .hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDeclaration,
        HIRDereferenceExpression,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFormattedStringExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRIfBranch,
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
        HIRStructLiteralExpression,
        HIRTry,
        HIRUnsafeBlock,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRWhile,
    )
    from .ast_nodes import TypeReference
except ImportError:
    from hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCatchClause,
        HIRCompositeExpression,
        HIRDeclaration,
        HIRDereferenceExpression,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFormattedStringExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRIfBranch,
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
        HIRStructLiteralExpression,
        HIRTry,
        HIRUnsafeBlock,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRVariableSymbol,
        HIRViewDeclaration,
        HIRWhile,
    )
    from ast_nodes import TypeReference


class CleanupLoweringError(Exception):
    pass


def lower_hir_static_cleanups(program: HIRProgram) -> HIRProgram:
    return HIRStaticCleanupLoweringPass().lower(program)


class HIRStaticCleanupLoweringPass:
    def __init__(self) -> None:
        self.types: dict[str, HIRTypeDeclaration] = {}
        self.functions: dict[str, HIRFunctionDeclaration] = {}
        self.global_variables: dict[str, TypeReference] = {}
        self.used_names: set[str] = set()
        self.drop_flags: dict[str, str] = {}
        self.consuming_self_type: HIRTypeDeclaration | None = None

    def _hir_statement_raised_errors(
        self, statement: HIRStatement, env: dict[str, TypeReference]
    ) -> list[RaisedError]:
        errors: list[RaisedError] = []
        if isinstance(statement, (HIRGlobalVariable, HIRVariableDeclaration)):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.initializer)
            )
            env[statement.symbol.name] = statement.symbol.type_ref
            if statement.constructor_call is not None:
                self._merge_errors(
                    errors, self._hir_call_raised_errors(statement.constructor_call)
                )
        elif isinstance(statement, HIRAssignment):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.target)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.expr)
            )
        elif isinstance(statement, HIRExpressionStatement):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.expr)
            )
        elif isinstance(statement, HIRRaise):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.expr)
            )
            self._add_error(errors, self._type_name(statement.error_type))
        elif isinstance(statement, HIRPrint):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.expr)
            )
        elif isinstance(statement, HIRReturn):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.expr)
            )
        elif isinstance(statement, HIRIf):
            for branch in statement.branches:
                self._merge_errors(
                    errors, self._hir_expression_raised_errors(branch.condition)
                )
                self._merge_errors(
                    errors,
                    self._hir_statement_list_raised_errors(branch.body, dict(env)),
                )
            if statement.else_body is not None:
                self._merge_errors(
                    errors,
                    self._hir_statement_list_raised_errors(
                        statement.else_body, dict(env)
                    ),
                )
        elif isinstance(statement, HIRWhile):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(statement.condition)
            )
            self._merge_errors(
                errors,
                self._hir_statement_list_raised_errors(statement.body, dict(env)),
            )
        elif isinstance(statement, HIRFor):
            loop_env = dict(env)
            if statement.initializer is not None:
                self._merge_errors(
                    errors,
                    self._hir_statement_raised_errors(
                        statement.initializer, loop_env
                    ),
                )
            if statement.condition is not None:
                self._merge_errors(
                    errors, self._hir_expression_raised_errors(statement.condition)
                )
            if statement.update is not None:
                self._merge_errors(
                    errors,
                    self._hir_statement_raised_errors(statement.update, loop_env),
                )
            self._merge_errors(
                errors,
                self._hir_statement_list_raised_errors(
                    statement.body, dict(loop_env)
                ),
            )
        elif isinstance(statement, HIRTry):
            try_errors = self._hir_statement_list_raised_errors(
                statement.body, dict(env)
            )
            caught = {
                self._type_name(catch.error_type) for catch in statement.catches
            }
            self._merge_errors(
                errors, [error for error in try_errors if error not in caught]
            )
            for catch in statement.catches:
                catch_env = dict(env)
                if catch.name is not None:
                    catch_env[catch.name] = catch.error_type
                self._merge_errors(
                    errors,
                    self._hir_statement_list_raised_errors(
                        catch.body, catch_env
                    ),
                )
        elif isinstance(statement, (HIRBlock, HIRUnsafeBlock)):
            self._merge_errors(
                errors,
                self._hir_statement_list_raised_errors(statement.body, dict(env)),
            )
        return errors

    def _hir_expression_raised_errors(
        self, expression: HIRExpression | None
    ) -> list[RaisedError]:
        if expression is None:
            return []
        errors: list[RaisedError] = []
        if isinstance(expression, HIRCallExpression):
            self._merge_errors(errors, self._hir_call_raised_errors(expression))
        elif isinstance(expression, HIRCompositeExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.left)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.right)
            )
        elif isinstance(expression, HIRFormattedStringExpression):
            for part in expression.parts:
                if isinstance(part, HIRExpression):
                    self._merge_errors(
                        errors, self._hir_expression_raised_errors(part)
                    )
        elif isinstance(expression, HIRStructLiteralExpression):
            for field in expression.fields:
                self._merge_errors(
                    errors, self._hir_expression_raised_errors(field.expr)
                )
        elif isinstance(expression, HIRBorrowExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.expr)
            )
        elif isinstance(expression, (HIRMoveExpression, HIRDereferenceExpression)):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.expr)
            )
        elif isinstance(expression, HIRRawAddressExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.expr)
            )
        elif isinstance(expression, HIRPointerOffsetExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.pointer)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.offset)
            )
        elif isinstance(expression, HIRPointerCastExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.pointer)
            )
        elif isinstance(expression, HIRIndexExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.target)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.index)
            )
        elif isinstance(expression, HIRSliceExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.target)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.start)
            )
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.end)
            )
        elif isinstance(expression, HIRFieldAccessExpression):
            self._merge_errors(
                errors, self._hir_expression_raised_errors(expression.target)
            )
        return errors

    def _hir_call_raised_errors(
        self, call: HIRCallExpression
    ) -> list[RaisedError]:
        errors: list[RaisedError] = []
        for argument in call.arguments:
            self._merge_errors(
                errors, self._hir_expression_raised_errors(argument)
            )
        self._merge_errors(errors, self._declared_raised_errors(call.target.raises))
        return errors

    def _has_deinit(self, type_ref: TypeReference, seen: set[str] | None = None) -> bool:
        if type_ref.borrow is not None or type_ref.is_slice:
            return False
        if type_ref.array_size is not None:
            element_type = copy.deepcopy(type_ref)
            element_type.array_size = None
            return self._has_deinit(element_type, seen)
        type_decl = self.types.get(self._type_name(type_ref))
        if not isinstance(type_decl, HIRTypeDeclaration) or type_decl.extern:
            return False
        if any(method.name == 'deinit' for method in type_decl.methods):
            return True
        seen = set(seen or ())
        if type_decl.name in seen:
            return False
        seen.add(type_decl.name)
        return any(self._has_deinit(field.type_ref, seen) for field in type_decl.fields)

    def _declared_raised_errors(
        self, raises: Iterable[TypeReference]
    ) -> list[RaisedError]:
        return [self._type_name(error) for error in raises]

    def _merge_errors(
        self, target: list[RaisedError], source: Iterable[RaisedError]
    ) -> None:
        for error in source:
            self._add_error(target, error)

    def _add_error(self, target: list[RaisedError], error: RaisedError) -> None:
        if error not in target:
            target.append(error)

    def _type_name(self, type_ref: str | TypeReference) -> str:
        return type_ref if isinstance(type_ref, str) else type_ref.name

    def _next_generated_name(self, prefix: str) -> str:
        index = 1
        while True:
            name = f'{prefix}_{index}'
            if name not in self.used_names:
                self.used_names.add(name)
                return name
            index += 1

    def lower(self, program: HIRProgram) -> HIRProgram:
        self.types = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRTypeDeclaration)
        }
        self.functions = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        }
        self.global_variables = {
            declaration.symbol.name: declaration.symbol.type_ref
            for declaration in program.declarations
            if isinstance(declaration, HIRGlobalVariable)
        }
        self.used_names = self._collect_hir_names(program)

        declaration_replacements: dict[int, HIRDeclaration] = {}
        declarations: list[HIRDeclaration] = []
        for declaration in program.declarations:
            lowered = self._lower_hir_declaration(declaration)
            declaration_replacements[id(declaration)] = lowered
            declarations.append(lowered)

        top_level: list[HIRStatement] = []
        top_level_env: dict[str, TypeReference] = {}
        for statement in program.top_level:
            if isinstance(statement, HIRDeclaration):
                lowered_declaration = declaration_replacements[id(statement)]
                top_level.append(lowered_declaration)
                if isinstance(lowered_declaration, HIRGlobalVariable):
                    top_level_env[
                        lowered_declaration.symbol.name
                    ] = lowered_declaration.symbol.type_ref
                continue
            top_level.extend(
                self._lower_hir_statement(
                    statement,
                    dict(top_level_env),
                    [],
                    return_type=None,
                )
            )

        return HIRProgram(
            declarations=declarations,
            body=[statement for statement in top_level if not isinstance(statement, HIRDeclaration)],
            top_level=top_level,
            entry_module=program.entry_module,
            module_dependencies={
                module: list(dependencies)
                for module, dependencies in program.module_dependencies.items()
            },
            span=program.span,
        )

    def _lower_hir_declaration(self, declaration: HIRDeclaration) -> HIRDeclaration:
        if isinstance(declaration, HIRFunctionDeclaration):
            if declaration.extern:
                return declaration
            env = dict(self.global_variables)
            env.update(
                {parameter.name: parameter.type_ref for parameter in declaration.parameters}
            )
            self.drop_flags = {}
            move_parameters = [
                parameter for parameter in declaration.parameters
                if parameter.type_ref.borrow is None and self._has_deinit(parameter.type_ref)
            ]
            flag_declarations = [
                flag
                for parameter in move_parameters
                for flag in self._hir_create_drop_flags(
                    parameter.name, parameter.type_ref, True, parameter.span
                )
            ]
            body = self._lower_hir_block(
                declaration.body,
                env,
                [parameter.name for parameter in move_parameters],
                declaration.return_type,
            )
            if not self._hir_ends_with_terminator(body):
                body.extend(
                    self._hir_cleanup_calls(
                        [parameter.name for parameter in move_parameters],
                        env,
                        declaration.span,
                    )
                )
            return replace(
                declaration,
                body=[*flag_declarations, *body],
            )
        if isinstance(declaration, HIRTypeDeclaration):
            methods: list[HIRFunctionDeclaration] = []
            for method in declaration.methods:
                if method.extern:
                    methods.append(method)
                    continue
                env = dict(self.global_variables)
                if method.self_parameter is None:
                    raise CleanupLoweringError(
                        f'Method "{declaration.name}.{method.name}" must declare an explicit self parameter.'
                    )
                env['self'] = method.self_parameter.type_ref
                env.update(
                    {parameter.name: parameter.type_ref for parameter in method.parameters}
                )
                self.drop_flags = {}
                previous_consuming_self = self.consuming_self_type
                self.consuming_self_type = (
                    declaration if method.name == 'deinit' else None
                )
                move_parameters = [
                    parameter for parameter in method.parameters
                    if parameter.type_ref.borrow is None and self._has_deinit(parameter.type_ref)
                ]
                flag_declarations = [
                    flag
                    for parameter in move_parameters
                    for flag in self._hir_create_drop_flags(
                        parameter.name, parameter.type_ref, True, parameter.span
                    )
                ]
                inherited_names = [parameter.name for parameter in move_parameters]
                if self.consuming_self_type is not None:
                    for field in declaration.fields:
                        if self._has_deinit(field.type_ref):
                            flag_declarations.extend(self._hir_create_drop_flags(
                                f'self.{field.name}', field.type_ref, True,
                                field.span or method.span,
                            ))
                    inherited_names.insert(0, 'self')
                body = self._lower_hir_block(
                    method.body,
                    env,
                    inherited_names,
                    method.return_type,
                )
                if not self._hir_ends_with_terminator(body):
                    body.extend(
                        self._hir_cleanup_calls(
                            inherited_names,
                            env,
                            method.span,
                        )
                    )
                methods.append(
                    replace(
                        method,
                        body=[*flag_declarations, *body],
                    )
                )
                self.consuming_self_type = previous_consuming_self
            return replace(declaration, methods=methods)
        return declaration

    def _lower_hir_block(
        self,
        statements: Iterable[HIRStatement],
        env: dict[str, TypeReference],
        inherited_deinit_names: list[str],
        return_type: TypeReference | None,
    ) -> list[HIRStatement]:
        previous_drop_flags = dict(self.drop_flags)
        lowered: list[HIRStatement] = []
        local_deinit_names: list[str] = []
        cleanup_span = None
        for statement in statements:
            cleanup_span = statement.span or cleanup_span
            active_deinit_names = [*inherited_deinit_names, *local_deinit_names]
            for moved_place in self._hir_moved_places_in_statement(statement):
                for place in self._drop_places_below(moved_place):
                    lowered.append(self._hir_set_drop_flag(place, False, statement.span))
            assignment_place = (
                self._hir_place_name(statement.target)
                if isinstance(statement, HIRAssignment)
                else None
            )
            if assignment_place in self.drop_flags:
                lowered.extend(
                    self._hir_cleanup_place(
                        statement.target, statement.target_type,
                        assignment_place, statement.span,
                    )
                )
            lowered_statements = self._lower_hir_statement(
                statement,
                env,
                active_deinit_names,
                return_type,
            )
            lowered.extend(lowered_statements)

            if assignment_place is not None:
                for place in self._drop_places_below(assignment_place):
                    lowered.append(self._hir_set_drop_flag(place, True, statement.span))

            if isinstance(statement, HIRVariableDeclaration):
                env[statement.symbol.name] = statement.symbol.type_ref
                if self._has_deinit(statement.symbol.type_ref):
                    local_deinit_names.append(statement.symbol.name)
                    lowered.extend(
                        self._hir_create_drop_flags(
                            statement.symbol.name, statement.symbol.type_ref,
                            True, statement.span,
                        )
                    )

            if self._hir_ends_with_terminator(lowered_statements):
                self.drop_flags = previous_drop_flags
                return lowered

        lowered.extend(
            self._hir_cleanup_calls(local_deinit_names, env, cleanup_span)
        )
        self.drop_flags = previous_drop_flags
        return lowered

    def _lower_hir_statement(
        self,
        statement: HIRStatement,
        env: dict[str, TypeReference],
        active_deinit_names: list[str],
        return_type: TypeReference | None,
    ) -> list[HIRStatement]:
        if isinstance(statement, HIRVariableDeclaration):
            errors = self._hir_expression_raised_errors(statement.initializer)
            if statement.constructor_call is not None:
                self._merge_errors(
                    errors,
                    self._hir_call_raised_errors(statement.constructor_call),
                )
            needs_split = bool(
                statement.constructor_call is not None
                and statement.constructor_call.target.raises
            ) or bool(active_deinit_names and errors)
            if not needs_split:
                return [statement]

            declaration = replace(
                statement,
                initializer=None,
                constructor_call=None,
            )
            initialization: list[HIRStatement] = []
            if statement.initializer is not None:
                initialization.append(
                    HIRAssignment(
                        target=self._hir_variable(
                            statement.symbol.name,
                            statement.symbol.type_ref,
                            statement.span,
                        ),
                        expr=statement.initializer,
                        target_type=copy.deepcopy(statement.symbol.type_ref),
                        span=statement.span,
                    )
                )
            if statement.constructor_call is not None:
                initialization.append(
                    HIRExpressionStatement(
                        expr=statement.constructor_call,
                        span=statement.span,
                    )
                )

            if active_deinit_names and errors:
                return [
                    declaration,
                    self._hir_error_cleanup_try(
                        initialization,
                        errors,
                        active_deinit_names,
                        env,
                        statement.span,
                    )
                ]
            return [declaration, *initialization]

        if isinstance(statement, HIRAssignment):
            errors = self._hir_statement_raised_errors(statement, dict(env))
            return self._hir_guarded_statements(
                [statement], errors, active_deinit_names, env, statement.span
            )

        if isinstance(statement, HIRExpressionStatement):
            errors = self._hir_expression_raised_errors(statement.expr)
            return self._hir_guarded_statements(
                [statement], errors, active_deinit_names, env, statement.span
            )

        if isinstance(statement, HIRReturn):
            if not active_deinit_names:
                return [statement]
            if statement.expr is None:
                return [
                    *self._hir_cleanup_calls(
                        active_deinit_names, env, statement.span
                    ),
                    statement,
                ]
            if return_type is None:
                raise CleanupLoweringError(
                    'Return statement reached cleanup lowering outside a function.'
                )
            temporary_name = self._next_generated_name('jack_cleanup_return_value')
            temporary = self._hir_uninitialized_temporary(
                temporary_name, return_type, statement.span
            )
            assignment = self._hir_temporary_assignment(
                temporary_name, return_type, statement.expr, statement.span
            )
            evaluated = self._hir_guarded_statements(
                [assignment],
                self._hir_expression_raised_errors(statement.expr),
                active_deinit_names,
                env,
                statement.span,
            )
            returned = HIRReturn(
                expr=self._hir_variable(temporary_name, return_type, statement.span),
                span=statement.span,
            )
            return [
                temporary,
                *evaluated,
                *self._hir_cleanup_calls(
                    active_deinit_names, env, statement.span
                ),
                returned,
            ]

        if isinstance(statement, HIRRaise):
            if not active_deinit_names:
                return [statement]
            temporary_name = self._next_generated_name('jack_cleanup_error_value')
            temporary = self._hir_uninitialized_temporary(
                temporary_name, statement.error_type, statement.span
            )
            assignment = self._hir_temporary_assignment(
                temporary_name,
                statement.error_type,
                statement.expr,
                statement.span,
            )
            evaluated = self._hir_guarded_statements(
                [assignment],
                self._hir_expression_raised_errors(statement.expr),
                active_deinit_names,
                env,
                statement.span,
            )
            raised = HIRRaise(
                expr=self._hir_variable(
                    temporary_name, statement.error_type, statement.span
                ),
                error_type=copy.deepcopy(statement.error_type),
                span=statement.span,
            )
            return [
                temporary,
                *evaluated,
                *self._hir_cleanup_calls(
                    active_deinit_names, env, statement.span
                ),
                raised,
            ]

        if isinstance(statement, HIRRethrow):
            return [
                *self._hir_cleanup_calls(
                    active_deinit_names, env, statement.span
                ),
                statement,
            ]

        if isinstance(statement, HIRPrint):
            return self._hir_guarded_statements(
                [statement],
                self._hir_expression_raised_errors(statement.expr),
                active_deinit_names,
                env,
                statement.span,
            )

        if isinstance(statement, HIRIf):
            if active_deinit_names and any(
                self._hir_expression_raised_errors(branch.condition)
                for branch in statement.branches
            ):
                return self._lower_hir_raising_if(
                    statement,
                    env,
                    active_deinit_names,
                    return_type,
                )
            branches: list[HIRIfBranch] = []
            for branch in statement.branches:
                branches.append(
                    replace(
                        branch,
                        body=self._lower_hir_block(
                            branch.body,
                            dict(env),
                            active_deinit_names,
                            return_type,
                        ),
                    )
                )
            else_body = None
            if statement.else_body is not None:
                else_body = self._lower_hir_block(
                    statement.else_body,
                    dict(env),
                    active_deinit_names,
                    return_type,
                )
            return [replace(statement, branches=branches, else_body=else_body)]

        if isinstance(statement, HIRWhile):
            if (
                active_deinit_names
                and self._hir_expression_raised_errors(statement.condition)
            ):
                return self._lower_hir_raising_while(
                    statement,
                    env,
                    active_deinit_names,
                    return_type,
                )
            return [
                replace(
                    statement,
                    body=self._lower_hir_block(
                        statement.body,
                        dict(env),
                        active_deinit_names,
                        return_type,
                    ),
                )
            ]

        if isinstance(statement, HIRFor):
            loop_env = dict(env)
            header_errors: list[RaisedError] = []
            if statement.initializer is not None:
                self._merge_errors(
                    header_errors,
                    self._hir_statement_raised_errors(
                        statement.initializer, dict(loop_env)
                    ),
                )
            self._merge_errors(
                header_errors,
                self._hir_expression_raised_errors(statement.condition),
            )
            if statement.update is not None:
                self._merge_errors(
                    header_errors,
                    self._hir_statement_raised_errors(
                        statement.update, dict(loop_env)
                    ),
                )
            needs_desugaring = bool(header_errors)
            if isinstance(statement.initializer, HIRVariableDeclaration):
                if self._has_deinit(statement.initializer.symbol.type_ref):
                    needs_desugaring = True
                if statement.initializer.constructor_call is not None:
                    needs_desugaring = True
                loop_env[
                    statement.initializer.symbol.name
                ] = statement.initializer.symbol.type_ref
            if needs_desugaring:
                return self._lower_hir_for_as_block(
                    statement,
                    env,
                    active_deinit_names,
                    return_type,
                )
            return [
                replace(
                    statement,
                    body=self._lower_hir_block(
                        statement.body,
                        dict(loop_env),
                        active_deinit_names,
                        return_type,
                    ),
                )
            ]

        if isinstance(statement, HIRTry):
            try_errors = self._hir_statement_list_raised_errors(
                statement.body, dict(env)
            )
            body = self._lower_hir_block(statement.body, dict(env), [], return_type)
            catches = [
                replace(
                    catch,
                    body=self._lower_hir_block(
                        catch.body,
                        {
                            **env,
                            **(
                                {catch.name: catch.error_type}
                                if catch.name is not None
                                else {}
                            ),
                        },
                        active_deinit_names,
                        return_type,
                    ),
                )
                for catch in statement.catches
            ]
            if active_deinit_names:
                caught = {self._type_name(catch.error_type) for catch in catches}
                for error_name in try_errors:
                    if error_name in caught:
                        continue
                    catches.append(
                        HIRCatchClause(
                            error_type=TypeReference(error_name),
                            name=None,
                            body=[
                                *self._hir_cleanup_calls(
                                    active_deinit_names, env, statement.span
                                ),
                                HIRRethrow(span=statement.span),
                            ],
                            span=statement.span,
                        )
                    )
                    caught.add(error_name)
            return [replace(statement, body=body, catches=catches)]

        if isinstance(statement, (HIRBlock, HIRUnsafeBlock)):
            return [
                replace(
                    statement,
                    body=self._lower_hir_block(
                        statement.body,
                        dict(env),
                        active_deinit_names,
                        return_type,
                    ),
                )
            ]

        raise CleanupLoweringError(
            f'Unknown HIR statement type "{type(statement).__name__}".'
        )

    def _lower_hir_raising_if(
        self,
        statement: HIRIf,
        env: dict[str, TypeReference],
        active_deinit_names: list[str],
        return_type: TypeReference | None,
    ) -> list[HIRStatement]:
        lowered_branches = [
            replace(
                branch,
                body=self._lower_hir_block(
                    branch.body,
                    dict(env),
                    active_deinit_names,
                    return_type,
                ),
            )
            for branch in statement.branches
        ]
        lowered_else = (
            None
            if statement.else_body is None
            else self._lower_hir_block(
                statement.else_body,
                dict(env),
                active_deinit_names,
                return_type,
            )
        )

        tail = lowered_else
        for branch in reversed(lowered_branches):
            condition = branch.condition
            errors = self._hir_expression_raised_errors(condition)
            if errors:
                temporary_name = self._next_generated_name(
                    'jack_cleanup_condition'
                )
                temporary = self._hir_uninitialized_temporary(
                    temporary_name, TypeReference('bool'), condition.span
                )
                assignment = self._hir_temporary_assignment(
                    temporary_name,
                    TypeReference('bool'),
                    condition,
                    condition.span,
                )
                guarded = self._hir_guarded_statements(
                    [assignment],
                    errors,
                    active_deinit_names,
                    env,
                    condition.span,
                )
                condition = self._hir_variable(
                    temporary_name, TypeReference('bool'), condition.span
                )
                branch_statement = HIRIf(
                    branches=[replace(branch, condition=condition)],
                    else_body=tail,
                    span=statement.span,
                )
                tail = [temporary, *guarded, branch_statement]
            else:
                tail = [
                    HIRIf(
                        branches=[branch],
                        else_body=tail,
                        span=statement.span,
                    )
                ]
        return tail or []

    def _lower_hir_raising_while(
        self,
        statement: HIRWhile,
        env: dict[str, TypeReference],
        active_deinit_names: list[str],
        return_type: TypeReference | None,
    ) -> list[HIRStatement]:
        lowered_body = self._lower_hir_block(
            statement.body,
            dict(env),
            active_deinit_names,
            return_type,
        )
        return self._hir_while_with_guarded_condition(
            statement.condition,
            lowered_body,
            env,
            active_deinit_names,
            statement.span,
        )

    def _hir_while_with_guarded_condition(
        self,
        condition: HIRExpression,
        lowered_body: list[HIRStatement],
        env: dict[str, TypeReference],
        active_deinit_names: list[str],
        span,
    ) -> list[HIRStatement]:
        temporary_name = self._next_generated_name('jack_cleanup_condition')
        condition_type = TypeReference('bool')
        temporary = self._hir_uninitialized_temporary(
            temporary_name, condition_type, condition.span
        )
        assignment = self._hir_temporary_assignment(
            temporary_name, condition_type, condition, condition.span
        )
        errors = self._hir_expression_raised_errors(condition)
        initial_evaluation = self._hir_guarded_statements(
            [assignment],
            errors,
            active_deinit_names,
            env,
            condition.span,
        )
        repeated_evaluation = self._hir_guarded_statements(
            [assignment],
            errors,
            active_deinit_names,
            env,
            condition.span,
        )
        loop = HIRWhile(
            condition=self._hir_variable(
                temporary_name, condition_type, condition.span
            ),
            body=[*lowered_body, *repeated_evaluation],
            span=span,
        )
        return [temporary, *initial_evaluation, loop]

    def _lower_hir_for_as_block(
        self,
        statement: HIRFor,
        env: dict[str, TypeReference],
        active_deinit_names: list[str],
        return_type: TypeReference | None,
    ) -> list[HIRStatement]:
        block_env = dict(env)
        loop_active_names = list(active_deinit_names)
        initializer_cleanup_names: list[str] = []
        lowered_initializer: list[HIRStatement] = []
        if statement.initializer is not None:
            lowered_initializer = self._lower_hir_statement(
                statement.initializer,
                block_env,
                active_deinit_names,
                return_type,
            )
            if isinstance(statement.initializer, HIRVariableDeclaration):
                symbol = statement.initializer.symbol
                block_env[symbol.name] = symbol.type_ref
                if self._has_deinit(symbol.type_ref):
                    loop_active_names.append(symbol.name)
                    initializer_cleanup_names.append(symbol.name)

        lowered_body = self._lower_hir_block(
            statement.body,
            dict(block_env),
            loop_active_names,
            return_type,
        )
        if statement.update is not None:
            lowered_body.extend(
                self._lower_hir_statement(
                    statement.update,
                    dict(block_env),
                    loop_active_names,
                    return_type,
                )
            )

        condition = statement.condition or HIRLiteralExpression(
            value=True,
            literal_type='bool',
            type_ref=TypeReference('bool'),
            read_type=TypeReference('bool'),
            span=statement.span,
        )
        if self._hir_expression_raised_errors(condition):
            lowered_loop = self._hir_while_with_guarded_condition(
                condition,
                lowered_body,
                block_env,
                loop_active_names,
                statement.span,
            )
        else:
            lowered_loop = [
                HIRWhile(
                    condition=condition,
                    body=lowered_body,
                    span=statement.span,
                )
            ]

        return [
            HIRBlock(
                body=[
                    *lowered_initializer,
                    *lowered_loop,
                    *self._hir_cleanup_calls(
                        initializer_cleanup_names, block_env, statement.span
                    ),
                ],
                span=statement.span,
            )
        ]

    def _hir_error_cleanup_try(
        self,
        body: list[HIRStatement],
        errors: Iterable[RaisedError],
        active_deinit_names: list[str],
        env: dict[str, TypeReference],
        span,
    ) -> HIRTry:
        catches = [
            HIRCatchClause(
                error_type=TypeReference(error_name),
                name=None,
                body=[
                    *self._hir_cleanup_calls(
                        active_deinit_names, env, span
                    ),
                    HIRRethrow(span=span),
                ],
                span=span,
            )
            for error_name in errors
        ]
        return HIRTry(
            body=body,
            catches=catches,
            span=span,
        )

    def _hir_guarded_statements(
        self,
        statements: list[HIRStatement],
        errors: Iterable[RaisedError],
        active_deinit_names: list[str],
        env: dict[str, TypeReference],
        span,
    ) -> list[HIRStatement]:
        error_names = list(errors)
        if not active_deinit_names or not error_names:
            return statements
        return [
            self._hir_error_cleanup_try(
                statements,
                error_names,
                active_deinit_names,
                env,
                span,
            )
        ]

    def _hir_cleanup_calls(
        self,
        names: list[str],
        env: dict[str, TypeReference],
        span=None,
    ) -> list[HIRStatement]:
        statements: list[HIRStatement] = []
        for name in reversed(names):
            if name == 'self' and self.consuming_self_type is not None:
                statements.extend(self._hir_cleanup_consuming_self(span))
                continue
            type_ref = env.get(name)
            if type_ref is None:
                raise CleanupLoweringError(
                    f'Unknown cleanup variable "{name}" during HIR lowering.'
                )
            statements.extend(
                self._hir_cleanup_place(
                    self._hir_variable(name, type_ref, span), type_ref, name, span
                )
            )
        return statements

    def _hir_cleanup_consuming_self(self, span) -> list[HIRStatement]:
        assert self.consuming_self_type is not None
        statements: list[HIRStatement] = []
        self_type = TypeReference(self.consuming_self_type.name, borrow='inout')
        receiver = self._hir_variable('self', self_type, span)
        for field in reversed(self.consuming_self_type.fields):
            if not self._has_deinit(field.type_ref):
                continue
            field_expression = HIRFieldAccessExpression(
                target=receiver,
                field_name=field.name,
                owner_type_name=self.consuming_self_type.name,
                from_view=False,
                type_ref=copy.deepcopy(field.type_ref),
                read_type=copy.deepcopy(field.type_ref),
                span=field.span or span,
            )
            statements.extend(self._hir_cleanup_place(
                field_expression, field.type_ref, f'self.{field.name}',
                field.span or span,
            ))
        return statements

    def _hir_cleanup_place(
        self, expression: HIRExpression, type_ref: TypeReference,
        place: str, span,
    ) -> list[HIRStatement]:
        cleanups = self._hir_cleanup_value(expression, type_ref, span, place)
        flag_name = self.drop_flags.get(place)
        if flag_name is None or not cleanups:
            return cleanups
        return [HIRIf(
            branches=[HIRIfBranch(
                condition=self._hir_variable(flag_name, TypeReference('bool'), span),
                body=cleanups,
                span=span,
            )],
            else_body=None,
            span=span,
        )]

    def _hir_cleanup_value(
        self, expression: HIRExpression, type_ref: TypeReference, span,
        place: str | None = None,
    ) -> list[HIRStatement]:
        if type_ref.borrow is not None or type_ref.is_slice:
            return []
        if type_ref.array_size is not None:
            if type(type_ref.array_size) is not int:
                raise CleanupLoweringError('Owned array cleanup requires a normalized extent.')
            element_type = copy.deepcopy(type_ref)
            element_type.array_size = None
            statements: list[HIRStatement] = []
            for index in reversed(range(type_ref.array_size)):
                item = HIRIndexExpression(
                    target=expression,
                    index=HIRLiteralExpression(
                        value=index,
                        literal_type='i32',
                        type_ref=TypeReference('i32'),
                        read_type=TypeReference('i32'),
                        span=span,
                    ),
                    type_ref=copy.deepcopy(element_type),
                    read_type=copy.deepcopy(element_type),
                    span=span,
                )
                child_place = f'{place}[{index}]' if place is not None else None
                if child_place is None:
                    statements.extend(self._hir_cleanup_value(item, element_type, span))
                else:
                    statements.extend(
                        self._hir_cleanup_place(item, element_type, child_place, span)
                    )
            return statements

        type_decl = self.types.get(self._type_name(type_ref))
        if not isinstance(type_decl, HIRTypeDeclaration) or type_decl.extern:
            return []
        statements: list[HIRStatement] = []
        method = next(
            (candidate for candidate in type_decl.methods if candidate.name == 'deinit'),
            None,
        )
        if method is not None:
            return [
                self._hir_deinit_call(expression, type_ref, type_decl, method, span)
            ]
        for field in reversed(type_decl.fields):
            if not self._has_deinit(field.type_ref):
                continue
            field_expression = HIRFieldAccessExpression(
                target=expression,
                field_name=field.name,
                owner_type_name=type_decl.name,
                from_view=False,
                type_ref=copy.deepcopy(field.type_ref),
                read_type=copy.deepcopy(field.type_ref),
                span=field.span or span,
            )
            child_place = f'{place}.{field.name}' if place is not None else None
            if child_place is None:
                statements.extend(self._hir_cleanup_value(
                    field_expression, field.type_ref, field.span or span
                ))
            else:
                statements.extend(self._hir_cleanup_place(
                    field_expression, field.type_ref, child_place, field.span or span
                ))
        return statements

    def _hir_create_drop_flag(
        self, owner_name: str, initialized: bool, span
    ) -> HIRVariableDeclaration:
        flag_name = self._next_generated_name(f'jack_drop_{owner_name}')
        self.drop_flags[owner_name] = flag_name
        return HIRVariableDeclaration(
            symbol=HIRVariableSymbol(
                name=flag_name,
                type_ref=TypeReference('bool'),
                synthetic=True,
                span=span,
            ),
            initializer=HIRLiteralExpression(
                value=initialized,
                literal_type='bool',
                type_ref=TypeReference('bool'),
                read_type=TypeReference('bool'),
                span=span,
            ),
            span=span,
        )

    def _hir_create_drop_flags(
        self, owner_name: str, type_ref: TypeReference,
        initialized: bool, span,
    ) -> list[HIRVariableDeclaration]:
        declarations = [
            self._hir_create_drop_flag(owner_name, initialized, span)
        ]
        if type_ref.array_size is not None and type(type_ref.array_size) is int:
            element_type = copy.deepcopy(type_ref)
            element_type.array_size = None
            if self._has_deinit(element_type):
                for index in range(type_ref.array_size):
                    declarations.extend(self._hir_create_drop_flags(
                        f'{owner_name}[{index}]', element_type, initialized, span
                    ))
            return declarations
        declaration = self.types.get(self._type_name(type_ref))
        if not isinstance(declaration, HIRTypeDeclaration) or declaration.extern:
            return declarations
        if any(method.name == 'deinit' for method in declaration.methods):
            return declarations
        for field in declaration.fields:
            if self._has_deinit(field.type_ref):
                declarations.extend(self._hir_create_drop_flags(
                    f'{owner_name}.{field.name}', field.type_ref, initialized,
                    field.span or span,
                ))
        return declarations

    def _hir_set_drop_flag(
        self, owner_name: str, initialized: bool, span
    ) -> HIRAssignment:
        flag_name = self.drop_flags[owner_name]
        return HIRAssignment(
            target=self._hir_variable(flag_name, TypeReference('bool'), span),
            expr=HIRLiteralExpression(
                value=initialized,
                literal_type='bool',
                type_ref=TypeReference('bool'),
                read_type=TypeReference('bool'),
                span=span,
            ),
            target_type=TypeReference('bool'),
            span=span,
        )

    def _hir_place_name(self, expression: HIRExpression) -> str | None:
        if isinstance(expression, HIRVariableExpression):
            return expression.name
        if isinstance(expression, HIRFieldAccessExpression):
            parent = self._hir_place_name(expression.target)
            return None if parent is None else f'{parent}.{expression.field_name}'
        if isinstance(expression, HIRIndexExpression):
            parent = self._hir_place_name(expression.target)
            if (
                parent is None
                or not isinstance(expression.index, HIRLiteralExpression)
                or type(expression.index.value) is not int
            ):
                return None
            return f'{parent}[{expression.index.value}]'
        return None

    def _drop_places_below(self, place: str) -> list[str]:
        return [
            candidate for candidate in self.drop_flags
            if candidate == place
            or candidate.startswith(f'{place}.')
            or candidate.startswith(f'{place}[')
        ]

    def _hir_moved_places_in_statement(self, statement: HIRStatement) -> set[str]:
        names: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, HIRMoveExpression):
                place = self._hir_place_name(value.expr)
                if place is not None:
                    names.add(place)
                return
            if isinstance(value, (str, int, float, bool, bytes, TypeReference)) or value is None:
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
                return
            if is_dataclass(value):
                for item in fields(value):
                    if item.name != 'span':
                        visit(getattr(value, item.name))

        visit(statement)
        return names

    def _hir_deinit_call(
        self,
        receiver: HIRExpression,
        type_ref: TypeReference,
        type_decl: HIRTypeDeclaration,
        method: HIRFunctionDeclaration,
        span=None,
    ) -> HIRExpressionStatement:
        if method is None or method.self_parameter is None:
            raise CleanupLoweringError(
                f'Type "{type_decl.name}" has no valid deinit method.'
            )
        self_type = copy.deepcopy(method.self_parameter.type_ref)
        self_borrow = HIRBorrowExpression(
            mode=self_type.borrow or 'inout',
            expr=receiver,
            type_ref=self_type,
            read_type=copy.deepcopy(self_type),
            span=span,
        )
        target = HIRCallTarget(
            kind='method',
            name=f'{type_decl.name}.deinit',
            return_type=copy.deepcopy(method.return_type),
            parameters=list(method.parameters),
            self_parameter=method.self_parameter,
            raises=list(method.raises),
            owner_type_name=type_decl.name,
            receiver_name=(
                receiver.name
                if isinstance(receiver, HIRVariableExpression)
                else 'jack_cleanup_receiver'
            ),
        )
        call = HIRCallExpression(
            target=target,
            arguments=[],
            receiver=receiver,
            implicit_self_argument=self_borrow,
            type_ref=copy.deepcopy(method.return_type),
            read_type=copy.deepcopy(method.return_type),
            span=span,
        )
        return HIRExpressionStatement(expr=call, span=span)

    def _hir_uninitialized_temporary(
        self, name: str, type_ref: TypeReference, span
    ) -> HIRVariableDeclaration:
        return HIRVariableDeclaration(
            symbol=HIRVariableSymbol(
                name=name,
                type_ref=copy.deepcopy(type_ref),
                synthetic=True,
                span=span,
            ),
            span=span,
        )

    def _hir_temporary_assignment(
        self,
        name: str,
        type_ref: TypeReference,
        expression: HIRExpression,
        span,
    ) -> HIRAssignment:
        return HIRAssignment(
            target=self._hir_variable(name, type_ref, span),
            expr=expression,
            target_type=copy.deepcopy(type_ref),
            span=span,
        )

    def _hir_variable(
        self, name: str, type_ref: TypeReference, span
    ) -> HIRVariableExpression:
        copied_type = copy.deepcopy(type_ref)
        return HIRVariableExpression(
            name=name,
            type_ref=copied_type,
            read_type=copy.deepcopy(copied_type),
            span=span,
        )

    def _hir_statement_list_raised_errors(
        self,
        statements: Iterable[HIRStatement],
        env: dict[str, TypeReference],
    ) -> list[RaisedError]:
        errors: list[RaisedError] = []
        for statement in statements:
            self._merge_errors(
                errors,
                self._hir_statement_raised_errors(statement, env),
            )
        return errors

    def _hir_ends_with_terminator(
        self, statements: list[HIRStatement]
    ) -> bool:
        if not statements:
            return False
        last = statements[-1]
        if isinstance(last, HIRBlock):
            return self._hir_ends_with_terminator(last.body)
        return isinstance(last, (HIRReturn, HIRRaise, HIRRethrow))

    def _collect_hir_names(self, program: HIRProgram) -> set[str]:
        names: set[str] = set()
        for declaration in program.declarations:
            if hasattr(declaration, 'name'):
                names.add(declaration.name)
            if isinstance(declaration, HIRGlobalVariable):
                names.add(declaration.symbol.name)
            elif isinstance(declaration, HIRFunctionDeclaration):
                names.update(parameter.name for parameter in declaration.parameters)
                self._collect_hir_statement_names(declaration.body, names)
            elif isinstance(declaration, HIRTypeDeclaration):
                names.update(field.name for field in declaration.fields)
                for method in declaration.methods:
                    names.add(method.name)
                    names.update(parameter.name for parameter in method.parameters)
                    self._collect_hir_statement_names(method.body, names)
        self._collect_hir_statement_names(program.body, names)
        return names

    def _collect_hir_statement_names(
        self, statements: Iterable[HIRStatement], names: set[str]
    ) -> None:
        for statement in statements:
            if isinstance(statement, HIRVariableDeclaration):
                names.add(statement.symbol.name)
            elif isinstance(statement, HIRIf):
                for branch in statement.branches:
                    self._collect_hir_statement_names(branch.body, names)
                if statement.else_body is not None:
                    self._collect_hir_statement_names(statement.else_body, names)
            elif isinstance(statement, (HIRWhile, HIRFor)):
                if isinstance(statement, HIRFor):
                    self._collect_hir_statement_names(
                        [
                            part
                            for part in (statement.initializer, statement.update)
                            if part is not None
                        ],
                        names,
                    )
                self._collect_hir_statement_names(statement.body, names)
            elif isinstance(statement, HIRTry):
                self._collect_hir_statement_names(statement.body, names)
                for catch in statement.catches:
                    if catch.name is not None:
                        names.add(catch.name)
                    self._collect_hir_statement_names(catch.body, names)
            elif isinstance(statement, (HIRBlock, HIRUnsafeBlock)):
                self._collect_hir_statement_names(statement.body, names)
