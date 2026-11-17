import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from tracker.models import Lease

class Command(BaseCommand):
    help = "Create the first administrator. No default password is installed."
    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="Owner")
    def handle(self, *args, **options):
        if User.objects.filter(is_staff=True).exists():
            raise CommandError("An owner already exists. Use invitations to add friends.")
        email = options["email"].lower().strip()
        try:
            validate_email(email)
            if len(email) > 150:
                raise ValidationError("Email must be at most 150 characters.")
            password = getpass.getpass("Owner password (12+ characters): ")
            if password != getpass.getpass("Repeat password: "):
                raise ValidationError("Passwords did not match.")
            user = User(username=email, email=email, first_name=options["name"])
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))
        user.set_password(password)
        user.is_staff = True
        user.save()
        Lease.objects.get_or_create(name="signup")
        self.stdout.write("Owner created. Sign in with your email address.")
