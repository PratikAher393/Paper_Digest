# Weekly Paper Digest

Automatically fetch and score arXiv + journal papers every Monday. Browse a searchable archive on GitHub Pages with zero email setup.

## What it does

1. **Every Monday** (or on-demand), the workflow:
   - Fetches papers from arXiv categories you choose
   - Fetches recent papers from journals (via Crossref)
   - Scores each paper against your weighted keywords
   - Saves the digest as JSON to `docs/digests/`

2. **GitHub Pages** serves an archive site where you can:
   - Search by title, author, keyword
   - Filter by topic
   - Click papers to jump to arXiv or journal

3. **No passwords, no email setup** — GitHub handles everything.

## Setup (5 minutes)

### 1. Customize topics.yaml
Edit `topics.yaml` to add/remove research areas and keywords. Weights control how strongly each keyword scores a paper.

### 2. Upload files to GitHub
Create a new private repo (e.g., `paper-digest`). Upload these files, keeping the folder structure:
```
paper-digest/
  digest.py
  topics.yaml
  seen.json (create empty: {})
  docs/
    index.html
  .github/workflows/
    weekly-digest.yml
```

### 3. Enable GitHub Pages
- Go to **Settings → Pages**
- Set source to `Deploy from a branch`
- Branch: `main`, folder: `/docs`
- Save

### 4. (Optional) Enable email digests

The site works great on its own, but if you want the digest emailed to you every Monday:

**4a. Sign up for free email service** (no password, just an API key)
- Go to [resend.com](https://resend.com) (free tier for 100 emails/day)
- Create an account, get your API key
- Paste it into repo **Settings → Secrets and variables → Actions** as `RESEND_API_KEY`

**4b. Add your email address**
- Go to **Settings → Secrets and variables → Actions**
- Click **New repository secret**
- Name: `EMAIL_TO` → Value: `paher@vols.utk.edu`

**4c. Turn on email**
- Go to **Settings → Variables → Actions**
- Click **New repository variable**
- Name: `SEND_EMAIL` → Value: `true`

**4d. Uncomment the Resend code**
- Open `send_email.py`
- Find the comment `# Uncomment and use Resend if you want real emails:`
- Uncomment those 6 lines (remove the `#` at the start)
- Replace `digest@yourdomain.com` with an email like `digest@example.resend.dev` (Resend will tell you this)

That's it. Starting next Monday, you'll get an HTML email with paper links + a link back to the archive site.

**Why Resend?** No password. No SMTP relay. Just an API key. Free tier covers 100 emails/month.

**To turn off emails later:** change `SEND_EMAIL` to `false` in repo variables.

The site will be live at `https://your-username.github.io/paper-digest/` in ~1 min.

### 5. Test it
- Go to the **Actions** tab
- Click **weekly-paper-digest**
- Click **Run workflow**
- Wait ~2 min. If it succeeds, check your repo — a new folder `docs/digests/` will appear with today's digest.
- Visit your GitHub Pages URL. The digest should appear instantly.

## How to iterate

**Want to change keywords?**
- Edit `topics.yaml`
- Click "Run workflow" to rebuild immediately

**Want to adjust the schedule?**
- Edit `.github/workflows/weekly-digest.yml`
- Change `cron: "0 12 * * 1"` to a different day/time (cron syntax)

**Want to add more arXiv categories or journals?**
- Edit `topics.yaml`
- Add ISSN codes from any journal's website (search "[Journal Name] ISSN")

## Troubleshooting

**Workflow fails?**
- Click the failed workflow in Actions
- Scroll down and read the red error text
- Common issues:
  - Missing `seen.json` file (create an empty one: `{}`)
  - Network timeout (just re-run, arXiv is slow sometimes)

**Site doesn't update?**
- Wait 2 minutes after the workflow succeeds (Pages deploy is async)
- Hard-refresh your browser (Ctrl+Shift+R or Cmd+Shift+R)
- Check that `.github/workflows/weekly-digest.yml` has `deploy` job and Pages is enabled

**No papers showing up?**
- Keywords may be too strict. Lower `min_score` in `topics.yaml` from 3 to 2
- Run `--dry-run` locally to test (see below)

## Running locally

To test before committing:
```bash
pip install feedparser requests pyyaml
python digest.py --dry-run
open digest.html
```

This writes a test digest to `digest.html` without touching the archive. Useful for tuning keywords.

## Next steps

Once you're happy with the digest quality:

1. **Claude API reranking** — add a step that uses Claude to rerank top papers and write one-line summaries
2. **Browser notifications** — get a push notification every Monday when the digest arrives
3. **Slack integration** — post the digest to a Slack channel
4. **Export as email** — GitHub Pages can send you a weekly summary via Actions

---

Built to solve the problem: existing alerting (Google Scholar, arXiv email) doesn't combine sources or let you weight your own keywords.
