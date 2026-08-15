from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .source_model import SourceSpan


class LLVMValidationError(Exception):
    pass


_SSA_DEFINITION = re.compile(r'^(%[-A-Za-z$._0-9]+)\s*=')
_BRANCH_TARGET = re.compile(r'\blabel\s+%([-A-Za-z$._0-9]+)')


def _is_terminator(instruction: str) -> bool:
    return instruction.startswith(('ret ', 'br ', 'switch ', 'unreachable'))


@dataclass(frozen=True)
class LLVMInstruction:
    text: str
    span: SourceSpan | None = None


@dataclass(frozen=True)
class LLVMFunction:
    name: str
    return_type: str
    parameters: tuple[tuple[str, str], ...]
    blocks: tuple[tuple[str, tuple[LLVMInstruction | str, ...]], ...]
    span: SourceSpan | None = None
    debug_name: str | None = None

    def render(self, debug_info: '_LLVMDebugInfo | None' = None) -> str:
        params = ', '.join(f'{type_name} %{name}' for type_name, name in self.parameters)
        header = f'define {self.return_type} @{quoted(self.name)}({params})'
        if debug_info is not None and self.name in debug_info.subprograms:
            header += f' !dbg !{debug_info.subprograms[self.name]}'
        lines = [header + ' {']
        for label, instructions in self.blocks:
            lines.append(f'{label}:')
            for instruction in instructions:
                rendered = (
                    _instruction_text(instruction)
                    if debug_info is None
                    else debug_info.render_instruction(self, instruction)
                )
                lines.append(f'  {rendered}')
        lines.append('}')
        return '\n'.join(lines)


@dataclass
class LLVMModule:
    type_definitions: list[str] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    declarations: list[str] = field(default_factory=list)
    functions: list[LLVMFunction] = field(default_factory=list)
    debug: bool = False
    optimization: int = 0

    def validate(self) -> None:
        type_names: set[str] = set()
        for definition in self.type_definitions:
            name, separator, _ = definition.partition(' = type ')
            if not separator:
                raise LLVMValidationError(f'Invalid LLVM type definition: {definition}')
            if name in type_names:
                raise LLVMValidationError(f'Duplicate LLVM type definition {name}.')
            type_names.add(name)

        function_names: set[str] = set()
        for function in self.functions:
            if function.name in function_names:
                raise LLVMValidationError(
                    f'Duplicate LLVM function definition "{function.name}".'
                )
            function_names.add(function.name)
            self._validate_function(function)

        uses_payload = any(
            '%jack.error.payload' in definition
            for definition in self.type_definitions
        ) or any(
            '%jack.error.payload' in _instruction_text(instruction)
            for function in self.functions
            for _, instructions in function.blocks
            for instruction in instructions
        )
        if uses_payload and '%jack.error.payload' not in type_names:
            raise LLVMValidationError('LLVM error payload type is not declared.')
        payload_definition = next(
            (
                definition
                for definition in self.type_definitions
                if definition.startswith('%jack.error.payload = type ')
            ),
            None,
        )
        if payload_definition is not None:
            payload_body = payload_definition.split(' = type ', 1)[1]
            references = re.findall(
                r'%(?:"(?:[^"\\]|\\.)*"|[-A-Za-z$._0-9]+)', payload_body
            )
            missing = next((name for name in references if name not in type_names), None)
            if missing is not None:
                raise LLVMValidationError(
                    f'LLVM error payload uses undeclared type {missing}.'
                )
        for definition in self.type_definitions:
            if (
                definition.startswith('%jack.result.')
                and '%jack.error.payload' not in definition
            ):
                raise LLVMValidationError(
                    'LLVM result envelope does not contain the declared error payload type.'
                )
        for function in self.functions:
            if (
                function.return_type.startswith('%jack.result.')
                and function.return_type not in type_names
            ):
                raise LLVMValidationError(
                    f'Function "{function.name}" uses undeclared result type '
                    f'{function.return_type}.'
                )

    @staticmethod
    def _validate_function(function: LLVMFunction) -> None:
        if not function.blocks:
            raise LLVMValidationError(f'Function "{function.name}" has no blocks.')
        labels = [label for label, _ in function.blocks]
        if len(labels) != len(set(labels)):
            raise LLVMValidationError(f'Function "{function.name}" has duplicate labels.')
        label_set = set(labels)
        names = {f'%{name}' for _, name in function.parameters}
        if len(names) != len(function.parameters):
            raise LLVMValidationError(f'Function "{function.name}" has duplicate parameters.')
        for label, instructions in function.blocks:
            for index, instruction in enumerate(instructions):
                text = _instruction_text(instruction)
                if _is_terminator(text) and index != len(instructions) - 1:
                    raise LLVMValidationError(
                        f'Block "{label}" contains instructions after its terminator.'
                    )
                match = _SSA_DEFINITION.match(text)
                if match:
                    name = match.group(1)
                    if name in names:
                        raise LLVMValidationError(
                            f'Duplicate SSA name {name} in function "{function.name}".'
                        )
                    names.add(name)
                for target in _BRANCH_TARGET.findall(text):
                    if target not in label_set:
                        raise LLVMValidationError(
                            f'Branch targets missing block "{target}" in function '
                            f'"{function.name}".'
                        )
            if not instructions or not _is_terminator(
                _instruction_text(instructions[-1])
            ):
                raise LLVMValidationError(
                    f'Block "{label}" in function "{function.name}" has no terminator.'
                )

    def render(self) -> str:
        self.validate()
        debug_info = _LLVMDebugInfo(self) if self.debug else None
        sections = [
            '\n'.join(self.type_definitions),
            '\n'.join(self.globals),
            '\n'.join(dict.fromkeys(self.declarations)),
            '\n\n'.join(function.render(debug_info) for function in self.functions),
        ]
        if debug_info is not None and debug_info.files:
            sections.append(debug_info.render())
        return '\n\n'.join(section for section in sections if section) + '\n'


