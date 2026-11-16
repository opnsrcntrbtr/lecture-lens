# ClinicQ: strategy

## Moat
Our edge is the two-way integration with the clinics' scheduling systems and the de-identified record of 18 months of rescheduling patterns per clinic. A stronger base model would improve language handling but would not give a competitor the integrations or the scheduling history.

## Unit economics
A rescheduling conversation averages 5 model calls at 0.3 cents; retries add 15% and 4% fail and go to staff. Cost per successful reschedule: 5 x 0.3 x 1.15 / 0.96 = 1.8 cents. Median clinic: 900 reschedules a month = 16 dollars; busiest tenth: 3,000 a month = 54 dollars. Price: 150 dollars per clinic per month, so gross margin is above 60% even for the busiest clinics.
