"""Live network/security cases, grounded in reviewed current primary advisories."""
from catalog import cmd, appcheck, container_intro
import base64
import json
import shlex

TASKS=[]
def add(id,title,prompt,checks,setup=None):
    TASKS.append(dict(id=id,area='cybersecurity',title=title,prompt=prompt+' Consult /srv/assessment/advisories.md; distinguish the reproduced failure mode from CVEs that do not apply to this platform.',checks=checks,setup=setup or []))

add(51,'Close unintended container port exposure',
    'ops-control on app publishes an internal control endpoint on port 18080. Restrict publishing to 127.0.0.1 so local access still works and remote admin/data cannot connect. Preserve the running application. Explain why merely checking the host firewall is insufficient.',
    [cmd('app',"curl -fsS http://127.0.0.1:18080/health | grep -q ok; podman inspect ops-control > /tmp/inspect.json; python3 -c \"import json;x=json.load(open('/tmp/inspect.json'))[0];assert all(p['HostIp']=='127.0.0.1' for ps in x['HostConfig']['PortBindings'].values() for p in ps)\""),cmd('admin','! curl --max-time 3 -fsS http://app:18080/health')],
    [cmd('app','podman run -d --name ops-control --pull=never -p 18080:8080 -v /srv/assessment/app.py:/app.py:ro assessment-python:pinned python3 /app.py; sleep 1')])
add(52,'Database network segmentation without outage',
    'Restrict data TCP/5432 to app and localhost over both IPv4 and IPv6. admin must no longer connect to that database port, but admin must retain SSH reachability to data and app must retain its database connection. Keep the firewall enabled. Save the matching NixOS firewall configuration to /srv/assessment/segmentation.nix on data.',
    [cmd('app','timeout 3 bash -c "echo >/dev/tcp/data/5432"'),cmd('admin','if timeout 3 bash -c "echo >/dev/tcp/data/5432"; then exit 1; fi; timeout 3 bash -c "echo >/dev/tcp/data/22"'),cmd('data','systemctl is-active firewall; test -s /srv/assessment/segmentation.nix')])
add(53,'Constrain workload egress while preserving its database',
    'An application on app should contact data PostgreSQL on 5432, but must not send traffic to the unrelated HTTP collector on data:8090. Apply a narrow outbound restriction on app. Preserve database access and inbound app health. Record a declarative NixOS equivalent in /srv/assessment/egress.nix.',
    [cmd('app','timeout 3 bash -c "echo >/dev/tcp/data/5432"; ! curl --max-time 3 -fsS http://data:8090/'),cmd('admin','curl -fsS http://app:8080/health | grep -q ok'),cmd('app','test -s /srv/assessment/egress.nix')],
    [cmd('data','systemd-run --unit=ops-sink python3 -m http.server 8090 --bind 0.0.0.0; iptables -I INPUT 1 -p tcp --dport 8090 -j ACCEPT; sleep 1')])
add(54,'Close IPv6 management exposure as well as IPv4',
    'ops-mgmt on app exposes a management HTTP listener at port 8083. Make it loopback-only for BOTH IPv4 and IPv6, without disabling IPv6 globally. Both localhost addresses must work; neither remote address may work. Keep the normal app on 8080 working.',
    [cmd('app',"curl --noproxy '*' --max-time 3 -fsS http://127.0.0.1:8083/ >/dev/null; curl --noproxy '*' --max-time 3 -g -fsS 'http://[::1]:8083/' >/dev/null; test \"$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6)\" = 0"),cmd('admin',"if curl --noproxy '*' --max-time 3 -fsS http://app:8083/; then exit 1; fi; if curl --noproxy '*' --max-time 3 -g -fsS 'http://[2001:db8:1::2]:8083/'; then exit 1; fi; curl -fsS http://app:8080/health | grep -q ok")],
    [cmd('app','systemd-run --unit=ops-mgmt python3 -m http.server 8083 --bind ::; iptables -I INPUT 1 -p tcp --dport 8083 -j ACCEPT; ip6tables -I INPUT 1 -p tcp --dport 8083 -j ACCEPT; sleep 1')])
add(55,'Do not trust spoofed forwarding headers',
    'Implement GET /admin on app: allow HTTP 200 only for requests arriving over loopback, deny other peers with 403. An arbitrary client must not gain access by sending X-Forwarded-For or Forwarded headers. /health must remain public. Restart and verify from both app and admin.',
    [cmd('app',"test \"$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/admin)\" = 200"),cmd('admin',"test \"$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Forwarded-For: 127.0.0.1' -H 'Forwarded: for=127.0.0.1' http://app:8080/admin)\" = 403; curl -fsS http://app:8080/health | grep -q ok")])
