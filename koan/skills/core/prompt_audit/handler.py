"""Koan /prompt_audit skill -- queue a prompt audit mission."""


def handle(ctx):
    """Handle /prompt_audit command -- queue a prompt quality audit.

    Usage:
        /prompt_audit                     -- audit all prompts (koan project)
        /prompt_audit <project>           -- audit prompts for a specific project
    """
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /prompt_audit [project-name]\n\n"
            "Audits all system and skill prompts for quality, clarity, "
            "redundancy, staleness, and effectiveness.\n\n"
            "Uses signal data from post-mission hooks (if available) to "
            "correlate prompt usage with outcomes.\n\n"
            "Findings are written to memory/prompt-audit-report.md.\n\n"
            "Examples:\n"
            "  /prompt_audit\n"
            "  /prompt_audit koan"
        )

    # Project name is optional — default to "koan"
    project_name = args.split()[0] if args else "koan"

    return _queue_audit(ctx, project_name)


def _queue_audit(ctx, project_name):
    """Queue a prompt audit mission."""
    from app.utils import insert_pending_mission, resolve_project_path

    path = resolve_project_path(project_name)
    if not path:
        from app.utils import get_known_projects

        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"\u274c Unknown project '{project_name}'.\n"
            f"Known projects: {known}"
        )

    mission_entry = f"- [project:{project_name}] /prompt_audit"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f4dd Prompt audit queued for {project_name}"
