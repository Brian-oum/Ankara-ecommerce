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
    path("account/orders/<str:order_reference>/", views.account_order_detail, name="account_order_detail"),
    path("account/reviews/<int:product_id>/", views.account_submit_review, name="account_submit_review"),
    path("wishlist/summary/", views.wishlist_summary, name="wishlist_summary"),
    path("wishlist/toggle/<int:product_id>/", views.wishlist_toggle, name="wishlist_toggle"),
    path("wishlist/remove/<int:product_id>/", views.wishlist_remove, name="wishlist_remove"),

    path("checkout/", views.checkout, name="checkout"),
    path("success/", views.success, name="success"),
    path("order/<str:order_reference>/confirm/", views.order_confirmation, name="order_confirmation"),
    path("contact/", views.contact, name="contact"),
    path("reviews/", views.store_reviews, name="store_reviews"),

    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("manager/reports/", views.manager_reports, name="manager_reports"),
    path("manager/orders/", views.manager_order_list, name="manager_order_list"),
    path("manager/orders/<str:order_reference>/", views.manager_order_detail, name="manager_order_detail"),
    path(
        "manager/orders/<str:order_reference>/status/",
        views.manager_order_update_status,
        name="manager_order_update_status",
    ),
    path("manager/products/", views.manager_product_list, name="manager_product_list"),
    path("manager/products/add/", views.manager_product_create, name="manager_product_create"),
    path("manager/products/<int:pk>/edit/", views.manager_product_edit, name="manager_product_edit"),
    path(
        "manager/products/<int:pk>/toggle-active/",
        views.manager_product_toggle_active,
        name="manager_product_toggle_active",
    ),
    path("manager/products/<int:pk>/delete/", views.manager_product_delete, name="manager_product_delete"),
    path("manager/categories/", views.manager_category_list, name="manager_category_list"),

    path("manager/settings/", views.manager_settings, name="manager_settings"),
    path("manager/settings/avatar/", views.manager_update_avatar, name="manager_update_avatar"),
    path("manager/settings/theme/", views.manager_update_theme, name="manager_update_theme"),

    path("callback/", views.payment_callback, name="payment_callback"),
    path("authenticate/", views.test_authentication, name="authenticate"),
    path("privacy-policy/", views.privacy_policy, name="privacy_policy"),
    path("terms-of-service/", views.terms_of_service, name="terms_of_service"),

    path("automation/failed-payments/", views.automation_failed_payments_report, name="automation_failed_payments_report"),
]