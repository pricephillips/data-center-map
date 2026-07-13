# Data Quality Report — master_opposition.csv

**Rows processed:** 1659

This pass is **backward compatible**: existing columns keep their names and meanings, values were fixed in place only where the correction is unambiguous, and all new structure was added as additional columns. The HTML map and Notion sync continue to work without modification.

## Changes applied

**1. Unicode/spelling normalization (NFC + known variants)**  
13 cell(s) standardized (e.g. 'Dona Ana' -> 'Doña Ana')

**2. Source URL — stringified Python dicts parsed to bare URLs**  
282 cell(s) repaired

**3. Validation flag: source_url_valid (new column)**  
1659 valid; 0 non-empty but still non-URL (flagged for review)

**4. Sources — backfilled from Source URL where empty**  
289 row(s) now have a populated Sources list (Source URL was confirmed == Sources[0] in 100% of dual-filled rows)

**5. Issue Category — tokens alphabetically sorted & de-duplicated**  
540 cell(s) reordered; distinct combinations 609 -> 446 (eliminated 163 phantom duplicates from ordering)

**6. Boolean issue-category columns (new): 16 added**  
Columns: is_air_quality, is_anti_ai, is_community_impact, is_contract_guarantees, is_design_standards, is_environmental, is_farmland, is_grid_energy, is_noise, is_property_values, is_ratepayer, is_tax_incentive, is_traffic, is_transparency, is_water, is_zoning

**7. Statewide rows — incorrect County + capital-City attribution cleared**  
81 statewide row(s) had County nulled (geocoder assigned the capital's county); 1 had a capital City cleared (neutralizes the gate's STATEWIDE_CAPITAL_SINK block). is_statewide flag set. Coordinates retained; map should render via is_statewide.

**8. Geography backfill from headline (feed now matches what the gate validated)**  
83 blank State value(s) and 48 blank County value(s) recovered from the Incident/Summary text (conservative: blanks only, never overwrites). Removes 'Unknown state' dashboard buckets for real events.

**9. Incident split into location_name + project_descriptor (new columns)**  
649 row(s) had a parenthetical descriptor extracted; Incident left intact for backward compatibility

**10. project_id + project_row_count + is_primary_record (new columns)**  
1473 distinct projects identified; 116 span multiple rows; 29 row(s) unified by manual cross-venue override. Largest clusters: xai_colossus (29), port_washington_wi (9), prince_william_county_va (8), reno_nv (6), fort_worth_tx (4). Heuristic = location_name + state, plus PROJECT_OVERRIDES for cross-venue projects.

**11. Date enrichment: action_year + date_parseable + data_era (new columns)**  
289 unparseable date(s) flagged; 5 row(s) tagged crypto_era_pre2022 (e.g. the lone 2014 Chelan County PUD record) so the two opposition waves can be analyzed separately

**12. Quantitative review flags: mw_review_flag (>3000 MW), investment_review_flag (>$10B) (new columns)**  
7 capacity outlier(s) and 74 investment outlier(s) flagged for unit/scope verification (MW vs GW; phase vs total-campus)

**13. Status normalized: status_clean + status_notes + legislative_stage (new columns)**  
58 raw values -> 13 controlled codes (active, announced, approved, expired, failed, introduced, passed, passed_one_chamber, passed_pending_signature, pending, resolved, unknown, withdrawn); 12 narrative memo(s) preserved in status_notes; 4 legislative stage(s) extracted. Raw Status untouched.

**14. Legislative completion verification: bill_progress + action_complete + outcome_overstated (new columns)**  
256 legislative record(s) staged via the gate's ladder (now reading the Status field too); status_clean corrected on 50 record(s) so committee/one-chamber actions aren't labelled enacted; 4 record(s) flagged outcome_overstated (claims success but only at committee/one chamber — the 'approved ≠ law' trap).

**15. Judgment-assisted classifications (new columns)**  
objective_type: 952/1081 objectives classified (129 left as 'other'); actor_type: 173/174 sponsors classified, party/chamber extracted for legislators; opposition_group_type assigned; opposition_group_verified flags 344/762 named groups as having a website/social presence (418 unverified — the network-analysis follow-up). All are first-pass heuristics; original Objective/Sponsors/Opposition Groups text is preserved.

**16. Capacity/investment scope hints (new columns)**  
capacity_unit flags 7 possible GW-as-MW entries; capacity_scope/investment_scope inferred for 28 rows from text (phase_1 / total_campus), rest 'unknown' — confirm against announcements in the review pass.

**17. Duplicate scan (flagged, not auto-deleted)**  
Same location+state+investment appearing >1x: Doña Ana County/NM @ $165B x2 — review whether these are true duplicates or distinct events on one project (now linked by project_id)

## Recommended next pass (human review of heuristic classifications)

Every item in the original critique is now addressed in the data. What remains is **verification** of the judgment-assisted columns, which were generated by first-pass heuristics and should be spot-checked before they drive client-facing analysis:

- **objective_type** — ~13% remain 'other'. Review those and any borderline legal_challenge / oppose_specific_project calls. Original Objective prose is preserved.
- **actor_type / actor_party / actor_chamber** — <1% 'other'; party/chamber parsed from the sponsor string. Verify multi-sponsor rows (only the primary sponsor is classified).
- **opposition_group_type** — ~21% 'other' (proper-noun group names with no keyword signal). These are the best candidates for manual tagging, and feed the activist-network analysis.
- **opposition_group_verified** — flags which named groups already have a website/social; the unverified ones are the lookup worklist, not an error.
- **capacity_scope / investment_scope** — mostly 'unknown'; only set where text was explicit. Confirm phase-vs-total and the 7 capacity_unit='GW?' rows against primary announcements.
- **Statewide map display** — statewide rows now have null County and cleared capital City (so they pass the gate), but retain coordinates. Decide whether the map renders them as state polygons, a distinct centroid icon, or filters them from the pin layer (suggest a statewide_display_mode in the tracker).

## Row-level change log

495 individual value fixes recorded in `change_log.csv` (columns: row, field, before, after).