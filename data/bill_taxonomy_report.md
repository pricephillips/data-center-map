# Bill taxonomy

Generated 2026-09-15.

Every bill this repository has matched, classified on two axes: how the law reaches data centers, and what it does to them. Reach decides whether a roll call on the bill may carry a stance; only `data_center_specific` and `large_load_class` may.

## Reach

| reach | bills | stance? | what it means |
| --- | --- | --- | --- |
| `data_center_specific` | 61 | yes | the title names data centers. A vote is a position. |
| `large_load_class` | 13 | yes | the title binds a load or size class data centers dominate. A vote is a position on how that class is treated. |
| `sector_vehicle` | 28 | no | the data center content is established by the incident that put the bill here, not by the title. Votes publish with NO stance. |
| `lookup_suspect` | 3 | no | the incident says data centers and the fetched title is about something else entirely. A wrong bill, not a gap. |
| `unestablished` | 69 | no | nothing establishes reach. Withheld. |

## Instrument

| instrument | direction | bills |
| --- | --- | --- |
| `unclassified` | `—` | 63 |
| `moratorium_prohibition` | `restrictive` | 26 |
| `ratepayer_cost_allocation` | `restrictive` | 20 |
| `disclosure_reporting` | `disclosure` | 20 |
| `incentive_repeal` | `restrictive` | 14 |
| `siting_zoning` | `restrictive` | 12 |
| `incentive_grant` | `enabling` | 7 |
| `supply_enablement` | `enabling` | 6 |
| `water_environmental` | `restrictive` | 4 |
| `local_control` | `restrictive` | 2 |

## Bills to repair (3)

The incident names the bill and says data centers, and the title that came back is about something else. These are not coverage gaps: the identifier resolved against the wrong bill, most often because a state reuses short numbers across sessions or uses a compound numbering the lookup flattened. Repair belongs in `bill_sync.py`, not here.

