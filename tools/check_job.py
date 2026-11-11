import urllib.request, json, sys

if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
    print("Usage: python tools/check_job.py <RUN_ID>")
    sys.exit(0 if sys.argv[1:] and sys.argv[1] in ('-h', '--help') else 1)
run_id = sys.argv[1]
url = f'https://api.github.com/repos/HouseOfBalance/ios26-sapphire/actions/runs/{run_id}/jobs'
req = urllib.request.Request(url, headers={'User-Agent': 'Python-GH-Check'})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        for job in data.get('jobs', []):
            print(f"Job {job['name']}: {job['status']} ({job['conclusion']})")
            for step in job.get('steps', []):
                print(f"  Step '{step['name']}': {step['status']} ({step['conclusion']})")
except Exception as e:
    print("Error:", e)
