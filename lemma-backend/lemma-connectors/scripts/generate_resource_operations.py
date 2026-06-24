from __future__ import annotations

import argparse
import json
import keyword
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


ACTION_NAMES = {
    "batchDelete": "batch_delete",
    "batchModify": "batch_modify",
    "getProfile": "get_profile",
    "quickAdd": "quick_add",
}

RESOURCE_NAMESPACE_TOKENS = {
    "gmail": {"gmail"},
    "google_calendar": {"calendar"},
    "google_drive": {"drive"},
    "google_docs": {"docs"},
    "google_sheets": {"sheets"},
}

RESOURCE_PREFIX_STRIP = {
    "gmail": {"users"},
}

ACTION_VERBS = {
    "add",
    "append",
    "approve",
    "archive",
    "batch",
    "clear",
    "copy",
    "create",
    "delete",
    "disable",
    "empty",
    "enable",
    "expand",
    "export",
    "generate",
    "get",
    "hide",
    "import",
    "insert",
    "list",
    "modify",
    "move",
    "notify",
    "patch",
    "query",
    "remove",
    "rename",
    "restore",
    "search",
    "send",
    "set",
    "stop",
    "trash",
    "unhide",
    "untrash",
    "update",
    "watch",
}


def snake_case(value: str) -> str:
    if value in ACTION_NAMES:
        return ACTION_NAMES[value]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = value.replace("-", "_").replace(".", "_")
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value.lower()


def pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in snake_case(value).split("_") if part)


def safe_identifier(value: str) -> str:
    value = snake_case(value)
    if not value:
        value = "operation"
    if keyword.iskeyword(value):
        return f"{value}_"
    return value


def operation_tokens(tool: dict[str, Any], app_name: str) -> tuple[list[str], bool]:
    operation_id = str(tool.get("operation_id") or "").strip()
    if operation_id and "." in operation_id:
        tokens = [snake_case(part) for part in operation_id.split(".") if part]
        while tokens and tokens[0] in RESOURCE_NAMESPACE_TOKENS.get(app_name, set()):
            tokens = tokens[1:]
        while len(tokens) > 1 and tokens[0] in RESOURCE_PREFIX_STRIP.get(app_name, set()):
            tokens = tokens[1:]
        return tokens, True
    fallback = snake_case(operation_id or tool["name"])
    return [token for token in fallback.split("_") if token], False


def logical_operation_name(tool: dict[str, Any], app_name: str) -> str:
    tokens, _ = operation_tokens(tool, app_name)
    if tokens:
        return "_".join(tokens)
    return snake_case(tool["name"])


def operation_parts(tool: dict[str, Any], app_name: str) -> tuple[str, str, str]:
    tokens, came_from_dotted_id = operation_tokens(tool, app_name)
    operation_name = logical_operation_name(tool, app_name)
    if not tokens:
        return "root", "run", operation_name

    if came_from_dotted_id:
        if len(tokens) == 1:
            return "root", tokens[0], operation_name
        return "_".join(tokens[:-1]), tokens[-1], operation_name

    if len(tokens) == 1:
        return "root", tokens[0], operation_name
    if tokens[0] in ACTION_VERBS:
        return "_".join(tokens[1:]) or "root", tokens[0], operation_name
    return "_".join(tokens[:-1]) or "root", tokens[-1], operation_name


def normalize_sentence(value: str) -> str:
    compact = " ".join((value or "").split()).strip()
    if not compact:
        return "Performs the operation."
    if compact[-1] not in ".!?":
        compact += "."
    return compact


_VALID_ESCAPE_CHARS = frozenset("\\\"'nrtabfv01234567xNuU\n")


def _description_has_invalid_escape(text: str) -> bool:
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\":
            if i + 1 >= n or text[i + 1] not in _VALID_ESCAPE_CHARS:
                return True
        i += 1
    return False


