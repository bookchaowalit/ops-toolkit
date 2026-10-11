#!/usr/bin/env python3
"""
Script Registry — Auto-generate manifest of all scripts with descriptions.

Scans infra/scripts/ for Python scripts, extracts docstrings, and generates
a registry with usage info.

Usage:
    python3 script_registry.py              # Print registry to stdout
    python3 script_registry.py --save       # Save to data/generated/reports/script-registry.md
    python3 script_registry.py --json       # JSON output
    python3 script_registry.py --category utils  # Filter by category
"""

import os
import re
import sys
import json
from datetime import datetime

SCRIPTS_ROOT = os.path.join(os.path.dirname(__file__), '..')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'generated', 'reports', 'script-registry.md')

CATEGORIES = {
    'utils': 'Utility Scripts',
    'cli': 'CLI Tools',
    'db': 'Database Scripts',
    'sync': 'Sync & Integration',
    'workflows': 'Workflow Scripts',
    'content': 'Content Generation',
    'github': 'GitHub Scripts',
    'setup': 'Setup Scripts',
}


def extract_docstring(filepath):
    """Extract first line of module docstring."""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read(8000)  # Read 8KB to capture long docstrings
        content = raw.decode('utf-8', errors='replace')
        # Normalize CRLF → LF so regex works on all files
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        # Match module docstring (with or without shebang line)
        match = re.search(r'^"""(.*?)"""', content, re.MULTILINE | re.DOTALL)
        if match:
            doc = match.group(1).strip()
            # Get first line
            first_line = doc.split('\n')[0].strip()
            return first_line
    except Exception:
        pass
    return None


def extract_usage(filepath):
    """Extract usage examples from docstring."""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read(3000)
        content = raw.decode('utf-8', errors='replace')
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        match = re.search(r'"""(.*?)"""', content, re.MULTILINE | re.DOTALL)
        if match:
            doc = match.group(1)
            # Find Usage: section
            usage_match = re.search(r'Usage:\s*\n((?:\s+.*\n)+)', doc)
            if usage_match:
                usage_lines = usage_match.group(1).strip()
                return usage_lines
    except Exception:
        pass
    return None


def scan_scripts(category_filter=None):
    """Scan all Python scripts and extract metadata."""
    scripts = []
    
    for category in sorted(os.listdir(SCRIPTS_ROOT)):
        cat_path = os.path.join(SCRIPTS_ROOT, category)
        if not os.path.isdir(cat_path) or category.startswith('_'):
            continue
        
        if category_filter and category != category_filter:
            continue
        
        for filename in sorted(os.listdir(cat_path)):
            if not filename.endswith('.py') or filename.startswith('_'):
                continue
            
            filepath = os.path.join(cat_path, filename)
            rel_path = os.path.relpath(filepath, SCRIPTS_ROOT)
            
            description = extract_docstring(filepath)
            usage = extract_usage(filepath)
            
            # Get file size
            size = os.path.getsize(filepath)
            
            scripts.append({
                'category': category,
                'filename': filename,
                'path': rel_path,
                'description': description or 'No description',
                'usage': usage,
                'size': size,
                'size_kb': round(size / 1024, 1),
            })
    
    return scripts


def print_registry(scripts):
    """Print formatted registry."""
    print(f'# Solo Empire Script Registry')
    print(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'Total scripts: {len(scripts)}')
    print()
    
    # Group by category
    by_category = {}
    for s in scripts:
        cat = s['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(s)
    
    for category in sorted(by_category.keys()):
        cat_name = CATEGORIES.get(category, category.title())
        cat_scripts = by_category[category]
        print(f'## {cat_name} ({len(cat_scripts)} scripts)')
        print()
        
        for s in cat_scripts:
            print(f'### `{s["filename"]}`')
            print(f'{s["description"]}')
            print(f'- Path: `{s["path"]}`')
            print(f'- Size: {s["size_kb"]} KB')
            if s['usage']:
                print(f'- Usage:')
                print(f'  ```')
                for line in s['usage'].split('\n'):
                    print(f'  {line}')
                print(f'  ```')
            print()


def save_registry(scripts):
    """Save registry to markdown file."""
    import io
    import sys
    
    # Capture print output
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    print_registry(scripts)
    content = sys.stdout.getvalue()
    sys.stdout = old_stdout
    
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f'✅ Saved to {OUTPUT_PATH}')


def main():
    args = sys.argv[1:]
    json_output = '--json' in args
    save_output = '--save' in args
    category_filter = None
    
    if '--category' in args:
        idx = args.index('--category')
        if idx + 1 < len(args):
            category_filter = args[idx + 1]
    
    scripts = scan_scripts(category_filter)
    
    if json_output:
        print(json.dumps(scripts, indent=2))
    elif save_output:
        save_registry(scripts)
    else:
        print_registry(scripts)


if __name__ == '__main__':
    main()
