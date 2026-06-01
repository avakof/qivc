"""Dossier builder — renders a RunReport to Markdown (jinja2) and JSON."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from qivc.schemas import RunReport

# Verbatim disclaimer from PROJECT_BRIEF §1.4
DISCLAIMER = (
    "This is research output, not investment advice. No personal recommendation "
    "is intended. Verify all signals independently before any investment decision."
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "run_report.md.j2"


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_markdown(run_report: RunReport) -> str:
    """Render the run report to a Markdown string."""
    env = _environment()
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(
        disclaimer=DISCLAIMER,
        run_id=run_report.run_id,
        generated_at=run_report.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        regime=run_report.regime,
        candidates=run_report.candidates,
        rejected=run_report.rejected,
        universe_size=run_report.universe_size,
        run_duration_seconds=run_report.run_duration_seconds,
    )


def build_markdown(run_report: RunReport, output_path: Path) -> None:
    """Write the Markdown dossier to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(run_report), encoding="utf-8")


def build_json(run_report: RunReport, output_path: Path) -> None:
    """Write the pretty-printed JSON dossier to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        run_report.model_dump_json(indent=2),
        encoding="utf-8",
    )
