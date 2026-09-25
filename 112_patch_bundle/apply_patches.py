#!/usr/bin/env python3
"""Apply the complete bundle after git's preflight. Dry-run unless --apply is explicit.

Usage: python apply_patches.py /path/to/project [--apply] [--reverse]
Works with an ordinary project directory as well as a Git worktree. No dependency
installation, database access, service restart or voice model modification.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BUNDLE=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("project",type=Path);p.add_argument("--apply",action="store_true")
    p.add_argument("--reverse",action="store_true",help="Reverse source changes only; never rollback DB")
    args=p.parse_args();root=args.project.resolve()
    if not (root/"api/main.py").is_file() or not (root/"web/app.js").is_file():
        p.error("Project must contain api/main.py and web/app.js")
    if not shutil.which("git"):p.error("Install Git first")
    manifest=json.loads((BUNDLE/"manifest.json").read_text(encoding="utf-8"))
    patch=BUNDLE/"all.patch"
    if sha(patch)!=manifest["patch_sha256"]:p.error("Patch checksum mismatch")
    changed=[]
    for item in manifest["files"]:
        file=Path(item["path"])
        if file.is_absolute() or ".." in file.parts or not (root/file).resolve().is_relative_to(root):
            p.error("Unsafe path or symlink: "+str(file))
        target=root/file
        expected=item["after_sha256" if args.reverse else "before_sha256"]
        actual=sha(target) if target.is_file() else None
        if actual!=expected:changed.append(str(file))
    if changed:
        print("Files differ from the supplied baseline (possibly colleague's work):")
        for file in changed:print("  "+file)
        print("Git preflight will determine whether non-overlapping application is possible.")
    command=["git","apply","--whitespace=nowarn"]
    if args.reverse:command.append("--reverse")
    check=subprocess.run(command+["--check",str(patch)],cwd=root)
    if check.returncode:
        print("STOP: patch does not fit; no source files changed. Resolve on a separate branch. Do not use --reject/force.")
        return 2
    if not args.apply:
        print("PREFLIGHT OK. No changes made. Repeat with --apply to apply the complete bundle.")
        return 0
    # Source-only recovery archive before mutation. No .env/state.
    backup=Path(tempfile.mkdtemp(prefix="t112-source-backup-"))/"source.tar.gz"
    with tarfile.open(backup,"w:gz") as archive:
        for item in manifest["files"]:
            file=root/item["path"]
            if file.is_file():archive.add(file,arcname=item["path"])
    result=subprocess.run(command+[str(patch)],cwd=root)
    print("Source backup:",backup)
    if result.returncode:
        print("APPLY FAILED. Inspect the working tree; do not run services until reviewed.")
        return result.returncode
    print("SOURCE PATCH APPLIED. Database migrations run on application startup. Run the supplied acceptance tests before deployment.")
    return 0

if __name__=="__main__":sys.exit(main())
