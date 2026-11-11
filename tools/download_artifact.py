import subprocess, os, zipfile, sys, json, urllib.request

run_id = sys.argv[1] if len(sys.argv) > 1 else '42536240172'

p = subprocess.Popen(['git', 'credential', 'fill'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
out, err = p.communicate(input='protocol=https\nhost=github.com\n\n')
token = None
for line in out.splitlines():
    if line.startswith('password='):
        token = line.split('=', 1)[1]
        break

headers = {'User-Agent': 'Mozilla/5.0'}
if token:
    headers['Authorization'] = f'Bearer {token}'

# Get artifacts list for run
url = f'https://api.github.com/repos/HouseOfBalance/ios26-sapphire/actions/runs/{run_id}/artifacts'
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))

artifacts = data.get('artifacts', [])
if not artifacts:
    print(f"No artifacts found for run {run_id}")
    sys.exit(1)

art = artifacts[0]
art_id = art['id']
art_name = art['name']
dl_url = art['archive_download_url']
print(f"Found artifact: {art_name} (ID: {art_id})")

os.makedirs('out', exist_ok=True)
zip_path = f"out/{run_id}.zip"

cmd = ['curl.exe', '-f', '-s', '-S', '-L']
if token:
    cmd.extend(['-H', f'Authorization: Bearer {token}'])
cmd.extend(['-H', 'User-Agent: curl', '-o', zip_path, dl_url])

print(f"Downloading artifact to {zip_path}...")
subprocess.run(cmd, check=True)

extract_dir = f"out/{run_id}"
os.makedirs(extract_dir, exist_ok=True)
print(f"Extracting to {extract_dir}...")
with zipfile.ZipFile(zip_path) as z:
    z.extractall(extract_dir)

print("Extracted files:")
for root, dirs, files in os.walk(extract_dir):
    for f in files:
        fp = os.path.join(root, f)
        print(f"  {f} ({os.path.getsize(fp)} bytes)")
        if f == 'enxerto-boot.img':
            # Also copy to root of hakios repo
            dst = os.path.join('C:\\Users\\hungtv\\Downloads\\hakios-arm64e-dada-main', 'enxerto-boot.img')
            import shutil
            shutil.copyfile(fp, dst)
            print(f"  -> Copied to {dst}")
