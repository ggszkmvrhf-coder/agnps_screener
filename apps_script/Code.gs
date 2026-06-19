/**
 * AgNPS Candidate Lead Screener — Apps Script glue (v0.2).
 *
 * Scans Leads submitted for processing, sends each to the Python backend
 * /process-lead, and writes the returned Auto_Facts, BMP candidates, and
 * Calculations back into the Google Sheet. Sheets is the system of record; this
 * script is the bridge to the GIS backend.
 *
 * SETUP:
 *   1. Extensions > Apps Script from the Google Sheet behind your AppSheet app.
 *   2. Paste this file. Project Settings > Script Properties:
 *        BACKEND_URL   = https://your-backend.example.com   (no trailing slash)
 *        BACKEND_TOKEN = optional API key (sent as X-API-Key, matches API_KEY)
 *   3. Run setUpTrigger() once (5-min trigger) or use the AgNPS menu.
 */

const CONFIG = {
  sheets: {
    leads: 'Leads',
    boundaries: 'Field_Boundaries',
    autoFacts: 'Auto_Facts',
    bmpCandidates: 'BMP_Candidates',
    calculations: 'Calculations',
  },
  statusValues: { new: 'New', processing: 'Processing', ready: 'Report Ready', needsReview: 'Needs Review' },
  boundaryDrawn: 'Drawn',
};

// AGENT-M1: REQUIRED_HEADERS is the authoritative schema contract.
// Schema version: 1.0
// Any column rename MUST be updated here AND in schema/*.csv simultaneously.
// Contract test: backend/tests/test_schema_contract.py verifies these match the CSV files.
const REQUIRED_HEADERS = {
  Leads: [
    'LeadID', 'CreatedAt', 'UpdatedAt', 'SalesRepEmail', 'SalesRepName',
    'CustomerName', 'FarmName', 'FieldName', 'ProblemType', 'ProblemDescription',
    'ProblemLocation', 'GPSLatitude', 'GPSLongitude', 'BoundaryStatus',
    'BoundarySource', 'BoundaryAreaAcres', 'BoundaryDrawURL', 'BoundaryShareURL',
    'FarmerInterestedInCostShare', 'PermissionToShareWithSWCD', 'Urgency',
    'SendToDesignTeam', 'Status', 'CandidateScore', 'CandidateClass',
    'GISConfidence', 'EstimatedProjectCost', 'EstimatedCostShareLow',
    'EstimatedCostShareHigh', 'EstimatedFarmerCostLow', 'EstimatedFarmerCostHigh',
    'EstimatedCompanyRevenue', 'ReportURL', 'InternalNotes', 'NextAction',
  ],
  Field_Boundaries: [
    'BoundaryID', 'LeadID', 'CreatedAt', 'BoundarySource', 'BoundaryGeoJSON',
    'BoundaryWKT', 'BoundaryAreaAcres', 'BoundaryCentroidLat',
    'BoundaryCentroidLng', 'BoundaryConfidence', 'GeometryValid',
    'GeometryWarning', 'Notes',
  ],
  Auto_Facts: [
    'FactID', 'LeadID', 'ProcessedAt', 'AnalysisGeometrySource', 'CountyAuto',
    'TownAuto', 'HUC8', 'HUC10', 'HUC12', 'HUC12Name',
    'NearestWaterbodyName', 'NearestWaterbodyType', 'DistanceToWaterbodyFt',
    'WIPWLNearby', 'WIPWLSummary', 'DACIntersecting', 'DACNearby',
    'DominantSoilDrainageClass', 'DominantHydrologicSoilGroup',
    'MeanSlopePercent', 'MaxSlopePercent', 'GISConfidence',
    'MissingInfoChecklist', 'HumanReviewWarnings', 'ProcessingError',
    'WaterQualityConnectionScore', 'WIPWLScore', 'BMPFitScore',
    'TopoSoilsScore', 'DocumentationScore', 'DACScore', 'ScoreExplanation',
  ],
  BMP_Candidates: [
    'BMPCandidateID', 'LeadID', 'BMPName', 'BMPCategory', 'ReasonSuggested',
    'Confidence', 'NeedsHumanReview', 'Notes',
  ],
  Calculations: [
    'CalculationID', 'LeadID', 'CreatedAt', 'EstimatedProjectCost',
    'CostShareLowPercent', 'CostShareHighPercent', 'EstimatedCostShareLow',
    'EstimatedCostShareHigh', 'EstimatedFarmerCostLow',
    'EstimatedFarmerCostHigh', 'EstimatedCompanyRevenue',
    'EstimatedCompanyGrossMarginPercent', 'EstimatedCompanyGrossMarginDollars',
    'Assumptions', 'CalculatorWarnings',
  ],
};

function setUpTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processLeads') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('processLeads').timeBased().everyMinutes(5).create();
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('AgNPS')
    .addItem('Process leads now', 'processLeads')
    .addToUi();
}

/** Main entry point. */
function processLeads() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) {
    Logger.log('Another processLeads run is active. Skipping this run.');
    return;
  }
  try {
    processLeadsLocked_();
  } finally {
    lock.releaseLock();
  }
}

function processLeadsLocked_() {
  const props = PropertiesService.getScriptProperties();
  const backendUrl = props.getProperty('BACKEND_URL');
  if (!backendUrl) { Logger.log('BACKEND_URL not set. Aborting.'); return; }
  const token = props.getProperty('BACKEND_TOKEN');

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  validateWorkbook_(ss);
  const leadsSheet = requireSheet_(ss, CONFIG.sheets.leads);
  const headers = leadsSheet.getRange(1, 1, 1, leadsSheet.getLastColumn()).getValues()[0];
  const col = indexMap_(headers);
  const rows = leadsSheet.getDataRange().getValues().slice(1);
  const boundaries = boundaryMap_(ss);

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const rowNumber = i + 2;
    const status = String(get_(row, col, 'Status')).trim();
    const boundaryStatus = String(get_(row, col, 'BoundaryStatus')).trim();
    const leadId = get_(row, col, 'LeadID');
    if (!leadId) continue;

    const explicitlySubmitted = status === CONFIG.statusValues.processing;
    if (!explicitlySubmitted) continue;

    try {
      const payload = buildPayload_(row, col, boundaries[leadId]);
      const result = callBackend_(backendUrl, token, payload);
      writeResult_(ss, leadsSheet, rowNumber, col, leadId, result);
    } catch (err) {
      Logger.log('Lead ' + leadId + ' failed: ' + err);
      setCell_(leadsSheet, rowNumber, col, 'Status', CONFIG.statusValues.needsReview);
      appendNote_(leadsSheet, rowNumber, col, 'Backend error: ' + (err && err.message ? err.message : err));
    }
  }
}

/** Map LeadID -> BoundaryGeoJSON string from the Field_Boundaries sheet. */
function boundaryMap_(ss) {
  const sheet = requireSheet_(ss, CONFIG.sheets.boundaries);
  const map = {};
  if (sheet.getLastRow() < 2) return map;
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const c = indexMap_(headers);
  sheet.getDataRange().getValues().slice(1).forEach(function (r) {
    const id = r[c['LeadID']];
    const gj = r[c['BoundaryGeoJSON']];
    if (id && gj) map[id] = gj;
  });
  return map;
}

function buildPayload_(row, col, boundaryGeoJSON) {
  const payload = {
    LeadID: get_(row, col, 'LeadID'),
    CustomerName: get_(row, col, 'CustomerName'),
    FarmName: get_(row, col, 'FarmName'),
    FieldName: get_(row, col, 'FieldName'),
    ProblemType: get_(row, col, 'ProblemType'),
    ProblemDescription: get_(row, col, 'ProblemDescription'),
    ProblemLocation: get_(row, col, 'ProblemLocation'),
    GPSLatitude: toNum_(get_(row, col, 'GPSLatitude')),
    GPSLongitude: toNum_(get_(row, col, 'GPSLongitude')),
    EstimatedProjectCost: toNum_(get_(row, col, 'EstimatedProjectCost')),
    FarmerInterestedInCostShare: get_(row, col, 'FarmerInterestedInCostShare'),
    PermissionToShareWithSWCD: get_(row, col, 'PermissionToShareWithSWCD'),
  };
  if (boundaryGeoJSON) {
    try { payload.BoundaryGeoJSON = JSON.parse(boundaryGeoJSON); }
    catch (e) { payload.BoundaryGeoJSON = boundaryGeoJSON; }
  }
  return payload;
}

