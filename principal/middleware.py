from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

class LoginRequiredMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response
        self.login_url = reverse('login')

    def __call__(self, request):

        path = request.path

        public_paths = [
            self.login_url,
            '/admin/',
            settings.STATIC_URL,
            settings.MEDIA_URL if hasattr(settings, 'MEDIA_URL') else '/media/',
        ]

        is_public = any(path.startswith(p) for p in public_paths)

        if not request.user.is_authenticated and not is_public:
            return redirect(f"{self.login_url}?next={path}")

        response = self.get_response(request)
        return response