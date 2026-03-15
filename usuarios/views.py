from django.contrib.auth.views import LoginView
from .forms import CustomAuthenticationForm
from django.urls import reverse_lazy


class CustomLoginView(LoginView):
    template_name = 'usuarios/login.html'
    authentication_form = CustomAuthenticationForm
    redirect_authenticated_user = True

    # valor por defecto si no hay ?next=
    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('panel')