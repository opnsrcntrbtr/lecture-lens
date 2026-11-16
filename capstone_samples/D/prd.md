# Pantry Pal: PRD (revised)

## Problem
Online grocery customers of our partner grocer place 2.1 million repeat orders a year (grocer's 2026 annual report), and 61% of baskets repeat at least five items from the previous week (grocer data, 50,000 baskets).

## Users
Busy parents who already order groceries online every week.

## Requirements and acceptance criteria
- A user can approve, edit or skip the suggested basket in at most three taps from the notification.
- The suggested basket is ready by Saturday 18:00 for 99% of active households.
- Every suggested item shows why it was suggested (last bought, usual interval).

## Evaluation
Golden set of 200 past orders. Release threshold: reorder precision at least 0.85, because wrong items waste money; recall at least 0.75, because missed staples are the failure users complain about; allergen-flagged items never suggested to a household with that allergy (zero tolerance, 40 golden cases).

## Trust architecture
Pantry Pal suggests the basket; the user approves before any payment. If the user does not respond, nothing is ordered and a reminder is sent. If stock or price changes by more than 10%, the item is held for approval. A wrong order costs the household the price of unwanted items and a return trip; a missed staple costs an extra delivery fee.

## Launch plan
Pilot with 300 households in one city. Stop condition: if more than 5% of approved baskets are edited heavily (three or more items) for two weeks running, or any allergen incident occurs, the product lead pauses suggestions and reverts to the plain reorder button.
