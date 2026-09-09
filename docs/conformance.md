# WCAG and draft Accessibility Conformance Reports

This additive workflow uses saved `results.json` files. It does not change crawling,
start a browser, call an AI provider, or send report data to a service. HTML and JSON
exports are local. Keep the original scan folder, including its evidence, for each release.

## What is included

- A versioned WCAG 2.0, 2.1 and 2.2 criterion catalog, filtered by A, AA or AAA.
  The existing scanner configuration still supports its existing 2.1/2.2 targets.
- Automated rule observations and findings mapped to success criteria.
- A manual-review starter checklist, normative W3C links and evidence references.
- Human conformance assessments with reviewer name, date, methods, remarks and evidence.
- Responsive, searchable draft ACR and comparison dashboards with JSON exports.
- Pairwise finding comparisons and optional comparisons of reviewer assessments.

This is a **preparation worksheet for the WCAG edition**, not the official ITI template,
a completed VPAT, a certificate or a legal assurance. Before publication, the product
owner must transfer or verify the content against the applicable official template and
instructions. Revised Section 508 and EN 301 549 contain additional requirements not
evaluated by this feature. ITI offers separate editions for those standards.

## 1 Save a scan for each release

Run the crawler as before. Use separate output folders so older scans are not overwritten:

```powershell
.\launch-agent.cmd scan --url https://example.com --output reports/release-1
```

Use equivalent environments, roles, viewport sizes, WCAG targets and engine versions
when comparing releases. Record the role and environment explicitly in your worksheet.
The tool does not infer your actual application role from your credentials.

## 2 Prepare the first draft

```powershell
.\launch-agent.cmd acr prepare --report reports/release-1/results.json --application-id orangehrm-qa-admin --product-name OrangeHRM --product-version 1.0 --scope "Employee dashboard and leave workflow" --environment QA --user-role Administrator --output reports/acr-release-1
```

Optional product fields are `--organization`, `--description`, and `--contact`.
Complete these before sharing any report. `--scope` must describe what was evaluated,
not claim full-product coverage for a sampled scan. Use a stable `application-id` across
releases of the same application/environment/role. This is a user-provided identifier,
not proof that two applications or sessions are equivalent.

The output contains:

- `acr-report.html`: offline draft dashboard and review editor.
- `acr.json`: structured snapshot of observations, assessments and limitations.
- `review.json`: editable reviewer worksheet, initially with all decisions pending.
- Local screenshot copies when valid source evidence exists.

The prepare command refuses to overwrite an existing draft. Open `acr-report.html`
and use the review editor to enter observations. Export/download the updated review
JSON, then use that downloaded file as the input to the build command. Browser edits
are not silently saved back to disk. Treat manual notes and exported JSON as potentially
confidential, just like the underlying scan.

## 3 Perform manual review and rebuild

For a criterion decision, record the reviewer, review date, evaluation methods, remarks
and at least one evidence reference. A reference can identify a test record, reviewed
screenshot or ticket; the program does not independently verify that the manual test ran.
Check full pages and complete processes with the relevant keyboard and assistive-technology
tests. The starter checklist is not an exhaustive testing procedure.

Conformance terms are **Supports**, **Partially Supports**, **Does Not Support**,
**Not Applicable**, and **Not Evaluated** (only for Level AAA). A blank selection means
pending manual review; it is not a VPAT conformance term. Automatic rule passes or
inapplicable rules never populate a conformance decision.

```powershell
.\launch-agent.cmd acr build --report reports/release-1/results.json --review reports/acr-release-1/review.json --output reports/acr-release-1
```

If the browser downloaded a different filename, use that file instead. Build refreshes
the HTML/ACR JSON but never overwrites the reviewer input. Each worksheet is bound to
the source scan ID and a digest of the entire validated scan. A changed/new scan needs
a fresh worksheet; previous approvals are not silently carried forward.

A Supports/Not Applicable decision that conflicts with recorded violations remains
visible with a conflict warning and exit code 3. It is not accepted as proof of
conformance. Investigate false positives and scope, or fix and retest, before publishing.
Even with every criterion reviewed, this feature keeps the output labeled draft.

## 4 Compare releases

Create a second scan and worksheet in separate release-2 folders, then run:

```powershell
.\launch-agent.cmd acr compare --baseline reports/release-1/results.json --current reports/release-2/results.json --output reports/comparison-1-2
```

To include changes in human conformance assessments, supply both review worksheets:

```powershell
.\launch-agent.cmd acr compare --baseline reports/release-1/results.json --current reports/release-2/results.json --baseline-review reports/acr-release-1/review.json --current-review reports/acr-release-2/review.json --output reports/comparison-1-2
```

The output is `comparison.html` and `comparison.json`. Assessment comparisons require
matching application ID, WCAG target, scope, environment and role. Changing a reviewer
assessment is labeled an assessment change, not an automatically verified fix.

Finding status meanings:

| Status | Meaning |
| --- | --- |
| New | Current issue was absent in comparable baseline rule coverage. |
| Still present | Same rule/source/classification, URL, UI context and target remain affected. |
| Resolved | The exact affected target explicitly passed the rule in comparable complete coverage. |
| Not verified | An older issue cannot be verified as resolved from the available evidence. |
| Newly observed | Current finding lacks sufficiently comparable baseline coverage. |

Repeated observations are grouped with occurrence counts; different targets violating
the same rule remain distinct for comparison. URL fragments and meaningful query values
are retained. Scan-specific state IDs are not used alone to establish equivalence.
Changed selectors, unanchored DOM drift, incomplete scans, changed engine/settings or
missing rule-target pass evidence prevent a resolved classification. This is deliberately
conservative: fixing a control may still require manual verification when its identity
or state changes. No match is invented to produce a better score.

Two scans cannot prove that an issue returned after a previously confirmed fix. A multi-scan
application registry, automatic baseline selection and returned-issue tracking are not
implemented. Preserve release folders for history and select comparison inputs explicitly.

## Sharing and safety

- Review product metadata, screenshots and manual notes for confidential information.
- ZIP the whole export folder to keep copied screenshots available.
- The dashboards have no remote scripts or frameworks. URLs from reports are treated as data.
- Source evidence is copied only from supported image files confined to the source scan folder.
- The original scan report and crawler results are not changed by the ACR commands.
- There is no OpenAI integration or automatic certification in this feature.

## Official references

- [ITI VPAT editions and instructions](https://www.itic.org/policy/accessibility/vpat)
- [Creating an ACR with VPAT](https://www.section508.gov/sell/how-to-create-acr-with-vpat/)
- [W3C evaluation overview and human review requirements](https://www.w3.org/WAI/test-evaluate/)
- [Catalog sources, counts and version-specific notes](wcag-catalog-sources.md)

The catalog metadata was checked on 2026-09-09. Select the edition required by your
customer; this worksheet's WCAG scope must not be presented as coverage of other standards.
