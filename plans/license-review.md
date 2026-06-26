# Squishy-2026 — license & redistribution review

**We're not lawyers, but we reviewed the licenses.** Owner diligence, not legal
advice. Method: four parallel reviews, each fetching and quoting the *actual*
upstream license/terms pages (not from memory). Verified against the files on disk.

## Verdicts & remediation

| File | Verdict | Finding | Action |
|------|---------|---------|--------|
| **photo** | ✅ CLEAR | Confirmed the *exact* Wikimedia file is the NASA original (AS17-148-22727), tagged `PD-USGov-NASA` — **not** a CC-BY restorer version | none |
| **genome** | ✅ CLEAR | ENA/INSDC "free and unrestricted"; E. coli (no human-subject/PII) | none |
| **log** | ✅ CLEAR | NASA-HTTP: ITA states "may be freely redistributed"; 1995 host data = negligible PII | attribution in NOTICE ✓ |
| **weights** | ✅ CLEAR | Apache-2.0 confirmed on the live model card | shipped `LICENSES/Apache-2.0.txt` ✓ |
| **aozora** | ✅ CLEAR | Author 000148 = Natsume Sōseki (1867–1916) → PD in JP+US; no translations among the 117 works | spot-check colophons removed (minor) |
| **csv** | ✅ CLEAR | **Swapped off NYC TLC** → NOAA GHCN-Daily 2024 weather observations = U.S. Government public domain (NOAA/NCEI); no personal data | source + sha256 in manifest ✓ |
| **sqlite** | ✅ CLEAR | **Swapped off NYC TLC** → USDA FoodData Central SR Legacy = U.S. Government public domain (USDA); no personal data | source + sha256 in manifest ✓ |
| **parquet** | ⚠️→OK | NYC TLC license is soft (generic NYC ToS, no explicit grant) but PII-clean (2024 zone-only schema: no medallion/driver/GPS). Only remaining NYC-TLC file after the csv/sqlite swap | attribution + rights note in NOTICE ✓ |
| **dickens** | ✅ FIXED | Shipped the full **Project Gutenberg trademark + "Small Print" royalty** boilerplate | **stripped all PG references** → bare PD Dickens (PG policy blesses this); re-derived, re-uploaded ✓ |
| **monorepo** | ✅ FIXED | Apache-2.0 §4(a) violation — source tar had **no LICENSE.TXT** | **added LLVM `LICENSE.TXT` into the tar** + `LICENSES/` ✓ |
| **minjs** | ✅ FIXED | In-file MIT banner OK for Plotly code, but bundled-dep notices (`LICENSE.txt`) were missing | **shipped `LICENSES/plotly-2.27.0.LICENSE.txt`** ✓ |
| **movie** | ✅ FIXED | CC-BY 3.0 — requires attribution; excerpt/derivative is permitted | **added required attribution string to NOTICE** ✓ |
| **json** | ✅ FIXED (replaced) | GH Archive: **no license** + **2,788 real committer emails** (GDPR vs frozen DOI) | **replaced with USGS earthquake catalog GeoJSON** (U.S. Govt public domain, no PII) ✓ |

## Update — 2 of the 3 RISKY now resolved (identity-preserving fixes)

- **`markup` — FIXED.** Replaced the unlicensed Silesia "xml" with **Jon Bosak's
  Shakespeare XML** (`shaks200`, ibiblio.org/bosak) — freely distributable, same
  "XML markup" regime, member name preserved. Validated, re-uploaded; stale
  `draft/corpus/xml` removed.
- **`exe` — FIXED.** Kept the Hugo binary (member preserved)
  and shipped `LICENSES/tool.bin.THIRD-PARTY.txt` enumerating all 106 embedded
  modules. Confirmed an **MPL-2.0 component** (`hashicorp/golang-lru`); used
  unmodified, source available upstream → documented; MPL §3 is satisfied by
  upstream source availability.

**Only `mail` remains an owner decision** (it can't be made clean while staying
"real email"):

## Outstanding — owner decision required (1 item)

1. **`mail` (Apache `users@httpd` mbox) — RISKY. Recommend DROP or replace.**
   ASF declares list content "public without conditions" but grants **no copyright
   license** (posters retain copyright), and the file contains **142 real personal
   email addresses + names + bodies**. A frozen-forever DOI conflicts with GDPR
   right-to-erasure. Anonymizing destroys its purpose (it'd no longer be "real
   mail"). **Options:** (a) drop → 15-file core; (b) replace with a clean
   "messy/mixed text" regime (e.g. a public-domain letters collection, or IETF
   RFCs under the IETF Trust license — no living-person PII).

2. **`exe` (Hugo v0.162.1 binary) — RISKY. Recommend replace or bundle.**
   Apache-2.0 itself is fine, but a compiled Go binary statically links **hundreds
   of transitive deps** (MIT/BSD/Apache/**MPL-2.0** — MPL adds source-availability
   obligations). Faithfully bundling + freezing every dependency notice for 20
   years is fragile. **Options:** (a) replace with a binary that ships a
   consolidated third-party license (or one I build from a single permissive
   source I control); (b) keep Hugo + ship the full v0.162.1 third-party license
   bundle (incl. MPL source pointer).

3. **`markup` (Silesia "xml") — RISKY. Recommend rebuild or get permission.**
   The Silesia corpus has **no explicit license** (only "freely available" by
   custom). Content is de-risked (it's Jon Bosak's freely-distributable
   Shakespeare XML + W3C specs + protein/stats data — **not** the license-encumbered
   Penn Treebank/WSJ). But distributing an unlicensed third-party compilation in a
   permanent public dataset is the weakest link. **Options:** (a) rebuild an
   equivalent XML file from clearly-licensed sources (Bosak Shakespeare + W3C specs
   under the W3C Document License) — I can do this; (b) obtain written permission
   from S. Deorowicz / J. Cheney.

## Cross-cutting

- **Bundle licenses, don't rely on URLs.** `license_url`s rot over 20 years; the
  full texts now live in `build/meta/LICENSES/` and travel in the release. (Done
  for Apache-2.0, LLVM, Plotly deps; extend as needed.)
- **NOTICE file** (`build/meta/NOTICE`) carries every attribution that must travel
  with the data (esp. the CC-BY `movie` string). Ships in the release.
- **Soft data licenses** (NYC TLC; previously GH Archive — now removed): where a
  source has no explicit open license but is published as open data, we attribute
  + document it, rather than assert a license we weren't granted.

## Bottom line

The core is now 15 files (`mail` dropped; `markup` rebuilt from Bosak's
freely-distributable Shakespeare XML). **13 of 15 are clear or fixed.** After the
NYC-TLC decoupling (`csv` → NOAA weather PD, `sqlite` → USDA nutrition PD), only
**2 items were owner judgment calls** (both since resolved):

1. **`parquet`** — was the one remaining NYC-TLC file (soft license: open data, no
   explicit grant; PII-clean zone-only schema). Later swapped to BTS On-Time, a
   U.S. Government public-domain source.
2. **`exe`** — Hugo binary, Apache-2.0, statically links an MPL-2.0 module; MPL §3
   is met by upstream source availability (documented in the third-party bundle).

Either could also be eliminated outright by swapping to an unambiguously
permissive source.
