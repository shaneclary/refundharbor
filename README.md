# DenseWealth

Static marketing / lead-generation site for tariff refund recovery, attorney-led.
Plain HTML + one shared stylesheet. No build step. Deploys to Vercel as static files.

## Files

| File | Purpose |
|------|---------|
| `index.html` | Landing page: hero, opportunity stats, process, why-attorney-led, legal team, FAQ, eligibility intake form |
| `disclosures.html` | Affiliate / not-a-law-firm / no-guarantee disclosures |
| `privacy.html` | Privacy policy |
| `terms.html` | Terms of use |
| `styles.css` | Shared design tokens + all components |
| `form-handler.gs` | Google Apps Script that receives intake POSTs and appends them to a Sheet |
| `FORM_SETUP.md` | Step-by-step to deploy the Apps Script and wire the form |
| `vercel.json` | Security + no-index response headers |
| `robots.txt` | Blocks AI/training crawlers |

## Before you deploy

1. **Wire up the intake form.** Leads are collected in a Google Sheet via a
   Google Apps Script web app. Follow [FORM_SETUP.md](FORM_SETUP.md) (~10 min):
   create a Sheet, paste [`form-handler.gs`](form-handler.gs), deploy it as a
   web app, and put the resulting `/exec` URL in the `action` of `#intake-form`
   in `index.html` (replacing `YOUR_DEPLOYMENT_ID`). Submissions append a row to
   the Sheet and show inline success/error states without leaving the page;
   until configured, the form shows a "not configured" message.
2. **Have counsel review the legal pages.** `disclosures.html`, `privacy.html`,
   and `terms.html` still contain bracketed `[placeholders]` (legal entity name,
   address, governing law, vendor names, retention periods, state-specific
   disclosures). Have qualified counsel review and finalize them before relying
   on them as binding legal terms.
3. **Fill the dates.** Replace `[Month Day, 2026]` "last updated" dates on the
   legal pages.
4. **Confirm the figures.** Statistics in the hero/stats/FAQ ($166B, 301,000+,
   timelines, 40% rejection rate, deadlines) and fee terms ($10,000 engagement,
   20% contingency) are marketing copy — verify they match the actual program
   and engagement agreement.

## Local preview

```sh
# Python
python -m http.server 8000
# or Node
npx serve .
```

Then open http://localhost:8000.

## Deploy (Vercel)

No framework — deploy as a static site (root directory, no build command).

```sh
vercel --prod
```
