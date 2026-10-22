# Temporary parallel receiving: SES/S3 to Stalwart

WorkMail stays primary. This optional worker imports copies directly over JMAP into Stalwart inboxes; it never sends SMTP mail, changes MX records, deletes S3 messages, or modifies WorkMail. The worker is not included in the main deployment workflow. AWS resources are created only when you explicitly deploy the CloudFormation template.

Configure your source bucket, actual bucket region, and permitted prefixes in the ignored `mailboxes.json`. Public examples use placeholders only. SES/SNS/SQS use `us-east-1` in this example; substitute your actual region if different. The S3 bucket may be in another region.

## Preserve recipients correctly

S3 raw mail alone does not reliably identify BCC recipients or alias expansion. Do not guess from To/Cc. Use the **optional SNS notification on the existing SES S3 receipt action**, which includes `receipt.recipients`, `receipt.action.bucketName` and `receipt.action.objectKey`.

The optional `aws-resources.yaml` CloudFormation template creates the topic, queues, restricted resource policies and a dedicated importer IAM user in the account where you deploy it, region `us-east-1`. Supply exact SES receipt-rule ARNs or the per-set patterns below, comma-separated, as `ReceiptRuleArns`. It does not edit receipt rules or DNS. Review and deploy it separately, then use its outputs in `mailboxes.json`.

### IAM permissions and stack parameters

The stack creates a generated-name IAM user with an inline policy; it has no
console password. Supply `SourceBucketName` (name only), `MailPrefixOne` and
`MailPrefixTwo` (both including their trailing slash). These values must match
`mailboxes.json`. The policy allows only reading objects under those prefixes
and consuming/inspecting this stack's SQS queue. No S3 write, SES send, WorkMail,
Route 53 or administrator permissions are granted.

For ordinary S3 SSE-KMS, optionally set `SourceKmsKeyArn` to the exact key ARN;
the stack adds `kms:Decrypt` on that key only. The key policy must also allow the
user. Leave it empty for SSE-S3. SES client-side message encryption remains
unsupported.

On the CloudFormation review screen, acknowledge creation of IAM resources
(`CAPABILITY_IAM` when using the CLI). After creation/update, find
`ImporterUserName` in Outputs. Open that user in IAM, create an access key, and
save the pair directly in the ignored Lightsail `aws-credentials` file. Keys are
not generated or exposed in stack outputs. If updating a stack created with the
older template, upload the new template and provide the new bucket/prefix
parameters. Existing resources and receipt rules remain in place.

### SES reports "Could not publish to SNS topic"

Use a CloudFormation stack update to correct permissions. Do not edit the topic
or IAM policies manually. Keep `ReceiptRuleArns` set to the intended exact rules
or named-set wildcard patterns; the stack uses `ArnLike` to match them.

An SES **Deliver to S3** action may have an IAM role selected. In that mode AWS
uses **only that role**, including for the SNS publish operation. Granting
`ses.amazonaws.com` access in the topic policy alone does not grant the role its
missing identity permission. Inspect the action to read its role name, then set
`ExistingS3ActionRoleNames` in the stack update to that name. If the two actions
use different roles, provide both names separated by a comma. If they share one
role, provide it once. This is an existing SES delivery role, **not** the mirror
importer's IAM user.

The stack attaches a separate inline policy granting only `sns:Publish` on this
topic to those roles. It leaves their S3/KMS policies, trust relationships,
WorkMail actions and receipt rules intact. Leave the parameter empty when SES
has no role selected. Do not remove a working S3 role to bypass this error.

This handles the role-based publish path; it does not prove that is the cause
of every publish error. If the error persists, verify the deployed stack's
parameters, region and status, and check topic encryption through read-only
inspection before making another CloudFormation change. The template does not
configure SNS KMS encryption. Existing explicit denies or incompatible KMS key
policies can still prevent publishing.

