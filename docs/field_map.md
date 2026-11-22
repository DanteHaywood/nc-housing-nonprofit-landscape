# Field Map
This is a selection of fields from the 990 and 990-EZ xml files that this project pulls and organizes.

## IRS Form 990 XML Schema Field Mapping

### 1. Filing Identifiers
* `ReturnHeader/Filer/EIN` — Unique Employer Identification Number.
* `ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt` — Legal name of the organization.
* `ReturnHeader/TaxYr` — Tax year of the filing.

### 2. Mission & Program Service Descriptions
* `IRS990/MissionDesc` — Part III primary exempt purpose/mission statement.
* `IRS990/Desc` — Program Service Accomplishment #1 description (Part III, Line 4a).
* `IRS990/ExpenseAmt` — Program Service Accomplishment #1 expenses.
* `IRS990/RevenueAmt` — Program Service Accomplishment #1 revenue.
* `IRS990/ProgSrvcAccomActy2Grp/Desc` — Program Service Accomplishment #2 description (Part III, Line 4b).
* `IRS990/ProgSrvcAccomActy2Grp/ExpenseAmt` — Program Service Accomplishment #2 expenses.
* `IRS990/ProgSrvcAccomActy2Grp/RevenueAmt` — Program Service Accomplishment #2 revenue.
* `IRS990/ProgSrvcAccomActy3Grp/Desc` — Program Service Accomplishment #3 description (Part III, Line 4c).
* `IRS990/ProgSrvcAccomActy3Grp/ExpenseAmt` — Program Service Accomplishment #3 expenses.
* `IRS990/ProgSrvcAccomActy3Grp/RevenueAmt` — Program Service Accomplishment #3 revenue.
* `IRS990/ProgSrvcAccomActyOtherGrp/Desc` — Repeating node for all other program service descriptions (Part III, Line 4d / Schedule O).

### 3. Contributions & Government Grants
* `IRS990/GovernmentGrantsAmt` — Total government grants received (Part VIII, Line 1e).
* `IRS990/AllOtherContributionsAmt` — Non-government contributions, gifts, and grants (Part VIII, Line 1f).
* `IRS990/TotalContributionsAmt` — Total contributions and grants (Part VIII, Line 1h).

### 4. Program Service Revenue & Total Revenue
* `IRS990/ProgramServiceRevenueGrp` — Repeating node containing individual revenue stream descriptions (`Desc`) and amounts (`TotalRevenueColumnAmt`) (Part VIII, Line 2a–2f).
* `IRS990/TotalProgramServiceRevenueAmt` — Sum of all program service revenue (Part VIII, Line 2g).
* `IRS990/TotalRevenueGrp/TotalRevenueColumnAmt` — Total revenue for the current tax year (Part VIII, Line 12, Col A).
* `IRS990/PYTotalRevenueAmt` — Prior year total revenue for year-over-year comparison (Part I, Line 12).

### 5. Total Expenses
* `IRS990/TotalProgramServiceExpensesAmt` — Total expenses specifically allocated to program services (Part III, Line 4e / Part IX, Line 25, Col B).
* `IRS990/TotalFunctionalExpensesGrp/TotalAmt` — Total functional expenses across program, management, and fundraising (Part IX, Line 25, Col A).

### 6. Net Assets
* `IRS990/TotalNetAssetsFundBalanceGrp/BOYAmt` — Total net assets or fund balances at Beginning of Year (Part X, Line 33, Col A).
* `IRS990/TotalNetAssetsFundBalanceGrp/EOYAmt` — Total net assets or fund balances at End of Year (Part X, Line 33, Col B).

## IRS Form 990-EZ XML Schema Field Mapping

### 1. Filing Identifiers
* `ReturnHeader/Filer/EIN` — Unique Employer Identification Number.
* `ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt` — Legal name of the organization.
* `ReturnHeader/TaxYr` — Tax year of the filing.

### 2. Mission & Program Service Descriptions
* `IRS990EZ/PrimaryExemptPurposeTxt` — Part III primary exempt purpose/mission statement (equivalent to `MissionDesc` on Form 990).
* `IRS990EZ/ProgramSrvcAccomplishmentGrp` — Repeating node for each program service accomplishment (Part III, Lines 28–31).
* `IRS990EZ/ProgramSrvcAccomplishmentGrp/DescriptionProgramSrvcAccomTxt` — Narrative description of the program service accomplishment.
* `IRS990EZ/ProgramSrvcAccomplishmentGrp/GrantsAndAllocationsAmt` — Grants and allocations paid within the program.
* `IRS990EZ/ProgramSrvcAccomplishmentGrp/ProgramServiceExpensesAmt` — Program service expenses reported for the specific accomplishment.
* `IRS990EZ/TotalProgramServiceExpensesAmt` — Total program service expenses across all accomplishments (Part III, Line 32; omitted in this specific filing when 0, but standard in the 990-EZ schema).

