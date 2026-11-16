# ClinicQ: PRD

## Problem
Clinic front desks answer about 1,400 appointment calls a week per clinic (our pilot clinics' call logs, 2026); 38% are simple rescheduling requests.

## Users
Patients of three partner outpatient clinics who book by phone or WhatsApp.

## Requirements and acceptance criteria
- A patient can reschedule an existing appointment to any open slot in under 2 minutes, measured from first message to confirmation, for 90% of attempts.
- The bot shows the clinic's address and the doctor's name in every confirmation.

## Evaluation
We will make sure the model has accuracy above 90% before launch.

## Trust architecture
ClinicQ answers triage questions such as whether symptoms need same-day care, and books the slot directly.

## Launch plan
Pilot in one clinic for six weeks. Stop condition: if more than 2% of bookings need manual correction in any week, or any triage complaint reaches the medical director, the clinic manager pauses the bot and routes all messages to the desk.
