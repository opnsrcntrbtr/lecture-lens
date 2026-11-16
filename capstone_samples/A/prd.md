# Pantry Pal: PRD

## Problem
Households run out of staples and waste time on repeat grocery orders. There is a big opportunity because lots of people order groceries online and everyone finds it annoying.

## Users
Busy parents who already order groceries online every week.

## Requirements and acceptance criteria
- The agent should be fast and helpful when suggesting reorders.
- Suggestions should feel smart and personalised.
- The checkout experience should be seamless.

## Evaluation
Golden set of 200 past orders with the items each household actually reordered. Release threshold: reorder precision at least 0.85 on the golden set, guarding against unwanted items in the basket; recall at least 0.75, guarding against missed staples, the failure users report most.

## Trust architecture
Pantry Pal places the weekly order automatically every Sunday using the saved card, without approval, so users never have to think about groceries.

## Launch plan
Pilot with 300 households in one city in March, then expand to all users in April.
