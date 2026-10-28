
import os
from datetime import date
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

app = Flask(__name__)
app.secret_key = os.getenv(
    "SECRET_KEY",
    "change-this-secret-key"
)

# Database connection
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "appointment_slots"),
    )


# Available departments
DEPARTMENTS = {
    "General Consultation":
        "General health consultations and routine checkups.",
    "Orthopedics":
        "Consultations for bones, joints, and muscles.",
    "Gynecology":
        "Women's health consultations.",
}


# Admin authentication decorator
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped


# Home page: show only three departments
@app.route("/")
def index():
    departments = [
        {
            "name": "General Consultation",
            "description": DEPARTMENTS["General Consultation"],
            "icon": "🩺",
        },
        {
            "name": "Orthopedics",
            "description": DEPARTMENTS["Orthopedics"],
            "icon": "🦴",
        },
        {
            "name": "Gynecology",
            "description": DEPARTMENTS["Gynecology"],
            "icon": "🌸",
        },
    ]

    return render_template(
        "appointments.html",
        departments=departments,
    )


# Display slots for the selected department
@app.route("/department/<department_name>")
def department_slots(department_name):
    if department_name not in DEPARTMENTS:
        return "Department not found", 404

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, service_name, slot_date,
                   start_time, end_time
            FROM appointment_slots
            WHERE service_name = %s
              AND is_booked = FALSE
              AND slot_date >= %s
            ORDER BY slot_date, start_time
            """,
            (department_name, date.today()),
        )

        slots = cursor.fetchall()

        return render_template(
            "department_slots.html",
            department_name=department_name,
            slots=slots,
        )

    except Exception:
        app.logger.exception("Loading department slots failed")
        flash("Unable to load appointments. Please try again.")
        return redirect(url_for("index"))

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# Book an appointment
@app.route("/book/<int:slot_id>", methods=["GET", "POST"])
def book(slot_id):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if request.method == "POST":
            name = request.form.get(
                "customer_name", ""
            ).strip()
            email = request.form.get(
                "customer_email", ""
            ).strip()

            if (
                not name
                or len(name) > 100
                or not email
                or len(email) > 150
                or "@" not in email
            ):
                flash("Enter a valid name and email address.")
                return redirect(
                    url_for("book", slot_id=slot_id)
                )

            conn.start_transaction()

            cursor.execute(
                """
                SELECT id, service_name, slot_date,
                       start_time, end_time, is_booked
                FROM appointment_slots
                WHERE id = %s
                FOR UPDATE
                """,
                (slot_id,),
            )

            slot = cursor.fetchone()

            if (
                not slot
                or slot["is_booked"]
                or slot["slot_date"] < date.today()
            ):
                conn.rollback()
                flash("This appointment slot is no longer available.")
                return redirect(url_for("index"))

            cursor.execute(
                """
                INSERT INTO appointments
                    (slot_id, customer_name, customer_email, status)
                VALUES (%s, %s, %s, 'BOOKED')
                """,
                (slot_id, name, email),
            )

            booking_id = cursor.lastrowid

            cursor.execute(
                """
                UPDATE appointment_slots
                SET is_booked = TRUE
                WHERE id = %s
                """,
                (slot_id,),
            )

            conn.commit()

            flash(
                f"Appointment booked successfully! "
                f"Your booking ID is {booking_id}."
            )

            return redirect(url_for("index"))

        cursor.execute(
            """
            SELECT id, service_name, slot_date,
                   start_time, end_time
            FROM appointment_slots
            WHERE id = %s
              AND is_booked = FALSE
              AND slot_date >= %s
            """,
            (slot_id, date.today()),
        )

        slot = cursor.fetchone()

        if not slot:
            flash("That appointment slot is not available.")
            return redirect(url_for("index"))

        return render_template("book.html", slot=slot)

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()

        app.logger.exception("Booking failed")
        flash("Unable to complete booking. Please try again.")
        return redirect(url_for("index"))

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# Cancel an appointment
@app.route("/cancel", methods=["GET", "POST"])
def cancel():
    if request.method == "GET":
        return render_template("cancel.html")

    email = request.form.get(
        "customer_email", ""
    ).strip()
    appointment_id = request.form.get(
        "appointment_id", ""
    ).strip()

    if not appointment_id.isdigit() or not email:
        flash("Enter your booking ID and email address.")
        return redirect(url_for("cancel"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        conn.start_transaction()

        cursor.execute(
            """
            SELECT a.id, a.slot_id
            FROM appointments a
            JOIN appointment_slots s ON s.id = a.slot_id
            WHERE a.id = %s
              AND a.customer_email = %s
              AND a.status = 'BOOKED'
            FOR UPDATE
            """,
            (int(appointment_id), email),
        )

        booking = cursor.fetchone()

        if not booking:
            conn.rollback()
            flash("Active booking not found. Check the ID and email.")
            return redirect(url_for("cancel"))

        cursor.execute(
            """
            UPDATE appointments
            SET status = 'CANCELLED'
            WHERE id = %s
            """,
            (booking["id"],),
        )

        cursor.execute(
            """
            UPDATE appointment_slots
            SET is_booked = FALSE
            WHERE id = %s
            """,
            (booking["slot_id"],),
        )

        conn.commit()

        flash("Appointment cancelled successfully.")
        return redirect(url_for("index"))

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()

        app.logger.exception("Cancellation failed")
        flash("Unable to cancel the appointment.")
        return redirect(url_for("cancel"))

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()




@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Read credentials from the .env file
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "")

        # Check credentials
        if username == admin_username and password == admin_password and admin_password:
            session["is_admin"] = True
            flash("Login successful!")
            return redirect(url_for("admin"))

        app.logger.warning(
            "Admin login failed. Username matched: %s; password configured: %s",
            username == admin_username,
            bool(admin_password),
        )
        flash("Invalid admin username or password.")

    return render_template("admin_login.html")




# Admin logout
@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# Admin dashboard
@app.route("/admin")
@admin_required
def admin():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT s.id, s.service_name, s.slot_date,
                   s.start_time, s.end_time, s.is_booked,
                   a.id AS appointment_id,
                   a.customer_name, a.customer_email, a.status
            FROM appointment_slots s
            LEFT JOIN appointments a
              ON a.slot_id = s.id
             AND a.status = 'BOOKED'
            ORDER BY s.slot_date, s.start_time
            """
        )

        slots = cursor.fetchall()

        return render_template("admin.html", slots=slots)

    except Exception:
        app.logger.exception("Loading admin dashboard failed")
        flash("Unable to load the admin dashboard.")
        return redirect(url_for("index"))

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# Add a new appointment slot
@app.route("/admin/add-slot", methods=["POST"])
@admin_required
def add_slot():
    service = request.form.get(
        "service_name", ""
    ).strip()
    slot_date = request.form.get("slot_date", "")
    start_time = request.form.get("start_time", "")
    end_time = request.form.get("end_time", "")

    if service not in DEPARTMENTS:
        flash("Select a valid department.")
        return redirect(url_for("admin"))

    if not slot_date or not start_time or not end_time:
        flash("Complete all appointment slot fields.")
        return redirect(url_for("admin"))

    try:
        selected_date = date.fromisoformat(slot_date)

        if selected_date < date.today():
            flash("Choose today or a future date.")
            return redirect(url_for("admin"))

        if start_time >= end_time:
            flash("End time must be later than start time.")
            return redirect(url_for("admin"))

    except ValueError:
        flash("Enter a valid date and time.")
        return redirect(url_for("admin"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO appointment_slots
                (service_name, slot_date, start_time, end_time)
            VALUES (%s, %s, %s, %s)
            """,
            (service, slot_date, start_time, end_time),
        )

        conn.commit()
        flash("New appointment slot added successfully.")

    except mysql.connector.IntegrityError:
        if conn and conn.is_connected():
            conn.rollback()

        flash("A slot already exists for this department and start time.")

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()

        app.logger.exception("Adding appointment slot failed")
        flash("Could not add the appointment slot.")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    return redirect(url_for("admin"))


# Delete an unbooked slot
@app.route("/admin/delete-slot/<int:slot_id>", methods=["POST"])
@admin_required
def delete_slot(slot_id):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT is_booked
            FROM appointment_slots
            WHERE id = %s
            FOR UPDATE
            """,
            (slot_id,),
        )

        slot = cursor.fetchone()

        if not slot:
            flash("Appointment slot not found.")

        elif slot["is_booked"]:
            flash("Cancel the booking before deleting this slot.")

        else:
            cursor.execute(
                "DELETE FROM appointment_slots WHERE id = %s",
                (slot_id,),
            )
            conn.commit()
            flash("Appointment slot deleted successfully.")

    except Exception:
        if conn and conn.is_connected():
            conn.rollback()

        app.logger.exception("Deleting appointment slot failed")
        flash("Could not delete the appointment slot.")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(debug=True)

