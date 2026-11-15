# Credential monitoring

Nexo scans new messages in guild channels it can access, including webhook messages and embed text. It does not require a list of channel IDs. The Discord Message Content privileged intent must be enabled in the Developer Portal and in `main.py`.

## Detection and response

- Known GitHub and Slack token formats, private key headers, and database connection URLs with a non-placeholder password are treated as high confidence. Nexo removes these messages and posts a generic notice.
- A non-placeholder value beside a credential-related field such as `GITHUB_SECRET` or `DISCORD_BOT_TOKEN` is reviewed. Entropy raises confidence but is not required to flag the field. Nexo leaves the source message in place and adds Delete message and Mark as false positive actions.
- Detection runs locally. Alerts and logs include finding categories and Discord message/user IDs, never the matched credential value.
- User-facing alerts identify human senders with a non-notifying Discord mention and identify webhook messages by webhook name. Internal logs retain stable Discord IDs.
- False-positive decisions dismiss the alert only. They do not create a permanent detector allowlist.

Nexo needs Manage Messages to remove another user's or webhook's message. If deletion is denied, it posts a review alert instead. File attachments and image contents are not scanned.

This monitor is a secondary response layer: a webhook message is delivered to Discord before Nexo can inspect it. Revoke or rotate a credential if it may have been exposed, even when the message is later deleted.