| state | bill | title that came back | domain | in frame via |
| --- | --- | --- | --- | --- |
| CO | SB 26 | Weight for Vehicles with Child Restraint System | `vehicles_transport` | Ban data center development NDAs via SB 26 |
| MN | HF 16 | Immigration law enforcement noncooperation ordinances and po | `immigration` | Eliminate data center electricity tax exemption statewide (H |
| TX | SB 6 | Relating to abortion, including civil liability for the manu | `health` | Implement SB 6 data center grid rules via PUCT rulemaking |

## Bills to re-fetch (59)

These have no title at all: the Open States lookup came back `http_429` or `not_found`, so there is nothing to classify against. That is a spent API quota rather than a coverage judgement, and re-running `bill_sync.py` with quota available resolves it without anyone deciding anything. They are listed separately from the honest misses for exactly that reason.

| state | bill | lookup | in frame via |
| --- | --- | --- | --- |
| AL | SB 265 | `http_429` | Limit data center tax abatements to 20 years and require 100MW+ sales  |
| AL | SB 354 | `http_429` | Impose one-year moratorium on large-scale solar facilities tied to dat |
| AZ | HB 2631 | `http_429` | Repeal Arizona data center transaction privilege and use tax exemption |
| CA | AB 2619 | `http_429` | Mandate data center water usage disclosure in CA |
| CA | SB 887 | `http_429` | Remove CEQA exemptions and mandate 100% zero-carbon electricity for da |
| CA | SB 978 | `http_429` | Shield residential ratepayers from data center costs in CA |
| CO | HB 1030 | `http_429` | Offer 100% sales tax exemption for data centers investing $250M+ in Co |
| CT | HB 5469 | `http_429` | Codify bring-your-own-power mandate for data centers in Connecticut |
| DC | HR 6984 | `not_found` | Require EPA quarterly water reporting and EIA biannual energy reportin |
| DE | SB 205 | `http_429` | Require PSC operating certificate for 30MW+ facilities in Delaware |
| FL | SB 1517 | `http_429` | Require data center developers to disclose projected energy, water, em |
| GA | HB 559 | `http_429` | Accelerate data center tax exemption sunset (HB 559) |
| GA | SB 408 | `http_429` | Accelerate data center tax exemption sunset (HB 559) |
| ID | HB 609 | `http_429` | Reduce data center tax exemptions via HB 609 |
| ID | HB 756 | `http_429` | Reduce data center tax exemptions via HB 609 |
| ID | HB 874 | `http_429` | Prevent data center cost pass-through to residential rates in ID |
| ID | HB 895 | `http_429` | Prohibit consumptive water cooling for new Idaho data centers unless u |
| IN | HB 1245 | `http_429` | Halt data centers in Indiana after Meta proposal |
| KY | HB 544 | `http_429` | Ban data center development NDAs via HB 593 |
| KY | HB 593 | `http_429` | Ban data center development NDAs via HB 593 |
| MD | HB 1411 | `http_429` | Mandate public environmental reporting from data centers (HB 1411) |
| MD | HB 1595 | `http_429` | Reform data center property tax rules in MD |
| MD | SB 427 | `http_429` | Reform data center property tax rules in MD |
| MI | HB 5396 | `http_429` | Repeal Michigan's data center tax exemptions and press for a statewide |
| MI | SB 761 | `http_429` | Ban data center water withdrawals over 2M gallons/day (SB 761-763) |
| MI | SB 762 | `http_429` | Ban data center water withdrawals over 2M gallons/day (SB 761-763) |
| MI | SB 763 | `http_429` | Ban data center water withdrawals over 2M gallons/day (SB 761-763) |
| MN | HF 4077 | `http_429` | Ban data center development NDAs via SF 4298 |
| MN | SF 4298 | `http_429` | Statewide moratorium on data center permits until PUC completes compre |
| MN | SF 4379 | `http_429` | Ban data center development NDAs via SF 4298 |
| MO | HB 3362 | `http_429` | Prevent data center cost pass-through to residential rates in MO |
| MO | HB 3364 | `http_429` | Prevent data center cost pass-through to residential rates in MO |
| MS | SB 3168 | `http_429` | Create data center tax incentive program in MS |
| MT | SB 212 | `http_429` | Establish right to compute, limiting local data center regulation |
| NH | SB 439 | `http_429` | Enable NH local planning boards to adopt data center zoning regulation |
| NJ | A 2757 | `http_429` | Redirect uncommitted NJ AI/data-center tax credits to other priorities |
| NJ | A 5165 | `http_429` | Redirect uncommitted NJ AI/data-center tax credits to other priorities |
| NJ | S 1673 | `http_429` | Redirect uncommitted NJ AI/data-center tax credits to other priorities |
| NJ | S 4390 | `http_429` | Redirect uncommitted NJ AI/data-center tax credits to other priorities |
| NJ | S 4411 | `http_429` | Redirect uncommitted NJ AI/data-center tax credits to other priorities |
| NJ | S 680 | `http_429` | Require NJ data centers to source 100% of electricity from new clean o |
| NY | A 11560 | `http_429` | Pause state environmental permits for new hyperscale data centers for  |
| NY | S 10642 | `http_429` | Pause state environmental permits for new hyperscale data centers for  |
| OH | HB 706 | `http_429` | Repeal data center tax exemptions via HB 706 |
| OH | SR 37 | `http_429` | Block Amazon/AWS rezoning of 330 acres in Sunbury |
| OK | HB 299 | `not_found` | Ban NDAs used by city officials when negotiating with data center deve |
| OK | SB 1488 | `http_429` | Pause data center permits for study via SB 1488 |
| OR | SB 1586 | `http_429` | Block SB 1586 from adding 373 acres of farmland to Hillsboro's urban g |
| PA | HB 1834 | `http_429` | Pass Pennsylvania's first data center regulatory framework via HB 1834 |
| PA | SB 1345 | `http_429` | Give PA municipalities the option to impose an 18-month moratorium on  |
| SC | HB 4583 | `not_found` | Ban data center development NDAs in SC |
| SD | HB 1005 | `http_429` | Shield ratepayers from data center costs via HB 1005 |
| SD | SB 232 | `http_429` | Enact statewide data center moratorium in SD |
| TN | HB 2047 | `http_429` | Require data center owners to pay full infrastructure costs in TN |
| TN | SB 2584 | `http_429` | Require data center owners to pay full infrastructure costs in TN |
| VA | HB 153 | `http_429` | Require site impact assessments for 100MW+ data centers near farms, pa |
| VA | HB 496 | `http_429` | Mandate data center water usage disclosure in VA |
| VA | HB 784 | `http_429` | Mandate transparency and reporting from data centers in VA |
| VA | SB 553 | `http_429` | Mandate data center water usage disclosure in VA |

## Truncated titles (10)

`bill_sync.py` stores the first 160 characters of a title. A title at that cap may have been cut mid-subject, so its silence is not evidence: these fall through to the incident rather than being classified `unestablished` on the title alone. West Virginia HB 4983 is the case that made this necessary -- its stored title ends at "certification as a high i", one word before "impact data center".

## Every classified bill

| state | bill | reach | basis | instrument | direction | stance | title |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AK | SB 250 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | An Act relating to data centers; and relating to utilit |
| AL | SB 270 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Electric Utilities; review by Public Service Commission |
| AZ | HB 2452 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | comprehensive plans; data centers; nuclear |
| AZ | HB 4009 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | data centers; state lands; map |
| AZ | SB 1463 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | data centers; tax relief; repeal.. |
| CA | AB 1577 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | Data centers: reporting. |
| CA | AB 222 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Data centers: power usage effectiveness: cost shifts. |
| CA | SB 1168 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Data centers: rate structures. |
| CA | SB 57 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | Electrical corporations: data centers: report. |
| CT | SB 245 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | AN ACT ELIMINATING CERTAIN TAX INCENTIVES FOR DATA CENT |
| FL | HB 1007 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Data Centers |
| FL | SB 1118 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Public Records/Data Centers |
| FL | SB 484 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Data Centers |
| GA | HB 1012 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Local government; construction or development of  new d |
| GA | HB 1059 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Data Center Impact Assessment and Development Moratoriu |
| GA | HB 1063 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Electric utilities; protect residential and retail elec |
| GA | SB 34 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | Public Service Commission; costs incurred by an electri |
| GA | SB 410 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | State Sales and Use Taxes; the data center equipment sa |
| IA | HF 2447 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | A bill for an act relating to water and energy use for  |
| IA | HF 2690 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | A bill for an act relating to water and energy use for  |
| IL | SB 4016 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | HYPERSCALE DATA CENTERS |
| KS | SB 526 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Requiring data centers to be located on land that was z |
| KS | SB 98 | `data_center_specific` | `title_term` | `incentive_grant` | `enabling` | yes | Providing a sales tax exemption for the construction or |
| LA | HB 827 | `data_center_specific` | `title_term` | `incentive_grant` | `enabling` | yes | TAX/TAX REBATES: Provides relative to sales and use tax |
| MD | HB 120 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Moratorium on Construction of New Data Centers - Co-Loc |
| MD | HB 560 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | Sales and Use Tax and Property Tax - Exemptions for Dat |
| MI | HB 5594 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Businesses: other; moratorium on certain approvals for  |
| NE | LB 1111 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | Require an annual data center load report to the Nebras |
| NH | HB 1265 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | prohibiting the construction of data centers in the sta |
| NJ | A 796 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Requires electric public utilities to develop and apply |
| NJ | S 3379 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Requires data center owners and operators to submit sem |
| NY | A 10141 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Imposes a moratorium on data center permit issuance; an |
| NY | S 8540 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Establishes the "accountability of costs for data cente |
| NY | S 9144 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Imposes a moratorium on data center permit issuance; an |
| OH | HB 646 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | Create the Data Center Study Commission |
| OK | HB 2992 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Corporation Commission; creating the Data Center Custom |
| PA | HB 2150 | `data_center_specific` | `title_term` | `water_environmental` | `restrictive` | yes | An Act providing for annual reporting of energy consump |
| PA | HB 2151 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | An Act amending Title 53 (Municipalities Generally) of  |
| SD | HB 1038 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | allow the Public Utilities Commission to assess actual  |
| SD | SB 135 | `data_center_specific` | `title_term` | `ratepayer_cost_allocation` | `restrictive` | yes | protect residents from increased utility costs and util |
| TN | HB 2392 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Business and Commerce - As introduced, creates the "Ten |
| TN | SB 1999 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Energy - As introduced, enacts the "Data Center, Artifi |
| TN | SB 2653 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Business and Commerce - As introduced, creates the "Ten |
| UT | HB 76 | `data_center_specific` | `title_term` | `disclosure_reporting` | `disclosure` | yes | Data Center Water Transparency Amendments |
| VA | HB 1515 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Local approval of data centers; temporary moratorium. |
| VA | HB 503 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Electric utilities; cost recovery, costs substantially  |
| VA | HB 507 | `data_center_specific` | `title_term` | `siting_zoning` | `restrictive` | yes | Data centers; permit requirements, emission limits for  |
| VA | HB 641 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Virginia's Great Outdoors Act; established, creates dat |
| VA | SB 393 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Virginia's Great Outdoors Act; established, creates dat |
| VA | SB 94 | `data_center_specific` | `title_term` | `water_environmental` | `restrictive` | yes | Data centers; site assessment, sound profile of the hig |
| WA | SB 6231 | `data_center_specific` | `title_term` | `incentive_repeal` | `restrictive` | yes | Removing a tax exemption for the replacement of equipme |
| WI | AB 1099 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Relating to: moratorium on data centers. |
| WI | AB 722 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Relating to: large energy customer fees; electric utili |
| WI | AB 840 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Relating to: certain requirements related to data cente |
| WI | SB 1061 | `data_center_specific` | `title_term` | `moratorium_prohibition` | `restrictive` | yes | Relating to: moratorium on data centers. |
| WI | SB 729 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Relating to: large energy customer fees; electric utili |
| WI | SB 843 | `data_center_specific` | `title_term` | `unclassified` | `unclassified` | yes | Relating to: certain requirements related to data cente |
| WV | HB 4983 | `data_center_specific` | `title_term` | `supply_enablement` | `enabling` | yes | Authorizing the Department of Commerce to promulgate a  |
| WV | HB 5590 | `data_center_specific` | `title_term` | `water_environmental` | `restrictive` | yes | Create a limited and predictable water-quantity review  |
| WV | HB 5620 | `data_center_specific` | `title_term` | `local_control` | `restrictive` | yes | To restore local control in the decision making process |
| WV | SB 658 | `data_center_specific` | `title_term` | `supply_enablement` | `enabling` | yes | Relating to certified microgrid district projects and c |
| AK | HB 259 | `large_load_class` | `title_class_term` | `unclassified` | `unclassified` | yes | An Act relating to large energy use facilities; relatin |
| AZ | HB 2756 | `large_load_class` | `title_class_term` | `ratepayer_cost_allocation` | `restrictive` | yes | utilities; high load factor customers |
| DE | HB 445 | `large_load_class` | `title_class_term` | `ratepayer_cost_allocation` | `restrictive` | yes | AN ACT TO AMEND TITLE 26 AND TITLE 29 OF THE DELAWARE C |
| IA | HF 2261 | `large_load_class` | `title_class_term` | `disclosure_reporting` | `disclosure` | yes | A bill for an act relating to customer classes for larg |
| MD | SB 596 | `large_load_class` | `title_class_term` | `unclassified` | `unclassified` | yes | Large Load Customers - Electric System Interconnection  |
| ND | HB 1579 | `large_load_class` | `title_class_term` | `disclosure_reporting` | `disclosure` | yes | AN ACT to provide for a legislative management study re |
| NJ | A 5462 | `large_load_class` | `title_class_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Requires electric public utilities to develop and apply |
| NJ | S 731 | `large_load_class` | `title_class_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Requires electric public utilities to develop and apply |
| OR | HB 3546 | `large_load_class` | `title_class_term` | `unclassified` | `unclassified` | yes | Relating to large energy use facilities; and declaring  |
| PA | HB 2496 | `large_load_class` | `human_override` | `unclassified` | `unclassified` | yes | An Act amending the act of July 31, 1968 (P.L.805, No.2 |
| VA | HB 155 | `large_load_class` | `title_class_term` | `siting_zoning` | `restrictive` | yes | Electric utilities; certificate of operation for high-l |
| VA | SB 619 | `large_load_class` | `title_class_term` | `siting_zoning` | `restrictive` | yes | Electric utilities; certificate of operation for high-l |
| WA | HB 2515 | `large_load_class` | `title_class_term` | `ratepayer_cost_allocation` | `restrictive` | yes | Addressing emerging large energy use facilities. |
| AL | HB 399 | `sector_vehicle` | `incident_corroborated:taxation` | `unclassified` | `unclassified` | no | Tax abatements for data processing centers, exemption p |
| AZ | HB 2457 | `sector_vehicle` | `incident_corroborated:energy_utility` | `supply_enablement` | `unclassified` | no | public utilities; plant construction; colocation |
| AZ | HB 2795 | `sector_vehicle` | `incident_corroborated:energy_utility` | `siting_zoning` | `unclassified` | no | small modular reactors; zoning; approval |
| DE | HB 233 | `sector_vehicle` | `incident_corroborated:taxation` | `ratepayer_cost_allocation` | `unclassified` | no | AN ACT TO AMEND TITLE 30 OF THE DELAWARE CODE RELATING  |
| DE | SB 326 | `sector_vehicle` | `incident_corroborated:energy_utility` | `ratepayer_cost_allocation` | `unclassified` | no | AN ACT TO AMEND TITLE 26 OF THE DELAWARE CODE RELATING  |
| GA | SB 436 | `sector_vehicle` | `incident_corroborated:energy_utility` | `local_control` | `unclassified` | no | Counties, Municipal Corporations; local governments and |
| IN | HB 1210 | `sector_vehicle` | `incident_corroborated:land_use` | `unclassified` | `unclassified` | no | Department of local government finance. |
| IN | HB 1333 | `sector_vehicle` | `incident_corroborated:land_use` | `incentive_repeal` | `unclassified` | no | Land use and development. |
| KY | SB 197 | `sector_vehicle` | `incident_corroborated:taxation` | `disclosure_reporting` | `unclassified` | no | AN ACT providing funding and establishing conditions fo |
| MD | HB 1532 | `sector_vehicle` | `incident_corroborated:energy_utility` | `unclassified` | `unclassified` | no | Utility RELIEF (Reducing Energy Load Inflation for Ever |
| ME | LD 307 | `sector_vehicle` | `incident_corroborated:energy_utility` | `incentive_grant` | `unclassified` | no | An Act Regarding Energy, Utilities And Technology |
| ME | LD 713 | `sector_vehicle` | `incident_corroborated:taxation` | `incentive_repeal` | `unclassified` | no | An Act Regarding Taxation |
| MS | HB 1635 | `sector_vehicle` | `incident_corroborated:taxation` | `unclassified` | `unclassified` | no | Fee-in-lieu of ad valorem tax; revise distribution of f |
| NC | HB 1002 | `sector_vehicle` | `incident_corroborated:energy_utility` | `ratepayer_cost_allocation` | `unclassified` | no | Rate Payer Protection Act. |
| NC | SB 730 | `sector_vehicle` | `human_override` | `ratepayer_cost_allocation` | `unclassified` | no | Ratepayer Protection Act. |
| NE | LB 1261 | `sector_vehicle` | `incident_corroborated:energy_utility` | `moratorium_prohibition` | `unclassified` | no | Prohibit the use of eminent domain to acquire certain p |
| NE | LB 209 | `sector_vehicle` | `incident_corroborated:taxation` | `incentive_grant` | `unclassified` | no | Change provisions relating to homestead exemptions for  |
| NH | HB 1724 | `sector_vehicle` | `incident_corroborated:energy_utility` | `disclosure_reporting` | `unclassified` | no | relative to public transparency of electric utility ret |
| NM | SB 235 | `sector_vehicle` | `incident_corroborated:energy_utility` | `supply_enablement` | `unclassified` | no | MICROGRID OVERSIGHT ACT |
| OH | HB 96 | `sector_vehicle` | `incident_corroborated:taxation` | `incentive_repeal` | `unclassified` | no | Make state operating appropriations for FY 2026-27 |
| OR | HB 4084 | `sector_vehicle` | `incident_corroborated:economic_development` | `incentive_repeal` | `unclassified` | no | Relating to economic development; and prescribing an ef |
| TN | HB 1847 | `sector_vehicle` | `incident_corroborated:energy_utility` | `moratorium_prohibition` | `unclassified` | no | Computers and Electronic Processing - As enacted, gener |
| TN | SB 2128 | `sector_vehicle` | `incident_corroborated:energy_utility` | `moratorium_prohibition` | `unclassified` | no | Computers and Electronic Processing - As enacted, gener |
| VA | HB 1393 | `sector_vehicle` | `incident_corroborated:energy_utility` | `unclassified` | `unclassified` | no | Electric utilities; pilot program for energy assistance |
| VA | SB 253 | `sector_vehicle` | `incident_corroborated:energy_utility` | `unclassified` | `unclassified` | no | Electric utilities; pilot programs for energy assistanc |
| WA | SB 5982 | `sector_vehicle` | `incident_corroborated:energy_utility` | `ratepayer_cost_allocation` | `unclassified` | no | Updating provisions for consumer-owned utilities, inclu |
| WV | HB 2014 | `sector_vehicle` | `incident_corroborated:energy_utility` | `supply_enablement` | `unclassified` | no | Certified Microgrid Program |
| WY | HB 90 | `sector_vehicle` | `incident_corroborated:environment_water` | `water_environmental` | `unclassified` | no | State engineer-consumptive water use study. |
| CO | SB 26 | `lookup_suspect` | `title_domain_incompatible` | `unclassified` | `unclassified` | no | Weight for Vehicles with Child Restraint System |
| MN | HF 16 | `lookup_suspect` | `title_domain_incompatible` | `moratorium_prohibition` | `unclassified` | no | Immigration law enforcement noncooperation ordinances a |
| TX | SB 6 | `lookup_suspect` | `title_domain_incompatible` | `unclassified` | `unclassified` | no | Relating to abortion, including civil liability for the |
| AL | SB 265 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| AL | SB 354 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| AZ | HB 2631 | `unestablished` | `title_unavailable` | `incentive_repeal` | `unclassified` | no |  |
| CA | AB 2619 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| CA | SB 887 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| CA | SB 978 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| CO | HB 1030 | `unestablished` | `title_unavailable` | `incentive_grant` | `unclassified` | no |  |
| CO | HB 26 | `unestablished` | `incident_names_another_bill` | `unclassified` | `unclassified` | no |  |
| CO | SB 24 | `unestablished` | `incident_names_another_bill` | `incentive_grant` | `unclassified` | no |  |
| CT | HB 5469 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| DC | HR 6984 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| DE | SB 205 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| FL | HB 1517 | `unestablished` | `incident_names_another_bill` | `disclosure_reporting` | `unclassified` | no |  |
| FL | SB 1517 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| GA | HB 559 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| GA | SB 408 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| ID | HB 609 | `unestablished` | `title_unavailable` | `incentive_repeal` | `unclassified` | no |  |
| ID | HB 756 | `unestablished` | `title_unavailable` | `incentive_repeal` | `unclassified` | no |  |
| ID | HB 874 | `unestablished` | `title_unavailable` | `ratepayer_cost_allocation` | `unclassified` | no |  |
| ID | HB 895 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| IL | SB 2182 | `unestablished` | `title_domain_unrecognised` | `disclosure_reporting` | `unclassified` | no | FACILITY FEE TRANSPARENCY |
| IL | SB 4004 | `unestablished` | `title_domain_unrecognised` | `unclassified` | `unclassified` | no | AQUIFER PROTECTION ACT |
| IN | HB 1245 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| KY | HB 544 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| KY | HB 593 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MD | HB 1411 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| MD | HB 1595 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MD | SB 427 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MI | HB 5396 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| MI | SB 1018 | `unestablished` | `incident_names_another_bill` | `moratorium_prohibition` | `unclassified` | no |  |
| MI | SB 761 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MI | SB 762 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MI | SB 763 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MN | HF 4077 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MN | SF 4298 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| MN | SF 4379 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| MO | HB 3362 | `unestablished` | `title_unavailable` | `ratepayer_cost_allocation` | `unclassified` | no |  |
| MO | HB 3364 | `unestablished` | `title_unavailable` | `ratepayer_cost_allocation` | `unclassified` | no |  |
| MS | SB 3168 | `unestablished` | `title_unavailable` | `incentive_grant` | `unclassified` | no |  |
| MT | SB 212 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NC | HB 7971 | `unestablished` | `incident_silent` | `unclassified` | `unclassified` | no |  |
| NH | SB 439 | `unestablished` | `title_unavailable` | `siting_zoning` | `unclassified` | no |  |
| NJ | A 2757 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NJ | A 5165 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NJ | A 6181 | `unestablished` | `incident_names_another_bill` | `unclassified` | `unclassified` | no |  |
| NJ | S 1673 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NJ | S 4390 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NJ | S 4411 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NJ | S 680 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| NY | A 11560 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| NY | S 10642 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| OH | HB 706 | `unestablished` | `title_unavailable` | `incentive_repeal` | `unclassified` | no |  |
| OH | SR 37 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| OK | HB 299 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| OK | SB 1488 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| OR | HR 655 | `unestablished` | `incident_silent` | `unclassified` | `unclassified` | no |  |
| OR | SB 1586 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| PA | HB 1834 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| PA | SB 1345 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| SC | HB 4583 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| SD | HB 1005 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| SD | SB 232 | `unestablished` | `title_unavailable` | `moratorium_prohibition` | `unclassified` | no |  |
| TN | HB 2047 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| TN | SB 2 | `unestablished` | `incident_names_another_bill` | `supply_enablement` | `unclassified` | no | Taxes - As introduced, enacts the "End the Grocery Tax  |
| TN | SB 2584 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| VA | HB 153 | `unestablished` | `title_unavailable` | `unclassified` | `unclassified` | no |  |
| VA | HB 496 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| VA | HB 784 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |
| VA | SB 553 | `unestablished` | `title_unavailable` | `disclosure_reporting` | `unclassified` | no |  |

