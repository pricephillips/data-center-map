# Socrata discovery: wa_sepa

Domain: `data.wa.gov`  
Keywords: sepa, environmental policy, register, ecology  
Score threshold: 2

## Resolved

- **State Environmental Policy Act (SEPA) Register** (`mmcb-z6jf`)
- Query URL: `https://data.wa.gov/resource/mmcb-z6jf.json`
- Columns: `documentsubsubtypecode`, `applicantcontactinfo`, `relatedrecordsdescription`, `commentsduedate`, `countyname`, `documenttypecode`, `applicantname`, `siteparcelnumber`, `sitecityname`, `siteline1address`, `leadagencyissuedate`, `leadagencycontactphonenumber`, `leadagencycontactemail`, `leadagencyfilenumber`, `leadagencyname`, `proposalname`, `sepanumber`, `separegisterid`, `leadagencycontactname`, `proposaldescription`, `documentsubtypecode`, `sitelongitudedecimal`, `sitelatitudedecimal`, `siteline2address`, `leadagencycontactextensi`, `publisheddate`, `sitezipcode`, `sitesectiontownrange`, `proposaltypename`, `regionname`, `separegisterlink`

fetch_permits.py picks this up automatically on the next run. It still needs a column map at `configs/wa_sepa_ingest.json` before anything reaches the dated baseline.

## Candidates

- `mmcb-z6jf` score 4 — State Environmental Policy Act (SEPA) Register
- `pxch-cjvc` score 1 — EPA Agreements Public Register
- `yntp-rvcu` score 1 — EPA Authorisations Public Register
- `h8u2-6ejg` score 1 — Currently Registered Pesticide Businesses and Agencies
- `3if8-4tcz` score 0 — ACT Socio-economic Index for Individuals (SEIFI) 2006
- `jxme-znux` score 0 — Creating Healthy Places Intervention Locations
- `nnrk-hs73` score 0 — ARRA Grant Revenues As Of COB July 31, 2012
- `w32v-gn8m` score 0 — ARRA Grant Expenditures As Of COB July 31, 2012
- `h55x-hu6n` score 0 — Environmental Radiation Surveillance Gamma Radiation Readings: Beginning 1995
- `5hbp-c6bb` score 0 — Lead Testing in School Drinking Water Buildings with Lead-Free Plumbing: Compliance Year 2016
- `64hr-r7jd` score 0 — Housing and Segregation Study - Race/Ethnicity Indices
- `7qtc-b5w8` score 0 — Housing and Segregation Study - All Data
- `msyu-e4n6` score 0 — WA_Contracted_APN
- `nrxy-njbq` score 0 — Caltrans Districts
- `dhyx-e7yg` score 0 — CA Air Districts
