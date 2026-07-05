"""App URLs"""

# Django
from django.urls import path

# AA ACL Checker
from acl_checker import views

app_name: str = "acl_checker"  # pylint: disable=invalid-name

urlpatterns = [
    path("", views.index, name="index"),
    path("add-owner/", views.add_owner, name="add_owner"),
]
