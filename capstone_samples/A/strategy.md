# Pantry Pal: strategy

## Moat
Our advantage is three years of household purchase histories from the partner grocer and the integration with its inventory and delivery slots. A stronger general model would make our suggestions better; it would not give a competitor the purchase data or the slot integration.

## Unit economics
Each weekly order run makes 6 model calls at 0.4 cents each; retries add 20% and 5% of runs fail and are redone. Cost per successful order: 6 x 0.4 x 1.2 / 0.95 = 3.0 cents. Median household: 4 runs a month = 12 cents; heaviest tenth: 20 runs a month = 61 cents. Price: 2 dollars a month, so gross margin stays above 69% even for heavy users.
