# DenseWealth

Static marketing / lead-generation site for tariff refund recovery, attorney-led.
Plain HTML + one shared stylesheet. No build step. Deploys to Vercel as static files.

## Files

| File | Purpose |
|------|---------|
| `index.html` | Landing page: hero, opportunity stats, process, why-attorney-led, legal team, FAQ, eligibility intake form |
| `disclosures.html` | Affiliate / not-a-law-firm / no-guarantee disclosures (**draft**) |
| `privacy.html` | Privacy policy (**draft**) |
| `terms.html` | Terms of use (**draft**) |
| `styles.css` | Shared design tokens + all components |
| `vercel.json` | Security + no-index response headers |
| `robots.txt` | Blocks AI/training crawlers |

## Before you deploy

1. **Wire up the intake form.** In `index.html`, the form `#intake-form`
   posts to `https://formspree.io/f/YOUR_FORM_ID`. Create a form at
   [formspree.io](https://formspree.io) and replace `YOUR_FORM_ID` with your
   real form ID (or swap the `action` for any endpoint that accepts a POST and
   returns JSON). Submissions show inline success/error states without leaving
   the page; until configured, the form shows a "not configured" message.
2. **Have counsel review the legal pages.** `disclosures.html`, `privacy.html`,
   and `terms.html` are working drafts, each topped with a draft banner and
   containing bracketed `[placeholders]` (legal entity name, address, governing
   law, vendor names, retention periods, state-specific disclosures). They are
   **not** final legal terms.
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
