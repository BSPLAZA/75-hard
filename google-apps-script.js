// Google Apps Script — paste this into your Google Sheet's script editor
// It receives form submissions and writes them as rows

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var data = JSON.parse(e.postData.contents);

  // Add headers if the sheet is empty
  if (sheet.getLastRow() === 0) {
    sheet.appendRow([
      'Timestamp', 'Name', 'Phone', 'Email', 'Tier',
      'Rule Proposals', 'Feedback'
    ]);
    // Bold the header row
    sheet.getRange(1, 1, 1, 7).setFontWeight('bold');
  }

  sheet.appendRow([
    new Date().toLocaleString(),
    data.name || '',
    data.phone || '',
    data.email || '',
    data.tier || '',
    data.rule_proposals || '',
    data.feedback || ''
  ]);

  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok' }))
    .setMimeType(ContentService.MimeType.JSON);
}
