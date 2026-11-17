from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from tracker import views as v
urlpatterns = [
    path("", v.dashboard, name="dashboard"), path("login/", v.login_view, name="login"), path("logout/", v.logout_view, name="logout"), path("join/<str:token>/", v.register, name="register"),
    path("orders/new/", v.order_form, name="order_new"), path("orders/<int:pk>/", v.order_detail, name="order_detail"), path("orders/<int:pk>/edit/", v.order_form, name="order_edit"), path("orders/<int:pk>/archive/", v.order_archive, name="order_archive"),
    path("inboxes/", v.inboxes, name="inboxes"), path("inboxes/<int:pk>/<str:action>/", v.inbox_action, name="inbox_action"), path("import/", v.import_email, name="import_email"),
    path("oauth/<str:provider>/start/", v.oauth_start, name="oauth_start"), path("oauth/<str:provider>/callback/", v.oauth_callback),
    path("insights/", v.analytics, name="analytics"), path("calendar/", v.calendar_view, name="calendar"), path("inventory/", v.inventory_view, name="inventory"), path("inventory/new/", v.inventory_form, name="inventory_new"), path("inventory/<int:pk>/edit/", v.inventory_form, name="inventory_edit"), path("inventory/<int:pk>/sell/", v.sale_form, name="sale_new"), path("sales/<int:pk>/delete/", v.sale_delete, name="sale_delete"),
    path("export/", v.export_csv, name="export"), path("notifications/", v.notifications, name="notifications"), path("settings/", v.settings_view, name="settings"), path("push/subscribe/", v.push_subscribe), path("push/unsubscribe/", v.push_unsubscribe),
    path("password/", auth_views.PasswordChangeView.as_view(template_name="password.html", success_url="/settings/"), name="password_change"),
    path("sw.js", v.service_worker), path("healthz/", v.health), path("privacy/", v.privacy, name="privacy"),
]
