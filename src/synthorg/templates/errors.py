"""Custom exception hierarchy for template errors."""

from synthorg.config.errors import ConfigError, ConfigLocation


class TemplateError(ConfigError):
    """Base exception for template errors."""


class TemplateNotFoundError(TemplateError):
    """Raised when a template cannot be found."""


class TemplateRenderError(TemplateError):
    """Raised when template rendering fails.

    Covers Jinja2 evaluation errors, missing required variables,
    YAML parse errors during template processing, and invalid
    numeric values in rendered output.
    """


class TemplateInheritanceError(TemplateRenderError):
    """Raised when template inheritance fails.

    Covers circular inheritance chains, excessive depth,
    and merge conflicts.
    """


class TemplateValidationError(TemplateError):
    """Raised when a rendered template fails validation.

    Attributes:
        field_errors: Per-field error messages as
            ``(key_path, message)`` pairs.
    """

    def __init__(
        self,
        message: str,
        locations: tuple[ConfigLocation, ...] = (),
        field_errors: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message, locations)
        self.field_errors = field_errors

    def __str__(self) -> str:
        """Format validation error with per-field details."""
        if not self.field_errors:
            return super().__str__()
        parts = [f"{self.message} ({len(self.field_errors)} errors):"]
        loc_by_key: dict[str, ConfigLocation] = {
            loc.key_path: loc for loc in self.locations if loc.key_path
        }
        for key_path, msg in self.field_errors:
            parts.append(f"  {key_path}: {msg}")
            loc = loc_by_key.get(key_path)
            if loc and loc.file_path:
                if loc.line is not None and loc.column is not None:
                    line_info = f" at line {loc.line}, column {loc.column}"
                elif loc.line is not None:
                    line_info = f" at line {loc.line}"
                else:
                    line_info = ""
                parts.append(f"    in {loc.file_path}{line_info}")
        return "\n".join(parts)
