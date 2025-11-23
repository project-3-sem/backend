from django.db import models

class Text(models.Model):
    EASY = 'easy'
    MEDIUM = 'medium'
    HARD = 'hard'

    DIFFICULTY_CHOICES = [
        (EASY, 'легкий'),
        (MEDIUM, 'средний'),
        (HARD, 'сложный'),
    ]

    title = models.CharField(max_length=255)
    body = models.TextField()
    difficulty = models.CharField(max_length=6, choices=DIFFICULTY_CHOICES, default=EASY)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
