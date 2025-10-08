"""Utilities for rendering WiX authoring for the windows-package action."""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path
from uuid import UUID, uuid5

from jinja2 import Environment, StrictUndefined, select_autoescape

__all__ = [
    "FileSpecification",
    "TemplateError",
    "TemplateOptions",
    "parse_file_specification",
    "prepare_template_options",
    "render_default_wxs",
]


_env = Environment(
    autoescape=select_autoescape(default_for_string=True),
    undefined=StrictUndefined,
)


def render(jinja_template_string: str, /, **context: object) -> str:
    """Render ``jinja_template_string`` with the shared environment."""
    return _env.from_string(jinja_template_string).render(**context)


_DEFAULT_TEMPLATE = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<Wix xmlns=\"http://wixtoolset.org/schemas/v4/wxs\">
  {% macro render_directory(node) -%}
  <Directory Id=\"{{ node.id }}\" Name=\"{{ node.name }}\">
    {% for component in node.components -%}
    <Component Id=\"{{ component.id }}\" Guid=\"{{ component.guid }}\">
      <File Source=\"{{ component.source }}\" Name=\"{{ component.name }}\" />
    </Component>
    {% endfor -%}
    {% for child in node.children -%}
    {{ render_directory(child) | indent(4) }}
    {% endfor -%}
  </Directory>
  {%- endmacro %}
  <Package
      Name=\"{{ product_name }}\"
      Manufacturer=\"{{ manufacturer }}\"
      Version=\"{{ version }}\"
      UpgradeCode=\"{{ upgrade_code }}\"
      Language=\"1033\">
    <MediaTemplate EmbedCab=\"yes\" CompressionLevel=\"high\" />
    <MajorUpgrade
        AllowDowngrades=\"no\"
        DowngradeErrorMessage=\"A newer version of {{ product_name }} is installed.\"
    />
    {% if description -%}
    <SummaryInformation
        Description=\"{{ description }}\"
        Manufacturer=\"{{ manufacturer }}\"
    />
    {% endif -%}
    {% if license_path -%}
    <WixVariable Id=\"WixUILicenseRtf\" Value=\"{{ license_path }}\" />
    {% endif -%}
    <StandardDirectory Id=\"{{ program_files_directory }}\">
      {{ render_directory(root_directory) | indent(6) }}
    </StandardDirectory>
    <Feature
        Id=\"MainFeature\"
        Title=\"{{ product_name }}\"
        Level=\"1\"
        Display=\"expand\"
        Absent=\"disallow\"
    >
      {% for component in components -%}
      <ComponentRef Id=\"{{ component.id }}\" />
      {% endfor -%}
    </Feature>
    <UI>
      <UIRef Id=\"WixUI_InstallDir\" />
      <Property Id=\"WIXUI_INSTALLDIR\" Value=\"INSTALLFOLDER\" />
    </UI>
  </Package>
