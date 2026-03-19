For cleanup/refactor tasks:
- distinguish live code from legacy code
- delete only after checking references
- preserve behavior
- propose phased changes
- do not continue automatically to the next phase
- report anything ambiguous instead of guessing

Critical files to avoid unless explicitly approved:
- portal/app.py
- portal/database.py
- portal/video_processor.py
- portal/templates/clean_dashboard.html