### 3. Contributions & Government Grants
* `IRS990EZ/ContributionsGiftsGrantsEtcAmt` — Combined contributions, gifts, grants, and similar amounts received (Part I, Line 1). *(Note: Unlike Form 990, Form 990-EZ does not split government grants into a separate XML tag on the core form.)*

### 4. Program Service Revenue & Total Revenue
* `IRS990EZ/ProgramServiceRevenueAmt` — Total program service revenue, including government fees and contracts (Part I, Line 2).
* `IRS990EZ/MembershipDuesAmt` — Membership dues and assessments (Part I, Line 3; omitted in this specific filing when 0, but commonly used on 990-EZs).
* `IRS990EZ/InvestmentIncomeAmt` — Investment income (Part I, Line 4).
* `IRS990EZ/GrossProfitLossSlsOfInvntryAmt` — Gross profit or loss from sales of inventory (Part I, Line 7c).
* `IRS990EZ/OtherRevenueTotalAmt` — Other revenue not captured on lines 1–7 (Part I, Line 8).
* `IRS990EZ/TotalRevenueAmt` — Total revenue for the current tax year (Part I, Line 9).
* `IRS990EZ/GrossReceiptsAmt` — Gross receipts for the filing threshold check (Header/Item L).

### 5. Total Expenses
* `IRS990EZ/GrantsAndSimilarAmountsPaidAmt` — Grants and similar amounts paid (Part I, Line 10).
* `IRS990EZ/SalariesOtherCompEmplBnftsAmt` — Salaries, other compensation, and employee benefits (Part I, Line 12; omitted in this specific filing when 0).
* `IRS990EZ/FeesAndOtherPymtToIndCntrctAmt` — Professional fees and other payments to independent contractors (Part I, Line 13).
* `IRS990EZ/OccupancyRentUtltsAndMaintAmt` — Occupancy, rent, utilities, and maintenance expenses (Part I, Line 14).
* `IRS990EZ/OtherExpensesTotalAmt` — Other expenses detailed in Schedule O (Part I, Line 16).
* `IRS990EZ/TotalExpensesAmt` — Total expenses for the current tax year (Part I, Line 17).
* `IRS990EZ/ExcessOrDeficitForYearAmt` — Excess or deficit for the year (`TotalRevenueAmt` minus `TotalExpensesAmt`, Part I, Line 18).

### 6. Net Assets
* `IRS990EZ/NetAssetsOrFundBalancesGrp/BOYAmt` — Total net assets or fund balances at Beginning of Year (Part II, Line 27, Col A; also mirrored at `IRS990EZ/NetAssetsOrFundBalancesBOYAmt`).
* `IRS990EZ/NetAssetsOrFundBalancesGrp/EOYAmt` — Total net assets or fund balances at End of Year (Part II, Line 27, Col B; also mirrored at `IRS990EZ/NetAssetsOrFundBalancesEOYAmt`).
* `IRS990EZ/Form990TotalAssetsGrp/BOYAmt` — Total assets at Beginning of Year (Part II, Line 25, Col A).
* `IRS990EZ/Form990TotalAssetsGrp/EOYAmt` — Total assets at End of Year (Part II, Line 25, Col B).

### Key Schema Differences (990 vs. 990-EZ)
* **No Government Grant Split:** Form 990-EZ aggregates government grants directly into `ContributionsGiftsGrantsEtcAmt` rather than exposing a standalone `GovernmentGrantsAmt` field.
* **No Functional Expense Matrix:** Form 990-EZ reports expenses by natural classification on Part I (`GrantsAndSimilarAmountsPaidAmt`, `OtherExpensesTotalAmt`, etc.) rather than splitting them into Program, Management & General, and Fundraising columns.
* **Uniform Program Nodes:** Unlike Form 990 (which splits the first three programs into root tags and `ProgSrvcAccomActy2Grp`/`3Grp`), Form 990-EZ puts all program accomplishments inside repeating `ProgramSrvcAccomplishmentGrp` nodes.
