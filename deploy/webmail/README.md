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
`d1f4ccaccff840a43471d40e645453e0dcfcfef4` (Bulwark 1.17.1). The personal deployment workflow builds
and installs this pinned version with the customization. These changes are local until committed and pushed to main.

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

The personal deployment workflow builds this bundle before building the mail
server image. The Dockerfile includes the bundle, and the Lightsail deployment
activates it only after the new mail container passes its health check. A failed
webmail build prevents the image from being deployed. No public release or
separate bundle hosting is needed.

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

Before replacing the server, the deployment checks that the admin API is
reachable and exactly one enabled Application matches the configured prefix.
It preserves that Application's prefixes and OAuth settings and changes only
`resourceUrl`. It copies the image's bundle to the persistent data volume at
`/var/lib/stalwart/custom-webmail/COMMIT.zip`, runs `UpdateApps`, and verifies the
entry page contains the exact deployment revision. If activation or verification
fails, it restores the previous resource URL and reloads applications. A failure
still marks the workflow failed; it does not roll back the mail server image or
its database. Old revision bundles remain available for webmail rollback.

The installer requires an existing webmail Application. Initial server and
webmail setup must be completed before enabling this deployment workflow.

For a manual build to use with the production Dockerfile, set
`WEBMAIL_DEPLOY_REVISION` to the full commit SHA when running `build.sh`. Manual
runs of `deploy/lightsail/deploy.sh` also require `DEPLOY_SHA` and the admin
configuration above in the environment. The deployment script handles the server
and webmail activation under the same lock.

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

## Local validation

- Patch applies cleanly to the pinned source revision.
- TypeScript validation passed.
- Existing email-header and viewer/plugin regression tests passed: 67 tests.
- Production bundle generation was attempted but could not finish in this
  execution environment: Turbopack's CSS processing worker could not bind a
  local port (`Operation not permitted`). No bundle was produced or deployed.
- Eight deployment regression tests passed, including rejected changes, bundle
  mismatches, verification failure, and restoration of the previous resource URL.
- Workflow YAML parses, shell syntax checks pass, and Compose configuration
  validates. A Docker packaging check confirms the archive is included despite
  the restrictive build context. A live GitHub Actions run has not been performed.
- Browser layout has not been visually verified against a running instance.
