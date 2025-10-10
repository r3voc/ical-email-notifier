from dotenv import load_dotenv
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from icalendar import Calendar, Event
from os import environ
import datetime
import jinja2
import json
import logging
import os
import recurring_ical_events
import requests
import smtplib
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO)
load_dotenv()

log = logging.getLogger(__name__)

SENT_EMAIL_LOG = './data/sent_emails.json'
NEXT_HOURS = 24

def check_config() -> bool:
    required_vars = [
        'CALENDAR_URL',
        'SMTP_HOST',
        'SMTP_PORT',
        'SMTP_USER',
        'SMTP_PASSWORD',
        'EMAIL_FROM',
        'EMAIL_TO'
    ]
    missing_vars = [var for var in required_vars if not environ.get(var)]
    if missing_vars:
        print(f"Missing required environment variables: {', '.join(missing_vars)}")
        return False
    return True


CONFIG = {
    'CALENDAR_URL': environ.get('CALENDAR_URL'),
    'SMTP_HOST': environ.get('SMTP_HOST'),
    'SMTP_PORT': int(environ.get('SMTP_PORT', 587)),
    'SMTP_USER': environ.get('SMTP_USER'),
    'SMTP_PASSWORD': environ.get('SMTP_PASSWORD'),
    'EMAIL_FROM': environ.get('EMAIL_FROM'),
    'EMAIL_TO': environ.get('EMAIL_TO')
}

def fix_datetime(dt) -> datetime.datetime:
    if isinstance(dt, datetime.datetime):
        return dt
    elif isinstance(dt, datetime.date):
        return datetime.datetime.combine(dt, datetime.time.min)
    else:
        raise ValueError("Invalid date/time format")


def get_calendar_events() -> list:
    # fetch from URL
    response = requests.get(CONFIG['CALENDAR_URL'])
    response.raise_for_status()
    cal = Calendar.from_ical(response.content)

    recurring_events = recurring_ical_events.of(cal).between(datetime.datetime.now(),
                                                             datetime.datetime.now() + datetime.timedelta(days=30))

    events = []

    for component in recurring_events:
        try:
            if component.name == "VEVENT":
                # if the event is in the past, skip it
                dtstart = component.get('dtstart')

                dtstart_dt = fix_datetime(dtstart.dt)

                start_unix = dtstart_dt.timestamp()
                if start_unix < datetime.datetime.now().timestamp():
                    continue

                events.append({
                    'summary': str(component.get('summary')),
                    'dtstart': component.get('dtstart').dt,
                    'dtend': component.get('dtend').dt,
                    'description': str(component.get('description', '')),
                    'location': str(component.get('location', ''))
                })
        except Exception as e:
            log.error(f"Error processing event: {e}")
            continue
    return events


def run_task() -> None:
    log.info("Running scheduled task...")

    events = get_calendar_events()
    if not events:
        log.info("No upcoming events found.")
        return

    try:
        with open(SENT_EMAIL_LOG, 'r') as f:
            sent_emails = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        sent_emails = {}

        if isinstance(err, FileNotFoundError):
            log.warning(f"Sent email log file not found. A new one will be created at {SENT_EMAIL_LOG}.")
            os.makedirs(os.path.dirname(SENT_EMAIL_LOG), exist_ok=True)

            with open(SENT_EMAIL_LOG, 'w') as f:
                json.dump(sent_emails, f)

    # Filter by events that are within the next 24 hours

    upcoming_events = []

    for event in events:
        try:
            start_unix = fix_datetime(event['dtstart']).timestamp()
            if start_unix < (datetime.datetime.now() + datetime.timedelta(hours=NEXT_HOURS)).timestamp():
                upcoming_events.append(event)
        except Exception as e:
            log.error(f"Error processing event date: {e}")
            continue

    if not upcoming_events:
        log.info(f"No events within the next {NEXT_HOURS} hours.")
        return

    new_events = [event for event in upcoming_events if event['summary'] not in sent_emails]

    if not new_events:
        log.info("No new events to notify.")
        return

    for event in new_events:
        if send_mail_for_event(event):
            sent_emails[event['summary']] = event['dtstart'].isoformat()

    with open(SENT_EMAIL_LOG, 'w') as f:
        json.dump(sent_emails, f)

def send_mail_for_event(ical_event):
    event = ical_event

    event['name'] = event['summary']
    event['start_time'] = event['dtstart'] if isinstance(event['dtstart'], datetime.datetime) else datetime.datetime.combine(event['dtstart'], datetime.time.min)
    event['end_time'] = event['dtend'] if isinstance(event['dtend'], datetime.datetime) else datetime.datetime.combine(event['dtend'], datetime.time.min)

    email_subject = f"Upcoming Event: {event['summary']} on {event['dtstart'].strftime('%Y-%m-%d %H:%M')}"

    template_loader = jinja2.FileSystemLoader(searchpath="./templates")
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template("default.jinja")
    email_body = template.render(event=event)

    msg = MIMEMultipart()
    msg['From'] = CONFIG['EMAIL_FROM']
    msg['To'] = CONFIG['EMAIL_TO']
    msg['Subject'] = email_subject
    msg.attach(MIMEText(email_body, 'html'))

    cal_attachment = Calendar()
    cal_event = Event()
    cal_event.add('summary', event['summary'])
    cal_event.add('dtstart', event['dtstart'])
    cal_event.add('dtend', event['dtend'])
    cal_event.add('description', event['description'])
    cal_event.add('location', event['location'])
    cal_attachment.add_component(cal_event)

    cal_part = MIMEApplication(cal_attachment.to_ical(), Name="event.ics")
    cal_part['Content-Disposition'] = 'attachment; filename="event.ics"'
    msg.attach(cal_part)

    log.info(f"Sending email for event: {event['summary']}")

    try:
        with smtplib.SMTP(CONFIG['SMTP_HOST'], CONFIG['SMTP_PORT']) as server:
            server.starttls()
            server.login(CONFIG['SMTP_USER'], CONFIG['SMTP_PASSWORD'])
            server.sendmail(CONFIG['EMAIL_FROM'], CONFIG['EMAIL_TO'].split(','), msg.as_string())
        log.info(f"Sent email notification for event: {event['summary']}")
        return True
    except Exception as e:
        log.error(f"Failed to send email for event {event['summary']}: {e}")
        return False

def main():
    if not check_config():
        log.error("Configuration check failed. Exiting.")
        sys.exit(1)

    run_task()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_task, 'interval', minutes=30)

    # make sure to stop the scheduler gracefully on exit

    try:
        log.info("Starting scheduler...")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down scheduler...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
