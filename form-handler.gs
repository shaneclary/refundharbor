/**
 * DenseWealth — eligibility intake handler (Google Apps Script)
 * =============================================================
 * Receives POSTs from the intake form in index.html and appends each
 * submission as a row in this Spreadsheet. Returns JSON so the page can
 * show inline success/error states.
 *
 * Setup: see FORM_SETUP.md. In short — create a Google Sheet, open
 * Extensions → Apps Script, paste this file, then Deploy → New deployment →
 * Web app (Execute as: Me, Who has access: Anyone). Use the /exec URL as the
 * form's `action`.
 *
 * This script is BOUND to the Sheet it lives in (getActiveSpreadsheet).
 * To use a standalone script instead, replace getActiveSpreadsheet() with
 * SpreadsheetApp.openById('YOUR_SHEET_ID').
 */

var SHEET_NAME = 'Intakes';

var HEADERS = [
  'Timestamp', 'Status', 'Company', 'Name', 'Role', 'Email',
  'Phone', 'Import value', 'Goods', 'Details', 'User agent'
];

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000); // avoid two submissions writing the same row

    var p = (e && e.parameter) ? e.parameter : {};

    // Honeypot: real users never fill `_gotcha`. Silently accept + drop bots.
    if (p._gotcha) {
      return json({ result: 'success' });
    }

    var sheet = getSheet_();

    sheet.appendRow([
      new Date(),          // Timestamp
      'new',               // Status (you edit this as you work the lead)
      p.company || '',
      p.name || '',
      p.role || '',
      p.email || '',
      p.phone || '',
      p.import_value || '',
      p.goods || '',
      p.details || '',
      p._ua || ''          // User agent (sent by the page)
    ]);

    return json({ result: 'success' });
  } catch (err) {
    return json({ result: 'error', message: String(err && err.message ? err.message : err) });
  } finally {
    try { lock.releaseLock(); } catch (ignore) {}
  }
}

// Lets you open the /exec URL in a browser to confirm the deployment is live.
function doGet() {
  return json({ result: 'ok', message: 'DenseWealth intake endpoint is live.' });
}

function getSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold');
  }
  return sheet;
}

function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
