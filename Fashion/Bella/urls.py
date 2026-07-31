from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),

    path("products/", views.product_list, name="product_list"),
    path("products/search-suggest/", views.product_search_suggest, name="product_search_suggest"),
    path("products/<slug:slug>/", views.product_detail, name="product_detail"),

    path("cart/", views.cart_detail, name="cart_detail"),
    path("cart/summary/", views.cart_summary, name="cart_summary"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/remove/<int:product_id>/", views.cart_remove, name="cart_remove"),
    path("cart/update/<int:product_id>/", views.cart_update, name="cart_update"),

    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("account/", views.account_orders, name="account_orders"),

    path("wishlist/summary/", views.wishlist_summary, name="wishlist_summary"),
    path("wishlist/toggle/<int:product_id>/", views.wishlist_toggle, name="wishlist_toggle"),
    path("wishlist/remove/<int:product_id>/", views.wishlist_remove, name="wishlist_remove"),

    path("checkout/", views.checkout, name="checkout"),
    path("success/", views.success, name="success"),
    path("order/<str:order_reference>/confirm/", views.order_confirmation, name="order_confirmation"),
    path("contact/", views.contact, name="contact"),
    path("reviews/", views.store_reviews, name="store_reviews"),

    path("callback/", views.payment_callback, name="payment_callback"),
    path("authenticate/", views.test_authentication, name="authenticate"),
    path("privacy-policy/", views.privacy_policy, name="privacy_policy"),
    path("terms-of-service/", views.terms_of_service, name="terms_of_service"),
]