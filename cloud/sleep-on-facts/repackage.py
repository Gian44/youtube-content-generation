"""Re-package live long videos (sof/repackage.py).

python repackage.py --videos all|ID[,ID...] [--apply] [--from DIR] [--out out]

Without --apply nothing on YouTube changes: out/<id>/thumb.jpg + meta.json are the B arm of a YouTube
Studio "Test & compare". With --apply the title, description and thumbnail are replaced and the ledger
records it (committed when running on GitHub Actions). --apply --from DIR publishes an earlier prepare
run's files (DIR/<id>/meta.json, description.txt, thumb.jpg) exactly as reviewed.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from sof import ledger, repackage, upload
from sof.config import Config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--videos", default="all", help="'all' (every live long video in the ledger) or comma-separated ids")
    p.add_argument("--apply", action="store_true", help="update the videos on YouTube (default: prepare files only)")
    p.add_argument("--from", dest="prepared", default="", help="apply this folder of reviewed prepare results")
    p.add_argument("--out", default="out")
    ns = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = Config.from_env()
    rows = ledger.load_ledger(cfg.ledger_path)
    live = [r for r in rows if r.get("video_id") and not r.get("abandoned") and r.get("kind", "video") == "video"]
    if ns.videos.strip().lower() != "all":
        want = [v.strip() for v in ns.videos.split(",") if v.strip()]
        live = [r for r in live if r["video_id"] in want]
        missing = set(want) - {r["video_id"] for r in live}
        if missing:
            print(f"! not in the ledger (skipped): {', '.join(sorted(missing))}", flush=True)
    if not live:
        print("nothing to repackage", flush=True)
        return 0

    yt = upload.build_client(cfg)
    print(f"connected as {upload.health(yt)} · {len(live)} video(s) · {'APPLY' if ns.apply else 'prepare only'}", flush=True)
    llm = cfg.make_llm()
    if cfg.claude_enabled:
        print(f"claude: {llm.check_claude() or 'ok'}", flush=True)
    out = Path(ns.out)
    reports, failed = [], 0
    for i, row in enumerate(live):
        try:
            if ns.prepared:
                vdir = Path(ns.prepared) / row["video_id"]
                if not (vdir / "meta.json").exists():
                    print(f"- {row['video_id']}: no prepared files; skipped", flush=True)
                    continue
                if not ns.apply:
                    raise SystemExit("--from needs --apply")
                rep = repackage.apply_prepared(yt, vdir)
            else:
                rep = repackage.repackage_row(cfg, yt, llm, row, index=i, work=out, apply=ns.apply)
            reports.append(rep)
            print(f"{'✓' if rep['applied'] else '·'} {rep['video_id']}: {rep['title_before']!r}\n"
                  f"    → {rep['title']!r} · thumb {rep['thumb_text']!r} · {rep['chapters_renamed']} chapters renamed", flush=True)
        except Exception as exc:  # noqa: BLE001 — one bad video must not stop the rest
            failed += 1
            logging.exception("repackage failed for %s", row.get("video_id"))
            print(f"✗ {row.get('video_id')}: {exc}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(reports, indent=1, ensure_ascii=False), encoding="utf-8")
    if ns.apply and any(r["applied"] for r in reports):
        all_rows = ledger.load_ledger(cfg.ledger_path)
        repackage.mark_ledger(all_rows, reports)
        with open(cfg.ledger_path, "w", encoding="utf-8") as f:
            json.dump(all_rows, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if os.environ.get("GITHUB_ACTIONS"):
            _commit_ledger(cfg.ledger_path, f"ledger: repackaged {sum(r['applied'] for r in reports)} videos (titles, thumbnails, chapters)")
    print(f"done: {len(reports)} ok, {failed} failed", flush=True)
    return 1 if failed and not reports else 0


def _commit_ledger(path: str, message: str) -> None:
    """Plain commit + push (no row re-append on conflict — that recovery is for new-video rows)."""
    import subprocess

    def git(*a, check=True):
        return subprocess.run(["git", *a], check=check, capture_output=True, text=True)

    git("config", "user.name", "sleep-on-facts-bot")
    git("config", "user.email", "actions@users.noreply.github.com")
    git("add", path)
    git("commit", "-m", message)
    for _ in range(3):
        if git("pull", "--rebase", check=False).returncode != 0:
            git("rebase", "--abort", check=False)
            raise RuntimeError("ledger changed meanwhile; repackage edits are on YouTube but not in the ledger — rerun later")
        if git("push", check=False).returncode == 0:
            return
    raise RuntimeError("ledger push failed")


if __name__ == "__main__":
    sys.exit(main())