function callBackend_(backendUrl, token, payload) {
  const headers = {};
  if (token) headers['X-API-Key'] = token;
  const resp = UrlFetchApp.fetch(backendUrl.replace(/\/$/, '') + '/process-lead', {
    method: 'post', contentType: 'application/json', headers: headers,
    payload: JSON.stringify(payload), muteHttpExceptions: true,
  });
  const code = resp.getResponseCode();
  const text = resp.getContentText();
  if (code < 200 || code >= 300) throw new Error('HTTP ' + code + ': ' + text.slice(0, 300));
  return JSON.parse(text);
}

function writeResult_(ss, leadsSheet, rowNumber, col, leadId, result) {
  const af = result.AutoFacts || {};
  const calc = result.Calculations || {};

  // Upsert-in-place: overwrite existing rows for this LeadID rather than
  // delete-then-append. A crash mid-write leaves stale data visible instead of
  // creating a gap (no recovery path). See upsertSingle_/upsertMulti_.
  upsertAutoFacts_(ss, leadId, af);
  upsertBmpCandidates_(ss, leadId, result.BMPCandidates || []);
  upsertCalculation_(ss, leadId, calc);

  setCell_(leadsSheet, rowNumber, col, 'CandidateScore', result.CandidateScore);
  setCell_(leadsSheet, rowNumber, col, 'CandidateClass', result.CandidateClass);
  setCell_(leadsSheet, rowNumber, col, 'GISConfidence', result.GISConfidence);
  setCell_(leadsSheet, rowNumber, col, 'NextAction', result.NextAction);
  setCell_(leadsSheet, rowNumber, col, 'BoundaryStatus', result.BoundaryStatus);
  setCell_(leadsSheet, rowNumber, col, 'BoundarySource', result.BoundarySource);
  setCell_(leadsSheet, rowNumber, col, 'BoundaryAreaAcres', result.BoundaryAreaAcres);
  setCell_(leadsSheet, rowNumber, col, 'BoundaryShareURL', result.BoundaryShareURL);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedProjectCost', calc.EstimatedProjectCost);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedCostShareLow', calc.EstimatedCostShareLow);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedCostShareHigh', calc.EstimatedCostShareHigh);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedFarmerCostLow', calc.EstimatedFarmerCostLow);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedFarmerCostHigh', calc.EstimatedFarmerCostHigh);
  setCell_(leadsSheet, rowNumber, col, 'EstimatedCompanyRevenue', calc.EstimatedCompanyRevenue);
  setCell_(leadsSheet, rowNumber, col, 'UpdatedAt', new Date());

  const ok = result.Status !== 'Error' && !result.ProcessingError;
  setCell_(leadsSheet, rowNumber, col, 'Status', ok ? CONFIG.statusValues.ready : CONFIG.statusValues.needsReview);
  if (!ok) appendNote_(leadsSheet, rowNumber, col, 'Processing error: ' + (result.ProcessingError || 'unknown'));
}