def _format_method_docstring(description: str, parameter_hint: str) -> str:
    if not _description_has_invalid_escape(description):
        safe = description.replace('"""', '\\"\\"\\"')
        return f'"""{safe}\n\nImportant inputs: {parameter_hint}"""'
    if '"""' not in description:
        return f'r"""{description}\n\nImportant inputs: {parameter_hint}"""'
    safe = description.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""{safe}\n\nImportant inputs: {parameter_hint}"""'



def build_operation_models(tool: dict[str, Any], operation_name: str) -> str:
    input_name = pascal_case(f"{operation_name}_input")
    output_name = pascal_case(f"{operation_name}_output")
    tool_base = pascal_case(tool["name"])
    return "\n".join(
        [
            f"class {input_name}({tool_base}ToolInput):",
            f'    """Operation input for `{operation_name}`."""',
            "    pass",
            "",
            f"class {output_name}({tool_base}ToolOutput):",
            f'    """Operation output for `{operation_name}`."""',
            "    pass",
        ]
    )


def build_method(tool: dict[str, Any], operation_name: str, action: str) -> str:
    input_name = pascal_case(f"{operation_name}_input")
    output_name = pascal_case(f"{operation_name}_output")
    method_name = safe_identifier(action)
    parameter_names = [
        safe_identifier(parameter.get("python_name") or parameter["name"])
        for parameter in tool.get("parameters") or []
    ]
    if tool.get("request_body") is not None:
        parameter_names.append("body")
    parameter_hint = ", ".join(parameter_names) if parameter_names else "No explicit inputs."
    description = normalize_sentence(tool.get("description") or "")
    docstring = _format_method_docstring(description, parameter_hint)
    return "\n".join(
        [
            "    @operation(",
            f"        name={operation_name!r},",
            f"        title={pascal_case(operation_name)!r},",
            f"        input_model={input_name},",
            f"        output_model={output_name},",
            f"        tools_used=({tool['name']!r},),",
            f"        tags=tuple({tool.get('tags', [])!r}),",
            "    )",
            f"    async def {method_name}(self, data: {input_name}) -> {output_name}:",
            f"        {docstring}",
            f"        tool = self._client.get_tool({tool['name']!r})",
            "        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))",
            "        return output_name.model_validate(coerce_tool_result(result))".replace(
                "output_name", output_name
            ),
        ]
    )


def build_resource_file(
    *,
    app_name: str,
    resource: str,
    tools: list[dict[str, Any]],
) -> str:
    model_sections: list[str] = []
    method_sections: list[str] = []
    tool_type_imports: list[str] = []

    for tool in tools:
        _, action, operation_name = operation_parts(tool, app_name)
        tool_base = pascal_case(tool["name"])
        tool_type_imports.append(f"{tool_base}ToolInput")
        tool_type_imports.append(f"{tool_base}ToolOutput")
        model_sections.append(build_operation_models(tool, operation_name))
        model_sections.append("")
        method_sections.append(build_method(tool, operation_name, action))
        method_sections.append("")

    class_name = pascal_case(f"{app_name}_{resource}_resource")
    tool_type_import_line = (
        f"from lemma_connectors.{app_name}.generated.tool_types import {', '.join(sorted(set(tool_type_imports)))}"
    )

    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            tool_type_import_line,
            "from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation",
            "",
            *model_sections,
            f"class {class_name}(BaseResourceClient):",
            f'    """Operations for the `{resource}` resource."""',
            "",
            *method_sections[:-1],
            "",
        ]
    )


def build_init_file(
    app_name: str,
    resources: list[str],
    operation_to_resource: dict[str, str],
) -> str:
    registry_lines = []
    for slug in resources:
        module_path = f"lemma_connectors.{app_name}.resources.{slug}"
        class_name = pascal_case(f"{app_name}_{slug}_resource")
        registry_lines.append(f"    {slug!r}: ({module_path!r}, {class_name!r}),")
    operation_lines = [
        f"    {operation_name!r}: {slug!r},"
        for operation_name, slug in sorted(operation_to_resource.items())
    ]
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            "import importlib",
            "",
            "OPERATION_TO_RESOURCE: dict[str, str] = {",
            *operation_lines,
            "}",
            "",
            "RESOURCE_REGISTRY: dict[str, tuple[str, str]] = {",
            *registry_lines,
            "}",
            "",
            "",
            "def build_resource(client, resource_slug: str):",
            '    """Lazily import and build a single resource client by slug."""',
            "    module_path, class_name = RESOURCE_REGISTRY[resource_slug]",
            "    module = importlib.import_module(module_path)",
            "    return getattr(module, class_name)(client)",
            "",
            "",
            "def build_resources(client):",
            '    """Eagerly build all resource clients (backward-compatible)."""',
            "    return {slug: build_resource(client, slug) for slug in RESOURCE_REGISTRY}",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    metadata = json.loads(Path(args.metadata).read_text())
    tools_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    operation_to_resource: dict[str, str] = {}
    for tool in metadata["tools"]:
        resource, _, operation_name = operation_parts(tool, args.app)
        tools_by_resource[resource].append(tool)
        operation_to_resource[operation_name] = resource

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for resource, tools in sorted(tools_by_resource.items()):
        file_path = output_dir / f"{resource}.py"
        file_path.write_text(
            build_resource_file(
                app_name=args.app,
                resource=resource,
                tools=tools,
            )
        )

    (output_dir / "__init__.py").write_text(
        build_init_file(args.app, sorted(tools_by_resource), operation_to_resource)
    )


if __name__ == "__main__":
    main()
