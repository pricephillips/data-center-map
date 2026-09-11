# Proximity analysis
Coordinates: 1445 incidents; dated: 1439.

**Contagion:** 626/1439 incidents (43.5%) arose within 50 miles of an enacted block from the prior 365 days, vs. 43.1% (sd 0.8pp) under a 50-permutation date-shuffled null (z = +0.5; |z| < 2 means no evidence of spatial contagion beyond baseline geography). Rows in contagion_rows.csv.

**Clustering:** median nearest-neighbor distance 3.1 miles; 943/1445 incidents (65%) have another incident within 8 miles.

**Density (incidents per 1,000 sq mi, covered states):** NJ 5.98 (n=44), MD 3.09 (n=30), VA 2.23 (n=88), OH 2.20 (n=90), PA 1.99 (n=89), MI 1.72 (n=97), IN 1.59 (n=57), GA 1.37 (n=79), TN 1.33 (n=55), WI 1.29 (n=70)

_8-mile test scaffold ready: feed group geocodes to group_distance() to reproduce the Axios protester-distance claim against our data._