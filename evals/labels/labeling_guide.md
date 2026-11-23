# Labeling guide

**Status: in use.** It follows the taxonomy in D-008, which was reviewed after
the 40 dev labels and accepted unchanged (D-017). The labels are strict on
primary purpose. Record housing-relevant organizations that fall outside them
with a `TAXONOMY:` note; broader coverage is a decision for the client.

## What you're labeling

Each row of `labels_dev.csv` and `labels_test.csv` is one North Carolina
nonprofit, described by its latest 990 or 990-EZ. Give each organization **one
primary label** from the six below, and write a short note when the call isn't
obvious.

The sheets are blind on purpose. There is no NTEE code and no note of why the
organization was sampled, because the NTEE codes are the baseline these labels
are scored against (D-004, D-015). Don't look them up.

## The rules

1. **Label from the sheet's text only.** Use the mission, the Part I activity
   line, the programs, and the Schedule O notes. Claude will be scored on the
   same text, so hand labels should rest on the same evidence. If you know
   something outside the text that contradicts it, still label from the text,
   and put what you know in `notes`.
2. **One primary label.** Ask: *if this organization stopped doing one thing,
   which loss would end it as an organization?* In practice:
   1. **Follow the money.** The `programs` column shows each program's
      expenses. The program with the largest share usually decides the label.
   2. **If expenses are missing or close**, use the mission statement's main
      purpose.
   3. **If both are unclear**, pick the best fit and start the note with
      `UNSURE:`.
3. **Housing must be the primary purpose** for any label other than
   `not_housing`. Organizations that touch housing as one of many services
   (a food bank that also gives out rent vouchers, a church with a small
   repair ministry) are `not_housing`, unless housing is where most of their
   program spending goes.
4. **Fill in `label` with the exact label text**, lowercase with underscores.
   Anything else is an error at scoring time.

## The labels

### `emergency_shelter`
Emergency or short-term shelter: a bed for tonight or for a short stay.
- **Includes:** homeless shelters, domestic violence shelters, emergency
  family shelters, winter or white-flag shelters, motel-voucher programs run
  as shelter.
- **Excludes:** day centers with no beds (see open questions), and longer
  transitional or permanent housing with services (`supportive_housing`).

### `affordable_housing_development`
Builds, owns, rehabilitates, or operates affordable housing, usually rental.
- **Includes:** community development corporations (CDCs) that develop
  housing, nonprofit owners of tax-credit or subsidized apartments, housing
  preservation and major rehab programs, community land trusts that hold
  rental housing.
- **Excludes:** programs that mainly help people *buy* homes
  (`homeownership`), and housing whose point is the services that come with
  it (`supportive_housing`).

### `homeownership`
Helps people become or stay homeowners.
- **Includes:** homebuyer education and counseling, down-payment assistance,
  self-help or sweat-equity building (Habitat for Humanity-style),
  foreclosure-prevention counseling, and owner-occupied repair programs whose
  purpose is keeping owners in their homes.
- **Excludes:** general financial literacy with no housing focus
  (`not_housing`).

### `rental_assistance`
Helps renters pay for or keep housing they already have.
- **Includes:** rent and utility relief, eviction prevention and diversion,
  security-deposit programs, rapid rehousing that is mostly rent payments, and
  tenant counseling.
- **Excludes:** one-time emergency help where rent is one of many things paid
  for (food, medical, car repair). That's `not_housing` unless rent is most of
  the spending.

### `supportive_housing`
Housing combined with services, for a population that needs both.
- **Includes:** group homes and supported living for people with
  disabilities (including intellectual and developmental disabilities),
  recovery and sober-living housing, housing for youth aging out of foster
  care, permanent supportive housing for formerly homeless people, and
  transitional housing with case management.
- **Excludes:** services delivered to people in their own homes, with no
  housing provided (`not_housing`). Day programs, home care, and employment
  support for people with disabilities are services, not housing.

### `not_housing`
Housing is not the primary purpose.
- **Includes:** most neighborhood and homeowner associations, community
  centers, food banks, general human services, economic development without a
  housing focus, and anything clearly outside housing.
- A housing-sounding name or NTEE code doesn't change this. Label what the
  filing says the organization does.

## Edge cases decided for this draft

| Case | Label | Why |
|---|---|---|
| Habitat for Humanity affiliate | `homeownership` | Self-help building for buyers. The ReStore is a revenue source, not the purpose. |
| CDC doing both rental development and homebuyer counseling | larger program by expenses | Rule 2. Say in `notes` that it does both. |
| Group home or supported living for adults with disabilities | `supportive_housing` | Housing plus services. |
| Agency that *places* people in family homes (e.g. alternative family living) | `supportive_housing` if residential support is the largest program, else `not_housing` | The housing comes from the placement. Flag it with `UNSURE:` the first few times so the D-008 review can confirm. |
| Emergency assistance fund that sometimes pays rent | `not_housing`, unless rent is most of the spending | Rules 2 and 3. |
| Homeowners' or neighborhood association | `not_housing` | Amenities and upkeep, not housing provision. |
| Mission says only "See Schedule O" | Read `schedule_o_text` and `activity_or_mission` | Both are there for this case (D-013). If neither has mission text, label from the programs and add `UNSURE:`. |

## Notes conventions

Use `notes` for anything that will help the error analysis later. Start a note
with one of these when it applies:
- `UNSURE:` you're torn between labels. Name the runner-up, e.g. `UNSURE:
  supportive_housing vs emergency_shelter. 90-day stays with case managers.`
- `TAXONOMY:` no label really fits, or two would have to be equally primary.
  These record what the strict labels miss (D-017).
- `TEXT:` the filing text is too thin to label with confidence (very short
  mission, no programs).

Otherwise, keep notes to one or two sentences of evidence: what in the text
decided it.

## Open questions (D-008 review: settled by D-017)

These come up often in NC housing filings. D-017 keeps the strict labels, so
label each by the closest rule above, add `TAXONOMY:` to the note, and count
how often they appear. The counts feed the client discussion on broader
coverage.

1. **Senior living and continuing-care retirement communities** (often NTEE
   P75). Are they `supportive_housing` (housing plus services for seniors),
   `affordable_housing_development` (if subsidized), or `not_housing` (if
   market-rate)?
2. **Transitional housing.** It sits between shelter and supportive housing.
   Should length of stay or services decide it?
3. **Homeless day centers and outreach with no beds.** `emergency_shelter`,
   `not_housing`, or does the taxonomy need a homelessness-services label?
4. **Home repair for low-income owners**, run as a charity rather than to
   prevent displacement: `homeownership` or `not_housing`?
5. **Organizations doing two housing things about equally** (e.g. a shelter
   with a large rental-assistance program). Does one primary label hold up,
   or should multi-label be reconsidered (deferred in D-008)?

## After labeling

- Don't change a label once it's set, especially not after seeing model
  output. Any change needs a `docs/decisions.md` entry saying why (CLAUDE.md
  eval rules).
- Label `labels_dev.csv` first. Prompt work only ever looks at dev, and the
  test set is scored once per prompt version that's ready.
