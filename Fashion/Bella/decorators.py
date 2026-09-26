from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


def manager_required(view_func):
    """
    Gate manager-dashboard views behind is_staff - the same flag that
    already controls access to Django admin, so there's one source of
    truth for "who can manage the store" rather than a second role
    field to keep in sync.

    Not authenticated -> straight to login, with `next` pointing back
    at the page they wanted. Authenticated but not staff -> sent home
    with an explanatory message, rather than back to login (which
    would just bounce them in a loop since they're already signed in).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.is_staff:
            messages.error(request, "You don't have access to the manager dashboard.")
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return _wrapped