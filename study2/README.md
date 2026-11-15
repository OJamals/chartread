# study2: source snapshot

The modules and scripts that built, ran and scored Study 2 and addenda 6 to 9, copied from the
development repository (`clinical_eval/` and `scripts/` there; the same relative paths here). It is
a snapshot for reading and for checking a number against the code that produced it. It is **not**
installable and does not run on its own: it imports modules that are not released, namely
`clinical_preprocessor` (the rule engine, an arm whose answers are released as data),
`clinical_dataset_builder` (the retrieval transport used by an unrelated arm) and the UMLS-derived
disorder lexicon that locates findings in real notes. `SOURCES.sha256` lists each file's SHA-256;
the development repository's commit was `e2c16fb`, with the working-tree changes to
`chart_read_v2_describe.py`, `chart_read_v2_keys.py` and `scripts/chart_read.py` that the paper's
macros need.

The standalone package in `chartread/` at the repository root is v1's, and is unchanged.
