# ClinicQ: PRD (revised)

## Problem
Clinic front desks answer about 1,400 appointment calls a week per clinic (our pilot clinics' call logs, 2026); 38% are simple rescheduling requests.

## Users
Patients of three partner outpatient clinics who book by phone or WhatsApp.

## Requirements and acceptance criteria
- A patient can reschedule an existing appointment to any open slot in under 2 minutes, measured from first message to confirmation, for 90% of attempts.
- The bot shows the clinic's address and the doctor's name in every confirmation.

## Evaluation
Golden set of 300 real (de-identified) rescheduling conversations. Thresholds: wrong-slot bookings at most 0.5%, because a wrong slot means a missed appointment; misread dates at most 1%, the failure we saw most in the pilot logs; any symptom question routed to staff in 100% of 60 golden cases, because the bot must never triage.

## Trust architecture
ClinicQ only reschedules existing appointments. It never answers medical or triage questions: any symptom wording hands the conversation to the front desk with the transcript. If the bot is unsure of the date after one clarifying question, it hands off. A wrong booking costs the patient a missed appointment and the clinic an empty slot; a wrong triage answer could harm a patient, which is why triage is excluded.

## Launch plan
Pilot in one clinic for six weeks. Stop condition: if more than 2% of bookings need manual correction in any week, or any triage complaint reaches the medical director, the clinic manager pauses the bot and routes all messages to the desk.
