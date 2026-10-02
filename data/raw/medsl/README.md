# MEDSL county presidential returns (hand-downloaded)

`countypres_2000-2024.csv` goes in this folder. It is the MIT Election Data and
Science Lab's *County Presidential Election Returns 2000-2024* (Harvard
Dataverse, doi:10.7910/DVN/VOQCHQ).

## Why it is committed by hand

Dataverse requires a guestbook response (guestbookID 458) before it serves this
file, so the API download in `fetch_county_features.py` is refused with HTTP
400. The fetcher reads this committed copy instead. It still pulls the dataset
metadata from Dataverse on every run, which needs no guestbook, and it:

- checks this file's md5 against the md5 Dataverse publishes, so a stale or
  altered copy fails loudly;
- records the dataset version and license in `data/features/features_manifest.json`.

## How to refresh it

1. Open https://doi.org/10.7910/DVN/VOQCHQ and fill in the guestbook.
2. Download `countypres_2000-2024`. Choose the **original file format (CSV)**,
   not the archival `.tab`.
3. Replace `countypres_2000-2024.csv` here with the download, unedited, and
   commit it.
4. Run **Actions → Fetch county plugin features**, with `only` set to `political`.

MEDSL updates this file about once per election cycle. If the md5 check fails,
a new version has been published: repeat the steps above.
