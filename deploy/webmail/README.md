# Webmail authentication badges

The reader in the screenshots is Bulwark Webmail. Its interface is distributed
separately from the Stalwart server; changing server Rust code cannot reposition
these badges.

`collapsed-authentication.patch` customizes Bulwark's
`components/email/email-viewer.tsx`. When message details are collapsed, the
Authentication & security section appears to the right of sender information on
desktop. It wraps when space is limited; phones and tablets show it in a right
aligned row below the sender. Expanding details keeps the existing full security
section. Both views use the same badge renderer, results, tooltips, and colors.
Messages without authentication results or a spam score have no collapsed section.

The patch is pinned to upstream commit
`d1f4ccaccff840a43471d40e645453e0dcfcfef4` (Bulwark 1.17.1).

## Build

With Git, Node.js, and npm installed:

```sh
./deploy/webmail/build.sh
```

The script downloads the pinned source, applies the patch, runs existing header
and viewer regression checks plus TypeScript validation, and builds a Stalwart
Lite bundle at `deploy/webmail/dist/bulwark-lite-stalwart.zip`. Upstream's Lite
builder runs in a disposable checkout because it removes server-only files.
Build-time Lite configuration can be supplied through environment variables.
This script does not upload the bundle or change a running server.

## Automatic deployment

**Deploy personal webmail** (`.github/workflows/deploy-webmail.yml`) runs on
main pushes touching `deploy/webmail/**` or its own workflow. It builds the
patched ZIP, transfers it and its installer to a unique temporary directory on
Lightsail, and activates the bundle in the running mail container. It does not
build a server image, restart the mail container, or change the server's Git
checkout. Manual runs are also available on main. Pull requests run validation
without deployment. Build artifacts are retained for seven days.

**Deploy personal Stalwart** remains responsible for server source, Docker,
Compose, and Lightsail server deployment files. It no longer builds or installs
webmail. A push changing both areas triggers both workflows. Each workflow
serializes its own runs; both deployment scripts use the same host lock, so
server replacement and webmail activation cannot run concurrently. Builds may
run concurrently.

Set this GitHub repository secret once:

- `STALWART_ADMIN_AUTHORIZATION`: the complete `Basic ...` or `Bearer ...`
  authorization header for an administrator. The credential needs session access,
  `sysApplicationQuery`, `sysApplicationGet`, `sysApplicationUpdate`, and
  `actionUpdateApps`. Use a credential suitable for unattended deployments rather
  than a short-lived browser session token.

Optional GitHub repository variables:

- `STALWART_ADMIN_URL`: the API origin as reached **on the Lightsail host**;
  default `http://127.0.0.1:8080`. Change this for a custom admin port. Remote
  origins require HTTPS; loopback HTTP is permitted.
- `STALWART_WEBMAIL_PREFIX`: the existing webmail Application's URL prefix;
  default `/webmail`.

Before activating webmail, the installer checks that the admin API is
reachable and exactly one enabled Application matches the configured prefix.
It preserves that Application's prefixes and OAuth settings and changes only
`resourceUrl`. It writes the uploaded bundle atomically to the persistent data volume at
`/var/lib/stalwart/custom-webmail/COMMIT.zip`, runs `UpdateApps`, and verifies the
entry page contains the exact deployment revision. If activation or verification
fails, it restores the previous resource URL and reloads applications. A failure
still marks the workflow failed; it does not roll back the mail server image or
its database. Old revision bundles remain available for webmail rollback.

The installer requires an existing webmail Application. Initial server and
webmail setup must be completed before enabling this deployment workflow.

For a manual build, set `WEBMAIL_DEPLOY_REVISION` to the full commit SHA when
running `build.sh`. Transfer `install.py`, `deploy.sh`, and the ZIP together to a
temporary directory on Lightsail, then run:

```sh
bash /path/to/upload/deploy.sh /path/to/upload/bulwark-lite-stalwart.zip FULL_COMMIT_SHA
```

Set the admin configuration above in the host environment. The deployment root
defaults to `$HOME/stalwart`; override with `STALWART_DEPLOY_ROOT` for a different
server checkout. The script identifies the running Compose service by project
`stalwart` and service `mail`, and waits up to 15 minutes for the server's
deployment lock. It leaves both the running image and its Git checkout intact.

## Badge colors

Bulwark's `lib/email-headers.ts`, `getSecurityStatus`, defines:

| Result | Color |
| --- | --- |
| `pass` | Green |
| `fail`, `permerror` | Red |
| `softfail`, `neutral`, `temperror` | Amber |
| `none`, missing or unrecognized result | Gray |

`none` follows the gray fallback because it is not an explicit authentication
failure. The patch preserves this behavior. The separate spam-score badge is red
above 5, amber above 2 through 5, and green at 2 or below.

## Validation

- Patch applies cleanly to the pinned source revision.
- TypeScript validation and 67 upstream regression tests passed. The customized
  webmail production build also succeeded in GitHub Actions for commit
  `22cad8d31535279d29218e89eab310d9a51ed0f9`.
- Thirteen deployment regression tests cover uploaded and image-bundled ZIPs, corrupt
  uploads, revision mismatches, rejected changes, and webmail rollback.
- Workflow YAML and shell syntax are checked locally. Server Compose validation
  is retained in its own workflow.
- Browser layout has not been visually verified against a running instance.

The upload uses OpenSSH with the server's pinned Ed25519 key. The scanned key
must match `LIGHTSAIL_FINGERPRINT` before any SSH login or file transfer. Admin
configuration travels over encrypted SSH stdin, is passed to the installer in
its process environment, and is never written to a credential file on Lightsail.
