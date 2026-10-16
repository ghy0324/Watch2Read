"""Install an allowlisted, self-contained skill without credentials or notes."""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path

FILES = [
    "SKILL.md", "agents/openai.yaml", "references/skill-workflow.md",
    "scripts/skill_pipeline.py", "scripts/install_skill.py",
    "download_subtitle.py", "video_meta.py", "structure_subtitle.py",
    "segment_video.py", "render_markdown.py", "md2pdf.py", "main.py", "update.py",
]


def install(target: Path, force: bool = False) -> Path:
    source = Path(__file__).resolve().parents[1]
    target = target.expanduser().resolve()
    if target == source or target in source.parents or source in target.parents:
        raise ValueError("Install outside the source repository")
    for name in FILES:
        for root in (source, target):
            path = root
            for part in Path(name).parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError(f"Packaged paths must not be symlinks: {path}")
        if not (source / name).is_file():
            raise ValueError(f"Missing packaged file: {name}")
    if target.exists():
        if not force:
            raise ValueError(f"Already installed: {target}; use --force to back up and update")
        backup = target.parent / ".watch2read-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup, symlinks=True,
                        ignore=shutil.ignore_patterns(".venv", "__pycache__"))
        print(f"BACKUP={backup}")
    for name in FILES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    print(f"INSTALLED={target}\nNo API credentials, notes, or repository history were copied.")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills" / "watch2read")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        install(args.target_dir, args.force)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
