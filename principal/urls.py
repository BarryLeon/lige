from django.urls import path
from .views import (
    panel_view,
    )

urlpatterns = [
    path('panel/',                              panel_view,                  name='panel'),
   ]