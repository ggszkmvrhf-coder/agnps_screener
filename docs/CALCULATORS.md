# Calculators

Three internal calculators. **All outputs are rough internal estimates** — not
bids, not awarded amounts, and not official grant scores. Every result carries
warnings that SWCD/NRCS human review is required.

## 1. Internal Candidate Score (`app/scoring.py`)

0–100, summed from six components (each capped):

| Component | Max | How it scores |
|-----------|-----|----------------|
| Water-quality connection | 25 | ≤250 ft → 25; 251–1000 → 20; 1001–2500 → 15; 2501–5000 → 8; >5000/missing → 0–5 |
| WI/PWL / priority water | 20 | relevant record → 20; nearby unclear → 12; weak attributes → 8; none → 0 |
| BMP fit | 20 | strong → 20; moderate → 12; weak → 5; none → 0 |
| Topography / soils | 15 | strong evidence → 15; moderate → 8–12; weak/missing → 0–5 |
| Documentation completeness | 10 | +2 each: GPS point · 2+ photos · good description · boundary drawn · farmer interest/permission known |
| DAC / public benefit | 10 | intersects DAC → 10; nearby/downstream → 5–8; none → 0 |

**Classification:** 80–100 Strong · 60–79 Possible · 40–59 Weak · 0–39 Poor.

Returns the score, `CandidateClass`, the six breakdown numbers, and a
`ScoreExplanation` string so any number is traceable. Always returns the four
required warnings (internal-only, SWCD review, not eligibility, layers may be
incomplete).

## 2. Cost-Share Calculator (`app/calculators.py → cost_share`)

Inputs: `EstimatedProjectCost` (+ optional override percents/margin).
Defaults (in `settings.py`): low 0.75, high 0.875, company margin 0.20.

```
EstimatedCostShareLow      = cost * 0.75
EstimatedCostShareHigh     = cost * 0.875
EstimatedFarmerCostHigh    = cost - EstimatedCostShareLow      (low cost-share → farmer pays more)
EstimatedFarmerCostLow     = cost - EstimatedCostShareHigh
EstimatedCompanyRevenue    = cost
EstimatedCompanyGrossMarginDollars = cost * 0.20
```

Warning: *“Cost-share estimate is rough and depends on program rules, farmer
contribution, SWCD review, and final award.”* Never claims an awarded amount.

## 3. Rough Project Cost Estimator (`app/calculators.py → estimate_project_cost`)

If the rep entered `EstimatedProjectCost`, that value is used as-is. Otherwise
it’s estimated from acreage + ProblemType using **editable placeholders** in
`settings.project_cost_table` (`base $ + $/acre`):

| ProblemType | Base | Per acre |
|-------------|------|----------|
| Bad outlet | $8,000 | $300 |
| Ditch or stream erosion / Surface erosion / Surface runoff | $10,000 | $400 |
| Possible controlled drainage | $12,000 | $250 |
| Wet field / Unknown old tile | $7,500 | $250 |
| Other / Unknown | $5,000 | $200 |

If no acreage is available, 1 acre is assumed (with a warning). Warnings:
*“Rough planning estimate only — not a bid”* and *“must be reviewed before
customer or SWCD use.”*

> These numbers are placeholders the company owns — edit
> `Settings.project_cost_table` / `project_cost_default` (or override via the
> calculator inputs). No code logic changes are needed to retune them.

## Where the numbers go
`build_calculation()` bundles all of the above into the `Calculations` record
(one row per processed lead) and the headline figures are mirrored onto `Leads`
(`EstimatedProjectCost`, cost-share range, farmer-cost range, company revenue).
