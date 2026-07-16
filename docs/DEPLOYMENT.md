# Deployment Guide

This guide takes the Kirana Operations Agent from a local checkout to a public GitHub repository and a continuously running Render worker. Commands below use PowerShell unless stated otherwise.

> **Never put a real OpenAI API key or Telegram bot token in this file, a screenshot, a chat message, a Git commit, or a public repository.** Keep secrets only in the local `.env` file and the deployment provider's protected environment-variable UI.

## 1. If a key or token was exposed, rotate it first

Treat any credential pasted into a chat, terminal recording, screenshot, issue, log, or Git commit as compromised. Deleting the message or commit is not enough because copies may still exist.

1. Stop local and hosted copies of the bot so they cannot keep using the old credentials.
2. In the [OpenAI API key settings](https://platform.openai.com/api-keys), delete the exposed key immediately.
3. Review recent API usage and billing for activity you do not recognize. Configure project budgets or usage alerts. If suspicious usage occurred, contact OpenAI support.
4. Create a new OpenAI project key. Do not reuse or share the old one.
5. Open the official `@BotFather` chat in Telegram. Use `/mybots`, select the bot, open its API-token controls, revoke the exposed token, and generate a replacement. Menu labels can vary slightly.
6. Replace the credentials in the local `.env` file and in Render's environment-variable UI.
7. Restart or redeploy the bot, then complete the smoke test in [Section 9](#9-smoke-test-the-deployed-bot).

Revoking the old credentials is mandatory even if they have already been removed from Git history. A Telegram token grants control of the bot, and an API key can generate billable API usage.

If a credential was committed:

1. Rotate it before doing anything else.
2. Remove the secret-bearing file or text from the current commit.
3. Follow GitHub's [sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) if it exists in older commits.
4. Never bypass GitHub push protection to publish a detected secret.

## 2. Prerequisites

Install or create the following:

- Git
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- An OpenAI API project and a newly generated API key
- A Telegram bot created through the official `@BotFather`
- Docker Desktop, only if testing the container locally
- A GitHub account and an empty **public** repository for this project
- A Render account, for the hosted worker

Run all project commands from the repository root:

```powershell
Set-Location "C:\path\to\Dhruvi AI Project"
```

Replace that example path with the actual checkout location.

## 3. Configure the local `.env` safely

Install the project and create a local environment file from the safe template:

```powershell
uv sync --extra dev
Copy-Item .env.example .env
notepad .env
```

Fill in the local `.env` with your own values. The following are placeholders, not working credentials:

```dotenv
OPENAI_API_KEY=<new-openai-api-key>
TELEGRAM_BOT_TOKEN=<new-telegram-bot-token>
OPENAI_MODEL=gpt-5.6-terra
ALLOW_ALL_TELEGRAM_USERS=false
AUTHORIZED_TELEGRAM_USER_IDS=<your-numeric-telegram-user-id>
DATABASE_PATH=./data/kirana.sqlite3
AGENT_SESSION_DATABASE_PATH=./data/agent_sessions.sqlite3
ARTIFACT_OUTPUT_DIR=./output
STORE_TIMEZONE=Asia/Kolkata
LOG_LEVEL=INFO
```

`ALLOW_ALL_TELEGRAM_USERS=false` is the safe default. In this mode, `AUTHORIZED_TELEGRAM_USER_IDS` is required and accepts one or more numeric IDs separated by commas, for example `<owner-id>,<manager-id>`. Do not enter Telegram usernames such as `@shopowner`.

Confirm that Git ignores the file:

```powershell
git check-ignore -v .env
git ls-files .env
```

The first command should report the `.gitignore` rule. The second command should print nothing. Before every commit, also inspect:

```powershell
git status --short
git diff --cached
```

If `.env` was tracked previously, ignoring it is not enough. Keep the local file but stop tracking it:

```powershell
git rm --cached .env
```

Then rotate any credentials it contained and commit the removal. Keep `.env.example` committed with blank or clearly fake placeholder values only.

## 4. Find the Telegram numeric user ID for locked mode

The easiest method uses the Kirana bot itself, so the ID is not shared with an unrelated
third-party bot.

1. Run exactly one copy of the bot.
2. Ask the new user to open a private chat with the bot and send `/start`.
3. Because the user is not authorized yet, the bot replies with their numeric Telegram user ID.
4. Copy that number into `AUTHORIZED_TELEGRAM_USER_IDS`, keeping any existing owner IDs and
   separating multiple IDs with commas.
5. Restart or redeploy the bot. Environment changes are not hot-reloaded.

For first-time setup, or if the bot cannot be started yet, use Telegram's Bot API directly:

1. Stop every running copy of this bot, including local terminals and hosted workers. Only one long-polling process should use a bot token at a time.
2. Open your bot in Telegram and send it `/start`, followed by a short message such as `hello`. A bot cannot begin a private conversation until the user messages it.
3. From the repository root, run the following PowerShell commands. They read the token from `.env` without printing it:

```powershell
$tokenLine = Get-Content .env | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' }
$token = $tokenLine.Substring('TELEGRAM_BOT_TOKEN='.Length).Trim()
$updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getUpdates"
$updates.result | ForEach-Object {
    if ($_.message.from) {
        $_.message.from | Select-Object id, username, first_name
    }
}
Remove-Variable token, tokenLine, updates
```

4. Copy only the number in the `id` column into `AUTHORIZED_TELEGRAM_USER_IDS`.

Telegram IDs are numeric and can be larger than a 32-bit integer. Keep the full number exactly as returned.

If no update appears:

- Send the bot another message and run the commands again.
- Confirm that the bot token came from the correct bot.
- Stop any local or deployed bot that may already have consumed the update.
- Make sure the bot is using long polling rather than an old webhook configuration.

Do not paste the request URL or its error output into a public issue because a Bot API URL can contain the token.

### Temporarily open access for a supervised demo

Keep the normal owner ID saved in `AUTHORIZED_TELEGRAM_USER_IDS`, then:

1. Set `ALLOW_ALL_TELEGRAM_USERS=true`.
2. Restart or redeploy the bot; environment changes are not hot-reloaded.
3. Let testers use the bot only during the supervised window, preferably in private chats.
4. Watch OpenAI usage because public mode has no per-user rate limit.
5. Set `ALLOW_ALL_TELEGRAM_USERS=false` and restart immediately afterward. The saved allowlist becomes active again.

Public mode grants full access to shared inventory, bills, customers, Khata, reports, generated files, and API spend. It is not read-only and has no automatic expiry. Avoid enabling it after the bot has been offline with unreviewed messages waiting in Telegram, because polling retains pending updates.

## 5. Run and verify locally

Install dependencies and run the quality checks:

```powershell
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Seed the bundled product catalog, then start the Telegram bot:

```powershell
uv run kirana-seed
uv run kirana-bot
```

The normal bot startup also runs the seed operation safely, so an explicit `kirana-seed` is useful but not required on every launch. Leave that terminal open while testing. Press `Ctrl+C` to stop it.

Only run one copy of the bot at a time. If a hosted worker is already active, stop it before starting locally; otherwise Telegram can report a `409 Conflict` because two processes are requesting updates.

For a no-Telegram demonstration of the deterministic workflows, run:

```powershell
uv run kirana-demo
```

### Local smoke test

With `ALLOW_ALL_TELEGRAM_USERS=false`, from the authorized Telegram account:

1. Send `/start` and confirm the bot responds.
2. Ask `How much sugar is left?` and confirm a stock answer is returned.
3. Ask `What is running low?` and confirm the seeded low-stock examples appear.
4. Start a small bill draft, review the totals, and cancel it unless a finalized test sale is desired.
5. Send a message from an account not listed in `AUTHORIZED_TELEGRAM_USER_IDS` and confirm access is refused.

To test the optional public window, change the toggle to `true`, restart, and confirm the unlisted account can use the bot. Then return the toggle to `false`, restart again, and confirm that account is refused. Never leave public mode enabled after the test.

Watch the terminal for errors, but do not share logs until secret values and Bot API URLs have been removed.

## 6. Publish the public GitHub repository over SSH

### 6.1 Verify SSH access

Test the GitHub connection:

```powershell
ssh -T git@github.com
```

On the first connection, compare the displayed host fingerprint with GitHub's published fingerprints before accepting it. A successful test says which GitHub username authenticated. GitHub intentionally returns exit code `1` for this authentication test even when the greeting is successful.

If no SSH key is configured, generate an Ed25519 key with a passphrase:

```powershell
ssh-keygen -t ed25519 -C "YOUR_GITHUB_EMAIL"
```

Then add the **public** key (the file ending in `.pub`) to GitHub under **Settings → SSH and GPG keys**. Never upload or share the private key.

GitHub's detailed instructions are available in [Generating a new SSH key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent) and [Adding an SSH key to GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account).

### 6.2 Inspect the commit before pushing

```powershell
git status --short
git check-ignore -v .env
git ls-files .env
git diff --cached
```

Verify that `.env`, SQLite databases, generated PDFs or PowerPoints, and runtime output are not staged. If a real credential appears anywhere, stop, rotate it, and remove it before continuing.

### 6.3 Connect and push

Create an empty public repository on GitHub without generating a second README, then connect it using its SSH URL:

```powershell
git remote -v
git remote add origin git@github.com:OWNER/REPOSITORY.git
git branch -M main
git push -u origin main
```

If `origin` already exists but points somewhere else, update it instead:

```powershell
git remote set-url origin git@github.com:OWNER/REPOSITORY.git
git remote -v
git push -u origin main
```

Open the public repository in a private browser window and verify that it contains no `.env`, credentials, databases, customer data, or generated operational artifacts.

## 7. Optional: test the Docker image locally

The checked-in `Dockerfile` starts `kirana-bot`. Build it:

```powershell
docker build -t kirana-ops-agent:local .
```

Use named volumes so databases and generated artifacts survive container replacement:

```powershell
docker volume create kirana-data
docker volume create kirana-output
docker run --rm --name kirana-bot --env-file .env `
  -v kirana-data:/app/data `
  -v kirana-output:/app/output `
  kirana-ops-agent:local
```

Press `Ctrl+C` to stop the container. Do not start this container while another copy of the bot is polling Telegram.

## 8. Deploy as a Render background worker

The repository includes `render.yaml`. It defines:

- A Docker background worker named `kirana-ops-agent`
- Automatic deployment from commits
- A 1 GB persistent disk mounted at `/app/persist`
- Persistent database, session, and artifact paths under that mount
- Protected prompts for the three required secrets

Render filesystems are otherwise ephemeral, so only files under the persistent-disk mount survive a replacement or redeploy. Persistent disks require a paid worker plan. Keep the worker at one instance because this project uses SQLite and Telegram long polling.

### 8.1 Create from the Blueprint

1. Push the repository to GitHub.
2. In Render, select **New → Blueprint**.
3. Connect the public GitHub repository.
4. Select the branch containing `render.yaml`.
5. During the initial Blueprint setup, enter the three values marked `sync: false`.
6. Create the service and wait for the first deployment.

Values marked `sync: false` are requested during initial Blueprint creation. If one is added to `render.yaml` later, add its value manually in the service's **Environment** page because existing Blueprint services are not prompted again automatically.

### 8.2 Set environment variables in the Render UI

Open **Render Dashboard → kirana-ops-agent → Environment** and configure:

| Name | Value or purpose | Secret? |
| --- | --- | --- |
| `OPENAI_API_KEY` | Newly generated OpenAI project key | Yes |
| `TELEGRAM_BOT_TOKEN` | Newly generated BotFather token | Yes |
| `ALLOW_ALL_TELEGRAM_USERS` | `false` normally; `true` only for a short supervised public demo | No |
| `AUTHORIZED_TELEGRAM_USER_IDS` | Numeric IDs, comma-separated | Treat as private |
| `OPENAI_MODEL` | `gpt-5.6-terra`, or another model available to the API project | No |
| `DATABASE_PATH` | `/app/persist/kirana.sqlite3` | No |
| `AGENT_SESSION_DATABASE_PATH` | `/app/persist/agent_sessions.sqlite3` | No |
| `ARTIFACT_OUTPUT_DIR` | `/app/persist/output` | No |
| `STORE_TIMEZONE` | `Asia/Kolkata` | No |
| `LOG_LEVEL` | `INFO` | No |

Store secret values only in Render's protected UI. Do not write them into `render.yaml`. Choose **Save and deploy** after changing credentials so the running worker receives the new values.

### 8.3 Verify the disk

In the service settings, confirm:

- Disk name: `kirana-persist`
- Mount path: `/app/persist`
- Size: at least 1 GB
- All three persistent paths begin with `/app/persist/`

The application seeds its database automatically on normal startup. A separate seed job is not required.

## 9. Smoke-test the deployed bot

1. Make sure the local bot and local Docker container are stopped.
2. Open the latest Render deployment and inspect its logs.
3. Confirm there is no missing-environment-variable error and no Telegram polling conflict.
4. Confirm `ALLOW_ALL_TELEGRAM_USERS=false`, then send `/start` from an authorized account.
5. Ask `How much sugar is left?`.
6. Ask `What is running low?`.
7. Create and review a small draft bill. Finalize it only if a test transaction is acceptable.
8. Request a supported report or artifact and confirm the bot returns it.
9. Restart the worker once, then verify inventory and session-backed behavior still use the persistent disk.
10. From an unauthorized Telegram account, confirm the allowlist blocks access. If public mode is tested, return it to `false`, redeploy, and repeat this refusal check.

Do not consider the deployment complete until the Telegram response, OpenAI call, allowlist, database, and persistent storage have all been checked.

## 10. Roll back safely

For a bad application deployment:

1. Open the Render service's deployment history.
2. Select the last known-good deployment and redeploy or roll back to it.
3. Watch the logs and repeat the smoke test.

Important cautions:

- A code rollback does not roll back data already written to the persistent disk.
- Render takes disk snapshots, but do not rely on a rollback to reverse incompatible database changes. Back up important data before a risky release.
- Never restore a revoked API key or bot token when rolling back. Keep the new active credentials in the provider UI.
- Disk size can be increased but not decreased, so choose capacity changes deliberately.
- For local Docker rollback, start a previously tagged image while retaining the named data volumes.

## 11. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Startup says a setting is missing | Check the exact environment-variable names, remove accidental spaces, save them in the provider UI, and redeploy. |
| Everyone is allowed unexpectedly | Set `ALLOW_ALL_TELEGRAM_USERS=false` exactly and restart/redeploy. Confirm an owner ID remains in `AUTHORIZED_TELEGRAM_USER_IDS`. |
| `AUTHORIZED_TELEGRAM_USER_IDS` is rejected | Use numeric IDs only. Separate multiple values with commas; do not include `@` usernames. |
| Telegram returns `409 Conflict` | Two processes are polling the same bot. The process that detects the conflict now exits cleanly. Stop the extra local process, Docker container, or hosted worker before restarting; run only one. |
| The bot is silent | Send the bot a message first, verify the token and numeric user ID, then inspect redacted logs. Also confirm the worker is running. |
| An authorized user is refused | Send `/start` in a private chat and copy the user ID shown by the bot. Make sure that full user ID—not the bot ID, username, or unrelated chat ID—is in the allowlist, then restart. |
| OpenAI returns `401` | The API key is invalid, revoked, or copied incorrectly. Create a new project key, update the environment, and redeploy. |
| The configured model is unavailable | Set `OPENAI_MODEL` to a model accessible to the current API project, save, and redeploy. |
| Database resets after deployment | Confirm the disk exists and every database path starts with `/app/persist/`. Paths elsewhere in the container are ephemeral. |
| Reports disappear after deployment | Confirm `ARTIFACT_OUTPUT_DIR=/app/persist/output` and that the disk is mounted. |
| SQLite reports locking errors | Ensure there is only one worker instance and no second process using the same database. |
| Disk is full | Inspect disk usage and increase the Render disk size if needed. Disk size cannot later be reduced. |
| `Permission denied (publickey)` from GitHub | Check which GitHub account owns the public key, load the correct private key into the SSH agent, and retry `ssh -T git@github.com`. |
| GitHub blocks the push as a secret | Do not bypass the warning. Rotate the credential, remove it from the commit and history, then push again. |

## 12. Final safety checklist

- [ ] Every previously exposed OpenAI key and Telegram token has been revoked.
- [ ] `.env` is ignored and `git ls-files .env` prints nothing.
- [ ] No credential, database, customer data, or generated artifact is in Git history.
- [ ] GitHub SSH authenticates as the intended account.
- [ ] The GitHub repository is public and contains only safe project files.
- [ ] Render secrets are stored in the Environment UI, not `render.yaml`.
- [ ] The persistent disk is mounted at `/app/persist`.
- [ ] Database, session, and artifact paths all use `/app/persist` in production.
- [ ] Exactly one bot worker is running.
- [ ] `ALLOW_ALL_TELEGRAM_USERS=false` after any supervised public demo.
- [ ] Authorized access, unauthorized rejection, OpenAI behavior, and persistence all pass the smoke test.
- [ ] Usage budgets or alerts are configured for the OpenAI project.

## Official references

- OpenAI: [API key safety](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety), [delete an API key](https://help.openai.com/en/articles/9047852-how-can-i-delete-my-api-key), and [prevent unauthorized usage](https://help.openai.com/en/articles/8304786-preventing-unauthorized-usage)
- Telegram: [bot tutorial and token security](https://core.telegram.org/bots/tutorial), [Bot API](https://core.telegram.org/bots/api), and [numeric user IDs](https://core.telegram.org/api/bots/ids)
- GitHub: [test the SSH connection](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection), [manage remote repositories](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories), and [push commits](https://docs.github.com/en/get-started/using-git/pushing-commits-to-a-remote-repository)
- Render: [background workers](https://render.com/docs/background-workers), [persistent disks](https://render.com/docs/disks), [environment variables](https://render.com/docs/configure-environment-variables), and [Blueprint specification](https://render.com/docs/blueprint-spec)
