from __future__ import annotations

import re
from dataclasses import dataclass, field


class LLVMValidationError(Exception):
    pass


_SSA_DEFINITION = re.compile(r'^(%[-A-Za-z$._0-9]+)\s*=')
_BRANCH_TARGET = re.compile(r'\blabel\s+%([-A-Za-z$._0-9]+)')


def _is_terminator(instruction: str) -> bool:
    return instruction.startswith(('ret ', 'br ', 'switch ', 'unreachable'))


@dataclass(frozen=True)
class LLVMFunction:
    name: str
    return_type: str
    parameters: tuple[tuple[str, str], ...]
    blocks: tuple[tuple[str, tuple[str, ...]], ...]

    def render(self) -> str:
        params = ', '.join(f'{type_name} %{name}' for type_name, name in self.parameters)
        lines = [f'define {self.return_type} @{quoted(self.name)}({params}) {{']
        for label, instructions in self.blocks:
            lines.append(f'{label}:')
            lines.extend(f'  {instruction}' for instruction in instructions)
        lines.append('}')
        return '\n'.join(lines)


@dataclass
class LLVMModule:
    type_definitions: list[str] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    declarations: list[str] = field(default_factory=list)
    functions: list[LLVMFunction] = field(default_factory=list)

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
            '%jack.error.payload' in instruction
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
            references = re.findall(r'%(?:"(?:[^"\\]|\\.)*"|[-A-Za-z$._0-9]+)', payload_body)
            missing = next((name for name in references if name not in type_names), None)
            if missing is not None:
                raise LLVMValidationError(
                    f'LLVM error payload uses undeclared type {missing}.'
                )
        for definition in self.type_definitions:
            if definition.startswith('%jack.result.') and '%jack.error.payload' not in definition:
                raise LLVMValidationError(
                    'LLVM result envelope does not contain the declared error payload type.'
                )
        for function in self.functions:
            if function.return_type.startswith('%jack.result.') and function.return_type not in type_names:
                raise LLVMValidationError(
                    f'Function "{function.name}" uses undeclared result type {function.return_type}.'
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
                if _is_terminator(instruction) and index != len(instructions) - 1:
                    raise LLVMValidationError(
                        f'Block "{label}" contains instructions after its terminator.'
                    )
                match = _SSA_DEFINITION.match(instruction)
                if match:
                    name = match.group(1)
                    if name in names:
                        raise LLVMValidationError(
                            f'Duplicate SSA name {name} in function "{function.name}".'
                        )
                    names.add(name)
                for target in _BRANCH_TARGET.findall(instruction):
                    if target not in label_set:
                        raise LLVMValidationError(
                            f'Branch targets missing block "{target}" in function "{function.name}".'
                        )
            if not instructions or not _is_terminator(instructions[-1]):
                raise LLVMValidationError(
                    f'Block "{label}" in function "{function.name}" has no terminator.'
                )

    def render(self) -> str:
        self.validate()
        sections = [
            '\n'.join(self.type_definitions),
            '\n'.join(self.globals),
            '\n'.join(dict.fromkeys(self.declarations)),
            '\n\n'.join(function.render() for function in self.functions),
        ]
        return '\n\n'.join(section for section in sections if section) + '\n'


def quoted(name: str) -> str:
    escaped = name.replace('\\', '\\5C').replace('"', '\\22')
    return f'"{escaped}"'
