from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required(login_url='login')
def panel_view(request):
    """
    Vista del panel principal para usuarios logueados.
    """
    return render(request, 'principal/panel.html')