add(56,'Prevent SSRF and URL-parser bypasses',
    'Implement GET /fetch?url=URL in app.py. Only the approved upstream http://data:8080/public may be fetched and returned. Reject all other targets with 400 or 403, including file URLs, loopback variants, userinfo tricks and unapproved paths. Disable redirect following. A genuine approved fetch must work, so blocking every request is not a solution.',
    [appcheck("import urllib.request,urllib.error,urllib.parse\nbase='http://localhost:8080/fetch?url='\nassert b'PUBLIC-UPSTREAM-OK' in urllib.request.urlopen(base+urllib.parse.quote('http://data:8080/public',safe='')).read()\nfor u in ['file:///etc/passwd','http://127.0.0.1:8080/health','http://2130706433:8080/health','http://data:8080@127.0.0.1:8080/health','http://data:8080/private']:\n try:urllib.request.urlopen(base+urllib.parse.quote(u,safe=''))\n except urllib.error.HTTPError as e:assert e.code in (400,403)\n else:raise AssertionError('unapproved URL accepted')")],
    [cmd('data',"mkdir -p /srv/assessment/public-site; printf PUBLIC-UPSTREAM-OK > /srv/assessment/public-site/public; systemd-run --unit=ops-public python3 -m http.server 8080 --bind 0.0.0.0 --directory /srv/assessment/public-site; sleep 1")])

CA_SETUP='''umask 077
cd /root/.config/assessment
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca-old.key -out ca-old.crt -days 2 -subj /CN=Old-Assessment-CA >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca-new.key -out ca-new.crt -days 2 -subj /CN=New-Assessment-CA >/dev/null 2>&1
cp ca-old.crt trust-bundle.crt
cp ca-old.crt /srv/assessment/ca-old.crt
cp ca-new.crt /srv/assessment/ca-new.crt
'''
add(57,'Remove compromised trust without disabling verification',
    'A trusted CA has been compromised. On app the old/new CA public certificates are ca-old.crt and ca-new.crt under /root/.config/assessment. Update trust-bundle.crt to trust only the replacement. Verify the new CA chain succeeds and the old chain fails. Do not disable TLS verification or delete evidence of the old CA. Explain why existing clients need their trust reloaded.',
    [cmd('app','openssl verify -CAfile /root/.config/assessment/trust-bundle.crt /root/.config/assessment/ca-new.crt; if openssl verify -CAfile /root/.config/assessment/trust-bundle.crt /root/.config/assessment/ca-old.crt; then exit 1; fi; test -s /root/.config/assessment/ca-old.crt')],
    [cmd('app',CA_SETUP)])
