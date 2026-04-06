---
name: radioagent-setup
description: "Set up Radio Agent connection for this project. Asks the user for their Radio Agent host and port, then saves the announce URL for webhook calls. TRIGGER when: user says 'set up radio agent', 'connect to radio agent', 'configure radio', 'radio setup', or when the dj skill fails because the announce URL is not configured."
---

# Radio Agent Setup

One-time setup to connect this project to a Radio Agent instance. After setup, agents can POST to the webhook to send voice announcements to the radio.

## What this does

1. Ask the user for their Radio Agent host (IP or hostname) and port (default 8001)
2. Verify the instance is reachable
3. Save the announce URL to `.radioagent.yaml` in the project root
4. Send a test announcement to confirm it works

## Process

### Step 1: Ask for connection details

Ask the user:

"What's the host and port of your Radio Agent instance? For example: 192.168.1.100:8001 or localhost:8001"

If they give just an IP, assume port 8001.
If they give host:port, use both.
If they say "localhost" or "local", use localhost:8001.

### Step 2: Verify the instance is reachable

```bash
curl -sf -o /dev/null -w '%{http_code}' http://<host>:<port>/now-playing
```

If 200: proceed.
If connection refused: tell the user "Can't reach Radio Agent at that address. Is the brain running?"
If timeout: tell the user "Connection timed out. Check the host and port."

### Step 3: Save the announce URL

Write a `.radioagent.yaml` file in the project root:

```yaml
# Radio Agent connection config
announce_url: http://<host>:<port>/announce
```

This file is read by the DJ skill to know where to send announcements.

### Step 4: Send test announcement

```bash
curl -s -X POST http://<host>:<port>/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail": "Radio Agent connected. You should hear this on the stream."}'
```

If successful: tell the user "You're connected. Agents can now POST announcements to the webhook."

If it fails: check that the brain is running and the host/port are correct.

### Step 5: Suggest the DJ skill

After setup, tell the user:

"Want creative announcements instead of robotic ones? Install the DJ skill: cp -r skills/dj ~/.claude/skills/dj"

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Announce URL not configured | Create `.radioagent.yaml` with `announce_url: http://host:port/announce` |
| Connection refused | Radio Agent brain is not running. Start it with: cd /opt/radioagent && venv/bin/python brain.py |
| Timeout | Wrong IP or host is behind a firewall |
| Announcements work but no audio | Check Liquidsoap and Icecast are running. Open http://host:8000/stream |
