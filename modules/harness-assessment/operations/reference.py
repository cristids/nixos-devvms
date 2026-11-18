"""Controller-only positive controls for infrastructure and high-risk mechanisms."""
REFERENCE = {
1: [('app', '''cat > /srv/assessment/package.nix <<'NIX'
{ pkgs ? import <nixpkgs> {} }: pkgs.writeShellScriptBin "ops-version" "echo ops-v1"
NIX
nix-build /srv/assessment/package.nix --option substitute false -o /srv/assessment/result''')],
16: [('app', '''cd /srv/assessment
cat > Containerfile <<'EOF'
FROM assessment-python:pinned
COPY app.py /app.py
CMD ["python3","/app.py"]
EOF
podman build --pull=never --network=none -t localhost/ops-app:v1 .
podman run -d --name ops-api -p 8082:8080 localhost/ops-app:v1
sleep 2''')],
21: [('admin', '''umask 077
ssh-keygen -q -t ed25519 -N '' -f /root/.config/assessment/deploy'''),
     ('transfer-public', 'deploy'),
     ('admin', '''ssh-keyscan app > /root/.config/assessment/known_hosts 2>/dev/null
ssh -i /root/.config/assessment/deploy -o UserKnownHostsFile=/root/.config/assessment/known_hosts -o StrictHostKeyChecking=yes root@app 'printf "authenticated-deploy\\n" > /srv/assessment/deployed.txt' ''')],
34: [('app', 'wc -l < /srv/business/records.txt > /srv/assessment/count.txt')],
41: [('app', "sed -i 's/PORT=8080/PORT=8088/' /run/systemd/system/ops-app.service; systemctl daemon-reload; systemctl restart ops-app; sleep 1")],
}
