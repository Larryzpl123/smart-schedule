"""Run once to create the database (app.py also auto-creates it on startup,
so this is optional). Safe to run repeatedly — only creates missing tables."""
import app
print("Database ready: schedule.db (users + schedules table created).")
