# Seeded-defect set for the capstone checkers (M-11)

Invented products, written for testing. Each folder holds a `prd.md` and a `strategy.md`.
`labels.json` says which checks each folder should fail (planted defects); every other
check should pass. A, B and C carry planted defects; D and E are clean controls. Each check
is planted in exactly two folders, so it has two positives and three negatives.