class _LLVMDebugInfo:
    def __init__(self, module: LLVMModule) -> None:
        paths = {
            span.source_path
            for function in module.functions
            for span in self._function_spans(function)
            if span.source_path is not None
        }
        self.files: dict[str, int] = {}
        self.compile_units: dict[str, int] = {}
        self.subprograms: dict[str, int] = {}
        self.locations: dict[tuple[str, str, int, int], int] = {}
        self.scopes: dict[tuple[str, str], int] = {}
        self.nodes: list[tuple[int, str]] = []
        self.next_id = 0
        for path in sorted(paths):
            source = Path(path)
            self.files[path] = self._add(
                f'!DIFile(filename: "{_metadata_string(source.name)}", '
                f'directory: "{_metadata_string(str(source.parent))}")'
            )
        for path in sorted(paths):
            self.compile_units[path] = self._add(
                'distinct !DICompileUnit('
                f'language: DW_LANG_C11, file: !{self.files[path]}, '
                'producer: "jack", '
                f'isOptimized: {str(module.optimization > 0).lower()}, '
                'runtimeVersion: 0, emissionKind: FullDebug, nameTableKind: None)'
            )
        if not paths:
            return
        type_list = self._add('!{null}')
        self.subroutine_type = self._add(f'!DISubroutineType(types: !{type_list})')
        for function in module.functions:
            span = self._function_span(function)
            if span is None or span.source_path is None:
                continue
            display_name = function.debug_name or function.name
            self.subprograms[function.name] = self._add(
                'distinct !DISubprogram('
                f'name: "{_metadata_string(display_name)}", '
                f'linkageName: "{_metadata_string(function.name)}", '
                f'scope: !{self.files[span.source_path]}, '
                f'file: !{self.files[span.source_path]}, line: {span.start_line}, '
                f'type: !{self.subroutine_type}, scopeLine: {span.start_line}, '
                'spFlags: DISPFlagDefinition, '
                f'unit: !{self.compile_units[span.source_path]})'
            )
        for function in module.functions:
            for _, instructions in function.blocks:
                for instruction in instructions:
                    self._location(function, instruction)
        self.dwarf_flag = self._add('!{i32 7, !"Dwarf Version", i32 5}')
        self.debug_flag = self._add('!{i32 2, !"Debug Info Version", i32 3}')
        self.ident = self._add('!{!"jack"}')

    @staticmethod
    def _function_spans(function: LLVMFunction) -> list[SourceSpan]:
        spans: list[SourceSpan] = []
        if function.span is not None:
            spans.append(function.span)
        spans.extend(
            instruction.span
            for _, instructions in function.blocks
            for instruction in instructions
            if isinstance(instruction, LLVMInstruction) and instruction.span is not None
        )
        return spans

    def _function_span(self, function: LLVMFunction) -> SourceSpan | None:
        spans = self._function_spans(function)
        return spans[0] if spans else None

    def _add(self, body: str) -> int:
        identifier = self.next_id
        self.next_id += 1
        self.nodes.append((identifier, body))
        return identifier

    def _location(
        self, function: LLVMFunction, instruction: LLVMInstruction | str
    ) -> int | None:
        if not isinstance(instruction, LLVMInstruction) or instruction.span is None:
            return None
        span = instruction.span
        path = span.source_path
        subprogram = self.subprograms.get(function.name)
        if path is None or subprogram is None:
            return None
        scope_key = (function.name, path)
        scope = self.scopes.get(scope_key)
        function_span = self._function_span(function)
        if scope is None:
            if function_span is not None and function_span.source_path == path:
                scope = subprogram
            else:
                scope = self._add(
                    f'!DILexicalBlockFile(scope: !{subprogram}, '
                    f'file: !{self.files[path]}, discriminator: 0)'
                )
            self.scopes[scope_key] = scope
        key = (function.name, path, span.start_line, span.start_column)
        location = self.locations.get(key)
        if location is None:
            location = self._add(
                f'!DILocation(line: {span.start_line}, '
                f'column: {max(span.start_column, 1)}, scope: !{scope})'
            )
            self.locations[key] = location
        return location

    def render_instruction(
        self, function: LLVMFunction, instruction: LLVMInstruction | str
    ) -> str:
        text = _instruction_text(instruction)
        location = self._location(function, instruction)
        return text if location is None else f'{text}, !dbg !{location}'

    def render(self) -> str:
        compile_units = ', '.join(
            f'!{self.compile_units[path]}' for path in sorted(self.compile_units)
        )
        lines = [
            f'!llvm.dbg.cu = !{{{compile_units}}}',
            f'!llvm.module.flags = !{{!{self.dwarf_flag}, !{self.debug_flag}}}',
            f'!llvm.ident = !{{!{self.ident}}}',
            '',
        ]
        lines.extend(f'!{identifier} = {body}' for identifier, body in self.nodes)
        return '\n'.join(lines)


def _instruction_text(instruction: LLVMInstruction | str) -> str:
    return instruction.text if isinstance(instruction, LLVMInstruction) else instruction


def _metadata_string(value: str) -> str:
    return value.replace('\\', '\\5C').replace('"', '\\22')


def quoted(name: str) -> str:
    escaped = name.replace('\\', '\\5C').replace('"', '\\22')
    return f'"{escaped}"'
