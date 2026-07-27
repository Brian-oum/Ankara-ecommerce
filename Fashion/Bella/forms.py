import re

from django import forms
from django.contrib.auth import get_user_model, password_validation
# ---------------------------------------------------------------------------
# Add this block to your existing forms.py (it isn't a standalone file -
# I don't have your current forms.py, which already defines CheckoutForm
# and ContactForm, so this is written to sit alongside them rather than
# replace the file).
#
# Needs these imports added at the top of forms.py, alongside whatever's
# already there:
#
#   from django import forms
#   from django.contrib.auth import get_user_model, password_validation
#
# ---------------------------------------------------------------------------

User = get_user_model()


def _unique_username_from_email(email):
    """
    Django's username field is unique and capped at 150 chars. An email
    address already satisfies the username character rules, so we use
    it directly - this just guards the (very rare) case of a collision
    or an over-length address.
    """
    base = email[:150]
    username = base
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        tail = str(suffix)
        username = f"{base[:150 - len(tail) - 1]}-{tail}"
    return username


class RegisterForm(forms.Form):
    name = forms.CharField(max_length=100, label="Full name")
    email = forms.EmailField(label="Email address")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1", "")
        password_validation.validate_password(password1)
        return password1

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords don't match.")
        return cleaned

    def save(self):
        email = self.cleaned_data["email"]
        name = self.cleaned_data["name"].strip()
        first_name, _, last_name = name.partition(" ")
        return User.objects.create_user(
            username=_unique_username_from_email(email),
            email=email,
            password=self.cleaned_data["password1"],
            first_name=first_name,
            last_name=last_name,
        )


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email address")
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

class CheckoutForm(forms.Form):
    """
    Collects the customer's details for a cart checkout. The amount is
    not part of this form - it's always computed from the server-side
    cart, never trusted from the client.
    """

    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20)

    def clean_phone(self):
        digits = re.sub(r"\D", "", self.cleaned_data["phone"])

        if digits.startswith("0") and len(digits) == 10:
            digits = "254" + digits[1:]
        elif digits.startswith("7") and len(digits) == 9:
            digits = "254" + digits
        elif digits.startswith("254") and len(digits) == 12:
            pass
        else:
            raise forms.ValidationError(
                "Enter a valid Safaricom number, e.g. 0722000000"
            )

        return digits


class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    subject = forms.CharField(max_length=150, required=False)
    message = forms.CharField(widget=forms.Textarea)