function upsertAutoFacts_(ss, leadId, af) {
  const sheet = requireSheet_(ss, CONFIG.sheets.autoFacts);
  // ProcessedAt is part of the Auto_Facts schema contract, so set it here.
  upsertSingle_(sheet, leadId, {
    FactID: leadId + '-' + new Date().getTime(),
    LeadID: leadId, ProcessedAt: new Date(),
    AnalysisGeometrySource: af.AnalysisGeometrySource,
    CountyAuto: af.CountyAuto, TownAuto: af.TownAuto,
    HUC8: af.HUC8, HUC10: af.HUC10, HUC12: af.HUC12, HUC12Name: af.HUC12Name,
    NearestWaterbodyName: af.NearestWaterbodyName, NearestWaterbodyType: af.NearestWaterbodyType,
    DistanceToWaterbodyFt: af.DistanceToWaterbodyFt,
    WIPWLNearby: af.WIPWLNearby, WIPWLSummary: af.WIPWLSummary,
    DACIntersecting: af.DACIntersecting, DACNearby: af.DACNearby,
    DominantSoilDrainageClass: af.DominantSoilDrainageClass,
    DominantHydrologicSoilGroup: af.DominantHydrologicSoilGroup,
    MeanSlopePercent: af.MeanSlopePercent, MaxSlopePercent: af.MaxSlopePercent,
    GISConfidence: af.GISConfidence,
    MissingInfoChecklist: (af.MissingInfoChecklist || []).join(' | '),
    HumanReviewWarnings: (af.HumanReviewWarnings || []).join(' | '),
    ProcessingError: af.ProcessingError || '',
    WaterQualityConnectionScore: af.WaterQualityConnectionScore,
    WIPWLScore: af.WIPWLScore, BMPFitScore: af.BMPFitScore,
    TopoSoilsScore: af.TopoSoilsScore, DocumentationScore: af.DocumentationScore,
    DACScore: af.DACScore, ScoreExplanation: af.ScoreExplanation,
  });
}

function upsertBmpCandidates_(ss, leadId, bmps) {
  const sheet = requireSheet_(ss, CONFIG.sheets.bmpCandidates);
  // BMP_Candidates has no ProcessedAt column in REQUIRED_HEADERS; do not add one.
  const valueMaps = bmps.map(function (b, idx) {
    return {
      BMPCandidateID: leadId + '-bmp-' + (idx + 1), LeadID: leadId,
      BMPName: b.BMPName, BMPCategory: b.BMPCategory, ReasonSuggested: b.ReasonSuggested,
      Confidence: b.Confidence, NeedsHumanReview: b.NeedsHumanReview, Notes: b.Notes || '',
    };
  });
  upsertMulti_(sheet, leadId, valueMaps);
}

function upsertCalculation_(ss, leadId, calc) {
  const sheet = requireSheet_(ss, CONFIG.sheets.calculations);
  // Calculations has no ProcessedAt column in REQUIRED_HEADERS; do not add one.
  upsertSingle_(sheet, leadId, {
    CalculationID: leadId + '-calc-' + new Date().getTime(), LeadID: leadId, CreatedAt: new Date(),
    EstimatedProjectCost: calc.EstimatedProjectCost,
    CostShareLowPercent: calc.CostShareLowPercent, CostShareHighPercent: calc.CostShareHighPercent,
    EstimatedCostShareLow: calc.EstimatedCostShareLow, EstimatedCostShareHigh: calc.EstimatedCostShareHigh,
    EstimatedFarmerCostLow: calc.EstimatedFarmerCostLow, EstimatedFarmerCostHigh: calc.EstimatedFarmerCostHigh,
    EstimatedCompanyRevenue: calc.EstimatedCompanyRevenue,
    EstimatedCompanyGrossMarginPercent: calc.EstimatedCompanyGrossMarginPercent,
    EstimatedCompanyGrossMarginDollars: calc.EstimatedCompanyGrossMarginDollars,
    Assumptions: calc.Assumptions, CalculatorWarnings: calc.CalculatorWarnings,
  });
}

/* ----------------------------- helpers ----------------------------- */
function validateWorkbook_(ss) {
  Object.keys(REQUIRED_HEADERS).forEach(function (sheetName) {
    const sheet = requireSheet_(ss, sheetName);
    requireHeaders_(sheet, REQUIRED_HEADERS[sheetName]);
  });
}

function requireSheet_(ss, sheetName) {
  const sheet = ss.getSheetByName(sheetName);
  if (!sheet) throw new Error('Missing required sheet tab: ' + sheetName);
  return sheet;
}

function requireHeaders_(sheet, required) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const col = indexMap_(headers);
  const missing = required.filter(function (name) { return col[name] === undefined; });
  if (missing.length) {
    throw new Error('Sheet ' + sheet.getName() + ' is missing columns: ' + missing.join(', '));
  }
}

