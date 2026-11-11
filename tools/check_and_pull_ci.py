#!/usr/bin/env python3
"""
check_and_pull_ci.py - GitHub Actions CI workflow watcher, artifact downloader, and boot installer.

Target repository: HouseOfBalance/ios26-sapphire
Zero external dependencies (uses standard library: urllib.request, json, sys, os, time, zipfile, shutil, hashlib).
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

DEFAULT_REPO = "HouseOfBalance/ios26-sapphire"
DEFAULT_ARTIFACT_NAME = "hakios-ios26-sapphire"
DEFAULT_INSTALL_PATH = r"C:\Users\hungtv\platform-tools\enxerto-boot.img"
USER_AGENT = "hakios-ci-tool/1.0 (Windows; Python)"


class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """
    HTTP redirect handler that strips the Authorization header when redirected
    off github.com (e.g. to Azure Blob storage), preventing authentication errors.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req and "github.com" not in newurl.lower():
            for key in list(new_req.headers.keys()):
                if key.lower() == "authorization":
                    del new_req.headers[key]
        return new_req


def get_default_token():
    """
    Resolve GitHub token from environment variables or git credential helper.
    """
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(var, "").strip()
        if tok:
            return tok

    # Try Git credential helper
    git_paths = ["git", r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"]
    for git_cmd in git_paths:
        try:
            proc = subprocess.Popen(
                [git_cmd, "credential", "fill"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, _ = proc.communicate("protocol=https\nhost=github.com\n\n", timeout=3)
            for line in stdout.splitlines():
                if line.startswith("password="):
                    tok = line.split("=", 1)[1].strip()
                    if tok:
                        return tok
        except Exception:
            continue

    return None


def format_rate_limit(headers):
    """Format rate limit status from response headers."""
    limit = headers.get("x-ratelimit-limit")
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")

    if not limit or not remaining:
        return ""

    reset_str = ""
    if reset and reset.isdigit():
        reset_ts = int(reset)
        diff_sec = max(0, int(reset_ts - time.time()))
        diff_min = diff_sec // 60
        reset_str = f", reset in {diff_min}m {diff_sec % 60}s"

    return f"{remaining}/{limit} requests remaining{reset_str}"


def api_request(url, token=None, custom_opener=None):
    """
    Perform a GET request to GitHub REST API with robust rate-limit and error handling.
    Returns (data, headers).
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    opener = custom_opener or urllib.request.build_opener()

    try:
        with opener.open(req) as resp:
            data = resp.read()
            return data, resp.headers
    except urllib.error.HTTPError as e:
        status = e.code
        limit = e.headers.get("x-ratelimit-limit", "unknown")
        remaining = e.headers.get("x-ratelimit-remaining", "unknown")
        reset = e.headers.get("x-ratelimit-reset")

        if status in (403, 429) and remaining == "0":
            reset_msg = ""
            if reset and reset.isdigit():
                reset_ts = int(reset)
                dt = datetime.datetime.fromtimestamp(reset_ts, datetime.timezone.utc)
                diff = max(0, int(reset_ts - time.time()))
                reset_msg = f"\n  Reset Time: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')} (in ~{diff // 60} min {diff % 60} s)"

            sys.stderr.write(
                f"\n[ERROR] GitHub API rate limit exceeded (HTTP {status})!\n"
                f"  Limit: {remaining}/{limit}{reset_msg}\n\n"
                f"Tips to avoid rate limiting:\n"
                f"  - Provide a GitHub token using --token <TOKEN>\n"
                f"  - Or set the GITHUB_TOKEN or GH_TOKEN environment variable.\n"
                f"  - Authenticated requests allow 5,000 req/hr (vs 60 unauthenticated).\n\n"
            )
            sys.exit(2)
        elif status == 401:
            sys.stderr.write(
                f"\n[ERROR] GitHub API Authentication Failed (HTTP 401 Unauthorized).\n"
                f"  Provided token is invalid or does not have sufficient permissions.\n\n"
            )
            sys.exit(2)
        elif status == 404:
            sys.stderr.write(f"\n[ERROR] Resource not found (HTTP 404): {url}\n\n")
            sys.exit(1)
        else:
            sys.stderr.write(f"\n[ERROR] GitHub API request failed with HTTP {status}: {e.reason}\n\n")
            sys.exit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(f"\n[ERROR] Network error contacting GitHub API: {e.reason}\n\n")
        sys.exit(1)


def get_workflow_runs(repo, token=None, limit=5):
    """Fetch recent workflow runs."""
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page={limit}"
    raw_data, headers = api_request(url, token)
    parsed = json.loads(raw_data.decode("utf-8"))
    return parsed.get("workflow_runs", []), headers


def get_single_run(repo, run_id, token=None):
    """Fetch a single workflow run by ID."""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    raw_data, headers = api_request(url, token)
    return json.loads(raw_data.decode("utf-8")), headers


def get_run_artifacts(repo, run_id, token=None):
    """Fetch artifacts for a workflow run."""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    raw_data, headers = api_request(url, token)
    parsed = json.loads(raw_data.decode("utf-8"))
    return parsed.get("artifacts", []), headers


def display_runs_table(runs, repo, headers=None, token_present=False):
    """Display workflow runs in a formatted table."""
    auth_status = "Authenticated" if token_present else "Unauthenticated"
    rl_info = f" [{format_rate_limit(headers)}]" if headers else ""
    print(f"\nRepository: {repo} ({auth_status}{rl_info})")
    print(f"Workflow Runs (Displaying {len(runs)}):")

    header = (
        f"{'Run ID':<13} "
        f"{'Workflow Name':<32} "
        f"{'Event':<8} "
        f"{'Status':<12} "
        f"{'Conclusion':<11} "
        f"{'Commit':<8} "
        f"{'Branch':<12} "
        f"{'Created At':<20}"
    )
    separator = "-" * len(header)
    print(separator)
    print(header)
    print(separator)

    for r in runs:
        run_id = str(r.get("id", ""))
        name = (r.get("name") or "Workflow")[:31]
        event = (r.get("event") or "")[:7]
        status = (r.get("status") or "")[:11]
        conclusion = (r.get("conclusion") or "-")[:10]
        sha = (r.get("head_sha") or "")[:7]
        branch = (r.get("head_branch") or "")[:11]
        created_at = r.get("created_at") or ""

        # Color-friendly markers or clean text
        print(
            f"{run_id:<13} "
            f"{name:<32} "
            f"{event:<8} "
            f"{status:<12} "
            f"{conclusion:<11} "
            f"{sha:<8} "
            f"{branch:<12} "
            f"{created_at:<20}"
        )
    print(separator + "\n")


def watch_run(repo, run_id, token=None, interval=15):
    """
    Poll a workflow run until status is 'completed'.
    Prints dynamic updates.
    """
    print(f"[*] Watching Run ID {run_id} on {repo} (polling every {interval}s)...")
    start_time = time.time()
    last_status = None
    last_conclusion = None

    while True:
        run, _ = get_single_run(repo, run_id, token)
        status = run.get("status")
        conclusion = run.get("conclusion")
        name = run.get("name")
        sha = (run.get("head_sha") or "")[:7]
        elapsed = int(time.time() - start_time)
        now_str = datetime.datetime.now().strftime("%H:%M:%S")

        status_display = f"[{now_str} | +{elapsed:3d}s] Run {run_id} ({name} @ {sha}): status={status}"
        if conclusion:
            status_display += f", conclusion={conclusion}"

        if (status != last_status) or (conclusion != last_conclusion) or (elapsed % (interval * 2) == 0):
            print(f"  {status_display}")
            last_status = status
            last_conclusion = conclusion

        if status == "completed":
            print(f"[+] Run {run_id} completed with conclusion: '{conclusion}' (total time: {elapsed}s)")
            return run

        time.sleep(interval)


def download_artifact(repo, run_id, artifact_name, out_dir, token=None, force=False):
    """
    Download and extract artifact zip file.
    Follows HTTP 302 redirects removing Authorization header for Azure Blob Storage.
    """
    artifacts, _ = get_run_artifacts(repo, run_id, token)
    if not artifacts:
        sys.stderr.write(f"[ERROR] No artifacts found for run {run_id}.\n")
        sys.exit(1)

    target_art = None
    for art in artifacts:
        if art.get("name") == artifact_name:
            target_art = art
            break

    if not target_art:
        available = [a.get("name") for a in artifacts]
        if len(artifacts) == 1:
            target_art = artifacts[0]
            print(f"[*] Artifact '{artifact_name}' not found, using only available artifact: '{target_art['name']}'")
        else:
            sys.stderr.write(
                f"[ERROR] Artifact '{artifact_name}' not found for run {run_id}.\n"
                f"  Available artifacts: {', '.join(available)}\n"
            )
            sys.exit(1)

    art_id = target_art["id"]
    art_name = target_art["name"]
    art_size = target_art.get("size_in_bytes", 0)
    dl_url = target_art["archive_download_url"]

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{art_name}.zip")
    extract_dir = os.path.join(out_dir, art_name)

    if not force and os.path.isfile(zip_path) and (art_size == 0 or os.path.getsize(zip_path) == art_size):
        print(f"[*] Found existing matching zip archive at {zip_path} ({os.path.getsize(zip_path):,} bytes). Skipping download.")
    else:
        print(f"[*] Downloading artifact '{art_name}' (ID: {art_id}, size: {art_size:,} bytes)...")
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(dl_url, headers=headers)
        opener = urllib.request.build_opener(NoAuthRedirect)

        start_dl = time.time()
        try:
            with opener.open(req) as resp, open(zip_path, "wb") as f:
                total_read = 0
                chunk_size = 131072  # 128 KB
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_read += len(chunk)
                    if art_size > 0:
                        pct = (total_read / art_size) * 100
                        mb_read = total_read / (1024 * 1024)
                        mb_total = art_size / (1024 * 1024)
                        print(f"\r  Progress: {mb_read:.1f}/{mb_total:.1f} MB ({pct:.1f}%)", end="", flush=True)
                    else:
                        mb_read = total_read / (1024 * 1024)
                        print(f"\r  Downloaded: {mb_read:.1f} MB", end="", flush=True)
            print()
        except Exception as e:
            sys.stderr.write(f"\n[ERROR] Failed to download artifact: {e}\n")
            sys.exit(1)

        dl_time = max(0.1, time.time() - start_dl)
        mbps = (total_read / (1024 * 1024)) / dl_time
        print(f"[+] Artifact saved to {zip_path} ({total_read:,} bytes in {dl_time:.1f}s @ {mbps:.2f} MB/s)")

    print(f"[*] Extracting to {extract_dir}...")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
        names = zf.namelist()

    print(f"[+] Extracted {len(names)} files:")
    for n in names:
        p = os.path.join(extract_dir, n)
        sz = os.path.getsize(p) if os.path.isfile(p) else 0
        print(f"    - {n} ({sz:,} bytes)")

    return extract_dir, zip_path


def compute_sha256(filepath):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(131072):
            h.update(chunk)
    return h.hexdigest()


def install_boot_image(extract_dir, install_path):
    """
    Locate enxerto-boot.img from extract_dir, copy to install_path, and verify size and SHA-256.
    """
    boot_name = "enxerto-boot.img"
    src_file = None

    # Search for enxerto-boot.img in extract_dir
    direct_path = os.path.join(extract_dir, boot_name)
    if os.path.isfile(direct_path):
        src_file = direct_path
    else:
        for root, _, files in os.walk(extract_dir):
            if boot_name in files:
                src_file = os.path.join(root, boot_name)
                break

    if not src_file:
        sys.stderr.write(f"[ERROR] Could not find '{boot_name}' in extracted artifact directory: {extract_dir}\n")
        sys.exit(1)

    src_size = os.path.getsize(src_file)
    src_hash = compute_sha256(src_file)
    print(f"[*] Source file: {src_file}")
    print(f"    Size:   {src_size:,} bytes")
    print(f"    SHA256: {src_hash}")

    dest_dir = os.path.dirname(os.path.abspath(install_path))
    os.makedirs(dest_dir, exist_ok=True)

    print(f"[*] Copying to destination: {install_path}...")
    shutil.copy2(src_file, install_path)

    dest_size = os.path.getsize(install_path)
    dest_hash = compute_sha256(install_path)

    print(f"[*] Destination verification:")
    print(f"    Path:   {install_path}")
    print(f"    Size:   {dest_size:,} bytes")
    print(f"    SHA256: {dest_hash}")

    if src_size == dest_size and src_hash == dest_hash:
        print(f"[+] Verification SUCCESS: File size and SHA-256 match perfectly! [OK]")
    else:
        sys.stderr.write(f"[ERROR] Verification FAILED: Checksum or file size mismatch after copying!\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Check and pull CI runs and artifacts from HouseOfBalance/ios26-sapphire GitHub Actions."
    )
    parser.add_argument(
        "-n", "--limit", type=int, default=5, help="Number of recent workflow runs to list (default: 5)"
    )
    parser.add_argument(
        "--watch", action="store_true", help="Poll the target run until completed, printing dynamic status updates"
    )
    parser.add_argument(
        "--interval", type=int, default=15, help="Polling interval in seconds for --watch (default: 15)"
    )
    parser.add_argument(
        "--download", action="store_true", help="Download artifact upon successful completion of the run"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=f"Extract enxerto-boot.img and copy to install path (default: {DEFAULT_INSTALL_PATH})",
    )
    parser.add_argument(
        "--run-id", type=int, help="Target specific workflow run ID (default: latest run)"
    )
    parser.add_argument(
        "--latest-success",
        action="store_true",
        help="Target the most recent successful run instead of the absolute latest run",
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help=f"GitHub repository owner/repo (default: {DEFAULT_REPO})"
    )
    parser.add_argument(
        "--artifact-name",
        default=DEFAULT_ARTIFACT_NAME,
        help=f"Artifact name to download (default: {DEFAULT_ARTIFACT_NAME})",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Local directory for downloaded and extracted artifacts (default: <repo_root>/out/ci_download)",
    )
    parser.add_argument(
        "--install-path",
        default=DEFAULT_INSTALL_PATH,
        help=f"Destination path for enxerto-boot.img (default: {DEFAULT_INSTALL_PATH})",
    )
    parser.add_argument(
        "--token", default=None, help="GitHub Personal Access Token (defaults to env or git credentials)"
    )
    parser.add_argument(
        "--no-auth", action="store_true", help="Force unauthenticated API requests (to test rate limit behavior)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Force re-download even if zip archive already exists locally"
    )

    args = parser.parse_args()

    # If --install is specified, --download is implied
    if args.install:
        args.download = True

    # Resolve token
    token = None
    if not args.no_auth:
        token = args.token or get_default_token()

    # Resolve default out_dir
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    out_dir = args.out_dir or os.path.join(repo_root, "out", "ci_download")

    # 1. Fetch recent runs
    runs, headers = get_workflow_runs(args.repo, token=token, limit=max(args.limit, 10))

    if not runs:
        print(f"No workflow runs found for {args.repo}.")
        return

    # Display table of recent runs
    display_runs_table(runs[: args.limit], args.repo, headers, token_present=bool(token))

    # If neither --watch nor --download nor --install is specified, we're done
    if not (args.watch or args.download or args.install):
        return

    # 2. Identify the target run
    target_run = None
    if args.run_id:
        target_run, _ = get_single_run(args.repo, args.run_id, token=token)
    elif args.latest_success:
        for r in runs:
            if r.get("conclusion") == "success":
                target_run = r
                break
        if not target_run:
            sys.stderr.write(f"[ERROR] No successful run found among recent runs in {args.repo}.\n")
            sys.exit(1)
        print(f"[*] Selected latest successful run ID: {target_run['id']} ({target_run.get('name')})")
    else:
        target_run = runs[0]

    run_id = target_run["id"]
    status = target_run.get("status")
    conclusion = target_run.get("conclusion")

    # 3. Handle --watch
    if args.watch:
        if status != "completed":
            target_run = watch_run(args.repo, run_id, token=token, interval=args.interval)
            status = target_run.get("status")
            conclusion = target_run.get("conclusion")
        else:
            print(f"[*] Run {run_id} is already completed (conclusion: '{conclusion}').")

    # 4. Handle --download / --install
    if args.download:
        if status != "completed":
            sys.stderr.write(
                f"[ERROR] Run {run_id} is currently '{status}' (not completed).\n"
                f"  Use --watch to wait for it to finish before downloading.\n"
            )
            sys.exit(1)

        if conclusion != "success":
            sys.stderr.write(
                f"[ERROR] Run {run_id} completed with conclusion '{conclusion}' (not 'success').\n"
                f"  To download the latest successful run instead, pass --latest-success.\n"
                f"  Or specify a known successful run with --run-id <ID>.\n"
            )
            sys.exit(1)

        if not token:
            sys.stderr.write(
                f"[WARNING] GitHub artifact downloads require authentication.\n"
                f"  Attempting download without token may fail with HTTP 401 or 404.\n"
            )

        extract_dir, _ = download_artifact(
            args.repo, run_id, args.artifact_name, out_dir=out_dir, token=token, force=args.force
        )

        # 5. Handle --install
        if args.install:
            print(f"\n[*] Installing boot image to platform-tools...")
            install_boot_image(extract_dir, args.install_path)


if __name__ == "__main__":
    main()
