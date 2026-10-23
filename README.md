# TickBit

Daily news on AI, politics, crypto and stocks, plus market prices. The site is static: `build.py` collects headlines and prices, and GitHub Pages hosts the result for free. It rebuilds automatically every 3 hours. No API keys are needed.

## Publishing

1. Create a public repository on github.com and upload all files from this folder (including the `.github` folder).
2. **Settings → Pages → Source: GitHub Actions**.
3. **Actions → Build and deploy TickBit → Run workflow**.

## Optional settings (Settings → Secrets and variables → Actions → Variables)

- `SITE_URL`: your own domain, if you add one.
- `NEWSLETTER_ACTION`: the form address from Buttondown or MailerLite. The signup form appears automatically.
- `ADSENSE_CLIENT` and `ADSENSE_SLOT`: your Google AdSense details. Until then the ad spaces show gray placeholders.

## Editing

Sources, cryptocurrencies and stocks are listed at the top of `build.py`. The list of the 15 largest US stocks (`TOP_CAP`) is fixed and should be refreshed from time to time.
