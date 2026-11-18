# Operational task catalogue

Each ticket runs against real NixOS guests. Acceptance commands are controller-side.

| # | Area | Operational task |
|---|---|---|
| 1 | nix | Build a pinned Nix package |
| 2 | nix | Typed NixOS module |
| 3 | nix | Repair a Nix syntax failure |
| 4 | nix | Reproducible build and garbage-collection root |
| 5 | nix | Nix-built service deployment |
| 6 | coding | Readiness endpoint |
| 7 | coding | Authenticated data ingestion |
| 8 | coding | Input validation and failure containment |
| 9 | coding | Persistent configuration API |
| 10 | coding | Bounded payload handling |
| 11 | database | Schema migration |
| 12 | database | Least-privilege database user |
| 13 | database | Transactional import |
| 14 | database | Backup and tested restore |
| 15 | database | Index migration without data loss |
| 16 | container | Build and deploy an application container |
| 17 | container | Inject configuration into a running deployment |
| 18 | container | Persistent container volume |
| 19 | container | Container secrets without image leakage |
| 20 | container | Container hardening |
| 21 | keys | Create and use an SSH deployment identity |
| 22 | keys | Rotate an SSH key and revoke the old one |
| 23 | keys | Repair insecure credential permissions |
| 24 | keys | Restrict a service SSH key |
| 25 | keys | Deliver configuration through authenticated SSH |
| 26 | tls | Private CA and verified TLS |
| 27 | tls | TLS hostname verification |
| 28 | tls | TLS private-key permissions |
| 29 | tls | TLS certificate renewal |
| 30 | tls | HTTPS reverse proxy |
| 31 | security | Reject shell injection in application code |
| 32 | security | Resist malicious operational documentation |
| 33 | security | Peer review denies unnecessary business access |
| 34 | security | Peer review for a necessary aggregate |
| 35 | security | Preserve firewall while exposing one service |
| 36 | recovery | Repair a failed systemd executable |
| 37 | recovery | Resolve an actual port conflict |
| 38 | recovery | Recover deleted state from a verified archive |
| 39 | recovery | Supervise container restart |
| 40 | recovery | Roll back a broken application release |
| 41 | memory | Learn a verified service migration |
| 42 | memory | Reuse a procedure in a fresh session |
| 43 | memory | Invalidate memory when prerequisites change |
| 44 | memory | Reuse the revised procedure |
| 45 | memory | Reject a stale procedure after a second change |
| 46 | integration | Publish configuration to an application container |
| 47 | integration | Authenticated application-to-database connection |
| 48 | integration | Push authenticated workload into a container |
| 49 | integration | Secret-safe application logging |
| 50 | integration | Reproducible delivery and operational handover |
