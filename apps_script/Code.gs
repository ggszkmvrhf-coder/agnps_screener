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
  const props = PropertiesService.getScriptProperties();
  const backendUrl = props.getProperty('BACKEND_URL');
  if (!backendUrl) { Logger.log('BACKEND_URL not set. Aborting.'); return; }
  const token = props.getProperty('BACKEND_TOKEN');

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const leadsSheet = ss.getSheetByName(CONFIG.sheets.leads);
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
    const boundaryReady = boundaryStatus === CONFIG.boundaryDrawn && status !== CONFIG.statusValues.ready;
    if (!explicitlySubmitted && !boundaryReady) continue;

    if (!explicitlySubmitted) {
      setCell_(leadsSheet, rowNumber, col, 'Status', CONFIG.statusValues.processing);
    }

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
  const sheet = ss.getSheetByName(CONFIG.sheets.boundaries);
  const map = {};
  if (!sheet || sheet.getLastRow() < 2) return map;
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

  appendAutoFacts_(ss, leadId, af);
  appendBmpCandidates_(ss, leadId, result.BMPCandidates || []);
  appendCalculation_(ss, leadId, calc);

  setCell_(leadsSheet, rowNumber, col, 'CandidateScore', result.CandidateScore);
  setCell_(leadsSheet, rowNumber, col, 'CandidateClass', result.CandidateClass);
  setCell_(leadsSheet, rowNumber, col, 'GISConfidence', result.GISConfidence);
  setCell_(leadsSheet, rowNumber, col, 'NextAction', result.NextAction);
  setCell_(leadsSheet, rowNumber, col, 'BoundaryStatus', result.BoundaryStatus);
  setCell_(leadsSheet, rowNumber, col, 'BoundarySource', result.BoundarySource);
  setCell_(leadsSheet, rowNumber, col, 'BoundaryAreaAcres', result.BoundaryAreaAcres);
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

function appendAutoFacts_(ss, leadId, af) {
  const sheet = ss.getSheetByName(CONFIG.sheets.autoFacts);
  appendByHeader_(sheet, {
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

function appendBmpCandidates_(ss, leadId, bmps) {
  const sheet = ss.getSheetByName(CONFIG.sheets.bmpCandidates);
  bmps.forEach(function (b, idx) {
    appendByHeader_(sheet, {
      BMPCandidateID: leadId + '-bmp-' + (idx + 1), LeadID: leadId,
      BMPName: b.BMPName, BMPCategory: b.BMPCategory, ReasonSuggested: b.ReasonSuggested,
      Confidence: b.Confidence, NeedsHumanReview: b.NeedsHumanReview, Notes: b.Notes || '',
    });
  });
}

function appendCalculation_(ss, leadId, calc) {
  const sheet = ss.getSheetByName(CONFIG.sheets.calculations);
  if (!sheet) return;
  appendByHeader_(sheet, {
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
function appendByHeader_(sheet, valueMap) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const row = headers.map(function (h) {
    const k = String(h).trim();
    return Object.prototype.hasOwnProperty.call(valueMap, k) ? valueMap[k] : '';
  });
  sheet.appendRow(row);
}
function toNum_(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}