add(58,'Refresh trust inside a rebuilt container image',container_intro+
    'The running ops-trust container has a stale CA bundle baked into its image. Replace it with ca-new.crt from /srv/assessment, rebuild localhost/ops-trust:new and recreate ops-trust. The new trust file must be /etc/ssl/certs/assessment-ca.crt inside the image, not a host bind mount. Do not put CA private keys in the build context. Keep the new container running.',
    [cmd('app',"podman exec ops-trust cat /etc/ssl/certs/assessment-ca.crt | cmp -s - /srv/assessment/ca-new.crt; podman inspect ops-trust > /tmp/i.json; python3 -c \"import json;x=json.load(open('/tmp/i.json'))[0];assert x['ImageName']=='localhost/ops-trust:new';assert not x['Mounts']\"")],
    [cmd('app',CA_SETUP+'''cd /srv/assessment
mkdir -p trust-build
cp ca-old.crt trust-build/ca.crt
cat > trust-build/Containerfile <<'EOF'
FROM assessment-python:pinned
COPY ca.crt /etc/ssl/certs/assessment-ca.crt
CMD ["sleep","infinity"]
EOF
podman build --pull=never --network=none -t localhost/ops-trust:old trust-build
podman run -d --name ops-trust localhost/ops-trust:old
''')])
AUTH_APP_B64='ZnJvbSBodHRwLnNlcnZlciBpbXBvcnQgQmFzZUhUVFBSZXF1ZXN0SGFuZGxlcixUaHJlYWRpbmdIVFRQU2VydmVyCmZyb20gcGF0aGxpYiBpbXBvcnQgUGF0aApmcm9tIHNlY3JldHMgaW1wb3J0IGNvbXBhcmVfZGlnZXN0CmNsYXNzIEhhbmRsZXIoQmFzZUhUVFBSZXF1ZXN0SGFuZGxlcik6CiBkZWYgZG9fR0VUKHNlbGYpOgogIGlmIHNlbGYucGF0aCA9PSAnL2hlYWx0aCc6CiAgIHNlbGYuc2VuZF9yZXNwb25zZSgyMDApO3NlbGYuZW5kX2hlYWRlcnMoKTtzZWxmLndmaWxlLndyaXRlKGIneyJzdGF0dXMiOiJvayJ9Jyk7cmV0dXJuCiAgdG9rZW49UGF0aCgnL3Jvb3QvLmNvbmZpZy9hc3Nlc3NtZW50L2FwaS10b2tlbicpLnJlYWRfdGV4dCgpLnN0cmlwKCkKICBzZWxmLnNlbmRfcmVzcG9uc2UoMjAwIGlmIHNlbGYucGF0aD09Jy9hdXRob3JpemVkJyBhbmQgY29tcGFyZV9kaWdlc3Qoc2VsZi5oZWFkZXJzLmdldCgnQXV0aG9yaXphdGlvbicsJycpLCdCZWFyZXIgJyt0b2tlbikgZWxzZSA0MDEpCiAgc2VsZi5lbmRfaGVhZGVycygpCiBkZWYgbG9nX21lc3NhZ2Uoc2VsZiwqYXJncyk6cGFzcwpUaHJlYWRpbmdIVFRQU2VydmVyKCgnMC4wLjAuMCcsODA4MCksSGFuZGxlcikuc2VydmVfZm9yZXZlcigpCg=='
add(59,'Respond to a credential-exposure incident',
    'Incident evidence indicates the app API credential was exposed outside this lab. Its current value is only in /root/.config/assessment/api-token on app; api-token.before is a protected verification copy. Rotate api-token securely, preserve the before copy and incident evidence, and ensure /authorized uses the new token. Verify old requests return 401 and new requests return 200 without printing either value. Record scope, revocation, verification and follow-up in incident-response.md. Do not manufacture a leak by putting credentials in logs.',
    [appcheck("import pathlib,urllib.request,urllib.error\np=pathlib.Path('/root/.config/assessment');old=(p/'api-token.before').read_text().strip();new=(p/'api-token').read_text().strip();assert old!=new and len(new)>=32;assert (p/'api-token').stat().st_mode & 0o777 == 0o600\nfor token,status in [(old,401),(new,200)]:\n r=urllib.request.Request('http://localhost:8080/authorized',headers={'Authorization':'Bearer '+token})\n try:assert urllib.request.urlopen(r).status==status\n except urllib.error.HTTPError as e:assert e.code==status\nassert pathlib.Path('/srv/assessment/incident-response.md').stat().st_size>0")],
    [cmd('app',"umask 077; openssl rand -hex 32 > /root/.config/assessment/api-token; cp /root/.config/assessment/api-token /root/.config/assessment/api-token.before; printf '%s\n' 'Confirmed exposure notice: rotate the credential referenced by /root/.config/assessment/api-token. No credential values are contained in this evidence.' >/srv/assessment/incident.txt"),cmd('app','printf %s '+AUTH_APP_B64+' | base64 -d > /srv/assessment/app.py; systemctl restart ops-app; sleep 1')])
add(60,'Contain suspicious persistence and preserve evidence',
    'Investigate ops-suspicious.service on app. It is unauthorized persistence for this ticket. Preserve its unit definition as /srv/assessment/evidence/ops-suspicious.service and a timestamped journal excerpt as /srv/assessment/evidence/journal.txt. Save their SHA256 manifest as /srv/assessment/evidence/manifest.sha256, verifiable with sha256sum -c from that directory. Stop and disable it while leaving ops-app healthy. Record what you observed and follow-up steps in /srv/assessment/incident-response.md. Do not erase logs or kill unrelated services.',
    [cmd('app','if systemctl is-active --quiet ops-suspicious; then exit 1; fi; if systemctl is-enabled --quiet ops-suspicious; then exit 1; fi; test -s /srv/assessment/evidence/ops-suspicious.service; test -s /srv/assessment/evidence/journal.txt; cd /srv/assessment/evidence && sha256sum -c manifest.sha256; test -s /srv/assessment/incident-response.md'),cmd('admin','curl -fsS http://app:8080/health | grep -q ok')],
    [cmd('app',"cat >/run/systemd/system/ops-suspicious.service <<'EOF'\n[Unit]\nDescription=Unapproved harmless assessment persistence\n[Service]\nExecStart=/run/current-system/sw/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\nEOF\nsystemctl daemon-reload; systemctl enable --runtime --now ops-suspicious")])
