"""Brief skill runner — agent-loop dispatch for scheduled briefs."""

import argparse
import contextlib
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-dir", required=True)
    parser.add_argument("--project-path", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--context-file", default="")
    args = parser.parse_args()

    instance_dir = Path(args.instance_dir)
    koan_root = instance_dir.parent

    ctx_text = ""
    if args.context_file:
        with contextlib.suppress(OSError):
            ctx_text = Path(args.context_file).read_text().strip()

    from skills.core.brief.handler import handle

    class BriefCtx:
        pass

    ctx = BriefCtx()
    ctx.koan_root = koan_root
    ctx.instance_dir = instance_dir
    ctx.command_name = "brief"
    ctx.args = ctx_text

    result = handle(ctx)

    if result:
        from app.utils import append_to_outbox
        append_to_outbox(instance_dir / "outbox.md", result)

    print("Daily brief sent to outbox.", file=sys.stderr)


if __name__ == "__main__":
    main()
