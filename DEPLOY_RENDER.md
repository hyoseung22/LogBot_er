# Render deployment

This is the simplest production setup for this Slack bot:

`Slack -> Render web service -> OpenAI API -> Slack reply`

Use `direct` mode. Do not deploy the separate analysis server.

## What this repo already supports

- `python slack_bot.py` starts the Slack events server
- `/slack/events` receives Slack event callbacks
- `/health` returns a basic health response
- The app now honors the generic `PORT` environment variable used by hosted web services

## Required environment variables

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `OPENAI_API_KEY`
- `SLACK_ANALYSIS_MODE=direct`
- `SLACK_BOT_HOST=0.0.0.0`

Optional:

- `OPENAI_MODEL=gpt-5-mini`
- `SLACK_ALLOWED_CHANNELS=C01234567,C07654321`

## Render steps

1. Push this folder to a GitHub repository.
2. In Render, create a new Blueprint or Web Service from that repository.
3. If you use Blueprint, Render will read `render.yaml` from the repo root.
4. If prompted for secret values, set:
   - `SLACK_BOT_TOKEN`
   - `SLACK_SIGNING_SECRET`
   - `OPENAI_API_KEY`
5. Deploy the service.
6. After Render assigns a public hostname, set the Slack Request URL to:

`https://<your-render-hostname>/slack/events`

7. In the Slack app settings, enable `app_mention` under Event Subscriptions.
8. Reinstall the Slack app to the workspace after changing scopes or events.
9. Invite the bot to the target Slack channel.
10. Upload a `.log` or `.txt` file and mention the bot in the same message or thread.

## Recommended Slack scopes

- `app_mentions.read`
- `chat:write`
- `files:read`
- `channels:read`
- `channels:history`

If you want private channels:

- `groups:read`
- `groups:history`

If you want DMs:

- `im:read`
- `im:history`

## Notes

- Hosted free tiers may sleep when idle. For production Slack event handling, use an always-on plan.
- Slack must reach your bot over public HTTPS unless you switch to Socket Mode.
