"""Run a campaign from the terminal, with human approval at the prompt.

    python -m campaignpilot.cli "Win back customers inactive for 60+ days"
    python -m campaignpilot.cli "Promote agency workspaces" --channel social
    python -m campaignpilot.cli "..." --auto-approve      # non-interactive (CI)
"""
import argparse
import json

from campaignpilot.agents.service import resume_run, start_run


def show(view: dict) -> None:
    s = view["state"]
    print("\n== Agent trace ==")
    for line in s.get("trace", []):
        print(" -", line)
    print(f"\n== Segment: {s['segment']['count']} customers ==\n{s['segment']['sql']}")
    print(f"\n== Review: {s['review']['score']}/10 ==")
    for issue in s["review"]["issues"]:
        print(" !", issue)
    for v in s["content"]["variants"]:
        print(f"\n--- Variant: {v['label']} ---\n{v['subject_or_headline']}")
        if v.get("preview_text"):
            print(f"({v['preview_text']})")
        print(f"\n{v['body']}\n[{v['cta']}] {' '.join(v.get('hashtags') or [])}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("goal")
    p.add_argument("--channel", choices=["email", "social", "ads"])
    p.add_argument("--auto-approve", action="store_true")
    args = p.parse_args()

    view = start_run(args.goal, args.channel)
    while view["status"] == "awaiting_approval":
        show(view)
        if args.auto_approve:
            choice, feedback = "approve", ""
        else:
            choice = input("\n[a]pprove / [r]evise / [x] reject? ").strip().lower()[:1]
            choice = {"a": "approve", "r": "revise", "x": "reject"}.get(choice, "reject")
            feedback = input("What should change? ") if choice == "revise" else ""
        view = resume_run(view["thread_id"], choice, feedback)

    print(f"\nFinal status: {view['status']}")
    result = view["state"].get("result", {})
    if result.get("previews"):
        print(json.dumps(result["previews"][0], indent=2))
        print(f"Outbox: {result['outbox_file']}")


if __name__ == "__main__":
    main()
