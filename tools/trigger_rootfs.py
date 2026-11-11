import urllib.request
import json
import os
import subprocess
import sys


def get_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            check=True
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def list_workflows():
    token = get_token()
    if not token:
        print("Error: No GitHub token found in git credentials or environment.")
        return
    url = "https://api.github.com/repos/HouseOfBalance/ios26-sapphire/actions/workflows"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-Trigger"
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            for wf in data.get("workflows", []):
                print(f"ID: {wf['id']}, Name: '{wf['name']}', Path: {wf['path']}, State: {wf['state']}")
    except Exception as e:
        print("Error listing workflows:", e)


def trigger_rootfs(device="iPhone14,7", version="26.0", ipsw_url=""):
    token = get_token()
    if not token:
        print("Error: No GitHub token found in git credentials or environment.")
        return
    url = "https://api.github.com/repos/HouseOfBalance/ios26-sapphire/actions/workflows/build-rootfs.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {
            "device": device,
            "build_version": version,
            "ipsw_url": ipsw_url
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "Python-Trigger"
    }, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Trigger successful! HTTP Status: {resp.status}")
    except Exception as e:
        print("Trigger error:", e)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        import argparse
        parser = argparse.ArgumentParser(description="Trigger HakiOS 26 RootFS Workflow")
        parser.add_argument("cmd", choices=["run"])
        parser.add_argument("--device", default="iPhone14,7")
        parser.add_argument("--version", default="26.0")
        parser.add_argument("--url", default="", help="Direct IPSW URL")
        args, _ = parser.parse_known_args()
        trigger_rootfs(device=args.device, version=args.version, ipsw_url=args.url)
    else:
        list_workflows()
