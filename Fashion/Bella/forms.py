import re

from django import forms


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