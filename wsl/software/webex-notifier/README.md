# WebEx Notifier

This app sends direct WebEx messages for job start, periodic status, completion,
and failure notices.

## Configure

Edit:

```bash
LINUX/webex-notifier/config/application.properties
```

Set:

```properties
webex.token.file=~/.config/webex/token
webex.to.email=your.email@example.com
webex.status.seconds=3600
```

The token file should contain only the WebEx access token.

## Install

```bash
cd /path/to/SOFTWARE/LINUX/webex-notifier
./bin/install_webex_notifier.sh
```

Then edit:

```bash
nano ~/.config/webex/token
```

## Test

```bash
send_webex_notification "CineVault WebEx test"
```

## Interactive Bot Listener

The bundle also includes `webex_bot_listener.py` for direct bot commands from the
approved user only.

Run it in the background:

```bash
cd /path/to/SOFTWARE/LINUX/webex-notifier
nohup ./bin/webex_bot_listener.py >> ./logs/webex-bot-listener.log 2>&1 &
```

Useful commands sent to the bot:

```text
status
queue
cine
start prod
stop prod
start lab
stop lab
```

Start/stop actions require the five-digit security confirmation code sent back
by the bot before anything is changed.

## Tips

- If notifications stop, test the token first.
- Personal access tokens expire; bot tokens are better for long-term installs.
- Missing WebEx configuration should not stop transcodes; the worker skips
  notifications if the notifier is unavailable.
