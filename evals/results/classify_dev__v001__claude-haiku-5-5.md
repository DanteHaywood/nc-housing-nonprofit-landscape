# Stage 1 eval: dev set, prompt v001, claude-haiku-5-5

Generated 2026-11-24T01:27:29+00:00 from 40 hand-labeled organizations. The labels are strict on primary purpose (D-017). NTEE codes are mapped with `ntee_taxonomy_map.csv` (D-018); `unmapped` means a group L code that names no kind of housing, scored wrong. Claude rows without an ok status count as `(no label)`.

## Accuracy

| Classifier | Correct | Accuracy |
|---|---|---|
| claude | 33 | 82.5% |
| ntee | 25 | 62.5% |
| random | 3 | 7.5% |

## Accuracy by sample group

| Group | n | claude | ntee | random |
|---|---|---|---|---|
| ntee_L | 16 | 75.0% | 43.8% | 0.0% |
| ntee_adjacent | 12 | 75.0% | 50.0% | 16.7% |
| random | 12 | 100.0% | 100.0% | 8.3% |

## claude: per-label precision and recall

| Label | Support | Predicted | Precision | Recall | F1 |
|---|---|---|---|---|---|
| emergency_shelter | 0 | 2 | 0.00 | – | 0.00 |
| affordable_housing_development | 12 | 9 | 1.00 | 0.75 | 0.86 |
| homeownership | 1 | 1 | 1.00 | 1.00 | 1.00 |
| rental_assistance | 1 | 2 | 0.50 | 1.00 | 0.67 |
| supportive_housing | 5 | 6 | 0.33 | 0.40 | 0.36 |
| not_housing | 21 | 20 | 1.00 | 0.95 | 0.98 |

## claude: confusion matrix (rows: hand label)

| hand \ predicted | emergency_shelter | affordable_housing_development | homeownership | rental_assistance | supportive_housing | not_housing |
|---|---|---|---|---|---|---|
| emergency_shelter | 0 | 0 | 0 | 0 | 0 | 0 |
| affordable_housing_development | 0 | 9 | 0 | 0 | 3 | 0 |
| homeownership | 0 | 0 | 1 | 0 | 0 | 0 |
| rental_assistance | 0 | 0 | 0 | 1 | 0 | 0 |
| supportive_housing | 2 | 0 | 0 | 1 | 2 | 0 |
| not_housing | 0 | 0 | 0 | 0 | 1 | 20 |

## ntee: per-label precision and recall

| Label | Support | Predicted | Precision | Recall | F1 |
|---|---|---|---|---|---|
| emergency_shelter | 0 | 2 | 0.00 | – | 0.00 |
| affordable_housing_development | 12 | 11 | 0.64 | 0.58 | 0.61 |
| homeownership | 1 | 1 | 0.00 | 0.00 | 0.00 |
| rental_assistance | 1 | 3 | 0.00 | 0.00 | 0.00 |
| supportive_housing | 5 | 3 | 0.00 | 0.00 | 0.00 |
| not_housing | 21 | 20 | 0.90 | 0.86 | 0.88 |

## ntee: confusion matrix (rows: hand label)

| hand \ predicted | emergency_shelter | affordable_housing_development | homeownership | rental_assistance | supportive_housing | not_housing |
|---|---|---|---|---|---|---|
| emergency_shelter | 0 | 0 | 0 | 0 | 0 | 0 |
| affordable_housing_development | 0 | 7 | 1 | 2 | 1 | 1 |
| homeownership | 0 | 1 | 0 | 0 | 0 | 0 |
| rental_assistance | 0 | 0 | 0 | 0 | 0 | 1 |
| supportive_housing | 2 | 2 | 0 | 1 | 0 | 0 |
| not_housing | 0 | 1 | 0 | 0 | 2 | 18 |

## random: per-label precision and recall

| Label | Support | Predicted | Precision | Recall | F1 |
|---|---|---|---|---|---|
| emergency_shelter | 0 | 10 | 0.00 | – | 0.00 |
| affordable_housing_development | 12 | 5 | 0.00 | 0.00 | 0.00 |
| homeownership | 1 | 5 | 0.00 | 0.00 | 0.00 |
| rental_assistance | 1 | 7 | 0.14 | 1.00 | 0.25 |
| supportive_housing | 5 | 5 | 0.00 | 0.00 | 0.00 |
| not_housing | 21 | 8 | 0.25 | 0.10 | 0.14 |

## random: confusion matrix (rows: hand label)

| hand \ predicted | emergency_shelter | affordable_housing_development | homeownership | rental_assistance | supportive_housing | not_housing |
|---|---|---|---|---|---|---|
| emergency_shelter | 0 | 0 | 0 | 0 | 0 | 0 |
| affordable_housing_development | 1 | 0 | 2 | 3 | 1 | 5 |
| homeownership | 0 | 0 | 0 | 0 | 0 | 1 |
| rental_assistance | 0 | 0 | 0 | 1 | 0 | 0 |
| supportive_housing | 1 | 3 | 0 | 1 | 0 | 0 |
| not_housing | 8 | 2 | 3 | 2 | 4 | 2 |
