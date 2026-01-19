"""
Analyze JSON structure across all experiment outputs.

Scans ~/qp4p directory to:
1. Find all JSON files at UUID and case levels
2. Build comprehensive dictionary of all field names
3. Identify non-standardized outputs
"""

import json
from pathlib import Path
from collections import defaultdict
import sys


def flatten_keys(data, parent_key='', sep='.'):
    """Recursively extract all keys from nested JSON."""
    keys = set()
    
    if isinstance(data, dict):
        for k, v in data.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            keys.add(new_key)
            if isinstance(v, (dict, list)):
                keys.update(flatten_keys(v, new_key, sep))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                keys.update(flatten_keys(item, parent_key, sep))
    
    return keys


def analyze_json_files(base_dir):
    """Analyze all JSON files in directory structure."""
    base_path = Path(base_dir).expanduser()
    
    if not base_path.exists():
        print(f"Directory not found: {base_dir}")
        return
    
    # Find all JSON files
    json_files = list(base_path.glob("**/*.json"))
    
    print(f"Found {len(json_files)} JSON files\n")
    
    # Categorize files
    uuid_dirs = set()
    stdout_files = []
    params_files = []
    sweep_files = []
    postproc_files = []
    expanded_files = []
    other_files = []
    
    for jf in json_files:
        # Get UUID directory (8 hex chars)
        parts = jf.relative_to(base_path).parts
        if len(parts) > 0 and len(parts[0]) == 8:
            uuid_dirs.add(parts[0])
        
        # Categorize by filename
        if jf.name == "stdout.json":
            stdout_files.append(jf)
        elif jf.name == "params.json":
            params_files.append(jf)
        elif jf.name == "sweep_results.json":
            sweep_files.append(jf)
        elif "postproc" in jf.name:
            postproc_files.append(jf)
        elif jf.name == "expanded_cases.json":
            expanded_files.append(jf)
        else:
            other_files.append(jf)
    
    print("=" * 80)
    print("FILE CATEGORIZATION")
    print("=" * 80)
    print(f"UUID run directories: {len(uuid_dirs)}")
    print(f"stdout.json files (experiment outputs): {len(stdout_files)}")
    print(f"params.json files (case parameters): {len(params_files)}")
    print(f"sweep_results.json files: {len(sweep_files)}")
    print(f"postproc files: {len(postproc_files)}")
    print(f"expanded_cases.json files: {len(expanded_files)}")
    print(f"Other JSON files: {len(other_files)}")
    print()
    
    # Analyze stdout.json files (main experiment outputs)
    print("=" * 80)
    print("ANALYZING STDOUT.JSON FILES (Experiment Outputs)")
    print("=" * 80)
    
    all_keys = set()
    key_frequency = defaultdict(int)
    file_keys = {}
    parse_errors = []
    empty_files = []
    
    for stdout_file in stdout_files:
        try:
            with open(stdout_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    empty_files.append(stdout_file)
                    continue
                
                data = json.loads(content)
                keys = flatten_keys(data)
                
                file_keys[stdout_file] = keys
                all_keys.update(keys)
                
                for key in keys:
                    key_frequency[key] += 1
                    
        except json.JSONDecodeError as e:
            parse_errors.append((stdout_file, str(e)))
        except Exception as e:
            parse_errors.append((stdout_file, f"Error: {e}"))
    
    print(f"\nTotal unique field names across all stdout.json: {len(all_keys)}")
    print(f"Successfully parsed: {len(file_keys)}")
    print(f"Parse errors: {len(parse_errors)}")
    print(f"Empty files: {len(empty_files)}")
    
    if parse_errors:
        print("\nPARSE ERRORS:")
        for file, error in parse_errors[:10]:
            print(f"  {file.relative_to(base_path)}: {error}")
        if len(parse_errors) > 10:
            print(f"  ... and {len(parse_errors) - 10} more")
    
    if empty_files:
        print("\nEMPTY FILES:")
        for file in empty_files[:10]:
            print(f"  {file.relative_to(base_path)}")
        if len(empty_files) > 10:
            print(f"  ... and {len(empty_files) - 10} more")
    
    # Comprehensive field dictionary
    print("\n" + "=" * 80)
    print("COMPREHENSIVE FIELD DICTIONARY (sorted alphabetically)")
    print("=" * 80)
    
    sorted_keys = sorted(all_keys)
    for key in sorted_keys:
        freq = key_frequency[key]
        pct = (freq / len(file_keys) * 100) if file_keys else 0
        print(f"  {key:60s} [{freq:4d} files, {pct:5.1f}%]")
    
    # Identify top-level structure variations
    print("\n" + "=" * 80)
    print("TOP-LEVEL STRUCTURE ANALYSIS")
    print("=" * 80)
    
    top_level_patterns = defaultdict(list)
    
    for stdout_file, keys in file_keys.items():
        # Get only top-level keys
        top_keys = frozenset(k.split('.')[0] for k in keys)
        top_level_patterns[top_keys].append(stdout_file)
    
    print(f"\nFound {len(top_level_patterns)} distinct top-level structures:\n")
    
    for i, (pattern, files) in enumerate(sorted(top_level_patterns.items(), key=lambda x: -len(x[1])), 1):
        print(f"Pattern {i}: {len(files)} files")
        print(f"  Top-level keys: {sorted(pattern)}")
        print(f"  Example: {files[0].relative_to(base_path)}")
        if len(files) > 1:
            print(f"  (and {len(files)-1} more files with this pattern)")
        print()
    
    # Identify fields that should be standardized
    print("=" * 80)
    print("STANDARDIZATION ISSUES")
    print("=" * 80)
    
    # Fields present in some but not all files
    total_files = len(file_keys)
    threshold = 0.5  # Fields in >50% but not 100% of files
    
    inconsistent_fields = []
    for key, freq in key_frequency.items():
        pct = freq / total_files
        if threshold < pct < 1.0:
            inconsistent_fields.append((key, freq, pct))
    
    if inconsistent_fields:
        print(f"\nFields present in >50% but not all files ({len(inconsistent_fields)} fields):")
        print("These may indicate missing standardization:\n")
        
        for key, freq, pct in sorted(inconsistent_fields, key=lambda x: -x[2]):
            missing = total_files - freq
            print(f"  {key:60s} [{freq:4d}/{total_files} files, {pct*100:5.1f}%] MISSING IN {missing} FILES")
    
    # Find files missing common fields
    print("\n" + "=" * 80)
    print("FILES WITH NON-STANDARD STRUCTURE")
    print("=" * 80)
    
    # Identify most common top-level keys
    common_top_keys = set()
    for key in all_keys:
        top_key = key.split('.')[0]
        if key_frequency[key] > total_files * 0.7:  # Present in >70% of files
            common_top_keys.add(top_key)
    
    print(f"\nCommon top-level keys (>70% of files): {sorted(common_top_keys)}\n")
    
    non_standard_files = []
    for stdout_file, keys in file_keys.items():
        file_top_keys = set(k.split('.')[0] for k in keys)
        missing_common = common_top_keys - file_top_keys
        
        if missing_common:
            non_standard_files.append((stdout_file, missing_common))
    
    if non_standard_files:
        print(f"Found {len(non_standard_files)} files missing common top-level keys:\n")
        for file, missing in non_standard_files[:20]:
            print(f"  {file.relative_to(base_path)}")
            print(f"    Missing: {sorted(missing)}")
        if len(non_standard_files) > 20:
            print(f"  ... and {len(non_standard_files) - 20} more")
    else:
        print("All files have consistent top-level structure!")
    
    # Analyze params.json structure
    print("\n" + "=" * 80)
    print("PARAMS.JSON STRUCTURE")
    print("=" * 80)
    
    params_keys = set()
    params_key_freq = defaultdict(int)
    
    for params_file in params_files:
        try:
            with open(params_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                keys = set(data.keys())
                params_keys.update(keys)
                for key in keys:
                    params_key_freq[key] += 1
        except Exception:
            pass
    
    print(f"\nTotal unique parameter names: {len(params_keys)}")
    print("\nParameter frequency:")
    for key in sorted(params_keys):
        freq = params_key_freq[key]
        pct = (freq / len(params_files) * 100) if params_files else 0
        print(f"  {key:40s} [{freq:4d} files, {pct:5.1f}%]")


if __name__ == "__main__":
    base_dir = sys.argv[1] if len(sys.argv) > 1 else "~/qp4p"
    analyze_json_files(base_dir)
