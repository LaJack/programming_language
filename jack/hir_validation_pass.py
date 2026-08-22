from __future__ import annotations

from dataclasses import fields, is_dataclass

from .builtin_types import BUILTIN_TYPE_SPECS
from .hir_nodes import (
    HIRCallExpression,
    HIRCatchClause,
    HIRFunctionDeclaration,
    HIRNode,
    HIRProgram,
    HIRRaise,
    HIRTypeDeclaration,
    HIRVariableSymbol,
    HIRViewDeclaration,
)
from .source_model import SourceSpan, TypeReference


class HIRValidationError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.span = span


class BackendHIRValidator:
    def __init__(self, program: HIRProgram) -> None:
        self.program = program
        self.types = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, (HIRTypeDeclaration, HIRViewDeclaration))
        }
        self.functions = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        }
        for declaration in program.declarations:
            if isinstance(declaration, HIRTypeDeclaration):
                for method in declaration.methods:
                    self.functions[f'{declaration.name}.{method.name}'] = method

    def validate(self) -> HIRProgram:
        self._walk(self.program, self.program.span)
        return self.program

    def _walk(self, value: object, span: SourceSpan | None) -> None:
        if value is None or isinstance(value, (str, int, float, bool, bytes, SourceSpan)):
            return
        if isinstance(value, TypeReference):
            self._type_reference(value, value.span or span)
            return
        if isinstance(value, HIRVariableSymbol) and value.comptime:
            self._fail(
                f'Comptime symbol "{value.name}" reached a native backend.',
                value.span or span,
            )
        if isinstance(value, HIRCallExpression):
            self._call(value)
        elif isinstance(value, HIRRaise):
            self._error_type(value.error_type, value.span)
        elif isinstance(value, HIRCatchClause):
            self._error_type(value.error_type, value.span)
        elif isinstance(value, HIRFunctionDeclaration):
            seen: set[str] = set()
            for error_type in value.raises:
                self._error_type(error_type, value.span)
                if error_type.name in seen:
                    self._fail(
                        f'Function "{value.name}" has duplicate error "{error_type.name}".',
                        value.span,
                    )
                seen.add(error_type.name)
        if isinstance(value, dict):
            for key, item in value.items():
                self._walk(key, span)
                self._walk(item, span)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                self._walk(item, span)
            return
        if is_dataclass(value):
            child_span = value.span if isinstance(value, HIRNode) else span
            for item in fields(value):
                self._walk(getattr(value, item.name), child_span)

    def _type_reference(
        self, type_ref: TypeReference, span: SourceSpan | None
    ) -> None:
        if type_ref.array_size is not None and (
            type(type_ref.array_size) is not int or type_ref.array_size < 0
        ):
            self._fail(
                f'Runtime array type "{type_ref.name}" has a non-normalized extent.',
                span,
            )
        if type_ref.name == 'type':
            self._fail('Compile-time type values cannot reach a native backend.', span)
        known = {
            *BUILTIN_TYPE_SPECS,
            'void',
            'str',
            'c_void',
            'c_char',
            'null',
            *self.types,
        }
        if type_ref.name not in known:
            self._fail(f'Runtime type "{type_ref.name}" is not concrete.', span)
        for argument in type_ref.arguments:
            if not isinstance(argument, TypeReference):
                self._fail(
                    f'Runtime type "{type_ref.name}" contains a comptime argument.',
                    span,
                )
            self._type_reference(argument, argument.span or span)

    def _call(self, call: HIRCallExpression) -> None:
        target = call.target
        if target.kind == 'len':
            if target.name != 'len' or len(call.arguments) != 1:
                self._fail('Malformed len call target in backend HIR.', call.span)
            return
        if target.kind == 'builtin_conversion':
            if target.name not in BUILTIN_TYPE_SPECS or len(call.arguments) != 1:
                self._fail('Malformed builtin conversion target in backend HIR.', call.span)
            return
        if target.kind not in {'function', 'method'}:
            self._fail(f'Unknown HIR call target kind "{target.kind}".', call.span)
        declaration = self.functions.get(target.name)
        if declaration is None:
            self._fail(f'Call target "{target.name}" has no declaration.', call.span)
        if (
            len(call.arguments) != len(target.parameters)
            or target.return_type != declaration.return_type
            or [parameter.type_ref for parameter in target.parameters]
            != [parameter.type_ref for parameter in declaration.parameters]
            or target.raises != declaration.raises
            or target.extern != declaration.extern
            or target.abi != declaration.abi
        ):
            self._fail(
                f'Call target "{target.name}" has incomplete declaration metadata.',
                call.span,
            )
        if target.kind == 'method':
            owner = target.owner_type_name
            if (
                not owner
                or target.self_parameter is None
                or call.receiver is None
                or call.implicit_self_argument is None
                or not target.name.startswith(f'{owner}.')
            ):
                self._fail(f'Method call target "{target.name}" is incomplete.', call.span)
            if target.name.endswith('.deinit'):
                owner_type = self.types.get(owner)
                if not isinstance(owner_type, HIRTypeDeclaration) or not any(
                    method.name == 'deinit' and method.self_parameter is not None
                    for method in owner_type.methods
                ):
                    self._fail(f'Invalid cleanup call "{target.name}".', call.span)

    def _error_type(
        self, type_ref: TypeReference, span: SourceSpan | None
    ) -> None:
        if (
            type_ref.arguments
            or type_ref.array_size is not None
            or type_ref.is_slice
            or type_ref.borrow is not None
        ):
            self._fail('Error payload references must be concrete struct types.', span)
        declaration = self.types.get(type_ref.name)
        if not isinstance(declaration, HIRTypeDeclaration) or declaration.extern:
            self._fail(
                f'Error payload "{type_ref.name}" is not a concrete struct type.',
                span,
            )
        self._validate_error_fields(declaration, set(), span)

    def _validate_error_fields(
        self,
        declaration: HIRTypeDeclaration,
        seen: set[str],
        span: SourceSpan | None,
    ) -> None:
        if declaration.name in seen:
            return
        seen.add(declaration.name)
        if any(method.name == 'deinit' for method in declaration.methods):
            self._fail(
                f'Error payload "{declaration.name}" cannot define deinit.', span
            )
        for field in declaration.fields:
            field_type = field.type_ref
            if field_type.borrow is not None or field_type.is_slice or field_type.name in {
                'str', 'c_char', 'c_void', 'void', 'type'
            }:
                self._fail(
                    f'Error payload "{declaration.name}" contains an unsupported field.',
                    field.span or span,
                )
            nested = self.types.get(field_type.name)
            if isinstance(nested, HIRTypeDeclaration):
                if nested.extern:
                    self._fail(
                        f'Error payload "{declaration.name}" contains an opaque field.',
                        field.span or span,
                    )
                self._validate_error_fields(nested, seen, field.span or span)

    @staticmethod
    def _fail(message: str, span: SourceSpan | None) -> None:
        raise HIRValidationError(message, span)


def validate_backend_hir(program: HIRProgram) -> HIRProgram:
    return BackendHIRValidator(program).validate()


__all__ = ['BackendHIRValidator', 'HIRValidationError', 'validate_backend_hir']
