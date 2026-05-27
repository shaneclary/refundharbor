# Intake form setup — Google Sheets via Apps Script

Leads from the eligibility form land as rows in a Google Sheet. No Formspree,
no server, no monthly cost. About 10 minutes, once.

## 1. Create the Sheet

1. Go to [sheets.google.com](https://sheets.google.com) and create a blank Sheet
   (e.g. **Refund Harbor — Intakes**). Put it in a Drive owned by your business
   account, not a personal one.
2. You don't need to add headers — the script creates them on the first
   submission.

## 2. Add the script

1. In that Sheet: **Extensions → Apps Script**.
2. Delete the starter `function myFunction() {}`.
3. Copy the entire contents of [`form-handler.gs`](form-handler.gs) and paste it in.
4. Click **Save** (disk icon).

## 3. Deploy it as a web app

1. Click **Deploy → New deployment**.
2. Click the gear next to "Select type" → **Web app**.
3. Set:
   - **Execute as:** Me
   - **Who has access:** Anyone
4. Click **Deploy**.
5. Click **Authorize access**, pick your Google account, and approve. (You may
   see an "unverified app" screen — click **Advanced → Go to … (unsafe)**. This
   is normal for your own script; you're authorizing your own code.)
6. Copy the **Web app URL**. It ends in `/exec` and looks like:
   `https://script.google.com/macros/s/AKfycb..../exec`

## 4. Wire it into the site

Open `index.html`, find the form (`id="intake-form"`), and replace the
placeholder in its `action`:

```html
<!-- before -->
action="https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec"
<!-- after: your real /exec URL -->
action="https://script.google.com/macros/s/AKfycb..../exec"
```

(Or just send me the URL and I'll paste it in.)

## 5. Test

1. Open `index.html` (locally or deployed), fill the form, submit.
2. You should see the green "Thank you" message, and a new row should appear in
   the Sheet's **Intakes** tab.
3. To confirm the endpoint itself is live, open the `/exec` URL in a browser —
   it returns `{"result":"ok", ...}`.

## Editing the script later

If you change `form-handler.gs`, the live endpoint won't update until you
redeploy: **Deploy → Manage deployments → (pencil/edit) → Version: New version →
Deploy**. Editing the existing deployment keeps the same `/exec` URL.

## Notes

- **The endpoint is public** (that's required for a browser to POST to it). The
  built-in honeypot field silently drops basic bots. At this scale that's
  enough; if spam becomes a problem, add a shared-secret field or CAPTCHA.
- **Email alerts (optional):** in the Sheet, **Tools → Notification settings**
  ("Notify me … any changes are made") to get an email on each new lead. Or add
  a `MailApp.sendEmail(...)` call inside `doPost` in the script.
- **Columns written:** Timestamp, Status, Company, Name, Role, Email, Phone,
  Import value, Goods, Details, User agent. The **Status** column starts as
  `new` for you to update as you work each lead.