</Wix>
"""


_WINDOWS_INSTALLER_NAMESPACE = uuid5(UUID(int=0), "shared-actions/windows-installer")


_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


class TemplateError(ValueError):
    """Raised when inputs cannot be converted to WiX authoring."""


class Component(typ.NamedTuple):
    """A single WiX component containing one file."""

    id: str
    guid: str
    source: str
    name: str


class DirectoryNode:
    """A directory in the install tree containing child nodes and components."""

    __slots__ = ("_children_by_name", "children", "components", "id", "name")

    def __init__(self, identifier: str, name: str) -> None:
        self.id = identifier
        self.name = name
        self.components: list[Component] = []
        self.children: list[DirectoryNode] = []
        self._children_by_name: dict[str, DirectoryNode] = {}

    def child(self, identifier: str, name: str) -> DirectoryNode:
        """Return an existing or newly-created child directory."""
        node = self._children_by_name.get(name)
        if node is None:
            node = DirectoryNode(identifier, name)
            self._children_by_name[name] = node
            self.children.append(node)
        return node


class FileSpecification(typ.NamedTuple):
    """A mapping from a source file to a destination inside the install root."""

    source: Path
    destination: tuple[str, ...]


class TemplateOptions(typ.NamedTuple):
    """Options consumed by :func:`render_default_wxs`."""

    product_name: str
    manufacturer: str
    version: str
    program_files_directory: str
    install_dir_name: str
    description: str
    upgrade_code: UUID
    files: tuple[FileSpecification, ...]
    license_path: str | None


def _normalise_identifier(prefix: str, value: str, used: set[str]) -> str:
    candidate = _IDENTIFIER_RE.sub("_", value).strip("_")
    if not candidate:
        candidate = prefix
    if candidate[0].isdigit():
        candidate = f"{prefix}_{candidate}"
    base = candidate.upper()
    candidate = base
    counter = 1
    while candidate in used:
        counter += 1
        candidate = f"{base}_{counter}"
    used.add(candidate)
    return candidate


def _normalise_directory_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return "Application"
    return re.sub(r"[\\/:*?\"<>|]", "_", cleaned)


def _split_destination(value: str | None, fallback_name: str) -> tuple[str, ...]:
    if value is None or not value.strip():
        return (fallback_name,)
    parts = [part for part in re.split(r"[\\/]", value.strip()) if part]
    if not parts:
        return (fallback_name,)
    return tuple(parts)


def _program_files_directory(architecture: str) -> str:
    normalised = architecture.lower()
    if normalised in {"x86", "ia32"}:
        return "ProgramFilesFolder"
    if normalised in {"x64", "amd64", "arm64"}:
        return "ProgramFiles64Folder"
    message = f"Unsupported architecture '{architecture}'"
    raise TemplateError(message)


def _as_windows_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    return str(resolved).replace("/", "\\")


def _flatten_components(node: DirectoryNode) -> list[Component]:
    items = list(node.components)
    for child in node.children:
        items.extend(_flatten_components(child))
    return items


def parse_file_specification(spec: str) -> FileSpecification:
    r"""Parse ``spec`` in the form ``source|relative\path\file``."""
    if not spec or not spec.strip():
        message = "File specification cannot be empty"
        raise TemplateError(message)
    if "|" in spec:
        source_str, destination = spec.split("|", 1)
    else:
        source_str, destination = spec, ""
    source_str = source_str.strip()
    if not source_str:
        message = "Source path cannot be empty"
        raise TemplateError(message)
    source = Path(source_str)
    destination_parts = _split_destination(destination, source.name)
    return FileSpecification(source=source, destination=destination_parts)


def _resolve_file(spec: FileSpecification) -> FileSpecification:
    return FileSpecification(
        source=spec.source.resolve(strict=True), destination=spec.destination
    )


def _build_directory_tree(
    files: tuple[FileSpecification, ...], install_dir_name: str, upgrade_code: UUID
) -> DirectoryNode:
    used_ids: set[str] = {"INSTALLFOLDER"}
    used_components: set[str] = set()
    root = DirectoryNode("INSTALLFOLDER", install_dir_name)
    for spec in files:
        if len(spec.destination) < 2:
            message = "Destinations must include the install directory and file name"
            raise TemplateError(message)
        current = root
        for depth, part in enumerate(spec.destination[1:-1], start=1):
            identifier = _normalise_identifier(
                "DIR", "/".join(spec.destination[: depth + 1]), used_ids
            )
            current = current.child(identifier, part)
        component_id = _normalise_identifier(
            "CMP", "/".join(spec.destination), used_components
        )
        guid_seed = f"component:{upgrade_code}:{'/'.join(spec.destination)}"
        component_guid = str(uuid5(_WINDOWS_INSTALLER_NAMESPACE, guid_seed)).upper()
        current.components.append(
            Component(
                id=component_id,
                guid=component_guid,
                source=_as_windows_path(spec.source),
                name=spec.destination[-1],
            )
        )
    return root


def prepare_template_options(
    *,
    version: str,
    architecture: str,
    application: FileSpecification,
    product_name: str | None = None,
    manufacturer: str | None = None,
    install_dir_name: str | None = None,
    description: str | None = None,
    upgrade_code: str | UUID | None = None,
    additional_files: typ.Iterable[FileSpecification] | None = None,
    license_path: str | None = None,
) -> TemplateOptions:
    """Create :class:`TemplateOptions` from user-facing parameters."""
    if not version or not version.strip():
        message = "Version is required"
        raise TemplateError(message)

    resolved_application = _resolve_file(application)
    files: list[FileSpecification] = [resolved_application]
    if additional_files:
        files.extend(_resolve_file(spec) for spec in additional_files)

    if not files:
        message = "At least one file must be provided"
        raise TemplateError(message)

    app_filename = resolved_application.destination[-1]
    chosen_product = (
        product_name or app_filename.removesuffix(".exe") or app_filename
    ).strip()
    if not chosen_product:
        chosen_product = app_filename
    chosen_manufacturer = (
        manufacturer or "Unknown Publisher"
    ).strip() or "Unknown Publisher"
    chosen_description = (
        description or f"{chosen_product} installer"
    ).strip() or chosen_product
    chosen_install_dir = _normalise_directory_name(install_dir_name or chosen_product)

    program_files_directory = _program_files_directory(architecture)

    if isinstance(upgrade_code, UUID):
        upgrade_uuid = upgrade_code
    elif upgrade_code:
        try:
            upgrade_uuid = UUID(str(upgrade_code))
        except ValueError as exc:
            message = f"Invalid upgrade code: {upgrade_code}"
            raise TemplateError(message) from exc
    else:
        seed = f"{chosen_product}|{chosen_manufacturer}"
        upgrade_uuid = uuid5(_WINDOWS_INSTALLER_NAMESPACE, f"upgrade:{seed}")

    resolved_license: str | None = None
    if license_path and license_path.strip():
        resolved_license = _as_windows_path(Path(license_path))

    adjusted_files: list[FileSpecification] = []
    for spec in files:
        destination = spec.destination
        if not destination:
            message = "Destination paths must include a filename"
            raise TemplateError(message)
        if destination[0] != chosen_install_dir:
            destination = (chosen_install_dir, *destination)
        adjusted_files.append(
            FileSpecification(source=spec.source, destination=destination)
        )

    return TemplateOptions(
        product_name=chosen_product,
        manufacturer=chosen_manufacturer,
        version=version.strip(),
        program_files_directory=program_files_directory,
        install_dir_name=chosen_install_dir,
        description=chosen_description,
        upgrade_code=upgrade_uuid,
        files=tuple(adjusted_files),
        license_path=resolved_license,
    )


def render_default_wxs(options: TemplateOptions) -> str:
    """Render WiX authoring using ``options`` and the default template."""
    directory_tree = _build_directory_tree(
        options.files, options.install_dir_name, options.upgrade_code
    )
    components = _flatten_components(directory_tree)
    return render(
        _DEFAULT_TEMPLATE,
        product_name=options.product_name,
        manufacturer=options.manufacturer,
        version=options.version,
        upgrade_code=str(options.upgrade_code).upper(),
        description=options.description,
        license_path=options.license_path,
        program_files_directory=options.program_files_directory,
        root_directory=directory_tree,
        components=components,
    )
