# forms.py
from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField
from wtforms.validators import DataRequired, Length, Regexp

class RenameSessionForm(FlaskForm):
    session_id = HiddenField(
        'Session ID',
        validators=[DataRequired(message="Missing session_id")]
    )
    new_name = StringField(
        'New Name',
        validators=[
            DataRequired(message="Name cannot be empty"),
            Length(max=50, message="Name too long (max 50 chars)"),
            Regexp(
                r"^[\w\s\-\.,'()!?]+$",
                message="Only letters, numbers, spaces, hyphens, periods, commas, apostrophes, and common punctuation allowed"
            )
        ]
    )
