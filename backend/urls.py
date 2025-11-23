from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from texts.views import TextViewSet

router = DefaultRouter()
router.register(r'texts', TextViewSet, basename='texts')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
]
