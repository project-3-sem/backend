from django.urls import path

from .views import CorrectionClipDownloadAPIView, ProcessAudioAPIView


urlpatterns = [
    path('audio/process/', ProcessAudioAPIView.as_view(), name='audio-process'),
    path(
        # Use <path:filename> so traversal attempts like '../secret.mp3' are
        # routed to the view and can be rejected with a 400 (not a 404).
        'audio/corrections/<str:task_id>/<path:filename>',
        CorrectionClipDownloadAPIView.as_view(),
        name='audio-correction-download',
    ),
]
