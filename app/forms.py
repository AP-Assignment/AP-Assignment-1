# -*- coding: utf-8 -*-
"""WTForms form definitions for registration, login, and booking."""

from datetime import timedelta

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, DateTimeLocalField, TextAreaField, SelectMultipleField, BooleanField
from wtforms.validators import DataRequired, Email, Length, ValidationError

from .services.booking_rules import MAX_BOOKING_DAYS

class RegisterForm(FlaskForm):
    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    team = StringField("Team / department", validators=[DataRequired(), Length(min=2, max=120)])
    manager_email = StringField("Manager email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Create account")

class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in")

class TOTPVerificationForm(FlaskForm):
    token = StringField("Authentication code", validators=[DataRequired()])
    submit = SubmitField("Verify")

class BookingForm(FlaskForm):
    start_at = DateTimeLocalField("Start", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    end_at = DateTimeLocalField("End", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    purpose = TextAreaField("Purpose / notes", validators=[DataRequired(), Length(min=5, max=300)])
    machines = SelectMultipleField("Machines", coerce=int, validators=[DataRequired()])
    request_access = BooleanField("Request site access?")
    submit = SubmitField("Request booking")

    def validate_end_at(self, field):
        if self.start_at.data and field.data and field.data <= self.start_at.data:
            raise ValidationError("End time must be after start time.")
        if self.start_at.data and field.data and field.data - self.start_at.data > timedelta(days=MAX_BOOKING_DAYS):
            raise ValidationError("Maximum booking time is 28 days.")
