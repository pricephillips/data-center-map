# Socrata discovery: wa_sepa

Domain: `data.wa.gov`  
Keywords: sepa, environmental policy, register, ecology  
Score threshold: 2

## Resolved

- **State Environmental Policy Act (SEPA) Register** (`mmcb-z6jf`)
- Query URL: `https://data.wa.gov/resource/mmcb-z6jf.json`
- Columns: `leadagencyfilenumber`, `leadagencycontactphonenumber`, `leadagencyissuedate`, `siteline1address`, `sitecityname`, `siteparcelnumber`, `applicantname`, `documenttypecode`, `applicantcontactinfo`, `relatedrecordsdescription`, `commentsduedate`, `countyname`, `separegisterlink`, `documentsubsubtypecode`, `regionname`, `proposaltypename`, `sitesectiontownrange`, `sitezipcode`, `publisheddate`, `leadagencycontactextensi`, `siteline2address`, `sitelatitudedecimal`, `sitelongitudedecimal`, `documentsubtypecode`, `proposaldescription`, `leadagencycontactname`, `separegisterid`, `sepanumber`, `proposalname`, `leadagencyname`, `leadagencycontactemail`

fetch_permits.py picks this up automatically on the next run. It still needs a column map at `configs/wa_sepa_ingest.json` before anything reaches the dated baseline.

## Candidates

- `mmcb-z6jf` score 4 — State Environmental Policy Act (SEPA) Register
- `pxch-cjvc` score 1 — EPA Agreements Public Register
- `yntp-rvcu` score 1 — EPA Authorisations Public Register
- `jxme-znux` score 0 — Creating Healthy Places Intervention Locations
- `nnrk-hs73` score 0 — ARRA Grant Revenues As Of COB July 31, 2012
- `w32v-gn8m` score 0 — ARRA Grant Expenditures As Of COB July 31, 2012
- `t6hf-u79n` score 0 — ACT Police Station Locations
- `h55x-hu6n` score 0 — Environmental Radiation Surveillance Gamma Radiation Readings: Beginning 1995
- `5hbp-c6bb` score 0 — Lead Testing in School Drinking Water Buildings with Lead-Free Plumbing: Compliance Year 2016
- `64hr-r7jd` score 0 — Housing and Segregation Study - Race/Ethnicity Indices
- `7qtc-b5w8` score 0 — Housing and Segregation Study - All Data
- `7bgc-7sg9` score 0 — Metropolitan Planning Organizations
- `nrxy-njbq` score 0 — Caltrans Districts
- `dhyx-e7yg` score 0 — CA Air Districts
- `dapt-ejhb` score 0 — Waste Tire Abatement Sites
