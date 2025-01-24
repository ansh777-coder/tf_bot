from twilio.rest import Client
from flask import Flask, request, Response, jsonify, render_template
from twilio.twiml.messaging_response import MessagingResponse
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import csv
import os
import re

# Twilio credentials
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
client = Client(account_sid, auth_token)

# Flask app
app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.DEBUG)

# CSV File setup
CSV_FILE = 'employee_attendance.csv'

# Initialize CSV file if it doesn't exist
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['phone_number', 'date', 'in_time', 'out_time', 'Present', 'Leave', 'Leave_reason'])

# Employee tracking
responded_employees = set()
out_time_responses = {}
employee_reminder_times = {}

# Employee phone numbers
EMPLOYEES = ['whatsapp:mobile1', 'whatsapp:+mobile2']

# Function to log data to CSV
def log_to_csv(phone_number, date, in_time="", out_time="", present="", leave="", leave_reason=""):
    rows = []
    updated = False

    # Read existing CSV data
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='r') as file:
            rows = list(csv.reader(file))

    # Update or append new data
    for row in rows:
        if row[0] == phone_number and row[1] == date:
            row[2] = in_time or row[2]
            row[3] = out_time or row[3]
            row[4] = present or row[4]
            row[5] = leave or row[5]
            row[6] = leave_reason or row[6]
            updated = True
            break

    if not updated:
        rows.append([phone_number, date, in_time, out_time, present, leave, leave_reason])

    # Write back to CSV
    with open(CSV_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerows(rows)

# Function to send reminders
def send_reminder(reminder_type):
    today_date = datetime.now().strftime('%Y-%m-%d')

    for employee in EMPLOYEES:
        if reminder_type == 'attendance' and employee not in responded_employees:
            message = client.messages.create(
                body="Reminder: Please mark your attendance. Type 'P' for present with time (e.g. 9:00 AM) or 'L' for leave with a reason (e.g. 'I'm sick').",
                from_="whatsapp:+14155238886",
                to=employee
            )
            employee_reminder_times[employee] = datetime.now()
            logging.info(f"Attendance reminder sent to {employee}. SID: {message.sid}")

        elif reminder_type == 'out_time' and employee not in out_time_responses.get(employee, []):
            message = client.messages.create(
                body="Reminder: Please mark your out time (e.g. 6:00 PM).",
                from_="whatsapp:+14155238886",
                to=employee
            )
            employee_reminder_times[employee] = datetime.now()
            logging.info(f"Out-time reminder sent to {employee}. SID: {message.sid}")

# Block reminders on Sunday
@app.before_request
def block_on_sunday():
    if datetime.now().strftime('%A') == 'Sunday':
        return jsonify({"message": "The service is unavailable on Sundays. Please come back tomorrow."}), 503

# Handle incoming WhatsApp messages
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.form.get("Body")
    employee = request.form.get("From")
    
    if not incoming_msg or not employee:
        logging.error("Missing 'Body' or 'From' in the request.")
        return "Missing 'Body' or 'From' in the request.", 400
    
    incoming_msg = incoming_msg.strip()
    today_date = datetime.now().strftime('%Y-%m-%d')
    response = MessagingResponse()

    # Handle Present (P) with in_time
    if incoming_msg.lower().startswith('p') and employee not in responded_employees:
        in_time_input = incoming_msg[2:].strip()
        if re.match(r'^\d{1,2}:\d{2}\s?(am|pm|AM|PM)$', in_time_input):
            responded_employees.add(employee)
            log_to_csv(employee, today_date, in_time=in_time_input, present='Yes')
            response.message(f"✅ Your presence is marked successfully at {in_time_input}. Have a great day!")
        else:
            response.message("⚠️ Invalid format. Please type 'P' followed by your in-time, e.g., 'P 9:00 AM'.")
            return Response(str(response), mimetype="application/xml")

    # Handle Leave (L) with leave reason
    elif incoming_msg.lower().startswith('l') and employee not in responded_employees:
        leave_reason = incoming_msg[2:].strip()
        if leave_reason:
            responded_employees.add(employee)
            log_to_csv(employee, today_date, leave='Yes', leave_reason=leave_reason)
            response.message(f"✅ Your leave has been marked successfully. Reason: {leave_reason}")
        else:
            response.message("⚠️ Please provide a reason for leave, e.g., 'L I am sick'.")
            return Response(str(response), mimetype="application/xml")

    # Handle Out Time
    elif incoming_msg.lower().startswith('out_time'):
        out_time = incoming_msg[8:].strip()
        if re.match(r'^\d{1,2}:\d{2}\s?(am|pm|AM|PM)$', out_time):
            log_to_csv(employee, today_date, out_time=out_time)
            response.message(f"✅ Thanks for marking your out time: {out_time}. Have a good evening!")
        else:
            response.message("⚠️ Invalid format. Please type 'out_time' followed by the time, e.g., 'out_time 6:00 PM'.")
            return Response(str(response), mimetype="application/xml")

    else:
        response.message("❓ Sorry, I didn't understand that. Use:\n- 'P <time>' for present (e.g., 'P 9:00 AM')\n- 'L <reason>' for leave (e.g., 'L I am sick')\n- 'out_time <time>' to mark out time (e.g., 'out_time 6:00 PM').")

    return Response(str(response), mimetype="application/xml")

# Route to display the attendance table
@app.route("/attendance", methods=["GET"])
def attendance_table():
    if not os.path.exists(CSV_FILE):
        return "No attendance data available."

    with open(CSV_FILE, mode='r') as file:
        rows = list(csv.reader(file))

    headers = rows.pop(0)  # First row as headers
    return render_template('attendance.html', headers=headers, rows=rows)

# Main function
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminder, 'cron', hour=17, minute=00, args=['attendance'], id='attendance_9_30')
    scheduler.add_job(send_reminder, 'cron', hour=18, minute=00, args=['attendance'], id='attendance_11_30')
    scheduler.add_job(send_reminder, 'cron', hour=18, minute=50, args=['out_time'], id='out_time_19_30')
    scheduler.add_job(send_reminder, 'cron', hour=22, minute=30, args=['out_time'], id='out_time_22_30')
    scheduler.start()

    try:
        logging.info("Starting Flask server...")
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
        logging.info("Scheduler shutdown.")