Reference: [AWS SES S3-action role behavior](https://docs.aws.amazon.com/ses/latest/APIReference/API_S3Action.html).

### Authorizing two receipt rule sets

`ReceiptRuleArns` accepts exact rule ARNs or a `*` in the rule-name position to
authorize every rule in an explicitly named set. For two sets, enter both patterns
as one comma-separated value (replace the example account and set names):

```text
arn:aws:ses:us-east-1:123456789012:receipt-rule-set/SET_ONE:receipt-rule/*,arn:aws:ses:us-east-1:123456789012:receipt-rule-set/SET_TWO:receipt-rule/*
```

Existing exact-rule inputs remain supported and give narrower access. The topic
policy also restricts publishing to the CloudFormation stack's AWS account. The
queue and topic are shared by the authorized rules; no second stack is needed.
Only one receipt rule set can be active per AWS account and region, so inactive
sets do not produce incoming-mail notifications. This template does not activate
or switch sets. If both domains receive in the same account/region, verify whether
they are actually two rules within the same active set. Select the topic in each
relevant S3 action when ready, retaining its existing WorkMail action.

### If resources were created manually before using this template

Prefer CloudFormation for creating and updating all mirror resources and policies. The following describes the required configuration for an existing installation; do not make ad hoc policy edits to CloudFormation-managed resources.

The setup requires an SNS topic `stalwart-mirror` and a standard SQS queue `stalwart-mirror` in us-east-1 with a dead-letter queue (maxReceiveCount 5) and sufficient retention (for example 14 days). Subscribe the queue to the topic with **raw message delivery OFF**. The queue policy should allow `sqs:SendMessage` only from that SNS topic, and the topic policy should allow SES publishing only from your account's intended receipt rule. Use a dedicated queue, not one shared with another consumer.

In the existing SES **Deliver to S3 bucket** action, select that topic. Retain the WorkMail action and all existing recipient conditions. Check the AWS policies and send one test first: receipt-action errors can affect mail handling. This adds metadata notifications for future mail; it does not replay the S3 archive or require another SMTP receiving action. Do not use S3 ObjectCreated notifications for this worker, since those omit SES envelope recipients.

## Configure the worker on Lightsail

1. Pull this repository's reviewed revision. Copy `mailboxes.example.json` to `mailboxes.json` in this directory. Verify bucket region, set the actual topic ARN and queue URL, and map **every desired recipient/alias** to one or more mailbox identifiers. Several aliases may point to one mailbox; each original object is imported once per target. Use lowercase recipient mapping keys.
2. Configure each mailbox's Stalwart username and an app password in `mailboxes.json`. The password is for that Stalwart account, not WorkMail or SES. Accounts need JMAP access and permission to import messages into their own Inbox. The endpoint must have a valid HTTPS certificate (the existing Caddy web certificate suffices for JMAP; this does not use SMTP/IMAP).
3. Copy `aws-credentials.example` to `aws-credentials`. Use the stack output `ImporterUserName` to find the already-provisioned IAM user and create its access key in IAM. If you created resources manually instead, replace placeholders in `worker-policy.example.json` and provision the policy/user yourself. Its permissions are limited to `s3:GetObject` on these two prefixes and `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on this queue. The worker needs no SES sending, Route 53, S3 write, or WorkMail permissions. For ordinary S3 SSE-KMS, add the specific key's decrypt permission when required. **SES receipt-action client-side KMS encryption is not supported**; verify objects are readable RFC822 mail, not encrypted ciphertext. Do not change existing bucket encryption just for the importer.
4. Make both files readable only by the container's UID 2001:

   ```sh
   cd ~/stalwart/deploy/mirror
   sudo chown 2001:2001 mailboxes.json aws-credentials
   sudo chmod 600 mailboxes.json aws-credentials
   docker compose build
   docker compose up -d
   docker compose logs --tail=50 importer
   ```

The default is **dry run**: it reads queue depth without receiving messages or spending their dead-letter retry budget. It does not validate JMAP login or mailbox routing. After checking credentials/mappings and observing a test notification, enable imports explicitly:

```sh
MIRROR_DRY_RUN=false docker compose up -d
```

Repeat the environment assignment on future `up` commands, or put `MIRROR_DRY_RUN=false` in this directory's ignored `.env`. Never commit `mailboxes.json` or `aws-credentials`; they contain secrets. There are no published ports. State is in the `stalwart-mirror_state` Docker volume; retain and back it up for duplicate protection. The production mail backup script does not cover this separate state volume.

## Test and monitor

Send a new message from an external account to one mapped address. Confirm it arrives in WorkMail and Stalwart. Repeat for an alias, a BCC recipient, both domains, and a message with attachments. Resubmit a test notification to verify it creates no second Stalwart copy. The original MIME content and attachments are uploaded unchanged; a deterministic JMAP keyword tracks each import. Imported copies appear unread and their Stalwart receipt time is the import time.

Check importer logs, SQS queue age, and the dead-letter queue. No alarms are installed automatically. Unknown recipients, failed virus scans, oversized mail, authentication failures, or failed imports remain unacknowledged and eventually reach the DLQ. Logs omit mail contents and credentials. Held deliveries include an allowlisted `reason=` code: `unmapped_recipient` means a missing mailbox/alias mapping; `bucket_not_allowed` or `prefix_not_allowed` means the source differs from the allowlist; `virus_scan_not_passed` means the receipt did not have a passing virus result; `not_ses_receipt_notification`, `not_ses_s3_notification`, or `invalid_notification_json` means the queued notification is not in the expected SES S3 receipt format. Do not assume every rejected notification represents an actual email. Unrecognized exception text is never logged. A partial multi-mailbox delivery resumes without repeating completed targets. Alias matching is explicit; there is no catch-all or automatic plus-address expansion.

Successful imports are stored in SQLite and tagged with a deterministic `mirror-...` JMAP keyword. If a response is lost after the import, a retry checks that keyword. If an earlier import remains uncertain and no matching message is visible, the worker holds it for manual review instead of blindly creating a duplicate. Inspect SQLite's `deliveries` table and the corresponding JMAP keyword before resolving/replaying such a notification. Do not delete state or remove keywords during the trial. Keep one worker/state volume; no active-active operation is supported.

The polling visibility timeout is 15 minutes. For a very large alias fanout or slow server, extend visibility handling before production use. Notifications already in a queue are processed when imports are enabled; older S3 objects are not scanned. Existing-archive migration requires a separate, explicit recipient manifest or a WorkMail mailbox export/IMAP migration so that BCC and aliases are preserved.

To stop the parallel receiving trial: `docker compose stop importer`. WorkMail remains unchanged. Remove the optional SNS notification from the SES S3 action after the trial if no longer needed. Keep the existing WorkMail and S3 actions.

## Local verification

```sh
cd deploy/mirror
python3 -m unittest -v test_importer.py
```

References: [SES S3 notifications](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-notifications-examples.html), [Stalwart JMAP](https://stalw.art/docs/http/jmap/).
