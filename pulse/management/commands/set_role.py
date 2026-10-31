from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from pulse.models import UserProfile


class Command(BaseCommand):
    help = "Set an existing user role (public registration always creates regular users)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=UserProfile.Role.values)

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("User does not exist. Register the user first.") from exc
        UserProfile.objects.update_or_create(user=user, defaults={"role": options["role"]})
        self.stdout.write(self.style.SUCCESS(f"{user.username}: {options['role']}"))
