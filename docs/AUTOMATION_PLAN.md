# Automation plan

How a submitted lead flows from the phone to a finished candidate summary.

```
[Field rep]                                                        
   │  fills "New Lead" form                                        
   ▼                                                               
[AppSheet]  ──writes row (Status=New)──►  [Google Sheet: Leads]    
                                                │                  
                                  every ~5 min  │  (or manual menu) 
                                                ▼                  
                                        [Apps Script]              
                                        processNewLeads()          
                                                │                  
                          POST /process-lead     │  (UrlFetchApp)   
                                                ▼                  
                                   [Python FastAPI backend]        
                              county/town · HUC · waterbody ·      
                              WI/PWL · DAC · soil · slope ·         
                              BMP rules · candidate score          
                                                │                  
                            JSON response        │                 
                                                ▼                  
                                        [Apps Script]              
                  writes Auto_Facts + BMP_Candidates rows,         
                  sets Leads.CandidateScore, Status=Report Ready   
                                                │                  
                                                ▼                  
                              [AppSheet] shows read-only result    
                              on Lead Detail (candidate / review)  
```

## Steps in detail

1. **Submit** — Rep submits a lead; `Status` initial value is `"New"`.
2. **Pick up** — Apps Script `processNewLeads()` (time trigger every 5 min, or
   the "AgNPS → Process new leads now" menu) scans for `Status = "New"`.
3. **Lock** — Each lead is immediately set to `Processing` so a second run won't
   double-send it.
4. **Call backend** — `POST {BACKEND_URL}/process-lead` with the lead payload
   (optional `Authorization: Bearer {BACKEND_TOKEN}`).
5. **Compute** — Backend runs GIS lookups + rules and returns the full result,
   never a 5xx (errors come back as `Status="Error"` + `ProcessingError`).
6. **Write back** — Script appends an `Auto_Facts` row and one `BMP_Candidates`
   row per suggestion, sets `Leads.CandidateScore` and `UpdatedAt`.
7. **Finish** — `Status` → `Report Ready` on success, or `Error` (+ note in
   `InternalNotes`) on failure.

## Configuration (Apps Script → Project Settings → Script Properties)

| Property | Required | Example |
|----------|----------|---------|
| `BACKEND_URL` | yes | `https://agnps.example.com` (no trailing slash) |
| `BACKEND_TOKEN` | optional | shared secret sent as `Bearer` token |

Run `setUpTrigger()` once to install the 5-minute trigger.

## Failure handling

* Backend unreachable / HTTP error → caught; lead set to `Error`, message stored
  in `InternalNotes`. It will **not** silently retry forever (it's no longer
  `New`); a human can reset `Status` to `New` to retry.
* Backend reachable but data partial → `Report Ready` with warnings; that's the
  normal "needs review" path, not an error.

## Local development

To test against a local backend, expose it with a tunnel (e.g. ngrok) and set
`BACKEND_URL` to the tunnel URL. The backend has no inbound dependency on Google.
