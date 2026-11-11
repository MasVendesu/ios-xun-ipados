import urllib.request, json

url = 'https://api.github.com/repos/HouseOfBalance/ios26-sapphire/actions/runs?per_page=5'
req = urllib.request.Request(url, headers={'User-Agent': 'Python-GH-Check'})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        for run in data.get('workflow_runs', []):
            run_id = run['id']
            status = run['status']
            conclusion = run['conclusion']
            commit_id = run['head_commit']['id'][:7]
            msg = run['head_commit']['message'].splitlines()[0]
            print(f"Run {run_id}: status={status}, conclusion={conclusion}, commit={commit_id} ({msg})")
except Exception as e:
    print("Error:", e)
