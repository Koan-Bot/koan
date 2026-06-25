"""Koan -- /debug bridge handler.

Queues a /debug mission to the pending queue. The actual debugging
happens in debug_runner.py via the agent loop.
"""


def handle(ctx):
    """Handle /debug — queue a structured debug mission."""
    args = ctx.args.strip() if ctx.args else ""
    if not args:
        return "Usage: `/debug <issue-url> [context]`"

    from app.github_url_parser import parse_github_url
    from app.github_skill_helpers import handle_github_skill
    from app.missions import extract_now_flag

    urgent, args = extract_now_flag(args)
    ctx.args = args

    return handle_github_skill(
        ctx,
        command="debug",
        url_type="issue",
        parse_func=parse_github_url,
        success_prefix="Debug queued",
        urgent=urgent,
    )
