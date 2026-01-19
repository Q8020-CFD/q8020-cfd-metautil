#!/usr/bin/env python3
"""
Print command to open all PNG images from a sweep.

Usage:
    python -m qp4p_print_open_cmd <final_postproc_json>
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m qp4p_print_open_cmd <final_postproc_json>")
        sys.exit(1)
    
    # Load final postproc context from JSON file
    postproc_json = Path(sys.argv[1])
    with open(postproc_json, "r", encoding="utf-8") as f:
        context = json.load(f)
    
    run_dir = Path(context.get("run_dir", ""))
    
    # Find all PNG files in case directories
    png_files = []
    for case_dir_str in context.get("case_dirs", []):
        case_dir = Path(case_dir_str)
        for png_file in case_dir.glob("*.png"):
            png_files.append(png_file)
    
    if not png_files:
        print("No PNG files found in case directories")
        return
    
    # Sort by filename for consistent ordering
    png_files.sort(key=lambda p: (p.parent.name, p.name))
    
    print(f"\n{'='*70}")
    print(f"Sweep complete! Found {len(png_files)} visualization(s)")
    print(f"{'='*70}")
    print(f"\nTo view all images as slideshow, run:\n")
    
    # Create a pattern that matches all the images
    if png_files:
        # Get the common pattern
        first_parent = png_files[0].parent.parent
        pattern = f"{first_parent}/*/image_flip_*.png"
        print(f"  open {pattern}")
    
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
