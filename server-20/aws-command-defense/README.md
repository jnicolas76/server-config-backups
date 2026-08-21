# AWS Command Defense

AWS Command Defense is a self-contained browser training game covering AWS
Cloud Practitioner and AWS AI Practitioner material.

## Online version

Open `http://192.168.1.20:8131/` from a device on the local network.

## Offline version

Download `AWS-Command-Defense-Offline.html` and open it in any modern browser.
The complete question and glossary banks, styles, game engine, and graphics are
embedded in that single file. No server, installation, or internet connection
is required.

## Content and modes

- Cloud Practitioner: 117 glossary terms and 100 base questions
- AI Practitioner: 100 glossary terms and 100 base questions
- Mixed Command combines both banks
- Glossary Defense, Exam Defense, and Adaptive Campaign modes
- Beginner 20 seconds, Intermediate 15 seconds, Expert 10 seconds, Insane 5 seconds

## Server service

The systemd service is named `aws-command-defense.service` and serves the game
from `/home/jnicolas/aws-command-defense` on port `8131`. It is enabled at boot.

## Rebuild

Keep `build_aws_defense_game.py`, `aws-ultimate-clf-c02.html`, and
`aws-ultimate-aif-c01.html` together, then run the builder. It creates
`aws-command-defense.html`, which is also the portable offline edition.
