---
name: radioagent-setup
description: "Set up Radio Agent connection for this project. Asks the user for their Radio Agent host and port, then configures initech.yaml with the announce_url. TRIGGER when: user says 'set up radio agent', 'connect to radio agent', 'configure radio', 'radio setup', or when the dj skill fails because announce_url is not configured."
---

# Radio Agent Setup

One-time setup to connect this project to a Radio Agent instance. After setup, agents can use `initech announce` to send voice announcements to the radio.

## What this does

1. Ask the user for their Radio Agent host (IP or hostname) and port (default 8001)
2. Verify the instance is reachable
3. Configure `announce_url` in initech.yaml via `initech config`
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

### Step 3: Configure initech.yaml

```bash
initech config set announce_url "http://<host>:<port>/announce"
```

If `initech config set` is not available (older version), tell the user to add this line to their initech.yaml manually:

```yaml
announce_url: http://<host>:<port>/announce
```

### Step 4: Send test announcement

```bash
initech announce "Radio Agent connected. You should hear this on the stream."
```

If successful: tell the user "You're connected. Any agent in this project can now use initech announce to send voice announcements."

If it fails with "announce_url not configured": the config didn't save. Fall back to manual instructions.

### Step 5: Suggest the DJ skill

After setup, tell the user:

"Want creative announcements instead of robotic ones? Install the DJ skill: cp -r skills/dj ~/.claude/skills/dj"

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "announce_url not configured" | initech config set announce_url "http://host:port/announce" |
| Connection refused | Radio Agent brain is not running. Start it with: cd /opt/radioagent && venv/bin/python brain.py |
| Timeout | Wrong IP or host is behind a firewall |
| "initech config set" not found | Add announce_url manually to initech.yaml |
| Announcements work but no audio | Check Liquidsoap and Icecast are running. Open http://host:8000/stream |