/**
 * Find the 1-based sheet row numbers whose LeadID column matches leadId.
 * Returns an ascending array (e.g. [3, 7]). Reads the data range once.
 */
function findRowsByLeadId_(sheet, leadId) {
  if (sheet.getLastRow() < 2) return [];
  const data = sheet.getDataRange().getValues();
  const col = indexMap_(data[0]);
  if (col['LeadID'] === undefined) throw new Error('Sheet ' + sheet.getName() + ' is missing LeadID.');
  const matches = [];
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][col['LeadID']]) === String(leadId)) matches.push(i + 1);
  }
  return matches;
}

/** Build a row array ordered to match the sheet headers from a {header: value} map. */
function rowFromMap_(sheet, valueMap) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  return headers.map(function (h) {
    const k = String(h).trim();
    return Object.prototype.hasOwnProperty.call(valueMap, k) ? valueMap[k] : '';
  });
}

/**
 * Upsert exactly one row per LeadID. Overwrites the first existing row in place;
 * appends if none exists. A crash here cannot create a gap: either the existing
 * row's old values stay visible, or no row existed and the lead simply has no
 * row yet (same as a never-processed lead).
 */
function upsertSingle_(sheet, leadId, valueMap) {
  const rowArray = rowFromMap_(sheet, valueMap);
  const matches = findRowsByLeadId_(sheet, leadId);
  if (matches.length === 0) {
    sheet.appendRow(rowArray);
    return;
  }
  // Overwrite the first match in place.
  sheet.getRange(matches[0], 1, 1, rowArray.length).setValues([rowArray]);
  // Remove any surplus duplicate rows AFTER the primary row is written.
  deleteSurplusRows_(sheet, matches.slice(1));
}

/**
 * Upsert multiple rows per LeadID. Existing rows are overwritten in place; extra
 * new rows are appended; surplus old rows are deleted only AFTER the new data has
 * been written. A crash partway through leaves a mix of new + stale rows visible
 * (never a gap), and the surplus-delete is the only remaining delete operation.
 */
function upsertMulti_(sheet, leadId, valueMaps) {
  const matches = findRowsByLeadId_(sheet, leadId);
  const numCols = sheet.getLastColumn();

  // 1) Overwrite as many existing rows as we have new data for.
  const overlap = Math.min(matches.length, valueMaps.length);
  for (let i = 0; i < overlap; i++) {
    const rowArray = rowFromMap_(sheet, valueMaps[i]);
    sheet.getRange(matches[i], 1, 1, numCols).setValues([rowArray]);
  }

  // 2) New data has MORE rows than existed: append the extras.
  for (let i = overlap; i < valueMaps.length; i++) {
    sheet.appendRow(rowFromMap_(sheet, valueMaps[i]));
  }

  // 3) New data has FEWER rows than existed: delete the surplus trailing rows.
  //    Only happens after the kept rows are already overwritten with new data.
  if (matches.length > valueMaps.length) {
    deleteSurplusRows_(sheet, matches.slice(valueMaps.length));
  }
}

/** Delete the given 1-based row numbers, highest first so indices do not shift. */
function deleteSurplusRows_(sheet, rowNumbers) {
  if (!rowNumbers || rowNumbers.length === 0) return;
  rowNumbers.slice().sort(function (a, b) { return b - a; }).forEach(function (rn) {
    sheet.deleteRow(rn);
  });
}

function indexMap_(headers) {
  const m = {};
  headers.forEach(function (h, i) { m[String(h).trim()] = i; });
  return m;
}
function get_(row, col, name) { return col[name] === undefined ? null : row[col[name]]; }
function setCell_(sheet, rowNumber, col, name, value) {
  if (col[name] === undefined || value === undefined || value === null) return;
  sheet.getRange(rowNumber, col[name] + 1).setValue(value);
}
function appendNote_(sheet, rowNumber, col, msg) {
  if (col['InternalNotes'] === undefined) return;
  sheet.getRange(rowNumber, col['InternalNotes'] + 1).setValue(msg);
}
function toNum_(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}
