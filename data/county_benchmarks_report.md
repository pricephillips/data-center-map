# County benchmark reference

Generated 2026-09-24.

Reference profiles for the county comparison layer. Every figure is a summary over the county frame in `data/county_aggregate.csv` joined to the published policy scores. Nothing here is an estimate; these are descriptions of groups that exist.

## Group sizes

| group | counties | description |
| --- | --- | --- |
| `national` | 3,222 | All counties in the frame |
| `restrictive` | 359 | Counties with an enacted restrictive action on record |
| `non_restrictive` | 2,863 | Counties with no enacted restriction on record |
| `dc_present` | 251 | Counties with a data center on record |
| `dc_absent` | 2,971 | Counties with no data center on record |
| `tracked_activity` | 701 | Counties with at least one tracked opposition event |
| `state:XX` | varies | 52 state groups, one per state in the frame |

## The comparison that matters

Median values for the three groups a county page reads against.

| metric | all counties | enacted a restriction | no restriction |
| --- | --- | --- | --- |
| Restriction resemblance score | 0.0706 | 0.1636 | 0.0654 |
| Opposition events | 0 | 1 | 0 |
| Opposition events per 100k residents | 0.000 | 1.809 | 0.000 |
| Data center records in the atlas | 0 | 0 | 0 |
| Population | 25967 | 98592 | 22890 |
| Population density (per sq mi) | 46.58 | 162.66 | 40.55 |
| Median household income | 63162 | 69990 | 62385 |
| Bachelor's degree or higher (%) | 21.53 | 27.66 | 21.05 |
| 2024 presidential margin | -0.4197 | -0.2301 | -0.4405 |

## Peer matching

Each county is matched to its **10** nearest counties on standardised log population, log population density, share with a bachelor's degree, and 2024 presidential margin. **3,115** counties carry all four features and are matchable; the rest are published with an empty peer set rather than an imputed one.

| feature | mean | sd |
| --- | --- | --- |
| `log_population` | 10.2888 | 1.5048 |
| `log_pop_density` | 3.8966 | 1.6553 |
| `pct_bachelors_plus` | 24.1002 | 10.1947 |
| `margin_2024` | -0.3513 | 0.3132 |

A peer set is a similarity group, not a matched control. It supports statements of the form "counties that look like this one restrict at X%". It does not support any statement about what would have happened in this county, and `IDENTIFIABILITY.md` records why no such statement is available from this data.

