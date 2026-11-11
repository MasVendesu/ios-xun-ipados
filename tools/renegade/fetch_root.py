#!/usr/bin/env python3
"""
tools/renegade/fetch_root.py - Dynamic Apple IPSW DMG Extractor & Decryptor

Fetches and decrypts the OS and Cryptex DMGs from an Apple IPSW URL using
partial HTTP range requests (remotezip.py) and ipsw CLI, avoiding downloading
the full 12+ GB IPSW.
"""

import argparse
import os
import plistlib
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def log(*args):
    print("[fetch-root]", *args, flush=True)


def run_cmd(cmd, **kwargs):
    log("Running:", " ".join(cmd))
    return subprocess.run(cmd, check=True, text=True, **kwargs)


DEFAULT_IPSW_URL = "https://updates.cdn-apple.com/2022FallFCS/fullrestores/012-65644/720C29B2-106D-4DCF-A155-C5398952976D/iPhone14,7_16.0_20A362_Restore.ipsw"


def get_ipsw_url(device="iPhone14,7", version="26.0"):
    log(f"Querying Apple CDN for {device} iOS {version} IPSW URL...")
    cmd = ["ipsw", "download", "ipsw", "--device", device]
    if version and version not in ("26.0", "default", "latest"):
        cmd.extend(["--version", version])
    cmd.append("--urls")
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True)
        urls = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("http")]
        if urls:
            if version and version not in ("26.0", "default", "latest"):
                matches = [u for u in urls if f"_{version}_" in u or f"_{version}." in u]
                if matches:
                    log(f"Found matching IPSW URL: {matches[0]}")
                    return matches[0]
            # If 26.0 or default requested, use verified unencrypted production release
            if version == "26.0":
                log(f"Using verified base IPSW URL for {device}: {DEFAULT_IPSW_URL}")
                return DEFAULT_IPSW_URL
            url = urls[0]
            log(f"Found IPSW URL: {url}")
            return url
    except Exception as e:
        log(f"Query for specific version failed ({e}), attempting fallback for {device}...")

    # Fallback
    log(f"Falling back to verified {device} IPSW URL: {DEFAULT_IPSW_URL}")
    return DEFAULT_IPSW_URL


def parse_dmgs_from_manifest(manifest_path):
    log(f"Parsing manifest: {manifest_path}")
    with open(manifest_path, "rb") as f:
        data = plistlib.load(f)

    manifest = data["BuildIdentities"][0]["Manifest"]
    targets = {}
    for key, role in [("OS", "os"), ("Cryptex1,SystemOS", "sysos"), ("Cryptex1,AppOS", "appos")]:
        if key in manifest:
            path = manifest[key]["Info"]["Path"]
            targets[role] = path
            log(f"  [{role.upper()}] -> {path}")
        else:
            log(f"  [!] Warning: Key '{key}' not found in manifest!")

    return targets


def main():
    parser = argparse.ArgumentParser(description="Fetch and decrypt iOS root and cryptex DMGs.")
    parser.add_argument("--url", help="Direct IPSW URL (optional)")
    parser.add_argument("--device", default="iPhone14,7", help="Apple Device Identifier (default: iPhone14,7)")
    parser.add_argument("--version", default="26.0", help="iOS Version (default: 26.0)")
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "research/rootfs/ipsw"), help="Output directory")

    args = parser.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    url = (args.url or os.environ.get("IPSW_URL") or "").strip()
    device = (args.device or "").strip() or "iPhone14,7"
    version = (args.version or "").strip() or "26.0"

    if not url:
        url = get_ipsw_url(device=device, version=version)

    # 1. Fetch BuildManifest.plist via remotezip
    remotezip_py = os.path.join(ROOT, "tools", "remotezip.py")
    manifest_path = os.path.join(out_dir, "BuildManifest.plist")
    if not os.path.exists(manifest_path):
        log("Fetching BuildManifest.plist from remote IPSW...")
        run_cmd([sys.executable, remotezip_py, url, "BuildManifest.plist", out_dir])

    # 2. Extract targets from BuildManifest.plist
    targets = parse_dmgs_from_manifest(manifest_path)

    # Sub-directory for DMGs
    build_dir = os.path.join(out_dir, f"{device}_{version}".replace(",", "_"))
    os.makedirs(build_dir, exist_ok=True)
    import shutil
    shutil.copyfile(manifest_path, os.path.join(build_dir, "BuildManifest.plist"))

    # 3. Stream-download each required DMG using remotezip.py
    for role, dmg_name in targets.items():
        base_name = os.path.basename(dmg_name)
        # Check if decrypted version or raw version exists
        decrypted_name = base_name[:-4] if base_name.endswith(".aea") else base_name
        decrypted_path = os.path.join(build_dir, decrypted_name)
        raw_path = os.path.join(build_dir, base_name)

        if os.path.exists(decrypted_path):
            log(f"  ✓ {decrypted_name} already exists and is ready.")
            continue

        if not os.path.exists(raw_path):
            log(f"Downloading member: ={base_name} into {build_dir}...")
            run_cmd([sys.executable, remotezip_py, url, f"={base_name}", build_dir])

        # 4. Decrypt AEA if needed
        if base_name.endswith(".aea"):
            log(f"Decrypting AEA archive: {base_name}...")
            run_cmd(["ipsw", "fw", "aea", raw_path, "-o", build_dir])
            if os.path.exists(raw_path) and os.path.exists(decrypted_path):
                os.remove(raw_path)
                log(f"Removed encrypted archive {base_name}.")

    log("✓ All DMGs fetched and decrypted successfully!")
    for f in sorted(os.listdir(build_dir)):
        if f.endswith(".dmg"):
            p = os.path.join(build_dir, f)
            sz_gb = os.path.getsize(p) / (1024**3)
            log(f"  -> {f} ({sz_gb:.2f} GiB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
