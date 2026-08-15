from __future__ import annotations

from dataclasses import dataclass, field


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

    def render(self) -> str:
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