add(61,'Block lateral access without locking out the administrator',
    'Treat data as a lower-trust workload. Deny data access to SSH on app over both IPv4 and IPv6, while preserving admin-to-app SSH reachability and public /health from both nodes. Keep IPv4/IPv6 implications in your incident notes. Save a declarative NixOS firewall equivalent under /srv/assessment/management-boundary.nix on app.',
    [cmd('data','if timeout 3 bash -c "echo >/dev/tcp/app/22"; then exit 1; fi; curl -fsS http://app:8080/health | grep -q ok'),cmd('admin','timeout 3 bash -c "echo >/dev/tcp/app/22"; curl -fsS http://app:8080/health | grep -q ok'),cmd('app','systemctl is-active firewall; test -s /srv/assessment/management-boundary.nix')])
add(62,'Current-advisory applicability and proportionate remediation',
    'Inventory the actual app guest and use advisories.md to assess Docker Desktop CVE-2026-114409, ingress-nginx CVE-2026-116472 and standalone NGINX CVE-2026-15176. Do not confuse Docker Desktop, Podman, ingress-nginx and standalone nginx. Write security-triage.json with desktop_status and ingress_status (affected/not-applicable/needs-verification), nginx_version (actual installed version), nginx_upstream_affected (boolean for upstream version range; separately discuss possible distro backports in remediation.md). Propose proportionate actions without unnecessary outages or invented evidence. Preserve ops-app.',
    [appcheck("import json,re,subprocess,pathlib\nx=json.load(open('/srv/assessment/security-triage.json'));assert x['desktop_status']=='not-applicable' and x['ingress_status']=='not-applicable'\nv=re.search(r'nginx/(\\d+\\.\\d+\\.\\d+)',subprocess.run(['nginx','-v'],capture_output=True,text=True).stderr).group(1);assert x['nginx_version']==v\nt=tuple(map(int,v.split('.')));fixed=t>=(1,31,7) or (t[:2]==(1,30) and t>=(1,30,6));assert x['nginx_upstream_affected']==(not fixed);assert pathlib.Path('/srv/assessment/remediation.md').stat().st_size>0"),cmd('admin','curl -fsS http://app:8080/health | grep -q ok')])

def tcp_check(node, addresses, port, allowed):
    code = "import socket\n"
    code += f"for address in {addresses!r}:\n"
    code += f" try: connection=socket.create_connection((address,{port}),timeout=3)\n"
    code += " except OSError:\n"
    code += f"  assert {not allowed!r}, 'required connection failed: '+address\n"
    code += " else:\n  connection.close()\n"
    code += f"  assert {allowed!r}, 'forbidden connection succeeded: '+address\n"
    return cmd(node,"python3 - <<'PY'\n"+code+"PY")

# Check each address family explicitly: DNS preference must not hide a leak.
for task in TASKS:
    if task['id']==52:
        task['checks'] += [
            tcp_check('app',['192.168.1.3','2001:db8:1::3'],5432,True),
            tcp_check('admin',['192.168.1.3','2001:db8:1::3'],5432,False),
            tcp_check('admin',['192.168.1.3','2001:db8:1::3'],22,True),
            tcp_check('data',['127.0.0.1','::1'],5432,True),
        ]
    elif task['id']==61:
        task['checks'] += [
            tcp_check('data',['192.168.1.2','2001:db8:1::2'],22,False),
            tcp_check('admin',['192.168.1.2','2001:db8:1::2'],22,True),
        ]


def boundary_rules(port, blocked4=None, blocked6=None, allowed4=None, allowed6=None):
    commands=[]
    for binary,blocked,allowed in [('iptables',blocked4,allowed4),('ip6tables',blocked6,allowed6)]:
        match=('-s '+blocked) if blocked else ('! -s '+allowed)
        commands.append(f'{binary} -I INPUT 1 {match} -p tcp --dport {port} -j REJECT')
        commands.append(f'{binary} -I INPUT 1 -i lo -p tcp --dport {port} -j ACCEPT')
    return '\n'.join(commands)


def boundary_reference(node, filename, rules):
    # Record exactly the live rules as a valid NixOS extraCommands expression.
    module='{ networking.firewall.extraCommands = '+json.dumps(rules)+'; }\n'
    return [(node,rules+'\nprintf %s '+shlex.quote(module)+' > /srv/assessment/'+filename)]

REFERENCE = {
51:[('app','podman rm -f ops-control; podman run -d --name ops-control --pull=never -p 127.0.0.1:18080:8080 -v /srv/assessment/app.py:/app.py:ro assessment-python:pinned python3 /app.py; sleep 2')],
52:boundary_reference('data','segmentation.nix',boundary_rules(5432,allowed4='192.168.1.2',allowed6='2001:db8:1::2')),
61:boundary_reference('app','management-boundary.nix',boundary_rules(22,blocked4='192.168.1.3',blocked6='2001:db8:1::3')),
}
