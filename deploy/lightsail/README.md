# Personal Lightsail deployment

This deploys **gpardon-alo/stalwart**, built from this fork's source. It does not publish images, issues, PRs or branches to Stalwart Labs. The existing upstream release workflows are separate; use **Deploy personal Stalwart** for this deployment.

## What runs

GitHub Actions builds the fork using the upstream Dockerfile, publishes `ghcr.io/gpardon-alo/stalwart:sha-COMMIT`, and deploys its immutable digest over SSH. The host runs one container with persistent `stalwart_config` and `stalwart_data` Docker volumes. Before replacing an existing container, the deployment makes an offline backup and restarts the old container. The new container must pass the image's health check within 180 seconds.

Main pushes affecting server source, Docker/Compose configuration, or
`deploy/lightsail/**` trigger this server deployment. Manual runs are available
through Actions on **main**. Other branches and other GitHub repositories cannot
deploy through this workflow. No AWS credentials are needed by Actions. A new
Lightsail host starts with empty volumes: your laptop's settings and mail are
NOT copied automatically.

Webmail is deployed separately by **Deploy personal webmail**. Interface updates
build and upload only the customized ZIP and reload web applications, leaving
the mail container running. See [the webmail deployment notes](../webmail/README.md#automatic-deployment)
for its admin authorization secret and Application prefix. Both deployment
scripts share a host lock; server deployments preserve the installed webmail in
the persistent data volume and do not replace it with a bundled copy.

## Prepare Lightsail

1. Choose the intended Ubuntu host, attach a static IP, and enable instance snapshots. Install Docker Engine, Compose v2 with `up --wait` support, Git, Python 3 and `flock` (util-linux). Give the SSH deployment user Docker access.
2. Check CPU architecture with `uname -m`. The workflow defaults to an x86-64 runner/image. For an ARM/Graviton host (`aarch64`), set repository variable `LIGHTSAIL_RUNNER=ubuntu-24.04-arm` before building. Rust builds run in Actions, not on the instance; the first build can take substantial time.
3. Check existing port ownership with `sudo ss -lntp`. A dedicated instance can publish HTTPS on 443. If prediction market's Caddy owns 443, follow **Sharing the prediction market host** below before starting mail.
4. Allow inbound TCP 25 (receiving), 465/587 (submission), 993 (IMAP), and 443 (HTTPS) in Lightsail networking and the host firewall. Limit SSH 22 where practical; GitHub-hosted runner SSH access must be allowed by your networking policy. Keep admin HTTP 8080 and any alternative HTTPS port private. Docker-published ports can bypass some host firewall rules, so use the Lightsail firewall too.
5. On the instance:

   ```sh
   git clone https://github.com/gpardon-alo/stalwart.git ~/stalwart
   cd ~/stalwart
   cp .env.production.example .env.production
   chmod 600 .env.production
   docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
   ```

The deployment checks out an exact commit in detached HEAD. Do not edit tracked files in this server clone. Keep host-specific values in the ignored `.env.production`. Production and laptop volumes have different names and are independent.

## GitHub configuration

In **gpardon-alo/stalwart → Settings → Secrets and variables → Actions**, add:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `LIGHTSAIL_HOST` | Static IP or SSH hostname |
| Secret | `LIGHTSAIL_SSH_KEY` | Private SSH key authorized for the deployment user |
| Secret | `LIGHTSAIL_FINGERPRINT` | Trusted SSH host-key SHA256 fingerprint |
| Variable, optional | `LIGHTSAIL_USER` | Defaults to `ubuntu` |
| Variable, optional | `LIGHTSAIL_RUNNER` | Defaults to `ubuntu-latest`; ARM: `ubuntu-24.04-arm` |

Obtain the host fingerprint from a trusted Lightsail console session, for example `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`; copy the `SHA256:...` field. Do not blindly accept an unverified network scan.

Enable Actions for your fork if GitHub has disabled it by default. The build job needs the configured `packages: write` permission; the deploy job uses `packages: read`. If the GHCR package already exists, ensure this repository has Actions access to it. The workflow uses its short-lived `GITHUB_TOKEN` to pull private or public images, stores registry credentials in a temporary directory on the host, and removes that directory on exit. No registry PAT is required. Manual future pulls of a private package need separate authentication.

Missing SSH secrets fail preflight before the Rust build starts. Dispatch **Deploy personal Stalwart** on main after completing server setup. Keep deployment secrets only in your fork.

## First setup and both domains

Use `STALWART_PUBLIC_URL=https://mail.taisventures.com` in `.env.production`. In Route 53, point `mail.taisventures.com` to the static IP. Do not change either domain's existing MX records until inbound and outbound delivery have been tested. Avoid AAAA records unless IPv6 mail and firewall rules work too.

After the first successful deployment, open a tunnel from your workstation:

```sh
ssh -L 8081:127.0.0.1:8080 ubuntu@LIGHTSAIL_IP
```

Open `http://127.0.0.1:8081/admin`. Read the bootstrap login **on the host**, not in Actions logs:

```sh
cd ~/stalwart
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=100 mail
```

Configure hostname `mail.taisventures.com`, default domain `taisventures.com`, local RocksDB stores, internal accounts, and console logging. Save the permanent administrator credentials from the final setup screen. Then restart:

```sh
docker compose --env-file .env.production -f docker-compose.production.yml restart mail
```

Add `boardly.com` in the domain manager. Both domains can use `mail.taisventures.com` as their MX target and client server hostname. Create mailboxes plus `postmaster` and `abuse` aliases as appropriate. Configure Route 53 integration with narrowly scoped credentials if you want automatic DNS and DNS-01 certificates. Preserve existing website records, SES verification records, and other authorized mail senders. Scope DNS automation carefully before enabling it on an existing zone.

With direct HTTPS 443, ACME TLS-ALPN-01 can work without DNS automation. With a shared HTTP proxy, use Stalwart's Route 53 DNS-01 integration or install/renew trusted certificates separately for SMTP/IMAP. Caddy's web certificate alone does not configure the mail listeners.

## SES outbound relay — us-east-1

Configure Stalwart's outbound routing through its WebUI to relay all external mail through SES:

| Setting | Value |
| --- | --- |
| SMTP endpoint | `email-smtp.us-east-1.amazonaws.com` |
| Port | `587` |
| Encryption | Required STARTTLS, certificate verification enabled |
| Authentication | SES SMTP credentials generated for us-east-1 |
| Sender domains | Verify both `taisventures.com` and `boardly.com` in SES |

SES SMTP credentials are different from AWS API access keys. Store them in Stalwart's persistent settings/your secret manager; never commit them or print them in CI. Confirm SES production access in us-east-1 if sending to unverified recipients is required. Preserve SES DKIM records and configure any custom MAIL FROM records according to SES. Maintain a single SPF record per domain/MAIL FROM domain and merge existing authorized senders; do not replace it with a generic example.

SES submission on 587 avoids relying on outbound port 25, which AWS restricts by default. Inbound mail to Stalwart still requires reachable TCP 25. Before MX cutover, verify inbound mail, SES outbound delivery from both domains, trusted TLS on IMAP/submission, SPF/DKIM/DMARC alignment and unauthenticated external-to-external relay rejection. Container health alone does not prove those work.

## Sharing the prediction market host

Do not start another service on host port 443. In Stalwart's `.env.production`, set:

```dotenv
STALWART_HTTPS_BIND=127.0.0.1
STALWART_HTTPS_PORT=18443
```

The Compose stack creates a Docker network named `stalwart-mail`. Attach the existing Caddy **web service** to that network in the prediction market deployment, while retaining its existing default network:

```yaml
services:
  web:
    networks:
      - default
      - mail
networks:
  mail:
    external: true
    name: stalwart-mail
```

Add this separate site to its existing Caddyfile, retaining the prediction market site:

```caddyfile
mail.taisventures.com {
    reverse_proxy stalwart-mail:8080
}
```

Create the mail stack/network before recreating the Caddy service. Validate the combined Compose configuration and Caddyfile, then reload/recreate that service through the prediction market project. Do not apply these fragments as replacements for its full files. The Stalwart workflow does not modify the prediction market application or proxy.

Keep Stalwart's HTTP listener enabled on its container network for this proxy route; its published host port remains loopback-only. Configure Stalwart to trust forwarded headers only from the intended proxy/network. Use Route 53 DNS-01 certificates in Stalwart for mail protocols, since Caddy occupies public 443. Plain HTTP is only between these containers; clients use HTTPS through Caddy. The HTTPS admin interface shares this site, so apply administrator MFA and access controls.

## Backup, failures and recovery

Every update of an existing container takes an offline snapshot of both local volumes, `.env.production`, the Compose file and the current image reference under ignored `backups/`. Mail briefly stops while copying; the backup script restarts it on completion or failure. A failed image pull happens before mail is stopped. Snapshots are private but unencrypted and can contain mail, passwords and keys. Encrypt and copy them off-host, monitor disk usage, and define retention. This backup assumes all stores are local RocksDB; external stores need coordinated backups.

Run an extra snapshot manually with `bash deploy/lightsail/backup.sh`. Do not run it concurrently with deployment. Never run `docker compose down -v` against this production project.

A failed health check fails the deployment; it does not automatically downgrade the database. Inspect container state and logs privately. The script records the new image in `.env.production` only after health succeeds; if a new container started but failed health, the recorded reference may still be the previous one. Do not blindly run `up` using the old image against migrated data.

Restore rehearsals must use an isolated host with outbound SMTP blocked. Extract a trusted snapshot into a private directory, restore its `.env.production` and Compose file, create a stopped container using the saved `image.txt` reference, then copy `config/.` to `/etc/stalwart/` and `data/.` to `/var/lib/stalwart/` using `docker compose cp`. Restore ownership to UID/GID 2000 with a one-off root `chown` using the same image and volumes before starting. Verify mailbox contents and settings before a planned cutover. Authenticate to GHCR separately if the saved image is private. Rollback after a migration requires the matching pre-upgrade snapshot and image; preserve failed volumes and newer mail before recovery.

## References

- [Stalwart Docker setup](https://stalw.art/docs/install/platform/docker/)
- [Stalwart behind Caddy](https://stalw.art/docs/server/reverse-proxy/caddy/)
- [SES SMTP TLS and ports](https://docs.aws.amazon.com/ses/latest/dg/smtp-connect.html)
- [Lightsail port 25 restrictions](https://repost.aws/knowledge-center/lightsail-port-25-throttle)
