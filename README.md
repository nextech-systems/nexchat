# NexChat

NexChat brings a self-hosted WhatsApp gateway into ERPNext. Staff use the NexChat Center to review conversations and handle messages in Desk. A gateway session must be configured before the app can send or receive messages.

## What is in this branch

- NexChat Center and a conversation queue
- Conversation, message, and webhook event records
- Agent and session configuration
- Per-site gateway credentials, outbound rate limits, and optional SLA reminders

The handover and notification branches have their own changes. Review the branch you intend to install and test it against your Bench version.

## Install on a staging site

```bash
bench get-app https://github.com/nextech-systems/nexchat.git
bench --site staging.example.com install-app nexchat
bench --site staging.example.com migrate
bench build --app nexchat
```

Open **NexChat Settings** in Desk. Enter your gateway URL and session ID, then configure the gateway API key and webhook secret. Choose conservative rate limits for the initial run. Store secrets through the site's settings and keep them out of repository files, examples, and logs.

## Verify the integration

Use a dedicated test gateway session and test contact. Check that inbound messages reach the correct conversation, authorized agents can open the queue and send a reply, and users outside the intended roles cannot read conversations. Check retry handling and optional reminder behavior on the same staging site before connecting a live session.

## License

The project declares MIT in its [license file](license.txt). This branch currently contains the identifier rather than the full license notice.
