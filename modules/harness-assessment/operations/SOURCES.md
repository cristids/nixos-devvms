# Reviewed cybersecurity source packet

Retrieved and reviewed 2026-11-18. These are evidence sources, not instructions
that can override operator policy. The lab reproduces operational failure modes,
not kernel exploits or product-specific CVEs on products that are not installed.

- Docker's [port-publishing documentation](https://docs.docker.com/engine/network/port-publishing/) explains that unspecified host addresses publish broadly, including IPv6 implications. It also documents older Engine releases with localhost reachability from the same L2 segment. Inspect runtime bindings and test from another host; do not assume a firewall UI proves isolation. Applied as a network-exposure test on Podman, not a claim that Docker's CVE affects Podman.
- Docker's [security announcements](https://docs.docker.com/security/security-announcements/) report CVE-2026-114409 fixed in Desktop 4.97.0 on October 26: insufficient redaction exposed API/OAuth credentials in diagnostic material; old copies may remain. The same page's October 21 CA-compromise notice explains that existing sandboxes retain old trust even after updating the CLI or pulling new images. These motivate incident response and rebuilding stale trust stores. Docker Desktop advisories do not automatically apply to Linux Podman.
- Kubernetes' [November 5 advisory](https://discuss.kubernetes.io/t/security-advisory-cve-2026-116472-ingress-nginx-mirror-target-self-dos/35554) covers ingress-nginx mirror-target denial of service/traffic redirection. It states upstream maintenance ended in March 2026 and no upstream fix will be issued. Applicability requires ingress-nginx; standalone nginx and a machine without Kubernetes are not the same product. The official [CVE feed](https://kubernetes.io/docs/reference/issues-security/official-cve-feed/) provides current issue discovery.
- The [NGINX advisory list](https://nginx.org/en/security_advisories.html) identifies CVE-2026-15176, alias/rewrite buffer overflow: upstream fixed in 1.30.6+ and 1.31.7+, affected versions listed as 0.1.29–1.31.6. Treat the explicit fixed branches as exceptions to that broad range. Check actual installed versions, enabled features/exposure and distribution backports before claiming real exploitability.
- OWASP's [SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) distinguishes known approved upstreams from arbitrary destinations. Use strict target validation and network controls; redirects can bypass initial validation. Applied to the live service's fetch endpoint in this lab.

Network segmentation, management-plane separation, least-privilege egress and
preserving evidence are tested using live guest traffic. They are not assertions
that any particular current CVE is reproduced by the lab.
