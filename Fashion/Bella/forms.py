import re

from django import forms
from django.forms import inlineformset_factory
from django.contrib.auth import get_user_model, password_validation
from .models import *
from . import shipping
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

    # Populated from the area <select> on the checkout page (grouped by
    # zone via shipping.area_choices()) - this is what shipping_fee is
    # looked up from, server-side, in views.checkout(). Required: no
    # area picked means no fee we can trust, so checkout can't proceed.
    delivery_area = forms.ChoiceField(choices=[])

    # Populated by the Leaflet/OSM map's marker (dropped or dragged by
    # the customer) via a couple of hidden inputs + JS. Optional and
    # informational only for the rider - never used to compute price.
    delivery_lat = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False, widget=forms.HiddenInput
    )
    delivery_lng = forms.DecimalField(
        max_digits=9, decimal_places=6, required=False, widget=forms.HiddenInput
    )
    delivery_address = forms.CharField(
        max_length=255, required=False, widget=forms.HiddenInput
    )

    # Free-text from the customer (estate, building, door number,
    # landmark…) to help the rider find the exact spot - separate from
    # delivery_address, which is the map pin's reverse-geocoded label
    # and gets overwritten whenever the pin moves.
    delivery_notes = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "placeholder": "Estate, building/house name, door number, landmark…",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built at instantiation time (not as a class-level default) so
        # editing shipping.ZONES is picked up without touching this file.
        self.fields["delivery_area"].choices = shipping.area_choices()

    def clean_delivery_area(self):
        area = self.cleaned_data["delivery_area"]
        if not shipping.is_valid_area(area):
            raise forms.ValidationError("Please choose a delivery area from the list.")
        return area

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

 
class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["category", "name", "description", "price", "image", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }
        help_texts = {
            "price": "Used directly for simple products, and as the fallback "
                     "display price if all variants are removed.",
        }
 
 
# One form per variant row (size, material, style, etc). extra=1 gives a
# single blank row to start from - the product-form template adds more
# client-side via JS rather than bumping this, so the count stays in sync
# with whatever the manager actually filled in.
ProductVariantFormSet = inlineformset_factory(
    Product,
    ProductVariant,
    fields=["name", "price", "is_default", "sort_order"],
    extra=1,
    can_delete=True,
)


# One form per colour row (Black, Burgundy, Honey Blonde, etc). Fields are
# rendered as hidden inputs in the template - the manager never edits them
# directly; the colour-picker modal (see product_form.html) reads/writes
# these hidden inputs via JS to add or remove colours.
ProductColourFormSet = inlineformset_factory(
    Product,
    ProductColour,
    fields=["name", "hex_code", "sort_order"],
    extra=1,
    can_delete=True,
    widgets={
        "name": forms.HiddenInput(),
        "hex_code": forms.HiddenInput(),
        "sort_order": forms.HiddenInput(),
    },
)
 
 
class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]


# ---- manager settings -----------------------------------------------------

class ManagerAccountForm(forms.ModelForm):
    """Name + email fields on the manager's own auth User, from Settings."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        # Exclude self, or a manager who hasn't touched their email
        # would get told it's "already in use" by their own account.
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account is already using this email.")
        return email


class ManagerAvatarForm(forms.ModelForm):
    """Sidebar/Settings photo upload - backs both the quick picker in
    manager_base.html and the full form on the Settings page."""

    class Meta:
        model = ManagerProfile
        fields = ["photo"]


class ManagerThemeForm(forms.ModelForm):
    """Just the theme field - backs the quick toggle in manager_base.html's
    sidebar as well as the segmented control on the Settings page."""

    class Meta:
        model = ManagerProfile
        fields = ["theme_preference"]
        widgets = {"theme_preference": forms.RadioSelect}


class ManagerNotificationsForm(forms.ModelForm):
    """Master switch + one toggle per event, from Settings -> Preferences."""

    class Meta:
        model = ManagerProfile
        fields = [
            "email_notifications_enabled",
            "notify_new_orders",
            "notify_new_reviews",
            "notify_contact_messages",
        ]