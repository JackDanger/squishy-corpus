### A Proposed Redesign: The Algorithmic Redundancy Taxonomy

 To incentivize balanced engineering, we group the 13 scored kinds into four distinct, equally-weighted ($25%$ each) categories based on how they must be
 compressed.

 The incompressible media files (photo, movie, weights) are stripped of category status entirely and moved to a dedicated, unscored Performance &
 Throughput Diagnostic Tier.

 ```
                              SQUISHY SCORE (100%)
                                       │
          ┌────────────────────┬───────┴────────────┬───────────────────┐
          ▼                    ▼                    ▼                   ▼
   Local Context (25%)   Syntax & Schema (25%)   DB & Columnar (25%)  Dense Non-Text (25%)
    ├── dickens (12.5%)   ├── monorepo (5.0%)     ├── csv (8.3%)       ├── exe (12.5%)
    └── aozora (12.5%)    ├── minjs (5.0%)        ├── parquet (8.3%)   └── genome (12.5%)
                          ├── markup (5.0%)       └── sqlite (8.3%)
                          ├── json (5.0%)
                          └── log (5.0%)
 ```

 ────────────────────────────────────────────────────────────────────────────────

 ### Category 1: Local Context & Prose (25% Weight)

 - The Files: dickens (and large prose), aozora (Japanese prose).
 - The Compression Challenge: Classic natural language. These files feature high-frequency local repetitions, varying vocabularies, and distinct
   byte-frequency distributions (Markov chains of characters).
 - What it rewards: Highly optimized sliding-window Lempel-Ziv parsing, dynamic Huffman or Range/Arithmetic coding, and adaptive entropy modeling.

 ### Category 2: Syntax & Structured Schemas (25% Weight)

 - The Files: monorepo (and large source archive), minjs, markup (XML), json, log (and large server log).
 - The Compression Challenge: Highly repetitive structural syntax (brackets, quotes, repetitive tags, recurring JSON keys, schema names, and IP/date
   prefixes in logs).
 - What it rewards: Mid-to-long range match-finding, nested parser structures, and the ability to find massive repeating substrings (e.g., duplicated
   source code files hundreds of megabytes apart in the archive).

 ### Category 3: Databases & Columnar Stride (25% Weight)

 - The Files: csv (and large CSV), parquet (and large Parquet), sqlite.
 - The Compression Challenge: Tabular data has rigid structural strides. Values repeat at predictable $N$-byte boundaries (columns). Parquet introduces
   dense columnar blocks, while SQLite introduces B-Tree page layouts and zero-padded empty space.
 - What it rewards: Delta/delta-of-delta encoding, transpose-like stride modeling, and running over numeric-heavy tabular data without inflating the
   output.

 ### Category 4: Dense Non-Text Bitstreams (25% Weight)

 - The Files: exe (machine binaries), genome (FASTQ reads, and large genome).
 - The Compression Challenge: These files are notoriously hostile to standard "text-optimized" compressors.
     - exe consists of binary machine code instruction offsets, branch/jump targets, and aligned pointer structures.
     - genome is a highly repetitive but dense 4-character alphabet ($A, C, T, G, N$) interspersed with high-entropy binary sequence-quality scores.
 - What it rewards: Advanced binary filters (like BCJ/Branch-Call-Jump preprocessing), byte-contextual modeling (PPM/PAQ-style bitwise modeling), and
   specialized modeling of low-cardinality alphabets.

 ────────────────────────────────────────────────────────────────────────────────

 ### Why this is a vastly superior design

 1. Mathematical Balance (No Dictators):
    The maximum leverage any single kind can exert on the headline score is now strictly capped and balanced. exe drops from a massive $20%$ to a
    reasonable $12.5%$, where it is perfectly balanced by genome ($12.5%$), which presents an equally difficult dense binary challenge.
 2. Explicit Incentive Alignment:
    Codec designers are given a clear roadmap. To get a high Squishy Score, you cannot simply be "the best at text." You must excel across all four
    fundamental disciplines of compression:
     - Natural Language (Local Context)
     - System Redundancy (Syntax & Schema)
     - Enterprise Storage (DB & Columnar)
     - Binary Data (Dense Non-Text)
 3. Logical Consistency:
    We no longer have a "Binary & Media" category where three-quarters of the members are "ghost files" that are excluded from scoring. The scoring math
    becomes clean, transparent, and direct.
