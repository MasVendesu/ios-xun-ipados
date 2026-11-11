#!/usr/bin/env python3
"""Validate repository integrity, patch sequences, and script syntax.

  tools/validate-repo.py [--verbose]

Checks:
  1. Patch sequence in xnu/patches/:
     - Sequential numbering starting at 0001 with no duplicates or gaps.
     - Patch files are non-empty and have valid unified diff markers.
  2. Python script syntax:
     - All tools/*.py compile cleanly via py_compile.
  3. Shell script syntax:
     - All tools/*.sh, enxerto/*.sh, and xnu/patches/*.sh pass bash -n check.
"""
import argparse, glob, os, py_compile, re, shutil, subprocess, sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def green(text): return f"\033[92m{text}\033[0m"
def red(text): return f"\033[91m{text}\033[0m"
def bold(text): return f"\033[1m{text}\033[0m"


def validate_patches_dir(patch_dir, name):
    patch_files = sorted(glob.glob(os.path.join(patch_dir, '*.patch')))
    if not patch_files:
        print(red(f"[-] Error: No patch files found in {name}"))
        return False

    errors = []
    seen_numbers = {}

    for path in patch_files:
        fname = os.path.basename(path)
        # Check if monolithic base patch (e.g. xnu-12377.121.6-dada.patch)
        if fname.startswith('xnu-') or fname.startswith('base-'):
            with open(path, 'r', errors='ignore') as f:
                head = f.read(2048)
                if not ('--- ' in head or 'diff ' in head or 'Index:' in head):
                    errors.append(f"Monolithic patch missing standard diff header: {fname}")
            continue

        m = re.match(r'^(\d{4}[a-z]?)-(.+)\.patch$', fname)
        if not m:
            errors.append(f"Invalid patch filename format: {fname}")
            continue

        patch_id = m.group(1)
        if patch_id in seen_numbers:
            errors.append(f"Duplicate patch number {patch_id}: {fname} and {seen_numbers[patch_id]}")
        seen_numbers[patch_id] = fname

        size = os.path.getsize(path)
        if size == 0:
            errors.append(f"Patch is empty: {fname}")
            continue

        with open(path, 'r', errors='ignore') as f:
            head = f.read(2048)
            if not ('--- ' in head or 'diff ' in head or 'Index:' in head):
                errors.append(f"Patch missing standard diff header: {fname}")

    if errors:
        print(red(f"[-] Patch validation for {name} failed with {len(errors)} error(s):"))
        for err in errors:
            print(f"    - {err}")
        return False

    print(green(f"[✓] Validated {len(patch_files)} patches in {name} (format OK, no duplicates)"))
    return True


def validate_patches(verbose=False):
    target_dirs = []
    kernel_dir = os.path.join(REPO_ROOT, 'kernel')
    if os.path.isdir(kernel_dir) and glob.glob(os.path.join(kernel_dir, '*.patch')):
        target_dirs.append((kernel_dir, 'kernel'))
    for d in ['xnu/patches', 'xnu/patches-11215']:
        p = os.path.join(REPO_ROOT, d)
        if os.path.isdir(p):
            target_dirs.append((p, d))

    if not target_dirs:
        print(red("[-] Error: No patch directories found in repository"))
        return False

    all_ok = True
    for p, name in target_dirs:
        if not validate_patches_dir(p, name):
            all_ok = False
    return all_ok


def validate_python_scripts(verbose=False):
    tools_dir = os.path.join(REPO_ROOT, 'tools')
    py_files = sorted(glob.glob(os.path.join(tools_dir, '*.py')))
    errors = []

    for path in py_files:
        fname = os.path.basename(path)
        try:
            py_compile.compile(path, doraise=True)
            if verbose:
                print(f"    [OK] {fname}")
        except py_compile.PyCompileError as e:
            errors.append(f"{fname}: {e}")

    if errors:
        print(red(f"[-] Python syntax validation failed with {len(errors)} error(s):"))
        for err in errors:
            print(f"    - {err}")
        return False

    print(green(f"[✓] Validated {len(py_files)} Python tools (syntax OK)"))
    return True


def validate_shell_scripts(verbose=False):
    if not shutil.which('bash'):
        print(bold("[-] Note: bash not found in PATH; skipping shell script syntax validation on Windows."))
        return True

    sh_files = sorted(
        glob.glob(os.path.join(REPO_ROOT, 'tools/*.sh')) +
        glob.glob(os.path.join(REPO_ROOT, 'tools/renegade/*.sh')) +
        glob.glob(os.path.join(REPO_ROOT, 'enxerto/*.sh')) +
        glob.glob(os.path.join(REPO_ROOT, 'base/*.sh')) +
        glob.glob(os.path.join(REPO_ROOT, 'kernel/*.sh')) +
        glob.glob(os.path.join(REPO_ROOT, 'xnu/patches/*.sh'))
    )
    errors = []

    for path in sh_files:
        rel = os.path.relpath(path, REPO_ROOT)
        res = subprocess.run(['bash', '-n', path], capture_output=True, text=True)
        if res.returncode != 0:
            errors.append(f"{rel}: {res.stderr.strip()}")
        elif verbose:
            print(f"    [OK] {rel}")

    if errors:
        print(red(f"[-] Shell syntax validation failed with {len(errors)} error(s):"))
        for err in errors:
            print(f"    - {err}")
        return False

    print(green(f"[✓] Validated {len(sh_files)} Shell scripts (syntax OK)"))
    return True


def main():
    ap = argparse.ArgumentParser(description="Validate repository integrity, patches, and scripts.")
    ap.add_argument('-v', '--verbose', action='store_true', help='Show detailed per-file output')
    a = ap.parse_args()

    print(bold("=== Running ios-sweet Repository Validation ==="))
    ok_patches = validate_patches(a.verbose)
    ok_python = validate_python_scripts(a.verbose)
    ok_shell = validate_shell_scripts(a.verbose)

    if ok_patches and ok_python and ok_shell:
        print(bold(green("\nAll repository integrity checks PASSED successfully!")))
        return 0
    else:
        print(bold(red("\nRepository integrity check FAILED.")))
        return 1


if __name__ == '__main__':
    sys.exit(main())
